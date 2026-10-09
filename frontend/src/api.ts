// Client for the FastAPI backend (proxied under /api by Vite in development).

export type Mode = 'blended' | 'inlay'

export type BBox = [number, number, number, number] // west, south, east, north

export interface TrackStats {
  distance_km: number
  ascent_m: number | null
  descent_m: number | null
  min_ele_m: number | null
  max_ele_m: number | null
  points: number
}

export interface Profile {
  distance_km: number[]
  elevation_m: number[] // empty when the GPX has no elevations
}

export type RouteLine = GeoJSON.Feature<GeoJSON.MultiLineString>

export interface UploadedTrack {
  gpx_id: string
  name: string
  stats: TrackStats
  length_km: number
  profile: Profile
  bbox: BBox
  geojson: RouteLine
}

export interface Frame {
  bbox: BBox
  real_size_km: [number, number]
  size_mm: [number, number]
  scale: string
  selection: { stats: TrackStats; geojson: RouteLine }
}

/** Portion of the route to print, in km along it; null = the whole route. */
export type Cut = [number, number] | null

function cutParams(cut: Cut): Record<string, number> {
  return cut ? { start_km: cut[0], end_km: cut[1] } : {}
}

export interface ModelOptions {
  mode: Mode
  size_mm: number
  margin_pct: number
  z_exaggeration: number
  route_width_mm: number
  base_mm: number
  route_raise_mm: number
  groove_depth_mm: number
  resolution_mm: number
  inlay_clearance_mm: number
  inlay_piece_height_mm: number
}

export interface ModelStats {
  size_mm: [number, number, number]
  scale: string
  real_size_km: [number, number]
  terrain_volume_cm3: number
  route_volume_cm3: number
  route_parts: number
  triangles: number
  build_seconds: number
}

export interface BuiltModel {
  model_id: string
  mode: Mode
  stats: ModelStats
  preview_url: string
  downloads: Record<'bambu' | '3mf' | 'stl', string>
}

export const DEFAULT_ROUTE_WIDTH: Record<Mode, number> = { blended: 1.2, inlay: 1.6 }

export const DEFAULT_OPTIONS: ModelOptions = {
  mode: 'blended',
  size_mm: 180,
  margin_pct: 10,
  z_exaggeration: 1.5,
  route_width_mm: DEFAULT_ROUTE_WIDTH.blended,
  base_mm: 3,
  route_raise_mm: 0.6,
  groove_depth_mm: 1,
  resolution_mm: 0.25,
  inlay_clearance_mm: 0.15,
  inlay_piece_height_mm: 6,
}

async function request<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init)
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
      else if (Array.isArray(body.detail))
        message = body.detail.map((d: { msg: string }) => d.msg).join('; ')
    } catch {
      // Not JSON: keep the status text.
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export function uploadGpx(file: File): Promise<UploadedTrack> {
  const form = new FormData()
  form.append('file', file)
  return request('/api/gpx', { method: 'POST', body: form })
}

export function getFrame(
  gpxId: string,
  marginPct: number,
  sizeMm: number,
  cut: Cut,
): Promise<Frame> {
  const params = new URLSearchParams({ margin_pct: String(marginPct), size_mm: String(sizeMm) })
  for (const [key, value] of Object.entries(cutParams(cut))) params.set(key, String(value))
  return request(`/api/gpx/${gpxId}/frame?${params}`)
}

export function buildModel(gpxId: string, options: ModelOptions, cut: Cut): Promise<BuiltModel> {
  return request('/api/models', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ gpx_id: gpxId, ...options, ...cutParams(cut) }),
  })
}

export function fitTestUrl(options: ModelOptions): string {
  const params = new URLSearchParams({
    format: 'bambu',
    route_width_mm: String(options.route_width_mm),
    inlay_clearance_mm: String(options.inlay_clearance_mm),
    inlay_piece_height_mm: String(Math.min(options.inlay_piece_height_mm, 50)),
  })
  return `/api/fit-test?${params}`
}
