"""Portfolio/program rollup for the PPM reducer (PPM-M0-WP4).

Pure module: zero I/O, stdlib allowlist only (dataclasses, math, typing).
Output conforms to ppm-v2/evals/fixtures/rollup.schema.json (internal to M0;
official contract at PPM-C1).

Rules (ROLLUP_SCHEMA.md; DG-1 condition DG1-C5; conformance C11, C24, C43):

1. Authorization filter first. The visible project set is computed and every
   other project (and everything it owns) is discarded *before* any validation
   or arithmetic. Downstream code only ever receives visible projects, so no
   hidden project id, name, count, weight, cost or validation error can reach
   the result. ``partial`` is a single boolean and is the only signal that
   something was withheld.
2. Single ownership (DG1-C5). Each deliverable is counted once, under its owner
   project. Consumers reference shared components through ``dependency_refs``;
   those references never contribute weight. Baseline criteria in the assessed
   project's input whose ``owner_project_id`` is another project are dropped
   from that project's units: a consumer never adds a shared component's
   criteria to its denominator. Two visible projects claiming the same
   deliverable is rejected (``InputRejected("deliverable_multiple_owners")``)
   instead of being silently double counted.
3. Cost (C11, s10). Any visible project with missing/unknown cost makes
   ``cost_state="unknown"`` and ``cost_usd=None``; cost is never defaulted to 0.
   ``budget_usd`` is the sum only when every visible project has a budget.
4. Denominator. ``percent`` is only emitted together with a positive
   ``total_weight`` (waived units excluded). No denominator -> weights and
   percent are ``None``.

Progress units. The assessed project (``inputs["project_id"]``) contributes one
unit per owned baseline criterion, with the status computed by
``assess_criteria`` (so waivers, contradictions and staleness are honoured).
Every other visible project contributes one unit per deliverable listed in the
portfolio context, ``verified`` or ``unverified`` according to its flag.
"""

import dataclasses
import math
from collections.abc import Sequence
from typing import Any

from .criteria import assess_criteria
from .types import CriterionResult, CriterionStatus, InputRejected, ProgressBasis

COST_KNOWN = "known"
COST_UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True)
class RollupProgress:
    """Aggregate progress across visible projects (rollup.schema.json ``progress``)."""

    basis: str
    verified_weight: float | None
    total_weight: float | None
    percent: float | None
    criteria_total: int
    criteria_verified: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "verified_weight": self.verified_weight,
            "total_weight": self.total_weight,
            "percent": self.percent,
            "criteria_total": self.criteria_total,
            "criteria_verified": self.criteria_verified,
        }


@dataclasses.dataclass(frozen=True)
class RollupResult:
    """Portfolio rollup result (internal to M0; ``rollup.schema.json``)."""

    portfolio_id: str
    as_of: str
    partial: bool
    authorized_project_ids: list[str]
    distinct_deliverable_ids: list[str]
    progress: RollupProgress
    cost_state: str
    cost_usd: float | None
    budget_usd: float | None

    @property
    def distinct_deliverables_count(self) -> int:
        return len(self.distinct_deliverable_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id,
            "as_of": self.as_of,
            "partial": self.partial,
            "authorized_project_ids": list(self.authorized_project_ids),
            "distinct_deliverables_count": self.distinct_deliverables_count,
            "distinct_deliverable_ids": list(self.distinct_deliverable_ids),
            "progress": self.progress.to_dict(),
            "cost_state": self.cost_state,
            "cost_usd": self.cost_usd,
            "budget_usd": self.budget_usd,
        }


@dataclasses.dataclass(frozen=True)
class _Unit:
    """One progress unit (a criterion of the assessed project or a context deliverable)."""

    deliverable_id: str
    weight: float
    status: str


# --------------------------------------------------------------------------- #
# Step 1: authorization filter (runs before anything else touches projects)
# --------------------------------------------------------------------------- #


