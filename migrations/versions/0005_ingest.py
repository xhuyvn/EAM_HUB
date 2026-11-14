"""PPM Ingest tables: artifacts, updates, notes, checkpoints (P2a-WP3).

Revision ID: 0005_ingest
Revises: 0004_snapshots
Create Date: 2026-11-14

P2a-WP3 (docs/04, DG-1 Amendment A1, C17):
- ppm_ingested_artifacts: Identity and outcome log per artifact.
- ppm_updates: Append-only, deduplicated, supersession-tracked session updates.
- ppm_notes: Deduplicated verbatim notes with max_file_bytes size cap.
- ppm_ingest_checkpoints: High-water snapshot_seq per (tenant, source, project).

All uniqueness targets and foreign keys are tenant-scoped (DG-1 Amendment A1).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_ingest"
down_revision: str | None = "0004_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. ppm_ingested_artifacts
    op.create_table(
        "ppm_ingested_artifacts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("first_seen_snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ppm_ingested_artifacts_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "source_id",
            "artifact_path",
            "content_hash",
            name="uq_ppm_ingested_artifacts_identity",
        ),
        sa.CheckConstraint(
            "kind IN ('update', 'status', 'project', 'acceptance', 'decision', 'note')",
            name="ck_ppm_ingested_artifacts_kind",
        ),
        sa.CheckConstraint(
            "outcome IN ('ingested', 'duplicate', 'conflict', 'quarantined', 'rejected')",
            name="ck_ppm_ingested_artifacts_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_ppm_ingested_artifacts_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "first_seen_snapshot_id"],
            ["project_snapshots.tenant_id", "project_snapshots.id"],
            name="fk_ppm_ingested_artifacts_snapshot",
            ondelete="RESTRICT",
        ),
    )

    # 2. ppm_updates
    op.create_table(
        "ppm_updates",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("update_id", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("contract_version", sa.String(length=16), nullable=False),
        sa.Column("supersedes_update_id", sa.String(length=128), nullable=True),
        sa.Column(
            "supersession_state",
            sa.String(length=32),
            server_default="active",
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("ingested_artifact_id", sa.BigInteger(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ppm_updates_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "update_id",
            name="uq_ppm_updates_tenant_project_update_id",
        ),
        sa.CheckConstraint(
            "supersession_state IN ('active', 'superseded', 'orphan_supersedes', 'conflict')",
            name="ck_ppm_updates_supersession_state",
        ),
        sa.CheckConstraint(
            "contract_version IN ('2.0', '2.1')",
            name="ck_ppm_updates_contract_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_ppm_updates_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_snapshot_id"],
            ["project_snapshots.tenant_id", "project_snapshots.id"],
            name="fk_ppm_updates_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "ingested_artifact_id"],
            ["ppm_ingested_artifacts.tenant_id", "ppm_ingested_artifacts.id"],
            name="fk_ppm_updates_artifact",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_ppm_updates_supersedes",
        "ppm_updates",
        ["tenant_id", "project_id", "supersedes_update_id"],
    )

    # 3. ppm_notes
    op.create_table(
        "ppm_notes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("metadata_root", sa.Text(), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), server_default="unknown", nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("byte_length", sa.Integer(), nullable=False),
        sa.Column("first_seen_snapshot_id", sa.String(length=64), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ppm_notes_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "source_id",
            "artifact_path",
            "content_hash",
            name="uq_ppm_notes_dedup",
        ),
        sa.CheckConstraint(
            "kind IN ('idea', 'blocker', 'question', 'observation', 'unknown')",
            name="ck_ppm_notes_kind",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_ppm_notes_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "first_seen_snapshot_id"],
            ["project_snapshots.tenant_id", "project_snapshots.id"],
            name="fk_ppm_notes_snapshot",
            ondelete="RESTRICT",
        ),
    )

    # 4. ppm_ingest_checkpoints
    op.create_table(
        "ppm_ingest_checkpoints",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("last_ingested_snapshot_seq", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_ingested_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "source_id",
            "project_id",
            name="pk_ppm_ingest_checkpoints",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_ppm_ingest_checkpoints_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "last_ingested_snapshot_id"],
            ["project_snapshots.tenant_id", "project_snapshots.id"],
            name="fk_ppm_ingest_checkpoints_snapshot",
            ondelete="RESTRICT",
        ),
    )


def downgrade() -> None:
    op.drop_table("ppm_ingest_checkpoints")
    op.drop_table("ppm_notes")
    op.drop_index("ix_ppm_updates_supersedes", table_name="ppm_updates")
    op.drop_table("ppm_updates")
    op.drop_table("ppm_ingested_artifacts")
