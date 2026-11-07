"""Throughput smoke benchmark for outbox relay and projection (P1a-WP3 / DG1-C4 preparation).

Measures unthresholded local database events/s for 10,000 events.
Reports metrics for the P1a-WP3 acceptance packet.
"""

from __future__ import annotations

import time

import sqlalchemy as sa
from sqlalchemy import select

from eap.db.tables import event_log, outbox
from eap.events.binding import ProducerBinding, StaticBindingProvider
from eap.events.relay import OutboxRelayWorker

from .fixtures import make_event_envelope


def test_outbox_relay_10k_throughput_smoke(db_engine: sa.Engine) -> None:
    """Smoke test: relay 10,000 events on local PostgreSQL 18.6 and report events/s."""
    tenant = "tenant-smoke-10k"
    stream_id = "run:run-smoke-10k"
    producer_id = "worker-smoke"
    total_events = 10_000
    batch_size = 1000

    binding = ProducerBinding(
        tenant_id=tenant,
        producer_id=producer_id,
        binding_id="bind-smoke",
        certified_capabilities={"events": True},
    )
    provider = StaticBindingProvider([binding])

    print(f"\n[Throughput Smoke] Preparing {total_events} outbox rows...")
    t_prep_start = time.perf_counter()

    # Pre-generate rows
    rows_to_insert: list[dict] = []
    for i in range(1, total_events + 1):
        env = make_event_envelope(
            event_id=f"evt-10k-{i:06d}",
            tenant_id=tenant,
            event_type="run.progress" if i > 1 else "run.started",
            stream_id=stream_id,
            producer_id=producer_id,
            producer_seq=i,
            payload={
                "stage": f"Unit {i}",
                "completed_units": i,
                "total_units": total_events,
                "unit": "steps",
            }
            if i > 1
            else {"execution_ref": "exec-smoke", "agent_version": "1.0.0"},
        )
        rows_to_insert.append(
            {
                "tenant_id": tenant,
                "event_id": f"evt-10k-{i:06d}",
                "stream_id": stream_id,
                "event_type": "run.progress" if i > 1 else "run.started",
                "payload": env,
            }
        )

    # Bulk insert into outbox in chunks
    insert_chunk_size = 2500
    with db_engine.begin() as conn:
        for offset in range(0, total_events, insert_chunk_size):
            chunk = rows_to_insert[offset : offset + insert_chunk_size]
            conn.execute(outbox.insert(), chunk)

    t_prep = time.perf_counter() - t_prep_start
    print(f"[Throughput Smoke] Outbox seeded with {total_events} rows in {t_prep:.2f}s.")

    # Run the relay benchmark
    relay = OutboxRelayWorker(enabled=True, batch_size=batch_size)

    t_relay_start = time.perf_counter()
    res = relay.run_relay_stream_until_empty(
        db_engine,
        tenant_id=tenant,
        stream_id=stream_id,
        binding_provider=provider,
        max_batches=50,
    )
    elapsed = time.perf_counter() - t_relay_start

    events_per_sec = total_events / elapsed if elapsed > 0 else 0.0

    print(f"[Throughput Smoke] Relayed {res.ingested_count} events in {elapsed:.3f}s.")
    print(f"[Throughput Smoke] Unthresholded Throughput: {events_per_sec:.1f} events/s.")

    assert res.ingested_count == total_events, (
        f"Expected {total_events} ingested, got {res.ingested_count}"
    )
    assert res.last_ingest_offset == total_events

    with db_engine.connect() as conn:
        logged = conn.execute(
            select(sa.func.count())
            .select_from(event_log)
            .where(
                event_log.c.tenant_id == tenant,
                event_log.c.stream_id == stream_id,
            )
        ).scalar()
        assert logged == total_events
