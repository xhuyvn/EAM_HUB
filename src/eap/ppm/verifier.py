"""Sandboxed verifier for fixture repositories (P2a-WP9).

Replaces D0's ``simulated-verifier`` with real ``verifier_observed`` evidence:
- Enforces ``PPM_VERIFIER_ENABLED`` (default false; rollback switch).
- Enforces the fixtures-only boundary (``EAP_COLLECTOR_ALLOW_REAL_ROOTS=false``
  and source root inside ``EAP_COLLECTOR_FIXTURES_ROOT`` / permitted temp root).
- Exports ``assessed_revision`` read-only via ``export_git_archive_tar``
  (ISS-030/031 hardened) into an isolated temporary workspace outside the
  registered root, and deletes the temporary workspace in ``finally``.
- Never reads repository-written JUnit XML or report files as results.
- Executes only ``test_id``s mapped in the approved baseline via an allowlisted
  argv (``python -m pytest -q -p no:cacheprovider <nodeid>``), ``shell=False``,
  scrubbed environment, per-test wall-clock timeout, and output size cap.
- Records ``network_isolation`` honestly: ``enforced`` when Linux ``unshare -n``
  (or ``-rn``) succeeds, otherwise ``not_enforced`` (e.g. on Windows or
  unprivileged environments without user namespaces).
"""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from eap.config import Settings, get_settings
from eap.db import (
    location_registrations,
    ppm_baselines,
    ppm_evidence,
    ppm_updates,
    project_scopes,
    source_shares,
    sources,
)
from eap.ppm.collector.git import export_git_archive_tar
from eap.ppm.collector.identity import (
    RootNotPermittedSecurityError,
    check_root_permission,
)

PRODUCER_NAME = "eap-verifier"

_SAFE_NODEID_RE = re.compile(r"^[A-Za-z0-9_./:\[\]-]{1,256}$")


class VerifierError(RuntimeError):
    """Base error for sandbox verifier failures."""


class VerifierRealRootRefusedError(RootNotPermittedSecurityError):
    """Raised when attempting to run the fixtures-only verifier on real roots."""


class VerifierAllowlistError(ValueError):
    """Raised when a test nodeid or verifier command violates the execution allowlist."""


@dataclass(frozen=True)
class TestVerificationResult:
    """Outcome record for a single mapped or unmapped test identifier."""

    test_id: str
    outcome: str
    executed: bool
    evidence_id: str | None
    revision: str
    nodeid: str | None = None
    exit_status: int | None = None
    duration_ms: int | None = None
    output_hash: str | None = None
    network_isolation: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "outcome": self.outcome,
            "executed": self.executed,
            "evidence_id": self.evidence_id,
            "revision": self.revision,
            "nodeid": self.nodeid,
            "exit_status": self.exit_status,
            "duration_ms": self.duration_ms,
            "output_hash": self.output_hash,
            "network_isolation": self.network_isolation,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class VerifierReport:
    """Structured report returned by ``run_verifier``."""

    tenant_id: str
    project_id: str
    assessed_revision: str
    baseline_revision: int | None
    enabled: bool
    executed: bool
    network_isolation: str
    mapped_test_ids: list[str]
    unmapped_test_ids: list[str]
    results: list[TestVerificationResult]
    workspace_removed: bool = True
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "assessed_revision": self.assessed_revision,
            "baseline_revision": self.baseline_revision,
            "enabled": self.enabled,
            "executed": self.executed,
            "network_isolation": self.network_isolation,
            "mapped_test_ids": list(self.mapped_test_ids),
            "unmapped_test_ids": list(self.unmapped_test_ids),
            "results": [r.to_dict() for r in self.results],
            "workspace_removed": self.workspace_removed,
            "skipped_reason": self.skipped_reason,
        }


