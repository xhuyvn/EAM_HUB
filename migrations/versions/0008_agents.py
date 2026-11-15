"""Agent inventory tables: agents, agent_bindings (P3a-WP5, ISS-037).

Revision ID: 0008_agents
Revises: 0007_verifier_evidence
Create Date: 2026-11-15

Notes:
- Revision ``0008_agents`` avoids collision with parallel session ``P2a-WP9`` which
  uses ``0007_verifier_evidence``; owner renumbers/re-links at merge if needed.
- Run attribution: core table ``runs`` (``0001_core``) already defines
  ``binding_id VARCHAR(64) NOT NULL`` and ``task_id -> tasks(tenant_id, id)``
  (with ``tasks.agent_id``). Core table column parity is preserved against
  ``migrations/draft/0001_core.sql``. ``TaskService.create`` resolves the active
  (non-revoked) binding for ``(tenant_id, agent_id)`` when present; any existing
  or ad-hoc ``runs`` row whose ``binding_id`` does not match a registered
  ``agent_bindings`` row is reported as ``"unattributed"`` rather than
  backfilled destructively.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_agents"
down_revision: str | None = "0007_verifier_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. agents
    op.create_table(
        "agents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("domain", sa.String(length=128), nullable=False),
        sa.Column("owner_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("runtime_kind", sa.String(length=32), nullable=False),
        sa.Column("task_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), server_default="proposed", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_agents_tenant_id"),
        sa.UniqueConstraint("tenant_id", "agent_id", name="uq_agents_tenant_agent_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "manifest_sha256",
            name="uq_agents_tenant_manifest_sha256",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'active', 'retired')",
            name="ck_agents_status",
        ),
        sa.CheckConstraint(
            "runtime_kind IN ('native', 'http', 'cli', 'vendor_sdk')",
            name="ck_agents_runtime_kind",
        ),
    )
    op.create_index("ix_agents_tenant_status", "agents", ["tenant_id", "status"])

    # 2. agent_bindings
    op.create_table(
        "agent_bindings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("binding_id", sa.String(length=128), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("release_version", sa.String(length=64), nullable=False),
        sa.Column("release_sha256", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("execution_boundary", sa.String(length=32), nullable=False),
        sa.Column("certified_capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("capability_evidence_ref", sa.Text(), nullable=False),
        sa.Column("egress_policy_ref", sa.Text(), nullable=False),
        sa.Column("secret_ref", sa.Text(), nullable=True),
        sa.Column("endpoint_ref", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_agent_bindings_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "binding_id",
            name="uq_agent_bindings_tenant_binding_id",
        ),
        sa.CheckConstraint(
            "mode IN ('observe', 'managed', 'governed')",
            name="ck_agent_bindings_mode",
        ),
        sa.CheckConstraint(
            "execution_boundary IN ('platform_gateway', 'sandboxed', 'external_trusted')",
            name="ck_agent_bindings_boundary",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            ["agents.tenant_id", "agents.agent_id"],
            name="fk_agent_bindings_tenant_agent",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_agent_bindings_tenant_agent", "agent_bindings", ["tenant_id", "agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_bindings_tenant_agent", table_name="agent_bindings")
    op.drop_table("agent_bindings")
    op.drop_index("ix_agents_tenant_status", table_name="agents")
    op.drop_table("agents")
