"""Route insert: GPX track → coloured body set into the terrain.

The route is a band of `route_width_mm` that sinks `groove_depth_mm` into the
terrain and stands `route_raise_mm` above it. The terrain gets a matching
groove, so the two bodies share their faces exactly and can be printed
together in two colours without clearance.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from manifold3d import CrossSection, FillRule, Manifold
from pyproj import CRS
from shapely.geometry import LineString, MultiLineString, box

from .geo import to_crs
from .gpx import Track, simplify_track
from .heightfield import Heightfield
from .mesh import grid_solid, to_manifold
from .terrain import ModelParams, Surface, terrain_solid, terrain_surface

# Track simplification tolerance, in print millimetres: far below what a 0.4 mm nozzle shows.
SIMPLIFY_TOLERANCE_MM = 0.05
# How far helper solids reach past the model, so their faces never coincide with its walls.
OVERHANG_MM = 1.0
# Booleans can leave slivers with vertices < 0.1 µm apart, which slicers merge into
# degenerate triangles. Collapsing edges below 1 µm removes them without visible change.
CLEAN_TOLERANCE_MM = 1e-3


class RouteError(ValueError):
    """Raised when the route cannot be turned into an insert."""


@dataclass
class ModelParts:
    terrain: Manifold
    route: Manifold


def route_footprint(track: Track, hf: Heightfield, width_mm: float) -> shapely.Geometry:
    """2D outline of the route band in print millimetres (round caps and joins)."""
    tolerance_m = SIMPLIFY_TOLERANCE_MM / hf.scale
    simplified = simplify_track(track, tolerance_m)
    fwd = to_crs(CRS.from_epsg(hf.frame.epsg))

    lines = []
    for seg in simplified.segments:
        x, y = fwd.transform([p.lon for p in seg], [p.lat for p in seg])
        px, py = hf.to_print_xy(np.asarray(x), np.asarray(y))
        lines.append(LineString(np.column_stack([px, py])))

    band = MultiLineString(lines).buffer(width_mm / 2, quad_segs=4)
    # Keep a little beyond the model edge; the cut against the terrain trims it exactly.
    o = OVERHANG_MM
    band = band.intersection(box(-o, -o, hf.width_mm + o, hf.height_mm + o))
    if band.is_empty:
        raise RouteError("The route does not cross the print area.")
    return band


def _cross_section(footprint: shapely.Geometry) -> CrossSection:
    contours = []
    for poly in getattr(footprint, "geoms", [footprint]):
        if poly.geom_type != "Polygon" or poly.is_empty:
            continue
        contours.append(np.asarray(poly.exterior.coords)[:-1])
        contours.extend(np.asarray(ring.coords)[:-1] for ring in poly.interiors)
    return CrossSection(contours, FillRule.EvenOdd)


def build_parts(surface: Surface, footprint: shapely.Geometry, params: ModelParams) -> ModelParts:
    """Cut the route groove into the terrain and build the matching insert."""
    if params.groove_depth_mm >= params.base_mm:
        raise RouteError("The groove must be shallower than the base thickness.")

    o = OVERHANG_MM
    xs, ys, z = surface.xs, surface.ys, surface.z
    terrain = terrain_solid(surface)

    # Surface of the groove bottom, padded past the model edges so that its walls
    # and bottom never coincide with the terrain's (coplanar faces make booleans fragile).
    xs_p = np.concatenate([[xs[0] - o], xs, [xs[-1] + o]])
    ys_p = np.concatenate([[ys[0] + o], ys, [ys[-1] - o]])
    groove_floor = to_manifold(
        *grid_solid(xs_p, ys_p, np.pad(z - params.groove_depth_mm, 1, mode="edge"), bottom_z=-o)
    )
    # Terrain raised by the route height, limited to the model footprint.
    raised = to_manifold(*grid_solid(xs, ys, z + params.route_raise_mm))

    top = float(z.max()) + params.route_raise_mm + o
    prism = Manifold.extrude(_cross_section(footprint), top + 2 * o).translate((0, 0, -2 * o))
    cutter = prism - groove_floor  # everything above the groove floor, inside the band

    route = (cutter ^ raised).simplify(CLEAN_TOLERANCE_MM)
    terrain = (terrain - cutter).simplify(CLEAN_TOLERANCE_MM)
    if route.is_empty():
        raise RouteError("The route insert is empty.")
    return ModelParts(terrain=terrain, route=route)


def build_model(hf: Heightfield, track: Track, params: ModelParams) -> ModelParts:
    """Full printable model: terrain block with the route insert."""
    surface = terrain_surface(hf, params)
    footprint = route_footprint(track, hf, params.route_width_mm)
    return build_parts(surface, footprint, params)
