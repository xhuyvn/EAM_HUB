"""Differential testing between JSON Schema and Pydantic v2 contract models.

For every fixture in FIXTURES + events.ndjson, generates mutations across all nodes:
  - wrong type (including bool/numeric-string for ints, strings for bools, lists/dicts)
  - empty string
  - empty list
  - negative number / zero (where forbidden)
  - null
  - extra key
  - deleted key
  - invalid enum
and asserts: model accepts => jsonschema accepts (0 violations).
Prints total mutation count and violation count.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from pydantic import ValidationError

from eap.contracts.models import get_model_for_schema

REPO_ROOT = Path(__file__).resolve().parents[2]

FIXTURES: dict[str, str] = {
    "templates/new-agent/agent.json": "agent",
    "templates/existing-agent/agent.json": "agent",
    "templates/admin/deployment-binding.example.json": "binding",
    "templates/admin/existing-observe-binding.example.json": "binding",
    "templates/actions/action-proposal.example.json": "action-proposal",
    "templates/phase2/experiment.example.json": "experiment",
    "examples/task-request.json": "task-request",
    "examples/task-envelope.json": "task-envelope",
    "examples/result.json": "result",
    "examples/ppm-input.json": "ppm-input",
    "examples/ppm-output.json": "ppm-output",
    "examples/hud-projection.json": "hud-projection",
    "examples/approval-decision.json": "approval-decision",
    "examples/location-registration.json": "location-registration",
    "examples/run-command.json": "run-command",
}


def load_validators() -> dict[str, Draft202012Validator]:
    validators: dict[str, Draft202012Validator] = {}
    for p in (REPO_ROOT / "contracts").glob("*.schema.json"):
        schema = json.loads(p.read_text(encoding="utf-8"))
        validators[p.name.removesuffix(".schema.json")] = Draft202012Validator(
            schema, format_checker=FormatChecker()
        )
    return validators


def _apply_set(data_copy: Any, parent: tuple[str | int, ...], k: str | int, val: Any) -> Any:
    target = data_copy
    for seg in parent:
        target = target[seg]
    target[k] = val
    return data_copy


def _apply_del(data_copy: Any, parent: tuple[str | int, ...], k: str | int) -> Any:
    target = data_copy
    for seg in parent:
        target = target[seg]
    if isinstance(target, dict):
        del target[k]
    elif isinstance(target, list):
        assert isinstance(k, int)
        del target[k]
    return data_copy


def generate_mutations(data: Any) -> list[tuple[str, tuple[str | int, ...], Any]]:
    """Walk data tree and yield mutated copies: (mutation_type, path, mutated_data)."""
    mutations: list[tuple[str, tuple[str | int, ...], Any]] = []

    def get_paths(obj: Any, current_path: tuple[str | int, ...] = ()) -> list[tuple[tuple[str | int, ...], Any]]:
        paths = [(current_path, obj)]
        if isinstance(obj, dict):
            for k, v in obj.items():
                paths.extend(get_paths(v, current_path + (k,)))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                paths.extend(get_paths(v, current_path + (i,)))
        return paths

    paths = get_paths(data)
    for p, val in paths:
        if not p:
            if isinstance(val, dict):
                d = copy.deepcopy(data)
                d["__extra_root_key__"] = "unexpected"
                mutations.append(("extra_key_root", (), d))
            continue
        parent_path = p[:-1]
        key = p[-1]

        # 1. Deleted key/item
        mutations.append(("del_key", p, _apply_del(copy.deepcopy(data), parent_path, key)))

        # 2. Null value
        if val is not None:
            mutations.append(("null", p, _apply_set(copy.deepcopy(data), parent_path, key, None)))

        # 3. Object / Dict mutations
        if isinstance(val, dict):
            d = copy.deepcopy(data)
            target = d
            for seg in p:
                target = target[seg]
            target["__extra_key__"] = "unexpected"
            mutations.append(("extra_key", p, d))
            mutations.append(("wrong_type_bool", p, _apply_set(copy.deepcopy(data), parent_path, key, True)))
            mutations.append(("wrong_type_str", p, _apply_set(copy.deepcopy(data), parent_path, key, "string")))
            mutations.append(("wrong_type_int", p, _apply_set(copy.deepcopy(data), parent_path, key, 123)))
            mutations.append(("wrong_type_list", p, _apply_set(copy.deepcopy(data), parent_path, key, [])))

        # 4. List mutations
        elif isinstance(val, list):
            if len(val) > 0:
                mutations.append(("empty_list", p, _apply_set(copy.deepcopy(data), parent_path, key, [])))
            mutations.append(("wrong_type_bool", p, _apply_set(copy.deepcopy(data), parent_path, key, True)))
            mutations.append(("wrong_type_str", p, _apply_set(copy.deepcopy(data), parent_path, key, "string")))
            mutations.append(("wrong_type_int", p, _apply_set(copy.deepcopy(data), parent_path, key, 123)))
            mutations.append(("wrong_type_dict", p, _apply_set(copy.deepcopy(data), parent_path, key, {})))
            mutations.append(("list_append_empty_str", p, _apply_set(copy.deepcopy(data), parent_path, key, val + [""])))
            mutations.append(("list_append_none", p, _apply_set(copy.deepcopy(data), parent_path, key, val + [None])))
            mutations.append(("list_append_int", p, _apply_set(copy.deepcopy(data), parent_path, key, val + [123])))
            mutations.append(("list_append_bool", p, _apply_set(copy.deepcopy(data), parent_path, key, val + [True])))

        # 5. Bool mutations
        elif isinstance(val, bool):
            mutations.append(("wrong_type_int_1", p, _apply_set(copy.deepcopy(data), parent_path, key, 1)))
            mutations.append(("wrong_type_int_0", p, _apply_set(copy.deepcopy(data), parent_path, key, 0)))
            mutations.append(("wrong_type_str", p, _apply_set(copy.deepcopy(data), parent_path, key, "true")))
            mutations.append(("wrong_type_list", p, _apply_set(copy.deepcopy(data), parent_path, key, [])))
            mutations.append(("wrong_type_dict", p, _apply_set(copy.deepcopy(data), parent_path, key, {})))

        # 6. Integer mutations (not bool)
        elif isinstance(val, int) and not isinstance(val, bool):
            mutations.append(("wrong_type_bool_true", p, _apply_set(copy.deepcopy(data), parent_path, key, True)))
            mutations.append(("wrong_type_bool_false", p, _apply_set(copy.deepcopy(data), parent_path, key, False)))
            mutations.append(("wrong_type_str_numeric", p, _apply_set(copy.deepcopy(data), parent_path, key, "2")))
            mutations.append(("wrong_type_str_alpha", p, _apply_set(copy.deepcopy(data), parent_path, key, "abc")))
            mutations.append(("wrong_type_float", p, _apply_set(copy.deepcopy(data), parent_path, key, 1.5)))
            mutations.append(("wrong_type_list", p, _apply_set(copy.deepcopy(data), parent_path, key, [])))
            mutations.append(("wrong_type_dict", p, _apply_set(copy.deepcopy(data), parent_path, key, {})))
            mutations.append(("negative_number", p, _apply_set(copy.deepcopy(data), parent_path, key, -1)))
            mutations.append(("zero_number", p, _apply_set(copy.deepcopy(data), parent_path, key, 0)))

        # 7. Float mutations
        elif isinstance(val, float):
            mutations.append(("wrong_type_bool", p, _apply_set(copy.deepcopy(data), parent_path, key, True)))
            mutations.append(("wrong_type_str_numeric", p, _apply_set(copy.deepcopy(data), parent_path, key, "2.5")))
            mutations.append(("wrong_type_str_alpha", p, _apply_set(copy.deepcopy(data), parent_path, key, "abc")))
            mutations.append(("wrong_type_list", p, _apply_set(copy.deepcopy(data), parent_path, key, [])))
            mutations.append(("wrong_type_dict", p, _apply_set(copy.deepcopy(data), parent_path, key, {})))
            mutations.append(("negative_number", p, _apply_set(copy.deepcopy(data), parent_path, key, -1.0)))
            mutations.append(("zero_number", p, _apply_set(copy.deepcopy(data), parent_path, key, 0.0)))

        # 8. String mutations
        elif isinstance(val, str):
            if val != "":
                mutations.append(("empty_str", p, _apply_set(copy.deepcopy(data), parent_path, key, "")))
            mutations.append(("wrong_type_bool", p, _apply_set(copy.deepcopy(data), parent_path, key, True)))
            mutations.append(("wrong_type_int", p, _apply_set(copy.deepcopy(data), parent_path, key, 123)))
            mutations.append(("wrong_type_list", p, _apply_set(copy.deepcopy(data), parent_path, key, [])))
            mutations.append(("wrong_type_dict", p, _apply_set(copy.deepcopy(data), parent_path, key, {})))
            mutations.append(("invalid_enum", p, _apply_set(copy.deepcopy(data), parent_path, key, "__INVALID_ENUM_VALUE__")))

    return mutations


def test_differential_mutation_testing(capsys: pytest.CaptureFixture[str]) -> None:
    validators = load_validators()

    events_raw = (REPO_ROOT / "examples" / "events.ndjson").read_text(encoding="utf-8")
    events = [json.loads(line) for line in events_raw.splitlines() if line.strip()]

    all_targets: list[tuple[str, str, Any]] = [
        (f_path, kind, json.loads((REPO_ROOT / f_path).read_text(encoding="utf-8")))
        for f_path, kind in FIXTURES.items()
    ]
    for i, ev in enumerate(events):
        all_targets.append((f"examples/events.ndjson[{i}]", "event", ev))

    total_mutations = 0
    violations: list[tuple[str, str, tuple[str | int, ...]]] = []
    stricter: list[tuple[str, str, tuple[str | int, ...]]] = []

    for label, kind, data in all_targets:
        muts = generate_mutations(data)
        total_mutations += len(muts)
        model_cls = get_model_for_schema(kind)
        validator = validators[kind]

        for m_type, path, m_data in muts:
            js_valid = validator.is_valid(m_data)
            try:
                model_cls.model_validate(m_data)
                model_valid = True
            except (ValidationError, TypeError, ValueError):
                model_valid = False

            if model_valid and not js_valid:
                violations.append((label, m_type, path))
            elif js_valid and not model_valid:
                stricter.append((label, m_type, path))

    print("\n--- DIFFERENTIAL MUTATION TESTING ---")
    print(f"Total targets tested: {len(all_targets)} (15 fixtures + 11 events)")
    print(f"Total mutations generated: {total_mutations}")
    print(f"Model-looser violations: {len(violations)}")
    print(f"Model-stricter cases: {len(stricter)}")

    if stricter:
        print("\nModel-stricter cases (justification required):")
        for s in stricter:
            print(f"  Target: {s[0]}, mutation: {s[1]}, path: {s[2]}")

    if violations:
        msg = f"Differential testing failed with {len(violations)} model-looser violations!\n"
        for v in violations[:10]:
            msg += f"  Target: {v[0]}, mutation: {v[1]}, path: {v[2]}\n"
        pytest.fail(msg)

    assert len(violations) == 0, f"Expected 0 violations, found {len(violations)}"
