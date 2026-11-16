import { useState } from 'react'
import { formatTimestampLocal, formatWithDenominator } from '../state'
import type {
  AssessmentDetail,
  AssessmentDiff,
  AssessmentListItem,
  EvidenceItem,
} from '../types'
import { StatusBadge } from './StatusBadge'

interface ProjectAssessmentViewProps {
  projectId: string
  knownProjectIds: string[]
  assessments: AssessmentListItem[]
  detail: AssessmentDetail | null
  diff: AssessmentDiff | null
  loading: boolean
  error: string | null
  token: string
  onChangeProject: (projectId: string) => void
  onSelectAssessment: (assessmentId: string) => void
  onRefresh: () => void
}

function verificationBadge(status: string | null | undefined) {
  switch (status) {
    case 'verified':
      return { label: 'verified', symbol: '✓', tone: 'ok' as const }
    case 'partially_verified':
      return { label: 'partially_verified', symbol: '◐', tone: 'warn' as const }
    case 'contradicted':
      return { label: 'contradicted', symbol: '✗', tone: 'bad' as const }
    case 'stale':
      return { label: 'stale', symbol: '⚠', tone: 'warn' as const }
    case 'unverified':
      return { label: 'unverified', symbol: '○', tone: 'neutral' as const }
    default:
      return { label: status || 'unknown', symbol: '?', tone: 'neutral' as const }
  }
}

