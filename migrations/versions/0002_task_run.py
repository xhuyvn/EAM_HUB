"""Align tasks.state CHECK constraint and default with hud-projection enum (ISS-017).

Revision ID: 0002_task_run
Revises: 0001_core
Create Date: 2026-11-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_task_run"
down_revision: str | None = "0001_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TASKS_STATE_CHECK = (
    "state IN ('draft', 'ready', 'active', 'blocked', 'review_required', 'completed', 'failed', 'cancelled', 'queued')"
)
NEW_TASKS_STATE_CHECK = (
    "state IN ('draft', 'ready', 'active', 'blocked', 'review_required', 'completed', 'failed', 'cancelled')"
)


def upgrade() -> None:
    op.drop_constraint("ck_tasks_state", "tasks", type_="check")
    op.alter_column("tasks", "state", server_default="ready")
    op.create_check_constraint("ck_tasks_state", "tasks", NEW_TASKS_STATE_CHECK)


def downgrade() -> None:
    op.drop_constraint("ck_tasks_state", "tasks", type_="check")
    op.alter_column("tasks", "state", server_default="queued")
    op.create_check_constraint("ck_tasks_state", "tasks", OLD_TASKS_STATE_CHECK)
