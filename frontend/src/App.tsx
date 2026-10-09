import { lazy, Suspense, useEffect, useState } from 'react'
import {
  buildModel,
  DEFAULT_OPTIONS,
  fitTestUrl,
  getFrame,
  uploadGpx,
  type BuiltModel,
  type Frame,
  type ModelOptions,
  type UploadedTrack,
} from './api'
import { GpxDrop } from './components/GpxDrop'
import { ParamsPanel } from './components/ParamsPanel'
import { RouteMap } from './components/RouteMap'

// three.js is large: load the 3D view only when a model exists.
const Preview3D = lazy(() =>
  import('./components/Preview3D').then((m) => ({ default: m.Preview3D })),
)

type View = 'map' | '3d'

function fmt(value: number | null, unit: string, digits = 0) {
  return value === null
    ? '–'
    : `${value.toLocaleString('en', { maximumFractionDigits: digits })} ${unit}`
}

export default function App() {
  const [track, setTrack] = useState<UploadedTrack | null>(null)
  const [options, setOptions] = useState<ModelOptions>(DEFAULT_OPTIONS)
  const [frame, setFrame] = useState<Frame | null>(null)
  const [model, setModel] = useState<BuiltModel | null>(null)
  const [builtWith, setBuiltWith] = useState<ModelOptions | null>(null)
  const [view, setView] = useState<View>('map')
  const [uploading, setUploading] = useState(false)
  const [building, setBuilding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Print area and model size follow the margin and size sliders.
  useEffect(() => {
    if (!track) return
    const timer = setTimeout(() => {
      getFrame(track.gpx_id, options.margin_pct, options.size_mm)
        .then(setFrame)
        .catch(() => setFrame(null))
    }, 250)
    return () => clearTimeout(timer)
  }, [track, options.margin_pct, options.size_mm])

  async function onFile(file: File) {
    setUploading(true)
    setError(null)
    try {
      const uploaded = await uploadGpx(file)
      setTrack(uploaded)
      setModel(null)
      setBuiltWith(null)
      setView('map')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setUploading(false)
    }
  }

  async function onGenerate() {
    if (!track) return
    setBuilding(true)
    setError(null)
    try {
      const built = await buildModel(track.gpx_id, options)
      setModel(built)
      setBuiltWith(options)
      setView('3d')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBuilding(false)
    }
  }

  const outdated = model !== null && JSON.stringify(builtWith) !== JSON.stringify(options)

  return (
    <div className="layout">
      <aside className="sidebar">
        <header className="brand">
          <h1>3D Topo Print</h1>
          <p>Terrain models of your routes, ready for the Bambu Lab A1.</p>
        </header>

        <section>
          <h2>1. Route</h2>
          <GpxDrop onFile={onFile} busy={uploading} compact={track !== null} />
          {track && (
            <div className="route-info">
              <h3>{track.name}</h3>
              <dl className="stats">
                <div>
                  <dt>Distance</dt>
                  <dd>{fmt(track.stats.distance_km, 'km', 1)}</dd>
                </div>
                <div>
                  <dt>Climb</dt>
                  <dd>{fmt(track.stats.ascent_m, 'm')}</dd>
                </div>
                <div>
                  <dt>Highest</dt>
                  <dd>{fmt(track.stats.max_ele_m, 'm')}</dd>
                </div>
              </dl>
            </div>
          )}
        </section>

        {track && (
          <section>
            <h2>2. Model</h2>
            <ParamsPanel options={options} onChange={setOptions} />
            {frame && (
              <p className="frame-info">
                {frame.size_mm[0]} × {frame.size_mm[1]} mm · {frame.real_size_km[0]} ×{' '}
                {frame.real_size_km[1]} km · scale {frame.scale}
              </p>
            )}
            <button type="button" className="primary" onClick={onGenerate} disabled={building}>
              {building ? 'Generating… (5–20 s)' : model ? 'Regenerate model' : 'Generate model'}
            </button>
          </section>
        )}

        {model && (
          <section>
            <h2>3. Print</h2>
            {outdated && (
              <p className="notice">Settings changed: regenerate to update the files.</p>
            )}
            <dl className="stats">
              <div>
                <dt>Size</dt>
                <dd>{model.stats.size_mm.join(' × ')} mm</dd>
              </div>
              <div>
                <dt>Route</dt>
                <dd>
                  {model.mode === 'inlay'
                    ? `${model.stats.route_parts} piece${model.stats.route_parts > 1 ? 's' : ''}`
                    : 'blended'}
                </dd>
              </div>
              <div>
                <dt>Terrain</dt>
                <dd>{fmt(model.stats.terrain_volume_cm3, 'cm³')}</dd>
              </div>
            </dl>
            <a className="primary" href={model.downloads.bambu} download>
              Download Bambu Studio project
            </a>
            <p className="hint">
              Open it with <em>File → Open Project</em> (not Import).{' '}
              {model.mode === 'inlay'
                ? 'Terrain is on plate 1, route pieces on plate 2.'
                : 'Terrain uses filament 1, route filament 2.'}
            </p>
            <div className="secondary-downloads">
              <a href={model.downloads['3mf']} download>
                Plain 3MF
              </a>
              <a href={model.downloads.stl} download>
                STL files
              </a>
              {model.mode === 'inlay' && (
                <a href={fitTestUrl(options)} download>
                  Fit test
                </a>
              )}
            </div>
          </section>
        )}

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </aside>

      <main className="viewer">
        {track ? (
          <>
            <div className="tabs" role="tablist">
              <button
                role="tab"
                aria-selected={view === 'map'}
                className={view === 'map' ? 'tab tab--active' : 'tab'}
                onClick={() => setView('map')}
              >
                Map
              </button>
              <button
                role="tab"
                aria-selected={view === '3d'}
                className={view === '3d' ? 'tab tab--active' : 'tab'}
                onClick={() => setView('3d')}
                disabled={!model}
              >
                3D preview
              </button>
            </div>
            <div className="view" hidden={view !== 'map'}>
              <RouteMap track={track} frame={frame?.bbox ?? null} />
            </div>
            {model && view === '3d' && (
              <div className="view">
                <Suspense fallback={<div className="placeholder">Loading 3D view…</div>}>
                  <Preview3D url={model.preview_url} />
                </Suspense>
              </div>
            )}
          </>
        ) : (
          <div className="placeholder">
            <p>Upload a GPX route to start.</p>
          </div>
        )}
      </main>
    </div>
  )
}
