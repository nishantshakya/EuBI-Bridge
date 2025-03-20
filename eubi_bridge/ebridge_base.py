import shutil, time, os, zarr, pprint, psutil, dask, gc

import numpy as np, os, glob, tempfile
from aicsimageio import AICSImage

from dask import array as da
from pathlib import Path
from typing import Union

from eubi_bridge.ngff.multiscales import Pyramid
from eubi_bridge.fileset_io import FileSet
from eubi_bridge.ngff import defaults

from dask import delayed

from eubi_bridge.base.writers import store_arrays
from eubi_bridge.base.scale import Downscaler
from eubi_bridge.ngff.defaults import unit_map, scale_map, default_axes

import logging, warnings, dask

logging.getLogger('distributed.diskutils').setLevel(logging.CRITICAL)


class VoxelMeta:

    """
    Parse the metadata either from a reference image or an ome.xml file.
    """

    def __init__(self, path: Union[str, Path],
                 series: int = None,
                 metadata_reader = 'bfio' # bfio or aicsimageio
                 ):
        self.path = path
        self.series = series
        if series is not None:
            assert isinstance(self.series, (int, str)), f"The series parameter must be either an integer or string. Selection of multiple series from the same image is currently not supported."
        if self.series is None:
            self.series = 0
        self.omemeta = None
        self._meta_reader = metadata_reader
        self._read_meta()
        self._scales = None
        self._units = None
        assert self.ndim == 5, Exception(f"Metadata must define 5D image. Try defining the voxel metadata manually.")

    def _read_meta(self):
        if self.path.endswith('ome') or self.path.endswith('xml'):
            from ome_types import OME
            self.omemeta = OME().from_xml(self.path)
        else:
            if self._meta_reader == 'aicsimageio':
                from aicsimageio.readers.bioformats_reader import BioformatsReader
                img = AICSImage(
                    self.path,
                    reader = BioformatsReader
                )
                if self.series is not None:
                    img.set_scene(self.series)
                self.omemeta = img.ome_metadata
            elif self._meta_reader == 'bfio':
                from bfio import BioReader
                self.omemeta = BioReader(self.path, backend = 'bioformats').metadata
        return self.omemeta

    @property
    def axes(self):
        return 'tczyx'

    @property
    def ndim(self):
        return len(self.scales)

    @property
    def pixel_meta(self):
        if not hasattr(self, "omemeta"):
            omemeta = self._read_meta()
        elif self.omemeta is None:
            omemeta = self._read_meta()
        else:
            omemeta = self.omemeta
        if omemeta is None:
            pixels = None
        else:
            pixels = omemeta.images[self.series].pixels  # Image index is 0 by default. So far multiseries data not supported.
        return pixels

    def get_scales(self):
        scales = {}
        for ax in self.axes:
            if ax == 't':
                scalekey = f"time_increment"
            else:
                scalekey = f"physical_size_{ax.lower()}"
            if hasattr(self.pixel_meta, scalekey):
                scalevalue = getattr(self.pixel_meta, scalekey)
                scales[ax.lower()] = scalevalue
            else:
                scales[ax.lower()] = defaults.scale_map[ax.lower()]
        scales = [scales[key] for key in self.axes]
        return scales

    @property
    def scales(self):
        if self._scales is None:
            self._scales = self.get_scales()
        return self._scales

    def get_units(self):
        units = {}
        for ax in self.axes:
            if ax == 't':
                unitkey = f"time_increment_unit"
            else:
                unitkey = f"physical_size_{ax.lower()}_unit"
            if hasattr(self.pixel_meta, unitkey):
                unitvalue = getattr(self.pixel_meta, unitkey)
                units[ax.lower()] = unitvalue.value
            else:
                units[ax.lower()] = defaults.unit_map[ax.lower()]
        units_ = [units[key] for key in self.axes]
        return units_

    @property
    def units(self):
        if self._units is None:
            self._units = self.get_units()
        return self._units

    def set_scales(self,
                  **kwargs
                  ):
        for key, value in kwargs.items():
            idx = self.axes.index(key)
            self.scales[idx] = value
        return self

    def set_units(self,
                 **kwargs
                  ):
        for key, value in kwargs.items():
            idx = self.axes.index(key)
            self.units[idx] = value
        return self

    def fill_default_meta(self):
        non_ids = [idx for idx, key in enumerate(self.scales) if key is None]
        if len(non_ids) > 0:
            non_axes = [self.axes[idx] for idx in non_ids]
            for idx, ax in zip(non_ids, non_axes):
                self.set_scales(**{ax: scale_map[ax]})
                self.set_units(**{ax: unit_map[ax]})
        return self


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
    arr = read_single_image(input_path).get_image_dask_data()
    if arr.ndim > 5:
        new_shape = np.array(arr.shape)
        new_shape[1] = (arr.shape[-1] * arr.shape[1])
        reshaped = arr.reshape(new_shape[:-1])
        return reshaped
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
    arrays = {}

    for key in arrays_.keys():
        new_key = os.path.relpath(key, root_path)
        new_key = os.path.splitext(new_key)[0]
        new_key = new_key.replace(os.sep, path_separator)
        arrays[new_key] = arrays_[key]

    return arrays


