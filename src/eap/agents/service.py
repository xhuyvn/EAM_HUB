"""Agent inventory service: contract validation, inline secret rejection, registration, and derived health (P3a-WP5, ISS-037)."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.orm import Session

from eap.contracts.digest import sha256_digest
from eap.contracts.models.agent import Agent
from eap.contracts.models.binding import Binding
from eap.contracts.rules import ContractViolation, check_binding_rules
from eap.db import agent_bindings, agents, dispatch_intents, runs, tasks, usage_ledger
from eap.observability.logging import register_secret

logger = logging.getLogger("eap.agents.service")

_CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"

# Secret detection patterns for reference-only fields (never allow inline secret material).
_RE_API_KEY_PREFIX = re.compile(
    r"(?:sk-[A-Za-z0-9_.-]{4,}|"
    r"(?:pk|rk)_(?:live|test)_[A-Za-z0-9_.-]{4,}|"
    r"gh[pousr]_[A-Za-z0-9_]{6,}|"
    r"xox[baprs]-[A-Za-z0-9-]+|"
    r"AKIA[0-9A-Z]{16}|"
    r"AIza[0-9A-Za-z_-]{10,}|"
    r"EAP-CANARY-[A-Za-z0-9_-]+|"
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)
_RE_KV_EQUALS_SECRET = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|client[_-]?secret|private[_-]?key|token|bearer)\s*="
)
_RE_KV_COLON_SECRET = re.compile(
    r"(?i)(?:^|[\s,{])(?:password|passwd|pwd|api[_-]?key|access[_-]?key|client[_-]?secret|private[_-]?key)\s*:\s*\S+"
)
_RE_SECRET_COLON_INLINE = re.compile(r"(?i)(?:^|[\s,{])secret:\s+\S+")
_RE_TOKEN_COLON_INLINE = re.compile(r"(?i)(?:^|[\s,{])token:\s+\S+")
_RE_URL_USERINFO = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/\s@:]+)(?::([^/\s@]*))?@")
_RE_AUTH_SCHEME = re.compile(r"(?i)\b(?:Bearer|Basic)\s+\S+")
_RE_PEM_MARKER = re.compile(r"-----BEGIN\b")

_SINGLE_REF_FIELDS: tuple[str, ...] = (
    "secret_ref",
    "endpoint_ref",
    "capability_evidence_ref",
    "egress_policy_ref",
    "model_profile_ref",
    "embedding_profile_ref",
)
_LIST_REF_FIELDS: tuple[str, ...] = (
    "fallback_model_profile_refs",
    "grant_refs",
    "resource_scope_refs",
)


class AgentServiceError(Exception):
    """Base error for Agent Inventory operations."""

    def __init__(self, code: str, message: str, *, field_path: str | None = None) -> None:
        self.code = code
        self.message = message
        self.field_path = field_path
        super().__init__(f"{code} ({field_path}): {message}" if field_path else f"{code}: {message}")


class AgentValidationError(AgentServiceError):
    """Raised when an agent manifest or binding violates schema or semantic rules."""


class InlineSecretError(AgentValidationError):
    """Raised when a reference field contains an inline secret instead of a reference."""

    def __init__(self, field_path: str, message: str | None = None) -> None:
        super().__init__(
            "INLINE_SECRET_REJECTED",
            message
            or f"Inline secret or credential material rejected in '{field_path}'; provide an external reference only.",
            field_path=field_path,
        )


class AgentVersionImmutableError(AgentValidationError):
    """Raised when re-registering an existing agent version with a modified manifest."""

    def __init__(self, agent_id: str, version: str) -> None:
        super().__init__(
            "AGENT_VERSION_IMMUTABLE",
            f"Agent '{agent_id}' version '{version}' is immutable; modified manifest with same version rejected.",
            field_path="/version",
        )


class AgentNotFoundError(AgentServiceError):
    """Raised when an agent does not exist in the caller's tenant scope."""

    def __init__(self, agent_id: str) -> None:
        super().__init__("AGENT_NOT_FOUND", f"Agent '{agent_id}' not found", field_path="/agent_id")


def _to_iso_utc(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dt.datetime):
        aware = value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)
        return aware.isoformat().replace("+00:00", "Z")
    return str(value)


def _load_schema(filename: str) -> dict[str, Any]:
    path = _CONTRACTS_DIR / filename
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _extract_jsonschema_field_path(err: jsonschema.ValidationError) -> str:
    parts = [str(p) for p in err.path]
    if err.validator == "required":
        m = re.search(r"'([^']+)' is a required property", err.message)
        if m:
            parts.append(m.group(1))
    elif err.validator == "additionalProperties":
        m = re.search(r"'([^']+)'", err.message)
        if m:
            parts.append(m.group(1))
    return "/" + "/".join(parts) if parts else "/"