export function ProjectAssessmentView({
  projectId,
  knownProjectIds,
  assessments,
  detail,
  diff,
  loading,
  error,
  token,
  onChangeProject,
  onSelectAssessment,
  onRefresh,
}: ProjectAssessmentViewProps) {
  const [projectDraft, setProjectDraft] = useState(projectId)
  const [evidencePreview, setEvidencePreview] = useState<{ id: string; body: string } | null>(null)
  const [evidenceErr, setEvidenceErr] = useState<string | null>(null)

  async function inspectEvidence(ev: EvidenceItem) {
    setEvidenceErr(null)
    try {
      const resp = await fetch(`/v1/evidence/${encodeURIComponent(ev.evidence_id)}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      const text = await resp.text()
      if (!resp.ok) {
        setEvidenceErr(`Evidence ${ev.evidence_id}: HTTP ${resp.status} ${text}`)
        setEvidencePreview(null)
        return
      }
      setEvidencePreview({ id: ev.evidence_id, body: text })
    } catch (err) {
      setEvidenceErr(err instanceof Error ? err.message : 'Failed to fetch evidence')
    }
  }

  const criteria = detail?.output?.criteria || []
  const progress = detail?.output?.progress
  const weightProgress = formatWithDenominator(
    progress?.verified_weight,
    progress?.total_weight,
    'weight verified',
  )
  const criteriaProgress = formatWithDenominator(
    progress?.criteria_with_current_evidence,
    progress?.criteria_total,
    'criteria with current evidence',
  )

  return (
    <section aria-label="Project Assessment" className="panel">
      <div className="panel-header">
        <div>
          <h2>Project Assessment (Reported vs Verified)</h2>
          <p className="muted">
            Read-only PPM assessment view replacing the D0 page. Acceptance/approval actions are
            deferred to P2b/P3b.
          </p>
        </div>
        <div className="btn-group">
          {knownProjectIds.length > 0 && (
            <select
              aria-label="Known projects"
              value={projectId}
              onChange={(e) => {
                setProjectDraft(e.target.value)
                onChangeProject(e.target.value)
              }}
            >
              {knownProjectIds.map((pid) => (
                <option key={pid} value={pid}>
                  {pid}
                </option>
              ))}
            </select>
          )}
          <input
            aria-label="Project ID"
            value={projectDraft}
            onChange={(e) => setProjectDraft(e.target.value)}
            placeholder="proj-d0-demo"
          />
          <button
            type="button"
            className="btn-sm"
            onClick={() => onChangeProject(projectDraft.trim())}
            disabled={loading || !projectDraft.trim()}
          >
            Load Project
          </button>
          <button type="button" className="btn-sm" onClick={onRefresh} disabled={loading}>
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div role="alert" className="banner bad">
          {error}
        </div>
      )}

      {evidenceErr && (
        <div role="alert" className="banner bad">
          {evidenceErr}
        </div>
      )}

      {evidencePreview && (
        <div className="card">
          <strong>Evidence {evidencePreview.id} (via /v1/evidence)</strong>
          <pre className="pre-text">{evidencePreview.body}</pre>
        </div>
      )}

      {assessments.length > 0 && (
        <div className="btn-group wrap">
          <span className="muted small">Assessments:</span>
          {assessments.map((a) => (
            <button
              key={a.id}
              type="button"
              className={`btn-sm ${detail?.id === a.id ? 'active-btn' : ''}`}
              onClick={() => onSelectAssessment(a.id)}
            >
              #{a.basis_seq} {a.is_current ? '(current)' : ''} ·{' '}
              {a.assessed_revision ? a.assessed_revision.slice(0, 7) : 'no-rev'}
            </button>
          ))}
        </div>
      )}

      {!detail && !loading && <p className="muted">No assessment loaded for {projectId}.</p>}

      {detail && (
        <div className="stack">
          <div className="summary-grid">
            <div className="stat-box">
              <div className="stat-label">Verification</div>
              <StatusBadge status={verificationBadge(detail.output?.verification)} />
            </div>
            <div className="stat-box">
              <div className="stat-label">Acceptance Readiness</div>
              <div className="stat-value small">
                {detail.output?.acceptance_readiness || 'unknown'}
              </div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Analysis Status</div>
              <div className="stat-value small">{detail.analysis_status}</div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Assessed / Baseline Revision</div>
              <div className="mono small">
                rev {detail.assessed_revision || 'unknown'} · baseline{' '}
                {detail.baseline_revision ?? 'none'}
              </div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Last Successful Scan</div>
              <div className="stat-value small">
                {formatTimestampLocal(detail.last_successful_scan_at)}
              </div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Progress</div>
              <div className="stat-value small">
                {weightProgress} · {criteriaProgress}
              </div>
            </div>
          </div>

          <h3>Criteria: Reported vs Verified</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Criterion</th>
                <th>Reported (Claims)</th>
                <th>Verified (Evidence &amp; Reducer)</th>
                <th>Excluded Evidence</th>
              </tr>
            </thead>
            <tbody>
              {criteria.map((c) => {
                const expl = detail.explanation?.[c.criterion_id]
                const reported = expl?.reported || []
                const verified = expl?.verified
                const allActiveEvidence = [
                  ...(verified?.supporting_evidence || []),
                  ...(verified?.refuting_evidence || []),
                ]
                const excluded = verified?.excluded_evidence || []

                return (
                  <tr key={c.criterion_id}>
                    <td>
                      <div className="mono">
                        <strong>{c.criterion_id}</strong>
                      </div>
                      <div className="muted small">
                        {c.deliverable_id} · weight {c.weight} ·{' '}
                        {c.required ? 'required' : 'optional'}
                      </div>
                    </td>
                    <td>
                      {reported.length === 0 ? (
                        <span className="muted">No reported claims ({c.reported_status})</span>
                      ) : (
                        <ul className="plain-list">
                          {reported.map((rep) => (
                            <li key={rep.claim_id}>
                              <span className="badge tone-neutral">{rep.reported_status}</span>{' '}
                              <span className="mono small">{rep.source_file}</span>
                              {rep.summary && <div className="small">{rep.summary}</div>}
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                    <td>
                      <div className="stack-xs">
                        <StatusBadge status={verificationBadge(c.status)} />
                        <div className="small muted">
                          reason: <code>{c.reason_code}</code> · rev:{' '}
                          <code>{c.evidence_revision || 'none'}</code>
                        </div>
                        {c.covered_test_ids.length > 0 && (
                          <div className="small muted">
                            tests: <code>{c.covered_test_ids.join(', ')}</code>
                          </div>
                        )}
                        {allActiveEvidence.map((ev) => (
                          <div key={ev.evidence_id} className="evidence-chip">
                            <span className="mono small">{ev.evidence_id}</span> ·{' '}
                            <code>{ev.trust_class || 'unknown'}</code> · producer:{' '}
                            <span>{ev.producer}</span> · outcome: <strong>{ev.outcome}</strong>
                            {ev.simulated && (
                              <span className="badge tone-warn">simulated verifier</span>
                            )}{' '}
                            <button
                              type="button"
                              className="link-btn"
                              onClick={() => inspectEvidence(ev)}
                            >
                              Inspect API
                            </button>
                          </div>
                        ))}
                      </div>
                    </td>
                    <td>
                      {excluded.length === 0 ? (
                        <span className="muted">—</span>
                      ) : (
                        <ul className="plain-list">
                          {excluded.map((ex) => (
                            <li key={ex.evidence_id} className="small">
                              <span className="mono">{ex.evidence_id}</span> (
                              <code>{ex.trust_class || 'unknown'}</code>, rev{' '}
                              <code>{ex.revision || 'none'}</code>) —{' '}
                              <span className="badge tone-warn">{ex.reason || 'excluded'}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          {diff && (
            <div>
              <h3>
                Diff: Assessment #{diff.from_basis_seq} → #{diff.to_basis_seq}
              </h3>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Criterion</th>
                    <th>Status Change</th>
                    <th>Reason Code</th>
                    <th>Evidence Revision</th>
                    <th>Reasons</th>
                  </tr>
                </thead>
                <tbody>
                  {diff.changes.map((ch) => (
                    <tr key={ch.criterion_id}>
                      <td className="mono">{ch.criterion_id}</td>
                      <td>
                        {ch.from_status || 'none'} → <strong>{ch.to_status || 'none'}</strong>{' '}
                        {ch.changed ? <span className="badge tone-info">changed</span> : null}
                      </td>
                      <td className="mono small">
                        {ch.from_reason_code || '—'} → {ch.to_reason_code || '—'}
                      </td>
                      <td className="mono small">
                        {ch.from_evidence_revision || '—'} → {ch.to_evidence_revision || '—'}
                      </td>
                      <td className="small">{ch.reasons.join(', ') || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {detail.notes && detail.notes.length > 0 && (
            <div>
              <h3>Notes (Context Only — Inert Text)</h3>
              {detail.notes.map((n) => (
                <div key={n.id} className="card">
                  <div className="muted mono small">
                    {n.artifact_path} ({n.kind})
                  </div>
                  <pre className="pre-text">{n.body}</pre>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
