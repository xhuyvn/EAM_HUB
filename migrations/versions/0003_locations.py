"""Location Service tables and identity conflict tracking (P2a-WP1).

Revision ID: 0003_locations
Revises: 0002_task_run
Create Date: 2026-11-08

P2a-WP1 (docs/04, DG-1 section 6, C32, C42, Amendment A1):
- location_registrations: Persisted trusted host location registration records (proposed -> active).
- identity_conflicts: Tracks duplicate project_id claims from divergent repo identities (C42).

All keys are tenant-scoped (DG-1 Amendment A1).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_locations"
down_revision: str | None = "0002_task_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "location_registrations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("metadata_root", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="proposed", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("registered_by_ref", sa.String(length=128), nullable=False),
        sa.Column("approved_by_ref", sa.String(length=128), nullable=True),
        sa.Column("host_id", sa.String(length=64), nullable=False),
        sa.Column("connector_id", sa.String(length=64), nullable=False),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("scan_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("artifact_aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes_globs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("deny_globs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("classification", sa.String(length=32), nullable=False),
        sa.Column("egress_policy_ref", sa.String(length=128), nullable=False),
        sa.Column("identity", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("follow_symlinks", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("allow_hardlinked_files", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("max_file_bytes", sa.Integer(), server_default="2097152", nullable=False),
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
        sa.UniqueConstraint("tenant_id", "id", name="uq_location_registrations_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "metadata_root",
            name="uq_location_registrations_source_metadata",
        ),
        sa.CheckConstraint(
            "state IN ('proposed', 'active', 'suspended', 'retired')",
            name="ck_location_registrations_state",
        ),
    )

    op.create_table(
        "identity_conflicts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("location_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("repo_identity", sa.String(length=128), nullable=False),
        sa.Column("conflicting_source_id", sa.String(length=64), nullable=False),
        sa.Column("conflicting_repo_identity", sa.String(length=128), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_identity_conflicts_tenant_id"),
    )

    # 3. host_root_ownership: Host-scoped root ownership table (L3 Owner decision 2026-11-08)
    # Intentionally not tenant-scoped (models shared physical host filesystem resource).
    # Has no FK to tenant data; DG-1 Amendment A1 unaffected.
    op.create_table(
        "host_root_ownership",
        sa.Column("host_id", sa.String(length=64), nullable=False),
        sa.Column("canonical_root_key", sa.String(length=512), nullable=False),
        sa.Column("canonical_root", sa.Text(), nullable=False),
        sa.Column("owner_tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_source_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("host_id", "canonical_root_key", name="pk_host_root_ownership"),
    )

    # 4. source_shares: Cross-tenant folder sharing requests and approvals (L3)
    op.create_table(
        "source_shares",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("host_id", sa.String(length=64), nullable=False),
        sa.Column("canonical_root_key", sa.String(length=512), nullable=False),
        sa.Column("owner_tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_source_id", sa.String(length=64), nullable=False),
        sa.Column("requester_tenant_id", sa.String(length=64), nullable=False),
        sa.Column("requester_source_id", sa.String(length=64), nullable=False),
        sa.Column("requester_principal_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="pending_share", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_principal_id", sa.String(length=128), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_principal_id", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "requester_tenant_id",
            "requester_source_id",
            name="uq_source_shares_requester_source",
        ),
        sa.CheckConstraint(
            "state IN ('pending_share', 'approved', 'rejected', 'revoked')",
            name="ck_source_shares_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("source_shares")
    op.drop_table("host_root_ownership")
    op.drop_table("identity_conflicts")
    op.drop_table("location_registrations")
