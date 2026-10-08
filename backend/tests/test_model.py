from pathlib import Path

import numpy as np
import pytest
import trimesh
from shapely.geometry import box

from app.dem import CopernicusTileCache
from app.gpx import Track, TrackPoint, compute_frame, load_gpx
from app.heightfield import Heightfield, build_heightfield
from app.mesh import grid_solid, to_trimesh
from app.route import RouteError, build_model, build_parts, route_footprint
from app.terrain import ModelParams, terrain_solid, terrain_surface
from tests.synthetic import FakeSource, write_tile

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def track() -> Track:
    return load_gpx((FIXTURES / "alps_loop.gpx").read_bytes())


@pytest.fixture(scope="module")
def hf(track: Track, tmp_path_factory: pytest.TempPathFactory) -> Heightfield:
    tile = tmp_path_factory.mktemp("dem") / "t.tif"
    write_tile(tile, 45, 6, 0.002)
    source = FakeSource({(45, 6): tile})
    return build_heightfield(compute_frame(track), size_mm=120, resolution_mm=1.0, source=source)


def as_trimesh(vertices: np.ndarray, faces: np.ndarray) -> trimesh.Trimesh:
    return trimesh.Trimesh(vertices, faces, process=False)


def test_grid_solid_is_closed_and_outward() -> None:
    xs = np.linspace(0, 10, 11)
    ys = np.linspace(5, 0, 6)
    z = np.full((6, 11), 2.0)
    mesh = as_trimesh(*grid_solid(xs, ys, z))
    assert mesh.is_watertight and mesh.is_winding_consistent
    assert mesh.volume == pytest.approx(10 * 5 * 2)  # positive: normals point outward


def test_grid_solid_rejects_mismatched_shapes() -> None:
    with pytest.raises(ValueError):
        grid_solid(np.arange(3), np.arange(2), np.zeros((3, 3)))


def test_terrain_surface_scaling(hf: Heightfield) -> None:
    params = ModelParams(base_mm=3, z_exaggeration=2)
    surface = terrain_surface(hf, params)
    relief_m = float(hf.elevations_m.max() - hf.elevations_m.min())
    assert surface.z.min() == pytest.approx(3)
    assert surface.z.max() - surface.z.min() == pytest.approx(relief_m * hf.scale * 2, rel=1e-5)
    assert (surface.xs[0], surface.xs[-1]) == pytest.approx((0, hf.width_mm))
    assert (surface.ys[0], surface.ys[-1]) == pytest.approx((hf.height_mm, 0))


def test_terrain_solid_fits_the_requested_size(hf: Heightfield) -> None:
    mesh = to_trimesh(terrain_solid(terrain_surface(hf, ModelParams())))
    assert mesh.is_watertight
    (x0, y0, z0), (x1, y1, _) = mesh.bounds
    assert (x0, y0, z0) == pytest.approx((0, 0, 0), abs=1e-4)
    assert x1 == pytest.approx(120, abs=1e-3)
    assert y1 == pytest.approx(hf.height_mm, abs=1e-3)


def test_route_insert_fits_the_groove(track: Track, hf: Heightfield) -> None:
    params = ModelParams(route_width_mm=2.0, route_raise_mm=0.6, groove_depth_mm=1.0)
    surface = terrain_surface(hf, params)
    footprint = route_footprint(track, hf, params.route_width_mm)
    parts = build_parts(surface, footprint, params)

    terrain, route = to_trimesh(parts.terrain), to_trimesh(parts.route)
    assert terrain.is_watertight and route.is_watertight
    assert len(parts.route.decompose()) == 1  # the loop is one continuous insert

    # The bodies touch but do not overlap.
    assert abs((parts.terrain ^ parts.route).volume()) < 1e-3

    # All three surfaces share one triangulation, so the band has a constant
    # vertical thickness and the volumes are exact.
    area = footprint.intersection(box(0, 0, hf.width_mm, hf.height_mm)).area
    full = terrain_solid(surface).volume()
    assert parts.route.volume() == pytest.approx(area * 1.6, rel=1e-3)
    assert full - parts.terrain.volume() == pytest.approx(area * 1.0, rel=1e-3)


def test_route_reaching_the_edge_is_trimmed(hf: Heightfield) -> None:
    # A straight line running off both sides of the print area.
    lat = (hf.frame.bbox_lonlat[1] + hf.frame.bbox_lonlat[3]) / 2
    west, _, east, _ = hf.frame.bbox_lonlat
    line = Track(name=None, segments=[[TrackPoint(lat, west - 0.01), TrackPoint(lat, east + 0.01)]])
    parts = build_model(hf, line, ModelParams())

    terrain, route = to_trimesh(parts.terrain), to_trimesh(parts.route)
    assert terrain.is_watertight and route.is_watertight
    (x0, _, _), (x1, _, _) = route.bounds
    assert x0 == pytest.approx(0, abs=1e-3) and x1 == pytest.approx(hf.width_mm, abs=1e-3)


def test_route_outside_the_frame_is_rejected(hf: Heightfield) -> None:
    far = Track(name=None, segments=[[TrackPoint(48.0, 2.0), TrackPoint(48.01, 2.01)]])
    with pytest.raises(RouteError):
        build_model(hf, far, ModelParams())


def test_groove_deeper_than_base_is_rejected(track: Track, hf: Heightfield) -> None:
    with pytest.raises(RouteError):
        build_model(hf, track, ModelParams(base_mm=1.0, groove_depth_mm=1.5))


@pytest.mark.network
def test_real_alpe_d_huez_model() -> None:
    track = load_gpx((FIXTURES / "Alpe_d_Huez.gpx").read_bytes())
    hf = build_heightfield(
        compute_frame(track), size_mm=180, resolution_mm=0.5, source=CopernicusTileCache()
    )
    parts = build_model(hf, track, ModelParams())

    terrain, route = to_trimesh(parts.terrain), to_trimesh(parts.route)
    assert terrain.is_watertight and route.is_watertight
    assert len(parts.route.decompose()) == 1
    assert abs((parts.terrain ^ parts.route).volume()) < 1e-3
    # Portrait frame: the longest side (north-south) is 180 mm.
    (_, _, _), (x1, y1, _) = terrain.bounds
    assert y1 == pytest.approx(180, abs=1e-3)
    assert x1 == pytest.approx(180 * 3682 / 4883, abs=1)
