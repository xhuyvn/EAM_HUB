"""Location Service managing physical sources, project scopes, and location registrations.

Enforces:
- Physical source decoupling from project scopes (ADR-019, ADR-022, docs/04).
- One physical directory = one source per tenant (409 on duplicate canonical_root or repo_identity).
- UNIQUE (tenant_id, source_id, metadata_root).
- Multiple scopes per project (W7-3); a scope belongs to exactly one project.
- C32: Monorepo multi-scope registration.
- C42: Identity conflict detection for duplicate project_ids across divergent repo identities.
- Approval authority: owner/admin only; registrant cannot approve own registration.
- Atomic state updates with transactional outbox event publishing.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from eap.api.dependencies import Principal
from eap.contracts.models.location_registration import LocationRegistration
from eap.contracts.rules import semantic_checks
from eap.db import (
    host_root_ownership,
    identity_conflicts,
    location_registrations,
    outbox,
    project_scopes,
    source_shares,
    sources,
)
from eap.ppm.path import canonicalize_root, validate_aliases

logger = logging.getLogger("eap.ppm.service")


class LocationServiceError(ValueError):
    """Base error for Location Service operations."""

    def __init__(self, message: str, code: str = "LOCATION_ERROR") -> None:
        self.message = message
        self.code = code
        super().__init__(f"{code}: {message}")


class SourceAlreadyRegisteredError(LocationServiceError):
    """Raised when registering a source that conflicts with an existing directory or repo identity."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="SOURCE_ALREADY_REGISTERED")


class DuplicateLocationError(LocationServiceError):
    """Raised when registering the same (source_id, metadata_root) twice."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="DUPLICATE_LOCATION")


class MetadataRootConflictError(LocationServiceError):
    """Raised when a metadata_root on a source is claimed by two different projects."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="METADATA_ROOT_CONFLICT")


class SourceNotFoundError(LocationServiceError):
    """Raised when a source is not found within the tenant scope."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="SOURCE_NOT_FOUND")


class LocationNotFoundError(LocationServiceError):
    """Raised when a location is not found within the tenant scope."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="LOCATION_NOT_FOUND")


class UnauthorizedApprovalError(LocationServiceError):
    """Raised when a non-owner/non-admin principal attempts to approve a location."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="UNAUTHORIZED_APPROVAL")


class RegistrantApprovalForbiddenError(LocationServiceError):
    """Raised when the registrant principal attempts to approve its own registration."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="REGISTRANT_CANNOT_APPROVE")


class InvalidLocationStateError(LocationServiceError):
    """Raised when an action is invalid for the current state of the location."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_LOCATION_STATE")


class ShareNotFoundError(LocationServiceError):
    """Raised when a share request is not found within the tenant scope."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="SHARE_NOT_FOUND")


class ShareApprovalForbiddenError(LocationServiceError):
    """Raised when a principal is unauthorized to approve, reject, or revoke a share request."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="SHARE_APPROVAL_FORBIDDEN")


class UnapprovedSourceLocationError(LocationServiceError):
    """Raised when attempting to activate a location on an unapproved cross-tenant shared source."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="UNAPPROVED_SOURCE_LOCATION")


def _emit_outbox_event(
    db: Session,
    *,
    tenant_id: str,
    event_type: str,
    stream_id: str,
    payload: dict[str, Any],
) -> None:
    """Publish a validated event envelope into the transactional outbox table."""
    event_id = f"evt-{uuid.uuid4().hex}"
    envelope = {
        "contract_version": "1.1",
        "event_id": event_id,
        "tenant_id": tenant_id,
        "task_id": None,
        "run_id": None,
        "attempt": None,
        "producer_id": "ppm_location_service",
        "producer_seq": 1,
        "occurred_at": dt.datetime.now(dt.UTC).isoformat(),
        "event_type": event_type,
        "payload": payload,
    }
    db.execute(
        outbox.insert().values(
            tenant_id=tenant_id,
            event_id=event_id,
            stream_id=stream_id,
            event_type=event_type,
            payload=envelope,
        )
    )


