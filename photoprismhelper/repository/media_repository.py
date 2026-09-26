from __future__ import annotations

from injector import inject
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from photoprismhelper.entity.media_item import MediaItem
from photoprismhelper.manager.db_manager import DbManager
from photoprismhelper.model.media_stat import BreakdownStat, StorageSummary
from photoprismhelper.repository.base_repository import BaseRepository


class MediaRepository(BaseRepository[MediaItem]):
    _entity_type = MediaItem

    @inject
    def __init__(self, db_manager: DbManager) -> None:
        super().__init__(db_manager)

    def get_by_uid(self, session: Session, uid: str) -> MediaItem | None:
        stmt = select(MediaItem).where(MediaItem.uid == uid)
        return session.scalar(stmt)

    def upsert(self, session: Session, item: MediaItem) -> MediaItem:
        existing = self.get_by_uid(session, item.uid)
        if existing:
            existing.file_hash = item.file_hash
            existing.file_name = item.file_name
            existing.file_path = item.file_path
            existing.folder_path = item.folder_path
            existing.file_size = item.file_size
            existing.media_type = item.media_type
            existing.extension = item.extension
            existing.mime_type = item.mime_type
            existing.width = item.width
            existing.height = item.height
            existing.duration = item.duration
            existing.codec = item.codec
            existing.fps = item.fps
            existing.taken_at = item.taken_at
            existing.photo_title = item.photo_title
            existing.is_favorite = item.is_favorite
            existing.tags = item.tags
            if item.suggested_tags:
                existing.suggested_tags = item.suggested_tags
            return existing
        else:
            session.add(item)
            return item

    def get_storage_summary(self, session: Session) -> StorageSummary:
        total_stmt = select(
            func.count(MediaItem.id),
            func.coalesce(func.sum(MediaItem.file_size), 0),
        )
        total_files, total_size = session.execute(total_stmt).one()

        images_stmt = select(
            func.count(MediaItem.id),
            func.coalesce(func.sum(MediaItem.file_size), 0),
        ).where(MediaItem.media_type == "image")
        image_count, image_size = session.execute(images_stmt).one()

        videos_stmt = select(
            func.count(MediaItem.id),
            func.coalesce(func.sum(MediaItem.file_size), 0),
        ).where(MediaItem.media_type == "video")
        video_count, video_size = session.execute(videos_stmt).one()

        other_count = total_files - (image_count + video_count)
        other_size = total_size - (image_size + video_size)

        return StorageSummary(
            total_files=int(total_files or 0),
            total_size_bytes=int(total_size or 0),
            image_count=int(image_count or 0),
            image_size_bytes=int(image_size or 0),
            video_count=int(video_count or 0),
            video_size_bytes=int(video_size or 0),
            other_count=max(0, int(other_count or 0)),
            other_size_bytes=max(0, int(other_size or 0)),
        )

    def get_breakdown_by_extension(self, session: Session, limit: int = 15) -> list[BreakdownStat]:
        stmt = (
            select(
                MediaItem.extension,
                func.count(MediaItem.id).label("count"),
                func.coalesce(func.sum(MediaItem.file_size), 0).label("total_size"),
            )
            .group_by(MediaItem.extension)
            .order_by(desc("total_size"))
            .limit(limit)
        )
        rows = session.execute(stmt).all()
        return [
            BreakdownStat(
                category=row[0] or "unknown",
                count=int(row[1]),
                total_bytes=int(row[2]),
            )
            for row in rows
        ]

    def get_breakdown_by_folder(self, session: Session, limit: int = 20) -> list[BreakdownStat]:
        stmt = (
            select(
                MediaItem.folder_path,
                func.count(MediaItem.id).label("count"),
                func.coalesce(func.sum(MediaItem.file_size), 0).label("total_size"),
            )
            .where(MediaItem.folder_path != "")
            .group_by(MediaItem.folder_path)
            .order_by(desc("total_size"))
            .limit(limit)
        )
        rows = session.execute(stmt).all()
        return [
            BreakdownStat(
                category=row[0] or "/",
                count=int(row[1]),
                total_bytes=int(row[2]),
            )
            for row in rows
        ]

    def find_largest_files(
        self,
        session: Session,
        limit: int = 20,
        media_type: str | None = None,
    ) -> list[MediaItem]:
        stmt = select(MediaItem)
        if media_type:
            stmt = stmt.where(MediaItem.media_type == media_type)
        stmt = stmt.order_by(desc(MediaItem.file_size)).limit(limit)
        return list(session.scalars(stmt))

    def find_video_candidates_for_optimization(
        self,
        session: Session,
        min_size_bytes: int = 50 * 1024 * 1024,
        limit: int = 50,
    ) -> list[MediaItem]:
        """Find video items larger than min_size_bytes that are not already optimized."""
        stmt = (
            select(MediaItem)
            .where(MediaItem.media_type == "video")
            .where(MediaItem.file_size >= min_size_bytes)
            .where(MediaItem.optimization_status != "optimized")
            .order_by(desc(MediaItem.file_size))
            .limit(limit)
        )
        return list(session.scalars(stmt))

    def find_pending_suggested_tags(self, session: Session, limit: int = 100) -> list[MediaItem]:
        """Find media items that have suggested folder tags not yet present in existing tags."""
        stmt = (
            select(MediaItem)
            .where(MediaItem.suggested_tags.isnot(None))
            .where(MediaItem.suggested_tags != "")
            .limit(limit)
        )
        return list(session.scalars(stmt))
