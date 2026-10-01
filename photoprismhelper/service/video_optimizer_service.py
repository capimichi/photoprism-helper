from __future__ import annotations

import hashlib
import logging
import os
import shutil
import struct
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from injector import inject
from sqlalchemy import desc, select

from photoprismhelper.client.photoprism_client import PhotoprismClient
from photoprismhelper.entity.media_conversion import MediaConversion
from photoprismhelper.entity.media_file import MediaFile
from photoprismhelper.entity.media_item import MediaItem
from photoprismhelper.repository.media_conversion_repository import MediaConversionRepository
from photoprismhelper.repository.media_file_repository import MediaFileRepository
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
        file_repository: MediaFileRepository | None = None,
    ) -> None:
        self._media_repository = media_repository
        self._conversion_repository = conversion_repository
        self._storage_analysis_service = storage_analysis_service
        self._photoprism_client = photoprism_client
        self._converter = converter or VideoConverter()
        self._extractor = extractor or MetadataExtractor()
        self._originals_path = originals_path or os.getenv("PHOTOPRISM_ORIGINALS_PATH", "/photoprism/originals")
        self._file_repository = file_repository

    def get_candidate_videos(
        self,
        min_size_mb: int = 10,
        limit: int = 25,
        uid: str | None = None,
    ) -> list[MediaItem]:
        """Find video items sorted by size descending that have not been successfully optimized yet, or a specific UID."""
        session = self._media_repository.get_session()
        try:
            if uid:
                item = self._media_repository.get_by_uid(session, uid)
                return [item] if item else []
            min_size_bytes = min_size_mb * 1024 * 1024
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

    def get_stack_video_files(self, media_uid: str) -> list[MediaFile]:
        """Fetch all non-sidecar, non-missing video files in the stack for this media."""
        if not self._file_repository:
            return []
        session = self._file_repository.get_session()
        try:
            files = self._file_repository.get_stack_files_for_media(session, media_uid, include_sidecars=False)
            return [f for f in files if f.is_video and not f.is_missing]
        finally:
            session.close()

    def get_stack_duplicates(self, media_uid: str, primary_file_name: str | None = None) -> list[MediaFile]:
        """Find video duplicates in the stack (e.g. .00001, .00002 or unoptimized duplicates)."""
        import re

        video_files = self.get_stack_video_files(media_uid)
        if len(video_files) <= 1:
            return []

        # Find any completed conversion to protect the optimized output file
        session = self._conversion_repository.get_session()
        protected_names: set[str] = set()
        try:
            item = self._media_repository.get_by_uid(session, media_uid)
            if item:
                completed_conv = self._conversion_repository.get_latest_completed_for_media(session, item.id)
                if completed_conv and completed_conv.optimized_file_path:
                    protected_names.add(os.path.basename(completed_conv.optimized_file_path))
        finally:
            session.close()

        duplicates: list[MediaFile] = []
        for vf in video_files:
            # Never mark protected optimized file as duplicate
            if vf.file_name in protected_names:
                continue

            name = vf.file_name.lower()
            # 1. Matches numeric duplicate pattern like .00001.mov
            if re.search(r"\.\d{5}\.", name):
                duplicates.append(vf)
                continue

            # 2. If an optimized .mp4 exists and this is an unoptimized older video (.mov)
            if protected_names:
                for prot in protected_names:
                    prot_stem = os.path.splitext(prot)[0]
                    vf_stem = os.path.splitext(vf.file_name)[0]
                    if vf_stem == prot_stem and vf.file_name != prot:
                        duplicates.append(vf)
                        break

        return duplicates

    @staticmethod
    def get_mdat_info(file_path: str) -> tuple[int, str] | None:
        """Extract QuickTime/MP4 mdat atom size and sample hash for exact video data matching."""
        if not os.path.isfile(file_path):
            return None
        try:
            with open(file_path, "rb") as f:
                while True:
                    header = f.read(8)
                    if len(header) < 8:
                        break
                    size, atom_type = struct.unpack(">I4s", header)
                    if atom_type == b"mdat":
                        sample_size = min(1024 * 1024, size - 8 if size > 8 else 1024 * 1024)
                        sample = f.read(sample_size)
                        return (size, hashlib.md5(sample).hexdigest())
                    if size == 1:
                        size = struct.unpack(">Q", f.read(8))[0] - 8
                    elif size == 0:
                        break
                    f.seek(size - 8, 1)
        except Exception:
            pass
        return None

    def find_cross_media_duplicates(
        self,
        min_size_mb: int = 10,
        limit: int | None = None,
    ) -> list[tuple[MediaItem, MediaItem]]:
        """Find true cross-media duplicate video pairs with verified matching video payload."""
        min_size_bytes = min_size_mb * 1024 * 1024
        session = self._media_repository.get_session()
        try:
            stmt = (
                select(MediaItem)
                .where(MediaItem.media_type == "video", MediaItem.file_size >= min_size_bytes)
                .order_by(desc(MediaItem.file_size))
            )
            videos = list(session.scalars(stmt))
        finally:
            session.close()

        dur_groups: dict[tuple[float, int | None, int | None], list[MediaItem]] = defaultdict(list)
        for v in videos:
            if v.duration and v.duration > 0.5:
                dur_groups[(round(v.duration, 1), v.width, v.height)].append(v)

        pairs: list[tuple[MediaItem, MediaItem]] = []
        seen_ids: set[int] = set()
        mdat_cache: dict[str, tuple[int, str] | None] = {}

        for group in dur_groups.values():
            if len(group) < 2:
                continue
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    if a.id in seen_ids or b.id in seen_ids:
                        continue

                    # Fast in-memory check 1: relative size diff must be small (< 1% or < 2MB)
                    size_diff = abs(a.file_size - b.file_size)
                    if size_diff > max(2 * 1024 * 1024, 0.01 * max(a.file_size, b.file_size)):
                        continue

                    # Fast in-memory check 2: taken_at proximity
                    if a.taken_at and b.taken_at:
                        diff_sec = abs((a.taken_at - b.taken_at).total_seconds())
                        if diff_sec > 86400:
                            if not (a.taken_at.minute == b.taken_at.minute and a.taken_at.second == b.taken_at.second):
                                continue

                    path_a = self.resolve_disk_path(a.file_path)
                    path_b = self.resolve_disk_path(b.file_path)

                    if path_a not in mdat_cache:
                        mdat_cache[path_a] = self.get_mdat_info(path_a)
                    if path_b not in mdat_cache:
                        mdat_cache[path_b] = self.get_mdat_info(path_b)

                    info_a = mdat_cache[path_a]
                    info_b = mdat_cache[path_b]

                    is_match = False
                    if info_a and info_b:
                        is_match = (info_a == info_b)
                    elif os.path.isfile(path_a) and os.path.isfile(path_b):
                        # Fallback for non-MP4: sample 1MB from 10% offset
                        try:
                            with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
                                offset_a = int(a.file_size * 0.1)
                                offset_b = int(b.file_size * 0.1)
                                fa.seek(offset_a)
                                fb.seek(offset_b)
                                is_match = (fa.read(1024 * 1024) == fb.read(1024 * 1024))
                        except Exception:
                            is_match = False

                    if is_match:
                        ordered = self._order_duplicate_pair(a, b)
                        pairs.append(ordered)
                        seen_ids.add(a.id)
                        seen_ids.add(b.id)
                        if limit and len(pairs) >= limit:
                            return pairs

        return pairs

    @staticmethod
    def _order_duplicate_pair(item_a: MediaItem, item_b: MediaItem) -> tuple[MediaItem, MediaItem]:
        """Order a duplicate pair so Video 1 is the cleanest/primary and Video 2 is the duplicate candidate."""
        import re

        has_num_a = bool(re.search(r"\.\d{5}\.", item_a.file_name))
        has_num_b = bool(re.search(r"\.\d{5}\.", item_b.file_name))
        if has_num_a and not has_num_b:
            return item_b, item_a
        if has_num_b and not has_num_a:
            return item_a, item_b

        if item_a.taken_at and item_b.taken_at and item_a.taken_at != item_b.taken_at:
            return (item_a, item_b) if item_a.taken_at <= item_b.taken_at else (item_b, item_a)

        if item_a.created_at and item_b.created_at and item_a.created_at != item_b.created_at:
            return (item_a, item_b) if item_a.created_at <= item_b.created_at else (item_b, item_a)

        return (item_a, item_b) if item_a.file_name <= item_b.file_name else (item_b, item_a)

    def remove_duplicate_file(
        self, file_item: MediaFile, keep_backup: bool = False, notify_photoprism: bool = True
    ) -> tuple[bool, str]:
        """Remove a duplicate stack file from disk and database."""
        disk_path = self.resolve_disk_path(file_item.file_path)
        if os.path.exists(disk_path):
            if keep_backup:
                backup_path = f"{disk_path}.dup.bak"
                shutil.move(disk_path, backup_path)
            else:
                os.remove(disk_path)

        # Also remove any associated sidecar (e.g., .mov.jpg)
        sidecar_path = f"{disk_path}.jpg"
        if os.path.exists(sidecar_path):
            try:
                os.remove(sidecar_path)
            except OSError:
                pass

        if self._file_repository:
            session = self._file_repository.get_session()
            try:
                self._file_repository.delete_by_file_uid(session, file_item.file_uid)
                session.commit()
            finally:
                session.close()

        if notify_photoprism and self._photoprism_client:
            subfolder = self.get_relative_subfolder(disk_path)
            try:
                self._photoprism_client.trigger_index(path=subfolder, cleanup=True)
            except Exception as e:
                logger.warning("Could not trigger PhotoPrism cleanup: %s", e)

        return True, f"Removed duplicate {file_item.file_name} ({disk_path})"

    def delete_media_item(
        self, item: MediaItem, keep_backup: bool = False, notify_photoprism: bool = True
    ) -> tuple[bool, str]:
        """Completely delete a media item, all its stack files on disk, and records from DB."""
        disk_path = self.resolve_disk_path(item.file_path)
        deleted_paths: list[str] = []

        # Find all stack files for this media from the file repository
        files_to_remove: list[str] = []
        if self._file_repository:
            session = self._file_repository.get_session()
            try:
                stack_files = self._file_repository.get_by_media_uid(session, item.uid)
                for f in stack_files:
                    f_disk = self.resolve_disk_path(f.file_path)
                    files_to_remove.append(f_disk)
                    # Also include any sidecar jpg
                    files_to_remove.append(f"{f_disk}.jpg")
            finally:
                session.close()

        # Always include the primary item file and its sidecar
        files_to_remove.append(disk_path)
        files_to_remove.append(f"{disk_path}.jpg")

        # Check if there is any conversion backup to remove if keep_backup is False
        if not keep_backup and self._conversion_repository:
            conv_session = self._media_repository.get_session()
            try:
                conversions = self._conversion_repository.get_by_media_id(conv_session, item.id)
                for conv in conversions:
                    if conv.backup_file_path and os.path.exists(conv.backup_file_path):
                        files_to_remove.append(conv.backup_file_path)
                    conv.status = "deleted"
                conv_session.commit()
            finally:
                conv_session.close()

        for fpath in set(files_to_remove):
            if os.path.exists(fpath):
                try:
                    if keep_backup and not fpath.endswith(".jpg"):
                        shutil.move(fpath, f"{fpath}.dup.bak")
                    else:
                        os.remove(fpath)
                    deleted_paths.append(fpath)
                except OSError as e:
                    logger.warning("Failed to remove file %s: %s", fpath, e)

        # Remove from database (both media_file and media items)
        if self._file_repository:
            session = self._file_repository.get_session()
            try:
                self._file_repository.delete_by_media_uid(session, item.uid)
                session.commit()
            finally:
                session.close()

        session = self._media_repository.get_session()
        try:
            db_item = self._media_repository.get_by_uid(session, item.uid)
            if db_item:
                self._media_repository.delete(session, db_item)
                session.commit()
        finally:
            session.close()

        # Notify PhotoPrism to prune orphaned records
        if notify_photoprism and self._photoprism_client:
            subfolder = self.get_relative_subfolder(disk_path)
            try:
                self._photoprism_client.trigger_index(path=subfolder, cleanup=True)
            except Exception as e:
                logger.warning("Could not trigger PhotoPrism cleanup: %s", e)

        return True, f"Deleted {item.file_name} and {len(deleted_paths)} associated file(s)"

