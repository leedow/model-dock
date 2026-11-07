"""Track administrator-owned speech model downloads.

Revision ID: 94ae3c1d502b
Revises: 8b2e1f9a304c
"""

import sqlalchemy as sa
from alembic import op

revision = "94ae3c1d502b"
down_revision = "8b2e1f9a304c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_downloads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("preset", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("downloaded_bytes", sa.BigInteger(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.String(64), nullable=True),
        sa.Column("current_file", sa.String(300), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("worker_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("finished_at", sa.Float(), nullable=True),
    )
    op.create_index("ix_model_downloads_user_id", "model_downloads", ["user_id"])
    op.create_index("ix_model_downloads_status", "model_downloads", ["status"])


def downgrade() -> None:
    op.drop_index("ix_model_downloads_status", table_name="model_downloads")
    op.drop_index("ix_model_downloads_user_id", table_name="model_downloads")
    op.drop_table("model_downloads")
