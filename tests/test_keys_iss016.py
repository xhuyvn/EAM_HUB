"""Tests for ISS-016: Identifier allowlist and control character rejection.

Validates that eap.contracts.keys strictly rejects:
1. Control characters (newline, carriage return, NUL, tab, etc.)
2. Overlong identifiers (> 128 chars)
3. Disallowed characters (outside ^[A-Za-z0-9._:#/-]{1,128}$)
4. Empty strings for required identifier fields
while keeping valid identifiers and existing vectors working without normalization.
"""

from __future__ import annotations

import pytest

from eap.contracts.keys import (
    KeyDerivationError,
    derive_dispatch_idempotency_key,
    derive_operation_key,
    derive_payload_hash,
    derive_task_intent_key,
    validate_identifier,
)


@pytest.mark.parametrize(
    "bad_char,char_desc",
    [
        ("\n", "newline"),
        ("\r", "carriage return"),
        ("\0", "NUL byte"),
        ("\t", "tab"),
        ("\x08", "backspace"),
        ("\x1f", "unit separator"),
        ("\x7f", "delete character"),
    ],
)
def test_iss016_control_characters_rejected(bad_char: str, char_desc: str) -> None:
    """Control characters inside identifiers must be strictly rejected (never normalized)."""
    # 1. tenant_id
    with pytest.raises(KeyDerivationError, match="must not contain"):
        derive_task_intent_key(f"tenant{bad_char}alpha", "task-001")

    # 2. task_id
    with pytest.raises(KeyDerivationError, match="must not contain"):
        derive_task_intent_key("tenant-alpha", f"task{bad_char}001")

    # 3. action_slot
    with pytest.raises(KeyDerivationError, match="must not contain"):
        derive_operation_key("tenant-alpha", "task-001", f"create_pr{bad_char}#1")

    # 4. binding_id
    with pytest.raises(KeyDerivationError, match="must not contain"):
        derive_dispatch_idempotency_key(f"bind{bad_char}ing", "run-001", "submit")

    # 5. run_id
    with pytest.raises(KeyDerivationError, match="must not contain"):
        derive_dispatch_idempotency_key("binding-1", f"run{bad_char}001", "submit")

    # 6. tool_id
    valid_hash = "6102d7f674ef7d53850cec4bcb6f13346fe9b4e67596a059e0f3b07454751cbc"
    with pytest.raises(KeyDerivationError, match="must not contain"):
        derive_payload_hash(f"tool{bad_char}id", valid_hash, "repo:example")

    # 7. resource_ref
    with pytest.raises(KeyDerivationError, match="must not contain control characters"):
        derive_payload_hash("engineering.create_pr", valid_hash, f"repo:{bad_char}example")


def test_iss016_overlong_identifiers_rejected() -> None:
    """Identifiers exceeding 128 characters must be rejected."""
    overlong = "a" * 129
    with pytest.raises(KeyDerivationError, match="must not exceed 128 characters"):
        derive_task_intent_key(overlong, "task-001")

    with pytest.raises(KeyDerivationError, match="must not exceed 128 characters"):
        derive_task_intent_key("tenant-alpha", overlong)

    with pytest.raises(KeyDerivationError, match="must not exceed 128 characters"):
        derive_operation_key("tenant-alpha", "task-001", overlong)

    with pytest.raises(KeyDerivationError, match="must not exceed 128 characters"):
        derive_dispatch_idempotency_key(overlong, "run-001", "submit")


@pytest.mark.parametrize(
    "disallowed_input,desc",
    [
        ("tenant$alpha", "dollar sign"),
        ("tenant@domain", "at sign"),
        ("task!1", "exclamation mark"),
        ("task 1", "space in middle"),
        ("task*1", "asterisk"),
        ("task(1)", "parentheses"),
        ("task%1", "percent"),
    ],
)
def test_iss016_disallowed_characters_rejected(disallowed_input: str, desc: str) -> None:
    """Characters outside the allowlist ^[A-Za-z0-9._:#/-]{1,128}$ are rejected."""
    with pytest.raises(KeyDerivationError, match="must match identifier allowlist"):
        validate_identifier("test_field", disallowed_input)


def test_iss016_valid_identifiers_accepted() -> None:
    """Boundary valid identifiers (exact length 1, 128, all allowlist chars) succeed."""
    # Length 1
    assert derive_task_intent_key("t", "1") is not None

    # Length 128
    long_128 = "A" * 128
    assert derive_task_intent_key(long_128, "task-001") is not None

    # All allowed characters: A-Za-z0-9._:#/-
    all_allowed = "Alpha123.beta_gamma:delta#epsilon/zeta-theta"
    assert derive_operation_key("tenant-alpha", "task-001", all_allowed) is not None
