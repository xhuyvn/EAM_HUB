"""SQLAlchemy Core definitions for PPM Assessment tables (P2a-WP4).

Matches Alembic migration ``0006_assessments``.
Tenant scoping (DG-1 Amendment A1): every table carries ``tenant_id NOT NULL``,
every uniqueness target is tenant-scoped, and every foreign key is a composite
key including ``tenant_id``.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
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

# 1. ppm_baselines: Proposed (draft) and owner-approved baselines per (tenant, project, baseline_revision)
ppm_baselines = Table(
    "ppm_baselines",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("baseline_revision", Integer, nullable=False),
    Column("status", String(32), nullable=False, server_default="draft"),
    Column("criteria", JSONB, nullable=False, server_default="[]"),
    Column("source", Text, nullable=False),
    Column("approved_by", String(128), nullable=True),
    Column("approved_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_ppm_baselines_tenant_id"),
    UniqueConstraint(
        "tenant_id",
        "project_id",
        "baseline_revision",
        name="uq_ppm_baselines_tenant_project_rev",
    ),
    CheckConstraint(
        "status IN ('draft', 'approved')",
        name="ck_ppm_baselines_status",
    ),
    CheckConstraint(
        "baseline_revision >= 1",
        name="ck_ppm_baselines_rev_pos",
    ),
)

# 2. ppm_evidence: Test and probe execution results with provenance & simulated flag
ppm_evidence = Table(
    "ppm_evidence",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("evidence_id", String(128), nullable=False),
    Column("trust_class", String(32), nullable=False),
    Column("producer", String(128), nullable=False),
    Column("revision", String(128), nullable=True),
    Column("test_id", String(128), nullable=True),
    Column("covered_test_ids", JSONB, nullable=False, server_default="[]"),
    Column("outcome", String(32), nullable=False),
    Column("collected_at", DateTime(timezone=True), nullable=False),
    Column("output_hash", String(64), nullable=False),
    Column("artifact_path", Text, nullable=True),
    Column("simulated", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_ppm_evidence_tenant_id"),
    UniqueConstraint(
        "tenant_id",
        "project_id",
        "evidence_id",
        name="uq_ppm_evidence_tenant_project_ev",
    ),
    CheckConstraint(
        "trust_class IN ('verifier_observed', 'ci_attested', 'reported_artifact', 'none')",
        name="ck_ppm_evidence_trust_class",
    ),
    CheckConstraint(
        "outcome IN ('passed', 'failed', 'inconclusive', 'not_run')",
        name="ck_ppm_evidence_outcome",
    ),
    Index("ix_ppm_evidence_tenant_project", "tenant_id", "project_id"),
)

# 3. ppm_waivers: Approved criterion waivers
ppm_waivers = Table(
    "ppm_waivers",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("waiver_id", String(128), nullable=False),
    Column("criterion_id", String(128), nullable=False),
    Column("scope", String(128), nullable=False, server_default="criterion"),
    Column("rationale", Text, nullable=False),
    Column("approved_by", String(128), nullable=True),
    Column("approved_at", DateTime(timezone=True), nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_ppm_waivers_tenant_id"),
    UniqueConstraint(
        "tenant_id",
        "project_id",
        "waiver_id",
        name="uq_ppm_waivers_tenant_project_wid",
    ),
    Index("ix_ppm_waivers_tenant_project", "tenant_id", "project_id"),
)

# 4. ppm_basis_seq: Monotonic assessment basis_seq counter per (tenant, project) (DG-2 §4.1)
ppm_basis_seq = Table(
    "ppm_basis_seq",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("last_basis_seq", BigInteger, nullable=False, server_default="0"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("tenant_id", "project_id", name="pk_ppm_basis_seq"),
)

# 5. ppm_assessments: Immutable assessment history rows (no UPDATE path)
ppm_assessments = Table(
    "ppm_assessments",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("basis_seq", BigInteger, nullable=False),
    Column("snapshot_seq", BigInteger, nullable=False),
    Column("project_snapshot_id", String(64), nullable=True),
    Column("baseline_revision", Integer, nullable=True),
    Column("assessed_revision", String(128), nullable=True),
    Column("inputs", JSONB, nullable=False),
    Column("inputs_hash", String(64), nullable=False),
    Column("output", JSONB, nullable=False),
    Column("findings", JSONB, nullable=False, server_default="[]"),
    Column("trigger", String(32), nullable=False),
    Column("analysis_status", String(32), nullable=False, server_default="complete"),
    Column("previous_assessment_id", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_ppm_assessments_tenant_id"),
    UniqueConstraint(
        "tenant_id",
        "project_id",
        "basis_seq",
        name="uq_ppm_assessments_tenant_project_basis",
    ),
    CheckConstraint(
        "trigger IN ('snapshot', 'baseline_changed', 'evidence_ttl_expired', 'model_recovered', 'manual')",
        name="ck_ppm_assessments_trigger",
    ),
    CheckConstraint(
        "analysis_status IN ('complete', 'model_failed', 'skipped')",
        name="ck_ppm_assessments_analysis_status",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "project_snapshot_id"],
        ["project_snapshots.tenant_id", "project_snapshots.id"],
        name="fk_ppm_assessments_snapshot",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "previous_assessment_id"],
        ["ppm_assessments.tenant_id", "ppm_assessments.id"],
        name="fk_ppm_assessments_previous",
        ondelete="RESTRICT",
    ),
    Index("ix_ppm_assessments_tenant_project_created", "tenant_id", "project_id", "created_at"),
)

__all__ = [
    "ppm_assessments",
    "ppm_baselines",
    "ppm_basis_seq",
    "ppm_evidence",
    "ppm_waivers",
]
