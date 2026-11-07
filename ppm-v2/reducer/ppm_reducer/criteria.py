"""Pure deterministic criterion reducer for PPM v2.

Rule: Pure domain logic only. Zero I/O, zero network, zero wall-clock time.
Inputs in, results out; stable ordering by criterion_id.
"""

import datetime as dt
from typing import Any

from .supersession import resolve_supersession
from .trust import is_evidence_ttl_expired, is_verifying_trust_class, parse_datetime
from .types import (
    CriterionResult,
    CriterionStatus,
    ReasonCode,
    ReportedStatus,
    TrustClass,
)

# Status precedence for conflicting claims: claimed_done > in_progress > blocked > none
_STATUS_PRIORITY: dict[str, int] = {
    ReportedStatus.CLAIMED_DONE: 4,
    ReportedStatus.CLAIMED_IN_PROGRESS: 3,
    ReportedStatus.CLAIMED_BLOCKED: 2,
    ReportedStatus.NONE: 1,
}


def assess_criteria(
    baseline: dict[str, Any] | None,
    claims: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    waivers: list[dict[str, Any]],
    assessed_revision: str | None,
    now: str | dt.datetime,
    evidence_ttl_days: float = 7.0,
) -> list[CriterionResult]:
    """Pure, deterministic assessment of deliverable criteria.

    Args:
        baseline: Approved baseline containing criteria and verification refs (None if draft-only).
        claims: Parsed claims from repository updates and notes.
        evidence: Verification and test execution records.
        waivers: Approved waivers from domain storage.
        assessed_revision: Target commit or snapshot revision.
        now: Evaluation timestamp for TTL and waiver expiry calculation.
        evidence_ttl_days: Evidence validity lifetime threshold in days (default: 7.0).

    Returns:
        Deterministic list of CriterionResult stably ordered by criterion_id.

    Raises:
        InputRejected: If claims contain cyclic or dangling supersession chains.
    """
    # 1. Resolve supersession first (raises InputRejected on cycle or dangling)
    active_claims = resolve_supersession(claims)

    # 2. If no baseline exists (e.g. s24-draft-only), criteria evaluation returns empty list
    if not baseline or not baseline.get("criteria"):
        return []

    now_dt = parse_datetime(now)

    # 3. Index unexpired approved waivers by criterion_id
    active_waivers: dict[str, dict[str, Any]] = {}
    for waiver in waivers:
        cid = waiver.get("criterion_id")
        if not cid:
            continue
        exp_dt = parse_datetime(waiver["expires_at"])
        if now_dt < exp_dt:
            active_waivers[cid] = waiver

    results: list[CriterionResult] = []

    for criterion in baseline["criteria"]:
        cid = str(criterion["criterion_id"])
        deliv_id = str(criterion["deliverable_id"])
        owner_proj_id = str(criterion["owner_project_id"])
        weight = float(criterion["weight"])
        required = bool(criterion.get("required", True))
        prev_status = criterion.get("previous_status")

        v_refs = criterion.get("verification_refs", [])
        mapped_test_ids = [
            str(r["ref"]) for r in v_refs if r.get("kind") in ("test_id", "probe_id")
        ]
        mapped_artifact_paths = [
            str(r["ref"]) for r in v_refs if r.get("kind") == "artifact_path"
        ]
        mapped_set = set(mapped_test_ids) | set(mapped_artifact_paths) | {
            str(r["ref"]) for r in v_refs
        }
        primary_test_id = (
            mapped_test_ids[0]
            if mapped_test_ids
            else (v_refs[0]["ref"] if v_refs else "mapped tests")
        )

        # 4. Map active claims to this criterion
        matching_claims = [
            cl
            for cl in active_claims
            if cid in cl.get("criterion_ids", [])
            or (
                not cl.get("criterion_ids")
                and any(tid in mapped_test_ids for tid in cl.get("test_ids", []))
            )
        ]

        # Extract and stably sort claim identifiers and artifact paths
        claim_id_set: set[str] = set()
        artifact_set: set[str] = set()
        for cl in matching_claims:
            cl_id = cl.get("claim_id")
            if cl_id:
                claim_id_set.add(str(cl_id))
            ev_path = cl.get("evidence_path")
            if ev_path:
                artifact_set.add(f"artifact:repo/{ev_path}")

        rep_claim_refs: list[str] = sorted(claim_id_set) + sorted(artifact_set)

        # Reported status and conflict detection
        conflicting_claim_refs: list[str] = []
        if matching_claims:
            statuses = [cl.get("reported_status", ReportedStatus.NONE) for cl in matching_claims]
            unique_statuses = set(statuses)
            if len(unique_statuses) > 1:
                conflicting_claim_refs = sorted(
                    [str(cl["claim_id"]) for cl in matching_claims if "claim_id" in cl]
                )
            reported_status = max(statuses, key=lambda s: _STATUS_PRIORITY.get(s, 0))
        else:
            reported_status = ReportedStatus.NONE

        # 5. Map evidence to this criterion
        matching_evidence = [
            ev
            for ev in evidence
            if ev.get("test_id") in mapped_test_ids
            or any(tid in mapped_test_ids for tid in ev.get("covered_test_ids", []))
            or ev.get("artifact_path") in mapped_artifact_paths
            or (
                ev.get("artifact_path")
                and any(cl.get("evidence_path") == ev.get("artifact_path") for cl in matching_claims)
            )
            or cid in ev.get("criterion_ids", [])
        ]

        # Check for unmapped passing evidence from claims
        has_unmapped_pass = False
        if not matching_evidence and matching_claims:
            for cl in matching_claims:
                for tid in cl.get("test_ids", []):
                    if tid not in mapped_set:
                        unmapped_ev = [
                            ev
                            for ev in evidence
                            if (ev.get("test_id") == tid or tid in ev.get("covered_test_ids", []))
                            and ev.get("result") == "passed"
                        ]
                        if unmapped_ev:
                            has_unmapped_pass = True
                            break

        # 6. Apply deterministic decision table
        failing_at_assessed = [
            ev
            for ev in matching_evidence
            if ev.get("revision") == assessed_revision and ev.get("result") == "failed"
        ]

        if failing_at_assessed:
            # Rule: FAIL in ANY trust class at assessed_revision -> contradicted
            ref_ev = failing_at_assessed[0]
            status = CriterionStatus.CONTRADICTED
            reason_code = ReasonCode.EVIDENCE_REFUTED
            supporting_evidence_refs: list[str] = []
            refuting_evidence_refs = sorted(
                [str(ev["evidence_id"]) for ev in failing_at_assessed if ev.get("evidence_id")]
            )
            missing_evidence: list[str] = []
            evidence_revision = assessed_revision
            covered_test_ids = sorted(
                ref_ev.get("covered_test_ids")
                or ([ref_ev["test_id"]] if ref_ev.get("test_id") else [])
            )
            evidence_trust = ref_ev.get("trust_class", TrustClass.NONE)
            waiver_ref = None
            explanation = (
                f"Active refutation: verifier/test failure observed at revision {assessed_revision}."
            )

        elif cid in active_waivers:
            # Rule: Approved unexpired waiver -> waived (excluded from denominator)
            status = CriterionStatus.WAIVED
            reason_code = ReasonCode.WAIVER_APPROVED
            supporting_evidence_refs = []
            refuting_evidence_refs = []
            missing_evidence = []
            reported_status = ReportedStatus.NONE
            rep_claim_refs = []
            evidence_revision = None
            covered_test_ids = []
            evidence_trust = TrustClass.NONE
            waiver_ref = active_waivers[cid]["waiver_id"]
            explanation = f"Criterion waived under approved waiver {waiver_ref}."

        else:
            waiver_ref = None
            current_passes = [
                ev
                for ev in matching_evidence
                if ev.get("revision") == assessed_revision and ev.get("result") == "passed"
            ]

            if current_passes:
                # Prefer verifier_observed or ci_attested over reported_artifact if both present
                sorted_current = sorted(
                    current_passes,
                    key=lambda ev: (
                        1 if is_verifying_trust_class(ev.get("trust_class")) else 0,
                        parse_datetime(ev["observed_at"]),
                    ),
                    reverse=True,
                )
                latest_pass = sorted_current[0]
                is_expired = is_evidence_ttl_expired(
                    latest_pass["observed_at"], now_dt, evidence_ttl_days
                )
                if is_expired:
                    # Rule: Evidence at current revision but older than TTL -> stale
                    status = CriterionStatus.STALE
                    reason_code = ReasonCode.EVIDENCE_STALE_REVISION
                    supporting_evidence_refs = []
                    refuting_evidence_refs = []
                    missing_evidence = [
                        f"Fresh verifier execution of {primary_test_id} within {int(evidence_ttl_days)}-day TTL"
                    ]
                    evidence_revision = assessed_revision
                    covered_test_ids = sorted(
                        latest_pass.get("covered_test_ids")
                        or ([latest_pass["test_id"]] if latest_pass.get("test_id") else [])
                    )
                    evidence_trust = latest_pass.get("trust_class", TrustClass.NONE)
                    explanation = (
                        f"Evidence at current revision {assessed_revision} exceeds {evidence_ttl_days}-day TTL."
                    )
                else:
                    tc = latest_pass.get("trust_class")
                    if is_verifying_trust_class(tc):
                        # Rule: Passing verifier_observed or ci_attested at assessed_revision -> verified
                        status = CriterionStatus.VERIFIED
                        reason_code = ReasonCode.EVIDENCE_CURRENT_PASS
                        supporting_evidence_refs = sorted(
                            [
                                str(ev["evidence_id"])
                                for ev in current_passes
                                if is_verifying_trust_class(ev.get("trust_class"))
                                and ev.get("evidence_id")
                            ]
                        )
                        refuting_evidence_refs = []
                        missing_evidence = []
                        evidence_revision = assessed_revision
                        covered_test_ids = sorted(
                            latest_pass.get("covered_test_ids")
                            or ([latest_pass["test_id"]] if latest_pass.get("test_id") else [])
                        )
                        evidence_trust = tc
                        explanation = f"Verified by {tc} evidence at revision {assessed_revision}."
                    elif tc == TrustClass.REPORTED_ARTIFACT:
                        # Rule: Reported artifact in repo -> unverified/reported_artifact_only
                        status = CriterionStatus.UNVERIFIED
                        reason_code = ReasonCode.REPORTED_ARTIFACT_ONLY
                        supporting_evidence_refs = []
                        refuting_evidence_refs = []
                        missing_evidence = [
                            f"Independent verifier execution or CI attestation of {primary_test_id} at {assessed_revision}"
                        ]
                        evidence_revision = None
                        covered_test_ids = []
                        evidence_trust = TrustClass.REPORTED_ARTIFACT
                        explanation = (
                            "In-repo test report authored by producer cannot verify deliverable criterion."
                        )
                    else:
                        status = CriterionStatus.UNVERIFIED
                        reason_code = ReasonCode.EVIDENCE_MISSING
                        supporting_evidence_refs = []
                        refuting_evidence_refs = []
                        missing_evidence = [
                            f"Verifier execution of {primary_test_id} at {assessed_revision}"
                        ]
                        evidence_revision = None
                        covered_test_ids = []
                        evidence_trust = TrustClass.NONE
                        explanation = "Evidence trust class insufficient to verify criterion."

            else:
                older_passes = [
                    ev
                    for ev in matching_evidence
                    if ev.get("revision") != assessed_revision and ev.get("result") == "passed"
                ]

                if older_passes:
                    # Rule: Evidence at older revision -> stale
                    sorted_older = sorted(
                        older_passes,
                        key=lambda ev: parse_datetime(ev["observed_at"]),
                        reverse=True,
                    )
                    old_ev = sorted_older[0]
                    status = CriterionStatus.STALE
                    reason_code = ReasonCode.EVIDENCE_STALE_REVISION
                    supporting_evidence_refs = []
                    refuting_evidence_refs = []
                    if any("renamed" in cl.get("summary", "").lower() for cl in matching_claims):
                        missing_evidence = [
                            f"Mapped test {primary_test_id} not resolved at {assessed_revision}"
                        ]
                    else:
                        missing_evidence = [
                            f"Verifier execution of {primary_test_id} at {assessed_revision}"
                        ]
                    evidence_revision = old_ev.get("revision")
                    covered_test_ids = sorted(
                        old_ev.get("covered_test_ids")
                        or ([old_ev["test_id"]] if old_ev.get("test_id") else [])
                    )
                    evidence_trust = old_ev.get("trust_class", TrustClass.NONE)
                    explanation = (
                        f"Evidence passed at older revision {evidence_revision} is stale at {assessed_revision}."
                    )

                elif has_unmapped_pass:
                    # Rule: Unmapped test pass -> coverage_not_mapped
                    status = CriterionStatus.UNVERIFIED
                    reason_code = ReasonCode.COVERAGE_NOT_MAPPED
                    supporting_evidence_refs = []
                    refuting_evidence_refs = []
                    missing_evidence = [
                        f"Baseline mapping for test covering criterion {cid}"
                    ]
                    evidence_revision = None
                    covered_test_ids = []
                    evidence_trust = TrustClass.NONE
                    explanation = "Test passed but is not mapped in deliverable verification_refs."

                elif matching_claims:
                    # Rule: Claims only without evidence -> claim_only
                    status = CriterionStatus.UNVERIFIED
                    reason_code = ReasonCode.CLAIM_ONLY
                    supporting_evidence_refs = []
                    refuting_evidence_refs = []
                    missing_evidence = [
                        f"Verifier execution of {primary_test_id} at {assessed_revision}"
                    ]
                    evidence_revision = None
                    covered_test_ids = []
                    evidence_trust = TrustClass.NONE
                    explanation = "Self-reported claim only; independent verifier evidence missing."

                else:
                    # Rule: No evidence and no claims -> evidence_missing
                    status = CriterionStatus.UNVERIFIED
                    reason_code = ReasonCode.EVIDENCE_MISSING
                    supporting_evidence_refs = []
                    refuting_evidence_refs = []
                    missing_evidence = [
                        f"Verifier execution of {primary_test_id} at {assessed_revision}"
                    ]
                    evidence_revision = None
                    covered_test_ids = []
                    evidence_trust = TrustClass.NONE
                    explanation = "No verification evidence or claims found for criterion."

        results.append(
            CriterionResult(
                criterion_id=cid,
                deliverable_id=deliv_id,
                owner_project_id=owner_proj_id,
                weight=weight,
                required=required,
                status=status,
                reason_code=reason_code,
                supporting_evidence_refs=supporting_evidence_refs,
                refuting_evidence_refs=refuting_evidence_refs,
                missing_evidence=missing_evidence,
                explanation=explanation,
                reported_status=reported_status,
                reported_claim_refs=rep_claim_refs,
                evidence_revision=evidence_revision,
                covered_test_ids=covered_test_ids,
                previous_status=prev_status,
                evidence_trust=evidence_trust,
                waiver_ref=waiver_ref,
                conflicting_claim_refs=conflicting_claim_refs,
            )
        )

    # Deterministic output: stably ordered by criterion_id
    return sorted(results, key=lambda r: r.criterion_id)
