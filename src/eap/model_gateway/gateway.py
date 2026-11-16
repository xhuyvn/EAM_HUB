"""Model Gateway: binding-authorized structured generation & embeddings with egress enforcement."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import jsonschema  # type: ignore[import-untyped]

from eap.config import Settings, get_settings
from eap.contracts.models.binding import Binding
from eap.model_gateway.egress import (
    check_binding_profile_authorized,
    enforce_egress,
    normalize_binding,
)
from eap.model_gateway.errors import (
    InvalidModelOutputError,
    ModelGatewayError,
    ProfileNotFoundError,
)
from eap.model_gateway.profiles import ProfileRegistry, default_profile_registry
from eap.model_gateway.providers import FakeModelProvider, ModelProvider

logger = logging.getLogger(__name__)


class TelemetryExporter(Protocol):
    """Metadata-only telemetry exporter invoked only when binding + platform allow export."""

    def export(self, event: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class GatewayResult:
    """Validated structured generation result returned by ModelGateway."""

    raw_text: str
    parsed: dict[str, Any]
    dropped_fields_count: int
    profile_ref: str
    endpoint_class: str
    model_name: str
    prompt_sha256: str
    prompt_length: int
    output_sha256: str
    output_length: int
    telemetry_exported: bool


@dataclass(frozen=True)
class EmbeddingResult:
    """Embedding result returned by ModelGateway."""

    vectors: list[list[float]]
    profile_ref: str
    endpoint_class: str
    model_name: str
    input_sha256: str
    input_length: int
    telemetry_exported: bool


def _sha256_and_len(text: str) -> tuple[str, int]:
    encoded = text.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _strip_unknown_properties(data: Any, schema: dict[str, Any]) -> tuple[Any, int]:
    """Recursively drop fields not permitted by schema (additionalProperties=False) and count them."""
    schema_type = schema.get("type")
    if schema_type == "object" and isinstance(data, dict):
        props = schema.get("properties") or {}
        additional_allowed = schema.get("additionalProperties", True)
        dropped = 0
        cleaned: dict[str, Any] = {}
        for key, val in data.items():
            if key in props:
                sub_val, sub_dropped = _strip_unknown_properties(val, props[key])
                cleaned[key] = sub_val
                dropped += sub_dropped
            elif additional_allowed is False:
                dropped += 1
            else:
                cleaned[key] = val
        return cleaned, dropped

    if schema_type == "array" and isinstance(data, list):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            dropped = 0
            cleaned_list: list[Any] = []
            for item in data:
                sub_val, sub_dropped = _strip_unknown_properties(item, items_schema)
                cleaned_list.append(sub_val)
                dropped += sub_dropped
            return cleaned_list, dropped

    return data, 0


class ModelGateway:
    """Enforces binding profile authorization, egress policy, redaction, and telemetry rules."""

    def __init__(
        self,
        *,
        provider: ModelProvider | None = None,
        registry: ProfileRegistry | None = None,
        telemetry_exporter: TelemetryExporter | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._provider: ModelProvider = provider if provider is not None else FakeModelProvider()
        self._registry: ProfileRegistry = (
            registry if registry is not None else default_profile_registry()
        )
        self._telemetry_exporter = telemetry_exporter
        self._settings = settings

    def _should_export_telemetry(self, binding: Binding) -> bool:
        if binding.telemetry_export != "approved_endpoint":
            return False
        cfg = self._settings if self._settings is not None else get_settings()
        if cfg.eap_telemetry == "local_only":
            return False
        return self._telemetry_exporter is not None

    def _emit_telemetry(
        self,
        binding: Binding,
        *,
        operation: str,
        profile_ref: str,
        endpoint_class: str,
        prompt_sha256: str,
        prompt_length: int,
        output_sha256: str | None = None,
        output_length: int | None = None,
        status: str,
        error_code: str | None = None,
    ) -> bool:
        if not self._should_export_telemetry(binding):
            return False
        assert self._telemetry_exporter is not None
        self._telemetry_exporter.export(
            {
                "binding_id": binding.binding_id,
                "agent_id": binding.agent_id,
                "operation": operation,
                "profile_ref": profile_ref,
                "endpoint_class": endpoint_class,
                "prompt_sha256": prompt_sha256,
                "prompt_length": prompt_length,
                "output_sha256": output_sha256,
                "output_length": output_length,
                "status": status,
                "error_code": error_code,
            }
        )
        return True

    def generate_structured(
        self,
        binding: Binding | Mapping[str, Any],
        profile_ref: str,
        prompt: str,
        schema: dict[str, Any],
        *,
        timeout_seconds: float = 30.0,
    ) -> GatewayResult:
        """Execute a structured generation call after binding and egress checks."""
        norm_binding = normalize_binding(binding)

        # 1. Binding authorization check
        check_binding_profile_authorized(norm_binding, profile_ref, for_embedding=False)

        # 2. Profile lookup (fail-closed)
        profile = self._registry.get(profile_ref)
        if profile is None:
            raise ProfileNotFoundError(
                f"Model profile '{profile_ref}' is not registered",
                profile_ref=profile_ref,
            )

        # 3. Egress policy check BEFORE any provider call
        enforce_egress(norm_binding.egress_policy_ref, profile)

        # 4. Hash & length logging only (never log clear-text prompt or output)
        prompt_sha256, prompt_length = _sha256_and_len(prompt)
        logger.info(
            "model_gateway.generate.request binding_id=%s profile_ref=%s endpoint_class=%s "
            "prompt_sha256=%s prompt_length=%d",
            norm_binding.binding_id,
            profile_ref,
            profile.endpoint_class,
            prompt_sha256,
            prompt_length,
        )

        # 5. Provider call
        try:
            resp = self._provider.generate(
                profile,
                prompt,
                schema,
                timeout_seconds=timeout_seconds,
            )
        except ModelGatewayError as exc:
            logger.warning(
                "model_gateway.generate.error binding_id=%s profile_ref=%s error_code=%s "
                "prompt_sha256=%s prompt_length=%d",
                norm_binding.binding_id,
                profile_ref,
                exc.code,
                prompt_sha256,
                prompt_length,
            )
            self._emit_telemetry(
                norm_binding,
                operation="generate_structured",
                profile_ref=profile_ref,
                endpoint_class=profile.endpoint_class,
                prompt_sha256=prompt_sha256,
                prompt_length=prompt_length,
                status="error",
                error_code=exc.code,
            )
            raise

        output_sha256, output_length = _sha256_and_len(resp.raw_text)
        logger.info(
            "model_gateway.generate.response binding_id=%s profile_ref=%s "
            "prompt_sha256=%s output_sha256=%s output_length=%d",
            norm_binding.binding_id,
            profile_ref,
            prompt_sha256,
            output_sha256,
            output_length,
        )

        # 6. Parse JSON, drop/count unknown fields, validate against schema
        try:
            raw_obj = json.loads(resp.raw_text)
        except json.JSONDecodeError as exc:
            self._emit_telemetry(
                norm_binding,
                operation="generate_structured",
                profile_ref=profile_ref,
                endpoint_class=profile.endpoint_class,
                prompt_sha256=prompt_sha256,
                prompt_length=prompt_length,
                output_sha256=output_sha256,
                output_length=output_length,
                status="error",
                error_code="invalid_json",
            )
            raise InvalidModelOutputError(f"Model output is not valid JSON: {exc.msg}") from exc

        if not isinstance(raw_obj, dict):
            self._emit_telemetry(
                norm_binding,
                operation="generate_structured",
                profile_ref=profile_ref,
                endpoint_class=profile.endpoint_class,
                prompt_sha256=prompt_sha256,
                prompt_length=prompt_length,
                output_sha256=output_sha256,
                output_length=output_length,
                status="error",
                error_code="invalid_json",
            )
            raise InvalidModelOutputError("Model output JSON must be an object")

        cleaned_obj, dropped_count = _strip_unknown_properties(raw_obj, schema)
        try:
            jsonschema.validate(instance=cleaned_obj, schema=schema)
        except jsonschema.ValidationError as exc:
            self._emit_telemetry(
                norm_binding,
                operation="generate_structured",
                profile_ref=profile_ref,
                endpoint_class=profile.endpoint_class,
                prompt_sha256=prompt_sha256,
                prompt_length=prompt_length,
                output_sha256=output_sha256,
                output_length=output_length,
                status="error",
                error_code="invalid_json",
            )
            raise InvalidModelOutputError(f"Schema validation failed: {exc.message}") from exc

        exported = self._emit_telemetry(
            norm_binding,
            operation="generate_structured",
            profile_ref=profile_ref,
            endpoint_class=profile.endpoint_class,
            prompt_sha256=prompt_sha256,
            prompt_length=prompt_length,
            output_sha256=output_sha256,
            output_length=output_length,
            status="ok",
        )

        return GatewayResult(
            raw_text=resp.raw_text,
            parsed=cleaned_obj,
            dropped_fields_count=dropped_count,
            profile_ref=profile_ref,
            endpoint_class=profile.endpoint_class,
            model_name=resp.model_name,
            prompt_sha256=prompt_sha256,
            prompt_length=prompt_length,
            output_sha256=output_sha256,
            output_length=output_length,
            telemetry_exported=exported,
        )

    def generate_embeddings(
        self,
        binding: Binding | Mapping[str, Any],
        profile_ref: str,
        texts: list[str],
        *,
        timeout_seconds: float = 30.0,
    ) -> EmbeddingResult:
        """Execute an embedding call after binding and egress checks."""
        norm_binding = normalize_binding(binding)

        # 1. Binding authorization check
        check_binding_profile_authorized(norm_binding, profile_ref, for_embedding=True)

        # 2. Profile lookup (fail-closed)
        profile = self._registry.get(profile_ref)
        if profile is None:
            raise ProfileNotFoundError(
                f"Embedding profile '{profile_ref}' is not registered",
                profile_ref=profile_ref,
            )

        # 3. Egress policy check BEFORE any provider call
        enforce_egress(norm_binding.egress_policy_ref, profile)

        input_sha256, input_length = _sha256_and_len("\n".join(texts))
        logger.info(
            "model_gateway.embed.request binding_id=%s profile_ref=%s endpoint_class=%s "
            "input_sha256=%s input_length=%d count=%d",
            norm_binding.binding_id,
            profile_ref,
            profile.endpoint_class,
            input_sha256,
            input_length,
            len(texts),
        )

        resp = self._provider.embed(profile, texts, timeout_seconds=timeout_seconds)
        exported = self._emit_telemetry(
            norm_binding,
            operation="embed",
            profile_ref=profile_ref,
            endpoint_class=profile.endpoint_class,
            prompt_sha256=input_sha256,
            prompt_length=input_length,
            status="ok",
        )
        return EmbeddingResult(
            vectors=resp.vectors,
            profile_ref=profile_ref,
            endpoint_class=profile.endpoint_class,
            model_name=resp.model_name,
            input_sha256=input_sha256,
            input_length=input_length,
            telemetry_exported=exported,
        )


def generate_structured(
    binding: Binding | Mapping[str, Any],
    profile_ref: str,
    prompt: str,
    schema: dict[str, Any],
    *,
    provider: ModelProvider | None = None,
    registry: ProfileRegistry | None = None,
    telemetry_exporter: TelemetryExporter | None = None,
    settings: Settings | None = None,
    timeout_seconds: float = 30.0,
) -> GatewayResult:
    """Convenience entrypoint matching generate_structured(binding, profile_ref, prompt, schema)."""
    gw = ModelGateway(
        provider=provider,
        registry=registry,
        telemetry_exporter=telemetry_exporter,
        settings=settings,
    )
    return gw.generate_structured(
        binding,
        profile_ref,
        prompt,
        schema,
        timeout_seconds=timeout_seconds,
    )


def generate_embeddings(
    binding: Binding | Mapping[str, Any],
    profile_ref: str,
    texts: list[str],
    *,
    provider: ModelProvider | None = None,
    registry: ProfileRegistry | None = None,
    telemetry_exporter: TelemetryExporter | None = None,
    settings: Settings | None = None,
    timeout_seconds: float = 30.0,
) -> EmbeddingResult:
    """Convenience entrypoint for embedding generation with binding & egress enforcement."""
    gw = ModelGateway(
        provider=provider,
        registry=registry,
        telemetry_exporter=telemetry_exporter,
        settings=settings,
    )
    return gw.generate_embeddings(
        binding,
        profile_ref,
        texts,
        timeout_seconds=timeout_seconds,
    )
