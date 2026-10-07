from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine

from app.dem import (
    CopernicusTileCache,
    DemError,
    read_dem,
    tile_name,
    tiles_for_bbox,
)


def test_tile_name() -> None:
    assert tile_name(45, 6) == "Copernicus_DSM_COG_10_N45_00_E006_00_DEM"
    assert tile_name(-1, -12) == "Copernicus_DSM_COG_10_S01_00_W012_00_DEM"


def test_tiles_for_bbox() -> None:
    assert tiles_for_bbox((6.2, 45.3, 6.8, 45.9)) == [(45, 6)]
    assert tiles_for_bbox((6.5, 45.5, 7.5, 46.0)) == [(45, 6), (45, 7)]
    # A bbox ending exactly on a tile edge does not pull in the next tile.
    assert tiles_for_bbox((6.0, 45.0, 7.0, 46.0)) == [(45, 6)]
    assert tiles_for_bbox((-0.5, 44.5, 0.5, 45.5)) == [
        (44, -1),
        (44, 0),
        (45, -1),
        (45, 0),
    ]


def test_tiles_for_bbox_rejects_empty() -> None:
    with pytest.raises(DemError):
        tiles_for_bbox((7.0, 45.0, 6.0, 46.0))


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


def test_mosaic_across_tiles_with_different_resolutions(tmp_path: Path) -> None:
    write_tile(tmp_path / "a.tif", 45, 6, 0.01)
    write_tile(tmp_path / "b.tif", 45, 7, 0.02)  # coarser, like GLO-30 north of 50°
    source = FakeSource({(45, 6): tmp_path / "a.tif", (45, 7): tmp_path / "b.tif"})

    dem = read_dem((6.5, 45.2, 7.5, 45.8), source=source)

    assert sorted(source.requested) == [(45, 6), (45, 7)]
    assert dem.transform.a == pytest.approx(0.01)  # finest resolution wins
    west, south, east, north = dem.bounds
    assert west <= 6.5 and east >= 7.5 and south <= 45.2 and north >= 45.8

    # Values follow the plane on both sides of the tile seam.
    rows, cols = dem.data.shape
    for r, c in [
        (rows // 2, 5),
        (rows // 2, cols // 2 - 2),
        (rows // 2, cols // 2 + 2),
        (rows // 2, cols - 5),
    ]:
        lon, lat = dem.transform @ (c + 0.5, r + 0.5)
        assert dem.data[r, c] == pytest.approx(1000 * lon + 100 * lat, abs=15)


def test_missing_tiles_are_sea_level(tmp_path: Path) -> None:
    write_tile(tmp_path / "a.tif", 45, 6, 0.01)
    source = FakeSource({(45, 6): tmp_path / "a.tif"})

    dem = read_dem((6.5, 45.2, 7.5, 45.8), source=source)

    rows, cols = dem.data.shape
    assert dem.data[rows // 2, 5] > 6000
    assert dem.data[rows // 2, cols - 5] == 0.0
    assert not np.isnan(dem.data).any()


def test_coarser_output_resolution(tmp_path: Path) -> None:
    write_tile(tmp_path / "a.tif", 45, 6, 0.01)
    source = FakeSource({(45, 6): tmp_path / "a.tif"})

    dem = read_dem((6.0, 45.0, 7.0, 46.0), resolution_deg=0.05, source=source)

    assert dem.data.shape == (20, 20)
    lon, lat = dem.transform @ (10.5, 10.5)
    assert dem.data[10, 10] == pytest.approx(1000 * lon + 100 * lat, abs=1)


def test_too_large_area_is_rejected() -> None:
    source = FakeSource({})
    with pytest.raises(DemError):
        read_dem((0.0, 40.0, 10.0, 50.0), source=source)


def test_cache_returns_cached_tile_without_network(tmp_path: Path) -> None:
    cached = tmp_path / f"{tile_name(45, 6)}.tif"
    cached.write_bytes(b"cached")
    (tmp_path / f"{tile_name(0, -30)}.missing").touch()
    cache = CopernicusTileCache(cache_dir=tmp_path, url_template="http://invalid.invalid/{name}")

    assert cache.get(45, 6) == cached
    assert cache.get(0, -30) is None


@pytest.mark.network
def test_real_copernicus_mont_blanc(tmp_path: Path) -> None:
    """Downloads one real GLO-30 tile (~30 MB) and checks the Mont Blanc summit."""
    cache = CopernicusTileCache(cache_dir=tmp_path)
    dem = read_dem((6.84, 45.82, 6.89, 45.85), source=cache)

    # Mont Blanc is 4806 m; GLO-30 is a surface model at 30 m, so allow some slack.
    assert 4_700 < dem.data.max() < 4_850
    # Sea tile in the Atlantic is reported as missing and remembered.
    assert cache.get(38, -30) is None
    assert (tmp_path / f"{tile_name(38, -30)}.missing").exists()
