"""Snapshot-consistent HUD projection builder and opaque AES-GCM cursor (P3a-WP1, P3a-WP2)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import sqlalchemy as sa
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from eap.config import OLD_INSECURE_HUD_CURSOR_SECRET, Settings, get_settings
from eap.contracts.models.hud_projection import HudProjection
from eap.contracts.rules import ContractViolation, check_hud_projection_rules
from eap.db.assessment_tables import ppm_assessments
from eap.db.event_tables import projection_checkpoints, stream_heads
from eap.db.policy_tables import stream_retention_floors
from eap.db.snapshot_tables import project_snapshots
from eap.db.tables import (
    commands,
    event_log,
    project_current_assessment,
    runs,
    tasks,
    usage_ledger,
)
from eap.events.projection import PROJECTION_NAME_HUD_RUNS, reduce_event
from eap.policy.grants import get_grant_epoch, get_revoked_project_ids

if TYPE_CHECKING:
    from eap.api.dependencies import Principal

logger = logging.getLogger("eap.hud.snapshot")

CURSOR_PREFIX = "ehc2_"
_CURSOR_FORMAT_VERSION = 2
_WIRE_VERSION = 2
_NONCE_LEN = 12
_TAG_LEN = 16
_MAX_INLINE_STREAM_OFFSETS = 64
_CURSOR_AAD = b"eap-hud-cursor-v2:" + bytes([_WIRE_VERSION])
_ALLOWED_ACCEPTANCE = frozenset({"not_requested", "pending", "accepted", "rejected", "superseded"})
_ALLOWED_VERIFICATION = frozenset({"unknown", "claimed", "partially_verified", "verified", "contradicted", "stale"})
_ALLOWED_VALIDITY = frozenset({"current", "stale", "unknown", "conflicted"})
_ALLOWED_COMMAND_KINDS = frozenset({"cancel", "reconcile", "retry_new_attempt"})
_ALLOWED_COMMAND_STATES = frozenset({"accepted", "acknowledged"})
_ALLOWED_RUN_STATES = frozenset(
    {
        "queued",
        "dispatching",
        "running",
        "waiting_approval",
        "cancelling",
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
        "unknown",
    }
)
_ALLOWED_TASK_STATES = frozenset(
    {"draft", "ready", "active", "blocked", "review_required", "completed", "failed", "cancelled"}
)


class CursorError(Exception):
    """Base error for invalid, tampered, expired, or out-of-scope snapshot cursors."""

    status_code: int = 400
    code: str = "resync_required"
    reason: str = "cursor_invalid"
    resync_required: bool = True

    def __init__(
        self,
        message: str = "Snapshot cursor is invalid; resync required",
        *,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if reason is not None:
            self.reason = reason

    def to_response_dict(self) -> dict[str, Any]:
        return {
            "detail": self.message,
            "code": self.code,
            "reason": self.reason,
            "resync_required": self.resync_required,
        }


class CursorTamperedError(CursorError):
    """Cursor failed AEAD authentication tag verification, key check, or decoding."""

    status_code = 400
    code = "resync_required"
    reason = "cursor_invalid"


class CursorExpiredError(CursorError):
    """Cursor exceeded its configured TTL."""

    status_code = 400
    code = "resync_required"
    reason = "cursor_expired"


class CursorScopeMismatchError(CursorError):
    """Cursor belongs to a different tenant, viewer scope, or grant epoch."""

    status_code = 403
    code = "resync_required"
    reason = "cursor_scope_mismatch"


class CursorRetentionGapError(CursorError):
    """Cursor points behind retained stream offsets so a full replay is impossible."""

    status_code = 400
    code = "resync_required"
    reason = "cursor_retention_gap"


class HudContractViolationError(Exception):
    """Raised when an assembled HUD projection violates hud-projection 1.1 contract."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class CursorPayload:
    """Internal decoded cursor coordinates bound to tenant, scope, and grant epoch."""

    tenant_id: str
    scope: str
    offset: int
    grant_epoch: int = 0
    stream_offsets: dict[str, int] = field(default_factory=dict)
    issued_at: int = 0
    expires_at: int = 0
    last_event_id: int = 0
    stream_offsets_omitted: bool = False


@dataclass(frozen=True, slots=True)
class ProjectAssessmentSummary:
    """Project assessment summary combining contract 1.1 fields and extended inspection fields.

    Note (ISS-038): hud-projection 1.1 schema sets `additionalProperties: false` on
    `rows[].project` with 8 properties; `acceptance_readiness`, `assessed_revision`,
    and `last_successful_scan_at` are loaded atomically alongside them for internal
    callers and future contract 1.2 emission.
    """

    project_id: str
    assessment_id: str | None
    verification: str
    validity: str
    acceptance: str
    acceptance_readiness: str
    assessed_revision: str | None
    assessment_as_of: str | None
    last_successful_scan_at: str | None
    verified_weight: float | None
    total_weight: float | None

    def to_contract_dict(self) -> dict[str, Any]:
        """Serialize strictly to hud-projection 1.1 `rows[].project` shape."""
        return {
            "project_id": self.project_id,
            "assessment_id": self.assessment_id,
            "verification": self.verification,
            "validity": self.validity,
            "acceptance": self.acceptance,
            "assessment_as_of": self.assessment_as_of,
            "verified_weight": self.verified_weight,
            "total_weight": self.total_weight,
        }


def _to_iso_utc(dt: Any) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        aware = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        return aware.isoformat().replace("+00:00", "Z")
    return str(dt)


def _derive_cursor_aead_key(secret: bytes) -> bytes:
    """Derive a 256-bit AES-GCM key from the cursor secret, rejecting retired static keys."""
    if not secret or secret == OLD_INSECURE_HUD_CURSOR_SECRET:
        raise CursorTamperedError("Snapshot cursor secret is invalid or retired; resync required")
    return hmac.new(secret, b"eap-hud-cursor-aead-aes256gcm-v2", hashlib.sha256).digest()


