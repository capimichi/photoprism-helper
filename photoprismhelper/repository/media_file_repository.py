from __future__ import annotations

from injector import inject
from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from photoprismhelper.entity.media_file import MediaFile
from photoprismhelper.entity.media_item import MediaItem
from photoprismhelper.manager.db_manager import DbManager
from photoprismhelper.repository.base_repository import BaseRepository


class MediaFileRepository(BaseRepository[MediaFile]):
    _entity_type = MediaFile

    @inject
    def __init__(self, db_manager: DbManager) -> None:
        super().__init__(db_manager)

    def create(self, session: Session, file_item: MediaFile) -> MediaFile:
        session.add(file_item)
        session.flush()
        return file_item

    def get_by_id(self, session: Session, file_id: int) -> MediaFile | None:
        stmt = select(MediaFile).where(MediaFile.id == file_id)
        return session.scalar(stmt)

    def get_by_file_uid(self, session: Session, file_uid: str) -> MediaFile | None:
        stmt = select(MediaFile).where(MediaFile.file_uid == file_uid)
        return session.scalar(stmt)

    def get_by_media_uid(self, session: Session, media_uid: str) -> list[MediaFile]:
        stmt = (
            select(MediaFile)
            .where(MediaFile.media_uid == media_uid)
            .order_by(desc(MediaFile.is_primary), MediaFile.id)
        )
        return list(session.scalars(stmt).all())

    def get_by_media_id(self, session: Session, media_id: int) -> list[MediaFile]:
        stmt = (
            select(MediaFile)
            .where(MediaFile.media_id == media_id)
            .order_by(desc(MediaFile.is_primary), MediaFile.id)
        )
        return list(session.scalars(stmt).all())

    def upsert(self, session: Session, file_item: MediaFile) -> MediaFile:
        existing = self.get_by_file_uid(session, file_item.file_uid)
        if existing:
            existing.media_id = file_item.media_id
            existing.media_uid = file_item.media_uid
            existing.file_name = file_item.file_name
            existing.file_path = file_item.file_path
            existing.file_root = file_item.file_root
            existing.file_size = file_item.file_size
            existing.file_hash = file_item.file_hash
            existing.media_type = file_item.media_type
            existing.codec = file_item.codec
            existing.width = file_item.width
            existing.height = file_item.height
            existing.duration = file_item.duration
            existing.fps = file_item.fps
            existing.mime_type = file_item.mime_type
            existing.is_primary = file_item.is_primary
            existing.is_missing = file_item.is_missing
            existing.is_video = file_item.is_video
            existing.is_sidecar = file_item.is_sidecar
            return existing
        else:
            session.add(file_item)
            return file_item

    def delete_by_file_uid(self, session: Session, file_uid: str) -> bool:
        item = self.get_by_file_uid(session, file_uid)
        if item:
            session.delete(item)
            return True
        return False

    def delete_by_media_uid(self, session: Session, media_uid: str) -> int:
        files = self.get_by_media_uid(session, media_uid)
        count = len(files)
        for f in files:
            session.delete(f)
        return count

    def find_stacked_video_media_uids(self, session: Session) -> list[str]:
        """Find all media UIDs that have more than 1 non-sidecar, non-missing video file."""
        stmt = (
            select(MediaFile.media_uid)
            .where(
                and_(
                    MediaFile.is_video.is_(True),
                    MediaFile.is_sidecar.is_(False),
                    MediaFile.is_missing.is_(False),
                )
            )
            .group_by(MediaFile.media_uid)
            .having(func.count(MediaFile.id) > 1)
        )
        return list(session.scalars(stmt).all())

    def get_stack_files_for_media(
        self, session: Session, media_uid: str, include_sidecars: bool = False
    ) -> list[MediaFile]:
        """Return files in stack, optionally excluding sidecars."""
        conditions = [MediaFile.media_uid == media_uid]
        if not include_sidecars:
            conditions.append(MediaFile.is_sidecar.is_(False))

        stmt = select(MediaFile).where(and_(*conditions)).order_by(desc(MediaFile.is_primary), MediaFile.id)
        return list(session.scalars(stmt).all())
