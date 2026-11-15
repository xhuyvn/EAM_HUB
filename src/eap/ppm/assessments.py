"""Assessment creation, CAS pointer advancement, baseline approval, and fixture loaders (P2a-WP4).

Embeds ``ppm_reducer.assess_project`` without reimplementing reducer rules.
Persists immutable ``ppm_assessments`` rows, advances ``project_current_assessment``
via ``basis_seq`` compare-and-set (ADR-021 rev. 2, DG-2 §4), and emits
``ppm.assessment.created`` to ``outbox`` in the same transaction.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from eap.config import Settings, get_settings
from eap.contracts.digest import sha256_digest
from eap.contracts.models.ppm_output import PpmOutput
from eap.contracts.rules import check_ppm_output_rules
from eap.db import (
    outbox,
    ppm_assessments,
    ppm_baselines,
    ppm_basis_seq,
    ppm_evidence,
    ppm_notes,
    ppm_waivers,
    project_current_assessment,
    project_snapshots,
)
from eap.ppm.assessment_inputs import build_assessment_inputs
from ppm_reducer import assess_project
from ppm_reducer.supersession import resolve_supersession
from ppm_reducer.trust import is_evidence_ttl_expired, is_verifying_trust_class
from ppm_reducer.types import InputRejected

VALID_TRIGGERS = (
    "snapshot",
    "baseline_changed",
    "evidence_ttl_expired",
    "model_recovered",
    "manual",
)


class FixtureLoaderDisabledError(RuntimeError):
    """Raised when a fixture-only loader is invoked while real roots are enabled."""


class AssessmentNotFoundError(LookupError):
    """Raised when an assessment is not found for the authenticated tenant."""


class AssessmentProjectMismatchError(ValueError):
    """Raised when diffing two assessments belonging to different projects."""


class BaselineRevisionConflict(ValueError):
    """Raised when re-approving an approved baseline revision with different criteria (ISS-035)."""

    reason = "baseline_revision_conflict"


@dataclass
class AssessmentRecord:
    id: str
    tenant_id: str
    project_id: str
    basis_seq: int
    snapshot_seq: int
    project_snapshot_id: str | None
    baseline_revision: int | None
    assessed_revision: str | None
    inputs: dict[str, Any]
    inputs_hash: str
    output: dict[str, Any]
    findings: list[dict[str, Any]]
    trigger: str
    analysis_status: str
    previous_assessment_id: str | None
    created_at: dt.datetime
    is_current: bool
    deduplicated: bool = False


def _ensure_fixtures_only(settings: Settings | None = None) -> None:
    cfg = settings if settings is not None else get_settings()
    if cfg.eap_collector_allow_real_roots:
        raise FixtureLoaderDisabledError(
            "Fixture loaders are disabled when EAP_COLLECTOR_ALLOW_REAL_ROOTS=true"
        )


def allocate_basis_seq(
    conn: sa.Connection | Session,
    tenant_id: str,
    project_id: str,
) -> int:
    """Atomically allocate and return the next ``basis_seq`` for ``(tenant_id, project_id)``."""
    now = dt.datetime.now(dt.UTC)
    stmt = (
        pg_insert(ppm_basis_seq)
        .values(
            tenant_id=tenant_id,
            project_id=project_id,
            last_basis_seq=1,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "project_id"],
            set_={
                "last_basis_seq": ppm_basis_seq.c.last_basis_seq + 1,
                "updated_at": now,
            },
        )
        .returning(ppm_basis_seq.c.last_basis_seq)
    )
    seq = conn.execute(stmt).scalar_one()
    return int(seq)


def _row_to_record(row: Any, *, is_current: bool, deduplicated: bool = False) -> AssessmentRecord:
    return AssessmentRecord(
        id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        project_id=str(row["project_id"]),
        basis_seq=int(row["basis_seq"]),
        snapshot_seq=int(row["snapshot_seq"]),
        project_snapshot_id=row["project_snapshot_id"],
        baseline_revision=row["baseline_revision"],
        assessed_revision=row["assessed_revision"],
        inputs=dict(row["inputs"]),
        inputs_hash=str(row["inputs_hash"]),
        output=dict(row["output"]),
        findings=list(row["findings"] or []),
        trigger=str(row["trigger"]),
        analysis_status=str(row["analysis_status"]),
        previous_assessment_id=row["previous_assessment_id"],
        created_at=row["created_at"],
        is_current=is_current,
        deduplicated=deduplicated,
    )


def create_assessment(
    conn: sa.Connection | Session,
    tenant_id: str,
    project_id: str,
    trigger: str = "snapshot",
    *,
    basis_seq: int | None = None,
    assessment_id: str | None = None,
    analysis_status: str = "complete",
    now: dt.datetime | str | None = None,
    evidence_ttl_days: float = 7.0,
    project_snapshot_id: str | None = None,
    assessed_revision: str | None = None,
    previous_assessment_id: str | None = None,
    dependency_refs: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    fixture_id: str | None = None,
) -> AssessmentRecord:
    """Create and persist an immutable assessment, advancing the current pointer via CAS.

    If ``inputs_hash`` matches the current assessment's ``inputs_hash`` (and ``trigger`` is
    not ``evidence_ttl_expired`` and ``basis_seq`` was not pre-allocated), returns the
    existing assessment without creating a duplicate row or advancing ``basis_seq``.
    """
    if trigger not in VALID_TRIGGERS:
        raise ValueError(f"Invalid assessment trigger {trigger!r}; expected one of {VALID_TRIGGERS}")

    # 1. Build and lock inputs from DB state
    inputs_dict, inputs_hash, lock_meta = build_assessment_inputs(
        conn,
        tenant_id,
        project_id,
        basis_seq=basis_seq if basis_seq is not None else 1,
        now=now,
        evidence_ttl_days=evidence_ttl_days,
        project_snapshot_id=project_snapshot_id,
        assessed_revision=assessed_revision,
        previous_assessment_id=previous_assessment_id,
        dependency_refs=dependency_refs,
        context=context,
        fixture_id=fixture_id,
    )

    # 2. Deduplicate unchanged inputs against current assessment
    if basis_seq is None and trigger != "evidence_ttl_expired":
        curr_ptr = conn.execute(
            sa.select(project_current_assessment.c.assessment_id).where(
                project_current_assessment.c.tenant_id == tenant_id,
                project_current_assessment.c.project_id == project_id,
            )
        ).mappings().first()
        if curr_ptr is not None:
            curr_row = conn.execute(
                sa.select(ppm_assessments).where(
                    ppm_assessments.c.tenant_id == tenant_id,
                    ppm_assessments.c.id == str(curr_ptr["assessment_id"]),
                )
            ).mappings().first()
            if curr_row is not None and str(curr_row["inputs_hash"]) == inputs_hash:
                return _row_to_record(curr_row, is_current=True, deduplicated=True)

    # 3. Allocate basis_seq if not pre-allocated
    resolved_basis_seq = basis_seq if basis_seq is not None else allocate_basis_seq(conn, tenant_id, project_id)
    inputs_dict["basis_seq"] = resolved_basis_seq

    resolved_id = assessment_id or f"asmt-{uuid.uuid4().hex[:16]}"

    # 4. Run pure PPM-M0 reducer
    output_dict, findings_list = assess_project(
        inputs=inputs_dict,
        assessment_id=resolved_id,
        trigger=trigger,
        analysis_status=analysis_status,
        include_explanation=True,
    )

    # 5. Validate output against contracts/ppm-output.schema.json and semantic rules
    PpmOutput.model_validate(output_dict)
    check_ppm_output_rules(output_dict)

    # 6. Insert immutable row into ppm_assessments
    created_at = dt.datetime.now(dt.UTC)
    conn.execute(
        ppm_assessments.insert().values(
            id=resolved_id,
            tenant_id=tenant_id,
            project_id=project_id,
            basis_seq=resolved_basis_seq,
            snapshot_seq=lock_meta["snapshot_seq"],
            project_snapshot_id=lock_meta["project_snapshot_id"],
            baseline_revision=lock_meta["baseline_revision"],
            assessed_revision=lock_meta["assessed_revision"],
            inputs=inputs_dict,
            inputs_hash=inputs_hash,
            output=output_dict,
            findings=findings_list,
            trigger=trigger,
            analysis_status=analysis_status,
            previous_assessment_id=lock_meta["previous_assessment_id"],
            created_at=created_at,
        )
    )

    # 7. Compare-and-set project_current_assessment (ADR-021 rev. 2)
    cas_base = pg_insert(project_current_assessment).values(
        tenant_id=tenant_id,
        project_id=project_id,
        assessment_id=resolved_id,
        basis_seq=resolved_basis_seq,
        snapshot_seq=lock_meta["snapshot_seq"],
        baseline_revision=lock_meta["baseline_revision"],
        updated_at=created_at,
    )
    cas_stmt = (
        cas_base.on_conflict_do_update(
            index_elements=["tenant_id", "project_id"],
            set_={
                "assessment_id": cas_base.excluded.assessment_id,
                "basis_seq": cas_base.excluded.basis_seq,
                "snapshot_seq": cas_base.excluded.snapshot_seq,
                "baseline_revision": cas_base.excluded.baseline_revision,
                "updated_at": cas_base.excluded.updated_at,
            },
            where=project_current_assessment.c.basis_seq < cas_base.excluded.basis_seq,
        )
        .returning(project_current_assessment.c.assessment_id)
    )
    winner_id = conn.execute(cas_stmt).scalar_one_or_none()
    is_current = winner_id == resolved_id

    # 8. Emit ppm.assessment.created to outbox in the same transaction (ISS-032 pattern)
    conn.execute(
        outbox.insert().values(
            tenant_id=tenant_id,
            stream_id=f"assessment:{project_id}",
            event_id=f"evt-{uuid.uuid4().hex[:16]}",
            event_type="ppm.assessment.created",
            payload={
                "assessment_id": resolved_id,
                "project_id": project_id,
                "basis_seq": resolved_basis_seq,
                "snapshot_seq": lock_meta["snapshot_seq"],
                "project_snapshot_id": lock_meta["project_snapshot_id"],
                "baseline_revision": lock_meta["baseline_revision"],
                "assessed_revision": lock_meta["assessed_revision"],
                "trigger": trigger,
                "analysis_status": analysis_status,
                "verification": output_dict.get("verification"),
                "acceptance_readiness": output_dict.get("acceptance_readiness"),
                "is_current": is_current,
                "inputs_hash": inputs_hash,
                "created_at": created_at.isoformat(),
            },
            created_at=created_at,
        )
    )

    return AssessmentRecord(
        id=resolved_id,
        tenant_id=tenant_id,
        project_id=project_id,
        basis_seq=resolved_basis_seq,
        snapshot_seq=lock_meta["snapshot_seq"],
        project_snapshot_id=lock_meta["project_snapshot_id"],
        baseline_revision=lock_meta["baseline_revision"],
        assessed_revision=lock_meta["assessed_revision"],
        inputs=inputs_dict,
        inputs_hash=inputs_hash,
        output=output_dict,
        findings=findings_list,
        trigger=trigger,
        analysis_status=analysis_status,
        previous_assessment_id=lock_meta["previous_assessment_id"],
        created_at=created_at,
        is_current=is_current,
        deduplicated=False,
    )


def propose_baseline(
    conn: sa.Connection | Session,
    tenant_id: str,
    project_id: str,
    *,
    baseline_revision: int,
    criteria: list[dict[str, Any]],
    source: str,
) -> dict[str, Any]:
    """Store a proposed (draft) baseline. Never approves automatically."""
    bl_id = f"bl-{uuid.uuid4().hex[:16]}"
    now = dt.datetime.now(dt.UTC)
    stmt = (
        pg_insert(ppm_baselines)
        .values(
            id=bl_id,
            tenant_id=tenant_id,
            project_id=project_id,
            baseline_revision=baseline_revision,
            status="draft",
            criteria=criteria,
            source=source,
            approved_by=None,
            approved_at=None,
            created_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "project_id", "baseline_revision"]
        )
    )
    conn.execute(stmt)
    row = conn.execute(
        sa.select(ppm_baselines).where(
            ppm_baselines.c.tenant_id == tenant_id,
            ppm_baselines.c.project_id == project_id,
            ppm_baselines.c.baseline_revision == baseline_revision,
        )
    ).mappings().one()
    return dict(row)


def approve_baseline(
    conn: sa.Connection | Session,
    tenant_id: str,
    project_id: str,
    *,
    baseline_revision: int,
    criteria: list[dict[str, Any]],
    approved_by: str,
    source: str,
) -> dict[str, Any]:
    """Explicitly approve a baseline revision (draft -> approved; approved is immutable per ISS-035).

    If ``(tenant_id, project_id, baseline_revision)`` is already ``approved``:
    - identical criteria -> no-op returning the existing row;
    - different criteria -> raises ``BaselineRevisionConflict``.
    """
    existing = conn.execute(
        sa.select(ppm_baselines)
        .where(
            ppm_baselines.c.tenant_id == tenant_id,
            ppm_baselines.c.project_id == project_id,
            ppm_baselines.c.baseline_revision == baseline_revision,
        )
        .with_for_update()
    ).mappings().first()

    if existing is None:
        bl_id = f"bl-{uuid.uuid4().hex[:16]}"
        now = dt.datetime.now(dt.UTC)
        inserted = conn.execute(
            pg_insert(ppm_baselines)
            .values(
                id=bl_id,
                tenant_id=tenant_id,
                project_id=project_id,
                baseline_revision=baseline_revision,
                status="approved",
                criteria=criteria,
                source=source,
                approved_by=approved_by,
                approved_at=now,
                created_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "project_id", "baseline_revision"]
            )
            .returning(ppm_baselines)
        ).mappings().first()
        if inserted is not None:
            return dict(inserted)
        existing = conn.execute(
            sa.select(ppm_baselines)
            .where(
                ppm_baselines.c.tenant_id == tenant_id,
                ppm_baselines.c.project_id == project_id,
                ppm_baselines.c.baseline_revision == baseline_revision,
            )
            .with_for_update()
        ).mappings().one()

    if str(existing["status"]) == "approved":
        if sha256_digest(list(existing["criteria"] or [])) == sha256_digest(criteria):
            return dict(existing)
        raise BaselineRevisionConflict(
            f"baseline_revision_conflict: baseline revision {baseline_revision} for project "
            f"'{project_id}' is already approved with different criteria; approve a new baseline revision"
        )

    now = dt.datetime.now(dt.UTC)
    updated = conn.execute(
        ppm_baselines.update()
        .where(
            ppm_baselines.c.tenant_id == tenant_id,
            ppm_baselines.c.project_id == project_id,
            ppm_baselines.c.baseline_revision == baseline_revision,
        )
        .values(
            status="approved",
            criteria=criteria,
            source=source,
            approved_by=approved_by,
            approved_at=now,
        )
        .returning(ppm_baselines)
    ).mappings().one()
    return dict(updated)


def load_fixture_evidence(
    conn: sa.Connection | Session,
    tenant_id: str,
    project_id: str,
    evidence_items: list[dict[str, Any]],
    *,
    producer: str = "simulated-verifier",
    simulated: bool = True,
    settings: Settings | None = None,
) -> int:
    """Load fixture evidence rows into ``ppm_evidence`` (fixtures-only guard)."""
    _ensure_fixtures_only(settings)
    now = dt.datetime.now(dt.UTC)
    inserted = 0
    for item in evidence_items:
        ev_id = str(item["evidence_id"])
        obs_raw = item["observed_at"]
        obs_dt = (
            dt.datetime.fromisoformat(obs_raw)
            if isinstance(obs_raw, str)
            else obs_raw
        )
        out_hash = str(item.get("output_hash") or sha256_digest(item))
        stmt = (
            pg_insert(ppm_evidence)
            .values(
                id=f"ev-{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                project_id=project_id,
                evidence_id=ev_id,
                trust_class=str(item["trust_class"]),
                producer=str(item.get("producer") or producer),
                revision=item.get("revision"),
                test_id=item.get("test_id"),
                covered_test_ids=list(item.get("covered_test_ids") or []),
                outcome=str(item["result"]),
                collected_at=obs_dt,
                output_hash=out_hash,
                artifact_path=item.get("artifact_path"),
                simulated=bool(item.get("simulated", simulated)),
                exit_status=item.get("exit_status"),
                duration_ms=item.get("duration_ms"),
                network_isolation=item.get("network_isolation"),
                reason=item.get("reason"),
                created_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "project_id", "evidence_id"]
            )
            .returning(ppm_evidence.c.id)
        )
        res = conn.execute(stmt).scalar_one_or_none()
        if res is not None:
            inserted += 1
    return inserted


def load_fixture_waivers(
    conn: sa.Connection | Session,
    tenant_id: str,
    project_id: str,
    waiver_items: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
) -> int:
    """Load fixture waiver rows into ``ppm_waivers`` (fixtures-only guard)."""
    _ensure_fixtures_only(settings)
    now = dt.datetime.now(dt.UTC)
    inserted = 0
    for item in waiver_items:
        app_raw = item.get("approved_at")
        app_dt = (
            dt.datetime.fromisoformat(app_raw)
            if isinstance(app_raw, str)
            else app_raw
        )
        exp_raw = item["expires_at"]
        exp_dt = (
            dt.datetime.fromisoformat(exp_raw)
            if isinstance(exp_raw, str)
            else exp_raw
        )
        stmt = (
            pg_insert(ppm_waivers)
            .values(
                id=f"wav-{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                project_id=project_id,
                waiver_id=str(item["waiver_id"]),
                criterion_id=str(item["criterion_id"]),
                scope=str(item.get("scope") or "criterion"),
                rationale=str(item.get("rationale") or ""),
                approved_by=item.get("approved_by"),
                approved_at=app_dt,
                expires_at=exp_dt,
                created_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "project_id", "waiver_id"]
            )
            .returning(ppm_waivers.c.id)
        )
        res = conn.execute(stmt).scalar_one_or_none()
        if res is not None:
            inserted += 1
    return inserted


def get_assessment_detail(
    conn: sa.Connection | Session,
    tenant_id: str,
    assessment_id: str,
) -> dict[str, Any]:
    """Fetch an assessment with per-criterion Reported/Verified explanation columns."""
    row = conn.execute(
        sa.select(ppm_assessments).where(
            ppm_assessments.c.tenant_id == tenant_id,
            ppm_assessments.c.id == assessment_id,
        )
    ).mappings().first()
    if row is None:
        raise AssessmentNotFoundError(f"Assessment '{assessment_id}' not found")

    project_id = str(row["project_id"])
    curr_id = conn.execute(
        sa.select(project_current_assessment.c.assessment_id).where(
            project_current_assessment.c.tenant_id == tenant_id,
            project_current_assessment.c.project_id == project_id,
        )
    ).scalar_one_or_none()
    is_current = curr_id == assessment_id

    # Index evidence metadata (including producer, simulated, and verifier fields from ppm_evidence)
    ev_db_rows = conn.execute(
        sa.select(
            ppm_evidence.c.evidence_id,
            ppm_evidence.c.producer,
            ppm_evidence.c.simulated,
            ppm_evidence.c.output_hash,
            ppm_evidence.c.exit_status,
            ppm_evidence.c.duration_ms,
            ppm_evidence.c.network_isolation,
            ppm_evidence.c.reason,
        ).where(
            ppm_evidence.c.tenant_id == tenant_id,
            ppm_evidence.c.project_id == project_id,
        )
    ).mappings().all()
    ev_meta_by_id: dict[str, dict[str, Any]] = {
        str(e["evidence_id"]): {
            "producer": str(e["producer"]),
            "simulated": bool(e["simulated"]),
            "output_hash": e["output_hash"],
            "exit_status": e["exit_status"],
            "duration_ms": e["duration_ms"],
            "network_isolation": e["network_isolation"],
            "reason": e["reason"],
        }
        for e in ev_db_rows
    }

    inputs_dict = dict(row["inputs"] or {})
    output_dict = dict(row["output"] or {})

    ev_by_id: dict[str, dict[str, Any]] = {}
    for ev in inputs_dict.get("evidence", []):
        if isinstance(ev, dict) and ev.get("evidence_id"):
            eid = str(ev["evidence_id"])
            meta = ev_meta_by_id.get(eid, {})
            ev_by_id[eid] = {
                "evidence_id": eid,
                "trust_class": ev.get("trust_class"),
                "producer": meta.get("producer", "unknown"),
                "revision": ev.get("revision"),
                "outcome": ev.get("result"),
                "observed_at": ev.get("observed_at"),
                "test_id": ev.get("test_id"),
                "covered_test_ids": list(ev.get("covered_test_ids") or []),
                "artifact_path": ev.get("artifact_path"),
                "simulated": meta.get("simulated", False),
                "output_hash": meta.get("output_hash"),
                "exit_status": meta.get("exit_status"),
                "duration_ms": meta.get("duration_ms"),
                "network_isolation": meta.get("network_isolation"),
                "reason": meta.get("reason"),
            }

    claims_list = [c for c in inputs_dict.get("claims", []) if isinstance(c, dict)]
    claims_by_id = {str(c.get("claim_id")): c for c in claims_list if c.get("claim_id")}
    try:
        active_claims = resolve_supersession(claims_list)
    except InputRejected:
        active_claims = claims_list

    baseline_dict = inputs_dict.get("baseline")
    baseline_criteria_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(baseline_dict, dict):
        for bc in baseline_dict.get("criteria") or []:
            if isinstance(bc, dict) and bc.get("criterion_id"):
                baseline_criteria_by_id[str(bc["criterion_id"])] = bc

    assessed_rev = inputs_dict.get("assessed_revision") or output_dict.get("assessed_revision")
    now_val = inputs_dict.get("now")
    ttl_days = float(inputs_dict.get("evidence_ttl_days", 7.0))
    raw_evidence_list = [ev for ev in inputs_dict.get("evidence", []) if isinstance(ev, dict)]

    explanation: dict[str, dict[str, Any]] = {}
    for crit in output_dict.get("criteria", []):
        if not isinstance(crit, dict) or not crit.get("criterion_id"):
            continue
        cid = str(crit["criterion_id"])
        rep_refs = [str(r) for r in (crit.get("reported_claim_refs") or [])]
        reported_items: list[dict[str, Any]] = []
        seen_cids: set[str] = set()
        for ref in rep_refs:
            if ref in claims_by_id and ref not in seen_cids:
                c = claims_by_id[ref]
                seen_cids.add(ref)
                reported_items.append({
                    "claim_id": ref,
                    "source_file": c.get("source_file"),
                    "reported_status": c.get("reported_status"),
                    "evidence_path": c.get("evidence_path"),
                    "summary": c.get("summary"),
                    "recorded_at": c.get("recorded_at"),
                })
        for c in claims_list:
            c_id = str(c.get("claim_id") or "")
            if c_id and c_id not in seen_cids and cid in (c.get("criterion_ids") or []):
                seen_cids.add(c_id)
                reported_items.append({
                    "claim_id": c_id,
                    "source_file": c.get("source_file"),
                    "reported_status": c.get("reported_status"),
                    "evidence_path": c.get("evidence_path"),
                    "summary": c.get("summary"),
                    "recorded_at": c.get("recorded_at"),
                })

        supp_ids = [str(eid) for eid in (crit.get("supporting_evidence_refs") or [])]
        refut_ids = [str(eid) for eid in (crit.get("refuting_evidence_refs") or [])]
        included_ids = set(supp_ids) | set(refut_ids)

        supp = [ev_by_id[eid] for eid in supp_ids if eid in ev_by_id]
        refut = [ev_by_id[eid] for eid in refut_ids if eid in ev_by_id]

        # ISS-036: derive excluded_evidence[] from locked inputs + reducer output
        b_crit = baseline_criteria_by_id.get(cid)
        if b_crit is not None:
            v_refs = b_crit.get("verification_refs") or []
            mapped_test_ids = [
                str(r["ref"])
                for r in v_refs
                if isinstance(r, dict) and r.get("kind") in ("test_id", "probe_id")
            ]
            mapped_artifact_paths = [
                str(r["ref"])
                for r in v_refs
                if isinstance(r, dict) and r.get("kind") == "artifact_path"
            ]
            mapped_set = (
                set(mapped_test_ids)
                | set(mapped_artifact_paths)
                | {str(r["ref"]) for r in v_refs if isinstance(r, dict) and r.get("ref")}
            )
        else:
            mapped_test_ids = []
            mapped_artifact_paths = []
            mapped_set = set()

        matching_claims = [
            cl
            for cl in active_claims
            if cid in (cl.get("criterion_ids") or [])
            or (
                not cl.get("criterion_ids")
                and any(tid in mapped_test_ids for tid in (cl.get("test_ids") or []))
            )
        ]
        unmapped_claim_test_ids = {
            str(tid)
            for cl in matching_claims
            for tid in (cl.get("test_ids") or [])
            if str(tid) not in mapped_set
        }

        excl: list[dict[str, Any]] = []
        for raw_ev in raw_evidence_list:
            eid = str(raw_ev.get("evidence_id") or "")
            if not eid or eid in included_ids or eid not in ev_by_id:
                continue

            direct_match = (
                raw_ev.get("test_id") in mapped_test_ids
                or any(tid in mapped_test_ids for tid in (raw_ev.get("covered_test_ids") or []))
                or raw_ev.get("artifact_path") in mapped_artifact_paths
                or (
                    bool(raw_ev.get("artifact_path"))
                    and any(
                        cl.get("evidence_path") == raw_ev.get("artifact_path")
                        for cl in matching_claims
                    )
                )
                or cid in (raw_ev.get("criterion_ids") or [])
            )
            unmapped_claim_match = (
                not direct_match
                and bool(unmapped_claim_test_ids)
                and (
                    raw_ev.get("test_id") in unmapped_claim_test_ids
                    or any(
                        tid in unmapped_claim_test_ids
                        for tid in (raw_ev.get("covered_test_ids") or [])
                    )
                )
            )
            if not direct_match and not unmapped_claim_match:
                continue

            if b_crit is None:
                excl_reason = "outside_baseline"
            elif unmapped_claim_match:
                excl_reason = "test_not_mapped"
            elif assessed_rev is not None and raw_ev.get("revision") != assessed_rev:
                excl_reason = "revision_mismatch"
            elif (
                now_val is not None
                and raw_ev.get("observed_at") is not None
                and is_evidence_ttl_expired(raw_ev["observed_at"], now_val, ttl_days)
            ):
                excl_reason = "ttl_expired"
            elif not is_verifying_trust_class(raw_ev.get("trust_class")):
                excl_reason = "trust_class_insufficient"
            elif raw_ev.get("result") in ("inconclusive", "not_run"):
                excl_reason = str(ev_meta_by_id.get(eid, {}).get("reason") or raw_ev.get("result"))
            else:
                excl_reason = "unknown"

            excl.append({**ev_by_id[eid], "reason": excl_reason})

        explanation[cid] = {
            "reported": reported_items,
            "verified": {
                "status": crit.get("status"),
                "reason_code": crit.get("reason_code"),
                "baseline_revision": output_dict.get("baseline_revision"),
                "assessed_revision": output_dict.get("assessed_revision"),
                "evidence_revision": crit.get("evidence_revision"),
                "evidence_trust": crit.get("evidence_trust"),
                "covered_test_ids": list(crit.get("covered_test_ids") or []),
                "supporting_evidence": supp,
                "refuting_evidence": refut,
                "excluded_evidence": excl,
                "explanation": crit.get("explanation"),
            },
        }

    last_scan_dt = conn.execute(
        sa.select(sa.func.max(project_snapshots.c.created_at)).where(
            project_snapshots.c.tenant_id == tenant_id,
            project_snapshots.c.project_id == project_id,
            project_snapshots.c.status == "captured",
        )
    ).scalar_one_or_none()
    last_scan_iso = (
        last_scan_dt.isoformat()
        if isinstance(last_scan_dt, dt.datetime)
        else (str(last_scan_dt) if last_scan_dt is not None else None)
    )

    note_rows = conn.execute(
        sa.select(
            ppm_notes.c.id,
            ppm_notes.c.artifact_path,
            ppm_notes.c.kind,
            ppm_notes.c.body,
        )
        .where(
            ppm_notes.c.tenant_id == tenant_id,
            ppm_notes.c.project_id == project_id,
        )
        .order_by(ppm_notes.c.ingested_at.asc(), ppm_notes.c.artifact_path.asc())
    ).mappings().all()
    notes_list = [
        {
            "id": str(n["id"]),
            "artifact_path": str(n["artifact_path"]),
            "kind": str(n["kind"]),
            "body": str(n["body"]),
        }
        for n in note_rows
    ]

    created_at = row["created_at"]
    created_iso = created_at.isoformat() if isinstance(created_at, dt.datetime) else str(created_at)

    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "project_id": project_id,
        "basis_seq": int(row["basis_seq"]),
        "snapshot_seq": int(row["snapshot_seq"]),
        "project_snapshot_id": row["project_snapshot_id"],
        "baseline_revision": row["baseline_revision"],
        "assessed_revision": row["assessed_revision"],
        "trigger": str(row["trigger"]),
        "analysis_status": str(row["analysis_status"]),
        "previous_assessment_id": row["previous_assessment_id"],
        "inputs_hash": str(row["inputs_hash"]),
        "created_at": created_iso,
        "last_successful_scan_at": last_scan_iso,
        "is_current": is_current,
        "output": output_dict,
        "findings": list(row["findings"] or []),
        "notes": notes_list,
        "explanation": explanation,
    }


def diff_assessments(
    conn: sa.Connection | Session,
    tenant_id: str,
    assessment_id: str,
    against_id: str,
) -> dict[str, Any]:
    """Compute per-criterion status diff between ``against_id`` (from) and ``assessment_id`` (to)."""
    to_row = conn.execute(
        sa.select(ppm_assessments).where(
            ppm_assessments.c.tenant_id == tenant_id,
            ppm_assessments.c.id == assessment_id,
        )
    ).mappings().first()
    if to_row is None:
        raise AssessmentNotFoundError(f"Assessment '{assessment_id}' not found")

    from_row = conn.execute(
        sa.select(ppm_assessments).where(
            ppm_assessments.c.tenant_id == tenant_id,
            ppm_assessments.c.id == against_id,
        )
    ).mappings().first()
    if from_row is None:
        raise AssessmentNotFoundError(f"Assessment '{against_id}' not found")

    if str(to_row["project_id"]) != str(from_row["project_id"]):
        raise AssessmentProjectMismatchError(
            "Assessments belong to different projects and cannot be diffed"
        )

    from_out = dict(from_row["output"] or {})
    to_out = dict(to_row["output"] or {})
    from_crit = {
        str(c["criterion_id"]): c
        for c in from_out.get("criteria", [])
        if isinstance(c, dict) and c.get("criterion_id")
    }
    to_crit = {
        str(c["criterion_id"]): c
        for c in to_out.get("criteria", [])
        if isinstance(c, dict) and c.get("criterion_id")
    }

    changes: list[dict[str, Any]] = []
    for cid in sorted(set(from_crit) | set(to_crit)):
        fc = from_crit.get(cid)
        tc = to_crit.get(cid)
        f_status = fc.get("status") if fc else None
        t_status = tc.get("status") if tc else None
        f_reason = fc.get("reason_code") if fc else None
        t_reason = tc.get("reason_code") if tc else None
        reasons: list[str] = []
        if f_status != t_status:
            reasons.append(f"status: {f_status} -> {t_status}")
        if t_reason:
            reasons.append(f"reason_code: {t_reason}")
        if tc and tc.get("explanation"):
            reasons.append(str(tc["explanation"]))
        changes.append({
            "criterion_id": cid,
            "from_status": f_status,
            "to_status": t_status,
            "changed": f_status != t_status,
            "from_reason_code": f_reason,
            "to_reason_code": t_reason,
            "from_evidence_revision": fc.get("evidence_revision") if fc else None,
            "to_evidence_revision": tc.get("evidence_revision") if tc else None,
            "reasons": reasons,
        })

    return {
        "project_id": str(to_row["project_id"]),
        "from_assessment_id": against_id,
        "to_assessment_id": assessment_id,
        "from_basis_seq": int(from_row["basis_seq"]),
        "to_basis_seq": int(to_row["basis_seq"]),
        "from_assessed_revision": from_row["assessed_revision"],
        "to_assessed_revision": to_row["assessed_revision"],
        "changes": changes,
    }


def list_project_assessments(
    conn: sa.Connection | Session,
    tenant_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    """List assessments for ``(tenant_id, project_id)`` ordered by ``basis_seq DESC``."""
    curr_id = conn.execute(
        sa.select(project_current_assessment.c.assessment_id).where(
            project_current_assessment.c.tenant_id == tenant_id,
            project_current_assessment.c.project_id == project_id,
        )
    ).scalar_one_or_none()

    rows = conn.execute(
        sa.select(
            ppm_assessments.c.id,
            ppm_assessments.c.basis_seq,
            ppm_assessments.c.snapshot_seq,
            ppm_assessments.c.baseline_revision,
            ppm_assessments.c.assessed_revision,
            ppm_assessments.c.trigger,
            ppm_assessments.c.analysis_status,
            ppm_assessments.c.output,
            ppm_assessments.c.created_at,
        )
        .where(
            ppm_assessments.c.tenant_id == tenant_id,
            ppm_assessments.c.project_id == project_id,
        )
        .order_by(ppm_assessments.c.basis_seq.desc())
    ).mappings().all()

    items: list[dict[str, Any]] = []
    for r in rows:
        out = dict(r["output"] or {})
        created_at = r["created_at"]
        items.append({
            "id": str(r["id"]),
            "basis_seq": int(r["basis_seq"]),
            "snapshot_seq": int(r["snapshot_seq"]),
            "baseline_revision": r["baseline_revision"],
            "assessed_revision": r["assessed_revision"],
            "trigger": str(r["trigger"]),
            "analysis_status": str(r["analysis_status"]),
            "verification": out.get("verification"),
            "acceptance_readiness": out.get("acceptance_readiness"),
            "created_at": created_at.isoformat() if isinstance(created_at, dt.datetime) else str(created_at),
            "is_current": str(r["id"]) == curr_id,
        })
    return items


__all__ = [
    "VALID_TRIGGERS",
    "AssessmentNotFoundError",
    "AssessmentProjectMismatchError",
    "AssessmentRecord",
    "BaselineRevisionConflict",
    "FixtureLoaderDisabledError",
    "allocate_basis_seq",
    "approve_baseline",
    "create_assessment",
    "diff_assessments",
    "get_assessment_detail",
    "list_project_assessments",
    "load_fixture_evidence",
    "load_fixture_waivers",
    "propose_baseline",
]
