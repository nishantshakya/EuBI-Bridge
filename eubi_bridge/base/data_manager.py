import zarr

import shutil, time, os, zarr, pprint, psutil, dask, gc
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
from eubi_bridge.ngff.defaults import unit_map, scale_map, default_axes
from eubi_bridge.utils.convenience import sensitive_glob, is_zarr_group, is_zarr_array, take_filepaths

import logging, warnings, dask


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

def create_ome_xml( # make 5D omexml
    image_shape: tuple,
    axis_order: str,
    pixel_size_x: float = None,
    pixel_size_y: float = None,
    pixel_size_z: float = None,
    pixel_size_t: float = None,
    unit_x: str = "MICROMETER",
    unit_y: str = None,
    unit_z: str = None,
    unit_t: str = None,
    dtype: str = "uint8",
    image_name: str = "Default Image"
    ) -> str:
    fullaxes = 'xyczt'
    if len(axis_order) != len(image_shape):
        raise ValueError("Length of axis_order must match length of image_shape")
    axis_order = axis_order.upper()

    pixel_size_basemap = {
        'time_increment': pixel_size_t,
        'physical_size_z': pixel_size_z,
        'physical_size_y': pixel_size_y,
        'physical_size_x': pixel_size_x
    }

    pixel_size_map = {}
    for ax in 'tzyx':
        if ax == 't':
            if ax in axis_order.lower():
                pixel_size_map['time_increment'] = pixel_size_t or 1
        else:
            if ax in axis_order.lower():
                pixel_size_map[f'physical_size_{ax}'] = pixel_size_basemap[f'physical_size_{ax}'] or 1


    unit_basemap = {
        'time_increment_unit': unit_t,
        'physical_size_z_unit': unit_z,
        'physical_size_y_unit': unit_y,
        'physical_size_x_unit': unit_x,
    }

    unit_map = {}
    for ax in 'tzyx':
        if ax == 't':
            if ax in axis_order.lower():
                unit_map['time_increment_unit'] = unit_t or 'second'
        else:
            if ax in axis_order.lower():
                unit_map[f'physical_size_{ax}_unit'] = unit_basemap[f'physical_size_{ax}_unit'] or 'MICROMETER'
    unit_map = {key: abbreviate_units(value) for key, value in unit_map.items() if value is not None}

    # Map numpy dtype to OME PixelType
    dtype_map = {
        "uint8": PixelType.UINT8,
        "uint16": PixelType.UINT16,
        "uint32": PixelType.UINT32,
        "int8": PixelType.INT8,
        "int16": PixelType.INT16,
        "int32": PixelType.INT32,
        "float": PixelType.FLOAT,
        "double": PixelType.DOUBLE,
    }

    if dtype not in dtype_map:
        raise ValueError(f"Unsupported dtype: {dtype}")

    pixel_type = dtype_map[dtype]

    # Initialize axis sizes
    size_map_ = dict(zip(axis_order.lower(), image_shape))
    size_map = {}
    for ax in fullaxes:
        if ax in size_map_:
            size_map[f'size_{ax}'] = size_map_[ax]
        else:
            size_map[f'size_{ax}'] = 1

    pixels = Pixels(
        dimension_order=Pixels_DimensionOrder(fullaxes.upper()),
        **size_map,
        type=pixel_type,
        **pixel_size_map,
        **unit_map,
        channels=[Channel(id=f"Channel:{idx}", samples_per_pixel=1) for idx in range(size_map['size_c'])],
    )

    image = Image(id="Image:0", name=image_name, pixels=pixels)

    ome = OME(images=[image])

    return ome

class PFFImageMeta:
    essential_omexml_fields = {
        "physical_size_x", "physical_size_x_unit",
        "physical_size_y", "physical_size_y_unit",
        "physical_size_z", "physical_size_z_unit",
        "time_increment", "time_increment_unit",
        "size_x", "size_y", "size_z", "size_t", "size_c"
    }

    def __init__(self,
                 path,
                 series,
                 meta_reader
                 ):
        if path.endswith('ome') or path.endswith('xml'):
            from ome_types import OME
            omemeta = OME().from_xml(path)
        else:
            if meta_reader == 'aicsimageio':
                from aicsimageio.readers.bioformats_reader import BioformatsReader
                img = AICSImage(
                    path,
                    reader=BioformatsReader
                )
                if series is not None:
                    img.set_scene(series)
                omemeta = img.ome_metadata
            elif meta_reader == 'bfio':
                from bfio import BioReader
                omemeta = BioReader(path, backend='bioformats').metadata
        if series is not None:
            images = [omemeta.images[series]]
            omemeta.images = images

        self.omemeta = omemeta
        self.pixels = self.omemeta.images[series].pixels
        missing_fields = self.essential_omexml_fields - self.pixels.model_fields_set
        self.pixels.model_fields_set.update(missing_fields)
        self.omemeta.images[series].pixels = self.pixels
        self.pyr = None

    def get_axes(self):
        return 'tczyx'

    def get_scaledict(self):
        return {
            't': self.pixels.time_increment,
            'z': self.pixels.physical_size_z,
            'y': self.pixels.physical_size_y,
            'x': self.pixels.physical_size_x
        }

    def get_scales(self):
        scaledict = self.get_scaledict()
        caxes = [ax for ax in self.get_axes() if ax != 'c']
        return [scaledict[ax] for ax in caxes]

    def get_unitdict(self):
        return {
            't': self.pixels.time_increment_unit.name.lower(),
            'z': self.pixels.physical_size_z_unit.name.lower(),
            'y': self.pixels.physical_size_y_unit.name.lower(),
            'x': self.pixels.physical_size_x_unit.name.lower()
        }

    def get_units(self):
        unitdict = self.get_unitdict()
        caxes = [ax for ax in self.get_axes() if ax != 'c']
        return [unitdict[ax] for ax in caxes]



