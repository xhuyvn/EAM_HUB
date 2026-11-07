"""Location registration contract model (location-registration.schema.json)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from eap.contracts.models.base import (
    ContractModel,
    NonEmptyStr,
    SafePathStr,
    StrictFalse,
)


class LocationArtifactAliases(ContractModel):
    architecture: list[SafePathStr]
    blueprint: list[SafePathStr]
    implementation_plan: list[SafePathStr]


class LocationIdentity(ContractModel):
    canonical_root_fingerprint: Annotated[str | None, Field(min_length=1)] = Field(...)
    repo_identity: Annotated[str | None, Field(min_length=1)] = Field(...)


class LocationRegistration(ContractModel):
    """Registration of a physical source and project metadata scope."""

    contract_version: Literal["1.1"]
    location_id: Annotated[str, Field(min_length=1)]
    project_id: Annotated[str, Field(min_length=1)]
    source_id: Annotated[str, Field(min_length=1)]
    host_id: Annotated[str, Field(min_length=1)]
    connector_id: Annotated[str, Field(min_length=1)]
    root_path: Annotated[str, Field(min_length=1)]
    metadata_root: SafePathStr
    scan_scope: Annotated[list[SafePathStr], Field(min_length=1)]
    artifact_aliases: LocationArtifactAliases
    notes_globs: list[SafePathStr]
    follow_symlinks: StrictFalse
    allow_hardlinked_files: StrictFalse
    max_file_bytes: Annotated[int, Field(ge=1, le=16777216)]
    deny_globs: list[NonEmptyStr]
    classification: Literal["public", "internal", "confidential", "restricted"]
    egress_policy_ref: Annotated[str, Field(min_length=1)]
    identity: LocationIdentity
    state: Literal["proposed", "active", "suspended", "retired"]
    revision: Annotated[int, Field(ge=1)]
    registered_by_ref: Annotated[str, Field(min_length=1)]
    approved_by_ref: Annotated[str | None, Field(min_length=1)] = Field(...)
