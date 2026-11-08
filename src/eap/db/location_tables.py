"""SQLAlchemy Core definitions for Location Service tables (P2a-WP1).

Matches Alembic migration ``0003_locations``.
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
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from eap.db.tables import metadata

# 1. location_registrations: Persisted trusted host location registration records (P2a-WP1)
location_registrations = Table(
    "location_registrations",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("metadata_root", Text, nullable=False),
    Column("state", String(32), nullable=False, server_default="proposed"),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("registered_by_ref", String(128), nullable=False),
    Column("approved_by_ref", String(128), nullable=True),
    Column("host_id", String(64), nullable=False),
    Column("connector_id", String(64), nullable=False),
    Column("root_path", Text, nullable=False),
    Column("scan_scope", JSONB, nullable=False),
    Column("artifact_aliases", JSONB, nullable=False),
    Column("notes_globs", JSONB, nullable=False),
    Column("deny_globs", JSONB, nullable=False),
    Column("classification", String(32), nullable=False),
    Column("egress_policy_ref", String(128), nullable=False),
    Column("identity", JSONB, nullable=False),
    Column("follow_symlinks", Boolean, nullable=False, server_default="false"),
    Column("allow_hardlinked_files", Boolean, nullable=False, server_default="false"),
    Column("max_file_bytes", Integer, nullable=False, server_default="2097152"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_location_registrations_tenant_id"),
    UniqueConstraint("tenant_id", "source_id", "metadata_root", name="uq_location_registrations_source_metadata"),
    CheckConstraint(
        "state IN ('proposed', 'active', 'suspended', 'retired')",
        name="ck_location_registrations_state",
    ),
)

# 2. identity_conflicts: Records duplicate project_ids claimed by divergent repo identities (C42)
identity_conflicts = Table(
    "identity_conflicts",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("location_id", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("repo_identity", String(128), nullable=False),
    Column("conflicting_source_id", String(64), nullable=False),
    Column("conflicting_repo_identity", String(128), nullable=False),
    Column("detail", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_identity_conflicts_tenant_id"),
)

# 3. host_root_ownership: Host-level canonical root ownership for cross-tenant sharing (L3)
# Note: Intentionally NOT tenant-scoped in primary key because it models a shared physical
# host filesystem resource across tenants on a single host. Has no foreign keys to tenant data
# so DG-1 Amendment A1 is unaffected.
host_root_ownership = Table(
    "host_root_ownership",
    metadata,
    Column("host_id", String(64), primary_key=True),
    Column("canonical_root_key", String(512), primary_key=True),
    Column("canonical_root", Text, nullable=False),
    Column("owner_tenant_id", String(64), nullable=False),
    Column("owner_source_id", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# 4. source_shares: Cross-tenant folder sharing requests and approvals (L3)
source_shares = Table(
    "source_shares",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("host_id", String(64), nullable=False),
    Column("canonical_root_key", String(512), nullable=False),
    Column("owner_tenant_id", String(64), nullable=False),
    Column("owner_source_id", String(64), nullable=False),
    Column("requester_tenant_id", String(64), nullable=False),
    Column("requester_source_id", String(64), nullable=False),
    Column("requester_principal_id", String(128), nullable=False),
    Column("state", String(32), nullable=False, server_default="pending_share"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("decided_at", DateTime(timezone=True), nullable=True),
    Column("decided_by_principal_id", String(128), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("revoked_by_principal_id", String(128), nullable=True),
    UniqueConstraint("requester_tenant_id", "requester_source_id", name="uq_source_shares_requester_source"),
    CheckConstraint(
        "state IN ('pending_share', 'approved', 'rejected', 'revoked')",
        name="ck_source_shares_state",
    ),
)

__all__ = [
    "host_root_ownership",
    "identity_conflicts",
    "location_registrations",
    "source_shares",
]