def _filter_authorized(
    context: dict[str, Any], portfolio: dict[str, Any]
) -> tuple[list[dict[str, Any]], bool]:
    """Return (visible_projects, partial).

    Visible = listed in the portfolio, permitted by ``viewer_authorized_project_ids``
    (all projects when that key is absent) and not named in ``hidden_project_ids``.
    Entries without a usable string ``project_id`` cannot be authorized and are
    withheld. Nothing about withheld entries is returned except the boolean.
    """
    raw_projects = portfolio.get("projects")
    if not isinstance(raw_projects, list):
        raise InputRejected(
            reason="invalid_portfolio", message="context.portfolio.projects must be a list"
        )

    hidden_raw = context.get("hidden_project_ids") or []
    hidden = {h for h in hidden_raw if isinstance(h, str)}
    allow_raw = context.get("viewer_authorized_project_ids")
    allow: set[str] | None = (
        None if allow_raw is None else {a for a in allow_raw if isinstance(a, str)}
    )

    visible: list[dict[str, Any]] = []
    withheld = bool(hidden)
    for project in raw_projects:
        pid = project.get("project_id") if isinstance(project, dict) else None
        if (
            not isinstance(pid, str)
            or not pid
            or pid in hidden
            or (allow is not None and pid not in allow)
        ):
            withheld = True
            continue
        visible.append(project)
    return visible, withheld


# --------------------------------------------------------------------------- #
# Step 2: computation over visible projects only
# --------------------------------------------------------------------------- #


def _valid_weight(raw: Any) -> float:
    if raw is None or isinstance(raw, bool) or not isinstance(raw, int | float):
        raise InputRejected(
            reason="invalid_weight", message="Deliverable weight must be a positive number"
        )
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise InputRejected(
            reason="invalid_weight", message="Deliverable weight must be a positive number"
        )
    return value


def _money(raw: Any) -> float | None:
    """Return a non-negative finite amount, or None when unmeasured."""
    if raw is None:
        return None
    if (
        isinstance(raw, bool)
        or not isinstance(raw, int | float)
        or not math.isfinite(float(raw))
        or raw < 0
    ):
        raise InputRejected(
            reason="invalid_cost", message="Cost/budget must be a non-negative number or null"
        )
    return float(raw)


def _context_units(project: dict[str, Any]) -> list[_Unit]:
    units: list[_Unit] = []
    for d in project.get("deliverables") or []:
        did = d.get("deliverable_id") if isinstance(d, dict) else None
        if not isinstance(did, str) or not did:
            raise InputRejected(
                reason="invalid_deliverable", message="Deliverable requires a deliverable_id"
            )
        verified = d.get("verified")
        if not isinstance(verified, bool):
            raise InputRejected(
                reason="invalid_deliverable",
                message=f"Deliverable '{did}' requires boolean verified",
            )
        status = CriterionStatus.VERIFIED if verified else CriterionStatus.UNVERIFIED
        units.append(
            _Unit(deliverable_id=did, weight=_valid_weight(d.get("weight")), status=status)
        )
    return units


def _criteria_units(project_id: str, criteria_results: Sequence[CriterionResult]) -> list[_Unit]:
    # DG1-C5: criteria owned by another project (e.g. a shared component copied
    # into a consumer's baseline) are never part of this project's units.
    return [
        _Unit(deliverable_id=c.deliverable_id, weight=_valid_weight(c.weight), status=str(c.status))
        for c in criteria_results
        if c.owner_project_id == project_id
    ]


def _aggregate_progress(units: Sequence[_Unit]) -> RollupProgress:
    criteria_total = len(units)
    criteria_verified = sum(1 for u in units if u.status == CriterionStatus.VERIFIED)
    counted = [u for u in units if u.status != CriterionStatus.WAIVED]
    if not counted:
        # No denominator -> no weights and no percent.
        return RollupProgress(
            basis=ProgressBasis.BASELINE_WEIGHTED if units else ProgressBasis.NONE,
            verified_weight=None,
            total_weight=None,
            percent=None,
            criteria_total=criteria_total,
            criteria_verified=criteria_verified,
        )
    # math.fsum is exactly rounded, hence independent of input order.
    total = math.fsum(u.weight for u in counted)
    verified = math.fsum(u.weight for u in counted if u.status == CriterionStatus.VERIFIED)
    return RollupProgress(
        basis=ProgressBasis.BASELINE_WEIGHTED,
        verified_weight=float(round(verified, 6)),
        total_weight=float(round(total, 6)),
        percent=float(round(verified / total * 100.0, 2)),
        criteria_total=criteria_total,
        criteria_verified=criteria_verified,
    )


