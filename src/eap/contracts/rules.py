"""Semantic rules and invariant enforcement for EAP contracts.

Ports all package-level semantic checks so runtime and validation do not depend
on external scripts.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from eap.contracts.digest import action_digest

TERMINAL: set[str] = {"succeeded", "failed", "cancelled", "timed_out"}

# Mirrors docs/02_RUNTIME_AND_SECURITY.md "State transitions". Keep both in sync.
ALLOWED: set[tuple[str, str]] = {
    ("queued", "dispatching"),
    ("queued", "cancelled"),
    ("dispatching", "running"),
    ("dispatching", "unknown"),
    ("dispatching", "failed"),
    ("dispatching", "cancelled"),
    ("running", "waiting_approval"),
    ("waiting_approval", "running"),
    ("running", "succeeded"),
    ("running", "failed"),
    ("running", "cancelling"),
    ("waiting_approval", "cancelling"),
    ("running", "timed_out"),
    ("waiting_approval", "timed_out"),
    ("cancelling", "cancelled"),
    ("cancelling", "succeeded"),
    ("cancelling", "failed"),
    ("cancelling", "unknown"),
    ("unknown", "running"),
    ("unknown", "waiting_approval"),
    ("unknown", "succeeded"),
    ("unknown", "failed"),
    ("unknown", "cancelled"),
}


class ContractViolation(ValueError):
    """Raised when an explicit contract invariant or semantic rule is violated."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _to_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return dict(obj)
    return dict(obj)


def check_run_progress(completed_units: int | None, total_units: int | None) -> None:
    if total_units is not None and completed_units is not None and completed_units > total_units:
        raise ContractViolation("PROGRESS_EXCEEDS_DENOMINATOR", "progress exceeds denominator")


def check_run_state_transition(from_state: str, to_state: str) -> None:
    if from_state in TERMINAL:
        raise ContractViolation("TERMINAL_STATE_CANNOT_BE_LEFT", "terminal run state cannot be left")
    if (from_state, to_state) not in ALLOWED:
        raise ContractViolation(
            "ILLEGAL_RUN_TRANSITION",
            f"illegal run transition {from_state}->{to_state}",
        )


def check_usage_cost(cost_status: str, cost_usd: float | None) -> None:
    if (cost_status == "unknown") != (cost_usd is None):
        raise ContractViolation("USAGE_COST_MISMATCH", "usage cost_status/cost_usd mismatch")


def check_result_usage(usage: Mapping[str, Any] | BaseModel) -> None:
    u = _to_dict(usage)
    if u.get("cost_status") == "unknown" and u.get("cost_usd") is not None:
        raise ContractViolation("USAGE_COST_MISMATCH", "unknown cost must be null")
    if u.get("cost_status") != "unknown" and u.get("cost_usd") is None:
        raise ContractViolation("USAGE_COST_MISMATCH", "known or partial cost needs numeric value")


def check_binding_rules(binding: Mapping[str, Any] | BaseModel) -> None:
    b = _to_dict(binding)
    caps = b.get("certified_capabilities", {})
    if b.get("mode") == "observe" and any(
        caps.get(k) for k in ["submit", "cancel", "resume", "approve_tool"]
    ):
        raise ContractViolation("OBSERVE_ADVERTISES_CONTROL", "observe binding advertises control")
    if b.get("mode") == "governed" and b.get("execution_boundary") == "external_trusted":
        raise ContractViolation(
            "GOVERNED_REQUIRES_ENFORCEMENT_BOUNDARY",
            "governed binding requires enforcement boundary",
        )
    fallback_refs = b.get("fallback_model_profile_refs", [])
    if b.get("model_profile_ref") in fallback_refs:
        raise ContractViolation("FALLBACK_REPEATS_PRIMARY", "fallback list repeats primary profile")


