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


def abbreviate_units(measure: str) -> str:
    abbreviations = {
        # Length measurements
        "millimeter": "mm",
        "centimeter": "cm",
        "decimeter": "dm",
        "meter": "m",
        "decameter": "dam",
        "hectometer": "hm",
        "kilometer": "km",
        "micrometer": "µm",
        "nanometer": "nm",
        "picometer": "pm",

        # Time measurements
        "second": "s",
        "millisecond": "ms",
        "microsecond": "µs",
        "nanosecond": "ns",
        "minute": "min",
        "hour": "h"
    }

    # Return the input if it's already an abbreviation
    if measure.lower() in abbreviations.values():
        return measure.lower()

    return abbreviations.get(measure.lower(), "Unknown")


def expand_units(measure: str) -> str:
    expansions = {
        # Length measurements
        "mm": "millimeter",
        "cm": "centimeter",
        "dm": "decimeter",
        "m": "meter",
        "dam": "decameter",
        "hm": "hectometer",
        "km": "kilometer",
        "µm": "micrometer",
        "nm": "nanometer",
        "pm": "picometer",

        # Time measurements
        "s": "second",
        "ms": "millisecond",
        "µs": "microsecond",
        "ns": "nanosecond",
        "min": "minute",
        "h": "hour"
    }

    # Return the input if it's already an expanded form
    if measure.lower() in expansions.values():
        return measure.lower()

    return expansions.get(measure.lower(), "Unknown")


