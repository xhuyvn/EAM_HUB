"""Tests for eap.run.outbox.build_event validating against contracts/event.schema.json."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema  # type: ignore[import-untyped]
import pytest

from eap.run.outbox import build_event, create_outbox_record

ROOT = Path(__file__).resolve().parents[1]
EVENT_SCHEMA_PATH = ROOT / "contracts" / "event.schema.json"


@pytest.fixture(scope="module")
def event_validator() -> jsonschema.Draft202012Validator:
    schema_data = json.loads(EVENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema_data)


def test_build_event_state_changed_validates_against_schema(
    event_validator: jsonschema.Draft202012Validator,
) -> None:
    """build_event produces a run.state_changed envelope strictly matching event.schema.json."""
    event = build_event(
        tenant_id="tenant-alpha",
        task_id="task-001",
        run_id="run-001",
        attempt=1,
        event_type="run.state_changed",
        producer_id="run_service",
        producer_seq=1,
        payload={
            "from_state": "queued",
            "to_state": "dispatching",
            "reason_code": "dispatch_claimed",
            "evidence_ref": None,
        },
    )

    doc = event.model_dump(mode="json")
    event_validator.validate(doc)

    outbox_rec = create_outbox_record(event)
    assert outbox_rec["stream_id"] == "run:run-001"
    assert outbox_rec["tenant_id"] == "tenant-alpha"
    assert outbox_rec["event_id"] == event.event_id
    assert outbox_rec["event_type"] == "run.state_changed"
    assert "offset" not in outbox_rec["payload"]
    assert "ingest_offset" not in outbox_rec["payload"]


def test_build_event_run_started_validates_against_schema(
    event_validator: jsonschema.Draft202012Validator,
) -> None:
    """build_event produces a run.started envelope strictly matching event.schema.json."""
    event = build_event(
        tenant_id="tenant-beta",
        task_id="task-002",
        run_id="run-002",
        attempt=1,
        event_type="run.started",
        producer_id="adapter_mock",
        producer_seq=2,
        payload={
            "execution_ref": "exec-mock-123",
            "agent_version": "1.0.0",
        },
    )

    doc = event.model_dump(mode="json")
    event_validator.validate(doc)

    outbox_rec = create_outbox_record(event)
    assert outbox_rec["stream_id"] == "run:run-002"
