# EuBI-Bridge  

EuBI-Bridge is a tool for distributed conversion of microscopic image collections into the OME-Zarr (v0.4) format. It can be used from the command line or as part of a Python script, making it easy to integrate into existing workflows.  

A key feature of EuBI-Bridge is **aggregative conversion**, which concatenates multiple images along specified dimensions—particularly useful for handling large datasets stored as TIFF file collections.  

EuBI-Bridge is built on several powerful libraries, including `zarr`, `aicsimageio`, `dask-distributed`, and `rechunker`, among others. 
While a variety of input file formats are supported, testing has so far primarily focused on TIFF files.


## Installation

EuBI-Bridge can be installed via pip or conda. 

### Installation via pip

```bash
pip install eubi-bridge
```

### Installation via conda

```bash
conda install -c euro-bioimaging -c conda-forge eubi-bridge
```



## Basic Usage  

### Unary Conversion  

Given a dataset structured as follows: 

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

To convert each TIFF into a separate OME-Zarr container (unary conversion):  

```bash
eubi to_zarr multichannel_timeseries multichannel_timeseries_zarr
```  

This produces:  

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

Use **wildcards** to specifically convert the images belonging to Channel1:

```bash
eubi to_zarr "multichannel_timeseries/Channel1*" multichannel_timeseries_channel1_zarr
```

### Aggregative Conversion (Concatenation Along Dimensions)  

To concatenate images along specific dimensions, EuBI-Bridge needs to be informed
of file patterns that specify image dimensions. For this example,
the file pattern for the channel dimension is `Channel`, which is followed by the channel index,
and the file pattern for the time dimension is `T`, which is followed by the time index.

To concatenate along the **time** dimension:

```bash
eubi to_zarr multichannel_timeseries multichannel_timeseries_concat_zarr \
--channel_tag Channel \
--time_tag T \
--concatenation_axes t
```  

Output:  

```bash
multichannel_timeseries_time-concat_zarr
├── Channel1-T_tset.zarr
└── Channel2-T_tset.zarr
```  

**Important note:** if the `--channel_tag` was not provided, the tool would not be aware
of the multiple channels in the image and try to concatenate all images into a single one-channeled OME-Zarr. Therefore, 
when an aggregative conversion is performed, all dimensions existing in the input files must be specified via their respective tags. 

For multidimensional concatenation (**channel** + **time**):

```bash
eubi to_zarr multichannel_timeseries multichannel_timeseries_concat_zarr \
--channel_tag Channel \
--time_tag T \
--concatenation_axes ct
```  

Note that both axes are specified wia the argument `--concatenation_axes ct`.

Output:

```bash
multichannel_timeseries_concat_zarr
└── Channel_cset-T_tset.zarr
```  

### Handling Nested Directories  

For datasets stored in nested directories such as:  

```bash
multichannel_timeseries_nested
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

EuBI-Bridge automatically detects the nested structure. To concatenate along both channel and time dimensions:  

```bash
eubi to_zarr \
multichannel_timeseries_nested \
multichannel_timeseries_nested_concat_zarr \
--channel_tag Channel \
--time_tag T \
--concatenation_axes ct
```  

Output:  

```bash
multichannel_timeseries_nested_concat_zarr
└── Channel_cset-T_tset.zarr
```  

To concatenate along the channel dimension only:  

```bash
eubi to_zarr \
multichannel_timeseries_nested \
multichannel_timeseries_nested_concat_zarr \
--channel_tag Channel \
--time_tag T \
--concatenation_axes c
```  

Output:  

```bash
multichannel_timeseries_nested_concat_zarr
├── Channel_cset-T0001.zarr
├── Channel_cset-T0002.zarr
├── Channel_cset-T0003.zarr
└── Channel_cset-T0004.zarr
```  

### Selective Data Conversion    

To recursively select specific files for conversion, wildcard patterns can be used. 
For example, to concatenate only **timepoint 3** along the channel dimension:  

```bash
eubi to_zarr \
"multichannel_timeseries_nested/**/*T0003*" \
multichannel_timeseries_nested_concat_zarr \
--channel_tag Channel \
--time_tag T \
--concatenation_axes c
```  

Output:  

```bash
multichannel_timeseries_nested_concat_zarr
└── Channel_cset-T0003.zarr
```  

**Note:** When using wildcards, the input directory path must be enclosed 
in quotes as shown in the example above.  

### Handling Categorical Dimension Patterns  

For datasets where channel names are categorical such as in:

```bash
blueredchannel_timeseries
├── Blue-T0001.tif
├── Blue-T0002.tif
├── Blue-T0003.tif
├── Blue-T0004.tif
├── Red-T0001.tif
├── Red-T0002.tif
├── Red-T0003.tif
└── Red-T0004.tif
```

Specify categorical names as a comma-separated list:  

```bash
eubi to_zarr \
blueredchannels_timeseries \
blueredchannels_timeseries_concat_zarr \
--channel_tag Blue,Red \
--time_tag T \
--concatenation_axes ct
```  

Output:  

```bash
blueredchannels_timeseries_concat_zarr
└── BlueRed_cset-T_tset.zarr
```  

Note that the categorical names are aggregated in the output OME-Zarr name.  


With nested input structure such as in:  

```bash
blueredchannels_timeseries_nested
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

One can run the exact same command:

```bash
eubi to_zarr \
blueredchannels_timeseries_nested \
blueredchannels_timeseries_nested_concat_zarr \
--channel_tag Blue,Red \
--time_tag T \
--concatenation_axes ct
```  

Output:  

```bash
blueredchannels_timeseries_nested_concat_zarr
└── BlueRed_cset-T_tset.zarr
```

## Additional Notes

- EuBI-Bridge is in the **alpha stage**, and significant updates may be expected.
- **Community support:** Questions and contributions are welcome! Please report any issues.

