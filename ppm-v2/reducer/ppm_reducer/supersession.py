"""Supersession resolution for PPM update claims.

Pure module: only allowed stdlib imports (typing).
Rule: Resolve supersession first; cycles or dangling refs -> raise InputRejected.
"""

from typing import Any

from .types import InputRejected


def extract_update_id(claim: dict[str, Any]) -> str:
    """Derive canonical update identifier from update_id, source_file stem, or claim_id."""
    upd_id = claim.get("update_id")
    if upd_id is not None:
        return str(upd_id)
    source_file = claim.get("source_file")
    if source_file:
        filename = str(source_file).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        stem = filename.rsplit(".", 1)[0]
        if stem:
            return str(stem)
    cid = claim.get("claim_id")
    return str(cid) if cid is not None else ""


def resolve_supersession(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate supersession DAG and return active (non-superseded) claims.

    Raises:
        InputRejected: With reason 'supersession_cycle' or 'dangling_supersession'.
    """
    if not claims:
        return []

    # Map all aliases to canonical update ID
    canonical_map: dict[str, str] = {}
    for claim in claims:
        uid = extract_update_id(claim)
        canonical_map[uid] = uid
        cid = claim.get("claim_id")
        if cid:
            canonical_map[str(cid)] = uid

    graph: dict[str, str] = {}
    for claim in claims:
        target = claim.get("supersedes_update_id")
        if not target:
            continue
        target_str = str(target)
        if target_str not in canonical_map:
            raise InputRejected(
                reason="dangling_supersession",
                message=f"Claim references unknown supersedes_update_id: {target_str}",
            )
        source_uid = extract_update_id(claim)
        target_uid = canonical_map[target_str]
        if source_uid == target_uid:
            raise InputRejected(
                reason="supersession_cycle",
                message=f"Update {source_uid} cannot supersede itself",
            )
        graph[source_uid] = target_uid

    # Cycle detection across all paths
    for start in graph:
        seen: set[str] = set()
        cur = start
        while cur in graph:
            if cur in seen:
                raise InputRejected(
                    reason="supersession_cycle",
                    message=f"Supersession cycle involving update '{start}'",
                )
            seen.add(cur)
            cur = graph[cur]

    # Active resolution: filter out claims belonging to superseded updates
    superseded_uids = set(graph.values())
    active: list[dict[str, Any]] = []
    for claim in claims:
        uid = extract_update_id(claim)
        if uid in superseded_uids:
            continue
        active.append(claim)

    return active
