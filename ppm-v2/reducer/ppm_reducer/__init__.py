"""PPM Reducer Core.

Deterministic, pure domain reducer for PPM v2.
Rule: Zero I/O, zero network, zero model dependencies, zero imports of eap, requests, socket, or subprocess.
"""

from .criteria import assess_criteria
from .supersession import extract_update_id, resolve_supersession
from .trust import is_evidence_ttl_expired, is_verifying_trust_class, parse_datetime
from .types import (
    CriterionResult,
    CriterionStatus,
    InputRejected,
    ReasonCode,
    ReportedStatus,
    TrustClass,
)

__version__ = "0.1.0"
__all__ = [
    "CriterionResult",
    "CriterionStatus",
    "InputRejected",
    "ReasonCode",
    "ReportedStatus",
    "TrustClass",
    "__version__",
    "assess_criteria",
    "extract_update_id",
    "is_evidence_ttl_expired",
    "is_verifying_trust_class",
    "parse_datetime",
    "resolve_supersession",
]
