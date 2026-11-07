"""Tests for event_log constraints (C02, C33).

Enforces:
- C02: event_log same (tenant_id, event_id) -> rejected via uq_event_log_event_id.
- C33: event_log same (tenant_id, stream_id, ingest_offset) -> rejected via uq_event_log_stream_offset.
- Same event_id or stream offset in different tenants is allowed.
"""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from eap.db import event_log


def _assert_constraint(exc_info: pytest.ExceptionInfo[IntegrityError], constraint_name: str) -> None:
    orig = exc_info.value.orig
    diag_name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    assert (
        diag_name == constraint_name or constraint_name in str(exc_info.value)
    ), f"Expected constraint '{constraint_name}', got diag={diag_name}, str={exc_info.value}"


def test_c02_duplicate_event_id_rejected(db_conn: sa.Connection) -> None:
    """C02: Inserting same event_id within same tenant is rejected."""
    tenant = "tenant-event-c02"
    event_id = "evt-unique-001"
    now = dt.datetime.now(dt.UTC)

    db_conn.execute(
        event_log.insert().values(
            tenant_id=tenant,
            stream_id="stream-1",
            ingest_offset=1,
            event_id=event_id,
            producer_id="producer-1",
            event_type="run.started",
            payload={"task_id": "t1"},
            occurred_at=now,
        )
    )

    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            event_log.insert().values(
                tenant_id=tenant,
                stream_id="stream-1",
                ingest_offset=2,  # different offset
                event_id=event_id,  # duplicate event_id
                producer_id="producer-1",
                event_type="run.progress",
                payload={"task_id": "t1"},
                occurred_at=now,
            )
        )

    _assert_constraint(exc_info, "uq_event_log_event_id")


def test_c33_duplicate_stream_offset_rejected(db_conn: sa.Connection) -> None:
    """C33: Inserting same (stream_id, ingest_offset) within same tenant is rejected."""
    tenant = "tenant-stream-c33"
    stream = "stream-mono-1"
    now = dt.datetime.now(dt.UTC)

    db_conn.execute(
        event_log.insert().values(
            tenant_id=tenant,
            stream_id=stream,
            ingest_offset=100,
            event_id="evt-100a",
            producer_id="relay-1",
            event_type="run.state_changed",
            payload={"state": "running"},
            occurred_at=now,
        )
    )

    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            event_log.insert().values(
                tenant_id=tenant,
                stream_id=stream,
                ingest_offset=100,  # duplicate offset in stream
                event_id="evt-100b",  # different event_id
                producer_id="relay-1",
                event_type="run.state_changed",
                payload={"state": "succeeded"},
                occurred_at=now,
            )
        )

    _assert_constraint(exc_info, "uq_event_log_stream_offset")


def test_event_log_different_tenant_isolation(db_conn: sa.Connection) -> None:
    """Different tenants can share identical event_id and stream offsets."""
    event_id = "evt-shared-123"
    stream = "stream-default"
    offset = 42
    now = dt.datetime.now(dt.UTC)

    db_conn.execute(
        event_log.insert().values(
            tenant_id="tenant-alpha",
            stream_id=stream,
            ingest_offset=offset,
            event_id=event_id,
            producer_id="p1",
            event_type="run.started",
            payload={},
            occurred_at=now,
        )
    )

    db_conn.execute(
        event_log.insert().values(
            tenant_id="tenant-beta",
            stream_id=stream,
            ingest_offset=offset,
            event_id=event_id,
            producer_id="p2",
            event_type="run.started",
            payload={},
            occurred_at=now,
        )
    )

    count = db_conn.execute(
        sa.select(sa.func.count()).select_from(event_log).where(event_log.c.event_id == event_id)
    ).scalar()
    assert count == 2
