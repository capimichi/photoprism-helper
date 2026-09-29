"""create media_file table

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29 17:45:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_file",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("media_uid", sa.String(length=64), nullable=False),
        sa.Column("file_uid", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("file_root", sa.String(length=32), server_default="/", nullable=False),
        sa.Column("file_size", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("media_type", sa.String(length=32), nullable=True),
        sa.Column("codec", sa.String(length=64), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("fps", sa.Float(), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_missing", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_video", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_sidecar", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_uid"),
    )
    op.create_index(op.f("ix_media_file_media_id"), "media_file", ["media_id"], unique=False)
    op.create_index(op.f("ix_media_file_media_uid"), "media_file", ["media_uid"], unique=False)
    op.create_index(op.f("ix_media_file_file_uid"), "media_file", ["file_uid"], unique=True)
    op.create_index(op.f("ix_media_file_file_name"), "media_file", ["file_name"], unique=False)
    op.create_index(op.f("ix_media_file_file_path"), "media_file", ["file_path"], unique=False)
    op.create_index(op.f("ix_media_file_file_hash"), "media_file", ["file_hash"], unique=False)
    op.create_index(op.f("ix_media_file_media_type"), "media_file", ["media_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_media_file_media_type"), table_name="media_file")
    op.drop_index(op.f("ix_media_file_file_hash"), table_name="media_file")
    op.drop_index(op.f("ix_media_file_file_path"), table_name="media_file")
    op.drop_index(op.f("ix_media_file_file_name"), table_name="media_file")
    op.drop_index(op.f("ix_media_file_file_uid"), table_name="media_file")
    op.drop_index(op.f("ix_media_file_media_uid"), table_name="media_file")
    op.drop_index(op.f("ix_media_file_media_id"), table_name="media_file")
    op.drop_table("media_file")
