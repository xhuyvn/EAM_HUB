import type { StatusDisplay } from '../state'

export function StatusBadge({ status, title }: { status: StatusDisplay; title?: string }) {
  return (
    <span className={`badge tone-${status.tone}`} title={title}>
      <span aria-hidden="true" className="badge-symbol">
        {status.symbol}
      </span>{' '}
      <span>{status.label}</span>
    </span>
  )
}
