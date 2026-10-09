import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
// MapLibre looks for its worker next to its own module, which bundling moves:
// let Vite build the worker and hand its URL over explicitly.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { useEffect, useRef } from 'react'
import type { BBox, UploadedTrack } from '../api'

maplibregl.setWorkerUrl(workerUrl)

interface Props {
  track: UploadedTrack
  frame: BBox | null
}

// OpenTopoMap: topographic raster tiles, fine for occasional personal use.
const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    topo: {
      type: 'raster',
      tiles: [
        'https://a.tile.opentopomap.org/{z}/{x}/{y}.png',
        'https://b.tile.opentopomap.org/{z}/{x}/{y}.png',
        'https://c.tile.opentopomap.org/{z}/{x}/{y}.png',
      ],
      tileSize: 256,
      maxzoom: 17,
      attribution:
        'Map data © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, SRTM | ' +
        'Style © <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
    },
  },
  layers: [{ id: 'topo', type: 'raster', source: 'topo' }],
}

function framePolygon(b: BBox): GeoJSON.Feature<GeoJSON.Polygon> {
  const [w, s, e, n] = b
  return {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'Polygon',
      coordinates: [
        [
          [w, s],
          [e, s],
          [e, n],
          [w, n],
          [w, s],
        ],
      ],
    },
  }
}

function draw(m: maplibregl.Map, track: UploadedTrack, frame: BBox | null) {
  ;(m.getSource('route') as maplibregl.GeoJSONSource).setData(track.geojson)
  ;(m.getSource('frame') as maplibregl.GeoJSONSource).setData(framePolygon(frame ?? track.bbox))
}

export function RouteMap({ track, frame }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const loaded = useRef(false)
  // Latest data to draw; applied as soon as the map style has loaded.
  const latest = useRef<{ track: UploadedTrack; frame: BBox | null }>({ track, frame })
  useEffect(() => {
    latest.current = { track, frame }
  })

  // Create the map once.
  useEffect(() => {
    if (!container.current) return
    const m = new maplibregl.Map({
      container: container.current,
      style: STYLE,
      attributionControl: false,
    })
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    m.addControl(new maplibregl.AttributionControl({ compact: true }))
    // The style is enough to add our layers; 'load' would also wait for the first tiles.
    m.once('style.load', () => {
      m.addSource('frame', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      m.addSource('route', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      m.addLayer({
        id: 'frame-fill',
        type: 'fill',
        source: 'frame',
        paint: { 'fill-color': '#2f6fde', 'fill-opacity': 0.08 },
      })
      m.addLayer({
        id: 'frame-line',
        type: 'line',
        source: 'frame',
        paint: { 'line-color': '#2f6fde', 'line-width': 2, 'line-dasharray': [3, 2] },
      })
      m.addLayer({
        id: 'route-casing',
        type: 'line',
        source: 'route',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': '#ffffff', 'line-width': 6 },
      })
      m.addLayer({
        id: 'route',
        type: 'line',
        source: 'route',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': '#e4572e', 'line-width': 3 },
      })
      loaded.current = true
      draw(m, latest.current.track, latest.current.frame)
    })
    map.current = m
    return () => {
      loaded.current = false
      m.remove()
      map.current = null
    }
  }, [])

  // Route and print area.
  useEffect(() => {
    if (map.current && loaded.current) draw(map.current, track, frame)
  }, [track, frame])

  // Zoom to the print area when a new track arrives.
  useEffect(() => {
    const m = map.current
    if (!m) return
    const [w, s, e, n] = track.bbox
    m.fitBounds(
      [
        [w, s],
        [e, n],
      ],
      { padding: 40, duration: 0 },
    )
  }, [track])

  return (
    <div className="map">
      {/* MapLibre styles its own container (position: relative), so size it from a wrapper. */}
      <div ref={container} className="map__canvas" />
    </div>
  )
}