class NGFFImageMeta:
    def __init__(self,
                 path
                 ):
        if is_zarr_group(path):
            self.pyr = Pyramid().from_ngff(path)
            meta = self.pyr.meta
            self._meta = meta
            self._base_path = self._meta.resolution_paths[0]
        else:
            raise Exception(f"The given path does not contain an NGFF group.")

    def get_axes(self):
        return self._meta.axis_order

    def get_scales(self):
        return self._meta.get_scale(self._base_path)

    def get_scaledict(self):
        return self._meta.get_scaledict(self._base_path)

    def get_units(self):
        return self._meta.unit_list

    def get_unitdict(self):
        return self._meta.unit_dict


class ArrayManager:
    essential_omexml_fields = {
        "physical_size_x", "physical_size_x_unit",
        "physical_size_y", "physical_size_y_unit",
        "physical_size_z", "physical_size_z_unit",
        "time_increment", "time_increment_unit",
        "size_x", "size_y", "size_z", "size_t", "size_c"
    }
    def __init__(self,
                 path: Union[str, Path] = None,
                 series: int = None,
                 metadata_reader='bfio'  # bfio or aicsimageio
                 ):
        self.path = path
        self.series = series
        if series is not None:
            assert isinstance(self.series, (int,
                                            str)), f"The series parameter must be either an integer or string. Selection of multiple series from the same image is currently not supported."
        if self.series is None:
            self.series = 0
            self._seriesattrs = ""
        else:
            self._seriesattrs = self.series

        self._meta_reader = metadata_reader
        self.omemeta = None

        if not path is None:
            if is_zarr_group(path):
                self.img = NGFFImageMeta(self.path)
            else:
                self.img = PFFImageMeta(self.path, self.series, self._meta_reader)

        self.axes = self.img.get_axes()
        self.array = None
        self.pyr = self.img.pyr
        self.set_arraydata()

    def fill_default_meta(self):
        if self.array is None:
            raise Exception(f"Array is missing. An array needs to be assigned.")
        new_scaledict = {}
        new_unitdict = {}
        values = list(self.scaledict.values())
        if not None in values:
            return

        for ax, value in self.scaledict.items():
            if value is None:
                if (ax == 'z' or ax == 'y') and self.scaledict['x'] is not None:
                    new_scaledict[ax] = self.scaledict['x']
                    new_unitdict[ax] = self.unitdict['x']
                else:
                    new_scaledict[ax] = scale_map[ax]
                    new_unitdict[ax] = unit_map[ax]
            else:
                if ax in self.scaledict.keys():
                    new_scaledict[ax] = self.scaledict[ax]
                if ax in self.unitdict.keys():
                    new_unitdict[ax] = self.unitdict[ax]

        new_units = [new_unitdict[ax] for ax in self.axes if ax in new_unitdict]
        new_scales = [new_scaledict[ax] for ax in self.axes if ax in new_scaledict]

        self.set_arraydata(self.array, self.axes, new_units, new_scales)
        return self

    def get_pixel_size_basemap(self,
                               t = 1,
                               z = 1,
                               y = 1,
                               x = 1,
                               **kwargs
                               ):
        return {
            'pixel_size_t': t,
            'pixel_size_z': z,
            'pixel_size_y': y,
            'pixel_size_x': x
        }

    def get_unit_basemap(self,
                         t = 'second',
                         z = 'micrometer',
                         y = 'micrometer',
                         x = 'micrometer',
                         **kwargs
                         ):
        return {
            'unit_t': t,
            'unit_z': z,
            'unit_y': y,
            'unit_x': x
        }

    def update_meta(self,
                    new_scaledict = {},
                    new_unitdict = {}
                    ):

        scaledict = self.img.get_scaledict()
        for key, val in new_scaledict.items():
            if key in scaledict.keys() and val is not None:
                scaledict[key] = val
            # else:
            #     raise ValueError(f"The given axis {key} is not present in the array.")

        if 'c' in scaledict:
            scales = [scaledict[ax] for ax in self.axes]
        else:
            scales = [scaledict[ax] for ax in self.caxes]

        unitdict = self.img.get_unitdict()
        for key, val in new_unitdict.items():
            if key in unitdict.keys() and val is not None:
                unitdict[key] = val
            # else:
            #     raise ValueError(f"The given axis {key} is not present in the array.")

        if 'c' in unitdict:
            units = [expand_units(unitdict[ax]) for ax in self.axes]
        else:
            units = [expand_units(unitdict[ax]) for ax in self.caxes]

        self.set_arraydata(array = self.array,
                           axes = self.axes,
                           units = units,
                           scales = scales)

    def set_arraydata(self,
                      array = None,
                      axes = None,
                      units = None,
                      scales = None,
                      **kwargs # placehold
                      ):

        axes = axes or self.img.get_axes()
        units = units or self.img.get_units()
        scales = scales or self.img.get_scales()

        self.axes = axes
        if array is not None:
            self.array = array
            self.ndim = self.array.ndim
            assert len(self.axes) == self.ndim

        self.caxes = ''.join([ax for ax in axes if ax != 'c'])
        if self.array is not None:
            self.chunkdict = dict(zip(list(self.axes), self.array.chunksize))
            self.shapedict = dict(zip(list(self.axes), self.array.shape))
        if len(units) == len(self.axes):
            self.unitdict = dict(zip(list(self.axes), units))
        elif len(units) == len(self.caxes):
            self.unitdict = dict(zip(list(self.caxes), units))
        else:
            raise Exception(f"Unit length is invalid.")
        if len(scales) == len(self.axes):
            self.scaledict = dict(zip(list(self.axes), scales))
        elif len(scales) == len(self.caxes):
            self.scaledict = dict(zip(list(self.caxes), scales))
            self.scaledict['c'] = 1
        else:
            raise Exception(f"Scale length is invalid")

    @property
    def scales(self):
        if self.scaledict.__len__() < len(self.axes):
            return [self.scaledict[ax] for ax in self.caxes]
        elif self.scaledict.__len__() == len(self.axes):
            return [self.scaledict[ax] for ax in self.axes]
        else:
            raise ValueError

    @property
    def units(self):
        if self.unitdict.__len__() < len(self.axes):
            return [self.unitdict[ax] for ax in self.caxes]
        elif self.unitdict.__len__() == len(self.axes):
            return [self.unitdict[ax] for ax in self.axes]
        else:
            raise ValueError

    @property
    def chunks(self):
        return [self.chunkdict[ax] for ax in self.axes]

    def sync_pyramid(self): ### TODO: reconsider for improvement
        ### This is only to be used to update pyramidal metadata in place.
        """
        Synchronizes the scale and unit metadata with the Pyramid (if a Pyramid exists).
        The scale metadata is recalculated for all layers based on the shape of each layer.
        Also updates the ome-xml metadata to the pyramid.
        :return:
        """
        if self.pyr is None:
            raise Exception(f"No pyramid exists.")

        self.pyr.update_scales(**self.scaledict,
                                     hard=True)
        self.pyr.update_units(**self.unitdict,
                                   hard=True)
        if self.omemeta is not None:
            self.save_omexml(self.pyr.gr.store.path, overwrite=True)

    def create_omemeta(self):
        self.fill_default_meta()

        pixel_size_basemap = self.get_pixel_size_basemap(
            **self.scaledict
        )
        unit_basemap = self.get_unit_basemap(
            **self.unitdict
        )
        self.omemeta = create_ome_xml(image_shape = self.array.shape,
                                      axis_order = self.axes,
                                      **pixel_size_basemap,
                                      **unit_basemap,
                                      dtype = str(self.array.dtype)
                                      )
        self.pixels = self.omemeta.images[self.series].pixels
        missing_fields = self.essential_omexml_fields - self.pixels.model_fields_set
        self.pixels.model_fields_set.update(missing_fields)
        self.omemeta.images[self.series].pixels = self.pixels
        return self

    def save_omexml(self,
                    base_path: str,
                    overwrite = False
                    ):
        assert self.omemeta is not None, f"No ome-xml exists."
        gr = zarr.group(base_path)
        gr.create_group('OME', overwrite = overwrite)
        path = os.path.join(gr.store.path, 'OME/METADATA.ome.xml')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.omemeta.to_xml())
        gr.OME.attrs["series"] = [self._seriesattrs]

    def squeeze(self):
        singlet_axes = [ax for ax, size in self.shapedict.items() if size == 1]
        newaxes = ''.join(ax for ax in self.axes if ax not in singlet_axes)
        newunits, newscales = [], []
        assert (self.scaledict.__len__() - self.unitdict.__len__()) <= 1
        for ax in self.axes:
            if ax not in singlet_axes:
                if ax in self.unitdict.keys():
                    newunits.append(self.unitdict[ax])
                if ax in self.scaledict.keys():
                    newscales.append(self.scaledict[ax])
        newarray = da.squeeze(self.array)
        self.set_arraydata(newarray, newaxes, newunits, newscales)

    def transpose(self, newaxes):
        newaxes = ''.join(ax for ax in newaxes if ax in self.axes)
        new_ids = [self.axes.index(ax) for ax in newaxes]
        newunits, newscales = [], []
        assert (self.scaledict.__len__() - self.unitdict.__len__()) <= 1
        for ax in newaxes:
            if ax in self.unitdict:
                newunits.append(self.unitdict[ax])
            if ax in self.scaledict.keys():
                newscales.append(self.scaledict[ax])
        newarray = self.array.transpose(*new_ids)
        self.set_arraydata(newarray, newaxes, newunits, newscales)

    def crop(self,
             trange = None,
             crange = None,
             zrange = None,
             yrange = None,
             xrange = None,
             ):
        slicedict = {
            't': slice(*trange) if trange is not None else slice(None),
            'c': slice(*crange) if crange is not None else slice(None),
            'z': slice(*zrange) if zrange is not None else slice(None),
            'y': slice(*yrange) if yrange is not None else slice(None),
            'x': slice(*xrange) if xrange is not None else slice(None),
        }
        slicedict = {ax: r for ax,r in slicedict.items() if ax in self.axes}
        slices = tuple([slicedict[ax] for ax in self.axes])
        array = self.array[slices]
        self.set_arraydata(array, self.axes, self.units, self.scales)

    def split(self): ###TODO
        pass

