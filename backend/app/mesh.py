"""Low-level mesh helpers: closed solids from height grids, trimesh <-> manifold."""

from __future__ import annotations

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh


def grid_solid(
    xs: np.ndarray,
    ys: np.ndarray,
    z: np.ndarray,
    bottom_z: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Watertight solid: a height grid on top, vertical walls and a flat bottom.

    `xs` (cols,) increases west → east, `ys` (rows,) decreases north → south and
    `z` (rows, cols) holds the top surface heights, row 0 being the northern edge.
    Returns (vertices, faces) with outward-facing triangles.
    """
    rows, cols = z.shape
    if len(xs) != cols or len(ys) != rows or rows < 2 or cols < 2:
        raise ValueError("Grid coordinates do not match the height array")

    gx, gy = np.meshgrid(xs, ys)
    top = np.column_stack([gx.ravel(), gy.ravel(), z.ravel()])

    # Two triangles per cell, counter-clockwise seen from above (+z normals).
    idx = np.arange(rows * cols).reshape(rows, cols)
    v00, v01 = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()  # north-west, north-east
    v10, v11 = idx[1:, :-1].ravel(), idx[1:, 1:].ravel()  # south-west, south-east
    top_faces = np.concatenate([np.column_stack([v10, v11, v01]), np.column_stack([v10, v01, v00])])

    # Boundary ring, counter-clockwise seen from above: south, east, north, west edges.
    ring = np.concatenate(
        [
            idx[-1, :],  # south edge, west → east
            idx[-2::-1, -1],  # east edge, south → north
            idx[0, -2::-1],  # north edge, east → west
            idx[1:-1, 0],  # west edge, north → south (corners already included)
        ]
    )
    n_top = len(top)
    n_ring = len(ring)
    bottom = top[ring].copy()
    bottom[:, 2] = bottom_z
    center = np.array([[(xs[0] + xs[-1]) / 2, (ys[0] + ys[-1]) / 2, bottom_z]])

    a = np.arange(n_ring)
    b = np.roll(a, -1)
    ta, tb = ring[a], ring[b]
    ba, bb = n_top + a, n_top + b
    wall_faces = np.concatenate([np.column_stack([ba, bb, tb]), np.column_stack([ba, tb, ta])])
    c = n_top + n_ring
    bottom_faces = np.column_stack([np.full(n_ring, c), bb, ba])

    vertices = np.concatenate([top, bottom, center])
    faces = np.concatenate([top_faces, wall_faces, bottom_faces])
    return vertices, faces


def to_manifold(vertices: np.ndarray, faces: np.ndarray) -> Manifold:
    mesh = Mesh(
        vert_properties=np.ascontiguousarray(vertices, dtype=np.float32),
        tri_verts=np.ascontiguousarray(faces, dtype=np.uint32),
    )
    solid = Manifold(mesh)
    if solid.status().name != "NoError":
        raise ValueError(f"Mesh is not a valid solid: {solid.status().name}")
    return solid


def to_trimesh(solid: Manifold) -> trimesh.Trimesh:
    mesh = solid.to_mesh()
    return trimesh.Trimesh(
        vertices=np.asarray(mesh.vert_properties)[:, :3],
        faces=np.asarray(mesh.tri_verts),
        process=False,
    )
