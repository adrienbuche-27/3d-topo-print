"""Export: 3MF for the slicer, STL zip as a fallback, GLB for the web preview."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import quoteattr

import numpy as np
import trimesh

from .mesh import to_trimesh
from .route import ModelParts

# Preview colours (RGBA); the real colours are the filaments chosen in the slicer.
TERRAIN_COLOR = (214, 208, 196, 255)
ROUTE_COLOR = (228, 87, 46, 255)

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


def _parts(parts: ModelParts) -> list[tuple[str, trimesh.Trimesh]]:
    return [("terrain", to_trimesh(parts.terrain)), ("route", to_trimesh(parts.route))]


def _mesh_xml(out: io.StringIO, mesh: trimesh.Trimesh) -> None:
    out.write("   <mesh>\n    <vertices>\n")
    np.savetxt(out, mesh.vertices, fmt='     <vertex x="%.6f" y="%.6f" z="%.6f"/>')
    out.write("    </vertices>\n    <triangles>\n")
    np.savetxt(out, mesh.faces, fmt='     <triangle v1="%d" v2="%d" v3="%d"/>')
    out.write("    </triangles>\n   </mesh>\n")


def to_3mf(parts: ModelParts, name: str = "Topo print") -> bytes:
    """3MF with one object made of two named parts, `terrain` and `route`.

    Bambu Studio (and PrusaSlicer/OrcaSlicer) load it as a single object with
    two parts, so each part can be given its own filament.
    """
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write(
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
    )
    out.write(f' <metadata name="Title">{_escape(name)}</metadata>\n')
    out.write(' <metadata name="Application">3D Topo Print</metadata>\n')
    out.write(" <resources>\n")
    meshes = _parts(parts)
    for i, (part_name, mesh) in enumerate(meshes, start=1):
        out.write(f'  <object id="{i}" type="model" name={quoteattr(part_name)}>\n')
        _mesh_xml(out, mesh)
        out.write("  </object>\n")
    assembly_id = len(meshes) + 1
    out.write(f'  <object id="{assembly_id}" type="model" name={quoteattr(name)}>\n')
    out.write("   <components>\n")
    for i in range(1, len(meshes) + 1):
        out.write(f'    <component objectid="{i}"/>\n')
    out.write("   </components>\n  </object>\n")
    out.write(" </resources>\n")
    out.write(f' <build>\n  <item objectid="{assembly_id}"/>\n </build>\n')
    out.write("</model>\n")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("3D/3dmodel.model", out.getvalue())
    return buf.getvalue()


def to_stl_zip(parts: ModelParts, name: str = "topo-print") -> bytes:
    """Zip with one binary STL per part (they share coordinates, so they line up)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for part_name, mesh in _parts(parts):
            zf.writestr(f"{name}_{part_name}.stl", mesh.export(file_type="stl"))
    return buf.getvalue()


def to_glb(parts: ModelParts) -> bytes:
    """Binary glTF with the two parts coloured, for the browser preview."""
    scene = trimesh.Scene()
    colors = {"terrain": TERRAIN_COLOR, "route": ROUTE_COLOR}
    for part_name, mesh in _parts(parts):
        mesh.visual = trimesh.visual.ColorVisuals(mesh, face_colors=colors[part_name])
        scene.add_geometry(mesh, node_name=part_name, geom_name=part_name)
    return scene.export(file_type="glb")


def _escape(text: str) -> str:
    return quoteattr(text)[1:-1]
