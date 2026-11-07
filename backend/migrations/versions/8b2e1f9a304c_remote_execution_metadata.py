"""Persist sanitized upstream execution metadata.

Revision ID: 8b2e1f9a304c
Revises: 3541a7e55254
"""

import sqlalchemy as sa
from alembic import op

revision = "8b2e1f9a304c"
down_revision = "3541a7e55254"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("execution_metadata", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("tasks", "execution_metadata")
