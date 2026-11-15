import { useState, type FormEvent } from 'react'

export interface AssessmentListItem {
  id: string
  basis_seq: number
  snapshot_seq: number
  baseline_revision: number | null
  assessed_revision: string | null
  trigger: string
  analysis_status: string
  verification: string | null
  acceptance_readiness: string | null
  created_at: string
  is_current: boolean
}

export interface EvidenceItem {
  evidence_id: string
  trust_class: string | null
  producer: string
  revision: string | null
  outcome: string | null
  observed_at: string | null
  test_id: string | null
  covered_test_ids: string[]
  artifact_path: string | null
  simulated: boolean
  reason?: string
}

export interface ReportedClaimItem {
  claim_id: string
  source_file: string | null
  reported_status: string | null
  evidence_path: string | null
  summary: string | null
  recorded_at: string | null
}

export interface CriterionExplanation {
  reported: ReportedClaimItem[]
  verified: {
    status: string | null
    reason_code: string | null
    baseline_revision: number | null
    assessed_revision: string | null
    evidence_revision: string | null
    evidence_trust: string | null
    covered_test_ids: string[]
    supporting_evidence: EvidenceItem[]
    refuting_evidence: EvidenceItem[]
    excluded_evidence?: EvidenceItem[]
    explanation: string | null
  }
}

export interface CriterionOutput {
  criterion_id: string
  deliverable_id: string
  status: string
  weight: number
  required: boolean
  reason_code: string
  reported_status: string
  evidence_revision: string | null
  covered_test_ids: string[]
  explanation: string
}

export interface NoteItem {
  id: string
  artifact_path: string
  kind: string
  body: string
}

export interface AssessmentDetail {
  id: string
  tenant_id: string
  project_id: string
  basis_seq: number
  snapshot_seq: number
  baseline_revision: number | null
  assessed_revision: string | null
  trigger: string
  analysis_status: string
  created_at: string
  last_successful_scan_at?: string | null
  is_current: boolean
  output: {
    verification?: string
    acceptance_readiness?: string
    assessed_revision?: string | null
    baseline_revision?: number | null
    criteria?: CriterionOutput[]
    progress?: {
      basis?: string
      total_weight?: number | null
      verified_weight?: number | null
      criteria_total?: number | null
      criteria_with_current_evidence?: number | null
    }
  }
  findings: Array<Record<string, unknown>>
  notes?: NoteItem[]
  explanation: Record<string, CriterionExplanation>
}

export interface DiffChange {
  criterion_id: string
  from_status: string | null
  to_status: string | null
  changed: boolean
  from_reason_code: string | null
  to_reason_code: string | null
  from_evidence_revision: string | null
  to_evidence_revision: string | null
  reasons: string[]
}

export interface AssessmentDiff {
  project_id: string
  from_assessment_id: string
  to_assessment_id: string
  from_basis_seq: number
  to_basis_seq: number
  from_assessed_revision: string | null
  to_assessed_revision: string | null
  changes: DiffChange[]
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

function statusBadgeClass(status: string | null | undefined): string {
  if (status === 'verified' || status === 'ready_for_review') return 'badge badge-verified'
  if (status === 'contradicted' || status === 'not_ready') return 'badge badge-contradicted'
  if (status === 'unverified' || status === 'stale') return 'badge badge-unverified'
  return 'badge badge-neutral'
}

function EvidenceBox({ item, excluded }: { item: EvidenceItem; excluded?: boolean }) {
  return (
    <div className={excluded ? 'd0-item-box excluded' : 'd0-item-box'}>
      <div>
        <span className="d0-mono">{item.evidence_id}</span>{' '}
        <span className={item.outcome === 'passed' ? 'badge badge-verified' : 'badge badge-contradicted'}>
          {item.outcome ?? 'unknown'}
        </span>{' '}
        {item.simulated && <span className="badge badge-simulated">simulated verifier</span>}{' '}
        {excluded && item.reason && <span className="badge badge-unverified">{item.reason}</span>}
      </div>
      <div className="d0-mono" style={{ marginTop: 4 }}>
        trust_class: {item.trust_class ?? 'unknown'} | producer: {item.producer} | rev: {item.revision ?? 'none'}
      </div>
      <div className="d0-mono">
        test_id: {item.test_id ?? 'none'}
        {item.artifact_path ? ` | artifact: ${item.artifact_path}` : ''}
      </div>
    </div>
  )
}

export default function D0App() {
  const [projectId, setProjectId] = useState<string>('proj-d0-demo')
  const [token, setToken] = useState<string>('')
  const [loading, setLoading] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)

