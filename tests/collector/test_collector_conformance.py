"""Conformance and integration tests for PpmCollector (P2a-WP2).

Covers:
- C16: File modified during scan via a racing thread -> mixed_snapshot=True, status="partial".
- C27: Symlink, hardlink, traversal tricks -> refused + security finding + partial scan.
- C42: Matching project_id on source with differing repo_identity -> identity_conflict,
  location quarantined, outbox event emitted, nothing merged.
- pending_share and revoked share -> unreadable (PermissionError).
- Root replaced by another directory between scans -> refused (RootReplacedSecurityError).
- Path vanishing mid-scan -> partial scan, no crash.
- Oversized file -> partial scan, no crash.
- Duplicate YAML keys -> partial scan, parse error logged.
- Deny globs -> excluded files omitted.
- Monotonic snapshot_seq per (source_id, project_id).
- 10,000-file fixture throughput: measures and reports files/s.
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from eap.db import (
    collector_snapshots,
    identity_conflicts,
    location_registrations,
    outbox,
    project_scopes,
    project_snapshots,
    source_shares,
    sources,
)
from eap.ppm.collector import (
    PpmCollector,
    RootNotPermittedSecurityError,
    RootReplacedSecurityError,
)


def _setup_ppm_project_tree(
    root: Path,
    *,
    project_id: str = "proj-demo",
    metadata_root: str = ".ppm",
    extra_files: dict[str, str] | None = None,
) -> None:
    ppm_dir = root / metadata_root
    ppm_dir.mkdir(parents=True, exist_ok=True)
    updates_dir = ppm_dir / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    notes_dir = ppm_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    # 1. project.yaml
    (ppm_dir / "project.yaml").write_text(
        f"""contract_version: "2.0"
kind: project
project_id: "{project_id}"
title: "Demo Project"
artifact_mapping:
  architecture: "docs/ARCHITECTURE.md"
  blueprint: "docs/MASTER_BLUEPRINT.md"
  implementation_plan: "docs/IMPLEMENTATION_PLAN.md"
""",
        encoding="utf-8",
    )

    # 2. status.yaml
    (ppm_dir / "status.yaml").write_text(
        f"""contract_version: "2.0"
kind: status
project_id: "{project_id}"
updated_at: "2026-11-08T12:00:00Z"
overall_readiness: not_ready
""",
        encoding="utf-8",
    )

    # 3. updates/upd-001.yaml
    (updates_dir / "upd-001.yaml").write_text(
        f"""contract_version: "2.0"
kind: update
update_id: "upd-001"
project_id: "{project_id}"
recorded_at: "2026-11-08T12:00:00Z"
claims:
  - criterion_id: "AC-001"
    result: passed
