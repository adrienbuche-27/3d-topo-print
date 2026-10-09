import io
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import lib3mf
import numpy as np
import pytest

from app.bambu import BAMBU_STUDIO_VERSION, project_settings, to_bambu_3mf
from app.export import blended_layout, inlay_layout
from app.inlay import build_fit_test
from app.route import ModelParts
from app.terrain import ModelParams

FIXTURES = Path(__file__).parent / "fixtures"
CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
# lib3mf flags Bambu Studio's p:UUID attributes; its own saved projects get the same warning.
BAMBU_UUID_WARNING = 32935


@pytest.fixture(scope="module")
def inlay_project() -> bytes:
    return to_bambu_3mf(inlay_layout(build_fit_test(ModelParams(route_width_mm=1.6))))


@pytest.fixture(scope="module")
def blended_project() -> bytes:
    inlay = build_fit_test(ModelParams(route_width_mm=1.6))
    # Any two solids do for checking the project structure.
    parts = ModelParts(terrain=inlay.terrain, route=inlay.pieces[0].solid)
    return to_bambu_3mf(blended_layout(parts, name="Blended"))


def model_settings(project: bytes) -> ET.Element:
    with zipfile.ZipFile(io.BytesIO(project)) as zf:
        return ET.fromstring(zf.read("Metadata/model_settings.config"))


def metadata(element: ET.Element) -> dict[str, str]:
    return {m.get("key"): m.get("value") for m in element.findall("metadata") if m.get("key")}


def test_project_is_marked_as_written_by_bambu_studio(inlay_project: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(inlay_project)) as zf:
        model = ET.fromstring(zf.read("3D/3dmodel.model"))
        settings = json.loads(zf.read("Metadata/project_settings.config"))
    meta = {m.get("name"): m.text for m in model.findall(f"{CORE}metadata")}
    assert meta["Application"] == f"BambuStudio-{BAMBU_STUDIO_VERSION}"
    assert meta["BambuStudio:3mfVersion"] == "1"
    assert "DesignerUserId" not in meta  # nothing from the owner's account
    # Bambu Studio loads the config only if it names one of its own printers.
    assert settings["printer_model"] == "Bambu Lab A1"
    assert settings["printer_settings_id"] == "Bambu Lab A1 0.4 nozzle"
    assert settings == project_settings()


def test_template_matches_the_owner_reference_project() -> None:
    with zipfile.ZipFile(FIXTURES / "bambu_reference.3mf") as zf:
        reference = json.loads(zf.read("Metadata/project_settings.config"))
    assert project_settings() == reference


def test_meshes_are_valid_for_the_reference_reader(inlay_project: bytes, tmp_path: Path) -> None:
    path = tmp_path / "project.3mf"
    path.write_bytes(inlay_project)
    model = lib3mf.get_wrapper().CreateModel()
    reader = model.QueryReader("3mf")
    reader.ReadFromFile(str(path))
    warnings = {reader.GetWarning(i)[0] for i in range(reader.GetWarningCount())}
    assert warnings <= {BAMBU_UUID_WARNING}

    meshes = model.GetMeshObjects()
    count = 0
    while meshes.MoveNext():
        assert meshes.GetCurrentMeshObject().IsManifoldAndOriented()
        count += 1
    assert count == 3  # terrain + 2 pieces


def test_inlay_terrain_and_pieces_on_separate_plates(inlay_project: bytes) -> None:
    config = model_settings(inlay_project)
    objects = {o.get("id"): o for o in config.findall("object")}
    plates = {
        metadata(p)["plater_id"]: [metadata(i)["object_id"] for i in p.findall("model_instance")]
        for p in config.findall("plate")
    }
    names = {oid: metadata(o)["name"] for oid, o in objects.items()}
    assert {names[oid] for oid in plates["1"]} == {"terrain"}
    assert {names[oid] for oid in plates["2"]} == {"route pieces"}

    # Terrain on filament 1, every route piece on filament 2.
    for oid, obj in objects.items():
        expected = "1" if names[oid] == "terrain" else "2"
        assert metadata(obj)["extruder"] == expected
        assert all(metadata(p)["extruder"] == expected for p in obj.findall("part"))


def test_objects_sit_inside_their_plate(inlay_project: bytes) -> None:
    """Each build item, with its meshes, lies on its plate of the 256 mm A1 bed."""
    plate_stride = 256 * 1.2
    with zipfile.ZipFile(io.BytesIO(inlay_project)) as zf:
        model = ET.fromstring(zf.read("3D/3dmodel.model"))
        object_files = {
            name: ET.fromstring(zf.read(name)) for name in zf.namelist() if "Objects/" in name
        }
    vertices = {}
    for tree in object_files.values():
        for obj in tree.iter(f"{CORE}object"):
            pts = [[float(v.get(a)) for a in "xyz"] for v in obj.iter(f"{CORE}vertex")]
            vertices[obj.get("id")] = np.array(pts)
    parts_of = {
        obj.get("id"): [c.get("objectid") for c in obj.iter(f"{CORE}component")]
        for obj in model.iter(f"{CORE}object")
    }
    for plate, item in enumerate(model.iter(f"{CORE}item")):
        offset = np.array([float(v) for v in item.get("transform").split()[9:]])
        pts = np.concatenate([vertices[p] for p in parts_of[item.get("objectid")]]) + offset
        x0, y0, z0 = pts.min(axis=0)
        x1, y1, _ = pts.max(axis=0)
        assert z0 == pytest.approx(0, abs=1e-4)
        assert plate * plate_stride <= x0 and x1 <= plate * plate_stride + 256
        assert 0 <= y0 and y1 <= 256


def test_blended_project_flushes_into_the_terrain_infill(blended_project: bytes) -> None:
    config = model_settings(blended_project)
    (obj,) = config.findall("object")
    assert metadata(obj)["flush_into_infill"] == "1"
    parts = {metadata(p)["name"]: metadata(p)["extruder"] for p in obj.findall("part")}
    assert parts == {"terrain": "1", "route": "2"}
    assert len(config.findall("plate")) == 1
