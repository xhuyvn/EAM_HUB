"""Domain findings detection for PPM reducer.

Pure module: zero I/O, stdlib allowlist only (dataclasses, typing).
Rule: Domain findings provide reviewer/governance signals and never mutate
criterion verdicts or delivery-progress numbers.
"""

from collections.abc import Sequence
from typing import Any

from .types import (
    CriterionResult,
    FindingKind,
    FindingResult,
    FindingSeverity,
)


def detect_findings(
    context: dict[str, Any] | None,
    criteria_results: Sequence[CriterionResult | dict[str, Any]],
    claims: Sequence[dict[str, Any]] | None = None,
    project_id: str | None = None,
    baseline: dict[str, Any] | None = None,
) -> list[FindingResult]:
    """Detect domain findings across context, criteria results, and claims.

    Supported findings:
    - architecture_drift (s03): Implementation deviates from architecture blueprint without approved ADR.
    - relationship_proposed (s07): Inferred component dependency awaiting owner approval.
    - identity_conflict (s23): Duplicate project_id observed at multiple repositories.
    - conflicting_claims (s16): Concurrent updates in same snapshot submit divergent progress claims.
    """
    findings: list[FindingResult] = []
    ctx = context or {}

    # Extract default criterion_ids if baseline is present
    default_criterion_ids: list[str] = []
    if baseline and "criteria" in baseline:
        default_criterion_ids = [c["criterion_id"] for c in baseline["criteria"] if "criterion_id" in c]
    elif criteria_results:
        default_criterion_ids = [
            (c["criterion_id"] if isinstance(c, dict) else c.criterion_id) for c in criteria_results
        ]

    # 1. architecture_drift (s03)
    if "architecture_drift" in ctx:
        ad = ctx["architecture_drift"]
        if not ad.get("has_approved_adr", False):
            component = ad.get("component", "unknown")
            crit_ids = ad.get("criterion_ids") or list(default_criterion_ids)
            msg = ad.get("message")
            if not msg:
                if ad.get("implemented_pattern") == "direct_sql_query":
                    msg = "Direct SQL access observed without approved Architecture Decision Record."
                else:
                    msg = (
                        f"Implementation diverges from architectural blueprint without approved "
                        f"Architecture Decision Record for {component}."
                    )

            findings.append(
                FindingResult(
                    kind=FindingKind.ARCHITECTURE_DRIFT,
                    severity=FindingSeverity.REVIEW_REQUIRED,
                    target_ref=f"component:{component}",
                    criterion_ids=crit_ids,
                    details={
                        "component": component,
                        "implemented_pattern": ad.get("implemented_pattern"),
                        "prescribed_pattern": ad.get("prescribed_pattern"),
                        "has_approved_adr": False,
                        "message": msg,
                    },
                )
            )

    # 2. relationship_proposed (s07)
    if "relationship_proposals" in ctx:
        proposals = ctx.get("relationship_proposals", [])
        for prop in proposals:
            if prop.get("status") == "proposed":
                target_del = prop.get("target_deliverable_id", "unknown")
                crit_ids = prop.get("criterion_ids") or list(default_criterion_ids)
                msg = prop.get("message") or "Inferred component dependency awaiting owner approval."

                findings.append(
                    FindingResult(
                        kind=FindingKind.RELATIONSHIP_PROPOSED,
                        severity=FindingSeverity.INFO,
                        target_ref=f"deliverable:{target_del}",
                        criterion_ids=crit_ids,
                        details={
                            "source_project_id": prop.get("source_project_id"),
                            "target_project_id": prop.get("target_project_id"),
                            "target_deliverable_id": target_del,
                            "relation_type": prop.get("relation_type"),
                            "status": "proposed",
                            "message": msg,
                        },
                    )
                )

    # 3. identity_conflict (s23)
    if "repo_identity" in ctx:
        ri = ctx["repo_identity"]
        reg = ri.get("registered")
        obs = ri.get("observed")
        if reg and obs and reg != obs:
            pid = project_id or ri.get("project_id", "unknown")
            msg = (
                ri.get("message")
                or "Project ID encountered at a secondary repository without re-registration or clone confirmation."
            )

            findings.append(
                FindingResult(
                    kind=FindingKind.IDENTITY_CONFLICT,
                    severity=FindingSeverity.BLOCKER,
                    target_ref=f"project:{pid}",
                    criterion_ids=[],
                    details={
                        "project_id": pid,
                        "registered_repo": reg,
                        "observed_repo": obs,
                        "message": msg,
                    },
                )
            )

    # 4. conflicting_claims (s16)
    claims_map: dict[str, dict[str, Any]] = {}
    if claims:
        for cl in claims:
            cid = cl.get("claim_id")
            if cid:
                claims_map[cid] = cl

    for r in criteria_results:
        conflicts = r["conflicting_claim_refs"] if isinstance(r, dict) else r.conflicting_claim_refs
        if conflicts:
            crit_id = r["criterion_id"] if isinstance(r, dict) else r.criterion_id
            claimed_statuses = [
                claims_map[c_ref].get("reported_status")
                for c_ref in conflicts
                if c_ref in claims_map and claims_map[c_ref].get("reported_status")
            ]

            findings.append(
                FindingResult(
                    kind=FindingKind.CONFLICTING_CLAIMS,
                    severity=FindingSeverity.WARNING,
                    target_ref=f"criterion:{crit_id}",
                    criterion_ids=[crit_id],
                    details={
                        "conflicting_updates": list(conflicts),
                        "claimed_statuses": claimed_statuses,
                        "message": "Concurrent updates within the same snapshot submit conflicting progress claims.",
                    },
                )
            )

    return findings
