"""Transactional outbox relay per tenant stream (ADR-020, DG-1 section 5, C33)."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select, text

from eap.config import get_settings
from eap.db.event_tables import outbox_relay_marks
from eap.db.tables import outbox
from eap.events.binding import BindingProvider, ProducerBinding
from eap.events.ingest import IngestResult, check_and_ingest_event
from eap.events.projection import (
    PROJECTION_NAME_HUD_RUNS,
    load_checkpoint,
    reduce_event,
    save_checkpoint,
)


def stream_advisory_lock_id(tenant_id: str, stream_id: str) -> int:
    """Compute a deterministic 64-bit signed integer advisory lock ID for (tenant, stream)."""
    digest = hashlib.sha256(f"{tenant_id}:{stream_id}".encode()).digest()
    return int(struct.unpack(">q", digest[:8])[0])


@dataclass
class RelayBatchResult:
    """Result summary of a relay polling cycle."""

    tenant_id: str
    stream_id: str
    status: str
    rows_polled: int = 0
    ingested_count: int = 0
    duplicate_count: int = 0
    quarantined_count: int = 0
    last_ingest_offset: int | None = None
    results: list[IngestResult] = field(default_factory=list)


class OutboxRelayWorker:
    """Outbox relay worker enforcing single-relay-per-stream and commit-order offsets (ADR-020)."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        batch_size: int = 500,
    ) -> None:
        if enabled is None:
            self.enabled = bool(get_settings().relay_enabled)
        else:
            self.enabled = enabled
        self.batch_size = batch_size

    def relay_stream_batch(
        self,
        conn: sa.Connection,
        *,
        tenant_id: str,
        stream_id: str,
        binding_provider: BindingProvider | Callable[[str, str], ProducerBinding | None],
        batch_size: int | None = None,
    ) -> RelayBatchResult:
        """Poll and relay a batch of committed outbox events for a single (tenant, stream).

        All operations (advisory lock, select un-relayed, ingest, mark, projection checkpoint)
        execute within the active transaction of `conn`.
        """
        limit = batch_size or self.batch_size
        batch_res = RelayBatchResult(
            tenant_id=tenant_id,
            stream_id=stream_id,
            status="idle",
        )

        # 1. Feature flag check (rollback mechanism: outbox intact when disabled)
        if not self.enabled:
            batch_res.status = "disabled"
            return batch_res

        # 2. Acquire transaction-level advisory lock on (tenant_id, stream_id)
        lock_id = stream_advisory_lock_id(tenant_id, stream_id)
        conn.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id},
        )

        # 3. Query un-relayed committed outbox records
        # Uses subquery check against outbox_relay_marks
        stmt = (
            select(
                outbox.c.id,
                outbox.c.event_id,
                outbox.c.event_type,
                outbox.c.payload,
            )
            .where(
                outbox.c.tenant_id == tenant_id,
                outbox.c.stream_id == stream_id,
                ~sa.exists().where(
                    outbox_relay_marks.c.tenant_id == tenant_id,
                    outbox_relay_marks.c.outbox_id == outbox.c.id,
                ),
            )
            .order_by(outbox.c.id.asc())
            .limit(limit)
        )
        rows = conn.execute(stmt).fetchall()
        batch_res.rows_polled = len(rows)

        if not rows:
            batch_res.status = "idle"
            return batch_res

        # 4. Load current projection checkpoint state for stream
        ckpt = load_checkpoint(
            conn,
            tenant_id=tenant_id,
            stream_id=stream_id,
            projection_name=PROJECTION_NAME_HUD_RUNS,
        )
        current_state: dict[str, Any] | None = ckpt["state"] if ckpt is not None else None
        latest_offset: int | None = ckpt["last_ingest_offset"] if ckpt is not None else None
        state_dirty = False

        # 5. Process each outbox row
        for row in rows:
            outbox_id = int(row[0])
            envelope = dict(row[3])
            producer_id = str(envelope.get("producer_id", ""))
            binding = binding_provider(tenant_id, producer_id)

            res = check_and_ingest_event(
                conn,
                outbox_id=outbox_id,
                envelope=envelope,
                binding=binding,
                stream_id=stream_id,
            )
            batch_res.results.append(res)

            # Record relay mark
            conn.execute(
                outbox_relay_marks.insert().values(
                    tenant_id=tenant_id,
                    outbox_id=outbox_id,
                    outcome=res.status,
                    event_id=res.event_id,
                    stream_id=stream_id,
                    ingest_offset=res.ingest_offset,
                )
            )

            if res.status == "ingested":
                batch_res.ingested_count += 1
                latest_offset = res.ingest_offset
                current_state = reduce_event(current_state, envelope)
                state_dirty = True
            elif res.status == "duplicate":
                batch_res.duplicate_count += 1
            elif res.status == "quarantined":
                batch_res.quarantined_count += 1

        # 6. Save updated projection checkpoint if new events were ingested
        if state_dirty and current_state is not None and latest_offset is not None:
            save_checkpoint(
                conn,
                tenant_id=tenant_id,
                stream_id=stream_id,
                last_ingest_offset=latest_offset,
                state=current_state,
                projection_name=PROJECTION_NAME_HUD_RUNS,
            )

        batch_res.last_ingest_offset = latest_offset
        batch_res.status = "active"
        return batch_res

    def run_relay_stream_until_empty(
        self,
        engine: sa.Engine,
        *,
        tenant_id: str,
        stream_id: str,
        binding_provider: BindingProvider | Callable[[str, str], ProducerBinding | None],
        max_batches: int = 1000,
    ) -> RelayBatchResult:
        """Run relay batches in loop until no un-relayed rows remain for the stream."""
        aggregated = RelayBatchResult(
            tenant_id=tenant_id,
            stream_id=stream_id,
            status="idle",
        )
        for _ in range(max_batches):
            with engine.begin() as conn:
                res = self.relay_stream_batch(
                    conn,
                    tenant_id=tenant_id,
                    stream_id=stream_id,
                    binding_provider=binding_provider,
                )
                aggregated.rows_polled += res.rows_polled
                aggregated.ingested_count += res.ingested_count
                aggregated.duplicate_count += res.duplicate_count
                aggregated.quarantined_count += res.quarantined_count
                aggregated.results.extend(res.results)
                if res.last_ingest_offset is not None:
                    aggregated.last_ingest_offset = res.last_ingest_offset
                aggregated.status = res.status
                if res.rows_polled == 0 or res.status == "disabled":
                    break
        return aggregated


__all__ = [
    "OutboxRelayWorker",
    "RelayBatchResult",
    "stream_advisory_lock_id",
]
