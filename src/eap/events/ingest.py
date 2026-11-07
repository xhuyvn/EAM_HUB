"""Ingestion checks, gap detection, and event append pipeline (C02, C23, C33)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy import select

from eap.contracts.digest import sha256_digest
from eap.db.event_tables import stream_heads
from eap.db.tables import event_log
from eap.events.binding import ProducerBinding
from eap.events.quarantine import (
    KIND_EVENT_CONTENT_CONFLICT,
    KIND_PRODUCER_UNAUTHORIZED,
    KIND_TENANT_MISMATCH,
    REASON_EVENT_CONTENT_CONFLICT,
    REASON_PRODUCER_UNAUTHORIZED,
    REASON_TENANT_MISMATCH,
    record_quarantine,
    record_security_event,
)

IngestStatus = Literal["ingested", "duplicate", "quarantined"]


@dataclass(frozen=True)
class IngestResult:
    """Outcome of an ingestion attempt."""

    status: IngestStatus
    event_id: str
    stream_id: str
    tenant_id: str
    ingest_offset: int | None = None
    reason: str | None = None
    gap_detected: bool = False
    detail: dict[str, Any] | None = None


def check_and_ingest_event(
    conn: sa.Connection,
    *,
    outbox_id: int,
    envelope: dict[str, Any],
    binding: ProducerBinding | None,
    stream_id: str,
) -> IngestResult:
    """Execute ingestion checks and append to event_log in the active transaction.

    Invariants:
    1. Producer authorization and tenant taken from binding, NEVER from event body.
    2. event tenant_id != binding tenant -> quarantine + security event (C23).
    3. same event_id different content -> quarantine + security event, no overwrite (C02).
    4. same event_id same content -> idempotent duplicate no-op.
    5. Gap detection per stream tracked in stream_heads.
    """
    event_id = envelope.get("event_id") or f"unknown-{outbox_id}"
    event_tenant = envelope.get("tenant_id") or "unknown"
    producer_id = envelope.get("producer_id") or "unknown"

    # Check 1: Producer Authorization
    if binding is None or not binding.can_emit_events():
        reason = REASON_PRODUCER_UNAUTHORIZED
        detail = {
            "producer_id": producer_id,
            "stream_id": stream_id,
            "has_binding": binding is not None,
        }
        record_quarantine(
            conn,
            tenant_id=event_tenant,
            outbox_id=outbox_id,
            event_id=event_id,
            stream_id=stream_id,
            reason=reason,
            detail=detail,
            envelope=envelope,
        )
        record_security_event(
            conn,
            tenant_id=event_tenant,
            kind=KIND_PRODUCER_UNAUTHORIZED,
            subject_event_id=event_id,
            stream_id=stream_id,
            detail=detail,
        )
        return IngestResult(
            status="quarantined",
            event_id=event_id,
            stream_id=stream_id,
            tenant_id=event_tenant,
            reason=reason,
            detail=detail,
        )

    # Check 2: Tenant Isolation Check (C23)
    authoritative_tenant = binding.tenant_id
    if event_tenant != authoritative_tenant:
        reason = REASON_TENANT_MISMATCH
        detail = {
            "binding_tenant": authoritative_tenant,
            "event_tenant": event_tenant,
            "producer_id": producer_id,
            "stream_id": stream_id,
        }
        record_quarantine(
            conn,
            tenant_id=authoritative_tenant,
            outbox_id=outbox_id,
            event_id=event_id,
            stream_id=stream_id,
            reason=reason,
            detail=detail,
            envelope=envelope,
        )
        record_security_event(
            conn,
            tenant_id=authoritative_tenant,
            kind=KIND_TENANT_MISMATCH,
            subject_event_id=event_id,
            stream_id=stream_id,
            detail=detail,
        )
        return IngestResult(
            status="quarantined",
            event_id=event_id,
            stream_id=stream_id,
            tenant_id=authoritative_tenant,
            reason=reason,
            detail=detail,
        )

    # Check 3: Idempotency & Conflict Check (C02)
    stmt_existing = select(
        event_log.c.ingest_offset,
        event_log.c.event_type,
        event_log.c.payload,
    ).where(
        event_log.c.tenant_id == authoritative_tenant,
        event_log.c.event_id == event_id,
    )
    existing_row = conn.execute(stmt_existing).fetchone()

    if existing_row is not None:
        existing_offset = int(existing_row[0])
        existing_type = str(existing_row[1])
        existing_payload_raw = existing_row[2]
        incoming_payload = envelope.get("payload") or {}

        if (
            isinstance(existing_payload_raw, dict)
            and "event_type" in existing_payload_raw
            and "payload" in existing_payload_raw
        ):
            actual_existing_payload = existing_payload_raw.get("payload") or {}
        else:
            actual_existing_payload = existing_payload_raw

        incoming_type = envelope.get("event_type")

        existing_hash = sha256_digest(actual_existing_payload)
        incoming_hash = sha256_digest(incoming_payload)

        if existing_type == incoming_type and existing_hash == incoming_hash:
            # Idempotent duplicate
            return IngestResult(
                status="duplicate",
                event_id=event_id,
                stream_id=stream_id,
                tenant_id=authoritative_tenant,
                ingest_offset=existing_offset,
                reason="idempotent_duplicate",
            )
        # Content conflict (C02)
        reason = REASON_EVENT_CONTENT_CONFLICT
        detail = {
            "existing_event_type": existing_type,
            "incoming_event_type": incoming_type,
            "existing_payload_hash": existing_hash,
            "incoming_payload_hash": incoming_hash,
            "existing_offset": existing_offset,
        }
        record_quarantine(
            conn,
            tenant_id=authoritative_tenant,
            outbox_id=outbox_id,
            event_id=event_id,
            stream_id=stream_id,
            reason=reason,
            detail=detail,
            envelope=envelope,
        )
        record_security_event(
            conn,
            tenant_id=authoritative_tenant,
            kind=KIND_EVENT_CONTENT_CONFLICT,
            subject_event_id=event_id,
            stream_id=stream_id,
            detail=detail,
        )
        return IngestResult(
            status="quarantined",
            event_id=event_id,
            stream_id=stream_id,
            tenant_id=authoritative_tenant,
            reason=reason,
            detail=detail,
        )

    # Check 4: Stream Head & Gap Detection
    stmt_head = (
        select(stream_heads.c.last_offset, stream_heads.c.producer_state)
        .where(
            stream_heads.c.tenant_id == authoritative_tenant,
            stream_heads.c.stream_id == stream_id,
        )
        .with_for_update()
    )
    head_row = conn.execute(stmt_head).fetchone()

    if head_row is None:
        # Initialize stream head
        conn.execute(
            sa.dialects.postgresql.insert(stream_heads).values(
                tenant_id=authoritative_tenant,
                stream_id=stream_id,
                last_offset=0,
                producer_state={},
            )
        )
        head_row = conn.execute(stmt_head).fetchone()

    current_offset = int(head_row[0]) if head_row else 0
    producer_state = dict(head_row[1] or {}) if head_row else {}

    seqs: dict[str, int] = producer_state.setdefault("seqs", {})
    producer_seq = envelope.get("producer_seq")
    gap_detected = False

    if producer_seq is not None:
        last_seq = seqs.get(producer_id)
        if last_seq is not None and producer_seq > last_seq + 1:
            gap_detected = True
            gaps: list[dict[str, Any]] = producer_state.setdefault("gaps", [])
            gaps.append(
                {
                    "producer_id": producer_id,
                    "expected_seq": last_seq + 1,
                    "received_seq": producer_seq,
                    "event_id": event_id,
                }
            )
        seqs[producer_id] = producer_seq

    # Append to event_log with strictly increasing ingest_offset
    next_offset = current_offset + 1

    conn.execute(
        event_log.insert().values(
            tenant_id=authoritative_tenant,
            stream_id=stream_id,
            ingest_offset=next_offset,
            event_id=event_id,
            producer_id=producer_id,
            event_type=envelope["event_type"],
            payload=envelope,
            occurred_at=envelope["occurred_at"],
        )
    )

    # Update stream head
    conn.execute(
        stream_heads.update()
        .where(
            stream_heads.c.tenant_id == authoritative_tenant,
            stream_heads.c.stream_id == stream_id,
        )
        .values(
            last_offset=next_offset,
            producer_state=producer_state,
            updated_at=sa.func.now(),
        )
    )

    return IngestResult(
        status="ingested",
        event_id=event_id,
        stream_id=stream_id,
        tenant_id=authoritative_tenant,
        ingest_offset=next_offset,
        gap_detected=gap_detected,
    )


__all__ = [
    "IngestResult",
    "IngestStatus",
    "check_and_ingest_event",
]
