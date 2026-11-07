"""Research agent output contract model (research-output.schema.json)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from eap.contracts.models.base import ContractModel


class ResearchOutput(ContractModel):
    """Output summary, evidence references, and limitations from research task."""

    summary: Annotated[str, Field(min_length=1)]
    evidence_refs: list[str]
    limitations: list[str]
