# 3D Topo Print — Architecture & Implementation Plan

Tracking document for the project. Update the checkboxes and the decision log as work progresses.

## 1. Goal

A personal web app for designing 3D-printable terrain models of a location, with a GPX route (a hike, a bike race, …) shown as a **separate colour insert**.

**Version 1 scope:** upload a GPX → the terrain is framed automatically around the route → the route becomes a coloured insert → preview in 3D → download a 3MF that is ready for multi-colour printing.

## 2. Context & constraints

| Item | Value |
|---|---|
| Users | Single user (owner), runs locally |
| Printer | Bambu Lab A1 with AMS lite. Build volume 256 × 256 × 256 mm, 0.4 mm nozzle |
| Slicer | Bambu Studio |
| Data coverage (v1) | Europe |
| Shape (v1) | Rectangle |
| Route rendering | Separate body (insert) in its own colour |
| GPX input | Uploaded in the app |
| Stack | React (frontend) + Python (backend) |
| Maintenance | Claude Code |

## 3. Architecture

```
┌──────────────────────── Frontend (React + Vite + TS) ─────────────────────────┐
│  GPX upload ─► 2D map preview (MapLibre + OSM) ─► parameter panel            │
│  3D preview (three.js / react-three-fiber) ◄── meshes (GLB) from the API     │
│  Download 3MF / STL                                                           │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │ REST (JSON + binary)
┌───────────────────────────────────▼─────────── Backend (Python + FastAPI) ────┐
│  gpx.py      parse GPX (gpxpy), simplify, compute bbox + margin              │
│  dem.py      fetch Copernicus GLO-30 tiles (COG, windowed reads), local cache│
│  project.py  WGS84 → local UTM (pyproj), resample to the print grid          │
│  terrain.py  heightfield → watertight solid with a flat base                 │
│  route.py    route → 2D buffered polygon (shapely) → insert solid            │
│  boolean.py  terrain − insert (manifold3d through trimesh)                   │
│  export.py   3MF (2 objects: terrain + route), STL zip, GLB for preview      │
└───────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Elevation data (Europe)

- **Main source: Copernicus DEM GLO-30** (about 30 m resolution, global, free).
  - Stored as public Cloud-Optimized GeoTIFFs on AWS (`s3://copernicus-dem-30m/`, no authentication needed). We read only the window we need with `rasterio`.
  - Downloaded tiles are cached in `backend/.cache/dem/`.
- **Fallback (if AWS is unreachable):** AWS Terrain Tiles (Terrarium PNG, open), which are coarser in places.
- **Later:** high-resolution national datasets: IGN RGE ALTI (France, 1–5 m), swissALTI3D (Switzerland, 0.5–2 m), and others.

### 3.2 Geometry pipeline

1. **GPX:** parse all tracks and segments, drop outliers, simplify (Douglas-Peucker, tolerance tied to the print resolution).
2. **Framing:** bbox of the route plus a margin (default 10 %), expanded to the requested aspect ratio (default: fit the route).
3. **Projection:** convert everything to the local UTM zone (metres), so distances are true and not distorted.
4. **Scaling:** `scale = print_width_mm / bbox_width_m`. Vertical scale = `scale × z_exaggeration` (default 1.5–2× for prints).
5. **Heightfield:** resample the DEM onto a regular grid at about 0.2–0.25 mm print spacing (the target). Light smoothing is optional.
6. **Terrain solid:** top surface from the heightfield; z = base thickness + (elev − min_elev) × vertical scale; flat bottom; side walls. Must be watertight and manifold.
7. **Route insert:**
   - Buffer the projected polyline to a 2D polygon of `route_width_mm` (default 1.6 mm, which is 4 nozzle widths).
   - Insert = vertical prism of that polygon, intersected with the band between `surface − groove_depth` and `surface + route_raise`.
     - Defaults: groove depth 1.0 mm, raise 0.6 mm.
     - The band is built as Solid(surface + raise) − Solid(surface − depth).
   - Terrain final = Terrain − Insert. The two bodies share faces exactly. This works because the AMS prints them together, so no clearance is needed.
