import { useState } from 'react'
import { formatCost, formatTimestampLocal } from '../state'
import type { AgentInventoryResponse } from '../types'
import { StatusBadge } from './StatusBadge'

interface AgentInventoryViewProps {
  inventory: AgentInventoryResponse | null
  loading: boolean
  error: string | null
  token: string
  onRefresh: () => void
}

function agentHealthBadge(status: string) {
  switch (status) {
    case 'healthy':
      return { label: 'healthy', symbol: '✓', tone: 'ok' as const }
    case 'degraded':
      return { label: 'degraded', symbol: '⚠', tone: 'warn' as const }
    case 'no_data':
      return { label: 'no_data (0 runs)', symbol: '○', tone: 'neutral' as const }
    default:
      return { label: 'unknown', symbol: '?', tone: 'warn' as const }
  }
}

export function AgentInventoryView({
  inventory,
  loading,
  error,
  token,
  onRefresh,
}: AgentInventoryViewProps) {
  const [evidenceResult, setEvidenceResult] = useState<{ ref: string; body: string } | null>(null)
  const [evidenceError, setEvidenceError] = useState<string | null>(null)

  async function inspectCapabilityEvidence(ref: string) {
    setEvidenceError(null)
    const id = ref.startsWith('artifact:') ? ref.slice('artifact:'.length) : ref
    try {
      const resp = await fetch(`/v1/artifacts/${encodeURIComponent(id)}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      const text = await resp.text()
      if (!resp.ok) {
        setEvidenceError(`Capability evidence ${ref}: HTTP ${resp.status} ${text}`)
        setEvidenceResult(null)
        return
      }
      setEvidenceResult({ ref, body: text })
    } catch (err) {
      setEvidenceError(err instanceof Error ? err.message : 'Failed to fetch capability evidence')
    }
  }

  return (
    <section aria-label="Agent Inventory" className="panel">
      <div className="panel-header">
        <div>
          <h2>Agent Inventory (Read-Only)</h2>
          <p className="muted">
            Registered agents, versions, bindings, execution boundaries, certified capabilities,
            derived health, and unattributed runs.
          </p>
        </div>
        <button type="button" className="btn-sm" onClick={onRefresh} disabled={loading}>
          {loading ? 'Refreshing...' : 'Refresh Agents'}
        </button>
      </div>

      {error && (
        <div role="alert" className="banner bad">
          {error}
        </div>
      )}

      {evidenceError && (
        <div role="alert" className="banner bad">
          {evidenceError}
        </div>
      )}

      {evidenceResult && (
        <div className="card">
          <strong>Capability Evidence ({evidenceResult.ref})</strong>
          <pre className="pre-text">{evidenceResult.body}</pre>
        </div>
      )}

      {inventory && (
        <div className="stack">
          <div className="summary-grid">
            <div className="stat-box">
              <div className="stat-label">Registered Agents</div>
              <div className="stat-value">{inventory.agents.length}</div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Unattributed Runs</div>
              <div className="stat-value">{inventory.unattributed_runs_count}</div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Generated At</div>
              <div className="stat-value small">{formatTimestampLocal(inventory.generated_at)}</div>
            </div>
          </div>

          {inventory.agents.length === 0 ? (
            <p className="muted">No agents registered for this tenant.</p>
          ) : (
            inventory.agents.map((agent) => (
              <article key={`${agent.agent_id}-${agent.version}`} className="card stack">
                <div className="panel-header">
                  <div>
                    <h3>
                      {agent.name}{' '}
                      <span className="muted mono small">
                        ({agent.agent_id} · v{agent.version})
                      </span>
                    </h3>
                    <div className="muted small">
                      Domain: {agent.domain} · Runtime: {agent.runtime_kind} · Status:{' '}
                      {agent.status} · Owner:{' '}
                      {agent.owner?.verification_label || agent.owner?.business_owner_ref || 'unverified'}
                    </div>
                  </div>
                  <StatusBadge status={agentHealthBadge(agent.health.status)} />
                </div>

                <div className="summary-grid">
                  <div className="stat-box">
                    <div className="stat-label">Runs / Unknown / Unattributed</div>
                    <div className="stat-value small">
                      {agent.health.total_runs} runs · {agent.health.unknown_outcome_count} unknown ·{' '}
                      {agent.health.unattributed_run_count} unattributed
                    </div>
                  </div>
                  <div className="stat-box">
                    <div className="stat-label">Last Observed</div>
                    <div className="stat-value small">
                      {formatTimestampLocal(agent.health.last_observed_at)}
                    </div>
                  </div>
                  <div className="stat-box">
                    <div className="stat-label">Derived Cost</div>
                    <div className="stat-value small">{formatCost(agent.health.cost)}</div>
                  </div>
                </div>

                <div>
                  <strong>
                    Bindings ({agent.active_binding_count} active / {agent.revoked_binding_count}{' '}
                    revoked)
                  </strong>
                  {agent.bindings.length === 0 ? (
                    <p className="muted">No bindings.</p>
                  ) : (
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Binding ID</th>
                          <th>Release</th>
                          <th>Mode / Boundary</th>
                          <th>Status</th>
                          <th>Certified Capabilities</th>
                          <th>Evidence Ref</th>
                        </tr>
                      </thead>
                      <tbody>
                        {agent.bindings.map((b) => (
                          <tr key={b.binding_id}>
                            <td className="mono">{b.binding_id}</td>
                            <td className="mono">{b.release_version}</td>
                            <td>
                              {b.mode} · <code>{b.execution_boundary}</code>
                            </td>
                            <td>
                              <StatusBadge
                                status={
                                  b.status === 'active'
                                    ? { label: 'active', symbol: '✓', tone: 'ok' }
                                    : { label: 'revoked', symbol: '✗', tone: 'bad' }
                                }
                              />
                            </td>
                            <td>
                              <div className="cap-list">
                                {Object.entries(b.certified_capabilities || {}).map(([cap, ok]) => (
                                  <span
                                    key={cap}
                                    className={`badge ${ok ? 'tone-ok' : 'tone-neutral'}`}
                                  >
                                    {ok ? '✓' : '—'} {cap}
                                  </span>
                                ))}
                              </div>
                            </td>
                            <td>
                              {b.capability_evidence_ref ? (
                                <button
                                  type="button"
                                  className="btn-sm"
                                  onClick={() => inspectCapabilityEvidence(b.capability_evidence_ref!)}
                                >
                                  {b.capability_evidence_ref}
                                </button>
                              ) : (
                                <span className="muted">none</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </article>
            ))
          )}
        </div>
      )}
    </section>
  )
}
