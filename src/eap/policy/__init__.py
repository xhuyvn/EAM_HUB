"""Server-side policy grants, grant_epoch tracking, and retention watermarks (P3a-WP2)."""

from eap.policy.grants import (
    get_grant_epoch,
    get_revoked_project_ids,
    grant_project_access,
    is_project_authorized,
    policy_binding_provider,
    revoke_project_access,
    set_stream_retention_floor,
    update_project_grant,
)

__all__ = [
    "get_grant_epoch",
    "get_revoked_project_ids",
    "grant_project_access",
    "is_project_authorized",
    "policy_binding_provider",
    "revoke_project_access",
    "set_stream_retention_floor",
    "update_project_grant",
]
