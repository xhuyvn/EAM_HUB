"""HUD API routes: snapshots, SSE replay/revocation, grants, and authorized artifact/evidence access."""

from __future__ import annotations

import logging
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from eap.api.dependencies import (
    Principal,
    check_forbidden_headers_and_query,
    get_db,
    get_principal,
)
from eap.config import Settings, get_settings
from eap.db.assessment_tables import ppm_evidence
from eap.db.tables import artifacts, commands, event_log, runs, tasks, usage_ledger
from eap.hud.snapshot import (
    CursorError,
    HudContractViolationError,
    HudSnapshotBuilder,
    _extract_project_id_from_mapping,
    _to_iso_utc,
    get_default_snapshot_builder,
    load_project_assessment_summaries,
    take_hud_snapshot,
    verify_snapshot_cursor,
    viewer_scope_ref_for_principal,
)
from eap.hud.sse import (
    DEFAULT_SSE_HEARTBEAT_SEC,
    DEFAULT_SSE_MAX_BUFFER_SIZE,
    DEFAULT_SSE_POLL_INTERVAL_MS,
    iter_hud_sse_frames,
)
from eap.observability import redact_text
from eap.policy.grants import (
    get_grant_epoch,
    grant_project_access,
    is_project_authorized,
    revoke_project_access,
)

logger = logging.getLogger("eap.api.hud_routes")

hud_router = APIRouter(
    prefix="/v1/hud",
    tags=["hud"],
    dependencies=[Depends(check_forbidden_headers_and_query)],
)

artifact_router = APIRouter(
    prefix="/v1",
    tags=["artifacts"],
    dependencies=[Depends(check_forbidden_headers_and_query)],
)


class ProjectGrantMutationRequest(BaseModel):
    """Payload to revoke or restore a principal's access to a project."""

    principal_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=64)
    reason: str | None = Field(default=None, max_length=128)


def get_hud_builder() -> HudSnapshotBuilder:
    """FastAPI dependency returning the HUD snapshot builder (overridable in tests)."""
    return get_default_snapshot_builder()


def _fetch_evidence_row(
    conn: sa.Connection,
    *,
    tenant_id: str,
    evidence_id: str,
) -> Any | None:
    return (
        conn.execute(
            sa.select(
                ppm_evidence.c.evidence_id,
                ppm_evidence.c.project_id,
                ppm_evidence.c.trust_class,
                ppm_evidence.c.producer,
                ppm_evidence.c.revision,
                ppm_evidence.c.test_id,
                ppm_evidence.c.outcome,
                ppm_evidence.c.output_hash,
                ppm_evidence.c.artifact_path,
                ppm_evidence.c.collected_at,
            ).where(
                ppm_evidence.c.tenant_id == tenant_id,
                ppm_evidence.c.evidence_id == evidence_id,
            )
        )
        .mappings()
        .fetchone()
    )


def _maybe_commit_session(db: Session) -> None:
    bind = db.get_bind()
    if isinstance(bind, Engine):
        db.commit()
    else:
        db.flush()


def _resolve_artifact_project_id(
    conn: sa.Connection,
    *,
    tenant_id: str,
    artifact_id: str,
    task_id: str | None,
    run_id: str | None,
    storage_uri: str | None,
) -> str | None:
    if run_id:
        r_row = conn.execute(
            sa.select(runs.c.project_id, runs.c.task_id).where(
                runs.c.tenant_id == tenant_id,
                runs.c.run_id == run_id,
            )
        ).fetchone()
        if r_row is not None:
            if r_row[0]:
                return str(r_row[0])
            if not task_id and r_row[1]:
                task_id = str(r_row[1])

    if task_id:
        t_row = conn.execute(
            sa.select(tasks.c.project_id).where(
                tasks.c.tenant_id == tenant_id,
                tasks.c.id == task_id,
            )
        ).fetchone()
        if t_row is not None and t_row[0]:
            return str(t_row[0])

    if run_id or task_id:
        conds = []
        if run_id:
            conds.append(commands.c.run_id == run_id)
        if task_id:
            conds.append(commands.c.task_id == task_id)
        cmd_row = conn.execute(
            sa.select(commands.c.payload)
            .where(commands.c.tenant_id == tenant_id, sa.or_(*conds))
            .order_by(commands.c.created_at.desc(), commands.c.id.desc())
            .limit(1)
        ).fetchone()
        if cmd_row is not None:
            extracted = _extract_project_id_from_mapping(cmd_row[0])
            if extracted:
                return extracted

    ev_conds = [ppm_evidence.c.evidence_id == artifact_id]
    if storage_uri:
        ev_conds.append(ppm_evidence.c.artifact_path == storage_uri)
    ev_row = conn.execute(
        sa.select(ppm_evidence.c.project_id)
        .where(ppm_evidence.c.tenant_id == tenant_id, sa.or_(*ev_conds))
        .limit(1)
    ).fetchone()
    if ev_row is not None and ev_row[0]:
        return str(ev_row[0])

    summaries = load_project_assessment_summaries(conn, tenant_id=tenant_id)
    if len(summaries) == 1:
        return next(iter(summaries))
    return None


