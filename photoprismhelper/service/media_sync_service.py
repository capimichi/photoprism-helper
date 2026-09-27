from __future__ import annotations

import logging
from dataclasses import dataclass
from injector import inject

from photoprismhelper.client.photoprism_client import PhotoprismClient
from photoprismhelper.mapper.media_mapper import MediaMapper
from photoprismhelper.repository.media_repository import MediaRepository

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    total_processed: int
    total_size_bytes: int
    images_count: int
    videos_count: int
    other_count: int


class MediaSyncService:
    @inject
    def __init__(
        self,
        client: PhotoprismClient,
        repository: MediaRepository,
        mapper: MediaMapper,
    ) -> None:
        self._client = client
        self._repository = repository
        self._mapper = mapper

    def sync(
        self,
        batch_size: int = 100,
        max_items: int | None = None,
        query: str = "",
        progress_callback: Any | None = None,
    ) -> SyncResult:
        """Fetch media items from PhotoPrism and upsert them into MariaDB."""
        offset = 0
        total_processed = 0
        total_size = 0
        images_count = 0
        videos_count = 0
        other_count = 0

        logger.info("Starting PhotoPrism media synchronization (query='%s')...", query)

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

            with self._repository.transaction() as session:
                for photo_data in photos:
                    entity = self._mapper.to_entity(photo_data)
                    self._repository.upsert(session, entity)

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

        logger.info("Sync finished. Total processed: %d items.", total_processed)
        return SyncResult(
            total_processed=total_processed,
            total_size_bytes=total_size,
            images_count=images_count,
            videos_count=videos_count,
            other_count=other_count,
        )
