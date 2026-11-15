"""SQLAlchemy Core definitions for HUD policy grant epochs, project grants, and retention watermarks (P3a-WP2).

Matches Alembic migration ``0010_hud_sse_and_policy``.
All keys are tenant-scoped (DG-1 Amendment A1) with zero added foreign keys so the 19-FK
catalog invariant remains unchanged for parallel merge compatibility.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
    Table,
    UniqueConstraint,
    func,
)

from eap.db.tables import metadata

# 1. principal_grant_epochs: Server-side monotonic grant_epoch per (tenant_id, principal_id) (C28, C30)
principal_grant_epochs = Table(
    "principal_grant_epochs",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("principal_id", String(128), nullable=False),
    Column("grant_epoch", BigInteger, nullable=False, server_default="0"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "principal_id", name="uq_principal_grant_epochs_tenant_principal"),
    CheckConstraint("grant_epoch >= 0", name="ck_principal_grant_epochs_nonneg"),
    Index("ix_principal_grant_epochs_lookup", "tenant_id", "principal_id"),
)

# 2. principal_project_grants: Explicit project grant/revocation state per (tenant_id, principal_id, project_id)
principal_project_grants = Table(
    "principal_project_grants",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("principal_id", String(128), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("granted", Boolean, nullable=False, server_default="true"),
    Column("reason", String(256), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "principal_id", "project_id", name="uq_principal_project_grants_scope"),
    Index("ix_principal_project_grants_lookup", "tenant_id", "principal_id", "project_id"),
)

# 3. stream_retention_floors: Minimum retained ingest_offset per (tenant_id, stream_id) (C09 retention gap)
stream_retention_floors = Table(
    "stream_retention_floors",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("stream_id", String(128), nullable=False),
    Column("min_retained_offset", BigInteger, nullable=False, server_default="1"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "stream_id", name="uq_stream_retention_floors_stream"),
    CheckConstraint("min_retained_offset >= 1", name="ck_stream_retention_floors_min_pos"),
    Index("ix_stream_retention_floors_lookup", "tenant_id", "stream_id"),
)

__all__ = [
    "principal_grant_epochs",
    "principal_project_grants",
    "stream_retention_floors",
]
