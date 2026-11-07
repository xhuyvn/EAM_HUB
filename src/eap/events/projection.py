"""Versioned deterministic HUD projection reducer and checkpointing (docs/03, C33)."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select

from eap.contracts.digest import sha256_digest
from eap.contracts.models.hud_projection import HudRow
from eap.db.event_tables import projection_checkpoints
from eap.db.tables import event_log

PROJECTION_NAME_HUD_RUNS = "hud_runs"
PROJECTION_VERSION_CURRENT = 1


def initial_hud_row(
    *,
    task_id: str,
    run_id: str | None = None,
    attempt: int | None = 1,
    observation_source: str = "system",
) -> dict[str, Any]:
    """Create initial default HUD row for a task/run stream.

    Invariant: Unknown remote outcome and unknown cost stay explicit
    (never rendered as known or zero).
    """
    return {
        "task_id": task_id,
        "task_state": "active",
        "run_id": run_id,
        "attempt": attempt,
        "run_state": "queued",
        "remote_outcome": "unknown",  # Explicitly unknown
        "connectivity": "unknown",
        "last_observed_at": None,
        "observation_source": observation_source,
        "progress": {
            "kind": "none",
            "completed_units": None,
            "total_units": None,
            "unit": None,
            "stage": None,
        },
        "project": None,
        "cost": {
            "status": "unknown",  # Explicitly unknown
            "amount_usd": None,
            "as_of": None,
            "scope": "run",
        },
        "pending_command": None,
    }


def compute_projection_hash(state: dict[str, Any]) -> str:
    """Compute RFC 8785 canonical SHA-256 digest of projection state."""
    return sha256_digest(state)


def reduce_event(
    state: dict[str, Any] | None,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Pure, deterministic reducer folding one event into the HUD run projection state.

    Input event can be an Event contract dict or event_log row dict.
    """
    event_type = event["event_type"]
    raw_payload = event.get("payload") or {}
    occurred_at = event.get("occurred_at")
    producer_id = event.get("producer_id") or "system"
    task_id = event.get("task_id") or (state["row"]["task_id"] if state else "unknown")
    run_id = event.get("run_id") or (state["row"]["run_id"] if state else None)
    attempt = event.get("attempt") or (state["row"]["attempt"] if state else 1)

    # If payload is an entire event envelope (e.g. stored in event_log.payload)
    if isinstance(raw_payload, dict) and "event_type" in raw_payload and "payload" in raw_payload:
        env = raw_payload
        task_id = env.get("task_id") or task_id
        run_id = env.get("run_id") or run_id
        attempt = env.get("attempt") or attempt
        producer_id = env.get("producer_id") or producer_id
        occurred_at = env.get("occurred_at") or occurred_at
        payload = env.get("payload") or {}
    else:
        payload = raw_payload

    if state is None:
        row = initial_hud_row(
            task_id=task_id,
            run_id=run_id,
            attempt=attempt,
            observation_source=producer_id,
        )
    else:
        row = deepcopy(state["row"])
        if task_id and row.get("task_id") in ("unknown", None):
            row["task_id"] = task_id
        if run_id and row.get("run_id") is None:
            row["run_id"] = run_id
        if attempt and row.get("attempt") in (1, None):
            row["attempt"] = attempt

    # Update common observation fields if occurred_at is present
    if occurred_at:
        row["last_observed_at"] = occurred_at
    if producer_id:
        row["observation_source"] = producer_id

    if event_type == "run.started":
        row["run_state"] = "running"
        row["task_state"] = "active"
        row["remote_outcome"] = "known"
        row["connectivity"] = "healthy"
        if "agent_version" in payload:
            row["observation_source"] = producer_id

    elif event_type == "run.progress":
        completed = payload.get("completed_units")
        total = payload.get("total_units")
        stage = payload.get("stage")
        unit = payload.get("unit")
        if completed is not None and total is not None:
            row["progress"] = {
                "kind": "units",
                "completed_units": int(completed),
                "total_units": int(total),
                "unit": unit,
                "stage": stage,
            }
        elif stage:
            row["progress"] = {
                "kind": "stage_only",
                "completed_units": None,
                "total_units": None,
                "unit": unit,
                "stage": stage,
            }

    elif event_type == "run.state_changed":
        to_state = payload.get("to_state")
        if to_state:
            row["run_state"] = to_state
            if to_state == "unknown":
                row["remote_outcome"] = "unknown"
            elif to_state in ("succeeded", "failed", "cancelled", "timed_out"):
                if to_state == "succeeded":
                    row["task_state"] = "completed"
                    row["remote_outcome"] = "known"
                elif to_state == "cancelled":
                    row["task_state"] = "cancelled"
                    row["remote_outcome"] = "known"
                elif to_state == "failed":
                    row["task_state"] = "failed"
                    row["remote_outcome"] = "known"
                elif to_state == "timed_out":
                    row["task_state"] = "failed"
                    row["remote_outcome"] = "unknown"
            elif to_state == "waiting_approval":
                row["task_state"] = "review_required"

    elif event_type == "run.waiting_approval":
        row["run_state"] = "waiting_approval"
        row["task_state"] = "review_required"

    elif event_type == "run.finished":
        outcome = payload.get("outcome")
        if outcome == "succeeded":
            row["run_state"] = "succeeded"
            row["task_state"] = "completed"
            row["remote_outcome"] = "known"
        elif outcome == "failed":
            row["run_state"] = "failed"
            row["task_state"] = "failed"
            row["remote_outcome"] = "known"
        elif outcome == "cancelled":
            row["run_state"] = "cancelled"
            row["task_state"] = "cancelled"
            row["remote_outcome"] = "known"
        elif outcome == "partial":
            row["run_state"] = "succeeded"
            row["task_state"] = "review_required"
            row["remote_outcome"] = "known"

    elif event_type == "worker.heartbeat":
        row["connectivity"] = "healthy"

    elif event_type == "connector.status":
        conn_status = payload.get("connectivity")
        if conn_status:
            row["connectivity"] = conn_status
        if payload.get("last_observed_at"):
            row["last_observed_at"] = payload["last_observed_at"]

    elif event_type == "usage.reported":
        cost_status = payload.get("cost_status", "unknown")
        cost_usd = payload.get("cost_usd")
        usage_scope = payload.get("usage_scope", "call")

        if cost_status == "unknown" or cost_usd is None:
            row["cost"] = {
                "status": "unknown",
                "amount_usd": None,
                "as_of": occurred_at,
                "scope": "run" if usage_scope == "run_cumulative" else row["cost"]["scope"],
            }
        elif usage_scope == "run_cumulative":
            row["cost"] = {
                "status": cost_status,
                "amount_usd": float(cost_usd),
                "as_of": occurred_at,
                "scope": "run",
            }
        else:  # call
            prev_cost = row["cost"]
            prev_usd = (
                prev_cost["amount_usd"]
                if prev_cost["status"] in ("actual", "estimated", "partial")
                and prev_cost["amount_usd"] is not None
                else 0.0
            )
            row["cost"] = {
                "status": cost_status,
                "amount_usd": round(prev_usd + float(cost_usd), 6),
                "as_of": occurred_at,
                "scope": "run",
            }

    elif event_type == "approval.decided":
        decision = payload.get("decision")
        if decision == "approved":
            row["task_state"] = "active"
            row["run_state"] = "running"
        elif decision in ("denied", "expired", "revoked"):
            row["task_state"] = "blocked"

    elif event_type == "ppm.assessment.updated":
        row["project"] = {
            "project_id": payload["project_id"],
            "assessment_id": payload.get("assessment_id"),
            "verification": payload.get("verification", "unknown"),
            "validity": payload.get("validity", "unknown"),
            "acceptance": payload.get("acceptance", "not_requested"),
            "assessment_as_of": occurred_at,
            "verified_weight": payload.get("verified_weight"),
            "total_weight": payload.get("total_weight"),
        }

    # Strict contract validation: make sure the reduced row validates
    HudRow.model_validate(row)

    return {"row": row}


