"""Test loopback-only bind host enforcement on startup.

Covers:
- parse_bind_host extracts host correctly across IPv4, IPv6 (with/without brackets), and hostnames.
- is_loopback_host accepts 127.0.0.1, ::1, localhost; rejects 0.0.0.0, ::, 192.168.x.x, 10.x.x.x, etc.
- validate_loopback_bind refuses non-loopback addresses with InsecureBindError and a clear message.
- create_app() raises InsecureBindError on startup for non-loopback addresses without opening sockets.
- create_app() succeeds for loopback addresses (127.0.0.1, ::1, localhost).
"""

from __future__ import annotations

import pytest

from eap.api.app import create_app
from eap.config import (
    InsecureBindError,
    Settings,
    is_loopback_host,
    parse_bind_host,
    validate_loopback_bind,
)


@pytest.mark.parametrize(
    ("bind_str", "expected_host"),
    [
        ("127.0.0.1:8000", "127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.5:9000", "127.0.0.5"),
        ("localhost:8000", "localhost"),
        ("localhost", "localhost"),
        ("[::1]:8000", "::1"),
        ("::1", "::1"),
        ("0.0.0.0:8000", "0.0.0.0"),
        ("0.0.0.0", "0.0.0.0"),
        ("[::]:8000", "::"),
        ("::", "::"),
        ("192.168.1.100:8000", "192.168.1.100"),
        ("192.168.0.1", "192.168.0.1"),
        ("10.0.0.1:8000", "10.0.0.1"),
    ],
)
def test_parse_bind_host(bind_str: str, expected_host: str) -> None:
    assert parse_bind_host(bind_str) == expected_host


@pytest.mark.parametrize(
    ("host", "expected_loopback"),
    [
        ("127.0.0.1", True),
        ("127.0.0.2", True),
        ("::1", True),
        ("localhost", True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.1", False),
        ("192.168.0.100", False),
        ("10.0.0.1", False),
        ("172.16.0.1", False),
        ("example.com", False),
        ("8.8.8.8", False),
    ],
)
def test_is_loopback_host(host: str, expected_loopback: bool) -> None:
    assert is_loopback_host(host) is expected_loopback


@pytest.mark.parametrize(
    "bind_str",
    [
        "127.0.0.1:8000",
        "127.0.0.1",
        "127.0.0.99:5000",
        "[::1]:8000",
        "::1",
        "localhost:8000",
        "localhost",
    ],
)
def test_validate_loopback_bind_allowed(bind_str: str) -> None:
    # Does not raise
    validate_loopback_bind(bind_str)


@pytest.mark.parametrize(
    "bind_str",
    [
        "0.0.0.0:8000",
        "0.0.0.0",
        "[::]:8000",
        "::",
        "192.168.1.1:8000",
        "192.168.0.100",
        "10.0.0.1:8000",
        "172.16.0.1:8000",
        "example.com:8000",
    ],
)
def test_validate_loopback_bind_refused(bind_str: str) -> None:
    with pytest.raises(InsecureBindError, match="not a loopback address"):
        validate_loopback_bind(bind_str)


@pytest.mark.parametrize(
    "insecure_bind",
    [
        "0.0.0.0:8000",
        "0.0.0.0",
        "192.168.1.5:8000",
        "192.168.0.1",
        "[::]:8000",
        "::",
    ],
)
def test_startup_refuses_non_loopback(insecure_bind: str) -> None:
    """Startup refuses to run if EAP_BIND host is not loopback; tested without opening sockets."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        EAP_BIND=insecure_bind,
        REDIS_URL="",
        QDRANT_URL="",
    )
    with pytest.raises(InsecureBindError) as exc_info:
        create_app(settings)
    assert "not a loopback address" in str(exc_info.value)
    assert "loopback only: 127.0.0.1, ::1, localhost" in str(exc_info.value)


@pytest.mark.parametrize(
    "loopback_bind",
    [
        "127.0.0.1:8000",
        "[::1]:8000",
        "localhost:8000",
    ],
)
def test_startup_allows_loopback(loopback_bind: str) -> None:
    """Startup succeeds when EAP_BIND host is loopback."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        EAP_BIND=loopback_bind,
        REDIS_URL="",
        QDRANT_URL="",
    )
    app = create_app(settings)
    assert app is not None
