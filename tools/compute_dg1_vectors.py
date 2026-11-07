"""Key derivation and test vector generator for DG-1.

This script computes standard test vectors for:
1. task_intent_key = sha256(tenant_id|task_id)
2. operation_key = sha256(tenant_id|task_id|action_slot)
3. dispatch_idempotency_key = sha256(binding_id|run_id|operation)
4. payload_hash = sha256(tool_id|arguments_sha256|resource_ref)

All strings are UTF-8 encoded with pipe delimiter (0x7C).
"""

from __future__ import annotations

import hashlib
import json
from typing import NamedTuple


class VectorCase(NamedTuple):
    name: str
    key_type: str
    inputs: dict[str, str]
    raw_delimited: str
    expected_hex: str


def compute_task_intent_key(tenant_id: str, task_id: str) -> tuple[str, str]:
    raw = f"{tenant_id.strip()}|{task_id.strip()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


def compute_operation_key(tenant_id: str, task_id: str, action_slot: str) -> tuple[str, str]:
    raw = f"{tenant_id.strip()}|{task_id.strip()}|{action_slot.strip()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


def compute_dispatch_idempotency_key(
    binding_id: str, run_id: str, operation: str = "submit"
) -> tuple[str, str]:
    raw = f"{binding_id.strip()}|{run_id.strip()}|{operation.strip()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


def compute_payload_hash(
    tool_id: str, arguments_sha256: str, resource_ref: str | None = None
) -> tuple[str, str]:
    res = (resource_ref or "").strip()
    raw = f"{tool_id.strip()}|{arguments_sha256.strip().lower()}|{res}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


# Standard test vector set
TEST_VECTORS: list[VectorCase] = []


def _generate_vectors() -> list[VectorCase]:
    cases: list[VectorCase] = []

    # 1. task_intent_key
    # Case 1.1: Standard fixture task-001
    raw, h = compute_task_intent_key("company-example", "task-001")
    cases.append(
        VectorCase(
            name="task_intent_key_fixture",
            key_type="task_intent_key",
            inputs={"tenant_id": "company-example", "task_id": "task-001"},
            raw_delimited=raw,
            expected_hex=h,
        )
    )

    # Case 1.2: Alternate tenant/task
    raw, h = compute_task_intent_key("tenant-alpha", "task-42")
    cases.append(
        VectorCase(
            name="task_intent_key_alpha",
            key_type="task_intent_key",
            inputs={"tenant_id": "tenant-alpha", "task_id": "task-42"},
            raw_delimited=raw,
            expected_hex=h,
        )
    )

    # 2. operation_key
    # Case 2.1: create_pr#1
    raw, h = compute_operation_key("company-example", "task-002", "create_pr#1")
    cases.append(
        VectorCase(
            name="operation_key_pr1",
            key_type="operation_key",
            inputs={"tenant_id": "company-example", "task_id": "task-002", "action_slot": "create_pr#1"},
            raw_delimited=raw,
            expected_hex=h,
        )
    )

    # Case 2.2: run_tests#1
    raw, h = compute_operation_key("company-example", "task-002", "run_tests#1")
    cases.append(
        VectorCase(
            name="operation_key_tests1",
            key_type="operation_key",
            inputs={"tenant_id": "company-example", "task_id": "task-002", "action_slot": "run_tests#1"},
            raw_delimited=raw,
            expected_hex=h,
        )
    )

    # 3. dispatch_idempotency_key
    # Case 3.1: Standard fixture run-001 submit
    raw, h = compute_dispatch_idempotency_key("binding-ppm-pilot", "run-001", "submit")
    cases.append(
        VectorCase(
            name="dispatch_key_run001_submit",
            key_type="dispatch_idempotency_key",
            inputs={"binding_id": "binding-ppm-pilot", "run_id": "run-001", "operation": "submit"},
            raw_delimited=raw,
            expected_hex=h,
        )
    )

    # Case 3.2: run-002 cancel
    raw, h = compute_dispatch_idempotency_key("binding-ppm-pilot", "run-002", "cancel")
    cases.append(
        VectorCase(
            name="dispatch_key_run002_cancel",
            key_type="dispatch_idempotency_key",
            inputs={"binding_id": "binding-ppm-pilot", "run_id": "run-002", "operation": "cancel"},
            raw_delimited=raw,
            expected_hex=h,
        )
    )

    # 4. payload_hash
    # Case 4.1: Standard engineering.create_pr on repo:example
    raw, h = compute_payload_hash(
        "engineering.create_pr",
        "6102d7f674ef7d53850cec4bcb6f13346fe9b4e67596a059e0f3b07454751cbc",
        "repo:example",
    )
    cases.append(
        VectorCase(
            name="payload_hash_create_pr_example",
            key_type="payload_hash",
            inputs={
                "tool_id": "engineering.create_pr",
                "arguments_sha256": "6102d7f674ef7d53850cec4bcb6f13346fe9b4e67596a059e0f3b07454751cbc",
                "resource_ref": "repo:example",
            },
            raw_delimited=raw,
            expected_hex=h,
        )
    )

    # Case 4.2: Divergent resource_ref (causes OPERATION_KEY_CONFLICT)
    raw, h = compute_payload_hash(
        "engineering.create_pr",
        "6102d7f674ef7d53850cec4bcb6f13346fe9b4e67596a059e0f3b07454751cbc",
        "repo:example-divergent",
    )
    cases.append(
        VectorCase(
            name="payload_hash_create_pr_divergent",
            key_type="payload_hash",
            inputs={
                "tool_id": "engineering.create_pr",
                "arguments_sha256": "6102d7f674ef7d53850cec4bcb6f13346fe9b4e67596a059e0f3b07454751cbc",
                "resource_ref": "repo:example-divergent",
            },
            raw_delimited=raw,
            expected_hex=h,
        )
    )

    # Case 4.3: Omitted/empty resource_ref
    raw, h = compute_payload_hash(
        "system.echo",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "",
    )
    cases.append(
        VectorCase(
            name="payload_hash_empty_resource",
            key_type="payload_hash",
            inputs={
                "tool_id": "system.echo",
                "arguments_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "resource_ref": "",
            },
            raw_delimited=raw,
            expected_hex=h,
        )
    )

    return cases


TEST_VECTORS = _generate_vectors()

if __name__ == "__main__":
    print(f"Generated {len(TEST_VECTORS)} test vectors:")
    for v in TEST_VECTORS:
        print(f"\n--- {v.name} ({v.key_type}) ---")
        print(f"  Inputs:        {v.inputs}")
        print(f"  Raw delimited: {v.raw_delimited}")
        print(f"  Hex digest:    {v.expected_hex}")

    # Output JSON export
    as_json = [v._asdict() for v in TEST_VECTORS]
    with open("tools/dg1_test_vectors.json", "w", encoding="utf-8") as f:
        json.dump(as_json, f, indent=2)
    print("\nSaved to tools/dg1_test_vectors.json")
