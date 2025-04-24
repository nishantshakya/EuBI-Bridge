
# CLI Usage

After installing **EuBI-Bridge**, the CLI command `eubi` becomes globally available.

---

## Quick Start

### Unary vs aggregative conversion

**Unary conversion:** conversion of each input file to a single output OME-Zarr container.

**Aggregative conversion:** conversion that concatenates multiple input files along user-specified dimensions. 
 

Below are examples for both of these conversion modes:

### Examples

**Simple unary conversion:**

Convert each file in `input_dir` into an OME-Zarr container, saving the result in `output_dir`:

```bash
eubi to_zarr /path/to/input_dir /path/to/output_dir
```

**Excluding files:**

Exclude files with `thumbs` in the filename:

```bash
eubi to_zarr /path/to/input_dir /path/to/output_dir --excludes 'thumbs'
```

**Wildcard filtering:**

Convert only `tiff` files using wildcards:

```bash
eubi to_zarr "/path/to/input_dir/*tiff" /path/to/output_dir
```

**Aggregative conversion:**

Perform an aggregative conversion that concatenates input files along the z axis:

```bash
eubi to_zarr /path/to/input_dir /path/to/output_dir --z_tag slice_ --concatenation_axes z
```

Note that the pattern corresponding to the z axis is provided to the command via the `--z_tag` and the concatenation is activated by supplying `--concatenation_axes`. 

