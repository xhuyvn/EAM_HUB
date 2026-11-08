"""collector.py — Deterministic, read-only PPM Collector (P2a-WP2).

Enforces:
- Read-only execution: never writes into location root; never executes code from repository.
- Safety boundary: EAP_COLLECTOR_ALLOW_REAL_ROOTS (refuses real roots when false).
- Share permissions: pending_share or revoked share roots are unreadable.
- Physical identity (ISS-028): records fs_identity on first scan; refuses changed root.
- Git state: scrubbed environment, no hooks, no fsmonitor execution. Non-git fallback.
- Scope segmentation: per-scope files, notes, claims, deny_globs, max_file_bytes.
- Safe parsing: duplicate YAML/JSON keys rejected, prompt-injection preserved as data.
- Consistency check (C16): mid-scan changes mark mixed_snapshot=True and partial.
- C42: duplicate project_id across differing repo_identity -> identity_conflict & quarantine.
- Persistence: commits collector_snapshot, project_snapshots, monotonic snapshot_seq,
  and emits ppm.snapshot.captured outbox event in the SAME transaction.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from eap.api.dependencies import Principal
from eap.config import get_settings
from eap.db import (
    collector_snapshots,
    identity_conflicts,
    location_registrations,
    outbox,
    project_scopes,
    project_snapshot_sequences,
    project_snapshots,
    source_physical_identities,
    source_shares,
    sources,
)
from eap.ppm.collector.git import get_git_state
from eap.ppm.collector.identity import (
    RootReplacedSecurityError,
    check_root_permission,
    get_fs_identity,
)
from eap.ppm.collector.parser import DuplicateKeyError, SafeParseError, safe_load_yaml
from eap.ppm.collector.safe_open import (
    FileTooLargeError,
    FileTypeNotAllowedError,
    HardlinkNotAllowedError,
    PathTraversalError,
    SafeOpenError,
    safe_open,
)

logger = logging.getLogger("eap.ppm.collector")


@dataclass
class ScopeScanResult:
    project_id: str
    metadata_root: str
    snapshot_seq: int
    scope_hash: str
    status: str
    mixed_snapshot: bool
    partial_reasons: list[str]
    artifact_aliases: dict[str, str | None]
    notes: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    scanned_files_count: int


@dataclass
class CollectorScanResult:
    snapshot_id: str
    source_id: str
    tenant_id: str
    status: str
    mixed_snapshot: bool
    partial_reasons: list[str]
    git_commit: str | None
    git_dirty: bool
    tree_hash: str
    scopes: dict[str, ScopeScanResult] = field(default_factory=dict)
    quarantined_scopes: list[str] = field(default_factory=list)


def _emit_outbox_event(
    db: Session,
    *,
    tenant_id: str,
    event_type: str,
    stream_id: str,
    payload: dict[str, Any],
) -> None:
    now = dt.datetime.now(dt.UTC)
    db.execute(
        outbox.insert().values(
            tenant_id=tenant_id,
            stream_id=stream_id,
            event_id=f"evt-{uuid.uuid4().hex[:16]}",
            event_type=event_type,
            payload=payload,
            created_at=now,
        )
    )


class PpmCollector:
    """Read-only deterministic filesystem and Git state scanner."""

    def __init__(
        self,
        *,
        allow_real_roots: bool | None = None,
        fixtures_root: str | None = None,
    ) -> None:
        settings = get_settings()
        self.allow_real_roots = (
            allow_real_roots
            if allow_real_roots is not None
            else settings.eap_collector_allow_real_roots
        )
        self.fixtures_root = (
            fixtures_root
            if fixtures_root is not None
            else settings.eap_collector_fixtures_root
        )

    def scan_source(
        self,
        db: Session,
        *,
        tenant_id: str,
        source_id: str,
        principal: Principal | None = None,
    ) -> CollectorScanResult:
        """Scan a physical source root and all associated project scopes."""
        # 1. Retrieve source record
        src_row = db.execute(
            sa.select(sources).where(
                sources.c.tenant_id == tenant_id,
                sources.c.source_id == source_id,
            )
        ).mappings().first()

        if not src_row:
            raise ValueError(f"Source '{source_id}' not found for tenant '{tenant_id}'")

        canonical_root = src_row["canonical_root"]
        source_repo_identity = src_row["repo_identity"]

        # 2. Safety boundary check
        check_root_permission(
            canonical_root,
            allow_real_roots=self.allow_real_roots,
            fixtures_root=self.fixtures_root,
        )

        # 3. Share state check: pending_share or revoked share is unreadable (P2a-WP1 L3)
        share_row = db.execute(
            sa.select(source_shares).where(
                source_shares.c.requester_tenant_id == tenant_id,
                source_shares.c.requester_source_id == source_id,
            )
        ).mappings().first()
        if share_row and share_row["state"] in ("pending_share", "rejected", "revoked"):
            raise PermissionError(
                f"Source '{source_id}' is in unreadable share state '{share_row['state']}'"
            )

        # 4. Physical identity check (ISS-028)
        curr_fs_id = get_fs_identity(canonical_root)
        ident_row = db.execute(
            sa.select(source_physical_identities).where(
                source_physical_identities.c.tenant_id == tenant_id,
                source_physical_identities.c.source_id == source_id,
            )
        ).mappings().first()

        now = dt.datetime.now(dt.UTC)
        if not ident_row:
            # First scan: re-check one-folder-one-source by physical identity
            duplicate_ident = db.execute(
                sa.select(source_physical_identities).where(
                    source_physical_identities.c.tenant_id == tenant_id,
                    source_physical_identities.c.fs_identity == curr_fs_id,
                    source_physical_identities.c.source_id != source_id,
                )
            ).mappings().first()
            if duplicate_ident:
                raise ValueError(
                    f"Physical directory already registered under source '{duplicate_ident['source_id']}'"
                )

            # Record physical identity
            db.execute(
                source_physical_identities.insert().values(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    fs_identity=curr_fs_id,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            # Later scan: refuse if physical identity changed (moved/replaced root)
            if ident_row["fs_identity"] != curr_fs_id:
                raise RootReplacedSecurityError(
                    f"Root physical identity changed from '{ident_row['fs_identity']}' to '{curr_fs_id}'"
                )

        # 5. Pre-scan git and manifest state
        git_pre = get_git_state(canonical_root)

        # 6. Retrieve active project scopes for this source
        scope_rows = db.execute(
            sa.select(project_scopes).where(
                project_scopes.c.tenant_id == tenant_id,
                project_scopes.c.source_id == source_id,
            )
        ).mappings().all()

        # Build location settings lookup: metadata_root -> location_registrations row
        loc_rows = db.execute(
            sa.select(location_registrations).where(
                location_registrations.c.tenant_id == tenant_id,
                location_registrations.c.source_id == source_id,
            )
        ).mappings().all()
        loc_map = {row["metadata_root"]: row for row in loc_rows}

        snapshot_id = f"snap-{uuid.uuid4().hex[:16]}"
        manifest: dict[str, dict[str, Any]] = {}
        all_partial_reasons: list[str] = []
        is_mixed_snapshot = False

        scope_results: dict[str, ScopeScanResult] = {}
        quarantined_scopes: list[str] = []

        # 7. Scan each project scope
        for scope_row in scope_rows:
            p_id = scope_row["project_id"]
            m_root = scope_row["metadata_root"]
            loc_config = loc_map.get(m_root)

            # Check if location is already quarantined (state == suspended)
            if loc_config and loc_config["state"] == "suspended":
                quarantined_scopes.append(p_id)
                continue

            scan_scope_dirs: list[str] = (
                loc_config["scan_scope"] if loc_config and loc_config.get("scan_scope") else [m_root]
            )
            deny_globs: list[str] = loc_config["deny_globs"] if loc_config else []
            notes_globs: list[str] = loc_config["notes_globs"] if loc_config else []
            max_file_bytes: int = loc_config["max_file_bytes"] if loc_config else 16777216
            aliases_config: dict[str, list[str]] = (
                loc_config["artifact_aliases"]
                if loc_config and loc_config.get("artifact_aliases")
                else {"architecture": [], "blueprint": [], "implementation_plan": []}
            )

            scope_partial_reasons: list[str] = []
            scope_files: dict[str, str] = {}  # rel_path -> sha256
            notes: list[dict[str, Any]] = []
            claims: list[dict[str, Any]] = []

            # 7.1 Enumerate and read candidate files in scope
            candidate_files = self._find_candidate_files(
                canonical_root,
                scan_scope_dirs=scan_scope_dirs,
                metadata_root=m_root,
                deny_globs=deny_globs,
            )

            file_stats_before: dict[str, tuple[float, int]] = {}

            for rel_file in candidate_files:
                try:
                    # Enforce safe_open handle inspection
                    with safe_open(
                        canonical_root,
                        rel_file,
                        mode="rb",
                        follow_symlinks=False,
                        allow_hardlinked_files=False,
                        max_file_bytes=max_file_bytes,
                    ) as f:
                        data = f.read()

                    raw_bytes = data if isinstance(data, bytes) else data.encode("utf-8")
                    h_digest = hashlib.sha256(raw_bytes).hexdigest()
                    file_size = len(raw_bytes)
                    full_p = os.path.join(canonical_root, rel_file)
                    st_mtime = os.stat(full_p).st_mtime
                    file_stats_before[rel_file] = (st_mtime, file_size)

                    scope_files[rel_file] = h_digest
                    manifest[rel_file] = {
                        "size": file_size,
                        "mtime": st_mtime,
                        "sha256": h_digest,
                    }
                except (PathTraversalError, FileTypeNotAllowedError, HardlinkNotAllowedError) as sec_err:
                    # Security finding: log, mark partial, omit evidence
                    logger.warning("Security finding on '%s': %s", rel_file, sec_err)
                    reason = f"security_finding: {rel_file}: {sec_err}"
                    scope_partial_reasons.append(reason)
                    all_partial_reasons.append(reason)
                except FileNotFoundError:
                    # File vanished mid-scan
                    reason = f"file_vanished: {rel_file}"
                    scope_partial_reasons.append(reason)
                    all_partial_reasons.append(reason)
                except FileTooLargeError as sz_err:
                    reason = f"file_oversized: {rel_file}: {sz_err}"
                    scope_partial_reasons.append(reason)
                    all_partial_reasons.append(reason)
                except (OSError, UnicodeDecodeError) as e:
                    reason = f"read_error: {rel_file}: {e}"
                    scope_partial_reasons.append(reason)
                    all_partial_reasons.append(reason)

            # 7.2 Read and safely parse .ppm metadata files
            ppm_project_data: dict[str, Any] | None = None
            project_yaml_rel = f"{m_root}/project.yaml".replace("//", "/")
            if project_yaml_rel in scope_files:
                try:
                    with safe_open(canonical_root, project_yaml_rel, mode="r", encoding="utf-8") as f:
                        raw_proj = f.read()
                    str_proj = raw_proj if isinstance(raw_proj, str) else raw_proj.decode("utf-8")
                    ppm_project_data = safe_load_yaml(str_proj)
                except (DuplicateKeyError, SafeParseError) as pe:
                    reason = f"parse_error: {project_yaml_rel}: {pe}"
                    scope_partial_reasons.append(reason)
                    all_partial_reasons.append(reason)

            # C42 Check: Verify project_id in .ppm/project.yaml against existing repos
            if ppm_project_data and isinstance(ppm_project_data, dict):
                declared_p_id = ppm_project_data.get("project_id", p_id)
                # Query whether declared_p_id is claimed on a different repo identity
                conflict_rows = db.execute(
                    sa.select(
                        project_scopes.c.source_id,
                        sources.c.repo_identity,
                    )
                    .select_from(
                        project_scopes.join(
                            sources,
                            sa.and_(
                                sources.c.tenant_id == project_scopes.c.tenant_id,
                                sources.c.source_id == project_scopes.c.source_id,
                            ),
                        )
                    )
                    .where(
                        project_scopes.c.tenant_id == tenant_id,
                        project_scopes.c.project_id == declared_p_id,
                    )
                ).all()

                is_identity_conflict = False
                conflict_src = ""
                conflict_repo = ""
                for conf_s_id, conf_r_id in conflict_rows:
                    if conf_r_id != source_repo_identity:
                        is_identity_conflict = True
                        conflict_src = conf_s_id
                        conflict_repo = conf_r_id
                        break

                if is_identity_conflict:
                    # C42: Identity conflict detected! Quarantine location and skip merging
                    loc_id = loc_config["id"] if loc_config else f"loc-{p_id}"
                    logger.warning("C42 identity conflict: project '%s' claimed on conflicting repo", declared_p_id)

                    db.execute(
                        location_registrations.update()
                        .where(
                            location_registrations.c.tenant_id == tenant_id,
                            location_registrations.c.source_id == source_id,
                            location_registrations.c.metadata_root == m_root,
                        )
                        .values(state="suspended", updated_at=now)
                    )

                    conflict_detail = {
                        "reason": "duplicate project_id observed across distinct repositories",
                        "claimed_project_id": declared_p_id,
                        "conflicting_source_id": conflict_src,
                        "conflicting_repo_identity": conflict_repo,
                        "new_source_id": source_id,
                        "new_repo_identity": source_repo_identity,
                    }
                    db.execute(
                        identity_conflicts.insert().values(
                            tenant_id=tenant_id,
                            project_id=declared_p_id,
                            location_id=loc_id,
                            source_id=source_id,
                            repo_identity=source_repo_identity,
                            conflicting_source_id=conflict_src,
                            conflicting_repo_identity=conflict_repo,
                            detail=conflict_detail,
                            created_at=now,
                        )
                    )
                    _emit_outbox_event(
                        db,
                        tenant_id=tenant_id,
                        event_type="ppm.identity_conflict.detected",
                        stream_id=f"location:{loc_id}",
                        payload=conflict_detail,
                    )
                    quarantined_scopes.append(p_id)
                    continue

            # 7.3 Resolve artifact aliases via safe_open
            resolved_aliases: dict[str, str | None] = {}
            for kind in ("architecture", "blueprint", "implementation_plan"):
                resolved_aliases[kind] = self._resolve_alias(
                    canonical_root=canonical_root,
                    kind=kind,
                    ppm_project_data=ppm_project_data,
                    configured_aliases=aliases_config.get(kind, []),
                    max_file_bytes=max_file_bytes,
                )

            # 7.4 Collect notes matching notes_globs
            for rel_file in scope_files:
                if self._matches_any_glob(rel_file, notes_globs):
                    try:
                        with safe_open(canonical_root, rel_file, mode="r", encoding="utf-8") as nf:
                            raw_note = nf.read()
                        note_content = raw_note if isinstance(raw_note, str) else raw_note.decode("utf-8")
                        c_hash = hashlib.sha256(note_content.encode("utf-8")).hexdigest()
                        notes.append({
                            "path": rel_file,
                            "content_hash": c_hash,
                            "content": note_content,
                        })
                    except (OSError, UnicodeDecodeError) as e:
                        reason = f"note_read_error: {rel_file}: {e}"
                        scope_partial_reasons.append(reason)
                        all_partial_reasons.append(reason)

            # 7.5 Read updates and status claims
            for rel_file in scope_files:
                if rel_file.startswith(f"{m_root}/updates/") and rel_file.endswith(".yaml"):
                    try:
                        with safe_open(canonical_root, rel_file, mode="r", encoding="utf-8") as uf:
                            raw_upd = uf.read()
                        str_upd = raw_upd if isinstance(raw_upd, str) else raw_upd.decode("utf-8")
                        upd_data = safe_load_yaml(str_upd)
                        claims.append({
                            "path": rel_file,
                            "data": upd_data,
                        })
                    except (DuplicateKeyError, SafeParseError) as pe:
                        reason = f"parse_error: {rel_file}: {pe}"
                        scope_partial_reasons.append(reason)
                        all_partial_reasons.append(reason)

            # 7.6 Compute deterministic scope hash
            sorted_scope_items = sorted(scope_files.items())
            scope_hasher = hashlib.sha256()
            for rpath, fhash in sorted_scope_items:
                scope_hasher.update(f"{rpath}:{fhash}\n".encode())
            scope_hash = scope_hasher.hexdigest()

            # Check for per-file racing modification
            for rel_file, (mtime_before, sz_before) in file_stats_before.items():
                full_p = os.path.join(canonical_root, rel_file)
                try:
                    st_after = os.stat(full_p)
                    if st_after.st_mtime != mtime_before or st_after.st_size != sz_before:
                        is_mixed_snapshot = True
                        reason = f"file_modified_during_scan: {rel_file}"
                        scope_partial_reasons.append(reason)
                        all_partial_reasons.append(reason)
                except OSError:
                    is_mixed_snapshot = True
                    reason = f"file_vanished_during_scan: {rel_file}"
                    scope_partial_reasons.append(reason)
                    all_partial_reasons.append(reason)

            scope_status = "partial" if (scope_partial_reasons or is_mixed_snapshot) else "captured"

            scope_results[p_id] = ScopeScanResult(
                project_id=p_id,
                metadata_root=m_root,
                snapshot_seq=0,  # Will be assigned during commit
                scope_hash=scope_hash,
                status=scope_status,
                mixed_snapshot=is_mixed_snapshot,
                partial_reasons=scope_partial_reasons,
                artifact_aliases=resolved_aliases,
                notes=notes,
                claims=claims,
                scanned_files_count=len(scope_files),
            )

        # 8. Post-scan Git check (C16)
        git_post = get_git_state(canonical_root)
        if git_pre != git_post:
            is_mixed_snapshot = True
            all_partial_reasons.append(f"git_state_changed_during_scan: pre={git_pre} post={git_post}")

        # Compute overall tree hash
        tree_hasher = hashlib.sha256()
        for rpath, finfo in sorted(manifest.items()):
            tree_hasher.update(f"{rpath}:{finfo['sha256']}\n".encode())
        tree_hash = tree_hasher.hexdigest()

        has_any_partial_scope = any(s.status == "partial" for s in scope_results.values())
        overall_status = "partial" if (all_partial_reasons or is_mixed_snapshot or has_any_partial_scope) else "captured"

        # 9. Atomic Transactional Persistence
        git_commit = git_post.commit_sha if git_post else (git_pre.commit_sha if git_pre else None)
        git_dirty = git_post.is_dirty if git_post else (git_pre.is_dirty if git_pre else False)

        # 9.1 Insert collector_snapshots row
        db.execute(
            collector_snapshots.insert().values(
                id=snapshot_id,
                tenant_id=tenant_id,
                source_id=source_id,
                status=overall_status,
                mixed_snapshot=is_mixed_snapshot,
                partial_reasons=all_partial_reasons,
                git_commit=git_commit,
                git_dirty=git_dirty,
                tree_hash=tree_hash,
                manifest=manifest,
                created_at=now,
            )
        )

        # 9.2 For each active scope, issue monotonic snapshot_seq and insert project_snapshots
        for p_id, s_res in scope_results.items():
            # Atomically increment or initialize snapshot_seq
            seq_row = db.execute(
                sa.select(project_snapshot_sequences)
                .where(
                    project_snapshot_sequences.c.tenant_id == tenant_id,
                    project_snapshot_sequences.c.source_id == source_id,
                    project_snapshot_sequences.c.project_id == p_id,
                )
                .with_for_update()
            ).mappings().first()

            if not seq_row:
                new_seq = 1
                db.execute(
                    project_snapshot_sequences.insert().values(
                        tenant_id=tenant_id,
                        source_id=source_id,
                        project_id=p_id,
                        last_snapshot_seq=new_seq,
                        updated_at=now,
                    )
                )
            else:
                new_seq = seq_row["last_snapshot_seq"] + 1
                db.execute(
                    project_snapshot_sequences.update()
                    .where(
                        project_snapshot_sequences.c.tenant_id == tenant_id,
                        project_snapshot_sequences.c.source_id == source_id,
                        project_snapshot_sequences.c.project_id == p_id,
                    )
                    .values(last_snapshot_seq=new_seq, updated_at=now)
                )

            s_res.snapshot_seq = new_seq
            proj_snap_id = f"psnap-{uuid.uuid4().hex[:16]}"

            db.execute(
                project_snapshots.insert().values(
                    id=proj_snap_id,
                    tenant_id=tenant_id,
                    snapshot_id=snapshot_id,
                    source_id=source_id,
                    project_id=p_id,
                    snapshot_seq=new_seq,
                    scope_hash=s_res.scope_hash,
                    metadata_root=s_res.metadata_root,
                    status=s_res.status,
                    mixed_snapshot=s_res.mixed_snapshot,
                    partial_reasons=s_res.partial_reasons,
                    artifact_aliases=s_res.artifact_aliases,
                    notes=s_res.notes,
                    claims=s_res.claims,
                    created_at=now,
                )
            )

            # Emit ppm.snapshot.captured outbox event
            _emit_outbox_event(
                db,
                tenant_id=tenant_id,
                event_type="ppm.snapshot.captured",
                stream_id=f"snapshot:{p_id}",
                payload={
                    "snapshot_id": snapshot_id,
                    "project_snapshot_id": proj_snap_id,
                    "source_id": source_id,
                    "project_id": p_id,
                    "snapshot_seq": new_seq,
                    "scope_hash": s_res.scope_hash,
                    "metadata_root": s_res.metadata_root,
                    "status": s_res.status,
                    "mixed_snapshot": s_res.mixed_snapshot,
                    "git_commit": git_commit,
                    "git_dirty": git_dirty,
                    "captured_at": now.isoformat(),
                },
            )

        db.commit()

        return CollectorScanResult(
            snapshot_id=snapshot_id,
            source_id=source_id,
            tenant_id=tenant_id,
            status=overall_status,
            mixed_snapshot=is_mixed_snapshot,
            partial_reasons=all_partial_reasons,
            git_commit=git_commit,
            git_dirty=git_dirty,
            tree_hash=tree_hash,
            scopes=scope_results,
            quarantined_scopes=quarantined_scopes,
        )

    def _find_candidate_files(
        self,
        root: str,
        *,
        scan_scope_dirs: list[str],
        metadata_root: str,
        deny_globs: list[str],
    ) -> list[str]:
        """Walk root and return relative file paths inside scan_scope and not in deny_globs."""
        candidates: set[str] = set()

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune .git and denied directories early
            dirnames[:] = [
                d for d in dirnames
                if d != ".git" and not self._matches_any_glob(
                    os.path.relpath(os.path.join(dirpath, d), root).replace("\\", "/"),
                    deny_globs,
                )
            ]

            rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = ""

            for fname in filenames:
                rel_f = f"{rel_dir}/{fname}" if rel_dir else fname
                rel_f = rel_f.replace("\\", "/").lstrip("/")

                # Check deny globs
                if self._matches_any_glob(rel_f, deny_globs):
                    continue

                # Check if file falls into scan_scope or metadata_root
                if self._in_scope(rel_f, scan_scope_dirs, metadata_root):
                    candidates.add(rel_f)

        return sorted(candidates)

    @staticmethod
    def _in_scope(rel_file: str, scan_scopes: list[str], metadata_root: str) -> bool:
        norm_file = rel_file.lstrip("/")
        if norm_file.startswith(metadata_root.lstrip("/")):
            return True
        for sc in scan_scopes:
            clean_sc = sc.lstrip("/")
            if clean_sc in (".", ""):
                return True
            if norm_file == clean_sc or norm_file.startswith(f"{clean_sc}/"):
                return True
        return False

    @staticmethod
    def _matches_any_glob(path_str: str, globs: list[str]) -> bool:
        norm_p = path_str.replace("\\", "/")
        for g in globs:
            norm_g = g.replace("\\", "/")
            if fnmatch.fnmatch(norm_p, norm_g):
                return True
            # Match basename if pattern has no slash
            if "/" not in norm_g and fnmatch.fnmatch(os.path.basename(norm_p), norm_g):
                return True
        return False

    @staticmethod
    def _resolve_alias(
        canonical_root: str,
        kind: str,
        ppm_project_data: dict[str, Any] | None,
        configured_aliases: list[str],
        max_file_bytes: int,
    ) -> str | None:
        # 1. Check .ppm/project.yaml artifact_mapping
        if ppm_project_data and isinstance(ppm_project_data, dict):
            mapping = ppm_project_data.get("artifact_mapping", {})
            if isinstance(mapping, dict) and mapping.get(kind):
                target = str(mapping[kind]).replace("\\", "/").lstrip("/")
                try:
                    with safe_open(
                        canonical_root,
                        target,
                        mode="rb",
                        follow_symlinks=False,
                        allow_hardlinked_files=False,
                        max_file_bytes=max_file_bytes,
                    ):
                        return target
                except (OSError, SafeOpenError):
                    logger.debug("Artifact mapping for %s at %s not resolvable", kind, target)

        # 2. Check configured aliases in order
        for alias in configured_aliases:
            clean_alias = str(alias).replace("\\", "/").lstrip("/")
            try:
                with safe_open(
                    canonical_root,
                    clean_alias,
                    mode="rb",
                    follow_symlinks=False,
                    allow_hardlinked_files=False,
                    max_file_bytes=max_file_bytes,
                ):
                    return clean_alias
            except (OSError, SafeOpenError):
                continue

        # 3. Unmapped fallback
        return None


__all__ = [
    "CollectorScanResult",
    "PpmCollector",
    "ScopeScanResult",
]
