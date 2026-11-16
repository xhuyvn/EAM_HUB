import { useState, type KeyboardEvent } from 'react'
import {
  formatConnectivity,
  formatCost,
  formatProgress,
  formatRemoteOutcome,
  formatRunStatus,
  formatTaskStatus,
} from '../state'
import type { HudRow } from '../types'
import { StatusBadge } from './StatusBadge'

interface TasksRunsViewProps {
  rows: HudRow[]
  selectedRunId: string | null
  onInspectRun: (runId: string) => void
  onSelectProject: (projectId: string) => void
  onCancelRun: (runId: string) => Promise<void>
  onRetryRun: (runId: string) => Promise<void>
}

const TERMINAL_RUN_STATES = new Set(['succeeded', 'failed', 'cancelled', 'timed_out'])

export function TasksRunsView({
  rows,
  selectedRunId,
  onInspectRun,
  onSelectProject,
  onCancelRun,
  onRetryRun,
}: TasksRunsViewProps) {
  const [focusedIndex, setFocusedIndex] = useState<number>(0)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [actionMsg, setActionMsg] = useState<string | null>(null)

  function handleKeyDown(e: KeyboardEvent<HTMLTableSectionElement>) {
    if (rows.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setFocusedIndex((prev) => Math.min(rows.length - 1, prev + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setFocusedIndex((prev) => Math.max(0, prev - 1))
    } else if (e.key === 'Enter') {
      const row = rows[focusedIndex]
      if (row?.run_id) {
        e.preventDefault()
        onInspectRun(row.run_id)
      }
    }
  }

  async function handleCancel(runId: string) {
    setBusyKey(`cancel:${runId}`)
    setActionMsg(null)
    try {
      await onCancelRun(runId)
      setActionMsg(`Cancel requested for ${runId}.`)
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : 'Cancel failed')
    } finally {
      setBusyKey(null)
    }
  }

  async function handleRetry(runId: string) {
    setBusyKey(`retry:${runId}`)
    setActionMsg(null)
    try {
      await onRetryRun(runId)
      setActionMsg(`Retry attempt created for ${runId}.`)
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : 'Retry failed')
    } finally {
      setBusyKey(null)
    }
  }

  return (
    <section aria-label="Tasks and Runs" className="panel">
      <div className="panel-header">
        <div>
          <h2>Tasks &amp; Runs</h2>
          <p className="muted">
            Independent state axes: Task, Run, Remote Outcome, Connectivity, Progress, and Cost.
            Use Up/Down arrows and Enter to inspect a run.
          </p>
        </div>
        <span className="pill">{rows.length} rows</span>
      </div>

      {actionMsg && (
        <div role="status" className="banner info">
          {actionMsg}
        </div>
      )}

      {rows.length === 0 ? (
        <p className="muted">No tasks or runs in current projection scope.</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Task / Project</th>
                <th>Task Status</th>
                <th>Run Status</th>
                <th>Remote Outcome</th>
                <th>Connectivity / Last Observed</th>
                <th>Progress</th>
                <th>Cost</th>
                <th>Pending Command</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody onKeyDown={handleKeyDown}>
              {rows.map((row, idx) => {
                const isSelected = Boolean(row.run_id && row.run_id === selectedRunId)
                const isFocused = idx === focusedIndex
                const isUnknownOutcome =
                  row.remote_outcome === 'unknown' || row.run_state === 'unknown'
                const isTerminal = row.run_state ? TERMINAL_RUN_STATES.has(row.run_state) : true
                const canCancel = Boolean(row.run_id && !isTerminal && row.run_state !== 'cancelling')
                const canRetry = Boolean(
                  row.run_id &&
                    !isUnknownOutcome &&
                    row.run_state &&
                    ['failed', 'cancelled', 'timed_out'].includes(row.run_state),
                )

                return (
                  <tr
                    key={`${row.task_id}-${row.run_id ?? 'none'}`}
                    tabIndex={0}
                    onFocus={() => setFocusedIndex(idx)}
                    className={`${isSelected ? 'row-selected' : ''} ${isFocused ? 'row-focused' : ''}`.trim()}
                  >
                    <td>
                      <div className="mono">{row.task_id}</div>
                      {row.run_id && <div className="muted mono small">{row.run_id}</div>}
                      {row.project && (
                        <button
                          type="button"
                          className="link-btn"
                          onClick={() => onSelectProject(row.project!.project_id)}
                        >
                          Project: {row.project.project_id}
                        </button>
                      )}
                    </td>
                    <td>
                      <StatusBadge status={formatTaskStatus(row.task_state)} />
                    </td>
                    <td>
                      <StatusBadge status={formatRunStatus(row.run_state, row.attempt)} />
                    </td>
                    <td>
                      <StatusBadge status={formatRemoteOutcome(row.remote_outcome)} />
                    </td>
                    <td>
                      <StatusBadge
                        status={formatConnectivity(
                          row.connectivity,
                          row.last_observed_at,
                          row.observation_source,
                        )}
                        title={row.last_observed_at ? `UTC: ${row.last_observed_at}` : undefined}
                      />
                    </td>
                    <td>{formatProgress(row.progress)}</td>
                    <td className={row.cost.status === 'unknown' ? 'cost-unknown' : ''}>
                      {formatCost(row.cost)}
                    </td>
                    <td>
                      {row.pending_command ? (
                        <span className="badge tone-warn">
                          ◷ {row.pending_command.kind} ({row.pending_command.state})
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <div className="btn-group">
                        {row.run_id && (
                          <button
                            type="button"
                            className="btn-sm"
                            onClick={() => onInspectRun(row.run_id!)}
                          >
                            Inspect
                          </button>
                        )}
                        {row.run_id && (
                          <button
                            type="button"
                            className="btn-sm"
                            disabled={!canCancel || busyKey === `cancel:${row.run_id}`}
                            onClick={() => handleCancel(row.run_id!)}
                          >
                            Cancel
                          </button>
                        )}
                        {row.run_id && (
                          <button
                            type="button"
                            className="btn-sm"
                            disabled={!canRetry || busyKey === `retry:${row.run_id}`}
                            title={
                              isUnknownOutcome
                                ? 'Retry blocked: remote outcome is unknown until reconciled'
                                : undefined
                            }
                            onClick={() => handleRetry(row.run_id!)}
                          >
                            Retry
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
