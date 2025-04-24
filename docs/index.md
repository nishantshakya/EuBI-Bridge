# EuBI-Bridge

EuBI-Bridge is a Python-based command line tool for distributed conversion of microscopy datasets into the OME-Zarr (v0.4) format with optional downscaling.  

A key feature of EuBI-Bridge is **aggregative conversion**, which concatenates multiple images along specified dimensions—particularly useful for handling large datasets stored as file collections.  

EuBI-Bridge is built on several powerful libraries, including `zarr`, `aicsimageio`, `dask-distributed`, and `rechunker`, among others. 
While a variety of input file formats are supported, testing has so far primarily focused on TIFF files.

---

## Key Features

- Parallelised batch conversion
- Conversion with multi-dimensional concatenation
- Cluster-based conversion
- N-dimensional chunking
- N-dimensional downscaling
- OME-XML metadata export

---

## Installation

EuBI-Bridge can be installed via conda:

```bash
conda install -c euro-bioimaging -c conda-forge eubi-bridge
```

> ℹ️ **EuBI-Bridge is currently only compatible with Python 3.10 due to conflicting dependencies. We are working on supporting a wider range of Python versions in future releases.**



## Additional Notes

- EuBI-Bridge is in the **alpha stage**, and significant updates may be expected.
- **Community support:** Questions and contributions are welcome! Please report any issues.

