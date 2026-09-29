from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from injector import inject
from sqlalchemy import select

from photoprismhelper.client.photoprism_client import PhotoprismClient
from photoprismhelper.entity.media_conversion import MediaConversion
from photoprismhelper.mapper.media_mapper import MediaMapper
from photoprismhelper.repository.media_file_repository import MediaFileRepository
from photoprismhelper.repository.media_repository import MediaRepository

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    total_processed: int
    total_size_bytes: int
    images_count: int
    videos_count: int
    other_count: int
    files_count: int = 0
    stacked_items_count: int = 0


class MediaSyncService:
    @inject
    def __init__(
        self,
        client: PhotoprismClient,
        repository: MediaRepository,
        file_repository: MediaFileRepository,
        mapper: MediaMapper,
    ) -> None:
        self._client = client
        self._repository = repository
        self._file_repository = file_repository
        self._mapper = mapper

    def sync(
        self,
        batch_size: int = 100,
        max_items: int | None = None,
        query: str = "",
        progress_callback: Any | None = None,
        sync_files: bool = True,
    ) -> SyncResult:
        """Fetch media items and their complete stack files from PhotoPrism and upsert them into MariaDB."""
        offset = 0
        total_processed = 0
        total_size = 0
        images_count = 0
        videos_count = 0
        other_count = 0
        total_files_count = 0
        stacked_items_count = 0

        logger.info("Starting PhotoPrism media synchronization (query='%s', sync_files=%s)...", query, sync_files)

        while True:
            fetch_count = batch_size
            if max_items is not None:
                remaining = max_items - total_processed
                if remaining <= 0:
                    break
                fetch_count = min(batch_size, remaining)

            logger.debug("Fetching batch from offset %d (count=%d)...", offset, fetch_count)
            photos = self._client.get_photos(count=fetch_count, offset=offset, query=query)
            if not photos:
                logger.info("No more photos returned from PhotoPrism.")
                break

            # Limit photos to remaining items if max_items is set
            if max_items is not None:
                remaining = max_items - total_processed
                photos = photos[:remaining]

            # Fetch detailed photo info (including all stack files) concurrently
            photo_details: dict[str, dict[str, Any]] = {}
            if sync_files:
                batch_uids = [p.get("UID") for p in photos if p.get("UID")]
                photo_details = self._client.get_photos_detail_batch(batch_uids, max_workers=10)

            with self._repository.transaction() as session:
                for photo_data in photos:
                    uid = photo_data.get("UID")
                    entity = self._mapper.to_entity(photo_data)
                    media_item = self._repository.upsert(session, entity)
                    session.flush()

                    # Keep media_conversion.media_id aligned if conversion history exists
                    conv = session.scalar(
                        select(MediaConversion).where(MediaConversion.media_uid == media_item.uid)
                    )
                    if conv and conv.media_id != media_item.id:
                        conv.media_id = media_item.id

                    if sync_files and uid:
                        detail = photo_details.get(uid, photo_data)
                        file_entities = self._mapper.to_file_entities(detail, media_item)

                        active_file_uids = set()
                        real_files_in_stack = 0
                        for fe in file_entities:
                            fe.media_id = media_item.id
                            self._file_repository.upsert(session, fe)
                            active_file_uids.add(fe.file_uid)
                            total_files_count += 1
                            if not fe.is_sidecar and not fe.is_missing:
                                real_files_in_stack += 1

                        if real_files_in_stack > 1:
                            stacked_items_count += 1

                        # Clean up any removed files for this media item
                        if active_file_uids:
                            existing_files = self._file_repository.get_by_media_uid(session, media_item.uid)
                            for ef in existing_files:
                                if ef.file_uid not in active_file_uids:
                                    session.delete(ef)

                    total_processed += 1
                    total_size += entity.file_size
                    if entity.media_type == "image":
                        images_count += 1
                    elif entity.media_type == "video":
                        videos_count += 1
                    else:
                        other_count += 1

                    if max_items is not None and total_processed >= max_items:
                        break

            offset += fetch_count
            logger.debug("Processed %d items so far.", total_processed)

            if progress_callback:
                progress_callback(len(photos), total_processed)

            if max_items is not None and total_processed >= max_items:
                break

        logger.info("Sync finished. Total processed: %d items, %d files, %d stacked.", total_processed, total_files_count, stacked_items_count)
        return SyncResult(
            total_processed=total_processed,
            total_size_bytes=total_size,
            images_count=images_count,
            videos_count=videos_count,
            other_count=other_count,
            files_count=total_files_count,
            stacked_items_count=stacked_items_count,
        )
