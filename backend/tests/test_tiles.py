from pathlib import Path

import pytest
import shapely

from app.gpx import Track, compute_frame, load_gpx
from app.heightfield import Heightfield, build_heightfield
from app.inlay import build_inlay
from app.mesh import to_trimesh
from app.route import build_parts, route_centerlines, route_footprint
from app.terrain import ModelParams, Surface, terrain_surface
from app.tiles import build_tiles, grid_size, plan_tiles
from tests.synthetic import FakeSource, write_tile

FIXTURES = Path(__file__).parent / "fixtures"
PARAMS = ModelParams(route_width_mm=1.6, inlay_piece_height_mm=0.5)


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


def test_grid_size() -> None:
    assert grid_size(180, 136, 240) == (1, 1)
    assert grid_size(600, 400, 240) == (3, 2)
    assert grid_size(480, 240, 240) == (2, 1)  # exact multiples need no extra tile
    with pytest.raises(ValueError):
        grid_size(100, 100, 0)


def test_tiles_share_their_edges_and_cover_the_model(surface: Surface) -> None:
    tiles = plan_tiles(surface, cols=3, rows=2)
    assert [spec.label for spec, _ in tiles] == ["A1", "A2", "A3", "B1", "B2", "B3"]
    by_label = {spec.label: part for spec, part in tiles}
    # Neighbours share their edge nodes exactly: no gap, no overlap.
    assert by_label["A1"].xs[-1] == by_label["A2"].xs[0]
    assert by_label["A1"].ys[-1] == by_label["B1"].ys[0]
    assert (by_label["A1"].z[:, -1] == by_label["A2"].z[:, 0]).all()
    # Together they span the whole model.
    assert by_label["A1"].xs[0] == surface.xs[0] and by_label["A3"].xs[-1] == surface.xs[-1]
    assert by_label["A1"].ys[0] == surface.ys[0] and by_label["B1"].ys[-1] == surface.ys[-1]
    # Tiles have about the same size.
    widths = [part.xs[-1] - part.xs[0] for _, part in tiles]
    assert max(widths) - min(widths) <= 1.0 + 1e-9


def test_blended_tiles_add_up_to_the_whole_model(
    surface: Surface, footprint: shapely.Geometry, lines: list[shapely.LineString]
) -> None:
    whole = build_parts(surface, footprint, PARAMS)
    tiles = build_tiles(surface, footprint, lines, PARAMS, "blended", cols=2, rows=2)

    assert len(tiles) == 4
    for tile in tiles:
        assert to_trimesh(tile.terrain).is_watertight
        x0, y0, x1, y1 = tile.bounds_mm
        bx0, by0, _, bx1, by1, _ = tile.terrain.bounding_box()
        assert (bx0, by0, bx1, by1) == pytest.approx((x0, y0, x1, y1), abs=1e-3)
        if tile.route is not None:
            assert to_trimesh(tile.route).is_watertight

    terrain = sum(t.terrain.volume() for t in tiles)
    route = sum(t.route.volume() for t in tiles if t.route is not None)
    assert terrain == pytest.approx(whole.terrain.volume(), rel=1e-4)
    assert route == pytest.approx(whole.route.volume(), rel=1e-3)
    # Neighbouring tiles touch but do not overlap.
    for i, a in enumerate(tiles):
        for b in tiles[i + 1 :]:
            assert abs((a.terrain ^ b.terrain).volume()) < 1e-3


def test_inlay_tiles(
    surface: Surface, footprint: shapely.Geometry, lines: list[shapely.LineString]
) -> None:
    whole = build_inlay(surface, footprint, lines, PARAMS)
    tiles = build_tiles(surface, footprint, lines, PARAMS, "inlay", cols=2, rows=1)

    pieces = [p for t in tiles for p in t.pieces]
    # Pieces crossing the tile edge are cut in two, so there are at least as many.
    assert len(pieces) >= len(whole.pieces)
    for tile in tiles:
        assert to_trimesh(tile.terrain).is_watertight
        for piece in tile.pieces:
            assert to_trimesh(piece.on_bed()).is_watertight
            assert abs((tile.terrain ^ piece.solid).volume()) < 1e-3
            # Every piece stays inside its tile.
            x0, _, x1, _ = tile.bounds_mm
            bx0, _, _, bx1, _, _ = piece.solid.bounding_box()
            assert x0 - 1e-6 <= bx0 and bx1 <= x1 + 1e-6


def test_tile_without_route(surface: Surface, lines: list[shapely.LineString]) -> None:
    # A short route in the south-west corner only.
    corner = shapely.LineString([(5, 5), (15, 8)]).buffer(0.8)
    tiles = build_tiles(surface, corner, lines, PARAMS, "blended", cols=2, rows=2)
    with_route = {t.label for t in tiles if t.route is not None}
    assert with_route == {"B1"}
    assert all(to_trimesh(t.terrain).is_watertight for t in tiles)


@pytest.fixture(scope="module")
def split_surface(hf: Heightfield) -> Surface:
    from dataclasses import replace

    from app.tiles import MIN_SPLIT_BASE_MM

    return terrain_surface(hf, replace(PARAMS, base_mm=MIN_SPLIT_BASE_MM))


def test_pin_holes_on_every_shared_edge(split_surface: Surface) -> None:
    from app.tiles import pin_holes

    tiles = build_tiles(split_surface, shapely.Polygon(), [], PARAMS, "blended", cols=2, rows=2)
    holes = pin_holes(tiles)
    # 2 x 2 tiles share 4 edges, each ~50 mm long: two pins per edge.
    assert len(holes) == 8
    assert sum(h.axis == "x" for h in holes) == 4


def test_assembly_aids(split_surface: Surface, footprint: shapely.Geometry) -> None:
    from app.tiles import PIN_CENTER_Z_MM, _hole_solid, add_assembly_aids, pin_solid

    tiles = build_tiles(split_surface, footprint, [], PARAMS, "blended", cols=2, rows=1)
    before = [t.terrain.volume() for t in tiles]
    holes = add_assembly_aids(tiles, clearance_mm=0.15)
    assert len(holes) == 2

    for tile, volume in zip(tiles, before, strict=True):
        assert to_trimesh(tile.terrain).is_watertight
        assert tile.terrain.volume() < volume
        # The label is engraved under the tile: the bottom layer has less material.
        x0, y0, x1, y1 = tile.bounds_mm
        assert tile.terrain.slice(0.3).area() < (x1 - x0) * (y1 - y0) - 20
        assert tile.terrain.slice(1.0).area() == pytest.approx(
            (x1 - x0) * (y1 - y0) - 2 * 3.3 * 5.5, rel=0.01
        )

    # A pin sitting in its hole touches neither tile (clearance) and reaches into both.
    hole = holes[0]
    pin = pin_solid()
    x0, _, _, x1, _, _ = pin.bounding_box()
    centred = pin.translate((hole.x - (x0 + x1) / 2, hole.y, PIN_CENTER_Z_MM - 1.2))
    for tile in tiles:
        assert abs((tile.terrain ^ centred).volume()) < 1e-6
        assert (_hole_solid(hole, 0.15) ^ centred).volume() > 20