class BridgeBase:
    def __init__(self,
                 input_path: Union[str, Path],  # TODO: add csv option.
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

        if os.path.isfile(input_path):
            dirname = os.path.dirname(input_path)
            basename = os.path.basename(input_path)
            input_path = f"{dirname}/*{basename}"
            self._input_path = input_path

        if not '*' in input_path:
            input_path = os.path.join(input_path, '**')
        paths = glob.glob(input_path, recursive=True)
        paths = list(filter(lambda path: (includes in path if includes is not None else True) and
                                         (excludes not in path if excludes is not None else True),
                            paths))
        self.filepaths = sorted(list(filter(os.path.isfile, paths)))

        if series is None or series==0:
            futures = [delayed(read_single_image_asarray)(path) for path in self.filepaths]
            self.arrays = dask.compute(*futures)
        else:
            futures = [delayed(load_image_scene)(path, series) for path in self.filepaths]
            imgs = dask.compute(*futures)
            self.arrays = [img.get_image_dask_data() for img in imgs]
            self.filepaths = [os.path.join(img.reader._path, img.current_scene)
                              for img in imgs] # In multiseries images, create fake filepath for the specified series/scene.

        if metadata_path is None:
            self.metadata_path = self.filepaths[0]
        else:
            self.metadata_path = metadata_path

    def digest(self, # TODO: refactor to "assimilate_tags" and "concatenate"
               time_tag: Union[str, tuple] = None,
               channel_tag: Union[str, tuple] = None,
               z_tag: Union[str, tuple] = None,
               y_tag: Union[str, tuple] = None,
               x_tag: Union[str, tuple] = None,
               axes_of_concatenation: Union[int, tuple, str] = None,
               ): # TODO: handle pixel metadata here?

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

        return self

    def write_arrays(self,
                    output_path,
                    output_chunks = (1, 1, 256, 256, 256),
                    pixel_sizes = None,
                    pixel_units = None,
                    compute = True,
                    use_tensorstore = False,
                    rechunk_method = 'auto',
                    **kwargs
                    ):
        extra_kwargs = {}
        extra_kwargs.update(kwargs)
        if rechunk_method in ('rechunker', 'auto'):
            extra_kwargs['temp_dir'] = self._dask_temp_dir

        if None in (pixel_sizes, pixel_units):
            raise TypeError(f"'pixel_sizes' and 'pixel_units' must be provided.")
        if not isinstance(pixel_sizes, dict):
            pixel_sizes = {'0': pixel_sizes}
        arrays = _get_refined_arrays(self.fileset, self._input_path)
        pixel_sizes = {name: pixel_sizes for name in arrays}

        results = store_arrays(arrays,
                               output_path,
                               scales = pixel_sizes,
                               units = pixel_units,
                               output_chunks = output_chunks,
                               use_tensorstore = use_tensorstore,
                               compute = compute,
                               rechunk_method=rechunk_method,
                               **extra_kwargs
                               )

        # gc.collect()
        return results

def downscale(
        gr_paths,
        scale_factor,
        n_layers,
        downscale_method='simple',
        **kwargs
        ):

    if isinstance(gr_paths, dict):
        gr_paths = list(set(os.path.dirname(key) for key in gr_paths.keys()))

    pyrs = [Pyramid(path) for path in gr_paths]
    result_collection = []

    for pyr in pyrs:
        pyr.update_downscaler(scale_factor=scale_factor,
                              n_layers=n_layers,
                              downscale_method=downscale_method
                              )
        grpath = pyr.gr.store.path
        grname = os.path.basename(grpath)
        grdict = {grname: {}}
        scaledict = {grname: {}}
        for key, value in pyr.downscaler.downscaled_arrays.items():
            if key != '0':
                grdict[grname][key] = value
                scaledict[grname][key] = tuple(pyr.downscaler.dm.scales[int(key)])

        results = store_arrays(grdict,
                               output_path=os.path.dirname(grpath),
                               scales=scaledict,
                               units=pyr.meta.unit_list,
                               output_chunks=pyr.base_array.chunksize,
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


