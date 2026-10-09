"""Export: 3MF for the slicer, STL zip as a fallback, GLB for the web preview.

A print layout is a list of objects, each made of named parts:

- blended mode: one object with two parts (terrain, route), printed together;
- inlay mode: the terrain object, and a route object whose parts are the
  pieces lying flat in their map layout, printed separately.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import quoteattr

import numpy as np
import trimesh
from manifold3d import Manifold

from .inlay import InlayParts
from .mesh import to_trimesh
from .route import ModelParts
from .tiles import PinHole, Tile, pin_solid

# Preview colours (RGBA); the real colours are the filaments chosen in the slicer.
TERRAIN_COLOR = (214, 208, 196, 255)
ROUTE_COLOR = (228, 87, 46, 255)
# Space between the terrain and the route pieces when both are laid out.
LAYOUT_GAP_MM = 10.0
# Filament slots in the Bambu Studio project (1-based, as in the AMS).
TERRAIN_FILAMENT = 1
ROUTE_FILAMENT = 2

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>
"""

_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


@dataclass
class PrintObject:
    name: str
    parts: list[tuple[str, Manifold]]
    # Translation of the whole object on the plate.
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    color: tuple[int, int, int, int] = TERRAIN_COLOR
    part_colors: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)
    # Bambu Studio project only: plate number and name, filament slots, per-object settings.
    plate: int = 1
    plate_name: str = ""
    filament: int = 1
    part_filaments: dict[str, int] = field(default_factory=dict)
    settings: dict[str, str] = field(default_factory=dict)


def blended_layout(parts: ModelParts, name: str = "Topo print") -> list[PrintObject]:
    return [
        PrintObject(
            name=name,
            parts=[("terrain", parts.terrain), ("route", parts.route)],
            part_colors={"route": ROUTE_COLOR},
            part_filaments={"route": ROUTE_FILAMENT},
            # Purge the colour changes into the terrain's hidden infill, not only the tower.
            settings={"flush_into_infill": "1"},
        )
    ]


def inlay_layout(parts: InlayParts) -> list[PrintObject]:
    """Terrain, and next to it the route pieces lying flat in their map layout."""
    x1 = parts.terrain.bounding_box()[3]  # (min x, min y, min z, max x, max y, max z)
    return [
        PrintObject(name="terrain", parts=[("terrain", parts.terrain)]),
        PrintObject(
            name="route pieces",
            parts=[(f"piece {i}", p.on_bed()) for i, p in enumerate(parts.pieces, start=1)],
            offset=(x1 + LAYOUT_GAP_MM, 0.0, 0.0),
            color=ROUTE_COLOR,
            plate=2,
            filament=ROUTE_FILAMENT,
        ),
    ]


def _mesh_xml(out: io.StringIO, mesh: trimesh.Trimesh) -> None:
    out.write("   <mesh>\n    <vertices>\n")
    np.savetxt(out, mesh.vertices, fmt='     <vertex x="%.6f" y="%.6f" z="%.6f"/>')
    out.write("    </vertices>\n    <triangles>\n")
    np.savetxt(out, mesh.faces, fmt='     <triangle v1="%d" v2="%d" v3="%d"/>')
    out.write("    </triangles>\n   </mesh>\n")


def to_3mf(objects: list[PrintObject], title: str = "Topo print") -> bytes:
    """3MF where each object is an assembly of named mesh parts.

    Bambu Studio (and PrusaSlicer/OrcaSlicer) load each assembly as one object
    whose parts can be given their own filament.
    """
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write(
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
    )
    out.write(f' <metadata name="Title">{quoteattr(title)[1:-1]}</metadata>\n')
    out.write(' <metadata name="Application">3D Topo Print</metadata>\n')
    out.write(" <resources>\n")
    next_id = 1
    items = []
    for obj in objects:
        part_ids = []
        for part_name, solid in obj.parts:
            out.write(f'  <object id="{next_id}" type="model" name={quoteattr(part_name)}>\n')
            _mesh_xml(out, to_trimesh(solid))
            out.write("  </object>\n")
            part_ids.append(next_id)
            next_id += 1
        out.write(f'  <object id="{next_id}" type="model" name={quoteattr(obj.name)}>\n')
        out.write("   <components>\n")
        for part_id in part_ids:
            out.write(f'    <component objectid="{part_id}"/>\n')
        out.write("   </components>\n  </object>\n")
        items.append((next_id, obj.offset))
        next_id += 1
    out.write(" </resources>\n <build>\n")
    for object_id, (dx, dy, dz) in items:
        transform = f"1 0 0 0 1 0 0 0 1 {dx:.6f} {dy:.6f} {dz:.6f}"
        out.write(f'  <item objectid="{object_id}" transform="{transform}"/>\n')
    out.write(" </build>\n</model>\n")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("3D/3dmodel.model", out.getvalue())
    return buf.getvalue()


