"""Unified MVP1 background worker process (outbox relay, dispatcher, scheduler, orchestrator)."""

from __future__ import annotations

import argparse
import logging
import signal
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from eap.adapters.mock_provider import MockProvider
from eap.api.dependencies import Principal as _Principal  # noqa: F401  (break api<->ppm import cycle)
from eap.config import Settings, get_settings
from eap.db import get_engine, outbox, outbox_relay_marks, runs
from eap.db.agent_tables import agent_bindings
from eap.events.binding import ProducerBinding
from eap.events.relay import OutboxRelayWorker
from eap.observability import configure_logging
from eap.ppm.orchestrator import AssessmentOrchestrator
from eap.ppm.scheduler import PpmScanScheduler
from eap.run.dispatcher import Dispatcher

logger = logging.getLogger("eap.worker")

_INTERNAL_PRODUCERS: frozenset[str] = frozenset(
    {
        "task_service",
        "run_service",
        "mock-provider",
        "ppm_collector",
        "ppm_ingest",
        "ppm_assessments",
        "ppm_orchestrator",
        "policy_service",
        "platform-api",
    }
)


@dataclass
class WorkerCycleStats:
    """Summary of one worker poll cycle."""

    streams_relayed: int = 0
    events_ingested: int = 0
    runs_dispatched: int = 0
    scheduler_triggers: int = 0
    orchestrator_events: int = 0
    orchestrator_recovered: int = 0
    orchestrator_timers: int = 0


def build_db_binding_provider(engine: sa.Engine):
    """Build a binding_provider resolving agent_bindings and known internal producers."""

    def _resolve(tenant_id: str, producer_id: str) -> ProducerBinding | None:
        if producer_id in _INTERNAL_PRODUCERS:
            return ProducerBinding(
                tenant_id=tenant_id,
                producer_id=producer_id,
                binding_id=f"binding-internal-{producer_id}",
                certified_capabilities={"events": True},
            )
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(
                    agent_bindings.c.binding_id,
                    agent_bindings.c.certified_capabilities,
                )
                .where(
                    agent_bindings.c.tenant_id == tenant_id,
                    agent_bindings.c.agent_id == producer_id,
                    agent_bindings.c.revoked_at.is_(None),
                )
                .order_by(agent_bindings.c.created_at.desc(), agent_bindings.c.id.desc())
                .limit(1)
            ).mappings().first()
        if row is None:
            return None
        caps = row["certified_capabilities"]
        return ProducerBinding(
            tenant_id=tenant_id,
            producer_id=producer_id,
            binding_id=str(row["binding_id"]),
            certified_capabilities=dict(caps) if isinstance(caps, dict) else {"events": True},
        )

    return _resolve