8. **Export:**
   - **3MF** with 2 named objects (`terrain`, `route`) at the same origin, so Bambu Studio can load them as one object with 2 parts and assign a filament to each.
   - **STL zip** as a fallback.
   - **GLB** for the web preview.

### 3.3 API (v1)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/gpx` | Upload a GPX; returns track GeoJSON, stats (distance, D+), and the suggested bbox |
| POST | `/api/model` | Takes the GPX id + parameters; returns a model id, a GLB preview URL, and stats |
| GET | `/api/model/{id}/preview.glb` | Mesh for the 3D preview |
| GET | `/api/model/{id}/download?format=3mf\|stl` | Printable file |

**Parameters:**
- `width_mm` (default 180, max 250)
- `margin_pct`
- `z_exaggeration`
- `base_mm` (default 3)
- `route_width_mm`
- `route_raise_mm`
- `groove_depth_mm`
- `resolution_mm`

### 3.4 Project layout

```
backend/        FastAPI app, geometry pipeline, tests (pytest)
  app/
  tests/fixtures/   sample GPX files
frontend/       React + Vite + TypeScript
docs/PLAN.md    this file
README.md       written after v1
```

### 3.5 Main libraries

- **Backend:** fastapi, uvicorn, gpxpy, numpy, rasterio, pyproj, shapely, trimesh, manifold3d, scipy (resampling)
- **Frontend:** react, vite, typescript, maplibre-gl, three, @react-three/fiber, @react-three/drei

## 4. Implementation pipeline (v1)

- [ ] **Step 0: Scaffolding.** Monorepo layout, Python project (uv or pip + `pyproject.toml`), Vite React TS app, lint/format (ruff, eslint/prettier), dev script that runs both.
- [ ] **Step 1: GPX module.** Parse, clean, simplify, stats (distance, D+, D−), bbox + margin. Unit tests with fixtures.
- [ ] **Step 2: DEM module.** Find the GLO-30 tiles that cover a bbox, windowed read, mosaic, cache. Test on one known Alpine area.
- [ ] **Step 3: Projection & resampling.** UTM conversion and a regular print grid.
- [ ] **Step 4: Terrain solid.** Heightfield → watertight mesh with a base. Test: `mesh.is_watertight`, size within 256 mm.
- [ ] **Step 5: Route insert.** Buffered polygon → band solid → boolean ops. Test that both bodies are manifold and do not overlap.
- [ ] **Step 6: Export.** 3MF with 2 objects, STL zip, GLB. Manual check: open in Bambu Studio and assign 2 filaments.
- [ ] **Step 7: API.** FastAPI endpoints, in-memory/disk model store, error handling (GPX outside Europe, route too big).
- [ ] **Step 8: Frontend: upload & map.** Drag-and-drop GPX, route on a MapLibre map, bbox overlay, stats.
- [ ] **Step 9: Frontend: parameters & 3D preview.** Parameter form, "Generate" button, three.js preview with 2 colours, download buttons.
- [ ] **Step 10: End-to-end test print.** One real GPX, printed on the A1. Tune the defaults (route width, raise, exaggeration).
- [ ] **Step 11: README.** Setup, usage, parameters, printing tips for Bambu Studio.

## 5. Backlog (after v1)

- Other shapes: circle, hexagon, custom polygon
- Manual bbox editing on the map
- Text / labels (route name, distance, D+) on the base or the side
- Higher-resolution national DEMs (IGN, swisstopo, …)
- Water bodies and rivers from OSM as a third colour
- Several routes on one print
- Splitting into tiles for prints larger than 256 mm
- Strava / Komoot import
- Global coverage

## 6. Decision log

| Date | Decision | Reason |
|---|---|---|
| 2026-10-07 | React + FastAPI, local only | Single user; Python has the best geo/mesh libraries |
| 2026-10-07 | Copernicus GLO-30 as the v1 DEM | Free, covers Europe, COG on AWS, no auth needed |
| 2026-10-07 | Route as a separate body in a 3MF, sharing faces with the terrain | AMS prints both bodies together, so no clearance is needed |
| 2026-10-07 | Rectangle only, framed automatically from the GPX | Keeps v1 small |

## 7. Open questions

- Default print width: 180 mm, or the full 250 mm?
- Preferred z-exaggeration for Alpine vs. flatter areas (to tune after the first print)
