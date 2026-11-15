"""PostgreSQL integration tests for P3a-WP2: SSE replay, permission revocation, ISS-039, ISS-040.

Covers conformance cases C09, C10, C28, C30 plus security & scale bounds:
- ISS-039: random per-process fallback with warning when EAP_HUD_CURSOR_SECRET is unset;
  retired static key rejected; AES-GCM (ehc2_) cursor format; old ehc1_ & old-key cursors -> resync_required.
- C09: disconnect after N events, commit events while disconnected, reconnect via Last-Event-ID ->
  events N+1..M delivered exactly once (0 gaps, 0 duplicates).
- C10: expired cursor and retention floor gap -> single resync_required event then close,
  followed by fresh snapshot + SSE recovery.
- C28 & C30: dual bootstrap principals in same tenant; live stream open while access to a project
  is revoked -> row.removed sent, 0 post-revocation events for revoked project, other projects continue,
  artifact & evidence URLs immediately return 403, pre-revocation cursor reconnect -> resync_required;
  p50/p95 revocation propagation latency recorded.
- Cross-tenant cursor -> resync_required with zero tenant/scope leak.
- Slow consumer: bounded server-side buffer -> resync_required (slow_consumer) without unbounded memory.
- ISS-040: bounded reads (0 outbox scans, 0 full event_log scans) + >= 100k-event snapshot benchmark.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event as sa_event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import eap.config as eap_config
from eap.api.app import create_app
from eap.api.dependencies import Principal, get_db, hash_token
from eap.config import OLD_INSECURE_HUD_CURSOR_SECRET, ConfiguredPrincipal, Settings, get_settings
from eap.db.assessment_tables import ppm_evidence
from eap.db.event_tables import (
    event_quarantine,
    outbox_relay_marks,
    projection_checkpoints,
    security_events,
    stream_heads,
)
from eap.db.policy_tables import (
    principal_grant_epochs,
    principal_project_grants,
    stream_retention_floors,
)
from eap.db.tables import (
    artifacts,
    commands,
    event_log,
    outbox,
    run_attempt_history,
    runs,
    tasks,
    usage_ledger,
)
from eap.events.ingest import ProducerBinding
from eap.events.projection import PROJECTION_NAME_HUD_RUNS
from eap.events.relay import OutboxRelayWorker
from eap.hud.snapshot import (
    CURSOR_PREFIX,
    CursorTamperedError,
    _load_committed_event_rows,
    apply_replayed_events_to_snapshot,
    encode_snapshot_cursor,
    take_hud_snapshot,
    verify_snapshot_cursor,
    viewer_scope_ref_for_principal,
)
from eap.hud.sse import iter_hud_sse_frames, parse_sse_frames
from eap.policy.grants import (
    policy_binding_provider,
    revoke_project_access,
    set_stream_retention_floor,
)
from eap.run.outbox import build_event, create_outbox_record
from eap.task.service import TaskRequest, TaskService

pytestmark = pytest.mark.skipif(
    os.environ.get("TEST_DATABASE_URL") is None,
    reason="PostgreSQL database required for P3a-WP2 SSE replay and revocation tests",
)

TEST_CURSOR_SECRET = "p3a-wp2-strong-test-cursor-secret-32b!"


def _make_settings(
    *,
    principals: list[ConfiguredPrincipal] | None = None,
    cursor_secret: str | None = TEST_CURSOR_SECRET,
    ttl_seconds: int = 3600,
) -> Settings:
    return Settings(
        local_principals=principals or [],
        hud_cursor_secret=SecretStr(cursor_secret) if cursor_secret is not None else None,
        hud_cursor_ttl_seconds=ttl_seconds,
    )


def _make_binding(tenant_id: str, producer_id: str, binding_id: str) -> ProducerBinding | None:
    if tenant_id and producer_id == "policy_service":
        return policy_binding_provider(tenant_id, producer_id)
    return ProducerBinding(
        tenant_id=tenant_id,
        producer_id=producer_id,
        binding_id=binding_id,
        certified_capabilities={"events": True},
    )


def _emit_run_event(
    conn: sa.Connection,
    *,
    tenant_id: str,
    task_id: str,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
    producer_seq: int,
    producer_id: str = "run_service",
    binding_id: str = "bind-wp2",
) -> str:
    stream_id = f"run:{run_id}"
    event_obj = build_event(
        event_type=event_type,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        task_id=task_id,
        run_id=run_id,
        attempt=1,
        producer_id=producer_id,
        producer_seq=producer_seq,
        payload=payload,
    )
    ob = create_outbox_record(event_obj, stream_id=stream_id)
    conn.execute(outbox.insert().values(**ob))
    worker = OutboxRelayWorker(enabled=True, batch_size=100)
    res = worker.relay_stream_batch(
        conn,
        tenant_id=tenant_id,
        stream_id=stream_id,
        binding_provider=lambda t_id, p_id: _make_binding(t_id, p_id, binding_id),
    )
    assert res.ingested_count == 1
    assert res.quarantined_count == 0
    return str(event_obj.event_id)


def _legacy_ehc1_constant_key_cursor(
    *,
    tenant_id: str,
    scope: str,
    offset: int,
    stream_offsets: dict[str, int],
    grant_epoch: int,
) -> str:
    """Reproduce a P3a-WP1 `ehc1_` cursor encrypted/MACed with the retired constant key."""
    key = OLD_INSECURE_HUD_CURSOR_SECRET
    now_ts = int(datetime.now(UTC).timestamp())
    body = {
        "v": 1,
        "t": tenant_id,
        "s": scope,
        "o": offset,
        "so": stream_offsets,
        "ge": grant_epoch,
        "iat": now_ts,
        "exp": now_ts + 3600,
    }
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    nonce = b"\x11" * 16
    enc_key = hmac.new(key, b"enc", hashlib.sha256).digest()
    mac_key = hmac.new(key, b"mac", hashlib.sha256).digest()
    keystream = bytearray()
    counter = 0
    while len(keystream) < len(raw):
        block = hmac.new(enc_key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        keystream.extend(block)
        counter += 1
    ct = bytes(a ^ b for a, b in zip(raw, keystream[: len(raw)], strict=True))
    mac = hmac.new(mac_key, b"\x01" + nonce + ct, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(b"\x01" + nonce + ct + mac).rstrip(b"=").decode("ascii")
    return f"ehc1_{token}"


def _cleanup_tenant(engine: Engine, tenant_id: str) -> None:
    """Clean up all committed test rows for a tenant in FK-safe order."""
    tables_in_order = (
        artifacts,
        commands,
        usage_ledger,
        run_attempt_history,
        runs,
        tasks,
        ppm_evidence,
        principal_project_grants,
        principal_grant_epochs,
        stream_retention_floors,
        projection_checkpoints,
        outbox_relay_marks,
        event_quarantine,
        security_events,
        stream_heads,
        event_log,
        outbox,
    )
    with engine.begin() as conn:
        for tbl in tables_in_order:
            conn.execute(tbl.delete().where(tbl.c.tenant_id == tenant_id))


def _percentile(values: list[float], p: float) -> float:
    assert values
    ordered = sorted(values)
    idx = (len(ordered) - 1) * (p / 100.0)
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


# ---------------------------------------------------------------------------
# 1. ISS-039: Secret handling, AEAD cursor & old constant-key rejection
# ---------------------------------------------------------------------------


def test_iss039_cursor_secret_warning_retired_key_and_aead(
    db_conn: sa.Connection,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ISS-039: no constant fallback key, warning logged, old key & ehc1_ rejected."""
    # Unset secret emits warning and generates a random 32-byte key (same within process).
    monkeypatch.setattr(eap_config, "_WARNED_EPHEMERAL_CURSOR_SECRET", False)
    s_unset = _make_settings(cursor_secret=None)
    assert s_unset.eap_bind.startswith("127.0.0.1")
    with caplog.at_level(logging.WARNING, logger="eap.config"):
        k1 = s_unset.get_hud_cursor_secret()
        k2 = s_unset.get_hud_cursor_secret()
    assert len(k1) == 32
    assert k1 == k2
    assert k1 != hashlib.sha256(OLD_INSECURE_HUD_CURSOR_SECRET).digest()
    assert any("EAP_HUD_CURSOR_SECRET is unset" in rec.message for rec in caplog.records)

    # Configuring the retired WP1 static fallback key is explicitly refused.
    s_retired = _make_settings(cursor_secret=OLD_INSECURE_HUD_CURSOR_SECRET.decode("utf-8"))
    with pytest.raises(ValueError, match="retired static key"):
        s_retired.get_hud_cursor_secret()

    tenant_id = f"tenant-iss039-{uuid.uuid4().hex[:8]}"
    principal = ConfiguredPrincipal(
        tenant_id=tenant_id,
        principal_id="owner-iss039",
        token_hash=SecretStr(hash_token("tok-iss039")),
        roles=["owner"],
    )
    settings = _make_settings(principals=[principal])
    viewer = Principal(tenant_id=tenant_id, principal_id="owner-iss039", roles=["owner"])
    scope = viewer_scope_ref_for_principal(viewer)

    # New cursor uses ehc2_ prefix (AES-256-GCM).
    snap = take_hud_snapshot(db_conn, principal=viewer, settings=settings)
    assert snap["snapshot_cursor"].startswith(CURSOR_PREFIX)
    assert snap["snapshot_cursor"].startswith("ehc2_")

    # Old ehc1_ constant-key cursor is rejected with CursorTamperedError.
    old_ehc1 = _legacy_ehc1_constant_key_cursor(
        tenant_id=tenant_id,
        scope=scope,
        offset=0,
        stream_offsets={},
        grant_epoch=0,
    )
    with pytest.raises(CursorTamperedError):
        verify_snapshot_cursor(old_ehc1, expected_tenant_id=tenant_id, expected_scope=scope, settings=settings)

    # Even an ehc2_ cursor minted using the retired constant key is rejected.
    class _LegacyKeySettings(Settings):
        def get_hud_cursor_secret(self) -> bytes:
            return hashlib.sha256(OLD_INSECURE_HUD_CURSOR_SECRET).digest()

    legacy_key_settings = _LegacyKeySettings.model_construct(
        eap_env="test",
        local_principals=[principal],
        hud_cursor_secret=SecretStr("x" * 32),
        hud_cursor_ttl_seconds=3600,
    )
    forged_ehc2 = encode_snapshot_cursor(
        tenant_id=tenant_id,
        scope=scope,
        offset=0,
        stream_offsets={},
        settings=legacy_key_settings,
    )
    with pytest.raises(CursorTamperedError):
        verify_snapshot_cursor(forged_ehc2, expected_tenant_id=tenant_id, expected_scope=scope, settings=settings)

    # HTTP GET /v1/hud/events rejects old constant-key cursor with a single resync_required frame.
    app = create_app(settings=settings)

    def _override_db() -> Generator[Session, None, None]:
        sess = Session(bind=db_conn)
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        resp = client.get(
            f"/v1/hud/events?cursor={old_ehc1}",
            headers={"Authorization": "Bearer tok-iss039"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        frames = parse_sse_frames(resp.text)
        assert len(frames) == 1
        assert frames[0]["event"] == "resync_required"
        assert frames[0]["data"]["resync_required"] is True
        assert tenant_id not in resp.text


# ---------------------------------------------------------------------------
# 2. C09: Disconnect + Reconnect via Last-Event-ID (0 gaps, 0 duplicates)
# ---------------------------------------------------------------------------


def test_c09_sse_disconnect_and_reconnect_exact_once_no_gap_no_dup(db_engine: Engine) -> None:
    """Disconnect after N events, commit while disconnected, reconnect via Last-Event-ID -> N+1..M."""
    tenant_id = f"tenant-c09-{uuid.uuid4().hex[:8]}"
    tok_owner = "tok-c09-owner"
    principals = [
        ConfiguredPrincipal(
            tenant_id=tenant_id,
            principal_id="owner-c09",
            token_hash=SecretStr(hash_token(tok_owner)),
            roles=["owner"],
        )
    ]
    settings = _make_settings(principals=principals)
    app = create_app(settings=settings)

    def _override_db() -> Generator[Session, None, None]:
        sess = Session(bind=db_engine)
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        task_id = f"task-c09-{uuid.uuid4().hex[:8]}"
        with Session(bind=db_engine) as sess:
            TaskService.create(
                sess,
                tenant_id=tenant_id,
                task_id=task_id,
                binding_id="bind-c09",
                project_id="proj-c09",
                request=TaskRequest(
                    contract_version="1.0",
                    agent_id="agent-1",
                    task_type="code",
                    objective="Test SSE reconnect",
                    input_refs=[],
                    resource_refs=["project:proj-c09"],
                    idempotency_key=f"idem-{uuid.uuid4().hex[:10]}",
                ),
            )
            task_runs = TaskService.get_runs(sess, tenant_id=tenant_id, task_id=task_id)
            assert task_runs
            run_id = str(task_runs[0]["run_id"])
            OutboxRelayWorker(enabled=True).relay_stream_batch(
                sess.connection(),
                tenant_id=tenant_id,
                stream_id=f"run:{run_id}",
                binding_provider=lambda t_id, p_id: _make_binding(t_id, p_id, "bind-c09"),
            )
            sess.commit()

        with TestClient(app) as client:
            snap_resp = client.get("/v1/hud/snapshot", headers={"Authorization": f"Bearer {tok_owner}"})
            assert snap_resp.status_code == 200
            initial_snapshot = snap_resp.json()
            initial_cursor = initial_snapshot["snapshot_cursor"]

            # Commit events 1..3 (offsets 2, 3, 4 on run stream).
            emitted_ids: list[str] = []
            with db_engine.begin() as conn:
                for step in (1, 2, 3):
                    eid = _emit_run_event(
                        conn,
                        tenant_id=tenant_id,
                        task_id=task_id,
                        run_id=run_id,
                        event_type="run.progress",
                        producer_seq=step + 1,
                        payload={
                            "completed_units": step,
                            "total_units": 6,
                            "unit": "steps",
                            "stage": f"stage-{step}",
                        },
                    )
                    emitted_ids.append(eid)

            # Client receives first N=2 events via SSE, then disconnects cleanly.
            part1_resp = client.get(
                f"/v1/hud/events?cursor={initial_cursor}&max_events=2&poll_interval_ms=10&idle_timeout_ms=1500",
                headers={"Authorization": f"Bearer {tok_owner}"},
            )
            assert part1_resp.status_code == 200
            part1_frames = [f for f in parse_sse_frames(part1_resp.text) if f["type"] == "event"]
            assert len(part1_frames) == 2
            assert [f["data"]["event_id"] for f in part1_frames] == emitted_ids[:2]
            assert all(str(f["id"]).startswith("ehc2_") for f in part1_frames)
            last_event_id = str(part1_frames[-1]["id"])

            # While disconnected, commit events 4..6 (stage-4..stage-6 + completion).
            with db_engine.begin() as conn:
                for step in (4, 5):
                    eid = _emit_run_event(
                        conn,
                        tenant_id=tenant_id,
                        task_id=task_id,
                        run_id=run_id,
                        event_type="run.progress",
                        producer_seq=step + 1,
                        payload={
                            "completed_units": step,
                            "total_units": 6,
                            "unit": "steps",
                            "stage": f"stage-{step}",
                        },
                    )
                    emitted_ids.append(eid)
                eid_done = _emit_run_event(
                    conn,
                    tenant_id=tenant_id,
                    task_id=task_id,
                    run_id=run_id,
                    event_type="run.state_changed",
                    producer_seq=7,
                    payload={
                        "from_state": "running",
                        "to_state": "succeeded",
                        "reason_code": "completed",
                        "evidence_ref": None,
                    },
                )
                emitted_ids.append(eid_done)

            # Reconnect using Last-Event-ID header -> receive events 3..6 with 0 gaps & 0 duplicates.
            part2_resp = client.get(
                "/v1/hud/events?max_events=4&poll_interval_ms=10&idle_timeout_ms=1500",
                headers={
                    "Authorization": f"Bearer {tok_owner}",
                    "Last-Event-ID": last_event_id,
                },
            )
            assert part2_resp.status_code == 200
            part2_frames = [f for f in parse_sse_frames(part2_resp.text) if f["type"] == "event"]
            assert len(part2_frames) == 4
            assert [f["data"]["event_id"] for f in part2_frames] == emitted_ids[2:]
            assert [f["data"]["ingest_offset"] for f in [*part1_frames, *part2_frames]] == [2, 3, 4, 5, 6, 7]

            # Applying initial snapshot + replayed frames must equal a fresh snapshot.
            all_replayed = [f["data"] for f in [*part1_frames, *part2_frames]]
            folded = apply_replayed_events_to_snapshot(initial_snapshot, all_replayed)
            fresh = client.get("/v1/hud/snapshot", headers={"Authorization": f"Bearer {tok_owner}"}).json()
            assert folded["rows"][0]["run_state"] == fresh["rows"][0]["run_state"] == "succeeded"
            assert folded["rows"][0]["progress"]["completed_units"] == 5
    finally:
        _cleanup_tenant(db_engine, tenant_id)


# ---------------------------------------------------------------------------
# 3. C10: Expired cursor & retention gap -> resync_required + recovery
# ---------------------------------------------------------------------------


def test_c10_expired_cursor_retention_gap_heartbeat_and_cross_tenant(db_engine: Engine) -> None:
    """Verify C10 (expired & retention gap -> resync_required + recovery), heartbeats, cross-tenant."""
    tenant_a = f"tenant-c10-a-{uuid.uuid4().hex[:8]}"
    tenant_b = f"tenant-c10-b-{uuid.uuid4().hex[:8]}"
    tok_a = "tok-c10-a"
    tok_b = "tok-c10-b"
    principals = [
        ConfiguredPrincipal(
            tenant_id=tenant_a,
            principal_id="owner-a",
            token_hash=SecretStr(hash_token(tok_a)),
            roles=["owner"],
        ),
        ConfiguredPrincipal(
            tenant_id=tenant_b,
            principal_id="owner-b",
            token_hash=SecretStr(hash_token(tok_b)),
            roles=["owner"],
        ),
    ]
    settings = _make_settings(principals=principals, ttl_seconds=60)
    app = create_app(settings=settings)

    def _override_db() -> Generator[Session, None, None]:
        sess = Session(bind=db_engine)
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        task_id = f"task-c10-{uuid.uuid4().hex[:8]}"
        with Session(bind=db_engine) as sess:
            TaskService.create(
                sess,
                tenant_id=tenant_a,
                task_id=task_id,
                binding_id="bind-c10",
                project_id="proj-c10",
                request=TaskRequest(
                    contract_version="1.0",
                    agent_id="agent-1",
                    task_type="code",
                    objective="Retention gap recovery",
                    input_refs=[],
                    resource_refs=["project:proj-c10"],
                    idempotency_key=f"idem-{uuid.uuid4().hex[:10]}",
                ),
            )
            task_runs = TaskService.get_runs(sess, tenant_id=tenant_a, task_id=task_id)
            assert task_runs
            run_id = str(task_runs[0]["run_id"])
            OutboxRelayWorker(enabled=True).relay_stream_batch(
                sess.connection(),
                tenant_id=tenant_a,
                stream_id=f"run:{run_id}",
                binding_provider=lambda t_id, p_id: _make_binding(t_id, p_id, "bind-c10"),
            )
            sess.commit()

        with TestClient(app) as client:
            snap_a = client.get("/v1/hud/snapshot", headers={"Authorization": f"Bearer {tok_a}"}).json()
            cursor_a = snap_a["snapshot_cursor"]

            # (a) Cross-tenant cursor rejected on SSE with resync_required and zero tenant details leaked.
            cross_resp = client.get(
                f"/v1/hud/events?cursor={cursor_a}",
                headers={"Authorization": f"Bearer {tok_b}"},
            )
            assert cross_resp.status_code == 200
            cross_frames = parse_sse_frames(cross_resp.text)
            assert len(cross_frames) == 1
            assert cross_frames[0]["event"] == "resync_required"
            assert cross_frames[0]["data"]["reason"] == "cursor_scope_mismatch"
            assert tenant_a not in cross_resp.text
            assert "owner-a" not in cross_resp.text

            # (b) Expired cursor rejected on SSE with resync_required (cursor_expired).
            expired_cursor = encode_snapshot_cursor(
                tenant_id=tenant_a,
                scope=snap_a["viewer_scope_ref"],
                offset=snap_a["projection_revision"],
                stream_offsets={f"run:{run_id}": 1},
                settings=settings,
                now=datetime.now(UTC) - timedelta(seconds=120),
            )
            exp_resp = client.get(
                f"/v1/hud/events?cursor={expired_cursor}",
                headers={"Authorization": f"Bearer {tok_a}"},
            )
            exp_frames = parse_sse_frames(exp_resp.text)
            assert len(exp_frames) == 1
            assert exp_frames[0]["event"] == "resync_required"
            assert exp_frames[0]["data"]["reason"] == "cursor_expired"

            # (c) Retention gap: emit offsets 2..5, then advance retention floor to 4 (purging 1..3).
            with db_engine.begin() as conn:
                for step in (2, 3, 4, 5):
                    _emit_run_event(
                        conn,
                        tenant_id=tenant_a,
                        task_id=task_id,
                        run_id=run_id,
                        event_type="run.progress",
                        producer_seq=step,
                        payload={
                            "completed_units": step,
                            "total_units": 6,
                            "unit": "steps",
                            "stage": f"step-{step}",
                        },
                    )
                set_stream_retention_floor(
                    conn,
                    tenant_id=tenant_a,
                    stream_id=f"run:{run_id}",
                    min_retained_offset=4,
                    purge_event_log=True,
                )

            gap_resp = client.get(
                f"/v1/hud/events?cursor={cursor_a}",
                headers={"Authorization": f"Bearer {tok_a}"},
            )
            gap_frames = parse_sse_frames(gap_resp.text)
            assert len(gap_frames) == 1
            assert gap_frames[0]["event"] == "resync_required"
            assert gap_frames[0]["data"]["reason"] == "retention_gap"

            # Recovery: fresh snapshot + opening SSE with new cursor works + emits heartbeat comments.
            recovered_snap = client.get("/v1/hud/snapshot", headers={"Authorization": f"Bearer {tok_a}"})
            assert recovered_snap.status_code == 200
            recovered_cursor = recovered_snap.json()["snapshot_cursor"]

            hb_resp = client.get(
                f"/v1/hud/events?cursor={recovered_cursor}&heartbeat_sec=0.03&poll_interval_ms=10&idle_timeout_ms=120",
                headers={"Authorization": f"Bearer {tok_a}"},
            )
            assert hb_resp.status_code == 200
            hb_frames = parse_sse_frames(hb_resp.text)
            assert any(f["type"] == "comment" and "heartbeat" in f["comments"] for f in hb_frames)

            # New event after recovery arrives cleanly via SSE.
            with db_engine.begin() as conn:
                post_eid = _emit_run_event(
                    conn,
                    tenant_id=tenant_a,
                    task_id=task_id,
                    run_id=run_id,
                    event_type="run.progress",
                    producer_seq=6,
                    payload={
                        "completed_units": 6,
                        "total_units": 6,
                        "unit": "steps",
                        "stage": "recovered",
                    },
                )
            tail_resp = client.get(
                f"/v1/hud/events?cursor={recovered_cursor}&max_events=1&poll_interval_ms=10&idle_timeout_ms=1000",
                headers={"Authorization": f"Bearer {tok_a}"},
            )
            tail_events = [f for f in parse_sse_frames(tail_resp.text) if f["type"] == "event"]
            assert len(tail_events) == 1
            assert tail_events[0]["data"]["event_id"] == post_eid
            assert "recovered" in json.dumps(tail_events[0]["data"]["payload"])
    finally:
        _cleanup_tenant(db_engine, tenant_a)
        _cleanup_tenant(db_engine, tenant_b)


# ---------------------------------------------------------------------------
# 4. C28 & C30: Dual principals, live SSE revocation, artifact/evidence 403, latency
# ---------------------------------------------------------------------------


async def test_c28_c30_dual_principal_live_revocation_evidence_403_and_latency(db_engine: Engine) -> None:
    """Two bootstrap principals: revoke B's project while stream open -> row.removed + 0 events + 403."""
    tenant_id = f"tenant-c30-{uuid.uuid4().hex[:8]}"
    tok_a = "tok-c30-principal-a"
    tok_b = "tok-c30-principal-b"
    principals = [
        ConfiguredPrincipal(
            tenant_id=tenant_id,
            principal_id="principal-a",
            token_hash=SecretStr(hash_token(tok_a)),
            roles=["owner", "admin"],
        ),
        ConfiguredPrincipal(
            tenant_id=tenant_id,
            principal_id="principal-b",
            token_hash=SecretStr(hash_token(tok_b)),
            roles=["admin"],
        ),
    ]
    settings = _make_settings(principals=principals)
    app = create_app(settings=settings)

    def _override_db() -> Generator[Session, None, None]:
        sess = Session(bind=db_engine)
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        task_alpha = f"task-alpha-{uuid.uuid4().hex[:6]}"
        task_beta = f"task-beta-{uuid.uuid4().hex[:6]}"
        artifact_alpha_id = f"art-alpha-{uuid.uuid4().hex[:8]}"
        evidence_alpha_id = f"ev-alpha-{uuid.uuid4().hex[:8]}"

        with Session(bind=db_engine) as sess:
            TaskService.create(
                sess,
                tenant_id=tenant_id,
                task_id=task_alpha,
                binding_id="bind-c30",
                project_id="proj-alpha",
                request=TaskRequest(
                    contract_version="1.0",
                    agent_id="agent-1",
                    task_type="code",
                    objective="Alpha task",
                    input_refs=[],
                    resource_refs=["project:proj-alpha"],
                    idempotency_key=f"idem-a-{uuid.uuid4().hex[:8]}",
                ),
            )
            TaskService.create(
                sess,
                tenant_id=tenant_id,
                task_id=task_beta,
                binding_id="bind-c30",
                project_id="proj-beta",
                request=TaskRequest(
                    contract_version="1.0",
                    agent_id="agent-1",
                    task_type="code",
                    objective="Beta task",
                    input_refs=[],
                    resource_refs=["project:proj-beta"],
                    idempotency_key=f"idem-b-{uuid.uuid4().hex[:8]}",
                ),
            )
            runs_a = TaskService.get_runs(sess, tenant_id=tenant_id, task_id=task_alpha)
            runs_b = TaskService.get_runs(sess, tenant_id=tenant_id, task_id=task_beta)
            assert runs_a and runs_b
            run_alpha = str(runs_a[0]["run_id"])
            run_beta = str(runs_b[0]["run_id"])

            worker = OutboxRelayWorker(enabled=True)
            for s_id in (f"run:{run_alpha}", f"run:{run_beta}"):
                worker.relay_stream_batch(
                    sess.connection(),
                    tenant_id=tenant_id,
                    stream_id=s_id,
                    binding_provider=lambda t_id, p_id: _make_binding(t_id, p_id, "bind-c30"),
                )

            sess.execute(
                artifacts.insert().values(
                    id=f"artpk-{uuid.uuid4().hex[:8]}",
                    tenant_id=tenant_id,
                    artifact_id=artifact_alpha_id,
                    task_id=task_alpha,
                    run_id=run_alpha,
                    content_hash="a" * 64,
                    content_type="application/json",
                    size_bytes=128,
                    storage_uri=f"local://artifacts/{artifact_alpha_id}.json",
                )
            )
            sess.execute(
                ppm_evidence.insert().values(
                    id=f"evpk-{uuid.uuid4().hex[:8]}",
                    tenant_id=tenant_id,
                    project_id="proj-alpha",
                    evidence_id=evidence_alpha_id,
                    trust_class="verifier_observed",
                    producer="eap-verifier",
                    revision="rev-1",
                    test_id="AC-001",
                    outcome="passed",
                    collected_at=datetime.now(UTC),
                    output_hash="b" * 64,
                    artifact_path=f"artifacts/{evidence_alpha_id}.log",
                )
            )
            sess.commit()

        headers_a = {"Authorization": f"Bearer {tok_a}"}
        headers_b = {"Authorization": f"Bearer {tok_b}"}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            # Before revocation, principal B can snapshot both projects and read evidence/artifact via API.
            snap_b_resp = await client.get("/v1/hud/snapshot", headers=headers_b)
            assert snap_b_resp.status_code == 200
            snap_b = snap_b_resp.json()
            pre_revocation_cursor = snap_b["snapshot_cursor"]
            assert {r["task_id"] for r in snap_b["rows"]} == {task_alpha, task_beta}

            ev_before = await client.get(f"/v1/evidence/{evidence_alpha_id}", headers=headers_b)
            assert ev_before.status_code == 200
            assert ev_before.json()["signed_url"] is None
            art_before = await client.get(f"/v1/artifacts/{artifact_alpha_id}", headers=headers_b)
            assert art_before.status_code == 200
            assert art_before.json()["signed_url"] is None

            # Open principal B's live SSE stream and mutate policy + emit events concurrently.
            received_frames: list[dict[str, Any]] = []
            pre_rev_event_id: str | None = None
            post_alpha_event_id: str | None = None
            post_beta_event_id: str | None = None

            async def _read_stream() -> None:
                buffer = ""
                async with client.stream(
                    "GET",
                    f"/v1/hud/events?cursor={pre_revocation_cursor}&poll_interval_ms=5&idle_timeout_ms=450",
                    headers=headers_b,
                ) as resp:
                    assert resp.status_code == 200
                    async for chunk in resp.aiter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            raw_block, buffer = buffer.split("\n\n", 1)
                            for frame in parse_sse_frames(raw_block + "\n\n"):
                                if frame["type"] == "event":
                                    received_frames.append(frame)

            async def _drive_workload() -> None:
                nonlocal pre_rev_event_id, post_alpha_event_id, post_beta_event_id
                await asyncio.sleep(0.08)
                # 1. Pre-revocation event on proj-alpha -> B MUST receive this.
                with db_engine.begin() as conn:
                    pre_rev_event_id = _emit_run_event(
                        conn,
                        tenant_id=tenant_id,
                        task_id=task_alpha,
                        run_id=run_alpha,
                        event_type="run.progress",
                        producer_seq=2,
                        payload={
                            "completed_units": 1,
                            "total_units": 3,
                            "unit": "steps",
                            "stage": "pre-revocation",
                        },
                    )
                await asyncio.sleep(0.08)

                # 2. Principal A revokes Principal B's access to proj-alpha via API (emits policy.changed).
                revoke_resp = await client.post(
                    "/v1/hud/grants/revoke",
                    headers=headers_a,
                    json={"principal_id": "principal-b", "project_id": "proj-alpha", "reason": "c30_revoke"},
                )
                assert revoke_resp.status_code == 200
                assert revoke_resp.json()["grant_epoch"] == 1

                # 3. Emit post-revocation event on proj-alpha (MUST be blocked) and proj-beta (MUST be delivered).
                with db_engine.begin() as conn:
                    post_alpha_event_id = _emit_run_event(
                        conn,
                        tenant_id=tenant_id,
                        task_id=task_alpha,
                        run_id=run_alpha,
                        event_type="run.progress",
                        producer_seq=3,
                        payload={
                            "completed_units": 2,
                            "total_units": 3,
                            "unit": "steps",
                            "stage": "post-revocation-secret",
                        },
                    )
                    post_beta_event_id = _emit_run_event(
                        conn,
                        tenant_id=tenant_id,
                        task_id=task_beta,
                        run_id=run_beta,
                        event_type="run.progress",
                        producer_seq=2,
                        payload={
                            "completed_units": 1,
                            "total_units": 2,
                            "unit": "steps",
                            "stage": "beta-still-visible",
                        },
                    )

            reader = asyncio.create_task(_read_stream())
            driver = asyncio.create_task(_drive_workload())
            await asyncio.gather(reader, driver)

            event_names = [f["event"] for f in received_frames]
            delivered_eids = [f["data"].get("event_id") for f in received_frames if f["event"] != "row.removed"]
            assert pre_rev_event_id in delivered_eids
            assert post_beta_event_id in delivered_eids
            assert post_alpha_event_id not in delivered_eids
            assert all("post-revocation-secret" not in json.dumps(f) for f in received_frames)

            removed_frames = [f for f in received_frames if f["event"] == "row.removed"]
            assert len(removed_frames) == 1
            assert removed_frames[0]["data"]["task_id"] == task_alpha
            assert removed_frames[0]["data"]["project_id"] == "proj-alpha"
            assert removed_frames[0]["data"]["grant_epoch"] == 1
            assert "row.removed" in event_names

            # Old evidence & artifact URLs return 403 for principal B immediately, 200 for principal A.
            assert (await client.get(f"/v1/evidence/{evidence_alpha_id}", headers=headers_b)).status_code == 403
            assert (await client.get(f"/v1/artifacts/{artifact_alpha_id}", headers=headers_b)).status_code == 403
            assert (await client.get(f"/v1/artifacts/{evidence_alpha_id}", headers=headers_b)).status_code == 403
            assert (await client.get(f"/v1/evidence/{evidence_alpha_id}", headers=headers_a)).status_code == 200
            assert (await client.get(f"/v1/artifacts/{artifact_alpha_id}", headers=headers_a)).status_code == 200

            # C28: reconnecting with pre-revocation cursor returns resync_required; fresh snapshot omits proj-alpha.
            stale_reconnect = await client.get(
                f"/v1/hud/events?cursor={pre_revocation_cursor}",
                headers=headers_b,
            )
            stale_frames = parse_sse_frames(stale_reconnect.text)
            assert len(stale_frames) == 1
            assert stale_frames[0]["event"] == "resync_required"
            assert stale_frames[0]["data"]["reason"] == "grant_epoch_changed"

            fresh_b = (await client.get("/v1/hud/snapshot", headers=headers_b)).json()
            assert {r["task_id"] for r in fresh_b["rows"]} == {task_beta}
            # Explicitly scoped snapshot for revoked proj-alpha returns 403.
            assert (await client.get("/v1/hud/snapshot?project_id=proj-alpha", headers=headers_b)).status_code == 403

            # Measure propagation latency across 20 live revocations using unbuffered async SSE streaming.
            viewer_b = Principal(tenant_id=tenant_id, principal_id="principal-b", roles=["viewer"])
            prop_latencies_ms: list[float] = []
            for i in range(20):
                p_proj = f"proj-lat-{i}"
                t_lat = f"task-lat-{i}-{uuid.uuid4().hex[:6]}"
                with Session(bind=db_engine) as sess:
                    TaskService.create(
                        sess,
                        tenant_id=tenant_id,
                        task_id=t_lat,
                        binding_id="bind-c30",
                        project_id=p_proj,
                        request=TaskRequest(
                            contract_version="1.0",
                            agent_id="agent-1",
                            task_type="code",
                            objective="Latency sample",
                            input_refs=[],
                            resource_refs=[f"project:{p_proj}"],
                            idempotency_key=f"idem-lat-{i}-{uuid.uuid4().hex[:6]}",
                        ),
                    )
                    runs_lat = TaskService.get_runs(sess, tenant_id=tenant_id, task_id=t_lat)
                    assert runs_lat
                    OutboxRelayWorker(enabled=True).relay_stream_batch(
                        sess.connection(),
                        tenant_id=tenant_id,
                        stream_id=f"run:{runs_lat[0]['run_id']}",
                        binding_provider=lambda t_id, p_id: _make_binding(t_id, p_id, "bind-c30"),
                    )
                    sess.commit()
                cur_lat = (await client.get("/v1/hud/snapshot", headers=headers_b)).json()["snapshot_cursor"]
                sample_t0: float = 0.0
                sample_t1: float = 0.0
                last_buf: str = ""
                stream_ready = asyncio.Event()

                async def _wait_remove(
                    cursor_val: str = cur_lat,
                    ready_ev: asyncio.Event = stream_ready,
                ) -> None:
                    nonlocal sample_t1, last_buf
                    buf = ""
                    async for ch in iter_hud_sse_frames(
                        db_engine,
                        cursor=cursor_val,
                        principal=viewer_b,
                        settings=settings,
                        poll_interval_ms=5,
                        heartbeat_sec=0.01,
                        max_events=1,
                        idle_timeout_ms=1000,
                    ):
                        buf += ch
                        last_buf = buf
                        if not ready_ev.is_set():
                            ready_ev.set()
                        for fr in parse_sse_frames(buf):
                            if fr["type"] == "event" and fr["event"] == "row.removed":
                                sample_t1 = time.monotonic()
                                return

                async def _do_revoke(
                    target_proj: str = p_proj,
                    ready_ev: asyncio.Event = stream_ready,
                ) -> None:
                    nonlocal sample_t0
                    await asyncio.wait_for(ready_ev.wait(), timeout=1.0)
                    sample_t0 = time.monotonic()
                    with db_engine.begin() as conn:
                        revoke_project_access(
                            conn,
                            tenant_id=tenant_id,
                            principal_id="principal-b",
                            project_id=target_proj,
                            actor_id="principal-a",
                        )

                await asyncio.gather(_wait_remove(), _do_revoke())
                assert sample_t1 >= sample_t0 > 0, (
                    f"iter={i} target_proj={p_proj} sample_t1={sample_t1} sample_t0={sample_t0} buf={last_buf!r}"
                )
                prop_latencies_ms.append((sample_t1 - sample_t0) * 1000.0)

            p50_ms = _percentile(prop_latencies_ms, 50)
            p95_ms = _percentile(prop_latencies_ms, 95)
            assert p95_ms < 500.0, f"C30 p95 propagation too slow: {p95_ms:.2f}ms"
            print(f"\n[C30] Revocation propagation latency (n={len(prop_latencies_ms)}): p50={p50_ms:.2f}ms p95={p95_ms:.2f}ms")
    finally:
        _cleanup_tenant(db_engine, tenant_id)


# ---------------------------------------------------------------------------
# 5. Slow consumer bounded buffer -> resync_required without unbounded memory
# ---------------------------------------------------------------------------


def test_slow_client_bounded_buffer_emits_resync_required(db_engine: Engine) -> None:
    """When buffered unread events exceed max_buffer_size, emit resync_required and close."""
    tenant_id = f"tenant-slow-{uuid.uuid4().hex[:8]}"
    tok_owner = "tok-slow-owner"
    principals = [
        ConfiguredPrincipal(
            tenant_id=tenant_id,
            principal_id="owner-slow",
            token_hash=SecretStr(hash_token(tok_owner)),
            roles=["owner"],
        )
    ]
    settings = _make_settings(principals=principals)
    app = create_app(settings=settings)

    def _override_db() -> Generator[Session, None, None]:
        sess = Session(bind=db_engine)
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        task_id = f"task-slow-{uuid.uuid4().hex[:8]}"
        with Session(bind=db_engine) as sess:
            TaskService.create(
                sess,
                tenant_id=tenant_id,
                task_id=task_id,
                binding_id="bind-slow",
                project_id="proj-slow",
                request=TaskRequest(
                    contract_version="1.0",
                    agent_id="agent-1",
                    task_type="code",
                    objective="Slow buffer bound test",
                    input_refs=[],
                    resource_refs=["project:proj-slow"],
                    idempotency_key=f"idem-{uuid.uuid4().hex[:10]}",
                ),
            )
            task_runs = TaskService.get_runs(sess, tenant_id=tenant_id, task_id=task_id)
            assert task_runs
            run_id = str(task_runs[0]["run_id"])
            OutboxRelayWorker(enabled=True).relay_stream_batch(
                sess.connection(),
                tenant_id=tenant_id,
                stream_id=f"run:{run_id}",
                binding_provider=lambda t_id, p_id: _make_binding(t_id, p_id, "bind-slow"),
            )
            sess.commit()

        with TestClient(app) as client:
            cursor = client.get("/v1/hud/snapshot", headers={"Authorization": f"Bearer {tok_owner}"}).json()[
                "snapshot_cursor"
            ]
            # Emit 10 events while subscriber buffer limit is 3 -> overflow must trigger resync_required.
            with db_engine.begin() as conn:
                for step in range(2, 12):
                    _emit_run_event(
                        conn,
                        tenant_id=tenant_id,
                        task_id=task_id,
                        run_id=run_id,
                        event_type="run.progress",
                        producer_seq=step,
                        payload={
                            "completed_units": step,
                            "total_units": 20,
                            "unit": "steps",
                            "stage": f"burst-{step}",
                        },
                    )

            resp = client.get(
                f"/v1/hud/events?cursor={cursor}&max_buffer_size=3",
                headers={"Authorization": f"Bearer {tok_owner}"},
            )
            assert resp.status_code == 200
            frames = [f for f in parse_sse_frames(resp.text) if f["type"] == "event"]
            assert len(frames) == 1
            assert frames[0]["event"] == "resync_required"
            assert frames[0]["data"]["reason"] == "slow_consumer"
    finally:
        _cleanup_tenant(db_engine, tenant_id)


# ---------------------------------------------------------------------------
# 6. ISS-040: Bounded reads + >= 100,000-event snapshot latency benchmark
# ---------------------------------------------------------------------------


def test_iss040_snapshot_bounded_reads_at_100k_events_benchmark(db_engine: Engine) -> None:
    """Seed >= 100,000 committed events across 100 runs and verify bounded reads + latency."""
    tenant_id = f"tenant-100k-{uuid.uuid4().hex[:8]}"
    viewer = Principal(tenant_id=tenant_id, principal_id="owner-bench", roles=["owner"])
    settings = _make_settings()
    now_dt = datetime.now(UTC)

    try:
        # Seed 100 tasks, 100 runs, 100 stream_heads, 100 projection_checkpoints, and 100,000 event_log rows.
        num_runs = 100
        events_per_run = 1000
        total_events = num_runs * events_per_run  # 100,000 events
        with db_engine.begin() as conn:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO tasks (id, tenant_id, client_idempotency_key, task_intent_key, task_type, agent_id, agent_version, project_id, state, deadline)
                    SELECT
                        :tid_prefix || g,
                        :tenant_id,
                        :idem_prefix || g,
                        :intent_prefix || g,
                        'code',
                        'agent-bench',
                        '1.0.0',
                        'proj-bench',
                        'active',
                        :deadline
                    FROM generate_series(1, :n_runs) AS g
                    """
                ),
                {
                    "tid_prefix": f"{tenant_id}-task-",
                    "tenant_id": tenant_id,
                    "idem_prefix": f"idem-{tenant_id}-",
                    "intent_prefix": f"intent-{tenant_id}-",
                    "deadline": now_dt + timedelta(days=1),
                    "n_runs": num_runs,
                },
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO runs (id, tenant_id, task_id, run_id, attempt, binding_id, project_id, state, remote_outcome)
                    SELECT
                        :rid_pk_prefix || g,
                        :tenant_id,
                        :tid_prefix || g,
                        :rid_prefix || g,
                        1,
                        'bind-bench',
                        'proj-bench',
                        'running',
                        'known'
                    FROM generate_series(1, :n_runs) AS g
                    """
                ),
                {
                    "rid_pk_prefix": f"{tenant_id}-rpk-",
                    "tenant_id": tenant_id,
                    "tid_prefix": f"{tenant_id}-task-",
                    "rid_prefix": f"{tenant_id}-run-",
                    "n_runs": num_runs,
                },
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO stream_heads (tenant_id, stream_id, last_offset, producer_state)
                    SELECT
                        :tenant_id,
                        'run:' || :rid_prefix || g,
                        :events_per_run,
                        '{}'::jsonb
                    FROM generate_series(1, :n_runs) AS g
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "rid_prefix": f"{tenant_id}-run-",
                    "events_per_run": events_per_run,
                    "n_runs": num_runs,
                },
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO projection_checkpoints (tenant_id, stream_id, projection_name, projection_version, last_ingest_offset, state, state_hash)
                    SELECT
                        CAST(:tenant_id AS text),
                        'run:' || CAST(:rid_prefix AS text) || g,
                        CAST(:proj_name AS text),
                        1,
                        CAST(:events_per_run AS integer),
                        jsonb_build_object(
                            'row',
                            jsonb_build_object(
                                'task_id', CAST(:tid_prefix AS text) || g,
                                'task_state', 'active',
                                'run_id', CAST(:rid_prefix AS text) || g,
                                'attempt', 1,
                                'run_state', 'running',
                                'remote_outcome', 'known',
                                'connectivity', 'healthy',
                                'last_observed_at', CAST(:obs_ts AS text),
                                'observation_source', 'run_service',
                                'progress', jsonb_build_object('kind', 'units', 'completed_units', CAST(:events_per_run AS integer), 'total_units', CAST(:events_per_run AS integer), 'unit', 'steps', 'stage', 'steady'),
                                'project', NULL,
                                'cost', jsonb_build_object('status', 'unknown', 'amount_usd', NULL, 'as_of', NULL, 'scope', 'run'),
                                'pending_command', NULL
                            )
                        ),
                        md5(CAST(:rid_prefix AS text) || g)
                    FROM generate_series(1, CAST(:n_runs AS integer)) AS g
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "proj_name": PROJECTION_NAME_HUD_RUNS,
                    "rid_prefix": f"{tenant_id}-run-",
                    "tid_prefix": f"{tenant_id}-task-",
                    "events_per_run": events_per_run,
                    "obs_ts": now_dt.isoformat(),
                    "n_runs": num_runs,
                },
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO event_log (tenant_id, stream_id, ingest_offset, event_id, producer_id, event_type, payload, occurred_at)
                    SELECT
                        CAST(:tenant_id AS text),
                        'run:' || CAST(:rid_prefix AS text) || (((seq - 1) / CAST(:events_per_run AS integer)) + 1),
                        ((seq - 1) % CAST(:events_per_run AS integer)) + 1,
                        CAST(:eid_prefix AS text) || seq,
                        'run_service',
                        'run.progress',
                        jsonb_build_object(
                            'task_id', CAST(:tid_prefix AS text) || (((seq - 1) / CAST(:events_per_run AS integer)) + 1),
                            'run_id', CAST(:rid_prefix AS text) || (((seq - 1) / CAST(:events_per_run AS integer)) + 1),
                            'completed_units', ((seq - 1) % CAST(:events_per_run AS integer)) + 1,
                            'total_units', CAST(:events_per_run AS integer),
                            'unit', 'steps',
                            'stage', 'bench'
                        ),
                        :occurred_at
                    FROM generate_series(1, CAST(:total_events AS integer)) AS seq
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "rid_prefix": f"{tenant_id}-run-",
                    "tid_prefix": f"{tenant_id}-task-",
                    "eid_prefix": f"ev-{tenant_id}-",
                    "events_per_run": events_per_run,
                    "occurred_at": now_dt,
                    "total_events": total_events,
                },
            )

        # Assert SQL queries emitted during take_hud_snapshot NEVER touch outbox or scan full event_log.
        executed_sql: list[str] = []

        def _capture_sql(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
            executed_sql.append(statement)

        sa_event.listen(db_engine, "before_cursor_execute", _capture_sql)
        try:
            # Warmup once, then measure 15 runs of take_hud_snapshot (after ISS-040).
            warm = take_hud_snapshot(db_engine, principal=viewer, settings=settings)
            assert len(warm["rows"]) == num_runs
            assert warm["projection_revision"] == total_events

            after_latencies_ms: list[float] = []
            for _ in range(15):
                executed_sql.clear()
                t0 = time.perf_counter()
                snap = take_hud_snapshot(db_engine, principal=viewer, settings=settings)
                after_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
                assert len(snap["rows"]) == num_runs
                assert not any("FROM outbox" in q for q in executed_sql), f"Unexpected outbox query: {executed_sql}"
                el_queries = [q for q in executed_sql if "FROM event_log" in q]
                assert el_queries and all("ORDER BY event_log.id DESC" in q for q in el_queries), el_queries

            # Measure legacy full-history load (_load_committed_event_rows) on the same 100k DB for before/after.
            before_latencies_ms: list[float] = []
            for _ in range(5):
                t0 = time.perf_counter()
                with db_engine.connect() as conn:
                    loaded = _load_committed_event_rows(conn, tenant_id=tenant_id)
                before_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
                assert len(loaded) == num_runs
        finally:
            sa_event.remove(db_engine, "before_cursor_execute", _capture_sql)

        after_p50 = _percentile(after_latencies_ms, 50)
        after_p95 = _percentile(after_latencies_ms, 95)
        before_p50 = _percentile(before_latencies_ms, 50)
        before_p95 = _percentile(before_latencies_ms, 95)
        assert after_p95 < before_p50
        print(
            f"\n[ISS-040 100k Benchmark] before(full event_log scan): p50={before_p50:.2f}ms p95={before_p95:.2f}ms | "
            f"after(bounded checkpoint+head): p50={after_p50:.2f}ms p95={after_p95:.2f}ms"
        )
    finally:
        _cleanup_tenant(db_engine, tenant_id)
