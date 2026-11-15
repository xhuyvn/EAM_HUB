"""Server-side project grant management, grant_epoch bumping, policy.changed outbox events, and retention floors (P3a-WP2)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from eap.db.policy_tables import (
    principal_grant_epochs,
    principal_project_grants,
    stream_retention_floors,
)
from eap.db.tables import event_log, outbox
from eap.events.binding import ProducerBinding
from eap.events.relay import OutboxRelayWorker, stream_advisory_lock_id


def policy_binding_provider(tenant_id: str, producer_id: str) -> ProducerBinding | None:
    """Authorize the internal policy_service producer to emit policy.changed events."""
    if producer_id != "policy_service":
        return None
    return ProducerBinding(
        tenant_id=tenant_id,
        producer_id=producer_id,
        binding_id="binding-policy-service-internal",
        certified_capabilities={"events": True},
    )


def get_grant_epoch(
    conn: sa.Connection,
    *,
    tenant_id: str,
    principal_id: str,
) -> int:
    """Return current server-side grant_epoch for (tenant_id, principal_id), defaulting to 0."""
    val = conn.execute(
        sa.select(principal_grant_epochs.c.grant_epoch).where(
            principal_grant_epochs.c.tenant_id == tenant_id,
            principal_grant_epochs.c.principal_id == principal_id,
        )
    ).scalar_one_or_none()
    return int(val) if val is not None else 0


def get_revoked_project_ids(
    conn: sa.Connection,
    *,
    tenant_id: str,
    principal_id: str,
) -> set[str]:
    """Return set of project IDs explicitly revoked for (tenant_id, principal_id)."""
    rows = conn.execute(
        sa.select(principal_project_grants.c.project_id).where(
            principal_project_grants.c.tenant_id == tenant_id,
            principal_project_grants.c.principal_id == principal_id,
            principal_project_grants.c.granted.is_(False),
        )
    ).fetchall()
    return {str(r[0]) for r in rows}


def is_project_authorized(
    conn: sa.Connection,
    *,
    tenant_id: str,
    principal_id: str,
    project_id: str | None,
) -> bool:
    """Return True if principal currently retains access to project_id (revoked -> False)."""
    if not project_id:
        return True
    row = conn.execute(
        sa.select(principal_project_grants.c.granted).where(
            principal_project_grants.c.tenant_id == tenant_id,
            principal_project_grants.c.principal_id == principal_id,
            principal_project_grants.c.project_id == project_id,
        )
    ).scalar_one_or_none()
    if row is None:
        return True
    return bool(row)


def update_project_grant(
    conn: sa.Connection,
    *,
    tenant_id: str,
    principal_id: str,
    project_id: str,
    granted: bool,
    actor_id: str = "system",
    reason: str | None = None,
    relay_immediately: bool = True,
) -> dict[str, Any]:
    """Atomically update project grant, increment grant_epoch, and emit policy.changed event.

    In PostgreSQL, acquires the per-stream advisory lock for `policy:<tenant_id>`,
    upserts `principal_project_grants`, increments `principal_grant_epochs.grant_epoch`,
    appends `policy.changed` to `outbox`, and optionally relays it to `event_log` inside
    the same transaction so active SSE streams observe revocation with minimal latency.
    """
    now = dt.datetime.now(dt.UTC)
    policy_stream = f"policy:{tenant_id}"
    resolved_reason = reason or ("grant_restored" if granted else "permission_revoked")

    if conn.dialect.name == "postgresql":
        lock_id = stream_advisory_lock_id(tenant_id, policy_stream)
        conn.execute(sa.text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})

    # 1. Upsert project grant state
    grant_stmt = (
        postgresql.insert(principal_project_grants)
        .values(
            tenant_id=tenant_id,
            principal_id=principal_id,
            project_id=project_id,
            granted=bool(granted),
            reason=resolved_reason,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "principal_id", "project_id"],
            set_={
                "granted": bool(granted),
                "reason": resolved_reason,
                "updated_at": now,
            },
        )
    )
    conn.execute(grant_stmt)

    # 2. Atomically increment grant_epoch for (tenant_id, principal_id)
    epoch_stmt = (
        postgresql.insert(principal_grant_epochs)
        .values(
            tenant_id=tenant_id,
            principal_id=principal_id,
            grant_epoch=1,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "principal_id"],
            set_={
                "grant_epoch": principal_grant_epochs.c.grant_epoch + 1,
                "updated_at": now,
            },
        )
        .returning(principal_grant_epochs.c.grant_epoch)
    )
    new_epoch = int(conn.execute(epoch_stmt).scalar_one())

    # 3. Compute contiguous producer_seq on policy:<tenant_id>
    existing_outbox_count = (
        conn.execute(
            sa.select(sa.func.count())
            .select_from(outbox)
            .where(
                outbox.c.tenant_id == tenant_id,
                outbox.c.stream_id == policy_stream,
            )
        ).scalar_one()
        or 0
    )
    producer_seq = int(existing_outbox_count) + 1

    event_id = f"evt-pol-{uuid.uuid4().hex}"
    envelope = {
        "contract_version": "1.1",
        "event_id": event_id,
        "tenant_id": tenant_id,
        "task_id": None,
        "run_id": None,
        "attempt": None,
        "producer_id": "policy_service",
        "producer_seq": producer_seq,
        "occurred_at": now.isoformat().replace("+00:00", "Z"),
        "event_type": "policy.changed",
        "payload": {
            "principal_id": principal_id,
            "project_id": project_id,
            "granted": bool(granted),
            "grant_epoch": new_epoch,
            "actor_id": actor_id,
            "reason": resolved_reason,
        },
    }
    conn.execute(
        outbox.insert().values(
            tenant_id=tenant_id,
            event_id=event_id,
            stream_id=policy_stream,
            event_type="policy.changed",
            payload=envelope,
            created_at=now,
        )
    )

    relayed_offset: int | None = None
    if relay_immediately:
        res = OutboxRelayWorker(enabled=True).relay_stream_batch(
            conn,
            tenant_id=tenant_id,
            stream_id=policy_stream,
            binding_provider=policy_binding_provider,
        )
        relayed_offset = res.last_ingest_offset

    return {
        "tenant_id": tenant_id,
        "principal_id": principal_id,
        "project_id": project_id,
        "granted": bool(granted),
        "grant_epoch": new_epoch,
        "event_id": event_id,
        "relayed_offset": relayed_offset,
        "reason": resolved_reason,
        "updated_at": now.isoformat().replace("+00:00", "Z"),
    }


def revoke_project_access(
    conn: sa.Connection,
    *,
    tenant_id: str,
    principal_id: str,
    project_id: str,
    actor_id: str = "system",
    reason: str = "permission_revoked",
    relay_immediately: bool = True,
) -> dict[str, Any]:
    """Revoke project access for a principal, bumping grant_epoch and emitting policy.changed."""
    return update_project_grant(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        project_id=project_id,
        granted=False,
        actor_id=actor_id,
        reason=reason,
        relay_immediately=relay_immediately,
    )


def grant_project_access(
    conn: sa.Connection,
    *,
    tenant_id: str,
    principal_id: str,
    project_id: str,
    actor_id: str = "system",
    reason: str = "grant_restored",
    relay_immediately: bool = True,
) -> dict[str, Any]:
    """Restore/grant project access for a principal, bumping grant_epoch and emitting policy.changed."""
    return update_project_grant(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        project_id=project_id,
        granted=True,
        actor_id=actor_id,
        reason=reason,
        relay_immediately=relay_immediately,
    )


def set_stream_retention_floor(
    conn: sa.Connection,
    *,
    tenant_id: str,
    stream_id: str,
    min_retained_offset: int,
    purge_event_log: bool = False,
) -> None:
    """Set minimum retained ingest_offset for a stream and optionally purge older event_log rows."""
    if min_retained_offset < 1:
        raise ValueError("min_retained_offset must be >= 1")
    now = dt.datetime.now(dt.UTC)
    stmt = (
        postgresql.insert(stream_retention_floors)
        .values(
            tenant_id=tenant_id,
            stream_id=stream_id,
            min_retained_offset=int(min_retained_offset),
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "stream_id"],
            set_={
                "min_retained_offset": int(min_retained_offset),
                "updated_at": now,
            },
        )
    )
    conn.execute(stmt)
    if purge_event_log and min_retained_offset > 1:
        conn.execute(
            sa.delete(event_log).where(
                event_log.c.tenant_id == tenant_id,
                event_log.c.stream_id == stream_id,
                event_log.c.ingest_offset < int(min_retained_offset),
            )
        )