def validate_nodeid(nodeid: str) -> str:
    """Validate that a pytest nodeid/test_id contains no option injection or traversal."""
    cleaned = nodeid.strip()
    if (
        not cleaned
        or cleaned != nodeid
        or cleaned.startswith("-")
        or "\\" in cleaned
        or ".." in cleaned
        or "\0" in cleaned
        or not _SAFE_NODEID_RE.fullmatch(cleaned)
    ):
        raise VerifierAllowlistError(f"Unsafe or malformed test nodeid rejected: {nodeid!r}")
    return cleaned


def build_allowed_verifier_argv(nodeid: str) -> list[str]:
    """Construct the strictly allowlisted pytest argv for a single test nodeid."""
    safe_nodeid = validate_nodeid(nodeid)
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", safe_nodeid]
    validate_allowed_argv(argv)
    return argv


def validate_allowed_argv(argv: Sequence[str]) -> None:
    """Assert an argv matches the verifier's exact allowlist before spawning."""
    expected_prefix = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if len(argv) != len(expected_prefix) + 1 or list(argv[: len(expected_prefix)]) != expected_prefix:
        raise VerifierAllowlistError(
            f"Verifier argv rejected by allowlist policy: {list(argv)!r}"
        )
    validate_nodeid(argv[-1])


