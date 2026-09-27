"""create media_conversion table

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27 22:45:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_conversion",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("media_uid", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("original_file_path", sa.String(length=1024), nullable=False),
        sa.Column("optimized_file_path", sa.String(length=1024), nullable=True),
        sa.Column("backup_file_path", sa.String(length=1024), nullable=True),
        sa.Column("original_hash", sa.String(length=64), nullable=True),
        sa.Column("optimized_hash", sa.String(length=64), nullable=True),
        sa.Column("original_size", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("optimized_size", sa.BigInteger(), nullable=True),
        sa.Column("original_extension", sa.String(length=16), server_default="", nullable=False),
        sa.Column("optimized_extension", sa.String(length=16), server_default="", nullable=False),
        sa.Column("original_metadata", sa.JSON(), nullable=True),
        sa.Column("optimized_metadata", sa.JSON(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_media_conversion_media_id"), "media_conversion", ["media_id"], unique=False)
    op.create_index(op.f("ix_media_conversion_media_uid"), "media_conversion", ["media_uid"], unique=False)
    op.create_index(op.f("ix_media_conversion_status"), "media_conversion", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_media_conversion_status"), table_name="media_conversion")
    op.drop_index(op.f("ix_media_conversion_media_uid"), table_name="media_conversion")
    op.drop_index(op.f("ix_media_conversion_media_id"), table_name="media_conversion")
    op.drop_table("media_conversion")
