-- DRAFT DDL: Enterprise Agent Platform Core Schema (rev 1.5)
-- Milestone: P0-WP7 / Gates: P1a-WP1
-- DO NOT EXECUTE ON ANY DATABASE. This is an architectural draft specification
-- for schema, constraint, and idempotency key enforcement.

-- ============================================================================
-- 1. tasks: Logical business tasks
-- Enforces: C01 (Submit same key twice -> single logical run via task_intent_key)
-- ============================================================================
CREATE TABLE tasks (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    client_idempotency_key VARCHAR(128) NOT NULL,
    task_intent_key VARCHAR(64) NOT NULL,
    task_type VARCHAR(64) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    agent_version VARCHAR(32) NOT NULL,
    state VARCHAR(32) NOT NULL DEFAULT 'ready',
    deadline TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Enforces C01: Unique task intent key prevents duplicate task creation across attempts
    CONSTRAINT uq_tasks_tenant_task_intent_key UNIQUE (tenant_id, task_intent_key),
    -- Enforces client-level admission idempotency per tenant
    CONSTRAINT uq_tasks_tenant_client_key UNIQUE (tenant_id, client_idempotency_key),
    -- Enforces Amendment A1: Composite unique key for tenant-scoped foreign key references
    CONSTRAINT uq_tasks_tenant_id UNIQUE (tenant_id, id)
);

-- ============================================================================
-- 2. runs: Physical execution attempts of a task
-- Enforces: C05, C06, C21 (Lifecycle transitions, state tracking, lease fencing)
-- ============================================================================
CREATE TABLE runs (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    binding_id VARCHAR(64) NOT NULL,
    state VARCHAR(32) NOT NULL DEFAULT 'ready',
    remote_outcome VARCHAR(32),
    lease_fencing_token BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_runs_tenant_task FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT uq_runs_tenant_run_id UNIQUE (tenant_id, run_id),
    CONSTRAINT uq_runs_tenant_task_attempt UNIQUE (tenant_id, task_id, attempt)
);

-- ============================================================================
-- 3. run_attempt_history: Complete immutable history of run attempts
-- Enforces: C19 (audit trail across agent releases), C29 (complete attempt history)
-- ============================================================================
CREATE TABLE run_attempt_history (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    attempt INTEGER NOT NULL,
    from_state VARCHAR(32) NOT NULL,
    to_state VARCHAR(32) NOT NULL,
    reason TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_run_attempt_history_tenant_task FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id) ON DELETE RESTRICT
);

-- ============================================================================
-- 4. task_intents: Business action slots declared/persisted before tool calls
-- Enforces: C29b (Slots persisted prior to tool execution; agent-proposed slots
-- after restart recover persisted slot or reject with ACTION_SLOT_UNDECLARED)
-- ============================================================================
CREATE TABLE task_intents (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    action_slot VARCHAR(64) NOT NULL,
    tool_id VARCHAR(128) NOT NULL,
    workflow_step VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'declared',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_task_intents_tenant_task FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id) ON DELETE RESTRICT,
    -- Enforces C29b: One unique action slot per task, preventing duplicate slot allocation
    CONSTRAINT uq_task_intents_slot UNIQUE (tenant_id, task_id, action_slot)
);

-- ============================================================================
-- 5. dispatch_intents: Dispatch journal per run
-- Enforces: C05, C06, C21, C38 (Journal prepared->sent->acknowledged/ambiguous;
-- idempotency_key = sha256(binding_id|run_id|operation) per run)
-- ============================================================================
CREATE TABLE dispatch_intents (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    binding_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    operation VARCHAR(32) NOT NULL DEFAULT 'submit',
    idempotency_key VARCHAR(64) NOT NULL,
    state VARCHAR(32) NOT NULL DEFAULT 'prepared',
    execution_ref VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Enforces C21, C38: Unique dispatch intent per binding, run, and operation
    CONSTRAINT uq_dispatch_intents_op UNIQUE (tenant_id, binding_id, run_id, operation)
);

