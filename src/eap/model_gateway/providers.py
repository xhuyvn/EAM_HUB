"""Model provider interface, deterministic FakeModelProvider, and OllamaProvider stub."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from eap.model_gateway.errors import ModelRefusalError, ModelTimeoutError
from eap.model_gateway.profiles import ModelProfile


@dataclass(frozen=True)
class ProviderResponse:
    """Raw structured generation response from a provider."""

    raw_text: str
    model_name: str
    usage: dict[str, int] | None = None


@dataclass(frozen=True)
class EmbeddingResponse:
    """Embedding vectors returned by a provider."""

    vectors: list[list[float]]
    model_name: str
    usage: dict[str, int] | None = None


class ModelProvider(Protocol):
    """Provider interface called by the Model Gateway after authorization and egress checks."""

    def generate(
        self,
        profile: ModelProfile,
        prompt: str,
        schema: dict[str, Any],
        *,
        timeout_seconds: float = 30.0,
    ) -> ProviderResponse: ...

    def embed(
        self,
        profile: ModelProfile,
        texts: list[str],
        *,
        timeout_seconds: float = 30.0,
    ) -> EmbeddingResponse: ...


@dataclass(frozen=True)
class FakeSuccess:
    """Scripted successful response for FakeModelProvider."""

    payload: dict[str, Any] | str


@dataclass(frozen=True)
class FakeInvalidJson:
    """Scripted invalid JSON response for FakeModelProvider."""

    raw_text: str = "{invalid-json"


@dataclass(frozen=True)
class FakeTimeout:
    """Scripted timeout outcome for FakeModelProvider."""

    message: str = "fake model timeout"


@dataclass(frozen=True)
class FakeRefusal:
    """Scripted refusal outcome for FakeModelProvider."""

    reason: str = "fake model refusal"


FakeOutcome = FakeSuccess | FakeInvalidJson | FakeTimeout | FakeRefusal


@dataclass(frozen=True)
class FakeProviderCall:
    """Recorded call metadata on FakeModelProvider (no clear-text prompt stored)."""

    operation: str
    profile_ref: str
    endpoint_class: str
    model_name: str
    prompt_sha256: str
    prompt_length: int


def _deterministic_embedding(text: str, dims: int = 8) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [round(digest[i] / 255.0, 6) for i in range(dims)]


class FakeModelProvider:
    """Deterministic, scriptable fake model provider for tests and fixtures."""

    def __init__(
        self,
        outcomes: Sequence[FakeOutcome] | None = None,
        *,
        handler: Callable[[ModelProfile, str, dict[str, Any]], FakeOutcome] | None = None,
        default_payload: dict[str, Any] | None = None,
    ) -> None:
        self._outcomes: list[FakeOutcome] = list(outcomes) if outcomes else []
        self._handler = handler
        self._default_payload: dict[str, Any] = (
            default_payload if default_payload is not None else {"claims": []}
        )
        self.calls: list[FakeProviderCall] = []

    def generate(
        self,
        profile: ModelProfile,
        prompt: str,
        schema: dict[str, Any],
        *,
        timeout_seconds: float = 30.0,
    ) -> ProviderResponse:
        del timeout_seconds  # Unused by synchronous fake provider
        prompt_bytes = prompt.encode("utf-8")
        self.calls.append(
            FakeProviderCall(
                operation="generate",
                profile_ref=profile.profile_ref,
                endpoint_class=profile.endpoint_class,
                model_name=profile.model_name,
                prompt_sha256=hashlib.sha256(prompt_bytes).hexdigest(),
                prompt_length=len(prompt_bytes),
            )
        )

        outcome: FakeOutcome
        if self._outcomes:
            outcome = self._outcomes.pop(0)
        elif self._handler is not None:
            outcome = self._handler(profile, prompt, schema)
        else:
            outcome = FakeSuccess(self._default_payload)

        if isinstance(outcome, FakeTimeout):
            raise ModelTimeoutError(outcome.message)
        if isinstance(outcome, FakeRefusal):
            raise ModelRefusalError(outcome.reason)
        if isinstance(outcome, FakeInvalidJson):
            return ProviderResponse(
                raw_text=outcome.raw_text,
                model_name=profile.model_name,
                usage={"prompt_tokens": len(prompt_bytes) // 4, "completion_tokens": 4},
            )
        if isinstance(outcome, FakeSuccess):
            raw = (
                outcome.payload
                if isinstance(outcome.payload, str)
                else json.dumps(outcome.payload, sort_keys=True)
            )
            return ProviderResponse(
                raw_text=raw,
                model_name=profile.model_name,
                usage={
                    "prompt_tokens": max(1, len(prompt_bytes) // 4),
                    "completion_tokens": max(1, len(raw.encode("utf-8")) // 4),
                },
            )
        raise TypeError(f"Unsupported FakeOutcome: {outcome!r}")

    def embed(
        self,
        profile: ModelProfile,
        texts: list[str],
        *,
        timeout_seconds: float = 30.0,
    ) -> EmbeddingResponse:
        del timeout_seconds
        joined = "\n".join(texts).encode("utf-8")
        self.calls.append(
            FakeProviderCall(
                operation="embed",
                profile_ref=profile.profile_ref,
                endpoint_class=profile.endpoint_class,
                model_name=profile.model_name,
                prompt_sha256=hashlib.sha256(joined).hexdigest(),
                prompt_length=len(joined),
            )
        )
        vectors = [_deterministic_embedding(t) for t in texts]
        return EmbeddingResponse(
            vectors=vectors,
            model_name=profile.model_name,
            usage={"prompt_tokens": max(1, len(joined) // 4)},
        )


class OllamaProvider:
    """Stub Ollama provider (real Ollama throughput/runtime is gated on DG-3 / P0-WP6)."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        self.base_url = base_url

    def generate(
        self,
        profile: ModelProfile,
        prompt: str,
        schema: dict[str, Any],
        *,
        timeout_seconds: float = 30.0,
    ) -> ProviderResponse:
        del profile, prompt, schema, timeout_seconds
        raise NotImplementedError(
            "OllamaProvider is a stub until DG-3 / P0-WP6; no real Ollama calls are permitted."
        )

    def embed(
        self,
        profile: ModelProfile,
        texts: list[str],
        *,
        timeout_seconds: float = 30.0,
    ) -> EmbeddingResponse:
        del profile, texts, timeout_seconds
        raise NotImplementedError(
            "OllamaProvider is a stub until DG-3 / P0-WP6; no real Ollama calls are permitted."
        )
