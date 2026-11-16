// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import App from './App'
import {
  applyHudSseEvent,
  buildAttentionQueue,
  formatConnectivity,
  formatCost,
  formatProgress,
  formatRemoteOutcome,
  formatRunStatus,
  formatTaskStatus,
  formatWithDenominator,
} from './state'
import type { HudProjection } from './types'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  delete (window as unknown as Record<string, unknown>).__hudXssTriggered
})

describe('HUD state mapping and formatting', () => {
  it('keeps task, run, remote outcome, connectivity, and observation time independent', () => {
    expect(formatTaskStatus('active').label).toBe('active')
    expect(formatRunStatus('unknown', 2).label).toBe('Attempt 2 · unknown')
    expect(formatRemoteOutcome('unknown').label).toContain('Remote outcome unknown')
    const conn = formatConnectivity('stale', '2026-11-16T02:10:30Z', 'run-service')
    expect(conn.label).toContain('Stale · last observed')
    expect(conn.label).toContain('via run-service')
  })

  it('renders unknown cost as "Unknown" (never "$0") and partial cost as lower bound', () => {
    expect(formatCost({ status: 'unknown', amount_usd: null, as_of: null, scope: 'run' })).toBe(
      'Unknown',
    )
    expect(formatCost({ status: 'unknown', amount_usd: 0, as_of: null, scope: 'run' })).toBe(
      'Unknown',
    )
    expect(formatCost({ status: 'partial', amount_usd: 0.42, as_of: null, scope: 'run' })).toBe(
      '≥ $0.42 (some calls unknown)',
    )
    expect(formatCost({ status: 'actual', amount_usd: 1.25, as_of: null, scope: 'run' })).toBe(
      '$1.25 (actual)',
    )
  })

  it('never renders a percentage without a positive denominator', () => {
    expect(formatWithDenominator(0, 0, 'criteria')).toBe('unknown')
    expect(formatWithDenominator(1, null, 'criteria')).toBe('unknown')
    expect(formatWithDenominator(2, 4, 'criteria')).toBe('2 of 4 criteria (50%)')

    const stageOnly = formatProgress({
      kind: 'stage_only',
      completed_units: null,
      total_units: null,
      unit: null,
      stage: 'verifying',
    })
    expect(stageOnly).toBe('Stage: verifying')
    expect(stageOnly).not.toContain('%')

    expect(
      formatProgress({
        kind: 'units',
        completed_units: 3,
        total_units: 10,
        unit: 'files',
        stage: 'scan',
      }),
    ).toBe('3 / 10 files (30%) · scan')
  })

  it('drops rows immediately on row.removed and applies SSE deltas', () => {
    const snap: HudProjection = {
      contract_version: '1.1',
      projection_revision: 1,
      snapshot_cursor: 'ehc2_init',
      as_of: '2026-11-16T10:00:00Z',
      viewer_scope_ref: 'principal:owner/tenant:t1/all-projects',
      rows: [
        {
          task_id: 'task-1',
          task_state: 'active',
          run_id: 'run-1',
          attempt: 1,
          run_state: 'running',
          remote_outcome: 'known',
          connectivity: 'healthy',
          last_observed_at: '2026-11-16T10:00:00Z',
          observation_source: 'mock',
          progress: { kind: 'none', completed_units: null, total_units: null, unit: null, stage: null },
          project: {
            project_id: 'proj-alpha',
            assessment_id: 'a-1',
            verification: 'partially_verified',
            validity: 'current',
            acceptance: 'not_requested',
            assessment_as_of: '2026-11-16T09:50:00Z',
            verified_weight: 1,
            total_weight: 2,
          },
          cost: { status: 'unknown', amount_usd: null, as_of: null, scope: 'run' },
          pending_command: null,
        },
      ],
    }

    const progressed = applyHudSseEvent(
      snap,
      'run.progress',
      {
        stream_id: 'run:run-1',
        occurred_at: '2026-11-16T10:01:00Z',
        producer_id: 'mock',
        payload: { completed_units: 2, total_units: 5, unit: 'steps', stage: 'apply' },
      },
      'ehc2_step2',
    )
    expect(progressed.rows[0].progress.kind).toBe('units')
    expect(progressed.snapshot_cursor).toBe('ehc2_step2')

    const removed = applyHudSseEvent(
      progressed,
      'row.removed',
      { task_id: 'task-1', run_id: 'run-1', project_id: 'proj-alpha', reason: 'permission_revoked' },
      'ehc2_revoked',
    )
    expect(removed.rows).toHaveLength(0)
    expect(removed.snapshot_cursor).toBe('ehc2_revoked')
  })

  it('builds attention queue for unknown outcome, contradicted criteria, scan errors, and stale evidence', () => {
    const items = buildAttentionQueue(
      [
        {
          task_id: 'task-u',
          task_state: 'active',
          run_id: 'run-u',
          attempt: 1,
          run_state: 'unknown',
          remote_outcome: 'unknown',
          connectivity: 'stale',
          last_observed_at: '2026-11-16T10:00:00Z',
          observation_source: 'mock',
          progress: { kind: 'none', completed_units: null, total_units: null, unit: null, stage: null },
          project: null,
          cost: { status: 'unknown', amount_usd: null, as_of: null, scope: 'run' },
          pending_command: { command_id: 'cmd-1', kind: 'cancel', state: 'accepted' },
        },
      ],
      {
        id: 'asmt-1',
        tenant_id: 't1',
        project_id: 'proj-1',
        basis_seq: 1,
        snapshot_seq: 1,
        baseline_revision: 1,
        assessed_revision: 'rev-1',
        trigger: 'snapshot',
        analysis_status: 'complete',
        created_at: '2026-11-16T10:00:00Z',
        is_current: true,
        output: {
          criteria: [
            {
              criterion_id: 'AC-001',
              deliverable_id: 'DEL-1',
              status: 'contradicted',
              weight: 2,
              required: true,
              reason_code: 'evidence_refuted',
              reported_status: 'claimed_done',
              evidence_revision: 'rev-1',
              covered_test_ids: ['test_1'],
              explanation: 'Failed',
            },
          ],
        },
        findings: [],
        explanation: {},
      },
      [{ source_id: 'src-1', last_error: 'git_version_unsupported', last_scan_status: 'failed' }],
    )
    expect(items.map((i) => i.category)).toEqual([
      'unknown_outcome',
      'stale_evidence',
      'contradicted_criterion',
      'scan_error',
    ])
  })
})

