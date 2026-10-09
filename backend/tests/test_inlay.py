from pathlib import Path

import numpy as np
import pytest
import shapely

from app.dem import CopernicusTileCache
from app.gpx import Track, compute_frame, load_gpx
from app.heightfield import Heightfield, build_heightfield
from app.inlay import SurfaceHeight, build_fit_test, build_inlay, cut_labels, split_regions
from app.mesh import to_trimesh
from app.route import RouteError, route_centerlines, route_footprint
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


@pytest.fixture(scope="module")
def lines(track: Track, hf: Heightfield) -> list[shapely.LineString]:
    return route_centerlines(track, hf)


def test_cut_labels_follow_the_relief() -> None:
    xy = np.zeros((11, 2))
    z = np.array([0, 1, 2, 3, 4, 5, 4, 3, 2, 1, 0], dtype=float)
    # A piece starts once its own relief exceeds 2 mm: 0-2, then 3-5-3, then 2-0.
    assert cut_labels(xy, z, 2.0).tolist() == [0, 0, 0, 1, 1, 1, 1, 1, 2, 2, 2]
    assert cut_labels(xy, np.zeros(5), 2.0).tolist() == [0] * 5


def test_regions_are_cut_square_and_a_road_ridden_twice_counts_once() -> None:
    # Plane rising 1 mm per 10 mm along x.
    xs = np.linspace(0, 100, 101)
    ys = np.linspace(20, 0, 21)
    height = SurfaceHeight(Surface(xs=xs, ys=ys, z=np.tile(xs / 10, (21, 1))))
    params = ModelParams(route_width_mm=1.6, inlay_piece_height_mm=2.0)

    up = shapely.LineString([(5, 10), (95, 10)])
    band = up.buffer(0.8, cap_style=2)
    once = split_regions(band, [up], height, params)
    # Up and back down the same road, with a little GPS offset.
    there_and_back = shapely.LineString([(5, 10), (95, 10), (95, 10.1), (5, 10.1)])
    twice = split_regions(there_and_back.buffer(0.8), [there_and_back], height, params)

    assert len(once) == 5  # 9 mm of relief in 2 mm pieces
    assert len(twice) == len(once)
    for region in once:
        x0, y0, x1, y1 = region.bounds
        assert (y0, y1) == pytest.approx((9.2, 10.8))  # full width of the road
        assert region.area == pytest.approx((x1 - x0) * 1.6, rel=1e-3)  # square ends


def test_pieces_print_flat_and_stay_short(
    surface: Surface, footprint: shapely.Geometry, lines: list[shapely.LineString]
) -> None:
    parts = build_inlay(surface, footprint, lines, PARAMS)
    assert len(parts.pieces) >= 3

    # The relief is bounded along the centreline; the slope across the band adds a little.
    cross_slope = 0.1
    max_height = PARAMS.inlay_piece_height_mm + PARAMS.groove_depth_mm + PARAMS.route_raise_mm
    for piece in parts.pieces:
        mesh = to_trimesh(piece.on_bed())
        assert mesh.is_watertight
        (_, _, z0), (_, _, z1) = mesh.bounds
        assert z0 == pytest.approx(0, abs=1e-4)  # flat bottom on the bed
        assert z1 <= max_height + cross_slope


def test_terrain_stays_one_watertight_piece(
    surface: Surface, footprint: shapely.Geometry, lines: list[shapely.LineString]
) -> None:
    parts = build_inlay(surface, footprint, lines, PARAMS)
    assert to_trimesh(parts.terrain).is_watertight
    assert len(parts.terrain.decompose()) == 1


def test_pieces_fit_their_slots_with_clearance(
    surface: Surface, footprint: shapely.Geometry, lines: list[shapely.LineString]
) -> None:
    parts = build_inlay(surface, footprint, lines, PARAMS)
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


def test_neighbouring_pieces_are_cut_square_with_a_small_gap(
    surface: Surface, footprint: shapely.Geometry, lines: list[shapely.LineString]
) -> None:
    parts = build_inlay(surface, footprint, lines, PARAMS)
    outlines = [shapely.Polygon(piece.solid.project().to_polygons()[0]) for piece in parts.pieces]
    # Each piece's nearest neighbour is twice the clearance (plus the knife) away, never
    # more: a cut square across the route leaves no long tapered gap.
    for i, a in enumerate(outlines):
        nearest = min(a.distance(b) for j, b in enumerate(outlines) if j != i)
        assert 0.25 < nearest < 0.4


def test_single_piece_when_piece_height_covers_the_relief(
    surface: Surface, footprint: shapely.Geometry, lines: list[shapely.LineString]
) -> None:
    params = ModelParams(route_width_mm=1.6, inlay_piece_height_mm=100.0)
    parts = build_inlay(surface, footprint, lines, params)
    assert len(parts.pieces) == 1  # the loop is one ring-shaped piece


def test_invalid_parameters(
    surface: Surface, footprint: shapely.Geometry, lines: list[shapely.LineString]
) -> None:
    with pytest.raises(RouteError):
        build_inlay(surface, footprint, lines, ModelParams(inlay_piece_height_mm=0))
    with pytest.raises(RouteError):
        build_inlay(surface, footprint, lines, ModelParams(base_mm=1.0, groove_depth_mm=1.5))


@pytest.mark.network
def test_real_alpe_d_huez_inlay() -> None:
    track = load_gpx((FIXTURES / "Alpe_d_Huez.gpx").read_bytes())
    hf = build_heightfield(
        compute_frame(track), size_mm=180, resolution_mm=0.5, source=CopernicusTileCache()
    )
    params = ModelParams(route_width_mm=1.6)
    surface = terrain_surface(hf, params)
    lines = route_centerlines(track, hf)
    parts = build_inlay(surface, route_footprint(track, hf, 1.6), lines, params)

    assert to_trimesh(parts.terrain).is_watertight
    assert len(parts.terrain.decompose()) == 1
    # ~63 mm of relief along the route in 6 mm steps.
    assert 8 <= len(parts.pieces) <= 30
    # Relief is bounded along the road; steep slopes add up to ~1.5 mm across its width.
    max_height = 6.0 + params.groove_depth_mm + params.route_raise_mm + 2.0
    for piece in parts.pieces:
        assert to_trimesh(piece.on_bed()).is_watertight
        assert piece.solid.bounding_box()[5] - piece.bottom_z < max_height
        assert abs((parts.terrain ^ piece.solid).volume()) < 1e-3


def test_fit_test_piece() -> None:
    parts = build_fit_test(ModelParams(route_width_mm=1.6))
    assert len(parts.pieces) == 2
    assert to_trimesh(parts.terrain).is_watertight
    (x0, y0, z0, x1, y1, _) = parts.terrain.bounding_box()
    assert (x1 - x0, y1 - y0, z0) == pytest.approx((40, 24, 0))
    for piece in parts.pieces:
        assert to_trimesh(piece.on_bed()).is_watertight
        assert abs((parts.terrain ^ piece.solid).volume()) < 1e-3
