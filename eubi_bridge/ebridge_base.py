import shutil, time, os, zarr, pprint, psutil, dask, gc, copy
import numpy as np, os, glob, tempfile

from ome_types import from_xml
from ome_types.model import OME, Image, Pixels, Channel #TiffData, Plane
from ome_types.model import PixelType, Pixels_DimensionOrder, UnitsLength, UnitsTime

from aicsimageio import AICSImage

from typing import Tuple

from dask import array as da
from pathlib import Path
from typing import Union

from eubi_bridge.ngff.multiscales import Pyramid
from eubi_bridge.fileset_io import FileSet
from eubi_bridge.ngff import defaults

from dask import delayed

from eubi_bridge.base.writers import store_arrays
from eubi_bridge.base.scale import Downscaler
from eubi_bridge.base.data_manager import BatchManager
from eubi_bridge.ngff.defaults import unit_map, scale_map, default_axes
from eubi_bridge.utils.convenience import sensitive_glob, is_zarr_group, is_zarr_array, take_filepaths

import logging, warnings, dask

logging.getLogger('distributed.diskutils').setLevel(logging.CRITICAL)


def get_chunksize_from_shape(chunk_shape, dtype):
    itemsize = dtype.itemsize
    chunk_size = itemsize * np.prod(chunk_shape)
    return f"{((chunk_size + chunk_size * 0.1) / (1000 ** 2))}MB"

def load_image_scene(input_path, scene_idx = None):
    """ Function to load an image and return a Dask array. """
    from aicsimageio import AICSImage
    if input_path.endswith('ome.tiff') or input_path.endswith('ome.tif'):
        from aicsimageio.readers.ome_tiff_reader import OmeTiffReader as reader
        img = AICSImage(input_path, reader = reader)
    elif input_path.endswith('tiff') or input_path.endswith('tif'):
        from aicsimageio.readers.tiff_reader import TiffReader as reader
        img = AICSImage(input_path, reader = reader)
    elif input_path.endswith('lif'):
        from aicsimageio.readers.lif_reader import LifReader as reader
        img = AICSImage(input_path, reader = reader)
    elif input_path.endswith('czi'):
        from aicsimageio.readers.czi_reader import CziReader as reader
        img = AICSImage(input_path, reader = reader)
    elif input_path.endswith('lsm'):
        from aicsimageio.readers.tiff_reader import TiffReader as reader
        img = AICSImage(input_path, reader = reader)
    else:
        img = AICSImage(input_path)
    if scene_idx is not None:
        img.set_scene(img.scenes[scene_idx])
    return img

def read_single_image(input_path):
    return load_image_scene(input_path, scene_idx=None)

def read_single_image_asarray(input_path):
    if input_path.endswith('.zarr'):
        im = Pyramid().from_ngff(input_path)
        arr = im.base_array
    else:
        arr = read_single_image(input_path).get_image_dask_data()
    if arr.ndim > 5:
        new_shape = np.array(arr.shape)
        new_shape[1] = (arr.shape[-1] * arr.shape[1])
        reshaped = arr.reshape(new_shape[:-1])
        return reshaped
    ### !!! TODO: also handle zarr inputs with ndim < 5.
    return arr

def get_image_shape(input_path, scene_idx):
    from aicsimageio import AICSImage
    img = AICSImage(input_path)
    img.set_scene(img.scenes[scene_idx])
    return img.shape

def _get_refined_arrays(fileset: FileSet,
                        root_path: str,
                        path_separator = '-'
                        ):
    """Get concatenated arrays from the fileset in an organized way, respecting the operating system."""
    root_path_ = os.path.normpath(root_path).split(os.sep)
    root_path_top = []
    for item in root_path_:
        if '*' in item:
            break
        root_path_top.append(item)

    if os.name == 'nt':
        # Use os.path.splitdrive to handle any drive letter
        drive, _ = os.path.splitdrive(root_path)
        root_path = os.path.join(drive + os.sep, *root_path_top)
    else:
        root_path = os.path.join(os.sep, *root_path_top)

    arrays_ = fileset.get_concatenated_arrays()
    arrays, sample_paths = {}, {}

    for key, vals in arrays_.items():
        updated_key, arr = vals
        new_key = os.path.relpath(updated_key, root_path)
        new_key = os.path.splitext(new_key)[0]
        new_key = new_key.replace(os.sep, path_separator)
        arrays[new_key] = arrays_[key][1]
        sample_paths[new_key] = key

    return arrays, sample_paths


