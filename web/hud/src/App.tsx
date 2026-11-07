import { useState, useEffect } from 'react'

interface HealthResponse {
  status: string
  env: string
  redis_enabled: boolean
  qdrant_enabled: boolean
}

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [lastChecked, setLastChecked] = useState<string | null>(null)

  useEffect(() => {
    fetch('/healthz')
      .then((res) => {
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`)
        }
        return res.json()
      })
      .then((data: HealthResponse) => {
        setHealth(data)
        setLastChecked(new Date().toISOString())
        setLoading(false)
      })
      .catch(() => {
        setHealth(null)
        setLastChecked(new Date().toISOString())
        setLoading(false)
      })
  }, [])

  return (
    <div className="hud-container">
      <header className="hud-header">
        <h1>EAP HUD (Observability & PPM Console)</h1>
        <div className="hud-status">
          {loading ? (
            <span>Connecting...</span>
          ) : health ? (
            <span className="status-badge online">
              Backend: {health.status} ({health.env}) [checked: {lastChecked}]
            </span>
          ) : (
            <span className="status-badge offline">
              Backend unreachable [last checked: {lastChecked}]
            </span>
          )}
        </div>
      </header>
      <main className="hud-main">
        <section className="card">
          <h2>Platform Shell</h2>
          <p>Modular monolith skeleton running on Vite + React + TypeScript (Node 26 LTS).</p>
        </section>
      </main>
    </div>
  )
}
