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

# Preview colours (RGBA); the real colours are the filaments chosen in the slicer.
TERRAIN_COLOR = (214, 208, 196, 255)
ROUTE_COLOR = (228, 87, 46, 255)
# Space between the terrain and the route pieces when both are laid out.
LAYOUT_GAP_MM = 10.0

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


def blended_layout(parts: ModelParts, name: str = "Topo print") -> list[PrintObject]:
    return [
        PrintObject(
            name=name,
            parts=[("terrain", parts.terrain), ("route", parts.route)],
            part_colors={"route": ROUTE_COLOR},
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
