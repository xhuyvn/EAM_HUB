"""Pure contract semantic validation checks for PPM Contract 2.0 / 2.1.

Extracted from ``ppm-v2/tools/validate_contract.py`` so both the standalone
CLI validator and ``eap.ppm.ingest`` share a single source of truth without
subprocesses or dynamic code loading.

Purity: uses only ``datetime`` and ``typing`` from the standard library.
"""

import datetime as dt
from typing import Any

FUTURE_SKEW = dt.timedelta(hours=24)


def uses_21_fields(doc: dict[str, Any]) -> bool:
    """Return True if *doc* uses any field introduced in contract_version 2.1."""
    kind = doc.get("kind")
    if kind == "acceptance":
        criteria = doc.get("criteria", [])
        return isinstance(criteria, list) and any(
            isinstance(c, dict) and "verification_refs" in c for c in criteria
        )
    if kind == "update":
        completed = doc.get("completed", [])
        in_progress = doc.get("in_progress", [])
        checks = doc.get("checks", [])
        items = (completed if isinstance(completed, list) else []) + (
            in_progress if isinstance(in_progress, list) else []
        )
        return any(isinstance(i, dict) and "criterion_ids" in i for i in items) or (
            isinstance(checks, list)
            and any(
                isinstance(c, dict) and ("criterion_ids" in c or "test_ids" in c)
                for c in checks
            )
        )
    if kind == "project":
        proposals = doc.get("relationship_proposals", [])
        return isinstance(proposals, list) and any(
            isinstance(r, dict) and r.get("relation") == "uses_component"
            for r in proposals
        )
    return False


def parse_ts(value: Any) -> dt.datetime:
    """Parse an ISO-8601 timestamp string (supporting trailing 'Z')."""
    parsed = dt.datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed


def semantic_doc_errors(
    doc: dict[str, Any],
    now: dt.datetime | None = None,
) -> list[str]:
    """Return per-document semantic validation errors for a PPM contract document."""
    current_now = now or dt.datetime.now(dt.UTC)
    errors: list[str] = []
    if doc.get("contract_version") == "2.0" and uses_21_fields(doc):
        errors.append("document declares 2.0 but uses 2.1 fields")
    for field in ("recorded_at", "updated_at"):
        if field in doc:
            try:
                if parse_ts(doc[field]) > current_now + FUTURE_SKEW:
                    errors.append(f"{field} is more than 24h in the future")
            except ValueError:
                pass  # schema pattern reports malformed timestamps
    if doc.get("kind") == "acceptance":
        criteria = doc.get("criteria", [])
        if isinstance(criteria, list):
            ids = [c["criterion_id"] for c in criteria if isinstance(c, dict) and "criterion_id" in c]
            if len(ids) != len(set(ids)):
                errors.append("duplicate criterion_id in acceptance.yaml")
    if doc.get("kind") == "update" and doc.get("supersedes_update_id") == doc.get("update_id"):
        errors.append("update cannot supersede itself")
    return errors


def semantic_set_errors(docs: list[tuple[Any, dict[str, Any]]]) -> list[str]:
    """Return cross-document semantic errors for a metadata root's ``(path, doc)`` list."""
    errors: list[str] = []
    ids = {
        d["project_id"]
        for _, d in docs
        if "project_id" in d and d.get("kind") != "registry"
    }
    if len(ids) > 1:
        errors.append("project_id mismatch within metadata root")
    updates: dict[str, dict[str, Any]] = {}
    for _, d in docs:
        if d.get("kind") == "update":
            uid = d["update_id"]
            if uid in updates:
                errors.append("duplicate update ID " + uid)
            updates[uid] = d
    for uid, d in updates.items():
        target = d.get("supersedes_update_id")
        if target and target not in updates:
            errors.append(f"update {uid} supersedes unknown update {target}")
    for start in updates:
        seen: set[str] = set()
        cur: str | None = start
        while cur in updates and updates[cur].get("supersedes_update_id"):
            if cur in seen:
                errors.append("supersession cycle involving " + start)
                break
            seen.add(cur)
            cur = updates[cur]["supersedes_update_id"]
    criteria: set[str] = set()
    for _, d in docs:
        if d.get("kind") == "acceptance":
            criteria |= {c["criterion_id"] for c in d.get("criteria", [])}
    for uid, d in updates.items():
        refs: set[str] = set()
        for item in d.get("completed", []) + d.get("in_progress", []) + d.get("checks", []):
            refs |= set(item.get("criterion_ids", []))
        unknown = refs - criteria
        if unknown:
            errors.append(f"update {uid} references unknown criteria {sorted(unknown)}")
    for _, d in docs:
        if d.get("kind") == "registry":
            pids = [x["project_id"] for x in d["projects"]]
            if len(pids) != len(set(pids)):
                errors.append("duplicate registry project_id")
            for proj in d["projects"]:
                lids = [x["location_id"] for x in proj["locations"]]
                if len(lids) != len(set(lids)) or proj["metadata_location_id"] not in lids:
                    errors.append("invalid location identities")
    return sorted(set(errors))


__all__ = [
    "FUTURE_SKEW",
    "parse_ts",
    "semantic_doc_errors",
    "semantic_set_errors",
    "uses_21_fields",
]
