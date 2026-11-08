"""Event quarantine and security event recording (C02, C23)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from eap.db.event_tables import event_quarantine, security_events

# Standard quarantine reasons
REASON_TENANT_MISMATCH = "tenant_mismatch"
REASON_EVENT_CONTENT_CONFLICT = "event_content_conflict"
REASON_PRODUCER_UNAUTHORIZED = "producer_unauthorized"
REASON_SCHEMA_VIOLATION = "schema_violation"

# Standard security event kinds
KIND_TENANT_MISMATCH = "tenant_mismatch"
KIND_EVENT_CONTENT_CONFLICT = "event_content_conflict"
KIND_PRODUCER_UNAUTHORIZED = "producer_unauthorized"
KIND_PRODUCER_SEQ_GAP = "producer_seq_gap"


def record_quarantine(
    conn: sa.Connection,
    *,
    tenant_id: str,
    outbox_id: int,
    event_id: str,
    stream_id: str,
    reason: str,
    detail: dict[str, Any],
    envelope: dict[str, Any],
) -> int:
    """Record an invalid or conflicted event in the quarantine table."""
    stmt = (
        event_quarantine.insert()
        .values(
            tenant_id=tenant_id,
            outbox_id=outbox_id,
            event_id=event_id,
            stream_id=stream_id,
            reason=reason,
            detail=detail,
            envelope=envelope,
        )
        .returning(event_quarantine.c.id)
    )
    result = conn.execute(stmt)
    row = result.fetchone()
    return int(row[0]) if row else 0


def record_security_event(
    conn: sa.Connection,
    *,
    tenant_id: str,
    kind: str,
    subject_event_id: str | None,
    stream_id: str | None,
    detail: dict[str, Any],
) -> int:
    """Record a security finding associated with unauthorized or mismatched ingestion."""
    stmt = (
        security_events.insert()
        .values(
            tenant_id=tenant_id,
            kind=kind,
            subject_event_id=subject_event_id,
            stream_id=stream_id,
            detail=detail,
        )
        .returning(security_events.c.id)
    )
    result = conn.execute(stmt)
    row = result.fetchone()
    return int(row[0]) if row else 0


__all__ = [
    "KIND_EVENT_CONTENT_CONFLICT",
    "KIND_PRODUCER_UNAUTHORIZED",
    "KIND_TENANT_MISMATCH",
    "REASON_EVENT_CONTENT_CONFLICT",
    "REASON_PRODUCER_UNAUTHORIZED",
    "REASON_SCHEMA_VIOLATION",
    "REASON_TENANT_MISMATCH",
    "record_quarantine",
    "record_security_event",
]
