"""Assembly of single-project assessment proposals and domain findings.

Pure module: zero I/O, stdlib allowlist only (typing).
Combines assess_criteria, progress calculation, and findings detection into
the full single-project assessment proposal conforming to contracts/ppm-output.schema.json.
"""

from typing import Any

from .criteria import assess_criteria
from .findings import detect_findings
from .progress import (
    calculate_progress,
    calculate_readiness,
    calculate_reported_progress_percent,
    calculate_validity,
    calculate_verification,
)
from .trust import is_evidence_ttl_expired
from .types import AssessmentResult


def assemble_assessment(
    inputs: dict[str, Any],
    assessment_id: str | None = None,
    trigger: str | None = None,
    analysis_status: str | None = None,
) -> AssessmentResult:
    """Assemble a complete single-project assessment proposal and domain findings.

    Args:
        inputs: Reducer input dictionary conforming to inputs.schema.json.
        assessment_id: Optional caller-supplied assessment identifier.
        trigger: Optional caller-supplied trigger (defaults to inferred: 'evidence_ttl_expired' or 'snapshot').
        analysis_status: Optional analysis status (defaults to 'complete').

    Returns:
        AssessmentResult containing the full proposal model and domain findings.
    """
    baseline = inputs.get("baseline")
    has_baseline = baseline is not None
    claims = inputs.get("claims", [])
    evidence = inputs.get("evidence", [])
    waivers = inputs.get("waivers", [])
    assessed_revision = inputs.get("assessed_revision")
    now = inputs["now"]
    evidence_ttl_days = float(inputs.get("evidence_ttl_days", 7.0))
    project_id = inputs["project_id"]
    snapshot_refs = list(inputs.get("snapshot_refs", []))
    snapshot_seq = int(inputs.get("snapshot_seq", 1))
    basis_seq = int(inputs.get("basis_seq", 1))
    previous_assessment_id = inputs.get("previous_assessment_id")
    source_as_of = inputs.get("source_as_of")
    context = inputs.get("context")

    # 1. Evaluate acceptance criteria
    criteria_results = assess_criteria(
        baseline=baseline,
        claims=claims,
        evidence=evidence,
        waivers=waivers,
        assessed_revision=assessed_revision,
        now=now,
        evidence_ttl_days=evidence_ttl_days,
    )

    # 2. Calculate delivery progress, validity, verification, and readiness
    progress = calculate_progress(criteria_results, has_baseline=has_baseline)
    validity = calculate_validity(criteria_results, has_baseline=has_baseline)
    verification = calculate_verification(criteria_results, has_baseline=has_baseline)
    readiness = calculate_readiness(criteria_results, verification=verification, has_baseline=has_baseline)
    reported_progress = calculate_reported_progress_percent(claims, has_baseline=has_baseline)

    # 3. Collect evidence_refs sorted by (observed_at DESC, evidence_id DESC)
    sorted_ev = sorted(
        evidence,
        key=lambda e: (e.get("observed_at") or "", e.get("evidence_id") or ""),
        reverse=True,
    )
    evidence_refs = [e["evidence_id"] for e in sorted_ev if "evidence_id" in e]

    # 4. Collect missing_evidence
    if not has_baseline:
        missing_evidence = ["No approved baseline revision in PPM domain database"]
    else:
        missing_evidence = []
        for r in criteria_results:
            missing_evidence.extend(r.missing_evidence)

    # 5. Determine assessment trigger
    if trigger is None:
        has_ttl_expired = any(
            e.get("revision") == assessed_revision
            and is_evidence_ttl_expired(e.get("observed_at"), now, evidence_ttl_days)
            for e in evidence
        )
        resolved_trigger = "evidence_ttl_expired" if has_ttl_expired else "snapshot"
    else:
        resolved_trigger = trigger

    # 6. Resolve dependency_refs (relationship_proposed is excluded from dependency_refs per s07)
    raw_deps = inputs.get("dependency_refs", [])
    dependency_refs = [
        dict(d)
        for d in raw_deps
        if isinstance(d, dict)
        and d.get("relation_type") != "proposed"
        and d.get("status") != "proposed"
    ]

    # 7. Detect domain findings (pure signals that never mutate criteria or progress)
    findings = detect_findings(
        context=context,
        criteria_results=criteria_results,
        claims=claims,
        project_id=project_id,
        baseline=baseline,
    )

    resolved_assessment_id = assessment_id or f"asmt-{project_id}-{basis_seq}"
    resolved_analysis_status = analysis_status or "complete"
    baseline_revision = baseline.get("baseline_revision") if baseline else None

    return AssessmentResult(
        contract_version="1.1",
        assessment_id=resolved_assessment_id,
        project_id=project_id,
        baseline_revision=baseline_revision,
        snapshot_refs=snapshot_refs,
        validity=validity,
        verification=verification,
        acceptance_readiness=readiness,
        criteria=criteria_results,
        dependency_refs=dependency_refs,
        progress=progress,
        reported_progress_percent=reported_progress,
        evidence_refs=evidence_refs,
        missing_evidence=missing_evidence,
        as_of=now,
        source_as_of=source_as_of,
        assessed_revision=assessed_revision,
        previous_assessment_id=previous_assessment_id,
        snapshot_seq=snapshot_seq,
        analysis_status=resolved_analysis_status,
        basis_seq=basis_seq,
        trigger=resolved_trigger,
        findings=findings,
    )


def assess_project(
    inputs: dict[str, Any],
    assessment_id: str | None = None,
    trigger: str | None = None,
    analysis_status: str | None = None,
    include_explanation: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute the full single-project reducer, returning assessment and findings dictionaries.

    Returns:
        tuple of (assessment_dict, list_of_findings_dicts)
    """
    result = assemble_assessment(
        inputs=inputs,
        assessment_id=assessment_id,
        trigger=trigger,
        analysis_status=analysis_status,
    )
    assessment_dict = result.to_dict(include_explanation=include_explanation)
    findings_dicts = [f.to_dict() for f in result.findings]
    return assessment_dict, findings_dicts