def to_stl_zip(objects: list[PrintObject], name: str = "topo-print") -> bytes:
    """Zip with one binary STL per part, each at its place in the layout."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for obj in objects:
            for part_name, solid in obj.parts:
                mesh = to_trimesh(solid.translate(obj.offset))
                filename = f"{name}_{part_name}.stl".replace(" ", "_")
                zf.writestr(filename, mesh.export(file_type="stl"))
    return buf.getvalue()


def to_glb(objects: list[PrintObject]) -> bytes:
    """Binary glTF with coloured parts, for the browser preview."""
    scene = trimesh.Scene()
    for obj in objects:
        for part_name, solid in obj.parts:
            mesh = to_trimesh(solid.translate(obj.offset))
            color = obj.part_colors.get(part_name, obj.color)
            mesh.visual = trimesh.visual.ColorVisuals(mesh, face_colors=color)
            scene.add_geometry(mesh, node_name=part_name, geom_name=part_name)
    return scene.export(file_type="glb")


# Gap between tiles in the exploded layout of plain 3MF / STL exports.
TILE_GAP_MM = 10.0
PIN_SPACING_MM = 6.0


def _exploded(tile: Tile) -> tuple[float, float, float]:
    """Offset that spreads the tiles apart by TILE_GAP_MM, keeping their arrangement."""
    return (tile.spec.col * TILE_GAP_MM, -tile.spec.row * TILE_GAP_MM, 0.0)


def tiled_layout(
    tiles: list[Tile], holes: list[PinHole], mode: str, name: str
) -> list[PrintObject]:
    """Split model: one plate per tile (inlay: plus one for its route pieces) and a plate
    of alignment pins.

    Plain 3MF / STL exports show the tiles spread slightly apart in their grid, the inlay
    pieces as a second grid to the east, and the pins to the south.
    """
    objects: list[PrintObject] = []
    plate = 0
    cols = 1 + max(t.spec.col for t in tiles)
    rows = 1 + max(t.spec.row for t in tiles)
    model_width = max(t.bounds_mm[2] for t in tiles)
    pieces_shift = model_width + (cols + 2) * TILE_GAP_MM
    for tile in tiles:
        plate += 1
        parts = [("terrain", tile.terrain)]
        if mode == "blended" and tile.route is not None:
            parts.append(("route", tile.route))
        objects.append(
            PrintObject(
                name=f"{name} {tile.label}",
                parts=parts,
                offset=_exploded(tile),
                part_colors={"route": ROUTE_COLOR},
                plate=plate,
                plate_name=f"{tile.label} terrain" if mode == "inlay" else tile.label,
                part_filaments={"route": ROUTE_FILAMENT},
                settings={"flush_into_infill": "1"} if mode == "blended" else {},
            )
        )
        if mode == "inlay" and tile.pieces:
            plate += 1
            dx, dy, _ = _exploded(tile)
            objects.append(
                PrintObject(
                    name=f"{tile.label} route pieces",
                    parts=[
                        (f"{tile.label} piece {i}", p.on_bed())
                        for i, p in enumerate(tile.pieces, start=1)
                    ],
                    offset=(dx + pieces_shift, dy, 0.0),
                    color=ROUTE_COLOR,
                    plate=plate,
                    plate_name=f"{tile.label} route pieces",
                    filament=ROUTE_FILAMENT,
                )
            )
    if holes:
        pin = pin_solid()
        pin_length = pin.bounding_box()[3]
        per_row = 10
        parts = []
        for i in range(len(holes)):
            row, col = divmod(i, per_row)
            step = (col * (pin_length + PIN_SPACING_MM), row * PIN_SPACING_MM, 0.0)
            parts.append((f"pin {i + 1}", pin.translate(step)))
        objects.append(
            PrintObject(
                name="alignment pins",
                parts=parts,
                offset=(0.0, -(rows + 2) * TILE_GAP_MM - 10 * PIN_SPACING_MM, 0.0),
                plate=plate + 1,
                plate_name="alignment pins",
            )
        )
    return objects
