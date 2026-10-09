import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
import trimesh
from fastapi.testclient import TestClient

from app.main import app
from tests.synthetic import FakeSource, write_tile

FIXTURES = Path(__file__).parent / "fixtures"
FAST = {"size_mm": 100, "resolution_mm": 1.0}


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    tile = tmp_path_factory.mktemp("dem") / "t.tif"
    write_tile(tile, 45, 6, 0.002)
    app.state.tile_source = FakeSource({(45, 6): tile})
    yield TestClient(app)
    del app.state.tile_source


@pytest.fixture(scope="module")
def gpx_id(client: TestClient) -> str:
    gpx = (FIXTURES / "alps_loop.gpx").read_bytes()
    response = client.post("/api/gpx", files={"file": ("loop.gpx", gpx, "application/gpx+xml")})
    assert response.status_code == 200
    return response.json()["gpx_id"]


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}


def test_upload_gpx(client: TestClient) -> None:
    gpx = (FIXTURES / "alps_loop.gpx").read_bytes()
    body = client.post("/api/gpx", files={"file": ("loop.gpx", gpx)}).json()
    assert body["name"] == "Synthetic Chamonix loop"
    assert body["stats"]["distance_km"] == pytest.approx(14.1, abs=0.3)
    assert body["stats"]["ascent_m"] == pytest.approx(1100, abs=30)
    west, south, east, north = body["bbox"]
    assert west < 6.89 < east and south < 45.95 < north
    assert body["geojson"]["geometry"]["type"] == "MultiLineString"


def test_upload_rejects_invalid_gpx(client: TestClient) -> None:
    response = client.post("/api/gpx", files={"file": ("bad.gpx", b"not a gpx")})
    assert response.status_code == 422
    assert "GPX" in response.json()["detail"]


def test_blended_model_preview_and_downloads(client: TestClient, gpx_id: str) -> None:
    response = client.post("/api/models", json={"gpx_id": gpx_id, **FAST})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "blended"
    assert body["stats"]["size_mm"][0] == pytest.approx(100, abs=0.1)
    assert body["stats"]["route_parts"] == 1

    glb = client.get(body["preview_url"])
    assert glb.headers["content-type"] == "model/gltf-binary"
    scene = trimesh.load(io.BytesIO(glb.content), file_type="glb", force="scene")
    assert sorted(scene.geometry) == ["route", "terrain"]

    bambu = client.get(body["downloads"]["bambu"])
    assert bambu.status_code == 200
    assert 'filename="synthetic-chamonix-loop.3mf"' in bambu.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(bambu.content)) as zf:
        assert "Metadata/project_settings.config" in zf.namelist()

    stl = client.get(body["downloads"]["stl"])
    with zipfile.ZipFile(io.BytesIO(stl.content)) as zf:
        assert len(zf.namelist()) == 2


def test_inlay_model(client: TestClient, gpx_id: str) -> None:
    request = {"gpx_id": gpx_id, "mode": "inlay", "inlay_piece_height_mm": 1, **FAST}
    body = client.post("/api/models", json=request).json()
    assert body["mode"] == "inlay"
    assert body["stats"]["route_parts"] >= 2

    # The preview shows the pieces assembled in their slots.
    glb = client.get(body["preview_url"]).content
    scene = trimesh.load(io.BytesIO(glb), file_type="glb", force="scene")
    assert "terrain" in scene.geometry and "piece 1" in scene.geometry

    download = client.get(body["downloads"]["3mf"])
    assert 'filename="synthetic-chamonix-loop-inlay.3mf"' in download.headers["content-disposition"]


def test_model_parameter_validation(client: TestClient, gpx_id: str) -> None:
    too_big = client.post("/api/models", json={"gpx_id": gpx_id, "size_mm": 1200})
    assert too_big.status_code == 422
    bad_mode = client.post("/api/models", json={"gpx_id": gpx_id, "mode": "painted"})
    assert bad_mode.status_code == 422
    groove = client.post(
        "/api/models", json={"gpx_id": gpx_id, "base_mm": 1, "groove_depth_mm": 2, **FAST}
    )
    assert groove.status_code == 422
    assert "groove" in groove.json()["detail"]


def test_unknown_ids(client: TestClient) -> None:
    assert client.post("/api/models", json={"gpx_id": "nope"}).status_code == 404
    assert client.get("/api/models/nope/preview.glb").status_code == 404
    assert client.get("/api/models/nope/download").status_code == 404


def test_fit_test_download(client: TestClient) -> None:
    response = client.get("/api/fit-test", params={"inlay_clearance_mm": 0.2})
    assert response.status_code == 200
    assert 'filename="inlay-fit-test.3mf"' in response.headers["content-disposition"]


