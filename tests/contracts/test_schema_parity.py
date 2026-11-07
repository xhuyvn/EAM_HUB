"""Schema-parity tests (SC-01 / P0-WP2).

Prevents silent drift between contracts/*.schema.json and Pydantic v2 models.
For each of the 16 schemas, asserts that the model's field names, required set,
and enum/const values equal the JSON Schema's (walking nested objects).
"""

from __future__ import annotations

import glob
import json
import os
import types
import typing
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from eap.contracts.models import get_model_for_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "contracts"


def get_literal_values(tp: Any) -> set[Any] | None:
    origin = typing.get_origin(tp)
    if origin is typing.Literal:
        return set(typing.get_args(tp))
    if origin in (typing.Union, types.UnionType):
        args = typing.get_args(tp)
        lits: set[Any] = set()
        has_lit = False
        for a in args:
            if a is type(None):
                lits.add(None)
            else:
                sub = get_literal_values(a)
                if sub is not None:
                    has_lit = True
                    lits.update(sub)
        if has_lit:
            return lits
    return None


def get_nested_model(tp: Any) -> type[BaseModel] | None:
    origin = typing.get_origin(tp)
    if origin is list:
        args = typing.get_args(tp)
        if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
            return args[0]
    if origin in (typing.Union, types.UnionType):
        for a in typing.get_args(tp):
            if isinstance(a, type) and issubclass(a, BaseModel):
                return a
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return tp
    return None


def assert_schema_model_parity(schema_dict: dict[str, Any], model_cls: type[BaseModel], path: str = "") -> None:
    schema_props = set(schema_dict.get("properties", {}).keys())
    model_props = set(model_cls.model_fields.keys())
    diff = schema_props ^ model_props
    assert schema_props == model_props, (
        f"[{path}] Field names mismatch!\n"
        f"  Schema properties ({len(schema_props)}): {sorted(schema_props)}\n"
        f"  Model fields ({len(model_props)}):      {sorted(model_props)}\n"
        f"  Symmetric diff: {sorted(diff)}"
    )

    schema_req = set(schema_dict.get("required", []))
    model_req = {k for k, v in model_cls.model_fields.items() if v.is_required()}
    req_diff = schema_req ^ model_req
    assert schema_req == model_req, (
        f"[{path}] Required fields mismatch!\n"
        f"  Schema required ({len(schema_req)}): {sorted(schema_req)}\n"
        f"  Model required ({len(model_req)}):  {sorted(model_req)}\n"
        f"  Symmetric diff: {sorted(req_diff)}"
    )

    for prop_name, prop_schema in schema_dict.get("properties", {}).items():
        field_info = model_cls.model_fields[prop_name]
        cur_path = f"{path}.{prop_name}" if path else prop_name

        if "const" in prop_schema:
            expected_const = prop_schema["const"]
            lits = get_literal_values(field_info.annotation)
            assert lits == {expected_const}, (
                f"[{cur_path}] Const mismatch: schema const={expected_const!r}, model literal={lits!r}"
            )

        if "enum" in prop_schema:
            expected_enum = set(prop_schema["enum"])
            lits = get_literal_values(field_info.annotation)
            assert lits == expected_enum, (
                f"[{cur_path}] Enum values mismatch!\n"
                f"  Schema enum: {sorted(str(x) for x in expected_enum)}\n"
                f"  Model values: {sorted(str(x) for x in (lits or set()))}\n"
                f"  Diff: {expected_enum ^ (lits or set())}"
            )

        if prop_schema.get("type") == "object" and "properties" in prop_schema:
            nested_cls = get_nested_model(field_info.annotation)
            assert nested_cls is not None, f"[{cur_path}] Expected nested model for object schema"
            assert_schema_model_parity(prop_schema, nested_cls, cur_path)

        if prop_schema.get("type") == "array" and "items" in prop_schema:
            items_schema = prop_schema["items"]
            if items_schema.get("type") == "object" and "properties" in items_schema:
                nested_cls = get_nested_model(field_info.annotation)
                assert nested_cls is not None, f"[{cur_path}[]] Expected nested model for array items schema"
                assert_schema_model_parity(items_schema, nested_cls, f"{cur_path}[]")
            if "enum" in items_schema:
                expected_enum = set(items_schema["enum"])
                origin = typing.get_origin(field_info.annotation)
                if origin is list:
                    item_type = typing.get_args(field_info.annotation)[0]
                    lits = get_literal_values(item_type)
                    assert lits == expected_enum, (
                        f"[{cur_path}[]] Array items enum mismatch: expected {expected_enum}, got {lits}"
                    )

        if "anyOf" in prop_schema:
            for branch in prop_schema["anyOf"]:
                if branch.get("type") == "object" and "properties" in branch:
                    nested_cls = get_nested_model(field_info.annotation)
                    assert nested_cls is not None, f"[{cur_path}] Expected nested model for anyOf object branch"
                    assert_schema_model_parity(branch, nested_cls, cur_path)


ALL_SCHEMA_PATHS = sorted(glob.glob(str(CONTRACTS_DIR / "*.schema.json")))


def test_schema_parity_all_16_schemas(capsys: pytest.CaptureFixture[str]) -> None:
    assert len(ALL_SCHEMA_PATHS) == 16, f"Expected 16 schemas, found {len(ALL_SCHEMA_PATHS)}"
    schema_names = []
    for schema_file in ALL_SCHEMA_PATHS:
        name = os.path.basename(schema_file)
        schema_dict = json.loads(Path(schema_file).read_text(encoding="utf-8"))
        model_cls = get_model_for_schema(name)
        assert_schema_model_parity(schema_dict, model_cls, name)
        schema_names.append(f"{name} -> {model_cls.__name__}")

    print("\n--- SCHEMA PARITY COVERAGE (16/16 schemas) ---")
    for item in schema_names:
        print(f"  [PARITY OK] {item}")
    print(f"Total: {len(schema_names)}/16 schemas verified")
