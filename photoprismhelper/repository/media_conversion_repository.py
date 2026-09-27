from __future__ import annotations

from injector import inject
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from photoprismhelper.entity.media_conversion import MediaConversion
from photoprismhelper.manager.db_manager import DbManager
from photoprismhelper.repository.base_repository import BaseRepository


class MediaConversionRepository(BaseRepository[MediaConversion]):
    _entity_type = MediaConversion

    @inject
    def __init__(self, db_manager: DbManager) -> None:
        super().__init__(db_manager)

    def create(self, session: Session, conversion: MediaConversion) -> MediaConversion:
        session.add(conversion)
        session.flush()
        return conversion

    def get_by_id(self, session: Session, conversion_id: int) -> MediaConversion | None:
        stmt = select(MediaConversion).where(MediaConversion.id == conversion_id)
        return session.scalar(stmt)

    def get_by_media_id(self, session: Session, media_id: int) -> list[MediaConversion]:
        stmt = (
            select(MediaConversion)
            .where(MediaConversion.media_id == media_id)
            .order_by(desc(MediaConversion.id))
        )
        return list(session.scalars(stmt).all())

    def get_latest_completed_for_media(self, session: Session, media_id: int) -> MediaConversion | None:
        stmt = (
            select(MediaConversion)
            .where(
                MediaConversion.media_id == media_id,
                MediaConversion.status == "completed",
            )
            .order_by(desc(MediaConversion.id))
            .limit(1)
        )
        return session.scalar(stmt)

    def find_completed_media_ids(self, session: Session) -> set[int]:
        stmt = select(MediaConversion.media_id).where(MediaConversion.status == "completed")
        results = session.scalars(stmt).all()
        return set(results)

    def list_conversions(
        self, session: Session, limit: int = 50, status: str | None = None
    ) -> list[MediaConversion]:
        stmt = select(MediaConversion).order_by(desc(MediaConversion.id))
        if status:
            stmt = stmt.where(MediaConversion.status == status)
        if limit:
            stmt = stmt.limit(limit)
        return list(session.scalars(stmt).all())