class BridgeBase:
    def __init__(self,
                 input_path: Union[str, Path],  # TODO: add csv option (or a general table option).
                 includes=None,
                 excludes=None,
                 metadata_path = None,
                 series = None,
                 client = None,
                 ):
        if not input_path.startswith(os.sep):
            input_path = os.path.abspath(input_path)
        self._input_path = input_path
        self._includes = includes
        self._excludes = excludes
        self._metadata_path = metadata_path
        self._series = series
        self._dask_temp_dir = None
        self.vmeta = None
        self._cluster_params = None
        self.client = client
        self.fileset = None
        if self._series is not None:
            assert isinstance(self._series, (int, str)), f"The series parameter must be either an integer or string. Selection of multiple series from the same image is currently not supported."
        self.pixel_metadata = None

    def set_dask_temp_dir(self, temp_dir = 'auto'):
        if isinstance(temp_dir, tempfile.TemporaryDirectory):
            self._dask_temp_dir = temp_dir
            return self
        if temp_dir in ('auto', None):
            temp_dir = tempfile.TemporaryDirectory(delete = False)
        else:
            os.makedirs(temp_dir, exist_ok=True)
            temp_dir = tempfile.TemporaryDirectory(dir=temp_dir, delete = False)
        self._dask_temp_dir = temp_dir
        return self

    def read_dataset(self,
                     verified_for_cluster,
                     ):
        """
        - If the input path is a directory, can read single or multiple files from it.
        - If the input path is a file, can read a single image from it.
        - If the input path is a file with multiple series, can currently only read one series from it. Reading multiple series is currently not supported.
        :return:
        """
        input_path = self._input_path # todo: make them settable from this method?
        includes = self._includes
        excludes = self._excludes
        metadata_path = self._metadata_path
        series = self._series

        if os.path.isfile(input_path) or input_path.endswith('.zarr'):
            dirname = os.path.dirname(input_path)
            basename = os.path.basename(input_path)
            input_path = f"{dirname}/*{basename}"
            self._input_path = input_path

        if not '*' in input_path and not input_path.endswith('.zarr'):
            input_path = os.path.join(input_path, '**')
        paths = sensitive_glob(input_path, recursive=False, sensitive_to = '.zarr')
        paths = list(filter(lambda path: (includes in path if includes is not None else True) and
                                         (excludes not in path if excludes is not None else True),
                            paths))
        self.filepaths = sorted(list(filter(lambda path: os.path.isfile(path) or path.endswith('.zarr'), paths)))

        if series is None or series==0:
            futures = [delayed(read_single_image_asarray)(path) for path in self.filepaths]
            self.arrays = dask.compute(*futures)
        else: ### OME-Zarr is not yet compatible with the series option.
            futures = [delayed(load_image_scene)(path, series) for path in self.filepaths]
            imgs = dask.compute(*futures)
            self.arrays = [img.get_image_dask_data() for img in imgs]
            self.filepaths = [os.path.join(img.reader._path, img.current_scene)
                              for img in imgs] #TODO: In multiseries images, create fake filepath for the specified series/scene.

        if metadata_path is None:
            self.metadata_path = self.filepaths[0]
        else:
            self.metadata_path = metadata_path

    def digest(self, # TODO: refactor to "assimilate_tags" and "concatenate?"
               time_tag: Union[str, tuple] = None,
               channel_tag: Union[str, tuple] = None,
               z_tag: Union[str, tuple] = None,
               y_tag: Union[str, tuple] = None,
               x_tag: Union[str, tuple] = None,
               axes_of_concatenation: Union[int, tuple, str] = None,
               ):

        tags = (time_tag, channel_tag, z_tag, y_tag, x_tag)

        self.fileset = FileSet(self.filepaths,
                               arrays=self.arrays,
                               axis_tag0=time_tag,
                               axis_tag1=channel_tag,
                               axis_tag2=z_tag,
                               axis_tag3=y_tag,
                               axis_tag4=x_tag,
                               )

        if axes_of_concatenation is None:
            axes_of_concatenation = [idx for idx, tag in enumerate(tags) if tag is not None]

        if isinstance(axes_of_concatenation, str):
            axes = 'tczyx'
            axes_of_concatenation = [axes.index(item) for item in axes_of_concatenation]

        if np.isscalar(axes_of_concatenation):
            axes_of_concatenation = (axes_of_concatenation,)

        for axis in axes_of_concatenation:
            self.fileset.concatenate_along(axis)

        self.digested_arrays, self.digested_arrays_sample_paths = _get_refined_arrays(self.fileset, self._input_path)
        return self

    def compute_pixel_metadata(self,
                               series = None,
                               metadata_reader = 'bfio',
                               **kwargs
                               ):

        assert self.digested_arrays is not None
        assert self.digested_arrays_sample_paths is not None

        unitnamemap = {'time_unit': 't',
                       'channel_unit': 'c',
                       'z_unit': 'z',
                       'y_unit': 'y',
                       'x_unit': 'x'
                       }
        scalenamemap = {'time_scale': 't',
                     'channel_scale': 'c',
                     'z_scale': 'z',
                     'y_scale': 'y',
                     'x_scale': 'x'
                     }

        update_unitdict, update_scaledict = {}, {}
        for key in kwargs:
            if key in unitnamemap.keys():
                update_unitdict[unitnamemap[key]] = kwargs.get(key)
        for key in kwargs:
            if key in scalenamemap.keys():
                update_scaledict[scalenamemap[key]] = kwargs.get(key)

        self.batchdata = BatchManager(list(self.digested_arrays_sample_paths.values()),
                                        series,
                                        metadata_reader,
                                        **kwargs
                                        )
        for name, arr in self.digested_arrays.items():
            path = self.digested_arrays_sample_paths[name]
            self.batchdata.managers[path].set_arraydata(arr)
            self.batchdata.managers[path].update_meta(new_unitdict = update_unitdict,
                                                      new_scaledict = update_scaledict
                                                      )
        self.batchdata.fill_default_meta()

    def squeeze_dataset(self):
        self.batchdata.squeeze()

    def transpose_dataset(self,
                          dimension_order
                          ):
        self.batchdata.transpose(newaxes = dimension_order)

    def crop_dataset(self, **kwargs):
        self.batchdata.crop(**kwargs)

    def write_arrays(self,
                    output_path,
                    compute = True,
                    use_tensorstore = False,
                    rechunk_method = 'auto',
                    **kwargs
                    ):
        output_path = os.path.abspath(output_path)
        extra_kwargs = {}
        extra_kwargs.update(kwargs)
        if rechunk_method in ('rechunker', 'auto'):
            extra_kwargs['temp_dir'] = self._dask_temp_dir

        if extra_kwargs['squeeze']:
            self.squeeze_dataset()

        if extra_kwargs['dimension_order'] is not None:
            self.transpose_dataset(extra_kwargs['dimension_order'])

        self.crop_dataset(**extra_kwargs)

        sample_paths = self.digested_arrays_sample_paths
        assert self.batchdata is not None, f"At this stage batchdata should have been calculated."
        batch = self.batchdata

        ### TODO: make this a separate method?
        arrays = {k: {'0': batch.managers[v].array}
                  for k, v in sample_paths.items()}
        pixel_sizes = {k: {'0': batch.managers[v].scales}
                       for k, v in sample_paths.items()}
        pixel_axes = {k: {'0': batch.managers[v].axes}
                       for k, v in sample_paths.items()}
        pixel_units = {k: {'0': batch.managers[v].units}
                       for k, v in sample_paths.items()}
        chunk_shapes = {k: {'0': batch.managers[v].chunks}
                        for k, v in sample_paths.items()}

        flatarrays = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): arr
                      for key, subarrays in arrays.items()
                      for level, arr in subarrays.items()}
        flatscales = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): scale
                      for key, subscales in pixel_sizes.items()
                      for level, scale in subscales.items()}
        flataxes = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): ax
                      for key, subaxes in pixel_axes.items()
                      for level, ax in subaxes.items()}
        flatunits = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): unit
                      for key, subunits in pixel_units.items()
                      for level, unit in subunits.items()}
        flatchunks = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): chunk
                      for key, subchunks in chunk_shapes.items()
                      for level, chunk in subchunks.items()}
        ### TODO ends

        chunkdict = {
            't': extra_kwargs.get('time_chunk', None),
            'c': extra_kwargs.get('channel_chunk', None),
            'z': extra_kwargs.get('z_chunk', None),
            'y': extra_kwargs.get('y_chunk', None),
            'x': extra_kwargs.get('x_chunk', None)
        }

        ### TODO: Make this ugly block elegant.
        flatchunks_ = {}
        for path, flatchunks_unit in flatchunks.items():
            flataxes_unit = flataxes[path]
            updated_chunks_dict = copy.deepcopy(chunkdict)
            for ax in flataxes_unit:
                if chunkdict[ax] is None:
                    idx = flataxes_unit.index(ax)
                    updated_chunks_dict[ax] = flatchunks_unit[idx]
            updated_chunks = [updated_chunks_dict[ax]
                              for ax in flataxes_unit]
            flatchunks_[path] = updated_chunks
        ### TODO ends.

        results = store_arrays(flatarrays,
                               output_path,
                               axes = flataxes,
                               scales = flatscales,#pixel_sizes,
                               units = flatunits,
                               output_chunks = flatchunks_,
                               use_tensorstore = use_tensorstore,
                               compute = compute,
                               rechunk_method=rechunk_method,
                               **extra_kwargs
                               )

        # gc.collect()
        self.flatarrays = flatarrays

        if extra_kwargs['save_omexml']:
            managers = {k: batch.managers[v]
                           for k, v in sample_paths.items()}

            flatmanagers = {os.path.join(output_path, f"{key}.zarr"
                         if not key.endswith('zarr') else key): manager
                         for key, manager in managers.items()
                         }

            for key, manager in flatmanagers.items():
                if manager.omemeta is None:
                    manager.create_omemeta()
                manager.save_omexml(key)

        return results

