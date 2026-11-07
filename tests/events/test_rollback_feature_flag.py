"""Tests for relay feature flag (relay.enabled) and rollback safety (docs/IMPLEMENTATION_PLAN.md).

Scenario:
Disabling the relay halts all outbox processing while keeping all un-relayed outbox rows
intact in PostgreSQL. Re-enabling the relay processes the backlog with zero loss.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import select

from eap.db.event_tables import outbox_relay_marks
from eap.db.tables import event_log, outbox
from eap.events.binding import ProducerBinding, StaticBindingProvider
from eap.events.relay import OutboxRelayWorker

from .fixtures import make_event_envelope


def test_relay_feature_flag_disabled_preserves_outbox(db_engine: sa.Engine) -> None:
    """When relay.enabled = False, outbox rows remain untouched and un-relayed."""
    tenant = "tenant-flag-test"
    stream_id = "run:run-flag-1"
    producer_id = "worker-flag"

    binding = ProducerBinding(
        tenant_id=tenant,
        producer_id=producer_id,
        binding_id="bind-flag",
        certified_capabilities={"events": True},
    )
    provider = StaticBindingProvider([binding])

    # 1. Insert 5 outbox events
    with db_engine.begin() as conn:
        for i in range(1, 6):
            env = make_event_envelope(
                event_id=f"evt-flag-{i}",
                tenant_id=tenant,
                event_type="run.progress" if i > 1 else "run.started",
                stream_id=stream_id,
                producer_id=producer_id,
                producer_seq=i,
                payload={"stage": f"Step {i}", "completed_units": i, "total_units": 10, "unit": "steps"}
                if i > 1
                else {"execution_ref": "exec-1", "agent_version": "1.0.0"},
            )
            conn.execute(
                outbox.insert().values(
                    tenant_id=tenant,
                    event_id=f"evt-flag-{i}",
                    stream_id=stream_id,
                    event_type="run.progress" if i > 1 else "run.started",
                    payload=env,
                )
            )

    # 2. Worker with relay disabled
    disabled_worker = OutboxRelayWorker(enabled=False)
    with db_engine.begin() as conn:
        res = disabled_worker.relay_stream_batch(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )

    assert res.status == "disabled"
    assert res.rows_polled == 0
    assert res.ingested_count == 0

    # Verify outbox rows and event_log remain untouched
    with db_engine.connect() as conn:
        marks_count = conn.execute(
            select(sa.func.count())
            .select_from(outbox_relay_marks)
            .where(outbox_relay_marks.c.tenant_id == tenant)
        ).scalar()
        log_count = conn.execute(
            select(sa.func.count())
            .select_from(event_log)
            .where(event_log.c.tenant_id == tenant)
        ).scalar()
        assert marks_count == 0, "No relay marks must be written while disabled"
        assert log_count == 0, "No events must be appended while disabled"

    # 3. Enable relay (rollback reversed)
    enabled_worker = OutboxRelayWorker(enabled=True)
    with db_engine.begin() as conn:
        res2 = enabled_worker.relay_stream_batch(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )

    assert res2.status == "active"
    assert res2.rows_polled == 5
    assert res2.ingested_count == 5
    assert res2.last_ingest_offset == 5

    # Verify all 5 rows were ingested in event_log
    with db_engine.connect() as conn:
        log_count = conn.execute(
            select(sa.func.count())
            .select_from(event_log)
            .where(event_log.c.tenant_id == tenant)
        ).scalar()
        assert log_count == 5
