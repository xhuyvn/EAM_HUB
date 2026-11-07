"""Schema-parity tests (SC-01 / P0-WP2).

Prevents silent drift between contracts/*.schema.json and Pydantic v2 models.
For each of the 16 schemas, asserts that the model's field names, required set,
types, pattern, minLength/maxLength, minItems, minimum/maximum, enum/const values,
and if/then payload branches equal the JSON Schema's (walking nested objects).
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
from pydantic.fields import FieldInfo

from eap.contracts.models import get_model_for_schema
from eap.contracts.models.base import _validate_safe_path
from eap.contracts.models.event import (
    ApprovalDecidedPayload,
    ArtifactCreatedPayload,
    ConnectorStatusPayload,
    PpmAssessmentUpdatedPayload,
    RunFinishedPayload,
    RunProgressPayload,
    RunStartedPayload,
    RunStateChangedPayload,
    RunWaitingApprovalPayload,
    UsageReportedPayload,
    WorkerHeartbeatPayload,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "contracts"

EVENT_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "run.started": RunStartedPayload,
    "run.progress": RunProgressPayload,
    "run.waiting_approval": RunWaitingApprovalPayload,
    "run.finished": RunFinishedPayload,
    "worker.heartbeat": WorkerHeartbeatPayload,
    "artifact.created": ArtifactCreatedPayload,
    "run.state_changed": RunStateChangedPayload,
    "approval.decided": ApprovalDecidedPayload,
    "usage.reported": UsageReportedPayload,
    "ppm.assessment.updated": PpmAssessmentUpdatedPayload,
    "connector.status": ConnectorStatusPayload,
}


def get_literal_values(tp: Any) -> set[Any] | None:
    origin = typing.get_origin(tp)
    if origin is typing.Annotated:
        return get_literal_values(typing.get_args(tp)[0])
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


def get_python_types(tp: Any) -> set[str]:
    origin = typing.get_origin(tp)
    if origin is typing.Annotated:
        return get_python_types(typing.get_args(tp)[0])
    if origin in (typing.Union, types.UnionType):
        res: set[str] = set()
        for a in typing.get_args(tp):
            res.update(get_python_types(a))
        return res
    if origin is typing.Literal:
        type_map = {str: "string", int: "integer", float: "number", bool: "boolean", type(None): "null"}
        return {type_map.get(type(v), str(type(v))) for v in typing.get_args(tp)}
    if origin is list:
        return {"array"}
    if origin is dict:
        return {"object"}
    if tp is type(None):
        return {"null"}
    if tp is str:
        return {"string"}
    if tp is int:
        return {"integer"}
    if tp is float:
        return {"number"}
    if tp is bool:
        return {"boolean"}
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return {"object"}
    return {str(tp)}


def get_field_constraints(field_info: FieldInfo) -> dict[str, Any]:
    constraints: dict[str, Any] = {}
    for meta in field_info.metadata:
        if hasattr(meta, "min_length"):
            constraints["minLength"] = meta.min_length
            constraints["minItems"] = meta.min_length
        if hasattr(meta, "max_length"):
            constraints["maxLength"] = meta.max_length
            constraints["maxItems"] = meta.max_length
        if hasattr(meta, "ge"):
            constraints["minimum"] = meta.ge
        if hasattr(meta, "le"):
            constraints["maximum"] = meta.le
        if hasattr(meta, "gt"):
            constraints["exclusiveMinimum"] = meta.gt
        if hasattr(meta, "lt"):
            constraints["exclusiveMaximum"] = meta.lt
        if hasattr(meta, "pattern"):
            constraints["pattern"] = meta.pattern
        if getattr(meta, "func", None) is _validate_safe_path:
            constraints["safe_path_validator"] = True
    return constraints


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


def extract_type_metadata(t: Any) -> list[Any]:
    res: list[Any] = []
    for m in getattr(t, "__metadata__", ()):
        res.append(m)
        if hasattr(m, "metadata"):
            res.extend(m.metadata)
    return res


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

        if "type" in prop_schema:
            st = prop_schema["type"]
            expected_types = {st} if isinstance(st, str) else set(st)
            actual_types = get_python_types(field_info.annotation)
            assert expected_types == actual_types, (
                f"[{cur_path}] Type mismatch: schema={expected_types}, model={actual_types}"
            )

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

        constraints = get_field_constraints(field_info)
        for k in ["minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"]:
            if k in prop_schema:
                assert constraints.get(k) == prop_schema[k], (
                    f"[{cur_path}] Constraint mismatch on {k}: expected {prop_schema[k]}, got {constraints.get(k)}"
                )

        if "pattern" in prop_schema:
            pat = prop_schema["pattern"]
            if r"^(?!/)" in pat:
                assert constraints.get("safe_path_validator") is True, f"[{cur_path}] Missing safe path validator"
            else:
                assert constraints.get("pattern") == pat, (
                    f"[{cur_path}] Pattern mismatch: expected {pat}, got {constraints.get('pattern')}"
                )

        if prop_schema.get("type") == "array" and "items" in prop_schema:
            items_schema = prop_schema["items"]
            origin = typing.get_origin(field_info.annotation)
            args = typing.get_args(field_info.annotation)
            if origin is typing.Annotated:
                raw_list = args[0]
                args = typing.get_args(raw_list)
            item_type = args[0] if args else None

            if "minLength" in items_schema and item_type is not None:
                item_metas = extract_type_metadata(item_type)
                item_min = next((m.min_length for m in item_metas if hasattr(m, "min_length")), None)
                assert item_min == items_schema["minLength"], (
                    f"[{cur_path}[]] items minLength mismatch: expected {items_schema['minLength']}, got {item_min}"
                )

            if items_schema.get("type") == "object" and "properties" in items_schema:
                nested_cls = get_nested_model(field_info.annotation)
                assert nested_cls is not None, f"[{cur_path}[]] Expected nested model for array items schema"
                assert_schema_model_parity(items_schema, nested_cls, f"{cur_path}[]")

            if "enum" in items_schema and item_type is not None:
                expected_enum = set(items_schema["enum"])
                lits = get_literal_values(item_type)
                assert lits == expected_enum, (
                    f"[{cur_path}[]] Array items enum mismatch: expected {expected_enum}, got {lits}"
                )

        if prop_schema.get("type") == "object" and "properties" in prop_schema:
            nested_cls = get_nested_model(field_info.annotation)
            assert nested_cls is not None, f"[{cur_path}] Expected nested model for object schema"
            assert_schema_model_parity(prop_schema, nested_cls, cur_path)

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


def test_event_payload_branches_parity() -> None:
    event_schema_path = CONTRACTS_DIR / "event.schema.json"
    schema = json.loads(event_schema_path.read_text(encoding="utf-8"))
    checked_branches = 0
    for branch in schema.get("allOf", []):
        if_prop = branch.get("if", {}).get("properties", {}).get("event_type", {})
        if "const" in if_prop:
            et = if_prop["const"]
            assert et in EVENT_PAYLOAD_MODELS, f"Unmapped event payload type {et}"
            model_cls = EVENT_PAYLOAD_MODELS[et]
            payload_schema = branch["then"]["properties"]["payload"]
            assert_schema_model_parity(payload_schema, model_cls, f"event[{et}].payload")
            checked_branches += 1
    assert checked_branches == 11, f"Expected 11 payload branches, checked {checked_branches}"
