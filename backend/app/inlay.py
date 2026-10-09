"""Inlay mode: terrain and route printed separately, then assembled.

The route insert of the blended mode floats in 3D and cannot be printed on its
own. Here the route is split into short pieces, each with a flat bottom, so
every piece prints flat on the bed without supports:

- Walking along the route centreline, a new piece starts each time the
  terrain under it has changed by `inlay_piece_height_mm` (going up or down).
  Each point of the route band belongs to its nearest centreline point, so the
  cuts run square across the road and hairpin legs stay apart.
- Each region between cuts gets a slot in the terrain with a flat floor
  `groove_depth_mm` below its lowest point, so the slot floor steps up the
  mountain. The piece is the region shrunk by `inlay_clearance_mm` on every
  side: its flat bottom rests on the slot floor and its top follows the
  terrain plus `route_raise_mm`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from manifold3d import JoinType, Manifold, OpType
from scipy.interpolate import RegularGridInterpolator
from scipy.spatial import cKDTree
from shapely.geometry import LineString

from .route import (
    CLEAN_TOLERANCE_MM,
    OVERHANG_MM,
    RouteError,
    cross_section,
    raised_solid,
)
from .terrain import ModelParams, Surface, terrain_solid

# Pieces smaller than this are dropped: they would be lost on the bed (their slot stays).
MIN_PIECE_VOLUME_MM3 = 1.0
# Sampling step along the centreline when looking for cut positions.
CUT_SAMPLING_MM = 0.2
# Slot cutters are widened by this much so neighbouring ones overlap slightly and no
# sliver of terrain is left standing between them.
SLOT_OVERLAP_MM = 0.01


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


class _SurfaceHeight:
    """Terrain height (print mm) at arbitrary x/y."""

    def __init__(self, surface: Surface) -> None:
        # The interpolator needs increasing coordinates; ys run north → south.
        self._f = RegularGridInterpolator(
            (surface.ys[::-1], surface.xs), surface.z[::-1], bounds_error=False, fill_value=None
        )

    def __call__(self, xy: np.ndarray) -> np.ndarray:
        return self._f(np.column_stack([xy[:, 1], xy[:, 0]]))


def cut_labels(xy: np.ndarray, z: np.ndarray, piece_height: float) -> np.ndarray:
    """Piece number of each sample along a line: a new piece starts each time the
    terrain has changed by `piece_height` since the start of the current one."""
    labels = np.zeros(len(z), dtype=int)
    label = 0
    lo = hi = z[0]
    for i in range(1, len(z)):
        lo, hi = min(lo, z[i]), max(hi, z[i])
        if hi - lo > piece_height:
            label += 1
            lo = hi = z[i]
        labels[i] = label
    return labels


def split_regions(
    footprint: shapely.Geometry,
    lines: list[LineString],
    height: _SurfaceHeight,
    params: ModelParams,
) -> list[shapely.Polygon]:
    """Split the route band into regions covering at most `inlay_piece_height_mm` of relief.

    Every point of the band belongs to its nearest centreline sample (a Voronoi
    partition), and samples are grouped into pieces along the route. On a
    straight road the boundaries are square across it; at hairpins each leg
    keeps its own region. Where the road is ridden twice (up and down), only the
    first pass is used.
    """
    points, labels = [], []
    next_label = 0
    for line in lines:
        n = max(int(line.length / CUT_SAMPLING_MM), 1) + 1
        xy = np.array([line.interpolate(v).coords[0] for v in np.linspace(0, line.length, n)])
        line_labels = cut_labels(xy, height(xy), params.inlay_piece_height_mm) + next_label
        next_label = int(line_labels.max()) + 1
        points.append(xy)
        labels.append(line_labels)
    xy = np.concatenate(points)
    label = np.concatenate(labels)

    # Drop samples where an earlier, distant part of the route already passed.
    tree = cKDTree(xy)
    keep = np.ones(len(xy), dtype=bool)
    same_pass = 2 * params.route_width_mm / CUT_SAMPLING_MM  # samples
    for i, neighbours in enumerate(tree.query_ball_point(xy, params.route_width_mm / 2)):
        if any(j < i - same_pass and keep[j] for j in neighbours):
            keep[i] = False
    xy, label = xy[keep], label[keep]

    cells = shapely.voronoi_polygons(
        shapely.MultiPoint(xy), extend_to=footprint.envelope.buffer(10), ordered=True
    )
    regions = []
    for value in np.unique(label):
        group = shapely.unary_union([cells.geoms[i] for i in np.flatnonzero(label == value)])
        part = group.intersection(footprint)
        regions.extend(g for g in getattr(part, "geoms", [part]) if g.geom_type == "Polygon")
    return regions


def _min_height(region: shapely.Polygon, height: _SurfaceHeight, surface: Surface) -> float:
    """Lowest terrain height over a region: grid nodes inside it plus its outline."""
    x0, y0, x1, y1 = region.bounds
    xs = surface.xs[(surface.xs >= x0) & (surface.xs <= x1)]
    ys = surface.ys[(surface.ys >= y0) & (surface.ys <= y1)]
    samples = [np.asarray(region.exterior.segmentize(CUT_SAMPLING_MM).coords)]
    if len(xs) and len(ys):
        gx, gy = np.meshgrid(xs, ys)
        inside = shapely.contains_xy(region, gx, gy)
        samples.append(np.column_stack([gx[inside], gy[inside]]))
    return float(height(np.concatenate(samples)).min())


def build_inlay(
    surface: Surface,
    footprint: shapely.Geometry,
    lines: list[LineString],
    params: ModelParams,
) -> InlayParts:
    """Terrain with stepped slots, and flat-bottomed route pieces that fit them."""
    if params.groove_depth_mm >= params.base_mm:
        raise RouteError("The groove must be shallower than the base thickness.")
    if params.inlay_piece_height_mm <= 0:
        raise RouteError("The piece height must be positive.")

    height = _SurfaceHeight(surface)
    terrain = terrain_solid(surface)
    raised = raised_solid(surface, params.route_raise_mm)
    top = float(surface.z.max()) + params.route_raise_mm + OVERHANG_MM

    cutters = []
    pieces: list[InlayPiece] = []
    for region in split_regions(footprint, lines, height, params):
        bottom = _min_height(region, height, surface) - params.groove_depth_mm
        slot = cross_section(region.buffer(SLOT_OVERLAP_MM, join_style=2))
        cutters.append(Manifold.extrude(slot, top - bottom).translate((0, 0, bottom)))

        insert = cross_section(region).offset(-params.inlay_clearance_mm, JoinType.Round)
        if insert.is_empty():
            continue
        solid = Manifold.extrude(insert, top - bottom).translate((0, 0, bottom)) ^ raised
        for part in solid.decompose():
            if part.volume() >= MIN_PIECE_VOLUME_MM3:
                pieces.append(InlayPiece(solid=part.simplify(CLEAN_TOLERANCE_MM), bottom_z=bottom))

    if not pieces:
        raise RouteError("The route produced no printable inlay pieces.")
    terrain = (terrain - Manifold.batch_boolean(cutters, OpType.Add)).simplify(CLEAN_TOLERANCE_MM)
    return InlayParts(terrain=terrain, pieces=pieces)


def build_fit_test(params: ModelParams) -> InlayParts:
    """Small calibration print for the inlay fit: a 40 x 24 mm block on a slope.

    An S-shaped route climbs the slope over two pieces, so the test covers
    curves, a slot floor step and the gap between two pieces, all with the
    same width, clearance and depths as the real model.
    """
    width, depth, spacing = 40.0, 24.0, 0.25
    xs = np.linspace(0.0, width, round(width / spacing) + 1)
    ys = np.linspace(depth, 0.0, round(depth / spacing) + 1)
    # Slope rising along x: the S-curve climbs 1.5 piece heights.
    rise = 1.5 * params.inlay_piece_height_mm
    z = params.base_mm + np.tile(xs / width * rise, (len(ys), 1))
    surface = Surface(xs=xs, ys=ys, z=z)

    t = np.linspace(0.0, 1.0, 60)
    path = LineString(np.column_stack([5 + 30 * t, depth / 2 + 6 * np.sin(2 * np.pi * t)]))
    footprint = path.buffer(params.route_width_mm / 2, quad_segs=4)
    return build_inlay(surface, footprint, [path], params)
