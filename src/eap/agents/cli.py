"""Local MVP CLI for agent inventory registration (fixtures/admin only, loopback, owner principal)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from eap.agents.service import AgentServiceError, register_agent_and_binding
from eap.config import InsecureBindError, Settings, get_settings, validate_loopback_bind
from eap.db import get_engine
from eap.observability.logging import redact_text


def _is_owner_principal(principal_id: str, settings: Settings) -> bool:
    return (
        principal_id == settings.eap_owner_principal_id
        or principal_id == "owner"
        or principal_id.startswith(("owner-", "principal-owner"))
    )


def _emit_error(*, code: str, message: str, field_path: str | None = None) -> None:
    payload: dict[str, Any] = {
        "error": {
            "code": code,
            "message": redact_text(message),
            "field_path": field_path,
        }
    }
    print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{label} file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{label} JSON root must be an object: {path}")
    return raw


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eap.agents.cli",
        description="EAP Agent Inventory CLI (local MVP fixtures/admin loader).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    reg = sub.add_parser(
        "register",
        help="Validate and idempotently register an agent manifest and deployment binding.",
    )
    reg.add_argument("--manifest", required=True, help="Path to agent.json manifest file.")
    reg.add_argument(
        "--binding",
        required=False,
        default=None,
        help="Optional path to deployment binding JSON file.",
    )
    reg.add_argument(
        "--tenant",
        required=False,
        default=None,
        help="Owner tenant ID (defaults to settings.eap_owner_tenant_id).",
    )
    reg.add_argument(
        "--principal",
        required=False,
        default=None,
        help="Owner principal ID (defaults to settings.eap_owner_principal_id).",
    )
    reg.add_argument(
        "--database-url",
        required=False,
        default=None,
        help="Override database URL (defaults to EAP settings database_url).",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    conn_or_session: Engine | sa.Connection | Session | None = None,
    settings: Settings | None = None,
) -> int:
    """CLI entrypoint for `python -m eap.agents.cli`."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    cfg = settings or get_settings()

    if args.command != "register":
        _emit_error(code="UNSUPPORTED_COMMAND", message=f"Unknown command: {args.command}")
        return 1

    # Enforce local MVP guardrails: loopback bind + fixtures/admin-only + owner principal.
    try:
        validate_loopback_bind(cfg.eap_bind)
    except (InsecureBindError, ValueError) as exc:
        _emit_error(
            code="LOOPBACK_BIND_REQUIRED",
            message=f"Agent CLI loader requires loopback EAP_BIND: {exc}",
        )
        return 1

    if cfg.eap_collector_allow_real_roots:
        _emit_error(
            code="FIXTURES_ONLY_MODE_REQUIRED",
            message="Agent CLI loader is restricted to fixtures/admin mode (EAP_COLLECTOR_ALLOW_REAL_ROOTS must be false).",
        )
        return 1

    principal_id = str(args.principal or cfg.eap_owner_principal_id)
    if not _is_owner_principal(principal_id, cfg):
        _emit_error(
            code="OWNER_PRINCIPAL_REQUIRED",
            message=f"Principal '{principal_id}' is not authorized; owner principal required.",
            field_path="/principal_id",
        )
        return 1

    tenant_id = str(args.tenant or cfg.eap_owner_tenant_id)
    if args.tenant and args.tenant != cfg.eap_owner_tenant_id:
        _emit_error(
            code="TENANT_MISMATCH",
            message=f"CLI local loader only permits owner tenant '{cfg.eap_owner_tenant_id}', got '{args.tenant}'.",
            field_path="/tenant_id",
        )
        return 1

    try:
        manifest = _load_json_object(Path(args.manifest), label="manifest")
        binding = (
            _load_json_object(Path(args.binding), label="binding")
            if args.binding is not None
            else None
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        _emit_error(code="INVALID_INPUT_FILE", message=str(exc))
        return 1

    owned_engine: Engine | None = None
    target: Engine | sa.Connection | Session
    if conn_or_session is not None:
        target = conn_or_session
    else:
        db_url: str | None
        if args.database_url is not None:
            db_url = str(args.database_url)
        elif isinstance(cfg.database_url, SecretStr):
            db_url = cfg.database_url.get_secret_value()
        else:
            db_url = cfg.database_url
        if not db_url:
            _emit_error(
                code="DATABASE_URL_REQUIRED",
                message="DATABASE_URL (or --database-url) must be configured for CLI registration.",
            )
            return 1
        owned_engine = get_engine(db_url)
        target = owned_engine

    try:
        result = register_agent_and_binding(
            target,
            tenant_id=tenant_id,
            manifest=manifest,
            binding=binding,
        )
    except AgentServiceError as exc:
        _emit_error(code=exc.code, message=exc.message, field_path=exc.field_path)
        return 1
    finally:
        if owned_engine is not None:
            owned_engine.dispose()

    print(
        json.dumps(
            {
                "ok": True,
                "principal_id": principal_id,
                **result,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
