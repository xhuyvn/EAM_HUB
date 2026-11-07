"""Conformance Case C33: Outbox relay offset assignment in commit order (ADR-020).

Scenario:
Two transactions obtain outbox sequential IDs 10 and 11, but transaction 11 commits
BEFORE transaction 10. The single relay assigns ingest_offset strictly in commit order.
Replay from every valid cursor produces an identical projection matching fresh state snapshots,
with zero lost and zero duplicate events.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import select

from eap.db.event_tables import outbox_relay_marks
from eap.db.tables import event_log, outbox
from eap.events.binding import ProducerBinding, StaticBindingProvider
from eap.events.projection import build_fresh_snapshot, replay_stream
from eap.events.relay import OutboxRelayWorker

from .fixtures import make_event_envelope


def test_c33_relay_commit_order_offsets(db_engine: sa.Engine) -> None:
    """Verify C33: Tx 11 commits before Tx 10 -> assigned offsets reflect commit order."""
    tenant = "tenant-c33-test"
    stream_id = "run:run-c33-concurrency"
    producer_id = "worker-c33"

    binding = ProducerBinding(
        tenant_id=tenant,
        producer_id=producer_id,
        binding_id="bind-c33",
        certified_capabilities={"events": True},
    )
    provider = StaticBindingProvider([binding])
    relay = OutboxRelayWorker(enabled=True)

    envelope_10 = make_event_envelope(
        event_id="evt-tx-10",
        tenant_id=tenant,
        event_type="run.started",
        stream_id=stream_id,
        producer_id=producer_id,
        producer_seq=1,
        payload={"execution_ref": "exec-10", "agent_version": "1.0.0"},
    )
    envelope_11 = make_event_envelope(
        event_id="evt-tx-11",
        tenant_id=tenant,
        event_type="run.progress",
        stream_id=stream_id,
        producer_id=producer_id,
        producer_seq=2,
        payload={
            "stage": "Processing batch",
            "completed_units": 10,
            "total_units": 100,
            "unit": "items",
        },
    )

    # 1. Establish two concurrent connections to PostgreSQL
    conn1 = db_engine.connect()
    conn2 = db_engine.connect()

    trans1 = conn1.begin()
    trans2 = conn2.begin()

    # Tx 1 inserts envelope 10
    res1 = conn1.execute(
        outbox.insert()
        .values(
            tenant_id=tenant,
            event_id="evt-tx-10",
            stream_id=stream_id,
            event_type="run.started",
            payload=envelope_10,
        )
        .returning(outbox.c.id)
    )
    id10 = int(res1.fetchone()[0])  # type: ignore[index]

    # Tx 2 inserts envelope 11
    res2 = conn2.execute(
        outbox.insert()
        .values(
            tenant_id=tenant,
            event_id="evt-tx-11",
            stream_id=stream_id,
            event_type="run.progress",
            payload=envelope_11,
        )
        .returning(outbox.c.id)
    )
    id11 = int(res2.fetchone()[0])  # type: ignore[index]

    assert id11 > id10, f"Expected id11 ({id11}) > id10 ({id10})"

    # BARRIER: Force Tx 11 to COMMIT FIRST!
    trans2.commit()
    conn2.close()

    # Tx 1 remains uncommitted at this point.
    # Run the relay cycle while Tx 10 is still in-flight
    with db_engine.begin() as relay_conn:
        batch1 = relay.relay_stream_batch(
            relay_conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )

    # Only Tx 11 was committed and visible to the relay
    assert batch1.rows_polled == 1
    assert batch1.ingested_count == 1
    assert batch1.results[0].event_id == "evt-tx-11"
    assert batch1.results[0].ingest_offset == 1

    # Now Tx 10 commits LATER
    trans1.commit()
    conn1.close()

    # Next relay cycle polls Tx 10
    with db_engine.begin() as relay_conn:
        batch2 = relay.relay_stream_batch(
            relay_conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )

    assert batch2.rows_polled == 1
    assert batch2.ingested_count == 1
    assert batch2.results[0].event_id == "evt-tx-10"
    assert batch2.results[0].ingest_offset == 2

    # Verify event_log ordering and offsets
    with db_engine.connect() as verify_conn:
        stmt = (
            select(
                event_log.c.ingest_offset,
                event_log.c.event_id,
                event_log.c.event_type,
            )
            .where(
                event_log.c.tenant_id == tenant,
                event_log.c.stream_id == stream_id,
            )
            .order_by(event_log.c.ingest_offset.asc())
        )
        logged_events = verify_conn.execute(stmt).fetchall()

        assert len(logged_events) == 2, f"Expected 2 events, got {len(logged_events)}"

        # STRICT COMMIT ORDER: Tx 11 got offset 1, Tx 10 got offset 2!
        assert logged_events[0][0] == 1
        assert logged_events[0][1] == "evt-tx-11"
        assert logged_events[1][0] == 2
        assert logged_events[1][1] == "evt-tx-10"

        # Verify outbox_relay_marks
        stmt_marks = (
            select(outbox_relay_marks.c.outbox_id, outbox_relay_marks.c.ingest_offset)
            .where(
                outbox_relay_marks.c.tenant_id == tenant,
                outbox_relay_marks.c.stream_id == stream_id,
            )
            .order_by(outbox_relay_marks.c.ingest_offset.asc())
        )
        marks = verify_conn.execute(stmt_marks).fetchall()
        assert len(marks) == 2
        assert marks[0] == (id11, 1)
        assert marks[1] == (id10, 2)

        # REPLAY INVARIANT: Replay from every cursor == fresh snapshot
        fresh_state, fresh_hash, fresh_max = build_fresh_snapshot(
            verify_conn,
            tenant_id=tenant,
            stream_id=stream_id,
        )
        assert fresh_max == 2

        # Replay from cursor 0 (full rebuild)
        replay_state_0, replay_hash_0, _ = replay_stream(
            verify_conn,
            tenant_id=tenant,
            stream_id=stream_id,
            from_cursor=0,
        )
        # Replay from cursor 1 (replays offset 2 on top of cursor 1 checkpoint)
        replay_state_1, replay_hash_1, _ = replay_stream(
            verify_conn,
            tenant_id=tenant,
            stream_id=stream_id,
            from_cursor=1,
        )
        # Replay from cursor 2 (already at head)
        replay_state_2, replay_hash_2, _ = replay_stream(
            verify_conn,
            tenant_id=tenant,
            stream_id=stream_id,
            from_cursor=2,
        )

        assert replay_hash_0 == fresh_hash, "Replay from cursor 0 does not match fresh snapshot"
        assert replay_hash_1 == fresh_hash, "Replay from cursor 1 does not match fresh snapshot"
        assert replay_hash_2 == fresh_hash, "Replay from cursor 2 does not match fresh snapshot"
        assert replay_state_0 == fresh_state
        assert replay_state_1 == fresh_state
        assert replay_state_2 == fresh_state

        # Zero lost and zero duplicated events
        assert fresh_state["row"]["run_state"] == "running"
        assert fresh_state["row"]["progress"]["completed_units"] == 10
        assert fresh_state["row"]["progress"]["total_units"] == 100
