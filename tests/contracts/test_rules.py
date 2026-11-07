"""Unit tests for EAP contract semantic rules (rules.py)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from eap.contracts.rules import (
    ALLOWED,
    TERMINAL,
    ContractViolation,
    apply_assessment,
    check_event_stream,
    check_ppm_output_rules,
    check_run_progress,
    check_run_state_transition,
    check_usage_cost,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_transition_table_allowed_and_terminal() -> None:
    # All allowed transitions must succeed
    for from_st, to_st in ALLOWED:
        check_run_state_transition(from_st, to_st)

    # Leaving terminal states must raise ContractViolation with TERMINAL_STATE_CANNOT_BE_LEFT
    for term in TERMINAL:
        with pytest.raises(ContractViolation) as exc_info:
            check_run_state_transition(term, "running")
        assert exc_info.value.code == "TERMINAL_STATE_CANNOT_BE_LEFT"

    # Disallowed transition
    with pytest.raises(ContractViolation) as exc_info:
        check_run_state_transition("queued", "running")
    assert exc_info.value.code == "ILLEGAL_RUN_TRANSITION"


def test_run_progress_rules() -> None:
    check_run_progress(5, 10)
    check_run_progress(10, 10)
    check_run_progress(None, None)
    with pytest.raises(ContractViolation) as exc_info:
        check_run_progress(11, 10)
    assert exc_info.value.code == "PROGRESS_EXCEEDS_DENOMINATOR"


def test_usage_cost_rules() -> None:
    check_usage_cost("actual", 1.25)
    check_usage_cost("unknown", None)
    with pytest.raises(ContractViolation) as exc_info:
        check_usage_cost("unknown", 0.0)
    assert exc_info.value.code == "USAGE_COST_MISMATCH"

    with pytest.raises(ContractViolation) as exc_info:
        check_usage_cost("actual", None)
    assert exc_info.value.code == "USAGE_COST_MISMATCH"


def test_apply_assessment_basis_seq_compare_and_set() -> None:
    ppm_output_path = REPO_ROOT / "examples" / "ppm-output.json"
    base = json.loads(ppm_output_path.read_text(encoding="utf-8"))

    # Out-of-order scan completion: scan B (newer snapshot) finishes before scan A.
    a_old = dict(base, assessment_id="asmt-A", snapshot_seq=2, basis_seq=2)
    b_new = dict(base, assessment_id="asmt-B", snapshot_seq=3, basis_seq=3)
    current, history = apply_assessment(None, b_new)
    assert current["assessment_id"] == "asmt-B"
    assert history is None

    current, history = apply_assessment(current, a_old)
    assert current["assessment_id"] == "asmt-B"
    assert history["assessment_id"] == "asmt-A"

    # Same snapshot, baseline changed while old assessment is still running (C39)
    old_basis = dict(b_new, assessment_id="asmt-C-baseline1", basis_seq=4, baseline_revision=1, trigger="snapshot")
    new_basis = dict(b_new, assessment_id="asmt-D-baseline2", basis_seq=5, baseline_revision=2, trigger="baseline_changed")
    current, _ = apply_assessment(current, new_basis)
    assert current["assessment_id"] == "asmt-D-baseline2"

    current, history = apply_assessment(current, old_basis)
    assert current["assessment_id"] == "asmt-D-baseline2"
    assert history["assessment_id"] == "asmt-C-baseline1"


def test_ppm_waiver_rules_and_baseline() -> None:
    ppm_output_path = REPO_ROOT / "examples" / "ppm-output.json"
    waived = json.loads(ppm_output_path.read_text(encoding="utf-8"))
    waived["criteria"][2].update({
        "status": "waived",
        "reason_code": "waiver_approved",
        "waiver_ref": "waiver:WV-1",
        "missing_evidence": [],
    })
    waived["progress"]["total_weight"] = 3
    # Approved waiver is valid and excluded from denominator
    check_ppm_output_rules(waived)


def test_event_stream_ordering_and_continuity() -> None:
    events_raw = (REPO_ROOT / "examples" / "events.ndjson").read_text(encoding="utf-8")
    events = [json.loads(line) for line in events_raw.splitlines() if line.strip()]
    check_event_stream(events)

    # Duplicate event
    dup_events = copy.deepcopy(events)
    dup_events.append(copy.deepcopy(events[0]))
    with pytest.raises(ContractViolation) as exc_info:
        check_event_stream(dup_events)
    assert exc_info.value.code == "DUPLICATE_EVENT"

    # Decreasing producer_seq
    bad_seq_events = copy.deepcopy(events)
    bad_seq_events[1]["producer_seq"] = bad_seq_events[0]["producer_seq"]
    with pytest.raises(ContractViolation) as exc_info:
        check_event_stream(bad_seq_events)
    assert exc_info.value.code == "PRODUCER_SEQ_ORDERING"
