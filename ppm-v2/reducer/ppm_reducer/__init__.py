"""PPM Reducer Core.

Deterministic, pure domain reducer for PPM v2.
Rule: Zero I/O, zero network, zero model dependencies, zero imports of eap, requests, socket, or subprocess.
"""

from .assemble import assemble_assessment, assess_project
from .criteria import assess_criteria
from .findings import detect_findings
from .progress import (
    calculate_progress,
    calculate_readiness,
    calculate_reported_progress_percent,
    calculate_validity,
    calculate_verification,
)
from .rollup import RollupProgress, RollupResult, rollup_portfolio
from .supersession import extract_update_id, resolve_supersession
from .trust import is_evidence_ttl_expired, is_verifying_trust_class, parse_datetime
from .types import (
    AcceptanceReadiness,
    AssessmentResult,
    CriterionResult,
    CriterionStatus,
    FindingKind,
    FindingResult,
    FindingSeverity,
    InputRejected,
    ProgressBasis,
    ProgressResult,
    ReasonCode,
    ReportedStatus,
    TrustClass,
    Validity,
    Verification,
)

__version__ = "0.3.0"
__all__ = [
    "AcceptanceReadiness",
    "AssessmentResult",
    "CriterionResult",
    "CriterionStatus",
    "FindingKind",
    "FindingResult",
    "FindingSeverity",
    "InputRejected",
    "ProgressBasis",
    "ProgressResult",
    "ReasonCode",
    "ReportedStatus",
    "RollupProgress",
    "RollupResult",
    "TrustClass",
    "Validity",
    "Verification",
    "__version__",
    "assemble_assessment",
    "assess_criteria",
    "assess_project",
    "calculate_progress",
    "calculate_readiness",
    "calculate_reported_progress_percent",
    "calculate_validity",
    "calculate_verification",
    "detect_findings",
    "extract_update_id",
    "is_evidence_ttl_expired",
    "is_verifying_trust_class",
    "parse_datetime",
    "resolve_supersession",
    "rollup_portfolio",
]
