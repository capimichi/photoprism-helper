from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from injector import inject
from sqlalchemy.orm import Session

from photoprismhelper.client.photoprism_client import PhotoprismClient
from photoprismhelper.entity.media_file import MediaFile
from photoprismhelper.entity.media_item import MediaItem
from photoprismhelper.repository.media_file_repository import MediaFileRepository
from photoprismhelper.repository.media_repository import MediaRepository

logger = logging.getLogger(__name__)


@dataclass
class InvalidStackFile:
    file_uid: str
    file_name: str
    file_path: str
    file_size: int
    duration: float | None


@dataclass
class InvalidVideoStack:
    media_id: int
    media_uid: str
    photo_title: str | None
    taken_at: datetime | None
    primary_file: InvalidStackFile
    invalid_files: list[InvalidStackFile]
    max_duration_diff: float


class StackAnalysisService:
    @inject
    def __init__(
        self,
        media_repository: MediaRepository,
        file_repository: MediaFileRepository,
        photoprism_client: PhotoprismClient | None = None,
        originals_path: str | None = None,
    ) -> None:
        self._media_repository = media_repository
        self._file_repository = file_repository
        self._photoprism_client = photoprism_client
        self._originals_path = originals_path or os.getenv(
            "PHOTOPRISM_ORIGINALS_PATH", "/photoprism/originals"
        )

    def resolve_disk_path(self, file_path: str) -> str:
        """Resolve the absolute path of the file on disk."""
        if os.path.isabs(file_path):
            return file_path
        return os.path.join(self._originals_path, file_path)

    def find_invalid_video_stacks(
        self,
        min_diff_sec: float = 2.0,
        limit: int | None = None,
    ) -> list[InvalidVideoStack]:
        """Find media items where distinct videos are stacked together with different durations."""
        session: Session = self._file_repository.get_session()
        try:
            stacked_uids = self._file_repository.find_stacked_video_media_uids(session)
            invalid_stacks: list[InvalidVideoStack] = []

            for uid in stacked_uids:
                media_item = self._media_repository.get_by_uid(session, uid)
                if not media_item:
                    continue

                files = self._file_repository.get_stack_files_for_media(
                    session, uid, include_sidecars=False
                )
                # Video files only, not missing, not sidecar, with valid duration
                video_files = [
                    f
                    for f in files
                    if f.is_video
                    and not f.is_missing
                    and not f.is_sidecar
                    and f.duration is not None
                    and f.duration > 0
                ]

                if len(video_files) < 2:
                    continue

                # Determine reference/primary video
                primary_candidate = next(
                    (f for f in video_files if f.file_name == media_item.file_name),
                    None,
                )
                if not primary_candidate:
                    primary_candidate = next(
                        (f for f in video_files if f.is_primary), video_files[0]
                    )

                invalid_secondaries: list[InvalidStackFile] = []
                max_diff = 0.0

                for vf in video_files:
                    if vf.file_uid == primary_candidate.file_uid:
                        continue

                    diff = abs((vf.duration or 0.0) - (primary_candidate.duration or 0.0))
                    if diff >= min_diff_sec:
                        max_diff = max(max_diff, diff)
                        invalid_secondaries.append(
                            InvalidStackFile(
                                file_uid=vf.file_uid,
                                file_name=vf.file_name,
                                file_path=vf.file_path,
                                file_size=vf.file_size,
                                duration=vf.duration,
                            )
                        )

                if invalid_secondaries:
                    primary_obj = InvalidStackFile(
                        file_uid=primary_candidate.file_uid,
                        file_name=primary_candidate.file_name,
                        file_path=primary_candidate.file_path,
                        file_size=primary_candidate.file_size,
                        duration=primary_candidate.duration,
                    )
                    invalid_stacks.append(
                        InvalidVideoStack(
                            media_id=media_item.id,
                            media_uid=media_item.uid,
                            photo_title=media_item.photo_title,
                            taken_at=media_item.taken_at,
                            primary_file=primary_obj,
                            invalid_files=invalid_secondaries,
                            max_duration_diff=round(max_diff, 2),
                        )
                    )

            # Sort descending by greatest duration discrepancy
            invalid_stacks.sort(key=lambda s: s.max_duration_diff, reverse=True)

            if limit is not None and limit > 0:
                invalid_stacks = invalid_stacks[:limit]

            return invalid_stacks
        finally:
            session.close()

    def unstack_file(self, photo_uid: str, file_uid: str) -> bool:
        """Call PhotoPrism API to unstack the file and update local database."""
        if not self._photoprism_client:
            logger.warning("PhotoPrism client is not configured; cannot unstack.")
            return False

        success = self._photoprism_client.unstack_file(photo_uid, file_uid)
        if success:
            session = self._file_repository.get_session()
            try:
                self._file_repository.delete_by_file_uid(session, file_uid)
                session.commit()
            except Exception as e:
                logger.error("Failed to delete unstacked file from local DB: %s", e)
                session.rollback()
            finally:
                session.close()
        else:
            # Check if the photo was deleted in PhotoPrism (HTTP 404)
            try:
                self._photoprism_client.get_photo(photo_uid)
            except Exception:
                # 404 or entity not found: remove purged media item and its files from local DB
                session = self._media_repository.get_session()
                try:
                    self._media_repository.delete_by_uid(session, photo_uid)
                    session.commit()
                    logger.info("Photo %s does not exist in PhotoPrism; cleaned up from local DB.", photo_uid)
                    return True
                except Exception as db_err:
                    session.rollback()
                    logger.error("Failed to delete purged photo %s from local DB: %s", photo_uid, db_err)
                finally:
                    session.close()

        return success