def run_worker_cycle(
    engine: sa.Engine,
    session_factory: sessionmaker[Session],
    *,
    settings: Settings,
    mock_provider: MockProvider | None = None,
    scheduler: PpmScanScheduler | None = None,
    orchestrator: AssessmentOrchestrator | None = None,
) -> WorkerCycleStats:
    """Execute one poll cycle across relay, dispatcher, scheduler, and orchestrator."""
    stats = WorkerCycleStats()
    provider = mock_provider or MockProvider()

    # 1. Outbox relay + HUD projection
    if settings.relay_enabled:
        relay = OutboxRelayWorker(enabled=True)
        binding_provider = build_db_binding_provider(engine)
        with engine.connect() as conn:
            pending_streams = conn.execute(
                sa.select(outbox.c.tenant_id, outbox.c.stream_id)
                .select_from(
                    outbox.outerjoin(
                        outbox_relay_marks,
                        sa.and_(
                            outbox.c.tenant_id == outbox_relay_marks.c.tenant_id,
                            outbox.c.id == outbox_relay_marks.c.outbox_id,
                        ),
                    )
                )
                .where(outbox_relay_marks.c.outbox_id.is_(None))
                .distinct()
                .limit(100)
            ).all()
        for tenant_id, stream_id in pending_streams:
            res = relay.run_relay_stream_until_empty(
                engine,
                tenant_id=str(tenant_id),
                stream_id=str(stream_id),
                binding_provider=binding_provider,
            )
            stats.streams_relayed += 1
            stats.events_ingested += res.ingested_count

    # 2. Dispatcher (mock provider) for queued runs
    dispatcher = Dispatcher(enabled=True)
    with session_factory() as db:
        queued_runs = db.execute(
            sa.select(runs.c.tenant_id, runs.c.run_id)
            .where(runs.c.state == "queued")
            .order_by(runs.c.created_at.asc())
            .limit(20)
        ).all()
        for tenant_id, run_id in queued_runs:
            dispatcher.dispatch(
                db,
                tenant_id=str(tenant_id),
                run_id=str(run_id),
                worker_id="mvp1-worker",
                provider=provider,
            )
            stats.runs_dispatched += 1

    # 3. PPM Scan Scheduler (when enabled)
    if settings.ppm_scheduler_enabled and scheduler is not None:
        with session_factory() as db:
            stats.scheduler_triggers += len(scheduler.scan_filesystem_changes(db))
            stats.scheduler_triggers += len(scheduler.tick_interval(db))

    # 4. PPM Assessment Orchestrator (when enabled)
    if settings.ppm_orchestrator_enabled and orchestrator is not None:
        tenant_id = settings.eap_owner_tenant_id
        with session_factory() as db:
            consumed = orchestrator.consume_outbox_events(db, tenant_id=tenant_id)
            stats.orchestrator_events += len(consumed)
            recovered = orchestrator.recover_pending_tasks(db, tenant_id=tenant_id)
            stats.orchestrator_recovered += len(recovered)
            ttl_res = orchestrator.check_evidence_ttl_timers(db, tenant_id=tenant_id)
            waiver_res = orchestrator.check_waiver_expiry_timers(db, tenant_id=tenant_id)
            stats.orchestrator_timers += len(ttl_res) + len(waiver_res)

    return stats


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for `python -m eap.worker`."""
    parser = argparse.ArgumentParser(
        prog="python -m eap.worker",
        description="EAP MVP1 background worker (relay, dispatcher, scheduler, orchestrator).",
    )
    parser.add_argument("--once", action="store_true", help="Run a single poll cycle and exit.")
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=1.0,
        help="Sleep interval between poll cycles in seconds (default: 1.0).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    settings = get_settings()
    configure_logging(settings)

    engine = get_engine(settings.database_url.get_secret_value() if settings.database_url else None)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    mock_provider = MockProvider()
    scheduler = PpmScanScheduler(settings=settings) if settings.ppm_scheduler_enabled else None
    orchestrator = AssessmentOrchestrator(settings=settings) if settings.ppm_orchestrator_enabled else None

    running = True

    def _handle_stop(signum: int, _frame: Any) -> None:
        nonlocal running
        logger.info("worker_shutdown_signal", extra={"signal": signum})
        running = False

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    logger.info(
        "worker_started",
        extra={
            "relay_enabled": settings.relay_enabled,
            "scheduler_enabled": settings.ppm_scheduler_enabled,
            "orchestrator_enabled": settings.ppm_orchestrator_enabled,
            "verifier_enabled": settings.ppm_verifier_enabled,
        },
    )

    if scheduler is not None:
        with session_factory() as db:
            scheduler.startup_catch_up(db)
            scheduler.snapshot_filesystem_baselines(db)

    try:
        while running:
            try:
                stats = run_worker_cycle(
                    engine,
                    session_factory,
                    settings=settings,
                    mock_provider=mock_provider,
                    scheduler=scheduler,
                    orchestrator=orchestrator,
                )
                if (
                    stats.streams_relayed
                    or stats.runs_dispatched
                    or stats.scheduler_triggers
                    or stats.orchestrator_events
                ):
                    logger.info(
                        "worker_cycle_active",
                        extra={
                            "streams_relayed": stats.streams_relayed,
                            "events_ingested": stats.events_ingested,
                            "runs_dispatched": stats.runs_dispatched,
                            "scheduler_triggers": stats.scheduler_triggers,
                            "orchestrator_events": stats.orchestrator_events,
                        },
                    )
            except Exception:
                logger.exception("worker_cycle_error")
                if args.once:
                    return 1

            if args.once:
                break
            time.sleep(max(0.1, float(args.poll_seconds)))
    finally:
        engine.dispose()
        logger.info("worker_stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
