export type TaskState =
  | 'draft'
  | 'ready'
  | 'active'
  | 'blocked'
  | 'review_required'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type RunState =
  | 'queued'
  | 'dispatching'
  | 'running'
  | 'waiting_approval'
  | 'cancelling'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'timed_out'
  | 'unknown'

export type RemoteOutcome = 'known' | 'unknown' | 'not_applicable'
export type ConnectivityState = 'healthy' | 'stale' | 'offline' | 'unknown'

export interface ProgressInfo {
  kind: 'units' | 'stage_only' | 'none'
  completed_units: number | null
  total_units: number | null
  unit: string | null
  stage: string | null
}

export interface ProjectSummary {
  project_id: string
  assessment_id: string | null
  verification: 'unknown' | 'claimed' | 'partially_verified' | 'verified' | 'contradicted' | 'stale'
  validity: 'current' | 'stale' | 'unknown' | 'conflicted'
  acceptance: 'not_requested' | 'pending' | 'accepted' | 'rejected' | 'superseded'
  assessment_as_of: string | null
  verified_weight: number | null
  total_weight: number | null
}

export interface CostInfo {
  status: 'actual' | 'estimated' | 'partial' | 'unknown'
  amount_usd: number | null
  as_of: string | null
  scope: 'run' | 'task_tree'
}

export interface PendingCommand {
  command_id: string
  kind: 'cancel' | 'reconcile' | 'retry_new_attempt'
  state: 'accepted' | 'acknowledged'
}

export interface HudRow {
  task_id: string
  task_state: TaskState
  run_id: string | null
  attempt: number | null
  run_state: RunState | null
  remote_outcome: RemoteOutcome
  connectivity: ConnectivityState
  last_observed_at: string | null
  observation_source: string
  progress: ProgressInfo
  project: ProjectSummary | null
  cost: CostInfo
  pending_command: PendingCommand | null
}

export interface HudProjection {
  contract_version: '1.1'
  projection_revision: number
  snapshot_cursor: string
  as_of: string
  viewer_scope_ref: string
  rows: HudRow[]
}

export type ConnectionState = 'disconnected' | 'connecting' | 'live' | 'reconnecting' | 'stale' | 'resyncing'

export interface RunInspectorData {
  run: {
    run_id: string
    task_id: string
    project_id: string | null
    attempt: number
    binding_id: string
    state: RunState
    remote_outcome: RemoteOutcome
    created_at: string | null
    updated_at: string | null
  }
  task: {
    task_id: string
    task_type: string
    state: TaskState
    agent_id: string
    agent_version: string
    project_id: string | null
    created_at: string | null
    updated_at: string | null
    deadline: string | null
  } | null
  attempts: Array<{
    run_id: string
    attempt: number
    binding_id: string
    state: RunState
    remote_outcome: RemoteOutcome
    created_at: string | null
    updated_at: string | null
  }>
  commands: Array<{
    command_id: string
    run_id: string | null
    command_type: string
    status: string
    payload: unknown
    created_at: string | null
    updated_at: string | null
  }>
  events: Array<{
    event_id: string
    stream_id: string
    ingest_offset: number
    producer_id: string
    event_type: string
    payload: unknown
    occurred_at: string | null
    ingested_at: string | null
  }>
  artifacts: Array<{
    artifact_id: string
    content_hash: string
    content_type: string
    size_bytes: number
    access_url: string
    signed_url: null
    created_at: string | null
  }>
  usage: Array<{
    provider_usage_id: string
    model_profile: string
    prompt_tokens: number
    completion_tokens: number
    cost_usd: number
    reported_at: string | null
  }>
  cost: CostInfo
}

export interface AgentBinding {
  binding_id: string
  release_version: string
  release_sha256: string
  mode: string
  execution_boundary: string
  endpoint_ref: string | null
  secret_ref: string | null
  egress_policy_ref: string | null
  telemetry_export: string | null
  policy_version: string | null
  model_profile_ref: string | null
  certified_capabilities: Record<string, boolean>
  capability_evidence_ref: string | null
  status: 'active' | 'revoked'
  created_at: string | null
  revoked_at: string | null
}

export interface AgentHealth {
  status: 'healthy' | 'degraded' | 'unknown' | 'no_data'
  total_runs: number
  unknown_outcome_count: number
  unattributed_run_count: number
  last_run_at: string | null
  last_observed_at: string | null
  cost: CostInfo
}

export interface AgentEntry {
  agent_id: string
  name: string
  version: string
  domain: string
  status: string
  runtime_kind: string
  task_types: string[]
  manifest_sha256: string
  owner: {
    business_owner_ref?: string
    technical_owner_ref?: string
    verification_status?: string
    verification_label?: string
  }
  registered_at: string | null
  active_binding_count: number
  revoked_binding_count: number
  bindings: AgentBinding[]
  health: AgentHealth
}

export interface AgentInventoryResponse {
  schema_version: string
  tenant_id: string
  generated_at: string
  window: { mode: string; hours: number | null }
  unattributed_runs_count: number
  agents: AgentEntry[]
}

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

export interface SourceScheduleStatus {
  source_id: string
  canonical_root?: string
  last_scan_status?: string | null
  last_error?: string | null
  last_successful_scan_at?: string | null
  next_run_at?: string | null
  interval_seconds?: number | null
}

export interface AttentionItem {
  id: string
  category: 'unknown_outcome' | 'contradicted_criterion' | 'scan_error' | 'stale_evidence'
  severity: 'critical' | 'warning'
  title: string
  detail: string
  targetTab: 'inspector' | 'ppm'
  runId?: string
  projectId?: string
}
