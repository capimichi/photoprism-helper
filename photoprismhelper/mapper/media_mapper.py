from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

from photoprismhelper.entity.media_file import MediaFile
from photoprismhelper.entity.media_item import MediaItem


class MediaMapper:
    @staticmethod
    def parse_datetime(dt_str: str | None) -> datetime | None:
        if not dt_str:
            return None
        # Common ISO formats from PhotoPrism e.g. 2023-08-15T14:32:00Z
        for fmt in (
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(dt_str.split("+")[0].rstrip("Z"), fmt.rstrip("Z"))
            except ValueError:
                continue
        return None

    @classmethod
    def extract_suggested_tags_from_folder(cls, folder_path: str) -> list[str]:
        """Extract clean tags from folder names, skipping pure date/time segments."""
        if not folder_path or folder_path == ".":
            return []

        parts = [p.strip() for p in folder_path.replace("\\", "/").split("/") if p.strip()]
        tags: list[str] = []

        for part in parts:
            # Skip purely numeric parts (like year '2023', month '07', day '15')
            if re.match(r"^\d{1,4}$", part):
                continue
            # Skip standard system or camera folder names
            if part.lower() in {"dcim", "camera", "import", "originals", "photos", "videos"}:
                continue
            # Format tag nicely: replace underscores/dashes with spaces or keep clean
            tag = part.replace("_", " ").strip()
            if tag and tag not in tags:
                tags.append(tag)

        return tags

    VIDEO_EXTENSIONS = (".mov", ".mp4", ".avi", ".mkv", ".m4v", ".3gp", ".webm")

    @classmethod
    def choose_media_file(cls, files: list[dict[str, Any]], media_type: str) -> dict[str, Any]:
        """Select the most representative original media file instead of a sidecar preview."""
        if not files:
            return {}

        # 1. For videos, prefer actual video file over thumbnail/sidecar JPG
        if media_type == "video":
            for f in files:
                name = (f.get("Name") or "").lower()
                if (f.get("Video") or f.get("MediaType") == "video") and not name.endswith(".jpg"):
                    return f
            for f in files:
                name = (f.get("Name") or "").lower()
                if any(name.endswith(ext) for ext in cls.VIDEO_EXTENSIONS):
                    return f

        # 2. Look for an original file (Root != 'sidecar' and != 'cache')
        originals = [f for f in files if f.get("Root") not in ("sidecar", "cache")]
        if originals:
            for f in originals:
                if f.get("Primary"):
                    return f
            return max(originals, key=lambda f: int(f.get("Size") or 0))

        # 3. Fallback to Primary file or first available
        for f in files:
            if f.get("Primary"):
                return f
        return files[0]

    @classmethod
    def to_entity(cls, photo_data: dict[str, Any]) -> MediaItem:
        uid = photo_data.get("UID", "")
        media_type = (photo_data.get("Type") or "unknown").lower()
        title = photo_data.get("Title") or photo_data.get("Name")
        is_favorite = bool(photo_data.get("Favorite", False))
        taken_at = cls.parse_datetime(photo_data.get("TakenAt"))

        files = photo_data.get("Files") or []
        primary_file = cls.choose_media_file(files, media_type)

        file_name = (
            primary_file.get("Name")
            or photo_data.get("FileName")
            or photo_data.get("OriginalName")
            or "unknown"
        )
        file_path = photo_data.get("FileName") or primary_file.get("Name") or file_name
        folder_path = photo_data.get("Path") or os.path.dirname(file_path) or ""

        # If file_name or file_path points to a sidecar like video.mov.jpg, strip .jpg
        for vid_ext in cls.VIDEO_EXTENSIONS:
            sidecar_suffix = f"{vid_ext}.jpg"
            if file_name.lower().endswith(sidecar_suffix):
                file_name = file_name[:-4]
            if file_path.lower().endswith(sidecar_suffix):
                file_path = file_path[:-4]
                media_type = "video"
                break

        file_hash = primary_file.get("Hash") or photo_data.get("Hash")
        file_size = int(primary_file.get("Size") or photo_data.get("Size") or 0)

        # If originals path is accessible on disk, verify actual size
        originals_dir = os.getenv("PHOTOPRISM_ORIGINALS_PATH", "/photoprism/originals")
        if os.path.isdir(originals_dir):
            disk_file = os.path.join(originals_dir, file_path)
            if os.path.isfile(disk_file):
                try:
                    disk_size = os.path.getsize(disk_file)
                    if disk_size > 0:
                        file_size = disk_size
                except OSError:
                    pass

        mime_type = primary_file.get("Mime")
        width = primary_file.get("Width") or photo_data.get("Width")
        height = primary_file.get("Height") or photo_data.get("Height")
        duration = primary_file.get("Duration") or photo_data.get("Duration")
        codec = primary_file.get("Codec")
        fps = primary_file.get("FPS")

        # Determine extension
        _, ext = os.path.splitext(file_name)
        extension = ext.lstrip(".").lower()
        if not extension and "." in file_path:
            extension = os.path.splitext(file_path)[1].lstrip(".").lower()

        if media_type == "unknown" and any(file_name.lower().endswith(ext) for ext in cls.VIDEO_EXTENSIONS):
            media_type = "video"

        # Existing tags/keywords
        keywords = ""
        details = photo_data.get("Details")
        if isinstance(details, dict):
            keywords = details.get("Keywords") or ""

        # Suggested tags based on folder path
        suggested_tags_list = cls.extract_suggested_tags_from_folder(folder_path)
        suggested_tags_str = ", ".join(suggested_tags_list) if suggested_tags_list else None

        duration_val = float(duration) if duration else None
        if duration_val and duration_val > 100_000:
            duration_val = duration_val / 1e9

        return MediaItem(
            uid=uid,
            file_hash=file_hash,
            file_name=os.path.basename(file_name),
            file_path=file_path,
            folder_path=folder_path,
            file_size=file_size,
            media_type=media_type,
            extension=extension,
            mime_type=mime_type,
            width=int(width) if width else None,
            height=int(height) if height else None,
            duration=duration_val,
            codec=codec,
            fps=float(fps) if fps else None,
            taken_at=taken_at,
            photo_title=title,
            is_favorite=is_favorite,
            tags=keywords if keywords else None,
        )

    @classmethod
    def to_file_entities(
        cls, photo_data: dict[str, Any], media_item: MediaItem
    ) -> list[MediaFile]:
        """Map all files in photo details to MediaFile entities."""
        files_data = photo_data.get("Files") or []
        entities: list[MediaFile] = []

        for f in files_data:
            file_uid = f.get("UID")
            if not file_uid:
                continue

            name = f.get("Name") or ""
            root = f.get("Root") or "/"
            size = int(f.get("Size") or 0)
            media_type = (f.get("MediaType") or "").lower()
            codec = f.get("Codec")
            width = int(f.get("Width")) if f.get("Width") else None
            height = int(f.get("Height")) if f.get("Height") else None
            duration = float(f.get("Duration")) if f.get("Duration") else None
            if duration and duration > 100_000:
                duration = duration / 1e9
            fps = float(f.get("FPS")) if f.get("FPS") else None
            mime_type = f.get("Mime")
            is_primary = bool(f.get("Primary", False))
            is_missing = bool(f.get("Missing", False))
            is_sidecar = bool(root == "sidecar" or f.get("Sidecar", False))
            is_video = bool(
                f.get("Video")
                or media_type == "video"
                or any(name.lower().endswith(ext) for ext in cls.VIDEO_EXTENSIONS)
            )

            media_file = MediaFile(
                media_id=media_item.id,
                media_uid=media_item.uid,
                file_uid=file_uid,
                file_name=os.path.basename(name),
                file_path=name,
                file_root=root,
                file_size=size,
                file_hash=f.get("Hash"),
                media_type=media_type if media_type else ("video" if is_video else "image"),
                codec=codec,
                width=width,
                height=height,
                duration=duration,
                fps=fps,
                mime_type=mime_type,
                is_primary=is_primary,
                is_missing=is_missing,
                is_video=is_video,
                is_sidecar=is_sidecar,
            )
            entities.append(media_file)

        return entities

