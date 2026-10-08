import io
import zipfile
from pathlib import Path

import lib3mf
import pytest
import trimesh

from app.export import ROUTE_COLOR, TERRAIN_COLOR, to_3mf, to_glb, to_stl_zip
from app.gpx import compute_frame, load_gpx
from app.heightfield import build_heightfield
from app.route import ModelParts, build_model
from app.terrain import ModelParams
from tests.synthetic import FakeSource, write_tile

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def parts(tmp_path_factory: pytest.TempPathFactory) -> ModelParts:
    track = load_gpx((FIXTURES / "alps_loop.gpx").read_bytes())
    tile = tmp_path_factory.mktemp("dem") / "t.tif"
    write_tile(tile, 45, 6, 0.002)
    hf = build_heightfield(
        compute_frame(track), size_mm=100, resolution_mm=1.0, source=FakeSource({(45, 6): tile})
    )
    return build_model(hf, track, ModelParams())


def test_3mf_is_valid_for_the_reference_reader(parts: ModelParts, tmp_path: Path) -> None:
    path = tmp_path / "model.3mf"
    path.write_bytes(to_3mf(parts, name="Test & <loop>"))

    model = lib3mf.get_wrapper().CreateModel()
    reader = model.QueryReader("3mf")
    reader.ReadFromFile(str(path))
    assert reader.GetWarningCount() == 0

    meshes = {}
    it = model.GetMeshObjects()
    while it.MoveNext():
        obj = it.GetCurrentMeshObject()
        meshes[obj.GetName()] = obj
    assert set(meshes) == {"terrain", "route"}
    assert all(obj.IsManifoldAndOriented() for obj in meshes.values())

    # One build item: an assembly of the two parts, so slicers load one object with two parts.
    assemblies = []
    it = model.GetComponentsObjects()
    while it.MoveNext():
        assemblies.append(it.GetCurrentComponentsObject())
    assert len(assemblies) == 1
    assert assemblies[0].GetName() == "Test & <loop>"
    assert assemblies[0].GetComponentCount() == 2
    items = model.GetBuildItems()
    assert items.Count() == 1


def test_3mf_parts_survive_vertex_merging(parts: ModelParts) -> None:
    # Readers that merge vertices by position must still see closed solids.
    scene = trimesh.load(io.BytesIO(to_3mf(parts)), file_type="3mf", force="scene")
    assert sorted(scene.geometry) == ["route", "terrain"]
    for mesh in scene.geometry.values():
        assert mesh.is_watertight
    assert scene.geometry["terrain"].volume == pytest.approx(parts.terrain.volume(), rel=1e-4)
    assert scene.geometry["route"].volume == pytest.approx(parts.route.volume(), rel=1e-4)


def test_stl_zip(parts: ModelParts) -> None:
    with zipfile.ZipFile(io.BytesIO(to_stl_zip(parts, name="loop"))) as zf:
        assert sorted(zf.namelist()) == ["loop_route.stl", "loop_terrain.stl"]
        for name in zf.namelist():
            mesh = trimesh.load(io.BytesIO(zf.read(name)), file_type="stl")
            assert mesh.is_watertight


def test_glb_preview(parts: ModelParts) -> None:
    scene = trimesh.load(io.BytesIO(to_glb(parts)), file_type="glb", force="scene")
    assert sorted(scene.geometry) == ["route", "terrain"]
    terrain = scene.geometry["terrain"]
    route = scene.geometry["route"]
    assert tuple(terrain.visual.main_color) == TERRAIN_COLOR
    assert tuple(route.visual.main_color) == ROUTE_COLOR
