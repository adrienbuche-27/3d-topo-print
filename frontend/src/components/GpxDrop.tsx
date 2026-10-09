import { useRef, useState } from 'react'

interface Props {
  onFile: (file: File) => void
  busy: boolean
  compact: boolean
}

export function GpxDrop({ onFile, busy, compact }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  function pick(files: FileList | null) {
    const file = files?.[0]
    if (file) onFile(file)
  }

  return (
    <div
      className={`drop ${dragging ? 'drop--active' : ''} ${compact ? 'drop--compact' : ''}`}
      onClick={() => input.current?.click()}
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        pick(e.dataTransfer.files)
      }}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
    >
      <input
        ref={input}
        type="file"
        accept=".gpx,application/gpx+xml"
        hidden
        onChange={(e) => {
          pick(e.target.files)
          e.target.value = ''
        }}
      />
      {busy ? (
        <span>Reading GPX…</span>
      ) : compact ? (
        <span>Drop another GPX, or click to choose</span>
      ) : (
        <>
          <strong>Drop a GPX file here</strong>
          <span>or click to choose one (Strava, Garmin, Komoot…)</span>
        </>
      )}
    </div>
  )
}
