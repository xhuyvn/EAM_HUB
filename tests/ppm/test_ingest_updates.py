"""Tests for PPM update ingestion: C17 supersession, dedup, conflict, orphans, cycles, late updates, and shared components (P2a-WP3)."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import yaml
from sqlalchemy.orm import Session

from eap.db import (
    collector_snapshots,
    location_registrations,
    outbox,
    ppm_ingest_checkpoints,
    ppm_ingested_artifacts,
    ppm_updates,
    project_scopes,
    project_snapshots,
    sources,
)
from eap.ppm.collector.collector import PpmCollector
from eap.ppm.ingest import get_supersession_chain, ingest_snapshot
from eap.ppm.path import canonicalize_root

TEMPLATE_UPDATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "ppm-v2"
    / "templates"
    / "project"
    / ".ppm"
    / "updates"
    / "upd-example-001.yaml"
)


def _base_update(
    *,
    project_id: str = "proj-alpha",
    update_id: str = "upd-001",
    supersedes_update_id: str | None = None,
    recorded_at: str = "2026-11-05T11:50:00Z",
    summary: str = "Implemented CSV export.",
    contract_version: str = "2.1",
) -> dict[str, Any]:
    doc = json.loads(TEMPLATE_UPDATE_PATH.read_text(encoding="utf-8"))
    doc["contract_version"] = contract_version
    doc["project_id"] = project_id
    doc["update_id"] = update_id
    doc["supersedes_update_id"] = supersedes_update_id
    doc["recorded_at"] = recorded_at
    doc["summary"] = summary
    return dict(doc)


def _seed_source_and_scope(
    conn: sa.Connection | Session,
    *,
    tenant_id: str,
    source_id: str | None = None,
    project_id: str,
    root: Path,
    metadata_root: str = ".ppm",
) -> str:
    source_id = source_id or f"src-{uuid.uuid4().hex[:10]}"
    canon = canonicalize_root(str(root), "windows" if str(root)[1:2] == ":" else "linux")
    now = dt.datetime.now(dt.UTC)
    conn.execute(
        sources.insert().values(
            source_id=source_id,
            tenant_id=tenant_id,
            canonical_root=canon,
            repo_identity=f"git@example.com:{tenant_id}/{source_id}.git",
            created_at=now,
            updated_at=now,
        )
    )
    conn.execute(
        project_scopes.insert().values(
            id=f"scope-{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            source_id=source_id,
            project_id=project_id,
            metadata_root=metadata_root,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    conn.execute(
        location_registrations.insert().values(
            id=f"loc-{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            source_id=source_id,
            project_id=project_id,
            metadata_root=metadata_root,
            state="active",
            revision=1,
            registered_by_ref="owner",
            host_id="host-test",
            connector_id="conn-test",
            root_path=str(root),
            scan_scope=[metadata_root],
            artifact_aliases={"architecture": [], "blueprint": [], "implementation_plan": []},
            notes_globs=[f"{metadata_root}/notes/*.md"],
            deny_globs=[],
            classification="internal",
            egress_policy_ref="local_only",
            identity={"repo_identity": f"git@example.com:{tenant_id}/{source_id}.git"},
            created_at=now,
            updated_at=now,
        )
    )
    return source_id


def _insert_snapshot(
    conn: sa.Connection,
    *,
    tenant_id: str,
    source_id: str,
    project_id: str,
    snapshot_seq: int,
    claims: list[dict[str, Any]],
    notes: list[dict[str, Any]] | None = None,
    partial_reasons: list[str] | None = None,
    metadata_root: str = ".ppm",
) -> str:
    now = dt.datetime.now(dt.UTC)
    snap_id = f"snap-{uuid.uuid4().hex[:16]}"
    psnap_id = f"psnap-{uuid.uuid4().hex[:16]}"
    manifest: dict[str, Any] = {}
    for c in claims:
        if "path" in c and "content_hash" in c:
            manifest[c["path"]] = {"size": 100, "mtime": 0.0, "sha256": c["content_hash"]}
    conn.execute(
        collector_snapshots.insert().values(
            id=snap_id,
            tenant_id=tenant_id,
            source_id=source_id,
            status="captured",
            mixed_snapshot=False,
            partial_reasons=partial_reasons or [],
            tree_hash=hashlib.sha256(snap_id.encode()).hexdigest(),
            manifest=manifest,
            created_at=now,
        )
    )
    conn.execute(
        project_snapshots.insert().values(
            id=psnap_id,
            tenant_id=tenant_id,
            snapshot_id=snap_id,
            source_id=source_id,
            project_id=project_id,
            snapshot_seq=snapshot_seq,
            scope_hash=hashlib.sha256(psnap_id.encode()).hexdigest(),
            metadata_root=metadata_root,
            status="captured",
            mixed_snapshot=False,
            partial_reasons=partial_reasons or [],
            artifact_aliases={},
            notes=notes or [],
            claims=claims,
            created_at=now,
        )
    )
    return psnap_id


def _claim(doc: dict[str, Any], path: str, raw_text: str | None = None) -> dict[str, Any]:
    text = raw_text if raw_text is not None else yaml.safe_dump(doc, sort_keys=False)
    return {
        "path": path,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "data": copy.deepcopy(doc),
    }


def test_c17_supersession_chain_visible(db_engine: sa.Engine, tmp_path: Path) -> None:
    """C17: Old update is retained as superseded; chain query returns [old, new] in order."""
    tenant = "tenant-c17"
    with db_engine.begin() as conn:
        src_id = _seed_source_and_scope(conn, tenant_id=tenant, project_id="proj-alpha", root=tmp_path)
        u1 = _base_update(update_id="upd-001", summary="Initial claim")
        ps1 = _insert_snapshot(
            conn,
            tenant_id=tenant,
            source_id=src_id,
            project_id="proj-alpha",
            snapshot_seq=1,
            claims=[_claim(u1, ".ppm/updates/upd-001.yaml")],
        )
        r1 = ingest_snapshot(conn, tenant, ps1)
        assert r1.counts["ingested"] == 1

        u2 = _base_update(
            update_id="upd-002",
            supersedes_update_id="upd-001",
            recorded_at="2026-11-05T12:10:00Z",
            summary="Correction of upd-001",
        )
        ps2 = _insert_snapshot(
            conn,
            tenant_id=tenant,
            source_id=src_id,
            project_id="proj-alpha",
            snapshot_seq=2,
            claims=[_claim(u1, ".ppm/updates/upd-001.yaml"), _claim(u2, ".ppm/updates/upd-002.yaml")],
        )
        r2 = ingest_snapshot(conn, tenant, ps2)
        assert r2.counts["ingested"] == 1
        assert r2.counts["duplicate"] == 1

        chain = get_supersession_chain(conn, tenant, "proj-alpha", "upd-002")
        assert [row["update_id"] for row in chain] == ["upd-001", "upd-002"]
        assert chain[0]["supersession_state"] == "superseded"
        assert chain[1]["supersession_state"] == "active"


def test_duplicate_same_bytes_and_conflict_different_bytes(
    db_engine: sa.Engine, tmp_path: Path
) -> None:
    """Same update_id + same bytes -> duplicate no-op; same update_id + different bytes -> conflict, first payload preserved."""
    tenant = "tenant-dup"
    with db_engine.begin() as conn:
        src_id = _seed_source_and_scope(conn, tenant_id=tenant, project_id="proj-alpha", root=tmp_path)
        u1 = _base_update(update_id="upd-001", summary="Original payload")
        ps1 = _insert_snapshot(
            conn,
            tenant_id=tenant,
            source_id=src_id,
            project_id="proj-alpha",
            snapshot_seq=1,
            claims=[_claim(u1, ".ppm/updates/upd-001.yaml")],
        )
        ingest_snapshot(conn, tenant, ps1)

        # Same bytes at a new path -> duplicate no-op
        ps2 = _insert_snapshot(
            conn,
            tenant_id=tenant,
            source_id=src_id,
            project_id="proj-alpha",
            snapshot_seq=2,
            claims=[_claim(u1, ".ppm/updates/upd-001.yaml")],
        )
        r2 = ingest_snapshot(conn, tenant, ps2)
        assert r2.counts["duplicate"] == 1
        assert r2.counts["conflict"] == 0

        # Different bytes with same update_id -> conflict, first payload unchanged
        u1_tampered = _base_update(update_id="upd-001", summary="Tampered payload!")
        ps3 = _insert_snapshot(
            conn,
            tenant_id=tenant,
            source_id=src_id,
            project_id="proj-alpha",
            snapshot_seq=3,
            claims=[_claim(u1_tampered, ".ppm/updates/upd-001.yaml")],
        )
        r3 = ingest_snapshot(conn, tenant, ps3)
        assert r3.counts["conflict"] == 1

        stored = conn.execute(
            sa.select(ppm_updates.c.payload).where(
                ppm_updates.c.tenant_id == tenant,
                ppm_updates.c.project_id == "proj-alpha",
                ppm_updates.c.update_id == "upd-001",
            )
        ).scalar_one()
        assert stored["summary"] == "Original payload"

        conflict_art = conn.execute(
            sa.select(ppm_ingested_artifacts.c.outcome, ppm_ingested_artifacts.c.reason_code).where(
                ppm_ingested_artifacts.c.tenant_id == tenant,
                ppm_ingested_artifacts.c.outcome == "conflict",
            )
        ).mappings().first()
        assert conflict_art is not None
        assert conflict_art["reason_code"] == "update_id_conflict"


def test_orphan_supersedes_resolved_and_cycle_conflict(
    db_engine: sa.Engine, tmp_path: Path
) -> None:
    """Orphan supersedes becomes active and marks target superseded when target arrives; cycle marks both conflict."""
    tenant = "tenant-orphan"
    with db_engine.begin() as conn:
        src_id = _seed_source_and_scope(conn, tenant_id=tenant, project_id="proj-alpha", root=tmp_path)
        u2 = _base_update(update_id="upd-002", supersedes_update_id="upd-001", summary="Supersedes 001")
        ps1 = _insert_snapshot(
            conn,
            tenant_id=tenant,
            source_id=src_id,
            project_id="proj-alpha",
            snapshot_seq=1,
            claims=[_claim(u2, ".ppm/updates/upd-002.yaml")],
        )
        ingest_snapshot(conn, tenant, ps1)
        s2 = conn.execute(
            sa.select(ppm_updates.c.supersession_state).where(
                ppm_updates.c.tenant_id == tenant,
                ppm_updates.c.update_id == "upd-002",
            )
        ).scalar_one()
        assert s2 == "orphan_supersedes"

        # Now upd-001 arrives
        u1 = _base_update(update_id="upd-001", summary="Original 001")
        ps2 = _insert_snapshot(
            conn,
            tenant_id=tenant,
            source_id=src_id,
            project_id="proj-alpha",
            snapshot_seq=2,
            claims=[_claim(u1, ".ppm/updates/upd-001.yaml")],
        )
        ingest_snapshot(conn, tenant, ps2)

        states = dict(
            conn.execute(
                sa.select(ppm_updates.c.update_id, ppm_updates.c.supersession_state).where(
                    ppm_updates.c.tenant_id == tenant,
                    ppm_updates.c.project_id == "proj-alpha",
                )
            ).all()
        )
        assert states == {"upd-001": "superseded", "upd-002": "active"}

        # Cycle: upd-a supersedes upd-b (orphan), then upd-b supersedes upd-a -> both conflict
        ua = _base_update(update_id="upd-a", supersedes_update_id="upd-b", summary="A")
        ub = _base_update(update_id="upd-b", supersedes_update_id="upd-a", summary="B")
        ps3 = _insert_snapshot(
            conn,
            tenant_id=tenant,
            source_id=src_id,
            project_id="proj-alpha",
            snapshot_seq=3,
            claims=[_claim(ua, ".ppm/updates/upd-a.yaml"), _claim(ub, ".ppm/updates/upd-b.yaml")],
        )
        ingest_snapshot(conn, tenant, ps3)
        cycle_states = dict(
            conn.execute(
                sa.select(ppm_updates.c.update_id, ppm_updates.c.supersession_state).where(
                    ppm_updates.c.tenant_id == tenant,
                    ppm_updates.c.update_id.in_(["upd-a", "upd-b"]),
                )
            ).all()
        )
        assert cycle_states == {"upd-a": "conflict", "upd-b": "conflict"}


def test_late_arriving_update_ingested(db_engine: sa.Engine, tmp_path: Path) -> None:
    """Updates with older recorded_at timestamps are still ingested (timestamps are not a cursor)."""
    tenant = "tenant-late"
    with db_engine.begin() as conn:
        src_id = _seed_source_and_scope(conn, tenant_id=tenant, project_id="proj-alpha", root=tmp_path)
        u_new = _base_update(update_id="upd-new", recorded_at="2026-11-10T12:00:00Z", summary="Newer ts")
        ps1 = _insert_snapshot(
            conn,
            tenant_id=tenant,
            source_id=src_id,
            project_id="proj-alpha",
            snapshot_seq=1,
            claims=[_claim(u_new, ".ppm/updates/upd-new.yaml")],
        )
        ingest_snapshot(conn, tenant, ps1)

        u_old = _base_update(update_id="upd-old", recorded_at="2026-10-01T08:00:00Z", summary="Older ts")
        ps2 = _insert_snapshot(
            conn,
            tenant_id=tenant,
            source_id=src_id,
            project_id="proj-alpha",
            snapshot_seq=2,
            claims=[_claim(u_old, ".ppm/updates/upd-old.yaml")],
        )
        r2 = ingest_snapshot(conn, tenant, ps2)
        assert r2.counts["ingested"] == 1

        uids = conn.execute(
            sa.select(ppm_updates.c.update_id).where(ppm_updates.c.tenant_id == tenant)
        ).scalars().all()
        assert set(uids) == {"upd-new", "upd-old"}


def test_shared_component_s08_no_duplication(db_engine: sa.Engine, tmp_path: Path) -> None:
    """s08 pattern: an update in a shared component is stored once under that project, never duplicated into consumers."""
    tenant = "tenant-s08"
    shared_root = tmp_path / "shared"
    cons_a_root = tmp_path / "consumer_a"
    cons_b_root = tmp_path / "consumer_b"
    for r in (shared_root, cons_a_root, cons_b_root):
        (r / ".ppm" / "updates").mkdir(parents=True, exist_ok=True)
        (r / ".ppm" / "notes").mkdir(parents=True, exist_ok=True)

    # Shared component project + update
    u_shared = _base_update(project_id="proj-shared", update_id="upd-shared-001", summary="Shared lib v2")
    (shared_root / ".ppm" / "updates" / "upd-shared-001.yaml").write_text(
        yaml.safe_dump(u_shared, sort_keys=False), encoding="utf-8"
    )

    # Two consumers referencing proj-shared via uses_component
    for pid, root in (("proj-cons-a", cons_a_root), ("proj-cons-b", cons_b_root)):
        proj_doc = {
            "contract_version": "2.1",
            "kind": "project",
            "project_id": pid,
            "name": pid,
            "owner_ref": "owner-1",
            "objectives": [{"objective_id": "OBJ-1", "statement": "Use shared", "metric": None}],
            "artifact_mapping": {"architecture": None, "blueprint": None, "implementation_plan": None},
            "notes_paths": [".ppm/notes/"],
            "relationship_proposals": [{"target_project_id": "proj-shared", "relation": "uses_component"}],
        }
        (root / ".ppm" / "project.yaml").write_text(
            yaml.safe_dump(proj_doc, sort_keys=False), encoding="utf-8"
        )

    with Session(db_engine) as db:
        _seed_source_and_scope(db, tenant_id=tenant, source_id="src-shared", project_id="proj-shared", root=shared_root)
        _seed_source_and_scope(db, tenant_id=tenant, source_id="src-cons-a", project_id="proj-cons-a", root=cons_a_root)
        _seed_source_and_scope(db, tenant_id=tenant, source_id="src-cons-b", project_id="proj-cons-b", root=cons_b_root)
        db.commit()

        collector = PpmCollector(allow_real_roots=False, fixtures_root=str(tmp_path))
        collector.scan_source(db, tenant_id=tenant, source_id="src-shared")
        collector.scan_source(db, tenant_id=tenant, source_id="src-cons-a")
        collector.scan_source(db, tenant_id=tenant, source_id="src-cons-b")

        rows = db.execute(
            sa.select(ppm_updates.c.project_id, ppm_updates.c.update_id).where(
                ppm_updates.c.tenant_id == tenant
            )
        ).all()
        assert rows == [("proj-shared", "upd-shared-001")]

        ckpt_count = db.execute(
            sa.select(sa.func.count()).select_from(ppm_ingest_checkpoints).where(
                ppm_ingest_checkpoints.c.tenant_id == tenant
            )
        ).scalar_one()
        assert ckpt_count == 3

        outbox_events = db.execute(
            sa.select(outbox.c.event_type).where(
                outbox.c.tenant_id == tenant,
                outbox.c.event_type == "ppm.ingest.completed",
            )
        ).scalars().all()
        assert len(outbox_events) == 3