@hud_router.get("/snapshot")
def get_hud_snapshot(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    builder: Annotated[HudSnapshotBuilder, Depends(get_hud_builder)],
    project_id: Annotated[str | None, Query(min_length=1)] = None,
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> Any:
    """Return atomic REPEATABLE READ HUD projection (hud-projection 1.1) for caller scope."""
    expected_scope = viewer_scope_ref_for_principal(principal, project_id=project_id)
    current_epoch = get_grant_epoch(
        db.connection(),
        tenant_id=principal.tenant_id,
        principal_id=principal.principal_id,
    )

    if cursor is not None:
        try:
            verify_snapshot_cursor(
                cursor,
                expected_tenant_id=principal.tenant_id,
                expected_scope=expected_scope,
                expected_grant_epoch=current_epoch,
                settings=settings,
            )
        except CursorError as exc:
            logger.warning("HUD cursor rejected (status=%s code=%s)", exc.status_code, exc.code)
            return JSONResponse(status_code=exc.status_code, content=exc.to_response_dict())

    try:
        snapshot = take_hud_snapshot(
            db,
            principal=principal,
            project_id=project_id,
            grant_epoch=current_epoch,
            settings=settings,
            builder=builder,
        )
    except PermissionError:
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Access denied for requested project",
                "code": "PERMISSION_REVOKED",
            },
        )
    except HudContractViolationError as exc:
        logger.error("HUD snapshot contract validation blocked response: %s", exc.reason)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "HUD projection contract validation failed",
                "code": "HUD_CONTRACT_VIOLATION",
            },
        )

    return JSONResponse(status_code=200, content=snapshot)


