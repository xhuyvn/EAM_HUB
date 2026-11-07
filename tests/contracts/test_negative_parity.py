"""Negative-parity tests (SC-01 / P0-WP2).

Builds tests dynamically from tools/validate_package.py so that any new negative
case added there is automatically tested and cannot be silently missed.
Asserts that every negative case is rejected by Pydantic v2 models or pure rules
with an appropriate ContractViolation or ValidationError.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from eap.contracts.models import get_model_for_schema
from eap.contracts.rules import (
    ContractViolation,
    check_approval_pair,
    check_location_set,
    check_operation_key_reuse,
    semantic_checks,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
import validate_package  # type: ignore[import-not-found]


def validate_against_model_and_rules(kind: str, data: Any) -> None:
    model_cls = get_model_for_schema(kind)
    instance = model_cls.model_validate(data)
    semantic_checks(kind, instance)


def test_all_validator_negatives_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    events_raw = (REPO_ROOT / "examples" / "events.ndjson").read_text(encoding="utf-8")
    events = [json.loads(line) for line in events_raw.splitlines() if line.strip()]

    # 1. 43 negatives from build_negatives(events)
    validator_negatives = validate_package.build_negatives(events)
    assert len(validator_negatives) == 43, f"Expected 43 negatives, found {len(validator_negatives)}"

    rejected_cases: list[str] = []
    for name, kind, data in validator_negatives:
        with pytest.raises((ValidationError, ContractViolation, ValueError)):
            validate_against_model_and_rules(kind, data)
        rejected_cases.append(name)

    # 2. approval pair negatives from validate_package.build_pair_negatives()
    proposal = json.loads((REPO_ROOT / "templates" / "actions" / "action-proposal.example.json").read_text(encoding="utf-8"))
    decision = json.loads((REPO_ROOT / "examples" / "approval-decision.json").read_text(encoding="utf-8"))

    pair_negatives = validate_package.build_pair_negatives(decision)
    for name, d in pair_negatives:
        with pytest.raises((ContractViolation, ValueError)):
            check_approval_pair(proposal, d)
        rejected_cases.append(name)

    # 3. Operation key conflict negative from validate_package.build_operation_key_negatives()
    retry = json.loads((REPO_ROOT / "examples" / "action-proposal-retry.json").read_text(encoding="utf-8"))
    for name, pair in validate_package.build_operation_key_negatives(proposal, retry):
        with pytest.raises((ContractViolation, ValueError)):
            check_operation_key_reuse(pair)
        rejected_cases.append(name)

    # 4. Location set negatives from validate_package.build_location_negatives()
    locs = json.loads((REPO_ROOT / "examples" / "locations-monorepo.json").read_text(encoding="utf-8"))
    for name, ls_copy in validate_package.build_location_negatives(locs):
        with pytest.raises((ContractViolation, ValueError)):
            check_location_set(ls_copy)
        rejected_cases.append(name)

    total_n = len(rejected_cases)
    print(f"\n--- NEGATIVE PARITY COVERAGE ({total_n}/{total_n} cases rejected) ---")
    for i, name in enumerate(rejected_cases, 1):
        print(f"  {i:02d}. [REJECTED OK] {name}")
    print(f"Total validator negative cases tested: {total_n}")
    assert total_n == 50
