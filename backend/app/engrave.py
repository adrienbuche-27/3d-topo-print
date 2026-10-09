"""Engraved markings: a small stroke font for tile labels, and a north arrow.

Glyphs are polylines on a 4 x 6 grid (x right, y up), thickened into
polygons. Only the characters used by tile labels are defined: rows A–H and
columns 1–9.
"""

from __future__ import annotations

import shapely
from shapely import affinity
from shapely.geometry import LineString, MultiLineString

_GLYPHS: dict[str, list[list[tuple[float, float]]]] = {
    "A": [[(0, 0), (0, 4), (2, 6), (4, 4), (4, 0)], [(0, 3), (4, 3)]],
    "B": [
        [(0, 0), (0, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0, 3)],
        [(3, 3), (4, 2), (4, 1), (3, 0), (0, 0)],
    ],
    "C": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1)]],
    "D": [[(0, 0), (0, 6), (2, 6), (4, 4), (4, 2), (2, 0), (0, 0)]],
    "E": [[(4, 6), (0, 6), (0, 0), (4, 0)], [(0, 3), (3, 3)]],
    "F": [[(4, 6), (0, 6), (0, 0)], [(0, 3), (3, 3)]],
    "G": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1), (4, 3), (2, 3)]],
    "H": [[(0, 0), (0, 6)], [(4, 0), (4, 6)], [(0, 3), (4, 3)]],
    "0": [[(1, 0), (3, 0), (4, 1), (4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0)]],
    "1": [[(1, 5), (2, 6), (2, 0)], [(1, 0), (3, 0)]],
    "2": [[(0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (0, 0), (4, 0)]],
    "3": [
        [(0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3), (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)],
        [(1, 3), (3, 3)],
    ],
    "4": [[(3, 0), (3, 6), (0, 2), (4, 2)]],
    "5": [[(4, 6), (0, 6), (0, 3), (3, 3), (4, 2), (4, 1), (3, 0), (0, 0)]],
    "6": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1), (4, 2), (3, 3), (0, 3)]],
    "7": [[(0, 6), (4, 6), (1, 0)]],
    "8": [
        [(1, 3), (0, 4), (0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3), (1, 3)],
        [(1, 3), (0, 2), (0, 1), (1, 0), (3, 0), (4, 1), (4, 2), (3, 3)],
    ],
    "9": [[(4, 3), (1, 3), (0, 4), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1, 0), (0, 1)]],
    # North arrow: shaft and head.
    "^": [[(2, 0), (2, 6)], [(0, 4), (2, 6), (4, 4)]],
}
_ADVANCE = 6.0  # glyph width (4) + spacing (2), in grid units
_GRID_HEIGHT = 6.0


def text_outline(text: str, height_mm: float) -> shapely.Geometry:
    """Engraving outline of `text` (characters A–H, 0–9 and ^ for a north arrow),
    with its lower-left corner at the origin."""
    unit = height_mm / _GRID_HEIGHT
    strokes = []
    for i, char in enumerate(text):
        if char not in _GLYPHS:
            raise ValueError(f"No engraving glyph for {char!r}")
        for stroke in _GLYPHS[char]:
            strokes.append(LineString([((x + i * _ADVANCE) * unit, y * unit) for x, y in stroke]))
    # Stroke width ~0.9 grid unit: at 10 mm high, 1.5 mm (about 4 nozzle widths).
    return MultiLineString(strokes).buffer(0.45 * unit, quad_segs=4)


def label_outline(label: str, height_mm: float, center: tuple[float, float]) -> shapely.Geometry:
    """Tile label followed by a north arrow, centred on `center`, mirrored left-right
    so that it reads correctly when the tile is turned over to look at its underside."""
    shape = text_outline(f"{label}^", height_mm)
    x0, y0, x1, y1 = shape.bounds
    shape = affinity.translate(shape, center[0] - (x0 + x1) / 2, center[1] - (y0 + y1) / 2)
    return affinity.scale(shape, xfact=-1, yfact=1, origin=(center[0], center[1]))
