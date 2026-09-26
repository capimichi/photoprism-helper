from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from photoprismhelper.entity.base import Base

logger = logging.getLogger(__name__)


class DbManager:
    def __init__(self, database_url: str, debug: bool = False) -> None:
        self.database_url = database_url
        self._engine = create_engine(
            database_url,
            echo=debug,
            pool_pre_ping=True,
            future=True,
        )
        self._session_factory = sessionmaker(
            bind=self._engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

    @property
    def engine(self):
        return self._engine

    def get_session(self) -> Session:
        return self._session_factory()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        session = self.get_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_tables(self) -> None:
        """Create database tables if they do not exist."""
        logger.info("Ensuring database tables are created...")
        Base.metadata.create_all(self._engine)