class VoxelMeta:

    """
    Parse the metadata either from a reference image or an ome.xml file.
    """

    def __init__(self, path: Union[str, Path],
                 series: int = None,
                 dimension_order = 'tczyx',
                 metadata_reader = 'bfio' # bfio or aicsimageio
                 ):
        self.path = path
        self.series = series
        self._axes = dimension_order
        if series is not None:
            assert isinstance(self.series, (int, str)), f"The series parameter must be either an integer or string. Selection of multiple series from the same image is currently not supported."
        if self.series is None:
            self.series = 0
            self._seriesattrs = ""
        else:
            self._seriesattrs = self.series
        self.omemeta = None
        self._meta_reader = metadata_reader
        self._read_meta()
        self._scales = None
        self._units = None
        self._shape = None
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
        if self.series is not None:
            images = [self.omemeta.images[self.series]]
            self.omemeta.images = images
        return self.omemeta

    @property
    def axes(self):
        return self._axes

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

    @property
    def scaledict(self):
        return dict(zip(self.axes, self.scales))

    # dict(zip(vmeta.axes, vmeta.scales))
    # {key: value for key, value in zip(vmeta.axes, vmeta.scales)}


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

    @property
    def unitdict(self):
        return dict(zip(self.axes, self.units))

    def get_shape(self):
        shape = {}
        for ax in self.axes:
            shapekey = f"size_{ax.lower()}"
            if hasattr(self.pixel_meta, shapekey):
                shapevalue = getattr(self.pixel_meta, shapekey)
                shape[ax.lower()] = shapevalue
            else:
                shape[ax.lower()] = 1
        shape = [shape[key] for key in self.axes]
        return shape

    @property
    def shape(self):
        if self._shape is None:
            self._shape = self.get_shape()
        return self._shape

    def fill_default_meta(self):
        # non_ids = [idx for idx, key in enumerate(self.scales) if key is None]
        # if len(non_ids) > 0:
        x_id = self.axes.index('x')
        x_scale = self.scales[x_id]
        x_unit = self.units[x_id]
        y_id = self.axes.index('y')
        y_scale = self.scales[y_id]
        y_unit = self.units[y_id]
        z_id = self.axes.index('z')
        z_scale = self.scales[z_id]
        z_unit = self.units[z_id]
        t_id = self.axes.index('t')
        t_scale = self.scales[t_id]
        t_unit = self.units[t_id]
        if t_scale is None:
            t_scale = scale_map['t']
            self.set_scales(axes='t', scales=[t_scale])# , update_ome=False)
        if t_unit is None:
            t_unit = unit_map['t']
            self.set_units(axes='t', scales=[t_unit])# , update_ome=False)
        if z_scale is None:
            if x_scale is not None:
                z_scale = x_scale
            elif y_scale is not None:
                z_scale = y_scale
            else:
                z_scale = scale_map['z']
            self.set_scales(axes='z', scales=[z_scale])#, update_ome=False)
        if z_unit is None:
            if x_unit is not None:
                z_unit = x_unit
            elif y_unit is not None:
                z_unit = y_unit
            else:
                z_unit = unit_map['z']
            self.set_units(axes='z', units=[z_unit], update_ome=False)
        if y_scale is None:
            if x_scale is not None:
                y_scale = x_scale
            else:
                y_scale = scale_map['y']
            self.set_scales(axes='y', scales=[y_scale], update_ome=False)
        if y_unit is None:
            if x_unit is not None:
                y_unit = x_unit
            else:
                y_unit = unit_map['y']
            self.set_units(axes='y', units=[y_unit], update_ome=False)
        if x_scale is None:
            x_scale = scale_map['x']
            self.set_scales(axes='x', scales=[x_scale], update_ome=False)
        if x_unit is None:
            x_unit = unit_map['x']
            self.set_units(axes='x', units=[x_unit], update_ome=False)
        return self

    def set_shape(self,
                     axes: str,
                     sizes: (tuple, list)
                     ):
        if not hasattr(sizes, '__len__'):
            sizes = [sizes]

        for i, ax in enumerate(axes):
            shapekey = f"size_{ax}"
            setattr(self.pixel_meta, shapekey, sizes[i])
        self.omemeta.images[self.series].pixels = self.pixel_meta
        indices_remapped = [axes.index(ax) for ax in 'xyczt']
        sizes_remapped = [sizes[idx] for idx in indices_remapped]
        self.omemeta.images[self.series].description = str({"shape": sizes_remapped})
        # print(self.omemeta.images[self.series].description)
        self._shape = None
        return self

    def set_scales(self,
                  axes: str,
                  scales: (tuple, list),
                  update_ome = True
                  ):
        if not hasattr(scales, '__len__'):
            scales = [scales]
        assert len(axes) == len(scales), f"Axes and scales must have the same length."

        if not update_ome:
            for i, ax in enumerate(axes):
                idx = self.axes.index(ax)
                self.scales[idx] = scales[i]
            return self

        for i, ax in enumerate(axes):
            if ax == 'c':
                continue
            if ax == 't':
                scalekey = f"time_increment"
            else:
                scalekey = f"physical_size_{ax.lower()}"
            # if hasattr(self.pixel_meta, scalekey):
            setattr(self.pixel_meta, scalekey, scales[i])
            # else:
            #     setattr(self.pixel_meta, scalekey, defaults.scale_map[ax.lower()])
        self.omemeta.images[self.series].pixels = self.pixel_meta
        self._scales = None
        return self

    def set_units(self,
                  axes: str,
                  units: (tuple, list),
                  update_ome = True
                  ):

        if not hasattr(units, '__len__'):
            units = [units]
        assert len(axes) == len(units), f"Axes and units must have the same length."

        if not update_ome:
            for i, ax in enumerate(axes):
                idx = self.axes.index(ax)
                self.units[idx] = units[i]
            return self

        for i, ax in enumerate(axes):
            if ax == 'c':
                continue
            if ax == 't':
                unitkey = f"time_increment_unit"
            else:
                unitkey = f"physical_size_{ax.lower()}_unit"
            # if hasattr(self.pixel_meta, unitkey):
            setattr(self.pixel_meta, unitkey, abbreviate_units(units[i]))
            # else:
            #     setattr(self.pixel_meta, scalekey, abbreviate_units(defaults.unit_map[ax.lower()]))
        self.omemeta.images[self.series].pixels = self.pixel_meta
        self._units = None
        return self

    def ensure_omexml_fields(self):
        essential_fields = {
            "physical_size_x", "physical_size_x_unit",
            "physical_size_y", "physical_size_y_unit",
            "physical_size_z", "physical_size_z_unit",
            "time_increment", "time_increment_unit",
            "size_x", "size_y", "size_z", "size_t", "size_c"
        }
        missing_fields = essential_fields - self.pixel_meta.model_fields_set
        self.pixel_meta.model_fields_set.update(missing_fields)
        self.omemeta.images[self.series].pixels = self.pixel_meta

    def save_omexml(self,
                    base_path: str,
                    overwrite = False
                    ):
        assert self.omemeta is not None, f"No ome-xml exists."
        # print(base_path)
        gr = zarr.group(base_path)
        gr.create_group('OME', overwrite = overwrite)
        path = os.path.join(gr.store.path, 'OME/METADATA.ome.xml')
        # print(path)
        self.ensure_omexml_fields()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.omemeta.to_xml())
        gr.OME.attrs["series"] = [self._seriesattrs]



