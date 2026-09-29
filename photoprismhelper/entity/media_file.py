from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from photoprismhelper.entity.base import Base

if TYPE_CHECKING:
    from photoprismhelper.entity.media_item import MediaItem


class MediaFile(Base):
    __tablename__ = "media_file"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("media.id", ondelete="CASCADE"), index=True, nullable=False
    )
    media_uid: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    file_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), index=True, nullable=False)
    file_root: Mapped[str] = mapped_column(String(32), default="/", nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_missing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_video: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_sidecar: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    media: Mapped["MediaItem"] = relationship("MediaItem", back_populates="files")

    def __repr__(self) -> str:
        return (
            f"<MediaFile(id={self.id}, file_uid={self.file_uid}, name={self.file_name}, "
            f"size={self.file_size}, primary={self.is_primary}, missing={self.is_missing})>"
        )