-- ============================================================================
-- 6. action_executions: Execution journal and side-effect idempotency
-- Enforces: C29 (operation_key = sha256(tenant_id|task_id|action_slot);
-- matching key + same payload_hash -> journaled result;
-- matching key + differing payload_hash -> 409 OPERATION_KEY_CONFLICT)
-- ============================================================================
CREATE TABLE action_executions (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    operation_key VARCHAR(64) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    tool_id VARCHAR(128) NOT NULL,
    resource_ref TEXT NOT NULL,
    state VARCHAR(32) NOT NULL DEFAULT 'running',
    external_ref VARCHAR(128),
    result_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Enforces C29: Exactly one execution record per tenant and operation key
    CONSTRAINT uq_action_executions_key UNIQUE (tenant_id, operation_key)
);

-- ============================================================================
-- 7. leases: Distributed execution leases with monotonically increasing fencing
-- Enforces: C15, C21 (Worker crash/stale worker fenced by token; gateway denies stale writes)
-- ============================================================================
CREATE TABLE leases (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    resource_type VARCHAR(64) NOT NULL,
    resource_id VARCHAR(128) NOT NULL,
    fencing_token BIGINT NOT NULL DEFAULT 1,
    holder_id VARCHAR(128) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Enforces C15: Mutual exclusion per resource with fencing token
    CONSTRAINT uq_leases_resource UNIQUE (tenant_id, resource_type, resource_id)
);

-- ============================================================================
-- 8. commands: Control plane commands (e.g. cancel, pause, resume)
-- Enforces: C06, C21 (Cancel accepted while run is unknown; reconciliation handles terminal stop)
-- ============================================================================
CREATE TABLE commands (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    command_type VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'accepted',
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_commands_tenant_task FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id) ON DELETE RESTRICT
);

-- ============================================================================
-- 9. outbox: Durable outbox for transactional event publishing
-- Enforces: ADR-020 (NO offset column: business transaction writes outbox WITHOUT
-- offset; single relay assigns ingest_offset post-commit in commit order)
-- ============================================================================
CREATE TABLE outbox (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    event_id VARCHAR(64) NOT NULL,
    stream_id VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    -- Note: deliberately NO offset column. Offsets are strictly assigned post-commit.
);

-- ============================================================================
-- 10. event_log: Authoritative ingested event log
-- Enforces: C02 (Deduplication on event_id), C33 (Strict commit-order offset assignment
-- by single relay under advisory lock, ensuring replay matches snapshot)
-- ============================================================================
CREATE TABLE event_log (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    stream_id VARCHAR(128) NOT NULL,
    ingest_offset BIGINT NOT NULL,
    event_id VARCHAR(64) NOT NULL,
    producer_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Enforces C33: Monotonic, strictly contiguous sequence per tenant stream
    CONSTRAINT uq_event_log_stream_offset UNIQUE (tenant_id, stream_id, ingest_offset),
    -- Enforces C02: Event ID idempotency per tenant
    CONSTRAINT uq_event_log_event_id UNIQUE (tenant_id, event_id)
);

-- ============================================================================
-- 11. artifacts: Metadata and content-addressed references for task artifacts
-- Enforces: C10 (Access gated via API; artifact references bound to tenant/task)
-- ============================================================================
CREATE TABLE artifacts (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    artifact_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    content_type VARCHAR(128) NOT NULL,
    size_bytes BIGINT NOT NULL,
    storage_uri TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_artifacts_tenant_task FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT uq_artifacts_tenant_artifact UNIQUE (tenant_id, artifact_id)
);

