import numpy as np
import warnings
import dask.array as da
import os
from typing import Optional, Dict, Any, Tuple, Union
import warnings
import dask.array as da

from eubi_bridge.utils.logging_config import get_logger
logger = get_logger(__name__)

def read_czi(
    input_path: str,
    as_mosaic: bool = False,
    view_index: int = 0,
    phase_index: int = 0,
    illumination_index: int = 0,
    scene_index: int = 0,
    rotation_index: int = 0,
    mosaic_tile_index: int = 0,
    sample_index: int = 0
) -> da.Array:
    """
    Read a CZI (Zeiss microscopy) file with specified dimension indices.

    Args:
        input_path: Path to the CZI file.
        as_mosaic: Whether to read as a mosaic (tiled) image.
        view_index: Index for the view dimension (v).
        phase_index: Index for the phase dimension (h).
        illumination_index: Index for the illumination dimension (i).
        scene_index: Index for the scene dimension (s).
        rotation_index: Index for the rotation dimension (r).
        mosaic_tile_index: Index for the mosaic tile dimension (m).
        sample_index: Index for the sample dimension (a).

    Returns:
        dask.array.Array: The image data with dimension order TCZYX.

    Raises:
        FileNotFoundError: If the input file doesn't exist.
        ValueError: If invalid dimension indices are provided.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"File not found: {input_path}")

    # Import the appropriate reader
    if as_mosaic:
        from bioio_czi.pylibczirw_reader.reader import Reader
    else:
        from bioio_czi.aicspylibczi_reader.reader import Reader

    # Initialize reader and get image metadata
    try:
        img = Reader(input_path)
    except Exception as e:
        raise RuntimeError(f"Failed to read CZI file: {str(e)}") from e

    # Handle scene selection
    if scene_index != 0:
        if not 0 <= scene_index < len(img.scenes):
            raise ValueError(
                f"Scene index {scene_index} is out of range. "
                f"Must be between 0 and {len(img.scenes) - 1}."
            )
        img.set_scene(scene_index)

    # Process non-standard dimensions
    nonstandard_dims = [
        dim.upper() for dim in img.standard_metadata.dimensions_present
        if dim.upper() not in {"X", "Y", "C", "T", "Z"}
    ]

    # Handle mosaic-specific logic
    if as_mosaic:
        if 'M' in nonstandard_dims:
            nonstandard_dims.remove('M')
        else:
            logger.warning(
                f"Mosaic tile dimension not found in {input_path}. "
                "Ignoring 'as_mosaic' parameter."
            )
            as_mosaic = False
        if mosaic_tile_index != 0:
            logger.warning(
                "Mosaic tile index is ignored when reading the entire mosaic. "
                "Set as_mosaic=False to read specific tiles."
            )

    # Map dimension indices
    czi_dim_map = {
        'V': view_index,
        'H': phase_index,
        'I': illumination_index,
        'S': scene_index,
        'R': rotation_index,
        'M': mosaic_tile_index,
        'A': sample_index
    }

    # Create index map for non-standard dimensions
    index_map = {
        dim: czi_dim_map[dim]
        for dim in nonstandard_dims
        if dim in czi_dim_map
    }

    # Read the image data
    try:
        return img.get_image_dask_data(dimension_order_out='TCZYX', **index_map)
    except Exception as e:
        raise RuntimeError(f"Failed to read image data: {str(e)}") from e





