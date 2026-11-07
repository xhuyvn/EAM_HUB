"""Pytest configuration and database fixtures for EAP."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

from eap.config import get_settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def test_db_url() -> str:
    """Retrieve and validate TEST_DATABASE_URL.

    Skips tests if unset. Enforces that tests never touch eap_dev.
    """
    settings = get_settings()
    url_secret = settings.test_database_url
    if url_secret is None:
        url_str = os.environ.get("TEST_DATABASE_URL")
    else:
        url_str = url_secret.get_secret_value()

    if not url_str or not url_str.strip():
        pytest.skip(
            "TEST_DATABASE_URL is not set; skipping database tests",
            allow_module_level=True,
        )

    # Invariant: tests never touch eap_dev
    url = make_url(url_str)
    if url.database == "eap_dev":
        raise RuntimeError("TEST_DATABASE_URL must never point to eap_dev")
    if url.database != "eap_test":
        raise RuntimeError(f"TEST_DATABASE_URL must point to eap_test, got {url.database}")

    return url_str


@pytest.fixture(scope="session")
def db_engine(test_db_url: str) -> Generator[Engine, None, None]:
    """Create and drop eap_test per pytest session, migrated to head."""
    url = make_url(test_db_url)
    maint_url = url.set(database="postgres")

    # Connect to maintenance database and create clean eap_test
    maint_engine = create_engine(maint_url, isolation_level="AUTOCOMMIT")
    with maint_engine.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS eap_test WITH (FORCE)"))
        conn.execute(text("CREATE DATABASE eap_test OWNER eap"))
    maint_engine.dispose()

    # Run Alembic migrations on eap_test
    alembic_cfg = Config(str(ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", test_db_url.replace("%", "%%"))
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(test_db_url, pool_pre_ping=True)
    yield engine

    # Teardown: dispose engine and drop eap_test
    engine.dispose()
    maint_engine = create_engine(maint_url, isolation_level="AUTOCOMMIT")
    with maint_engine.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS eap_test WITH (FORCE)"))
    maint_engine.dispose()


@pytest.fixture
def db_conn(db_engine: Engine) -> Generator[sa.Connection, None, None]:
    """Provide a database connection wrapped in a rolled-back transaction."""
    with db_engine.connect() as conn:
        trans = conn.begin()
        yield conn
        trans.rollback()
