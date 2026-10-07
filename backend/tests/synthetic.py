"""Synthetic elevation tiles for tests that must not touch the network."""

from pathlib import Path

import numpy as np
import rasterio
from affine import Affine


def write_tile(path: Path, lat: int, lon: int, res_deg: float) -> None:
    """Synthetic 1° tile whose elevation is a plane: ele = 1000 * lon + 100 * lat."""
    n_rows = round(1 / res_deg)
    n_cols = round(1 / res_deg)
    xs = lon + (np.arange(n_cols) + 0.5) * res_deg
    ys = lat + 1 - (np.arange(n_rows) + 0.5) * res_deg
    data = (1000 * xs[None, :] + 100 * ys[:, None]).astype(np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=n_rows,
        width=n_cols,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=Affine(res_deg, 0, lon, 0, -res_deg, lat + 1),
    ) as dst:
        dst.write(data, 1)


class FakeSource:
    def __init__(self, tiles: dict[tuple[int, int], Path]) -> None:
        self.tiles = tiles
        self.requested: list[tuple[int, int]] = []

    def get(self, lat: int, lon: int) -> Path | None:
        self.requested.append((lat, lon))
        return self.tiles.get((lat, lon))
