from pathlib import Path

import numpy as np
import pytest
from pyproj import CRS

from app.dem import CopernicusTileCache
from app.geo import from_crs
from app.gpx import Frame, compute_frame, load_gpx
from app.heightfield import build_heightfield
from tests.synthetic import FakeSource, write_tile

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def frame() -> Frame:
    track = load_gpx((FIXTURES / "alps_loop.gpx").read_bytes())
    return compute_frame(track, margin_pct=10)


@pytest.fixture
def plane_source(tmp_path: Path) -> FakeSource:
    write_tile(tmp_path / "t.tif", 45, 6, 0.001)
    return FakeSource({(45, 6): tmp_path / "t.tif"})


def test_grid_shape_and_scale(frame: Frame, plane_source: FakeSource) -> None:
    hf = build_heightfield(frame, width_mm=180, resolution_mm=1.0, source=plane_source)

    rows, cols = hf.shape
    assert cols == 181
    assert hf.width_mm == pytest.approx(180)
    # ~6.0 x 5.0 km frame → ~150 mm tall.
    assert hf.height_mm == pytest.approx(180 * frame.height_m / frame.width_m)
    assert rows == round(hf.height_mm) + 1
    dx, dy = hf.spacing_mm
    assert dx == pytest.approx(1.0) and dy == pytest.approx(1.0, rel=0.01)


def test_values_follow_the_source_terrain(frame: Frame, plane_source: FakeSource) -> None:
    hf = build_heightfield(frame, width_mm=180, resolution_mm=1.0, source=plane_source)

    # Compare each sampled node with the synthetic plane at the node's true lon/lat.
    rows, cols = hf.shape
    min_x, min_y, max_x, max_y = frame.bounds_m
    xs = np.linspace(min_x, max_x, cols)
    ys = np.linspace(max_y, min_y, rows)
    gx, gy = np.meshgrid(xs, ys)
    lon, lat = from_crs(CRS.from_epsg(frame.epsg)).transform(gx, gy)
    expected = 1000 * lon + 100 * lat
    assert np.abs(hf.elevations_m - expected).max() < 1.0


def test_print_coordinates(frame: Frame, plane_source: FakeSource) -> None:
    hf = build_heightfield(frame, width_mm=180, resolution_mm=1.0, source=plane_source)
    min_x, min_y, max_x, max_y = frame.bounds_m
    px, py = hf.to_print_xy(np.array([min_x, max_x]), np.array([min_y, max_y]))
    assert px.tolist() == pytest.approx([0, 180])
    assert py.tolist() == pytest.approx([0, hf.height_mm])


def test_smoothing_keeps_a_plane(frame: Frame, plane_source: FakeSource) -> None:
    raw = build_heightfield(frame, width_mm=180, resolution_mm=1.0, source=plane_source)
    smooth = build_heightfield(
        frame, width_mm=180, resolution_mm=1.0, smooth_sigma_cells=2, source=plane_source
    )
    # A blur leaves a linear surface unchanged away from the edges.
    inner = (slice(10, -10), slice(10, -10))
    assert np.abs(raw.elevations_m[inner] - smooth.elevations_m[inner]).max() < 0.5


def test_rejects_too_fine_grid(frame: Frame, plane_source: FakeSource) -> None:
    with pytest.raises(ValueError):
        build_heightfield(frame, width_mm=1000, resolution_mm=0.1, source=plane_source)


@pytest.mark.network
def test_real_chamonix_terrain(frame: Frame) -> None:
    hf = build_heightfield(frame, width_mm=180, resolution_mm=0.5, source=CopernicusTileCache())
    # The frame spans the Chamonix valley floor (~1000 m) up to the Aiguilles Rouges
    # (Le Brévent 2525 m) and the lower slopes of the Mont Blanc massif.
    assert 950 < hf.elevations_m.min() < 1_200
    assert 2_300 < hf.elevations_m.max() < 3_200