def check_experiment_rules(experiment: Mapping[str, Any] | BaseModel) -> None:
    e = _to_dict(experiment)
    if e.get("base_release_digest") == e.get("candidate_release_digest"):
        raise ContractViolation("IDENTICAL_CANDIDATE", "candidate must differ from baseline")
    if e.get("holdout_ref") == e.get("dataset_ref"):
        raise ContractViolation("HOLDOUT_NOT_ISOLATED", "holdout must be isolated from optimizer dataset")
    if e.get("proposer_ref") == e.get("reviewer_ref"):
        raise ContractViolation("PROPOSER_CANNOT_REVIEW", "proposer cannot review its own candidate")
    rollout = e.get("rollout", {})
    if rollout.get("stage") == "canary" and e.get("state") not in ("canary", "promoted", "reverted"):
        raise ContractViolation(
            "CANARY_REQUIRES_APPROVED_STATE",
            "canary rollout requires approved experiment state",
        )


def check_ppm_output_rules(output: Mapping[str, Any] | BaseModel) -> None:
    value = _to_dict(output)
    crits = value.get("criteria", [])
    for c in crits:
        if c.get("owner_project_id") != value.get("project_id"):
            raise ContractViolation(
                "CRITERION_OWNER_MISMATCH",
                "criterion owned by another project must be a dependency_ref",
            )
        if c.get("status") == "verified" and (
            not c.get("supporting_evidence_refs")
            or c.get("refuting_evidence_refs")
            or c.get("missing_evidence")
        ):
            raise ContractViolation(
                "VERIFIED_CRITERION_EVIDENCE_INVALID",
                "verified criterion needs supporting evidence and no refutation/missing evidence",
            )
        if c.get("status") == "contradicted" and not c.get("refuting_evidence_refs"):
            raise ContractViolation(
                "CONTRADICTED_CRITERION_NEEDS_REFUTATION",
                "contradicted criterion needs refuting evidence",
            )
        if c.get("status") in ("verified", "contradicted"):
            if value.get("assessed_revision") is None or c.get("evidence_revision") != value.get("assessed_revision"):
                raise ContractViolation(
                    "EVIDENCE_STALE_REVISION",
                    "verdict evidence must be produced at the assessed revision",
                )
            if not c.get("covered_test_ids") and c.get("reason_code") in ("evidence_current_pass", "evidence_refuted"):
                raise ContractViolation(
                    "COVERAGE_NOT_MAPPED",
                    "test-based verdict must name the covered baseline test ids",
                )
        if c.get("status") == "verified" and c.get("reason_code") != "evidence_current_pass":
            raise ContractViolation(
                "VERIFIED_MUST_BE_CURRENT_PASS",
                "verified criterion must have reason evidence_current_pass",
            )
        if set(c.get("supporting_evidence_refs", [])) & set(c.get("reported_claim_refs", [])):
            raise ContractViolation(
                "EVIDENCE_WRONG_PRODUCER",
                "reported claim used as supporting evidence",
            )
        if c.get("status") == "verified" and c.get("evidence_trust") not in ("verifier_observed", "ci_attested"):
            raise ContractViolation(
                "REPORTED_ARTIFACT_ONLY",
                "only verifier-observed or CI-attested evidence can verify a criterion",
            )
        if (c.get("status") == "waived") != (c.get("waiver_ref") is not None):
            raise ContractViolation(
                "WAIVED_REQUIRES_APPROVED_WAIVER",
                "waived status requires an approved waiver_ref (and only waived criteria carry one)",
            )
        if c.get("status") == "waived" and c.get("reason_code") != "waiver_approved":
            raise ContractViolation(
                "WAIVED_MUST_HAVE_WAIVER_APPROVED",
                "waived criterion must have reason waiver_approved",
            )

    ids = [c.get("criterion_id") for c in crits]
    if len(ids) != len(set(ids)):
        raise ContractViolation("DUPLICATE_CRITERION", "duplicate criterion in assessment")

    req = [c for c in crits if c.get("required")]
    if value.get("verification") == "verified" and (
        not crits
        or any(c.get("status") not in ("verified", "waived") for c in req)
        or value.get("missing_evidence")
        or value.get("validity") != "current"
    ):
        raise ContractViolation(
            "VERIFIED_PROJECT_UNMET_CRITERIA",
            "verified project requires all required criteria verified, no missing evidence, current validity",
        )

    if any(c.get("status") == "contradicted" for c in crits) and value.get("verification") != "contradicted":
        raise ContractViolation(
            "CONTRADICTING_EVIDENCE_SURFACING",
            "contradicting evidence must surface as contradicted",
        )

    if value.get("acceptance_readiness") == "ready_for_review" and value.get("verification") != "verified":
        raise ContractViolation(
            "ACCEPTANCE_READINESS_UNVERIFIED",
            "only verified assessments are ready for acceptance review",
        )

    prog = value.get("progress", {})
    if prog.get("basis") == "none":
        if prog.get("verified_weight") is not None or prog.get("total_weight") is not None:
            raise ContractViolation("PROGRESS_NO_BASELINE", "no baseline means no progress ratio")
    else:
        if value.get("baseline_revision") is None:
            raise ContractViolation(
                "PROGRESS_BASELINE_REVISION_REQUIRED",
                "baseline_weighted progress requires baseline_revision",
            )
        total = sum(c.get("weight", 0) for c in crits if c.get("status") != "waived")
        ver = sum(c.get("weight", 0) for c in crits if c.get("status") == "verified")
        if prog.get("total_weight") != total or prog.get("verified_weight") != ver:
            raise ContractViolation(
                "PROGRESS_WEIGHT_MISMATCH",
                "progress weights do not match criteria (denominator must be the baseline criteria set)",
            )

    if prog.get("criteria_total") != len(crits):
        raise ContractViolation("CRITERIA_TOTAL_MISMATCH", "criteria_total mismatch")


