"""Tests for relay crash safety and transaction atomicity (docs/02, ADR-020).

Scenario:
Worker or transaction crashes / aborts between event append and relay mark.
Rolling back ensures no partial state. Re-running the relay proceeds without duplicates
or gaps.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from eap.db.event_tables import outbox_relay_marks, stream_heads
from eap.db.tables import event_log, outbox
from eap.events.binding import ProducerBinding, StaticBindingProvider
from eap.events.projection import build_fresh_snapshot
from eap.events.relay import OutboxRelayWorker

from .fixtures import make_event_envelope


def test_relay_crash_abort_recovery(db_engine: sa.Engine) -> None:
    """Simulate a crash / abort mid-relay and verify clean recovery without duplicate or gaps."""
    tenant = "tenant-crash-test"
    stream_id = "run:run-crash-1"
    producer_id = "worker-crash"

    binding = ProducerBinding(
        tenant_id=tenant,
        producer_id=producer_id,
        binding_id="bind-crash",
        certified_capabilities={"events": True},
    )
    provider = StaticBindingProvider([binding])
    relay = OutboxRelayWorker(enabled=True)

    # 1. Insert 3 outbox rows
    with db_engine.begin() as conn:
        for i in range(1, 4):
            env = make_event_envelope(
                event_id=f"evt-crash-{i}",
                tenant_id=tenant,
                event_type="run.progress" if i > 1 else "run.started",
                stream_id=stream_id,
                producer_id=producer_id,
                producer_seq=i,
                payload={
                    "stage": f"Step {i}",
                    "completed_units": i,
                    "total_units": 10,
                    "unit": "steps",
                }
                if i > 1
                else {"execution_ref": "exec-1", "agent_version": "1.0.0"},
            )
            conn.execute(
                outbox.insert().values(
                    tenant_id=tenant,
                    event_id=f"evt-crash-{i}",
                    stream_id=stream_id,
                    event_type="run.progress" if i > 1 else "run.started",
                    payload=env,
                )
            )

    # 2. Simulate transaction crash / abort mid-relay:
    # We open a connection, run relay batch, and explicitly ROLLBACK or raise error
    with pytest.raises(RuntimeError, match="Simulated worker crash"), db_engine.connect() as conn, conn.begin():
        relay.relay_stream_batch(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )
        # Crash before transaction commit!
        raise RuntimeError("Simulated worker crash mid-relay")

    # Verify that NOTHING was committed
    with db_engine.connect() as conn:
        count_log = conn.execute(
            select(sa.func.count())
            .select_from(event_log)
            .where(
                event_log.c.tenant_id == tenant,
                event_log.c.stream_id == stream_id,
            )
        ).scalar()
        count_marks = conn.execute(
            select(sa.func.count())
            .select_from(outbox_relay_marks)
            .where(
                outbox_relay_marks.c.tenant_id == tenant,
                outbox_relay_marks.c.stream_id == stream_id,
            )
        ).scalar()
        assert count_log == 0, "event_log rows must not persist after aborted transaction"
        assert count_marks == 0, "outbox_relay_marks must not persist after aborted transaction"

    # 3. Rerun the relay worker cleanly
    with db_engine.begin() as conn:
        batch_res = relay.relay_stream_batch(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )

    assert batch_res.rows_polled == 3
    assert batch_res.ingested_count == 3
    assert batch_res.duplicate_count == 0
    assert batch_res.quarantined_count == 0
    assert batch_res.last_ingest_offset == 3

    # Verify event_log and stream_heads
    with db_engine.connect() as conn:
        rows = conn.execute(
            select(event_log.c.ingest_offset, event_log.c.event_id)
            .where(
                event_log.c.tenant_id == tenant,
                event_log.c.stream_id == stream_id,
            )
            .order_by(event_log.c.ingest_offset.asc())
        ).fetchall()

        assert len(rows) == 3
        assert [int(r[0]) for r in rows] == [1, 2, 3], "Offsets must be strictly contiguous"
        assert [str(r[1]) for r in rows] == ["evt-crash-1", "evt-crash-2", "evt-crash-3"]

        # Verify stream head
        head_offset = conn.execute(
            select(stream_heads.c.last_offset).where(
                stream_heads.c.tenant_id == tenant,
                stream_heads.c.stream_id == stream_id,
            )
        ).scalar()
        assert head_offset == 3

        # Fresh snapshot
        state, _state_hash, last_offset = build_fresh_snapshot(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
        )
        assert last_offset == 3
        assert state["row"]["progress"]["completed_units"] == 3

    # 4. Re-running relay again on empty stream is a clean no-op
    with db_engine.begin() as conn:
        empty_batch = relay.relay_stream_batch(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )
    assert empty_batch.rows_polled == 0
    assert empty_batch.ingested_count == 0
    assert empty_batch.status == "idle"
