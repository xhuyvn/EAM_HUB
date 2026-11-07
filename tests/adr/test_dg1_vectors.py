"""Test verification for DG-1 key and hash derivation test vectors.

Verifies that the canonical byte encoding, delimiter convention (0x7C),
and SHA-256 hex digest calculations match the documented test vectors in DG-1.md.
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
    TEST_VECTORS,
    compute_dispatch_idempotency_key,
    compute_operation_key,
    compute_payload_hash,
    compute_task_intent_key,
)


@pytest.mark.parametrize("vector", TEST_VECTORS, ids=[v.name for v in TEST_VECTORS])
def test_test_vector_recomputation(vector) -> None:
    """Every test vector must recompute to the exact expected hex string."""
    ktype = vector.key_type
    inputs = vector.inputs

    if ktype == "task_intent_key":
        raw, digest = compute_task_intent_key(inputs["tenant_id"], inputs["task_id"])
    elif ktype == "operation_key":
        raw, digest = compute_operation_key(inputs["tenant_id"], inputs["task_id"], inputs["action_slot"])
    elif ktype == "dispatch_idempotency_key":
        raw, digest = compute_dispatch_idempotency_key(inputs["binding_id"], inputs["run_id"], inputs["operation"])
    elif ktype == "payload_hash":
        raw, digest = compute_payload_hash(inputs["tool_id"], inputs["arguments_sha256"], inputs.get("resource_ref"))
    else:
        raise ValueError(f"Unknown key type {ktype}")

    assert raw == vector.raw_delimited, f"Delimited encoding mismatch for {vector.name}"
    assert digest == vector.expected_hex, f"Hex digest mismatch for {vector.name}"
    assert len(digest) == 64, f"Digest must be 64 characters lowercase hex for {vector.name}"
    assert all(c in "0123456789abcdef" for c in digest), f"Digest must be lowercase hex for {vector.name}"


def test_dg1_json_artifact_consistency() -> None:
    """Assert tools/dg1_test_vectors.json file matches the in-memory definition."""
    json_path = ROOT / "tools/dg1_test_vectors.json"
    assert json_path.exists(), "tools/dg1_test_vectors.json must exist"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(data) == len(TEST_VECTORS)

    for item, vector in zip(data, TEST_VECTORS, strict=True):
        assert item["name"] == vector.name
        assert item["key_type"] == vector.key_type
        assert item["raw_delimited"] == vector.raw_delimited
        assert item["expected_hex"] == vector.expected_hex


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
