"""Fixture parity and JCS round-trip tests (SC-01 / P0-WP2).

Verifies that every JSON fixture and event in examples/ and templates/ parses
cleanly with its matching model, and model_dump(mode="json", exclude_unset=True, by_alias=True)
equals the input after JCS normalization.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eap.contracts.digest import jcs
from eap.contracts.models import (
    ResearchInput,
    ResearchOutput,
    get_model_for_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

FIXTURES: dict[str, str] = {
    "templates/new-agent/agent.json": "agent",
    "templates/existing-agent/agent.json": "agent",
    "templates/admin/deployment-binding.example.json": "binding",
    "templates/admin/existing-observe-binding.example.json": "binding",
    "templates/actions/action-proposal.example.json": "action-proposal",
    "templates/phase2/experiment.example.json": "experiment",
    "examples/task-request.json": "task-request",
    "examples/task-envelope.json": "task-envelope",
    "examples/result.json": "result",
    "examples/ppm-input.json": "ppm-input",
    "examples/ppm-output.json": "ppm-output",
    "examples/hud-projection.json": "hud-projection",
    "examples/approval-decision.json": "approval-decision",
    "examples/location-registration.json": "location-registration",
    "examples/run-command.json": "run-command",
    "examples/action-proposal-retry.json": "action-proposal",
}


@pytest.mark.parametrize("rel_path,schema_name", list(FIXTURES.items()))
def test_fixture_round_trip(rel_path: str, schema_name: str) -> None:
    full_path = REPO_ROOT / rel_path
    raw_data = json.loads(full_path.read_text(encoding="utf-8"))
    model_cls = get_model_for_schema(schema_name)
    instance = model_cls.model_validate(raw_data)
    dumped = instance.model_dump(mode="json", exclude_unset=True, by_alias=True)

    raw_jcs = jcs(raw_data)
    dumped_jcs = jcs(dumped)
    assert raw_jcs == dumped_jcs, (
        f"Round-trip JCS equality failed for {rel_path}!\n"
        f"Raw JCS:    {raw_jcs.decode('utf-8')}\n"
        f"Dumped JCS: {dumped_jcs.decode('utf-8')}"
    )


def test_events_ndjson_round_trip() -> None:
    events_path = REPO_ROOT / "examples" / "events.ndjson"
    lines = [line.strip() for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 11, f"Expected 11 events, got {len(lines)}"
    event_model_cls = get_model_for_schema("event")

    for idx, line in enumerate(lines):
        raw_event = json.loads(line)
        event_obj = event_model_cls.model_validate(raw_event)
        dumped = event_obj.model_dump(mode="json", exclude_unset=True, by_alias=True)
        assert jcs(raw_event) == jcs(dumped), f"Event {idx} ({raw_event.get('event_type')}) JCS round-trip mismatch!"


def test_locations_monorepo_round_trip() -> None:
    monorepo_path = REPO_ROOT / "examples" / "locations-monorepo.json"
    raw_list = json.loads(monorepo_path.read_text(encoding="utf-8"))
    assert len(raw_list) == 3
    loc_model_cls = get_model_for_schema("location-registration")

    for idx, loc in enumerate(raw_list):
        loc_obj = loc_model_cls.model_validate(loc)
        dumped = loc_obj.model_dump(mode="json", exclude_unset=True, by_alias=True)
        assert jcs(loc) == jcs(dumped), f"Location {idx} ({loc.get('location_id')}) JCS round-trip mismatch!"


def test_research_models_round_trip() -> None:
    r_in = {
        "topic": "Quantum Computing",
        "source_refs": ["arxiv:2301.00001", "doi:10.1000/182"],
    }
    m_in = ResearchInput.model_validate(r_in)
    dump_in = m_in.model_dump(mode="json", exclude_unset=True, by_alias=True)
    assert jcs(r_in) == jcs(dump_in)

    r_out = {
        "summary": "Overview of NISQ era devices.",
        "evidence_refs": ["ref-1", "ref-2"],
        "limitations": ["Limited to superconducting qubits"],
    }
    m_out = ResearchOutput.model_validate(r_out)
    dump_out = m_out.model_dump(mode="json", exclude_unset=True, by_alias=True)
    assert jcs(r_out) == jcs(dump_out)
