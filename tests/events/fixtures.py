"""Test fixture helpers for building valid event envelopes and outbox rows (P1a-WP3).

Builds event envelopes strictly conforming to contracts/event.schema.json
using eap.contracts.models without importing any P1a-WP2 code.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from eap.contracts.models.event import Event, EventType


def make_event_envelope(
    *,
    event_id: str,
    tenant_id: str,
    event_type: EventType,
    stream_id: str,
    producer_id: str = "worker-default",
    producer_seq: int = 1,
    occurred_at: str | None = None,
    payload: dict[str, Any] | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    attempt: int | None = 1,
) -> dict[str, Any]:
    """Construct and validate a producer event envelope dictionary conforming to event.schema.json."""
    if occurred_at is None:
        occurred_at = dt.datetime.now(dt.UTC).isoformat()

    # Default run_id and task_id from stream_id if not provided
    if run_id is None and stream_id.startswith("run:"):
        run_id = stream_id[len("run:") :]
    if task_id is None:
        task_id = "task-001"

    if payload is None:
        if event_type == "run.started":
            payload = {"execution_ref": "exec-default", "agent_version": "1.0.0"}
        elif event_type == "run.progress":
            payload = {
                "stage": "Working",
                "completed_units": 5,
                "total_units": 10,
                "unit": "steps",
            }
        elif event_type == "run.state_changed":
            payload = {
                "from_state": "queued",
                "to_state": "running",
                "reason_code": "DISPATCHED",
                "evidence_ref": None,
            }
        elif event_type == "run.waiting_approval":
            payload = {
                "approval_id": "appr-001",
                "action_digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "expires_at": (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).isoformat(),
            }
        elif event_type == "run.finished":
            payload = {"outcome": "succeeded", "result_ref": "res-001"}
        elif event_type == "worker.heartbeat":
            payload = {"worker_id": producer_id, "observed_state": "running"}
        elif event_type == "usage.reported":
            payload = {
                "provider_usage_id": f"usage-{event_id}",
                "usage_scope": "call",
                "cost_status": "actual",
                "cost_usd": 0.05,
                "input_tokens": 100,
                "output_tokens": 50,
            }
        elif event_type == "ppm.assessment.updated":
            payload = {
                "project_id": "proj-001",
                "assessment_id": "asmt-001",
                "baseline_revision": 1,
                "verification": "verified",
                "validity": "current",
                "acceptance": "not_requested",
            }
        elif event_type == "connector.status":
            payload = {
                "connector_id": "conn-001",
                "connectivity": "healthy",
                "last_observed_at": occurred_at,
            }
        else:
            payload = {}

    model = Event(
        contract_version="1.1",
        event_id=event_id,
        tenant_id=tenant_id,
        task_id=task_id,
        run_id=run_id,
        attempt=attempt,
        producer_id=producer_id,
        producer_seq=producer_seq,
        occurred_at=occurred_at,
        event_type=event_type,
        payload=payload,
    )
    dumped: dict[str, Any] = model.model_dump(mode="json")
    return dumped


__all__ = [
    "make_event_envelope",
]
