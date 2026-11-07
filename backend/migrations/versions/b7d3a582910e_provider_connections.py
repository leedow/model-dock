"""Encrypted provider connections and optional model binding.

Revision ID: b7d3a582910e
Revises: 94ae3c1d502b
"""

import sqlalchemy as sa
from alembic import op

revision = "b7d3a582910e"
down_revision = "94ae3c1d502b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("connect_timeout", sa.Float(), nullable=False),
        sa.Column("read_timeout", sa.Float(), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    with op.batch_alter_table("models") as batch:
        batch.add_column(sa.Column("provider_connection_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_models_provider_connection",
            "provider_connections",
            ["provider_connection_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_models_provider_connection_id", ["provider_connection_id"])


def downgrade() -> None:
    with op.batch_alter_table("models") as batch:
        batch.drop_index("ix_models_provider_connection_id")
        batch.drop_constraint("fk_models_provider_connection", type_="foreignkey")
        batch.drop_column("provider_connection_id")
    op.drop_table("provider_connections")
