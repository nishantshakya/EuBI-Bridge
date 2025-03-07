# EuBI-Bridge

EuBI-Bridge is a package for distributed conversion of collections of microscopic 
image data to the OME-Zarr format.

EuBI-Bridge can be run from the command line or as part of a Python script,
the latter enabling it to be easily incorporated into Python workflows.

A particular feature of EuBI-Bridge is the "aggregative" conversion, which 
concatenates multiple images along specified dimensions. This is particularly 
useful for converting big datasets, which are often stored in collections 
of TIFF files.

## Basic usage

### Unary conversion

Imagine we have a multichannel timeseries dataset as follows:

```bash
multichannel_timeseries
├── Channel1-T0001.tif
├── Channel1-T0002.tif
├── Channel1-T0003.tif
├── Channel1-T0004.tif
├── Channel2-T0001.tif
├── Channel2-T0002.tif
├── Channel2-T0003.tif
└── Channel2-T0004.tif
```
If we want to perform an unary conversion, i.e., convert each of these TIFFs to separate OME-Zarrs, 
we can run the following command:

```bash
eubi to_zarr multichannel_timeseries multichannel_timeseries_zarr
```

This will result in a directory like:

```bash
multichannel_timeseries_zarr
├── Channel1-T0001.zarr
├── Channel1-T0002.zarr
├── Channel1-T0003.zarr
├── Channel1-T0004.zarr
├── Channel2-T0001.zarr
├── Channel2-T0002.zarr
├── Channel2-T0003.zarr
└── Channel2-T0004.zarr
```

Each of the output folder is an independent OME-Zarr container.

### Aggregative conversion (concatenate along dimensions)

If we want to concatenate the input files along certain dimensions, we need to
inform EuBI-Bridge about the image dimensionality using file patterns. For this example,
the file pattern for the channel dimension is 'Channel', which is followed by the channel index,
and the file pattern for the time dimension is 'T', which is followed by the time index. We 
need to provide this information to the tool using the command line arguments `--channel_tag` 
and `--time_tag`. **Note that even if we want to concatenate along only one dimension,
we still need to inform the tool of all the dimensional groups existing in the input dataset by 
providing the relevant tags (time and channel in this example). Providing only the 
tag for the dimension of concatenation will lead to wrong results.** 

In the example below, we want to concatenate the files along the time dimension only.

```bash
eubi to_zarr multichannel_timeseries multichannel_timeseries_concat_zarr \
--channel_tag Channel \
--time_tag T \
--concatenation_axes t
```

This will result in a directory like this:

```bash
multichannel_timeseries_time-concat_zarr
├── Channel1-Tset.zarr
└── Channel2-Tset.zarr
```

Since we concatenated the files along only the time dimension, we now have two OME-Zarr containers 
in the output, namely the `Channel1-Tset.zarr` and `Channel2-Tset.zarr`. In the output folder 
names, the time tag, `T` is joined with the string `set` to indicate that the 
data is an output of concatenation along this dimension. **An important point here is that 
if we did not provide the`--channel_tag`, the tool would not know that there are
different channels and would try to concatenate all images into a single OME-Zarr 
container.**

If we want to perform a multi-dimensional concatenation, we should specify the `--concatenation_axes` parameter
accordingly, as indicated below:

```bash
eubi to_zarr multichannel_timeseries multichannel_timeseries_concat_zarr \
--channel_tag Channel \
--time_tag T \
--concatenation_axes ct
```

Note that now we specified both channel and time via `--concatenation_axes ct`. This will result in an 
output directory like this:

```bash
multichannel_timeseries_concat_zarr
└── Channelset-Tset.zarr
```

The folder ```Channelset-Tset.zarr``` is an OME-Zarr representing multidimensional concatenation of all TIFF 
files in the `multichannel_timeseries` directory. Note that now both the time and channel tags in 
the resulting OME-Zarr's name are attached with the string `set` since the concatenation
has been along both of these dimensions.


### Converting nested directories

Quite often, large datasets are organized in nested directories. For instance, the example
dataset we covered above could also be represented like this:

