"""SQLAlchemy Core definitions for PPM Scan Scheduler tables (P2a-WP5, DG-2).

Matches Alembic migration ``0009_scan_scheduler``.
Preserves ``ppm_scan_schedules`` column parity with ``migrations/draft/0001_core.sql``
while persisting per-source observability and deterministic occurrence deduplication.
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

from eap.db.tables import metadata

# 1. ppm_scan_source_status: Per-source observability state readable by HUD / API
ppm_scan_source_status = Table(
    "ppm_scan_source_status",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("last_scan_started_at", DateTime(timezone=True), nullable=True),
    Column("last_successful_scan_at", DateTime(timezone=True), nullable=True),
    Column("last_error", Text, nullable=True),
    Column("last_occurrence_id", String(128), nullable=True),
    Column("last_trigger", String(32), nullable=True),
    Column("last_task_id", String(64), nullable=True),
    Column("last_run_id", String(64), nullable=True),
    Column("last_snapshot_id", String(64), nullable=True),
    Column("last_fencing_token", BigInteger, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "id", name="uq_ppm_scan_source_status_tenant_id"),
    UniqueConstraint("tenant_id", "source_id", name="uq_ppm_scan_source_status_source"),
    ForeignKeyConstraint(
        ["tenant_id", "source_id"],
        ["sources.tenant_id", "sources.source_id"],
        name="fk_ppm_scan_source_status_source",
        ondelete="RESTRICT",
    ),
)

# 2. ppm_scan_occurrences: Deterministic trigger occurrence journal (no-op on redelivery)
ppm_scan_occurrences = Table(
    "ppm_scan_occurrences",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("occurrence_id", String(128), nullable=False),
    Column("trigger", String(32), nullable=False),
    Column("status", String(32), nullable=False, server_default="running"),
    Column("fencing_token", BigInteger, nullable=False),
    Column("task_id", String(64), nullable=True),
    Column("run_id", String(64), nullable=True),
    Column("snapshot_id", String(64), nullable=True),
    Column("error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("tenant_id", "id", name="uq_ppm_scan_occurrences_tenant_id"),
    UniqueConstraint("tenant_id", "source_id", "occurrence_id", name="uq_ppm_scan_occurrences_source_occ"),
    ForeignKeyConstraint(
        ["tenant_id", "source_id"],
        ["sources.tenant_id", "sources.source_id"],
        name="fk_ppm_scan_occurrences_source",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "trigger IN ('startup', 'interval', 'watcher', 'manual')",
        name="ck_ppm_scan_occurrences_trigger",
    ),
    CheckConstraint(
        "status IN ('running', 'succeeded', 'failed')",
        name="ck_ppm_scan_occurrences_status",
    ),
    Index("ix_ppm_scan_occurrences_lookup", "tenant_id", "source_id", "created_at"),
)

__all__ = [
    "ppm_scan_occurrences",
    "ppm_scan_source_status",
]
