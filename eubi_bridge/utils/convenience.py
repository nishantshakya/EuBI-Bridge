import os, json
import warnings

import numpy as np, pandas as pd
import zarr, json, shutil, os, copy, zarr
from dask import array as da
import dask
import numcodecs
import shutil, tempfile
from pathlib import Path

from pathlib import Path

from typing import (
    Union,
    Tuple,
    Dict,
    Any,
    Iterable,
    List,
    Optional
)

def asstr(s):
    if isinstance(s, str):
        return s
    elif isinstance(s, int):
        return str(s)
    else:
        raise TypeError(f"Input must be either of types {str, int}")

def asdask(data, chunks = 'auto'):
    assert isinstance(data, (da.Array, zarr.Array, np.ndarray)), f'data must be of type: {da.Array, zarr.Array, np.ndarray}'
    if isinstance(data, zarr.Array):
        return da.from_zarr(data)
    elif isinstance(data, np.ndarray):
        return da.from_array(data, chunks = chunks)
    return data

def path_has_pyramid(path):
    try:
        store = zarr.DirectoryStore(path)
        _ = zarr.open_group(store, mode = 'r')
        return True
    except:
        return False


def parse_as_list(path_or_paths: Union[Iterable, str, int, float]
                   ):
    if isinstance(path_or_paths, (str, int, float)):
        inputs = [path_or_paths]
    else:
        inputs = path_or_paths
    return inputs

def includes(group1: Union[Iterable, str, int, float],
             group2: Union[Iterable, str, int, float]
             ):
    """Convenience function that checks if group1 includes group2 completely."""
    gr1 = parse_as_list(group1)
    gr2 = parse_as_list(group2)
    return all([item in gr1 for item in gr2])

def insert_at_indices(iterable1, iterable2, indices):
    if not hasattr(iterable1, '__len__'):
        iterable1 = [iterable1]
    if not hasattr(iterable2, '__len__'):
        iterable2 = [iterable2]
    if not hasattr(indices, '__len__'):
        indices = [indices]
    endlen = (len(iterable1) + len(iterable2))
    end_indices = [None] * endlen
    other_indices = [i for i in range(endlen) if i not in indices]
    for i, j in zip(other_indices, iterable1):
        end_indices[i] = j
    for i, j in zip(indices, iterable2):
        end_indices[i] = j
    return end_indices

def index_nth_dimension(array,
                        dimensions = 2, # a scalar or iterable
                        intervals = None # a scalar, an iterable of scalars, a list of tuple or None
                        ):
    if isinstance(array, zarr.Array):
        array = da.from_zarr(array)
    allinds = np.arange(array.ndim).astype(int)
    if np.isscalar(dimensions):
        dimensions = [dimensions]
    if intervals is None or np.isscalar(intervals):
        intervals = np.repeat(intervals, len(dimensions))
    assert len(intervals) == len(dimensions) ### KALDIM
    interval_dict = {item: interval for item, interval in zip(dimensions, intervals)}
    shape = array.shape
    slcs = []
    for idx, dimlen in zip(allinds, shape):
        if idx not in dimensions:
            slc = slice(dimlen)
        else:
            try:
                slc = slice(interval_dict[idx][0], interval_dict[idx][1])
            except:
                slc = interval_dict[idx]
        slcs.append(slc)
    slcs = tuple(slcs)
    indexed = array[slcs]
    return indexed

def transpose_dict(dictionary):
    keys, values = [], []
    for key, value in dictionary.items():
        keys.append(key)
        values.append(value)
    return keys, values

def argsorter(s):
    return sorted(range(len(s)), key = lambda k: s[k])


######## ARRAY UTILITIES BELOW

def copy_zarray(zarray):
    copied = zarr.zeros_like(zarray)
    copied[:] = zarray[:]
    return copied

def copy_array(array):
    if isinstance(array, zarr.Array):
        copied = copy_zarray(array)
    elif isinstance(array, (da.Array, np.array)):
        copied = array.copy()
    return copied

def insert_zarray(collection,
                  axis: int = 0
                  ):
    zarray = copy_zarray(collection[0])
    for z in collection[1:]:
        zarray.append(z, axis = axis)
    return zarray

def is_zarr_array(path: (str, Path)
                  ):
    try:
        _ = zarr.open_array(path, mode = 'r')
        return True
    except:
        return False

######## GROUP UTILITIES BELOW
def is_zarr_group(path: (str, Path)
                  ):
    try:
        _ = zarr.open_group(path, mode = 'r')
        return True
    except:
        return False

