"""Test verification for DG-1 key and hash derivation test vectors.

Verifies that the canonical byte encoding, delimiter convention (0x7C),
strict validation rejection rules (W7-1), and SHA-256 hex digest calculations
match the documented test vectors in DG-1.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from compute_dg1_vectors import (  # type: ignore[import-not-found]
    NEGATIVE_VECTORS,
    TEST_VECTORS,
    KeyDerivationError,
    compute_dispatch_idempotency_key,
    compute_operation_key,
    compute_payload_hash,
    compute_task_intent_key,
    derive_dispatch_idempotency_key,
    derive_operation_key,
    derive_payload_hash,
    derive_task_intent_key,
)


@pytest.mark.parametrize("vector", TEST_VECTORS, ids=[v.name for v in TEST_VECTORS])
def test_test_vector_recomputation(vector) -> None:
    """Every positive test vector must recompute to the exact expected hex string."""
    ktype = vector.key_type
    inputs = vector.inputs

    if ktype == "task_intent_key":
        raw, digest = compute_task_intent_key(inputs["tenant_id"], inputs["task_id"])
        direct_derived = derive_task_intent_key(inputs["tenant_id"], inputs["task_id"])
    elif ktype == "operation_key":
        raw, digest = compute_operation_key(inputs["tenant_id"], inputs["task_id"], inputs["action_slot"])
        direct_derived = derive_operation_key(inputs["tenant_id"], inputs["task_id"], inputs["action_slot"])
    elif ktype == "dispatch_idempotency_key":
        raw, digest = compute_dispatch_idempotency_key(inputs["binding_id"], inputs["run_id"], inputs["operation"])
        direct_derived = derive_dispatch_idempotency_key(inputs["binding_id"], inputs["run_id"], inputs["operation"])
    elif ktype == "payload_hash":
        raw, digest = compute_payload_hash(inputs["tool_id"], inputs["arguments_sha256"], inputs.get("resource_ref"))
        direct_derived = derive_payload_hash(inputs["tool_id"], inputs["arguments_sha256"], inputs.get("resource_ref"))
    else:
        raise ValueError(f"Unknown key type {ktype}")

    assert raw == vector.raw_delimited, f"Delimited encoding mismatch for {vector.name}"
    assert digest == vector.expected_hex, f"Hex digest mismatch for {vector.name}"
    assert direct_derived == vector.expected_hex, f"Direct derive mismatch for {vector.name}"
    assert len(digest) == 64, f"Digest must be 64 characters lowercase hex for {vector.name}"
    assert all(c in "0123456789abcdef" for c in digest), f"Digest must be lowercase hex for {vector.name}"


@pytest.mark.parametrize("vector", NEGATIVE_VECTORS, ids=[v.name for v in NEGATIVE_VECTORS])
def test_negative_vectors_strict_rejection(vector) -> None:
    """W7-1: Key derivation MUST reject components with pipes, whitespace, or non-NFC input."""
    ktype = vector.key_type
    inputs = vector.inputs

    with pytest.raises(KeyDerivationError) as exc_info:
        if ktype == "task_intent_key":
            compute_task_intent_key(inputs["tenant_id"], inputs["task_id"])
        elif ktype == "operation_key":
            compute_operation_key(inputs["tenant_id"], inputs["task_id"], inputs["action_slot"])
        elif ktype == "dispatch_idempotency_key":
            compute_dispatch_idempotency_key(inputs["binding_id"], inputs["run_id"], inputs.get("operation", "submit"))
        elif ktype == "payload_hash":
            compute_payload_hash(inputs["tool_id"], inputs["arguments_sha256"], inputs.get("resource_ref"))
        else:
            raise ValueError(f"Unknown key type {ktype}")

    assert vector.expected_error in str(exc_info.value), (
        f"Expected {vector.expected_error!r} in error message, got {str(exc_info.value)!r}"
    )


def test_cross_tenant_collision_prevention() -> None:
    """W7-1: Cross-tenant collision attempt 'a|b' + 'c' vs 'a' + 'b|c' must be rejected."""
    with pytest.raises(KeyDerivationError, match="tenant_id must not contain delimiter"):
        compute_task_intent_key("a|b", "c")

    with pytest.raises(KeyDerivationError, match="task_id must not contain delimiter"):
        compute_task_intent_key("a", "b|c")


def test_dg1_json_artifact_consistency() -> None:
    """Assert tools/dg1_test_vectors.json file matches the in-memory definitions."""
    json_path = ROOT / "tools/dg1_test_vectors.json"
    assert json_path.exists(), "tools/dg1_test_vectors.json must exist"
    data = json.loads(json_path.read_text(encoding="utf-8"))

    pos_data = data["positive_vectors"]
    neg_data = data["negative_vectors"]

    assert len(pos_data) == len(TEST_VECTORS)
    for item, vector in zip(pos_data, TEST_VECTORS, strict=True):
        assert item["name"] == vector.name
        assert item["key_type"] == vector.key_type
        assert item["raw_delimited"] == vector.raw_delimited
        assert item["expected_hex"] == vector.expected_hex

    assert len(neg_data) == len(NEGATIVE_VECTORS)
    for item, vector in zip(neg_data, NEGATIVE_VECTORS, strict=True):
        assert item["name"] == vector.name
        assert item["key_type"] == vector.key_type
        assert item["expected_error"] == vector.expected_error
        assert item["reason"] == vector.reason


def test_fixture_envelope_vector_match() -> None:
    """Assert task_intent_key and idempotency_key in examples/task-envelope.json match our vectors."""
    envelope_path = ROOT / "examples/task-envelope.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))

    # Check task_intent_key
    _, expected_tik = compute_task_intent_key(
        envelope["execution_context"]["tenant_id"], envelope["task_id"]
    )
    assert envelope["task_intent_key"] == expected_tik

    # Check dispatch idempotency_key
    _, expected_ik = compute_dispatch_idempotency_key(
        envelope["binding_id"], envelope["run_id"], "submit"
    )
    assert envelope["idempotency_key"] == expected_ik


def test_conflict_divergence_assertion() -> None:
    """Verifies that matching operation_key inputs with different payload_hash produces divergence."""
    # Same operation_key
    _, op1 = compute_operation_key("tenant-1", "task-100", "create_pr#1")
    _, op2 = compute_operation_key("tenant-1", "task-100", "create_pr#1")
    assert op1 == op2

    # Different payload hashes (e.g. target repo changed)
    _, p1 = compute_payload_hash("engineering.create_pr", "a" * 64, "repo:repo-a")
    _, p2 = compute_payload_hash("engineering.create_pr", "a" * 64, "repo:repo-b")
    assert p1 != p2, "Different target resource must yield distinct payload_hash"