describe('App security and XSS-as-text', () => {
  it('keeps token in memory only, renders <script> notes as inert text, and has no approve buttons', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input instanceof Request ? input.url : String(input)
      if (url.startsWith('/v1/hud/snapshot')) {
        return new Response(
          JSON.stringify({
            contract_version: '1.1',
            projection_revision: 1,
            snapshot_cursor: 'ehc2_abc',
            as_of: '2026-11-16T10:00:00Z',
            viewer_scope_ref: 'principal:owner/tenant:t1/all-projects',
            rows: [],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.startsWith('/v1/hud/events')) {
        return new Response('', { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.startsWith('/v1/agents')) {
        return new Response(
          JSON.stringify({
            schema_version: '1.0',
            tenant_id: 't1',
            generated_at: '2026-11-16T10:00:00Z',
            window: { mode: 'all_recorded', hours: null },
            unattributed_runs_count: 0,
            agents: [],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.startsWith('/v1/ppm/sources')) {
        return new Response(JSON.stringify([]), { status: 200 })
      }
      if (url.includes('/assessments')) {
        if (url.endsWith('/assessments')) {
          return new Response(
            JSON.stringify([
              {
                id: 'asmt-1',
                basis_seq: 1,
                snapshot_seq: 1,
                baseline_revision: 1,
                assessed_revision: 'rev-1',
                trigger: 'snapshot',
                analysis_status: 'complete',
                verification: 'contradicted',
                acceptance_readiness: 'not_ready',
                created_at: '2026-11-16T10:00:00Z',
                is_current: true,
              },
            ]),
            { status: 200 },
          )
        }
        return new Response(
          JSON.stringify({
            id: 'asmt-1',
            tenant_id: 't1',
            project_id: 'proj-d0-demo',
            basis_seq: 1,
            snapshot_seq: 1,
            baseline_revision: 1,
            assessed_revision: 'rev-1',
            trigger: 'snapshot',
            analysis_status: 'complete',
            created_at: '2026-11-16T10:00:00Z',
            last_successful_scan_at: '2026-11-16T09:59:00Z',
            is_current: true,
            output: {
              verification: 'contradicted',
              acceptance_readiness: 'not_ready',
              criteria: [],
              progress: { basis: 'none', total_weight: null, verified_weight: null },
            },
            findings: [],
            notes: [
              {
                id: 'note-xss',
                artifact_path: '.ppm/notes/xss.md',
                kind: 'observation',
                body: '<script>window.__hudXssTriggered = true;</script>',
              },
            ],
            explanation: {},
          }),
          { status: 200 },
        )
      }
      return new Response('{}', { status: 404 })
    })

    render(<App />)
    const tokenInput = screen.getByLabelText('Operator Bearer Token')
    fireEvent.change(tokenInput, { target: { value: 'secret-token-mem' } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))

    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)

    fireEvent.click(screen.getByRole('tab', { name: 'Project Assessment' }))
    await waitFor(() => {
      expect(screen.getByText('<script>window.__hudXssTriggered = true;</script>')).toBeTruthy()
    })
    expect((window as unknown as Record<string, unknown>).__hudXssTriggered).toBeUndefined()
    expect(screen.queryByRole('button', { name: /approve|accept/i })).toBeNull()
  })
})
