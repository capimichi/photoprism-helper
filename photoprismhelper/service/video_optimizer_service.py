from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from typing import Any
from injector import inject

from photoprismhelper.client.photoprism_client import PhotoprismClient
from photoprismhelper.entity.media_conversion import MediaConversion
from photoprismhelper.entity.media_item import MediaItem
from photoprismhelper.repository.media_conversion_repository import MediaConversionRepository
from photoprismhelper.repository.media_repository import MediaRepository
from photoprismhelper.service.metadata_extractor import MetadataExtractor
from photoprismhelper.service.storage_analysis_service import StorageAnalysisService
from photoprismhelper.service.video_converter import VideoConverter

logger = logging.getLogger(__name__)


class VideoOptimizerService:
    @inject
    def __init__(
        self,
        media_repository: MediaRepository,
        conversion_repository: MediaConversionRepository,
        storage_analysis_service: StorageAnalysisService,
        photoprism_client: PhotoprismClient | None = None,
        converter: VideoConverter | None = None,
        extractor: MetadataExtractor | None = None,
        originals_path: str | None = None,
    ) -> None:
        self._media_repository = media_repository
        self._conversion_repository = conversion_repository
        self._storage_analysis_service = storage_analysis_service
        self._photoprism_client = photoprism_client
        self._converter = converter or VideoConverter()
        self._extractor = extractor or MetadataExtractor()
        self._originals_path = originals_path or os.getenv("PHOTOPRISM_ORIGINALS_PATH", "/photoprism/originals")

    def get_candidate_videos(
        self,
        min_size_mb: int = 10,
        limit: int = 25,
    ) -> list[MediaItem]:
        """Find video items sorted by size descending that have not been successfully optimized yet."""
        min_size_bytes = min_size_mb * 1024 * 1024
        session = self._media_repository.get_session()
        try:
            return self._media_repository.find_unoptimized_videos(
                session, limit=limit, min_size_bytes=min_size_bytes
            )
        finally:
            session.close()

    def resolve_disk_path(self, file_path: str) -> str:
        """Resolve the absolute path of the file on disk."""
        if os.path.isabs(file_path):
            return file_path
        return os.path.join(self._originals_path, file_path)

    def get_relative_subfolder(self, file_path: str) -> str:
        """Extract the subfolder relative to PhotoPrism originals."""
        if os.path.isabs(file_path) and self._originals_path and file_path.startswith(self._originals_path):
            rel = os.path.relpath(os.path.dirname(file_path), self._originals_path)
            return "" if rel == "." else rel
        dirname = os.path.dirname(file_path)
        return "" if dirname in (".", "/") else dirname

    def optimize_media(
        self,
        item: MediaItem,
        keep_backup: bool = True,
        replace_in_place: bool = True,
        max_height: int = 1080,
        notify_photoprism: bool = True,
    ) -> MediaConversion:
        """Transcode and optimize a single video item, record in media_conversion, and update media."""
        session = self._media_repository.get_session()
        try:
            input_path = self.resolve_disk_path(item.file_path)
            if not os.path.isfile(input_path):
                raise FileNotFoundError(f"Input file not found on disk: {input_path}")

            orig_size = os.path.getsize(input_path)
            orig_hash = self._extractor.calculate_file_hash(input_path)
            orig_metadata = self._extractor.extract_metadata(input_path)
            _, orig_ext = os.path.splitext(input_path)
            orig_ext = orig_ext.lstrip(".").lower()

            base_no_ext, _ = os.path.splitext(input_path)
            temp_output = f"{base_no_ext}.optimized.tmp.mp4"
            final_output = f"{base_no_ext}.mp4"
            backup_path = f"{input_path}.bak" if keep_backup else None

            # Create pending conversion record
            conversion = MediaConversion(
                media_id=item.id,
                media_uid=item.uid,
                status="pending",
                original_file_path=input_path,
                optimized_file_path=final_output,
                backup_file_path=backup_path,
                original_hash=orig_hash,
                original_size=orig_size,
                original_extension=orig_ext,
                optimized_extension="mp4",
                original_metadata=orig_metadata,
            )
            self._conversion_repository.create(session, conversion)
            session.commit()

            # Execute conversion
            result = self._converter.convert(input_path, temp_output, max_height=max_height)

            if not result.success:
                conversion.status = "failed"
                conversion.error_message = result.error_message
                conversion.duration_seconds = result.duration_seconds
                session.commit()
                return conversion

            # Extract optimized file metadata
            opt_hash = self._extractor.calculate_file_hash(temp_output)
            opt_metadata = self._extractor.extract_metadata(temp_output)

            # Apply replacement
            if replace_in_place:
                if keep_backup and backup_path:
                    # Move original to .bak
                    shutil.move(input_path, backup_path)
                elif not keep_backup and os.path.exists(input_path) and input_path != final_output:
                    os.remove(input_path)

                # Move temp optimized to final output path
                shutil.move(temp_output, final_output)

                # Update MediaItem in DB
                db_item = self._media_repository.get_by_uid(session, item.uid)
                if db_item:
                    rel_dir = os.path.dirname(db_item.file_path)
                    new_file_name = os.path.basename(final_output)
                    db_item.file_name = new_file_name
                    db_item.file_path = os.path.join(rel_dir, new_file_name) if rel_dir else new_file_name
                    db_item.extension = "mp4"
                    db_item.file_size = result.optimized_size
                    db_item.file_hash = opt_hash
                    db_item.codec = "hevc"

                # Notify PhotoPrism to re-index the affected subfolder
                if notify_photoprism and self._photoprism_client:
                    subfolder = self.get_relative_subfolder(final_output)
                    logger.info("Notifying PhotoPrism to re-index folder: '%s'...", subfolder)
                    try:
                        self._photoprism_client.trigger_index(path=subfolder, cleanup=True)
                    except Exception as e:
                        logger.warning("Could not notify PhotoPrism for re-index: %s", e)

            conversion.status = "completed"
            conversion.optimized_size = result.optimized_size
            conversion.optimized_hash = opt_hash
            conversion.optimized_metadata = opt_metadata
            conversion.duration_seconds = result.duration_seconds
            conversion.completed_at = datetime.utcnow()
            session.commit()

            return conversion
        finally:
            session.close()

    def revert_conversion(self, conversion_id: int, notify_photoprism: bool = True) -> tuple[bool, str]:
        """Revert a previously completed conversion using its backup file."""
        session = self._media_repository.get_session()
        try:
            conv = self._conversion_repository.get_by_id(session, conversion_id)
            if not conv:
                return False, f"Conversion ID {conversion_id} not found."

            if conv.status != "completed":
                return False, f"Cannot revert conversion with status '{conv.status}'."

            backup_path = conv.backup_file_path
            if not backup_path or not os.path.isfile(backup_path):
                return False, f"Backup file not found at: {backup_path}"

            optimized_path = conv.optimized_file_path
            orig_path = conv.original_file_path

            # Remove current optimized file if it exists
            if optimized_path and os.path.isfile(optimized_path):
                os.remove(optimized_path)

            # Move backup back to original
            shutil.move(backup_path, orig_path)

            # Restore MediaItem in DB
            db_item = self._media_repository.get_by_uid(session, conv.media_uid)
            if db_item:
                rel_dir = os.path.dirname(db_item.file_path)
                orig_file_name = os.path.basename(orig_path)
                db_item.file_name = orig_file_name
                db_item.file_path = os.path.join(rel_dir, orig_file_name) if rel_dir else orig_file_name
                db_item.extension = conv.original_extension
                db_item.file_size = conv.original_size
                db_item.file_hash = conv.original_hash

            conv.status = "reverted"
            session.commit()

            # Notify PhotoPrism to re-index folder after revert
            if notify_photoprism and self._photoprism_client:
                subfolder = self.get_relative_subfolder(orig_path)
                logger.info("Notifying PhotoPrism to re-index folder after revert: '%s'...", subfolder)
                try:
                    self._photoprism_client.trigger_index(path=subfolder, cleanup=True)
                except Exception as e:
                    logger.warning("Could not notify PhotoPrism for re-index after revert: %s", e)

            return True, f"Successfully restored original file for media {conv.media_uid}."
        finally:
            session.close()
