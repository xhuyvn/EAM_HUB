"""SQLAlchemy Core schema table definitions for EAP.

Defines all platform core tables matching the draft DDL (migrations/draft/0001_core.sql)
and Alembic migration 0001_core.py.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

# Baseline schema table from 0000_init
baseline_table = Table(
    "_eap_schema_baseline",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("installed_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("version", String(64), nullable=False),
)

# 1. tasks: Logical business tasks (C01)
tasks = Table(
    "tasks",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("client_idempotency_key", String(128), nullable=False),
    Column("task_intent_key", String(64), nullable=False),
    Column("task_type", String(64), nullable=False),
    Column("agent_id", String(64), nullable=False),
    Column("agent_version", String(32), nullable=False),
    Column("state", String(32), nullable=False, server_default="ready"),
    Column("deadline", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "task_intent_key", name="uq_tasks_tenant_task_intent_key"),
    UniqueConstraint("tenant_id", "client_idempotency_key", name="uq_tasks_tenant_client_key"),
    UniqueConstraint("tenant_id", "id", name="uq_tasks_tenant_id"),
    CheckConstraint(
        "state IN ('draft', 'ready', 'active', 'blocked', 'review_required', 'completed', 'failed', 'cancelled')",
        name="ck_tasks_state",
    ),
)

# 2. runs: Physical execution attempts of a task (C05, C06, C21)
runs = Table(
    "runs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("task_id", String(64), nullable=False),
    Column("run_id", String(64), nullable=False),
    Column("attempt", Integer, nullable=False, server_default="1"),
    Column("binding_id", String(64), nullable=False),
    Column("state", String(32), nullable=False, server_default="queued"),
    Column("remote_outcome", String(32), nullable=True),
    Column("lease_fencing_token", BigInteger, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        ["tasks.tenant_id", "tasks.id"],
        name="fk_runs_tenant_task",
        ondelete="RESTRICT",
    ),
    UniqueConstraint("tenant_id", "run_id", name="uq_runs_tenant_run_id"),
    UniqueConstraint("tenant_id", "task_id", "attempt", name="uq_runs_tenant_task_attempt"),
    CheckConstraint(
        "state IN ('queued', 'dispatching', 'running', 'waiting_approval', 'cancelling', 'succeeded', 'failed', 'cancelled', 'timed_out', 'unknown')",
        name="ck_runs_state",
    ),
    CheckConstraint(
        "remote_outcome IS NULL OR remote_outcome IN ('known', 'unknown', 'not_applicable')",
        name="ck_runs_remote_outcome",
    ),
)

# 3. run_attempt_history: Complete immutable history of run attempts (C19, C29)
run_attempt_history = Table(
    "run_attempt_history",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("task_id", String(64), nullable=False),
    Column("run_id", String(64), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("from_state", String(32), nullable=False),
    Column("to_state", String(32), nullable=False),
    Column("reason", Text, nullable=True),
    Column("recorded_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        ["tasks.tenant_id", "tasks.id"],
        name="fk_run_attempt_history_tenant_task",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "from_state IN ('queued', 'dispatching', 'running', 'waiting_approval', 'cancelling', 'succeeded', 'failed', 'cancelled', 'timed_out', 'unknown')",
        name="ck_run_attempt_history_from_state",
    ),
    CheckConstraint(
        "to_state IN ('queued', 'dispatching', 'running', 'waiting_approval', 'cancelling', 'succeeded', 'failed', 'cancelled', 'timed_out', 'unknown')",
        name="ck_run_attempt_history_to_state",
    ),
)

# 4. task_intents: Business action slots declared/persisted before tool calls (C29b)
task_intents = Table(
    "task_intents",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("task_id", String(64), nullable=False),
    Column("action_slot", String(64), nullable=False),
    Column("tool_id", String(128), nullable=False),
    Column("workflow_step", String(64), nullable=False),
    Column("status", String(32), nullable=False, server_default="declared"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        ["tasks.tenant_id", "tasks.id"],
        name="fk_task_intents_tenant_task",
        ondelete="RESTRICT",
    ),
    UniqueConstraint("tenant_id", "task_id", "action_slot", name="uq_task_intents_slot"),
    CheckConstraint(
        "status IN ('declared', 'running', 'completed', 'cancelled')",
        name="ck_task_intents_status",
    ),
)

# 5. dispatch_intents: Dispatch journal per run (C05, C06, C21, C38)
dispatch_intents = Table(
    "dispatch_intents",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("binding_id", String(64), nullable=False),
    Column("run_id", String(64), nullable=False),
    Column("operation", String(32), nullable=False, server_default="submit"),
    Column("idempotency_key", String(64), nullable=False),
    Column("state", String(32), nullable=False, server_default="prepared"),
    Column("execution_ref", String(128), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "binding_id", "run_id", "operation", name="uq_dispatch_intents_op"),
    CheckConstraint(
        "state IN ('prepared', 'sent', 'acknowledged', 'ambiguous')",
        name="ck_dispatch_intents_state",
    ),
)

# 6. action_executions: Execution journal and side-effect idempotency (C29)
action_executions = Table(
    "action_executions",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("operation_key", String(64), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("tool_id", String(128), nullable=False),
    Column("resource_ref", Text, nullable=False),
    Column("state", String(32), nullable=False, server_default="running"),
    Column("external_ref", String(128), nullable=True),
    Column("result_payload", JSONB, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "operation_key", name="uq_action_executions_key"),
    CheckConstraint(
        "state IN ('running', 'succeeded', 'failed', 'cancelled', 'partial', 'unknown')",
        name="ck_action_executions_state",
    ),
)

# 7. leases: Distributed execution leases with monotonically increasing fencing (C15, C21)
leases = Table(
    "leases",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("resource_type", String(64), nullable=False),
    Column("resource_id", String(128), nullable=False),
    Column("fencing_token", BigInteger, nullable=False, server_default="1"),
    Column("holder_id", String(128), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "resource_type", "resource_id", name="uq_leases_resource"),
)

# 8. commands: Control plane commands (C06, C21)
commands = Table(
    "commands",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("task_id", String(64), nullable=False),
    Column("run_id", String(64), nullable=False),
    Column("command_type", String(32), nullable=False),
    Column("status", String(32), nullable=False, server_default="accepted"),
    Column("payload", JSONB, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        ["tasks.tenant_id", "tasks.id"],
        name="fk_commands_tenant_task",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "status IN ('accepted', 'acknowledged', 'completed', 'rejected')",
        name="ck_commands_status",
    ),
)

# 9. outbox: Durable outbox for transactional event publishing (ADR-020: no offset column)
outbox = Table(
    "outbox",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("event_id", String(64), nullable=False),
    Column("stream_id", String(128), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# 10. event_log: Authoritative ingested event log (C02, C33)
event_log = Table(
    "event_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("stream_id", String(128), nullable=False),
    Column("ingest_offset", BigInteger, nullable=False),
    Column("event_id", String(64), nullable=False),
    Column("producer_id", String(64), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "stream_id", "ingest_offset", name="uq_event_log_stream_offset"),
    UniqueConstraint("tenant_id", "event_id", name="uq_event_log_event_id"),
)

# 11. artifacts: Metadata and content-addressed references for task artifacts (C10)
artifacts = Table(
    "artifacts",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("artifact_id", String(64), nullable=False),
    Column("task_id", String(64), nullable=False),
    Column("run_id", String(64), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("content_type", String(128), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("storage_uri", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        ["tasks.tenant_id", "tasks.id"],
        name="fk_artifacts_tenant_task",
        ondelete="RESTRICT",
    ),
    UniqueConstraint("tenant_id", "artifact_id", name="uq_artifacts_tenant_artifact"),
)

# 12. usage_ledger: Deduped accounting for model and compute usage (C38)
usage_ledger = Table(
    "usage_ledger",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("provider_usage_id", String(128), nullable=False),
    Column("run_id", String(64), nullable=False),
    Column("model_profile", String(64), nullable=False),
    Column("prompt_tokens", Integer, nullable=False, server_default="0"),
    Column("completion_tokens", Integer, nullable=False, server_default="0"),
    Column("cost_estimate", Numeric(12, 6), nullable=False, server_default="0"),
    Column("reported_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "provider_usage_id", name="uq_usage_ledger_provider_id"),
)

# 13. sources: Physical repository or directory roots (ADR-022, C42)
sources = Table(
    "sources",
    metadata,
    Column("source_id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("canonical_root", Text, nullable=False),
    Column("repo_identity", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "canonical_root", name="uq_sources_tenant_canonical_root"),
    UniqueConstraint("tenant_id", "repo_identity", name="uq_sources_tenant_repo_identity"),
    UniqueConstraint("tenant_id", "source_id", name="uq_sources_tenant_source_id"),
)

# 14. project_scopes: Mapping between project, source, and metadata root (ADR-019, ADR-022, C32, W7-3)
project_scopes = Table(
    "project_scopes",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("metadata_root", Text, nullable=False),
    Column("scan_scope", Text, nullable=True),
    Column("status", String(32), nullable=False, server_default="active"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["tenant_id", "source_id"],
        ["sources.tenant_id", "sources.source_id"],
        name="fk_project_scopes_tenant_source",
        ondelete="RESTRICT",
    ),
    UniqueConstraint("tenant_id", "source_id", "metadata_root", name="uq_project_scopes_source_metadata"),
    CheckConstraint(
        "status IN ('proposed', 'active', 'suspended', 'retired')",
        name="ck_project_scopes_status",
    ),
)

# 15. ppm_scan_schedules: Background scan scheduler policy and concurrency control (DG-2)
ppm_scan_schedules = Table(
    "ppm_scan_schedules",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("interval_seconds", Integer, nullable=False, server_default="300"),
    Column("debounce_seconds", Integer, nullable=False, server_default="5"),
    Column("last_scan_at", DateTime(timezone=True), nullable=True),
    Column("next_scan_at", DateTime(timezone=True), nullable=True),
    Column("lease_holder", String(128), nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("fencing_token", BigInteger, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["tenant_id", "source_id"],
        ["sources.tenant_id", "sources.source_id"],
        name="fk_ppm_scan_schedules_tenant_source",
        ondelete="RESTRICT",
    ),
    UniqueConstraint("tenant_id", "source_id", name="uq_ppm_scan_schedules_source"),
)

# 16. project_current_assessment: Pointer to the active assessment (ADR-021 rev. 2, C34, C39)
project_current_assessment = Table(
    "project_current_assessment",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("assessment_id", String(64), nullable=False),
    Column("basis_seq", BigInteger, nullable=False),
    Column("snapshot_seq", BigInteger, nullable=False),
    Column("baseline_revision", Integer, nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("tenant_id", "project_id", name="pk_project_current_assessment"),
)
