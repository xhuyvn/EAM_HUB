"""EAP Outbox relay, event ingestion, and projection pipeline (P1a-WP3).

Normative implementation of:
- ADR-020: Post-commit monotonic outbox relay offset assignment
- C02: Same event_id with divergent payload detection and quarantine
- C23: Cross-tenant producer event isolation and quarantine
- C33: Strict commit-order ingest_offset assignment via advisory locking
- docs/03: Deterministic versioned HUD projection reducer and cursor replay
"""

from eap.events.binding import BindingProvider, ProducerBinding, StaticBindingProvider
from eap.events.ingest import IngestResult, IngestStatus, check_and_ingest_event
from eap.events.projection import (
    PROJECTION_NAME_HUD_RUNS,
    PROJECTION_VERSION_CURRENT,
    build_fresh_snapshot,
    compute_projection_hash,
    fold_events,
    initial_hud_row,
    load_checkpoint,
    reduce_event,
    replay_stream,
    save_checkpoint,
)
from eap.events.quarantine import (
    KIND_EVENT_CONTENT_CONFLICT,
    KIND_PRODUCER_UNAUTHORIZED,
    KIND_TENANT_MISMATCH,
    REASON_EVENT_CONTENT_CONFLICT,
    REASON_PRODUCER_UNAUTHORIZED,
    REASON_SCHEMA_VIOLATION,
    REASON_TENANT_MISMATCH,
    record_quarantine,
    record_security_event,
)
from eap.events.relay import (
    OutboxRelayWorker,
    RelayBatchResult,
    stream_advisory_lock_id,
)

__all__ = [
    "KIND_EVENT_CONTENT_CONFLICT",
    "KIND_PRODUCER_UNAUTHORIZED",
    "KIND_TENANT_MISMATCH",
    "PROJECTION_NAME_HUD_RUNS",
    "PROJECTION_VERSION_CURRENT",
    "REASON_EVENT_CONTENT_CONFLICT",
    "REASON_PRODUCER_UNAUTHORIZED",
    "REASON_SCHEMA_VIOLATION",
    "REASON_TENANT_MISMATCH",
    "BindingProvider",
    "IngestResult",
    "IngestStatus",
    "OutboxRelayWorker",
    "ProducerBinding",
    "RelayBatchResult",
    "StaticBindingProvider",
    "build_fresh_snapshot",
    "check_and_ingest_event",
    "compute_projection_hash",
    "fold_events",
    "initial_hud_row",
    "load_checkpoint",
    "record_quarantine",
    "record_security_event",
    "reduce_event",
    "replay_stream",
    "save_checkpoint",
    "stream_advisory_lock_id",
]
