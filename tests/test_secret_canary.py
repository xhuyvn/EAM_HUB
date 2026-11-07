"""Test SC-04 secret canary and credential redaction."""

import logging
import uuid

import pytest
from pydantic import SecretStr

from eap.config import Settings


def test_canary_is_never_leaked_in_repr():
    canary = f"EAP-CANARY-{uuid.uuid4()}"
    secret_pw = "my-super-secret-password"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        EAP_SECRET_CANARY=SecretStr(canary),
        POSTGRES_PASSWORD=SecretStr(secret_pw),
    )
    s_repr = repr(settings)
    assert canary not in s_repr, "Canary leaked in settings repr"
    assert secret_pw not in s_repr, "Password leaked in settings repr"


def test_dsn_password_not_in_repr_or_str():
    """Case (a): repr/str of Settings with a DSN containing a password does not contain the password."""
    secret_pw = "super-secret-db-pass-xyz"
    dsn = f"postgresql+psycopg://eap:{secret_pw}@127.0.0.1:5432/eap_dev"
    settings = Settings(
        _env_file=None,
        DATABASE_URL=SecretStr(dsn),
    )
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == dsn

    s_repr = repr(settings)
    s_str = str(settings)
    assert secret_pw not in s_repr, f"Password leaked in settings repr: {s_repr}"
    assert secret_pw not in s_str, f"Password leaked in settings str: {s_str}"


def test_caplog_logging_does_not_leak_secrets(caplog: pytest.LogCaptureFixture):
    """Case (b): logging.info('%s', settings) and logging.info('%r', settings) contain neither canary nor password."""
    canary = f"EAP-CANARY-{uuid.uuid4()}"
    secret_pw = "logging-secret-pw-999"
    dsn = f"postgresql+psycopg://eap:{secret_pw}@127.0.0.1:5432/eap_dev"

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        EAP_SECRET_CANARY=SecretStr(canary),
        POSTGRES_PASSWORD=SecretStr(secret_pw),
        DATABASE_URL=SecretStr(dsn),
    )

    with caplog.at_level(logging.INFO):
        logging.info("%s", settings)  # noqa: LOG015
        logging.info("%r", settings)  # noqa: LOG015

    captured_logs = caplog.text
    assert canary not in captured_logs, f"Canary leaked in logs: {captured_logs}"
    assert secret_pw not in captured_logs, f"Password leaked in logs: {captured_logs}"
