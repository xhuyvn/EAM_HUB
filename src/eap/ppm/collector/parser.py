"""parser.py — Safe YAML and JSON parsing with duplicate key rejection and depth limits.

Enforces:
- Reject duplicate keys in YAML mappings (as in PPM-M0 and run_negative_cases.py).
- Reject duplicate keys in JSON objects.
- Enforce maximum structure depth limits (prevent recursion / stack overflow attacks).
- Prompt-injection text is preserved strictly as literal data without execution.
"""

from __future__ import annotations

import json
from typing import Any

import yaml


class SafeParseError(ValueError):
    """Raised when structured data parsing violates security bounds."""


class DuplicateKeyError(SafeParseError):
    """Raised when duplicate keys are encountered in YAML or JSON mappings."""


class DepthLimitExceededError(SafeParseError):
    """Raised when nested object depth exceeds the security limit."""


def _get_depth(obj: Any, current_depth: int = 1) -> int:
    """Compute maximum nesting depth of a Python collection."""
    if isinstance(obj, dict):
        if not obj:
            return current_depth
        return max(_get_depth(v, current_depth + 1) for v in obj.values())
    elif isinstance(obj, (list, tuple, set)):
        if not obj:
            return current_depth
        return max(_get_depth(item, current_depth + 1) for item in obj)
    return current_depth


# --- Safe YAML Parsing ---

class _StrictYamlLoader(yaml.SafeLoader):
    pass


def _strict_mapping_constructor(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateKeyError(f"Duplicate YAML key: '{key}'")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictYamlLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _strict_mapping_constructor,
)


def safe_load_yaml(content: str, *, max_depth: int = 32) -> Any:
    """Parse YAML content rejecting duplicate keys and excessive depth."""
    if not isinstance(content, str):
        raise SafeParseError("Content must be a string.")
    try:
        parsed = yaml.load(content, Loader=_StrictYamlLoader)
    except yaml.YAMLError as e:
        raise SafeParseError(f"YAML parsing error: {e}") from e

    depth = _get_depth(parsed)
    if depth > max_depth:
        raise DepthLimitExceededError(f"YAML nesting depth {depth} exceeds limit {max_depth}")
    return parsed


# --- Safe JSON Parsing ---

def _json_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, val in pairs:
        if key in obj:
            raise DuplicateKeyError(f"Duplicate JSON key: '{key}'")
        obj[key] = val
    return obj


def safe_load_json(content: str, *, max_depth: int = 32) -> Any:
    """Parse JSON content rejecting duplicate keys and excessive depth."""
    if not isinstance(content, str):
        raise SafeParseError("Content must be a string.")
    try:
        parsed = json.loads(content, object_pairs_hook=_json_pairs_hook)
    except json.JSONDecodeError as e:
        raise SafeParseError(f"JSON parsing error: {e}") from e

    depth = _get_depth(parsed)
    if depth > max_depth:
        raise DepthLimitExceededError(f"JSON nesting depth {depth} exceeds limit {max_depth}")
    return parsed


__all__ = [
    "DepthLimitExceededError",
    "DuplicateKeyError",
    "SafeParseError",
    "safe_load_json",
    "safe_load_yaml",
]
