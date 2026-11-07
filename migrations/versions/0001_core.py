"""Enterprise Agent Platform Core Schema.

Revision ID: 0001_core
Revises: 0000_init
Create Date: 2026-11-07

Implements the 16 core platform tables and constraints per signed DG-1 and DG-2:
- tasks: logical business tasks (C01, client & task intent unique keys)
- runs: physical execution attempts (C05, C06, C21)
- run_attempt_history: immutable attempt history (C19, C29)
- task_intents: business action slots declared prior to tool calls (C29b)
- dispatch_intents: dispatch journal per run (C05, C21, C38)
- action_executions: execution journal and side-effect idempotency (C29)
- leases: distributed execution leases with fencing tokens (C15, C21)
- commands: control plane commands (C06, C21)
- outbox: transactional outbox (ADR-020: no offset column)
- event_log: authoritative ingested event log (C02, C33: monotonic offsets)
- artifacts: artifact metadata and content hashes (C10)
- usage_ledger: provider usage deduplication (C38)
- sources: physical filesystem roots (ADR-022, C42)
- project_scopes: project-to-source scoping (ADR-019, C32, W7-3)
- ppm_scan_schedules: background scan policy and mutual exclusion (DG-2)
- project_current_assessment: active assessment CAS pointer (ADR-021 rev 2, C34, C39)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_core"
down_revision: str | None = "0000_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. tasks: Logical business tasks (C01)
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("client_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("task_intent_key", sa.String(length=64), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("agent_version", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "task_intent_key", name="uq_tasks_tenant_task_intent_key"),
        sa.UniqueConstraint("tenant_id", "client_idempotency_key", name="uq_tasks_tenant_client_key"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tasks_tenant_id"),
        sa.CheckConstraint(
            "state IN ('draft', 'ready', 'active', 'blocked', 'review_required', 'completed', 'failed', 'cancelled', 'queued')",
            name="ck_tasks_state",
        ),
    )

    # 2. runs: Physical execution attempts of a task (C05, C06, C21)
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("binding_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("remote_outcome", sa.String(length=32), nullable=True),
        sa.Column("lease_fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tasks.tenant_id", "tasks.id"],
            name="fk_runs_tenant_task",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "run_id", name="uq_runs_tenant_run_id"),
        sa.UniqueConstraint("tenant_id", "task_id", "attempt", name="uq_runs_tenant_task_attempt"),
        sa.CheckConstraint(
            "state IN ('queued', 'dispatching', 'running', 'waiting_approval', 'cancelling', 'succeeded', 'failed', 'cancelled', 'timed_out', 'unknown')",
            name="ck_runs_state",
        ),
        sa.CheckConstraint(
            "remote_outcome IS NULL OR remote_outcome IN ('known', 'unknown', 'not_applicable')",
            name="ck_runs_remote_outcome",
        ),
    )

    # 3. run_attempt_history: Complete immutable history of run attempts (C19, C29)
    op.create_table(
        "run_attempt_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=False),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tasks.tenant_id", "tasks.id"],
            name="fk_run_attempt_history_tenant_task",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "from_state IN ('queued', 'dispatching', 'running', 'waiting_approval', 'cancelling', 'succeeded', 'failed', 'cancelled', 'timed_out', 'unknown')",
            name="ck_run_attempt_history_from_state",
        ),
        sa.CheckConstraint(
            "to_state IN ('queued', 'dispatching', 'running', 'waiting_approval', 'cancelling', 'succeeded', 'failed', 'cancelled', 'timed_out', 'unknown')",
            name="ck_run_attempt_history_to_state",
        ),
    )

    # 4. task_intents: Business action slots declared/persisted before tool calls (C29b)
    op.create_table(
        "task_intents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("action_slot", sa.String(length=64), nullable=False),
        sa.Column("tool_id", sa.String(length=128), nullable=False),
        sa.Column("workflow_step", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="declared", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tasks.tenant_id", "tasks.id"],
            name="fk_task_intents_tenant_task",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "task_id", "action_slot", name="uq_task_intents_slot"),
        sa.CheckConstraint(
            "status IN ('declared', 'running', 'completed', 'cancelled')",
            name="ck_task_intents_status",
        ),
    )

    # 5. dispatch_intents: Dispatch journal per run (C05, C06, C21, C38)
    op.create_table(
        "dispatch_intents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("binding_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=32), server_default="submit", nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="prepared", nullable=False),
        sa.Column("execution_ref", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "binding_id",
            "run_id",
            "operation",
            name="uq_dispatch_intents_op",
        ),
        sa.CheckConstraint(
            "state IN ('prepared', 'sent', 'acknowledged', 'ambiguous')",
            name="ck_dispatch_intents_state",
        ),
    )

    # 6. action_executions: Execution journal and side-effect idempotency (C29)
    op.create_table(
        "action_executions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("operation_key", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("tool_id", sa.String(length=128), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="running", nullable=False),
        sa.Column("external_ref", sa.String(length=128), nullable=True),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "operation_key", name="uq_action_executions_key"),
        sa.CheckConstraint(
            "state IN ('running', 'succeeded', 'failed', 'cancelled', 'partial', 'unknown')",
            name="ck_action_executions_state",
        ),
    )

    # 7. leases: Distributed execution leases with monotonically increasing fencing (C15, C21)
    op.create_table(
        "leases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("holder_id", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "resource_type",
            "resource_id",
            name="uq_leases_resource",
        ),
    )

    # 8. commands: Control plane commands (C06, C21)
    op.create_table(
        "commands",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("command_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="accepted", nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tasks.tenant_id", "tasks.id"],
            name="fk_commands_tenant_task",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('accepted', 'acknowledged', 'completed', 'rejected')",
            name="ck_commands_status",
        ),
    )

    # 9. outbox: Durable outbox for transactional event publishing (ADR-020: NO offset column)
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("stream_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 10. event_log: Authoritative ingested event log (C02, C33)
    op.create_table(
        "event_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("stream_id", sa.String(length=128), nullable=False),
        sa.Column("ingest_offset", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("producer_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "event_id", name="uq_event_log_event_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "stream_id",
            "ingest_offset",
            name="uq_event_log_stream_offset",
        ),
    )

    # 11. artifacts: Metadata and content-addressed references for task artifacts (C10)
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tasks.tenant_id", "tasks.id"],
            name="fk_artifacts_tenant_task",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "artifact_id", name="uq_artifacts_tenant_artifact"),
    )

    # 12. usage_ledger: Deduped accounting for model and compute usage (C38)
    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("provider_usage_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("model_profile", sa.String(length=64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "cost_estimate",
            sa.Numeric(precision=12, scale=6),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "reported_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "provider_usage_id",
            name="uq_usage_ledger_provider_id",
        ),
    )

    # 13. sources: Physical repository or directory roots (ADR-022, C42)
    op.create_table(
        "sources",
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("canonical_root", sa.Text(), nullable=False),
        sa.Column("repo_identity", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "canonical_root",
            name="uq_sources_tenant_canonical_root",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "repo_identity",
            name="uq_sources_tenant_repo_identity",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            name="uq_sources_tenant_source_id",
        ),
    )

    # 14. project_scopes: Mapping between project, source, and metadata root (ADR-019, C32, W7-3)
    op.create_table(
        "project_scopes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("metadata_root", sa.Text(), nullable=False),
        sa.Column("scan_scope", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_project_scopes_tenant_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "metadata_root",
            name="uq_project_scopes_source_metadata",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'active', 'suspended', 'retired')",
            name="ck_project_scopes_status",
        ),
    )

    # 15. ppm_scan_schedules: Background scan scheduler policy and concurrency control (DG-2)
    op.create_table(
        "ppm_scan_schedules",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), server_default="300", nullable=False),
        sa.Column("debounce_seconds", sa.Integer(), server_default="5", nullable=False),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_holder", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_ppm_scan_schedules_tenant_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            name="uq_ppm_scan_schedules_source",
        ),
    )

    # 16. project_current_assessment: Pointer to the active assessment (ADR-021 rev. 2, C34, C39)
    op.create_table(
        "project_current_assessment",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("assessment_id", sa.String(length=64), nullable=False),
        sa.Column("basis_seq", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_seq", sa.BigInteger(), nullable=False),
        sa.Column("baseline_revision", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id", "project_id", name="pk_project_current_assessment"),
    )


def downgrade() -> None:
    # Drop all 16 tables in reverse dependency order
    op.drop_table("project_current_assessment")
    op.drop_table("ppm_scan_schedules")
    op.drop_table("project_scopes")
    op.drop_table("sources")
    op.drop_table("usage_ledger")
    op.drop_table("artifacts")
    op.drop_table("event_log")
    op.drop_table("outbox")
    op.drop_table("commands")
    op.drop_table("leases")
    op.drop_table("action_executions")
    op.drop_table("dispatch_intents")
    op.drop_table("task_intents")
    op.drop_table("run_attempt_history")
    op.drop_table("runs")
    op.drop_table("tasks")
