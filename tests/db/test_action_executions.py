"""Tests for action_executions constraints (DG1-C3, C29).

Enforces:
- DG1-C3 / C29: Two action_executions with the same (tenant_id, operation_key)
  cause a unique violation on uq_action_executions_key.
- Same operation_key across different tenants is permitted.
- Check constraint on action_executions.state (ck_action_executions_state).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from eap.db import action_executions


def _assert_constraint(exc_info: pytest.ExceptionInfo[IntegrityError], constraint_name: str) -> None:
    orig = exc_info.value.orig
    diag_name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    assert (
        diag_name == constraint_name or constraint_name in str(exc_info.value)
    ), f"Expected constraint '{constraint_name}', got diag={diag_name}, str={exc_info.value}"


def test_dg1_c3_duplicate_operation_key_rejected(db_conn: sa.Connection) -> None:
    """DG1-C3 / C29: Two action_executions with same (tenant_id, operation_key) -> rejected."""
    tenant = "tenant-corp"
    op_key = "99842d00b17b5a263e9d4492ce3bc15fd1681b44dd0af40a8fefe378b5516fdd"

    # Insert initial action execution record
    db_conn.execute(
        action_executions.insert().values(
            tenant_id=tenant,
            operation_key=op_key,
            payload_hash="56330e2b4ebffade1ad3d90131e6073fc5ec8bb149b229524b565fd829e50f08",
            tool_id="engineering.create_pr",
            resource_ref="repo:example",
            state="running",
        )
    )

    # Attempt to insert second action execution with same (tenant_id, operation_key) but divergent payload_hash
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            action_executions.insert().values(
                tenant_id=tenant,
                operation_key=op_key,
                payload_hash="15329b88e5b0ce51f22ce4c524791cc529fe0922a59e62dcf8362fa2cd9ff441",
                tool_id="engineering.create_pr",
                resource_ref="repo:example-divergent",
                state="running",
            )
        )

    _assert_constraint(exc_info, "uq_action_executions_key")


def test_dg1_c3_same_operation_key_different_tenant_allowed(db_conn: sa.Connection) -> None:
    """Same operation_key in a different tenant is allowed."""
    op_key = "op_key_shared_token_abc"

    db_conn.execute(
        action_executions.insert().values(
            tenant_id="tenant-alpha",
            operation_key=op_key,
            payload_hash="payload_hash_alpha",
            tool_id="system.echo",
            resource_ref="resource-1",
            state="running",
        )
    )

    # Different tenant should succeed
    db_conn.execute(
        action_executions.insert().values(
            tenant_id="tenant-beta",
            operation_key=op_key,
            payload_hash="payload_hash_beta",
            tool_id="system.echo",
            resource_ref="resource-2",
            state="running",
        )
    )

    rows = db_conn.execute(
        sa.select(action_executions).where(action_executions.c.operation_key == op_key)
    ).fetchall()
    assert len(rows) == 2


def test_action_executions_state_check_constraint(db_conn: sa.Connection) -> None:
    """Invalid action_executions state violates ck_action_executions_state."""
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            action_executions.insert().values(
                tenant_id="tenant-alpha",
                operation_key="op_invalid_state",
                payload_hash="payload_hash_val",
                tool_id="test.tool",
                resource_ref="res",
                state="bogus_state",
            )
        )

    _assert_constraint(exc_info, "ck_action_executions_state")
