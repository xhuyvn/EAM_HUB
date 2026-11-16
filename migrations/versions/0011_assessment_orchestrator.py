"""Assessment orchestrator task tracking and outbox cursor (P2a-WP6).

Revision ID: 0011_assessment_orchestrator
Revises: 0010_hud_sse_and_policy
Create Date: 2026-11-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_assessment_orchestrator"
down_revision: str | None = "0010_hud_sse_and_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ppm_assessment_tasks",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("trigger_ref", sa.String(length=256), nullable=False),
        sa.Column("basis_seq", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("project_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("snapshot_seq", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("assessed_revision", sa.String(length=128), nullable=True),
        sa.Column("baseline_revision", sa.Integer(), nullable=True),
        sa.Column("evidence_ttl_days", sa.Float(), server_default="7.0", nullable=False),
        sa.Column("locked_now", sa.DateTime(timezone=True), nullable=True),
        sa.Column("previous_assessment_id", sa.String(length=64), nullable=True),
        sa.Column("dependency_refs", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("fixture_id", sa.String(length=128), nullable=True),
        sa.Column("assessment_id", sa.String(length=64), nullable=True),
        sa.Column("analysis_status", sa.String(length=32), nullable=True),
        sa.Column("verifier_executed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ppm_assessment_tasks_tenant_id"),
        sa.UniqueConstraint("tenant_id", "project_id", "trigger", "trigger_ref", name="uq_ppm_assessment_tasks_dedup"),
        sa.UniqueConstraint("tenant_id", "project_id", "basis_seq", name="uq_ppm_assessment_tasks_basis_seq"),
        sa.CheckConstraint(
            "trigger IN ('snapshot', 'baseline_changed', 'evidence_ttl_expired', 'model_recovered', 'manual')",
            name="ck_ppm_assessment_tasks_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'bypassed')",
            name="ck_ppm_assessment_tasks_status",
        ),
        sa.CheckConstraint(
            "analysis_status IS NULL OR analysis_status IN ('complete', 'model_failed', 'skipped')",
            name="ck_ppm_assessment_tasks_analysis_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tasks.tenant_id", "tasks.id"],
            name="fk_ppm_assessment_tasks_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_snapshot_id"],
            ["project_snapshots.tenant_id", "project_snapshots.id"],
            name="fk_ppm_assessment_tasks_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "assessment_id"],
            ["ppm_assessments.tenant_id", "ppm_assessments.id"],
            name="fk_ppm_assessment_tasks_assessment",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_ppm_assessment_tasks_tenant_status", "ppm_assessment_tasks", ["tenant_id", "status"])
    op.create_index("ix_ppm_assessment_tasks_tenant_project", "ppm_assessment_tasks", ["tenant_id", "project_id"])

    op.create_table(
        "ppm_orchestrator_cursors",
        sa.Column("consumer_name", sa.String(length=128), nullable=False),
        sa.Column("last_outbox_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("consumer_name", name="pk_ppm_orchestrator_cursors"),
    )


def downgrade() -> None:
    op.drop_table("ppm_orchestrator_cursors")
    op.drop_index("ix_ppm_assessment_tasks_tenant_project", table_name="ppm_assessment_tasks")
    op.drop_index("ix_ppm_assessment_tasks_tenant_status", table_name="ppm_assessment_tasks")
    op.drop_table("ppm_assessment_tasks")
