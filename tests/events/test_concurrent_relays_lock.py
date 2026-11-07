"""Tests for concurrent relay workers serialized by advisory lock (ADR-020, DG-1 section 5).

Scenario:
Two relay workers run concurrently for the exact same (tenant, stream).
pg_advisory_xact_lock serializes execution; offsets remain unique and strictly contiguous.
Zero collisions or duplicate offset errors.
"""

from __future__ import annotations

import concurrent.futures

import sqlalchemy as sa
from sqlalchemy import select

from eap.db.event_tables import outbox_relay_marks
from eap.db.tables import event_log, outbox
from eap.events.binding import ProducerBinding, StaticBindingProvider
from eap.events.relay import OutboxRelayWorker

from .fixtures import make_event_envelope


def test_concurrent_relays_serialized_by_advisory_lock(db_engine: sa.Engine) -> None:
    """Two concurrent relays on same stream -> advisory lock serializes, contiguous offsets."""
    tenant = "tenant-lock-test"
    stream_id = "run:run-lock-concurrency"
    producer_id = "worker-lock"
    num_events = 40

    binding = ProducerBinding(
        tenant_id=tenant,
        producer_id=producer_id,
        binding_id="bind-lock",
        certified_capabilities={"events": True},
    )
    provider = StaticBindingProvider([binding])

    # 1. Seed outbox events
    with db_engine.begin() as conn:
        for i in range(1, num_events + 1):
            env = make_event_envelope(
                event_id=f"evt-lock-{i:03d}",
                tenant_id=tenant,
                event_type="run.progress" if i > 1 else "run.started",
                stream_id=stream_id,
                producer_id=producer_id,
                producer_seq=i,
                payload={
                    "stage": f"Step {i}",
                    "completed_units": i,
                    "total_units": num_events,
                    "unit": "steps",
                }
                if i > 1
                else {"execution_ref": "exec-lock", "agent_version": "1.0.0"},
            )
            conn.execute(
                outbox.insert().values(
                    tenant_id=tenant,
                    event_id=f"evt-lock-{i:03d}",
                    stream_id=stream_id,
                    event_type="run.progress" if i > 1 else "run.started",
                    payload=env,
                )
            )

    # 2. Run two relay workers concurrently in parallel threads
    relay1 = OutboxRelayWorker(enabled=True, batch_size=10)
    relay2 = OutboxRelayWorker(enabled=True, batch_size=10)

    def worker_run(worker: OutboxRelayWorker) -> int:
        res = worker.run_relay_stream_until_empty(
            db_engine,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider,
            max_batches=50,
        )
        return res.ingested_count

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker_run, relay1)
        f2 = executor.submit(worker_run, relay2)
        count1 = f1.result(timeout=20)
        count2 = f2.result(timeout=20)

    # 3. Assert total ingested equals total created
    assert count1 + count2 == num_events, (
        f"Expected {num_events} total ingested, got {count1} + {count2}"
    )

    # 4. Verify event_log offsets: strictly contiguous 1..num_events
    with db_engine.connect() as conn:
        rows = conn.execute(
            select(event_log.c.ingest_offset, event_log.c.event_id)
            .where(
                event_log.c.tenant_id == tenant,
                event_log.c.stream_id == stream_id,
            )
            .order_by(event_log.c.ingest_offset.asc())
        ).fetchall()

        assert len(rows) == num_events
        offsets = [int(r[0]) for r in rows]
        expected_offsets = list(range(1, num_events + 1))
        assert offsets == expected_offsets, "Offsets must be strictly contiguous without gaps"

        # Unique event_ids
        event_ids = [str(r[1]) for r in rows]
        assert len(set(event_ids)) == num_events, "All event IDs must be unique"

        # Check outbox_relay_marks
        marks_count = conn.execute(
            select(sa.func.count())
            .select_from(outbox_relay_marks)
            .where(
                outbox_relay_marks.c.tenant_id == tenant,
                outbox_relay_marks.c.stream_id == stream_id,
                outbox_relay_marks.c.outcome == "ingested",
            )
        ).scalar()
        assert marks_count == num_events
