"""SQLAlchemy Core definitions for the relay / ingestion / projection tables (P1a-WP3).

Matches Alembic migration ``0002_events``. These tables share the ``metadata`` object of
``eap.db.tables`` so that autogenerate comparison covers them.

Tenant scoping (DG-1 Amendment A1): every table carries ``tenant_id NOT NULL`` and every
primary key / uniqueness target is tenant-scoped. No foreign keys are declared on purpose:
the 7-FK catalog invariant of P1a-WP1 stays untouched and the references to ``outbox`` /
``event_log`` rows are enforced by the single relay transaction (never by cross-table FKs
to tables whose key is not tenant-scoped).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from eap.db.tables import metadata

# 1. outbox_relay_marks: which outbox rows the relay has processed (ADR-020 relay mark).
#    The outbox itself stays append-only and offset-free; relaying never mutates it.
outbox_relay_marks = Table(
    "outbox_relay_marks",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("outbox_id", BigInteger, nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("event_id", String(64), nullable=False),
    Column("stream_id", String(128), nullable=False),
    Column("ingest_offset", BigInteger, nullable=True),
    Column("relayed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("tenant_id", "outbox_id", name="pk_outbox_relay_marks"),
    UniqueConstraint("outbox_id", name="uq_outbox_relay_marks_outbox_id"),
    CheckConstraint(
        "outcome IN ('ingested', 'duplicate', 'quarantined')",
        name="ck_outbox_relay_marks_outcome",
    ),
)

# 2. stream_heads: per (tenant, stream) offset head and producer sequence / gap state.
stream_heads = Table(
    "stream_heads",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("stream_id", String(128), nullable=False),
    Column("last_offset", BigInteger, nullable=False, server_default="0"),
    Column("producer_state", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("tenant_id", "stream_id", name="pk_stream_heads"),
    CheckConstraint("last_offset >= 0", name="ck_stream_heads_offset"),
)

# 3. event_quarantine: events rejected by ingestion checks (C02, C23); never projected.
event_quarantine = Table(
    "event_quarantine",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("outbox_id", BigInteger, nullable=False),
    Column("event_id", String(64), nullable=False),
    Column("stream_id", String(128), nullable=False),
    Column("reason", String(48), nullable=False),
    Column("detail", JSONB, nullable=False),
    Column("envelope", JSONB, nullable=False),
    Column("quarantined_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_event_quarantine_tenant_id"),
    UniqueConstraint("tenant_id", "outbox_id", name="uq_event_quarantine_outbox"),
    Index("ix_event_quarantine_tenant_event", "tenant_id", "event_id"),
)

# 4. security_events: security-relevant ingestion findings (C23, C02).
security_events = Table(
    "security_events",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("kind", String(48), nullable=False),
    Column("subject_event_id", String(64), nullable=True),
    Column("stream_id", String(128), nullable=True),
    Column("detail", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_security_events_tenant_id"),
    Index("ix_security_events_tenant_kind", "tenant_id", "kind"),
)

# 5. projection_checkpoints: versioned projection state per (tenant, stream, projection).
projection_checkpoints = Table(
    "projection_checkpoints",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("stream_id", String(128), nullable=False),
    Column("projection_name", String(64), nullable=False),
    Column("projection_version", Integer, nullable=False),
    Column("last_ingest_offset", BigInteger, nullable=False),
    Column("state", JSONB, nullable=False),
    Column("state_hash", String(64), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint(
        "tenant_id", "stream_id", "projection_name", name="pk_projection_checkpoints"
    ),
    CheckConstraint("last_ingest_offset >= 0", name="ck_projection_checkpoints_offset"),
)

__all__ = [
    "event_quarantine",
    "outbox_relay_marks",
    "projection_checkpoints",
    "security_events",
    "stream_heads",
]
