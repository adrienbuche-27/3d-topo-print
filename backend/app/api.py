"""HTTP API: upload a GPX, build a model, preview it, download printable files.

Everything is kept in memory: this is a single-user app running locally. Only
the most recent uploads and models are kept.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .bambu import to_bambu_3mf
from .dem import DemError, TileSource
from .export import inlay_layout, to_3mf, to_glb, to_stl_zip
from .gpx import (
    GpxError,
    Track,
    compute_frame,
    compute_stats,
    elevation_profile,
    load_gpx,
    slice_track,
    track_length_m,
    track_to_geojson,
)
from .inlay import build_fit_test
from .pipeline import DEFAULT_ROUTE_WIDTH_MM, BuildOptions, BuiltModel, Mode, build
from .route import RouteError
from .terrain import ModelParams

MAX_GPX_BYTES = 20 * 1024 * 1024
KEEP_TRACKS = 20
KEEP_MODELS = 5

router = APIRouter(prefix="/api")


class _Store(OrderedDict):
    """Keeps the `limit` most recently added items."""

    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit

    def add(self, value: object) -> str:
        key = uuid.uuid4().hex[:12]
        self[key] = value
        while len(self) > self.limit:
            self.popitem(last=False)
        return key


_tracks: _Store = _Store(KEEP_TRACKS)
_models: _Store = _Store(KEEP_MODELS)


def _tile_source(request: Request) -> TileSource | None:
    # Tests replace the elevation source; the app uses the Copernicus cache by default.
    return getattr(request.app.state, "tile_source", None)


def _stats(track: Track) -> dict:
    stats = compute_stats(track)
    return {
        "distance_km": round(stats.distance_m / 1000, 2),
        "ascent_m": None if stats.ascent_m is None else round(stats.ascent_m),
        "descent_m": None if stats.descent_m is None else round(stats.descent_m),
        "min_ele_m": stats.min_ele_m,
        "max_ele_m": stats.max_ele_m,
        "points": stats.point_count,
    }


def _selection(gpx_id: str, start_km: float | None, end_km: float | None) -> tuple[str, Track]:
    """The uploaded track, or the portion of it between two distances (route cutter)."""
    if gpx_id not in _tracks:
        raise HTTPException(404, "Unknown GPX id; upload the file again.")
    name, track = _tracks[gpx_id]
    if start_km is None and end_km is None:
        return name, track
    start_m = 1000 * (start_km or 0.0)
    end_m = track_length_m(track) if end_km is None else 1000 * end_km
    try:
        return name, slice_track(track, start_m, end_m)
    except GpxError as exc:
        raise HTTPException(422, str(exc)) from exc


class ModelRequest(BaseModel):
    gpx_id: str
    # Route cutter: print only the portion between these distances along the route.
    start_km: float | None = Field(None, ge=0)
    end_km: float | None = Field(None, ge=0)
    mode: Mode = "blended"
    size_mm: float = Field(180, ge=20, le=250, description="Longest side of the model")
    margin_pct: float = Field(10, ge=0, le=200)
    z_exaggeration: float = Field(1.5, ge=0.2, le=10)
    base_mm: float = Field(3, ge=1, le=30)
    route_width_mm: float | None = Field(
        None, ge=0.4, le=10, description="Default: 1.2 blended, 1.6 inlay"
    )
    route_raise_mm: float = Field(0.6, ge=0, le=5)
    groove_depth_mm: float = Field(1.0, ge=0.2, le=10)
    resolution_mm: float = Field(0.25, ge=0.1, le=2)
    inlay_clearance_mm: float = Field(0.15, ge=0, le=1)
    inlay_piece_height_mm: float = Field(6, ge=1, le=1000)

    def options(self) -> BuildOptions:
        width = self.route_width_mm or DEFAULT_ROUTE_WIDTH_MM[self.mode]
        params = ModelParams(
            base_mm=self.base_mm,
            z_exaggeration=self.z_exaggeration,
            route_width_mm=width,
            route_raise_mm=self.route_raise_mm,
            groove_depth_mm=self.groove_depth_mm,
            inlay_clearance_mm=self.inlay_clearance_mm,
            inlay_piece_height_mm=self.inlay_piece_height_mm,
        )
        return BuildOptions(
            mode=self.mode,
            size_mm=self.size_mm,
            margin_pct=self.margin_pct,
            resolution_mm=self.resolution_mm,
            params=params,
        )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/gpx")
async def upload_gpx(file: UploadFile = File(...)) -> dict:
    data = await file.read(MAX_GPX_BYTES + 1)
    if len(data) > MAX_GPX_BYTES:
        raise HTTPException(413, "GPX file is too large (max 20 MB).")
    try:
        track = load_gpx(data)
    except GpxError as exc:
        raise HTTPException(422, str(exc)) from exc

    name = track.name or (file.filename or "Route").rsplit(".", 1)[0]
    gpx_id = _tracks.add((name, track))
    return {
        "gpx_id": gpx_id,
        "name": name,
        "stats": _stats(track),
        "length_km": round(track_length_m(track) / 1000, 4),
        "profile": elevation_profile(track),
        "bbox": compute_frame(track).bbox_lonlat,
        "geojson": track_to_geojson(track),
    }


@router.get("/gpx/{gpx_id}/frame")
def frame(
    gpx_id: str,
    margin_pct: float = Query(10, ge=0, le=200),
    size_mm: float = Query(180, ge=20, le=250),
    start_km: float | None = Query(None, ge=0),
    end_km: float | None = Query(None, ge=0),
) -> dict:
    """Print area for a margin, the model's footprint for a size (longest side), and the
    selected portion of the route."""
    _, track = _selection(gpx_id, start_km, end_km)
    f = compute_frame(track, margin_pct=margin_pct)
    scale = size_mm / max(f.width_m, f.height_m)  # print mm per metre
    return {
        "bbox": f.bbox_lonlat,
        "real_size_km": [round(f.width_m / 1000, 2), round(f.height_m / 1000, 2)],
        "size_mm": [round(f.width_m * scale, 1), round(f.height_m * scale, 1)],
        "scale": f"1:{round(1000 / scale):,}",
        "selection": {"stats": _stats(track), "geojson": track_to_geojson(track)},
    }


@router.post("/models")
async def create_model(body: ModelRequest, request: Request) -> dict:
    name, track = _selection(body.gpx_id, body.start_km, body.end_km)
    try:
        model: BuiltModel = await run_in_threadpool(
            build, track, body.options(), name, _tile_source(request)
        )
    except (RouteError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except DemError as exc:
        raise HTTPException(502, f"Elevation data unavailable: {exc}") from exc

    model_id = _models.add(model)
    return {
        "model_id": model_id,
        "mode": body.mode,
        "stats": model.stats,
        "preview_url": f"/api/models/{model_id}/preview.glb",
        "downloads": {
            fmt: f"/api/models/{model_id}/download?format={fmt}" for fmt in ("bambu", "3mf", "stl")
        },
    }


def _get_model(model_id: str) -> BuiltModel:
    if model_id not in _models:
        raise HTTPException(404, "Unknown model id; generate the model again.")
    return _models[model_id]


@router.get("/models/{model_id}/preview.glb")
async def preview(model_id: str) -> Response:
    model = _get_model(model_id)
    data = await run_in_threadpool(to_glb, model.preview)
    return Response(data, media_type="model/gltf-binary")


Format = Literal["bambu", "3mf", "stl"]


def _file_response(layout: list, title: str, fmt: Format) -> Response:
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-") or "topo-print"
    if fmt == "bambu":
        data, name, mime = to_bambu_3mf(layout, title=title), f"{slug}.3mf", "model/3mf"
    elif fmt == "3mf":
        data, name, mime = to_3mf(layout, title=title), f"{slug}.3mf", "model/3mf"
    else:
        data, name, mime = to_stl_zip(layout, name=slug), f"{slug}-stl.zip", "application/zip"
    return Response(
        data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@router.get("/models/{model_id}/download")
async def download(model_id: str, format: Format = "bambu") -> Response:
    model = _get_model(model_id)
    suffix = "" if model.options.mode == "blended" else " inlay"
    return await run_in_threadpool(_file_response, model.layout, model.title + suffix, format)


@router.get("/fit-test")
async def fit_test(
    format: Format = "bambu",
    route_width_mm: float = Query(1.6, ge=0.4, le=10),
    inlay_clearance_mm: float = Query(0.15, ge=0, le=1),
    inlay_piece_height_mm: float = Query(6, ge=1, le=50),
) -> Response:
    params = ModelParams(
        route_width_mm=route_width_mm,
        inlay_clearance_mm=inlay_clearance_mm,
        inlay_piece_height_mm=inlay_piece_height_mm,
    )
    layout = inlay_layout(await run_in_threadpool(build_fit_test, params))
    return await run_in_threadpool(_file_response, layout, "Inlay fit test", format)
