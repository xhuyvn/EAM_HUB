"""Tests for project_scopes constraints (C32, W7-3).

Enforces:
- C32 / W7-3: Two scopes with same source, different metadata_root, and different projects -> allowed.
- Duplicate (source, metadata_root) in same tenant -> rejected with uq_project_scopes_source_metadata.
- Single project having multiple scopes -> allowed (owner decision W7-3).
- Scope status check constraint (ck_project_scopes_status).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from eap.db import project_scopes, sources


def _assert_constraint(exc_info: pytest.ExceptionInfo[IntegrityError], constraint_name: str) -> None:
    orig = exc_info.value.orig
    diag_name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    assert (
        diag_name == constraint_name or constraint_name in str(exc_info.value)
    ), f"Expected constraint '{constraint_name}', got diag={diag_name}, str={exc_info.value}"


def test_c32_w7_3_monorepo_scopes_and_cardinality(db_conn: sa.Connection) -> None:
    """C32 / W7-3: Monorepo multiple project scopes and one project with multiple scopes."""
    tenant = "tenant-monorepo"
    source_id = "src-mono-repo-1"

    # Register physical source root
    db_conn.execute(
        sources.insert().values(
            source_id=source_id,
            tenant_id=tenant,
            canonical_root="/repos/monorepo",
            repo_identity="git@github.com:example/monorepo.git",
        )
    )

    # Scope 1: Project A
    db_conn.execute(
        project_scopes.insert().values(
            id="scope-app-a",
            tenant_id=tenant,
            source_id=source_id,
            project_id="proj-app-a",
            metadata_root="apps/app-a/.ppm",
            status="active",
        )
    )

    # Scope 2: Project B (different project, different metadata_root, same source) -> allowed
    db_conn.execute(
        project_scopes.insert().values(
            id="scope-app-b",
            tenant_id=tenant,
            source_id=source_id,
            project_id="proj-app-b",
            metadata_root="apps/app-b/.ppm",
            status="active",
        )
    )

    # Scope 3: W7-3: One project with multiple scopes (Project A has a second scope) -> allowed
    db_conn.execute(
        project_scopes.insert().values(
            id="scope-app-a-sub",
            tenant_id=tenant,
            source_id=source_id,
            project_id="proj-app-a",
            metadata_root="libs/app-a-core/.ppm",
            status="active",
        )
    )

    # Verify all 3 scopes exist
    scopes_count = db_conn.execute(
        sa.select(sa.func.count())
        .select_from(project_scopes)
        .where(project_scopes.c.source_id == source_id)
    ).scalar()
    assert scopes_count == 3


def test_c32_duplicate_source_metadata_root_rejected(db_conn: sa.Connection) -> None:
    """Same (source_id, metadata_root) cannot be claimed twice within same tenant."""
    tenant = "tenant-monorepo-dup"
    source_id = "src-mono-repo-dup"

    db_conn.execute(
        sources.insert().values(
            source_id=source_id,
            tenant_id=tenant,
            canonical_root="/repos/dup_repo",
            repo_identity="git@github.com:example/dup.git",
        )
    )

    db_conn.execute(
        project_scopes.insert().values(
            id="scope-first",
            tenant_id=tenant,
            source_id=source_id,
            project_id="proj-owner-1",
            metadata_root="services/auth/.ppm",
            status="active",
        )
    )

    # Attempt to insert same metadata_root on same source
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            project_scopes.insert().values(
                id="scope-second",
                tenant_id=tenant,
                source_id=source_id,
                project_id="proj-owner-2",
                metadata_root="services/auth/.ppm",  # DUPLICATE
                status="active",
            )
        )

    _assert_constraint(exc_info, "uq_project_scopes_source_metadata")


def test_project_scopes_status_check_constraint(db_conn: sa.Connection) -> None:
    """Invalid status violates ck_project_scopes_status."""
    tenant = "tenant-scope-status"
    source_id = "src-scope-status"

    db_conn.execute(
        sources.insert().values(
            source_id=source_id,
            tenant_id=tenant,
            canonical_root="/repos/status_repo",
            repo_identity="git@github.com:example/status.git",
        )
    )

    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            project_scopes.insert().values(
                id="scope-invalid",
                tenant_id=tenant,
                source_id=source_id,
                project_id="proj-test",
                metadata_root="root/.ppm",
                status="invalid_status",
            )
        )

    _assert_constraint(exc_info, "ck_project_scopes_status")
