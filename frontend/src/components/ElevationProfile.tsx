import { useEffect, useMemo, useRef, useState } from 'react'
import type { Profile } from '../api'

export type Range = [number, number] // start, end in km along the route

interface Props {
  profile: Profile
  lengthKm: number
  range: Range
  onChange: (range: Range) => void
}

const HEIGHT = 120
const PAD = { top: 10, right: 10, bottom: 20, left: 40 }
// Smallest selectable portion, as a share of the route.
const MIN_SHARE = 0.02

function niceStep(span: number, ticks: number) {
  const raw = span / ticks
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const residual = raw / magnitude
  return magnitude * (residual > 5 ? 10 : residual > 2 ? 5 : residual > 1 ? 2 : 1)
}

export function ElevationProfile({ profile, lengthKm, range, onChange }: Props) {
  const svg = useRef<SVGSVGElement>(null)
  const [width, setWidth] = useState(320)
  const [hover, setHover] = useState<number | null>(null) // index into the profile
  const [dragging, setDragging] = useState<0 | 1 | null>(null)

  // Follow the real width so lines and handles keep their pixel size.
  useEffect(() => {
    const el = svg.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const d = profile.distance_km
  const e = profile.elevation_m
  const [lo, hi] = useMemo(() => {
    const min = Math.min(...e)
    const max = Math.max(...e)
    const pad = Math.max((max - min) * 0.1, 10)
    return [min - pad, max + pad]
  }, [e])

  const plotW = Math.max(width - PAD.left - PAD.right, 10)
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const x = (km: number) => PAD.left + (km / lengthKm) * plotW
  const y = (m: number) => PAD.top + (1 - (m - lo) / (hi - lo)) * plotH
  const kmAt = (px: number) => Math.min(Math.max(((px - PAD.left) / plotW) * lengthKm, 0), lengthKm)

  const line = (from: number, to: number) => {
    const pts = d.map((km, i) => [km, e[i]] as const).filter(([km]) => km >= from && km <= to)
    return pts
      .map(([km, m], i) => `${i ? 'L' : 'M'}${x(km).toFixed(1)},${y(m).toFixed(1)}`)
      .join('')
  }
  const area = (from: number, to: number) => {
    const path = line(from, to)
    if (!path) return ''
    const base = (PAD.top + plotH).toFixed(1)
    return `${path}L${x(Math.min(to, d[d.length - 1])).toFixed(1)},${base}L${x(Math.max(from, d[0])).toFixed(1)},${base}Z`
  }

  const step = niceStep(hi - lo, 3)
  const yTicks: number[] = []
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) yTicks.push(t)
  const kmStep = niceStep(lengthKm, 4)
  const xTicks: number[] = []
  for (let t = 0; t <= lengthKm + 1e-9; t += kmStep) xTicks.push(t)

  const minGap = lengthKm * MIN_SHARE
  function moveHandle(which: 0 | 1, km: number) {
    const [start, end] = range
    if (which === 0) onChange([Math.min(Math.max(km, 0), end - minGap), end])
    else onChange([start, Math.max(Math.min(km, lengthKm), start + minGap)])
  }

  function localX(event: React.PointerEvent) {
    const rect = svg.current!.getBoundingClientRect()
    return event.clientX - rect.left
  }

  function onPointerMove(event: React.PointerEvent) {
    const px = localX(event)
    if (dragging !== null) {
      moveHandle(dragging, kmAt(px))
      return
    }
    const km = kmAt(px)
    // Nearest sample for the tooltip.
    let i = Math.round((km / lengthKm) * (d.length - 1))
    i = Math.min(Math.max(i, 0), d.length - 1)
    setHover(px >= PAD.left && px <= PAD.left + plotW ? i : null)
  }

  const [start, end] = range
  const handles: [0 | 1, number][] = [
    [0, start],
    [1, end],
  ]

  return (
    <div className="profile">
      <svg
        ref={svg}
        className="profile__chart"
        height={HEIGHT}
        role="img"
        aria-label={`Elevation profile, ${lengthKm.toFixed(1)} km`}
        onPointerMove={onPointerMove}
        onPointerLeave={() => setHover(null)}
        onPointerUp={() => setDragging(null)}
      >
        {/* Axes and gridlines: hairline, recessive. */}
        {yTicks.map((t) => (
          <g key={`y${t}`}>
            <line
              className="profile__grid"
              x1={PAD.left}
              x2={PAD.left + plotW}
              y1={y(t)}
              y2={y(t)}
            />
            <text className="profile__tick" x={PAD.left - 6} y={y(t)} dy="0.32em" textAnchor="end">
              {t.toLocaleString('en')}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text
            key={`x${t}`}
            className="profile__tick"
            x={x(t)}
            y={HEIGHT - 4}
            textAnchor={t === 0 ? 'start' : 'middle'}
          >
            {t.toLocaleString('en', { maximumFractionDigits: 1 })}
            {t === 0 ? ' km' : ''}
          </text>
        ))}

        {/* Whole route in gray, selected portion in the route colour. */}
        <path className="profile__area profile__area--off" d={area(0, lengthKm)} />
        <path className="profile__line profile__line--off" d={line(0, lengthKm)} />
        <path className="profile__area" d={area(start, end)} />
        <path className="profile__line" d={line(start, end)} />

        {hover !== null && dragging === null && (
          <g pointerEvents="none">
            <line
              className="profile__crosshair"
              x1={x(d[hover])}
              x2={x(d[hover])}
              y1={PAD.top}
              y2={PAD.top + plotH}
            />
            <circle className="profile__dot" cx={x(d[hover])} cy={y(e[hover])} r={4} />
          </g>
        )}

        {handles.map(([which, km]) => (
          <g
            key={which}
            className={`profile__handle ${dragging === which ? 'profile__handle--active' : ''}`}
            transform={`translate(${x(km)},0)`}
            tabIndex={0}
            role="slider"
            aria-label={which === 0 ? 'Start of the printed portion' : 'End of the printed portion'}
            aria-valuemin={0}
            aria-valuemax={lengthKm}
            aria-valuenow={km}
            aria-valuetext={`${km.toFixed(1)} km`}
            onPointerDown={(event) => {
              event.stopPropagation()
              ;(event.target as Element).setPointerCapture?.(event.pointerId)
              setDragging(which)
            }}
            onPointerUp={() => setDragging(null)}
            onKeyDown={(event) => {
              const delta = (event.shiftKey ? 0.05 : 0.01) * lengthKm
              if (event.key === 'ArrowLeft') moveHandle(which, km - delta)
              else if (event.key === 'ArrowRight') moveHandle(which, km + delta)
              else return
              event.preventDefault()
            }}
          >
            {/* Wide invisible hit area around the thin handle line. */}
            <rect x={-8} y={PAD.top} width={16} height={plotH} fill="transparent" />
            <line y1={PAD.top} y2={PAD.top + plotH} />
            <rect
              className="profile__grip"
              x={-5}
              y={PAD.top + plotH / 2 - 10}
              width={10}
              height={20}
              rx={4}
            />
          </g>
        ))}
      </svg>
      {hover !== null && dragging === null && (
        <div
          className="profile__tooltip"
          style={{ left: Math.min(Math.max(x(d[hover]), 50), width - 50) }}
        >
          {d[hover].toFixed(1)} km · {Math.round(e[hover]).toLocaleString('en')} m
        </div>
      )}
    </div>
  )
}
