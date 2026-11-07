"""Rollup tests for ppm_reducer (PPM-M0-WP4).

Covers:
1. s08, s10, s19 expected_rollup.json match post RFC 8785 JCS; outputs validate
   against ppm-v2/evals/fixtures/rollup.schema.json.
   (Note: s08 and s10 inputs.json on disk lack context.viewer_authorized_project_ids;
   per M4-1 fail-closed authorization they xfail with InputRejected(reason="missing_authorization"),
   tracked under ISS-NEW-1; with the remedy supplied, 3/3 match byte-for-byte post-JCS).
2. M4-1 Fail-Closed Authorization:
   - absent viewer_authorized_project_ids -> InputRejected(reason="missing_authorization")
   - only hidden_project_ids given -> InputRejected(reason="missing_authorization")
   - empty list -> InputRejected(reason="no_authorized_projects")
   - non-list -> InputRejected(reason="missing_authorization")
   - differential test: adding/removing hidden projects leaves output byte-identical except partial
3. DG1-C5 / C24: shared component counted once; moving the shared component's
   criteria into a consumer's input leaves the consumer's total_weight unchanged.
4. C43 / s19: hidden project ids, names and deliverable ids never appear in the
   serialized output; adding/removing hidden projects leaves the output byte-identical.
5. C11 / s10: missing cost -> cost_state=unknown, cost_usd=None (never 0).
6. Denominator: no percent without a positive total_weight.
7. Hypothesis properties: total_weight == sum over distinct owner deliverables;
   schema validity; order independence; adding hidden projects leaves output byte-identical except partial.
8. Determinism (shuffled inputs -> identical JCS SHA-256) and runtime purity audit.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, cast

import jsonschema  # type: ignore[import-untyped]
import pytest
import rfc8785
from hypothesis import given, settings
from hypothesis import strategies as st

from ppm_reducer import InputRejected, assess_criteria, rollup_portfolio

ROLLUP_FIXTURES = ["s08-shared-component", "s10-missing-budget", "s19-partial-portfolio"]


def _fixtures_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "ppm-v2" / "evals" / "fixtures"


def _load(fixture: str, name: str) -> Any:
    return json.loads((_fixtures_dir() / fixture / name).read_text(encoding="utf-8"))


def _load_with_auth(fixture: str) -> dict[str, Any]:
    """Load fixture inputs and ensure viewer_authorized_project_ids is present.

    Used by tests that exercise non-authorization rollup domain logic (single ownership,
    cost matrix, determinism) using s08 or s10 as test vectors.
    """
    inputs = cast(dict[str, Any], _load(fixture, "inputs.json"))
    ctx = inputs.setdefault("context", {})
    if "viewer_authorized_project_ids" not in ctx:
        expected = _load(fixture, "expected_rollup.json")
        ctx["viewer_authorized_project_ids"] = list(expected["authorized_project_ids"])
    return inputs


def _jcs(obj: Any) -> bytes:
    return rfc8785.dumps(obj)


def _sha(obj: Any) -> str:
    return hashlib.sha256(_jcs(obj)).hexdigest()


@pytest.fixture(scope="module")
def rollup_validator() -> jsonschema.Draft202012Validator:
    schema = json.loads((_fixtures_dir() / "rollup.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


# --------------------------------------------------------------------------- #
# 1. Corpus match
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param(
            "s08-shared-component",
            marks=pytest.mark.xfail(
                reason="ISS-NEW-1: fixture inputs.json lacks context.viewer_authorized_project_ids required by M4-1 fail-closed authorization",
                raises=InputRejected,
                strict=True,
            ),
        ),
        pytest.param(
            "s10-missing-budget",
            marks=pytest.mark.xfail(
                reason="ISS-NEW-1: fixture inputs.json lacks context.viewer_authorized_project_ids required by M4-1 fail-closed authorization",
                raises=InputRejected,
                strict=True,
            ),
        ),
        "s19-partial-portfolio",
    ],
)
def test_expected_rollup_matches_post_jcs(
    fixture: str, rollup_validator: jsonschema.Draft202012Validator
) -> None:
    produced = rollup_portfolio(_load(fixture, "inputs.json")).to_dict()
    expected = _load(fixture, "expected_rollup.json")
    rollup_validator.validate(produced)
    assert _jcs(produced) == _jcs(expected), (
        f"[{fixture}] rollup JCS mismatch\nPRODUCED: {json.dumps(produced, indent=2)}\n"
        f"EXPECTED: {json.dumps(expected, indent=2)}"
    )


@pytest.mark.parametrize("fixture", ROLLUP_FIXTURES)
def test_expected_rollup_matches_post_jcs_with_remedy(
    fixture: str, rollup_validator: jsonschema.Draft202012Validator
) -> None:
    """Demonstrates that once ISS-NEW-1 is resolved (viewer_authorized_project_ids in inputs.json),
    all 3/3 rollup fixtures match byte-for-byte post-JCS."""
    inputs = _load_with_auth(fixture)
    expected = _load(fixture, "expected_rollup.json")
    produced = rollup_portfolio(inputs).to_dict()
    rollup_validator.validate(produced)
    assert _jcs(produced) == _jcs(expected), (
        f"[{fixture}] rollup JCS mismatch with remedy\nPRODUCED: {json.dumps(produced, indent=2)}\n"
        f"EXPECTED: {json.dumps(expected, indent=2)}"
    )


def test_rollup_corpus_summary_table() -> None:
    """Print per-fixture PASS table for the acceptance packet."""
    print("\n" + "=" * 80)
    print(f"{'Fixture ID':<28} | {'Raw (M4-1)':<14} | {'With Remedy':<12} | sha256(JCS) prefix")
    print("-" * 80)
    for fixture in ROLLUP_FIXTURES:
        raw_inputs = _load(fixture, "inputs.json")
        expected = _load(fixture, "expected_rollup.json")
        try:
            raw_prod = rollup_portfolio(raw_inputs).to_dict()
            raw_status = "PASS" if _jcs(raw_prod) == _jcs(expected) else "FAIL"
        except InputRejected as exc:
            raw_status = f"XFAIL({exc.reason})"

        remedy_inputs = _load_with_auth(fixture)
        rem_prod = rollup_portfolio(remedy_inputs).to_dict()
        rem_status = "PASS" if _jcs(rem_prod) == _jcs(expected) else "FAIL"
        print(f"{fixture:<28} | {raw_status:<14} | {rem_status:<12} | {_sha(rem_prod)[:16]}")
    print("=" * 80)


# --------------------------------------------------------------------------- #
# 2. M4-1 Fail-Closed Authorization Tests
# --------------------------------------------------------------------------- #


def test_m4_1_absent_viewer_authorized_project_ids_rejected() -> None:
    inputs = _load("s19-partial-portfolio", "inputs.json")
    inputs["context"].pop("viewer_authorized_project_ids", None)
    with pytest.raises(InputRejected) as exc:
        rollup_portfolio(inputs)
    assert exc.value.reason == "missing_authorization"


def test_m4_1_only_hidden_project_ids_given_rejected() -> None:
    inputs = _load("s19-partial-portfolio", "inputs.json")
    inputs["context"].pop("viewer_authorized_project_ids", None)
    assert "hidden_project_ids" in inputs["context"]
    with pytest.raises(InputRejected) as exc:
        rollup_portfolio(inputs)
    assert exc.value.reason == "missing_authorization"


def test_m4_1_empty_viewer_authorized_project_ids_rejected() -> None:
    inputs = _load("s19-partial-portfolio", "inputs.json")
    inputs["context"]["viewer_authorized_project_ids"] = []
    with pytest.raises(InputRejected) as exc:
        rollup_portfolio(inputs)
    assert exc.value.reason == "no_authorized_projects"


def test_m4_1_non_list_viewer_authorized_project_ids_rejected() -> None:
    inputs = _load("s19-partial-portfolio", "inputs.json")
    inputs["context"]["viewer_authorized_project_ids"] = "proj-public-frontend"
    with pytest.raises(InputRejected) as exc:
        rollup_portfolio(inputs)
    assert exc.value.reason == "missing_authorization"


def test_m4_1_hidden_project_ids_does_not_grant_or_deny_by_itself() -> None:
    inputs = _load("s19-partial-portfolio", "inputs.json")
    # Even if listed in hidden_project_ids, authorization in viewer_authorized_project_ids governs
    inputs["context"]["hidden_project_ids"] = ["proj-public-frontend"]
    out = rollup_portfolio(inputs).to_dict()
    assert "proj-public-frontend" in out["authorized_project_ids"]


def test_m4_1_differential_hidden_projects_byte_identical_except_partial() -> None:
    # Starting from a non-partial portfolio where all projects are authorized:
    inputs = _load_with_auth("s10-missing-budget")
    base_res = rollup_portfolio(inputs).to_dict()
    assert base_res["partial"] is False

    # Adding a hidden project (not in viewer_authorized_project_ids) changes partial False -> True
    v = copy.deepcopy(inputs)
    v["context"]["portfolio"]["projects"].append(_hidden_project(1))
    v_res = rollup_portfolio(v).to_dict()
    assert v_res["partial"] is True

    # But every other field is 100% byte-identical
    base_res_copy = dict(base_res)
    v_res_copy = dict(v_res)
    base_res_copy.pop("partial")
    v_res_copy.pop("partial")
    assert _jcs(v_res_copy) == _jcs(base_res_copy)


# --------------------------------------------------------------------------- #
# 3. DG1-C5 / C24: single ownership
# --------------------------------------------------------------------------- #

GW_CRITERION = {
    "criterion_id": "AC-GW-001",
    "deliverable_id": "DEL-GW-001",
    "owner_project_id": "proj-shared-gw",
    "weight": 3.0,
    "required": True,
    "verification_refs": [{"kind": "test_id", "ref": "test_gateway_routing"}],
    "previous_status": None,
}

GW_EVIDENCE = {
    "evidence_id": "ev-gw-001",
    "trust_class": "verifier_observed",
    "revision": "rev-cons-a-001",
    "observed_at": "2026-11-05T11:56:00Z",
    "test_id": "test_gateway_routing",
    "covered_test_ids": ["test_gateway_routing"],
    "result": "passed",
    "artifact_path": None,
}


def _with_gw_criteria_in_consumer(inputs: dict[str, Any]) -> dict[str, Any]:
    moved = copy.deepcopy(inputs)
    moved["baseline"]["criteria"].append(copy.deepcopy(GW_CRITERION))
    moved["evidence"].append(copy.deepcopy(GW_EVIDENCE))  # even a verified copy must not count
    return moved


def _consumer_only(inputs: dict[str, Any]) -> dict[str, Any]:
    view = copy.deepcopy(inputs)
    view["context"]["viewer_authorized_project_ids"] = ["proj-consumer-a"]
    return view


def test_dg1_c5_moving_shared_criteria_into_consumer_keeps_consumer_total_weight() -> None:
    base = _load_with_auth("s08-shared-component")
    moved = _with_gw_criteria_in_consumer(base)

    # The criterion reducer does see the copied criterion ...
    crit = assess_criteria(
        baseline=moved["baseline"],
        claims=moved["claims"],
        evidence=moved["evidence"],
        waivers=moved["waivers"],
        assessed_revision=moved["assessed_revision"],
        now=moved["now"],
    )
    assert any(c.owner_project_id == "proj-shared-gw" for c in crit)

    # ... but the consumer's rollup denominator ignores it.
    before = rollup_portfolio(_consumer_only(base)).to_dict()
    after = rollup_portfolio(_consumer_only(moved)).to_dict()
    assert before["progress"]["total_weight"] == 2.0
    assert after["progress"]["total_weight"] == before["progress"]["total_weight"]
    assert after["progress"]["criteria_total"] == before["progress"]["criteria_total"] == 2
    assert "DEL-GW-001" not in after["distinct_deliverable_ids"]
    assert _jcs(after) == _jcs(before)

    # Portfolio view: the shared deliverable is still counted exactly once (8.0, 5 deliverables).
    expected = _load("s08-shared-component", "expected_rollup.json")
    full_after = rollup_portfolio(moved).to_dict()
    assert _jcs(full_after) == _jcs(expected)
    assert full_after["distinct_deliverable_ids"].count("DEL-GW-001") == 1


def test_dg1_c5_dependency_refs_never_add_weight() -> None:
    base = _load_with_auth("s08-shared-component")
    extra = copy.deepcopy(base)
    extra["dependency_refs"] = [
        {
            "deliverable_id": "DEL-GW-001",
            "owner_project_id": "proj-shared-gw",
            "observed_status": "verified",
        },
        {
            "deliverable_id": "DEL-B-001",
            "owner_project_id": "proj-consumer-b",
            "observed_status": "contradicted",
        },
        {
            "deliverable_id": "DEL-X-404",
            "owner_project_id": "proj-not-in-portfolio",
            "observed_status": "verified",
        },
    ]
    assert _jcs(rollup_portfolio(extra).to_dict()) == _jcs(rollup_portfolio(base).to_dict())


def test_dg1_c5_shared_owner_hidden_means_not_counted_and_not_leaked() -> None:
    view = _consumer_only(_with_gw_criteria_in_consumer(_load_with_auth("s08-shared-component")))
    out = json.dumps(rollup_portfolio(view).to_dict())
    assert "DEL-GW-001" not in out and "proj-shared-gw" not in out


def test_deliverable_claimed_by_two_visible_projects_is_rejected() -> None:
    # Consumer re-declares the shared deliverable as its own criterion.
    inputs = _load_with_auth("s08-shared-component")
    owned_copy = dict(GW_CRITERION, owner_project_id="proj-consumer-a")
    inputs["baseline"]["criteria"].append(owned_copy)
    with pytest.raises(InputRejected) as exc:
        rollup_portfolio(inputs)
    assert exc.value.reason == "deliverable_multiple_owners"

    # Consumer B lists the shared deliverable among its own context deliverables.
    inputs2 = _load_with_auth("s08-shared-component")
    inputs2["context"]["portfolio"]["projects"][2]["deliverables"].append(
        {"deliverable_id": "DEL-GW-001", "weight": 3.0, "verified": True}
    )
    with pytest.raises(InputRejected) as exc2:
        rollup_portfolio(inputs2)
    assert exc2.value.reason == "deliverable_multiple_owners"


# --------------------------------------------------------------------------- #
# 4. C43 / s19 confidentiality
# --------------------------------------------------------------------------- #


def _hidden_tokens(inputs: dict[str, Any]) -> set[str]:
    ctx = inputs["context"]
    visible = set(ctx["viewer_authorized_project_ids"])
    tokens = set(ctx.get("hidden_project_ids", []))
    for p in ctx["portfolio"]["projects"]:
        if p["project_id"] in visible:
            continue
        tokens.add(p["project_id"])
        if p.get("owner"):
            tokens.add(p["owner"])
        tokens.update(d["deliverable_id"] for d in p.get("deliverables", []))
    return tokens


def test_s19_hidden_identifiers_absent_from_serialized_output() -> None:
    inputs = _load("s19-partial-portfolio", "inputs.json")
    result = rollup_portfolio(inputs)
    tokens = _hidden_tokens(inputs)
    assert {
        "proj-secret-analytics",
        "proj-classified-ml",
        "Team Internal Intel",
        "DEL-ML-001",
    } <= tokens
    for serialized in (
        _jcs(result.to_dict()).decode("utf-8"),
        json.dumps(result.to_dict()),
        repr(result),
    ):
        hits = [t for t in tokens if t in serialized]
        assert hits == [], f"hidden tokens leaked: {hits}"
    assert result.partial is True
    # Hidden-only weights/costs are not reflected in any aggregate.
    d = result.to_dict()
    assert d["progress"]["total_weight"] == 5.0
    assert d["cost_usd"] == 12000.0 and d["budget_usd"] == 15000.0


def _hidden_project(i: int) -> dict[str, Any]:
    return {
        "project_id": f"proj-hidden-{i}",
        "owner": f"Hidden Team {i}",
        "deliverables": [
            {"deliverable_id": f"DEL-HID-{i}", "weight": 7.0 + i, "verified": i % 2 == 0}
        ],
        "budget_usd": None if i % 3 == 0 else 1000.0 * i,
        "cost_usd": None if i % 3 == 0 else 900.0 * i,
        "cost_state": "unknown" if i % 3 == 0 else "known",
    }


def _variant(inputs: dict[str, Any], add: int = 0, drop: tuple[str, ...] = ()) -> dict[str, Any]:
    v = copy.deepcopy(inputs)
    ctx = v["context"]
    ctx["portfolio"]["projects"] = [
        p for p in ctx["portfolio"]["projects"] if p["project_id"] not in drop
    ]
    if "hidden_project_ids" in ctx:
        ctx["hidden_project_ids"] = [h for h in ctx["hidden_project_ids"] if h not in drop]
    for i in range(add):
        ctx["portfolio"]["projects"].append(_hidden_project(i))
        if "hidden_project_ids" in ctx:
            ctx["hidden_project_ids"].append(f"proj-hidden-{i}")
    return v


@pytest.mark.parametrize(
    "add,drop",
    [
        (0, ("proj-classified-ml",)),
        (0, ("proj-secret-analytics",)),
        (1, ("proj-secret-analytics", "proj-classified-ml")),
        (1, ()),
        (5, ()),
        (25, ("proj-classified-ml",)),
    ],
)
def test_s19_differential_hidden_projects_byte_identical(add: int, drop: tuple[str, ...]) -> None:
    base = _load("s19-partial-portfolio", "inputs.json")
    reference = _jcs(rollup_portfolio(base).to_dict())
    variant = _variant(base, add=add, drop=drop)
    assert _jcs(rollup_portfolio(variant).to_dict()) == reference


def test_s19_hidden_projects_cannot_influence_output_or_errors() -> None:
    """Hidden data that would be invalid or conflicting if visible is never examined."""
    base = _load("s19-partial-portfolio", "inputs.json")
    reference = _jcs(rollup_portfolio(base).to_dict())
    v = copy.deepcopy(base)
    projects = v["context"]["portfolio"]["projects"]
    secret = next(p for p in projects if p["project_id"] == "proj-secret-analytics")
    secret["deliverables"] = [
        {
            "deliverable_id": "DEL-FE-001",
            "weight": -1.0,
            "verified": True,
        },  # duplicates a visible id + bad weight
        {"deliverable_id": "DEL-API-001", "weight": float("nan"), "verified": False},
    ]
    secret["cost_usd"] = -5.0
    secret["cost_state"] = "unknown"
    projects.append(
        {"project_id": "proj-secret-analytics", "owner": "dup"}
    )  # duplicate hidden entry
    projects.append({"owner": "no id at all"})  # unauthorizable entry
    # Viewer lists an id outside the portfolio (ghost project)
    v["context"]["viewer_authorized_project_ids"] = list(
        base["context"]["viewer_authorized_project_ids"]
    ) + ["proj-ghost"]
    assert _jcs(rollup_portfolio(v).to_dict()) == reference


def test_partial_flag_semantics() -> None:
    base = _load("s19-partial-portfolio", "inputs.json")
    none_hidden = _variant(base, drop=("proj-secret-analytics", "proj-classified-ml"))
    assert rollup_portfolio(none_hidden).partial is False
    # Unauthorized via viewer list alone (no hidden_project_ids) is still partial.
    only_viewer = copy.deepcopy(base)
    only_viewer["context"].pop("hidden_project_ids", None)
    assert rollup_portfolio(only_viewer).to_dict() == rollup_portfolio(base).to_dict()


def test_no_authorized_project_rejected_without_detail() -> None:
    inputs = _load("s19-partial-portfolio", "inputs.json")
    inputs["context"]["viewer_authorized_project_ids"] = []
    with pytest.raises(InputRejected) as exc:
        rollup_portfolio(inputs)
    assert exc.value.reason == "no_authorized_projects"
    for token in _hidden_tokens(_load("s19-partial-portfolio", "inputs.json")):
        assert token not in str(exc.value)


def test_assessed_project_not_visible_uses_no_criteria() -> None:
    inputs = _load("s19-partial-portfolio", "inputs.json")
    inputs["context"]["viewer_authorized_project_ids"] = ["proj-public-api"]
    out = rollup_portfolio(inputs).to_dict()
    assert out["authorized_project_ids"] == ["proj-public-api"]
    assert out["distinct_deliverable_ids"] == ["DEL-API-001"]
    assert "proj-public-frontend" not in json.dumps(out)


# --------------------------------------------------------------------------- #
# 5. C11 / s10 cost state
# --------------------------------------------------------------------------- #


def test_s10_missing_cost_is_unknown_never_zero() -> None:
    out = rollup_portfolio(_load_with_auth("s10-missing-budget")).to_dict()
    assert out["cost_state"] == "unknown"
    assert out["cost_usd"] is None and out["cost_usd"] != 0
    assert out["budget_usd"] is None


@pytest.mark.parametrize(
    "patch,expected_state,expected_cost,expected_budget",
    [
        (
            {"cost_state": "known", "cost_usd": 100.0, "budget_usd": 200.0},
            "known",
            4600.0,
            5200.0,
        ),
        ({"cost_state": "known", "cost_usd": None, "budget_usd": 200.0}, "unknown", None, 5200.0),
        ({"cost_state": "unknown", "cost_usd": 100.0, "budget_usd": None}, "unknown", None, None),
        ({"cost_usd": 100.0, "budget_usd": 200.0}, "unknown", None, 5200.0),  # cost_state absent
        (
            {"cost_state": "known", "cost_usd": 0.0, "budget_usd": 0.0},
            "known",
            4500.0,
            5000.0,
        ),  # real zero ok
    ],
)
def test_cost_state_matrix(
    patch: dict[str, Any],
    expected_state: str,
    expected_cost: float | None,
    expected_budget: float | None,
) -> None:
    inputs = _load_with_auth("s10-missing-budget")
    core = inputs["context"]["portfolio"]["projects"][0]
    for k in ("cost_state", "cost_usd", "budget_usd"):
        core.pop(k, None)
    core.update(patch)
    out = rollup_portfolio(inputs).to_dict()
    assert (out["cost_state"], out["cost_usd"], out["budget_usd"]) == (
        expected_state,
        expected_cost,
        expected_budget,
    )


@pytest.mark.parametrize("bad", [-1.0, float("inf"), "100", True])
def test_invalid_visible_cost_rejected(bad: Any) -> None:
    inputs = _load_with_auth("s10-missing-budget")
    inputs["context"]["portfolio"]["projects"][1]["cost_usd"] = bad
    with pytest.raises(InputRejected) as exc:
        rollup_portfolio(inputs)
    assert exc.value.reason == "invalid_cost"


@pytest.mark.parametrize("bad", [0, -2.0, None, "3", float("nan"), False])
def test_invalid_visible_weight_rejected(bad: Any) -> None:
    inputs = _load_with_auth("s10-missing-budget")
    inputs["context"]["portfolio"]["projects"][1]["deliverables"][0]["weight"] = bad
    with pytest.raises(InputRejected) as exc:
        rollup_portfolio(inputs)
    assert exc.value.reason == "invalid_weight"


def test_missing_portfolio_context_rejected() -> None:
    inputs = _load("s01-claim-done-test-failed", "inputs.json")
    with pytest.raises(InputRejected) as exc:
        rollup_portfolio(inputs)
    assert exc.value.reason == "missing_portfolio_context"


# --------------------------------------------------------------------------- #
# 6. Denominator rule
# --------------------------------------------------------------------------- #


def test_all_waived_has_no_denominator_and_no_percent(
    rollup_validator: jsonschema.Draft202012Validator,
) -> None:
    inputs = _load_with_auth("s08-shared-component")
    inputs["context"]["viewer_authorized_project_ids"] = ["proj-consumer-a"]
    inputs["waivers"].append(
        dict(inputs["waivers"][0], waiver_id="waiver-cons-a-002", criterion_id="AC-A-001")
    )
    out = rollup_portfolio(inputs).to_dict()
    rollup_validator.validate(out)
    assert out["progress"] == {
        "basis": "baseline_weighted",
        "verified_weight": None,
        "total_weight": None,
        "percent": None,
        "criteria_total": 2,
        "criteria_verified": 0,
    }


def test_no_deliverables_basis_none(
    rollup_validator: jsonschema.Draft202012Validator,
) -> None:
    inputs = _load_with_auth("s10-missing-budget")
    inputs["context"]["viewer_authorized_project_ids"] = ["proj-ui-app"]
    inputs["context"]["portfolio"]["projects"][1]["deliverables"] = []
    out = rollup_portfolio(inputs).to_dict()
    rollup_validator.validate(out)
    assert out["progress"]["basis"] == "none"
    assert out["progress"]["percent"] is None and out["progress"]["total_weight"] is None


# --------------------------------------------------------------------------- #
# 7. Hypothesis properties
# --------------------------------------------------------------------------- #

NOW = "2026-11-05T12:00:00Z"
REV = "rev-prop-001"


@st.composite
def portfolios(draw: st.DrawFn) -> dict[str, Any]:
    n_projects = draw(st.integers(min_value=1, max_value=6))
    projects: list[dict[str, Any]] = []
    for pi in range(n_projects):
        n_del = draw(st.integers(min_value=0, max_value=4))
        cost_known = draw(st.booleans())
        projects.append(
            {
                "project_id": f"proj-{pi}",
                "owner": f"Team {pi}",
                "deliverables": [
                    {
                        "deliverable_id": f"DEL-{pi}-{di}",
                        "weight": draw(st.floats(min_value=0.1, max_value=50.0, allow_nan=False)),
                        "verified": draw(st.booleans()),
                    }
                    for di in range(n_del)
                ],
                "budget_usd": draw(st.one_of(st.none(), st.floats(min_value=0, max_value=1e6))),
                "cost_usd": draw(st.floats(min_value=0, max_value=1e6)) if cost_known else None,
                "cost_state": "known" if cost_known else "unknown",
            }
        )
    ids = [p["project_id"] for p in projects]
    visible = draw(st.lists(st.sampled_from(ids), min_size=1, unique=True))
    hidden = sorted(set(ids) - set(visible))
    assessed = draw(st.sampled_from(ids))
    owner = projects[ids.index(assessed)]

    # Assessed project baseline: one criterion per owned deliverable plus foreign
    # copies of other projects' deliverables (DG1-C5: must never count here).
    criteria: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for d in owner["deliverables"]:
        criteria.append(_criterion(d["deliverable_id"], assessed, d["weight"]))
        if d["verified"]:
            evidence.append(_passing_evidence(d["deliverable_id"]))
    for p in projects:
        if p["project_id"] == assessed:
            continue
        for d in p["deliverables"]:
            if draw(st.booleans()):
                criteria.append(_criterion(d["deliverable_id"], p["project_id"], d["weight"]))
                evidence.append(_passing_evidence(d["deliverable_id"]))
    use_baseline = draw(st.booleans()) and bool(criteria)

    return {
        "fixture_id": "prop-rollup",
        "project_id": assessed,
        "snapshot_refs": ["snap-prop"],
        "snapshot_seq": 1,
        "basis_seq": 1,
        "previous_assessment_id": None,
        "assessed_revision": REV,
        "now": NOW,
        "evidence_ttl_days": 7,
        "baseline": {"baseline_revision": 1, "criteria": criteria} if use_baseline else None,
        "claims": [],
        "evidence": evidence,
        "waivers": [],
        "context": {
            "portfolio": {"portfolio_id": "port-prop", "projects": projects},
            "viewer_authorized_project_ids": sorted(visible),
            "hidden_project_ids": hidden,
        },
    }


def _criterion(did: str, owner: str, weight: float) -> dict[str, Any]:
    return {
        "criterion_id": f"AC-{did}",
        "deliverable_id": did,
        "owner_project_id": owner,
        "weight": weight,
        "required": True,
        "verification_refs": [{"kind": "test_id", "ref": f"t-{did}"}],
        "previous_status": None,
    }


def _passing_evidence(did: str) -> dict[str, Any]:
    return {
        "evidence_id": f"ev-{did}",
        "trust_class": "verifier_observed",
        "revision": REV,
        "observed_at": "2026-11-05T11:55:00Z",
        "test_id": f"t-{did}",
        "covered_test_ids": [f"t-{did}"],
        "result": "passed",
        "artifact_path": None,
    }


def _expected_total(inputs: dict[str, Any]) -> float | None:
    ctx = inputs["context"]
    visible = set(ctx["viewer_authorized_project_ids"])
    weights: dict[str, float] = {}  # distinct owner deliverable -> weight
    for p in ctx["portfolio"]["projects"]:
        if p["project_id"] in visible:
            for d in p["deliverables"]:
                assert d["deliverable_id"] not in weights
                weights[d["deliverable_id"]] = d["weight"]
    if not weights:
        return None
    return float(round(math.fsum(weights.values()), 6))


@settings(max_examples=200, deadline=None)
@given(portfolios())
def test_property_total_weight_is_sum_of_distinct_owner_deliverables(
    inputs: dict[str, Any],
) -> None:
    schema = json.loads((_fixtures_dir() / "rollup.schema.json").read_text(encoding="utf-8"))
    result = rollup_portfolio(inputs)
    out = result.to_dict()
    jsonschema.Draft202012Validator(schema).validate(out)

    ctx = inputs["context"]
    visible = set(ctx["viewer_authorized_project_ids"])
    assert out["progress"]["total_weight"] == _expected_total(inputs)
    assert out["authorized_project_ids"] == sorted(visible)
    assert out["partial"] == (len(visible) < len(ctx["portfolio"]["projects"]))
    assert out["distinct_deliverables_count"] == len(set(out["distinct_deliverable_ids"]))

    # Percent only with a denominator.
    if out["progress"]["percent"] is not None:
        assert out["progress"]["total_weight"] is not None and out["progress"]["total_weight"] > 0
    else:
        assert out["progress"]["total_weight"] is None

    # No hidden token anywhere in the output.
    serialized = _jcs(out).decode("utf-8")
    for p in ctx["portfolio"]["projects"]:
        if p["project_id"] not in visible:
            assert f'"{p["project_id"]}"' not in serialized
            for d in p["deliverables"]:
                assert f'"{d["deliverable_id"]}"' not in serialized

    # Cost never defaults to zero.
    if out["cost_state"] == "unknown":
        assert out["cost_usd"] is None


@settings(max_examples=100, deadline=None)
@given(portfolios(), st.randoms(use_true_random=False))
def test_property_order_independent(inputs: dict[str, Any], rng: random.Random) -> None:
    shuffled = _shuffle(inputs, rng)
    assert _sha(rollup_portfolio(shuffled).to_dict()) == _sha(rollup_portfolio(inputs).to_dict())


@settings(max_examples=100, deadline=None)
@given(portfolios(), st.integers(min_value=1, max_value=10))
def test_property_adding_hidden_projects_is_invisible(inputs: dict[str, Any], extra: int) -> None:
    reference = rollup_portfolio(inputs).to_dict()
    v = copy.deepcopy(inputs)
    for i in range(extra):
        v["context"]["portfolio"]["projects"].append(_hidden_project(100 + i))
        if "hidden_project_ids" in v["context"]:
            v["context"]["hidden_project_ids"].append(f"proj-hidden-{100 + i}")
    produced = rollup_portfolio(v).to_dict()
    # Adding projects not in viewer_authorized_project_ids leaves output byte-identical except partial
    ref_copy = dict(reference)
    prod_copy = dict(produced)
    ref_copy.pop("partial")
    prod_copy.pop("partial")
    assert _jcs(prod_copy) == _jcs(ref_copy)


# --------------------------------------------------------------------------- #
# 8. Determinism and purity
# --------------------------------------------------------------------------- #


def _shuffle(inputs: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    s = copy.deepcopy(inputs)
    for key in ("claims", "evidence", "waivers", "dependency_refs"):
        if key in s:
            rng.shuffle(s[key])
    if s.get("baseline"):
        rng.shuffle(s["baseline"]["criteria"])
    ctx = s["context"]
    rng.shuffle(ctx["portfolio"]["projects"])
    for p in ctx["portfolio"]["projects"]:
        rng.shuffle(p.get("deliverables", []))
    for key in ("viewer_authorized_project_ids", "hidden_project_ids"):
        if key in ctx:
            rng.shuffle(ctx[key])
    return s


@pytest.mark.parametrize("fixture", ROLLUP_FIXTURES)
@pytest.mark.parametrize("seed", [1, 7, 42, 2026])
def test_determinism_shuffled_inputs_identical_jcs_sha256(fixture: str, seed: int) -> None:
    inputs = _load_with_auth(fixture)
    h1 = _sha(rollup_portfolio(inputs).to_dict())
    h2 = _sha(rollup_portfolio(_shuffle(inputs, random.Random(seed))).to_dict())
    h3 = _sha(rollup_portfolio(inputs).to_dict())
    assert h1 == h2 == h3


_HOOK_INSTALLED = False
_HOOK_ACTIVE = False
_HOOK_EVENTS: list[tuple[str, tuple[Any, ...]]] = []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if _HOOK_ACTIVE and event.startswith(("open", "socket", "subprocess", "os.")):
        _HOOK_EVENTS.append((event, args))


def test_rollup_runtime_audit_hook_clean() -> None:
    """rollup_portfolio performs no open/socket/subprocess/os.* events."""
    global _HOOK_INSTALLED, _HOOK_ACTIVE
    if not _HOOK_INSTALLED:
        sys.addaudithook(_audit)
        _HOOK_INSTALLED = True
    corpus = [_load_with_auth(f) for f in ROLLUP_FIXTURES]
    _HOOK_EVENTS.clear()
    _HOOK_ACTIVE = True
    try:
        outputs = [rollup_portfolio(inp).to_dict() for inp in corpus]
    finally:
        _HOOK_ACTIVE = False
    assert len(outputs) == 3
    assert _HOOK_EVENTS == [], f"forbidden runtime events: {_HOOK_EVENTS}"
