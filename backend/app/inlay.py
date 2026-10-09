"""Inlay mode: terrain and route printed separately, then assembled.

The route insert of the blended mode floats in 3D and cannot be printed on its
own. Here the route is split into short pieces, each with a flat bottom, so
every piece prints flat on the bed without supports:

- The route band is cut into elevation bands of `inlay_piece_height_mm` along
  the terrain's contour lines. Each connected part of a band is one piece.
- A piece's flat bottom sits `groove_depth_mm` below the band's lowest
  contour; its top follows the terrain plus `route_raise_mm`.
- The terrain gets a slot per band with a matching flat floor, so the slot
  floor steps up the mountain. Pieces are `inlay_clearance_mm` smaller than
  their slot on every side.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import shapely
from manifold3d import CrossSection, JoinType, Manifold, OpType

from .route import (
    CLEAN_TOLERANCE_MM,
    OVERHANG_MM,
    RouteError,
    cross_section,
    padded_solid,
    raised_solid,
)
from .terrain import ModelParams, Surface, terrain_solid

# Pieces smaller than this are dropped: they would be lost on the bed (their slot stays).
MIN_PIECE_VOLUME_MM3 = 1.0


@dataclass
class InlayPiece:
    solid: Manifold  # in its assembled position
    bottom_z: float  # height of its flat bottom (and of its slot floor)

    def on_bed(self) -> Manifold:
        """The piece lowered onto the print bed, keeping its x/y position."""
        return self.solid.translate((0.0, 0.0, -self.bottom_z))


@dataclass
class InlayParts:
    terrain: Manifold
    pieces: list[InlayPiece]


def band_levels(z_min: float, z_max: float, piece_height: float) -> list[float]:
    """Lower contour of each elevation band, from z_min upward, covering z_max."""
    count = max(1, math.ceil((z_max - z_min) / piece_height - 1e-9))
    return [z_min + k * piece_height for k in range(count)]


def build_inlay(surface: Surface, footprint: shapely.Geometry, params: ModelParams) -> InlayParts:
    """Terrain with stepped slots, and flat-bottomed route pieces that fit them."""
    if params.groove_depth_mm >= params.base_mm:
        raise RouteError("The groove must be shallower than the base thickness.")
    if params.inlay_piece_height_mm <= 0:
        raise RouteError("The piece height must be positive.")

    o = OVERHANG_MM
    terrain = terrain_solid(surface)
    raised = raised_solid(surface, params.route_raise_mm)
    # Padded copy of the terrain: its contour slices reach past the model edges, so
    # slot walls never coincide with the terrain's outer walls.
    contour_source = padded_solid(surface, 0.0)

    slot_outline = cross_section(footprint)
    z_min, z_max = _surface_range_under(surface, footprint)
    levels = band_levels(z_min, z_max, params.inlay_piece_height_mm)
    top = float(surface.z.max()) + params.route_raise_mm + o

    (x0, y0), (x1, y1) = _bounds(surface)
    everything = CrossSection.square((x1 - x0 + 4 * o, y1 - y0 + 4 * o)).translate(
        (x0 - 2 * o, y0 - 2 * o)
    )

    cutters = []
    pieces: list[InlayPiece] = []
    for k, level in enumerate(levels):
        # Region where the terrain lies in [level, next level): the lowest band also takes
        # anything below z_min, the highest anything above its top.
        above = everything if k == 0 else contour_source.slice(level)
        if k + 1 < len(levels):
            above = above - contour_source.slice(levels[k + 1])
        slot = slot_outline ^ above
        if slot.is_empty():
            continue

        bottom = level - params.groove_depth_mm
        height = top - bottom
        cutters.append(Manifold.extrude(slot, height).translate((0, 0, bottom)))

        insert = slot.offset(-params.inlay_clearance_mm, JoinType.Round)
        if insert.is_empty():
            continue
        solid = Manifold.extrude(insert, height).translate((0, 0, bottom)) ^ raised
        for part in solid.decompose():
            if part.volume() >= MIN_PIECE_VOLUME_MM3:
                pieces.append(InlayPiece(solid=part.simplify(CLEAN_TOLERANCE_MM), bottom_z=bottom))

    if not pieces:
        raise RouteError("The route produced no printable inlay pieces.")
    terrain = (terrain - Manifold.batch_boolean(cutters, OpType.Add)).simplify(CLEAN_TOLERANCE_MM)
    return InlayParts(terrain=terrain, pieces=pieces)


def _bounds(surface: Surface) -> tuple[tuple[float, float], tuple[float, float]]:
    return (float(surface.xs[0]), float(surface.ys[-1])), (
        float(surface.xs[-1]),
        float(surface.ys[0]),
    )


def _surface_range_under(surface: Surface, footprint: shapely.Geometry) -> tuple[float, float]:
    """Lowest and highest terrain height at the grid nodes inside the route band."""
    gx, gy = np.meshgrid(surface.xs, surface.ys)
    inside = shapely.contains_xy(footprint, gx, gy)
    if not inside.any():  # band narrower than the grid: fall back to the nearest nodes
        inside = shapely.contains_xy(footprint.buffer(max(np.diff(surface.xs))), gx, gy)
    z = surface.z[inside]
    return float(z.min()), float(z.max())


def build_fit_test(params: ModelParams) -> InlayParts:
    """Small calibration print for the inlay fit: a 40 x 24 mm block on a slope.

    An S-shaped route crosses the slope over two elevation bands, so the test
    covers curves, a slot floor step and the gap between two pieces, all with
    the same width, clearance and depths as the real model.
    """
    width, depth, spacing = 40.0, 24.0, 0.25
    xs = np.linspace(0.0, width, round(width / spacing) + 1)
    ys = np.linspace(depth, 0.0, round(depth / spacing) + 1)
    # Slope rising along x, so the S-curve climbs 1.5 piece heights.
    rise = 1.5 * params.inlay_piece_height_mm
    z = params.base_mm + np.tile(xs / width * rise, (len(ys), 1))
    surface = Surface(xs=xs, ys=ys, z=z)

    t = np.linspace(0.0, 1.0, 60)
    path = shapely.LineString(np.column_stack([5 + 30 * t, depth / 2 + 6 * np.sin(2 * np.pi * t)]))
    footprint = path.buffer(params.route_width_mm / 2, quad_segs=4)
    return build_inlay(surface, footprint, params)
