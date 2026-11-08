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
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from eap.api.dependencies import Principal
from eap.contracts.models.location_registration import LocationRegistration
from eap.contracts.rules import semantic_checks
from eap.db import (
    identity_conflicts,
    location_registrations,
    outbox,
    project_scopes,
    sources,
)
from eap.ppm.path import canonicalize_root, validate_aliases


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
        host_os: str = "windows",
    ) -> dict[str, Any]:
        """Register a physical repository/directory root.

        Enforces: One physical directory / repo identity = one source per tenant (409 on conflict).
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
                return dict(existing_src)
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
            },
        )
        db.commit()

        return {
            "source_id": source_id,
            "tenant_id": tenant_id,
            "canonical_root": clean_root,
            "repo_identity": repo_identity,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

    @staticmethod
    def get_source(db: Session, *, tenant_id: str, source_id: str) -> dict[str, Any] | None:
        """Retrieve a physical source record scoped strictly to tenant."""
        row = db.execute(
            sa.select(sources).where(
                sources.c.tenant_id == tenant_id,
                sources.c.source_id == source_id,
            )
        ).mappings().first()
        return dict(row) if row else None

    @staticmethod
    def list_sources(db: Session, *, tenant_id: str) -> list[dict[str, Any]]:
        """List all physical sources for a tenant."""
        rows = db.execute(
            sa.select(sources)
            .where(sources.c.tenant_id == tenant_id)
            .order_by(sources.c.source_id)
        ).mappings().all()
        return [dict(r) for r in rows]

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
            # Check if source_id is already claimed globally by another tenant
            global_src = db.execute(
                sa.select(sources).where(sources.c.source_id == source_id)
            ).mappings().first()
            if global_src is not None and global_src["tenant_id"] != tenant_id:
                raise SourceAlreadyRegisteredError(
                    f"source_id '{source_id}' is already registered by another tenant"
                )

            # Check if directory or repo_identity already registered under differing source_id
            conflicting_root = db.execute(
                sa.select(sources).where(
                    sources.c.tenant_id == tenant_id,
                    sources.c.canonical_root == clean_root,
                )
            ).mappings().first()
            if conflicting_root is not None:
                raise SourceAlreadyRegisteredError(
                    f"Same physical folder already registered under source_id '{conflicting_root['source_id']}'"
                )

            conflicting_repo = db.execute(
                sa.select(sources).where(
                    sources.c.tenant_id == tenant_id,
                    sources.c.repo_identity == repo_ident,
                )
            ).mappings().first()
            if conflicting_repo is not None:
                raise SourceAlreadyRegisteredError(
                    f"Same repo_identity already registered under source_id '{conflicting_repo['source_id']}'"
                )

            # Auto-register source
            now = dt.datetime.now(dt.UTC)
            db.execute(
                sources.insert().values(
                    source_id=source_id,
                    tenant_id=tenant_id,
                    canonical_root=clean_root,
                    repo_identity=repo_ident,
                    created_at=now,
                    updated_at=now,
                )
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
