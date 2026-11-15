"""Build and lock deterministic reducer inputs from DB state (P2a-WP4 / P2a-WP5 ISS-034).

Reads:
- Locked ``project_snapshots`` row (and its ``claims`` JSONB for status/project/acceptance payloads per ISS-033).
- ``ppm_updates`` (active plus supersession chain).
- ``ppm_baselines`` (latest approved baseline; draft baselines yield ``None`` -> provisional per C44).
- ``ppm_evidence`` (test/probe results with trust_class, producer, revision, simulated flag).
- ``ppm_waivers`` (approved waivers).

``ppm_notes`` are context only and NEVER create claims or alter criterion status.

``ISS-034`` semantic hashing:
- ``inputs_hash`` hashes semantic content only (``project_id``, ``scope_hash``,
  ``assessed_revision``, ``evidence_ttl_days``, approved ``baseline`` without runtime-injected
  ``previous_status``, ``claims``, ``evidence``, ``waivers``, ``dependency_refs``, ``context``).
- Snapshot IDs (``snapshot_refs``, ``snapshot_seq``) and snapshot time (``source_as_of`` and
  snapshot-derived ``now``) stay in ``inputs_dict`` as lineage outside the hash so rescans of
  unchanged data deduplicate instead of creating duplicate assessments.
- Migration impact: pre-P2a-WP5 rows in ``ppm_assessments`` retain their stored ``inputs_hash``;
  the first assessment scan after upgrading may create at most one new assessment row with the
  semantic hash, after which subsequent unchanged rescans deduplicate cleanly.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from eap.contracts.digest import sha256_digest
from eap.db import (
    collector_snapshots,
    ppm_assessments,
    ppm_baselines,
    ppm_evidence,
    ppm_updates,
    ppm_waivers,
    project_current_assessment,
    project_snapshots,
)


def _iso_utc(val: dt.datetime | str | None) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if val.tzinfo is None:
        val = val.replace(tzinfo=dt.UTC)
    return val.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


def _map_update_row_to_claims(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a ``ppm_updates`` row into reducer claim dict(s) matching inputs.schema.json."""
    payload = dict(row.get("payload") or {})
    update_id = str(row["update_id"])
    source_file = str(row.get("artifact_path") or f".ppm/updates/{update_id}.yaml")
    supersedes_update_id = row.get("supersedes_update_id")
    recorded_at = _iso_utc(row.get("recorded_at")) or payload.get("recorded_at")
    summary = str(payload.get("summary") or "")
    wts_raw = payload.get("working_tree_state")
    wts = wts_raw if wts_raw in ("clean", "dirty", "untracked_only") else None

    # If the payload is already in reducer claim shape (e.g. direct fixture seeding)
    if "claim_id" in payload and "criterion_ids" in payload and "reported_status" in payload:
        claim: dict[str, Any] = {
            "claim_id": str(payload["claim_id"]),
            "source_file": str(payload.get("source_file") or source_file),
            "criterion_ids": list(payload["criterion_ids"]),
            "reported_status": str(payload["reported_status"]),
        }
        if payload.get("test_ids") is not None:
            claim["test_ids"] = list(payload["test_ids"])
        if payload.get("evidence_path") is not None:
            claim["evidence_path"] = payload["evidence_path"]
        if wts is not None:
            claim["working_tree_state"] = wts
        if recorded_at is not None:
            claim["recorded_at"] = str(recorded_at)
        if supersedes_update_id is not None:
            claim["supersedes_update_id"] = str(supersedes_update_id)
        if summary:
            claim["summary"] = summary
        return [claim]

    # Map PPM 2.0/2.1 update document into reducer claims
    claims: list[dict[str, Any]] = []
    checks = payload.get("checks") or []
    for idx, chk in enumerate(checks):
        if not isinstance(chk, dict):
            continue
        cids = [str(c) for c in (chk.get("criterion_ids") or []) if c]
        if not cids:
            continue
        res = str(chk.get("result") or "not_run")
        if res == "passed":
            rep_status = "claimed_done"
        elif res == "failed":
            rep_status = "claimed_blocked"
        else:
            rep_status = "none"
        claim_id = update_id if idx == 0 and len(checks) == 1 else f"{update_id}:chk:{idx}"
        c_dict: dict[str, Any] = {
            "claim_id": claim_id,
            "source_file": source_file,
            "criterion_ids": cids,
            "reported_status": rep_status,
        }
        if chk.get("test_ids"):
            c_dict["test_ids"] = [str(t) for t in chk["test_ids"]]
        if chk.get("evidence_path") is not None:
            c_dict["evidence_path"] = str(chk["evidence_path"])
        if wts is not None:
            c_dict["working_tree_state"] = wts
        if recorded_at is not None:
            c_dict["recorded_at"] = str(recorded_at)
        if supersedes_update_id is not None:
            c_dict["supersedes_update_id"] = str(supersedes_update_id)
        if summary:
            c_dict["summary"] = summary
        claims.append(c_dict)

    # Also extract completed / in_progress / blockers with item_ref if no check already covered them
    if not claims:
        for field_name, rep_status in (
            ("completed", "claimed_done"),
            ("in_progress", "claimed_in_progress"),
            ("blockers", "claimed_blocked"),
        ):
            items = payload.get(field_name) or []
            item_refs: list[str] = []
            for item in items:
                if isinstance(item, dict) and item.get("item_ref"):
                    item_refs.append(str(item["item_ref"]))
            if item_refs:
                c_dict = {
                    "claim_id": f"{update_id}:{field_name}",
                    "source_file": source_file,
                    "criterion_ids": item_refs,
                    "reported_status": rep_status,
                }
                if wts is not None:
                    c_dict["working_tree_state"] = wts
                if recorded_at is not None:
                    c_dict["recorded_at"] = str(recorded_at)
                if supersedes_update_id is not None:
                    c_dict["supersedes_update_id"] = str(supersedes_update_id)
                if summary:
                    c_dict["summary"] = summary
                claims.append(c_dict)

    # Fallback: preserve update node in supersession DAG even if it has no criterion_ids
    if not claims:
        c_dict = {
            "claim_id": update_id,
            "source_file": source_file,
            "criterion_ids": [],
            "reported_status": "none",
        }
        if wts is not None:
            c_dict["working_tree_state"] = wts
        if recorded_at is not None:
            c_dict["recorded_at"] = str(recorded_at)
        if supersedes_update_id is not None:
            c_dict["supersedes_update_id"] = str(supersedes_update_id)
        if summary:
            c_dict["summary"] = summary
        claims.append(c_dict)

    return claims


