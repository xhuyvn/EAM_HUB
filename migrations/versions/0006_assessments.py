"""PPM Assessment tables: baselines, evidence, waivers, basis_seq, assessments (P2a-WP4).

Revision ID: 0006_assessments
Revises: 0005_ingest
Create Date: 2026-11-15

P2a-WP4 (docs/04, DG-1 Amendment A1, DG-2 §3-4):
- ppm_baselines: Proposed (draft) and owner-approved baselines.
- ppm_evidence: Test and probe execution results with provenance & simulated flag.
- ppm_waivers: Approved criterion waivers.
- ppm_basis_seq: Monotonic assessment basis_seq counter per (tenant, project).
- ppm_assessments: Immutable assessment history rows (no UPDATE path).

All uniqueness targets and foreign keys are tenant-scoped (DG-1 Amendment A1).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_assessments"
down_revision: str | None = "0005_ingest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. ppm_baselines
    op.create_table(
        "ppm_baselines",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("baseline_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column(
            "criteria",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ppm_baselines_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "baseline_revision",
            name="uq_ppm_baselines_tenant_project_rev",
        ),
        sa.CheckConstraint("status IN ('draft', 'approved')", name="ck_ppm_baselines_status"),
        sa.CheckConstraint("baseline_revision >= 1", name="ck_ppm_baselines_rev_pos"),
    )

    # 2. ppm_evidence
    op.create_table(
        "ppm_evidence",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("evidence_id", sa.String(length=128), nullable=False),
        sa.Column("trust_class", sa.String(length=32), nullable=False),
        sa.Column("producer", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.String(length=128), nullable=True),
        sa.Column("test_id", sa.String(length=128), nullable=True),
        sa.Column(
            "covered_test_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=True),
        sa.Column("simulated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ppm_evidence_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "evidence_id",
            name="uq_ppm_evidence_tenant_project_ev",
        ),
        sa.CheckConstraint(
            "trust_class IN ('verifier_observed', 'ci_attested', 'reported_artifact', 'none')",
            name="ck_ppm_evidence_trust_class",
        ),
        sa.CheckConstraint(
            "outcome IN ('passed', 'failed', 'inconclusive', 'not_run')",
            name="ck_ppm_evidence_outcome",
        ),
    )
    op.create_index("ix_ppm_evidence_tenant_project", "ppm_evidence", ["tenant_id", "project_id"])

    # 3. ppm_waivers
    op.create_table(
        "ppm_waivers",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("waiver_id", sa.String(length=128), nullable=False),
        sa.Column("criterion_id", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=128), server_default="criterion", nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ppm_waivers_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "waiver_id",
            name="uq_ppm_waivers_tenant_project_wid",
        ),
    )
    op.create_index("ix_ppm_waivers_tenant_project", "ppm_waivers", ["tenant_id", "project_id"])

    # 4. ppm_basis_seq
    op.create_table(
        "ppm_basis_seq",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("last_basis_seq", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id", "project_id", name="pk_ppm_basis_seq"),
    )

    # 5. ppm_assessments
    op.create_table(
        "ppm_assessments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("basis_seq", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_seq", sa.BigInteger(), nullable=False),
        sa.Column("project_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("baseline_revision", sa.Integer(), nullable=True),
        sa.Column("assessed_revision", sa.String(length=128), nullable=True),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("inputs_hash", sa.String(length=64), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "findings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("analysis_status", sa.String(length=32), server_default="complete", nullable=False),
        sa.Column("previous_assessment_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ppm_assessments_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "basis_seq",
            name="uq_ppm_assessments_tenant_project_basis",
        ),
        sa.CheckConstraint(
            "trigger IN ('snapshot', 'baseline_changed', 'evidence_ttl_expired', 'model_recovered', 'manual')",
            name="ck_ppm_assessments_trigger",
        ),
        sa.CheckConstraint(
            "analysis_status IN ('complete', 'model_failed', 'skipped')",
            name="ck_ppm_assessments_analysis_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_snapshot_id"],
            ["project_snapshots.tenant_id", "project_snapshots.id"],
            name="fk_ppm_assessments_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "previous_assessment_id"],
            ["ppm_assessments.tenant_id", "ppm_assessments.id"],
            name="fk_ppm_assessments_previous",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_ppm_assessments_tenant_project_created",
        "ppm_assessments",
        ["tenant_id", "project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ppm_assessments_tenant_project_created", table_name="ppm_assessments")
    op.drop_table("ppm_assessments")
    op.drop_table("ppm_basis_seq")
    op.drop_index("ix_ppm_waivers_tenant_project", table_name="ppm_waivers")
    op.drop_table("ppm_waivers")
    op.drop_index("ix_ppm_evidence_tenant_project", table_name="ppm_evidence")
    op.drop_table("ppm_evidence")
    op.drop_table("ppm_baselines")
