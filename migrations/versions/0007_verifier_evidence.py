"""P2a-WP9 verifier columns on ppm_evidence (exit_status, duration_ms, network_isolation, reason).

Revision ID: 0007_verifier_evidence
Revises: 0006_assessments
Create Date: 2026-11-15 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_verifier_evidence"
down_revision: str | None = "0006_assessments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ppm_evidence", sa.Column("exit_status", sa.Integer(), nullable=True))
    op.add_column("ppm_evidence", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column(
        "ppm_evidence",
        sa.Column("network_isolation", sa.String(length=32), nullable=True),
    )
    op.add_column("ppm_evidence", sa.Column("reason", sa.String(length=128), nullable=True))
    op.create_check_constraint(
        "ck_ppm_evidence_duration_nonneg",
        "ppm_evidence",
        "duration_ms IS NULL OR duration_ms >= 0",
    )
    op.create_check_constraint(
        "ck_ppm_evidence_network_isolation",
        "ppm_evidence",
        "network_isolation IS NULL OR network_isolation IN ('enforced', 'not_enforced')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ppm_evidence_network_isolation", "ppm_evidence", type_="check")
    op.drop_constraint("ck_ppm_evidence_duration_nonneg", "ppm_evidence", type_="check")
    op.drop_column("ppm_evidence", "reason")
    op.drop_column("ppm_evidence", "network_isolation")
    op.drop_column("ppm_evidence", "duration_ms")
    op.drop_column("ppm_evidence", "exit_status")
