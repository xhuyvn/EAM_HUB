"""Server-Sent Events (SSE) replay and permission-revocation stream for EAP HUD (P3a-WP2).

Design guarantees:
- Reconnect-safe exact-once delivery strictly after a verified ``ehc2_`` cursor (or ``Last-Event-ID``)
  using per-stream relay offsets and commit ordering (C09).
- Expired, tampered, legacy ``ehc1_`` / retired static-key, cross-tenant, scope-mismatched,
  stale-epoch, and retention-gap cursors emit a single ``resync_required`` event and close (C10, C28).
- Active streams monitor server-side ``grant_epoch`` (bumped by ``policy.changed``); upon revocation,
  ``row.removed`` deltas are emitted, buffered/new events for revoked projects are dropped, and
  subsequent delivery respects the active epoch (C30).
- Bounded per-subscriber memory: overflow emits ``resync_required`` (``slow_consumer``) and closes.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import anyio
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from eap.api.dependencies import Principal
from eap.config import Settings, get_settings
from eap.db.tables import commands, runs, tasks
from eap.hud.snapshot import (
    CursorError,
    CursorExpiredError,
    CursorRetentionGapError,
    CursorScopeMismatchError,
    CursorTamperedError,
    _extract_project_id_from_mapping,
    _replay_events_with_watermarks,
    encode_snapshot_cursor,
    load_project_assessment_summaries,
    resolve_event_project_id,
    viewer_scope_ref_for_principal,
)
from eap.policy.grants import get_grant_epoch, get_revoked_project_ids

logger = logging.getLogger(__name__)

DEFAULT_SSE_POLL_INTERVAL_MS = 25
DEFAULT_SSE_HEARTBEAT_SEC = 15.0
DEFAULT_SSE_MAX_BUFFER_SIZE = 256


@dataclass(slots=True)
class SseFrame:
    """Serialized SSE wire frame with optional project attribution for epoch re-filtering."""

    wire_text: str
    event_name: str
    project_id: str | None = None
    is_terminal: bool = False
    counts_as_event: bool = True


@dataclass(slots=True)
class _StreamCursorState:
    tenant_id: str
    scope: str
    stream_offsets: dict[str, int]
    stream_offsets_omitted: bool
    last_event_id: int
    grant_epoch: int
    revoked_project_ids: set[str]
    known_rows: dict[tuple[str, str | None], str | None] = field(default_factory=dict)
    proj_cache: dict[str, str | None] = field(default_factory=dict)

    def current_cursor(self, *, settings: Settings, now: datetime | None = None) -> str:
        return encode_snapshot_cursor(
            tenant_id=self.tenant_id,
            scope=self.scope,
            offset=sum(self.stream_offsets.values()),
            stream_offsets=self.stream_offsets,
            stream_offsets_omitted=self.stream_offsets_omitted,
            grant_epoch=self.grant_epoch,
            last_event_id=self.last_event_id,
            settings=settings,
            now=now,
        )


def format_sse_event(
    *,
    event: str,
    data: dict[str, Any],
    event_id: str | None = None,
    retry_ms: int | None = None,
) -> str:
    """Serialize one SSE frame with compact JSON data."""
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    if retry_ms is not None:
        lines.append(f"retry: {int(retry_ms)}")
    payload_json = json.dumps(data, separators=(",", ":"), sort_keys=True)
    lines.append(f"data: {payload_json}")
    return "\n".join(lines) + "\n\n"


def format_sse_comment(comment: str = "heartbeat") -> str:
    """Serialize an SSE comment line (used for keepalive heartbeats)."""
    sanitized = str(comment).replace("\r", " ").replace("\n", " ")
    return f": {sanitized}\n\n"


def format_resync_required_sse(*, reason: str) -> str:
    """Serialize a terminal ``resync_required`` SSE event (without ``id:`` so Last-Event-ID is not clobbered)."""
    return format_sse_event(
        event="resync_required",
        data={
            "code": "resync_required",
            "reason": reason,
            "resync_required": True,
        },
        event_id=None,
    )


def parse_sse_frames(raw_text: str) -> list[dict[str, Any]]:
    """Parse SSE text output into structured frames (comments and events)."""
    frames: list[dict[str, Any]] = []
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    for raw_block in normalized.split("\n\n"):
        if not raw_block.strip():
            continue
        event_id: str | None = None
        event_name: str | None = None
        data_lines: list[str] = []
        comments: list[str] = []
        for line in raw_block.split("\n"):
            if line.startswith(":"):
                comments.append(line[1:].lstrip(" "))
            elif line.startswith("id:"):
                event_id = line[3:].lstrip(" ")
            elif line.startswith("event:"):
                event_name = line[6:].lstrip(" ")
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
        if comments and event_name is None and not data_lines:
            frames.append({"type": "comment", "comments": comments})
            continue
        raw_data = "\n".join(data_lines)
        parsed_data: Any = raw_data
        if raw_data:
            try:
                parsed_data = json.loads(raw_data)
            except json.JSONDecodeError:
                parsed_data = raw_data
        frames.append(
            {
                "type": "event",
                "id": event_id,
                "event": event_name or "message",
                "data": parsed_data,
                "comments": comments,
            }
        )
    return frames


def _sanitize_cursor_error_reason(exc: CursorError) -> str:
    """Return a non-leaky resync_required reason code for cursor/scope errors."""
    if isinstance(exc, CursorExpiredError):
        return "cursor_expired"
    if isinstance(exc, CursorRetentionGapError):
        return "retention_gap"
    if isinstance(exc, CursorTamperedError):
        return "cursor_tampered"
    if isinstance(exc, CursorScopeMismatchError):
        if exc.reason in ("grant_epoch_changed", "permission_revoked"):
            return exc.reason
        return "cursor_scope_mismatch"
    return "resync_required"


def _load_visible_row_projects(
    conn: sa.Connection,
    *,
    tenant_id: str,
    project_id: str | None,
    revoked_project_ids: set[str],
) -> dict[tuple[str, str | None], str | None]:
    """Load lightweight ``(task_id, run_id) -> project_id`` mapping for active SSE row.removed tracking."""
    summaries = load_project_assessment_summaries(conn, tenant_id=tenant_id)
    single_project_id = next(iter(summaries)) if len(summaries) == 1 else None

    task_rows = conn.execute(
        sa.select(tasks.c.id, tasks.c.project_id).where(tasks.c.tenant_id == tenant_id)
    ).fetchall()
    run_rows = conn.execute(
        sa.select(runs.c.task_id, runs.c.run_id, runs.c.project_id)
        .where(runs.c.tenant_id == tenant_id)
        .order_by(runs.c.task_id.asc(), runs.c.attempt.desc(), runs.c.created_at.desc(), runs.c.id.desc())
    ).fetchall()

    project_by_task: dict[str, str] = {
        str(t_id): str(t_proj) for t_id, t_proj in task_rows if t_proj
    }
    latest_run_by_task: dict[str, str] = {}
    project_by_run: dict[str, str] = {}
    for t_id, r_id, r_proj in run_rows:
        t_key = str(t_id)
        r_key = str(r_id)
        latest_run_by_task.setdefault(t_key, r_key)
        if r_proj:
            project_by_run.setdefault(r_key, str(r_proj))
            project_by_task.setdefault(t_key, str(r_proj))

    cmd_rows = conn.execute(
        sa.select(commands.c.task_id, commands.c.run_id, commands.c.payload)
        .where(commands.c.tenant_id == tenant_id)
        .order_by(commands.c.created_at.desc(), commands.c.id.desc())
    ).fetchall()
    for cmd_t_id, cmd_r_id, cmd_payload in cmd_rows:
        extracted = _extract_project_id_from_mapping(cmd_payload)
        if extracted:
            if cmd_r_id:
                project_by_run.setdefault(str(cmd_r_id), extracted)
            if cmd_t_id:
                project_by_task.setdefault(str(cmd_t_id), extracted)

    known: dict[tuple[str, str | None], str | None] = {}
    for t_id, _t_proj in task_rows:
        t_key = str(t_id)
        latest_r_key: str | None = latest_run_by_task.get(t_key)
        linked = (
            (project_by_run.get(latest_r_key) if latest_r_key else None)
            or project_by_task.get(t_key)
            or single_project_id
        )
        if project_id is not None and linked != project_id:
            continue
        if linked in revoked_project_ids:
            continue
        known[(t_key, latest_r_key)] = linked
    return known


def _bootstrap_stream_state(
    conn: sa.Connection,
    *,
    cursor: str,
    principal: Principal,
    project_id: str | None,
    settings: Settings,
    now: datetime | None,
) -> tuple[_StreamCursorState, list[dict[str, Any]], dict[str, int], int]:
    decoded, initial_events, next_offsets, next_last_id, srv_epoch, revoked = _replay_events_with_watermarks(
        conn,
        cursor=cursor,
        principal=principal,
        project_id=project_id,
        expected_grant_epoch=None,
        settings=settings,
        now=now,
    )
    known_rows = _load_visible_row_projects(
        conn,
        tenant_id=decoded.tenant_id,
        project_id=project_id,
        revoked_project_ids=revoked,
    )
    state = _StreamCursorState(
        tenant_id=decoded.tenant_id,
        scope=decoded.scope,
        stream_offsets=dict(decoded.stream_offsets),
        stream_offsets_omitted=decoded.stream_offsets_omitted,
        last_event_id=decoded.last_event_id,
        grant_epoch=srv_epoch,
        revoked_project_ids=set(revoked),
        known_rows=known_rows,
    )
    return state, initial_events, next_offsets, next_last_id


def _build_frames_from_poll(
    conn: sa.Connection,
    *,
    state: _StreamCursorState,
    principal: Principal,
    project_id: str | None,
    settings: Settings,
    pre_replayed: list[dict[str, Any]] | None = None,
) -> tuple[list[SseFrame], bool]:
    """Check policy changes + replay deltas cleanly against server-side current epoch."""
    frames: list[SseFrame] = []
    # Check active server-side epoch and grants FIRST before delivering any buffered/new event.
    current_epoch = get_grant_epoch(
        conn,
        tenant_id=state.tenant_id,
        principal_id=principal.principal_id,
    )
    current_revoked = get_revoked_project_ids(
        conn,
        tenant_id=state.tenant_id,
        principal_id=principal.principal_id,
    )

    if current_epoch != state.grant_epoch or current_revoked != state.revoked_project_ids:
        state.grant_epoch = current_epoch
        state.revoked_project_ids = set(current_revoked)

        removed_keys = [
            key
            for key, row_proj in sorted(state.known_rows.items())
            if row_proj is not None and row_proj in state.revoked_project_ids
        ]
        for row_key in removed_keys:
            row_proj = state.known_rows.pop(row_key)
            task_key, run_key = row_key
            next_id = state.current_cursor(settings=settings)
            frames.append(
                SseFrame(
                    wire_text=format_sse_event(
                        event="row.removed",
                        event_id=next_id,
                        data={
                            "task_id": task_key,
                            "run_id": run_key,
                            "project_id": row_proj,
                            "reason": "permission_revoked",
                            "grant_epoch": current_epoch,
                        },
                    ),
                    event_name="row.removed",
                    project_id=None,
                )
            )

        if project_id is not None and project_id in state.revoked_project_ids:
            frames.append(
                SseFrame(
                    wire_text=format_resync_required_sse(reason="permission_revoked"),
                    event_name="resync_required",
                    is_terminal=True,
                )
            )
            return frames, True

    if pre_replayed is not None:
        events = pre_replayed
        # Initial replay already applied exact watermark checks at state.grant_epoch;
        # still re-filter below if policy changed between steps.
    else:
        poll_cursor = state.current_cursor(settings=settings)
        (
            _decoded,
            events,
            next_offsets,
            next_last_id,
            _srv_epoch,
            _revoked,
        ) = _replay_events_with_watermarks(
            conn,
            cursor=poll_cursor,
            principal=principal,
            project_id=project_id,
            expected_grant_epoch=state.grant_epoch,
            settings=settings,
        )
        # Advance watermarks past filtered/other-project events if no matching events arrived.
        if not events:
            state.stream_offsets = next_offsets
            state.last_event_id = max(state.last_event_id, next_last_id)

    single_project_fallback: str | None = None
    if events and (state.revoked_project_ids or project_id is not None):
        summaries = load_project_assessment_summaries(conn, tenant_id=state.tenant_id)
        if len(summaries) == 1:
            single_project_fallback = next(iter(summaries))

    for ev in events:
        ev_stream = str(ev["stream_id"])
        ev_off = int(ev["ingest_offset"])
        ev_id_int = int(ev["id"])
        state.stream_offsets[ev_stream] = max(state.stream_offsets.get(ev_stream, 0), ev_off)
        state.last_event_id = max(state.last_event_id, ev_id_int)

        ev_proj = resolve_event_project_id(
            conn,
            tenant_id=state.tenant_id,
            event=ev,
            cache=state.proj_cache,
            single_project_fallback=single_project_fallback,
        )
        if project_id is not None and ev_proj != project_id:
            continue
        if state.revoked_project_ids and ev_proj in state.revoked_project_ids:
            continue

        raw_payload = ev.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        t_id = payload.get("task_id")
        r_id = ev_stream.split("run:", 1)[1] if ev_stream.startswith("run:") else payload.get("run_id")
        if t_id:
            state.known_rows[(str(t_id), str(r_id) if r_id else None)] = ev_proj

        next_cursor = state.current_cursor(settings=settings)
        event_data = {
            "event_id": ev["event_id"],
            "tenant_id": ev["tenant_id"],
            "stream_id": ev["stream_id"],
            "ingest_offset": ev_off,
            "producer_id": ev["producer_id"],
            "event_type": ev["event_type"],
            "payload": payload,
            "occurred_at": ev["occurred_at"],
            "ingested_at": ev["ingested_at"],
            "grant_epoch": state.grant_epoch,
        }
        frames.append(
            SseFrame(
                wire_text=format_sse_event(
                    event=ev["event_type"],
                    event_id=next_cursor,
                    data=event_data,
                ),
                event_name=ev["event_type"],
                project_id=ev_proj,
            )
        )

    return frames, False


async def _run_db_op(bind: Engine | sa.Connection, fn: Callable[[sa.Connection], Any]) -> Any:
    if isinstance(bind, Engine):
        def _sync() -> Any:
            with bind.connect() as conn:
                return fn(conn)

        return await anyio.to_thread.run_sync(_sync)
    return fn(bind)


async def iter_hud_sse_frames(
    db_or_engine: Engine | sa.Connection | Session,
    *,
    cursor: str | None,
    principal: Principal,
    project_id: str | None = None,
    settings: Settings | None = None,
    poll_interval_ms: int = DEFAULT_SSE_POLL_INTERVAL_MS,
    heartbeat_sec: float = DEFAULT_SSE_HEARTBEAT_SEC,
    max_buffer_size: int = DEFAULT_SSE_MAX_BUFFER_SIZE,
    max_events: int | None = None,
    idle_timeout_ms: int | None = None,
    is_disconnected: Callable[[], Any] | None = None,
    now: datetime | None = None,
) -> AsyncIterator[str]:
    """Async SSE generator streaming post-cursor events with bounded buffering and revocation."""
    cfg = settings or get_settings()
    if not cursor or not cursor.strip():
        yield format_resync_required_sse(reason="missing_cursor")
        return

    clean_cursor = cursor.strip()
    bind: Engine | sa.Connection = (
        db_or_engine.get_bind() if isinstance(db_or_engine, Session) else db_or_engine
    )
    poll_sec = max(0.005, float(poll_interval_ms) / 1000.0)
    hb_sec = max(0.01, float(heartbeat_sec))
    buf_cap = max(1, int(max_buffer_size))

    # Validate scope reference early without hitting DB for malformed parameters.
    _ = viewer_scope_ref_for_principal(principal, project_id=project_id)

    try:
        state, initial_events, initial_next_offsets, initial_next_last_id = await _run_db_op(
            bind,
            lambda conn: _bootstrap_stream_state(
                conn,
                cursor=clean_cursor,
                principal=principal,
                project_id=project_id,
                settings=cfg,
                now=now,
            ),
        )
    except CursorError as exc:
        yield format_resync_required_sse(reason=_sanitize_cursor_error_reason(exc))
        return

    queue: asyncio.Queue[SseFrame | None] = asyncio.Queue(maxsize=buf_cap)
    stop_event = asyncio.Event()

    async def _push_frame(frame: SseFrame) -> bool:
        if queue.full():
            return False
        queue.put_nowait(frame)
        return True

    def _drain_and_enqueue_resync(reason: str) -> None:
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        # Single terminal frame fits even when max_buffer_size == 1 (consumer stops on is_terminal).
        queue.put_nowait(
            SseFrame(
                wire_text=format_resync_required_sse(reason=reason),
                event_name="resync_required",
                is_terminal=True,
                counts_as_event=False,
            )
        )

    async def _poll_step(pre_events: list[dict[str, Any]] | None) -> tuple[list[SseFrame], bool]:
        def _op(conn: sa.Connection) -> tuple[list[SseFrame], bool]:
            return _build_frames_from_poll(
                conn,
                state=state,
                principal=principal,
                project_id=project_id,
                settings=cfg,
                pre_replayed=pre_events,
            )

        res: tuple[list[SseFrame], bool] = await _run_db_op(bind, _op)
        return res

    async def _producer() -> None:
        first_tick = True
        last_hb = time.monotonic()
        try:
            while not stop_event.is_set():
                try:
                    pre = initial_events if first_tick else None
                    if first_tick and not initial_events:
                        state.stream_offsets = initial_next_offsets
                        state.last_event_id = max(state.last_event_id, initial_next_last_id)
                    first_tick = False
                    frames, terminal = await _poll_step(pre)
                except CursorError as exc:
                    _drain_and_enqueue_resync(_sanitize_cursor_error_reason(exc))
                    return

                if len(frames) > buf_cap or queue.qsize() + len(frames) > buf_cap:
                    _drain_and_enqueue_resync("slow_consumer")
                    return

                for frame in frames:
                    if not await _push_frame(frame):
                        _drain_and_enqueue_resync("slow_consumer")
                        return
                    last_hb = time.monotonic()

                if terminal:
                    return

                now_mono = time.monotonic()
                if now_mono - last_hb >= hb_sec and not queue.full():
                    queue.put_nowait(
                        SseFrame(
                            wire_text=format_sse_comment("heartbeat"),
                            event_name="comment",
                            counts_as_event=False,
                        )
                    )
                    last_hb = now_mono

                await asyncio.sleep(poll_sec)
        except asyncio.CancelledError:
            raise
        except sa.exc.SQLAlchemyError:
            logger.exception("HUD SSE producer database error")
            with contextlib.suppress(asyncio.QueueFull):
                _drain_and_enqueue_resync("stream_error")
        except RuntimeError:
            logger.exception("HUD SSE producer runtime error")
            with contextlib.suppress(asyncio.QueueFull):
                _drain_and_enqueue_resync("stream_error")

    producer_task = asyncio.create_task(_producer())
    emitted_count = 0
    last_activity = time.monotonic()
    idle_limit_sec = (float(idle_timeout_ms) / 1000.0) if idle_timeout_ms is not None else None

    try:
        while True:
            if is_disconnected is not None:
                res = is_disconnected()
                disconnected = await res if inspect.isawaitable(res) else bool(res)
                if disconnected:
                    break

            wait_timeout = poll_sec
            if idle_limit_sec is not None:
                remaining = idle_limit_sec - (time.monotonic() - last_activity)
                if remaining <= 0:
                    break
                wait_timeout = min(wait_timeout, max(0.005, remaining))

            try:
                item = await asyncio.wait_for(queue.get(), timeout=wait_timeout)
            except TimeoutError:
                if idle_limit_sec is not None and (time.monotonic() - last_activity) >= idle_limit_sec:
                    break
                continue

            if item is None:
                break

            # Last-chance revocation gate right before wire flush (defense in depth).
            if item.project_id is not None and item.project_id in state.revoked_project_ids:
                continue

            yield item.wire_text
            if item.counts_as_event:
                last_activity = time.monotonic()
                emitted_count += 1
            if item.is_terminal:
                break
            if max_events is not None and emitted_count >= max_events:
                break
    finally:
        stop_event.set()
        if not producer_task.done():
            producer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await producer_task
