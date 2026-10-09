import io
import zipfile
from pathlib import Path

import lib3mf
import pytest
import trimesh

from app.export import (
    ROUTE_COLOR,
    TERRAIN_COLOR,
    blended_layout,
    inlay_layout,
    to_3mf,
    to_glb,
    to_stl_zip,
)
from app.gpx import compute_frame, load_gpx
from app.heightfield import build_heightfield
from app.inlay import build_fit_test
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
    path.write_bytes(to_3mf(blended_layout(parts, name="Test & <loop>")))

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
    scene = trimesh.load(io.BytesIO(to_3mf(blended_layout(parts))), file_type="3mf", force="scene")
    assert sorted(scene.geometry) == ["route", "terrain"]
    for mesh in scene.geometry.values():
        assert mesh.is_watertight
    assert scene.geometry["terrain"].volume == pytest.approx(parts.terrain.volume(), rel=1e-4)
    assert scene.geometry["route"].volume == pytest.approx(parts.route.volume(), rel=1e-4)


def test_stl_zip(parts: ModelParts) -> None:
    with zipfile.ZipFile(io.BytesIO(to_stl_zip(blended_layout(parts), name="loop"))) as zf:
        assert sorted(zf.namelist()) == ["loop_route.stl", "loop_terrain.stl"]
        for name in zf.namelist():
            mesh = trimesh.load(io.BytesIO(zf.read(name)), file_type="stl")
            assert mesh.is_watertight


def test_glb_preview(parts: ModelParts) -> None:
    scene = trimesh.load(io.BytesIO(to_glb(blended_layout(parts))), file_type="glb", force="scene")
    assert sorted(scene.geometry) == ["route", "terrain"]
    terrain = scene.geometry["terrain"]
    route = scene.geometry["route"]
    assert tuple(terrain.visual.main_color) == TERRAIN_COLOR
    assert tuple(route.visual.main_color) == ROUTE_COLOR


def test_inlay_layout_puts_pieces_beside_the_terrain(tmp_path: Path) -> None:
    inlay = build_fit_test(ModelParams())
    layout = inlay_layout(inlay)
    assert [obj.name for obj in layout] == ["terrain", "route pieces"]
    assert [name for name, _ in layout[1].parts] == ["piece 1", "piece 2"]

    path = tmp_path / "inlay.3mf"
    path.write_bytes(to_3mf(layout))
    model = lib3mf.get_wrapper().CreateModel()
    model.QueryReader("3mf").ReadFromFile(str(path))
    assert model.GetBuildItems().Count() == 2

    # Meshes with their build-item placement applied; the terrain is the largest one.
    scene = trimesh.load(io.BytesIO(to_3mf(layout)), file_type="3mf", force="scene")
    meshes = sorted(scene.dump(concatenate=False), key=lambda m: m.volume, reverse=True)
    terrain, pieces = meshes[0], meshes[1:]
    assert len(pieces) == 2
    for mesh in pieces:
        assert mesh.bounds[0][0] > terrain.bounds[1][0]  # beside the terrain, not overlapping
        assert mesh.bounds[0][2] == pytest.approx(0, abs=1e-4)  # lying on the bed
