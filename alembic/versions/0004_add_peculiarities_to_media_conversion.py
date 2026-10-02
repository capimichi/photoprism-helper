"""add peculiarities to media_conversion

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-02 22:25:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_conversion", sa.Column("encoder_used", sa.String(length=64), nullable=True))
    op.add_column("media_conversion", sa.Column("is_hardware", sa.Boolean(), server_default=sa.text("0"), nullable=False))
    op.add_column("media_conversion", sa.Column("fallback_triggered", sa.Boolean(), server_default=sa.text("0"), nullable=False))
    op.add_column("media_conversion", sa.Column("fallback_reason", sa.String(length=255), nullable=True))
    op.add_column("media_conversion", sa.Column("audio_transcoded", sa.Boolean(), server_default=sa.text("0"), nullable=False))
    op.add_column("media_conversion", sa.Column("source_audio_codec", sa.String(length=32), nullable=True))
    op.add_column("media_conversion", sa.Column("source_video_codec", sa.String(length=32), nullable=True))
    op.add_column("media_conversion", sa.Column("peculiarities", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_conversion", "peculiarities")
    op.drop_column("media_conversion", "source_video_codec")
    op.drop_column("media_conversion", "source_audio_codec")
    op.drop_column("media_conversion", "audio_transcoded")
    op.drop_column("media_conversion", "fallback_reason")
    op.drop_column("media_conversion", "fallback_triggered")
    op.drop_column("media_conversion", "is_hardware")
    op.drop_column("media_conversion", "encoder_used")