def check_hud_projection_rules(hud: Mapping[str, Any] | BaseModel) -> None:
    value = _to_dict(hud)
    for row in value.get("rows", []):
        p = row.get("progress", {})
        if p.get("kind") == "units":
            if (
                p.get("total_units") is None
                or p.get("completed_units") is None
                or p.get("completed_units", 0) > p.get("total_units", 0)
            ):
                raise ContractViolation("HUD_PROGRESS_DENOMINATOR_INVALID", "units progress needs a valid denominator")
        elif p.get("total_units") is not None or p.get("completed_units") is not None:
            raise ContractViolation(
                "HUD_PROGRESS_KIND_MISMATCH",
                "percent data present without units progress kind",
            )

        c = row.get("cost", {})
        if (c.get("status") == "unknown") != (c.get("amount_usd") is None):
            raise ContractViolation(
                "HUD_COST_MISMATCH",
                "cost status/amount mismatch (unknown must be null, partial is a lower bound)",
            )

        if (row.get("run_id") is None) != (row.get("run_state") is None):
            raise ContractViolation("HUD_RUN_STATE_REQUIRES_RUN_ID", "run_state requires run_id")

        if row.get("run_state") == "unknown" and row.get("remote_outcome") != "unknown":
            raise ContractViolation(
                "HUD_UNKNOWN_RUN_REMOTE_OUTCOME",
                "unknown run must expose unknown remote outcome",
            )

        if row.get("task_state") == "completed" and row.get("run_state") not in ("succeeded", None):
            raise ContractViolation(
                "HUD_COMPLETED_TASK_NON_SUCCEEDED_RUN",
                "completed task with non-succeeded current run",
            )

        pr = row.get("project")
        if (
            pr
            and pr.get("verified_weight") is not None
            and pr.get("total_weight") is not None
            and pr.get("verified_weight") > pr.get("total_weight")
        ):
            raise ContractViolation(
                "HUD_PROJECT_WEIGHT_EXCEEDS_DENOMINATOR",
                "project verified weight exceeds denominator",
            )


