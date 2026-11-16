"""PPM Assessment Orchestrator (P2a-WP6 / DG-2 / ADR-021 rev. 2).

Owns the ``snapshot -> assessment`` transition and all independent reassessment triggers:
- ``snapshot``: ``ppm.snapshot.captured`` outbox event (scheduler/collector)
- ``baseline_changed``: ``ppm.baseline.approved`` outbox event
- ``evidence_ttl_expired``: evidence TTL timer and waiver expiry timer (wall-clock ``now``)
- ``model_recovered``: model health recovery for ``analysis_status=model_failed`` assessments
- ``manual``: explicit operator/CLI reassessment command

Invariants:
- Every trigger produces at most one assessment task per ``(tenant_id, project_id, trigger, trigger_ref)``.
- ``basis_seq`` and all inputs (``project_snapshot_id``, ``snapshot_seq``, ``assessed_revision``,
  ``baseline_revision``, ``evidence_ttl_days``, ``locked_now``) are locked at task creation time.
- "Bypass unchanged scope" (dedup on ``inputs_hash``) applies ONLY to trigger ``snapshot``.
- Pipeline per task: optional verifier (WP9) -> optional analysis (WP7) -> reducer + store + ``basis_seq`` CAS.
- Model failure continues to reducer with ``analysis_status="model_failed"``.
- Reducer error fails the task with a redacted reason and leaves ``project_current_assessment`` unchanged.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from eap.config import Settings, get_settings
from eap.contracts.models.binding import Binding
from eap.contracts.models.task_request import TaskRequest
from eap.db import (
    agent_bindings,
    collector_snapshots,
    outbox,
    ppm_assessment_tasks,
    ppm_assessments,
    ppm_baselines,
    ppm_evidence,
    ppm_orchestrator_cursors,
    ppm_waivers,
    project_current_assessment,
    project_snapshots,
    runs,
)
from eap.model_gateway import (
    ModelProvider,
    ProfileRegistry,
    TelemetryExporter,
)
from eap.observability.logging import log_context, redact_text
from eap.ppm.analysis import load_locked_analysis_inputs, run_analysis
from eap.ppm.assessment_inputs import build_assessment_inputs
from eap.ppm.assessments import (
    VALID_TRIGGERS,
    AssessmentRecord,
    allocate_basis_seq,
    create_assessment,
)
from eap.ppm.verifier import run_verifier
from eap.run.service import RunService
from eap.task.service import TaskService

logger = logging.getLogger("eap.ppm.orchestrator")

DEFAULT_ORCHESTRATOR_AGENT_ID = "ppm-assessment-orchestrator"
DEFAULT_ORCHESTRATOR_CONSUMER = "ppm_assessment_orchestrator"


def _default_model_binding() -> Binding:
    return Binding.model_validate(
        {
            "contract_version": "1.1",
            "binding_id": "binding-ppm-orchestrator-default",
            "agent_id": DEFAULT_ORCHESTRATOR_AGENT_ID,
            "release_version": "1.0.0",
            "release_sha256": "0" * 64,
            "mode": "managed",
            "endpoint_ref": "inproc:eap.model_gateway.FakeModelProvider",
            "secret_ref": None,
            "resource_scope_refs": ["project:*"],
            "certified_capabilities": {
                "submit": True,
                "status": True,
                "events": True,
                "collect_result": True,
                "cancel": True,
                "resume": False,
                "approve_tool": False,
            },
            "capability_evidence_ref": "artifact:ppm-orchestrator",
            "model_profile_ref": "local-scoped-default",
            "grant_refs": ["grant:local"],
            "execution_boundary": "platform_gateway",
            "policy_version": "policy-1.0",
            "fallback_model_profile_refs": [],
            "embedding_profile_ref": None,
            "egress_policy_ref": "egress:local-only",
            "telemetry_export": "local_only",
        }
    )


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _ensure_utc(value: dt.datetime | str | None) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        parsed = dt.datetime.fromisoformat(value)
    else:
        parsed = value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


@dataclass(frozen=True)
class AdmittedAssessmentTask:
    id: str
    tenant_id: str
    project_id: str
    trigger: str
    trigger_ref: str
    basis_seq: int
    task_id: str
    run_id: str
    status: str
    project_snapshot_id: str | None
    snapshot_seq: int
    assessed_revision: str | None
    baseline_revision: int | None
    evidence_ttl_days: float
    locked_now: dt.datetime | None
    previous_assessment_id: str | None
    deduplicated: bool
    assessment_id: str | None = None


@dataclass(frozen=True)
class OrchestratedAssessmentResult:
    orchestrator_task_id: str
    tenant_id: str
    project_id: str
    trigger: str
    trigger_ref: str
    basis_seq: int
    task_id: str
    run_id: str
    status: str  # succeeded | bypassed | failed
    deduplicated: bool = False
    assessment: AssessmentRecord | None = None
    assessment_id: str | None = None
    analysis_status: str | None = None
    verifier_executed: bool = False
    error_reason: str | None = None


def _row_to_admitted(row: Mapping[Any, Any], *, deduplicated: bool) -> AdmittedAssessmentTask:
    return AdmittedAssessmentTask(
        id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        project_id=str(row["project_id"]),
        trigger=str(row["trigger"]),
        trigger_ref=str(row["trigger_ref"]),
        basis_seq=int(row["basis_seq"]),
        task_id=str(row["task_id"]),
        run_id=str(row["run_id"]),
        status=str(row["status"]),
        project_snapshot_id=row["project_snapshot_id"],
        snapshot_seq=int(row["snapshot_seq"]),
        assessed_revision=row["assessed_revision"],
        baseline_revision=row["baseline_revision"],
        evidence_ttl_days=float(row["evidence_ttl_days"]),
        locked_now=row["locked_now"],
        previous_assessment_id=row["previous_assessment_id"],
        deduplicated=deduplicated,
        assessment_id=row["assessment_id"],
    )


class AssessmentOrchestrator:
    """Coordinates snapshot/baseline/timer/health/manual triggers -> assessment pipeline."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        model_provider: ModelProvider | None = None,
        profile_registry: ProfileRegistry | None = None,
        telemetry_exporter: TelemetryExporter | None = None,
        model_binding: Binding | Mapping[str, Any] | None = None,
        evidence_ttl_days: float = 7.0,
    ) -> None:
        self._settings = settings
        self.model_provider = model_provider
        self.profile_registry = profile_registry
        self.telemetry_exporter = telemetry_exporter
        self.model_binding = model_binding
        self.default_evidence_ttl_days = float(evidence_ttl_days)

    @property
    def settings(self) -> Settings:
        return self._settings if self._settings is not None else get_settings()

    def _resolve_binding(self, db: Session, tenant_id: str) -> Binding | Mapping[str, Any]:
        if self.model_binding is not None:
            return self.model_binding
        row = db.execute(
            sa.select(agent_bindings)
            .where(
                agent_bindings.c.tenant_id == tenant_id,
                agent_bindings.c.agent_id == DEFAULT_ORCHESTRATOR_AGENT_ID,
                agent_bindings.c.revoked_at.is_(None),
            )
            .order_by(agent_bindings.c.created_at.desc(), agent_bindings.c.id.desc())
            .limit(1)
        ).mappings().first()
        if row is not None:
            default_b = _default_model_binding().model_dump()
            default_b.update(
                {
                    "binding_id": str(row["binding_id"]),
                    "agent_id": str(row["agent_id"]),
                    "model_profile_ref": str(row["model_profile_ref"]),
                    "egress_policy_ref": str(row["egress_policy_ref"]),
                    "fallback_model_profile_refs": list(row.get("fallback_model_profile_refs") or ()),
                }
            )
            return Binding.model_validate(default_b)
        return _default_model_binding()

    def admit_trigger(
        self,
        db: Session,
        tenant_id: str,
        project_id: str,
        trigger: str,
        trigger_ref: str,
        *,
        project_snapshot_id: str | None = None,
        assessed_revision: str | None = None,
        baseline_revision: int | None = None,
        evidence_ttl_days: float | None = None,
        locked_now: dt.datetime | str | None = None,
        previous_assessment_id: str | None = None,
        dependency_refs: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
        fixture_id: str | None = None,
    ) -> AdmittedAssessmentTask:
        """Admit a trigger idempotently on ``(tenant_id, project_id, trigger, trigger_ref)``.

        Locks all inputs and allocates ``basis_seq`` at creation time.
        """
        if trigger not in VALID_TRIGGERS:
            raise ValueError(f"Invalid trigger {trigger!r}; expected one of {VALID_TRIGGERS}")
        if not trigger_ref:
            raise ValueError("trigger_ref must be non-empty")

        # 1. Fast dedup check
        existing = db.execute(
            sa.select(ppm_assessment_tasks).where(
                ppm_assessment_tasks.c.tenant_id == tenant_id,
                ppm_assessment_tasks.c.project_id == project_id,
                ppm_assessment_tasks.c.trigger == trigger,
                ppm_assessment_tasks.c.trigger_ref == trigger_ref,
            )
        ).mappings().first()
        if existing is not None:
            return _row_to_admitted(existing, deduplicated=True)

        # 2. Lock snapshot inputs at creation time
        if project_snapshot_id is not None:
            psnap = db.execute(
                sa.select(project_snapshots).where(
                    project_snapshots.c.tenant_id == tenant_id,
                    project_snapshots.c.project_id == project_id,
                    project_snapshots.c.id == project_snapshot_id,
                )
            ).mappings().first()
        else:
            psnap = db.execute(
                sa.select(project_snapshots)
                .where(
                    project_snapshots.c.tenant_id == tenant_id,
                    project_snapshots.c.project_id == project_id,
                )
                .order_by(project_snapshots.c.snapshot_seq.desc(), project_snapshots.c.created_at.desc())
                .limit(1)
            ).mappings().first()

        locked_snapshot_id: str | None = str(psnap["id"]) if psnap is not None else None
        locked_snapshot_seq: int = int(psnap["snapshot_seq"]) if psnap is not None else 1
        locked_revision: str | None = assessed_revision
        if locked_revision is None and psnap is not None:
            csnap = db.execute(
                sa.select(collector_snapshots.c.git_commit, collector_snapshots.c.tree_hash).where(
                    collector_snapshots.c.tenant_id == tenant_id,
                    collector_snapshots.c.id == str(psnap["snapshot_id"]),
                )
            ).mappings().first()
            if csnap is not None:
                locked_revision = csnap["git_commit"] or csnap["tree_hash"]

        # 3. Lock approved baseline revision at creation time
        locked_baseline_rev = baseline_revision
        if locked_baseline_rev is None:
            bl_row = db.execute(
                sa.select(ppm_baselines.c.baseline_revision)
                .where(
                    ppm_baselines.c.tenant_id == tenant_id,
                    ppm_baselines.c.project_id == project_id,
                    ppm_baselines.c.status == "approved",
                )
                .order_by(ppm_baselines.c.baseline_revision.desc())
                .limit(1)
            ).mappings().first()
            if bl_row is not None:
                locked_baseline_rev = int(bl_row["baseline_revision"])

        # 4. Lock previous assessment id at creation time
        locked_prev_id = previous_assessment_id
        if locked_prev_id is None:
            curr_ptr = db.execute(
                sa.select(project_current_assessment.c.assessment_id).where(
                    project_current_assessment.c.tenant_id == tenant_id,
                    project_current_assessment.c.project_id == project_id,
                )
            ).mappings().first()
            if curr_ptr is not None:
                locked_prev_id = str(curr_ptr["assessment_id"])

        if locked_revision is None and locked_prev_id is not None:
            prev_rev = db.execute(
                sa.select(ppm_assessments.c.assessed_revision).where(
                    ppm_assessments.c.tenant_id == tenant_id,
                    ppm_assessments.c.id == locked_prev_id,
                )
            ).scalar_one_or_none()
            if prev_rev is not None:
                locked_revision = str(prev_rev)

        resolved_ttl = float(evidence_ttl_days if evidence_ttl_days is not None else self.default_evidence_ttl_days)
        resolved_now = _ensure_utc(locked_now)
        if resolved_now is None and trigger != "snapshot":
            resolved_now = _utc_now()

        # 5. Create logical Task & Run idempotently
        idem_key = f"assess:{project_id}:{trigger}:{trigger_ref}"
        task_rec = TaskService.create(
            db,
            tenant_id=tenant_id,
            request=TaskRequest(
                contract_version="1.0",
                idempotency_key=idem_key,
                task_type="ppm.project.assess",
                agent_id=DEFAULT_ORCHESTRATOR_AGENT_ID,
                objective=f"Assess project '{project_id}' on trigger '{trigger}' ({trigger_ref})",
                input_refs=[
                    f"project:{project_id}",
                    f"trigger:{trigger}",
                    f"trigger_ref:{trigger_ref}",
                ],
                resource_refs=[f"project:{project_id}"],
            ),
            project_id=project_id,
        )
        run_row = db.execute(
            sa.select(runs.c.id)
            .where(runs.c.tenant_id == tenant_id, runs.c.task_id == task_rec.id)
            .order_by(runs.c.attempt.desc())
            .limit(1)
        ).mappings().one()
        run_id = str(run_row["id"])

        # 6. Allocate basis_seq and insert ppm_assessment_tasks with ON CONFLICT DO NOTHING
        orch_id = f"atask-{uuid.uuid4().hex[:16]}"
        basis_seq = allocate_basis_seq(db, tenant_id, project_id)
        now = _utc_now()
        inserted = db.execute(
            pg_insert(ppm_assessment_tasks)
            .values(
                id=orch_id,
                tenant_id=tenant_id,
                project_id=project_id,
                trigger=trigger,
                trigger_ref=trigger_ref,
                basis_seq=basis_seq,
                task_id=task_rec.id,
                run_id=run_id,
                status="pending",
                project_snapshot_id=locked_snapshot_id,
                snapshot_seq=locked_snapshot_seq,
                assessed_revision=locked_revision,
                baseline_revision=locked_baseline_rev,
                evidence_ttl_days=resolved_ttl,
                locked_now=resolved_now,
                previous_assessment_id=locked_prev_id,
                dependency_refs=list(dependency_refs or []),
                context=context,
                fixture_id=fixture_id,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "project_id", "trigger", "trigger_ref"]
            )
            .returning(ppm_assessment_tasks)
        ).mappings().first()

        if inserted is None:
            existing_after = db.execute(
                sa.select(ppm_assessment_tasks).where(
                    ppm_assessment_tasks.c.tenant_id == tenant_id,
                    ppm_assessment_tasks.c.project_id == project_id,
                    ppm_assessment_tasks.c.trigger == trigger,
                    ppm_assessment_tasks.c.trigger_ref == trigger_ref,
                )
            ).mappings().one()
            return _row_to_admitted(existing_after, deduplicated=True)

        logger.info(
            "orchestrator_task_admitted project_id=%s trigger=%s trigger_ref=%s basis_seq=%s task_id=%s",
            project_id,
            trigger,
            trigger_ref,
            basis_seq,
            task_rec.id,
        )
        return _row_to_admitted(inserted, deduplicated=False)

    def execute_task(
        self,
        db: Session,
        tenant_id: str,
        orchestrator_task_id: str,
        *,
        before_reduce_hook: Callable[[AdmittedAssessmentTask], None] | None = None,
    ) -> OrchestratedAssessmentResult:
        """Execute an admitted assessment task through verifier -> analysis -> reducer -> CAS."""
        cfg = self.settings
        row = db.execute(
            sa.select(ppm_assessment_tasks)
            .where(
                ppm_assessment_tasks.c.tenant_id == tenant_id,
                ppm_assessment_tasks.c.id == orchestrator_task_id,
            )
            .with_for_update()
        ).mappings().first()
        if row is None:
            raise ValueError(f"Assessment orchestrator task {orchestrator_task_id!r} not found")

        task = _row_to_admitted(row, deduplicated=False)

        # Already terminal -> idempotent return
        if task.status in ("succeeded", "bypassed", "failed"):
            return OrchestratedAssessmentResult(
                orchestrator_task_id=task.id,
                tenant_id=task.tenant_id,
                project_id=task.project_id,
                trigger=task.trigger,
                trigger_ref=task.trigger_ref,
                basis_seq=task.basis_seq,
                task_id=task.task_id,
                run_id=task.run_id,
                status=task.status,
                deduplicated=(task.status == "bypassed"),
                assessment_id=row["assessment_id"],
                analysis_status=row["analysis_status"],
                verifier_executed=bool(row["verifier_executed"]),
                error_reason=row["error_reason"],
            )

        # Crash recovery check: if ppm_assessments already has (tenant_id, project_id, basis_seq),
        # finalize the task without creating a duplicate assessment or moving the pointer again.
        existing_asmt = db.execute(
            sa.select(ppm_assessments).where(
                ppm_assessments.c.tenant_id == tenant_id,
                ppm_assessments.c.project_id == task.project_id,
                ppm_assessments.c.basis_seq == task.basis_seq,
            )
        ).mappings().first()
        if existing_asmt is not None:
            now = _utc_now()
            db.execute(
                ppm_assessment_tasks.update()
                .where(
                    ppm_assessment_tasks.c.tenant_id == tenant_id,
                    ppm_assessment_tasks.c.id == task.id,
                )
                .values(
                    status="succeeded",
                    assessment_id=str(existing_asmt["id"]),
                    analysis_status=str(existing_asmt["analysis_status"]),
                    updated_at=now,
                    completed_at=now,
                )
            )
            self._ensure_run_terminal(db, tenant_id, task.run_id, "succeeded", "recovered_existing_assessment", str(existing_asmt["id"]))
            return OrchestratedAssessmentResult(
                orchestrator_task_id=task.id,
                tenant_id=task.tenant_id,
                project_id=task.project_id,
                trigger=task.trigger,
                trigger_ref=task.trigger_ref,
                basis_seq=task.basis_seq,
                task_id=task.task_id,
                run_id=task.run_id,
                status="succeeded",
                deduplicated=False,
                assessment_id=str(existing_asmt["id"]),
                analysis_status=str(existing_asmt["analysis_status"]),
            )

        # Transition task to running and RunService to running
        db.execute(
            ppm_assessment_tasks.update()
            .where(
                ppm_assessment_tasks.c.tenant_id == tenant_id,
                ppm_assessment_tasks.c.id == task.id,
            )
            .values(status="running", updated_at=_utc_now())
        )
        self._advance_run_to_running(db, tenant_id, task.run_id)

        with log_context(tenant_id=tenant_id, task_id=task.task_id, run_id=task.run_id):
            # 0. "Bypass unchanged scope" applies ONLY to trigger == "snapshot"
            if task.trigger == "snapshot":
                _, candidate_hash, _ = build_assessment_inputs(
                    db,
                    tenant_id,
                    task.project_id,
                    basis_seq=task.basis_seq,
                    now=task.locked_now,
                    evidence_ttl_days=task.evidence_ttl_days,
                    project_snapshot_id=task.project_snapshot_id,
                    assessed_revision=task.assessed_revision,
                    previous_assessment_id=task.previous_assessment_id,
                    baseline_revision=task.baseline_revision,
                    dependency_refs=list(row["dependency_refs"] or []),
                    context=row["context"],
                    fixture_id=row["fixture_id"],
                )
                curr_ptr = db.execute(
                    sa.select(project_current_assessment.c.assessment_id).where(
                        project_current_assessment.c.tenant_id == tenant_id,
                        project_current_assessment.c.project_id == task.project_id,
                    )
                ).mappings().first()
                if curr_ptr is not None:
                    curr_asmt = db.execute(
                        sa.select(ppm_assessments).where(
                            ppm_assessments.c.tenant_id == tenant_id,
                            ppm_assessments.c.id == str(curr_ptr["assessment_id"]),
                        )
                    ).mappings().first()
                    if curr_asmt is not None and str(curr_asmt["inputs_hash"]) == candidate_hash:
                        return self._bypass_unchanged_task(
                            db,
                            tenant_id=tenant_id,
                            task=task,
                            curr_asmt=curr_asmt,
                        )
                    if (
                        curr_asmt is not None
                        and cfg.ppm_analysis_enabled
                        and curr_asmt["assessed_revision"] == task.assessed_revision
                        and curr_asmt["baseline_revision"] == task.baseline_revision
                    ):
                        locked_pre = load_locked_analysis_inputs(
                            db,
                            tenant_id,
                            task.project_id,
                            assessed_revision=task.assessed_revision,
                            baseline_revision=task.baseline_revision,
                        )
                        pre_binding = self._resolve_binding(db, tenant_id)
                        pre_analysis = run_analysis(
                            locked_pre,
                            pre_binding,
                            provider=self.model_provider,
                            registry=self.profile_registry,
                            telemetry_exporter=self.telemetry_exporter,
                            settings=cfg,
                        )
                        _, pre_final_hash, _ = build_assessment_inputs(
                            db,
                            tenant_id,
                            task.project_id,
                            basis_seq=task.basis_seq,
                            now=task.locked_now,
                            evidence_ttl_days=task.evidence_ttl_days,
                            project_snapshot_id=task.project_snapshot_id,
                            assessed_revision=task.assessed_revision,
                            previous_assessment_id=task.previous_assessment_id,
                            baseline_revision=task.baseline_revision,
                            dependency_refs=list(row["dependency_refs"] or []),
                            context=row["context"],
                            fixture_id=row["fixture_id"],
                            extra_claims=list(pre_analysis.reducer_claims),
                        )
                        if str(curr_asmt["inputs_hash"]) == pre_final_hash:
                            return self._bypass_unchanged_task(
                                db,
                                tenant_id=tenant_id,
                                task=task,
                                curr_asmt=curr_asmt,
                            )

            # Optional test hook for deterministic parallel ordering (C34 / C39)
            if before_reduce_hook is not None:
                before_reduce_hook(task)

            # 1. Optional Verifier (WP9) when PPM_VERIFIER_ENABLED=true
            verifier_executed = False
            if cfg.ppm_verifier_enabled and task.assessed_revision and task.baseline_revision is not None:
                v_report = run_verifier(
                    db,
                    tenant_id,
                    task.project_id,
                    task.assessed_revision,
                    baseline_revision=task.baseline_revision,
                    settings=cfg,
                )
                verifier_executed = bool(v_report.executed)

            # 2. Optional Analysis step (WP7) when PPM_ANALYSIS_ENABLED=true
            extra_claims: list[dict[str, Any]] = []
            analysis_status = "skipped"
            if cfg.ppm_analysis_enabled:
                locked_analysis = load_locked_analysis_inputs(
                    db,
                    tenant_id,
                    task.project_id,
                    assessed_revision=task.assessed_revision,
                    baseline_revision=task.baseline_revision,
                )
                binding = self._resolve_binding(db, tenant_id)
                a_res = run_analysis(
                    locked_analysis,
                    binding,
                    provider=self.model_provider,
                    registry=self.profile_registry,
                    telemetry_exporter=self.telemetry_exporter,
                    settings=cfg,
                )
                analysis_status = a_res.analysis_status
                extra_claims = list(a_res.reducer_claims)

            # 3. Post-verifier/analysis semantic dedup for trigger='snapshot' (e.g. when extra_claims or verifier ran)
            if task.trigger == "snapshot" and task.assessed_revision is not None and (verifier_executed or extra_claims):
                _, final_hash, _ = build_assessment_inputs(
                    db,
                    tenant_id,
                    task.project_id,
                    basis_seq=task.basis_seq,
                    now=task.locked_now,
                    evidence_ttl_days=task.evidence_ttl_days,
                    project_snapshot_id=task.project_snapshot_id,
                    assessed_revision=task.assessed_revision,
                    previous_assessment_id=task.previous_assessment_id,
                    baseline_revision=task.baseline_revision,
                    dependency_refs=list(row["dependency_refs"] or []),
                    context=row["context"],
                    fixture_id=row["fixture_id"],
                    extra_claims=extra_claims,
                )
                curr_ptr2 = db.execute(
                    sa.select(project_current_assessment.c.assessment_id).where(
                        project_current_assessment.c.tenant_id == tenant_id,
                        project_current_assessment.c.project_id == task.project_id,
                    )
                ).mappings().first()
                if curr_ptr2 is not None:
                    curr_asmt2 = db.execute(
                        sa.select(ppm_assessments).where(
                            ppm_assessments.c.tenant_id == tenant_id,
                            ppm_assessments.c.id == str(curr_ptr2["assessment_id"]),
                        )
                    ).mappings().first()
                    if curr_asmt2 is not None and str(curr_asmt2["inputs_hash"]) == final_hash:
                        return self._bypass_unchanged_task(
                            db,
                            tenant_id=tenant_id,
                            task=task,
                            curr_asmt=curr_asmt2,
                            verifier_executed=verifier_executed,
                        )

            # 4. Reducer -> Store -> CAS pointer (inside savepoint so reducer error leaves pointer untouched)
            effective_now = task.locked_now
            if verifier_executed and task.trigger != "evidence_ttl_expired":
                post_verifier_now = _utc_now()
                if effective_now is None or post_verifier_now > effective_now:
                    effective_now = post_verifier_now
            try:
                with db.begin_nested():
                    asmt = create_assessment(
                        db,
                        tenant_id,
                        task.project_id,
                        trigger=task.trigger,
                        basis_seq=task.basis_seq,
                        analysis_status=analysis_status,
                        now=effective_now,
                        evidence_ttl_days=task.evidence_ttl_days,
                        project_snapshot_id=task.project_snapshot_id,
                        assessed_revision=task.assessed_revision,
                        previous_assessment_id=task.previous_assessment_id,
                        baseline_revision=task.baseline_revision,
                        dependency_refs=list(row["dependency_refs"] or []),
                        context=row["context"],
                        fixture_id=row["fixture_id"],
                        extra_claims=extra_claims,
                        skip_inputs_hash_dedup=True,
                    )
            except Exception as exc:  # noqa: BLE001
                redacted_err = redact_text(f"{type(exc).__name__}: {exc}")[:2000]
                now = _utc_now()
                db.execute(
                    ppm_assessment_tasks.update()
                    .where(
                        ppm_assessment_tasks.c.tenant_id == tenant_id,
                        ppm_assessment_tasks.c.id == task.id,
                    )
                    .values(
                        status="failed",
                        analysis_status=analysis_status,
                        verifier_executed=verifier_executed,
                        error_reason=redacted_err,
                        updated_at=now,
                        completed_at=now,
                    )
                )
                self._ensure_run_terminal(db, tenant_id, task.run_id, "failed", "reducer_error", None)
                logger.warning(
                    "orchestrator_reducer_failed project_id=%s basis_seq=%s error=%s",
                    task.project_id,
                    task.basis_seq,
                    redacted_err,
                )
                return OrchestratedAssessmentResult(
                    orchestrator_task_id=task.id,
                    tenant_id=tenant_id,
                    project_id=task.project_id,
                    trigger=task.trigger,
                    trigger_ref=task.trigger_ref,
                    basis_seq=task.basis_seq,
                    task_id=task.task_id,
                    run_id=task.run_id,
                    status="failed",
                    analysis_status=analysis_status,
                    verifier_executed=verifier_executed,
                    error_reason=redacted_err,
                )

            now = _utc_now()
            db.execute(
                ppm_assessment_tasks.update()
                .where(
                    ppm_assessment_tasks.c.tenant_id == tenant_id,
                    ppm_assessment_tasks.c.id == task.id,
                )
                .values(
                    status="succeeded",
                    assessment_id=asmt.id,
                    analysis_status=analysis_status,
                    verifier_executed=verifier_executed,
                    updated_at=now,
                    completed_at=now,
                )
            )
            self._ensure_run_terminal(db, tenant_id, task.run_id, "succeeded", "assessment_created", asmt.id)
            logger.info(
                "orchestrator_task_succeeded project_id=%s trigger=%s basis_seq=%s assessment_id=%s is_current=%s analysis_status=%s",
                task.project_id,
                task.trigger,
                task.basis_seq,
                asmt.id,
                asmt.is_current,
                analysis_status,
            )
            return OrchestratedAssessmentResult(
                orchestrator_task_id=task.id,
                tenant_id=tenant_id,
                project_id=task.project_id,
                trigger=task.trigger,
                trigger_ref=task.trigger_ref,
                basis_seq=task.basis_seq,
                task_id=task.task_id,
                run_id=task.run_id,
                status="succeeded",
                deduplicated=False,
                assessment=asmt,
                assessment_id=asmt.id,
                analysis_status=analysis_status,
                verifier_executed=verifier_executed,
            )

    @staticmethod
    def _advance_run_to_running(db: Session, tenant_id: str, run_id: str) -> str | None:
        run_row = db.execute(
            sa.select(runs.c.state).where(runs.c.tenant_id == tenant_id, runs.c.id == run_id)
        ).mappings().first()
        if run_row is None:
            return None
        cur = str(run_row["state"])
        if cur == "queued":
            RunService.transition(
                db,
                tenant_id=tenant_id,
                run_id=run_id,
                to_state="dispatching",
                expected_from="queued",
                reason="orchestrator_dispatched",
            )
            cur = "dispatching"
        if cur == "dispatching":
            RunService.transition(
                db,
                tenant_id=tenant_id,
                run_id=run_id,
                to_state="running",
                expected_from="dispatching",
                reason="orchestrator_started",
            )
            cur = "running"
        return cur

    def _bypass_unchanged_task(
        self,
        db: Session,
        *,
        tenant_id: str,
        task: AdmittedAssessmentTask,
        curr_asmt: Mapping[Any, Any],
        verifier_executed: bool = False,
    ) -> OrchestratedAssessmentResult:
        """Mark an unchanged snapshot task as bypassed without creating a new assessment."""
        now = _utc_now()
        curr_id = str(curr_asmt["id"])
        db.execute(
            ppm_assessment_tasks.update()
            .where(
                ppm_assessment_tasks.c.tenant_id == tenant_id,
                ppm_assessment_tasks.c.id == task.id,
            )
            .values(
                status="bypassed",
                assessment_id=curr_id,
                analysis_status=str(curr_asmt["analysis_status"]),
                verifier_executed=verifier_executed,
                updated_at=now,
                completed_at=now,
            )
        )
        self._ensure_run_terminal(db, tenant_id, task.run_id, "succeeded", "unchanged_scope_bypassed", curr_id)
        logger.info(
            "orchestrator_scope_bypassed project_id=%s trigger_ref=%s assessment_id=%s",
            task.project_id,
            task.trigger_ref,
            curr_id,
        )
        return OrchestratedAssessmentResult(
            orchestrator_task_id=task.id,
            tenant_id=tenant_id,
            project_id=task.project_id,
            trigger=task.trigger,
            trigger_ref=task.trigger_ref,
            basis_seq=task.basis_seq,
            task_id=task.task_id,
            run_id=task.run_id,
            status="bypassed",
            deduplicated=True,
            assessment_id=curr_id,
            analysis_status=str(curr_asmt["analysis_status"]),
            verifier_executed=verifier_executed,
        )

    @classmethod
    def _ensure_run_terminal(
        cls,
        db: Session,
        tenant_id: str,
        run_id: str,
        to_state: str,
        reason: str,
        evidence_ref: str | None,
    ) -> None:
        cur = cls._advance_run_to_running(db, tenant_id, run_id)
        if cur == "running":
            RunService.transition(
                db,
                tenant_id=tenant_id,
                run_id=run_id,
                to_state=to_state,
                reason=reason,
                evidence_ref=evidence_ref,
            )

    def consume_outbox_events(
        self,
        db: Session,
        *,
        tenant_id: str | None = None,
        event_types: Sequence[str] = ("ppm.snapshot.captured", "ppm.baseline.approved"),
        consumer_name: str = DEFAULT_ORCHESTRATOR_CONSUMER,
        execute: bool = True,
        project_snapshot_ids: Sequence[str] | None = None,
    ) -> list[OrchestratedAssessmentResult]:
        """Consume ``ppm.snapshot.captured`` and ``ppm.baseline.approved`` from ``outbox``."""
        if not self.settings.ppm_orchestrator_enabled:
            return []

        cursor_row = db.execute(
            sa.select(ppm_orchestrator_cursors.c.last_outbox_id).where(
                ppm_orchestrator_cursors.c.consumer_name == consumer_name
            )
        ).scalar_one_or_none()
        last_id = int(cursor_row) if cursor_row is not None else 0

        stmt = sa.select(outbox).where(
            outbox.c.id > last_id,
            outbox.c.event_type.in_(list(event_types)),
        )
        if tenant_id is not None:
            stmt = stmt.where(outbox.c.tenant_id == tenant_id)
        rows = db.execute(stmt.order_by(outbox.c.id.asc())).mappings().all()

        results: list[OrchestratedAssessmentResult] = []
        max_seen_id = last_id
        filter_psnaps = set(project_snapshot_ids) if project_snapshot_ids is not None else None

        for ev in rows:
            ev_id = int(ev["id"])
            max_seen_id = max(max_seen_id, ev_id)
            ev_type = str(ev["event_type"])
            ev_tenant = str(ev["tenant_id"])
            payload = dict(ev["payload"] or {})

            if ev_type == "ppm.snapshot.captured":
                p_id = str(payload.get("project_id") or "")
                psnap_id = payload.get("project_snapshot_id")
                snap_id = payload.get("snapshot_id")
                if not p_id:
                    continue
                if filter_psnaps is not None and psnap_id not in filter_psnaps:
                    continue
                t_ref = str(psnap_id or snap_id or ev_id)
                admitted = self.admit_trigger(
                    db,
                    ev_tenant,
                    p_id,
                    trigger="snapshot",
                    trigger_ref=t_ref,
                    project_snapshot_id=str(psnap_id) if psnap_id else None,
                    assessed_revision=payload.get("git_commit"),
                )
                if execute and admitted.status == "pending":
                    results.append(self.execute_task(db, ev_tenant, admitted.id))
            elif ev_type == "ppm.baseline.approved":
                p_id = str(payload.get("project_id") or "")
                bl_rev = payload.get("baseline_revision")
                if not p_id or bl_rev is None:
                    continue
                admitted = self.admit_trigger(
                    db,
                    ev_tenant,
                    p_id,
                    trigger="baseline_changed",
                    trigger_ref=str(int(bl_rev)),
                    baseline_revision=int(bl_rev),
                    locked_now=payload.get("approved_at") or _utc_now(),
                )
                if execute and admitted.status == "pending":
                    results.append(self.execute_task(db, ev_tenant, admitted.id))

        if max_seen_id > last_id and filter_psnaps is None:
            now = _utc_now()
            db.execute(
                pg_insert(ppm_orchestrator_cursors)
                .values(consumer_name=consumer_name, last_outbox_id=max_seen_id, updated_at=now)
                .on_conflict_do_update(
                    index_elements=["consumer_name"],
                    set_={"last_outbox_id": max_seen_id, "updated_at": now},
                )
            )

        return results

    def check_evidence_ttl_timers(
        self,
        db: Session,
        *,
        tenant_id: str | None = None,
        now: dt.datetime | None = None,
        execute: bool = True,
    ) -> list[OrchestratedAssessmentResult]:
        """Emit ``evidence_ttl_expired`` tasks for active evidence past its TTL (no rescan needed)."""
        if not self.settings.ppm_orchestrator_enabled:
            return []

        eval_now = _ensure_utc(now) or _utc_now()
        stmt = (
            sa.select(
                project_current_assessment.c.tenant_id,
                project_current_assessment.c.project_id,
                project_current_assessment.c.assessment_id,
                ppm_assessments.c.assessed_revision,
                ppm_assessments.c.inputs,
                ppm_assessments.c.output,
            )
            .select_from(
                project_current_assessment.join(
                    ppm_assessments,
                    sa.and_(
                        ppm_assessments.c.tenant_id == project_current_assessment.c.tenant_id,
                        ppm_assessments.c.id == project_current_assessment.c.assessment_id,
                    ),
                )
            )
        )
        if tenant_id is not None:
            stmt = stmt.where(project_current_assessment.c.tenant_id == tenant_id)

        results: list[OrchestratedAssessmentResult] = []
        for ptr in db.execute(stmt).mappings().all():
            t_id = str(ptr["tenant_id"])
            p_id = str(ptr["project_id"])
            inputs = dict(ptr["inputs"] or {})
            output = dict(ptr["output"] or {})
            ttl_days = float(inputs.get("evidence_ttl_days", self.default_evidence_ttl_days))

            # Collect evidence IDs supporting currently verified criteria
            supporting_ev_ids: set[str] = set()
            for crit in output.get("criteria", []):
                if isinstance(crit, dict) and crit.get("status") == "verified":
                    for ref in crit.get("supporting_evidence_refs", []) or []:
                        supporting_ev_ids.add(str(ref))
            if not supporting_ev_ids:
                continue

            ev_rows = db.execute(
                sa.select(ppm_evidence).where(
                    ppm_evidence.c.tenant_id == t_id,
                    ppm_evidence.c.project_id == p_id,
                    ppm_evidence.c.evidence_id.in_(sorted(supporting_ev_ids)),
                )
            ).mappings().all()

            for ev in ev_rows:
                obs_at = _ensure_utc(ev["collected_at"])
                if obs_at is None:
                    continue
                expiry_at = obs_at + dt.timedelta(days=ttl_days)
                if eval_now > expiry_at:
                    trigger_ref = f"{ev['evidence_id']}:{expiry_at.isoformat()}"
                    admitted = self.admit_trigger(
                        db,
                        t_id,
                        p_id,
                        trigger="evidence_ttl_expired",
                        trigger_ref=trigger_ref,
                        assessed_revision=ptr["assessed_revision"],
                        evidence_ttl_days=ttl_days,
                        locked_now=eval_now,
                    )
                    if execute and admitted.status == "pending":
                        results.append(self.execute_task(db, t_id, admitted.id))
                    break  # One task per project per timer tick covers all expired evidence

        return results

    def check_waiver_expiry_timers(
        self,
        db: Session,
        *,
        tenant_id: str | None = None,
        now: dt.datetime | None = None,
        execute: bool = True,
    ) -> list[OrchestratedAssessmentResult]:
        """Emit ``evidence_ttl_expired`` tasks for approved waivers whose ``expires_at <= now``."""
        if not self.settings.ppm_orchestrator_enabled:
            return []

        eval_now = _ensure_utc(now) or _utc_now()
        stmt = sa.select(ppm_waivers).where(
            ppm_waivers.c.approved_by.is_not(None),
            ppm_waivers.c.approved_at.is_not(None),
            ppm_waivers.c.expires_at <= eval_now,
        )
        if tenant_id is not None:
            stmt = stmt.where(ppm_waivers.c.tenant_id == tenant_id)

        results: list[OrchestratedAssessmentResult] = []
        for w in db.execute(stmt.order_by(ppm_waivers.c.expires_at.asc(), ppm_waivers.c.waiver_id.asc())).mappings().all():
            t_id = str(w["tenant_id"])
            p_id = str(w["project_id"])
            exp_dt = _ensure_utc(w["expires_at"])
            exp_iso = exp_dt.isoformat() if exp_dt is not None else str(w["expires_at"])
            trigger_ref = f"waiver:{w['waiver_id']}:{exp_iso}"
            admitted = self.admit_trigger(
                db,
                t_id,
                p_id,
                trigger="evidence_ttl_expired",
                trigger_ref=trigger_ref,
                locked_now=eval_now,
            )
            if execute and admitted.status == "pending":
                results.append(self.execute_task(db, t_id, admitted.id))

        return results

    def notify_model_recovered(
        self,
        db: Session,
        *,
        tenant_id: str | None = None,
        now: dt.datetime | None = None,
        execute: bool = True,
    ) -> list[OrchestratedAssessmentResult]:
        """Trigger ``model_recovered`` for current assessments with ``analysis_status='model_failed'``."""
        if not self.settings.ppm_orchestrator_enabled:
            return []

        eval_now = _ensure_utc(now) or _utc_now()
        stmt = (
            sa.select(
                project_current_assessment.c.tenant_id,
                project_current_assessment.c.project_id,
                project_current_assessment.c.assessment_id,
            )
            .select_from(
                project_current_assessment.join(
                    ppm_assessments,
                    sa.and_(
                        ppm_assessments.c.tenant_id == project_current_assessment.c.tenant_id,
                        ppm_assessments.c.id == project_current_assessment.c.assessment_id,
                    ),
                )
            )
            .where(ppm_assessments.c.analysis_status == "model_failed")
        )
        if tenant_id is not None:
            stmt = stmt.where(project_current_assessment.c.tenant_id == tenant_id)

        results: list[OrchestratedAssessmentResult] = []
        for row in db.execute(stmt).mappings().all():
            t_id = str(row["tenant_id"])
            p_id = str(row["project_id"])
            failed_asmt_id = str(row["assessment_id"])
            admitted = self.admit_trigger(
                db,
                t_id,
                p_id,
                trigger="model_recovered",
                trigger_ref=failed_asmt_id,
                locked_now=eval_now,
            )
            if execute and admitted.status == "pending":
                results.append(self.execute_task(db, t_id, admitted.id))

        return results

    def trigger_manual(
        self,
        db: Session,
        tenant_id: str,
        project_id: str,
        *,
        request_id: str | None = None,
        now: dt.datetime | None = None,
        execute: bool = True,
    ) -> OrchestratedAssessmentResult | AdmittedAssessmentTask:
        """Manually trigger a reassessment for ``(tenant_id, project_id)``."""
        t_ref = request_id or f"req-{uuid.uuid4().hex[:16]}"
        admitted = self.admit_trigger(
            db,
            tenant_id,
            project_id,
            trigger="manual",
            trigger_ref=t_ref,
            locked_now=now or _utc_now(),
        )
        if not execute:
            return admitted
        return self.execute_task(db, tenant_id, admitted.id)

    def recover_pending_tasks(
        self,
        db: Session,
        *,
        tenant_id: str | None = None,
    ) -> list[OrchestratedAssessmentResult]:
        """Recover and finish any ``pending`` or ``running`` tasks after an orchestrator crash."""
        stmt = sa.select(ppm_assessment_tasks).where(
            ppm_assessment_tasks.c.status.in_(["pending", "running"])
        )
        if tenant_id is not None:
            stmt = stmt.where(ppm_assessment_tasks.c.tenant_id == tenant_id)
        rows = db.execute(stmt.order_by(ppm_assessment_tasks.c.basis_seq.asc())).mappings().all()
        return [self.execute_task(db, str(r["tenant_id"]), str(r["id"])) for r in rows]


__all__ = [
    "DEFAULT_ORCHESTRATOR_AGENT_ID",
    "DEFAULT_ORCHESTRATOR_CONSUMER",
    "AdmittedAssessmentTask",
    "AssessmentOrchestrator",
    "OrchestratedAssessmentResult",
]