def encode_snapshot_cursor(
    *,
    tenant_id: str,
    scope: str,
    offset: int,
    stream_offsets: dict[str, int] | None = None,
    grant_epoch: int = 0,
    last_event_id: int = 0,
    settings: Settings | None = None,
    now: datetime | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Encode and AES-256-GCM seal an opaque HUD snapshot cursor (ISS-039).

    Raw tenant IDs, scope refs, DB IDs, stream IDs, and offsets are encrypted and
    authenticated using ``cryptography`` AES-GCM with a random 96-bit nonce and
    a 128-bit authentication tag bound to protocol version AAD.
    """
    cfg = settings or get_settings()
    try:
        secret = cfg.get_hud_cursor_secret()
    except ValueError as exc:
        raise CursorTamperedError("Snapshot cursor secret is retired; resync required") from exc
    aead_key = _derive_cursor_aead_key(secret)

    effective_ttl = cfg.hud_cursor_ttl_seconds if ttl_seconds is None else max(1, int(ttl_seconds))
    current_dt = now if now is not None else datetime.now(UTC)
    issued_at = int(current_dt.timestamp())
    expires_at = issued_at + effective_ttl

    normalized_streams = {
        str(k): int(v) for k, v in sorted((stream_offsets or {}).items()) if int(v) >= 0
    }
    so_omitted = len(normalized_streams) > _MAX_INLINE_STREAM_OFFSETS
    payload_obj = {
        "v": _CURSOR_FORMAT_VERSION,
        "tid": tenant_id,
        "scp": scope,
        "off": int(offset),
        "so": {} if so_omitted else normalized_streams,
        "so_om": so_omitted,
        "lid": max(0, int(last_event_id)),
        "ge": int(grant_epoch),
        "iat": issued_at,
        "exp": expires_at,
    }
    plaintext = json.dumps(payload_obj, separators=(",", ":"), sort_keys=True).encode("utf-8")

    nonce = os.urandom(_NONCE_LEN)
    ct_with_tag = AESGCM(aead_key).encrypt(nonce, plaintext, _CURSOR_AAD)
    raw = bytes([_WIRE_VERSION]) + nonce + ct_with_tag
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"{CURSOR_PREFIX}{encoded}"


def verify_snapshot_cursor(
    cursor: str,
    *,
    expected_tenant_id: str,
    expected_scope: str | None = None,
    expected_grant_epoch: int | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> CursorPayload:
    """Verify AES-GCM authentication tag, decrypt opaque cursor, check TTL and tenant/scope/epoch.

    Error messages intentionally omit tenant/scope details to avoid cross-tenant leakage.
    Old-format cursors (e.g. ``ehc1_``) and cursors minted with the retired constant key
    fail verification with ``CursorTamperedError`` (``resync_required``).
    """
    if not isinstance(cursor, str) or not cursor.startswith(CURSOR_PREFIX):
        raise CursorTamperedError("Snapshot cursor is invalid; resync required")

    token_body = cursor[len(CURSOR_PREFIX) :]
    if not token_body:
        raise CursorTamperedError("Snapshot cursor is invalid; resync required")

    padded = token_body + ("=" * ((4 - len(token_body) % 4) % 4))
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise CursorTamperedError("Snapshot cursor is invalid; resync required") from exc

    min_len = 1 + _NONCE_LEN + _TAG_LEN + 2
    if len(raw) < min_len or raw[0] != _WIRE_VERSION:
        raise CursorTamperedError("Snapshot cursor is invalid; resync required")

    nonce = raw[1 : 1 + _NONCE_LEN]
    ct_with_tag = raw[1 + _NONCE_LEN :]

    cfg = settings or get_settings()
    try:
        secret = cfg.get_hud_cursor_secret()
    except ValueError as exc:
        raise CursorTamperedError("Snapshot cursor secret is retired; resync required") from exc
    aead_key = _derive_cursor_aead_key(secret)

    try:
        plaintext = AESGCM(aead_key).decrypt(nonce, ct_with_tag, _CURSOR_AAD)
    except (InvalidTag, ValueError) as exc:
        raise CursorTamperedError("Snapshot cursor integrity check failed; resync required") from exc

    try:
        data = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CursorTamperedError("Snapshot cursor is invalid; resync required") from exc

    if not isinstance(data, dict) or data.get("v") != _CURSOR_FORMAT_VERSION:
        raise CursorTamperedError("Snapshot cursor is invalid; resync required")

    try:
        tenant_id = str(data["tid"])
        scope = str(data["scp"])
        offset = int(data["off"])
        grant_epoch = int(data["ge"])
        issued_at = int(data["iat"])
        expires_at = int(data["exp"])
        last_event_id = int(data.get("lid", 0))
        so_omitted = bool(data.get("so_om", False))
        raw_so = data.get("so") or {}
        if not isinstance(raw_so, dict):
            raise TypeError("Invalid stream_offsets")
        stream_offsets = {str(k): int(v) for k, v in raw_so.items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise CursorTamperedError("Snapshot cursor is invalid; resync required") from exc

    # Generic message without tenant/scope identity details.
    if not hmac.compare_digest(tenant_id.encode("utf-8"), expected_tenant_id.encode("utf-8")):
        raise CursorScopeMismatchError("Snapshot cursor does not match caller scope; resync required")

    if expected_scope is not None and not hmac.compare_digest(
        scope.encode("utf-8"), expected_scope.encode("utf-8")
    ):
        raise CursorScopeMismatchError("Snapshot cursor does not match caller scope; resync required")

    if expected_grant_epoch is not None and grant_epoch != expected_grant_epoch:
        raise CursorScopeMismatchError("Snapshot cursor authorization epoch changed; resync required")

    current_ts = int((now if now is not None else datetime.now(UTC)).timestamp())
    if current_ts >= expires_at:
        raise CursorExpiredError("Snapshot cursor expired; resync required")

    return CursorPayload(
        tenant_id=tenant_id,
        scope=scope,
        offset=offset,
        grant_epoch=grant_epoch,
        stream_offsets=stream_offsets,
        issued_at=issued_at,
        expires_at=expires_at,
        last_event_id=last_event_id,
        stream_offsets_omitted=so_omitted,
    )


def viewer_scope_ref_for_principal(principal: Principal, project_id: str | None = None) -> str:
    """Derive deterministic viewer_scope_ref from authenticated server-side Principal."""
    if project_id:
        return f"principal:{principal.principal_id}/tenant:{principal.tenant_id}/project:{project_id}"
    return f"principal:{principal.principal_id}/tenant:{principal.tenant_id}/all-projects"


def _extract_project_id_from_mapping(obj: Any) -> str | None:
    if not isinstance(obj, dict):
        return None
    direct = obj.get("project_id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    nested_payload = obj.get("payload")
    if isinstance(nested_payload, dict):
        nested = nested_payload.get("project_id")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def _normalize_remote_outcome(raw_outcome: Any, run_state: str | None) -> str:
    if run_state is None:
        return "not_applicable"
    if run_state == "unknown":
        return "unknown"
    if isinstance(raw_outcome, str):
        low = raw_outcome.strip().lower()
        if low == "unknown":
            return "unknown"
        if low == "not_applicable":
            return "not_applicable"
        if low in ("known", "succeeded", "failed", "cancelled", "timed_out"):
            return "known"
    if run_state in ("succeeded", "failed", "cancelled", "timed_out", "running"):
        return "known"
    return "unknown"


def _normalize_connectivity(run_state: str | None, remote_outcome: str) -> str:
    if run_state is None:
        return "healthy"
    if run_state == "unknown" or remote_outcome == "unknown":
        return "unknown"
    return "healthy"


def load_project_assessment_summaries(
    conn: sa.Connection,
    *,
    tenant_id: str,
    project_id: str | None = None,
) -> dict[str, ProjectAssessmentSummary]:
    """Read current project assessments and latest successful scan timestamps in one query pass."""
    stmt = (
        sa.select(
            project_current_assessment.c.project_id,
            project_current_assessment.c.assessment_id,
            ppm_assessments.c.assessed_revision,
            ppm_assessments.c.output,
            ppm_assessments.c.created_at,
        )
        .select_from(
            project_current_assessment.outerjoin(
                ppm_assessments,
                sa.and_(
                    ppm_assessments.c.tenant_id == project_current_assessment.c.tenant_id,
                    ppm_assessments.c.id == project_current_assessment.c.assessment_id,
                ),
            )
        )
        .where(project_current_assessment.c.tenant_id == tenant_id)
    )
    if project_id is not None:
        stmt = stmt.where(project_current_assessment.c.project_id == project_id)

    current_rows = conn.execute(stmt).fetchall()
    if not current_rows:
        return {}

    # Query latest successful snapshot timestamp per project in the same transaction.
    scan_stmt = (
        sa.select(
            project_snapshots.c.project_id,
            sa.func.max(project_snapshots.c.created_at).label("last_successful_scan_at"),
        )
        .where(
            project_snapshots.c.tenant_id == tenant_id,
            project_snapshots.c.status.in_(["captured", "partial"]),
        )
        .group_by(project_snapshots.c.project_id)
    )
    if project_id is not None:
        scan_stmt = scan_stmt.where(project_snapshots.c.project_id == project_id)
    scan_by_project = {str(r[0]): _to_iso_utc(r[1]) for r in conn.execute(scan_stmt).fetchall()}

    summaries: dict[str, ProjectAssessmentSummary] = {}
    for proj_id, assessment_id, assessed_revision, output, created_at in current_rows:
        proj_key = str(proj_id)
        out = output if isinstance(output, dict) else {}

        verification = str(out.get("verification") or "unknown")
        if verification not in _ALLOWED_VERIFICATION:
            verification = "unknown"

        validity = str(out.get("validity") or "unknown")
        if validity not in _ALLOWED_VALIDITY:
            validity = "unknown"

        acceptance_val = out.get("acceptance")
        acceptance = str(acceptance_val) if acceptance_val in _ALLOWED_ACCEPTANCE else "not_requested"

        readiness_val = out.get("acceptance_readiness")
        acceptance_readiness = str(readiness_val) if isinstance(readiness_val, str) and readiness_val else "not_ready"

        raw_progress = out.get("progress")
        progress_block: dict[str, Any] = raw_progress if isinstance(raw_progress, dict) else {}
        basis = progress_block.get("basis")
        raw_total_w = progress_block.get("total_weight")
        raw_verified_w = progress_block.get("verified_weight")

        # Never fabricate a zero denominator or weight when baseline is missing or basis is "none".
        if basis == "none" or raw_total_w is None or float(raw_total_w) <= 0:
            verified_weight: float | None = None
            total_weight: float | None = None
        else:
            total_weight = float(raw_total_w)
            verified_weight = float(raw_verified_w) if raw_verified_w is not None else 0.0
            if verified_weight < 0:
                verified_weight = 0.0
            verified_weight = min(verified_weight, total_weight)

        as_of_iso = _to_iso_utc(out.get("as_of") or created_at)
        summaries[proj_key] = ProjectAssessmentSummary(
            project_id=proj_key,
            assessment_id=str(assessment_id) if assessment_id else None,
            verification=verification,
            validity=validity,
            acceptance=acceptance,
            acceptance_readiness=acceptance_readiness,
            assessed_revision=str(assessed_revision) if assessed_revision else out.get("assessed_revision"),
            assessment_as_of=as_of_iso,
            last_successful_scan_at=scan_by_project.get(proj_key),
            verified_weight=verified_weight,
            total_weight=total_weight,
        )

    return summaries


@runtime_checkable
class RowKindBuilder(Protocol):
    """Extensibility hook protocol for P3a-WP1+ HUD snapshot row builders (e.g. P3a-WP5 agent rows)."""

    kind: str

    def build_rows(
        self,
        conn: sa.Connection,
        *,
        tenant_id: str,
        project_id: str | None = None,
        **context: Any,
    ) -> list[dict[str, Any]]:
        """Build zero or more HUD row dicts inside the caller's REPEATABLE READ connection."""
        ...


class TaskRunRowBuilder:
    """Default row builder projecting tasks, runs, commands, usage cost, and project assessment."""

    kind: str = "task_run"

    def build_rows(
        self,
        conn: sa.Connection,
        *,
        tenant_id: str,
        project_id: str | None = None,
        project_summaries: dict[str, ProjectAssessmentSummary] | None = None,
        event_rows_by_stream: dict[str, list[dict[str, Any]]] | None = None,
        **context: Any,
    ) -> list[dict[str, Any]]:
        revoked_project_ids: set[str] = set(context.get("revoked_project_ids") or ())
        checkpoints_by_stream: dict[str, tuple[int, dict[str, Any]]] | None = context.get("checkpoints_by_stream")
        stream_head_offsets: dict[str, int] | None = context.get("stream_head_offsets")
        max_event_id: int | None = context.get("max_event_id")

        if project_summaries is None:
            project_summaries = load_project_assessment_summaries(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
            )

        task_rows = conn.execute(
            sa.select(
                tasks.c.id,
                tasks.c.state,
                tasks.c.project_id,
                tasks.c.created_at,
                tasks.c.updated_at,
            )
            .where(tasks.c.tenant_id == tenant_id)
            .order_by(tasks.c.id.asc())
        ).fetchall()

        run_rows = conn.execute(
            sa.select(
                runs.c.task_id,
                runs.c.run_id,
                runs.c.attempt,
                runs.c.state,
                runs.c.remote_outcome,
                runs.c.project_id,
                runs.c.created_at,
                runs.c.updated_at,
            )
            .where(runs.c.tenant_id == tenant_id)
            .order_by(runs.c.task_id.asc(), runs.c.attempt.desc(), runs.c.created_at.desc(), runs.c.id.desc())
        ).fetchall()

        latest_run_by_task: dict[str, dict[str, Any]] = {}
        run_id_to_task_id: dict[str, str] = {}
        project_by_run: dict[str, str] = {}
        project_by_task: dict[str, str] = {}

        for t_id, _t_state, t_proj, _t_created, _t_updated in task_rows:
            if t_proj:
                project_by_task[str(t_id)] = str(t_proj)

        for t_id, r_id, r_attempt, r_state, r_outcome, r_proj, r_created, r_updated in run_rows:
            t_key = str(t_id)
            r_key = str(r_id)
            run_id_to_task_id.setdefault(r_key, t_key)
            if r_proj:
                project_by_run.setdefault(r_key, str(r_proj))
                project_by_task.setdefault(t_key, str(r_proj))
            if t_key not in latest_run_by_task:
                latest_run_by_task[t_key] = {
                    "run_id": r_key,
                    "attempt": int(r_attempt) if r_attempt is not None else 1,
                    "state": str(r_state),
                    "remote_outcome": r_outcome,
                    "project_id": str(r_proj) if r_proj else None,
                    "created_at": r_created,
                    "updated_at": r_updated,
                }

        if event_rows_by_stream is None:
            if checkpoints_by_stream is None or stream_head_offsets is None or max_event_id is None:
                (
                    checkpoints_by_stream,
                    stream_head_offsets,
                    max_event_id,
                    event_rows_by_stream,
                ) = _load_projection_state_and_tail_events(
                    conn,
                    tenant_id=tenant_id,
                    run_ids=set(run_id_to_task_id.keys()),
                )
            else:
                event_rows_by_stream = {}
        else:
            checkpoints_by_stream = checkpoints_by_stream or {}

        # Pending commands per run_id (latest accepted/acknowledged command).
        cmd_rows = conn.execute(
            sa.select(
                commands.c.id,
                commands.c.task_id,
                commands.c.run_id,
                commands.c.command_type,
                commands.c.status,
                commands.c.payload,
            )
            .where(commands.c.tenant_id == tenant_id)
            .order_by(commands.c.created_at.desc(), commands.c.id.desc())
        ).fetchall()
        pending_by_run: dict[str, dict[str, str]] = {}
        pending_by_task: dict[str, dict[str, str]] = {}

        for cmd_id, cmd_task_id, cmd_run_id, cmd_type, cmd_status, cmd_payload in cmd_rows:
            extracted_proj = _extract_project_id_from_mapping(cmd_payload)
            if extracted_proj:
                if cmd_run_id and str(cmd_run_id) not in project_by_run:
                    project_by_run[str(cmd_run_id)] = extracted_proj
                if cmd_task_id and str(cmd_task_id) not in project_by_task:
                    project_by_task[str(cmd_task_id)] = extracted_proj

            if (
                str(cmd_status) in _ALLOWED_COMMAND_STATES
                and str(cmd_type) in _ALLOWED_COMMAND_KINDS
            ):
                cmd_obj = {
                    "command_id": str(cmd_id),
                    "kind": str(cmd_type),
                    "state": str(cmd_status),
                }
                if cmd_run_id and str(cmd_run_id) not in pending_by_run:
                    pending_by_run[str(cmd_run_id)] = cmd_obj
                if cmd_task_id and str(cmd_task_id) not in pending_by_task:
                    pending_by_task[str(cmd_task_id)] = cmd_obj

        # Inspect checkpoints and bounded tail event payloads for explicit project links (ISS-040: no outbox scan).
        for stream_id, (_off, ckpt_state) in checkpoints_by_stream.items():
            if stream_id.startswith("run:"):
                r_key = stream_id.split("run:", 1)[1]
                ckpt_row = ckpt_state.get("row") if isinstance(ckpt_state, dict) else None
                if isinstance(ckpt_row, dict) and isinstance(ckpt_row.get("project"), dict):
                    proj_val = ckpt_row["project"].get("project_id")
                    if isinstance(proj_val, str) and proj_val:
                        project_by_run.setdefault(r_key, proj_val)

        for stream_id, evs in event_rows_by_stream.items():
            if stream_id.startswith("run:"):
                r_key = stream_id.split("run:", 1)[1]
                for ev in reversed(evs):
                    extracted_proj = _extract_project_id_from_mapping(ev.get("payload"))
                    if extracted_proj:
                        project_by_run.setdefault(r_key, extracted_proj)
                        break

        # Usage ledger cost per run_id: missing usage MUST remain unknown/None, never 0.
        usage_rows = conn.execute(
            sa.select(
                usage_ledger.c.run_id,
                usage_ledger.c.cost_estimate,
                usage_ledger.c.reported_at,
            )
            .where(usage_ledger.c.tenant_id == tenant_id)
            .order_by(usage_ledger.c.reported_at.asc(), usage_ledger.c.id.asc())
        ).fetchall()
        usage_by_run: dict[str, list[tuple[float | None, Any]]] = {}
        for u_run_id, cost_est, reported_at in usage_rows:
            if u_run_id is None:
                continue
            val = float(cost_est) if cost_est is not None else None
            usage_by_run.setdefault(str(u_run_id), []).append((val, reported_at))

        rows: list[dict[str, Any]] = []
        seen_stream_ids: set[str] = set()
        single_project_id = next(iter(project_summaries)) if len(project_summaries) == 1 else None

        for t_id, t_state, _t_proj, t_created, t_updated in task_rows:
            task_key = str(t_id)
            task_state = str(t_state) if str(t_state) in _ALLOWED_TASK_STATES else "active"
            run_info = latest_run_by_task.get(task_key)

            run_id: str | None = None
            attempt: int | None = None
            run_state: str | None = None
            if run_info is not None:
                run_id = run_info["run_id"]
                attempt = run_info["attempt"]
                raw_rs = run_info["state"]
                run_state = raw_rs if raw_rs in _ALLOWED_RUN_STATES else "unknown"
                remote_outcome = _normalize_remote_outcome(run_info["remote_outcome"], run_state)
                connectivity = _normalize_connectivity(run_state, remote_outcome)
                obs_dt = run_info["updated_at"] or run_info["created_at"] or t_updated or t_created
                last_observed_at = _to_iso_utc(obs_dt)
                obs_source = "provider_ack" if run_state == "unknown" else "local_runtime"
            else:
                remote_outcome = "not_applicable"
                connectivity = "healthy"
                last_observed_at = _to_iso_utc(t_updated or t_created)
                obs_source = "local_runtime"

            cost_dict = _compute_usage_cost(usage_by_run.get(run_id) if run_id else None)
            pending_cmd = (
                pending_by_run.get(run_id) if run_id else None
            ) or pending_by_task.get(task_key)

            linked_project_id = (
                (project_by_run.get(run_id) if run_id else None)
                or project_by_task.get(task_key)
                or single_project_id
            )
            if project_id is not None and linked_project_id != project_id:
                continue
            if revoked_project_ids and linked_project_id in revoked_project_ids:
                continue

            proj_summary = project_summaries.get(linked_project_id) if linked_project_id else None
            proj_dict = proj_summary.to_contract_dict() if proj_summary is not None else None

            base_row: dict[str, Any] = {
                "task_id": task_key,
                "task_state": task_state,
                "run_id": run_id,
                "attempt": attempt,
                "run_state": run_state,
                "remote_outcome": remote_outcome,
                "connectivity": connectivity,
                "last_observed_at": last_observed_at,
                "observation_source": obs_source,
                "progress": {
                    "kind": "none",
                    "completed_units": None,
                    "total_units": None,
                    "unit": None,
                    "stage": None,
                },
                "project": proj_dict,
                "cost": cost_dict,
                "pending_command": pending_cmd,
            }

            if run_id is not None:
                stream_id = f"run:{run_id}"
                seen_stream_ids.add(stream_id)
                ckpt_entry = checkpoints_by_stream.get(stream_id)
                row_ckpt_state: dict[str, Any] | None = ckpt_entry[1] if ckpt_entry is not None else None
                ckpt_off = ckpt_entry[0] if ckpt_entry is not None else 0
                raw_evs = event_rows_by_stream.get(stream_id) or []
                tail_evs = [ev for ev in raw_evs if int(ev.get("ingest_offset", 0)) > ckpt_off] if ckpt_entry else raw_evs
                if row_ckpt_state is not None or tail_evs:
                    base_row = _fold_checkpoint_and_tail_onto_row(
                        base_row=base_row,
                        checkpoint_state=row_ckpt_state,
                        tail_events=tail_evs,
                        pending_override=pending_cmd,
                        preserve_project_if_unset=proj_dict,
                    )

            rows.append(base_row)

        # Include committed/checkpointed run streams without an explicit tasks table row (direct event ingest).
        orphan_streams = sorted(set(checkpoints_by_stream) | set(event_rows_by_stream))
        for stream_id in orphan_streams:
            if stream_id in seen_stream_ids or not stream_id.startswith("run:"):
                continue
            r_key = stream_id.split("run:", 1)[1]
            if r_key in run_id_to_task_id:
                continue
            ckpt_entry = checkpoints_by_stream.get(stream_id)
            orphan_ckpt_state: dict[str, Any] | None = (
                deepcopy(ckpt_entry[1]) if ckpt_entry is not None and isinstance(ckpt_entry[1], dict) else None
            )
            ckpt_off = ckpt_entry[0] if ckpt_entry is not None else 0
            raw_evs = event_rows_by_stream.get(stream_id) or []
            tail_evs = [ev for ev in raw_evs if int(ev.get("ingest_offset", 0)) > ckpt_off] if ckpt_entry else raw_evs
            if orphan_ckpt_state is None and not tail_evs:
                continue

            linked_project_id = project_by_run.get(r_key) or single_project_id
            if project_id is not None and linked_project_id != project_id:
                continue
            if revoked_project_ids and linked_project_id in revoked_project_ids:
                continue
            proj_summary = project_summaries.get(linked_project_id) if linked_project_id else None
            proj_dict = proj_summary.to_contract_dict() if proj_summary is not None else None

            state: dict[str, Any] | None = orphan_ckpt_state
            for ev in tail_evs:
                state = reduce_event(state, ev)
            if state is not None:
                row = dict(state["row"])
                if row.get("project") is None and proj_dict is not None:
                    row["project"] = proj_dict
                if r_key in pending_by_run:
                    row["pending_command"] = pending_by_run[r_key]
                rows.append(row)

        rows.sort(key=lambda r: (str(r["task_id"]), str(r.get("run_id") or "")))
        return rows


def _compute_usage_cost(entries: list[tuple[float | None, Any]] | None) -> dict[str, Any]:
    """Compute cost strictly enforcing explicit `unknown` when usage or cost is missing."""
    if not entries:
        return {
            "status": "unknown",
            "amount_usd": None,
            "as_of": None,
            "scope": "run",
        }

    known_values = [val for val, _ in entries if val is not None]
    latest_dt = entries[-1][1]
    if not known_values:
        return {
            "status": "unknown",
            "amount_usd": None,
            "as_of": _to_iso_utc(latest_dt),
            "scope": "run",
        }

    total_usd = round(sum(known_values), 6)
    status = "partial" if len(known_values) < len(entries) else "actual"
    return {
        "status": status,
        "amount_usd": total_usd,
        "as_of": _to_iso_utc(latest_dt),
        "scope": "run",
    }


def _fold_stream_events_onto_row(
    *,
    base_row: dict[str, Any],
    events: list[dict[str, Any]],
    pending_override: dict[str, str] | None,
    preserve_project_if_unset: dict[str, Any] | None,
) -> dict[str, Any]:
    """Fold committed event_log events onto a DB-seeded HUD row deterministically."""
    had_usage_event = any(ev.get("event_type") == "usage.reported" for ev in events)

    working = deepcopy(base_row)
    if had_usage_event:
        # Avoid double-counting when event_log already carries usage.reported deltas.
        working["cost"] = {
            "status": "unknown",
            "amount_usd": None,
            "as_of": None,
            "scope": "run",
        }

    state: dict[str, Any] = {"row": working}
    for ev in events:
        state = reduce_event(state, ev)

    row = dict(state["row"])
    if row.get("project") is None and preserve_project_if_unset is not None:
        row["project"] = preserve_project_if_unset
    if pending_override is not None and row.get("pending_command") is None and row.get("run_state") not in (
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
    ):
        row["pending_command"] = pending_override
    return row


def _fold_checkpoint_and_tail_onto_row(
    *,
    base_row: dict[str, Any],
    checkpoint_state: dict[str, Any] | None,
    tail_events: list[dict[str, Any]],
    pending_override: dict[str, str] | None,
    preserve_project_if_unset: dict[str, Any] | None,
) -> dict[str, Any]:
    """Fold checkpointed state plus lagging tail events onto a DB-seeded HUD row."""
    if checkpoint_state is None or not isinstance(checkpoint_state.get("row"), dict):
        return _fold_stream_events_onto_row(
            base_row=base_row,
            events=tail_events,
            pending_override=pending_override,
            preserve_project_if_unset=preserve_project_if_unset,
        )

    working = deepcopy(checkpoint_state["row"])
    if working.get("task_id") in (None, "unknown") and base_row.get("task_id"):
        working["task_id"] = base_row["task_id"]
    if working.get("run_id") is None and base_row.get("run_id") is not None:
        working["run_id"] = base_row["run_id"]
    if base_row.get("attempt") is not None and (
        working.get("attempt") is None
        or (working.get("attempt") == 1 and int(base_row["attempt"]) > 1)
    ):
        working["attempt"] = base_row["attempt"]
    if working.get("project") is None and base_row.get("project") is not None:
        working["project"] = base_row["project"]

    ckpt_cost = working.get("cost")
    ckpt_had_usage = isinstance(ckpt_cost, dict) and (
        ckpt_cost.get("status") != "unknown" or ckpt_cost.get("as_of") is not None
    )
    tail_has_usage = any(ev.get("event_type") == "usage.reported" for ev in tail_events)
    if not ckpt_had_usage and not tail_has_usage and base_row.get("cost") is not None:
        working["cost"] = deepcopy(base_row["cost"])

    state: dict[str, Any] = {"row": working}
    for ev in tail_events:
        state = reduce_event(state, ev)

    row = dict(state["row"])
    if row.get("project") is None and preserve_project_if_unset is not None:
        row["project"] = preserve_project_if_unset
    if pending_override is not None and row.get("pending_command") is None and row.get("run_state") not in (
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
    ):
        row["pending_command"] = pending_override
    return row


def _load_committed_event_rows(conn: sa.Connection, *, tenant_id: str) -> dict[str, list[dict[str, Any]]]:
    stmt = (
        sa.select(
            event_log.c.stream_id,
            event_log.c.ingest_offset,
            event_log.c.event_id,
            event_log.c.producer_id,
            event_log.c.event_type,
            event_log.c.payload,
            event_log.c.occurred_at,
        )
        .where(event_log.c.tenant_id == tenant_id)
        .order_by(event_log.c.stream_id.asc(), event_log.c.ingest_offset.asc())
    )
    by_stream: dict[str, list[dict[str, Any]]] = {}
    for stream_id, ingest_offset, event_id, producer_id, event_type, payload, occurred_at in conn.execute(stmt).fetchall():
        ev_dict = {
            "tenant_id": tenant_id,
            "stream_id": str(stream_id),
            "ingest_offset": int(ingest_offset),
            "event_id": str(event_id),
            "producer_id": str(producer_id),
            "event_type": str(event_type),
            "payload": payload if isinstance(payload, dict) else {},
            "occurred_at": _to_iso_utc(occurred_at),
        }
        by_stream.setdefault(str(stream_id), []).append(ev_dict)
    return by_stream


def _load_projection_state_and_tail_events(
    conn: sa.Connection,
    *,
    tenant_id: str,
    run_ids: set[str] | None = None,
) -> tuple[dict[str, tuple[int, dict[str, Any]]], dict[str, int], int, dict[str, list[dict[str, Any]]]]:
    """Load projection_checkpoints, stream_heads, max event_log.id, and bounded tail events (ISS-040)."""
    ckpt_stmt = sa.select(
        projection_checkpoints.c.stream_id,
        projection_checkpoints.c.last_ingest_offset,
        projection_checkpoints.c.state,
    ).where(
        projection_checkpoints.c.tenant_id == tenant_id,
        projection_checkpoints.c.projection_name == PROJECTION_NAME_HUD_RUNS,
    )
    checkpoints_by_stream: dict[str, tuple[int, dict[str, Any]]] = {}
    for s_id, last_off, state in conn.execute(ckpt_stmt).fetchall():
        if isinstance(state, dict):
            checkpoints_by_stream[str(s_id)] = (int(last_off), state)

    heads_stmt = sa.select(
        stream_heads.c.stream_id,
        stream_heads.c.last_offset,
    ).where(stream_heads.c.tenant_id == tenant_id)
    stream_head_offsets = {
        str(s_id): int(last_off)
        for s_id, last_off in conn.execute(heads_stmt).fetchall()
    }

    max_id_row = conn.execute(
        sa.select(event_log.c.id)
        .where(event_log.c.tenant_id == tenant_id)
        .order_by(event_log.c.id.desc())
        .limit(1)
    ).fetchone()
    max_event_id = int(max_id_row[0]) if max_id_row is not None else 0

    if max_event_id == 0:
        return checkpoints_by_stream, stream_head_offsets, 0, {}

    # Fallback ONLY when stream_heads is completely unpopulated (direct event_log test fixtures).
    if not stream_head_offsets:
        return (
            checkpoints_by_stream,
            stream_head_offsets,
            max_event_id,
            _load_committed_event_rows(conn, tenant_id=tenant_id),
        )

    bounded_targets: list[tuple[str, int]] = []
    seen_targets: set[str] = set()
    for s_id, head_off in stream_head_offsets.items():
        if not s_id.startswith("run:"):
            continue
        ckpt_off = checkpoints_by_stream[s_id][0] if s_id in checkpoints_by_stream else 0
        if head_off > ckpt_off:
            bounded_targets.append((s_id, ckpt_off))
            seen_targets.add(s_id)

    if run_ids:
        for r_id in sorted(run_ids):
            s_id = f"run:{r_id}"
            if s_id not in stream_head_offsets and s_id not in seen_targets:
                ckpt_off = checkpoints_by_stream[s_id][0] if s_id in checkpoints_by_stream else 0
                bounded_targets.append((s_id, ckpt_off))
                seen_targets.add(s_id)

    tail_events_by_stream: dict[str, list[dict[str, Any]]] = {}
    if not bounded_targets:
        return checkpoints_by_stream, stream_head_offsets, max_event_id, tail_events_by_stream

    chunk_size = 200
    for idx in range(0, len(bounded_targets), chunk_size):
        chunk = bounded_targets[idx : idx + chunk_size]
        predicates = [
            sa.and_(event_log.c.stream_id == s_id, event_log.c.ingest_offset > ckpt_off)
            for s_id, ckpt_off in chunk
        ]
        stmt = (
            sa.select(
                event_log.c.stream_id,
                event_log.c.ingest_offset,
                event_log.c.event_id,
                event_log.c.producer_id,
                event_log.c.event_type,
                event_log.c.payload,
                event_log.c.occurred_at,
            )
            .where(event_log.c.tenant_id == tenant_id, sa.or_(*predicates))
            .order_by(event_log.c.stream_id.asc(), event_log.c.ingest_offset.asc())
        )
        for s_id, ingest_offset, event_id, producer_id, event_type, payload, occurred_at in conn.execute(stmt).fetchall():
            tail_events_by_stream.setdefault(str(s_id), []).append(
                {
                    "tenant_id": tenant_id,
                    "stream_id": str(s_id),
                    "ingest_offset": int(ingest_offset),
                    "event_id": str(event_id),
                    "producer_id": str(producer_id),
                    "event_type": str(event_type),
                    "payload": payload if isinstance(payload, dict) else {},
                    "occurred_at": _to_iso_utc(occurred_at),
                }
            )
    return checkpoints_by_stream, stream_head_offsets, max_event_id, tail_events_by_stream


def _load_stream_offsets_and_max_id(conn: sa.Connection, *, tenant_id: str) -> tuple[dict[str, int], int]:
    """Load non-policy stream offsets from stream_heads plus O(1) max event_log.id (ISS-040)."""
    heads_stmt = sa.select(
        stream_heads.c.stream_id,
        stream_heads.c.last_offset,
    ).where(stream_heads.c.tenant_id == tenant_id)
    offsets: dict[str, int] = {
        str(stream_id): int(last_off)
        for stream_id, last_off in conn.execute(heads_stmt).fetchall()
        if not str(stream_id).startswith("policy:")
    }

    max_id_row = conn.execute(
        sa.select(event_log.c.id)
        .where(event_log.c.tenant_id == tenant_id)
        .order_by(event_log.c.id.desc())
        .limit(1)
    ).fetchone()
    max_event_id = int(max_id_row[0]) if max_id_row is not None else 0

    if max_event_id == 0:
        return offsets, 0

    if not offsets:
        stmt = (
            sa.select(
                event_log.c.stream_id,
                sa.func.max(event_log.c.ingest_offset),
            )
            .where(
                event_log.c.tenant_id == tenant_id,
                ~event_log.c.stream_id.startswith("policy:"),
            )
            .group_by(event_log.c.stream_id)
        )
        return {str(stream_id): int(max_off) for stream_id, max_off in conn.execute(stmt).fetchall()}, max_event_id

    # Check only run streams present in `runs` that have no `stream_heads` row (legacy direct inserts).
    run_rows = conn.execute(sa.select(runs.c.run_id).where(runs.c.tenant_id == tenant_id)).fetchall()
    unheaded_streams = [f"run:{r_id}" for (r_id,) in run_rows if f"run:{r_id}" not in offsets]
    if unheaded_streams:
        for idx in range(0, len(unheaded_streams), 200):
            chunk = unheaded_streams[idx : idx + 200]
            fallback_stmt = (
                sa.select(
                    event_log.c.stream_id,
                    sa.func.max(event_log.c.ingest_offset),
                )
                .where(
                    event_log.c.tenant_id == tenant_id,
                    event_log.c.stream_id.in_(chunk),
                )
                .group_by(event_log.c.stream_id)
            )
            for s_id, max_off in conn.execute(fallback_stmt).fetchall():
                offsets[str(s_id)] = int(max_off)

    return offsets, max_event_id


def _load_stream_offsets(conn: sa.Connection, *, tenant_id: str) -> dict[str, int]:
    offsets, _max_id = _load_stream_offsets_and_max_id(conn, tenant_id=tenant_id)
    return offsets


class HudSnapshotBuilder:
    """Extensible snapshot builder coordinating registered row builders (P3a-WP1, P3a-WP5 hook)."""

    def __init__(self, builders: list[RowKindBuilder] | None = None) -> None:
        self._builders: list[RowKindBuilder] = list(builders) if builders is not None else [TaskRunRowBuilder()]

    def register_row_builder(self, builder: RowKindBuilder, *, replace: bool = False) -> None:
        """Register an additional row-kind builder (hook for P3a-WP5 agent inventory)."""
        existing_idx = next((idx for idx, b in enumerate(self._builders) if b.kind == builder.kind), None)
        if existing_idx is not None:
            if not replace:
                raise ValueError(f"Row builder kind {builder.kind!r} already registered")
            self._builders[existing_idx] = builder
            return
        self._builders.append(builder)

    def get_row_builders(self) -> tuple[RowKindBuilder, ...]:
        return tuple(self._builders)

    def build_all_rows(
        self,
        conn: sa.Connection,
        *,
        tenant_id: str,
        project_id: str | None = None,
        project_summaries: dict[str, ProjectAssessmentSummary] | None = None,
        event_rows_by_stream: dict[str, list[dict[str, Any]]] | None = None,
        revoked_project_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        for row_builder in self._builders:
            built = row_builder.build_rows(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                project_summaries=project_summaries,
                event_rows_by_stream=event_rows_by_stream,
                revoked_project_ids=revoked_project_ids,
            )
            all_rows.extend(built)
        return all_rows


class AgentInventoryRowBuilder:
    """Extensible RowKindBuilder for P3a-WP5 read-only agent inventory rows (ISS-038).

    contracts/hud-projection.schema.json (v1.1) enforces additionalProperties=false on
    rows[] items and requires all 13 task_run properties on every row, so this builder
    is kept out of _DEFAULT_BUILDER until hud-projection 1.2 adds agent row support.
    """

    kind: str = "agent"

    def build_rows(
        self,
        conn: sa.Connection,
        *,
        tenant_id: str,
        project_id: str | None = None,
        **context: Any,
    ) -> list[dict[str, Any]]:
        """Build tenant-scoped read-only agent inventory rows inside the caller's connection."""
        from eap.agents.service import list_agents

        window_hours = context.get("window_hours")
        inventory = list_agents(conn, tenant_id=tenant_id, window_hours=window_hours)
        return [
            {
                "kind": self.kind,
                "agent": agent,
            }
            for agent in inventory["agents"]
        ]


_DEFAULT_BUILDER = HudSnapshotBuilder()


def get_default_snapshot_builder() -> HudSnapshotBuilder:
    """Return the process-default extensible HudSnapshotBuilder."""
    return _DEFAULT_BUILDER


def _execute_in_repeatable_read(
    conn_or_session: Engine | sa.Connection | Session,
    fn: Callable[[sa.Connection], dict[str, Any]],
) -> dict[str, Any]:
    if isinstance(conn_or_session, Engine):
        with conn_or_session.connect() as conn, conn.begin():
            if conn.dialect.name == "postgresql":
                conn.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            return fn(conn)

    if isinstance(conn_or_session, Session):
        bind = conn_or_session.get_bind()
        # Only issue SET TRANSACTION if this session owns a fresh transaction (not a test outer TX).
        if not conn_or_session.in_transaction() and not (
            isinstance(bind, sa.Connection) and bind.in_transaction()
        ):
            with conn_or_session.begin():
                conn = conn_or_session.connection()
                if conn.dialect.name == "postgresql":
                    conn.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                return fn(conn)
        conn = conn_or_session.connection()
        return fn(conn)

    if not conn_or_session.in_transaction():
        with conn_or_session.begin():
            if conn_or_session.dialect.name == "postgresql":
                conn_or_session.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            return fn(conn_or_session)

    return fn(conn_or_session)


def validate_hud_projection(projection_dict: dict[str, Any]) -> dict[str, Any]:
    """Validate HUD snapshot dict against hud-projection 1.1 Pydantic model and domain rules."""
    try:
        model = HudProjection.model_validate(projection_dict)
        check_hud_projection_rules(model)
    except (ValidationError, ContractViolation) as exc:
        logger.error("HUD snapshot contract validation failed: %s", exc)
        raise HudContractViolationError(str(exc)) from exc
    return model.model_dump(mode="json")


def take_hud_snapshot(
    conn_or_session: Engine | sa.Connection | Session,
    *,
    principal: Principal,
    project_id: str | None = None,
    grant_epoch: int | None = None,
    settings: Settings | None = None,
    builder: HudSnapshotBuilder | None = None,
    now: datetime | None = None,
    on_after_rows_read: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Read HUD rows and stream watermarks inside one REPEATABLE READ snapshot transaction."""
    cfg = settings or get_settings()
    active_builder = builder or get_default_snapshot_builder()
    scope_ref = viewer_scope_ref_for_principal(principal, project_id=project_id)
    snapshot_dt = now if now is not None else datetime.now(UTC)

    def _run_tx(conn: sa.Connection) -> dict[str, Any]:
        effective_grant_epoch = (
            grant_epoch
            if grant_epoch is not None
            else get_grant_epoch(
                conn,
                tenant_id=principal.tenant_id,
                principal_id=principal.principal_id,
            )
        )
        revoked_project_ids = get_revoked_project_ids(
            conn,
            tenant_id=principal.tenant_id,
            principal_id=principal.principal_id,
        )
        if project_id is not None and project_id in revoked_project_ids:
            raise PermissionError(
                f"Principal {principal.principal_id!r} is not authorized to view project {project_id!r}"
            )

        # Keep unfiltered project_summaries so single_project_id fallback never leaks onto remaining projects.
        project_summaries = load_project_assessment_summaries(
            conn,
            tenant_id=principal.tenant_id,
            project_id=project_id,
        )
        rows = active_builder.build_all_rows(
            conn,
            tenant_id=principal.tenant_id,
            project_id=project_id,
            project_summaries=project_summaries,
            event_rows_by_stream=None,
            revoked_project_ids=revoked_project_ids,
        )

        # Synchronization point used in concurrency tests to prove REPEATABLE READ isolation.
        if on_after_rows_read is not None:
            on_after_rows_read()

        stream_offsets, last_event_id = _load_stream_offsets_and_max_id(
            conn,
            tenant_id=principal.tenant_id,
        )
        total_offset = sum(stream_offsets.values())
        # hud-projection 1.1 requires projection_revision >= 1 even at offset 0.
        projection_revision = max(1, total_offset)

        cursor = encode_snapshot_cursor(
            tenant_id=principal.tenant_id,
            scope=scope_ref,
            offset=total_offset,
            stream_offsets=stream_offsets,
            grant_epoch=effective_grant_epoch,
            last_event_id=last_event_id,
            settings=cfg,
            now=snapshot_dt,
        )

        payload = {
            "contract_version": "1.1",
            "projection_revision": projection_revision,
            "snapshot_cursor": cursor,
            "as_of": _to_iso_utc(snapshot_dt),
            "viewer_scope_ref": scope_ref,
            "rows": rows,
        }
        return validate_hud_projection(payload)

    return _execute_in_repeatable_read(conn_or_session, _run_tx)


def resolve_event_project_id(
    conn: sa.Connection,
    *,
    tenant_id: str,
    event: dict[str, Any],
    cache: dict[str, str | None] | None = None,
    single_project_fallback: str | None = None,
) -> str | None:
    """Resolve the authoritative project_id for a committed event."""
    payload = event.get("payload")
    extracted = _extract_project_id_from_mapping(payload)
    if extracted:
        return extracted

    stream_id = str(event.get("stream_id") or "")
    r_key: str | None = None
    if stream_id.startswith("run:"):
        r_key = stream_id.split("run:", 1)[1]
    elif isinstance(payload, dict) and payload.get("run_id"):
        r_key = str(payload["run_id"])
    t_key: str | None = str(payload["task_id"]) if isinstance(payload, dict) and payload.get("task_id") else None

    if cache is not None:
        if r_key and f"run:{r_key}" in cache:
            cached = cache[f"run:{r_key}"]
            return cached if cached is not None else single_project_fallback
        if t_key and f"task:{t_key}" in cache:
            cached = cache[f"task:{t_key}"]
            return cached if cached is not None else single_project_fallback

    resolved: str | None = None
    if r_key:
        r_row = conn.execute(
            sa.select(runs.c.project_id, runs.c.task_id).where(
                runs.c.tenant_id == tenant_id,
                runs.c.run_id == r_key,
            )
        ).fetchone()
        if r_row is not None:
            if r_row[0]:
                resolved = str(r_row[0])
            elif not t_key and r_row[1]:
                t_key = str(r_row[1])

    if resolved is None and t_key:
        t_row = conn.execute(
            sa.select(tasks.c.project_id).where(
                tasks.c.tenant_id == tenant_id,
                tasks.c.id == t_key,
            )
        ).fetchone()
        if t_row is not None and t_row[0]:
            resolved = str(t_row[0])

    if resolved is None and (r_key or t_key):
        conds = []
        if r_key:
            conds.append(commands.c.run_id == r_key)
        if t_key:
            conds.append(commands.c.task_id == t_key)
        cmd_row = conn.execute(
            sa.select(commands.c.payload)
            .where(commands.c.tenant_id == tenant_id, sa.or_(*conds))
            .order_by(commands.c.created_at.desc(), commands.c.id.desc())
            .limit(1)
        ).fetchone()
        if cmd_row is not None:
            resolved = _extract_project_id_from_mapping(cmd_row[0])

    if cache is not None:
        if r_key:
            cache[f"run:{r_key}"] = resolved
        if t_key:
            cache[f"task:{t_key}"] = resolved
    return resolved if resolved is not None else single_project_fallback


def _check_stream_retention_gaps(
    conn: sa.Connection,
    *,
    decoded: CursorPayload,
    retention_floors: dict[str, int],
    stream_head_offsets: dict[str, int],
    fetched_by_stream: dict[str, list[tuple[Any, ...]]],
    changed_streams: list[tuple[str, int, int]],
) -> None:
    for s_id, min_retained in retention_floors.items():
        if s_id.startswith("policy:") or min_retained <= 1:
            continue
        head_off = stream_head_offsets.get(s_id, 0)
        if not decoded.stream_offsets_omitted:
            baseline = decoded.stream_offsets.get(s_id, 0)
            if baseline < min_retained - 1 and (head_off >= min_retained or s_id in decoded.stream_offsets):
                raise CursorRetentionGapError(
                    f"Retention floor {min_retained} for stream {s_id!r} exceeds cursor offset {baseline}"
                )
        else:
            oldest_row = conn.execute(
                sa.select(event_log.c.id)
                .where(
                    event_log.c.tenant_id == decoded.tenant_id,
                    event_log.c.stream_id == s_id,
                    event_log.c.ingest_offset >= min_retained,
                )
                .order_by(event_log.c.ingest_offset.asc())
                .limit(1)
            ).fetchone()
            if oldest_row is not None and int(oldest_row[0]) > decoded.last_event_id:
                raise CursorRetentionGapError(
                    f"Retention floor {min_retained} for stream {s_id!r} purged unseen events"
                )
            if oldest_row is None and head_off >= min_retained:
                raise CursorRetentionGapError(
                    f"Events past offset {min_retained} for stream {s_id!r} were compacted"
                )

    if not decoded.stream_offsets_omitted:
        for s_id, head_off, baseline in changed_streams:
            stream_rows = fetched_by_stream.get(s_id, [])
            if head_off > baseline and not stream_rows:
                raise CursorRetentionGapError(
                    f"Events after offset {baseline} for stream {s_id!r} are no longer retained"
                )
            sorted_offsets = sorted(int(r[2]) for r in stream_rows)
            expected = baseline + 1
            for off in sorted_offsets:
                if off != expected:
                    raise CursorRetentionGapError(
                        f"Missing offset {expected} for stream {s_id!r} (next retained is {off})"
                    )
                expected += 1
        for s_id, stream_rows in fetched_by_stream.items():
            if s_id.startswith("policy:"):
                continue
            baseline = decoded.stream_offsets.get(s_id, 0)
            sorted_offsets = sorted(int(r[2]) for r in stream_rows)
            expected = baseline + 1
            for off in sorted_offsets:
                if off != expected:
                    raise CursorRetentionGapError(
                        f"Missing offset {expected} for stream {s_id!r} (next retained is {off})"
                    )
                expected += 1


def _replay_events_with_watermarks(
    conn_or_session: Engine | sa.Connection | Session,
    *,
    cursor: str,
    principal: Principal,
    project_id: str | None = None,
    expected_grant_epoch: int | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[CursorPayload, list[dict[str, Any]], dict[str, int], int, int, set[str]]:
    expected_scope = viewer_scope_ref_for_principal(principal, project_id=project_id)
    decoded = verify_snapshot_cursor(
        cursor,
        expected_tenant_id=principal.tenant_id,
        expected_scope=expected_scope,
        expected_grant_epoch=expected_grant_epoch,
        settings=settings,
        now=now,
    )

    def _query(
        conn: sa.Connection,
    ) -> tuple[list[dict[str, Any]], dict[str, int], int, int, set[str]]:
        server_grant_epoch = get_grant_epoch(
            conn,
            tenant_id=decoded.tenant_id,
            principal_id=principal.principal_id,
        )
        if expected_grant_epoch is None and decoded.grant_epoch != server_grant_epoch:
            raise CursorScopeMismatchError(
                f"Cursor grant_epoch={decoded.grant_epoch} superseded by server epoch={server_grant_epoch}",
                reason="grant_epoch_changed",
            )

        revoked_project_ids = get_revoked_project_ids(
            conn,
            tenant_id=decoded.tenant_id,
            principal_id=principal.principal_id,
        )
        if project_id is not None and project_id in revoked_project_ids:
            raise CursorScopeMismatchError(
                f"Principal {principal.principal_id!r} no longer has access to project {project_id!r}",
                reason="permission_revoked",
            )

        retention_rows = conn.execute(
            sa.select(
                stream_retention_floors.c.stream_id,
                stream_retention_floors.c.min_retained_offset,
            ).where(stream_retention_floors.c.tenant_id == decoded.tenant_id)
        ).fetchall()
        retention_floors = {str(s_id): int(min_off) for s_id, min_off in retention_rows}

        head_rows = conn.execute(
            sa.select(stream_heads.c.stream_id, stream_heads.c.last_offset).where(
                stream_heads.c.tenant_id == decoded.tenant_id
            )
        ).fetchall()
        stream_head_offsets = {str(s_id): int(last_off) for s_id, last_off in head_rows}

        max_id_row = conn.execute(
            sa.select(event_log.c.id)
            .where(event_log.c.tenant_id == decoded.tenant_id)
            .order_by(event_log.c.id.desc())
            .limit(1)
        ).fetchone()
        max_event_id = int(max_id_row[0]) if max_id_row is not None else 0

        select_cols = (
            event_log.c.id,
            event_log.c.stream_id,
            event_log.c.ingest_offset,
            event_log.c.event_id,
            event_log.c.producer_id,
            event_log.c.event_type,
            event_log.c.payload,
            event_log.c.occurred_at,
            event_log.c.ingested_at,
        )

        fetched_rows: list[tuple[Any, ...]] = []
        changed_streams: list[tuple[str, int, int]] = []
        if max_event_id > 0:
            if decoded.stream_offsets_omitted:
                fetched_rows = list(
                    conn.execute(
                        sa.select(*select_cols)
                        .where(
                            event_log.c.tenant_id == decoded.tenant_id,
                            event_log.c.id > decoded.last_event_id,
                        )
                        .order_by(event_log.c.id.asc())
                    ).fetchall()
                )
            elif not stream_head_offsets:
                stmt = sa.select(*select_cols).where(event_log.c.tenant_id == decoded.tenant_id)
                if decoded.last_event_id > 0:
                    stmt = stmt.where(event_log.c.id > decoded.last_event_id)
                fetched_rows = [
                    r
                    for r in conn.execute(stmt.order_by(event_log.c.id.asc())).fetchall()
                    if int(r[2]) > decoded.stream_offsets.get(str(r[1]), 0)
                ]
            else:
                changed_streams = [
                    (s_id, head_off, decoded.stream_offsets.get(s_id, 0))
                    for s_id, head_off in stream_head_offsets.items()
                    if not s_id.startswith("policy:") and head_off > decoded.stream_offsets.get(s_id, 0)
                ]
                if changed_streams:
                    chunk_size = 200
                    for idx in range(0, len(changed_streams), chunk_size):
                        chunk = changed_streams[idx : idx + chunk_size]
                        predicates = [
                            sa.and_(event_log.c.stream_id == s_id, event_log.c.ingest_offset > baseline)
                            for s_id, _head_off, baseline in chunk
                        ]
                        fetched_rows.extend(
                            conn.execute(
                                sa.select(*select_cols)
                                .where(event_log.c.tenant_id == decoded.tenant_id, sa.or_(*predicates))
                                .order_by(event_log.c.id.asc())
                            ).fetchall()
                        )
                    fetched_rows.sort(key=lambda r: int(r[0]))
                elif max_event_id > decoded.last_event_id:
                    fetched_rows = [
                        r
                        for r in conn.execute(
                            sa.select(*select_cols)
                            .where(
                                event_log.c.tenant_id == decoded.tenant_id,
                                event_log.c.id > decoded.last_event_id,
                                ~event_log.c.stream_id.startswith("policy:"),
                            )
                            .order_by(event_log.c.id.asc())
                        ).fetchall()
                        if int(r[2]) > decoded.stream_offsets.get(str(r[1]), 0)
                    ]

        fetched_by_stream: dict[str, list[tuple[Any, ...]]] = {}
        for row in fetched_rows:
            fetched_by_stream.setdefault(str(row[1]), []).append(row)

        _check_stream_retention_gaps(
            conn,
            decoded=decoded,
            retention_floors=retention_floors,
            stream_head_offsets=stream_head_offsets,
            fetched_by_stream=fetched_by_stream,
            changed_streams=changed_streams,
        )

        next_stream_offsets = (
            {s_id: off for s_id, off in stream_head_offsets.items() if not s_id.startswith("policy:")}
            if decoded.stream_offsets_omitted
            else dict(decoded.stream_offsets)
        )
        next_last_event_id = decoded.last_event_id
        single_project_fallback: str | None = None
        if fetched_rows and (revoked_project_ids or project_id is not None):
            summaries = load_project_assessment_summaries(conn, tenant_id=decoded.tenant_id)
            if len(summaries) == 1:
                single_project_fallback = next(iter(summaries))

        proj_cache: dict[str, str | None] = {}
        delta: list[dict[str, Any]] = []
        for log_id, stream_id, ingest_offset, event_id, producer_id, event_type, payload, occurred_at, ingested_at in fetched_rows:
            s_key = str(stream_id)
            ev_type = str(event_type)
            if s_key.startswith("policy:") or ev_type == "policy.changed":
                continue

            ev_dict = {
                "id": int(log_id),
                "tenant_id": decoded.tenant_id,
                "stream_id": s_key,
                "ingest_offset": int(ingest_offset),
                "event_id": str(event_id),
                "producer_id": str(producer_id),
                "event_type": ev_type,
                "payload": payload if isinstance(payload, dict) else {},
                "occurred_at": _to_iso_utc(occurred_at),
                "ingested_at": _to_iso_utc(ingested_at),
            }
            next_stream_offsets[s_key] = max(next_stream_offsets.get(s_key, 0), int(ingest_offset))
            next_last_event_id = max(next_last_event_id, int(log_id))

            if project_id is not None or revoked_project_ids:
                ev_proj = resolve_event_project_id(
                    conn,
                    tenant_id=decoded.tenant_id,
                    event=ev_dict,
                    cache=proj_cache,
                    single_project_fallback=single_project_fallback,
                )
                if project_id is not None and ev_proj != project_id:
                    continue
                if revoked_project_ids and ev_proj in revoked_project_ids:
                    continue

            delta.append(ev_dict)

        return (
            delta,
            next_stream_offsets,
            next_last_event_id,
            server_grant_epoch,
            revoked_project_ids,
        )

    if isinstance(conn_or_session, Engine):
        with conn_or_session.connect() as conn:
            events, next_offsets, next_last_id, srv_epoch, revoked = _query(conn)
    elif isinstance(conn_or_session, Session):
        events, next_offsets, next_last_id, srv_epoch, revoked = _query(conn_or_session.connection())
    else:
        events, next_offsets, next_last_id, srv_epoch, revoked = _query(conn_or_session)

    return decoded, events, next_offsets, next_last_id, srv_epoch, revoked


def replay_events_after_cursor(
    conn_or_session: Engine | sa.Connection | Session,
    *,
    cursor: str,
    principal: Principal,
    project_id: str | None = None,
    expected_grant_epoch: int | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[CursorPayload, list[dict[str, Any]]]:
    """Replay committed events strictly after the decoded snapshot cursor without gaps or duplicates."""
    decoded, events, _next_offsets, _next_last_id, _srv_epoch, _revoked = _replay_events_with_watermarks(
        conn_or_session,
        cursor=cursor,
        principal=principal,
        project_id=project_id,
        expected_grant_epoch=expected_grant_epoch,
        settings=settings,
        now=now,
    )
    return decoded, events


def apply_replayed_events_to_snapshot(
    snapshot: dict[str, Any],
    replayed_events: list[dict[str, Any]],
    *,
    cursor_payload: CursorPayload | None = None,
) -> dict[str, Any]:
    """Deterministically apply post-cursor events onto a HUD snapshot and re-validate contract 1.1."""
    updated = deepcopy(snapshot)
    rows_by_run: dict[str, dict[str, Any]] = {}
    rows_without_run: list[dict[str, Any]] = []

    for row in updated.get("rows", []):
        r_id = row.get("run_id")
        if r_id:
            rows_by_run[str(r_id)] = row
        else:
            rows_without_run.append(row)

    ordered_events = sorted(
        replayed_events,
        key=lambda ev: (str(ev.get("stream_id", "")), int(ev.get("ingest_offset", 0))),
    )
    for ev in ordered_events:
        stream_id = str(ev.get("stream_id", ""))
        r_key: str | None = None
        if stream_id.startswith("run:"):
            r_key = stream_id.split("run:", 1)[1]
        elif ev.get("run_id"):
            r_key = str(ev["run_id"])
        elif isinstance(ev.get("payload"), dict) and ev["payload"].get("run_id"):
            r_key = str(ev["payload"]["run_id"])

        existing = rows_by_run.get(r_key) if r_key else None
        if existing is not None:
            prior_project = existing.get("project")
            folded = reduce_event({"row": existing}, ev)["row"]
            if folded.get("project") is None and prior_project is not None:
                folded["project"] = prior_project
            if r_key:
                rows_by_run[r_key] = folded
        else:
            folded = reduce_event(None, ev)["row"]
            target_key = r_key or str(folded.get("run_id") or "")
            if target_key:
                rows_by_run[target_key] = folded
            else:
                rows_without_run.append(folded)

    merged_rows = sorted(
        [*rows_without_run, *rows_by_run.values()],
        key=lambda r: (str(r["task_id"]), str(r.get("run_id") or "")),
    )
    updated["rows"] = merged_rows
    if cursor_payload is not None:
        updated["projection_revision"] = max(1, int(cursor_payload.offset) + len(replayed_events))
    elif replayed_events:
        updated["projection_revision"] = max(
            int(updated.get("projection_revision", 1)),
            int(updated.get("projection_revision", 1)) + len(replayed_events),
        )
    return validate_hud_projection(updated)
