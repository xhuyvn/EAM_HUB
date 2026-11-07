"""RFC 8785 JSON Canonicalization Scheme (JCS) and action digest utilities.

Primary specification:
RFC 8785: https://www.rfc-editor.org/rfc/rfc8785
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import rfc8785
from pydantic import BaseModel

DIGEST_FIELDS: list[str] = [
    "binding_id",
    "run_id",
    "task_id",
    "tool_id",
    "arguments_sha256",
    "resource_ref",
    "expected_resource_revision",
    "policy_version",
    "operation_key",
    "nonce",
    "expires_at",
]


def jcs(obj: Any) -> bytes:
    """Serialize an object into canonical JSON bytes according to RFC 8785."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json", exclude_unset=True, by_alias=True)
    return rfc8785.dumps(obj)


def sha256_digest(data: bytes | str | Any) -> str:
    """Compute the SHA-256 hex digest of data (bytes, str, or JCS-canonicalized object)."""
    if isinstance(data, bytes):
        raw = data
    elif isinstance(data, str):
        raw = data.encode("utf-8")
    else:
        raw = jcs(data)
    return hashlib.sha256(raw).hexdigest()


def action_digest(proposal: Mapping[str, Any] | BaseModel) -> str:
    """Compute the immutable action digest for an action proposal per RFC 8785."""
    if isinstance(proposal, BaseModel):
        data = proposal.model_dump(mode="json")
    else:
        data = dict(proposal)
    canonical_subset = {k: data[k] for k in DIGEST_FIELDS}
    return hashlib.sha256(jcs(canonical_subset)).hexdigest()
