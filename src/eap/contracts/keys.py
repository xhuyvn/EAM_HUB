"""Key derivation and strict canonical byte encoding for DG-1.

Implements DG-1 Section 2:
1. task_intent_key = sha256(tenant_id|task_id)
2. operation_key = sha256(tenant_id|task_id|action_slot)
3. dispatch_idempotency_key = sha256(binding_id|run_id|operation)
4. payload_hash = sha256(tool_id|arguments_sha256|resource_ref)

All strings are UTF-8 encoded with single ASCII pipe delimiter '|' (0x7C).
Strict validation (W7-1, ISS-016):
- Reject delimiter '|'
- Reject leading/trailing whitespace
- Reject non-NFC unicode inputs
- Reject empty strings (unless allow_empty=True, e.g. optional resource_ref)
- ISS-016: Reject control characters, enforce identifier allowlist ^[A-Za-z0-9._:#/-]{1,128}$
  for tenant_id, task_id, action_slot, tool_id, binding_id, run_id.
- Never normalize silently.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:#/-]{1,128}$")
HEX_64_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class KeyDerivationError(ValueError):
    """Raised when an input component violates strict canonical formatting rules."""



def validate_component(name: str, value: str, allow_empty: bool = False) -> None:
    """Validate a key derivation string component strictly per DG-1 W7-1.

    MUST reject:
    - Non-string types
    - Delimiter character '|' (0x7C)
    - Leading or trailing whitespace
    - Non-NFC unicode inputs
    - Empty strings (unless allow_empty=True)
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


def validate_identifier(name: str, value: str) -> None:
    """Validate identifier charset allowlist strictly per ISS-016.

    Applies to: tenant_id, task_id, action_slot, tool_id, binding_id, run_id.
    Rejects control characters, length > 128, and disallowed characters.
    """
    validate_component(name, value, allow_empty=False)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise KeyDerivationError(f"{name} must not contain control characters (was {value!r})")
    if len(value) > 128:
        raise KeyDerivationError(f"{name} must not exceed 128 characters (was length {len(value)})")
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise KeyDerivationError(
            f"{name} must match identifier allowlist '^[A-Za-z0-9._:#/-]{{1,128}}$' (was {value!r})"
        )


def derive_task_intent_key(tenant_id: str, task_id: str) -> str:
    """Derive task_intent_key: sha256(tenant_id|task_id)."""
    validate_identifier("tenant_id", tenant_id)
    validate_identifier("task_id", task_id)
    raw = f"{tenant_id}|{task_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def derive_operation_key(tenant_id: str, task_id: str, action_slot: str) -> str:
    """Derive operation_key: sha256(tenant_id|task_id|action_slot)."""
    validate_identifier("tenant_id", tenant_id)
    validate_identifier("task_id", task_id)
    validate_identifier("action_slot", action_slot)
    raw = f"{tenant_id}|{task_id}|{action_slot}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def derive_dispatch_idempotency_key(
    binding_id: str, run_id: str, operation: str = "submit"
) -> str:
    """Derive dispatch_idempotency_key: sha256(binding_id|run_id|operation)."""
    validate_identifier("binding_id", binding_id)
    validate_identifier("run_id", run_id)
    validate_identifier("operation", operation)
    raw = f"{binding_id}|{run_id}|{operation}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def derive_payload_hash(
    tool_id: str, arguments_sha256: str, resource_ref: str | None = None
) -> str:
    """Derive payload_hash: sha256(tool_id|arguments_sha256|resource_ref)."""
    validate_identifier("tool_id", tool_id)
    validate_component("arguments_sha256", arguments_sha256)
    if not HEX_64_PATTERN.fullmatch(arguments_sha256):
        raise KeyDerivationError(
            f"arguments_sha256 must be 64 lowercase hex characters (was {arguments_sha256!r})"
        )
    if resource_ref is None:
        resource_ref = ""
    validate_component("resource_ref", resource_ref, allow_empty=True)
    if resource_ref and any(ord(c) < 32 or ord(c) == 127 for c in resource_ref):
        raise KeyDerivationError("resource_ref must not contain control characters")

    raw = f"{tool_id}|{arguments_sha256}|{resource_ref}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_task_intent_key(tenant_id: str, task_id: str) -> tuple[str, str]:
    """Compute (raw_delimited, digest) for task_intent_key."""
    validate_identifier("tenant_id", tenant_id)
    validate_identifier("task_id", task_id)
    raw = f"{tenant_id}|{task_id}"
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_operation_key(tenant_id: str, task_id: str, action_slot: str) -> tuple[str, str]:
    """Compute (raw_delimited, digest) for operation_key."""
    validate_identifier("tenant_id", tenant_id)
    validate_identifier("task_id", task_id)
    validate_identifier("action_slot", action_slot)
    raw = f"{tenant_id}|{task_id}|{action_slot}"
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_dispatch_idempotency_key(
    binding_id: str, run_id: str, operation: str = "submit"
) -> tuple[str, str]:
    """Compute (raw_delimited, digest) for dispatch_idempotency_key."""
    validate_identifier("binding_id", binding_id)
    validate_identifier("run_id", run_id)
    validate_identifier("operation", operation)
    raw = f"{binding_id}|{run_id}|{operation}"
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_payload_hash(
    tool_id: str, arguments_sha256: str, resource_ref: str | None = None
) -> tuple[str, str]:
    """Compute (raw_delimited, digest) for payload_hash."""
    validate_identifier("tool_id", tool_id)
    validate_component("arguments_sha256", arguments_sha256)
    if not HEX_64_PATTERN.fullmatch(arguments_sha256):
        raise KeyDerivationError(
            f"arguments_sha256 must be 64 lowercase hex characters (was {arguments_sha256!r})"
        )
    if resource_ref is None:
        resource_ref = ""
    validate_component("resource_ref", resource_ref, allow_empty=True)
    if resource_ref and any(ord(c) < 32 or ord(c) == 127 for c in resource_ref):
        raise KeyDerivationError("resource_ref must not contain control characters")

    raw = f"{tool_id}|{arguments_sha256}|{resource_ref}"
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()
