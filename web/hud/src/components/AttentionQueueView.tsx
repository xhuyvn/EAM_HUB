import type { AttentionItem } from '../types'
import { StatusBadge } from './StatusBadge'

interface AttentionQueueViewProps {
  items: AttentionItem[]
  onOpenRun: (runId: string) => void
  onOpenProject: (projectId: string) => void
}

export function AttentionQueueView({ items, onOpenRun, onOpenProject }: AttentionQueueViewProps) {
  return (
    <section aria-label="Attention Queue" className="panel">
      <div className="panel-header">
        <div>
          <h2>Attention Queue</h2>
          <p className="muted">
            Runs with unknown remote outcome, contradicted required criteria, scan errors, and
            stale evidence/connectivity.
          </p>
        </div>
        <span className="pill">{items.length} items</span>
      </div>

      {items.length === 0 ? (
        <div className="banner info">No active attention items in current scope.</div>
      ) : (
        <ul className="plain-list stack">
          {items.map((item) => (
            <li key={item.id} className="card list-row">
              <div className="stack-xs">
                <div>
                  <StatusBadge
                    status={
                      item.severity === 'critical'
                        ? { label: item.category, symbol: '✗', tone: 'bad' }
                        : { label: item.category, symbol: '⚠', tone: 'warn' }
                    }
                  />{' '}
                  <strong>{item.title}</strong>
                </div>
                <div className="muted small">{item.detail}</div>
              </div>
              <div>
                {item.targetTab === 'inspector' && item.runId && (
                  <button
                    type="button"
                    className="btn-sm"
                    onClick={() => onOpenRun(item.runId!)}
                  >
                    Inspect Run
                  </button>
                )}
                {item.targetTab === 'ppm' && (
                  <button
                    type="button"
                    className="btn-sm"
                    onClick={() => onOpenProject(item.projectId || '')}
                  >
                    Open Assessment
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
