from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

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

    @classmethod
    def to_entity(cls, photo_data: dict[str, Any]) -> MediaItem:
        uid = photo_data.get("UID", "")
        media_type = (photo_data.get("Type") or "unknown").lower()
        title = photo_data.get("Title") or photo_data.get("Name")
        is_favorite = bool(photo_data.get("Favorite", False))
        taken_at = cls.parse_datetime(photo_data.get("TakenAt"))

        # Inspect files array inside photo data to find primary file information
        files = photo_data.get("Files") or []
        primary_file = files[0] if files else {}
        for f in files:
            if f.get("Primary"):
                primary_file = f
                break

        file_name = (
            primary_file.get("Name")
            or photo_data.get("FileName")
            or photo_data.get("OriginalName")
            or "unknown"
        )
        file_path = photo_data.get("FileName") or primary_file.get("Name") or file_name
        folder_path = photo_data.get("Path") or os.path.dirname(file_path) or ""

        file_hash = primary_file.get("Hash") or photo_data.get("Hash")
        file_size = int(primary_file.get("Size") or photo_data.get("Size") or 0)
        mime_type = primary_file.get("Mime")
        width = primary_file.get("Width") or photo_data.get("Width")
        height = primary_file.get("Height") or photo_data.get("Height")
        duration = primary_file.get("Duration") or photo_data.get("Duration")
        codec = primary_file.get("Codec")
        fps = primary_file.get("FPS")

        # Determine extension
        _, ext = os.path.splitext(file_name)
        extension = ext.lstrip(".").lower()

        # Existing tags/keywords
        keywords = ""
        details = photo_data.get("Details")
        if isinstance(details, dict):
            keywords = details.get("Keywords") or ""

        # Suggested tags based on folder path
        suggested_tags_list = cls.extract_suggested_tags_from_folder(folder_path)
        suggested_tags_str = ", ".join(suggested_tags_list) if suggested_tags_list else None

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
            duration=float(duration) if duration else None,
            codec=codec,
            fps=float(fps) if fps else None,
            taken_at=taken_at,
            photo_title=title,
            is_favorite=is_favorite,
            tags=keywords if keywords else None,
            suggested_tags=suggested_tags_str,
            optimization_status="pending",
        )
