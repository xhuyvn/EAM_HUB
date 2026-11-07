"""PPM domain input contract model (ppm-input.schema.json)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from eap.contracts.models.base import ContractModel


class PpmInput(ContractModel):
    """Input payload dispatched to PPM assessment runner."""

    project_id: Annotated[str, Field(min_length=1)]
    snapshot_refs: list[str]
    baseline_revision: Annotated[int, Field(ge=1)]
