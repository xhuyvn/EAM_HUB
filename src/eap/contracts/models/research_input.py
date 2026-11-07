"""Research agent input contract model (research-input.schema.json)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from eap.contracts.models.base import ContractModel


class ResearchInput(ContractModel):
    """Input parameters for research domain task."""

    topic: Annotated[str, Field(min_length=1)]
    source_refs: list[str]
