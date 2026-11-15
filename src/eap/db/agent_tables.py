"""SQLAlchemy Core definitions for Agent Inventory tables (P3a-WP5, ISS-037).

Matches Alembic migration ``0008_agents``.
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
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from eap.db.tables import metadata

# 1. agents: Validated agent manifests registered per tenant (ISS-037)
agents = Table(
    "agents",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("agent_id", String(128), nullable=False),
    Column("manifest", JSONB, nullable=False),
    Column("manifest_sha256", String(64), nullable=False),
    Column("name", String(256), nullable=False),
    Column("version", String(64), nullable=False),
    Column("domain", String(128), nullable=False),
    Column("owner_refs", JSONB, nullable=False),
    Column("runtime_kind", String(32), nullable=False),
    Column("task_types", JSONB, nullable=False),
    Column("registered_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("status", String(32), nullable=False, server_default="proposed"),
    UniqueConstraint("tenant_id", "id", name="uq_agents_tenant_id"),
    UniqueConstraint("tenant_id", "agent_id", name="uq_agents_tenant_agent_id"),
    UniqueConstraint("tenant_id", "manifest_sha256", name="uq_agents_tenant_manifest_sha256"),
    CheckConstraint(
        "status IN ('proposed', 'active', 'retired')",
        name="ck_agents_status",
    ),
    CheckConstraint(
        "runtime_kind IN ('native', 'http', 'cli', 'vendor_sdk')",
        name="ck_agents_runtime_kind",
    ),
    Index("ix_agents_tenant_status", "tenant_id", "status"),
)

# 2. agent_bindings: Trusted deployment bindings per tenant and agent
agent_bindings = Table(
    "agent_bindings",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("binding_id", String(128), nullable=False),
    Column("agent_id", String(128), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("release_version", String(64), nullable=False),
    Column("release_sha256", String(64), nullable=False),
    Column("mode", String(32), nullable=False),
    Column("execution_boundary", String(32), nullable=False),
    Column("certified_capabilities", JSONB, nullable=False),
    Column("capability_evidence_ref", Text, nullable=False),
    Column("egress_policy_ref", Text, nullable=False),
    Column("secret_ref", Text, nullable=True),
    Column("endpoint_ref", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("tenant_id", "id", name="uq_agent_bindings_tenant_id"),
    UniqueConstraint("tenant_id", "binding_id", name="uq_agent_bindings_tenant_binding_id"),
    CheckConstraint(
        "mode IN ('observe', 'managed', 'governed')",
        name="ck_agent_bindings_mode",
    ),
    CheckConstraint(
        "execution_boundary IN ('platform_gateway', 'sandboxed', 'external_trusted')",
        name="ck_agent_bindings_boundary",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "agent_id"],
        ["agents.tenant_id", "agents.agent_id"],
        name="fk_agent_bindings_tenant_agent",
        ondelete="RESTRICT",
    ),
    Index("ix_agent_bindings_tenant_agent", "tenant_id", "agent_id"),
)

__all__ = [
    "agent_bindings",
    "agents",
]
