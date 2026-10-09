import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
// MapLibre looks for its worker next to its own module, which bundling moves:
// let Vite build the worker and hand its URL over explicitly.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { useEffect, useRef } from 'react'
import type { BBox, RouteLine, TileOutline, UploadedTrack } from '../api'

maplibregl.setWorkerUrl(workerUrl)

interface Props {
  track: UploadedTrack
  // Print area outline (lon, lat ring); the route's bbox until it is known.
  frame: [number, number][] | null
  // Route cutter: the portion to print; the rest of the route is drawn faded.
  selection: RouteLine | null
  // Grid splitting: outlines of the printed tiles (empty when the model fits the bed).
  tiles: TileOutline[]
}

interface Drawn {
  track: UploadedTrack
  frame: [number, number][] | null
  selection: RouteLine | null
  tiles: TileOutline[]
}

const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] }

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

function bboxRing([w, s, e, n]: BBox): [number, number][] {
  return [
    [w, s],
    [e, s],
    [e, n],
    [w, n],
    [w, s],
  ]
}

function framePolygon(ring: [number, number][]): GeoJSON.Feature<GeoJSON.Polygon> {
  return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring] } }
}

function tileLines(tiles: TileOutline[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: tiles.map((t) => ({
      type: 'Feature',
      properties: { label: t.label },
      geometry: { type: 'LineString', coordinates: t.ring },
    })),
  }
}

function draw(m: maplibregl.Map, { track, frame, selection, tiles }: Drawn) {
  const source = (id: string) => m.getSource(id) as maplibregl.GeoJSONSource
  source('route').setData(selection ?? track.geojson)
  source('route-full').setData(selection ? track.geojson : EMPTY)
  source('frame').setData(framePolygon(frame ?? bboxRing(track.bbox)))
  source('tiles').setData(tileLines(tiles))
}

// Tile labels as HTML markers: the raster style has no fonts for map text.
function tileMarkers(m: maplibregl.Map, tiles: TileOutline[]): maplibregl.Marker[] {
  return tiles.map((t) => {
    const lon = t.ring.slice(0, 4).reduce((a, p) => a + p[0], 0) / 4
    const lat = t.ring.slice(0, 4).reduce((a, p) => a + p[1], 0) / 4
    const el = document.createElement('div')
    el.className = 'tile-label'
    el.textContent = t.label
    return new maplibregl.Marker({ element: el }).setLngLat([lon, lat]).addTo(m)
  })
}

export function RouteMap({ track, frame, selection, tiles }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const loaded = useRef(false)
  // Latest data to draw; applied as soon as the map style has loaded.
  const latest = useRef<Drawn>({ track, frame, selection, tiles })
  useEffect(() => {
    latest.current = { track, frame, selection, tiles }
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
      m.addSource('frame', { type: 'geojson', data: EMPTY })
      m.addSource('route', { type: 'geojson', data: EMPTY })
      m.addSource('route-full', { type: 'geojson', data: EMPTY })
      m.addSource('tiles', { type: 'geojson', data: EMPTY })
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
        id: 'tiles',
        type: 'line',
        source: 'tiles',
        paint: { 'line-color': '#2f6fde', 'line-width': 1.5 },
      })
      m.addLayer({
        id: 'route-full',
        type: 'line',
        source: 'route-full',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': '#59636e', 'line-width': 2, 'line-opacity': 0.5 },
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
      draw(m, latest.current)
    })
    map.current = m
    return () => {
      loaded.current = false
      m.remove()
      map.current = null
    }
  }, [])

  // Route, print area and tile grid.
  useEffect(() => {
    if (map.current && loaded.current) draw(map.current, { track, frame, selection, tiles })
  }, [track, frame, selection, tiles])

  // Tile labels.
  useEffect(() => {
    if (!map.current) return
    const markers = tileMarkers(map.current, tiles)
    return () => markers.forEach((marker) => marker.remove())
  }, [tiles])

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