class BatchManager:
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
        managers = {}
        for i, path in enumerate(meta_paths):
            if series is not None:
                s = series[i]
            else:
                s = None
            manager = ArrayManager(path, s, metadata_reader = metadata_reader)

            # manager.fill_default_meta()

            scaleupdates = self._collect_scaledict(**kwargs)
            unitupdates = self._collect_unitdict(**kwargs)

            scaledict = manager.scaledict
            if scaleupdates.__len__():
                scaledict.update(**scaleupdates)
            unitdict = manager.unitdict
            if unitdict.__len__():
                unitdict.update(**unitupdates)

            scales = [scaledict[ax] for ax in manager.caxes]
            units = [unitdict[ax] for ax in manager.caxes]
            manager.set_arraydata(units = units, scales = scales)

            managers[path] = manager
        self.managers = managers

    def _collect_scaledict(self, **kwargs):
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
        fulldict = dict(zip('tczyx', [t,c,z,y,x]))
        final = {key: val for key,val in fulldict.items() if val is not None}
        return final

    def _collect_unitdict(self, **kwargs):
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
        fulldict = dict(zip('tczyx', [t,c,z,y,x]))
        final = {key: val for key,val in fulldict.items() if val is not None}
        return final


    def _collect_chunks(self, **kwargs): ### TODO: KALDIM 12 MAYIS
        """
        Retrieves chunk specifications for image dimensions.

        Args:
            **kwargs: Chunk sizes for time, channel, z, y, and x dimensions.

        Returns:
            list: Chunk shape.
        """
        t = kwargs.get('time_chunk', None)
        c = kwargs.get('channel_chunk', None)
        y = kwargs.get('y_chunk', None)
        x = kwargs.get('x_chunk', None)
        z = kwargs.get('z_chunk', None)
        fulldict = dict(zip('tczyx', [t,c,z,y,x]))
        final = {key: val for key,val in fulldict.items() if val is not None}
        return final

    def fill_default_meta(self):
        for key, manager in self.managers.items():
            manager.fill_default_meta()

    def squeeze(self):
        for key, manager in self.managers.items():
            manager.squeeze()

    def crop(self,
             time_range = None,
             channel_range = None,
             z_range = None,
             y_range = None,
             x_range = None,
             **kwargs # placehold
             ):
        if any([item is not None 
                for item in (time_range, 
                             channel_range, 
                             z_range, 
                             y_range, 
                             x_range)]):
            for key, manager in self.managers.items():
                manager.crop(time_range, channel_range, z_range, y_range, x_range)

    def transpose(self, newaxes):
        for key, manager in self.managers.items():
            manager.transpose(newaxes)

    def sync_pyramids(self):
        pass