class PixelMeta:
    def __init__(self,
                 meta_paths: (list, tuple),
                 series = None,
                 metadata_reader = 'bfio',
                 **kwargs # This may include any updated scales or units
                 ):
        if not isinstance(meta_paths, (tuple, list)):
            meta_paths = [meta_paths]
        if series is not None:
            assert len(series) == len(meta_paths)
        vmetaset = {}
        for i, path in enumerate(meta_paths):
            if series is not None:
                s = series[i]
            else:
                s = None
            vmeta = VoxelMeta(path, s, metadata_reader = metadata_reader)

            vmeta.fill_default_meta()

            scales = self._collect_scales(**kwargs)
            for idx, scale in enumerate(scales):
                if scale is None:
                    scales[idx] = vmeta.scales[idx]
            assert len(scales) == 5, f"Scales must be a tuple of size 5. Add 1 for non-existent dimensions."

            units = self._collect_units(**kwargs)
            for idx, unit in enumerate(units):
                if unit is None:
                    units[idx] = vmeta.units[idx]
            assert len(units) == 5, f"Units must be a tuple of size 5. Add either of the default units for non-existent dimensions: (Frame, Channel, Slice, Pixel, Pixel)."

            vmeta.set_scales(vmeta.axes, scales)
            vmeta.set_units(vmeta.axes, units)

            vmetaset[path] = vmeta
        self.vmetaset = vmetaset

    def _collect_scales(self, **kwargs):
        """
        Retrieves pixel sizes for image dimensions.

        Args:
            **kwargs: Pixel sizes for time, channel, z, y, and x dimensions.

        Returns:
            list: Pixel sizes.
        """
        t = kwargs.get('time_scale', None)
        c = kwargs.get('channel_scale', None)
        y = kwargs.get('y_scale', None)
        x = kwargs.get('x_scale', None)
        z = kwargs.get('z_scale', None)
        return [t,c,z,y,x]

    def _collect_units(self, **kwargs):
        """
        Retrieves unit specifications for image dimensions.

        Args:
            **kwargs: Unit values for time, channel, z, y, and x dimensions.

        Returns:
            list: Unit values.
        """
        t = kwargs.get('time_unit', None)
        c = kwargs.get('channel_unit', None)
        y = kwargs.get('y_unit', None)
        x = kwargs.get('x_unit', None)
        z = kwargs.get('z_unit', None)
        return [t, c, z, y, x]