```bash
/multichannel_timeseries_nested
├── Channel1
│   ├── T0001.tif
│   ├── T0002.tif
│   ├── T0003.tif
│   ├── T0004.tif
├── Channel2
│   ├── T0001.tif
│   ├── T0002.tif
│   ├── T0003.tif
│   ├── T0004.tif
```
 
EuBI-Bridge is aware of nested directories by default. The dataset above can be
converted using the exact same commands as in the previous examples:

```bash
eubi to_zarr \
multichannel_timeseries_nested \
multichannel_timeseries_nested_concat_zarr \
--channel_tag Channel \
--time_tag T \
--concatenation_axes ct
```

This will lead to:

```bash
multichannel_timeseries_nested_concat_zarr
└── Channelset-Tset.zarr
```

where `Channelset-Tset.zarr` is an OME-Zarr representing the multidimensionally concatenated data.
Note that conversion process flattens nested directories, replacing the path separator `/` 
with a `-`. Therefore, all output OME-Zarr containers are listed in a single directory. 

In the same direction, one can also specify individual dimensions in the nested 
directories. For instance, to concatenate only along channels, one can do:

```bash
eubi to_zarr \
multichannel_timeseries_nested \
multichannel_timeseries_nested_concat_zarr \
--channel_tag Channel \
--time_tag T \
--concatenation_axes c
```

which will result in:

```bash
multichannel_timeseries_nested_concat_zarr
├── Channelset-T0001.zarr
├── Channelset-T0002.zarr
├── Channelset-T0003.zarr
└── Channelset-T0004.zarr
```

### Input data selection via globbing or command line arguments

While the default behaviour of EuBI-Bridge is to recurse the entire nested directories
from the input path, one can also use globbing to specifically select the data 
to be converted. 

Consider the same dataset:

```bash
/multichannel_timeseries_nested
├── Channel1
│   ├── T0001.tif
│   ├── T0002.tif
│   ├── T0003.tif
│   ├── T0004.tif
├── Channel2
│   ├── T0001.tif
│   ├── T0002.tif
│   ├── T0003.tif
│   ├── T0004.tif
```

For instance, we only want to select the timepoint 3 from both channels and
concatenate them along the channel dimension into a single OME-Zarr container. 
Here is how one can do it:

```bash
eubi to_zarr \
"multichannel_timeseries_nested/**/*T0003*" \
multichannel_timeseries_nested_concat_zarr \
--channel_tag Channel \
--time_tag T \
--concatenation_axes c
```

which will lead to 

```bash
multichannel_timeseries_nested_concat_zarr
└── Channelset-T0003.zarr
```

Also note that the input path must be in quotes when globbing is used.


### Conversion of files with categorical patterns in filepaths

The examples above demonstrate cases where the dimension indices are represented in the form of 
numerical values following a pattern, such as `Channel1`, `Channel2` or `T0`, `T1`, etc. However, 
in certain cases dimensions are simply specified with categorical values such as `Cyan`, `Magenta`, etc.,
This is indeed quite common for channel dimensions. See the following example folder structure:

```bash
multichannel_timeseries_nested
├── Blue
│   ├── T0001.tif
│   ├── T0002.tif
│   ├── T0003.tif
│   └── T0004.tif
└── Red
    ├── T0001.tif
    ├── T0002.tif
    ├── T0003.tif
    └── T0004.tif
```

In contrast to the case, where the channels were specified with numerical indices such as `Channel1` and
`Channel2`, now we have categorical names `Blue` and `Red`. Such categorical names are represented as a comma
separated values in the command, as shown below:

```bash
eubi to_zarr \
multichannel_timeseries_nested \
multichannel_timeseries_nested_concat_zarr \
--channel_tag Blue,Red \
--time_tag T \
--concatenation_axes ct
```

which will lead to:

```bash
multichannel_timeseries_nested_concat_zarr
└── BlueRedset-Tset.zarr
```

Note that the process aggregates the categorical names in the output OME-Zarr's name. 