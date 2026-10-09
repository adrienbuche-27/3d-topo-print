import { useState } from 'react'
import { DEFAULT_OPTIONS, DEFAULT_ROUTE_WIDTH, type ModelOptions, type Mode } from '../api'

interface Props {
  options: ModelOptions
  onChange: (options: ModelOptions) => void
}

interface SliderProps {
  label: string
  hint?: string
  value: number
  min: number
  max: number
  step: number
  unit: string
  onChange: (value: number) => void
}

function Slider({ label, hint, value, min, max, step, unit, onChange }: SliderProps) {
  return (
    <label className="field">
      <span className="field__row">
        <span className="field__label">{label}</span>
        <span className="field__value">
          <input
            type="number"
            value={value}
            min={min}
            max={max}
            step={step}
            onChange={(e) => {
              const v = e.target.valueAsNumber
              if (!Number.isNaN(v)) onChange(v)
            }}
          />
          {unit}
        </span>
      </span>
      <input
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(e.target.valueAsNumber)}
      />
      {hint && <span className="field__hint">{hint}</span>}
    </label>
  )
}

const MODES: { value: Mode; title: string; text: string }[] = [
  {
    value: 'blended',
    title: 'Blended',
    text: 'One print, two colours with the AMS. Simple, but purges filament at every layer.',
  },
  {
    value: 'inlay',
    title: 'Inlay',
    text: 'Terrain and route printed separately, then the route pieces drop into their slots.',
  },
]

export function ParamsPanel({ options, onChange }: Props) {
  const [advanced, setAdvanced] = useState(false)
  const set = <K extends keyof ModelOptions>(key: K, value: ModelOptions[K]) =>
    onChange({ ...options, [key]: value })

  function setMode(mode: Mode) {
    // Keep a custom route width, but switch the default along with the mode.
    const usesDefault = options.route_width_mm === DEFAULT_ROUTE_WIDTH[options.mode]
    onChange({
      ...options,
      mode,
      route_width_mm: usesDefault ? DEFAULT_ROUTE_WIDTH[mode] : options.route_width_mm,
    })
  }

  return (
    <div className="params">
      <div className="modes" role="radiogroup" aria-label="Print mode">
        {MODES.map((m) => (
          <button
            key={m.value}
            type="button"
            role="radio"
            aria-checked={options.mode === m.value}
            className={`mode ${options.mode === m.value ? 'mode--active' : ''}`}
            onClick={() => setMode(m.value)}
          >
            <strong>{m.title}</strong>
            <span>{m.text}</span>
          </button>
        ))}
      </div>

      <Slider
        label="Model size"
        hint="Longest side. Above the tile size, the model is printed as a grid of tiles."
        value={options.size_mm}
        min={60}
        max={1000}
        step={5}
        unit="mm"
        onChange={(v) => set('size_mm', v)}
      />
      <Slider
        label="Map margin"
        hint="Terrain around the route. More margin = smaller scale."
        value={options.margin_pct}
        min={0}
        max={100}
        step={1}
        unit="%"
        onChange={(v) => set('margin_pct', v)}
      />
      <Slider
        label="Vertical exaggeration"
        value={options.z_exaggeration}
        min={0.5}
        max={4}
        step={0.1}
        unit="×"
        onChange={(v) => set('z_exaggeration', v)}
      />
      <Slider
        label="Route width"
        hint={
          options.mode === 'inlay' ? 'Slot width; pieces are 2 × clearance narrower.' : undefined
        }
        value={options.route_width_mm}
        min={0.8}
        max={4}
        step={0.1}
        unit="mm"
        onChange={(v) => set('route_width_mm', v)}
      />

      {options.mode === 'inlay' && (
        <>
          <Slider
            label="Piece height"
            hint="Relief covered by one route piece. Very large = a single piece."
            value={options.inlay_piece_height_mm}
            min={2}
            max={100}
            step={1}
            unit="mm"
            onChange={(v) => set('inlay_piece_height_mm', v)}
          />
          <Slider
            label="Fit clearance"
            hint="Gap per side between a piece and its slot (0.15 mm fits on your A1)."
            value={options.inlay_clearance_mm}
            min={0}
            max={0.5}
            step={0.01}
            unit="mm"
            onChange={(v) => set('inlay_clearance_mm', v)}
          />
        </>
      )}

      <button type="button" className="link" onClick={() => setAdvanced(!advanced)}>
        {advanced ? '▾' : '▸'} Advanced settings
      </button>
      {advanced && (
        <div className="advanced">
          <Slider
            label="Base thickness"
            hint="Under the lowest point of the terrain."
            value={options.base_mm}
            min={1}
            max={10}
            step={0.5}
            unit="mm"
            onChange={(v) => set('base_mm', v)}
          />
          <Slider
            label="Route raise"
            hint="How far the route stands above the terrain."
            value={options.route_raise_mm}
            min={0}
            max={2}
            step={0.1}
            unit="mm"
            onChange={(v) => set('route_raise_mm', v)}
          />
          <Slider
            label="Groove depth"
            hint="How deep the route sits into the terrain."
            value={options.groove_depth_mm}
            min={0.4}
            max={3}
            step={0.1}
            unit="mm"
            onChange={(v) => set('groove_depth_mm', v)}
          />
          <Slider
            label="Detail"
            hint="Grid spacing. Finer is slower and makes bigger files."
            value={options.resolution_mm}
            min={0.2}
            max={1}
            step={0.05}
            unit="mm"
            onChange={(v) => set('resolution_mm', v)}
          />
          <Slider
            label="Max tile size"
            hint="Large models are split into tiles up to this size (A1 bed: 256 mm)."
            value={options.max_tile_mm}
            min={120}
            max={250}
            step={5}
            unit="mm"
            onChange={(v) => set('max_tile_mm', v)}
          />
          <div className="field">
            <span className="field__row">
              <span className="field__label">Tile grid</span>
              <label className="field__value">
                <input
                  type="checkbox"
                  checked={options.grid_cols === null}
                  onChange={(e) =>
                    onChange({
                      ...options,
                      grid_cols: e.target.checked ? null : 2,
                      grid_rows: e.target.checked ? null : 1,
                    })
                  }
                />
                automatic
              </label>
            </span>
            {options.grid_cols !== null && (
              <span className="field__row grid-input">
                <input
                  type="number"
                  aria-label="Columns"
                  min={1}
                  max={8}
                  value={options.grid_cols}
                  onChange={(e) => set('grid_cols', Math.max(1, e.target.valueAsNumber || 1))}
                />
                columns ×
                <input
                  type="number"
                  aria-label="Rows"
                  min={1}
                  max={8}
                  value={options.grid_rows ?? 1}
                  onChange={(e) => set('grid_rows', Math.max(1, e.target.valueAsNumber || 1))}
                />
                rows
              </span>
            )}
          </div>
          <button
            type="button"
            className="link"
            onClick={() =>
              onChange({
                ...DEFAULT_OPTIONS,
                mode: options.mode,
                route_width_mm: DEFAULT_ROUTE_WIDTH[options.mode],
              })
            }
          >
            Reset all settings
          </button>
        </div>
      )}
    </div>
  )
}
