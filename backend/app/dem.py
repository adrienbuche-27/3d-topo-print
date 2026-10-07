"""Elevation data: Copernicus DEM GLO-30 tiles, local cache and mosaicking.

GLO-30 is split into 1° x 1° Cloud-Optimized GeoTIFFs in a public AWS bucket.
Tiles are downloaded once into a local cache, then the requested bbox is
mosaicked into a single WGS84 grid. Tiles that do not exist (open sea) are
remembered in the cache and filled with sea level (0 m).
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import Resampling, reproject

COPERNICUS_URL = "https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "dem"
WGS84 = CRS.from_epsg(4326)

# GLO-30 native spacing is 1 arc-second in latitude.
NATIVE_RES_DEG = 1 / 3600
# Guard against accidentally building huge rasters (about 240 MB of float32).
MAX_CELLS = 60_000_000


class DemError(RuntimeError):
    """Raised when elevation data cannot be produced for an area."""


@dataclass
class DemRaster:
    """Elevation grid in WGS84. Row 0 is the northern edge."""

    data: np.ndarray  # float32 metres, shape (rows, cols)
    transform: Affine
    crs: CRS = WGS84

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(west, south, east, north)."""
        rows, cols = self.data.shape
        west, north = self.transform @ (0, 0)
        east, south = self.transform @ (cols, rows)
        return west, south, east, north


class TileSource(Protocol):
    def get(self, lat: int, lon: int) -> Path | None:
        """Local path of the tile whose south-west corner is (lat, lon), or None if absent."""
        ...


def tile_name(lat: int, lon: int) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"


def tiles_for_bbox(bbox: tuple[float, float, float, float]) -> list[tuple[int, int]]:
    """(lat, lon) south-west corners of all 1° tiles intersecting the bbox."""
    west, south, east, north = bbox
    if not (west < east and south < north):
        raise DemError(f"Invalid bbox: {bbox}")
    lats = range(math.floor(south), math.ceil(north))
    lons = range(math.floor(west), math.ceil(east))
    return [(lat, lon) for lat in lats for lon in lons]


class CopernicusTileCache:
    """Downloads GLO-30 tiles on first use and keeps them on disk."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        url_template: str = COPERNICUS_URL,
        retries: int = 3,
    ) -> None:
        env_dir = os.environ.get("TOPO_DEM_CACHE_DIR")
        self.cache_dir = Path(cache_dir or env_dir or DEFAULT_CACHE_DIR)
        self.url_template = url_template
        self.retries = retries

    def get(self, lat: int, lon: int) -> Path | None:
        name = tile_name(lat, lon)
        path = self.cache_dir / f"{name}.tif"
        missing_marker = self.cache_dir / f"{name}.missing"
        if path.exists():
            return path
        if missing_marker.exists():
            return None

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        url = self.url_template.format(name=name)
        for attempt in range(self.retries):
            try:
                self._download(url, path)
                return path
            except urllib.error.HTTPError as exc:
                if exc.code == 404:  # no land in this tile
                    missing_marker.touch()
                    return None
                error: Exception = exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                error = exc
            time.sleep(2**attempt)
        raise DemError(f"Could not download elevation tile {name}: {error}")

    def _download(self, url: str, path: Path) -> None:
        # Write to a temporary file first so an interrupted download never looks cached.
        with urllib.request.urlopen(url, timeout=60) as response:
            fd, tmp = tempfile.mkstemp(dir=self.cache_dir, suffix=".part")
            try:
                with os.fdopen(fd, "wb") as out:
                    shutil.copyfileobj(response, out, length=1 << 20)
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise


def read_dem(
    bbox: tuple[float, float, float, float],
    resolution_deg: float | None = None,
    source: TileSource | None = None,
) -> DemRaster:
    """Mosaic of the elevation data covering `bbox` (west, south, east, north).

    `resolution_deg` sets the output cell size; by default the finest native
    resolution of the tiles involved is used. Coarser outputs are averaged,
    which avoids aliasing when a large area is printed small.
    """
    source = source or CopernicusTileCache()
    paths = [p for lat, lon in tiles_for_bbox(bbox) if (p := source.get(lat, lon)) is not None]

    # Finest cell size among the tiles (GLO-30 longitude spacing widens north of 50°).
    native = NATIVE_RES_DEG
    if paths:
        native = math.inf
        for path in paths:
            with rasterio.open(path) as src:
                native = min(native, abs(src.transform.a), abs(src.transform.e))
    res = max(resolution_deg or native, native)
    resampling = Resampling.average if res > native * 1.5 else Resampling.bilinear

    # Snap the output grid to multiples of the resolution so mosaics are reproducible.
    west, south, east, north = bbox
    west = math.floor(west / res) * res
    north = math.ceil(north / res) * res
    cols = math.ceil((east - west) / res)
    rows = math.ceil((north - south) / res)
    if rows * cols > MAX_CELLS:
        raise DemError(
            f"Area too large for this resolution ({rows} x {cols} cells); use a coarser resolution."
        )
    transform = Affine(res, 0.0, west, 0.0, -res, north)

    mosaic = np.full((rows, cols), np.nan, dtype=np.float32)
    for path in paths:
        with rasterio.open(path) as src:
            part = np.full((rows, cols), np.nan, dtype=np.float32)
            reproject(
                source=rasterio.band(src, 1),
                destination=part,
                src_nodata=src.nodata,
                dst_transform=transform,
                dst_crs=WGS84,
                dst_nodata=np.nan,
                resampling=resampling,
            )
            mosaic = np.where(np.isnan(mosaic), part, mosaic)

    # Missing tiles and nodata are open sea.
    np.nan_to_num(mosaic, copy=False, nan=0.0)
    return DemRaster(data=mosaic, transform=transform)
