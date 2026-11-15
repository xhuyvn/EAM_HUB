"""PPM scan scheduler observability and occurrence tables (P2a-WP5, DG-2).

Revision ID: 0009_scan_scheduler
Revises: 0008_agents
Create Date: 2026-11-15

Notes:
- Preserves ``ppm_scan_schedules`` column parity against ``migrations/draft/0001_core.sql``.
- Adds ``ppm_scan_source_status`` for per-source observability (``last_scan_started_at``,
  ``last_successful_scan_at``, redacted ``last_error``, ``last_occurrence_id``, etc.).
- Adds ``ppm_scan_occurrences`` for deterministic per-source trigger deduplication
  so redelivered trigger occurrences are an idempotent no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_scan_scheduler"
down_revision: str | None = "0008_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. ppm_scan_source_status
    op.create_table(
        "ppm_scan_source_status",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("last_scan_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_occurrence_id", sa.String(length=128), nullable=True),
        sa.Column("last_trigger", sa.String(length=32), nullable=True),
        sa.Column("last_task_id", sa.String(length=64), nullable=True),
        sa.Column("last_run_id", sa.String(length=64), nullable=True),
        sa.Column("last_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("last_fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ppm_scan_source_status_tenant_id"),
        sa.UniqueConstraint("tenant_id", "source_id", name="uq_ppm_scan_source_status_source"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_ppm_scan_source_status_source",
            ondelete="RESTRICT",
        ),
    )

    # 2. ppm_scan_occurrences
    op.create_table(
        "ppm_scan_occurrences",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("occurrence_id", sa.String(length=128), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="running", nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ppm_scan_occurrences_tenant_id"),
        sa.UniqueConstraint("tenant_id", "source_id", "occurrence_id", name="uq_ppm_scan_occurrences_source_occ"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_ppm_scan_occurrences_source",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "trigger IN ('startup', 'interval', 'watcher', 'manual')",
            name="ck_ppm_scan_occurrences_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_ppm_scan_occurrences_status",
        ),
    )
    op.create_index(
        "ix_ppm_scan_occurrences_lookup",
        "ppm_scan_occurrences",
        ["tenant_id", "source_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ppm_scan_occurrences_lookup", table_name="ppm_scan_occurrences")
    op.drop_table("ppm_scan_occurrences")
    op.drop_table("ppm_scan_source_status")