# paths = ['/home/oezdemir/Desktop/TIM2025/data/example_images/pff/filament.tif',
#          '/home/oezdemir/Desktop/TIM2025/data/example_images/pff/FtsZ2-1_GFP_KO2-1_no16G.lsm'
#          ]
#
# pmeta = PixelMeta(paths)

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

    def get_sample_paths(self): ### This is valid after the `digest` method is run
        keys = list(self.fileset.path_dict.keys())
        vals = list(self.fileset.path_dict.values())
        _, ids = np.unique(vals, return_index = True)
        return [keys[idx] for idx in ids]

    def get_sample_shapes(self):
        paths = list(self.fileset.path_dict.values())
        shapes = list(self.fileset.shape_dict.values())
        _, ids = np.unique(paths, return_index = True)
        return [shapes[idx] for idx in ids]

    def compute_pixel_metadata(self, ### VIM
                               series = None,
                               metadata_reader = 'bfio',
                               **kwargs
                               ): ### KALDIM!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        self.input_sample_paths = self.get_sample_paths() ### Bunun aynisini array shape icin de yap
        shapes = self.get_sample_shapes()
        self.input_sample_shapes = dict(zip(self.input_sample_paths, shapes))
        self.pixel_metadata = PixelMeta(self.input_sample_paths,
                                        series,
                                        metadata_reader,
                                        **kwargs
                                        )
        for path, vmeta in self.pixel_metadata.vmetaset.items():
            vmeta.set_shape(vmeta.axes, self.input_sample_shapes[path])

        self.pixel_metadata

    def write_arrays(self,
                    output_path,
                    output_chunks = (1, 1, 256, 256, 256),
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

        arrays, sample_paths = _get_refined_arrays(self.fileset, self._input_path)
        assert self.pixel_metadata is not None, f"At this stage pixel_metadata should have been calculated."
        pixel_metadata = self.pixel_metadata

        ### TODO: make this a separate method?
        arrays = {k: {'0': v} if not isinstance(v, dict) else v for k, v in arrays.items()}
        pixel_sizes = {k: {'0': pixel_metadata.vmetaset[v].scales}
                       for k, v in sample_paths.items()}
        pixel_units = {k: {'0': [expand_units(measure) for
                                 measure in pixel_metadata.vmetaset[v].units]}
                       for k, v in sample_paths.items()}
        pixel_ome = {k: pixel_metadata.vmetaset[v]
                       for k, v in sample_paths.items()}

        flatarrays = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): arr
                      for key, subarrays in arrays.items()
                      for level, arr in subarrays.items()}
        flatscales = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): scale
                      for key, subscales in pixel_sizes.items()
                      for level, scale in subscales.items()}
        flatunits = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): unit
                      for key, subunits in pixel_units.items()
                      for level, unit in subunits.items()}
        flatome = {os.path.join(output_path, f"{key}.zarr"
                     if not key.endswith('zarr') else key): vmeta
                     for key, vmeta in pixel_ome.items()
                     }
        ### TODO ends

        results = store_arrays(flatarrays,
                               output_path,
                               scales = flatscales,#pixel_sizes,
                               units = flatunits,
                               output_chunks = output_chunks,
                               use_tensorstore = use_tensorstore,
                               compute = compute,
                               rechunk_method=rechunk_method,
                               **extra_kwargs
                               )

        # gc.collect()
        self.flatarrays = flatarrays
        self.flatome = flatome
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
        unitdict = {grname: {}}
        for key, value in pyr.downscaler.downscaled_arrays.items():
            if key != '0':
                grdict[grname][key] = value
                scaledict[grname][key] = tuple(pyr.downscaler.dm.scales[int(key)])
                unitdict[grname][key] = tuple(pyr.meta.unit_list)

        output_path = os.path.dirname(grpath)
        arrays = {k: {'0': v} if not isinstance(v, dict) else v for k, v in grdict.items()}

        ### TODO: make this a separate function
        flatarrays = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): arr
                      for key, subarrays in arrays.items()
                      for level, arr in subarrays.items()}
        flatscales = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): scale
                      for key, subscales in scaledict.items()
                      for level, scale in subscales.items()}
        flatunits = {os.path.join(output_path, f"{key}.zarr"
                      if not key.endswith('zarr') else key, str(level)): unit
                      for key, subunits in unitdict.items()
                      for level, unit in subunits.items()}
        ###

        results = store_arrays(flatarrays,
                               output_path=output_path,
                               scales=flatscales,
                               units=flatunits,
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



# path = f"/home/oezdemir/Desktop/TIM2025/data/example_images/multichannel_timeseries_nested"
# path = f"/home/oezdemir/Desktop/TIM2025/data/example_images/pff/nuclei.tif"
# path = f"/home/oezdemir/Desktop/TIM2025/data/example_images/pff/17_03_18.lif"
# path = f"/home/oezdemir/data/original/steyer/amst"
# base = BridgeBase(path)
# base.read_dataset(True)
# base.digest(z_tag = 'slice_', axes_of_concatenation='z')
# base.compute_pixel_metadata()
# arrays = base.fileset.get_concatenated_arrays()
# arrays, sample_paths = _get_refined_arrays(base.fileset, base._input_path)
# pixel_metadata = PixelMeta(list(sample_paths.values()))
# pixel_metadata

#                            # y_scale = 2,
#                            # x_scale = 2,
#                            # y_unit = 'nm'
#                            )

# pixel_metadata._collect_scales(y_scale = 2)
#
# base.fileset.array_dict.keys()
# sample_paths = list(base.fileset.get_concatenated_arrays())
# arrays