-- ============================================================================
-- 12. usage_ledger: Deduped accounting for model and compute usage
-- Enforces: C38 (Deduplication of cumulative provider usage by provider_usage_id)
-- ============================================================================
CREATE TABLE usage_ledger (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    provider_usage_id VARCHAR(128) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    model_profile VARCHAR(64) NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_estimate NUMERIC(12, 6) NOT NULL DEFAULT 0,
    reported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Enforces C38: Unique provider usage event per tenant
    CONSTRAINT uq_usage_ledger_provider_id UNIQUE (tenant_id, provider_usage_id)
);

-- ============================================================================
-- 13. sources: Physical repository or directory roots
-- Enforces: ADR-022, C42 (One physical directory = one source; matching fingerprint
-- or repo_identity under differing source -> 409 identity conflict)
-- ============================================================================
CREATE TABLE sources (
    source_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    canonical_root TEXT NOT NULL,
    repo_identity VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Enforces ADR-022: Physical path is unique per tenant
    CONSTRAINT uq_sources_tenant_canonical_root UNIQUE (tenant_id, canonical_root),
    -- Enforces C42: Repo identity is unique per tenant to prevent cloned repository aliasing
    CONSTRAINT uq_sources_tenant_repo_identity UNIQUE (tenant_id, repo_identity),
    -- Enforces Amendment A1: Composite unique key for tenant-scoped foreign key references
    CONSTRAINT uq_sources_tenant_source_id UNIQUE (tenant_id, source_id)
);

-- ============================================================================
-- 14. project_scopes: Mapping between project, source, and metadata root
-- Enforces: ADR-019, ADR-022, C32 (Monorepo support: unique (source_id, metadata_root);
-- exactly one project per scope; shared components not double-counted)
-- ============================================================================
CREATE TABLE project_scopes (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    project_id VARCHAR(64) NOT NULL,
    metadata_root TEXT NOT NULL,
    scan_scope TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_project_scopes_tenant_source FOREIGN KEY (tenant_id, source_id) REFERENCES sources (tenant_id, source_id) ON DELETE RESTRICT,
    -- Enforces ADR-019 & C32: (source_id, metadata_root) is unique within a tenant.
    -- Owner decision: a project may have multiple scopes; each scope belongs to exactly one project.
    CONSTRAINT uq_project_scopes_source_metadata UNIQUE (tenant_id, source_id, metadata_root)
);

-- ============================================================================
-- 15. ppm_scan_schedules: Background scan scheduler policy and concurrency control
-- Enforces: DG-2 (Interval, debounce, catch-up; max 1 active scan per source via lease)
-- ============================================================================
CREATE TABLE ppm_scan_schedules (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    interval_seconds INTEGER NOT NULL DEFAULT 300,
    debounce_seconds INTEGER NOT NULL DEFAULT 5,
    last_scan_at TIMESTAMPTZ,
    next_scan_at TIMESTAMPTZ,
    lease_holder VARCHAR(128),
    lease_expires_at TIMESTAMPTZ,
    fencing_token BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ppm_scan_schedules_tenant_source FOREIGN KEY (tenant_id, source_id) REFERENCES sources (tenant_id, source_id) ON DELETE RESTRICT,
    -- Enforces DG-2: One schedule definition per source per tenant
    CONSTRAINT uq_ppm_scan_schedules_source UNIQUE (tenant_id, source_id)
);

-- ============================================================================
-- 16. project_current_assessment: Pointer to the active assessment
-- Enforces: ADR-021 rev. 2, C34, C39 (CAS on basis_seq; late older scans with lower
-- basis_seq remain in history and cannot revert current assessment)
-- ============================================================================
CREATE TABLE project_current_assessment (
    tenant_id VARCHAR(64) NOT NULL,
    project_id VARCHAR(64) NOT NULL,
    assessment_id VARCHAR(64) NOT NULL,
    basis_seq BIGINT NOT NULL,
    snapshot_seq BIGINT NOT NULL,
    baseline_revision INTEGER,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Enforces ADR-021 rev. 2: Single current assessment pointer per project
    PRIMARY KEY (tenant_id, project_id)
);