def test_frame_follows_margin_and_size(client: TestClient, gpx_id: str) -> None:
    small = client.get(f"/api/gpx/{gpx_id}/frame", params={"margin_pct": 0, "size_mm": 100}).json()
    large = client.get(f"/api/gpx/{gpx_id}/frame", params={"margin_pct": 50, "size_mm": 100}).json()
    assert max(small["size_mm"]) == pytest.approx(100)
    assert large["real_size_km"][0] > small["real_size_km"][0]
    assert large["bbox"][0] < small["bbox"][0]  # wider map to the west
    assert client.get("/api/gpx/nope/frame").status_code == 404


def test_upload_returns_the_elevation_profile(client: TestClient) -> None:
    gpx = (FIXTURES / "alps_loop.gpx").read_bytes()
    body = client.post("/api/gpx", files={"file": ("loop.gpx", gpx)}).json()
    profile = body["profile"]
    assert len(profile["distance_km"]) == len(profile["elevation_m"]) > 100
    assert profile["distance_km"][-1] == pytest.approx(body["length_km"], abs=1e-3)


def test_route_cutter_frame_and_model(client: TestClient, gpx_id: str) -> None:
    full = client.get(f"/api/gpx/{gpx_id}/frame").json()
    half = client.get(f"/api/gpx/{gpx_id}/frame", params={"start_km": 0, "end_km": 7}).json()
    assert half["selection"]["stats"]["distance_km"] == pytest.approx(7, abs=0.01)
    assert full["selection"]["stats"]["distance_km"] == pytest.approx(14.1, abs=0.3)
    # Half of the loop covers a smaller area.
    assert half["real_size_km"][0] * half["real_size_km"][1] < (
        full["real_size_km"][0] * full["real_size_km"][1]
    )
    # Only an end: from the start of the route.
    to_end = client.get(f"/api/gpx/{gpx_id}/frame", params={"end_km": 7}).json()
    assert to_end["bbox"] == half["bbox"]

    body = client.post(
        "/api/models", json={"gpx_id": gpx_id, "start_km": 0, "end_km": 7, **FAST}
    ).json()
    assert body["stats"]["real_size_km"] == half["real_size_km"]


def test_route_cutter_rejects_empty_portions(client: TestClient, gpx_id: str) -> None:
    empty = client.get(f"/api/gpx/{gpx_id}/frame", params={"start_km": 5, "end_km": 5})
    assert empty.status_code == 422
    beyond = client.post("/api/models", json={"gpx_id": gpx_id, "start_km": 50, **FAST})
    assert beyond.status_code == 422


def test_large_model_is_split_into_tiles(client: TestClient, gpx_id: str) -> None:
    # 100 mm model with 60 mm tiles: 2 x 2 grid.
    frame = client.get(
        f"/api/gpx/{gpx_id}/frame", params={"size_mm": 100, "max_tile_mm": 80}
    ).json()
    assert frame["grid"] == [2, 2]
    assert [t["label"] for t in frame["tiles"]] == ["A1", "A2", "B1", "B2"]
    west, south, east, north = frame["bbox"]
    for tile in frame["tiles"]:
        for lon, lat in tile["ring"]:
            assert west - 1e-3 <= lon <= east + 1e-3 and south - 1e-3 <= lat <= north + 1e-3
    single = client.get(f"/api/gpx/{gpx_id}/frame", params={"size_mm": 100}).json()
    assert single["grid"] == [1, 1] and single["tiles"] == []

    for mode in ("blended", "inlay"):
        body = client.post(
            "/api/models",
            json={"gpx_id": gpx_id, "mode": mode, "max_tile_mm": 80, **FAST},
        ).json()
        assert body["stats"]["grid"] == [2, 2]
        assert [t["label"] for t in body["stats"]["tiles"]] == ["A1", "A2", "B1", "B2"]
        assert body["stats"]["pins"] == 8

        bambu = client.get(body["downloads"]["bambu"])
        with zipfile.ZipFile(io.BytesIO(bambu.content)) as zf:
            config = zf.read("Metadata/model_settings.config").decode()
        assert 'key="plater_name" value="A1' in config
        assert 'value="alignment pins"' in config
        # One plate per tile (inlay: plus one per tile with route pieces) and the pins.
        if mode == "blended":
            assert config.count("<plate>") == 4 + 1
        else:
            assert 4 + 1 < config.count("<plate>") <= 4 + 4 + 1

    # An explicit grid overrides the automatic one.
    body = client.post(
        "/api/models", json={"gpx_id": gpx_id, "grid_cols": 2, "grid_rows": 1, **FAST}
    ).json()
    assert body["stats"]["grid"] == [2, 1]
