"""Full pipeline: track + parameters → printable model, preview and statistics."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Literal

from .dem import TileSource
from .export import ROUTE_COLOR, PrintObject, blended_layout, inlay_layout
from .gpx import Track, compute_frame
from .heightfield import build_heightfield
from .inlay import build_inlay
from .route import build_parts, route_centerlines, route_footprint
from .terrain import ModelParams, terrain_surface

Mode = Literal["blended", "inlay"]

# Defaults that depend on the print mode: separate pieces are more fragile.
DEFAULT_ROUTE_WIDTH_MM = {"blended": 1.2, "inlay": 1.6}
# The browser preview is simplified to this tolerance: ~10x smaller, visually identical.
PREVIEW_TOLERANCE_MM = 0.05


@dataclass(frozen=True)
class BuildOptions:
    mode: Mode = "blended"
    size_mm: float = 180.0  # longest side of the model
    margin_pct: float = 10.0
    resolution_mm: float = 0.25
    params: ModelParams = field(default_factory=ModelParams)


@dataclass
class BuiltModel:
    title: str
    options: BuildOptions
    layout: list[PrintObject]  # what gets printed, laid out on the plate(s)
    preview: list[PrintObject]  # assembled view for the 3D preview
    stats: dict


def build(
    track: Track, options: BuildOptions, title: str, source: TileSource | None = None
) -> BuiltModel:
    started = time.perf_counter()
    params = options.params
    frame = compute_frame(track, margin_pct=options.margin_pct)
    hf = build_heightfield(frame, options.size_mm, options.resolution_mm, source=source)
    surface = terrain_surface(hf, params)
    lines = route_centerlines(track, hf)
    footprint = route_footprint(track, hf, params.route_width_mm)

    if options.mode == "blended":
        parts = build_parts(surface, footprint, params)
        layout = blended_layout(parts, name=title)
        preview = layout
        terrain, pieces = parts.terrain, [parts.route]
    else:
        inlay = build_inlay(surface, footprint, lines, params)
        layout = inlay_layout(inlay)
        # Assembled: the pieces sitting in their slots.
        preview = [
            layout[0],
            PrintObject(
                name="route pieces",
                parts=[(f"piece {i}", p.solid) for i, p in enumerate(inlay.pieces, start=1)],
                color=ROUTE_COLOR,
            ),
        ]
        terrain, pieces = inlay.terrain, [p.solid for p in inlay.pieces]

    preview = [
        replace(
            obj, parts=[(name, solid.simplify(PREVIEW_TOLERANCE_MM)) for name, solid in obj.parts]
        )
        for obj in preview
    ]

    x0, y0, z0, x1, y1, z1 = terrain.bounding_box()
    stats = {
        "size_mm": [round(x1 - x0, 1), round(y1 - y0, 1), round(z1 - z0, 1)],
        "scale": f"1:{round(1000 / hf.scale):,}",
        "real_size_km": [round(frame.width_m / 1000, 2), round(frame.height_m / 1000, 2)],
        "terrain_volume_cm3": round(terrain.volume() / 1000, 1),
        "route_volume_cm3": round(sum(p.volume() for p in pieces) / 1000, 2),
        "route_parts": len(pieces),
        "triangles": terrain.num_tri() + sum(p.num_tri() for p in pieces),
        "build_seconds": round(time.perf_counter() - started, 1),
    }
    return BuiltModel(title=title, options=options, layout=layout, preview=preview, stats=stats)
