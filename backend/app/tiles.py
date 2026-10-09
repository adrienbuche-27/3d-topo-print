"""Grid splitting: models larger than the print bed, printed as a grid of tiles.

The elevation grid of the whole model is computed once; each tile takes a
slice of it. Neighbouring tiles share their edge row/column of grid nodes, so
the terrain surfaces meet exactly. Each tile is then built like a small model
of its own (blended or inlay), with the route clipped to the tile: the route
continues across tile edges without a step.

Tiles are labelled like a map grid: rows A, B, C… from north to south,
columns 1, 2, 3… from west to east (A1 is the north-west corner).
"""

from __future__ import annotations

import math
import string
from dataclasses import dataclass, field

import shapely
from manifold3d import Manifold, OpType
from shapely.geometry import LineString, box

from .engrave import label_outline
from .inlay import InlayPiece, SurfaceHeight, build_inlay
from .route import CLEAN_TOLERANCE_MM, OVERHANG_MM, build_parts, cross_section
from .terrain import ModelParams, Surface, terrain_solid

# Largest tile printed on the A1 (256 mm bed), leaving room for the brim and handling.
DEFAULT_MAX_TILE_MM = 240.0


@dataclass(frozen=True)
class TileSpec:
    row: int  # 0 = northernmost
    col: int  # 0 = westernmost
    # Grid node index ranges (inclusive) of this tile in the whole-model surface.
    rows: tuple[int, int]
    cols: tuple[int, int]

    @property
    def label(self) -> str:
        return f"{string.ascii_uppercase[self.row]}{self.col + 1}"