def _extract_pydantic_field_path(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "/"
    loc = errors[0].get("loc", ())
    if not loc:
        return "/"
    return "/" + "/".join(str(p) for p in loc)


def _looks_like_inline_secret(value: str) -> bool:
    if "\n" in value or "\r" in value:
        return True
    if _RE_API_KEY_PREFIX.search(value):
        return True
    if _RE_KV_EQUALS_SECRET.search(value):
        return True
    if _RE_URL_USERINFO.search(value):
        return True
    if _RE_AUTH_SCHEME.search(value):
        return True
    if _RE_PEM_MARKER.search(value):
        return True
    if _RE_KV_COLON_SECRET.search(value):
        return True
    return bool(_RE_SECRET_COLON_INLINE.search(value) or _RE_TOKEN_COLON_INLINE.search(value))


def _register_detected_secret_safely(raw_value: str) -> None:
    """Register inline secret substrings immediately so logging redaction masks them."""
    register_secret(raw_value)
    userinfo_match = _RE_URL_USERINFO.search(raw_value)
    if userinfo_match:
        if userinfo_match.group(1):
            register_secret(userinfo_match.group(1))
        if userinfo_match.group(2):
            register_secret(userinfo_match.group(2))
    for match in _RE_KV_EQUALS_SECRET.finditer(raw_value):
        tail = raw_value[match.end() :].split("&", 1)[0].strip()
        if tail:
            register_secret(tail)


def validate_no_inline_secrets(binding: Mapping[str, Any]) -> None:
    """Reject any binding whose reference fields look like inline secrets.

    Never echoes or logs the rejected value.
    """

    def _check(field_path: str, val: Any) -> None:
        if not isinstance(val, str):
            return
        if _looks_like_inline_secret(val):
            _register_detected_secret_safely(val)
            logger.warning(
                "Rejected inline secret in binding reference field '%s' (code=%s)",
                field_path,
                "INLINE_SECRET_REJECTED",
            )
            raise InlineSecretError(field_path=field_path)

    for field_name in _SINGLE_REF_FIELDS:
        if field_name in binding:
            _check(f"/{field_name}", binding[field_name])

    for list_field in _LIST_REF_FIELDS:
        raw_list = binding.get(list_field)
        if isinstance(raw_list, list):
            for idx, item in enumerate(raw_list):
                _check(f"/{list_field}/{idx}", item)


def validate_agent_manifest(manifest: Mapping[str, Any]) -> Agent:
    """Validate an agent manifest dict against contracts/agent.schema.json and Agent model."""
    manifest_dict = dict(manifest)
    schema = _load_schema("agent.schema.json")
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(manifest_dict), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        raise AgentValidationError(
            "CONTRACT_VALIDATION_FAILED",
            first.message,
            field_path=_extract_jsonschema_field_path(first),
        )
    try:
        return Agent.model_validate(manifest_dict)
    except ValidationError as exc:
        raise AgentValidationError(
            "CONTRACT_VALIDATION_FAILED",
            str(exc),
            field_path=_extract_pydantic_field_path(exc),
        ) from exc


def validate_binding_payload(
    binding: Mapping[str, Any],
    *,
    agent_manifest: Mapping[str, Any] | Agent | None = None,
) -> Binding:
    """Validate a binding dict against inline secret rules, binding.schema.json, and semantic rules."""
    binding_dict = dict(binding)

    # Check inline secrets FIRST before any schema/rule diagnostics can inspect values.
    validate_no_inline_secrets(binding_dict)

    schema = _load_schema("binding.schema.json")
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(binding_dict), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        raise AgentValidationError(
            "CONTRACT_VALIDATION_FAILED",
            first.message,
            field_path=_extract_jsonschema_field_path(first),
        )

    try:
        model = Binding.model_validate(binding_dict)
    except ValidationError as exc:
        raise AgentValidationError(
            "CONTRACT_VALIDATION_FAILED",
            str(exc),
            field_path=_extract_pydantic_field_path(exc),
        ) from exc

    try:
        check_binding_rules(model)
    except ContractViolation as exc:
        field_path = "/"
        if exc.code == "OBSERVE_ADVERTISES_CONTROL":
            field_path = "/certified_capabilities"
        elif exc.code == "GOVERNED_REQUIRES_ENFORCEMENT_BOUNDARY":
            field_path = "/execution_boundary"
        elif exc.code == "FALLBACK_REPEATS_PRIMARY":
            field_path = "/fallback_model_profile_refs"
        raise AgentValidationError(exc.code, exc.detail, field_path=field_path) from exc

    if agent_manifest is not None:
        m_dict = (
            agent_manifest.model_dump(mode="json")
            if isinstance(agent_manifest, Agent)
            else dict(agent_manifest)
        )
        desired_caps = m_dict.get("desired_capabilities") or {}
        cert_caps = binding_dict.get("certified_capabilities") or {}
        for cap_name, enabled in cert_caps.items():
            if enabled and not desired_caps.get(cap_name, False):
                raise AgentValidationError(
                    "CAPABILITY_EXCEEDS_MANIFEST",
                    f"Binding certifies capability '{cap_name}' not requested in agent manifest",
                    field_path=f"/certified_capabilities/{cap_name}",
                )

    return model


@contextmanager
def _conn_context(conn_or_session: Engine | sa.Connection | Session) -> Iterator[sa.Connection]:
    if isinstance(conn_or_session, Session):
        yield conn_or_session.connection()
    elif isinstance(conn_or_session, sa.Connection):
        yield conn_or_session
    else:
        with conn_or_session.begin() as conn:
            yield conn


def register_agent_and_binding(
    conn_or_session: Engine | sa.Connection | Session,
    *,
    tenant_id: str,
    manifest: Mapping[str, Any],
    binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and idempotently register an agent manifest and optional binding for tenant_id.

    - Manifest and binding must validate against contracts/agent.schema.json and binding.schema.json.
    - Inline secrets in binding ref fields are rejected without storing or logging secrets.
    - Identical manifest (same RFC 8785 JCS sha256) is idempotent (no duplicate row).
    - Modified manifest with the same version raises AgentVersionImmutableError.
    """
    if binding is not None:
        # Check inline secrets before anything else runs.
        validate_no_inline_secrets(binding)

    agent_model = validate_agent_manifest(manifest)
    manifest_dict = agent_model.model_dump(mode="json")
    manifest_sha256 = sha256_digest(manifest_dict)

    binding_dict: dict[str, Any] | None = None
    if binding is not None:
        binding_model = validate_binding_payload(binding, agent_manifest=agent_model)
        binding_dict = binding_model.model_dump(mode="json")
        if binding_dict["agent_id"] != manifest_dict["agent_id"]:
            raise AgentValidationError(
                "BINDING_AGENT_MISMATCH",
                f"Binding agent_id '{binding_dict['agent_id']}' does not match manifest '{manifest_dict['agent_id']}'",
                field_path="/agent_id",
            )
        if binding_dict["release_version"] != manifest_dict["version"]:
            raise AgentValidationError(
                "BINDING_VERSION_MISMATCH",
                f"Binding release_version '{binding_dict['release_version']}' does not match manifest version '{manifest_dict['version']}'",
                field_path="/release_version",
            )
        if binding_dict["release_sha256"] != manifest_sha256:
            raise AgentValidationError(
                "BINDING_RELEASE_SHA_MISMATCH",
                "Binding release_sha256 does not match canonical SHA-256 of agent manifest",
                field_path="/release_sha256",
            )

    with _conn_context(conn_or_session) as conn:
        existing_agent = (
            conn.execute(
                sa.select(agents).where(
                    agents.c.tenant_id == tenant_id,
                    agents.c.agent_id == manifest_dict["agent_id"],
                )
            )
            .mappings()
            .one_or_none()
        )

        # Also verify no prior binding already locked a different manifest digest for this version.
        prior_version_bindings = (
            conn.execute(
                sa.select(agent_bindings.c.release_sha256).where(
                    agent_bindings.c.tenant_id == tenant_id,
                    agent_bindings.c.agent_id == manifest_dict["agent_id"],
                    agent_bindings.c.release_version == manifest_dict["version"],
                )
            )
            .scalars()
            .all()
        )
        for prior_sha in prior_version_bindings:
            if prior_sha != manifest_sha256:
                raise AgentVersionImmutableError(manifest_dict["agent_id"], manifest_dict["version"])

        owner_refs = {
            "business_owner_ref": manifest_dict["business_owner_ref"],
            "technical_owner_ref": manifest_dict["technical_owner_ref"],
            "verification_status": "unverified",
            "verification_label": "unverified owner",
        }

        agent_created = False
        agent_status = "active" if binding_dict is not None else "proposed"

        if existing_agent is None:
            conn.execute(
                agents.insert().values(
                    tenant_id=tenant_id,
                    agent_id=manifest_dict["agent_id"],
                    manifest=manifest_dict,
                    manifest_sha256=manifest_sha256,
                    name=manifest_dict["name"],
                    version=manifest_dict["version"],
                    domain=manifest_dict["domain"],
                    owner_refs=owner_refs,
                    runtime_kind=manifest_dict["runtime_kind"],
                    task_types=manifest_dict["task_types"],
                    status=agent_status,
                )
            )
            agent_created = True
        elif existing_agent["manifest_sha256"] == manifest_sha256:
            # Idempotent re-registration of the exact same manifest digest.
            if binding_dict is not None and existing_agent["status"] == "proposed":
                conn.execute(
                    agents.update()
                    .where(
                        agents.c.tenant_id == tenant_id,
                        agents.c.agent_id == manifest_dict["agent_id"],
                    )
                    .values(status="active")
                )
                agent_status = "active"
            else:
                agent_status = str(existing_agent["status"])
        elif existing_agent["version"] == manifest_dict["version"]:
            raise AgentVersionImmutableError(manifest_dict["agent_id"], manifest_dict["version"])
        else:
            # Newer version upgrade of existing agent_id in registry.
            conn.execute(
                agents.update()
                .where(
                    agents.c.tenant_id == tenant_id,
                    agents.c.agent_id == manifest_dict["agent_id"],
                )
                .values(
                    manifest=manifest_dict,
                    manifest_sha256=manifest_sha256,
                    name=manifest_dict["name"],
                    version=manifest_dict["version"],
                    domain=manifest_dict["domain"],
                    owner_refs=owner_refs,
                    runtime_kind=manifest_dict["runtime_kind"],
                    task_types=manifest_dict["task_types"],
                    status="active" if binding_dict is not None else str(existing_agent["status"]),
                )
            )
            agent_status = "active" if binding_dict is not None else str(existing_agent["status"])

        binding_created = False
        binding_id: str | None = None
        if binding_dict is not None:
            binding_id = str(binding_dict["binding_id"])
            binding_sha256 = sha256_digest(binding_dict)
            existing_binding = (
                conn.execute(
                    sa.select(agent_bindings).where(
                        agent_bindings.c.tenant_id == tenant_id,
                        agent_bindings.c.binding_id == binding_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing_binding is None:
                conn.execute(
                    agent_bindings.insert().values(
                        tenant_id=tenant_id,
                        binding_id=binding_id,
                        agent_id=manifest_dict["agent_id"],
                        payload=binding_dict,
                        release_version=binding_dict["release_version"],
                        release_sha256=binding_dict["release_sha256"],
                        mode=binding_dict["mode"],
                        execution_boundary=binding_dict["execution_boundary"],
                        certified_capabilities=binding_dict["certified_capabilities"],
                        capability_evidence_ref=binding_dict["capability_evidence_ref"],
                        egress_policy_ref=binding_dict["egress_policy_ref"],
                        secret_ref=binding_dict["secret_ref"],
                        endpoint_ref=binding_dict["endpoint_ref"],
                    )
                )
                binding_created = True
            else:
                existing_payload_sha = sha256_digest(existing_binding["payload"])
                if existing_payload_sha != binding_sha256:
                    raise AgentValidationError(
                        "BINDING_IMMUTABLE",
                        f"Binding '{binding_id}' already exists with a different payload",
                        field_path="/binding_id",
                    )

    return {
        "tenant_id": tenant_id,
        "agent_id": manifest_dict["agent_id"],
        "version": manifest_dict["version"],
        "manifest_sha256": manifest_sha256,
        "status": agent_status,
        "agent_created": agent_created,
        "binding_id": binding_id,
        "binding_created": binding_created,
    }


def revoke_binding(
    conn_or_session: Engine | sa.Connection | Session,
    *,
    tenant_id: str,
    binding_id: str,
    revoked_at: dt.datetime | None = None,
) -> dict[str, Any]:
    """Mark an existing binding as revoked within tenant scope."""
    ts = revoked_at or dt.datetime.now(dt.UTC)
    with _conn_context(conn_or_session) as conn:
        row = (
            conn.execute(
                sa.select(agent_bindings).where(
                    agent_bindings.c.tenant_id == tenant_id,
                    agent_bindings.c.binding_id == binding_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise AgentServiceError(
                "BINDING_NOT_FOUND",
                f"Binding '{binding_id}' not found",
                field_path="/binding_id",
            )
        effective_revoked = row["revoked_at"] or ts
        if row["revoked_at"] is None:
            conn.execute(
                agent_bindings.update()
                .where(
                    agent_bindings.c.tenant_id == tenant_id,
                    agent_bindings.c.binding_id == binding_id,
                )
                .values(revoked_at=effective_revoked)
            )
    return {
        "tenant_id": tenant_id,
        "binding_id": binding_id,
        "agent_id": str(row["agent_id"]),
        "status": "revoked",
        "revoked_at": _to_iso_utc(effective_revoked),
    }


def _resolve_window_bounds(
    *,
    window_hours: int | None = None,
    window_start: dt.datetime | None = None,
    window_end: dt.datetime | None = None,
) -> tuple[dt.datetime | None, dt.datetime | None]:
    end_dt = window_end
    start_dt = window_start
    if window_hours is not None and start_dt is None:
        anchor = end_dt or dt.datetime.now(dt.UTC)
        start_dt = anchor - dt.timedelta(hours=window_hours)
    return start_dt, end_dt


def _load_tenant_run_attribution_data(
    conn: sa.Connection,
    *,
    tenant_id: str,
    window_start: dt.datetime | None = None,
    window_end: dt.datetime | None = None,
) -> dict[str, Any]:
    run_stmt = (
        sa.select(
            runs.c.run_id,
            runs.c.task_id,
            runs.c.attempt,
            runs.c.binding_id,
            runs.c.state,
            runs.c.remote_outcome,
            runs.c.created_at,
            runs.c.updated_at,
            tasks.c.agent_id.label("task_agent_id"),
        )
        .select_from(
            runs.join(
                tasks,
                sa.and_(
                    tasks.c.tenant_id == runs.c.tenant_id,
                    tasks.c.id == runs.c.task_id,
                ),
            )
        )
        .where(runs.c.tenant_id == tenant_id)
        .order_by(runs.c.created_at.desc(), runs.c.id.desc())
    )
    if window_start is not None:
        run_stmt = run_stmt.where(runs.c.created_at >= window_start)
    if window_end is not None:
        run_stmt = run_stmt.where(runs.c.created_at <= window_end)

    run_rows = [dict(r) for r in conn.execute(run_stmt).mappings().all()]
    if not run_rows:
        return {
            "runs": [],
            "usage_by_run": {},
            "ambiguous_dispatch_runs": set(),
            "dispatch_obs_by_run": {},
        }

    run_ids = [str(r["run_id"]) for r in run_rows]

    usage_rows = conn.execute(
        sa.select(
            usage_ledger.c.run_id,
            usage_ledger.c.cost_estimate,
            usage_ledger.c.reported_at,
        ).where(
            usage_ledger.c.tenant_id == tenant_id,
            usage_ledger.c.run_id.in_(run_ids),
        )
    ).fetchall()
    usage_by_run: dict[str, list[tuple[float | None, Any]]] = {}
    for u_run_id, cost_est, reported_at in usage_rows:
        val = float(cost_est) if cost_est is not None else None
        usage_by_run.setdefault(str(u_run_id), []).append((val, reported_at))

    intent_rows = conn.execute(
        sa.select(
            dispatch_intents.c.run_id,
            dispatch_intents.c.state,
            dispatch_intents.c.updated_at,
        ).where(
            dispatch_intents.c.tenant_id == tenant_id,
            dispatch_intents.c.run_id.in_(run_ids),
        )
    ).fetchall()
    ambiguous_dispatch_runs: set[str] = set()
    dispatch_obs_by_run: dict[str, Any] = {}
    for d_run_id, d_state, d_updated in intent_rows:
        r_key = str(d_run_id)
        if str(d_state) == "ambiguous":
            ambiguous_dispatch_runs.add(r_key)
        prev = dispatch_obs_by_run.get(r_key)
        if d_updated is not None and (prev is None or d_updated > prev):
            dispatch_obs_by_run[r_key] = d_updated

    return {
        "runs": run_rows,
        "usage_by_run": usage_by_run,
        "ambiguous_dispatch_runs": ambiguous_dispatch_runs,
        "dispatch_obs_by_run": dispatch_obs_by_run,
    }


def _compute_health_from_runs(
    attributed_runs: list[dict[str, Any]],
    *,
    usage_by_run: dict[str, list[tuple[float | None, Any]]],
    ambiguous_dispatch_runs: set[str],
    dispatch_obs_by_run: dict[str, Any],
    unattributed_run_count: int = 0,
) -> dict[str, Any]:
    if not attributed_runs:
        return {
            "status": "no_data",
            "total_runs": 0,
            "last_run_at": None,
            "last_observed_at": None,
            "counts_by_state": {},
            "unknown_outcome_count": 0,
            "unattributed_run_count": unattributed_run_count,
            "cost": {
                "status": "unknown",
                "amount_usd": None,
                "currency": "USD",
                "known_runs": 0,
                "missing_usage_runs": 0,
            },
        }

    counts_by_state: dict[str, int] = {}
    unknown_outcome_count = 0
    last_run_dt: dt.datetime | None = None
    last_obs_dt: dt.datetime | None = None
    total_cost_usd = 0.0
    known_runs = 0
    missing_usage_runs = 0

    for run in attributed_runs:
        r_id = str(run["run_id"])
        r_state = str(run["state"])
        r_outcome = run.get("remote_outcome")
        counts_by_state[r_state] = counts_by_state.get(r_state, 0) + 1

        if r_state == "unknown" or r_outcome == "unknown" or r_id in ambiguous_dispatch_runs:
            unknown_outcome_count += 1

        created_at = run.get("created_at")
        updated_at = run.get("updated_at")
        if isinstance(created_at, dt.datetime) and (last_run_dt is None or created_at > last_run_dt):
            last_run_dt = created_at

        obs_candidates: list[dt.datetime] = []
        for candidate in (created_at, updated_at, dispatch_obs_by_run.get(r_id)):
            if isinstance(candidate, dt.datetime):
                obs_candidates.append(candidate)

        usage_entries = usage_by_run.get(r_id)
        if not usage_entries:
            missing_usage_runs += 1
        else:
            run_costs = [val for val, _ in usage_entries if val is not None]
            for _, rep_dt in usage_entries:
                if isinstance(rep_dt, dt.datetime):
                    obs_candidates.append(rep_dt)
            if not run_costs:
                missing_usage_runs += 1
            else:
                known_runs += 1
                total_cost_usd += sum(run_costs)

        for obs in obs_candidates:
            if last_obs_dt is None or obs > last_obs_dt:
                last_obs_dt = obs

    if known_runs == 0:
        cost_block: dict[str, Any] = {
            "status": "unknown",
            "amount_usd": None,
            "currency": "USD",
            "known_runs": 0,
            "missing_usage_runs": missing_usage_runs,
        }
    elif missing_usage_runs > 0:
        cost_block = {
            "status": "partial",
            "amount_usd": round(total_cost_usd, 6),
            "currency": "USD",
            "known_runs": known_runs,
            "missing_usage_runs": missing_usage_runs,
        }
    else:
        cost_block = {
            "status": "actual",
            "amount_usd": round(total_cost_usd, 6),
            "currency": "USD",
            "known_runs": known_runs,
            "missing_usage_runs": 0,
        }

    if unknown_outcome_count > 0:
        health_status = "unknown"
    elif (counts_by_state.get("failed", 0) + counts_by_state.get("timed_out", 0)) > 0:
        health_status = "degraded"
    else:
        health_status = "healthy"

    return {
        "status": health_status,
        "total_runs": len(attributed_runs),
        "last_run_at": _to_iso_utc(last_run_dt),
        "last_observed_at": _to_iso_utc(last_obs_dt),
        "counts_by_state": dict(sorted(counts_by_state.items())),
        "unknown_outcome_count": unknown_outcome_count,
        "unattributed_run_count": unattributed_run_count,
        "cost": cost_block,
    }


def _format_binding_summary(row: RowMapping | Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(row["payload"]) if isinstance(row.get("payload"), dict) else {}
    revoked_at = row.get("revoked_at")
    is_revoked = revoked_at is not None
    return {
        "binding_id": str(row["binding_id"]),
        "release_version": str(row["release_version"]),
        "release_sha256": str(row["release_sha256"]),
        "mode": str(row["mode"]),
        "execution_boundary": str(row["execution_boundary"]),
        "endpoint_ref": str(row["endpoint_ref"]),
        "secret_ref": row.get("secret_ref"),
        "egress_policy_ref": str(row["egress_policy_ref"]),
        "telemetry_export": payload.get("telemetry_export"),
        "policy_version": payload.get("policy_version"),
        "model_profile_ref": payload.get("model_profile_ref"),
        "certified_capabilities": dict(row["certified_capabilities"]),
        "capability_evidence_ref": str(row["capability_evidence_ref"]),
        "capability_evidence_level": "self_declared",
        "capability_evidence_status": "self_declared_unverified",
        "status": "revoked" if is_revoked else "active",
        "created_at": _to_iso_utc(row.get("created_at")),
        "revoked_at": _to_iso_utc(revoked_at),
    }


def derive_agent_health(
    conn_or_session: Engine | sa.Connection | Session,
    *,
    tenant_id: str,
    agent_id: str,
    window_hours: int | None = None,
    window_start: dt.datetime | None = None,
    window_end: dt.datetime | None = None,
) -> dict[str, Any]:
    """Compute derived health for a registered agent from attributed runs in window."""
    start_dt, end_dt = _resolve_window_bounds(
        window_hours=window_hours,
        window_start=window_start,
        window_end=window_end,
    )
    with _conn_context(conn_or_session) as conn:
        agent_exists = conn.execute(
            sa.select(agents.c.agent_id).where(
                agents.c.tenant_id == tenant_id,
                agents.c.agent_id == agent_id,
            )
        ).one_or_none()
        if agent_exists is None:
            raise AgentNotFoundError(agent_id)

        binding_ids = set(
            conn.execute(
                sa.select(agent_bindings.c.binding_id).where(
                    agent_bindings.c.tenant_id == tenant_id,
                    agent_bindings.c.agent_id == agent_id,
                )
            )
            .scalars()
            .all()
        )

        run_data = _load_tenant_run_attribution_data(
            conn,
            tenant_id=tenant_id,
            window_start=start_dt,
            window_end=end_dt,
        )
        attributed_runs = [r for r in run_data["runs"] if str(r["binding_id"]) in binding_ids]
        unattributed_for_agent = sum(
            1
            for r in run_data["runs"]
            if str(r["task_agent_id"]) == agent_id and str(r["binding_id"]) not in binding_ids
        )
        return _compute_health_from_runs(
            attributed_runs,
            usage_by_run=run_data["usage_by_run"],
            ambiguous_dispatch_runs=run_data["ambiguous_dispatch_runs"],
            dispatch_obs_by_run=run_data["dispatch_obs_by_run"],
            unattributed_run_count=unattributed_for_agent,
        )


def list_agents(
    conn_or_session: Engine | sa.Connection | Session,
    *,
    tenant_id: str,
    window_hours: int | None = None,
    window_start: dt.datetime | None = None,
    window_end: dt.datetime | None = None,
) -> dict[str, Any]:
    """List all registered agents, bindings, and derived health for a tenant."""
    start_dt, end_dt = _resolve_window_bounds(
        window_hours=window_hours,
        window_start=window_start,
        window_end=window_end,
    )
    with _conn_context(conn_or_session) as conn:
        agent_rows = (
            conn.execute(
                sa.select(agents)
                .where(agents.c.tenant_id == tenant_id)
                .order_by(agents.c.agent_id.asc())
            )
            .mappings()
            .all()
        )

        binding_rows = (
            conn.execute(
                sa.select(agent_bindings)
                .where(agent_bindings.c.tenant_id == tenant_id)
                .order_by(agent_bindings.c.created_at.desc(), agent_bindings.c.binding_id.asc())
            )
            .mappings()
            .all()
        )

        bindings_by_agent: dict[str, list[dict[str, Any]]] = {}
        all_registered_bindings: set[str] = set()
        binding_to_agent: dict[str, str] = {}
        for brow in binding_rows:
            a_id = str(brow["agent_id"])
            b_id = str(brow["binding_id"])
            all_registered_bindings.add(b_id)
            binding_to_agent[b_id] = a_id
            bindings_by_agent.setdefault(a_id, []).append(_format_binding_summary(brow))

        run_data = _load_tenant_run_attribution_data(
            conn,
            tenant_id=tenant_id,
            window_start=start_dt,
            window_end=end_dt,
        )

        runs_by_agent: dict[str, list[dict[str, Any]]] = {}
        unattributed_by_agent: dict[str, int] = {}
        unattributed_runs_count = 0
        for run in run_data["runs"]:
            b_id = str(run["binding_id"])
            if b_id in all_registered_bindings:
                owner_agent_id = binding_to_agent[b_id]
                runs_by_agent.setdefault(owner_agent_id, []).append(run)
            else:
                unattributed_runs_count += 1
                t_agent_id = str(run["task_agent_id"])
                unattributed_by_agent[t_agent_id] = unattributed_by_agent.get(t_agent_id, 0) + 1

        items: list[dict[str, Any]] = []
        for arow in agent_rows:
            a_id = str(arow["agent_id"])
            a_bindings = bindings_by_agent.get(a_id, [])
            active_count = sum(1 for b in a_bindings if b["status"] == "active")
            revoked_count = sum(1 for b in a_bindings if b["status"] == "revoked")
            health = _compute_health_from_runs(
                runs_by_agent.get(a_id, []),
                usage_by_run=run_data["usage_by_run"],
                ambiguous_dispatch_runs=run_data["ambiguous_dispatch_runs"],
                dispatch_obs_by_run=run_data["dispatch_obs_by_run"],
                unattributed_run_count=unattributed_by_agent.get(a_id, 0),
            )
            owner_meta = dict(arow["owner_refs"]) if isinstance(arow["owner_refs"], dict) else {}
            owner_meta.setdefault("verification_status", "unverified")
            owner_meta.setdefault("verification_label", "unverified owner")

            items.append(
                {
                    "agent_id": a_id,
                    "name": str(arow["name"]),
                    "version": str(arow["version"]),
                    "domain": str(arow["domain"]),
                    "status": str(arow["status"]),
                    "runtime_kind": str(arow["runtime_kind"]),
                    "task_types": list(arow["task_types"]),
                    "manifest_sha256": str(arow["manifest_sha256"]),
                    "owner": owner_meta,
                    "registered_at": _to_iso_utc(arow["registered_at"]),
                    "active_binding_count": active_count,
                    "revoked_binding_count": revoked_count,
                    "bindings": a_bindings,
                    "health": health,
                }
            )

    return {
        "schema_version": "1.0",
        "tenant_id": tenant_id,
        "generated_at": _to_iso_utc(dt.datetime.now(dt.UTC)),
        "window": {
            "mode": "rolling" if window_hours is not None else "all_recorded",
            "hours": window_hours,
        },
        "unattributed_runs_count": unattributed_runs_count,
        "agents": items,
    }


def get_agent_detail(
    conn_or_session: Engine | sa.Connection | Session,
    *,
    tenant_id: str,
    agent_id: str,
    window_hours: int | None = None,
    window_start: dt.datetime | None = None,
    window_end: dt.datetime | None = None,
    recent_limit: int = 20,
) -> dict[str, Any] | None:
    """Return full manifest summary, bindings, capability evidence refs, health, and recent runs.

    Returns None when agent_id does not exist in tenant_id (strict cross-tenant isolation -> 404).
    """
    start_dt, end_dt = _resolve_window_bounds(
        window_hours=window_hours,
        window_start=window_start,
        window_end=window_end,
    )
    with _conn_context(conn_or_session) as conn:
        arow = (
            conn.execute(
                sa.select(agents).where(
                    agents.c.tenant_id == tenant_id,
                    agents.c.agent_id == agent_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if arow is None:
            return None

        binding_rows = (
            conn.execute(
                sa.select(agent_bindings)
                .where(
                    agent_bindings.c.tenant_id == tenant_id,
                    agent_bindings.c.agent_id == agent_id,
                )
                .order_by(agent_bindings.c.created_at.desc(), agent_bindings.c.binding_id.asc())
            )
            .mappings()
            .all()
        )

        formatted_bindings = [_format_binding_summary(brow) for brow in binding_rows]
        active_count = sum(1 for b in formatted_bindings if b["status"] == "active")
        revoked_count = sum(1 for b in formatted_bindings if b["status"] == "revoked")
        agent_binding_ids = {b["binding_id"] for b in formatted_bindings}

        run_data = _load_tenant_run_attribution_data(
            conn,
            tenant_id=tenant_id,
            window_start=start_dt,
            window_end=end_dt,
        )

        attributed_runs = [r for r in run_data["runs"] if str(r["binding_id"]) in agent_binding_ids]
        unattributed_for_agent = sum(
            1
            for r in run_data["runs"]
            if str(r["task_agent_id"]) == agent_id and str(r["binding_id"]) not in agent_binding_ids
        )
        health = _compute_health_from_runs(
            attributed_runs,
            usage_by_run=run_data["usage_by_run"],
            ambiguous_dispatch_runs=run_data["ambiguous_dispatch_runs"],
            dispatch_obs_by_run=run_data["dispatch_obs_by_run"],
            unattributed_run_count=unattributed_for_agent,
        )

        recent_runs: list[dict[str, Any]] = []
        for run in attributed_runs[:recent_limit]:
            r_id = str(run["run_id"])
            usage_entries = run_data["usage_by_run"].get(r_id)
            run_costs = [val for val, _ in (usage_entries or []) if val is not None]
            if not run_costs:
                cost_status = "unknown"
                cost_usd: float | None = None
            else:
                cost_status = "actual"
                cost_usd = round(sum(run_costs), 6)

            recent_runs.append(
                {
                    "run_id": r_id,
                    "task_id": str(run["task_id"]),
                    "attempt": int(run["attempt"]),
                    "binding_id": str(run["binding_id"]),
                    "state": str(run["state"]),
                    "remote_outcome": run.get("remote_outcome"),
                    "attribution_status": "attributed",
                    "created_at": _to_iso_utc(run.get("created_at")),
                    "updated_at": _to_iso_utc(run.get("updated_at")),
                    "cost_status": cost_status,
                    "cost_usd": cost_usd,
                }
            )

        manifest = dict(arow["manifest"]) if isinstance(arow["manifest"], dict) else {}
        owner_meta = dict(arow["owner_refs"]) if isinstance(arow["owner_refs"], dict) else {}
        owner_meta.setdefault("verification_status", "unverified")
        owner_meta.setdefault("verification_label", "unverified owner")

        capability_evidence_refs = [
            {
                "binding_id": b["binding_id"],
                "binding_status": b["status"],
                "capability_evidence_ref": b["capability_evidence_ref"],
                "evidence_level": b["capability_evidence_level"],
                "verification_status": b["capability_evidence_status"],
            }
            for b in formatted_bindings
        ]

        return {
            "schema_version": "1.0",
            "tenant_id": tenant_id,
            "agent_id": str(arow["agent_id"]),
            "name": str(arow["name"]),
            "version": str(arow["version"]),
            "domain": str(arow["domain"]),
            "status": str(arow["status"]),
            "runtime_kind": str(arow["runtime_kind"]),
            "task_types": list(arow["task_types"]),
            "manifest_sha256": str(arow["manifest_sha256"]),
            "owner": owner_meta,
            "registered_at": _to_iso_utc(arow["registered_at"]),
            "manifest_summary": {
                "contract_version": manifest.get("contract_version"),
                "agent_id": manifest.get("agent_id"),
                "version": manifest.get("version"),
                "runtime_kind": manifest.get("runtime_kind"),
                "task_types": manifest.get("task_types", []),
                "desired_capabilities": manifest.get("desired_capabilities", {}),
                "desired_tools": manifest.get("desired_tools", []),
                "input_schema_ref": manifest.get("input_schema_ref"),
                "output_schema_ref": manifest.get("output_schema_ref"),
                "harness_version": manifest.get("harness_version"),
                "eval_profile_ref": manifest.get("eval_profile_ref"),
                "manifest_sha256": str(arow["manifest_sha256"]),
            },
            "active_binding_count": active_count,
            "revoked_binding_count": revoked_count,
            "bindings": formatted_bindings,
            "capability_evidence_refs": capability_evidence_refs,
            "health": health,
            "recent_runs": recent_runs,
        }
