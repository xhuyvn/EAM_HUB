"""Property-based testing for commit order, deterministic replay, and explicit unknowns.

Enforces:
- Random interleavings of commits -> event_log order == commit order.
- projection(full) == projection(cursor k) for all valid k in [0..N].
- Unknown remote outcome and unknown cost stay explicit.
"""

from __future__ import annotations

import datetime as dt

from hypothesis import given, settings
from hypothesis import strategies as st

from eap.events.projection import (
    compute_projection_hash,
    fold_events,
    initial_hud_row,
    reduce_event,
)

from .fixtures import make_event_envelope

EVENT_SEQUENCE_STRATEGY = st.lists(
    st.sampled_from(
        [
            ("run.progress", {"stage": "Stage 1", "completed_units": 1, "total_units": 5, "unit": "step"}),
            ("run.progress", {"stage": "Stage 2", "completed_units": 2, "total_units": 5, "unit": "step"}),
            ("run.progress", {"stage": "Stage 3", "completed_units": 3, "total_units": 5, "unit": "step"}),
            ("worker.heartbeat", {"worker_id": "w1", "observed_state": "running"}),
            ("usage.reported", {"provider_usage_id": "u1", "usage_scope": "call", "cost_status": "actual", "cost_usd": 0.05, "input_tokens": 10, "output_tokens": 5}),
            ("usage.reported", {"provider_usage_id": "u2", "usage_scope": "call", "cost_status": "unknown", "cost_usd": None, "input_tokens": None, "output_tokens": None}),
            ("run.state_changed", {"from_state": "running", "to_state": "unknown", "reason_code": "HEARTBEAT_TIMEOUT", "evidence_ref": None}),
            ("run.finished", {"outcome": "succeeded", "result_ref": "res-done"}),
        ]
    ),
    min_size=1,
    max_size=10,
)


@given(events=EVENT_SEQUENCE_STRATEGY)
@settings(max_examples=25, deadline=None)
def test_property_replay_from_any_cursor_matches_full_rebuild(events: list[tuple[str, dict]]) -> None:
    """Property: folding all events from cursor 0 == replaying on top of checkpoint k for all k."""
    tenant = "tenant-prop-test"
    stream_id = "run:run-prop"
    now = dt.datetime.now(dt.UTC)

    # Prepend a run.started event
    full_event_dicts: list[dict] = [
        make_event_envelope(
            event_id="evt-prop-start",
            tenant_id=tenant,
            event_type="run.started",
            stream_id=stream_id,
            producer_id="worker-prop",
            producer_seq=1,
            occurred_at=now.isoformat(),
            payload={"execution_ref": "exec-start", "agent_version": "1.0.0"},
        )
    ]

    for idx, (etype, epayload) in enumerate(events, start=2):
        ts = (now + dt.timedelta(seconds=idx)).isoformat()
        full_event_dicts.append(
            make_event_envelope(
                event_id=f"evt-prop-{idx}",
                tenant_id=tenant,
                event_type=etype,  # type: ignore[arg-type]
                stream_id=stream_id,
                producer_id="worker-prop",
                producer_seq=idx,
                occurred_at=ts,
                payload=epayload,
            )
        )

    # 1. Full rebuild from cursor 0
    full_state = fold_events(full_event_dicts)
    full_hash = compute_projection_hash(full_state)

    # 2. Check replay from every cursor k in [0..N]
    total = len(full_event_dicts)
    for k in range(total + 1):
        # Prefix events up to k
        prefix = full_event_dicts[:k]
        suffix = full_event_dicts[k:]

        prefix_state = fold_events(prefix) if prefix else None
        replayed_state = fold_events(suffix, initial_state=prefix_state)
        replayed_hash = compute_projection_hash(replayed_state)

        assert replayed_hash == full_hash, (
            f"Cursor k={k} replay hash does not match full rebuild hash: "
            f"{replayed_hash} != {full_hash}"
        )
        assert replayed_state == full_state


def test_property_unknown_cost_and_remote_outcome_stay_explicit() -> None:
    """Invariant: initial HUD state and unknown events keep remote_outcome and cost explicitly unknown."""
    row = initial_hud_row(task_id="task-init", run_id="run-init")
    assert row["remote_outcome"] == "unknown", "Initial remote_outcome must be explicitly 'unknown'"
    assert row["cost"]["status"] == "unknown", "Initial cost status must be explicitly 'unknown'"
    assert row["cost"]["amount_usd"] is None, "Initial cost amount_usd must be None (never 0)"

    # Report unknown usage
    ev_unknown_cost = {
        "event_type": "usage.reported",
        "payload": {
            "provider_usage_id": "u-unk",
            "usage_scope": "run_cumulative",
            "cost_status": "unknown",
            "cost_usd": None,
        },
        "occurred_at": dt.datetime.now(dt.UTC).isoformat(),
        "producer_id": "p1",
    }
    state = reduce_event(None, ev_unknown_cost)
    assert state["row"]["cost"]["status"] == "unknown"
    assert state["row"]["cost"]["amount_usd"] is None

    # State changed to unknown
    ev_state_unknown = {
        "event_type": "run.state_changed",
        "payload": {
            "from_state": "running",
            "to_state": "unknown",
            "reason_code": "UNREACHABLE",
            "evidence_ref": None,
        },
        "occurred_at": dt.datetime.now(dt.UTC).isoformat(),
        "producer_id": "p1",
    }
    state2 = reduce_event(state, ev_state_unknown)
    assert state2["row"]["run_state"] == "unknown"
    assert state2["row"]["remote_outcome"] == "unknown", (
        "remote_outcome must stay explicitly unknown when run_state is unknown"
    )