def _extract_snapshot_claims(
    snapshot_claims: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    """Extract status.yaml claims and project.yaml dependency_refs from project_snapshots.claims.

    ISS-033 decision: status.yaml, project.yaml, and acceptance.yaml payloads stay in
    ``project_snapshots.claims`` and are read from the locked snapshot row rather than
    copied into separate ingest tables. ``acceptance.yaml`` never auto-approves a baseline.
    """
    extracted_claims: list[dict[str, Any]] = []
    dep_refs: list[dict[str, Any]] | None = None

    for item in snapshot_claims:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        data = item.get("data")
        c_hash = str(item.get("content_hash") or "none")[:12]
        if not isinstance(data, dict):
            continue

        # acceptance.yaml is NEVER auto-approved as a baseline (C44)
        if path.endswith("acceptance.yaml"):
            continue

        if path.endswith("project.yaml"):
            raw_deps = data.get("dependency_refs")
            if isinstance(raw_deps, list):
                dep_refs = [dict(d) for d in raw_deps if isinstance(d, dict)]
            continue

        if path.endswith("status.yaml"):
            # Extract structured criterion status claims if present
            raw_criteria = data.get("criteria")
            if isinstance(raw_criteria, dict):
                for idx, (cid, st_val) in enumerate(sorted(raw_criteria.items())):
                    rep = str(st_val) if st_val in (
                        "claimed_done", "claimed_in_progress", "claimed_blocked", "none"
                    ) else "none"
                    extracted_claims.append({
                        "claim_id": f"status:{c_hash}:{idx}",
                        "source_file": path,
                        "criterion_ids": [str(cid)],
                        "reported_status": rep,
                    })
            elif isinstance(raw_criteria, list):
                for idx, entry in enumerate(raw_criteria):
                    if not isinstance(entry, dict) or not entry.get("criterion_id"):
                        continue
                    rep = str(entry.get("reported_status") or "none")
                    if rep not in ("claimed_done", "claimed_in_progress", "claimed_blocked", "none"):
                        rep = "none"
                    extracted_claims.append({
                        "claim_id": f"status:{c_hash}:{idx}",
                        "source_file": path,
                        "criterion_ids": [str(entry["criterion_id"])],
                        "reported_status": rep,
                    })

    return extracted_claims, dep_refs


def build_assessment_inputs(
    conn: sa.Connection | Session,
    tenant_id: str,
    project_id: str,
    *,
    basis_seq: int = 1,
    now: dt.datetime | str | None = None,
    evidence_ttl_days: float = 7.0,
    project_snapshot_id: str | None = None,
    assessed_revision: str | None = None,
    previous_assessment_id: str | None = None,
    dependency_refs: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    fixture_id: str | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Build deterministic reducer input dict (inputs.schema.json) and its canonical hash.

    Returns:
        ``(inputs_dict, inputs_hash, lock_meta)``
    """
    # 1. Lock project snapshot
    if project_snapshot_id is not None:
        psnap = conn.execute(
            sa.select(project_snapshots).where(
                project_snapshots.c.tenant_id == tenant_id,
                project_snapshots.c.project_id == project_id,
                project_snapshots.c.id == project_snapshot_id,
            )
        ).mappings().first()
    else:
        psnap = conn.execute(
            sa.select(project_snapshots)
            .where(
                project_snapshots.c.tenant_id == tenant_id,
                project_snapshots.c.project_id == project_id,
            )
            .order_by(project_snapshots.c.snapshot_seq.desc(), project_snapshots.c.created_at.desc())
            .limit(1)
        ).mappings().first()

    locked_snapshot_id: str | None = None
    snapshot_seq = 1
    snapshot_refs = ["snap-none"]
    scope_hash: str | None = None
    source_as_of: str | None = None
    snapshot_created_at: dt.datetime | None = None
    snapshot_claims_raw: list[dict[str, Any]] = []

    if psnap is not None:
        locked_snapshot_id = str(psnap["id"])
        snapshot_seq = int(psnap["snapshot_seq"])
        snapshot_refs = [locked_snapshot_id]
        scope_hash = str(psnap["scope_hash"]) if psnap.get("scope_hash") is not None else None
        snapshot_created_at = psnap["created_at"]
        source_as_of = _iso_utc(snapshot_created_at)
        snapshot_claims_raw = list(psnap["claims"] or [])

        if assessed_revision is None:
            csnap = conn.execute(
                sa.select(collector_snapshots.c.git_commit, collector_snapshots.c.tree_hash).where(
                    collector_snapshots.c.tenant_id == tenant_id,
                    collector_snapshots.c.id == str(psnap["snapshot_id"]),
                )
            ).mappings().first()
            if csnap is not None:
                assessed_revision = csnap["git_commit"] or csnap["tree_hash"]

    # 2. Previous assessment & prior criterion statuses
    if previous_assessment_id is None:
        curr_row = conn.execute(
            sa.select(project_current_assessment.c.assessment_id).where(
                project_current_assessment.c.tenant_id == tenant_id,
                project_current_assessment.c.project_id == project_id,
            )
        ).mappings().first()
        if curr_row is not None:
            previous_assessment_id = str(curr_row["assessment_id"])

    prev_criteria_status: dict[str, str] = {}
    if previous_assessment_id is not None:
        prev_asmt = conn.execute(
            sa.select(ppm_assessments.c.output).where(
                ppm_assessments.c.tenant_id == tenant_id,
                ppm_assessments.c.id == previous_assessment_id,
            )
        ).mappings().first()
        if prev_asmt is not None and isinstance(prev_asmt["output"], dict):
            for c_out in prev_asmt["output"].get("criteria", []):
                if isinstance(c_out, dict) and c_out.get("criterion_id") and c_out.get("status"):
                    prev_criteria_status[str(c_out["criterion_id"])] = str(c_out["status"])

    # 3. Latest approved baseline (draft baselines are ignored -> provisional per C44)
    bl_row = conn.execute(
        sa.select(ppm_baselines)
        .where(
            ppm_baselines.c.tenant_id == tenant_id,
            ppm_baselines.c.project_id == project_id,
            ppm_baselines.c.status == "approved",
        )
        .order_by(ppm_baselines.c.baseline_revision.desc())
        .limit(1)
    ).mappings().first()

    baseline: dict[str, Any] | None = None
    baseline_for_hash: dict[str, Any] | None = None
    baseline_revision: int | None = None
    if bl_row is not None:
        baseline_revision = int(bl_row["baseline_revision"])
        criteria_sorted = sorted(
            [dict(c) for c in (bl_row["criteria"] or [])],
            key=lambda c: str(c.get("criterion_id", "")),
        )
        # Preserve canonical baseline prior to injecting runtime previous_status so
        # initial scan and first unchanged rescan yield identical semantic hashes (ISS-034).
        baseline_for_hash = {
            "baseline_revision": baseline_revision,
            "criteria": copy.deepcopy(criteria_sorted),
        }
        for c_item in criteria_sorted:
            if "previous_status" not in c_item and str(c_item.get("criterion_id", "")) in prev_criteria_status:
                c_item["previous_status"] = prev_criteria_status[str(c_item["criterion_id"])]
        baseline = {
            "baseline_revision": baseline_revision,
            "criteria": criteria_sorted,
        }

    # 4. Claims from ppm_updates (active + supersession chain) and locked snapshot claims
    update_rows = conn.execute(
        sa.select(ppm_updates)
        .where(
            ppm_updates.c.tenant_id == tenant_id,
            ppm_updates.c.project_id == project_id,
            ppm_updates.c.supersession_state.in_(["active", "superseded"]),
        )
        .order_by(ppm_updates.c.recorded_at.asc().nulls_last(), ppm_updates.c.update_id.asc())
    ).mappings().all()

    claims: list[dict[str, Any]] = []
    for u_row in update_rows:
        claims.extend(_map_update_row_to_claims(dict(u_row)))

    snap_status_claims, snap_dep_refs = _extract_snapshot_claims(snapshot_claims_raw)
    claims.extend(snap_status_claims)

    # Note: ppm_notes are NEVER converted into claims (context only).

    # 5. Evidence from ppm_evidence
    ev_rows = conn.execute(
        sa.select(ppm_evidence)
        .where(
            ppm_evidence.c.tenant_id == tenant_id,
            ppm_evidence.c.project_id == project_id,
        )
        .order_by(ppm_evidence.c.collected_at.asc(), ppm_evidence.c.evidence_id.asc())
    ).mappings().all()

    evidence: list[dict[str, Any]] = []
    latest_collected_at: dt.datetime | None = None
    for e_row in ev_rows:
        c_at = e_row["collected_at"]
        if isinstance(c_at, dt.datetime):
            c_at_utc = c_at.replace(tzinfo=dt.UTC) if c_at.tzinfo is None else c_at.astimezone(dt.UTC)
            if latest_collected_at is None or c_at_utc > latest_collected_at:
                latest_collected_at = c_at_utc
        evidence.append({
            "evidence_id": str(e_row["evidence_id"]),
            "trust_class": str(e_row["trust_class"]),
            "revision": e_row["revision"],
            "observed_at": _iso_utc(c_at),
            "test_id": e_row["test_id"],
            "covered_test_ids": list(e_row["covered_test_ids"] or []),
            "result": str(e_row["outcome"]),
            "artifact_path": e_row["artifact_path"],
        })

    # 6. Waivers from ppm_waivers
    waiver_rows = conn.execute(
        sa.select(ppm_waivers)
        .where(
            ppm_waivers.c.tenant_id == tenant_id,
            ppm_waivers.c.project_id == project_id,
        )
        .order_by(ppm_waivers.c.waiver_id.asc())
    ).mappings().all()

    waivers: list[dict[str, Any]] = []
    for w_row in waiver_rows:
        waivers.append({
            "waiver_id": str(w_row["waiver_id"]),
            "criterion_id": str(w_row["criterion_id"]),
            "scope": str(w_row["scope"]),
            "rationale": str(w_row["rationale"]),
            "approved_by": w_row["approved_by"],
            "approved_at": _iso_utc(w_row["approved_at"]),
            "expires_at": _iso_utc(w_row["expires_at"]),
        })

    # 7. Deterministic 'now' if not explicitly supplied
    if now is not None:
        now_iso = _iso_utc(now) or ""
    else:
        candidates: list[dt.datetime] = []
        if snapshot_created_at is not None:
            sc = snapshot_created_at
            candidates.append(sc.replace(tzinfo=dt.UTC) if sc.tzinfo is None else sc.astimezone(dt.UTC))
        if latest_collected_at is not None:
            candidates.append(latest_collected_at)
        if candidates:
            now_iso = _iso_utc(max(candidates)) or ""
        else:
            now_iso = _iso_utc(dt.datetime.now(dt.UTC)) or ""

    resolved_deps = dependency_refs if dependency_refs is not None else (snap_dep_refs or [])

    inputs_dict: dict[str, Any] = {
        "fixture_id": fixture_id or f"db-{project_id}",
        "project_id": project_id,
        "snapshot_refs": snapshot_refs,
        "snapshot_seq": snapshot_seq,
        "basis_seq": basis_seq,
        "previous_assessment_id": previous_assessment_id,
        "assessed_revision": assessed_revision,
        "now": now_iso,
        "source_as_of": source_as_of,
        "evidence_ttl_days": evidence_ttl_days,
        "baseline": baseline,
        "claims": claims,
        "evidence": evidence,
        "waivers": waivers,
    }
    if resolved_deps:
        inputs_dict["dependency_refs"] = resolved_deps
    if context is not None:
        inputs_dict["context"] = context

    # Compute deterministic semantic inputs_hash (ISS-034):
    # Keeps snapshot ids (snapshot_refs, snapshot_seq), snapshot time (source_as_of,
    # snapshot-derived now), basis_seq, and previous_assessment_id as lineage outside the hash.
    hash_payload = {
        "project_id": project_id,
        "scope_hash": scope_hash,
        "assessed_revision": assessed_revision,
        "evidence_ttl_days": evidence_ttl_days,
        "baseline": baseline_for_hash,
        "claims": claims,
        "evidence": evidence,
        "waivers": waivers,
        "dependency_refs": resolved_deps,
        "context": context,
    }
    inputs_hash = sha256_digest(hash_payload)

    lock_meta = {
        "project_snapshot_id": locked_snapshot_id,
        "snapshot_seq": snapshot_seq,
        "scope_hash": scope_hash,
        "baseline_revision": baseline_revision,
        "assessed_revision": assessed_revision,
        "previous_assessment_id": previous_assessment_id,
    }
    return inputs_dict, inputs_hash, lock_meta


__all__ = ["build_assessment_inputs"]
