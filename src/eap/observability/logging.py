"""Structured JSON logging and credential redaction (SC-04)."""

from __future__ import annotations

import contextlib
import contextvars
import datetime
import json
import logging
import re
import sys
import threading
import traceback
from collections.abc import Generator
from typing import Any

from pydantic import SecretStr

# Context variables for structured log correlation
tenant_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("tenant_id", default=None)
principal_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("principal_id", default=None)
task_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("task_id", default=None)
run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def set_log_context(
    tenant_id: str | None = None,
    principal_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Set structured log context variables."""
    if tenant_id is not None:
        tenant_id_var.set(tenant_id)
    if principal_id is not None:
        principal_id_var.set(principal_id)
    if task_id is not None:
        task_id_var.set(task_id)
    if run_id is not None:
        run_id_var.set(run_id)
    if request_id is not None:
        request_id_var.set(request_id)


def clear_log_context() -> None:
    """Reset all structured log context variables to None."""
    tenant_id_var.set(None)
    principal_id_var.set(None)
    task_id_var.set(None)
    run_id_var.set(None)
    request_id_var.set(None)


@contextlib.contextmanager
def log_context(
    tenant_id: str | None = None,
    principal_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
) -> Generator[None, None, None]:
    """Context manager scoping log context variables to a code block."""
    tokens = []
    if tenant_id is not None:
        tokens.append((tenant_id_var, tenant_id_var.set(tenant_id)))
    if principal_id is not None:
        tokens.append((principal_id_var, principal_id_var.set(principal_id)))
    if task_id is not None:
        tokens.append((task_id_var, task_id_var.set(task_id)))
    if run_id is not None:
        tokens.append((run_id_var, run_id_var.set(run_id)))
    if request_id is not None:
        tokens.append((request_id_var, request_id_var.set(request_id)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


# Thread-safe registered secrets registry
_REGISTERED_SECRETS: set[str] = set()
_SECRETS_LOCK = threading.Lock()

# Redaction patterns
RE_DSN_PASSWORD = re.compile(r"([a-zA-Z0-9_+]+://[^:\s@]*):([^@\s/]+)@")
RE_CANARY = re.compile(r"EAP-CANARY-[A-Za-z0-9_\-]+", re.IGNORECASE)
RE_BEARER = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9_\-\.+=/]+)")
RE_AUTH_HEADER = re.compile(r"(?i)\bAuthorization\s*:\s*([^\r\n]+)")
RE_COOKIE_HEADER = re.compile(r"(?i)\bCookie\s*:\s*([^\r\n]+)")
RE_SENSITIVE_KEY_NAME = re.compile(r"(?i)(password|secret|token|api_key|authorization|cookie)")

# JSON key-value matching sensitive key
RE_JSON_SENSITIVE_KV = re.compile(
    r'(?i)("(?=[^"]*(?:password|secret|token|api_key|authorization|cookie))[^"]*"\s*:\s*)("(?:\\.|[^"\\])*"|[^,\s\}\]]+)'
)

# URL / key=value matching sensitive key
RE_KV_SENSITIVE = re.compile(
    r'(?i)([\?&]?(?:password|secret|token|api_key|authorization|cookie)[a-z0-9_-]*=)([^&\s\'",;]+)'
)

# Python repr / colon-equals matching sensitive key
RE_REPR_SENSITIVE_QUOTED = re.compile(
    r"(?i)(\b['\"]?(?:password|secret|token|api_key|authorization|cookie)[a-z0-9_-]*['\"]?\s*[:=]\s*)(['\"])(?:\\.|[^\\])*?\2"
)
RE_REPR_SENSITIVE_UNQUOTED = re.compile(
    r"(?i)(\b(?:password|secret|token|api_key|authorization|cookie)[a-z0-9_-]*\s*[:=]\s*)([^&\s,;'\"]+)"
)


def register_secret(secret: str | None) -> None:
    """Register a raw secret value to be permanently redacted from logs and errors."""
    if not secret:
        return
    cleaned = secret.strip()
    # Avoid registering trivial short strings that could cause false positive redactions
    if len(cleaned) >= 4:
        with _SECRETS_LOCK:
            _REGISTERED_SECRETS.add(cleaned)


def unregister_secret(secret: str | None) -> None:
    """Remove a secret from the registry (used in tests)."""
    if not secret:
        return
    with _SECRETS_LOCK:
        _REGISTERED_SECRETS.discard(secret.strip())


def register_settings_secrets(settings: Any) -> None:
    """Extract and register all SecretStr values from settings."""
    if settings is None:
        return

    # Check top-level settings fields
    model_fields = getattr(type(settings), "model_fields", None)
    if model_fields:
        for field_name in model_fields:
            val = getattr(settings, field_name, None)
            if isinstance(val, SecretStr):
                secret_val = val.get_secret_value()
                if secret_val:
                    register_secret(secret_val)
                    # If it's a DSN, also register password portion
                    if "://" in secret_val and "@" in secret_val:
                        m = RE_DSN_PASSWORD.search(secret_val)
                        if m:
                            register_secret(m.group(2))

    # Check configured principals
    if hasattr(settings, "get_configured_principals"):
        try:
            for principal in settings.get_configured_principals():
                if hasattr(principal, "token_hash") and isinstance(principal.token_hash, SecretStr):
                    raw_hash = principal.token_hash.get_secret_value()
                    register_secret(raw_hash)
                    if raw_hash.startswith("sha256:"):
                        register_secret(raw_hash[7:])
        except (AttributeError, ValueError, TypeError):
            pass


def redact_text(text: str) -> str:
    """Apply comprehensive redaction rules to a string."""
    if not text:
        return text

    result = text

    # 1. Redact DSN passwords: e.g. postgresql://user:pass@ -> postgresql://user:***@
    result = RE_DSN_PASSWORD.sub(r"\1:***@", result)

    # 2. Redact Authorization header
    result = RE_AUTH_HEADER.sub(r"Authorization: ***", result)

    # 3. Redact Bearer tokens
    result = RE_BEARER.sub(r"Bearer ***", result)

    # 4. Redact Cookie header
    result = RE_COOKIE_HEADER.sub(r"Cookie: ***", result)

    # 5. Redact Canary patterns
    result = RE_CANARY.sub(r"***", result)

    # 6. Redact sensitive JSON keys: "password": "...", "token": "..."
    result = RE_JSON_SENSITIVE_KV.sub(r'\1"***"', result)

    # 7. Redact sensitive key=value patterns: password=..., token=...
    result = RE_KV_SENSITIVE.sub(r"\1***", result)

    # 8. Redact sensitive python repr / dict patterns: 'password': '...'
    result = RE_REPR_SENSITIVE_QUOTED.sub(r'\1"***"', result)
    result = RE_REPR_SENSITIVE_UNQUOTED.sub(r"\1***", result)

    # 9. Redact all registered secret values (canary, tokens, passwords, hashes)
    with _SECRETS_LOCK:
        active_secrets = sorted(_REGISTERED_SECRETS, key=len, reverse=True)

    for secret in active_secrets:
        if secret in result:
            result = result.replace(secret, "***")

    return result


def redact_structure(obj: Any) -> Any:
    """Recursively redact sensitive data from dicts, lists, and primitives."""
    if isinstance(obj, SecretStr):
        return "***"
    if isinstance(obj, dict):
        redacted_dict: dict[str, Any] = {}
        for k, v in obj.items():
            k_str = str(k)
            if RE_SENSITIVE_KEY_NAME.search(k_str):
                redacted_dict[k_str] = "***"
            else:
                redacted_dict[k_str] = redact_structure(v)
        return redacted_dict
    if isinstance(obj, (list, tuple, set)):
        items = [redact_structure(item) for item in obj]
        return type(obj)(items)  # type: ignore[call-arg]
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, Exception):
        return redact_text(str(obj))
    return obj


# Global toggle for mutation testing
_REDACTION_DISABLED = False


@contextlib.contextmanager
def disabled_redaction() -> Generator[None, None, None]:
    """Temporarily disable redaction filter (strictly for mutation testing)."""
    global _REDACTION_DISABLED
    prev = _REDACTION_DISABLED
    _REDACTION_DISABLED = True
    try:
        yield
    finally:
        _REDACTION_DISABLED = prev


class RedactionFilter(logging.Filter):
    """Logging filter applied to every handler to redact credentials, secrets, and canaries."""

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        self.disabled: bool = False

    def filter(self, record: logging.LogRecord) -> bool:
        if self.disabled or _REDACTION_DISABLED:
            return True

        try:
            # 1. Format and redact record message and args
            if record.args:
                try:
                    formatted = record.msg % record.args
                    record.msg = redact_text(formatted)
                    record.args = ()
                except (TypeError, ValueError):
                    record.msg = redact_text(str(record.msg))
                    if isinstance(record.args, tuple):
                        record.args = tuple(redact_structure(a) for a in record.args)
                    elif isinstance(record.args, dict):
                        record.args = redact_structure(record.args)
            elif isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
            elif isinstance(record.msg, Exception):
                record.msg = redact_text(str(record.msg))

            # 2. Redact exceptions and tracebacks
            if record.exc_info:
                formatted_exc = "".join(traceback.format_exception(*record.exc_info))
                record.exc_text = redact_text(formatted_exc)
            elif record.exc_text:
                record.exc_text = redact_text(record.exc_text)

            # 3. Redact stack_info if present
            if record.stack_info:
                record.stack_info = redact_text(record.stack_info)

            # 4. Redact any sensitive custom record attributes
            standard_attrs = {
                "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
                "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                "created", "msecs", "relativeCreated", "thread", "threadName",
                "processName", "process", "message",
            }
            for attr, val in record.__dict__.items():
                if attr not in standard_attrs and isinstance(val, (dict, list, str, Exception, SecretStr)):
                    record.__dict__[attr] = redact_structure(val)

        except Exception:  # noqa: BLE001, S110
            # Redaction filter must never crash the application
            pass

        return True


class JsonFormatter(logging.Formatter):
    """Structured JSON formatter for application and worker logging."""

    def format(self, record: logging.LogRecord) -> str:
        # Determine contextual values
        tenant_id = getattr(record, "tenant_id", None) or tenant_id_var.get()
        principal_id = getattr(record, "principal_id", None) or principal_id_var.get()
        task_id = getattr(record, "task_id", None) or task_id_var.get()
        run_id = getattr(record, "run_id", None) or run_id_var.get()
        request_id = getattr(record, "request_id", None) or request_id_var.get()

        message = record.getMessage()
        if not _REDACTION_DISABLED:
            message = redact_text(message)

        timestamp = datetime.datetime.fromtimestamp(record.created, tz=datetime.UTC).isoformat()

        entry: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "task_id": task_id,
            "run_id": run_id,
            "request_id": request_id,
        }

        # Include duration or status if provided in record
        if hasattr(record, "status"):
            entry["status"] = record.status
        if hasattr(record, "duration_ms"):
            entry["duration_ms"] = record.duration_ms
        if hasattr(record, "method"):
            entry["method"] = record.method
        if hasattr(record, "path"):
            entry["path"] = record.path

        # Include exception/traceback if present
        if record.exc_text:
            exc_text = record.exc_text if _REDACTION_DISABLED else redact_text(record.exc_text)
            entry["exception"] = exc_text
        elif record.exc_info:
            raw_exc = "".join(traceback.format_exception(*record.exc_info))
            entry["exception"] = raw_exc if _REDACTION_DISABLED else redact_text(raw_exc)

        # Include any extra fields attached to the record
        standard_attrs = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "tenant_id", "principal_id",
            "task_id", "run_id", "request_id", "status", "duration_ms", "method", "path",
        }
        for attr, val in record.__dict__.items():
            if attr not in standard_attrs:
                entry[attr] = val if _REDACTION_DISABLED else redact_structure(val)

        return json.dumps(entry)


_GLOBAL_FILTER = RedactionFilter()
_PATCH_APPLIED = False
_CONFIGURE_LOCK = threading.Lock()


def apply_redaction_filter(target: logging.Handler | logging.Logger) -> None:
    """Attach the redaction filter to a logger or handler if not already present."""
    for f in target.filters:
        if isinstance(f, RedactionFilter):
            return
    target.addFilter(_GLOBAL_FILTER)


def _patch_logger_add_handler() -> None:
    """Ensure any handler added anywhere in the runtime gets the redaction filter automatically."""
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    orig_add_handler = logging.Logger.addHandler

    def add_handler_with_redaction(self: logging.Logger, hdlr: logging.Handler) -> None:
        apply_redaction_filter(hdlr)
        orig_add_handler(self, hdlr)

    logging.Logger.addHandler = add_handler_with_redaction  # type: ignore[method-assign]
    _PATCH_APPLIED = True


def configure_logging(
    settings: Any = None,
    level: int = logging.INFO,
    handler: logging.Handler | None = None,
) -> None:
    """Configure structured JSON logging and redaction once at startup.

    - Registers secrets from settings into the redaction filter.
    - Applies RedactionFilter to every handler.
    - Sets JsonFormatter on default StreamHandler.
    """
    with _CONFIGURE_LOCK:
        if settings is not None:
            register_settings_secrets(settings)

        _patch_logger_add_handler()

        root = logging.getLogger()
        root.setLevel(level)
        root.disabled = False
        apply_redaction_filter(root)

        # Ensure existing loggers are not disabled by external fileConfig calls
        for l in logging.root.manager.loggerDict.values():
            if isinstance(l, logging.Logger):
                l.disabled = False

        # Apply to all existing handlers on root and child loggers
        for h in root.handlers:
            apply_redaction_filter(h)

        if not root.handlers:
            default_handler = handler or logging.StreamHandler(sys.stdout)
            default_handler.setFormatter(JsonFormatter())
            default_handler.setLevel(level)
            apply_redaction_filter(default_handler)
            root.addHandler(default_handler)
        else:
            # Ensure existing handlers have the filter
            for h in root.handlers:
                apply_redaction_filter(h)
