"""create media table

Revision ID: 0001
Revises: 
Create Date: 2026-09-26 15:45:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uid", sa.String(length=64), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("folder_path", sa.String(length=1024), server_default="", nullable=False),
        sa.Column("file_size", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("media_type", sa.String(length=32), server_default="unknown", nullable=False),
        sa.Column("extension", sa.String(length=32), server_default="", nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("codec", sa.String(length=64), nullable=True),
        sa.Column("fps", sa.Float(), nullable=True),
        sa.Column("taken_at", sa.DateTime(), nullable=True),
        sa.Column("photo_title", sa.String(length=512), nullable=True),
        sa.Column("is_favorite", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uid", name="uq_media_uid"),
    )
    op.create_index("ix_media_uid", "media", ["uid"], unique=False)
    op.create_index("ix_media_file_hash", "media", ["file_hash"], unique=False)
    op.create_index("ix_media_file_path", "media", ["file_path"], unique=False)
    op.create_index("ix_media_folder_path", "media", ["folder_path"], unique=False)
    op.create_index("ix_media_file_size", "media", ["file_size"], unique=False)
    op.create_index("ix_media_media_type", "media", ["media_type"], unique=False)
    op.create_index("ix_media_extension", "media", ["extension"], unique=False)
    op.create_index("ix_media_taken_at", "media", ["taken_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_media_taken_at", table_name="media")
    op.drop_index("ix_media_extension", table_name="media")
    op.drop_index("ix_media_media_type", table_name="media")
    op.drop_index("ix_media_file_size", table_name="media")
    op.drop_index("ix_media_folder_path", table_name="media")
    op.drop_index("ix_media_file_path", table_name="media")
    op.drop_index("ix_media_file_hash", table_name="media")
    op.drop_index("ix_media_uid", table_name="media")
    op.drop_table("media")
