"""Projection & resampling: elevation data → regular grid in print millimetres.

The print frame is a rectangle in a local UTM projection. The heightfield
samples elevations at the nodes of a regular grid covering that rectangle,
which later become the vertices of the terrain mesh.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import Resampling, reproject
from scipy.ndimage import gaussian_filter

from .dem import TileSource, read_dem
from .gpx import Frame

METRES_PER_DEG_LAT = 111_320.0
# Keeps meshes manageable: 1000 x 1000 nodes is 250 mm at 0.25 mm spacing.
MAX_NODES = 4_000_000


@dataclass
class Heightfield:
    """Elevations at grid nodes. Row 0 is the northern edge, column 0 the western edge."""

    elevations_m: np.ndarray  # float32, shape (rows, cols)
    frame: Frame
    scale: float  # print millimetres per real-world metre (horizontal)

    @property
    def shape(self) -> tuple[int, int]:
        return self.elevations_m.shape

    @property
    def width_mm(self) -> float:
        return self.frame.width_m * self.scale

    @property
    def height_mm(self) -> float:
        return self.frame.height_m * self.scale

    @property
    def spacing_mm(self) -> tuple[float, float]:
        """(dx, dy) between neighbouring nodes."""
        rows, cols = self.shape
        return self.width_mm / (cols - 1), self.height_mm / (rows - 1)

    def to_print_xy(self, x_m: np.ndarray, y_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Projected metres → print millimetres, origin at the south-west corner, y north."""
        min_x, min_y, _, _ = self.frame.bounds_m
        return (np.asarray(x_m) - min_x) * self.scale, (np.asarray(y_m) - min_y) * self.scale


def build_heightfield(
    frame: Frame,
    size_mm: float,
    resolution_mm: float = 0.25,
    smooth_sigma_cells: float = 0.0,
    source: TileSource | None = None,
) -> Heightfield:
    """Sample the terrain of `frame` on a grid with ~`resolution_mm` print spacing.

    `size_mm` is the length of the model's longest side, so the model always
    fits a square print bed of that size whatever the shape of the frame.

    `smooth_sigma_cells` optionally applies a Gaussian blur (in grid cells) to
    soften DEM noise such as trees and buildings in the surface model.
    """
    if size_mm <= 0 or resolution_mm <= 0:
        raise ValueError("size_mm and resolution_mm must be positive")

    scale = size_mm / max(frame.width_m, frame.height_m)
    width_mm = frame.width_m * scale
    height_mm = frame.height_m * scale
    cols = max(round(width_mm / resolution_mm), 1) + 1
    rows = max(round(height_mm / resolution_mm), 1) + 1
    if rows * cols > MAX_NODES:
        raise ValueError(f"Grid of {rows} x {cols} nodes is too fine; increase resolution_mm.")

    min_x, min_y, max_x, max_y = frame.bounds_m
    dx = (max_x - min_x) / (cols - 1)
    dy = (max_y - min_y) / (rows - 1)

    # Read the DEM at about half the node spacing (never finer than native) so the
    # averaging inside read_dem removes detail the printer cannot show anyway.
    west, south, east, north = frame.bbox_lonlat
    cell_deg = min(dx, dy) / 2 / METRES_PER_DEG_LAT
    pad = 2 * max(dx, dy) / (METRES_PER_DEG_LAT * math.cos(math.radians(north)))
    dem = read_dem(
        (west - pad, south - pad, east + pad, north + pad),
        resolution_deg=cell_deg,
        source=source,
    )

    # Destination cells are centred on the grid nodes.
    dst_transform = Affine(dx, 0.0, min_x - dx / 2, 0.0, -dy, max_y + dy / 2)
    elevations = np.full((rows, cols), np.nan, dtype=np.float32)
    reproject(
        source=dem.data,
        destination=elevations,
        src_transform=dem.transform,
        src_crs=dem.crs,
        dst_transform=dst_transform,
        dst_crs=CRS.from_epsg(frame.epsg),
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    if np.isnan(elevations).any():
        raise RuntimeError("Elevation data does not fully cover the print frame")

    if smooth_sigma_cells > 0:
        elevations = gaussian_filter(elevations, sigma=smooth_sigma_cells, mode="nearest")

    return Heightfield(elevations_m=elevations.astype(np.float32), frame=frame, scale=scale)