def check_action_proposal_rules(proposal: Mapping[str, Any] | BaseModel) -> None:
    value = _to_dict(proposal)
    if action_digest(value) != value.get("action_digest"):
        raise ContractViolation(
            "ACTION_DIGEST_MISMATCH",
            "action digest does not match canonical proposal fields",
        )


def check_location_registration_rules(location: Mapping[str, Any] | BaseModel) -> None:
    value = _to_dict(location)
    root = value.get("root_path", "")
    if not (root.startswith("/") or (len(root) > 2 and root[1] == ":" and root[2] in "/\\")):
        raise ContractViolation("LOCATION_ROOT_NOT_ABSOLUTE", "root_path must be absolute")
    aliases_dict = value.get("artifact_aliases", {})
    for key, aliases in aliases_dict.items():
        if len(aliases) != len(set(aliases)):
            raise ContractViolation("LOCATION_DUPLICATE_ALIAS", f"duplicate alias for {key}")
    if value.get("state") == "active" and not value.get("approved_by_ref"):
        raise ContractViolation(
            "LOCATION_ACTIVE_REQUIRES_APPROVAL",
            "active location requires approval",
        )


def check_run_command_rules(command: Mapping[str, Any] | BaseModel) -> None:
    value = _to_dict(command)
    if (value.get("state") == "rejected") != (value.get("rejection_code") is not None):
        raise ContractViolation(
            "RUN_COMMAND_REJECTION_CODE_MISMATCH",
            "rejection_code only for rejected commands",
        )


def check_event_rules(event: Mapping[str, Any] | BaseModel) -> None:
    value = _to_dict(event)
    ev_type = value.get("event_type")
    payload = value.get("payload", {})
    if ev_type == "run.progress":
        check_run_progress(payload.get("completed_units"), payload.get("total_units"))
    elif ev_type == "run.state_changed":
        check_run_state_transition(payload.get("from_state"), payload.get("to_state"))
    elif ev_type == "usage.reported":
        check_usage_cost(payload.get("cost_status"), payload.get("cost_usd"))


def semantic_checks(kind: str, value: Any) -> None:
    """Validate semantic rules for a given contract kind."""
    if kind == "event":
        check_event_rules(value)
    elif kind == "result":
        val = _to_dict(value)
        check_result_usage(val.get("usage", {}))
    elif kind == "binding":
        check_binding_rules(value)
    elif kind == "experiment":
        check_experiment_rules(value)
    elif kind == "ppm-output":
        check_ppm_output_rules(value)
    elif kind == "hud-projection":
        check_hud_projection_rules(value)
    elif kind == "action-proposal":
        check_action_proposal_rules(value)
    elif kind == "location-registration":
        check_location_registration_rules(value)
    elif kind == "run-command":
        check_run_command_rules(value)


def check_approval_pair(
    proposal: Mapping[str, Any] | BaseModel,
    decision: Mapping[str, Any] | BaseModel,
) -> None:
    p = _to_dict(proposal)
    d = _to_dict(decision)
    if d.get("action_digest") != p.get("action_digest") or d.get("nonce") != p.get("nonce"):
        raise ContractViolation(
            "APPROVAL_DIGEST_NONCE_MISMATCH",
            "approval does not bind exact proposal digest/nonce",
        )
    if d.get("proposal_id") != p.get("proposal_id"):
        raise ContractViolation("APPROVAL_PROPOSAL_ID_MISMATCH", "approval proposal_id mismatch")

    p_exp = dt.datetime.fromisoformat(p["expires_at"])
    d_valid = dt.datetime.fromisoformat(d["valid_until"])
    if d_valid > p_exp:
        raise ContractViolation("APPROVAL_OUTLIVES_PROPOSAL", "approval outlives proposal expiry")

    cons = d.get("consumption", {})
    if cons.get("state") == "consumed" and (
        cons.get("consumed_at") is None or cons.get("consumed_by_operation_key") != p.get("operation_key")
    ):
        raise ContractViolation(
            "APPROVAL_CONSUMED_OPERATION_MISMATCH",
            "consumed approval must record consuming operation",
        )
    if d.get("decision") == "denied" and cons.get("state") == "consumed":
        raise ContractViolation("DENIED_APPROVAL_CONSUMED", "denied approval cannot be consumed")


