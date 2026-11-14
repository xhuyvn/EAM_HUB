"""SQLAlchemy Core definitions for PPM Ingest tables (P2a-WP3).

Matches Alembic migration ``0005_ingest``.
Tenant scoping (DG-1 Amendment A1): every table carries ``tenant_id NOT NULL``,
every uniqueness target is tenant-scoped, and every foreign key is a composite
key including ``tenant_id``.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
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

# 1. ppm_ingested_artifacts: Identity and outcome log for every ingested/rejected/quarantined artifact
ppm_ingested_artifacts = Table(
    "ppm_ingested_artifacts",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("artifact_path", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("first_seen_snapshot_id", String(64), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("reason_code", String(64), nullable=True),
    Column("detail", JSONB, nullable=False, server_default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_ppm_ingested_artifacts_tenant_id"),
    UniqueConstraint(
        "tenant_id",
        "project_id",
        "source_id",
        "artifact_path",
        "content_hash",
        name="uq_ppm_ingested_artifacts_identity",
    ),
    CheckConstraint(
        "kind IN ('update', 'status', 'project', 'acceptance', 'decision', 'note')",
        name="ck_ppm_ingested_artifacts_kind",
    ),
    CheckConstraint(
        "outcome IN ('ingested', 'duplicate', 'conflict', 'quarantined', 'rejected')",
        name="ck_ppm_ingested_artifacts_outcome",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_id"],
        ["sources.tenant_id", "sources.source_id"],
        name="fk_ppm_ingested_artifacts_source",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "first_seen_snapshot_id"],
        ["project_snapshots.tenant_id", "project_snapshots.id"],
        name="fk_ppm_ingested_artifacts_snapshot",
        ondelete="RESTRICT",
    ),
)

# 2. ppm_updates: Append-only, deduplicated, supersession-tracked session updates
ppm_updates = Table(
    "ppm_updates",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("update_id", String(128), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("contract_version", String(16), nullable=False),
    Column("supersedes_update_id", String(128), nullable=True),
    Column("supersession_state", String(32), nullable=False, server_default="active"),
    Column("payload", JSONB, nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("source_snapshot_id", String(64), nullable=False),
    Column("artifact_path", Text, nullable=False),
    Column("ingested_artifact_id", BigInteger, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=True),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_ppm_updates_tenant_id"),
    UniqueConstraint(
        "tenant_id",
        "project_id",
        "update_id",
        name="uq_ppm_updates_tenant_project_update_id",
    ),
    CheckConstraint(
        "supersession_state IN ('active', 'superseded', 'orphan_supersedes', 'conflict')",
        name="ck_ppm_updates_supersession_state",
    ),
    CheckConstraint(
        "contract_version IN ('2.0', '2.1')",
        name="ck_ppm_updates_contract_version",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_id"],
        ["sources.tenant_id", "sources.source_id"],
        name="fk_ppm_updates_source",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_snapshot_id"],
        ["project_snapshots.tenant_id", "project_snapshots.id"],
        name="fk_ppm_updates_snapshot",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "ingested_artifact_id"],
        ["ppm_ingested_artifacts.tenant_id", "ppm_ingested_artifacts.id"],
        name="fk_ppm_updates_artifact",
        ondelete="RESTRICT",
    ),
    Index("ix_ppm_updates_supersedes", "tenant_id", "project_id", "supersedes_update_id"),
)

# 3. ppm_notes: Deduplicated verbatim notes (untrusted data; never alters status/claims)
ppm_notes = Table(
    "ppm_notes",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("metadata_root", Text, nullable=False),
    Column("artifact_path", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("kind", String(32), nullable=False, server_default="unknown"),
    Column("body", Text, nullable=False),
    Column("byte_length", Integer, nullable=False),
    Column("first_seen_snapshot_id", String(64), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_ppm_notes_tenant_id"),
    UniqueConstraint(
        "tenant_id",
        "project_id",
        "source_id",
        "artifact_path",
        "content_hash",
        name="uq_ppm_notes_dedup",
    ),
    CheckConstraint(
        "kind IN ('idea', 'blocker', 'question', 'observation', 'unknown')",
        name="ck_ppm_notes_kind",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_id"],
        ["sources.tenant_id", "sources.source_id"],
        name="fk_ppm_notes_source",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "first_seen_snapshot_id"],
        ["project_snapshots.tenant_id", "project_snapshots.id"],
        name="fk_ppm_notes_snapshot",
        ondelete="RESTRICT",
    ),
)

# 4. ppm_ingest_checkpoints: High-water snapshot_seq per (tenant, source, project)
ppm_ingest_checkpoints = Table(
    "ppm_ingest_checkpoints",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("last_ingested_snapshot_seq", BigInteger, nullable=False, server_default="0"),
    Column("last_ingested_snapshot_id", String(64), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("tenant_id", "source_id", "project_id", name="pk_ppm_ingest_checkpoints"),
    ForeignKeyConstraint(
        ["tenant_id", "source_id"],
        ["sources.tenant_id", "sources.source_id"],
        name="fk_ppm_ingest_checkpoints_source",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "last_ingested_snapshot_id"],
        ["project_snapshots.tenant_id", "project_snapshots.id"],
        name="fk_ppm_ingest_checkpoints_snapshot",
        ondelete="RESTRICT",
    ),
)

__all__ = [
    "ppm_ingest_checkpoints",
    "ppm_ingested_artifacts",
    "ppm_notes",
    "ppm_updates",
]
