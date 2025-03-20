import os, itertools, tempfile, shutil, threading
import zarr, dask, numcodecs
from dask import delayed
import rechunker
from rechunker import rechunk, Rechunked
import dask.array as da
# from dask.diagnostics import ProgressBar
import numpy as np
# import tensorstore as ts
from pathlib import Path
from typing import List, Tuple, Dict, Union, Any, Tuple
### internal imports
from eubi_bridge.ngff.multiscales import Multimeta
from eubi_bridge.utils.convenience import get_chunksize_from_array, is_zarr_group

import logging, warnings

logging.getLogger('distributed.diskutils').setLevel(logging.CRITICAL)

def create_zarr_array(directory: Union[Path, str, zarr.Group],
                      array_name: str,
                      shape: Tuple[int, ...],
                      chunks: Tuple[int, ...],
                      dtype: Any,
                      overwrite: bool = False) -> zarr.Array:
    chunks = tuple(np.minimum(shape, chunks))

    if not isinstance(directory, zarr.Group):
        path = os.path.join(directory, array_name)
        dataset = zarr.create(shape=shape,
                              chunks=chunks,
                              dtype=dtype,
                              store=path,
                              dimension_separator='/',
                              overwrite=overwrite)
    else:
        dataset = directory.create(name=array_name,
                                   shape=shape,
                                   chunks=chunks,
                                   dtype=dtype,
                                   dimension_separator='/',
                                   overwrite=overwrite)
    return dataset


def get_regions(array_shape: Tuple[int, ...],
                region_shape: Tuple[int, ...],
                as_slices: bool = False) -> list:
    assert len(array_shape) == len(region_shape)
    steps = []
    for size, inc in zip(array_shape, region_shape):
        seq = np.arange(0, size, inc)
        if size > seq[-1]:
            seq = np.append(seq, size)
        increments = tuple((seq[i], seq[i + 1]) for i in range(len(seq) - 1))
        if as_slices:
            steps.append(tuple(slice(*item) for item in increments))
        else:
            steps.append(increments)
    return list(itertools.product(*steps))

def get_compressor(name, **params):
    name = name.lower()
    compression_dict = {
        "blosc": "Blosc",
        "bz2": "BZ2",
        "gzip": "GZip",
        "lzma": "LZMA",
        "lz4": "LZ4",
        "pcodec": "PCodec",
        "zfpy": "ZFPY",
        "zlib": "Zlib",
        "zstd": "Zstd"
    }

    compressor_name = compression_dict[name]
    compressor_class = getattr(numcodecs, compressor_name)
    compressor = compressor_class(**params)
    return compressor

def get_default_fill_value(dtype):
    if np.issubdtype(dtype, np.integer):
        return 0
    elif np.issubdtype(dtype, np.floating):
        return 0.0
    elif np.issubdtype(dtype, np.bool_):
        return False
    return None

def write_chunk_with_zarrpy(chunk: np.ndarray, zarr_array: zarr.Array, block_info: Dict) -> None:
    zarr_array[tuple(slice(*b) for b in block_info[0]["array-location"])] = chunk

def write_chunk_with_tensorstore(chunk: np.ndarray, ts_store, block_info: Dict) -> None:
    ts_store[tuple(slice(*b) for b in block_info[0]["array-location"])] = chunk

def write_with_rechunker(arr: da.Array,
                         chunks: Tuple[int, ...],
                         location: Union[str, Path],
                         overwrite: bool = True,
                         **kwargs) -> Rechunked:
    temp_dir = kwargs.get('temp_dir')
    if not temp_dir:
        raise ValueError("A temp_dir must be specified.")

    temp_dir_is_auto = temp_dir == 'auto'
    if temp_dir_is_auto:
        temp_dir = tempfile.TemporaryDirectory()

    max_mem = kwargs.get('rechunkers_max_mem', "auto")
    if max_mem == "auto":
        max_mem = get_chunksize_from_array(arr)

    if overwrite:
        shutil.rmtree(location, ignore_errors=True)

    target_store = zarr.DirectoryStore(location, dimension_separator='/')
    temp_store = zarr.DirectoryStore(temp_dir.name if isinstance(temp_dir, tempfile.TemporaryDirectory) else temp_dir,
                                     dimension_separator='/')

    compressor_name = kwargs.get('compressor', 'blosc')
    compressor_params = kwargs.get('compressor_params', {})
    compressor = get_compressor(compressor_name, **compressor_params)

    dtype = kwargs.get('dtype', arr.dtype)
    if dtype == 'auto':
        dtype = arr.dtype

    fill_value = kwargs.get('fill_value', get_default_fill_value(dtype))

    # Use rechunker (without fill_value)
    rechunked = rechunk(source=arr,
                        target_chunks=chunks,
                        target_store=target_store,
                        temp_store=temp_store,
                        max_mem=max_mem,
                        executor='dask',
                        target_options={'overwrite': True,
                                        'compressor': compressor,
                                        'write_empty_chunks': True})  # No fill_value here

    # **Reopen Zarr array and update fill_value properly**
    zarr_array = zarr.open_array(target_store, mode="a")  # Open in append mode
    zarr_array.fill_value = fill_value  # Set fill_value correctly

    # Cleanup temporary directory if auto-generated
    if temp_dir_is_auto:
        temp_dir.cleanup()

    return rechunked

