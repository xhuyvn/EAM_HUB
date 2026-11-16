"""Automated PPM Scan Scheduler with DB lease fencing and occurrence dedup (P2a-WP5 / DG-2).

Enforces:
- Triggers: ``startup`` (catch-up), ``interval``, ``watcher`` (debounced), and ``manual``.
- Deterministic ``occurrence_id`` per ``(tenant_id, source_id, trigger, window_key)`` so
  redelivered triggers are an idempotent no-op without creating duplicate tasks or scans.
- Scans created as logical tasks through ``TaskService`` (no OS cron).
- Single active scan per source enforced by PostgreSQL lease + monotonically increasing
  ``fencing_token`` across ``ppm_scan_schedules`` and ``leases``; stale tokens cannot
  commit snapshots in ``PpmCollector.scan_source``.
- Post-scan assessment request routed through ``AssessmentOrchestrator`` (P2a-WP6).
- Policy defaults: interval 300s (minimum override >= 60s in production/API), debounce 5s,
  feature flag ``PPM_SCHEDULER_ENABLED`` (default false; manual scans still work).
- Per-source observability persisted and readable via API/service for the HUD:
  ``last_scan_started_at``, ``last_successful_scan_at``, redacted ``last_error``, ``next_due_at``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from eap.config import Settings, get_settings
from eap.contracts.models.task_request import TaskRequest
from eap.db import (
    leases,
    location_registrations,
    ppm_scan_occurrences,
    ppm_scan_schedules,
    ppm_scan_source_status,
    project_scopes,
    source_shares,
    sources,
)
from eap.observability.logging import log_context, redact_text
from eap.ppm.orchestrator import AssessmentOrchestrator
from eap.run.service import RunService, StaleFencingTokenError
from eap.task.service import TaskService

if TYPE_CHECKING:
    from eap.ppm.collector.collector import CollectorScanResult, PpmCollector

logger = logging.getLogger("eap.ppm.scheduler")

DEFAULT_SCAN_INTERVAL_SECONDS = 300
MIN_SCAN_INTERVAL_SECONDS = 60
DEFAULT_SCAN_DEBOUNCE_SECONDS = 5
MIN_SCAN_DEBOUNCE_SECONDS = 1
DEFAULT_SCAN_LEASE_TTL_SECONDS = 60
SCAN_LEASE_RESOURCE_TYPE = "ppm_source_scan"
COLLECTOR_AGENT_ID = "agent-ppm-collector"
VALID_TRIGGERS: tuple[str, ...] = ("startup", "interval", "watcher", "manual")


def _utc_now(now: dt.datetime | None = None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(dt.UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=dt.UTC)
    return now.astimezone(dt.UTC)


def _ensure_utc_dt(val: dt.datetime | None) -> dt.datetime | None:
    if val is None:
        return None
    if val.tzinfo is None:
        return val.replace(tzinfo=dt.UTC)
    return val.astimezone(dt.UTC)


def validate_schedule_config(
    interval_seconds: int,
    debounce_seconds: int,
    *,
    _allow_sub_minimum_interval_for_testing: bool = False,
) -> None:
    """Validate scan interval and debounce seconds against DG-2 policy."""
    if interval_seconds <= 0:
        raise ValueError(f"Scan interval_seconds ({interval_seconds}) must be > 0")
    if not _allow_sub_minimum_interval_for_testing and interval_seconds < MIN_SCAN_INTERVAL_SECONDS:
        raise ValueError(
            f"Scan interval_seconds ({interval_seconds}) must be >= {MIN_SCAN_INTERVAL_SECONDS} seconds (DG-2 policy)"
        )
    if debounce_seconds < MIN_SCAN_DEBOUNCE_SECONDS:
        raise ValueError(
            f"Scan debounce_seconds ({debounce_seconds}) must be >= {MIN_SCAN_DEBOUNCE_SECONDS} second"
        )


def compute_occurrence_id(
    *,
    tenant_id: str,
    source_id: str,
    trigger: str,
    window_key: str,
) -> str:
    """Compute deterministic occurrence_id for a (tenant, source, trigger, window_key)."""
    if trigger not in VALID_TRIGGERS:
        raise ValueError(f"Invalid trigger {trigger!r}; expected one of {VALID_TRIGGERS}")
    raw = f"{tenant_id}|{source_id}|{trigger}|{window_key}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"occ-{digest}"


@dataclass(frozen=True)
class SourceScanObservability:
    """Per-source scheduler observability state readable by HUD and API."""

    tenant_id: str
    source_id: str
    interval_seconds: int
    debounce_seconds: int
    last_scan_started_at: dt.datetime | None
    last_successful_scan_at: dt.datetime | None
    last_error: str | None
    next_due_at: dt.datetime | None
    fencing_token: int
    lease_holder: str | None
    lease_expires_at: dt.datetime | None
    last_occurrence_id: str | None
    last_trigger: str | None
    last_task_id: str | None
    last_run_id: str | None
    last_snapshot_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "source_id": self.source_id,
            "interval_seconds": self.interval_seconds,
            "debounce_seconds": self.debounce_seconds,
            "last_scan_started_at": (
                self.last_scan_started_at.isoformat() if self.last_scan_started_at is not None else None
            ),
            "last_successful_scan_at": (
                self.last_successful_scan_at.isoformat()
                if self.last_successful_scan_at is not None
                else None
            ),
            "last_error": self.last_error,
            "next_due_at": self.next_due_at.isoformat() if self.next_due_at is not None else None,
            "fencing_token": self.fencing_token,
            "lease_holder": self.lease_holder,
            "lease_expires_at": (
                self.lease_expires_at.isoformat() if self.lease_expires_at is not None else None
            ),
            "last_occurrence_id": self.last_occurrence_id,
            "last_trigger": self.last_trigger,
            "last_task_id": self.last_task_id,
            "last_run_id": self.last_run_id,
            "last_snapshot_id": self.last_snapshot_id,
        }


@dataclass(frozen=True)
class ScanTriggerResult:
    """Outcome of processing a single scheduler trigger occurrence."""

    tenant_id: str
    source_id: str
    occurrence_id: str
    trigger: str
    status: str
    executed: bool
    fencing_token: int | None = None
    task_id: str | None = None
    run_id: str | None = None
    snapshot_id: str | None = None
    assessments_created: int = 0
    assessments_deduplicated: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "source_id": self.source_id,
            "occurrence_id": self.occurrence_id,
            "trigger": self.trigger,
            "status": self.status,
            "executed": self.executed,
            "fencing_token": self.fencing_token,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "assessments_created": self.assessments_created,
            "assessments_deduplicated": self.assessments_deduplicated,
            "error": self.error,
        }


@dataclass
class _PendingWatcherEvent:
    tenant_id: str
    source_id: str
    first_change_at: dt.datetime
    last_change_at: dt.datetime
    fingerprint: str


class PpmScanScheduler:
    """DB-backed scan scheduler with fencing-token leases, occurrence dedup, and watcher debounce."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        *,
        scheduler_id: str | None = None,
        enabled: bool | None = None,
        default_interval_seconds: int | None = None,
        default_debounce_seconds: int | None = None,
        lease_ttl_seconds: int | None = None,
        collector: PpmCollector | None = None,
        settings: Settings | None = None,
        _allow_sub_minimum_interval_for_testing: bool = False,
    ) -> None:
        cfg = settings if settings is not None else get_settings()
        self.session_factory = session_factory
        self.scheduler_id = scheduler_id or f"sched-{uuid.uuid4().hex[:12]}"
        self._enabled_override = enabled
        self._allow_sub_min_testing = _allow_sub_minimum_interval_for_testing

        interval = (
            default_interval_seconds
            if default_interval_seconds is not None
            else cfg.ppm_scan_interval_seconds
        )
        debounce = (
            default_debounce_seconds
            if default_debounce_seconds is not None
            else cfg.ppm_scan_debounce_seconds
        )
        validate_schedule_config(
            interval,
            debounce,
            _allow_sub_minimum_interval_for_testing=_allow_sub_minimum_interval_for_testing,
        )
        self.default_interval_seconds = int(interval)
        self.default_debounce_seconds = int(debounce)
        self.lease_ttl_seconds = int(
            lease_ttl_seconds if lease_ttl_seconds is not None else cfg.ppm_scan_lease_ttl_seconds
        )
        if collector is not None:
            self.collector: PpmCollector = collector
        else:
            from eap.ppm.collector.collector import PpmCollector

            self.collector = PpmCollector()

        self._watcher_lock = threading.Lock()
        self._pending_watchers: dict[tuple[str, str], _PendingWatcherEvent] = {}
        self._last_seen_fingerprints: dict[tuple[str, str], str] = {}

    @property
    def is_enabled(self) -> bool:
        if self._enabled_override is not None:
            return self._enabled_override
        return get_settings().ppm_scheduler_enabled

    def ensure_schedule(
        self,
        db: Session,
        *,
        tenant_id: str,
        source_id: str,
        interval_seconds: int | None = None,
        debounce_seconds: int | None = None,
        next_due_at: dt.datetime | None = None,
        now: dt.datetime | None = None,
        _allow_sub_minimum_interval_for_testing: bool | None = None,
    ) -> None:
        """Create or update schedule policy and observability status row for a source."""
        allow_sub_min = (
            _allow_sub_minimum_interval_for_testing
            if _allow_sub_minimum_interval_for_testing is not None
            else self._allow_sub_min_testing
        )
        eff_interval = (
            int(interval_seconds) if interval_seconds is not None else self.default_interval_seconds
        )
        eff_debounce = (
            int(debounce_seconds) if debounce_seconds is not None else self.default_debounce_seconds
        )
        validate_schedule_config(
            eff_interval,
            eff_debounce,
            _allow_sub_minimum_interval_for_testing=allow_sub_min,
        )

        ref_now = _utc_now(now)
        eff_next_due = _ensure_utc_dt(next_due_at) if next_due_at is not None else ref_now

        sched_stmt = (
            pg_insert(ppm_scan_schedules)
            .values(
                id=f"sched-{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                source_id=source_id,
                interval_seconds=eff_interval,
                debounce_seconds=eff_debounce,
                next_scan_at=eff_next_due,
                fencing_token=0,
                created_at=ref_now,
                updated_at=ref_now,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "source_id"])
        )
        db.execute(sched_stmt)

        if (
            interval_seconds is not None
            or debounce_seconds is not None
            or next_due_at is not None
        ):
            upd_vals: dict[str, Any] = {"updated_at": ref_now}
            if interval_seconds is not None:
                upd_vals["interval_seconds"] = eff_interval
            if debounce_seconds is not None:
                upd_vals["debounce_seconds"] = eff_debounce
            if next_due_at is not None:
                upd_vals["next_scan_at"] = _ensure_utc_dt(next_due_at)
            db.execute(
                ppm_scan_schedules.update()
                .where(
                    ppm_scan_schedules.c.tenant_id == tenant_id,
                    ppm_scan_schedules.c.source_id == source_id,
                )
                .values(**upd_vals)
            )

        status_stmt = (
            pg_insert(ppm_scan_source_status)
            .values(
                tenant_id=tenant_id,
                source_id=source_id,
                last_fencing_token=0,
                created_at=ref_now,
                updated_at=ref_now,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "source_id"])
        )
        db.execute(status_stmt)
        db.commit()

    def ensure_schedules_for_active_sources(
        self,
        db: Session,
        *,
        tenant_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> list[tuple[str, str]]:
        """Discover all active approved sources and ensure schedule rows exist."""
        stmt = (
            sa.select(
                sources.c.tenant_id,
                sources.c.source_id,
            )
            .where(
                sa.or_(
                    sa.exists(
                        sa.select(location_registrations.c.id).where(
                            location_registrations.c.tenant_id == sources.c.tenant_id,
                            location_registrations.c.source_id == sources.c.source_id,
                            location_registrations.c.state == "active",
                        )
                    ),
                    sa.exists(
                        sa.select(project_scopes.c.id).where(
                            project_scopes.c.tenant_id == sources.c.tenant_id,
                            project_scopes.c.source_id == sources.c.source_id,
                            project_scopes.c.status == "active",
                        )
                    ),
                )
            )
            .where(
                ~sa.exists(
                    sa.select(source_shares.c.id).where(
                        source_shares.c.requester_tenant_id == sources.c.tenant_id,
                        source_shares.c.requester_source_id == sources.c.source_id,
                        source_shares.c.state.in_(["pending_share", "rejected", "revoked"]),
                    )
                )
            )
            .order_by(sources.c.tenant_id.asc(), sources.c.source_id.asc())
        )
        if tenant_id is not None:
            stmt = stmt.where(sources.c.tenant_id == tenant_id)

        rows = db.execute(stmt).mappings().all()
        discovered: list[tuple[str, str]] = []
        for row in rows:
            t_id = str(row["tenant_id"])
            s_id = str(row["source_id"])
            discovered.append((t_id, s_id))
            self.ensure_schedule(
                db,
                tenant_id=t_id,
                source_id=s_id,
                now=now,
                _allow_sub_minimum_interval_for_testing=self._allow_sub_min_testing,
            )
        return discovered

    def _try_acquire_source_lease(
        self,
        db: Session,
        *,
        tenant_id: str,
        source_id: str,
        trigger: str,
        occurrence_id: str,
        now: dt.datetime,
    ) -> tuple[str, int | None, int]:
        """Atomically verify occurrence uniqueness and acquire a fenced per-source scan lease.

        Returns ``(claim_status, fencing_token_or_none, interval_seconds)`` where
        ``claim_status`` is one of ``"claimed"``, ``"duplicate_occurrence"``, ``"lease_busy"``.
        Commits the claim before returning ``"claimed"`` so parallel connections observe the lock.
        """
        self.ensure_schedule(
            db,
            tenant_id=tenant_id,
            source_id=source_id,
            now=now,
            _allow_sub_minimum_interval_for_testing=self._allow_sub_min_testing,
        )

        # Fast-path occurrence check before locking schedule row
        existing_occ = db.execute(
            sa.select(ppm_scan_occurrences.c.id).where(
                ppm_scan_occurrences.c.tenant_id == tenant_id,
                ppm_scan_occurrences.c.source_id == source_id,
                ppm_scan_occurrences.c.occurrence_id == occurrence_id,
            )
        ).scalar_one_or_none()
        if existing_occ is not None:
            return "duplicate_occurrence", None, self.default_interval_seconds

        # Lock schedule row FOR UPDATE (always before leases row to prevent deadlock)
        sched_row = db.execute(
            sa.select(ppm_scan_schedules)
            .where(
                ppm_scan_schedules.c.tenant_id == tenant_id,
                ppm_scan_schedules.c.source_id == source_id,
            )
            .with_for_update()
        ).mappings().one()

        interval_seconds = int(sched_row["interval_seconds"])

        # Re-check occurrence inside lock in case another worker committed while we waited
        existing_occ = db.execute(
            sa.select(ppm_scan_occurrences.c.id).where(
                ppm_scan_occurrences.c.tenant_id == tenant_id,
                ppm_scan_occurrences.c.source_id == source_id,
                ppm_scan_occurrences.c.occurrence_id == occurrence_id,
            )
        ).scalar_one_or_none()
        if existing_occ is not None:
            db.rollback()
            return "duplicate_occurrence", None, interval_seconds

        lease_expires_at = _ensure_utc_dt(sched_row["lease_expires_at"])
        if (
            sched_row["lease_holder"] is not None
            and lease_expires_at is not None
            and lease_expires_at > now
        ):
            db.rollback()
            return "lease_busy", None, interval_seconds

        # Lock or prepare leases row for this source scan resource
        lease_row = db.execute(
            sa.select(leases)
            .where(
                leases.c.tenant_id == tenant_id,
                leases.c.resource_type == SCAN_LEASE_RESOURCE_TYPE,
                leases.c.resource_id == source_id,
            )
            .with_for_update()
        ).mappings().first()

        sched_tok = int(sched_row["fencing_token"] or 0)
        lease_tok = int(lease_row["fencing_token"] or 0) if lease_row is not None else 0
        new_fencing_token = max(sched_tok, lease_tok) + 1
        new_expires_at = now + dt.timedelta(seconds=self.lease_ttl_seconds)

        # Record occurrence first inside savepoint to guarantee strict uniqueness
        try:
            with db.begin_nested():
                db.execute(
                    ppm_scan_occurrences.insert().values(
                        tenant_id=tenant_id,
                        source_id=source_id,
                        occurrence_id=occurrence_id,
                        trigger=trigger,
                        status="running",
                        fencing_token=new_fencing_token,
                        created_at=now,
                    )
                )
        except IntegrityError:
            db.rollback()
            return "duplicate_occurrence", None, interval_seconds

        # Upsert leases table with monotonic fencing token
        if lease_row is None:
            try:
                with db.begin_nested():
                    db.execute(
                        leases.insert().values(
                            tenant_id=tenant_id,
                            resource_type=SCAN_LEASE_RESOURCE_TYPE,
                            resource_id=source_id,
                            fencing_token=new_fencing_token,
                            holder_id=self.scheduler_id,
                            expires_at=new_expires_at,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            except IntegrityError:
                db.execute(
                    leases.update()
                    .where(
                        leases.c.tenant_id == tenant_id,
                        leases.c.resource_type == SCAN_LEASE_RESOURCE_TYPE,
                        leases.c.resource_id == source_id,
                    )
                    .values(
                        fencing_token=new_fencing_token,
                        holder_id=self.scheduler_id,
                        expires_at=new_expires_at,
                        updated_at=now,
                    )
                )
        else:
            db.execute(
                leases.update()
                .where(
                    leases.c.tenant_id == tenant_id,
                    leases.c.resource_type == SCAN_LEASE_RESOURCE_TYPE,
                    leases.c.resource_id == source_id,
                )
                .values(
                    fencing_token=new_fencing_token,
                    holder_id=self.scheduler_id,
                    expires_at=new_expires_at,
                    updated_at=now,
                )
            )

        # Update ppm_scan_schedules lease & fencing token
        db.execute(
            ppm_scan_schedules.update()
            .where(
                ppm_scan_schedules.c.tenant_id == tenant_id,
                ppm_scan_schedules.c.source_id == source_id,
            )
            .values(
                lease_holder=self.scheduler_id,
                lease_expires_at=new_expires_at,
                fencing_token=new_fencing_token,
                updated_at=now,
            )
        )

        # Record scan started timestamp in observability status table
        db.execute(
            ppm_scan_source_status.update()
            .where(
                ppm_scan_source_status.c.tenant_id == tenant_id,
                ppm_scan_source_status.c.source_id == source_id,
            )
            .values(
                last_scan_started_at=now,
                last_occurrence_id=occurrence_id,
                last_trigger=trigger,
                last_fencing_token=new_fencing_token,
                updated_at=now,
            )
        )

        db.commit()
        return "claimed", new_fencing_token, interval_seconds

    def _release_lease_if_owner(
        self,
        db: Session,
        *,
        tenant_id: str,
        source_id: str,
        fencing_token: int,
        finish_now: dt.datetime,
        next_scan_at: dt.datetime,
        succeeded: bool,
        task_id: str | None,
        run_id: str | None,
        snapshot_id: str | None,
        redacted_error: str | None,
        occurrence_id: str,
    ) -> None:
        """Finalize occurrence and conditionally release schedule/status only if still fencing owner."""
        occ_status = "succeeded" if succeeded else "failed"
        db.execute(
            ppm_scan_occurrences.update()
            .where(
                ppm_scan_occurrences.c.tenant_id == tenant_id,
                ppm_scan_occurrences.c.source_id == source_id,
                ppm_scan_occurrences.c.occurrence_id == occurrence_id,
            )
            .values(
                status=occ_status,
                task_id=task_id,
                run_id=run_id,
                snapshot_id=snapshot_id,
                error=redacted_error,
                completed_at=finish_now,
            )
        )

        # Only update schedule & source status if no newer fencing token has overtaken us
        sched_vals: dict[str, Any] = {
            "next_scan_at": next_scan_at,
            "lease_holder": None,
            "lease_expires_at": None,
            "updated_at": finish_now,
        }
        if succeeded:
            sched_vals["last_scan_at"] = finish_now

        db.execute(
            ppm_scan_schedules.update()
            .where(
                ppm_scan_schedules.c.tenant_id == tenant_id,
                ppm_scan_schedules.c.source_id == source_id,
                ppm_scan_schedules.c.fencing_token == fencing_token,
            )
            .values(**sched_vals)
        )

        db.execute(
            leases.update()
            .where(
                leases.c.tenant_id == tenant_id,
                leases.c.resource_type == SCAN_LEASE_RESOURCE_TYPE,
                leases.c.resource_id == source_id,
                leases.c.fencing_token == fencing_token,
            )
            .values(
                expires_at=finish_now,
                updated_at=finish_now,
            )
        )

        status_vals: dict[str, Any] = {
            "last_task_id": task_id,
            "last_run_id": run_id,
            "updated_at": finish_now,
        }
        if succeeded:
            status_vals["last_successful_scan_at"] = finish_now
            status_vals["last_error"] = None
            if snapshot_id is not None:
                status_vals["last_snapshot_id"] = snapshot_id
        else:
            status_vals["last_error"] = redacted_error

        db.execute(
            ppm_scan_source_status.update()
            .where(
                ppm_scan_source_status.c.tenant_id == tenant_id,
                ppm_scan_source_status.c.source_id == source_id,
                ppm_scan_source_status.c.last_fencing_token <= fencing_token,
            )
            .values(**status_vals)
        )

    def _create_scan_task_and_start_run(
        self,
        db: Session,
        *,
        tenant_id: str,
        source_id: str,
        trigger: str,
        occurrence_id: str,
        fencing_token: int,
    ) -> tuple[str, str]:
        """Create scan task via TaskService and transition its run to running."""
        req = TaskRequest(
            contract_version="1.0",
            agent_id=COLLECTOR_AGENT_ID,
            task_type="ppm.inventory.scan",
            objective=f"Scheduled PPM scan ({trigger}) for source '{source_id}'",
            input_refs=[f"source:{source_id}", f"occurrence:{occurrence_id}"],
            resource_refs=[f"source:{source_id}"],
            idempotency_key=f"scan-{occurrence_id}",
        )
        task = TaskService.create(
            db,
            tenant_id=tenant_id,
            request=req,
            agent_version="1.0.0",
        )
        task_runs = TaskService.get_runs(db, tenant_id=tenant_id, task_id=task.id) or []
        if not task_runs:
            raise RuntimeError(f"Task '{task.id}' created without an initial run")
        run_id = str(task_runs[0]["run_id"])
        run_state = str(task_runs[0]["state"])

        if run_state == "queued":
            RunService.transition(
                db,
                tenant_id=tenant_id,
                run_id=run_id,
                to_state="dispatching",
                expected_from="queued",
                reason="scheduler_dispatched",
                fencing_token=fencing_token,
            )
            run_state = "dispatching"
        if run_state == "dispatching":
            RunService.transition(
                db,
                tenant_id=tenant_id,
                run_id=run_id,
                to_state="running",
                expected_from="dispatching",
                reason="collector_started",
                fencing_token=fencing_token,
            )

        return task.id, run_id

    def _execute_claimed_scan(
        self,
        db: Session,
        *,
        tenant_id: str,
        source_id: str,
        trigger: str,
        occurrence_id: str,
        fencing_token: int,
        interval_seconds: int,
        explicit_now: dt.datetime | None,
        started_at: dt.datetime,
    ) -> ScanTriggerResult:
        """Execute TaskService task, Collector scan (fenced), ingest, and assessment request."""
        task_id: str | None = None
        run_id: str | None = None

        prev_success = _ensure_utc_dt(
            db.execute(
                sa.select(ppm_scan_source_status.c.last_successful_scan_at).where(
                    ppm_scan_source_status.c.tenant_id == tenant_id,
                    ppm_scan_source_status.c.source_id == source_id,
                )
            ).scalar_one_or_none()
        )

        with log_context(tenant_id=tenant_id):
            logger.info(
                "scheduler_scan_started source_id=%s trigger=%s occurrence_id=%s fencing_token=%s",
                source_id,
                trigger,
                occurrence_id,
                fencing_token,
            )
            try:
                task_id, run_id = self._create_scan_task_and_start_run(
                    db,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    trigger=trigger,
                    occurrence_id=occurrence_id,
                    fencing_token=fencing_token,
                )

                with log_context(tenant_id=tenant_id, task_id=task_id, run_id=run_id):
                    scan_res: CollectorScanResult = self.collector.scan_source(
                        db,
                        tenant_id=tenant_id,
                        source_id=source_id,
                        fencing_token=fencing_token,
                    )

                    if explicit_now is not None:
                        finish_now = explicit_now
                    else:
                        finish_now = _utc_now()
                        if prev_success is not None and finish_now <= prev_success:
                            finish_now = prev_success + dt.timedelta(microseconds=1)

                    if scan_res.status == "failed" or scan_res.ingest_status == "failed":
                        raw_err = (
                            "; ".join(scan_res.partial_reasons)
                            if scan_res.partial_reasons
                            else f"ingest_status={scan_res.ingest_status}"
                        )
                        redacted_err = redact_text(raw_err)[:2000]
                        next_due = finish_now + dt.timedelta(seconds=interval_seconds)

                        self._release_lease_if_owner(
                            db,
                            tenant_id=tenant_id,
                            source_id=source_id,
                            fencing_token=fencing_token,
                            finish_now=finish_now,
                            next_scan_at=next_due,
                            succeeded=False,
                            task_id=task_id,
                            run_id=run_id,
                            snapshot_id=scan_res.snapshot_id,
                            redacted_error=redacted_err,
                            occurrence_id=occurrence_id,
                        )
                        db.commit()

                        RunService.transition(
                            db,
                            tenant_id=tenant_id,
                            run_id=run_id,
                            to_state="failed",
                            reason="scan_or_ingest_failed",
                            fencing_token=fencing_token,
                        )
                        logger.warning(
                            "scheduler_scan_failed source_id=%s trigger=%s occurrence_id=%s error=%s",
                            source_id,
                            trigger,
                            occurrence_id,
                            redacted_err,
                        )
                        return ScanTriggerResult(
                            tenant_id=tenant_id,
                            source_id=source_id,
                            occurrence_id=occurrence_id,
                            trigger=trigger,
                            status="failed",
                            executed=True,
                            fencing_token=fencing_token,
                            task_id=task_id,
                            run_id=run_id,
                            snapshot_id=scan_res.snapshot_id,
                            error=redacted_err,
                        )

                    # Route snapshot -> assessment transition through AssessmentOrchestrator (P2a-WP6)
                    created_count = 0
                    dedup_count = 0
                    cfg = get_settings()
                    if cfg.ppm_orchestrator_enabled:
                        active_psnaps = [
                            str(scan_res.project_snapshot_ids[p_id])
                            for p_id in sorted(scan_res.scopes.keys())
                            if p_id not in scan_res.quarantined_scopes
                            and scan_res.project_snapshot_ids.get(p_id) is not None
                        ]
                        if active_psnaps:
                            orchestrator = AssessmentOrchestrator(settings=cfg)
                            orch_results = orchestrator.consume_outbox_events(
                                db,
                                tenant_id=tenant_id,
                                event_types=("ppm.snapshot.captured",),
                                project_snapshot_ids=active_psnaps,
                                execute=True,
                            )
                            for res in orch_results:
                                if res.deduplicated:
                                    dedup_count += 1
                                elif res.status == "succeeded":
                                    created_count += 1

                    next_due = finish_now + dt.timedelta(seconds=interval_seconds)
                    self._release_lease_if_owner(
                        db,
                        tenant_id=tenant_id,
                        source_id=source_id,
                        fencing_token=fencing_token,
                        finish_now=finish_now,
                        next_scan_at=next_due,
                        succeeded=True,
                        task_id=task_id,
                        run_id=run_id,
                        snapshot_id=scan_res.snapshot_id,
                        redacted_error=None,
                        occurrence_id=occurrence_id,
                    )
                    db.commit()

                    RunService.transition(
                        db,
                        tenant_id=tenant_id,
                        run_id=run_id,
                        to_state="succeeded",
                        reason="scan_completed",
                        evidence_ref=scan_res.snapshot_id,
                        fencing_token=fencing_token,
                    )

                    logger.info(
                        "scheduler_scan_succeeded source_id=%s trigger=%s occurrence_id=%s snapshot_id=%s "
                        "assessments_created=%s assessments_deduplicated=%s",
                        source_id,
                        trigger,
                        occurrence_id,
                        scan_res.snapshot_id,
                        created_count,
                        dedup_count,
                    )
                    return ScanTriggerResult(
                        tenant_id=tenant_id,
                        source_id=source_id,
                        occurrence_id=occurrence_id,
                        trigger=trigger,
                        status="succeeded",
                        executed=True,
                        fencing_token=fencing_token,
                        task_id=task_id,
                        run_id=run_id,
                        snapshot_id=scan_res.snapshot_id,
                        assessments_created=created_count,
                        assessments_deduplicated=dedup_count,
                    )

            except StaleFencingTokenError as exc:
                db.rollback()
                finish_now = explicit_now if explicit_now is not None else _utc_now()
                redacted_err = redact_text(f"{type(exc).__name__}: {exc}")[:2000]
                db.execute(
                    ppm_scan_occurrences.update()
                    .where(
                        ppm_scan_occurrences.c.tenant_id == tenant_id,
                        ppm_scan_occurrences.c.source_id == source_id,
                        ppm_scan_occurrences.c.occurrence_id == occurrence_id,
                    )
                    .values(
                        status="failed",
                        task_id=task_id,
                        run_id=run_id,
                        error=redacted_err,
                        completed_at=finish_now,
                    )
                )
                db.commit()
                self._best_effort_fail_run(
                    db,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    reason="stale_fencing_token",
                    fencing_token=fencing_token,
                )

                logger.warning(
                    "scheduler_scan_stale_fencing_token source_id=%s trigger=%s occurrence_id=%s fencing_token=%s",
                    source_id,
                    trigger,
                    occurrence_id,
                    fencing_token,
                )
                return ScanTriggerResult(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    occurrence_id=occurrence_id,
                    trigger=trigger,
                    status="stale_fencing_token",
                    executed=False,
                    fencing_token=fencing_token,
                    task_id=task_id,
                    run_id=run_id,
                    error=redacted_err,
                )

            except Exception as exc:  # noqa: BLE001 - scan boundary: record redacted error, release lease, schedule next attempt
                db.rollback()
                finish_now = explicit_now if explicit_now is not None else _utc_now()
                redacted_err = redact_text(f"{type(exc).__name__}: {exc}")[:2000]
                next_due = finish_now + dt.timedelta(seconds=interval_seconds)

                self._release_lease_if_owner(
                    db,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    fencing_token=fencing_token,
                    finish_now=finish_now,
                    next_scan_at=next_due,
                    succeeded=False,
                    task_id=task_id,
                    run_id=run_id,
                    snapshot_id=None,
                    redacted_error=redacted_err,
                    occurrence_id=occurrence_id,
                )
                db.commit()

                self._best_effort_fail_run(
                    db,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    reason="scan_exception",
                    fencing_token=fencing_token,
                )

                logger.warning(
                    "scheduler_scan_failed source_id=%s trigger=%s occurrence_id=%s error=%s",
                    source_id,
                    trigger,
                    occurrence_id,
                    redacted_err,
                )
                return ScanTriggerResult(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    occurrence_id=occurrence_id,
                    trigger=trigger,
                    status="failed",
                    executed=True,
                    fencing_token=fencing_token,
                    task_id=task_id,
                    run_id=run_id,
                    error=redacted_err,
                )

    def _best_effort_fail_run(
        self,
        db: Session,
        *,
        tenant_id: str,
        run_id: str | None,
        reason: str,
        fencing_token: int,
    ) -> None:
        if run_id is None:
            return
        try:
            RunService.transition(
                db,
                tenant_id=tenant_id,
                run_id=run_id,
                to_state="failed",
                reason=reason,
                fencing_token=fencing_token,
            )
        except Exception:  # noqa: BLE001 - best-effort run failure state cleanup after DB rollback
            db.rollback()

    def run_occurrence(
        self,
        db: Session,
        *,
        tenant_id: str,
        source_id: str,
        trigger: str,
        occurrence_id: str,
        now: dt.datetime | None = None,
        force_when_disabled: bool = False,
    ) -> ScanTriggerResult:
        """Execute a trigger occurrence if scheduler allows it, lease is free, and not yet seen."""
        if trigger not in VALID_TRIGGERS:
            raise ValueError(f"Invalid trigger {trigger!r}; expected one of {VALID_TRIGGERS}")

        if trigger != "manual" and not force_when_disabled and not self.is_enabled:
            logger.info(
                "scheduler_trigger_ignored_disabled source_id=%s trigger=%s occurrence_id=%s",
                source_id,
                trigger,
                occurrence_id,
            )
            return ScanTriggerResult(
                tenant_id=tenant_id,
                source_id=source_id,
                occurrence_id=occurrence_id,
                trigger=trigger,
                status="scheduler_disabled",
                executed=False,
            )

        ref_now = _utc_now(now)
        claim_status, fencing_token, interval_seconds = self._try_acquire_source_lease(
            db,
            tenant_id=tenant_id,
            source_id=source_id,
            trigger=trigger,
            occurrence_id=occurrence_id,
            now=ref_now,
        )
        if claim_status == "duplicate_occurrence":
            logger.info(
                "scheduler_duplicate_occurrence_noop source_id=%s trigger=%s occurrence_id=%s",
                source_id,
                trigger,
                occurrence_id,
            )
            return ScanTriggerResult(
                tenant_id=tenant_id,
                source_id=source_id,
                occurrence_id=occurrence_id,
                trigger=trigger,
                status="duplicate_occurrence",
                executed=False,
            )
        if claim_status == "lease_busy" or fencing_token is None:
            logger.info(
                "scheduler_lease_busy source_id=%s trigger=%s occurrence_id=%s",
                source_id,
                trigger,
                occurrence_id,
            )
            return ScanTriggerResult(
                tenant_id=tenant_id,
                source_id=source_id,
                occurrence_id=occurrence_id,
                trigger=trigger,
                status="lease_busy",
                executed=False,
            )

        return self._execute_claimed_scan(
            db,
            tenant_id=tenant_id,
            source_id=source_id,
            trigger=trigger,
            occurrence_id=occurrence_id,
            fencing_token=fencing_token,
            interval_seconds=interval_seconds,
            explicit_now=_ensure_utc_dt(now) if now is not None else None,
            started_at=ref_now,
        )

    def startup_catch_up(
        self,
        db: Session,
        *,
        tenant_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> list[ScanTriggerResult]:
        """Run one catch-up scan per overdue source on startup (collapsing missed intervals)."""
        if not self.is_enabled:
            return []

        ref_now = _utc_now(now)
        self.ensure_schedules_for_active_sources(db, tenant_id=tenant_id, now=ref_now)

        stmt = sa.select(ppm_scan_schedules).where(
            sa.or_(
                ppm_scan_schedules.c.next_scan_at.is_(None),
                ppm_scan_schedules.c.next_scan_at <= ref_now,
            )
        )
        if tenant_id is not None:
            stmt = stmt.where(ppm_scan_schedules.c.tenant_id == tenant_id)
        stmt = stmt.order_by(
            ppm_scan_schedules.c.tenant_id.asc(),
            ppm_scan_schedules.c.source_id.asc(),
        )

        due_rows = db.execute(stmt).mappings().all()
        results: list[ScanTriggerResult] = []
        for row in due_rows:
            t_id = str(row["tenant_id"])
            s_id = str(row["source_id"])
            due_dt = _ensure_utc_dt(row["next_scan_at"])
            anchor = str(int(due_dt.timestamp())) if due_dt is not None else "initial"
            occ_id = compute_occurrence_id(
                tenant_id=t_id,
                source_id=s_id,
                trigger="startup",
                window_key=f"startup:{anchor}",
            )
            res = self.run_occurrence(
                db,
                tenant_id=t_id,
                source_id=s_id,
                trigger="startup",
                occurrence_id=occ_id,
                now=now if now is not None else ref_now,
            )
            results.append(res)
        return results

    def tick_interval(
        self,
        db: Session,
        *,
        tenant_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> list[ScanTriggerResult]:
        """Run due interval scans (collapsing clock jumps / host sleep into ONE scan per source)."""
        if not self.is_enabled:
            return []

        ref_now = _utc_now(now)
        self.ensure_schedules_for_active_sources(db, tenant_id=tenant_id, now=ref_now)

        stmt = sa.select(ppm_scan_schedules).where(
            ppm_scan_schedules.c.next_scan_at.is_not(None),
            ppm_scan_schedules.c.next_scan_at <= ref_now,
        )
        if tenant_id is not None:
            stmt = stmt.where(ppm_scan_schedules.c.tenant_id == tenant_id)
        stmt = stmt.order_by(
            ppm_scan_schedules.c.tenant_id.asc(),
            ppm_scan_schedules.c.source_id.asc(),
        )

        due_rows = db.execute(stmt).mappings().all()
        results: list[ScanTriggerResult] = []
        for row in due_rows:
            t_id = str(row["tenant_id"])
            s_id = str(row["source_id"])
            due_dt = _ensure_utc_dt(row["next_scan_at"])
            assert due_dt is not None
            occ_id = compute_occurrence_id(
                tenant_id=t_id,
                source_id=s_id,
                trigger="interval",
                window_key=f"interval:{int(due_dt.timestamp())}",
            )
            res = self.run_occurrence(
                db,
                tenant_id=t_id,
                source_id=s_id,
                trigger="interval",
                occurrence_id=occ_id,
                now=now if now is not None else ref_now,
            )
            results.append(res)
        return results

    def trigger_manual(
        self,
        db: Session,
        *,
        tenant_id: str,
        source_id: str,
        occurrence_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> ScanTriggerResult:
        """Manually trigger a source scan (works even when PPM_SCHEDULER_ENABLED=false)."""
        src_exists = db.execute(
            sa.select(sources.c.source_id).where(
                sources.c.tenant_id == tenant_id,
                sources.c.source_id == source_id,
            )
        ).scalar_one_or_none()
        if src_exists is None:
            raise LookupError(f"Source '{source_id}' not found for tenant '{tenant_id}'")

        occ_id = occurrence_id or compute_occurrence_id(
            tenant_id=tenant_id,
            source_id=source_id,
            trigger="manual",
            window_key=f"manual:{uuid.uuid4().hex}",
        )
        return self.run_occurrence(
            db,
            tenant_id=tenant_id,
            source_id=source_id,
            trigger="manual",
            occurrence_id=occ_id,
            now=now,
            force_when_disabled=True,
        )

    def _compute_source_tree_fingerprint(
        self,
        db: Session,
        *,
        tenant_id: str,
        source_id: str,
    ) -> str | None:
        """Compute lightweight read-only stat fingerprint (size + mtime_ns) for polling watcher."""
        src = db.execute(
            sa.select(sources.c.canonical_root).where(
                sources.c.tenant_id == tenant_id,
                sources.c.source_id == source_id,
            )
        ).mappings().first()
        if src is None:
            return None

        root_path = str(src["canonical_root"])
        if not os.path.isdir(root_path):
            return f"missing:{root_path}"

        hasher = hashlib.sha256()
        for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d != ".git")
            rel_dir = os.path.relpath(dirpath, root_path).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = ""
            for fname in sorted(filenames):
                full_p = os.path.join(dirpath, fname)
                rel_p = f"{rel_dir}/{fname}" if rel_dir else fname
                try:
                    st = os.lstat(full_p)
                    hasher.update(f"{rel_p}:{st.st_size}:{st.st_mtime_ns}\n".encode())
                except OSError:
                    hasher.update(f"{rel_p}:error\n".encode())
        return hasher.hexdigest()

    def snapshot_filesystem_baselines(
        self,
        db: Session,
        *,
        tenant_id: str | None = None,
    ) -> None:
        """Capture initial filesystem fingerprints so polling watcher detects subsequent edits."""
        discovered = self.ensure_schedules_for_active_sources(db, tenant_id=tenant_id)
        for t_id, s_id in discovered:
            fp = self._compute_source_tree_fingerprint(db, tenant_id=t_id, source_id=s_id)
            if fp is not None:
                with self._watcher_lock:
                    self._last_seen_fingerprints[(t_id, s_id)] = fp

    def record_file_change(
        self,
        *,
        tenant_id: str,
        source_id: str,
        fingerprint: str | None = None,
        changed_path: str | None = None,
        now: dt.datetime | None = None,
    ) -> None:
        """Queue/coalesce a file-change notification inside the source debounce window."""
        ref_now = _utc_now(now)
        eff_fp = fingerprint or hashlib.sha256(
            (changed_path or str(ref_now.timestamp())).encode("utf-8")
        ).hexdigest()[:24]
        key = (tenant_id, source_id)
        with self._watcher_lock:
            existing = self._pending_watchers.get(key)
            if existing is None:
                self._pending_watchers[key] = _PendingWatcherEvent(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    first_change_at=ref_now,
                    last_change_at=ref_now,
                    fingerprint=eff_fp,
                )
            else:
                existing.last_change_at = ref_now
                existing.fingerprint = eff_fp

    def scan_filesystem_changes(
        self,
        db: Session,
        *,
        tenant_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> list[ScanTriggerResult]:
        """Poll active sources for file modifications and flush expired debounce windows."""
        if not self.is_enabled:
            return []

        ref_now = _utc_now(now)
        discovered = self.ensure_schedules_for_active_sources(db, tenant_id=tenant_id, now=ref_now)
        for t_id, s_id in discovered:
            fp = self._compute_source_tree_fingerprint(db, tenant_id=t_id, source_id=s_id)
            if fp is None:
                continue
            key = (t_id, s_id)
            with self._watcher_lock:
                prev_fp = self._last_seen_fingerprints.get(key)
                if prev_fp is None:
                    self._last_seen_fingerprints[key] = fp
                    continue
                if fp != prev_fp:
                    self._last_seen_fingerprints[key] = fp
                    existing = self._pending_watchers.get(key)
                    if existing is None:
                        self._pending_watchers[key] = _PendingWatcherEvent(
                            tenant_id=t_id,
                            source_id=s_id,
                            first_change_at=ref_now,
                            last_change_at=ref_now,
                            fingerprint=fp[:24],
                        )
                    else:
                        existing.last_change_at = ref_now
                        existing.fingerprint = fp[:24]

        return self.flush_debounced_watchers(db, tenant_id=tenant_id, now=ref_now)

    def flush_debounced_watchers(
        self,
        db: Session,
        *,
        tenant_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> list[ScanTriggerResult]:
        """Dispatch watcher scans for sources whose debounce quiet window has elapsed."""
        if not self.is_enabled:
            return []

        ref_now = _utc_now(now)
        with self._watcher_lock:
            pending_items = list(self._pending_watchers.items())

        results: list[ScanTriggerResult] = []
        for (t_id, s_id), evt in pending_items:
            if tenant_id is not None and t_id != tenant_id:
                continue
            sched_row = db.execute(
                sa.select(ppm_scan_schedules.c.debounce_seconds).where(
                    ppm_scan_schedules.c.tenant_id == t_id,
                    ppm_scan_schedules.c.source_id == s_id,
                )
            ).mappings().first()
            debounce_s = (
                int(sched_row["debounce_seconds"])
                if sched_row is not None
                else self.default_debounce_seconds
            )
            if ref_now < evt.last_change_at + dt.timedelta(seconds=debounce_s):
                continue

            occ_id = compute_occurrence_id(
                tenant_id=t_id,
                source_id=s_id,
                trigger="watcher",
                window_key=f"watcher:{int(evt.first_change_at.timestamp() * 1000)}:{evt.fingerprint}",
            )
            res = self.run_occurrence(
                db,
                tenant_id=t_id,
                source_id=s_id,
                trigger="watcher",
                occurrence_id=occ_id,
                now=now if now is not None else ref_now,
            )
            results.append(res)

            # Clear pending entry unless another change arrived after we started or lease was busy
            if res.status != "lease_busy":
                with self._watcher_lock:
                    cur = self._pending_watchers.get((t_id, s_id))
                    if cur is not None and cur.last_change_at == evt.last_change_at:
                        self._pending_watchers.pop((t_id, s_id), None)

        return results

    @staticmethod
    def get_source_observability(
        db: Session,
        *,
        tenant_id: str,
        source_id: str,
    ) -> SourceScanObservability | None:
        """Read per-source scheduler observability fields for HUD / API."""
        row = db.execute(
            sa.select(
                sources.c.tenant_id,
                sources.c.source_id,
                ppm_scan_schedules.c.interval_seconds,
                ppm_scan_schedules.c.debounce_seconds,
                ppm_scan_schedules.c.next_scan_at,
                ppm_scan_schedules.c.fencing_token,
                ppm_scan_schedules.c.lease_holder,
                ppm_scan_schedules.c.lease_expires_at,
                ppm_scan_source_status.c.last_scan_started_at,
                ppm_scan_source_status.c.last_successful_scan_at,
                ppm_scan_source_status.c.last_error,
                ppm_scan_source_status.c.last_occurrence_id,
                ppm_scan_source_status.c.last_trigger,
                ppm_scan_source_status.c.last_task_id,
                ppm_scan_source_status.c.last_run_id,
                ppm_scan_source_status.c.last_snapshot_id,
            )
            .select_from(
                sources.outerjoin(
                    ppm_scan_schedules,
                    sa.and_(
                        ppm_scan_schedules.c.tenant_id == sources.c.tenant_id,
                        ppm_scan_schedules.c.source_id == sources.c.source_id,
                    ),
                ).outerjoin(
                    ppm_scan_source_status,
                    sa.and_(
                        ppm_scan_source_status.c.tenant_id == sources.c.tenant_id,
                        ppm_scan_source_status.c.source_id == sources.c.source_id,
                    ),
                )
            )
            .where(
                sources.c.tenant_id == tenant_id,
                sources.c.source_id == source_id,
            )
        ).mappings().first()
        if row is None:
            return None

        return SourceScanObservability(
            tenant_id=str(row["tenant_id"]),
            source_id=str(row["source_id"]),
            interval_seconds=int(row["interval_seconds"] or DEFAULT_SCAN_INTERVAL_SECONDS),
            debounce_seconds=int(row["debounce_seconds"] or DEFAULT_SCAN_DEBOUNCE_SECONDS),
            last_scan_started_at=_ensure_utc_dt(row["last_scan_started_at"]),
            last_successful_scan_at=_ensure_utc_dt(row["last_successful_scan_at"]),
            last_error=str(row["last_error"]) if row["last_error"] is not None else None,
            next_due_at=_ensure_utc_dt(row["next_scan_at"]),
            fencing_token=int(row["fencing_token"] or 0),
            lease_holder=str(row["lease_holder"]) if row["lease_holder"] is not None else None,
            lease_expires_at=_ensure_utc_dt(row["lease_expires_at"]),
            last_occurrence_id=(
                str(row["last_occurrence_id"]) if row["last_occurrence_id"] is not None else None
            ),
            last_trigger=str(row["last_trigger"]) if row["last_trigger"] is not None else None,
            last_task_id=str(row["last_task_id"]) if row["last_task_id"] is not None else None,
            last_run_id=str(row["last_run_id"]) if row["last_run_id"] is not None else None,
            last_snapshot_id=(
                str(row["last_snapshot_id"]) if row["last_snapshot_id"] is not None else None
            ),
        )

    @staticmethod
    def list_source_observability(
        db: Session,
        *,
        tenant_id: str,
    ) -> list[SourceScanObservability]:
        """List per-source scheduler observability records for a tenant."""
        src_ids = db.execute(
            sa.select(sources.c.source_id)
            .where(sources.c.tenant_id == tenant_id)
            .order_by(sources.c.source_id.asc())
        ).scalars().all()
        items: list[SourceScanObservability] = []
        for s_id in src_ids:
            obs = PpmScanScheduler.get_source_observability(
                db,
                tenant_id=tenant_id,
                source_id=str(s_id),
            )
            if obs is not None:
                items.append(obs)
        return items


class BackgroundScanScheduler:
    """In-process background thread runner polling watcher and interval triggers (no OS cron)."""

    def __init__(
        self,
        scheduler: PpmScanScheduler,
        session_factory: sessionmaker[Session],
        *,
        poll_interval_seconds: float = 0.25,
        run_startup_catchup: bool = True,
    ) -> None:
        self.scheduler = scheduler
        self.session_factory = session_factory
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.run_startup_catchup = run_startup_catchup
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._startup_done = False

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_once(self) -> list[ScanTriggerResult]:
        """Execute a single scheduler poll cycle (startup catch-up if pending, watcher, interval)."""
        results: list[ScanTriggerResult] = []
        with self.session_factory() as db:
            if self.run_startup_catchup and not self._startup_done:
                results.extend(self.scheduler.startup_catch_up(db))
                self.scheduler.snapshot_filesystem_baselines(db)
                self._startup_done = True
            results.extend(self.scheduler.scan_filesystem_changes(db))
            results.extend(self.scheduler.tick_interval(db))
        return results

    def _loop(self) -> None:
        if self.run_startup_catchup and not self._startup_done:
            try:
                with self.session_factory() as db:
                    self.scheduler.startup_catch_up(db)
                    self._startup_done = True
            except Exception:
                logger.exception("scheduler_startup_catchup_error")

        while not self._stop_event.wait(timeout=self.poll_interval_seconds):
            try:
                with self.session_factory() as db:
                    self.scheduler.scan_filesystem_changes(db)
                    self.scheduler.tick_interval(db)
            except Exception:
                logger.exception("scheduler_background_tick_error")

    def start(self) -> None:
        if self.is_running:
            return
        with self.session_factory() as db:
            self.scheduler.snapshot_filesystem_baselines(db)
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"eap-{self.scheduler.scheduler_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None


__all__ = [
    "COLLECTOR_AGENT_ID",
    "DEFAULT_SCAN_DEBOUNCE_SECONDS",
    "DEFAULT_SCAN_INTERVAL_SECONDS",
    "DEFAULT_SCAN_LEASE_TTL_SECONDS",
    "MIN_SCAN_DEBOUNCE_SECONDS",
    "MIN_SCAN_INTERVAL_SECONDS",
    "SCAN_LEASE_RESOURCE_TYPE",
    "VALID_TRIGGERS",
    "BackgroundScanScheduler",
    "PpmScanScheduler",
    "ScanTriggerResult",
    "SourceScanObservability",
    "compute_occurrence_id",
    "validate_schedule_config",
]
