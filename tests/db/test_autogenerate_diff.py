"""Test that Alembic autogenerate produces an empty diff against src/eap/db metadata."""

from __future__ import annotations

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from eap.db import metadata


def test_autogenerate_diff_is_empty(db_conn: sa.Connection) -> None:
    """Verify that Alembic autogenerate finds zero drift between the live DB and metadata."""
    mc = MigrationContext.configure(
        db_conn,
        opts={
            "compare_type": True,
            "compare_server_default": True,
        },
    )
    diff = compare_metadata(mc, metadata)
    assert diff == [], f"Expected empty autogenerate diff, found: {diff}"
