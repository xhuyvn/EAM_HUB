"""Database tables for the PPM Assessment Orchestrator (P2a-WP6 / DG-2).

All tenant-scoped foreign keys are composite with ``tenant_id`` (DG-1 Amendment A1).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from eap.db.tables import metadata

# 1. ppm_assessment_tasks: Deduplicated, input-locked assessment tasks (DG-2 §3)
ppm_assessment_tasks = Table(
    "ppm_assessment_tasks",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("trigger", String(32), nullable=False),
    Column("trigger_ref", String(256), nullable=False),
    Column("basis_seq", BigInteger, nullable=False),
    Column("task_id", String(64), nullable=False),
    Column("run_id", String(64), nullable=False),
    Column("status", String(32), nullable=False, server_default="pending"),
    # Locked inputs at task creation time
    Column("project_snapshot_id", String(64), nullable=True),
    Column("snapshot_seq", BigInteger, nullable=False, server_default="1"),
    Column("assessed_revision", String(128), nullable=True),
    Column("baseline_revision", Integer, nullable=True),
    Column("evidence_ttl_days", Float, nullable=False, server_default="7.0"),
    Column("locked_now", DateTime(timezone=True), nullable=True),
    Column("previous_assessment_id", String(64), nullable=True),
    Column("dependency_refs", JSONB, nullable=False, server_default="[]"),
    Column("context", JSONB, nullable=True),
    Column("fixture_id", String(128), nullable=True),
    # Execution outcome
    Column("assessment_id", String(64), nullable=True),
    Column("analysis_status", String(32), nullable=True),
    Column("verifier_executed", Boolean, nullable=False, server_default="false"),
    Column("error_reason", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("tenant_id", "id", name="uq_ppm_assessment_tasks_tenant_id"),
    UniqueConstraint(
        "tenant_id",
        "project_id",
        "trigger",
        "trigger_ref",
        name="uq_ppm_assessment_tasks_dedup",
    ),
    UniqueConstraint(
        "tenant_id",
        "project_id",
        "basis_seq",
        name="uq_ppm_assessment_tasks_basis_seq",
    ),
    CheckConstraint(
        "trigger IN ('snapshot', 'baseline_changed', 'evidence_ttl_expired', 'model_recovered', 'manual')",
        name="ck_ppm_assessment_tasks_trigger",
    ),
    CheckConstraint(
        "status IN ('pending', 'running', 'succeeded', 'failed', 'bypassed')",
        name="ck_ppm_assessment_tasks_status",
    ),
    CheckConstraint(
        "analysis_status IS NULL OR analysis_status IN ('complete', 'model_failed', 'skipped')",
        name="ck_ppm_assessment_tasks_analysis_status",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        ["tasks.tenant_id", "tasks.id"],
        name="fk_ppm_assessment_tasks_task",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "project_snapshot_id"],
        ["project_snapshots.tenant_id", "project_snapshots.id"],
        name="fk_ppm_assessment_tasks_snapshot",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "assessment_id"],
        ["ppm_assessments.tenant_id", "ppm_assessments.id"],
        name="fk_ppm_assessment_tasks_assessment",
        ondelete="RESTRICT",
    ),
    Index("ix_ppm_assessment_tasks_tenant_status", "tenant_id", "status"),
    Index("ix_ppm_assessment_tasks_tenant_project", "tenant_id", "project_id"),
)

# 2. ppm_orchestrator_cursors: Outbox polling cursor for the assessment orchestrator
ppm_orchestrator_cursors = Table(
    "ppm_orchestrator_cursors",
    metadata,
    Column("consumer_name", String(128), nullable=False),
    Column("last_outbox_id", BigInteger, nullable=False, server_default="0"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("consumer_name", name="pk_ppm_orchestrator_cursors"),
)

__all__ = [
    "ppm_assessment_tasks",
    "ppm_orchestrator_cursors",
]