@hud_router.get("/events")
async def stream_hud_events(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    cursor: Annotated[str | None, Query(min_length=1)] = None,
    after: Annotated[str | None, Query(min_length=1)] = None,
    project_id: Annotated[str | None, Query(min_length=1)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    poll_interval_ms: Annotated[int, Query(ge=5, le=5000)] = DEFAULT_SSE_POLL_INTERVAL_MS,
    heartbeat_sec: Annotated[float, Query(ge=0.02, le=60.0)] = DEFAULT_SSE_HEARTBEAT_SEC,
    max_buffer_size: Annotated[int, Query(ge=1, le=4096)] = DEFAULT_SSE_MAX_BUFFER_SIZE,
    max_events: Annotated[int | None, Query(ge=1)] = None,
    idle_timeout_ms: Annotated[int | None, Query(ge=10, le=300000)] = None,
) -> StreamingResponse:
    """Stream SSE deltas strictly after cursor/Last-Event-ID with live revocation and bounded buffers."""
    header_cursor = last_event_id.strip() if last_event_id and last_event_id.strip() else None
    effective_cursor = header_cursor or cursor or after

    stream = iter_hud_sse_frames(
        db,
        cursor=effective_cursor,
        principal=principal,
        project_id=project_id,
        settings=settings,
        poll_interval_ms=poll_interval_ms,
        heartbeat_sec=heartbeat_sec,
        max_buffer_size=max_buffer_size,
        max_events=max_events,
        idle_timeout_ms=idle_timeout_ms,
        is_disconnected=request.is_disconnected,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@hud_router.post("/grants/revoke")
def revoke_hud_project_grant(
    body: ProjectGrantMutationRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """Revoke a principal's project access and emit policy.changed (bumping grant_epoch)."""
    if not ("owner" in principal.roles or "admin" in principal.roles):
        return JSONResponse(
            status_code=403,
            content={"detail": "Requires owner or admin role", "code": "FORBIDDEN"},
        )

    result = revoke_project_access(
        db.connection(),
        tenant_id=principal.tenant_id,
        principal_id=body.principal_id,
        project_id=body.project_id,
        actor_id=principal.principal_id,
        reason=body.reason or "permission_revoked",
        relay_immediately=True,
    )
    _maybe_commit_session(db)
    return JSONResponse(status_code=200, content=result)


@hud_router.post("/grants/restore")
def restore_hud_project_grant(
    body: ProjectGrantMutationRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """Restore/allow a principal's project access and emit policy.changed."""
    if not ("owner" in principal.roles or "admin" in principal.roles):
        return JSONResponse(
            status_code=403,
            content={"detail": "Requires owner or admin role", "code": "FORBIDDEN"},
        )

    result = grant_project_access(
        db.connection(),
        tenant_id=principal.tenant_id,
        principal_id=body.principal_id,
        project_id=body.project_id,
        actor_id=principal.principal_id,
        reason=body.reason or "grant_restored",
        relay_immediately=True,
    )
    _maybe_commit_session(db)
    return JSONResponse(status_code=200, content=result)


@artifact_router.get("/artifacts/{artifact_id}")
def get_authorized_artifact(
    artifact_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """Serve artifact metadata strictly with per-request authorization (no signed URLs)."""
    conn = db.connection()
    art_row = conn.execute(
        sa.select(
            artifacts.c.artifact_id,
            artifacts.c.task_id,
            artifacts.c.run_id,
            artifacts.c.content_hash,
            artifacts.c.content_type,
            artifacts.c.size_bytes,
            artifacts.c.storage_uri,
            artifacts.c.created_at,
        ).where(
            artifacts.c.tenant_id == principal.tenant_id,
            artifacts.c.artifact_id == artifact_id,
        )
    ).mappings().fetchone()

    if art_row is not None:
        resolved_proj = _resolve_artifact_project_id(
            conn,
            tenant_id=principal.tenant_id,
            artifact_id=artifact_id,
            task_id=art_row["task_id"],
            run_id=art_row["run_id"],
            storage_uri=art_row["storage_uri"],
        )
        if not is_project_authorized(
            conn,
            tenant_id=principal.tenant_id,
            principal_id=principal.principal_id,
            project_id=resolved_proj,
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Permission to project artifact has been revoked",
                    "code": "PERMISSION_REVOKED",
                },
            )
        return JSONResponse(
            status_code=200,
            content={
                "artifact_id": str(art_row["artifact_id"]),
                "tenant_id": principal.tenant_id,
                "project_id": resolved_proj,
                "task_id": art_row["task_id"],
                "run_id": art_row["run_id"],
                "content_hash": art_row["content_hash"],
                "content_type": art_row["content_type"],
                "size_bytes": int(art_row["size_bytes"]),
                "storage_uri": art_row["storage_uri"],
                "access_url": f"/v1/artifacts/{artifact_id}",
                "signed_url": None,
                "created_at": _to_iso_utc(art_row["created_at"]),
            },
        )

    # Support evidence IDs routed through /v1/artifacts/{id} per docs/03_HUD_SPEC.md.
    ev_row = _fetch_evidence_row(
        conn,
        tenant_id=principal.tenant_id,
        evidence_id=artifact_id,
    )
    if ev_row is None:
        return JSONResponse(
            status_code=404,
            content={
                "detail": f"Artifact {artifact_id!r} not found",
                "code": "ARTIFACT_NOT_FOUND",
            },
        )

    if not is_project_authorized(
        conn,
        tenant_id=principal.tenant_id,
        principal_id=principal.principal_id,
        project_id=ev_row["project_id"],
    ):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Permission to project artifact has been revoked",
                "code": "PERMISSION_REVOKED",
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "artifact_id": str(ev_row["evidence_id"]),
            "tenant_id": principal.tenant_id,
            "project_id": str(ev_row["project_id"]),
            "trust_class": str(ev_row["trust_class"]),
            "producer": str(ev_row["producer"]),
            "revision": str(ev_row["revision"]),
            "test_id": ev_row["test_id"],
            "outcome": str(ev_row["outcome"]),
            "output_hash": str(ev_row["output_hash"]),
            "artifact_path": ev_row["artifact_path"],
            "access_url": f"/v1/artifacts/{artifact_id}",
            "signed_url": None,
            "collected_at": _to_iso_utc(ev_row["collected_at"]),
        },
    )


@artifact_router.get("/evidence/{evidence_id}")
def get_authorized_evidence(
    evidence_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """Serve PPM evidence strictly with per-request authorization (no signed URLs -> 403 after revocation)."""
    conn = db.connection()
    ev_row = _fetch_evidence_row(
        conn,
        tenant_id=principal.tenant_id,
        evidence_id=evidence_id,
    )
    if ev_row is None:
        return JSONResponse(
            status_code=404,
            content={
                "detail": f"Evidence {evidence_id!r} not found",
                "code": "EVIDENCE_NOT_FOUND",
            },
        )

    if not is_project_authorized(
        conn,
        tenant_id=principal.tenant_id,
        principal_id=principal.principal_id,
        project_id=ev_row["project_id"],
    ):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Permission to project evidence has been revoked",
                "code": "PERMISSION_REVOKED",
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "evidence_id": str(ev_row["evidence_id"]),
            "tenant_id": principal.tenant_id,
            "project_id": str(ev_row["project_id"]),
            "trust_class": str(ev_row["trust_class"]),
            "producer": str(ev_row["producer"]),
            "revision": str(ev_row["revision"]),
            "test_id": ev_row["test_id"],
            "outcome": str(ev_row["outcome"]),
            "output_hash": str(ev_row["output_hash"]),
            "artifact_path": ev_row["artifact_path"],
            "access_url": f"/v1/evidence/{evidence_id}",
            "artifact_url": f"/v1/artifacts/{evidence_id}",
            "signed_url": None,
            "collected_at": _to_iso_utc(ev_row["collected_at"]),
        },
    )


def _redact_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _redact_json_value(v) for k, v in value.items()}
    return value


