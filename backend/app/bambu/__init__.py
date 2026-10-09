"""Bambu Studio project 3MF.

Bambu Studio only loads a 3MF's settings without warnings when the file is
marked as written by Bambu Studio and carries a project configuration naming
its Bambu Lab system presets. This writer produces the same structure as a
project saved by Bambu Studio itself (production extension with one model
file per object, `Metadata/model_settings.config` for parts, filaments and
plates, `Metadata/project_settings.config` for the presets).

The project configuration is a template saved by the owner's Bambu Studio
(see `project_settings.config` next to this file): Bambu Lab A1, 0.4 mm
nozzle, 0.20 mm Standard, and the owner's four AMS filaments. On load, Bambu
Studio fills every value from its own system presets of the same names, so
the template keeps working across Bambu Studio updates.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np

from ..export import PrintObject
from ..mesh import to_trimesh

TEMPLATE_DIR = Path(__file__).parent
# Version written into the project; it must not be newer than the installed Bambu Studio.
BAMBU_STUDIO_VERSION = "02.08.02.61"
# Logical gap between plates on Bambu Studio's canvas, as a fraction of the plate size.
PLATE_GAP = 1.0 / 5.0

# UUID suffixes Bambu Studio uses for its own objects (see bbs_3mf.cpp).
_OBJECT_UUID = "-61cb-4c03-9d28-80fed5dfa1dc"
_SUB_OBJECT_UUID = "-81cb-4c03-9d28-80fed5dfa1dc"
_COMPONENT_UUID = "-b206-40ff-9872-83e8017abed1"
_BUILD_ITEM_UUID = "-b1ec-4553-aec9-835e5b724bb4"
_BUILD_UUID = "2c7c17d8-22b5-4d84-8835-1976022ea369"

_NAMESPACES = (
    'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
    'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
    'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" '
    'requiredextensions="p"'
)

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Default Extension="gcode" ContentType="text/x.gcode"/>
</Types>
"""

_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def project_settings() -> dict:
    return json.loads((TEMPLATE_DIR / "project_settings.config").read_text())


def _bed(settings: dict) -> tuple[float, float]:
    """Printable width and depth from the printer's `printable_area` ("XxY" corners)."""
    corners = [tuple(map(float, c.split("x"))) for c in settings["printable_area"]]
    xs, ys = zip(*corners, strict=True)
    return max(xs) - min(xs), max(ys) - min(ys)


def _plate_origin(index: int, count: int, bed: tuple[float, float]) -> tuple[float, float]:
    """Position of plate `index` (0-based) on Bambu Studio's canvas (PartPlate.cpp)."""
    root = count**0.5
    cols = int(round(root)) + (1 if root > round(root) else 0)
    row, col = divmod(index, cols)
    return col * bed[0] * (1 + PLATE_GAP), -row * bed[1] * (1 + PLATE_GAP)


def _attr(text: str) -> str:
    return quoteattr(text)


