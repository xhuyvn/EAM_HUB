"""SQLAlchemy Core definitions for Collector Snapshot tables (P2a-WP2).

Matches Alembic migration ``0004_snapshots``.
Tenant scoping (DG-1 Amendment A1): every table carries ``tenant_id NOT NULL``
and every uniqueness target is tenant-scoped.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from eap.db.tables import metadata

# 1. source_physical_identities: Physical filesystem identity of directory roots (ISS-028)
source_physical_identities = Table(
    "source_physical_identities",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("fs_identity", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("tenant_id", "source_id", name="pk_source_physical_identities"),
    UniqueConstraint("tenant_id", "fs_identity", name="uq_source_physical_identities_fs"),
)

# 2. collector_snapshots: Physical source scan snapshot records
collector_snapshots = Table(
    "collector_snapshots",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("status", String(32), nullable=False, server_default="captured"),
    Column("mixed_snapshot", Boolean, nullable=False, server_default="false"),
    Column("partial_reasons", JSONB, nullable=False, server_default="[]"),
    Column("git_commit", String(64), nullable=True),
    Column("git_dirty", Boolean, nullable=False, server_default="false"),
    Column("tree_hash", String(64), nullable=False),
    Column("manifest", JSONB, nullable=False, server_default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_collector_snapshots_tenant_id"),
    CheckConstraint(
        "status IN ('captured', 'partial', 'failed')",
        name="ck_collector_snapshots_status",
    ),
)

# 3. project_snapshot_sequences: Monotonic sequence issuer per (tenant_id, source_id, project_id)
project_snapshot_sequences = Table(
    "project_snapshot_sequences",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("last_snapshot_seq", BigInteger, nullable=False, server_default="0"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("tenant_id", "source_id", "project_id", name="pk_project_snapshot_sequences"),
)

# 4. project_snapshots: Project-scoped snapshot view and artifact/notes extraction
project_snapshots = Table(
    "project_snapshots",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("snapshot_id", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("snapshot_seq", BigInteger, nullable=False),
    Column("scope_hash", String(64), nullable=False),
    Column("metadata_root", Text, nullable=False),
    Column("status", String(32), nullable=False, server_default="captured"),
    Column("mixed_snapshot", Boolean, nullable=False, server_default="false"),
    Column("partial_reasons", JSONB, nullable=False, server_default="[]"),
    Column("artifact_aliases", JSONB, nullable=False, server_default="{}"),
    Column("notes", JSONB, nullable=False, server_default="[]"),
    Column("claims", JSONB, nullable=False, server_default="[]"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_project_snapshots_tenant_id"),
    UniqueConstraint(
        "tenant_id", "source_id", "project_id", "snapshot_seq",
        name="uq_project_snapshots_seq",
    ),
    CheckConstraint(
        "status IN ('captured', 'partial', 'failed')",
        name="ck_project_snapshots_status",
    ),
)

__all__ = [
    "collector_snapshots",
    "project_snapshot_sequences",
    "project_snapshots",
    "source_physical_identities",
]