  const [assessments, setAssessments] = useState<AssessmentListItem[]>([])
  const [selectedId, setSelectedId] = useState<string>('')
  const [againstId, setAgainstId] = useState<string>('')
  const [detail, setDetail] = useState<AssessmentDetail | null>(null)
  const [diff, setDiff] = useState<AssessmentDiff | null>(null)

  async function apiGet<T>(path: string, bearer: string): Promise<T> {
    const res = await fetch(path, {
      headers: {
        Authorization: `Bearer ${bearer}`,
      },
    })
    if (!res.ok) {
      throw new Error(`HTTP ${res.status} on ${path}`)
    }
    return (await res.json()) as T
  }

  async function loadProject(targetProject: string, bearer: string) {
    if (!bearer.trim()) {
      setError('Enter your bearer token to query the API.')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const list = await apiGet<AssessmentListItem[]>(
        `/v1/ppm/projects/${encodeURIComponent(targetProject.trim())}/assessments`,
        bearer,
      )
      setAssessments(list)
      if (list.length === 0) {
        setDetail(null)
        setDiff(null)
        setSelectedId('')
        setAgainstId('')
        setError(`No assessments found for project '${targetProject.trim()}'.`)
        return
      }
      const currentItem = list.find((a) => a.is_current) ?? list[0]
      const prevItem = list.find((a) => a.id !== currentItem.id) ?? null
      setSelectedId(currentItem.id)
      setAgainstId(prevItem ? prevItem.id : '')

      const det = await apiGet<AssessmentDetail>(
        `/v1/ppm/assessments/${encodeURIComponent(currentItem.id)}`,
        bearer,
      )
      setDetail(det)

      if (prevItem) {
        const df = await apiGet<AssessmentDiff>(
          `/v1/ppm/assessments/${encodeURIComponent(currentItem.id)}/diff?against=${encodeURIComponent(prevItem.id)}`,
          bearer,
        )
        setDiff(df)
      } else {
        setDiff(null)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load project assessments')
    } finally {
      setLoading(false)
    }
  }

  async function loadSelection(nextSelectedId: string, nextAgainstId: string) {
    if (!token.trim() || !nextSelectedId) return
    setLoading(true)
    setError(null)
    try {
      const det = await apiGet<AssessmentDetail>(
        `/v1/ppm/assessments/${encodeURIComponent(nextSelectedId)}`,
        token,
      )
      setDetail(det)
      if (nextAgainstId && nextAgainstId !== nextSelectedId) {
        const df = await apiGet<AssessmentDiff>(
          `/v1/ppm/assessments/${encodeURIComponent(nextSelectedId)}/diff?against=${encodeURIComponent(nextAgainstId)}`,
          token,
        )
        setDiff(df)
      } else {
        setDiff(null)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load assessment')
    } finally {
      setLoading(false)
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    void loadProject(projectId, token)
  }

  const criteria = detail?.output.criteria ?? []
  const nonWaivedRequired = criteria.filter((c) => c.required && c.status !== 'waived')
  const verifiedRequired = nonWaivedRequired.filter((c) => c.status === 'verified')
  const requiredProgressText = formatWithDenominator(
    nonWaivedRequired.length > 0 ? verifiedRequired.length : null,
    nonWaivedRequired.length > 0 ? nonWaivedRequired.length : null,
    'required criteria verified',
  )

  const prog = detail?.output.progress
  const weightProgressText = formatWithDenominator(
    prog?.verified_weight ?? null,
    prog?.total_weight ?? null,
    'weight verified',
  )

  return (
    <div className="d0-container">
      <div className="d0-topbar">
        <div>
          <h1>EAP D0 Thin Slice — Reported vs Verified</h1>
          <p className="d0-subtitle">
            Read-only owner inspection view (disposable D0 prototype; no approval actions).
          </p>
        </div>
        <form className="d0-auth-form" onSubmit={handleSubmit}>
          <label className="d0-field">
            Project ID
            <input
              type="text"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              placeholder="proj-d0-demo"
            />
          </label>
          <label className="d0-field">
            Bearer Token (memory only)
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Paste owner bearer token"
              autoComplete="off"
            />
          </label>
          <button className="d0-btn" type="submit" disabled={loading}>
            {loading ? 'Loading...' : 'Load'}
          </button>
        </form>
      </div>

      {error && (
        <div className="d0-error" role="alert">
          {error}
        </div>
      )}

      {detail && (
        <>
          <section className="d0-card" aria-label="Assessment Header">
            <div className="d0-section-title">
              <span>Assessment Summary</span>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <label className="d0-field">
                  Assessment
                  <select
                    value={selectedId}
                    onChange={(e) => {
                      const nextId = e.target.value
                      setSelectedId(nextId)
                      void loadSelection(nextId, againstId)
                    }}
                  >
                    {assessments.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.id} (basis_seq {a.basis_seq}{a.is_current ? ', current' : ''})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="d0-field">
                  Diff Against
                  <select
                    value={againstId}
                    onChange={(e) => {
                      const nextAgainst = e.target.value
                      setAgainstId(nextAgainst)
                      void loadSelection(selectedId, nextAgainst)
                    }}
                  >
                    <option value="">None</option>
                    {assessments
                      .filter((a) => a.id !== selectedId)
                      .map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.id} (basis_seq {a.basis_seq})
                        </option>
                      ))}
                  </select>
                </label>
              </div>
            </div>

            <div className="d0-header-grid">
              <div>
                <div className="d0-kv-label">Project</div>
                <div className="d0-kv-value">{detail.project_id}</div>
              </div>
              <div>
                <div className="d0-kv-label">Assessment / basis_seq</div>
                <div className="d0-kv-value d0-mono">
                  {detail.id} / #{detail.basis_seq}
                </div>
              </div>
              <div>
                <div className="d0-kv-label">Pointer State</div>
                <div className="d0-kv-value">
                  <span className={detail.is_current ? 'badge badge-verified' : 'badge badge-neutral'}>
                    {detail.is_current ? 'is_current: true' : 'is_current: false'}
                  </span>
                </div>
              </div>
              <div>
                <div className="d0-kv-label">Verification / Readiness</div>
                <div className="d0-kv-value" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <span className={statusBadgeClass(detail.output.verification)}>
                    {detail.output.verification ?? 'unknown'}
                  </span>
                  <span className={statusBadgeClass(detail.output.acceptance_readiness)}>
                    {detail.output.acceptance_readiness ?? 'unknown'}
                  </span>
                </div>
              </div>
              <div>
                <div className="d0-kv-label">Last Successful Scan</div>
                <div className="d0-kv-value d0-mono">
                  {detail.last_successful_scan_at || 'unknown'}
                </div>
              </div>
              <div>
                <div className="d0-kv-label">Assessment Created At</div>
                <div className="d0-kv-value d0-mono">{detail.created_at}</div>
              </div>
              <div>
                <div className="d0-kv-label">Required Criteria Progress</div>
                <div className="d0-kv-value">{requiredProgressText}</div>
              </div>
              <div>
                <div className="d0-kv-label">Weighted Progress</div>
                <div className="d0-kv-value">{weightProgressText}</div>
              </div>
            </div>
          </section>

          <section aria-label="Criteria Reported vs Verified">
            <h2 className="d0-section-title">Criteria: Reported vs Verified</h2>
            {criteria.map((crit) => {
              const exp = detail.explanation[crit.criterion_id]
              const reported = exp?.reported ?? []
              const verified = exp?.verified
              const excluded = verified?.excluded_evidence ?? []

              return (
                <article key={crit.criterion_id} className="d0-criterion-card">
                  <div className="d0-criterion-head">
                    <div>
                      <strong>{crit.criterion_id}</strong>{' '}
                      <span className="d0-mono">({crit.deliverable_id})</span>{' '}
                      {crit.required && <span className="badge badge-neutral">required</span>}{' '}
                      <span className="d0-mono">weight={crit.weight}</span>
                    </div>
                    <span className={statusBadgeClass(crit.status)}>{crit.status}</span>
                  </div>

                  <div className="d0-criterion-cols">
                    <div className="d0-col">
                      <h3>Reported (Claims)</h3>
                      {reported.length === 0 ? (
                        <div className="d0-item-box">No reported claims.</div>
                      ) : (
                        reported.map((cl) => (
                          <div key={cl.claim_id} className="d0-item-box">
                            <div>
                              <span className="d0-mono">{cl.claim_id}</span>{' '}
                              <span className="badge badge-neutral">
                                {cl.reported_status ?? 'none'}
                              </span>
                            </div>
                            <div className="d0-mono" style={{ marginTop: 4 }}>
                              source: {cl.source_file ?? 'unknown'}
                            </div>
                            <div className="d0-mono">
                              recorded_at: {cl.recorded_at ?? 'unknown'}
                            </div>
                            {cl.evidence_path && (
                              <div className="d0-mono">evidence_path: {cl.evidence_path}</div>
                            )}
                            {cl.summary && <div style={{ marginTop: 4 }}>{cl.summary}</div>}
                          </div>
                        ))
                      )}
                    </div>

                    <div className="d0-col">
                      <h3>Verified (Reducer & Evidence)</h3>
                      <div className="d0-item-box">
                        <div>
                          status: <strong>{verified?.status ?? crit.status}</strong> | reason:{' '}
                          <span className="d0-mono">
                            {verified?.reason_code ?? crit.reason_code}
                          </span>
                        </div>
                        <div className="d0-mono" style={{ marginTop: 4 }}>
                          assessed_rev: {verified?.assessed_revision ?? detail.assessed_revision ?? 'none'} |{' '}
                          baseline_rev: {verified?.baseline_revision ?? detail.baseline_revision ?? 'none'} |{' '}
                          evidence_rev: {verified?.evidence_revision ?? 'none'}
                        </div>
                        <div className="d0-mono">
                          test_ids:{' '}
                          {(verified?.covered_test_ids ?? crit.covered_test_ids).length > 0
                            ? (verified?.covered_test_ids ?? crit.covered_test_ids).join(', ')
                            : 'none'}
                        </div>
                        <div style={{ marginTop: 4 }}>
                          {verified?.explanation ?? crit.explanation}
                        </div>
                      </div>

                      {verified && verified.supporting_evidence.length > 0 && (
                        <>
                          <div className="d0-kv-label" style={{ margin: '8px 0 4px' }}>
                            Supporting Evidence
                          </div>
                          {verified.supporting_evidence.map((ev) => (
                            <EvidenceBox key={ev.evidence_id} item={ev} />
                          ))}
                        </>
                      )}

                      {verified && verified.refuting_evidence.length > 0 && (
                        <>
                          <div className="d0-kv-label" style={{ margin: '8px 0 4px' }}>
                            Refuting Evidence
                          </div>
                          {verified.refuting_evidence.map((ev) => (
                            <EvidenceBox key={ev.evidence_id} item={ev} />
                          ))}
                        </>
                      )}

                      {excluded.length > 0 && (
                        <>
                          <div className="d0-kv-label" style={{ margin: '8px 0 4px' }}>
                            Excluded Evidence
                          </div>
                          {excluded.map((ev) => (
                            <EvidenceBox key={ev.evidence_id} item={ev} excluded />
                          ))}
                        </>
                      )}
                    </div>
                  </div>
                </article>
              )
            })}
          </section>

          {diff && (
            <section className="d0-card" aria-label="Assessment Diff">
              <h2 className="d0-section-title">
                <span>
                  Diff: {diff.from_assessment_id} (basis #{diff.from_basis_seq}, rev{' '}
                  {diff.from_assessed_revision ?? 'none'}) &rarr; {diff.to_assessment_id} (basis #
                  {diff.to_basis_seq}, rev {diff.to_assessed_revision ?? 'none'})
                </span>
              </h2>
              <table className="d0-diff-table">
                <thead>
                  <tr>
                    <th>Criterion</th>
                    <th>Transition</th>
                    <th>Reason Codes</th>
                    <th>Evidence Revision</th>
                    <th>Reasons</th>
                  </tr>
                </thead>
                <tbody>
                  {diff.changes.map((ch) => (
                    <tr key={ch.criterion_id}>
                      <td>
                        <strong>{ch.criterion_id}</strong>{' '}
                        {ch.changed && <span className="badge badge-unverified">changed</span>}
                      </td>
                      <td>
                        <span className={statusBadgeClass(ch.from_status)}>
                          {ch.from_status ?? 'none'}
                        </span>{' '}
                        &rarr;{' '}
                        <span className={statusBadgeClass(ch.to_status)}>
                          {ch.to_status ?? 'none'}
                        </span>
                      </td>
                      <td className="d0-mono">
                        {ch.from_reason_code ?? 'none'} &rarr; {ch.to_reason_code ?? 'none'}
                      </td>
                      <td className="d0-mono">
                        {ch.from_evidence_revision ?? 'none'} &rarr;{' '}
                        {ch.to_evidence_revision ?? 'none'}
                      </td>
                      <td>
                        {ch.reasons.map((r, i) => (
                          <div key={i}>{r}</div>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {detail.notes && detail.notes.length > 0 && (
            <section className="d0-card" aria-label="Repository Notes">
              <h2 className="d0-section-title">
                Repository Notes (context-only, untrusted text)
              </h2>
              {detail.notes.map((note) => (
                <div key={note.id} className="d0-item-box">
                  <div className="d0-mono">
                    {note.artifact_path} ({note.kind})
                  </div>
                  <pre className="d0-note-body">{note.body}</pre>
                </div>
              ))}
            </section>
          )}
        </>
      )}
    </div>
  )
}
