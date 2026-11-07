"""Outbox event builder and row formatting helper.

Shared contract with P1a-WP3:
- outbox.payload = one event envelope validating against contracts/event.schema.json
  via eap.contracts models (contract_version='1.1', event_id, event_type, tenant_id,
  task_id, run_id, attempt, producer_id, producer_seq, occurred_at, payload).
- outbox.stream_id = "run:<run_id>" (or task:<task_id> / tenant:<tenant_id>).
- outbox explicitly has NO offset column (assigned strictly post-commit by relay per ADR-020).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from eap.contracts.models.event import Event, EventType


def build_event(
    *,
    event_type: EventType,
    tenant_id: str,
    payload: dict[str, Any],
    task_id: str | None = None,
    run_id: str | None = None,
    attempt: int | None = None,
    producer_id: str = "run_service",
    producer_seq: int = 1,
    occurred_at: str | dt.datetime | None = None,
    event_id: str | None = None,
) -> Event:
    """Build and validate an event envelope conforming to contracts/event.schema.json."""
    if event_id is None:
        event_id = f"evt-{uuid.uuid4().hex}"

    if occurred_at is None:
        occurred_at = dt.datetime.now(dt.UTC).isoformat()
    elif isinstance(occurred_at, dt.datetime):
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=dt.UTC)
        occurred_at = occurred_at.isoformat()

    envelope = {
        "contract_version": "1.1",
        "event_id": event_id,
        "tenant_id": tenant_id,
        "task_id": task_id,
        "run_id": run_id,
        "attempt": attempt,
        "producer_id": producer_id,
        "producer_seq": producer_seq,
        "occurred_at": occurred_at,
        "event_type": event_type,
        "payload": payload,
    }
    return Event.model_validate(envelope)


def create_outbox_record(event: Event, stream_id: str | None = None) -> dict[str, Any]:
    """Format a database outbox row dictionary from a validated Event."""
    if stream_id is None:
        if event.run_id:
            stream_id = f"run:{event.run_id}"
        elif event.task_id:
            stream_id = f"task:{event.task_id}"
        else:
            stream_id = f"tenant:{event.tenant_id}"

    return {
        "tenant_id": event.tenant_id,
        "event_id": event.event_id,
        "stream_id": stream_id,
        "event_type": event.event_type,
        "payload": event.model_dump(mode="json"),
    }
