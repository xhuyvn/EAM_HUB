import type {
  AssessmentDetail,
  AttentionItem,
  ConnectivityState,
  CostInfo,
  HudProjection,
  HudRow,
  ProgressInfo,
  RemoteOutcome,
  RunState,
  SourceScheduleStatus,
  TaskState,
} from './types'

export interface StatusDisplay {
  label: string
  symbol: string
  tone: 'ok' | 'warn' | 'bad' | 'neutral' | 'info'
}

export function formatTimestampLocal(iso: string | null | undefined): string {
  if (!iso) return 'unknown'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function formatWithDenominator(
  numerator: number | null | undefined,
  denominator: number | null | undefined,
  unitLabel: string,
): string {
  if (
    typeof denominator !== 'number' ||
    !Number.isFinite(denominator) ||
    denominator <= 0 ||
    typeof numerator !== 'number' ||
    !Number.isFinite(numerator)
  ) {
    return 'unknown'
  }
  const pct = Math.round((numerator / denominator) * 100)
  return `${numerator} of ${denominator} ${unitLabel} (${pct}%)`
}

export function formatProgress(progress: ProgressInfo | null | undefined): string {
  if (!progress) return 'No progress'
  if (progress.kind === 'units') {
    if (
      typeof progress.total_units !== 'number' ||
      !Number.isFinite(progress.total_units) ||
      progress.total_units < 1 ||
      typeof progress.completed_units !== 'number' ||
      !Number.isFinite(progress.completed_units)
    ) {
      return progress.stage ? `Stage: ${progress.stage}` : 'unknown'
    }
    const pct = Math.round((progress.completed_units / progress.total_units) * 100)
    const unitPart = progress.unit ? ` ${progress.unit}` : ''
    const stagePart = progress.stage ? ` · ${progress.stage}` : ''
    return `${progress.completed_units} / ${progress.total_units}${unitPart} (${pct}%)${stagePart}`
  }
  if (progress.kind === 'stage_only') {
    return progress.stage ? `Stage: ${progress.stage}` : 'Stage only (no total)'
  }
  return 'No progress'
}

export function formatCost(cost: CostInfo | null | undefined): string {
  if (!cost || cost.status === 'unknown' || cost.amount_usd === null || !Number.isFinite(cost.amount_usd)) {
    return 'Unknown'
  }
  const formatted = `$${cost.amount_usd.toFixed(cost.amount_usd < 0.01 && cost.amount_usd > 0 ? 4 : 2)}`
  if (cost.status === 'partial') {
    return `≥ ${formatted} (some calls unknown)`
  }
  return `${formatted} (${cost.status})`
}

export function formatTaskStatus(state: TaskState | string): StatusDisplay {
  switch (state) {
    case 'completed':
      return { label: 'completed', symbol: '✓', tone: 'ok' }
    case 'active':
      return { label: 'active', symbol: '▶', tone: 'info' }
    case 'review_required':
      return { label: 'review_required', symbol: '⚑', tone: 'warn' }
    case 'blocked':
    case 'failed':
      return { label: state, symbol: '✗', tone: 'bad' }
    case 'cancelled':
      return { label: 'cancelled', symbol: '⊘', tone: 'neutral' }
    default:
      return { label: state, symbol: '•', tone: 'neutral' }
  }
}

export function formatRunStatus(state: RunState | null | undefined, attempt: number | null | undefined): StatusDisplay {
  const prefix = typeof attempt === 'number' ? `Attempt ${attempt} · ` : ''
  if (!state) {
    return { label: `${prefix}none`, symbol: '—', tone: 'neutral' }
  }
  switch (state) {
    case 'succeeded':
      return { label: `${prefix}succeeded`, symbol: '✓', tone: 'ok' }
    case 'running':
    case 'dispatching':
    case 'queued':
      return { label: `${prefix}${state}`, symbol: '▶', tone: 'info' }
    case 'waiting_approval':
    case 'cancelling':
      return { label: `${prefix}${state}`, symbol: '◷', tone: 'warn' }
    case 'unknown':
      return { label: `${prefix}unknown`, symbol: '?', tone: 'warn' }
    case 'failed':
    case 'timed_out':
      return { label: `${prefix}${state}`, symbol: '✗', tone: 'bad' }
    case 'cancelled':
      return { label: `${prefix}cancelled`, symbol: '⊘', tone: 'neutral' }
    default:
      return { label: `${prefix}${state}`, symbol: '•', tone: 'neutral' }
  }
}

export function formatRemoteOutcome(outcome: RemoteOutcome | string): StatusDisplay {
  if (outcome === 'unknown') {
    return {
      label: 'Remote outcome unknown — no retry until reconciled',
      symbol: '?',
      tone: 'warn',
    }
  }
  if (outcome === 'known') {
    return { label: 'known', symbol: '✓', tone: 'ok' }
  }
  return { label: outcome, symbol: '—', tone: 'neutral' }
}

export function formatConnectivity(
  connectivity: ConnectivityState | string,
  lastObservedAt: string | null | undefined,
  observationSource: string | null | undefined,
): StatusDisplay {
  const timeText = lastObservedAt ? formatTimestampLocal(lastObservedAt) : 'never'
  const srcText = observationSource ? ` via ${observationSource}` : ''
  switch (connectivity) {
    case 'healthy':
      return { label: `Healthy · ${timeText}${srcText}`, symbol: '●', tone: 'ok' }
    case 'stale':
      return { label: `Stale · last observed ${timeText}${srcText}`, symbol: '⚠', tone: 'warn' }
    case 'offline':
      return { label: `Offline · last observed ${timeText}${srcText}`, symbol: '✗', tone: 'bad' }
    default:
      return { label: `Unknown · last observed ${timeText}${srcText}`, symbol: '?', tone: 'warn' }
  }
}

export function applyHudSseEvent(
  snapshot: HudProjection,
  eventName: string,
  data: Record<string, unknown>,
  eventId?: string | null,
): HudProjection {
  if (eventName === 'row.removed') {
    const targetTaskId = typeof data.task_id === 'string' ? data.task_id : null
    const targetRunId = typeof data.run_id === 'string' ? data.run_id : null
    const nextRows = snapshot.rows.filter((row) => {
      if (targetRunId && row.run_id) {
        return !(row.task_id === targetTaskId && row.run_id === targetRunId)
      }
      return row.task_id !== targetTaskId
    })
    return {
      ...snapshot,
      snapshot_cursor: eventId || snapshot.snapshot_cursor,
      rows: nextRows,
    }
  }

  const rawPayload =
    data.payload && typeof data.payload === 'object' && !Array.isArray(data.payload)
      ? (data.payload as Record<string, unknown>)
      : data
  const envelopePayload =
    typeof rawPayload.event_type === 'string' &&
    rawPayload.payload &&
    typeof rawPayload.payload === 'object' &&
    !Array.isArray(rawPayload.payload)
      ? (rawPayload.payload as Record<string, unknown>)
      : rawPayload

  const streamId = typeof data.stream_id === 'string' ? data.stream_id : ''
  const runIdFromStream = streamId.startsWith('run:') ? streamId.slice(4) : null
  const runId =
    runIdFromStream ||
    (typeof data.run_id === 'string' ? data.run_id : null) ||
    (typeof envelopePayload.run_id === 'string' ? envelopePayload.run_id : null)
  const taskId =
    (typeof data.task_id === 'string' ? data.task_id : null) ||
    (typeof envelopePayload.task_id === 'string' ? envelopePayload.task_id : null)
  const occurredAt = typeof data.occurred_at === 'string' ? data.occurred_at : snapshot.as_of
  const producerId = typeof data.producer_id === 'string' ? data.producer_id : 'sse'

  const idx = snapshot.rows.findIndex((r) =>
    runId ? r.run_id === runId : taskId ? r.task_id === taskId : false,
  )

  const baseRow: HudRow =
    idx >= 0
      ? structuredClone(snapshot.rows[idx])
      : {
          task_id: taskId || 'unknown',
          task_state: 'active',
          run_id: runId,
          attempt: typeof envelopePayload.attempt === 'number' ? envelopePayload.attempt : 1,
          run_state: 'queued',
          remote_outcome: 'unknown',
          connectivity: 'unknown',
          last_observed_at: occurredAt,
          observation_source: producerId,
          progress: { kind: 'none', completed_units: null, total_units: null, unit: null, stage: null },
          project: null,
          cost: { status: 'unknown', amount_usd: null, as_of: null, scope: 'run' },
          pending_command: null,
        }

  if (occurredAt) baseRow.last_observed_at = occurredAt
  if (producerId) baseRow.observation_source = producerId

  switch (eventName) {
    case 'run.started': {
      baseRow.run_state = 'running'
      baseRow.task_state = 'active'
      baseRow.remote_outcome = 'known'
      baseRow.connectivity = 'healthy'
      break
    }
    case 'run.progress': {
      const completed = envelopePayload.completed_units
      const total = envelopePayload.total_units
      const stage = typeof envelopePayload.stage === 'string' ? envelopePayload.stage : null
      const unit = typeof envelopePayload.unit === 'string' ? envelopePayload.unit : null
      if (typeof completed === 'number' && typeof total === 'number' && total >= 1) {
        baseRow.progress = {
          kind: 'units',
          completed_units: completed,
          total_units: total,
          unit,
          stage,
        }
      } else if (stage) {
        baseRow.progress = {
          kind: 'stage_only',
          completed_units: null,
          total_units: null,
          unit,
          stage,
        }
      }
      break
    }
    case 'run.state_changed': {
      const toState = typeof envelopePayload.to_state === 'string' ? (envelopePayload.to_state as RunState) : null
      if (toState) {
        baseRow.run_state = toState
        if (toState === 'unknown') {
          baseRow.remote_outcome = 'unknown'
        } else if (toState === 'running') {
          baseRow.task_state = 'active'
          if (baseRow.remote_outcome === 'unknown') baseRow.remote_outcome = 'known'
        } else if (toState === 'succeeded') {
          baseRow.task_state = 'completed'
          baseRow.remote_outcome = 'known'
          baseRow.pending_command = null
        } else if (toState === 'cancelled') {
          baseRow.task_state = 'cancelled'
          baseRow.remote_outcome = 'known'
          baseRow.pending_command = null
        } else if (toState === 'failed') {
          baseRow.task_state = 'failed'
          baseRow.remote_outcome = 'known'
          baseRow.pending_command = null
        } else if (toState === 'timed_out') {
          baseRow.task_state = 'failed'
          baseRow.remote_outcome = 'unknown'
        } else if (toState === 'waiting_approval') {
          baseRow.task_state = 'review_required'
        }
      }
      break
    }
    case 'run.waiting_approval': {
      baseRow.run_state = 'waiting_approval'
      baseRow.task_state = 'review_required'
      break
    }
    case 'run.finished': {
      const outcome = envelopePayload.outcome
      if (outcome === 'succeeded') {
        baseRow.run_state = 'succeeded'
        baseRow.task_state = 'completed'
        baseRow.remote_outcome = 'known'
      } else if (outcome === 'failed') {
        baseRow.run_state = 'failed'
        baseRow.task_state = 'failed'
        baseRow.remote_outcome = 'known'
      } else if (outcome === 'cancelled') {
        baseRow.run_state = 'cancelled'
        baseRow.task_state = 'cancelled'
        baseRow.remote_outcome = 'known'
      }
      break
    }
    case 'worker.heartbeat': {
      baseRow.connectivity = 'healthy'
      break
    }
    case 'connector.status': {
      if (typeof envelopePayload.connectivity === 'string') {
        baseRow.connectivity = envelopePayload.connectivity as ConnectivityState
      }
      if (typeof envelopePayload.last_observed_at === 'string') {
        baseRow.last_observed_at = envelopePayload.last_observed_at
      }
      break
    }
    case 'usage.reported': {
      const costStatus = (envelopePayload.cost_status as CostInfo['status']) || 'unknown'
      const costUsd = typeof envelopePayload.cost_usd === 'number' ? envelopePayload.cost_usd : null
      const usageScope = envelopePayload.usage_scope
      if (costStatus === 'unknown' || costUsd === null) {
        baseRow.cost = { status: 'unknown', amount_usd: null, as_of: occurredAt, scope: 'run' }
      } else if (usageScope === 'run_cumulative') {
        baseRow.cost = { status: costStatus, amount_usd: costUsd, as_of: occurredAt, scope: 'run' }
      } else {
        const prev =
          baseRow.cost.status !== 'unknown' && baseRow.cost.amount_usd !== null ? baseRow.cost.amount_usd : 0
        baseRow.cost = {
          status: costStatus,
          amount_usd: Number((prev + costUsd).toFixed(6)),
          as_of: occurredAt,
          scope: 'run',
        }
      }
      break
    }
    case 'ppm.assessment.updated': {
      if (typeof envelopePayload.project_id === 'string') {
        baseRow.project = {
          project_id: envelopePayload.project_id,
          assessment_id:
            typeof envelopePayload.assessment_id === 'string' ? envelopePayload.assessment_id : null,
          verification:
            (envelopePayload.verification as NonNullable<HudRow['project']>['verification']) || 'unknown',
          validity: (envelopePayload.validity as NonNullable<HudRow['project']>['validity']) || 'unknown',
          acceptance:
            (envelopePayload.acceptance as NonNullable<HudRow['project']>['acceptance']) || 'not_requested',
          assessment_as_of: occurredAt,
          verified_weight:
            typeof envelopePayload.verified_weight === 'number' ? envelopePayload.verified_weight : null,
          total_weight:
            typeof envelopePayload.total_weight === 'number' ? envelopePayload.total_weight : null,
        }
      }
      break
    }
    default:
      break
  }

  const nextRows = [...snapshot.rows]
  if (idx >= 0) {
    nextRows[idx] = baseRow
  } else {
    nextRows.push(baseRow)
  }

  return {
    ...snapshot,
    projection_revision: snapshot.projection_revision + 1,
    snapshot_cursor: eventId || snapshot.snapshot_cursor,
    as_of: occurredAt || snapshot.as_of,
    rows: nextRows,
  }
}

export function buildAttentionQueue(
  rows: HudRow[],
  currentAssessment: AssessmentDetail | null,
  sources: SourceScheduleStatus[],
): AttentionItem[] {
  const items: AttentionItem[] = []

  for (const row of rows) {
    if (row.remote_outcome === 'unknown' || row.run_state === 'unknown') {
      const pendingCancel =
        row.pending_command?.kind === 'cancel'
          ? ` Pending cancel (${row.pending_command.state}) awaiting reconciliation (ISS-022).`
          : ''
      items.push({
        id: `unknown-${row.task_id}-${row.run_id || 'none'}`,
        category: 'unknown_outcome',
        severity: 'critical',
        title: `Unknown remote outcome: ${row.task_id}${row.run_id ? ` (${row.run_id})` : ''}`,
        detail: `Run state=${row.run_state ?? 'unknown'}, remote_outcome=${row.remote_outcome}. No retry until reconciled.${pendingCancel}`,
        targetTab: 'inspector',
        runId: row.run_id || undefined,
      })
    }

    if (row.connectivity === 'stale' || row.connectivity === 'offline') {
      items.push({
        id: `conn-${row.task_id}-${row.run_id || 'none'}`,
        category: 'stale_evidence',
        severity: 'warning',
        title: `Worker connectivity ${row.connectivity}: ${row.task_id}`,
        detail: `Last observed ${formatTimestampLocal(row.last_observed_at)} via ${row.observation_source}.`,
        targetTab: 'inspector',
        runId: row.run_id || undefined,
      })
    }

    if (row.project && (row.project.validity === 'stale' || row.project.verification === 'stale')) {
      items.push({
        id: `proj-stale-${row.project.project_id}`,
        category: 'stale_evidence',
        severity: 'warning',
        title: `Stale project evidence: ${row.project.project_id}`,
        detail: `verification=${row.project.verification}, validity=${row.project.validity}.`,
        targetTab: 'ppm',
        projectId: row.project.project_id,
      })
    }
  }

  if (currentAssessment) {
    const criteria = currentAssessment.output?.criteria || []
    for (const c of criteria) {
      if (c.status === 'contradicted' && c.required) {
        items.push({
          id: `contradicted-${currentAssessment.project_id}-${c.criterion_id}`,
          category: 'contradicted_criterion',
          severity: 'critical',
          title: `Contradicted required criterion ${c.criterion_id} (${currentAssessment.project_id})`,
          detail: `Reported "${c.reported_status}", verified "${c.status}" (${c.reason_code}).`,
          targetTab: 'ppm',
          projectId: currentAssessment.project_id,
        })
      }
    }
  }

  for (const src of sources) {
    if (src.last_error || src.last_scan_status === 'failed') {
      items.push({
        id: `scan-err-${src.source_id}`,
        category: 'scan_error',
        severity: 'critical',
        title: `Scan error on source ${src.source_id}`,
        detail: src.last_error || `Last scan status: ${src.last_scan_status}`,
        targetTab: 'ppm',
      })
    }
  }

  return items
}