""",
        encoding="utf-8",
    )

    # 4. notes/note-001.md
    (notes_dir / "note-001.md").write_text("# Progress note\nExport feature done.\n", encoding="utf-8")

    # 5. Core docs
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "ARCHITECTURE.md").write_text("# Architecture\n", encoding="utf-8")
    (docs_dir / "MASTER_BLUEPRINT.md").write_text("# Blueprint\n", encoding="utf-8")
    (docs_dir / "IMPLEMENTATION_PLAN.md").write_text("# Plan\n", encoding="utf-8")

    if extra_files:
        for rel_p, content in extra_files.items():
            fpath = root / rel_p
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")


def _register_source_and_scope(
    db: Session,
    *,
    tenant_id: str,
    source_id: str,
    root_path: str,
    repo_identity: str,
    project_id: str,
    metadata_root: str = ".ppm",
    scan_scope: list[str] | None = None,
    deny_globs: list[str] | None = None,
    max_file_bytes: int = 16777216,
) -> str:
    now = dt.datetime.now(dt.UTC)
    scope_id = f"scope-{source_id}-{project_id}"
    loc_id = f"loc-{source_id}-{project_id}"

    # Insert source
    db.execute(
        sources.insert().values(
            source_id=source_id,
            tenant_id=tenant_id,
            canonical_root=root_path,
            repo_identity=repo_identity,
            created_at=now,
            updated_at=now,
        )
    )

    # Insert project scope
    db.execute(
        project_scopes.insert().values(
            id=scope_id,
            tenant_id=tenant_id,
            source_id=source_id,
            project_id=project_id,
            metadata_root=metadata_root,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )

    # Insert location registration
    db.execute(
        location_registrations.insert().values(
            id=loc_id,
            tenant_id=tenant_id,
            source_id=source_id,
            project_id=project_id,
            metadata_root=metadata_root,
            state="active",
            revision=1,
            registered_by_ref="principal-test",
            approved_by_ref="principal-owner",
            host_id="host-test-1",
            connector_id="connector-test-1",
            root_path=root_path,
            scan_scope=scan_scope or [metadata_root, "docs"],
            artifact_aliases={
                "architecture": ["docs/ARCHITECTURE.md"],
                "blueprint": ["docs/MASTER_BLUEPRINT.md"],
                "implementation_plan": ["docs/IMPLEMENTATION_PLAN.md"],
            },
            notes_globs=[f"{metadata_root}/notes/**/*.md"],
            deny_globs=deny_globs or [],
            classification="internal",
            egress_policy_ref="policy-local",
            identity={"canonical_root_fingerprint": root_path, "repo_identity": repo_identity},
            follow_symlinks=False,
            allow_hardlinked_files=False,
            max_file_bytes=max_file_bytes,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return loc_id


def test_safety_boundary_refuses_root_outside_temp(db_session: Session, tmp_path: Path):
    """When allow_real_roots is false, collector strictly refuses roots outside temp/fixtures."""
    collector = PpmCollector(allow_real_roots=False, fixtures_root=None)
    fake_root = "C:/Windows/System32" if sys.platform == "win32" else "/etc"

    tenant = "tenant-safe-1"
    src_id = "src-forbidden-1"

    now = dt.datetime.now(dt.UTC)
    db_session.execute(
        sources.insert().values(
            source_id=src_id,
            tenant_id=tenant,
            canonical_root=fake_root,
            repo_identity="git@example.com:forbidden.git",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    with pytest.raises(RootNotPermittedSecurityError):
        collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)


def test_pending_and_revoked_shares_are_unreadable(db_session: Session, tmp_path: Path):
    """Sources whose share is pending_share or revoked are unreadable (P2a-WP1 L3)."""
    root = tmp_path / "shared_repo"
    root.mkdir()
    _setup_ppm_project_tree(root, project_id="proj-shared")

    tenant = "tenant-requester"
    src_id = "src-shared-1"
    now = dt.datetime.now(dt.UTC)

    _register_source_and_scope(
        db_session,
        tenant_id=tenant,
        source_id=src_id,
        root_path=str(root),
        repo_identity="git@example.com:shared.git",
        project_id="proj-shared",
    )

    # 1. Insert share in pending_share state
    db_session.execute(
        source_shares.insert().values(
            id="share-1",
            host_id="host-test-1",
            canonical_root_key="key-shared",
            owner_tenant_id="tenant-owner",
            owner_source_id="src-owner-1",
            requester_tenant_id=tenant,
            requester_source_id=src_id,
            requester_principal_id="principal-req",
            state="pending_share",
            created_at=now,
        )
    )
    db_session.commit()

    collector = PpmCollector(allow_real_roots=True)
    with pytest.raises(PermissionError) as exc_info:
        collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)
    assert "pending_share" in str(exc_info.value)

    # 2. Update share to revoked
    db_session.execute(
        source_shares.update()
        .where(source_shares.c.id == "share-1")
        .values(state="revoked", revoked_at=now)
    )
    db_session.commit()

    with pytest.raises(PermissionError) as exc_info2:
        collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)
    assert "revoked" in str(exc_info2.value)


def test_root_replaced_between_scans_refused(db_session: Session, tmp_path: Path):
    """If physical filesystem identity of root changed between scans, scan is refused (ISS-028)."""
    dir1 = tmp_path / "original_dir"
    dir1.mkdir()
    _setup_ppm_project_tree(dir1, project_id="proj-replace")

    tenant = "tenant-fsident-1"
    src_id = "src-replace-1"

    _register_source_and_scope(
        db_session,
        tenant_id=tenant,
        source_id=src_id,
        root_path=str(dir1),
        repo_identity="git@example.com:replace.git",
        project_id="proj-replace",
    )

    collector = PpmCollector(allow_real_roots=True)

    # Scan 1: Records physical identity
    res1 = collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)
    assert res1.status == "captured"

    # Simulate root directory moved / replaced on disk:
    # Rename original dir away, create a new directory at the same path
    dir2 = tmp_path / "temp_swap"
    dir1.rename(dir2)
    dir1.mkdir()
    _setup_ppm_project_tree(dir1, project_id="proj-replace")

    # Scan 2: Must detect root filesystem identity change and refuse scan!
    with pytest.raises(RootReplacedSecurityError) as exc_info:
        collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)
    assert "Root physical identity changed" in str(exc_info.value)


def test_c16_racing_modification_causes_mixed_snapshot(db_session: Session, tmp_path: Path):
    """C16: A file modified during scan via a racing thread causes mixed_snapshot=True and partial status."""
    import threading

    from eap.ppm.collector import safe_open as real_safe_open

    root = tmp_path / "racing_repo"
    root.mkdir()
    _setup_ppm_project_tree(root, project_id="proj-c16")

    tenant = "tenant-c16"
    src_id = "src-c16-1"

    _register_source_and_scope(
        db_session,
        tenant_id=tenant,
        source_id=src_id,
        root_path=str(root),
        repo_identity="git@example.com:c16.git",
        project_id="proj-c16",
    )

    collector = PpmCollector(allow_real_roots=True)

    ev_read = threading.Event()
    ev_modified = threading.Event()

    def racing_worker():
        ev_read.wait(timeout=5.0)
        arch_file = root / "docs" / "ARCHITECTURE.md"
        time.sleep(0.02)
        arch_file.write_text("# Racing thread mutation mid-scan\n", encoding="utf-8")
        ev_modified.set()

    arch_seen = False

    def wrapped_safe_open(root_p, rel_p, *args, **kwargs):
        nonlocal arch_seen
        f = real_safe_open(root_p, rel_p, *args, **kwargs)
        if "ARCHITECTURE.md" in str(rel_p):
            arch_seen = True
        elif arch_seen and not ev_modified.is_set():
            ev_read.set()
            ev_modified.wait(timeout=5.0)
        return f

    t = threading.Thread(target=racing_worker)
    t.start()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("eap.ppm.collector.collector.safe_open", wrapped_safe_open)
        res = collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)

    t.join(timeout=5.0)

    assert res.mixed_snapshot is True
    assert res.status == "partial"
    assert any("file_modified_during_scan" in r for r in res.partial_reasons)

    # Check persistence in database
    db_snap = db_session.execute(
        sa.select(collector_snapshots).where(collector_snapshots.c.id == res.snapshot_id)
    ).mappings().first()
    assert db_snap is not None
    assert db_snap["mixed_snapshot"] is True
    assert db_snap["status"] == "partial"

    proj_snap = db_session.execute(
        sa.select(project_snapshots).where(project_snapshots.c.snapshot_id == res.snapshot_id)
    ).mappings().first()
    assert proj_snap is not None
    assert proj_snap["mixed_snapshot"] is True
    assert proj_snap["status"] == "partial"


def test_c27_security_findings_produce_partial_scan(db_session: Session, tmp_path: Path):
    """C27: Hardlink or traversal attempt in scan scope produces security finding and partial scan."""
    root = tmp_path / "c27_root"
    root.mkdir()
    outside = tmp_path / "c27_outside"
    outside.mkdir()
    (outside / "leak.txt").write_text("outside secret", encoding="utf-8")

    _setup_ppm_project_tree(root, project_id="proj-c27")

    # Create hardlink pointing to outside
    hl_path = root / "docs" / "hardlink_escape.txt"
    try:
        import os
        os.link(str(outside / "leak.txt"), str(hl_path))
    except (OSError, NotImplementedError):
        pytest.skip("Hardlinks not supported on this filesystem")

    tenant = "tenant-c27"
    src_id = "src-c27-1"

    _register_source_and_scope(
        db_session,
        tenant_id=tenant,
        source_id=src_id,
        root_path=str(root),
        repo_identity="git@example.com:c27.git",
        project_id="proj-c27",
    )

    collector = PpmCollector(allow_real_roots=True)
    res = collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)

    assert res.status == "partial"
    assert any("security_finding" in r and "hardlink" in r.lower() for r in res.partial_reasons)

    # Check that hardlink content was NOT included as valid evidence
    db_snap = db_session.execute(
        sa.select(collector_snapshots).where(collector_snapshots.c.id == res.snapshot_id)
    ).mappings().first()
    assert db_snap is not None
    assert "docs/hardlink_escape.txt" not in db_snap["manifest"]


def test_c42_conflicting_repo_identity_quarantines_location(db_session: Session, tmp_path: Path):
    """C42: A scope whose .ppm/project.yaml project_id matches an existing project on a source

    with a different repo identity triggers identity_conflict, quarantines the location,
    emits an outbox event, and merges nothing.
    """
    tenant = "tenant-c42"
    project_id = "proj-c42-original"

    # 1. Existing legitimate repo for project_id
    legit_root = tmp_path / "legit_repo"
    legit_root.mkdir()
    _setup_ppm_project_tree(legit_root, project_id=project_id)
    _register_source_and_scope(
        db_session,
        tenant_id=tenant,
        source_id="src-c42-legit",
        root_path=str(legit_root),
        repo_identity="git@github.com:org/original_repo.git",
        project_id=project_id,
    )

    # 2. Second repository (a copy or rogue repo) with DIFFERENT repo identity claiming same project_id
    rogue_root = tmp_path / "rogue_repo"
    rogue_root.mkdir()
    _setup_ppm_project_tree(rogue_root, project_id=project_id)
    rogue_loc_id = _register_source_and_scope(
        db_session,
        tenant_id=tenant,
        source_id="src-c42-rogue",
        root_path=str(rogue_root),
        repo_identity="git@github.com:other/rogue_repo.git",
        project_id=project_id,
    )

    collector = PpmCollector(allow_real_roots=True)
    res = collector.scan_source(db_session, tenant_id=tenant, source_id="src-c42-rogue")

    # Scope was quarantined!
    assert project_id in res.quarantined_scopes
    assert project_id not in res.scopes

    # Location state is now suspended
    loc_row = db_session.execute(
        sa.select(location_registrations).where(location_registrations.c.id == rogue_loc_id)
    ).mappings().first()
    assert loc_row is not None
    assert loc_row["state"] == "suspended"

    # identity_conflicts table records conflict detail
    conf_row = db_session.execute(
        sa.select(identity_conflicts).where(
            identity_conflicts.c.tenant_id == tenant,
            identity_conflicts.c.project_id == project_id,
        )
    ).mappings().first()
    assert conf_row is not None
    assert conf_row["conflicting_source_id"] == "src-c42-legit"
    assert conf_row["conflicting_repo_identity"] == "git@github.com:org/original_repo.git"

    # ppm.identity_conflict.detected outbox event emitted
    evt_row = db_session.execute(
        sa.select(outbox).where(
            outbox.c.tenant_id == tenant,
            outbox.c.event_type == "ppm.identity_conflict.detected",
        )
    ).mappings().first()
    assert evt_row is not None
    assert evt_row["payload"]["claimed_project_id"] == project_id


def test_path_vanishing_mid_scan_handled_gracefully(db_session: Session, tmp_path: Path):
    """A file removed mid-scan produces a partial scan without crashing."""
    root = tmp_path / "vanish_repo"
    root.mkdir()
    _setup_ppm_project_tree(root, project_id="proj-vanish", extra_files={"docs/temp.txt": "ephemeral"})

    tenant = "tenant-vanish"
    src_id = "src-vanish-1"

    _register_source_and_scope(
        db_session,
        tenant_id=tenant,
        source_id=src_id,
        root_path=str(root),
        repo_identity="git@example.com:vanish.git",
        project_id="proj-vanish",
    )

    collector = PpmCollector(allow_real_roots=True)
    orig_find = collector._find_candidate_files

    def patched_find(*args, **kwargs):
        cands = orig_find(*args, **kwargs)
        # Delete the file immediately after listing
        (root / "docs" / "temp.txt").unlink()
        return cands

    collector._find_candidate_files = patched_find  # type: ignore

    res = collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)
    assert res.status == "partial"
    assert any("file_vanished" in r for r in res.partial_reasons)


def test_oversized_file_handled_gracefully(db_session: Session, tmp_path: Path):
    """An oversized file produces a partial scan with reasons, not a crash."""
    root = tmp_path / "oversize_repo"
    root.mkdir()
    _setup_ppm_project_tree(root, project_id="proj-oversize", extra_files={"docs/large.bin": "x" * 2048})

    tenant = "tenant-oversize"
    src_id = "src-oversize-1"

    _register_source_and_scope(
        db_session,
        tenant_id=tenant,
        source_id=src_id,
        root_path=str(root),
        repo_identity="git@example.com:oversize.git",
        project_id="proj-oversize",
        max_file_bytes=1024,  # Cap file size at 1 KB
    )

    collector = PpmCollector(allow_real_roots=True)
    res = collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)

    assert res.status == "partial"
    assert any("file_oversized" in r for r in res.partial_reasons)


def test_duplicate_yaml_keys_produces_partial_scan(db_session: Session, tmp_path: Path):
    """Duplicate keys in YAML updates produce partial scan and parse_error reason."""
    root = tmp_path / "dupyaml_repo"
    root.mkdir()
    _setup_ppm_project_tree(root, project_id="proj-dupyaml")

    # Write update with duplicate keys
    (root / ".ppm" / "updates" / "upd-dup.yaml").write_text(
        """contract_version: "2.0"
