"""Collector Snapshot tables, monotonic sequences, and physical identities (P2a-WP2).

Revision ID: 0004_snapshots
Revises: 0003_locations
Create Date: 2026-11-08

P2a-WP2 (docs/04, DG-1 Amendment A1, DG-2, ISS-027, ISS-028, C16, C42):
- source_physical_identities: physical filesystem identity of root (ISS-028).
- collector_snapshots: physical source scan snapshot records.
- project_snapshot_sequences: monotonic sequence per (tenant_id, source_id, project_id).
- project_snapshots: project-scoped snapshot view and artifact/notes extraction.

All uniqueness targets are tenant-scoped (DG-1 Amendment A1).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_snapshots"
down_revision: str | None = "0003_locations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. source_physical_identities
    op.create_table(
        "source_physical_identities",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("fs_identity", sa.String(length=128), nullable=False),
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
        sa.PrimaryKeyConstraint("tenant_id", "source_id", name="pk_source_physical_identities"),
        sa.UniqueConstraint("tenant_id", "fs_identity", name="uq_source_physical_identities_fs"),
    )

    # 2. collector_snapshots
    op.create_table(
        "collector_snapshots",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="captured", nullable=False),
        sa.Column("mixed_snapshot", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("partial_reasons", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("git_commit", sa.String(length=64), nullable=True),
        sa.Column("git_dirty", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("tree_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_collector_snapshots_tenant_id"),
        sa.CheckConstraint(
            "status IN ('captured', 'partial', 'failed')",
            name="ck_collector_snapshots_status",
        ),
    )

    # 3. project_snapshot_sequences
    op.create_table(
        "project_snapshot_sequences",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("last_snapshot_seq", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id", "source_id", "project_id", name="pk_project_snapshot_sequences"),
    )

    # 4. project_snapshots
    op.create_table(
        "project_snapshots",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_seq", sa.BigInteger(), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata_root", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="captured", nullable=False),
        sa.Column("mixed_snapshot", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("partial_reasons", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("artifact_aliases", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("notes", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("claims", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_project_snapshots_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "source_id", "project_id", "snapshot_seq",
            name="uq_project_snapshots_seq",
        ),
        sa.CheckConstraint(
            "status IN ('captured', 'partial', 'failed')",
            name="ck_project_snapshots_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("project_snapshots")
    op.drop_table("project_snapshot_sequences")
    op.drop_table("collector_snapshots")
    op.drop_table("source_physical_identities")
