"""Tests for leases table constraints (C15, C21).

Enforces:
- leases: fencing_token column present and NOT NULL.
- leases: mutual exclusion per resource via uq_leases_resource.
- leases: different tenants can lease the same resource identifier.
"""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from eap.db import leases


def _assert_constraint(exc_info: pytest.ExceptionInfo[IntegrityError], constraint_name: str) -> None:
    orig = exc_info.value.orig
    diag_name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    assert (
        diag_name == constraint_name or constraint_name in str(exc_info.value)
    ), f"Expected constraint '{constraint_name}', got diag={diag_name}, str={exc_info.value}"


def test_leases_fencing_token_present_and_not_null(db_engine: sa.Engine) -> None:
    """leases table has fencing_token column and it is NOT NULL."""
    inspector = inspect(db_engine)
    columns = {col["name"]: col for col in inspector.get_columns("leases")}
    assert "fencing_token" in columns, "leases must have fencing_token column"
    assert columns["fencing_token"]["nullable"] is False, "fencing_token must be NOT NULL"


def test_leases_resource_unique_per_tenant(db_conn: sa.Connection) -> None:
    """Duplicate lease on (tenant_id, resource_type, resource_id) is rejected."""
    tenant = "tenant-leases"
    res_type = "run"
    res_id = "run-101"
    now = dt.datetime.now(dt.UTC)
    expires = now + dt.timedelta(seconds=60)

    db_conn.execute(
        leases.insert().values(
            tenant_id=tenant,
            resource_type=res_type,
            resource_id=res_id,
            fencing_token=1,
            holder_id="worker-a",
            expires_at=expires,
        )
    )

    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            leases.insert().values(
                tenant_id=tenant,
                resource_type=res_type,
                resource_id=res_id,  # DUPLICATE
                fencing_token=2,
                holder_id="worker-b",
                expires_at=expires,
            )
        )

    _assert_constraint(exc_info, "uq_leases_resource")

    # Allowed in different tenant
    db_conn.execute(
        leases.insert().values(
            tenant_id="tenant-leases-other",
            resource_type=res_type,
            resource_id=res_id,
            fencing_token=1,
            holder_id="worker-c",
            expires_at=expires,
        )
    )
