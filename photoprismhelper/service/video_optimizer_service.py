from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
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


@dataclass
class OptimizationDraft:
    item_id: int
    item_uid: str
    input_path: str
    final_output_path: str
    backup_file_path: str | None
    temp_output_path: str
    original_size: int
    original_hash: str
    original_extension: str
    original_metadata: dict[str, Any]
    optimized_size: int = 0
    optimized_hash: str = ""
    optimized_metadata: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0
    encoder_used: str = ""
    success: bool = False
    error_message: str | None = None
    integrity_message: str = ""


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

    def prepare_optimization(
        self,
        item: MediaItem,
        tmp_dir: str,
        max_height: int = 1080,
    ) -> OptimizationDraft:
        """Perform video encoding and validation into a local temporary staging directory.

        Does not modify the original file on the NAS in any way.
        """
        input_path = self.resolve_disk_path(item.file_path)
        if not os.path.isfile(input_path):
            raise FileNotFoundError(f"Input file not found on disk: {input_path}")

        orig_size = os.path.getsize(input_path)
        orig_hash = self._extractor.calculate_file_hash(input_path)
        orig_metadata = self._extractor.extract_metadata(input_path)
        _, orig_ext = os.path.splitext(input_path)
        orig_ext = orig_ext.lstrip(".").lower()

        base_no_ext, _ = os.path.splitext(input_path)
        final_output = f"{base_no_ext}.mp4"
        backup_path = f"{input_path}.bak"

        # Unique staging file inside the local temp dir
        temp_output = os.path.join(tmp_dir, f"{item.uid}_1080p.mp4")

        draft = OptimizationDraft(
            item_id=item.id,
            item_uid=item.uid,
            input_path=input_path,
            final_output_path=final_output,
            backup_file_path=backup_path,
            temp_output_path=temp_output,
            original_size=orig_size,
            original_hash=orig_hash,
            original_extension=orig_ext,
            original_metadata=orig_metadata,
        )

        # Convert to local staging
        result = self._converter.convert(input_path, temp_output, max_height=max_height)
        draft.duration_seconds = result.duration_seconds
        draft.encoder_used = result.encoder_used

        if not result.success:
            draft.success = False
            draft.error_message = result.error_message
            return draft

        # Extract metadata and hash from optimized local file
        draft.optimized_size = result.optimized_size
        draft.optimized_hash = self._extractor.calculate_file_hash(temp_output)
        draft.optimized_metadata = self._extractor.extract_metadata(temp_output)
        draft.integrity_message = result.integrity_message
        draft.success = True

        return draft

    def apply_optimization(
        self,
        draft: OptimizationDraft,
        keep_backup: bool = True,
        notify_photoprism: bool = True,
    ) -> MediaConversion:
        """Apply a verified optimization draft to the NAS: backup, move, update DB, notify PhotoPrism."""
        if not draft.success:
            raise ValueError(f"Cannot apply failed optimization draft: {draft.error_message}")

        if not os.path.isfile(draft.temp_output_path):
            raise FileNotFoundError(f"Staged optimized file not found: {draft.temp_output_path}")

        session = self._media_repository.get_session()
        try:
            # 1. Create MediaConversion record
            conversion = MediaConversion(
                media_id=draft.item_id,
                media_uid=draft.item_uid,
                status="completed",
                original_file_path=draft.input_path,
                optimized_file_path=draft.final_output_path,
                backup_file_path=draft.backup_file_path if keep_backup else None,
                original_hash=draft.original_hash,
                original_size=draft.original_size,
                original_extension=draft.original_extension,
                optimized_extension="mp4",
                original_metadata=draft.original_metadata,
                optimized_size=draft.optimized_size,
                optimized_hash=draft.optimized_hash,
                optimized_metadata=draft.optimized_metadata,
                duration_seconds=draft.duration_seconds,
                completed_at=datetime.utcnow(),
            )
            self._conversion_repository.create(session, conversion)

            # 2. Handle backup on NAS
            if keep_backup and draft.backup_file_path:
                shutil.move(draft.input_path, draft.backup_file_path)
            elif not keep_backup and os.path.exists(draft.input_path) and draft.input_path != draft.final_output_path:
                os.remove(draft.input_path)

            # 3. Move optimized file from local staging to NAS destination
            shutil.move(draft.temp_output_path, draft.final_output_path)

            # 4. Update MediaItem in DB
            db_item = self._media_repository.get_by_uid(session, draft.item_uid)
            if db_item:
                rel_dir = os.path.dirname(db_item.file_path)
                new_file_name = os.path.basename(draft.final_output_path)
                db_item.file_name = new_file_name
                db_item.file_path = os.path.join(rel_dir, new_file_name) if rel_dir else new_file_name
                db_item.extension = "mp4"
                db_item.file_size = draft.optimized_size
                db_item.file_hash = draft.optimized_hash
                db_item.codec = "hevc"

            session.commit()

            # 5. Notify PhotoPrism to re-index the affected subfolder
            if notify_photoprism and self._photoprism_client:
                subfolder = self.get_relative_subfolder(draft.final_output_path)
                logger.info("Notifying PhotoPrism to re-index folder: '%s'...", subfolder)
                try:
                    self._photoprism_client.trigger_index(path=subfolder, cleanup=True)
                except Exception as e:
                    logger.warning("Could not notify PhotoPrism for re-index: %s", e)

            return conversion
        finally:
            session.close()

    def optimize_media(
        self,
        item: MediaItem,
        keep_backup: bool = True,
        max_height: int = 1080,
        notify_photoprism: bool = True,
    ) -> MediaConversion:
        """One-step programmatic optimization using tempfile staging."""
        with tempfile.TemporaryDirectory(prefix="pp_opt_") as tmp_dir:
            draft = self.prepare_optimization(item, tmp_dir=tmp_dir, max_height=max_height)
            if not draft.success:
                session = self._media_repository.get_session()
                try:
                    failed_conversion = MediaConversion(
                        media_id=item.id,
                        media_uid=item.uid,
                        status="failed",
                        original_file_path=draft.input_path,
                        optimized_file_path=draft.final_output_path,
                        backup_file_path=None,
                        original_hash=draft.original_hash,
                        original_size=draft.original_size,
                        original_extension=draft.original_extension,
                        optimized_extension="mp4",
                        original_metadata=draft.original_metadata,
                        duration_seconds=draft.duration_seconds,
                        error_message=draft.error_message,
                    )
                    self._conversion_repository.create(session, failed_conversion)
                    session.commit()
                    return failed_conversion
                finally:
                    session.close()

            return self.apply_optimization(draft, keep_backup=keep_backup, notify_photoprism=notify_photoprism)

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