def to_bambu_3mf(objects: list[PrintObject], title: str = "Topo print") -> bytes:
    """Bambu Studio project: each object centred on its plate, filaments assigned."""
    settings = project_settings()
    bed = _bed(settings)
    plate_count = max(obj.plate for obj in objects)
    files: dict[str, str] = {}

    model_resources, build_items, object_rels = [], [], []
    config = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    plates: dict[int, list[int]] = {}
    next_id = 1
    for n, obj in enumerate(objects, start=1):
        # Centre the object's meshes on its own origin, as Bambu Studio does, and place
        # the origin at the centre of its plate with the bottom on the bed.
        solids = [(name, solid.translate(obj.offset)) for name, solid in obj.parts]
        lo = np.min([s.bounding_box()[:3] for _, s in solids], axis=0)
        hi = np.max([s.bounding_box()[3:] for _, s in solids], axis=0)
        center = (lo + hi) / 2
        px, py = _plate_origin(obj.plate - 1, plate_count, bed)
        position = (px + bed[0] / 2, py + bed[1] / 2, center[2] - lo[2])

        path = f"/3D/Objects/object_{n}.model"
        sub = io.StringIO()
        sub.write('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" ')
        sub.write(f'xml:lang="en-US" {_NAMESPACES}>\n')
        sub.write(' <metadata name="BambuStudio:3mfVersion">1</metadata>\n <resources>\n')
        components = []
        part_configs = []
        for index, (part_name, solid) in enumerate(solids):
            mesh = to_trimesh(solid.translate(tuple(-center)))
            part_id = next_id
            next_id += 1
            uid = f"{(n << 16) + index:08x}"
            sub.write(f'  <object id="{part_id}" p:UUID="{uid}{_SUB_OBJECT_UUID}" type="model">\n')
            sub.write("   <mesh>\n    <vertices>\n")
            np.savetxt(sub, mesh.vertices, fmt='     <vertex x="%.6f" y="%.6f" z="%.6f"/>')
            sub.write("    </vertices>\n    <triangles>\n")
            np.savetxt(sub, mesh.faces, fmt='     <triangle v1="%d" v2="%d" v3="%d"/>')
            sub.write("    </triangles>\n   </mesh>\n  </object>\n")
            components.append(
                f'    <component p:path="{path}" objectid="{part_id}" '
                f'p:UUID="{uid}{_COMPONENT_UUID}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
            )
            filament = obj.part_filaments.get(part_name, obj.filament)
            part_configs += [
                f'    <part id="{part_id}" subtype="normal_part">',
                f'      <metadata key="name" value={_attr(part_name)}/>',
                '      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>',
                f'      <metadata key="extruder" value="{filament}"/>',
                f'      <mesh_stat face_count="{len(mesh.faces)}" edges_fixed="0" '
                'degenerate_facets="0" facets_removed="0" facets_reversed="0" '
                'backwards_edges="0"/>',
                "    </part>",
            ]
        sub.write(" </resources>\n <build/>\n</model>\n")
        files[path.lstrip("/")] = sub.getvalue()
        object_rels.append(path)

        object_id = next_id
        next_id += 1
        model_resources.append(
            f'  <object id="{object_id}" p:UUID="{n:08x}{_OBJECT_UUID}" type="model">\n'
            "   <components>\n" + "\n".join(components) + "\n   </components>\n  </object>"
        )
        x, y, z = position
        build_items.append(
            f'  <item objectid="{object_id}" p:UUID="{n:08x}{_BUILD_ITEM_UUID}" '
            f'transform="1 0 0 0 1 0 0 0 1 {x:.6f} {y:.6f} {z:.6f}" printable="1"/>'
        )
        config += [
            f'  <object id="{object_id}">',
            f'    <metadata key="name" value={_attr(obj.name)}/>',
            f'    <metadata key="extruder" value="{obj.filament}"/>',
            *(f'    <metadata key="{k}" value={_attr(v)}/>' for k, v in obj.settings.items()),
            *part_configs,
            "  </object>",
        ]
        plates.setdefault(obj.plate, []).append(object_id)

    for plate in range(1, plate_count + 1):
        config += [
            "  <plate>",
            f'    <metadata key="plater_id" value="{plate}"/>',
            '    <metadata key="plater_name" value=""/>',
            '    <metadata key="locked" value="false"/>',
            '    <metadata key="filament_map_mode" value="Auto For Flush"/>',
        ]
        for object_id in plates.get(plate, []):
            config += [
                "    <model_instance>",
                f'      <metadata key="object_id" value="{object_id}"/>',
                '      <metadata key="instance_id" value="0"/>',
                f'      <metadata key="identify_id" value="{100 + object_id}"/>',
                "    </model_instance>",
            ]
        config.append("  </plate>")
    config.append("</config>")

    model = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
        f"{_NAMESPACES}>\n"
        f' <metadata name="Application">BambuStudio-{BAMBU_STUDIO_VERSION}</metadata>\n'
        ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
        f' <metadata name="Title">{_attr(title)[1:-1]}</metadata>\n'
        " <resources>\n" + "\n".join(model_resources) + "\n </resources>\n"
        f' <build p:UUID="{_BUILD_UUID}">\n' + "\n".join(build_items) + "\n </build>\n</model>\n"
    )
    model_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        + "".join(
            f' <Relationship Target="{path}" Id="rel-{i}" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
            for i, path in enumerate(object_rels, start=1)
        )
        + "</Relationships>\n"
    )
    slice_info = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <header>\n'
        '    <header_item key="X-BBL-Client-Type" value="slicer"/>\n'
        f'    <header_item key="X-BBL-Client-Version" value="{BAMBU_STUDIO_VERSION}"/>\n'
        "  </header>\n</config>\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("3D/3dmodel.model", model)
        zf.writestr("3D/_rels/3dmodel.model.rels", model_rels)
        for name, content in files.items():
            zf.writestr(name, content)
        zf.writestr("Metadata/model_settings.config", "\n".join(config) + "\n")
        zf.writestr("Metadata/project_settings.config", json.dumps(settings, indent=4))
        zf.writestr("Metadata/slice_info.config", slice_info)
    return buf.getvalue()
