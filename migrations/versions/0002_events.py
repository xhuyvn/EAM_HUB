"""Outbox relay, ingestion and projection tables.

Revision ID: 0002_events
Revises: 0001_core
Create Date: 2026-11-07

P1a-WP3 (ADR-020, DG-1 section 5, C02, C23, C33):
- outbox_relay_marks: which outbox rows the relay processed (outbox stays append-only)
- stream_heads: per (tenant, stream) offset head + per-producer sequence/gap state
- event_quarantine: ingestion rejects (C02 conflict, C23 tenant mismatch, ...)
- security_events: security findings raised by ingestion
- projection_checkpoints: versioned projection state per (tenant, stream, projection)

All keys are tenant-scoped (DG-1 Amendment A1). No foreign keys are introduced.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_events"
down_revision: str | None = "0001_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_relay_marks",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("outbox_id", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("stream_id", sa.String(length=128), nullable=False),
        sa.Column("ingest_offset", sa.BigInteger(), nullable=True),
        sa.Column(
            "relayed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id", "outbox_id", name="pk_outbox_relay_marks"),
        sa.UniqueConstraint("outbox_id", name="uq_outbox_relay_marks_outbox_id"),
        sa.CheckConstraint(
            "outcome IN ('ingested', 'duplicate', 'quarantined')",
            name="ck_outbox_relay_marks_outcome",
        ),
    )

    op.create_table(
        "stream_heads",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("stream_id", sa.String(length=128), nullable=False),
        sa.Column("last_offset", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "producer_state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id", "stream_id", name="pk_stream_heads"),
        sa.CheckConstraint("last_offset >= 0", name="ck_stream_heads_offset"),
    )

    op.create_table(
        "event_quarantine",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("outbox_id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("stream_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=48), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("envelope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "quarantined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_event_quarantine_tenant_id"),
        sa.UniqueConstraint("tenant_id", "outbox_id", name="uq_event_quarantine_outbox"),
    )
    op.create_index(
        "ix_event_quarantine_tenant_event", "event_quarantine", ["tenant_id", "event_id"]
    )

    op.create_table(
        "security_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("subject_event_id", sa.String(length=64), nullable=True),
        sa.Column("stream_id", sa.String(length=128), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_security_events_tenant_id"),
    )
    op.create_index(
        "ix_security_events_tenant_kind", "security_events", ["tenant_id", "kind"]
    )

    op.create_table(
        "projection_checkpoints",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("stream_id", sa.String(length=128), nullable=False),
        sa.Column("projection_name", sa.String(length=64), nullable=False),
        sa.Column("projection_version", sa.Integer(), nullable=False),
        sa.Column("last_ingest_offset", sa.BigInteger(), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "stream_id", "projection_name", name="pk_projection_checkpoints"
        ),
        sa.CheckConstraint("last_ingest_offset >= 0", name="ck_projection_checkpoints_offset"),
    )


def downgrade() -> None:
    op.drop_table("projection_checkpoints")
    op.drop_index("ix_security_events_tenant_kind", table_name="security_events")
    op.drop_table("security_events")
    op.drop_index("ix_event_quarantine_tenant_event", table_name="event_quarantine")
    op.drop_table("event_quarantine")
    op.drop_table("stream_heads")
    op.drop_table("outbox_relay_marks")
