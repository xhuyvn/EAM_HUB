"""Key derivation and test vector generator for DG-1.

This script computes standard test vectors for:
1. task_intent_key = sha256(tenant_id|task_id)
2. operation_key = sha256(tenant_id|task_id|action_slot)
3. dispatch_idempotency_key = sha256(binding_id|run_id|operation)
4. payload_hash = sha256(tool_id|arguments_sha256|resource_ref)

All strings are UTF-8 encoded with pipe delimiter (0x7C).
Strict validation (W7-1): Key derivation MUST reject any component containing '|',
leading/trailing whitespace, or non-NFC input (raise KeyDerivationError, never normalize silently).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import NamedTuple

HEX_64_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class KeyDerivationError(ValueError):
    """Raised when an input component violates strict canonical formatting rules."""
    pass


class VectorCase(NamedTuple):
    name: str
    key_type: str
    inputs: dict[str, str]
    raw_delimited: str
    expected_hex: str


class NegativeVectorCase(NamedTuple):
    name: str
    key_type: str
    inputs: dict[str, str]
    expected_error: str
    reason: str


def validate_component(name: str, value: str, allow_empty: bool = False) -> None:
    """Validate a key derivation string component strictly.

    MUST reject:
    - Non-string types
    - Delimiter character '|' (0x7C)
    - Leading or trailing whitespace
    - Non-NFC unicode inputs
    - Empty strings (unless allow_empty=True, e.g. optional resource_ref)
    """
    if not isinstance(value, str):
        raise KeyDerivationError(f"{name} must be a string, got {type(value).__name__}")
    if not allow_empty and len(value) == 0:
        raise KeyDerivationError(f"{name} must not be empty")
    if "|" in value:
        raise KeyDerivationError(f"{name} must not contain delimiter '|' (was {value!r})")
    if value != value.strip():
        raise KeyDerivationError(f"{name} must not contain leading or trailing whitespace (was {value!r})")
    if unicodedata.normalize("NFC", value) != value:
        raise KeyDerivationError(f"{name} must be in Unicode Normalization Form C (NFC)")


def compute_task_intent_key(tenant_id: str, task_id: str) -> tuple[str, str]:
    validate_component("tenant_id", tenant_id)
    validate_component("task_id", task_id)
    raw = f"{tenant_id}|{task_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


def compute_operation_key(tenant_id: str, task_id: str, action_slot: str) -> tuple[str, str]:
    validate_component("tenant_id", tenant_id)
    validate_component("task_id", task_id)
    validate_component("action_slot", action_slot)
    raw = f"{tenant_id}|{task_id}|{action_slot}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


def compute_dispatch_idempotency_key(
    binding_id: str, run_id: str, operation: str = "submit"
) -> tuple[str, str]:
    validate_component("binding_id", binding_id)
    validate_component("run_id", run_id)
    validate_component("operation", operation)
    raw = f"{binding_id}|{run_id}|{operation}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


def compute_payload_hash(
    tool_id: str, arguments_sha256: str, resource_ref: str | None = None
) -> tuple[str, str]:
    validate_component("tool_id", tool_id)
    validate_component("arguments_sha256", arguments_sha256)
    if not HEX_64_PATTERN.match(arguments_sha256):
        raise KeyDerivationError(
            f"arguments_sha256 must be 64 lowercase hex characters, got {arguments_sha256!r}"
        )
    res = resource_ref or ""
    validate_component("resource_ref", res, allow_empty=True)
    raw = f"{tool_id}|{arguments_sha256}|{res}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


# -----------------------------------------------------------------------------
# Standard positive test vectors
# -----------------------------------------------------------------------------
def _generate_positive_vectors() -> list[VectorCase]:
    cases: list[VectorCase] = []

    # 1. task_intent_key
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


# -----------------------------------------------------------------------------
# Standard negative test vectors (W7-1)
# -----------------------------------------------------------------------------
def _generate_negative_vectors() -> list[NegativeVectorCase]:
    cases: list[NegativeVectorCase] = []

    # 1. Pipe in task_id
    cases.append(
        NegativeVectorCase(
            name="pipe_in_task_id",
            key_type="task_intent_key",
            inputs={"tenant_id": "company-example", "task_id": "task|001"},
            expected_error="task_id must not contain delimiter '|'",
            reason="Pipes in identifiers could cause delimiter injection across fields",
        )
    )

    # 2. Trailing space in tenant_id
    cases.append(
        NegativeVectorCase(
            name="trailing_space_tenant",
            key_type="task_intent_key",
            inputs={"tenant_id": "company-example ", "task_id": "task-001"},
            expected_error="tenant_id must not contain leading or trailing whitespace",
            reason="Silent whitespace trimming is forbidden; invalid whitespace must raise",
        )
    )

    # 3. Trailing space in task_id
    cases.append(
        NegativeVectorCase(
            name="trailing_space_task",
            key_type="task_intent_key",
            inputs={"tenant_id": "company-example", "task_id": "task-001 "},
            expected_error="task_id must not contain leading or trailing whitespace",
            reason="Whitespace in identifiers must be rejected strictly",
        )
    )

    # 4. Leading space in task_id
    cases.append(
        NegativeVectorCase(
            name="leading_space_task",
            key_type="task_intent_key",
            inputs={"tenant_id": "company-example", "task_id": " task-001"},
            expected_error="task_id must not contain leading or trailing whitespace",
            reason="Leading whitespace must be rejected strictly",
        )
    )

    # 5. Cross-tenant collision attempt "a|b" + "c" vs "a" + "b|c"
    cases.append(
        NegativeVectorCase(
            name="cross_tenant_collision_tenant_pipe",
            key_type="task_intent_key",
            inputs={"tenant_id": "a|b", "task_id": "c"},
            expected_error="tenant_id must not contain delimiter '|'",
            reason="Delimiter injection attempt: tenant 'a|b' rejected before concatenation",
        )
    )

    cases.append(
        NegativeVectorCase(
            name="cross_tenant_collision_task_pipe",
            key_type="task_intent_key",
            inputs={"tenant_id": "a", "task_id": "b|c"},
            expected_error="task_id must not contain delimiter '|'",
            reason="Delimiter injection attempt: task 'b|c' rejected before concatenation",
        )
    )

    # 6. Non-NFC input (decomposed NFD e + combining acute: e\u0301)
    decomposed_task_id = "task-" + unicodedata.normalize("NFD", "é")
    cases.append(
        NegativeVectorCase(
            name="non_nfc_input_task_id",
            key_type="task_intent_key",
            inputs={"tenant_id": "company-example", "task_id": decomposed_task_id},
            expected_error="task_id must be in Unicode Normalization Form C (NFC)",
            reason="Non-NFC Unicode representation causes hash divergence across platforms and must be rejected",
        )
    )

    # 7. Pipe in action_slot
    cases.append(
        NegativeVectorCase(
            name="pipe_in_action_slot",
            key_type="operation_key",
            inputs={"tenant_id": "company-example", "task_id": "task-002", "action_slot": "create|pr#1"},
            expected_error="action_slot must not contain delimiter '|'",
            reason="Pipes in action slots violate step identifier syntax",
        )
    )

    # 8. Pipe in tool_id
    cases.append(
        NegativeVectorCase(
            name="pipe_in_tool_id",
            key_type="payload_hash",
            inputs={
                "tool_id": "engineering|create_pr",
                "arguments_sha256": "6102d7f674ef7d53850cec4bcb6f13346fe9b4e67596a059e0f3b07454751cbc",
                "resource_ref": "repo:example",
            },
            expected_error="tool_id must not contain delimiter '|'",
            reason="Pipes in tool IDs violate tool identifier syntax",
        )
    )

    # 9. Invalid arguments_sha256 format (uppercase or non-hex)
    cases.append(
        NegativeVectorCase(
            name="invalid_arguments_sha256_uppercase",
            key_type="payload_hash",
            inputs={
                "tool_id": "engineering.create_pr",
                "arguments_sha256": "6102D7F674EF7D53850CEC4BCB6F13346FE9B4E67596A059E0F3B07454751CBC",
                "resource_ref": "repo:example",
            },
            expected_error="arguments_sha256 must be 64 lowercase hex characters",
            reason="arguments_sha256 must strictly be lowercase hex",
        )
    )

    return cases


TEST_VECTORS: list[VectorCase] = _generate_positive_vectors()
NEGATIVE_VECTORS: list[NegativeVectorCase] = _generate_negative_vectors()

if __name__ == "__main__":
    print(f"Generated {len(TEST_VECTORS)} positive test vectors:")
    for v in TEST_VECTORS:
        print(f"\n--- {v.name} ({v.key_type}) ---")
        print(f"  Inputs:        {v.inputs}")
        print(f"  Raw delimited: {v.raw_delimited}")
        print(f"  Hex digest:    {v.expected_hex}")

    print(f"\nGenerated {len(NEGATIVE_VECTORS)} negative test vectors:")
    for nv in NEGATIVE_VECTORS:
        print(f"\n--- {nv.name} ({nv.key_type}) ---")
        ascii_inputs = {k: v.encode("ascii", "backslashreplace").decode("ascii") for k, v in nv.inputs.items()}
        print(f"  Inputs:        {ascii_inputs}")
        print(f"  Expected err:  {nv.expected_error}")
        print(f"  Reason:        {nv.reason}")

    # Output JSON export with both positive and negative vectors
    payload = {
        "positive_vectors": [v._asdict() for v in TEST_VECTORS],
        "negative_vectors": [nv._asdict() for nv in NEGATIVE_VECTORS],
    }
    with open("tools/dg1_test_vectors.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print("\nSaved to tools/dg1_test_vectors.json")