def fold_events(
    events: Sequence[dict[str, Any]],
    initial_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fold a sequence of events into a deterministic projection state."""
    state = deepcopy(initial_state) if initial_state is not None else None
    for ev in events:
        state = reduce_event(state, ev)
    if state is None:
        state = {"row": initial_hud_row(task_id="unknown")}
    return state


def save_checkpoint(
    conn: sa.Connection,
    *,
    tenant_id: str,
    stream_id: str,
    last_ingest_offset: int,
    state: dict[str, Any],
    projection_name: str = PROJECTION_NAME_HUD_RUNS,
    projection_version: int = PROJECTION_VERSION_CURRENT,
) -> str:
    """Save or update a projection checkpoint. Returns the state hash."""
    state_hash = compute_projection_hash(state)
    stmt = (
        sa.dialects.postgresql.insert(projection_checkpoints)
        .values(
            tenant_id=tenant_id,
            stream_id=stream_id,
            projection_name=projection_name,
            projection_version=projection_version,
            last_ingest_offset=last_ingest_offset,
            state=state,
            state_hash=state_hash,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "stream_id", "projection_name"],
            set_={
                "projection_version": projection_version,
                "last_ingest_offset": last_ingest_offset,
                "state": state,
                "state_hash": state_hash,
                "updated_at": sa.func.now(),
            },
        )
    )
    conn.execute(stmt)
    return state_hash


def load_checkpoint(
    conn: sa.Connection,
    *,
    tenant_id: str,
    stream_id: str,
    projection_name: str = PROJECTION_NAME_HUD_RUNS,
) -> dict[str, Any] | None:
    """Load latest checkpoint for a stream."""
    stmt = select(
        projection_checkpoints.c.last_ingest_offset,
        projection_checkpoints.c.state,
        projection_checkpoints.c.state_hash,
        projection_checkpoints.c.projection_version,
    ).where(
        projection_checkpoints.c.tenant_id == tenant_id,
        projection_checkpoints.c.stream_id == stream_id,
        projection_checkpoints.c.projection_name == projection_name,
    )
    row = conn.execute(stmt).fetchone()
    if row is None:
        return None
    return {
        "last_ingest_offset": int(row[0]),
        "state": row[1],
        "state_hash": str(row[2]),
        "projection_version": int(row[3]),
    }


def replay_stream(
    conn: sa.Connection,
    *,
    tenant_id: str,
    stream_id: str,
    from_cursor: int | None = None,
    projection_name: str = PROJECTION_NAME_HUD_RUNS,
) -> tuple[dict[str, Any], str, int]:
    """Replay stream projection from a cursor (or from checkpoint / beginning).

    Returns: (state, state_hash, last_offset)
    """
    initial_state = None
    start_offset = 0

    if from_cursor is not None and from_cursor > 0:
        # Load checkpoint up to cursor if available
        ckpt = load_checkpoint(
            conn,
            tenant_id=tenant_id,
            stream_id=stream_id,
            projection_name=projection_name,
        )
        if ckpt is not None and ckpt["last_ingest_offset"] == from_cursor:
            initial_state = ckpt["state"]
            start_offset = from_cursor
        else:
            # Rebuild state from offset 1 up to from_cursor
            stmt_prev = (
                select(
                    event_log.c.ingest_offset,
                    event_log.c.event_id,
                    event_log.c.producer_id,
                    event_log.c.event_type,
                    event_log.c.payload,
                    event_log.c.occurred_at,
                )
                .where(
                    event_log.c.tenant_id == tenant_id,
                    event_log.c.stream_id == stream_id,
                    event_log.c.ingest_offset <= from_cursor,
                )
                .order_by(event_log.c.ingest_offset.asc())
            )
            prev_rows = conn.execute(stmt_prev).fetchall()
            prev_events = [
                {
                    "ingest_offset": int(r[0]),
                    "event_id": str(r[1]),
                    "producer_id": str(r[2]),
                    "event_type": str(r[3]),
                    "payload": r[4],
                    "occurred_at": r[5].isoformat() if hasattr(r[5], "isoformat") else str(r[5]),
                }
                for r in prev_rows
            ]
            if prev_events:
                initial_state = fold_events(prev_events, initial_state=None)
            start_offset = from_cursor

    # Fetch events strictly after start_offset ordered by ingest_offset
    stmt = (
        select(
            event_log.c.ingest_offset,
            event_log.c.event_id,
            event_log.c.producer_id,
            event_log.c.event_type,
            event_log.c.payload,
            event_log.c.occurred_at,
        )
        .where(
            event_log.c.tenant_id == tenant_id,
            event_log.c.stream_id == stream_id,
            event_log.c.ingest_offset > start_offset,
        )
        .order_by(event_log.c.ingest_offset.asc())
    )
    rows = conn.execute(stmt).fetchall()

    events: list[dict[str, Any]] = []
    last_offset = start_offset
    for r in rows:
        last_offset = int(r[0])
        events.append(
            {
                "ingest_offset": last_offset,
                "event_id": str(r[1]),
                "producer_id": str(r[2]),
                "event_type": str(r[3]),
                "payload": r[4],
                "occurred_at": r[5].isoformat() if hasattr(r[5], "isoformat") else str(r[5]),
            }
        )

    if events:
        final_state = fold_events(events, initial_state=initial_state)
    elif initial_state is not None:
        final_state = initial_state
    else:
        final_state = {"row": initial_hud_row(task_id="unknown")}

    final_hash = compute_projection_hash(final_state)
    return final_state, final_hash, last_offset


def build_fresh_snapshot(
    conn: sa.Connection,
    *,
    tenant_id: str,
    stream_id: str,
) -> tuple[dict[str, Any], str, int]:
    """Full rebuild from ingest_offset 0 of the stream.

    Returns: (state, state_hash, last_offset)
    """
    return replay_stream(conn, tenant_id=tenant_id, stream_id=stream_id, from_cursor=0)


__all__ = [
    "PROJECTION_NAME_HUD_RUNS",
    "PROJECTION_VERSION_CURRENT",
    "build_fresh_snapshot",
    "compute_projection_hash",
    "fold_events",
    "initial_hud_row",
    "load_checkpoint",
    "reduce_event",
    "replay_stream",
    "save_checkpoint",
]
