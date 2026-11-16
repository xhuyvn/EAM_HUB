"""Tests for MVP1-WP1 local deploy package, worker cycle, fixture seeder, and security invariants."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
import yaml
from sqlalchemy.orm import sessionmaker

from eap.config import Settings
from eap.mvp1_fixture import seed_mvp1_fixtures
from eap.worker import run_worker_cycle

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_compose_loopback_ports_and_profiles() -> None:
    compose = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert "postgres" in services
    assert "profiles" not in services["postgres"]
    assert services["redis"]["profiles"] == ["optional"]
    assert services["qdrant"]["profiles"] == ["optional"]

    for name in ("migrate", "api", "worker", "hud"):
        assert name in services
        assert services[name]["profiles"] == ["mvp1"]

    for name, svc in services.items():
        for port in svc.get("ports", []):
            port_str = str(port)
            assert port_str.startswith("127.0.0.1:"), f"Service {name} port {port_str} must bind to 127.0.0.1"
            assert "0.0.0.0" not in port_str


def test_dockerfiles_non_root_and_pinned_images() -> None:
    for rel in ("Dockerfile", "web/hud/Dockerfile"):
        content = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "USER " in content, f"{rel} must set a non-root USER"
        assert "USER root" not in content
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("FROM "):
                assert ":latest" not in stripped, f"{rel} must pin image tags, not :latest"

    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    for pattern in (".env", ".git", "node_modules", "tests"):
        assert pattern in dockerignore


def test_mvp1_reset_requires_confirmation() -> None:
    if sys.platform == "win32":
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPO_ROOT / "scripts/mvp1_reset.ps1")],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        res = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts/mvp1_reset.sh")],
            capture_output=True,
            text=True,
            check=False,
        )
    assert res.returncode != 0
    assert "REFUSED" in (res.stdout + res.stderr)


def test_mvp1_fixture_and_worker_cycle_idempotent(db_engine: sa.Engine) -> None:
    session_factory = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    settings = Settings.model_validate(
        {
            "EAP_ENV": "test",
            "EAP_BIND": "127.0.0.1:8000",
            "DATABASE_URL": os.environ.get("TEST_DATABASE_URL"),
            "PPM_SCHEDULER_ENABLED": False,
            "PPM_ORCHESTRATOR_ENABLED": True,
        }
    )
    with session_factory() as db:
        first = seed_mvp1_fixtures(
            db,
            tenant_id=settings.eap_owner_tenant_id,
            principal_id=settings.eap_owner_principal_id,
        )
        second = seed_mvp1_fixtures(
            db,
            tenant_id=settings.eap_owner_tenant_id,
            principal_id=settings.eap_owner_principal_id,
        )
    assert first["assessments"] == 2
    assert second["assessments"] == 2

    stats = run_worker_cycle(db_engine, session_factory, settings=settings)
    assert stats.runs_dispatched >= 0
    assert stats.streams_relayed >= 0

    second_stats = run_worker_cycle(db_engine, session_factory, settings=settings)
    assert second_stats.runs_dispatched == 0


@pytest.fixture
def db_engine():
    test_url = os.environ.get("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL not set")
    engine = sa.create_engine(test_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except sa.exc.SQLAlchemyError:
        engine.dispose()
        pytest.skip("PostgreSQL not reachable at TEST_DATABASE_URL")
    try:
        yield engine
    finally:
        engine.dispose()