> ℹ️ To better understand aggregative conversion, see the [conversion tutorial](conversion_tutorial.md#tutorial).


---

---

## Main Commands

### `eubi to_zarr`

Performs data conversion from supported input formats (including most BioFormats-compatible formats) to OME-Zarr. It supports both **unary** and **aggregative** conversion modes, with options for filtering, metadata specification, downscaling, and distributed processing.

---

### Non-configurable Parameters

These must be provided directly via the CLI:

| Argument               | Type                        | Description                     |
|------------------------|-----------------------------|---------------------------------|
| `input_path`           | `str` or `Path` (mandatory) | Path to input file or folder    |
| `output_path`          | `str` or `Path` (mandatory) | Path to output Zarr directory   |
| `--includes`           | `str`                       | Include filter for filenames    |
| `--excludes`           | `str`                       | Exclude filter for filenames    |
| `--series`             | `int`                       | BioFormats series index         |
| `--time_tag`           | `str` or `tuple`            | Time dimension tag              |
| `--channel_tag`        | `str` or `tuple`            | Channel dimension tag           |
| `--z_tag`              | `str` or `tuple`            | Z-dimension tag                 |
| `--y_tag`              | `str` or `tuple`            | Y-dimension tag                 |
| `--x_tag`              | `str` or `tuple`            | X-dimension tag                 |
| `--concatenation_axes` | `int`, `tuple`, or `str`    | Axes for concatenating datasets |
| `--time_scale`         | `int`, `float`              | Temporal increment              |
| `--z_scale`            | `int`, `float`              | Z spatial increment             |
| `--y_scale`            | `int`, `float`              | Y spatial increment             |
| `--x_scale`            | `int`, `float`              | X spatial increment             |
| `--time_unit`          | `str`                       | Temporal unit                   |
| `--z_unit`             | `str`                       | Z spatial unit                  |
| `--y_unit`             | `str`                       | Y spatial unit                  |
| `--x_unit`             | `str`                       | X spatial unit                  |

#### Examples

**Override metadata for unary conversion:**

```bash
eubi to_zarr /path/to/input_dir /path/to/output_dir --z_scale 2.5 --z_unit micrometer --time_scale 1.5 --time_unit second
```

**Perform aggregative conversion along the time and channel axes:**

```bash
eubi to_zarr /path/to/input_dir /path/to/output_dir --time_tag T --channel_tag Channel --concatenation_axes tc
```

> ℹ️ Tag arguments corresponding to the axes `tczyx` are: `--time_tag`, `--channel_tag`, `--z_tag` `--y_tag`, `--x_tag`, respectively.

> ℹ️ To better understand aggregative conversion, see the [conversion tutorial](conversion_tutorial.md#tutorial).

---

---

### Configurable Parameters

These can be passed via CLI or stored in the configuration file. 

#### Cluster Parameters

| Argument               | Type     | Description                                        |
|------------------------|----------|----------------------------------------------------|
| `--memory_limit`       | `str`    | Maximum memory per Dask worker                    |
| `--n_jobs`             | `int`    | Number of Dask workers                            |
| `--no_distributed`     | `bool`   | Disable distributed computation                   |
| `--no_worker_restart`  | `bool`   | Prevent automatic worker restarts on failure      |
| `--on_slurm`           | `bool`   | Enable SLURM-based execution                      |
| `--temp_dir`           | `str`    | Temporary directory for Dask                      |
| `--threads_per_worker` | `int`    | Threads per worker                                |
| `--verbose`            | `bool`   | Enable verbose logging                            |

#### Conversion Parameters

| Parameter                | Type     | Description                                          |
|--------------------------|----------|------------------------------------------------------|
| `--compressor`           | `str`    | Compression algorithm                                |
| `--compressor_params`    | `dict`   | Compressor parameters                                |
| `--output_chunks`        | `list`   | Output Zarr chunk size                               |
| `--overwrite`            | `bool`   | Overwrite existing Zarr data                         |
| `--rechunk_method`       | `str`    | Rechunking method (`tasks`, `p2p` or `rechunker`)    |
| `--rechunkers_max_mem`   | `str`    | Max memory for `rechunker`                           |
| `--trim_memory`          | `bool`   | Reduce memory usage                                  |
| `--use_tensorstore`      | `bool`   | Use TensorStore backend for writing                  |
| `--metadata_reader`      | `str`    | Metadata extraction method (`bfio` or `aicsimageio`) |
| `--save_omexml`          | `bool`   | Save OME-XML metadata                                |

#### Downscale Parameters

| Parameter            | Type     | Description                                  |
|----------------------|----------|----------------------------------------------|
| `--downscale_method` | `str`    | Downscale algorithm (`simple`, `mean`, etc.) |
| `--n_layers`         | `int`    | Number of downscaling layers                 |
| `--scale_factor`     | `list`   | Scaling factors in each dimension            |

#### Examples

**Run with 8 workers and limit memory per worker:**

```bash
eubi to_zarr /path/to/input_dir /path/to/output_dir --n_jobs 8 --memory_limit 10GB
```

**Specify output chunk size:**

```bash
eubi to_zarr /path/to/input_dir /path/to/output_dir --output_chunks 1,1,128,128,128
```

**Specify downscaling layers and scale factor:**

```bash
eubi to_zarr /path/to/input_dir /path/to/output_dir --n_layers 6 --scale_factor 1,1,3,3,3
```

> ℹ️ For more examples, see the [conversion tutorial](conversion_tutorial.md#tutorial).

---

## Configuration Commands

### `eubi configure_cluster`

Set cluster defaults using any of the [cluster parameters](#cluster-parameters).

#### Example

```bash
eubi configure_cluster --memory_limit 10GB
```

---

### `eubi configure_conversion`

Set conversion defaults using any of the [conversion parameters](#conversion-parameters).

#### Example

```bash
eubi configure_conversion --rechunk_method p2p
```

---

### `eubi configure_downscale`

Set downscale defaults using any of the [downscale parameters](#downscale-parameters).

#### Example

```bash
eubi configure_downscale --scale_factor 1,1,2,2,2
```

---


## Reset and Inspect Configuration

| Command                        | Description                                                            |
|--------------------------------|------------------------------------------------------------------------|
| `eubi reset_config`            | Reset cluster/conversion/downscale parameters to installation defaults |
| `eubi reset_dask_config`       | Reset the `dask.distributed` settings                                  |
| `eubi show_config`             | Show current cluster/conversion/downscale settings                     |
| `eubi show_dask_config`        | Show current Dask configuration                                        |
| `eubi show_root_defaults`      | Show installation defaults for cluster/conversion/downscale parameters |
| `eubi show_root_dask_defaults` | Show installation defaults for Dask parameters                         |