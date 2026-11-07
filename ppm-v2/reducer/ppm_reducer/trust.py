"""Trust class rules and evidence TTL boundary evaluation.

Pure module: only allowed stdlib imports (datetime, typing).
"""

import datetime as dt

from .types import TrustClass


def parse_datetime(val: str | dt.datetime) -> dt.datetime:
    """Parse an ISO-8601 string or return timezone-aware datetime."""
    if isinstance(val, dt.datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=dt.UTC)
        return val
    # Normalize trailing Z to UTC offset for standard library fromisoformat
    ts = str(val).strip()
    if ts.endswith(("Z", "z")):
        ts = ts[:-1] + "+00:00"
    res = dt.datetime.fromisoformat(ts)
    if res.tzinfo is None:
        return res.replace(tzinfo=dt.UTC)
    return res


def is_verifying_trust_class(trust_class: str | None) -> bool:
    """Return True if the trust class has authority to verify deliverable criteria."""
    return bool(trust_class and trust_class in (TrustClass.VERIFIER_OBSERVED, TrustClass.CI_ATTESTED))


def is_evidence_ttl_expired(
    observed_at: str | dt.datetime,
    now: str | dt.datetime,
    ttl_days: float,
) -> bool:
    """Evaluate whether evidence has exceeded the TTL threshold.

    Boundary rule: Strictly greater than (age > ttl_days).
    An evidence observed exactly at now - ttl_days is fresh; at 1 second older it is stale.
    """
    obs_dt = parse_datetime(observed_at)
    now_dt = parse_datetime(now)
    age = now_dt - obs_dt
    threshold = dt.timedelta(days=ttl_days)
    return age > threshold