def _aggregate_cost(projects: Sequence[dict[str, Any]]) -> tuple[str, float | None, float | None]:
    costs: list[float] = []
    budgets: list[float] = []
    cost_known = True
    budget_known = True
    for p in projects:
        cost = _money(p.get("cost_usd"))
        budget = _money(p.get("budget_usd"))
        if p.get("cost_state") == COST_KNOWN and cost is not None:
            costs.append(cost)
        else:
            cost_known = False  # never default a missing cost to 0
        if budget is None:
            budget_known = False
        else:
            budgets.append(budget)
    if cost_known:
        return (
            COST_KNOWN,
            float(math.fsum(costs)),
            (float(math.fsum(budgets)) if budget_known else None),
        )
    return COST_UNKNOWN, None, (float(math.fsum(budgets)) if budget_known else None)


def rollup_portfolio(
    inputs: dict[str, Any],
    criteria_results: Sequence[CriterionResult] | None = None,
) -> RollupResult:
    """Compute the confidentiality-preserving portfolio rollup.

    Args:
        inputs: Reducer input dictionary (inputs.schema.json) carrying
            ``context.portfolio`` and optionally ``context.viewer_authorized_project_ids``
            / ``context.hidden_project_ids``.
        criteria_results: Optional precomputed ``assess_criteria`` output for
            ``inputs["project_id"]``. Computed on demand when omitted and the
            assessed project is visible.

    Raises:
        InputRejected: missing portfolio context, no visible project, duplicate
            visible project, a deliverable owned by two visible projects, or an
            invalid visible weight/cost.
    """
    context = inputs.get("context")
    portfolio = context.get("portfolio") if isinstance(context, dict) else None
    if not isinstance(context, dict) or not isinstance(portfolio, dict):
        raise InputRejected(
            reason="missing_portfolio_context", message="context.portfolio is required for rollup"
        )
    portfolio_id = portfolio.get("portfolio_id")
    if not isinstance(portfolio_id, str) or not portfolio_id:
        raise InputRejected(reason="invalid_portfolio", message="portfolio_id is required")

    # ---- Step 1: filter. Nothing below sees a withheld project. ----
    visible, partial = _filter_authorized(context, portfolio)
    del portfolio  # the unfiltered list must not be used past this point
    if not visible:
        raise InputRejected(
            reason="no_authorized_projects", message="Viewer is not authorized for any project"
        )

    # ---- Step 2: compute over visible projects only. ----
    assessed_pid = inputs.get("project_id")
    baseline = inputs.get("baseline")
    owner_of: dict[str, str] = {}
    units: list[_Unit] = []
    seen_projects: set[str] = set()

    for project in sorted(visible, key=lambda p: str(p["project_id"])):
        pid = str(project["project_id"])
        if pid in seen_projects:
            raise InputRejected(
                reason="duplicate_project", message=f"Project '{pid}' listed more than once"
            )
        seen_projects.add(pid)

        if pid == assessed_pid and baseline is not None:
            results = criteria_results
            if results is None:
                results = assess_criteria(
                    baseline=baseline,
                    claims=inputs.get("claims", []),
                    evidence=inputs.get("evidence", []),
                    waivers=inputs.get("waivers", []),
                    assessed_revision=inputs.get("assessed_revision"),
                    now=inputs["now"],
                    evidence_ttl_days=float(inputs.get("evidence_ttl_days", 7.0)),
                )
            project_units = _criteria_units(pid, results)
        else:
            project_units = _context_units(project)

        for u in project_units:
            owner = owner_of.setdefault(u.deliverable_id, pid)
            if owner != pid:
                raise InputRejected(
                    reason="deliverable_multiple_owners",
                    message=f"Deliverable '{u.deliverable_id}' is claimed by more than one visible project",
                )
        units.extend(project_units)

    cost_state, cost_usd, budget_usd = _aggregate_cost(visible)

    return RollupResult(
        portfolio_id=portfolio_id,
        as_of=str(inputs["now"]),
        partial=partial,
        authorized_project_ids=sorted(seen_projects),
        distinct_deliverable_ids=sorted(owner_of),
        progress=_aggregate_progress(units),
        cost_state=cost_state,
        cost_usd=cost_usd,
        budget_usd=budget_usd,
    )