def is_generic_collection(group):
    res = False
    basepath = group.store.path
    basename = os.path.basename(basepath)
    paths = list(group.keys())
    attrs = dict(group.attrs)
    attrkeys, attrvalues = transpose_dict(attrs)
    if basename in attrkeys and (len(paths) > 0):
        if len(attrs[basename]) == len(paths):
            res = True
            for item0, item1 in zip(attrs[basename], paths):
                if item0 != item1:
                    res = False
    return res

def get_collection_paths(directory,
                         return_all = False
                         ):
    gr = zarr.group(directory)
    groupkeys = list(gr.group_keys())
    arraykeys = list(gr.array_keys())
    grouppaths = [os.path.join(directory, item) for item in groupkeys]
    arraypaths = [os.path.join(directory, item) for item in arraykeys]
    collection_paths = []
    multiscales_paths = []
    while len(grouppaths) > 0:
        if is_generic_collection(gr) or 'bioformats2raw.layout' in gr.attrs:
            collection_paths.append(directory)
        if 'multiscales' in list(gr.attrs.keys()):
            multiscales_paths.append(directory)
        directory = grouppaths[0]
        grouppaths.pop(0)
        gr = zarr.group(directory)
        groupkeys = list(gr.group_keys())
        arraykeys = list(gr.array_keys())
        grouppaths += [os.path.join(directory, item) for item in groupkeys]
        arraypaths += [os.path.join(directory, item) for item in arraykeys]
    if is_generic_collection(gr) or 'bioformats2raw.layout' in gr.attrs:
        collection_paths.append(directory)
    if 'multiscales' in list(gr.attrs.keys()):
        multiscales_paths.append(directory)
    out = [item for item in collection_paths]
    for mpath in multiscales_paths:
        s = os.path.dirname(mpath)
        if s in collection_paths:
            pass
        else:
            if mpath not in out:
                out.append(mpath)
    if return_all:
        return out, multiscales_paths, arraypaths
    return out

def convert_np_types(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    else:
        return obj

def is_valid_json(my_dict):
    try:
        json.dumps(my_dict)
        return True
    except:
        warnings.warn(f"Object is not a valid json!")
        return False

def turn2json(my_dict):
    stringified = json.dumps(my_dict, default = convert_np_types)
    return json.loads(stringified)

def as_store(store: (zarr.storage.Store, zarr.Array, Path, str) # TODO: other scenarios?
                ):
    assert isinstance(store, (zarr.storage.Store, zarr.Array, Path, str)), f"The given store cannot be parsed."
    if isinstance(store, (Path, str)):
        out = zarr.DirectoryStore(store, dimension_separator = '/')
    else:
        out = store
    return out

def as_dask_array(array: (da.Array, zarr.Array, np.ndarray,
                          # cp.ndarray
                          ),
                  backend = 'numpy',
                  **params
                  ):
    assert isinstance(array, (da.Array, zarr.Array, np.ndarray, cp.ndarray)), f"The given array type {type(array)} cannot be parsed."
    assert backend in ('numpy', 'cupy'), f"Currently, the only supported backends are 'numpy' or 'cupy'."
    if isinstance(array, zarr.Array):
        out = da.from_zarr(array, **params)
    elif isinstance(array, (np.ndarray, cp.ndarray)):
        out = da.from_array(array, **params)
    elif isinstance(array, da.Array):
        # out = da.array(array, **params)
        out = array
    if backend == 'cupy':
        out = out.map_blocks(cp.asarray)
    return out


#########################################################

def get_array_size(array, as_str = True):
    voxelcount = np.prod(array.shape)
    arraysize = voxelcount * array.dtype.itemsize
    if as_str:
        return f"{arraysize / 1024 ** 3}GB"
    else:
        return arraysize

def sizeof(array, unit = 'gb'):
    unit = unit.lower()
    assert unit in ('gb', 'mb', 'kb')
    bytes = get_array_size(array, False)
    if unit == 'gb':
        ret = bytes / 1024 ** 3
    elif unit == 'mb':
        ret = bytes / 1024 ** 2
    elif unit == 'kb':
        ret = bytes / 1024 ** 1
    else:
        ret = bytes
    return ret

def get_chunksize_from_array(arr):
    chunks = arr.chunksize if isinstance(arr, da.Array) else arr.chunks
    itemsize = arr.dtype.itemsize
    chunk_size = itemsize * np.prod(chunks)
    chunk_size_gb = chunk_size / (1024 ** 3)
    return f'{chunk_size_gb * 1.1}GB'

