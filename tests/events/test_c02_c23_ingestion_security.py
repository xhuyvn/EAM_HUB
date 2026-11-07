"""Conformance Cases C02 and C23: Ingestion isolation and conflict quarantine.

Enforces:
- C23: Producer event with tenant_id differing from binding -> quarantined in event_quarantine,
  security event emitted in security_events, projection hash unchanged.
- C02: Same event_id with divergent payload -> quarantined in event_quarantine,
  security event emitted, original event_log row NOT overwritten, projection hash unchanged.
- Idempotency: Same event_id with identical payload -> marked duplicate, no quarantine, no overwrite.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import select

from eap.db.event_tables import event_quarantine, outbox_relay_marks, security_events
from eap.db.tables import event_log, outbox
from eap.events.binding import ProducerBinding, StaticBindingProvider
from eap.events.projection import build_fresh_snapshot
from eap.events.relay import OutboxRelayWorker

from .fixtures import make_event_envelope


def test_c23_tenant_mismatch_quarantine_and_security_event(db_engine: sa.Engine) -> None:
    """C23: event tenant_id != binding tenant -> quarantine + security event, projection unchanged."""
    legit_tenant = "tenant-corp-c23"
    attacker_tenant = "tenant-attacker-c23"
    stream_id = "run:run-c23-sec"
    producer_id = "worker-c23"

    binding = ProducerBinding(
        tenant_id=legit_tenant,
        producer_id=producer_id,
        binding_id="bind-corp",
        certified_capabilities={"events": True},
    )
    provider = StaticBindingProvider([binding])
    relay = OutboxRelayWorker(enabled=True)

    # 1. First ingest a valid initial event for legit_tenant
    valid_env = make_event_envelope(
        event_id="evt-c23-valid",
        tenant_id=legit_tenant,
        event_type="run.started",
        stream_id=stream_id,
        producer_id=producer_id,
        producer_seq=1,
        payload={"execution_ref": "exec-legit", "agent_version": "1.0.0"},
    )
    with db_engine.begin() as conn:
        conn.execute(
            outbox.insert().values(
                tenant_id=legit_tenant,
                event_id="evt-c23-valid",
                stream_id=stream_id,
                event_type="run.started",
                payload=valid_env,
            )
        )
        relay.relay_stream_batch(
            conn,
            tenant_id=legit_tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )

    # Get baseline projection hash
    with db_engine.connect() as conn:
        _, initial_hash, offset = build_fresh_snapshot(
            conn,
            tenant_id=legit_tenant,
            stream_id=stream_id,
        )
        assert offset == 1

    # 2. Forge an event where body tenant_id = attacker_tenant
    forged_env = make_event_envelope(
        event_id="evt-c23-forged",
        tenant_id=attacker_tenant,  # Differs from binding tenant (legit_tenant)
        event_type="run.finished",
        stream_id=stream_id,
        producer_id=producer_id,
        producer_seq=2,
        payload={"outcome": "succeeded", "result_ref": "res-forged"},
    )
    with db_engine.begin() as conn:
        res = conn.execute(
            outbox.insert()
            .values(
                tenant_id=legit_tenant,
                event_id="evt-c23-forged",
                stream_id=stream_id,
                event_type="run.finished",
                payload=forged_env,
            )
            .returning(outbox.c.id)
        )
        forged_outbox_id = int(res.fetchone()[0])  # type: ignore[index]

        # Run relay
        batch_res = relay.relay_stream_batch(
            conn,
            tenant_id=legit_tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )

    assert batch_res.quarantined_count == 1
    assert batch_res.ingested_count == 0

    # 3. Verify quarantine table, security_events, and projection hash
    with db_engine.connect() as conn:
        # Check outbox_relay_marks
        mark = conn.execute(
            select(outbox_relay_marks.c.outcome).where(
                outbox_relay_marks.c.tenant_id == legit_tenant,
                outbox_relay_marks.c.outbox_id == forged_outbox_id,
            )
        ).scalar()
        assert mark == "quarantined"

        # Check event_quarantine
        q_row = conn.execute(
            select(
                event_quarantine.c.reason,
                event_quarantine.c.detail,
                event_quarantine.c.envelope,
            ).where(
                event_quarantine.c.tenant_id == legit_tenant,
                event_quarantine.c.event_id == "evt-c23-forged",
            )
        ).fetchone()
        assert q_row is not None
        assert q_row[0] == "tenant_mismatch"
        assert q_row[1]["binding_tenant"] == legit_tenant
        assert q_row[1]["event_tenant"] == attacker_tenant

        # Check security_events
        sec_row = conn.execute(
            select(
                security_events.c.kind,
                security_events.c.subject_event_id,
                security_events.c.detail,
            ).where(
                security_events.c.tenant_id == legit_tenant,
                security_events.c.subject_event_id == "evt-c23-forged",
            )
        ).fetchone()
        assert sec_row is not None
        assert sec_row[0] == "tenant_mismatch"

        # Check event_log NOT appended
        log_count = conn.execute(
            select(sa.func.count())
            .select_from(event_log)
            .where(
                event_log.c.tenant_id == legit_tenant,
                event_log.c.stream_id == stream_id,
            )
        ).scalar()
        assert log_count == 1

        # Check PROJECTION UNCHANGED
        _, post_hash, post_offset = build_fresh_snapshot(
            conn,
            tenant_id=legit_tenant,
            stream_id=stream_id,
        )
        assert post_offset == 1
        assert post_hash == initial_hash, "Projection hash must not change after quarantined event"


def test_c02_event_content_conflict_quarantine(db_engine: sa.Engine) -> None:
    """C02: Same event_id with different content -> quarantine, no overwrite, projection unchanged."""
    tenant = "tenant-c02-test"
    stream_id = "run:run-c02-conflict"
    producer_id = "worker-c02"

    binding = ProducerBinding(
        tenant_id=tenant,
        producer_id=producer_id,
        binding_id="bind-c02",
        certified_capabilities={"events": True},
    )
    provider = StaticBindingProvider([binding])
    relay = OutboxRelayWorker(enabled=True)

    # 1. Ingest event 1
    env1 = make_event_envelope(
        event_id="evt-c02-target",
        tenant_id=tenant,
        event_type="run.started",
        stream_id=stream_id,
        producer_id=producer_id,
        producer_seq=1,
        payload={"execution_ref": "exec-ORIGINAL", "agent_version": "1.0.0"},
    )
    with db_engine.begin() as conn:
        conn.execute(
            outbox.insert().values(
                tenant_id=tenant,
                event_id="evt-c02-target",
                stream_id=stream_id,
                event_type="run.started",
                payload=env1,
            )
        )
        relay.relay_stream_batch(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )

    # Get baseline projection hash
    with db_engine.connect() as conn:
        _, initial_hash, offset = build_fresh_snapshot(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
        )
        assert offset == 1

    # 2. Conflicting event with same event_id but DIFFERENT content / payload
    env2_conflicted = make_event_envelope(
        event_id="evt-c02-target",
        tenant_id=tenant,
        event_type="run.started",
        stream_id=stream_id,
        producer_id=producer_id,
        producer_seq=2,
        payload={"execution_ref": "exec-TAMPERED", "agent_version": "2.0.0"},
    )
    with db_engine.begin() as conn:
        res = conn.execute(
            outbox.insert()
            .values(
                tenant_id=tenant,
                event_id="evt-c02-target",
                stream_id=stream_id,
                event_type="run.started",
                payload=env2_conflicted,
            )
            .returning(outbox.c.id)
        )
        conflicted_outbox_id = int(res.fetchone()[0])  # type: ignore[index]

        batch_res = relay.relay_stream_batch(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )

    assert batch_res.quarantined_count == 1
    assert batch_res.ingested_count == 0

    # Verify quarantine and security event
    with db_engine.connect() as conn:
        mark = conn.execute(
            select(outbox_relay_marks.c.outcome).where(
                outbox_relay_marks.c.tenant_id == tenant,
                outbox_relay_marks.c.outbox_id == conflicted_outbox_id,
            )
        ).scalar()
        assert mark == "quarantined"

        q_row = conn.execute(
            select(
                event_quarantine.c.reason,
                event_quarantine.c.detail,
            ).where(
                event_quarantine.c.tenant_id == tenant,
                event_quarantine.c.event_id == "evt-c02-target",
            )
        ).fetchone()
        assert q_row is not None
        assert q_row[0] == "event_content_conflict"

        sec_row = conn.execute(
            select(security_events.c.kind).where(
                security_events.c.tenant_id == tenant,
                security_events.c.subject_event_id == "evt-c02-target",
            )
        ).scalar()
        assert sec_row == "event_content_conflict"

        # Verify NO OVERWRITE in event_log
        log_rows = conn.execute(
            select(event_log.c.payload).where(
                event_log.c.tenant_id == tenant,
                event_log.c.event_id == "evt-c02-target",
            )
        ).fetchall()
        assert len(log_rows) == 1
        # Original content preserved
        assert log_rows[0][0]["payload"]["execution_ref"] == "exec-ORIGINAL"

        # Verify PROJECTION UNCHANGED
        _, post_hash, post_offset = build_fresh_snapshot(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
        )
        assert post_offset == 1
        assert post_hash == initial_hash


def test_c02_idempotent_duplicate_no_op(db_engine: sa.Engine) -> None:
    """Same event_id with identical payload -> marked duplicate, no quarantine, no overwrite."""
    tenant = "tenant-c02-dup-test"
    stream_id = "run:run-c02-dup"
    producer_id = "worker-c02"

    binding = ProducerBinding(
        tenant_id=tenant,
        producer_id=producer_id,
        binding_id="bind-c02",
        certified_capabilities={"events": True},
    )
    provider = StaticBindingProvider([binding])
    relay = OutboxRelayWorker(enabled=True)

    env = make_event_envelope(
        event_id="evt-c02-idempotent",
        tenant_id=tenant,
        event_type="run.started",
        stream_id=stream_id,
        producer_id=producer_id,
        producer_seq=1,
        payload={"execution_ref": "exec-1", "agent_version": "1.0.0"},
    )

    with db_engine.begin() as conn:
        conn.execute(
            outbox.insert().values(
                tenant_id=tenant,
                event_id="evt-c02-idempotent",
                stream_id=stream_id,
                event_type="run.started",
                payload=env,
            )
        )
        relay.relay_stream_batch(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )

    # Re-insert exact duplicate
    with db_engine.begin() as conn:
        res = conn.execute(
            outbox.insert()
            .values(
                tenant_id=tenant,
                event_id="evt-c02-idempotent",
                stream_id=stream_id,
                event_type="run.started",
                payload=env,
            )
            .returning(outbox.c.id)
        )
        dup_outbox_id = int(res.fetchone()[0])  # type: ignore[index]

        batch_res = relay.relay_stream_batch(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )

    assert batch_res.duplicate_count == 1
    assert batch_res.quarantined_count == 0
    assert batch_res.ingested_count == 0

    with db_engine.connect() as conn:
        mark = conn.execute(
            select(outbox_relay_marks.c.outcome).where(
                outbox_relay_marks.c.tenant_id == tenant,
                outbox_relay_marks.c.outbox_id == dup_outbox_id,
            )
        ).scalar()
        assert mark == "duplicate"

        # Quarantine table has 0 rows
        q_count = conn.execute(
            select(sa.func.count())
            .select_from(event_quarantine)
            .where(event_quarantine.c.event_id == "evt-c02-idempotent")
        ).scalar()
        assert q_count == 0


def test_gap_detection_recorded_in_stream_heads(db_engine: sa.Engine) -> None:
    """Gap detection: Producer jumps sequence (e.g. seq 1 then seq 4) -> gap recorded in stream_heads."""
    from eap.db.event_tables import stream_heads

    tenant = "tenant-gap-test"
    stream_id = "run:run-gap-1"
    producer_id = "worker-gap"

    binding = ProducerBinding(
        tenant_id=tenant,
        producer_id=producer_id,
        binding_id="bind-gap",
        certified_capabilities={"events": True},
    )
    provider = StaticBindingProvider([binding])
    relay = OutboxRelayWorker(enabled=True)

    env1 = make_event_envelope(
        event_id="evt-gap-1",
        tenant_id=tenant,
        event_type="run.started",
        stream_id=stream_id,
        producer_id=producer_id,
        producer_seq=1,
        payload={"execution_ref": "exec-1", "agent_version": "1.0.0"},
    )
    # Jump to seq 4 (missing seq 2 and 3)
    env4 = make_event_envelope(
        event_id="evt-gap-4",
        tenant_id=tenant,
        event_type="run.progress",
        stream_id=stream_id,
        producer_id=producer_id,
        producer_seq=4,
        payload={"stage": "Step 4", "completed_units": 4, "total_units": 10, "unit": "steps"},
    )

    with db_engine.begin() as conn:
        conn.execute(
            outbox.insert().values(
                tenant_id=tenant,
                event_id="evt-gap-1",
                stream_id=stream_id,
                event_type="run.started",
                payload=env1,
            )
        )
        conn.execute(
            outbox.insert().values(
                tenant_id=tenant,
                event_id="evt-gap-4",
                stream_id=stream_id,
                event_type="run.progress",
                payload=env4,
            )
        )
        batch_res = relay.relay_stream_batch(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider,
        )

    assert batch_res.ingested_count == 2
    # Verify gap was detected
    assert any(r.gap_detected for r in batch_res.results)

    with db_engine.connect() as conn:
        state = conn.execute(
            select(stream_heads.c.producer_state).where(
                stream_heads.c.tenant_id == tenant,
                stream_heads.c.stream_id == stream_id,
            )
        ).scalar()
        assert state is not None
        assert "gaps" in state
        gaps = state["gaps"]
        assert len(gaps) == 1
        assert gaps[0]["expected_seq"] == 2
        assert gaps[0]["received_seq"] == 4

