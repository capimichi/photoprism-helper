from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from photoprismhelper.entity.base import Base

if TYPE_CHECKING:
    from photoprismhelper.entity.media_file import MediaFile


class MediaItem(Base):
    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), index=True, nullable=False)
    folder_path: Mapped[str] = mapped_column(String(1024), index=True, nullable=False, default="")
    file_size: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False, default=0)
    media_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="unknown")
    extension: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="")
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    photo_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    files: Mapped[list["MediaFile"]] = relationship(
        "MediaFile", back_populates="media", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<MediaItem(id={self.id}, uid={self.uid}, file_name={self.file_name}, size={self.file_size})>"
