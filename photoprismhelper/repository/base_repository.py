from __future__ import annotations

from contextlib import contextmanager
from typing import Generic, Iterator, TypeVar

from injector import inject
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from photoprismhelper.entity.base import Base
from photoprismhelper.manager.db_manager import DbManager

T = TypeVar("T", bound=Base)


class BaseRepository(Generic[T]):
    _entity_type: type[T]

    @inject
    def __init__(self, db_manager: DbManager) -> None:
        self._db_manager = db_manager

    def get_session(self) -> Session:
        return self._db_manager.get_session()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with self._db_manager.transaction() as session:
            yield session

    def get_by_id(self, session: Session, entity_id: int) -> T | None:
        return session.get(self._entity_type, entity_id)

    def add(self, session: Session, entity: T) -> None:
        session.add(entity)

    def delete(self, session: Session, entity: T) -> None:
        session.delete(entity)

    def list(self, session: Session, limit: int = 100, offset: int = 0) -> list[T]:
        stmt = select(self._entity_type).limit(limit).offset(offset)
        return list(session.scalars(stmt))

    def count(self, session: Session) -> int:
        stmt = select(func.count()).select_from(self._entity_type)
        return int(session.scalar(stmt) or 0)

    def delete_all(self, session: Session) -> None:
        session.execute(delete(self._entity_type))
