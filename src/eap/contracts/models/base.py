"""Base model and common types for EAP contract schemas."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

SHA256_HEX_PATTERN = r"^[a-f0-9]{64}$"
ISO8601_DATETIME_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
SAFE_RELATIVE_PATH_PATTERN = r"^(?!/)(?!.*\\)(?!.*:)(?!.*(?:^|/)\.\.(?:/|$)).+$"

_SAFE_PATH_RE = re.compile(SAFE_RELATIVE_PATH_PATTERN)


def _validate_safe_path(val: str) -> str:
    if not _SAFE_PATH_RE.match(val):
        raise ValueError(f"Path does not match safe relative path pattern: {val}")
    return val


def _validate_bool_exact(v: Any) -> Any:
    if type(v) is not bool:
        raise ValueError("Value must be a boolean, not an integer or other type")
    return v


DateTimeStr = Annotated[str, Field(pattern=ISO8601_DATETIME_PATTERN)]
NullableDateTimeStr = Annotated[str | None, Field(pattern=ISO8601_DATETIME_PATTERN)]
Sha256Str = Annotated[str, Field(pattern=SHA256_HEX_PATTERN)]
SafePathStr = Annotated[str, Field(min_length=1), AfterValidator(_validate_safe_path)]
NonEmptyStr = Annotated[str, Field(min_length=1)]
StrictFalse = Annotated[Literal[False], BeforeValidator(_validate_bool_exact)]
StrictTrue = Annotated[Literal[True], BeforeValidator(_validate_bool_exact)]


class ContractModel(BaseModel):
    """Base model enforcing strict schema validation with extra='forbid' and strict=True."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        strict=True,
    )