kind: update
update_id: "upd-dup"
update_id: "upd-dup-duplicate"
""",
        encoding="utf-8",
    )

    tenant = "tenant-dupyaml"
    src_id = "src-dupyaml-1"

    _register_source_and_scope(
        db_session,
        tenant_id=tenant,
        source_id=src_id,
        root_path=str(root),
        repo_identity="git@example.com:dupyaml.git",
        project_id="proj-dupyaml",
    )

    collector = PpmCollector(allow_real_roots=True)
    res = collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)

    assert res.status == "partial"
    assert any("parse_error" in r and "Duplicate YAML key" in r for r in res.partial_reasons)


def test_deny_globs_excludes_files(db_session: Session, tmp_path: Path):
    """Files matching deny_globs are completely excluded from the scan."""
    root = tmp_path / "deny_repo"
    root.mkdir()
    _setup_ppm_project_tree(
        root,
        project_id="proj-deny",
        extra_files={
            "docs/keep.md": "keep me",
            "docs/ignore.tmp": "do not scan",
            "node_modules/pkg/index.js": "ignored node module",
        },
    )

    tenant = "tenant-deny"
    src_id = "src-deny-1"

    _register_source_and_scope(
        db_session,
        tenant_id=tenant,
        source_id=src_id,
        root_path=str(root),
        repo_identity="git@example.com:deny.git",
        project_id="proj-deny",
        deny_globs=["*.tmp", "node_modules/**"],
    )

    collector = PpmCollector(allow_real_roots=True)
    res = collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)

    assert res.status == "captured"
    assert res.scopes["proj-deny"].scanned_files_count > 0

    db_snap = db_session.execute(
        sa.select(collector_snapshots).where(collector_snapshots.c.id == res.snapshot_id)
    ).mappings().first()
    assert db_snap is not None
    manifest = db_snap["manifest"]
    assert "docs/keep.md" in manifest
    assert "docs/ignore.tmp" not in manifest
    assert "node_modules/pkg/index.js" not in manifest


def test_monotonic_snapshot_seq_per_project(db_session: Session, tmp_path: Path):
    """Repeated scans increment monotonic snapshot_seq per (source_id, project_id) and emit outbox events."""
    root = tmp_path / "seq_repo"
    root.mkdir()
    _setup_ppm_project_tree(root, project_id="proj-seq")

    tenant = "tenant-seq"
    src_id = "src-seq-1"

    _register_source_and_scope(
        db_session,
        tenant_id=tenant,
        source_id=src_id,
        root_path=str(root),
        repo_identity="git@example.com:seq.git",
        project_id="proj-seq",
    )

    collector = PpmCollector(allow_real_roots=True)

    res1 = collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)
    assert res1.scopes["proj-seq"].snapshot_seq == 1

    res2 = collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)
    assert res2.scopes["proj-seq"].snapshot_seq == 2

    res3 = collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)
    assert res3.scopes["proj-seq"].snapshot_seq == 3

    # Verify 3 outbox events emitted with sequential snapshot_seq
    events = db_session.execute(
        sa.select(outbox).where(
            outbox.c.tenant_id == tenant,
            outbox.c.event_type == "ppm.snapshot.captured",
        ).order_by(outbox.c.id.asc())
    ).mappings().all()

    assert len(events) == 3
    seqs = [e["payload"]["snapshot_seq"] for e in events]
    assert seqs == [1, 2, 3]


def test_10000_file_throughput(db_session: Session, tmp_path: Path):
    """10,000-file fixture benchmark: measures and reports files/second throughput (no pass threshold)."""
    root = tmp_path / "bench_10k_repo"
    root.mkdir()

    # Generate 10,000 files in 100 subdirectories
    file_count = 10000
    subdirs = 100
    files_per_dir = file_count // subdirs

    for d in range(subdirs):
        dir_p = root / f"pkg_{d:03d}"
        dir_p.mkdir()
        for f in range(files_per_dir):
            (dir_p / f"mod_{f:03d}.py").write_text(f"# module {d}-{f}\n", encoding="utf-8")

    _setup_ppm_project_tree(root, project_id="proj-bench")

    tenant = "tenant-bench-10k"
    src_id = "src-bench-10k"

    _register_source_and_scope(
        db_session,
        tenant_id=tenant,
        source_id=src_id,
        root_path=str(root),
        repo_identity="git@example.com:bench10k.git",
        project_id="proj-bench",
        scan_scope=[".ppm", *[f"pkg_{d:03d}" for d in range(subdirs)]],
    )

    collector = PpmCollector(allow_real_roots=True)

    t0 = time.perf_counter()
    res = collector.scan_source(db_session, tenant_id=tenant, source_id=src_id)
    duration_s = time.perf_counter() - t0

    total_scanned = res.scopes["proj-bench"].scanned_files_count
    throughput_fps = total_scanned / duration_s if duration_s > 0 else 0.0

    print(
        f"\n[10k Throughput Benchmark] Scanned {total_scanned} files in {duration_s:.3f} s "
        f"({throughput_fps:.1f} files/s)"
    )
    assert total_scanned >= 10000
    assert res.status == "captured"
