from pathlib import Path

import pytest
import shapely

from app.dem import CopernicusTileCache
from app.gpx import Track, compute_frame, load_gpx
from app.heightfield import Heightfield, build_heightfield
from app.inlay import band_levels, build_inlay
from app.mesh import to_trimesh
from app.route import RouteError, route_footprint
from app.terrain import ModelParams, Surface, terrain_surface
from tests.synthetic import FakeSource, write_tile

FIXTURES = Path(__file__).parent / "fixtures"

# The synthetic plane rises ~2 mm across the model, so small bands give several pieces.
PARAMS = ModelParams(route_width_mm=1.6, inlay_piece_height_mm=0.5, inlay_clearance_mm=0.15)


@pytest.fixture(scope="module")
def track() -> Track:
    return load_gpx((FIXTURES / "alps_loop.gpx").read_bytes())


@pytest.fixture(scope="module")
def hf(track: Track, tmp_path_factory: pytest.TempPathFactory) -> Heightfield:
    tile = tmp_path_factory.mktemp("dem") / "t.tif"
    write_tile(tile, 45, 6, 0.002)
    return build_heightfield(
        compute_frame(track), size_mm=100, resolution_mm=1.0, source=FakeSource({(45, 6): tile})
    )


@pytest.fixture(scope="module")
def surface(hf: Heightfield) -> Surface:
    return terrain_surface(hf, PARAMS)


@pytest.fixture(scope="module")
def footprint(track: Track, hf: Heightfield) -> shapely.Geometry:
    return route_footprint(track, hf, PARAMS.route_width_mm)


def test_band_levels() -> None:
    assert band_levels(2.0, 14.0, 6.0) == [2.0, 8.0]
    assert band_levels(2.0, 14.5, 6.0) == [2.0, 8.0, 14.0]
    assert band_levels(2.0, 2.0, 6.0) == [2.0]


def test_pieces_print_flat_and_stay_short(surface: Surface, footprint: shapely.Geometry) -> None:
    parts = build_inlay(surface, footprint, PARAMS)
    assert len(parts.pieces) >= 3

    max_height = PARAMS.inlay_piece_height_mm + PARAMS.groove_depth_mm + PARAMS.route_raise_mm
    for piece in parts.pieces:
        mesh = to_trimesh(piece.on_bed())
        assert mesh.is_watertight
        (_, _, z0), (_, _, z1) = mesh.bounds
        assert z0 == pytest.approx(0, abs=1e-4)  # flat bottom on the bed
        assert z1 <= max_height + 1e-3


def test_terrain_stays_one_watertight_piece(surface: Surface, footprint: shapely.Geometry) -> None:
    parts = build_inlay(surface, footprint, PARAMS)
    assert to_trimesh(parts.terrain).is_watertight
    assert len(parts.terrain.decompose()) == 1


def test_pieces_fit_their_slots_with_clearance(
    surface: Surface, footprint: shapely.Geometry
) -> None:
    parts = build_inlay(surface, footprint, PARAMS)
    nudge = PARAMS.inlay_clearance_mm * 0.8
    for piece in parts.pieces:
        # In place, and nudged sideways by less than the clearance, a piece never
        # touches the terrain...
        for dx, dy in [(0, 0), (nudge, 0), (-nudge, 0), (0, nudge), (0, -nudge)]:
            moved = piece.solid.translate((dx, dy, 0))
            assert abs((parts.terrain ^ moved).volume()) < 1e-3
        # ...and it rests on its slot floor: lowered a little, it does.
        lowered = piece.solid.translate((0, 0, -0.1))
        assert (parts.terrain ^ lowered).volume() > 0.01


def test_single_piece_when_piece_height_covers_the_relief(
    surface: Surface, footprint: shapely.Geometry
) -> None:
    params = ModelParams(route_width_mm=1.6, inlay_piece_height_mm=100.0)
    parts = build_inlay(surface, footprint, params)
    assert len(parts.pieces) == 1  # the loop is one ring-shaped piece


def test_invalid_parameters(surface: Surface, footprint: shapely.Geometry) -> None:
    with pytest.raises(RouteError):
        build_inlay(surface, footprint, ModelParams(inlay_piece_height_mm=0))
    with pytest.raises(RouteError):
        build_inlay(surface, footprint, ModelParams(base_mm=1.0, groove_depth_mm=1.5))


@pytest.mark.network
def test_real_alpe_d_huez_inlay() -> None:
    track = load_gpx((FIXTURES / "Alpe_d_Huez.gpx").read_bytes())
    hf = build_heightfield(
        compute_frame(track), size_mm=180, resolution_mm=0.5, source=CopernicusTileCache()
    )
    params = ModelParams(route_width_mm=1.6)
    surface = terrain_surface(hf, params)
    parts = build_inlay(surface, route_footprint(track, hf, 1.6), params)

    assert to_trimesh(parts.terrain).is_watertight
    assert len(parts.terrain.decompose()) == 1
    # ~63 mm of relief along the route in 6 mm bands.
    assert 8 <= len(parts.pieces) <= 30
    for piece in parts.pieces:
        assert to_trimesh(piece.on_bed()).is_watertight
        assert abs((parts.terrain ^ piece.solid).volume()) < 1e-3


def test_fit_test_piece() -> None:
    from app.inlay import build_fit_test

    parts = build_fit_test(ModelParams(route_width_mm=1.6))
    assert len(parts.pieces) == 2
    assert to_trimesh(parts.terrain).is_watertight
    (x0, y0, z0, x1, y1, _) = parts.terrain.bounding_box()
    assert (x1 - x0, y1 - y0, z0) == pytest.approx((40, 24, 0))
    for piece in parts.pieces:
        assert to_trimesh(piece.on_bed()).is_watertight
        assert abs((parts.terrain ^ piece.solid).volume()) < 1e-3
