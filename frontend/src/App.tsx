import { useEffect, useState } from 'react'

type BackendStatus = 'checking' | 'ok' | 'unreachable'

function App() {
  const [status, setStatus] = useState<BackendStatus>('checking')

  useEffect(() => {
    fetch('/api/health')
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then(() => setStatus('ok'))
      .catch(() => setStatus('unreachable'))
  }, [])

  return (
    <main className="app">
      <h1>3D Topo Print</h1>
      <p>Upload a GPX route and turn it into a printable terrain model.</p>
      <p className="status">Backend: {status}</p>
    </main>
  )
}

export default App
