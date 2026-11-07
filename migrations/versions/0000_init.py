"""Initial schema baseline.

Revision ID: 0000_init
Revises: 
Create Date: 2026-11-06

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0000_init'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        '_eap_schema_baseline',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('installed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('version', sa.String(length=64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('_eap_schema_baseline')
