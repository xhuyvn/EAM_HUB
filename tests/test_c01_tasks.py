"""Test C01: Task creation idempotency and concurrency.

Conformance Case C01:
- Submit same tenant + client idempotency key twice sequentially -> returns same task ref, exactly 1 task row, 1 outbox event.
- Submit same tenant + client idempotency key concurrently across two DB connections -> exactly 1 task row, both callers receive same task ref, exactly 1 outbox event.
- Same client key across different tenants -> creates distinct tasks (tenant isolation).
"""

from __future__ import annotations

import concurrent.futures
import threading
import uuid

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from eap.contracts.models.task_request import TaskRequest
from eap.db import outbox, runs, tasks
from eap.task.service import TaskService


def make_task_request(idempotency_key: str) -> TaskRequest:
    return TaskRequest(
        contract_version="1.0",
        agent_id="agent-ppm",
        task_type="code.review",
        objective="Run automated code review",
        input_refs=["ref-input-1"],
        resource_refs=["ref-res-1"],
        idempotency_key=idempotency_key,
    )


def test_c01_sequential_same_client_key_returns_same_task(db_engine: Engine) -> None:
    """Sequential duplicate submission returns existing task without duplicate rows or events."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    req = make_task_request(client_key)

    with SessionLocal() as db1:
        task1 = TaskService.create(db1, tenant_id=tenant_id, request=req)

    with SessionLocal() as db2:
        task2 = TaskService.create(db2, tenant_id=tenant_id, request=req)

    assert task1.id == task2.id
    assert task1.task_intent_key == task2.task_intent_key
    assert task1.client_idempotency_key == client_key

    # Verify DB counts: exactly 1 task, 1 run, 1 outbox row
    with SessionLocal() as db:
        task_count = db.scalar(
            sa.select(sa.func.count()).select_from(tasks).where(
                tasks.c.tenant_id == tenant_id,
                tasks.c.client_idempotency_key == client_key,
            )
        )
        assert task_count == 1

        run_count = db.scalar(
            sa.select(sa.func.count()).select_from(runs).where(
                runs.c.tenant_id == tenant_id,
                runs.c.task_id == task1.id,
            )
        )
        assert run_count == 1

        events = db.execute(
            sa.select(outbox).where(outbox.c.tenant_id == tenant_id)
        ).mappings().all()
        task_events = [e for e in events if e["payload"].get("task_id") == task1.id]
        assert len(task_events) == 1
        assert task_events[0]["event_type"] == "run.state_changed"


def test_c01_concurrent_same_client_key_two_connections(db_engine: Engine) -> None:
    """Two concurrent submissions with same tenant and client key result in 1 task row and 1 outbox event."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    req = make_task_request(client_key)

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def worker() -> None:
        try:
            with SessionLocal() as session:
                barrier.wait(timeout=5)
                task = TaskService.create(session, tenant_id=tenant_id, request=req)
                results.append(task)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker)
        f2 = executor.submit(worker)
        concurrent.futures.wait([f1, f2], timeout=10)

    assert not errors, f"Concurrent workers raised errors: {errors}"
    assert len(results) == 2
    # Both callers get the exact same task reference
    assert results[0].id == results[1].id
    assert results[0].task_intent_key == results[1].task_intent_key

    # Verify database state: exactly 1 task and 1 outbox event
    with SessionLocal() as db:
        task_count = db.scalar(
            sa.select(sa.func.count()).select_from(tasks).where(
                tasks.c.tenant_id == tenant_id,
                tasks.c.client_idempotency_key == client_key,
            )
        )
        assert task_count == 1

        task_id = results[0].id
        events = db.execute(
            sa.select(outbox).where(outbox.c.tenant_id == tenant_id)
        ).mappings().all()
        task_events = [e for e in events if e["payload"].get("task_id") == task_id]
        assert len(task_events) == 1


def test_c01_same_client_key_different_tenants_allowed(db_engine: Engine) -> None:
    """Same client idempotency key in different tenants creates independent tasks."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant_a = f"tenant-{uuid.uuid4().hex[:8]}"
    tenant_b = f"tenant-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    req = make_task_request(client_key)

    with SessionLocal() as db_a:
        task_a = TaskService.create(db_a, tenant_id=tenant_a, request=req)

    with SessionLocal() as db_b:
        task_b = TaskService.create(db_b, tenant_id=tenant_b, request=req)

    assert task_a.id != task_b.id
    assert task_a.tenant_id == tenant_a
    assert task_b.tenant_id == tenant_b
    assert task_a.task_intent_key != task_b.task_intent_key
