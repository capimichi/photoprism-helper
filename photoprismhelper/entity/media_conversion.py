from __future__ import annotations

from datetime import datetime
from typing import Any
from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from photoprismhelper.entity.base import Base


class MediaConversion(Base):
    __tablename__ = "media_conversion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_id: Mapped[int] = mapped_column(Integer, ForeignKey("media.id", ondelete="CASCADE"), index=True, nullable=False)
    media_uid: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="pending")
    original_file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    optimized_file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    backup_file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    original_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    optimized_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    optimized_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    original_extension: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    optimized_extension: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    original_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    optimized_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        saved = self.original_size - (self.optimized_size or 0)
        return f"<MediaConversion(id={self.id}, media_uid={self.media_uid}, status={self.status}, saved={saved})>"