def write_with_zarrpy(arr: da.Array,
                      chunks: Tuple[int, ...],
                      location: Union[str, Path],
                      overwrite: bool = True,
                      **kwargs) -> da.Array:
    rechunk_method = kwargs.get('rechunk_method', 'tasks')

    if not np.equal(arr.chunksize, chunks).all():
        arr = arr.rechunk(chunks, method=rechunk_method #, threshold = 1_000_000
        )

    store = zarr.DirectoryStore(location, dimension_separator='/')
    try:
        zarr_array = zarr.open_array(location, mode='w')
    except:
        compressor_name = kwargs.get('compressor', 'blosc')
        compressor_params = kwargs.get('compressor_params', {})
        compressor = get_compressor(compressor_name, **compressor_params)
        dtype = kwargs.get('dtype', arr.dtype)
        if dtype == 'auto':
            dtype = arr.dtype

        fill_value = kwargs.get('fill_value', get_default_fill_value(dtype))

        zarr_array = zarr.create(shape=arr.shape, chunks=chunks, dtype=dtype, compressor = compressor, store=store, overwrite=overwrite, fill_value = fill_value)

    return arr.map_blocks(write_chunk_with_zarrpy, zarr_array=zarr_array, dtype=dtype)


def write_with_tensorstore(arr: da.Array,
                           chunks: Tuple[int, ...],
                           location: Union[str, Path],
                           overwrite: bool = True,
                           **kwargs) -> da.Array:

    try:
        import tensorstore as ts
    except:
        raise ModuleNotFoundError(f"The module tensorstore has not been found. Try 'conda install -c conda-forge tensorstore'")
    rechunk_method = kwargs.get('rechunk_method', 'tasks')

    compressor_name = kwargs.get('compressor', 'blosc')
    compressor_params = kwargs.get('compressor_params', {})
    compressor = dict(id = compressor_name, **compressor_params)
    dtype = kwargs.get('dtype', arr.dtype)
    fill_value = kwargs.get('fill_value', get_default_fill_value(dtype))

    if dtype == 'auto':
        dtype = arr.dtype

    zarr_spec = {
        "driver": "zarr",
        "kvstore": {
            "driver": "file",
            "path": location,
        },
        "metadata": {
            "dtype": dtype.str,
            "shape": arr.shape,
            "chunks": chunks,
            "compressor": compressor,
            "dimension_separator": "/",
            "fill_value": fill_value
        },
    }

    if not np.equal(arr.chunksize, chunks).all():
        arr = arr.rechunk(chunks, method=rechunk_method)

    ts_store = ts.open(zarr_spec, create=True, delete_existing=overwrite).result()
    return arr.map_blocks(write_chunk_with_tensorstore, ts_store=ts_store, dtype=arr.dtype)

def write_with_dask(arr: da.Array,
                    chunks: Tuple[int, ...],
                    location: Union[str, Path],
                    overwrite: bool = True,
                    **kwargs: Any
                    ) -> List[da.Array]:
    rechunk_method: str = kwargs.get('rechunk_method', 'tasks')

    if not np.equal(arr.chunksize, chunks).all():
        res: da.Array = arr.rechunk(chunks, method=rechunk_method)
    else:
        res: da.Array = arr

    store: zarr.DirectoryStore = zarr.DirectoryStore(location, dimension_separator='/')
    try:
        zarr_array: zarr.Array = zarr.open_array(location, mode='r')
    except:
        compressor_name = kwargs.get('compressor', 'blosc')
        compressor_params = kwargs.get('compressor_params', {})
        compressor = get_compressor(compressor_name, **compressor_params)
        dtype = kwargs.get('dtype', arr.dtype)
        if dtype == 'auto':
            dtype = arr.dtype
        fill_value = kwargs.get('fill_value', get_default_fill_value(dtype))
        zarr_array = zarr.create(shape=res.shape, chunks=chunks, dtype=dtype, compressor=compressor, store=store,
                                 overwrite=overwrite, fill_value = fill_value)

    region_shape: Tuple[int, ...] = kwargs.get('region_shape', chunks)
    regions: List[Tuple[slice, ...]] = get_regions(arr.shape, region_shape, as_slices=True)
    result: List[da.Array] = []
    for slc in regions:
        res: da.Array = da.to_zarr(
            arr=arr[slc],
            region=slc,
            url=zarr_array,
            compute=False,
        )
        result.append(res)

    return result

