"""Terrain solid: heightfield → printable block with a flat base."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from manifold3d import Manifold

from .heightfield import Heightfield
from .mesh import grid_solid, to_manifold


@dataclass(frozen=True)
class ModelParams:
    base_mm: float = 3.0  # thickness under the lowest point of the terrain
    z_exaggeration: float = 1.5
    route_width_mm: float = 1.6
    route_raise_mm: float = 0.6  # how far the route stands above the terrain
    groove_depth_mm: float = 1.0  # how deep the route sits into the terrain


@dataclass
class Surface:
    """Top surface of the model in print millimetres."""

    xs: np.ndarray  # (cols,) west → east, starting at 0
    ys: np.ndarray  # (rows,) north → south, ending at 0
    z: np.ndarray  # (rows, cols)


def terrain_surface(hf: Heightfield, params: ModelParams) -> Surface:
    """Scale elevations to print heights; the lowest point sits at `base_mm`."""
    rows, cols = hf.shape
    xs = np.linspace(0.0, hf.width_mm, cols)
    ys = np.linspace(hf.height_mm, 0.0, rows)
    elev = hf.elevations_m.astype(np.float64)
    z = params.base_mm + (elev - elev.min()) * hf.scale * params.z_exaggeration
    return Surface(xs=xs, ys=ys, z=z)


def terrain_solid(surface: Surface) -> Manifold:
    """Watertight block: terrain on top, flat bottom at z = 0."""
    return to_manifold(*grid_solid(surface.xs, surface.ys, surface.z))
