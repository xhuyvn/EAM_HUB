"""Real PostgreSQL concurrency and exact-once replay tests for P3a-WP1 HUD snapshot."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eap.api.dependencies import Principal
from eap.db.event_tables import outbox_relay_marks, projection_checkpoints, stream_heads
from eap.db.tables import commands, event_log, outbox, runs, tasks, usage_ledger
from eap.events.ingest import ProducerBinding
from eap.events.projection import compute_projection_hash
from eap.events.relay import OutboxRelayWorker
from eap.hud.snapshot import (
    apply_replayed_events_to_snapshot,
    replay_events_after_cursor,
    take_hud_snapshot,
)


def _cleanup_tenant_data(engine: Engine, tenant_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(sa.delete(projection_checkpoints).where(projection_checkpoints.c.tenant_id == tenant_id))
        conn.execute(sa.delete(outbox_relay_marks).where(outbox_relay_marks.c.tenant_id == tenant_id))
        conn.execute(sa.delete(event_log).where(event_log.c.tenant_id == tenant_id))
        conn.execute(sa.delete(stream_heads).where(stream_heads.c.tenant_id == tenant_id))
        conn.execute(sa.delete(outbox).where(outbox.c.tenant_id == tenant_id))
        conn.execute(sa.delete(usage_ledger).where(usage_ledger.c.tenant_id == tenant_id))
        conn.execute(sa.delete(commands).where(commands.c.tenant_id == tenant_id))
        conn.execute(sa.delete(runs).where(runs.c.tenant_id == tenant_id))
        conn.execute(sa.delete(tasks).where(tasks.c.tenant_id == tenant_id))


def _seed_task_and_run(
    engine: Engine,
    *,
    tenant_id: str,
    task_id: str,
    run_id: str,
    task_state: str = "ready",
    run_state: str = "queued",
    remote_outcome: str = "unknown",
) -> None:
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            tasks.insert().values(
                id=task_id,
                tenant_id=tenant_id,
                client_idempotency_key=f"idem-{task_id}",
                task_intent_key=f"intent-{task_id}",
                task_type="code_review",
                agent_id="agent-1",
                agent_version="1.0.0",
                state=task_state,
                deadline=now + timedelta(hours=1),
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            runs.insert().values(
                id=f"row-{run_id}",
                tenant_id=tenant_id,
                task_id=task_id,
                run_id=run_id,
                attempt=1,
                binding_id="bind-cons-1",
                state=run_state,
                remote_outcome=remote_outcome,
                lease_fencing_token=1,
                created_at=now,
                updated_at=now,
            )
        )


def _append_and_relay_event(
    engine: Engine,
    *,
    tenant_id: str,
    task_id: str,
    run_id: str,
    producer_id: str,
    binding_id: str,
    producer_seq: int,
    event_type: str,
    payload: dict[str, Any],
    occurred_at: datetime,
) -> str:
    stream_id = f"run:{run_id}"
    event_id = f"ev-{run_id}-{producer_seq}-{uuid.uuid4().hex[:6]}"
    envelope = {
        "contract_version": "1.1",
        "event_id": event_id,
        "tenant_id": tenant_id,
        "task_id": task_id,
        "run_id": run_id,
        "attempt": 1,
        "producer_id": producer_id,
        "producer_seq": producer_seq,
        "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
        "event_type": event_type,
        "payload": payload,
    }
    def _binding_provider(req_tenant: str, req_producer: str) -> ProducerBinding | None:
        if req_tenant == tenant_id and req_producer == producer_id:
            return ProducerBinding(
                tenant_id=tenant_id,
                producer_id=producer_id,
                binding_id=binding_id,
                certified_capabilities={"events": True},
            )
        return None
    with engine.begin() as conn:
        conn.execute(
            outbox.insert().values(
                tenant_id=tenant_id,
                event_id=event_id,
                stream_id=stream_id,
                event_type=event_type,
                payload=envelope,
                created_at=occurred_at,
            )
        )
    with engine.begin() as conn:
        res = OutboxRelayWorker(enabled=True).relay_stream_batch(
            conn,
            tenant_id=tenant_id,
            stream_id=stream_id,
            binding_provider=_binding_provider,
        )
        assert res.ingested_count == 1
    return event_id


def test_repeatable_read_barrier_concurrent_write_exact_once_replay(db_engine: Engine) -> None:
    """Prove snapshot reads rows and stream offsets atomically under REPEATABLE READ.

    A concurrent writer commits events *after* the snapshot transaction has read rows
    but *before* it reads stream_offsets. Under REPEATABLE READ both reads share one
    MVCC snapshot, so post-cursor replay captures every concurrent event without gap or dup.
    """
    suffix = uuid.uuid4().hex[:8]
    tenant_id = f"tenant-barrier-{suffix}"
    task_id = f"task-{suffix}"
    run_id = f"run-{suffix}"
    principal = Principal(tenant_id=tenant_id, principal_id=f"user-{suffix}", roles=["owner"])
    base_time = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)

    try:
        _seed_task_and_run(db_engine, tenant_id=tenant_id, task_id=task_id, run_id=run_id)
        ev1 = _append_and_relay_event(
            db_engine,
            tenant_id=tenant_id,
            task_id=task_id,
            run_id=run_id,
            producer_id="worker-1",
            binding_id="bind-cons-1",
            producer_seq=1,
            event_type="run.started",
            payload={"from_state": "queued", "to_state": "running"},
            occurred_at=base_time,
        )
        ev2 = _append_and_relay_event(
            db_engine,
            tenant_id=tenant_id,
            task_id=task_id,
            run_id=run_id,
            producer_id="worker-1",
            binding_id="bind-cons-1",
            producer_seq=2,
            event_type="run.progress",
            payload={"completed_units": 2, "total_units": 10, "unit": "files", "stage": "scan"},
            occurred_at=base_time + timedelta(seconds=1),
        )

        rows_read_evt = threading.Event()
        writer_done_evt = threading.Event()
        snapshot_holder: dict[str, Any] = {}

        def _barrier() -> None:
            rows_read_evt.set()
            assert writer_done_evt.wait(timeout=10.0), "Writer did not commit before barrier timeout"

        def _reader() -> None:
            snapshot_holder["snap"] = take_hud_snapshot(
                db_engine,
                principal=principal,
                on_after_rows_read=_barrier,
            )

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        assert rows_read_evt.wait(timeout=10.0), "Reader did not reach barrier"
        ev3 = _append_and_relay_event(
            db_engine,
            tenant_id=tenant_id,
            task_id=task_id,
            run_id=run_id,
            producer_id="worker-1",
            binding_id="bind-cons-1",
            producer_seq=3,
            event_type="run.progress",
            payload={"completed_units": 7, "total_units": 10, "unit": "files", "stage": "verify"},
            occurred_at=base_time + timedelta(seconds=2),
        )
        ev4 = _append_and_relay_event(
            db_engine,
            tenant_id=tenant_id,
            task_id=task_id,
            run_id=run_id,
            producer_id="worker-1",
            binding_id="bind-cons-1",
            producer_seq=4,
            event_type="usage.reported",
            payload={"cost_status": "actual", "cost_usd": 0.42, "usage_scope": "run_cumulative"},
            occurred_at=base_time + timedelta(seconds=3),
        )
        writer_done_evt.set()
        reader_thread.join(timeout=10.0)
        assert not reader_thread.is_alive()

        snap = snapshot_holder["snap"]
        assert len(snap["rows"]) == 1
        snap_row = snap["rows"][0]
        # Snapshot saw ONLY events 1..2
        assert snap_row["progress"]["completed_units"] == 2
        assert snap_row["cost"]["status"] == "unknown"
        assert snap_row["cost"]["amount_usd"] is None
        assert snap["projection_revision"] == 2

        decoded, replayed = replay_events_after_cursor(
            db_engine,
            cursor=snap["snapshot_cursor"],
            principal=principal,
        )
        assert decoded.stream_offsets == {f"run:{run_id}": 2}
        assert [ev["event_id"] for ev in replayed] == [ev3, ev4]
        assert [ev["ingest_offset"] for ev in replayed] == [3, 4]
        assert {ev1, ev2}.isdisjoint({ev["event_id"] for ev in replayed})

        replayed_snap = apply_replayed_events_to_snapshot(snap, replayed, cursor_payload=decoded)
        final_snap = take_hud_snapshot(db_engine, principal=principal)
        assert replayed_snap["projection_revision"] == final_snap["projection_revision"] == 4
        assert compute_projection_hash({"row": replayed_snap["rows"][0]}) == compute_projection_hash(
            {"row": final_snap["rows"][0]}
        )
        assert replayed_snap["rows"][0]["progress"]["completed_units"] == 7
        assert replayed_snap["rows"][0]["cost"]["amount_usd"] == pytest.approx(0.42)
    finally:
        _cleanup_tenant_data(db_engine, tenant_id)


def test_parallel_writers_and_snapshot_readers_exact_once_partition(db_engine: Engine) -> None:
    """Stress parallel writers (including a new stream created mid-flight) + concurrent snapshots."""
    suffix = uuid.uuid4().hex[:8]
    tenant_id = f"tenant-parallel-{suffix}"
    principal = Principal(tenant_id=tenant_id, principal_id=f"user-{suffix}", roles=["owner"])
    base_time = datetime(2026, 11, 15, 13, 0, 0, tzinfo=UTC)

    streams: list[tuple[str, str]] = [
        (f"task-{suffix}-{idx}", f"run-{suffix}-{idx}") for idx in range(4)
    ]
    late_task_id = f"task-{suffix}-late"
    late_run_id = f"run-{suffix}-late"

    try:
        for idx, (t_id, r_id) in enumerate(streams):
            _seed_task_and_run(db_engine, tenant_id=tenant_id, task_id=t_id, run_id=r_id)
            _append_and_relay_event(
                db_engine,
                tenant_id=tenant_id,
                task_id=t_id,
                run_id=r_id,
                producer_id=f"worker-{idx}",
                binding_id="bind-cons-1",
                producer_seq=1,
                event_type="run.started",
                payload={"from_state": "queued", "to_state": "running"},
                occurred_at=base_time,
            )

        def _writer(stream_idx: int, t_id: str, r_id: str) -> list[str]:
            emitted: list[str] = []
            for step in range(2, 9):
                ev_id = _append_and_relay_event(
                    db_engine,
                    tenant_id=tenant_id,
                    task_id=t_id,
                    run_id=r_id,
                    producer_id=f"worker-{stream_idx}",
                    binding_id="bind-cons-1",
                    producer_seq=step,
                    event_type="run.progress",
                    payload={
                        "completed_units": step,
                        "total_units": 10,
                        "unit": "chunks",
                        "stage": f"step-{step}",
                    },
                    occurred_at=base_time + timedelta(seconds=step),
                )
                emitted.append(ev_id)
            emitted.append(
                _append_and_relay_event(
                    db_engine,
                    tenant_id=tenant_id,
                    task_id=t_id,
                    run_id=r_id,
                    producer_id=f"worker-{stream_idx}",
                    binding_id="bind-cons-1",
                    producer_seq=9,
                    event_type="usage.reported",
                    payload={"cost_status": "actual", "cost_usd": 0.15 * (stream_idx + 1), "usage_scope": "run_cumulative"},
                    occurred_at=base_time + timedelta(seconds=10),
                )
            )
            emitted.append(
                _append_and_relay_event(
                    db_engine,
                    tenant_id=tenant_id,
                    task_id=t_id,
                    run_id=r_id,
                    producer_id=f"worker-{stream_idx}",
                    binding_id="bind-cons-1",
                    producer_seq=10,
                    event_type="run.finished",
                    payload={"outcome": "succeeded"},
                    occurred_at=base_time + timedelta(seconds=11),
                )
            )
            return emitted

        def _late_stream_writer() -> list[str]:
            _seed_task_and_run(db_engine, tenant_id=tenant_id, task_id=late_task_id, run_id=late_run_id)
            return [
                _append_and_relay_event(
                    db_engine,
                    tenant_id=tenant_id,
                    task_id=late_task_id,
                    run_id=late_run_id,
                    producer_id="worker-late",
                    binding_id="bind-cons-1",
                    producer_seq=1,
                    event_type="run.started",
                    payload={"from_state": "queued", "to_state": "running"},
                    occurred_at=base_time + timedelta(seconds=5),
                ),
                _append_and_relay_event(
                    db_engine,
                    tenant_id=tenant_id,
                    task_id=late_task_id,
                    run_id=late_run_id,
                    producer_id="worker-late",
                    binding_id="bind-cons-1",
                    producer_seq=2,
                    event_type="run.progress",
                    payload={"completed_units": 3, "total_units": 5, "unit": "steps", "stage": "late"},
                    occurred_at=base_time + timedelta(seconds=6),
                ),
            ]

        def _reader_loop(n_reads: int) -> list[dict[str, Any]]:
            snaps = []
            for _ in range(n_reads):
                snaps.append(take_hud_snapshot(db_engine, principal=principal))
            return snaps

        captured_snaps: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            writer_futs = [pool.submit(_writer, idx, t_id, r_id) for idx, (t_id, r_id) in enumerate(streams)]
            late_fut = pool.submit(_late_stream_writer)
            reader_futs = [pool.submit(_reader_loop, 4) for _ in range(3)]
            for f in writer_futs:
                f.result(timeout=30)
            late_fut.result(timeout=30)
            for rf in reader_futs:
                captured_snaps.extend(rf.result(timeout=30))

        with db_engine.connect() as conn:
            all_committed = conn.execute(
                sa.select(event_log.c.stream_id, event_log.c.ingest_offset, event_log.c.event_id)
                .where(event_log.c.tenant_id == tenant_id)
                .order_by(event_log.c.stream_id.asc(), event_log.c.ingest_offset.asc())
            ).fetchall()

        all_keys = {(str(s), int(o)): str(eid) for s, o, eid in all_committed}
        assert len(all_keys) == 4 * 10 + 2  # 42 committed events total

        final_snap = take_hud_snapshot(db_engine, principal=principal)
        final_rows = {r["task_id"]: r for r in final_snap["rows"]}
        final_by_task = {t_id: compute_projection_hash({"row": row}) for t_id, row in final_rows.items()}
        assert len(final_by_task) == 5

        for snap in captured_snaps:
            decoded, replayed = replay_events_after_cursor(
                db_engine,
                cursor=snap["snapshot_cursor"],
                principal=principal,
            )
            covered_keys = {
                (s, o) for (s, o) in all_keys if o <= decoded.stream_offsets.get(s, 0)
            }
            replayed_keys = {(ev["stream_id"], int(ev["ingest_offset"])) for ev in replayed}

            assert covered_keys.isdisjoint(replayed_keys), "Duplicate event in replay"
            assert covered_keys | replayed_keys == set(all_keys.keys()), "Gap in event replay"

            converged = apply_replayed_events_to_snapshot(snap, replayed, cursor_payload=decoded)
            assert converged["projection_revision"] == final_snap["projection_revision"]
            converged_rows = {r["task_id"]: r for r in converged["rows"]}
            assert converged_rows == final_rows
            converged_by_task = {t_id: compute_projection_hash({"row": row}) for t_id, row in converged_rows.items()}
            assert converged_by_task == final_by_task
    finally:
        _cleanup_tenant_data(db_engine, tenant_id)