def downscale(
        gr_paths,
        time_scale_factor,
        channel_scale_factor,
        z_scale_factor,
        y_scale_factor,
        x_scale_factor,
        n_layers,
        downscale_method='simple',
        **kwargs
        ):

    scale_factor_dict = {
                        't': time_scale_factor,
                        'c': channel_scale_factor,
                        'z': z_scale_factor,
                        'y': y_scale_factor,
                        'x': x_scale_factor
                         }

    if isinstance(gr_paths, dict):
        gr_paths = list(set(os.path.dirname(key) for key in gr_paths.keys()))

    pyrs = [Pyramid(path) for path in gr_paths]
    result_collection = []

    for pyr in pyrs:
        scale_factor = [scale_factor_dict[ax] for ax in pyr.meta.axis_order]
        pyr.update_downscaler(scale_factor=scale_factor,
                              n_layers=n_layers,
                              downscale_method=downscale_method
                              )
        grpath = pyr.gr.store.path
        grname = os.path.basename(grpath)
        grdict = {grname: {}}
        axisdict = {grname: {}}
        scaledict = {grname: {}}
        unitdict = {grname: {}}
        chunkdict = {grname: {}}

        for key, value in pyr.downscaler.downscaled_arrays.items():
            if key != '0':
                grdict[grname][key] = value
                axisdict[grname][key] = tuple(pyr.meta.axis_order)
                scaledict[grname][key] = tuple(pyr.downscaler.dm.scales[int(key)])
                unitdict[grname][key] = tuple(pyr.meta.unit_list)
                chunkdict[grname][key] = tuple(pyr.base_array.chunksize)

        output_path = os.path.dirname(grpath)
        arrays = {k: {'0': v} if not isinstance(v, dict) else v for k, v in grdict.items()}

        ### TODO: make this a separate function? def flatten_pyramids
        flatarrays = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): arr
                      for key, subarrays in arrays.items()
                      for level, arr in subarrays.items()}
        flataxes = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): axes
                      for key, subaxes in axisdict.items()
                      for level, axes in subaxes.items()}
        flatscales = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): scale
                      for key, subscales in scaledict.items()
                      for level, scale in subscales.items()}
        flatunits = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): unit
                      for key, subunits in unitdict.items()
                      for level, unit in subunits.items()}
        flatchunks = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): chunk
                      for key, subchunks in chunkdict.items()
                      for level, chunk in subchunks.items()}
        ### TODO ends

        results = store_arrays(flatarrays,
                               output_path=output_path,
                               axes = flataxes,
                               scales=flatscales,
                               units=flatunits,
                               output_chunks=flatchunks,
                               compute=False,
                               **kwargs
                               )

        result_collection += list(results.values())
    if 'rechunk_method' in kwargs:
        if kwargs.get('rechunk_method') == 'rechunker':
            raise NotImplementedError(f"Rechunker is not supported for the downscaling step.")
    if 'max_mem' in kwargs:
        raise NotImplementedError(f"Rechunker is not supported for the downscaling step.")
    try:
        dask.compute(*result_collection)
    except Exception as e:
        # print(e)
        pass
    return results