@delayed
def count_threads():
    return threading.active_count()

def store_arrays(arrays: Dict[str, Dict[str, da.Array]],
                 output_path: Union[Path, str],
                 scales: Dict[str, Dict[str, Tuple[float, ...]]],
                 units: list,
                 output_chunks: Tuple[int, ...] = None,
                 compute: bool = False,
                 overwrite: bool = False,
                 **kwargs) -> Dict[str, da.Array]:

    rechunk_method = kwargs.get('rechunk_method', 'tasks')
    use_tensorstore = kwargs.get('use_tensorstore', False)
    verbose = kwargs.get('verbose', False)

    arrays = {k: {'0': v} if not isinstance(v, dict) else v for k, v in arrays.items()}
    flatarrays = {os.path.join(output_path, f"{key}.zarr" if not key.endswith('zarr') else key, str(level)): arr
                  for key, subarrays in arrays.items()
                  for level, arr in subarrays.items()}
    flatscales = {os.path.join(output_path, f"{key}.zarr" if not key.endswith('zarr') else key, str(level)): scale
                  for key, subscales in scales.items()
                  for level, scale in subscales.items()}

    if rechunk_method == 'rechunker':
        writer_func = write_with_rechunker
        if use_tensorstore:
            raise NotImplementedError("The rechunker method cannot be used with tensorstore.")
        if 'region_shape' in kwargs:
            raise NotImplementedError("The rechunker method is not compatible with region-based writing.")
    elif 'region_shape' in kwargs:
        writer_func = write_with_dask
        if use_tensorstore:
            raise NotImplementedError("Region-based writing is not possible with tensorstore.")
    else:
        writer_func = write_with_tensorstore if use_tensorstore else write_with_zarrpy

    try:
        zarr.group(output_path, overwrite=overwrite)
        results = {}
        for key, arr in flatarrays.items():
            flatscale = flatscales[key]
            # Make sure chunk size is not larger than array shape in any dimension.
            chunks = np.minimum(output_chunks or arr.chunksize, arr.shape)

            if rechunk_method in (None, 'auto'):
                if np.all(np.less_equal(chunks, arr.chunksize)):
                    rechunk_method = 'rechunker'
                    kwargs['rechunk_method'] = rechunk_method
                    writer_func = write_with_rechunker
                    if use_tensorstore:
                        raise NotImplementedError("The rechunker method cannot be used with tensorstore.")
                    if 'region_shape' in kwargs:
                        raise NotImplementedError("The rechunker method is not compatible with region-based writing.")
                else:
                    kwargs['rechunk_method'] = 'tasks'

            if rechunk_method != 'rechunker':
                if 'temp_dir' in kwargs:
                    kwargs.pop('temp_dir')

            dirpath = os.path.dirname(key)
            arrpath = os.path.basename(key)

            gr = zarr.open_group(dirpath, mode='a') if is_zarr_group(dirpath) else zarr.group(dirpath, overwrite=overwrite)

            meta = Multimeta()
            try:
                meta.from_ngff(gr)
            except:
                pass
            if not meta.has_axes:
                meta.parse_axes(axis_order='tczyx', unit_list=units)

            meta.add_dataset(path=arrpath, scale=flatscale, overwrite=True)
            meta.retag(os.path.basename(dirpath))
            meta.to_ngff(gr)

            if verbose:
                print(f"Writer function: {writer_func}")
                print(f"Rechunk method: {rechunk_method}")
            results[key] = writer_func(arr=arr,
                                       chunks=chunks,
                                       location=key, # compressor = compressor, dtype = dtype,
                                       overwrite=overwrite,
                                       **kwargs
                                       )

        if compute:
            if rechunk_method == 'rechunker':
                for result in results.values():
                    result.execute()
            else:
                dask.compute(list(results.values()))
        else:
            return results
    except Exception as e:
    #     print(e)
        pass
    return results