@hud_router.get("/runs/{run_id}/inspector")
def get_run_inspector(
    run_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """Return read-only Run Inspector timeline (attempts, commands, events, artifacts, usage)."""
    conn = db.connection()
    run_row = conn.execute(
        sa.select(
            runs.c.id,
            runs.c.run_id,
            runs.c.task_id,
            runs.c.project_id,
            runs.c.attempt,
            runs.c.binding_id,
            runs.c.state,
            runs.c.remote_outcome,
            runs.c.lease_fencing_token,
            runs.c.created_at,
            runs.c.updated_at,
        ).where(
            runs.c.tenant_id == principal.tenant_id,
            runs.c.run_id == run_id,
        )
    ).mappings().fetchone()

    if run_row is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Run {run_id!r} not found", "code": "RUN_NOT_FOUND"},
        )

    task_id = str(run_row["task_id"])
    task_row = conn.execute(
        sa.select(
            tasks.c.id,
            tasks.c.task_type,
            tasks.c.state,
            tasks.c.agent_id,
            tasks.c.agent_version,
            tasks.c.project_id,
            tasks.c.created_at,
            tasks.c.updated_at,
            tasks.c.deadline,
        ).where(
            tasks.c.tenant_id == principal.tenant_id,
            tasks.c.id == task_id,
        )
    ).mappings().fetchone()

    resolved_proj = run_row["project_id"] or (task_row["project_id"] if task_row else None)
    if resolved_proj and not is_project_authorized(
        conn,
        tenant_id=principal.tenant_id,
        principal_id=principal.principal_id,
        project_id=str(resolved_proj),
    ):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Permission to project run has been revoked",
                "code": "PERMISSION_REVOKED",
            },
        )

    attempt_rows = conn.execute(
        sa.select(
            runs.c.run_id,
            runs.c.attempt,
            runs.c.binding_id,
            runs.c.state,
            runs.c.remote_outcome,
            runs.c.created_at,
            runs.c.updated_at,
        )
        .where(runs.c.tenant_id == principal.tenant_id, runs.c.task_id == task_id)
        .order_by(runs.c.attempt.asc(), runs.c.created_at.asc())
    ).mappings().fetchall()

    cmd_rows = conn.execute(
        sa.select(
            commands.c.id,
            commands.c.run_id,
            commands.c.command_type,
            commands.c.status,
            commands.c.payload,
            commands.c.created_at,
            commands.c.updated_at,
        )
        .where(
            commands.c.tenant_id == principal.tenant_id,
            commands.c.task_id == task_id,
            sa.or_(commands.c.run_id == run_id, commands.c.run_id.is_(None)),
        )
        .order_by(commands.c.created_at.asc(), commands.c.id.asc())
    ).mappings().fetchall()

    ev_rows = conn.execute(
        sa.select(
            event_log.c.event_id,
            event_log.c.stream_id,
            event_log.c.ingest_offset,
            event_log.c.producer_id,
            event_log.c.event_type,
            event_log.c.payload,
            event_log.c.occurred_at,
            event_log.c.ingested_at,
        )
        .where(
            event_log.c.tenant_id == principal.tenant_id,
            event_log.c.stream_id.in_([f"run:{run_id}", f"task:{task_id}"]),
        )
        .order_by(event_log.c.id.asc())
        .limit(200)
    ).mappings().fetchall()

    art_rows = conn.execute(
        sa.select(
            artifacts.c.artifact_id,
            artifacts.c.content_hash,
            artifacts.c.content_type,
            artifacts.c.size_bytes,
            artifacts.c.created_at,
        )
        .where(artifacts.c.tenant_id == principal.tenant_id, artifacts.c.run_id == run_id)
        .order_by(artifacts.c.created_at.asc())
    ).mappings().fetchall()

    usage_rows = conn.execute(
        sa.select(
            usage_ledger.c.provider_usage_id,
            usage_ledger.c.model_profile,
            usage_ledger.c.prompt_tokens,
            usage_ledger.c.completion_tokens,
            usage_ledger.c.cost_estimate,
            usage_ledger.c.reported_at,
        )
        .where(usage_ledger.c.tenant_id == principal.tenant_id, usage_ledger.c.run_id == run_id)
        .order_by(usage_ledger.c.reported_at.asc())
    ).mappings().fetchall()

    usage_list: list[dict[str, Any]] = [
        {
            "provider_usage_id": str(u["provider_usage_id"]),
            "model_profile": str(u["model_profile"]),
            "prompt_tokens": int(u["prompt_tokens"]),
            "completion_tokens": int(u["completion_tokens"]),
            "cost_usd": float(u["cost_estimate"]),
            "reported_at": _to_iso_utc(u["reported_at"]),
        }
        for u in usage_rows
    ]
    total_cost = round(sum(float(u["cost_usd"]) for u in usage_list), 6) if usage_list else None

    return JSONResponse(
        status_code=200,
        content={
            "run": {
                "run_id": str(run_row["run_id"]),
                "task_id": task_id,
                "project_id": resolved_proj,
                "attempt": int(run_row["attempt"]),
                "binding_id": str(run_row["binding_id"]),
                "state": str(run_row["state"]),
                "remote_outcome": str(run_row["remote_outcome"]),
                "created_at": _to_iso_utc(run_row["created_at"]),
                "updated_at": _to_iso_utc(run_row["updated_at"]),
            },
            "task": (
                {
                    "task_id": str(task_row["id"]),
                    "task_type": str(task_row["task_type"]),
                    "state": str(task_row["state"]),
                    "agent_id": str(task_row["agent_id"]),
                    "agent_version": str(task_row["agent_version"]),
                    "project_id": task_row["project_id"],
                    "created_at": _to_iso_utc(task_row["created_at"]),
                    "updated_at": _to_iso_utc(task_row["updated_at"]),
                    "deadline": _to_iso_utc(task_row["deadline"]),
                }
                if task_row is not None
                else None
            ),
            "attempts": [
                {
                    "run_id": str(a["run_id"]),
                    "attempt": int(a["attempt"]),
                    "binding_id": str(a["binding_id"]),
                    "state": str(a["state"]),
                    "remote_outcome": str(a["remote_outcome"]),
                    "created_at": _to_iso_utc(a["created_at"]),
                    "updated_at": _to_iso_utc(a["updated_at"]),
                }
                for a in attempt_rows
            ],
            "commands": [
                {
                    "command_id": str(c["id"]),
                    "run_id": c["run_id"],
                    "command_type": str(c["command_type"]),
                    "status": str(c["status"]),
                    "payload": _redact_json_value(c["payload"]),
                    "created_at": _to_iso_utc(c["created_at"]),
                    "updated_at": _to_iso_utc(c["updated_at"]),
                }
                for c in cmd_rows
            ],
            "events": [
                {
                    "event_id": str(e["event_id"]),
                    "stream_id": str(e["stream_id"]),
                    "ingest_offset": int(e["ingest_offset"]),
                    "producer_id": str(e["producer_id"]),
                    "event_type": str(e["event_type"]),
                    "payload": _redact_json_value(e["payload"]),
                    "occurred_at": _to_iso_utc(e["occurred_at"]),
                    "ingested_at": _to_iso_utc(e["ingested_at"]),
                }
                for e in ev_rows
            ],
            "artifacts": [
                {
                    "artifact_id": str(art["artifact_id"]),
                    "content_hash": str(art["content_hash"]),
                    "content_type": str(art["content_type"]),
                    "size_bytes": int(art["size_bytes"]),
                    "access_url": f"/v1/artifacts/{art['artifact_id']}",
                    "signed_url": None,
                    "created_at": _to_iso_utc(art["created_at"]),
                }
                for art in art_rows
            ],
            "usage": usage_list,
            "cost": {
                "status": "actual" if usage_list else "unknown",
                "amount_usd": total_cost,
                "as_of": usage_list[-1]["reported_at"] if usage_list else None,
                "scope": "run",
            },
        },
    )
