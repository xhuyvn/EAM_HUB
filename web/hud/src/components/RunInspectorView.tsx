import { useState } from 'react'
import {
  formatCost,
  formatRemoteOutcome,
  formatRunStatus,
  formatTaskStatus,
  formatTimestampLocal,
} from '../state'
import type { RunInspectorData } from '../types'
import { StatusBadge } from './StatusBadge'

interface RunInspectorViewProps {
  data: RunInspectorData | null
  loading: boolean
  error: string | null
  token: string
  onBack: () => void
}

export function RunInspectorView({ data, loading, error, token, onBack }: RunInspectorViewProps) {
  const [artifactPreview, setArtifactPreview] = useState<{ id: string; body: string } | null>(null)
  const [artifactError, setArtifactError] = useState<string | null>(null)

  async function inspectArtifact(artifactId: string, accessUrl: string) {
    setArtifactError(null)
    try {
      const resp = await fetch(accessUrl, {
        headers: { Authorization: `Bearer ${token}` },
      })
      const text = await resp.text()
      if (!resp.ok) {
        setArtifactError(`Artifact ${artifactId}: HTTP ${resp.status} ${text}`)
        setArtifactPreview(null)
        return
      }
      setArtifactPreview({ id: artifactId, body: text })
    } catch (err) {
      setArtifactError(err instanceof Error ? err.message : 'Failed to fetch artifact')
    }
  }

  return (
    <section aria-label="Run Inspector" className="panel">
      <div className="panel-header">
        <div>
          <h2>Run Inspector</h2>
          <p className="muted">Attempts, commands, event timeline, artifacts (API-only), and usage.</p>
        </div>
        <button type="button" className="btn-sm" onClick={onBack}>
          Back to Tasks &amp; Runs
        </button>
      </div>

      {loading && <p className="muted">Loading run inspector...</p>}
      {error && (
        <div role="alert" className="banner bad">
          {error}
        </div>
      )}
      {!loading && !error && !data && (
        <p className="muted">Select a run from Tasks &amp; Runs or the Attention Queue to inspect it.</p>
      )}

      {data && (
        <div className="stack">
          {(data.run.remote_outcome === 'unknown' || data.run.state === 'unknown') && (
            <div role="alert" className="banner warn">
              <strong>Remote outcome unknown — no retry until reconciled.</strong> Run state is{' '}
              <code>{data.run.state}</code> and remote outcome is{' '}
              <code>{data.run.remote_outcome}</code> (never coerced to success or failure).
            </div>
          )}

          <div className="summary-grid">
            <div className="stat-box">
              <div className="stat-label">Run</div>
              <div className="mono">{data.run.run_id}</div>
              <StatusBadge status={formatRunStatus(data.run.state, data.run.attempt)} />
            </div>
            <div className="stat-box">
              <div className="stat-label">Task</div>
              <div className="mono">{data.run.task_id}</div>
              {data.task && <StatusBadge status={formatTaskStatus(data.task.state)} />}
            </div>
            <div className="stat-box">
              <div className="stat-label">Remote Outcome</div>
              <StatusBadge status={formatRemoteOutcome(data.run.remote_outcome)} />
            </div>
            <div className="stat-box">
              <div className="stat-label">Cost</div>
              <div className="stat-value">{formatCost(data.cost)}</div>
            </div>
          </div>

          <h3>Attempts ({data.attempts.length})</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Attempt</th>
                <th>Run ID</th>
                <th>Binding</th>
                <th>State</th>
                <th>Remote Outcome</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {data.attempts.map((a) => (
                <tr key={a.run_id}>
                  <td>#{a.attempt}</td>
                  <td className="mono">{a.run_id}</td>
                  <td className="mono">{a.binding_id}</td>
                  <td>
                    <StatusBadge status={formatRunStatus(a.state, null)} />
                  </td>
                  <td>
                    <StatusBadge status={formatRemoteOutcome(a.remote_outcome)} />
                  </td>
                  <td>{formatTimestampLocal(a.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>Commands ({data.commands.length})</h3>
          {data.commands.length === 0 ? (
            <p className="muted">No operator commands recorded.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Command ID</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {data.commands.map((c) => (
                  <tr key={c.command_id}>
                    <td className="mono">{c.command_id}</td>
                    <td>{c.command_type}</td>
                    <td>
                      <span className="badge tone-info">• {c.status}</span>
                    </td>
                    <td>{formatTimestampLocal(c.created_at)}</td>
                    <td>{formatTimestampLocal(c.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h3>Event Timeline ({data.events.length})</h3>
          {data.events.length === 0 ? (
            <p className="muted">No committed events for this run.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Occurred</th>
                  <th>Stream / Offset</th>
                  <th>Event Type</th>
                  <th>Producer</th>
                  <th>Payload (redacted text)</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((ev) => (
                  <tr key={ev.event_id}>
                    <td>{formatTimestampLocal(ev.occurred_at)}</td>
                    <td className="mono">
                      {ev.stream_id} #{ev.ingest_offset}
                    </td>
                    <td className="mono">{ev.event_type}</td>
                    <td>{ev.producer_id}</td>
                    <td>
                      <pre className="pre-text">{JSON.stringify(ev.payload)}</pre>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h3>Artifacts ({data.artifacts.length})</h3>
          {artifactError && (
            <div role="alert" className="banner bad">
              {artifactError}
            </div>
          )}
          {data.artifacts.length === 0 ? (
            <p className="muted">No artifacts recorded.</p>
          ) : (
            <ul className="plain-list">
              {data.artifacts.map((art) => (
                <li key={art.artifact_id} className="list-row">
                  <span className="mono">
                    {art.artifact_id} ({art.content_type}, {art.size_bytes} B)
                  </span>
                  <button
                    type="button"
                    className="btn-sm"
                    onClick={() => inspectArtifact(art.artifact_id, art.access_url)}
                  >
                    Fetch via API
                  </button>
                </li>
              ))}
            </ul>
          )}
          {artifactPreview && (
            <div className="card">
              <strong>Artifact {artifactPreview.id}</strong>
              <pre className="pre-text">{artifactPreview.body}</pre>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
