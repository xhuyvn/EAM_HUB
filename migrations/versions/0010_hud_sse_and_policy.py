"""HUD SSE replay, policy grant epochs, retention floors, and ISS-040 project links (P3a-WP2).

Revision ID: 0010_hud_sse_and_policy
Revises: 0009_scan_scheduler
Create Date: 2026-11-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_hud_sse_and_policy"
down_revision: str | None = "0009_scan_scheduler"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. ISS-040: direct project_id attribution on tasks and runs (avoids full outbox/event scans per snapshot)
    op.add_column("tasks", sa.Column("project_id", sa.String(length=64), nullable=True))
    op.create_index("ix_tasks_tenant_project", "tasks", ["tenant_id", "project_id"])

    op.add_column("runs", sa.Column("project_id", sa.String(length=64), nullable=True))
    op.create_index("ix_runs_tenant_project", "runs", ["tenant_id", "project_id"])

    # 2. ISS-040 + SSE replay index on event_log(tenant_id, id)
    op.create_index("ix_event_log_tenant_id_pk", "event_log", ["tenant_id", "id"])

    # 3. Policy grant epoch table per (tenant_id, principal_id)
    op.create_table(
        "principal_grant_epochs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal_id", sa.String(length=128), nullable=False),
        sa.Column("grant_epoch", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "principal_id",
            name="uq_principal_grant_epochs_tenant_principal",
        ),
        sa.CheckConstraint("grant_epoch >= 0", name="ck_principal_grant_epochs_nonneg"),
    )
    op.create_index(
        "ix_principal_grant_epochs_lookup",
        "principal_grant_epochs",
        ["tenant_id", "principal_id"],
    )

    # 4. Per-principal project grants/revocations
    op.create_table(
        "principal_project_grants",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("granted", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "principal_id",
            "project_id",
            name="uq_principal_project_grants_scope",
        ),
    )
    op.create_index(
        "ix_principal_project_grants_lookup",
        "principal_project_grants",
        ["tenant_id", "principal_id", "project_id"],
    )

    # 5. Per-stream retention floor table for cursor retention-gap detection (C09)
    op.create_table(
        "stream_retention_floors",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("stream_id", sa.String(length=128), nullable=False),
        sa.Column("min_retained_offset", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "stream_id",
            name="uq_stream_retention_floors_stream",
        ),
        sa.CheckConstraint("min_retained_offset >= 1", name="ck_stream_retention_floors_min_pos"),
    )
    op.create_index(
        "ix_stream_retention_floors_lookup",
        "stream_retention_floors",
        ["tenant_id", "stream_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_stream_retention_floors_lookup", table_name="stream_retention_floors")
    op.drop_table("stream_retention_floors")

    op.drop_index("ix_principal_project_grants_lookup", table_name="principal_project_grants")
    op.drop_table("principal_project_grants")

    op.drop_index("ix_principal_grant_epochs_lookup", table_name="principal_grant_epochs")
    op.drop_table("principal_grant_epochs")

    op.drop_index("ix_event_log_tenant_id_pk", table_name="event_log")

    op.drop_index("ix_runs_tenant_project", table_name="runs")
    op.drop_column("runs", "project_id")

    op.drop_index("ix_tasks_tenant_project", table_name="tasks")
    op.drop_column("tasks", "project_id")