@dataclass
class Tile:
    spec: TileSpec
    surface: Surface
    terrain: Manifold
    # Blended: the route insert (possibly empty). Inlay: not used.
    route: Manifold | None = None
    # Inlay: route pieces with flat bottoms.
    pieces: list[InlayPiece] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def bounds_mm(self) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1) of the tile in model coordinates."""
        s = self.surface
        return float(s.xs[0]), float(s.ys[-1]), float(s.xs[-1]), float(s.ys[0])


def grid_size(
    width_mm: float, height_mm: float, max_tile_mm: float = DEFAULT_MAX_TILE_MM
) -> tuple[int, int]:
    """Smallest (columns, rows) grid whose tiles fit within `max_tile_mm`."""
    if max_tile_mm <= 0:
        raise ValueError("The tile size must be positive.")
    return max(1, math.ceil(width_mm / max_tile_mm - 1e-9)), max(
        1, math.ceil(height_mm / max_tile_mm - 1e-9)
    )


def _split(count: int, parts: int) -> list[tuple[int, int]]:
    """Split node indices 0..count-1 into `parts` ranges sharing their end nodes."""
    cells = count - 1
    if parts > cells:
        raise ValueError("Too many tiles for the grid resolution.")
    edges = [round(k * cells / parts) for k in range(parts + 1)]
    return list(zip(edges[:-1], edges[1:], strict=True))


def plan_tiles(surface: Surface, cols: int, rows: int) -> list[tuple[TileSpec, Surface]]:
    """Tile specs and their slices of the whole-model surface, row by row from the north."""
    n_rows, n_cols = surface.z.shape
    tiles = []
    for r, (r0, r1) in enumerate(_split(n_rows, rows)):
        for c, (c0, c1) in enumerate(_split(n_cols, cols)):
            spec = TileSpec(row=r, col=c, rows=(r0, r1), cols=(c0, c1))
            part = Surface(
                xs=surface.xs[c0 : c1 + 1],
                ys=surface.ys[r0 : r1 + 1],
                z=surface.z[r0 : r1 + 1, c0 : c1 + 1],
            )
            tiles.append((spec, part))
    return tiles


def _clip(footprint: shapely.Geometry, surface: Surface) -> shapely.Geometry:
    # Keep the overhang the builders rely on to avoid coplanar faces at the tile edge.
    o = 2 * OVERHANG_MM
    x0, x1 = float(surface.xs[0]), float(surface.xs[-1])
    y0, y1 = float(surface.ys[-1]), float(surface.ys[0])
    return footprint.intersection(box(x0 - o, y0 - o, x1 + o, y1 + o))


def build_tiles(
    surface: Surface,
    footprint: shapely.Geometry,
    lines: list[LineString],
    params: ModelParams,
    mode: str,
    cols: int,
    rows: int,
) -> list[Tile]:
    """Build every tile of a split model (blended or inlay)."""
    height = SurfaceHeight(surface) if mode == "inlay" else None
    tiles = []
    for spec, part in plan_tiles(surface, cols, rows):
        clipped = _clip(footprint, part)
        if clipped.is_empty:
            tiles.append(Tile(spec=spec, surface=part, terrain=terrain_solid(part)))
        elif mode == "blended":
            built = build_parts(part, clipped, params, require_route=False)
            route = None if built.route.is_empty() else built.route
            tiles.append(Tile(spec=spec, surface=part, terrain=built.terrain, route=route))
        else:
            inlay = build_inlay(part, clipped, lines, params, require_route=False, height=height)
            tiles.append(Tile(spec=spec, surface=part, terrain=inlay.terrain, pieces=inlay.pieces))
    return tiles


# --- Assembly aids --------------------------------------------------------------

# Split models need room for the alignment pins at the bottom of every tile edge.
MIN_SPLIT_BASE_MM = 6.0
PIN_DIAMETER_MM = 3.0
PIN_HOLE_DEPTH_MM = 5.5  # into each tile
PIN_CENTER_Z_MM = 2.6  # below the deepest groove or slot floor (base 6 - depth 1)
PIN_FLAT_MM = 0.3  # flat cut along the pin so it prints lying down
LABEL_DEPTH_MM = 0.6
LABEL_HEIGHT_MM = 10.0


@dataclass(frozen=True)
class PinHole:
    """Horizontal hole centred on a tile seam; `axis` is the direction across the seam."""

    x: float
    y: float
    axis: str  # "x" (seam between columns) or "y" (seam between rows)


def _edge_positions(start: float, end: float) -> list[float]:
    length = end - start
    if length < 40:
        return [start + length / 2]
    return [start + length / 4, start + 3 * length / 4]


def pin_holes(tiles: list[Tile]) -> list[PinHole]:
    """Two pin holes along every edge shared by two tiles (one on short edges)."""
    by_pos = {(t.spec.row, t.spec.col): t for t in tiles}
    holes = []
    for (row, col), tile in by_pos.items():
        x0, y0, x1, y1 = tile.bounds_mm
        if (row, col + 1) in by_pos:  # east neighbour
            holes += [PinHole(x1, y, "x") for y in _edge_positions(y0, y1)]
        if (row + 1, col) in by_pos:  # south neighbour
            holes += [PinHole(x, y0, "y") for x in _edge_positions(x0, x1)]
    return holes


def _along(axis: str, solid: Manifold) -> Manifold:
    """Turn a solid built along z to lie along x or y."""
    return solid.rotate((0, 90, 0)) if axis == "x" else solid.rotate((-90, 0, 0))


def _hole_solid(hole: PinHole, clearance_mm: float) -> Manifold:
    radius = PIN_DIAMETER_MM / 2 + clearance_mm
    length = 2 * PIN_HOLE_DEPTH_MM
    cylinder = Manifold.cylinder(length, radius, circular_segments=32).translate(
        (0, 0, -length / 2)
    )
    return _along(hole.axis, cylinder).translate((hole.x, hole.y, PIN_CENTER_Z_MM))


def pin_solid() -> Manifold:
    """One alignment pin lying along x, with a flat underside on the bed (z = 0)."""
    radius = PIN_DIAMETER_MM / 2
    # Slightly shorter than the two holes together, so the tiles close fully.
    length = 2 * PIN_HOLE_DEPTH_MM - 1.0
    pin = _along("x", Manifold.cylinder(length, radius, circular_segments=32))
    flat = Manifold.cube((length, 2 * radius, 2 * radius)).translate(
        (0, -radius, -radius + PIN_FLAT_MM)
    )
    return (pin ^ flat).translate((0, 0, radius - PIN_FLAT_MM))


def add_assembly_aids(tiles: list[Tile], clearance_mm: float) -> list[PinHole]:
    """Drill the pin holes and engrave each tile's label and north arrow underneath."""
    holes = pin_holes(tiles)
    hole_solids = [(h, _hole_solid(h, clearance_mm)) for h in holes]
    for tile in tiles:
        x0, y0, x1, y1 = tile.bounds_mm
        cut = [s for h, s in hole_solids if x0 - 1 <= h.x <= x1 + 1 and y0 - 1 <= h.y <= y1 + 1]

        height = min(LABEL_HEIGHT_MM, (x1 - x0) / 6, (y1 - y0) / 4)
        outline = label_outline(tile.label, height, ((x0 + x1) / 2, (y0 + y1) / 2))
        # From just below the bed up to the engraving depth.
        cut.append(
            Manifold.extrude(cross_section(outline), LABEL_DEPTH_MM + 1).translate((0, 0, -1))
        )
        tile.terrain = (tile.terrain - Manifold.batch_boolean(cut, OpType.Add)).simplify(
            CLEAN_TOLERANCE_MM
        )
    return holes