def _detect_network_isolation() -> tuple[str, list[str]]:
    """Detect whether OS-level network namespace isolation can be enforced."""
    if not sys.platform.startswith("linux"):
        return ("not_enforced", [])
    unshare_bin = shutil.which("unshare")
    if not unshare_bin:
        return ("not_enforced", [])

    probe_code = "import sys; sys.exit(0)"
    for flags in (["-n"], ["-rn"]):
        cmd = [unshare_bin, *flags, "--", sys.executable, "-c", probe_code]
        try:
            res = subprocess.run(
                cmd,
                shell=False,
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            if res.returncode == 0:
                return ("enforced", [unshare_bin, *flags, "--"])
        except (OSError, subprocess.SubprocessError):
            continue
    return ("not_enforced", [])


def _build_scrubbed_verifier_env(home_dir: Path, tmp_dir: Path) -> dict[str, str]:
    """Build a stripped environment containing zero parent secrets or proxy variables."""
    env: dict[str, str] = {
        "HOME": str(home_dir),
        "USERPROFILE": str(home_dir),
        "TMPDIR": str(tmp_dir),
        "TEMP": str(tmp_dir),
        "TMP": str(tmp_dir),
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    if os.name == "nt":
        for win_key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
            val = os.environ.get(win_key)
            if val:
                env[win_key] = val
    return env


def _is_path_within(root_dir: str | Path, candidate: str | Path) -> bool:
    """Return True if resolved ``candidate`` is identical to or inside resolved ``root_dir``."""
    real_root = os.path.realpath(os.fspath(root_dir))
    real_cand = os.path.realpath(os.fspath(candidate))
    if sys.platform == "win32":
        real_root = real_root.casefold()
        real_cand = real_cand.casefold()
    try:
        return os.path.commonpath([real_root, real_cand]) == real_root
    except ValueError:
        return False


def _extract_tar_safely(tar_bytes: bytes, workspace_dir: Path) -> None:
    """Extract only regular files and directories from a git tar archive into ``workspace_dir``."""
    workspace_dir.mkdir(parents=True, exist_ok=True)
    ws_resolved = workspace_dir.resolve()

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        for member in tf.getmembers():
            raw_name = member.name
            if not raw_name or raw_name == ".":
                continue
            if raw_name.startswith(("/", "\\")) or "\0" in raw_name or "\\" in raw_name:
                continue
            posix = PurePosixPath(raw_name)
            parts = [p for p in posix.parts if p and p != "."]
            if not parts:
                continue
            if any(p == ".." or ":" in p for p in parts):
                continue
            if parts[0] == ".git" or ".git" in parts:
                continue

            # Never create symlinks, hardlinks, FIFOs, or device nodes in workspace
            if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                continue

            dest = ws_resolved.joinpath(*parts)
            if not _is_path_within(ws_resolved, dest):
                continue

            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            src_f = tf.extractfile(member)
            if src_f is None:
                continue
            with src_f, open(dest, "wb") as out_f:
                shutil.copyfileobj(src_f, out_f)
            try:
                os.chmod(dest, 0o600)
            except OSError:
                pass


def _resolve_nodeid_in_workspace(workspace_dir: Path, test_id: str) -> tuple[str, bool, bool]:
    """Resolve ``test_id`` statically in ``workspace_dir`` using AST (never importing repo code).

    Returns ``(nodeid, found_in_workspace, syntax_error_seen)``.
    """
    validate_nodeid(test_id)
    ws_resolved = workspace_dir.resolve()

    if "::" in test_id:
        file_part = test_id.split("::", 1)[0]
        target = (ws_resolved / file_part).resolve()
        if not _is_path_within(ws_resolved, target) or not target.is_file():
            return (test_id, False, False)
        try:
            ast.parse(target.read_text(encoding="utf-8", errors="replace"), filename=file_part)
        except SyntaxError:
            return (test_id, True, True)
        return (test_id, True, False)

    if test_id.endswith(".py"):
        target = (ws_resolved / test_id).resolve()
        if not _is_path_within(ws_resolved, target) or not target.is_file():
            return (test_id, False, False)
        try:
            ast.parse(target.read_text(encoding="utf-8", errors="replace"), filename=test_id)
        except SyntaxError:
            return (test_id, True, True)
        return (test_id, True, False)

    # Walk .py files in sorted relative POSIX order
    py_files: list[Path] = []
    for path in ws_resolved.rglob("*.py"):
        if not path.is_file():
            continue
        rel = path.relative_to(ws_resolved)
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        py_files.append(path)
    py_files.sort(key=lambda p: p.relative_to(ws_resolved).as_posix())

    func_def_re = re.compile(rf"^\s*(?:async\s+)?def\s+{re.escape(test_id)}\b", re.MULTILINE)
    syntax_candidate: str | None = None

    for py_path in py_files:
        rel_posix = py_path.relative_to(ws_resolved).as_posix()
        try:
            text = py_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(text, filename=rel_posix)
        except SyntaxError:
            if func_def_re.search(text):
                return (rel_posix, True, True)
            if syntax_candidate is None and (
                py_path.name.startswith("test_")
                or py_path.name.endswith("_test.py")
                or "tests" in py_path.relative_to(ws_resolved).parts
            ):
                syntax_candidate = rel_posix
            continue

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_id:
                return (f"{rel_posix}::{test_id}", True, False)
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if (
                        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == test_id
                    ):
                        return (f"{rel_posix}::{node.name}::{test_id}", True, False)

    if syntax_candidate is not None and func_def_re.search(
        (ws_resolved / syntax_candidate).read_text(encoding="utf-8", errors="replace")
    ):
        return (syntax_candidate, True, True)

    return (test_id, False, False)


def _deterministic_evidence_id(assessed_revision: str, test_id: str) -> str:
    rev_tag = re.sub(r"[^A-Za-z0-9_-]", "", assessed_revision)[:12] or "rev"
    digest12 = hashlib.sha256(f"{assessed_revision}\0{test_id}".encode()).hexdigest()[:12]
    return f"ev-verifier-{rev_tag}-{digest12}"


def _run_single_test_in_workspace(
    *,
    workspace_dir: Path,
    home_dir: Path,
    tmp_dir: Path,
    out_dir: Path,
    test_id: str,
    assessed_revision: str,
    timeout_seconds: float,
    max_output_bytes: int,
    network_isolation: str,
    netns_prefix: list[str],
) -> TestVerificationResult:
    nodeid, found_in_ws, syntax_err = _resolve_nodeid_in_workspace(workspace_dir, test_id)
    ev_id = _deterministic_evidence_id(assessed_revision, test_id)

    # Never invoke pytest on an unresolved bare test_id (which could collide with a dir name).
    if not found_in_ws and not syntax_err:
        synthetic_msg = f"test_missing: {test_id} not found in revision {assessed_revision}\n".encode()
        out_hash = hashlib.sha256(synthetic_msg).hexdigest()
        return TestVerificationResult(
            test_id=test_id,
            outcome="inconclusive",
            executed=False,
            evidence_id=ev_id,
            revision=assessed_revision,
            nodeid=nodeid,
            exit_status=None,
            duration_ms=0,
            output_hash=out_hash,
            network_isolation=network_isolation,
            reason="test_missing",
        )

    argv = build_allowed_verifier_argv(nodeid)
    validate_allowed_argv(argv)
    full_argv = [*netns_prefix, *argv]
    scrubbed_env = _build_scrubbed_verifier_env(home_dir, tmp_dir)

    out_path = out_dir / f"{hashlib.sha256(test_id.encode('utf-8')).hexdigest()[:16]}.log"
    timed_out = False
    exit_status: int | None = None
    t0 = time.perf_counter()

    with open(out_path, "wb") as out_f:
        proc = subprocess.Popen(
            full_argv,
            cwd=str(workspace_dir),
            env=scrubbed_env,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=out_f,
            stderr=subprocess.STDOUT,
        )
        try:
            exit_status = proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait()
            exit_status = None

    duration_ms = max(0, round((time.perf_counter() - t0) * 1000))

    with open(out_path, "rb") as read_f:
        captured_bytes = read_f.read(max(0, max_output_bytes))
    output_hash = hashlib.sha256(captured_bytes).hexdigest()
    output_text = captured_bytes.decode("utf-8", errors="replace")

    reason: str | None = None
    if timed_out:
        outcome = "inconclusive"
        reason = "timeout"
        exit_status = None
    elif exit_status == 0:
        outcome = "passed"
    elif exit_status == 2 or syntax_err or (
        "ERROR collecting" in output_text and " failed" not in output_text
    ):
        outcome = "inconclusive"
        reason = "collection_error"
    elif exit_status == 1:
        outcome = "failed"
    elif exit_status in (4, 5):
        outcome = "inconclusive"
        reason = "test_missing"
    else:
        outcome = "inconclusive"
        reason = "collection_error"

    return TestVerificationResult(
        test_id=test_id,
        outcome=outcome,
        executed=True,
        evidence_id=ev_id,
        revision=assessed_revision,
        nodeid=nodeid,
        exit_status=exit_status,
        duration_ms=duration_ms,
        output_hash=output_hash,
        network_isolation=network_isolation,
        reason=reason,
    )


def _resolve_project_source_root(
    conn: sa.Connection | Session,
    tenant_id: str,
    project_id: str,
) -> tuple[str, str]:
    """Look up active registered source_id and canonical_root for ``(tenant_id, project_id)``."""
    row = conn.execute(
        sa.select(
            project_scopes.c.source_id,
            sources.c.canonical_root,
        )
        .select_from(
            project_scopes.join(
                sources,
                (project_scopes.c.tenant_id == sources.c.tenant_id)
                & (project_scopes.c.source_id == sources.c.source_id),
            )
        )
        .where(
            project_scopes.c.tenant_id == tenant_id,
            project_scopes.c.project_id == project_id,
            project_scopes.c.status != "retired",
        )
        .limit(1)
    ).mappings().first()

    if row is None:
        raise VerifierError(
            f"No active registered project_scope/source found for tenant={tenant_id!r}, project={project_id!r}"
        )

    source_id = str(row["source_id"])
    canonical_root = str(row["canonical_root"])

    loc = conn.execute(
        sa.select(location_registrations.c.state).where(
            location_registrations.c.tenant_id == tenant_id,
            location_registrations.c.source_id == source_id,
            location_registrations.c.project_id == project_id,
        )
    ).mappings().first()
    if loc is not None and loc["state"] in ("suspended", "retired"):
        raise PermissionError(
            f"Project location for source={source_id!r}, project={project_id!r} is in state {loc['state']!r}"
        )

    share_row = conn.execute(
        sa.select(source_shares.c.state).where(
            source_shares.c.requester_tenant_id == tenant_id,
            source_shares.c.requester_source_id == source_id,
        )
    ).mappings().first()
    if share_row is not None and share_row["state"] in ("pending_share", "rejected", "revoked"):
        raise PermissionError(
            f"Source {source_id!r} is in unreadable share state {share_row['state']!r}"
        )

    return source_id, canonical_root


def _enforce_fixtures_only_guard(canonical_root: str, cfg: Settings) -> None:
    """Refuse real repositories while DG-3 + D-07 remain pending."""
    if cfg.eap_collector_allow_real_roots:
        raise VerifierRealRootRefusedError(
            "P2a-WP9 verifier only supports fixture repositories "
            "(EAP_COLLECTOR_ALLOW_REAL_ROOTS must be false until DG-3 + D-07)."
        )

    if cfg.eap_collector_fixtures_root is not None and not _is_path_within(
        cfg.eap_collector_fixtures_root, canonical_root
    ):
        raise RootNotPermittedSecurityError(
            f"Verifier refused root '{canonical_root}': path is outside "
            f"EAP_COLLECTOR_FIXTURES_ROOT '{cfg.eap_collector_fixtures_root}'."
        )

    check_root_permission(
        canonical_root,
        allow_real_roots=False,
        fixtures_root=cfg.eap_collector_fixtures_root,
    )


def _extract_baseline_mapped_test_ids(criteria: Sequence[dict[str, Any]]) -> list[str]:
    mapped: list[str] = []
    seen: set[str] = set()
    for crit in criteria:
        if not isinstance(crit, dict):
            continue
        for ref in crit.get("verification_refs") or []:
            if not isinstance(ref, dict):
                continue
            if ref.get("kind") not in ("test_id", "probe_id"):
                continue
            tid = ref.get("ref") or ref.get("id")
            if isinstance(tid, str) and tid and tid not in seen:
                seen.add(tid)
                mapped.append(tid)
    return mapped


def _collect_unmapped_candidate_ids(
    conn: sa.Connection | Session,
    tenant_id: str,
    project_id: str,
    mapped_set: set[str],
    requested_test_ids: Sequence[str] | None,
) -> list[str]:
    """Identify test_ids requested or reported in active updates that are not in the baseline."""
    unmapped: list[str] = []
    seen: set[str] = set()

    def _consider(raw_tid: Any) -> None:
        if isinstance(raw_tid, str) and raw_tid and raw_tid not in mapped_set and raw_tid not in seen:
            seen.add(raw_tid)
            unmapped.append(raw_tid)

    if requested_test_ids is not None:
        for tid in requested_test_ids:
            _consider(tid)

    upd_rows = conn.execute(
        sa.select(ppm_updates.c.payload).where(
            ppm_updates.c.tenant_id == tenant_id,
            ppm_updates.c.project_id == project_id,
            ppm_updates.c.supersession_state == "active",
        )
    ).mappings().all()
    for u_row in upd_rows:
        payload = u_row["payload"] or {}
        if not isinstance(payload, dict):
            continue
        for chk in payload.get("checks") or []:
            if not isinstance(chk, dict):
                continue
            for tid in chk.get("test_ids") or []:
                _consider(tid)
            if chk.get("test_id"):
                _consider(chk.get("test_id"))

    return unmapped


def _upsert_verifier_evidence(
    conn: sa.Connection | Session,
    *,
    tenant_id: str,
    project_id: str,
    res: TestVerificationResult,
    now: dt.datetime,
) -> None:
    if not res.evidence_id:
        return
    existing = conn.execute(
        sa.select(ppm_evidence).where(
            ppm_evidence.c.tenant_id == tenant_id,
            ppm_evidence.c.project_id == project_id,
            ppm_evidence.c.evidence_id == res.evidence_id,
        )
    ).mappings().first()

    out_hash = res.output_hash or hashlib.sha256(b"").hexdigest()
    if existing is None:
        conn.execute(
            ppm_evidence.insert().values(
                id=f"ev-{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                project_id=project_id,
                evidence_id=res.evidence_id,
                trust_class="verifier_observed",
                producer=PRODUCER_NAME,
                revision=res.revision,
                test_id=res.test_id,
                covered_test_ids=[res.test_id],
                outcome=res.outcome,
                collected_at=now,
                output_hash=out_hash,
                artifact_path=None,
                simulated=False,
                exit_status=res.exit_status,
                duration_ms=res.duration_ms,
                network_isolation=res.network_isolation,
                reason=res.reason,
                created_at=now,
            )
        )
        return

    unchanged = (
        existing["trust_class"] == "verifier_observed"
        and existing["producer"] == PRODUCER_NAME
        and existing["revision"] == res.revision
        and existing["test_id"] == res.test_id
        and existing["outcome"] == res.outcome
        and existing["output_hash"] == out_hash
        and existing["simulated"] is False
        and existing["exit_status"] == res.exit_status
        and existing["network_isolation"] == res.network_isolation
        and existing["reason"] == res.reason
    )
    if unchanged:
        return

    conn.execute(
        ppm_evidence.update()
        .where(
            ppm_evidence.c.tenant_id == tenant_id,
            ppm_evidence.c.project_id == project_id,
            ppm_evidence.c.evidence_id == res.evidence_id,
        )
        .values(
            trust_class="verifier_observed",
            producer=PRODUCER_NAME,
            revision=res.revision,
            test_id=res.test_id,
            covered_test_ids=[res.test_id],
            outcome=res.outcome,
            collected_at=now,
            output_hash=out_hash,
            artifact_path=None,
            simulated=False,
            exit_status=res.exit_status,
            duration_ms=res.duration_ms,
            network_isolation=res.network_isolation,
            reason=res.reason,
        )
    )


def run_verifier(
    conn: sa.Connection | Session,
    tenant_id: str,
    project_id: str,
    assessed_revision: str,
    baseline_revision: int | None = None,
    *,
    requested_test_ids: Sequence[str] | None = None,
    timeout_seconds: float = 15.0,
    max_output_bytes: int = 65_536,
    settings: Settings | None = None,
) -> VerifierReport:
    """Verify mapped tests for ``assessed_revision`` inside an isolated temporary workspace."""
    cfg = settings if settings is not None else get_settings()
    net_iso, netns_prefix = _detect_network_isolation()

    # 1. Feature flag / rollback check
    if not cfg.ppm_verifier_enabled:
        return VerifierReport(
            tenant_id=tenant_id,
            project_id=project_id,
            assessed_revision=assessed_revision,
            baseline_revision=baseline_revision,
            enabled=False,
            executed=False,
            network_isolation=net_iso,
            mapped_test_ids=[],
            unmapped_test_ids=[],
            results=[],
            workspace_removed=True,
            skipped_reason="verifier_disabled",
        )

    # 2. Fixtures-only boundary + source lookup
    if cfg.eap_collector_allow_real_roots:
        raise VerifierRealRootRefusedError(
            "P2a-WP9 verifier only supports fixture repositories "
            "(EAP_COLLECTOR_ALLOW_REAL_ROOTS must be false until DG-3 + D-07)."
        )

    _, canonical_root = _resolve_project_source_root(conn, tenant_id, project_id)
    _enforce_fixtures_only_guard(canonical_root, cfg)

    # 3. Approved baseline lookup
    bl_query = sa.select(ppm_baselines).where(
        ppm_baselines.c.tenant_id == tenant_id,
        ppm_baselines.c.project_id == project_id,
        ppm_baselines.c.status == "approved",
    )
    if baseline_revision is not None:
        bl_query = bl_query.where(ppm_baselines.c.baseline_revision == baseline_revision)
    else:
        bl_query = bl_query.order_by(ppm_baselines.c.baseline_revision.desc())

    bl_row = conn.execute(bl_query.limit(1)).mappings().first()
    if bl_row is None:
        unmapped = list(dict.fromkeys(requested_test_ids or []))
        no_baseline_results = [
            TestVerificationResult(
                test_id=tid,
                outcome="test_not_mapped",
                executed=False,
                evidence_id=None,
                revision=assessed_revision,
                network_isolation=net_iso,
                reason="test_not_mapped",
            )
            for tid in unmapped
        ]
        return VerifierReport(
            tenant_id=tenant_id,
            project_id=project_id,
            assessed_revision=assessed_revision,
            baseline_revision=baseline_revision,
            enabled=True,
            executed=False,
            network_isolation=net_iso,
            mapped_test_ids=[],
            unmapped_test_ids=unmapped,
            results=no_baseline_results,
            workspace_removed=True,
            skipped_reason="no_approved_baseline",
        )

    resolved_baseline_rev = int(bl_row["baseline_revision"])
    mapped_test_ids = _extract_baseline_mapped_test_ids(list(bl_row["criteria"] or []))
    mapped_set = set(mapped_test_ids)
    unmapped_test_ids = _collect_unmapped_candidate_ids(
        conn,
        tenant_id,
        project_id,
        mapped_set,
        requested_test_ids,
    )

    if requested_test_ids is not None:
        seen_req: set[str] = set()
        tests_to_run: list[str] = []
        for tid in requested_test_ids:
            if tid in mapped_set and tid not in seen_req:
                seen_req.add(tid)
                tests_to_run.append(tid)
    else:
        tests_to_run = list(mapped_test_ids)

    results: list[TestVerificationResult] = []
    sandbox_root = Path(tempfile.mkdtemp(prefix="eap_verifier_sandbox_"))

    try:
        workspace_dir = sandbox_root / "workspace"
        home_dir = sandbox_root / "home"
        tmp_dir = sandbox_root / "tmp"
        out_dir = sandbox_root / "out"
        home_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Defense in depth: guarantee sandbox workspace is distinct from registered root
        if _is_path_within(canonical_root, workspace_dir) or _is_path_within(workspace_dir, canonical_root):
            raise VerifierError(
                f"Sandbox workspace '{workspace_dir}' overlaps registered root '{canonical_root}'"
            )

        tar_bytes = export_git_archive_tar(
            canonical_root,
            assessed_revision,
            timeout=max(timeout_seconds, 15.0),
        )
        _extract_tar_safely(tar_bytes, workspace_dir)

        now = dt.datetime.now(dt.UTC)
        for tid in tests_to_run:
            res = _run_single_test_in_workspace(
                workspace_dir=workspace_dir,
                home_dir=home_dir,
                tmp_dir=tmp_dir,
                out_dir=out_dir,
                test_id=tid,
                assessed_revision=assessed_revision,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                network_isolation=net_iso,
                netns_prefix=netns_prefix,
            )
            _upsert_verifier_evidence(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                res=res,
                now=now,
            )
            results.append(res)
    finally:
        shutil.rmtree(sandbox_root, ignore_errors=True)

    for utid in unmapped_test_ids:
        results.append(
            TestVerificationResult(
                test_id=utid,
                outcome="test_not_mapped",
                executed=False,
                evidence_id=None,
                revision=assessed_revision,
                network_isolation=net_iso,
                reason="test_not_mapped",
            )
        )

    workspace_removed = not sandbox_root.exists()
    return VerifierReport(
        tenant_id=tenant_id,
        project_id=project_id,
        assessed_revision=assessed_revision,
        baseline_revision=resolved_baseline_rev,
        enabled=True,
        executed=True,
        network_isolation=net_iso,
        mapped_test_ids=mapped_test_ids,
        unmapped_test_ids=unmapped_test_ids,
        results=results,
        workspace_removed=workspace_removed,
        skipped_reason=None,
    )


__all__ = [
    "PRODUCER_NAME",
    "TestVerificationResult",
    "VerifierAllowlistError",
    "VerifierError",
    "VerifierRealRootRefusedError",
    "VerifierReport",
    "build_allowed_verifier_argv",
    "run_verifier",
    "validate_allowed_argv",
    "validate_nodeid",
]
