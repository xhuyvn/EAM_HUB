"""Tests for GET /v1/hud/runs/{run_id}/inspector (P3a-WP3)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from eap.api.app import create_app
from eap.api.dependencies import get_db
from eap.config import ConfiguredPrincipal, Settings, get_settings
from eap.db.tables import artifacts, commands, event_log, runs, tasks
from eap.policy.grants import revoke_project_access

OWNER_TOKEN = "hud-inspector-owner-token"
SECOND_TOKEN = "hud-inspector-second-token"


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _make_settings() -> Settings:
    return Settings.model_validate(
        {
            "eap_env": "test",
            "eap_bind": "127.0.0.1:8000",
            "eap_owner_tenant_id": "tenant-a",
            "eap_owner_principal_id": "owner-a",
            "eap_owner_token_hash": SecretStr(_token_hash(OWNER_TOKEN)),
            "eap_second_tenant_id": "tenant-b",
            "eap_second_principal_id": "owner-b",
            "eap_second_token_hash": SecretStr(_token_hash(SECOND_TOKEN)),
            "eap_secret_canary": SecretStr("CANARY_INSPECTOR_SECRET"),
            "local_principals": [
                ConfiguredPrincipal(
                    tenant_id="tenant-a",
                    principal_id="viewer-a",
                    token_hash=SecretStr(_token_hash("viewer-a-token")),
                    roles=["admin"],
                )
            ],
            "hud_cursor_secret": SecretStr("hud-inspector-test-cursor-secret-32b"),
        }
    )


def _make_client(db_session: Session, settings: Settings) -> TestClient:
    app = create_app(settings)

    def _override_db() -> Any:
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def test_run_inspector_unknown_outcome_redaction_and_isolation(db_session: Session) -> None:
    settings = _make_settings()
    client = _make_client(db_session, settings)
    now = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)

    db_session.execute(
        tasks.insert().values(
            id="task-insp-1",
            tenant_id="tenant-a",
            project_id="proj-insp",
            client_idempotency_key="idem-insp-1",
            task_intent_key="intent-insp-1",
            agent_id="agent-mock-provider",
            agent_version="1.0.0",
            task_type="code.patch",
            state="active",
            created_at=now,
            updated_at=now,
            deadline=now + timedelta(hours=1),
        )
    )
    db_session.execute(
        runs.insert().values(
            id="r-insp-1",
            run_id="run-insp-1",
            tenant_id="tenant-a",
            task_id="task-insp-1",
            project_id="proj-insp",
            attempt=1,
            binding_id="binding-mock-provider-local",
            state="unknown",
            remote_outcome="unknown",
            lease_fencing_token=1,
            created_at=now,
            updated_at=now,
        )
    )
    db_session.execute(
        commands.insert().values(
            id="cmd-insp-1",
            tenant_id="tenant-a",
            task_id="task-insp-1",
            run_id="run-insp-1",
            command_type="cancel",
            status="accepted",
            payload={"reason": "operator_cancel"},
            created_at=now,
            updated_at=now,
        )
    )
    db_session.execute(
        event_log.insert().values(
            event_id="ev-insp-1",
            tenant_id="tenant-a",
            stream_id="run:run-insp-1",
            ingest_offset=1,
            producer_id="mock-provider",
            event_type="run.state_changed",
            payload={"to_state": "unknown", "note": "secret=CANARY_INSPECTOR_SECRET"},
            occurred_at=now,
            ingested_at=now,
        )
    )
    db_session.execute(
        artifacts.insert().values(
            id="art-pk-1",
            artifact_id="art-insp-1",
            tenant_id="tenant-a",
            task_id="task-insp-1",
            run_id="run-insp-1",
            content_hash="abc123",
            content_type="application/json",
            size_bytes=42,
            storage_uri="file://artifacts/art-insp-1.json",
            created_at=now,
        )
    )
    db_session.flush()

    resp = client.get(
        "/v1/hud/runs/run-insp-1/inspector",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["run"]["run_id"] == "run-insp-1"
    assert data["run"]["state"] == "unknown"
    assert data["run"]["remote_outcome"] == "unknown"
    assert len(data["attempts"]) == 1
    assert len(data["commands"]) == 1
    assert data["commands"][0]["command_type"] == "cancel"
    assert len(data["events"]) == 1
    assert "CANARY_INSPECTOR_SECRET" not in str(data["events"][0]["payload"])
    assert len(data["artifacts"]) == 1
    assert data["artifacts"][0]["signed_url"] is None
    assert data["artifacts"][0]["access_url"] == "/v1/artifacts/art-insp-1"
    assert data["cost"]["status"] == "unknown"
    assert data["cost"]["amount_usd"] is None

    # Cross-tenant returns 404
    cross = client.get(
        "/v1/hud/runs/run-insp-1/inspector",
        headers={"Authorization": f"Bearer {SECOND_TOKEN}"},
    )
    assert cross.status_code == 404

    # Revoked project access returns 403
    revoke_project_access(
        db_session.connection(),
        tenant_id="tenant-a",
        principal_id="viewer-a",
        project_id="proj-insp",
        actor_id="owner-a",
        relay_immediately=False,
    )
    db_session.flush()
    revoked = client.get(
        "/v1/hud/runs/run-insp-1/inspector",
        headers={"Authorization": "Bearer viewer-a-token"},
    )
    assert revoked.status_code == 403