class LocationService:
    """Domain service managing physical sources and project location registrations."""

    @staticmethod
    def register_source(
        db: Session,
        *,
        tenant_id: str,
        source_id: str,
        canonical_root: str,
        repo_identity: str,
        principal_id: str = "principal-system",
        host_id: str = "host-local-001",
        host_os: str = "windows",
    ) -> tuple[dict[str, Any], int]:
        """Register a physical repository/directory root (L3 Owner decision 2026-11-08).

        Enforces:
        - One physical directory / repo identity = one source per tenant (409 on duplicate within tenant).
        - Cross-tenant use of the same folder: ALLOWED ONLY WITH APPROVAL by owning tenant's owner (L3).
        - First tenant to register canonical root on a host owns it in host_root_ownership table.
        - Second tenant registering same root gets source in state pending_share (HTTP 202) + share request.
        - Response never reveals the owning tenant's id or name.
        """
        clean_root = canonicalize_root(canonical_root, host_os=host_os)

        # Check existing source by source_id (within tenant and globally)
        existing_src = db.execute(
            sa.select(sources).where(sources.c.source_id == source_id)
        ).mappings().first()

        if existing_src is not None:
            if existing_src["tenant_id"] != tenant_id:
                raise SourceAlreadyRegisteredError(
                    f"source_id '{source_id}' is already registered by another tenant"
                )
            if (
                existing_src["canonical_root"] == clean_root
                and existing_src["repo_identity"] == repo_identity
            ):
                return LocationService._attach_source_state(db, tenant_id, dict(existing_src)), 200
            raise SourceAlreadyRegisteredError(
                f"source_id '{source_id}' already registered with differing root or repo_identity"
            )

        # Enforce: one physical directory = one source per tenant
        conflicting_root = db.execute(
            sa.select(sources).where(
                sources.c.tenant_id == tenant_id,
                sources.c.canonical_root == clean_root,
            )
        ).mappings().first()

        if conflicting_root is not None:
            raise SourceAlreadyRegisteredError(
                f"Directory '{clean_root}' is already registered under source_id '{conflicting_root['source_id']}'"
            )

        # Enforce: one repo_identity = one source per tenant
        conflicting_repo = db.execute(
            sa.select(sources).where(
                sources.c.tenant_id == tenant_id,
                sources.c.repo_identity == repo_identity,
            )
        ).mappings().first()

        if conflicting_repo is not None:
            raise SourceAlreadyRegisteredError(
                f"repo_identity '{repo_identity}' is already registered under source_id '{conflicting_repo['source_id']}'"
            )

        now = dt.datetime.now(dt.UTC)

        # Check host-scoped root ownership table (L3)
        host_row = db.execute(
            sa.select(host_root_ownership).where(
                host_root_ownership.c.host_id == host_id,
                host_root_ownership.c.canonical_root_key == clean_root,
            )
        ).mappings().first()

        if host_row is None:
            # First tenant on host -> becomes owner of this canonical root (L3)
            db.execute(
                host_root_ownership.insert().values(
                    host_id=host_id,
                    canonical_root_key=clean_root,
                    canonical_root=clean_root,
                    owner_tenant_id=tenant_id,
                    owner_source_id=source_id,
                    created_at=now,
                )
            )
            db.execute(
                sources.insert().values(
                    source_id=source_id,
                    tenant_id=tenant_id,
                    canonical_root=clean_root,
                    repo_identity=repo_identity,
                    created_at=now,
                    updated_at=now,
                )
            )
            _emit_outbox_event(
                db,
                tenant_id=tenant_id,
                event_type="ppm.source.registered",
                stream_id=f"source:{source_id}",
                payload={
                    "source_id": source_id,
                    "canonical_root": clean_root,
                    "repo_identity": repo_identity,
                    "state": "active",
                },
            )
            db.commit()

            return {
                "source_id": source_id,
                "tenant_id": tenant_id,
                "canonical_root": clean_root,
                "repo_identity": repo_identity,
                "state": "active",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }, 201

        # Root is already registered on this host by a tenant
        owner_tenant = host_row["owner_tenant_id"]
        if owner_tenant == tenant_id:
            # Same tenant attempting to register same root twice under different source_id
            raise SourceAlreadyRegisteredError(
                f"Directory '{clean_root}' is already registered under source_id '{host_row['owner_source_id']}'"
            )

        # Different tenant -> cross-tenant registration requires share approval (L3)
        share_id = f"shr-{uuid.uuid4().hex[:12]}"
        db.execute(
            sources.insert().values(
                source_id=source_id,
                tenant_id=tenant_id,
                canonical_root=clean_root,
                repo_identity=repo_identity,
                created_at=now,
                updated_at=now,
            )
        )
        db.execute(
            source_shares.insert().values(
                id=share_id,
                host_id=host_id,
                canonical_root_key=clean_root,
                owner_tenant_id=owner_tenant,
                owner_source_id=host_row["owner_source_id"],
                requester_tenant_id=tenant_id,
                requester_source_id=source_id,
                requester_principal_id=principal_id,
                state="pending_share",
                created_at=now,
            )
        )

        # Outbox event in OWNING tenant stream (L3)
        _emit_outbox_event(
            db,
            tenant_id=owner_tenant,
            event_type="ppm.source_share.requested",
            stream_id=f"tenant-{owner_tenant}",
            payload={
                "share_id": share_id,
                "host_id": host_id,
                "canonical_root": clean_root,
                "requester_tenant_id": tenant_id,
                "requester_source_id": source_id,
                "state": "pending_share",
            },
        )

        # Outbox event in REQUESTER tenant stream
        _emit_outbox_event(
            db,
            tenant_id=tenant_id,
            event_type="ppm.source_share.requested",
            stream_id=f"tenant-{tenant_id}",
            payload={
                "share_id": share_id,
                "source_id": source_id,
                "state": "pending_share",
            },
        )

        # Audit log in owning tenant's stream (L3)
        logger.info(
            "Source share requested: share_id=%s host=%s root=%s requester_tenant=%s",
            share_id,
            host_id,
            clean_root,
            tenant_id,
            extra={
                "tenant_id": owner_tenant,
                "principal_id": principal_id,
            },
        )
        db.commit()

        # HTTP 202 response: NEVER reveals owning tenant id or name (L3)
        return {
            "source_id": source_id,
            "tenant_id": tenant_id,
            "canonical_root": clean_root,
            "repo_identity": repo_identity,
            "state": "pending_share",
            "share_id": share_id,
            "message": "Source registered in state pending_share; requires approval by host root owner",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }, 202

    @staticmethod
    def _attach_source_state(db: Session, tenant_id: str, row_dict: dict[str, Any]) -> dict[str, Any]:
        """Attach runtime sharing state to a source row."""
        result = dict(row_dict)
        source_id = result["source_id"]
        share = db.execute(
            sa.select(source_shares).where(
                source_shares.c.requester_tenant_id == tenant_id,
                source_shares.c.requester_source_id == source_id,
            )
        ).mappings().first()

        if share is not None:
            result["state"] = share["state"]
            result["share_id"] = share["id"]
        else:
            result["state"] = "active"

        if isinstance(result.get("created_at"), dt.datetime):
            result["created_at"] = result["created_at"].isoformat()
        if isinstance(result.get("updated_at"), dt.datetime):
            result["updated_at"] = result["updated_at"].isoformat()
        return result

    @staticmethod
    def get_source(db: Session, *, tenant_id: str, source_id: str) -> dict[str, Any] | None:
        """Retrieve a physical source record scoped strictly to tenant."""
        row = db.execute(
            sa.select(sources).where(
                sources.c.tenant_id == tenant_id,
                sources.c.source_id == source_id,
            )
        ).mappings().first()
        if not row:
            return None
        return LocationService._attach_source_state(db, tenant_id, dict(row))

    @staticmethod
    def list_sources(db: Session, *, tenant_id: str) -> list[dict[str, Any]]:
        """List all physical sources for a tenant."""
        rows = db.execute(
            sa.select(sources)
            .where(sources.c.tenant_id == tenant_id)
            .order_by(sources.c.source_id)
        ).mappings().all()
        return [LocationService._attach_source_state(db, tenant_id, dict(r)) for r in rows]

    @staticmethod
    def register_location(
        db: Session,
        *,
        tenant_id: str,
        data: dict[str, Any],
        principal_id: str,
        host_os: str = "windows",
    ) -> dict[str, Any]:
        """Create a location registration in state proposed (or suspended upon C42 identity conflict)."""
        loc_data = dict(data)

        # Enforce server-side principal resolution
        principal_ref = f"principal:{principal_id}"
        loc_data["registered_by_ref"] = principal_ref

        # Initial registration must start with proposed state and no approval
        # If client passes 'active' without approval -> reject per semantic rules
        if loc_data.get("state") == "active" and not loc_data.get("approved_by_ref"):
            raise LocationServiceError(
                "active location requires approval",
                code="LOCATION_ACTIVE_REQUIRES_APPROVAL",
            )

        loc_data["state"] = "proposed"
        loc_data["approved_by_ref"] = None

        # Resolve host_os: explicit in data, or infer from root_path format
        determined_os = data.get("host_os") or host_os
        raw_root = str(loc_data.get("root_path", ""))
        if not data.get("host_os") and raw_root.startswith("/"):
            determined_os = "linux"

        # Validate path and aliases
        clean_root = canonicalize_root(raw_root, host_os=determined_os)
        loc_data["root_path"] = clean_root

        artifact_aliases = loc_data.get("artifact_aliases", {})
        notes_globs = loc_data.get("notes_globs", [])
        validate_aliases(artifact_aliases, notes_globs)

        # Validate against LocationRegistration contract schema and semantic rules
        try:
            model = LocationRegistration.model_validate(loc_data)
            semantic_checks("location-registration", model)
        except Exception as exc:
            raise LocationServiceError(f"Invalid location registration contract: {exc}", code="CONTRACT_VIOLATION") from exc

        source_id = model.source_id
        project_id = model.project_id
        metadata_root = model.metadata_root
        location_id = model.location_id
        repo_ident = model.identity.repo_identity or f"repo:{source_id}"

        # Resolve or ensure source exists
        source_row = db.execute(
            sa.select(sources).where(
                sources.c.tenant_id == tenant_id,
                sources.c.source_id == source_id,
            )
        ).mappings().first()

        if source_row is None:
            # Auto-register source via register_source to enforce ownership & share rules (L3)
            LocationService.register_source(
                db,
                tenant_id=tenant_id,
                source_id=source_id,
                canonical_root=clean_root,
                repo_identity=repo_ident,
                principal_id=principal_id,
                host_id=model.host_id,
                host_os=determined_os,
            )
            source_repo_identity = repo_ident
        else:
            source_repo_identity = source_row["repo_identity"]
            # Enforce that location root matches the source root
            if source_row["canonical_root"] != clean_root:
                raise SourceAlreadyRegisteredError(
                    f"root_path '{clean_root}' does not match registered source root '{source_row['canonical_root']}'"
                )

        # Enforce: UNIQUE (tenant_id, source_id, metadata_root)
        existing_scope = db.execute(
            sa.select(project_scopes).where(
                project_scopes.c.tenant_id == tenant_id,
                project_scopes.c.source_id == source_id,
                project_scopes.c.metadata_root == metadata_root,
            )
        ).mappings().first()

        if existing_scope is not None:
            if existing_scope["project_id"] != project_id:
                raise MetadataRootConflictError(
                    f"metadata_root '{metadata_root}' is already claimed by project '{existing_scope['project_id']}'"
                )
            raise DuplicateLocationError(
                f"duplicate location: same source '{source_id}' and metadata_root '{metadata_root}'"
            )

        # C42: Identity Conflict Check
        # Check if project_id is already registered on a source with differing repo_identity
        existing_project_scopes = db.execute(
            sa.select(project_scopes.c.source_id, sources.c.repo_identity)
            .select_from(
                project_scopes.join(
                    sources,
                    sa.and_(
                        project_scopes.c.tenant_id == sources.c.tenant_id,
                        project_scopes.c.source_id == sources.c.source_id,
                    ),
                )
            )
            .where(
                project_scopes.c.tenant_id == tenant_id,
                project_scopes.c.project_id == project_id,
            )
        ).all()

        is_identity_conflict = False
        conflict_source = ""
        conflict_repo = ""
        for s_id, r_id in existing_project_scopes:
            if r_id != source_repo_identity:
                is_identity_conflict = True
                conflict_source = s_id
                conflict_repo = r_id
                break

        final_state = "suspended" if is_identity_conflict else "proposed"
        now = dt.datetime.now(dt.UTC)

        # Insert project_scope record (enforcing DB constraints and composite FK)
        db.execute(
            project_scopes.insert().values(
                id=location_id,
                tenant_id=tenant_id,
                source_id=source_id,
                project_id=project_id,
                metadata_root=metadata_root,
                scan_scope=json.dumps(model.scan_scope),
                status=final_state,
                created_at=now,
                updated_at=now,
            )
        )

        # Insert location_registrations record
        reg_dict = model.model_dump(mode="json")
        reg_dict["state"] = final_state
        reg_dict["registered_by_ref"] = principal_ref

        db.execute(
            location_registrations.insert().values(
                id=location_id,
                tenant_id=tenant_id,
                source_id=source_id,
                project_id=project_id,
                metadata_root=metadata_root,
                state=final_state,
                revision=model.revision,
                registered_by_ref=principal_ref,
                approved_by_ref=None,
                host_id=model.host_id,
                connector_id=model.connector_id,
                root_path=clean_root,
                scan_scope=model.scan_scope,
                artifact_aliases=model.artifact_aliases.model_dump(mode="json"),
                notes_globs=model.notes_globs,
                deny_globs=model.deny_globs,
                classification=model.classification,
                egress_policy_ref=model.egress_policy_ref,
                identity=model.identity.model_dump(mode="json"),
                follow_symlinks=model.follow_symlinks,
                allow_hardlinked_files=model.allow_hardlinked_files,
                max_file_bytes=model.max_file_bytes,
                created_at=now,
                updated_at=now,
            )
        )

        if is_identity_conflict:
            conflict_detail = {
                "reason": "duplicate project_id observed across distinct repositories",
                "claimed_project_id": project_id,
                "conflicting_source_id": conflict_source,
                "conflicting_repo_identity": conflict_repo,
                "new_source_id": source_id,
                "new_repo_identity": source_repo_identity,
            }
            db.execute(
                identity_conflicts.insert().values(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    location_id=location_id,
                    source_id=source_id,
                    repo_identity=source_repo_identity,
                    conflicting_source_id=conflict_source,
                    conflicting_repo_identity=conflict_repo,
                    detail=conflict_detail,
                    created_at=now,
                )
            )
            _emit_outbox_event(
                db,
                tenant_id=tenant_id,
                event_type="ppm.identity_conflict.detected",
                stream_id=f"location:{location_id}",
                payload=conflict_detail,
            )
        else:
            _emit_outbox_event(
                db,
                tenant_id=tenant_id,
                event_type="ppm.location.proposed",
                stream_id=f"location:{location_id}",
                payload={
                    "location_id": location_id,
                    "project_id": project_id,
                    "source_id": source_id,
                    "metadata_root": metadata_root,
                    "state": "proposed",
                },
            )

        db.commit()
        return reg_dict

    @staticmethod
    def approve_location(
        db: Session,
        *,
        tenant_id: str,
        location_id: str,
        principal: Principal,
    ) -> dict[str, Any]:
        """Approve a proposed location registration (proposed -> active).

        Enforces:
        - Tenant isolation (cross-tenant -> 404).
        - Approval authority: owner/admin only (403).
        - Registrant cannot approve own registration (403).
        - Quarantined / identity conflict locations cannot be approved.
        """
        loc = db.execute(
            sa.select(location_registrations).where(
                location_registrations.c.tenant_id == tenant_id,
                location_registrations.c.id == location_id,
            )
        ).mappings().first()

        if loc is None:
            raise LocationNotFoundError(f"Location '{location_id}' not found")

        # 1. Authority check: owner or admin only
        if not ("owner" in principal.roles or "admin" in principal.roles):
            raise UnauthorizedApprovalError(
                f"Principal '{principal.principal_id}' lacks owner/admin role required for approval"
            )

        # 2. Registrant check: registrant may not approve its own registration
        reg_by = loc["registered_by_ref"]
        caller_ref = f"principal:{principal.principal_id}"
        if principal.principal_id == reg_by or caller_ref == reg_by:
            raise RegistrantApprovalForbiddenError(
                f"Principal '{principal.principal_id}' cannot approve own location registration"
            )

        # 3. State check
        if loc["state"] == "suspended":
            raise InvalidLocationStateError(
                f"Location '{location_id}' is quarantined due to identity_conflict and cannot be approved"
            )

        if loc["state"] != "proposed":
            raise InvalidLocationStateError(
                f"Location '{location_id}' is in state '{loc['state']}'; only proposed locations can be approved"
            )

        # 4. Check if underlying source is a cross-tenant share requiring approval (L3)
        share_row = db.execute(
            sa.select(source_shares).where(
                source_shares.c.requester_tenant_id == tenant_id,
                source_shares.c.requester_source_id == loc["source_id"],
            )
        ).mappings().first()

        if share_row is not None and share_row["state"] != "approved":
            raise UnapprovedSourceLocationError(
                f"Cannot activate location '{location_id}': underlying source '{loc['source_id']}' "
                f"is in state '{share_row['state']}' and has not been approved by the root owner"
            )

        now = dt.datetime.now(dt.UTC)
        approver_ref = f"principal:{principal.principal_id}"

        # Update location_registrations
        db.execute(
            location_registrations.update()
            .where(
                location_registrations.c.tenant_id == tenant_id,
                location_registrations.c.id == location_id,
            )
            .values(
                state="active",
                approved_by_ref=approver_ref,
                updated_at=now,
            )
        )

        # Update project_scopes
        db.execute(
            project_scopes.update()
            .where(
                project_scopes.c.tenant_id == tenant_id,
                project_scopes.c.id == location_id,
            )
            .values(
                status="active",
                updated_at=now,
            )
        )

        # Emit outbox event
        _emit_outbox_event(
            db,
            tenant_id=tenant_id,
            event_type="ppm.location.approved",
            stream_id=f"location:{location_id}",
            payload={
                "location_id": location_id,
                "project_id": loc["project_id"],
                "source_id": loc["source_id"],
                "approved_by_ref": approver_ref,
                "state": "active",
            },
        )

        db.commit()

        # Re-fetch updated record
        updated_loc = db.execute(
            sa.select(location_registrations).where(
                location_registrations.c.tenant_id == tenant_id,
                location_registrations.c.id == location_id,
            )
        ).mappings().first()

        assert updated_loc is not None
        return LocationService._to_contract_dict(dict(updated_loc))

    @staticmethod
    def get_location(db: Session, *, tenant_id: str, location_id: str) -> dict[str, Any] | None:
        """Retrieve a location registration by id scoped strictly to tenant."""
        row = db.execute(
            sa.select(location_registrations).where(
                location_registrations.c.tenant_id == tenant_id,
                location_registrations.c.id == location_id,
            )
        ).mappings().first()
        if not row:
            return None
        return LocationService._to_contract_dict(dict(row))

    @staticmethod
    def list_locations(
        db: Session,
        *,
        tenant_id: str,
        project_id: str | None = None,
        source_id: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        """List locations for tenant with optional filters."""
        stmt = sa.select(location_registrations).where(
            location_registrations.c.tenant_id == tenant_id
        )
        if project_id:
            stmt = stmt.where(location_registrations.c.project_id == project_id)
        if source_id:
            stmt = stmt.where(location_registrations.c.source_id == source_id)
        if state:
            stmt = stmt.where(location_registrations.c.state == state)

        stmt = stmt.order_by(location_registrations.c.id)
        rows = db.execute(stmt).mappings().all()
        return [LocationService._to_contract_dict(dict(r)) for r in rows]

    @staticmethod
    def get_identity_conflicts(
        db: Session,
        *,
        tenant_id: str,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List identity conflict records for tenant."""
        stmt = sa.select(identity_conflicts).where(
            identity_conflicts.c.tenant_id == tenant_id
        )
        if project_id:
            stmt = stmt.where(identity_conflicts.c.project_id == project_id)
        rows = db.execute(stmt).mappings().all()
        return [dict(r) for r in rows]

    @staticmethod
    def approve_share(
        db: Session,
        *,
        share_id: str,
        principal: Principal,
    ) -> dict[str, Any]:
        """Approve a cross-tenant source share request (L3 Owner decision 2026-11-08).

        Enforces:
        - Only a principal with role 'owner' in the OWNING tenant may approve (403).
        - Requesting tenant cannot approve its own request (403).
        - Cross-tenant / unknown share -> 404.
        - Emits outbox event and audit line in owning tenant's stream.
        """
        share = db.execute(
            sa.select(source_shares).where(source_shares.c.id == share_id)
        ).mappings().first()

        if share is None:
            raise ShareNotFoundError(f"Share request '{share_id}' not found")

        # Requesting tenant cannot approve own request (403)
        if principal.tenant_id == share["requester_tenant_id"]:
            raise ShareApprovalForbiddenError(
                f"Requesting tenant '{principal.tenant_id}' cannot approve own share request"
            )

        # Cross-tenant check: must belong to owning tenant (404)
        if principal.tenant_id != share["owner_tenant_id"]:
            raise ShareNotFoundError(f"Share request '{share_id}' not found")

        # Authority check: only owner in owning tenant (403)
        if "owner" not in principal.roles:
            raise ShareApprovalForbiddenError(
                f"Principal '{principal.principal_id}' lacks owner role in owning tenant required to approve share"
            )

        now = dt.datetime.now(dt.UTC)
        db.execute(
            source_shares.update()
            .where(source_shares.c.id == share_id)
            .values(
                state="approved",
                decided_at=now,
                decided_by_principal_id=principal.principal_id,
            )
        )

        _emit_outbox_event(
            db,
            tenant_id=share["owner_tenant_id"],
            event_type="ppm.source_share.approved",
            stream_id=f"tenant-{share['owner_tenant_id']}",
            payload={
                "share_id": share_id,
                "owner_tenant_id": share["owner_tenant_id"],
                "requester_tenant_id": share["requester_tenant_id"],
                "requester_source_id": share["requester_source_id"],
                "decided_by_principal_id": principal.principal_id,
                "state": "approved",
            },
        )
        _emit_outbox_event(
            db,
            tenant_id=share["requester_tenant_id"],
            event_type="ppm.source_share.approved",
            stream_id=f"tenant-{share['requester_tenant_id']}",
            payload={
                "share_id": share_id,
                "requester_source_id": share["requester_source_id"],
                "state": "approved",
            },
        )

        logger.info(
            "Source share %s approved by %s for requester tenant %s",
            share_id,
            principal.principal_id,
            share["requester_tenant_id"],
            extra={
                "tenant_id": share["owner_tenant_id"],
                "principal_id": principal.principal_id,
            },
        )
        db.commit()

        updated = db.execute(
            sa.select(source_shares).where(source_shares.c.id == share_id)
        ).mappings().first()
        assert updated is not None
        return LocationService._format_share_dict(dict(updated), caller_tenant_id=principal.tenant_id)

    @staticmethod
    def reject_share(
        db: Session,
        *,
        share_id: str,
        principal: Principal,
    ) -> dict[str, Any]:
        """Reject a cross-tenant source share request (L3)."""
        share = db.execute(
            sa.select(source_shares).where(source_shares.c.id == share_id)
        ).mappings().first()

        if share is None:
            raise ShareNotFoundError(f"Share request '{share_id}' not found")

        if principal.tenant_id == share["requester_tenant_id"]:
            raise ShareApprovalForbiddenError(
                f"Requesting tenant '{principal.tenant_id}' cannot reject own share request"
            )

        if principal.tenant_id != share["owner_tenant_id"]:
            raise ShareNotFoundError(f"Share request '{share_id}' not found")

        if "owner" not in principal.roles:
            raise ShareApprovalForbiddenError(
                f"Principal '{principal.principal_id}' lacks owner role in owning tenant required to reject share"
            )

        now = dt.datetime.now(dt.UTC)
        db.execute(
            source_shares.update()
            .where(source_shares.c.id == share_id)
            .values(
                state="rejected",
                decided_at=now,
                decided_by_principal_id=principal.principal_id,
            )
        )

        _emit_outbox_event(
            db,
            tenant_id=share["owner_tenant_id"],
            event_type="ppm.source_share.rejected",
            stream_id=f"tenant-{share['owner_tenant_id']}",
            payload={
                "share_id": share_id,
                "owner_tenant_id": share["owner_tenant_id"],
                "requester_tenant_id": share["requester_tenant_id"],
                "requester_source_id": share["requester_source_id"],
                "decided_by_principal_id": principal.principal_id,
                "state": "rejected",
            },
        )

        logger.info(
            "Source share %s rejected by %s",
            share_id,
            principal.principal_id,
            extra={
                "tenant_id": share["owner_tenant_id"],
                "principal_id": principal.principal_id,
            },
        )
        db.commit()

        updated = db.execute(
            sa.select(source_shares).where(source_shares.c.id == share_id)
        ).mappings().first()
        assert updated is not None
        return LocationService._format_share_dict(dict(updated), caller_tenant_id=principal.tenant_id)

    @staticmethod
    def revoke_share(
        db: Session,
        *,
        share_id: str,
        principal: Principal,
    ) -> dict[str, Any]:
        """Revoke an approved share, moving requester's active locations to suspended (L3)."""
        share = db.execute(
            sa.select(source_shares).where(source_shares.c.id == share_id)
        ).mappings().first()

        if share is None:
            raise ShareNotFoundError(f"Share request '{share_id}' not found")

        if principal.tenant_id == share["requester_tenant_id"]:
            raise ShareApprovalForbiddenError(
                f"Requesting tenant '{principal.tenant_id}' cannot revoke share request"
            )

        if principal.tenant_id != share["owner_tenant_id"]:
            raise ShareNotFoundError(f"Share request '{share_id}' not found")

        if "owner" not in principal.roles:
            raise ShareApprovalForbiddenError(
                f"Principal '{principal.principal_id}' lacks owner role in owning tenant required to revoke share"
            )

        now = dt.datetime.now(dt.UTC)
        db.execute(
            source_shares.update()
            .where(source_shares.c.id == share_id)
            .values(
                state="revoked",
                revoked_at=now,
                revoked_by_principal_id=principal.principal_id,
            )
        )

        # Revocation moves requester's active locations on that source to suspended (L3)
        db.execute(
            location_registrations.update()
            .where(
                location_registrations.c.tenant_id == share["requester_tenant_id"],
                location_registrations.c.source_id == share["requester_source_id"],
                location_registrations.c.state == "active",
            )
            .values(
                state="suspended",
                updated_at=now,
            )
        )
        db.execute(
            project_scopes.update()
            .where(
                project_scopes.c.tenant_id == share["requester_tenant_id"],
                project_scopes.c.source_id == share["requester_source_id"],
                project_scopes.c.status == "active",
            )
            .values(
                status="suspended",
                updated_at=now,
            )
        )

        _emit_outbox_event(
            db,
            tenant_id=share["owner_tenant_id"],
            event_type="ppm.source_share.revoked",
            stream_id=f"tenant-{share['owner_tenant_id']}",
            payload={
                "share_id": share_id,
                "owner_tenant_id": share["owner_tenant_id"],
                "requester_tenant_id": share["requester_tenant_id"],
                "requester_source_id": share["requester_source_id"],
                "revoked_by_principal_id": principal.principal_id,
                "state": "revoked",
            },
        )
        _emit_outbox_event(
            db,
            tenant_id=share["requester_tenant_id"],
            event_type="ppm.source_share.revoked",
            stream_id=f"tenant-{share['requester_tenant_id']}",
            payload={
                "share_id": share_id,
                "requester_source_id": share["requester_source_id"],
                "state": "revoked",
            },
        )

        logger.info(
            "Source share %s revoked by %s; suspended active locations",
            share_id,
            principal.principal_id,
            extra={
                "tenant_id": share["owner_tenant_id"],
                "principal_id": principal.principal_id,
            },
        )
        db.commit()

        updated = db.execute(
            sa.select(source_shares).where(source_shares.c.id == share_id)
        ).mappings().first()
        assert updated is not None
        return LocationService._format_share_dict(dict(updated), caller_tenant_id=principal.tenant_id)

    @staticmethod
    def get_share(db: Session, *, share_id: str, principal: Principal) -> dict[str, Any]:
        """Retrieve share details ensuring owner identity is never leaked to requester (L3)."""
        share = db.execute(
            sa.select(source_shares).where(source_shares.c.id == share_id)
        ).mappings().first()

        if share is None:
            raise ShareNotFoundError(f"Share request '{share_id}' not found")

        is_owner = principal.tenant_id == share["owner_tenant_id"]
        is_requester = principal.tenant_id == share["requester_tenant_id"]

        if not (is_owner or is_requester):
            raise ShareNotFoundError(f"Share request '{share_id}' not found")

        return LocationService._format_share_dict(dict(share), caller_tenant_id=principal.tenant_id)

    @staticmethod
    def list_shares(db: Session, *, principal: Principal) -> list[dict[str, Any]]:
        """List share requests visible to the authenticated principal."""
        rows = db.execute(
            sa.select(source_shares).where(
                sa.or_(
                    source_shares.c.owner_tenant_id == principal.tenant_id,
                    source_shares.c.requester_tenant_id == principal.tenant_id,
                )
            ).order_by(source_shares.c.created_at.desc())
        ).mappings().all()

        return [
            LocationService._format_share_dict(dict(r), caller_tenant_id=principal.tenant_id)
            for r in rows
        ]

    @staticmethod
    def _format_share_dict(share_row: dict[str, Any], caller_tenant_id: str) -> dict[str, Any]:
        """Format share record and mask owner identity if requested by the requester (L3)."""
        res = dict(share_row)
        for ts_key in ("created_at", "decided_at", "revoked_at"):
            if isinstance(res.get(ts_key), dt.datetime):
                res[ts_key] = res[ts_key].isoformat()

        if res.get("requester_tenant_id") == caller_tenant_id and res.get("owner_tenant_id") != caller_tenant_id:
            res.pop("owner_tenant_id", None)
            res.pop("owner_source_id", None)
        return res

    @staticmethod
    def _to_contract_dict(row: dict[str, Any]) -> dict[str, Any]:
        """Format a database row to match contracts/location-registration.schema.json."""
        return {
            "contract_version": "1.1",
            "location_id": row["id"],
            "project_id": row["project_id"],
            "source_id": row["source_id"],
            "host_id": row["host_id"],
            "connector_id": row["connector_id"],
            "root_path": row["root_path"],
            "metadata_root": row["metadata_root"],
            "scan_scope": row["scan_scope"],
            "artifact_aliases": row["artifact_aliases"],
            "notes_globs": row["notes_globs"],
            "follow_symlinks": row["follow_symlinks"],
            "allow_hardlinked_files": row["allow_hardlinked_files"],
            "max_file_bytes": row["max_file_bytes"],
            "deny_globs": row["deny_globs"],
            "classification": row["classification"],
            "egress_policy_ref": row["egress_policy_ref"],
            "identity": row["identity"],
            "state": row["state"],
            "revision": row["revision"],
            "registered_by_ref": row["registered_by_ref"],
            "approved_by_ref": row["approved_by_ref"],
        }