def check_operation_key_reuse(
    proposals: Sequence[Mapping[str, Any] | BaseModel],
) -> None:
    """Same operation_key across attempts must describe the same business action."""
    seen: dict[str, tuple[Any, Any, Any]] = {}
    for item in proposals:
        p = _to_dict(item)
        content = (p.get("tool_id"), p.get("arguments_sha256"), p.get("resource_ref"))
        op_key = p["operation_key"]
        prev = seen.setdefault(op_key, content)
        if prev != content:
            raise ContractViolation(
                "OPERATION_KEY_CONFLICT",
                "OPERATION_KEY_CONFLICT: operation_key reused with different tool/arguments/target",
            )


def check_location_set(
    locations: Sequence[Mapping[str, Any] | BaseModel],
) -> None:
    """Physical source identity vs project scope (ADR-019)."""
    source_of_identity: dict[str, str] = {}
    scopes: dict[tuple[str, str], str] = {}
    for item in locations:
        loc = _to_dict(item)
        ident = loc.get("identity", {})
        for physical in (
            ident.get("canonical_root_fingerprint"),
            ident.get("repo_identity"),
        ):
            if physical is None:
                continue
            prev = source_of_identity.setdefault(physical, loc["source_id"])
            if prev != loc["source_id"]:
                raise ContractViolation(
                    "DUPLICATE_SOURCE",
                    "duplicate source: same physical folder registered under two source_ids",
                )
        key = (loc["source_id"], loc["metadata_root"])
        if key in scopes:
            if scopes[key] != loc["project_id"]:
                raise ContractViolation("METADATA_ROOT_CONFLICT", "metadata_root registered by two projects")
            raise ContractViolation("DUPLICATE_LOCATION", "duplicate location: same source and metadata_root")
        scopes[key] = loc["project_id"]


def apply_assessment(
    current: Mapping[str, Any] | BaseModel | None,
    incoming: Mapping[str, Any] | BaseModel,
) -> tuple[Any, Any]:
    """Current-assessment pointer rule (ADR-021 rev. 2): compare-and-set on basis_seq.

    Assigned when the assessment task is created by any trigger.
    A result that finishes later with a lower basis_seq is history.
    """
    inc = _to_dict(incoming)
    if current is None:
        return incoming, None
    curr = _to_dict(current)
    if inc["basis_seq"] > curr["basis_seq"]:
        return incoming, None
    return current, incoming


def check_event_stream(events: Sequence[Mapping[str, Any] | BaseModel]) -> None:
    """Validate ordering and integrity across an event sequence."""
    seen: set[tuple[str, str, str]] = set()
    last_state: dict[str, str] = {}
    producer_seq: dict[tuple[str, str | None], int] = {}
    for item in events:
        event = _to_dict(item)
        key = (event["tenant_id"], event["producer_id"], event["event_id"])
        if key in seen:
            raise ContractViolation("DUPLICATE_EVENT", "duplicate fixture event")
        seen.add(key)
        pkey = (event["producer_id"], event.get("run_id"))
        p_seq = event["producer_seq"]
        if p_seq <= producer_seq.get(pkey, 0):
            raise ContractViolation(
                "PRODUCER_SEQ_ORDERING",
                "producer_seq not increasing within producer/run",
            )
        producer_seq[pkey] = p_seq
        if event.get("event_type") == "run.state_changed":
            run = event.get("run_id")
            p = event.get("payload", {})
            from_state = p.get("from_state")
            to_state = p.get("to_state")
            if run and run in last_state and last_state[run] != from_state:
                raise ContractViolation(
                    "RUN_STATE_SEQUENCE_MISMATCH",
                    f"state_changed from_state does not match previous state for {run}",
                )
            if run:
                last_state[run] = to_state
