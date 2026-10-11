from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

INVALID_DATES = {
    "",
    "0000:00:00 00:00:00",
    "1970:01:01 00:00:00",
    "0000:00:00",
    "1970:01:01",
}

DEFAULT_TARGET_TAGS = ("CreateDate", "DateTimeOriginal", "ModifyDate")

DEFAULT_EXTENSIONS = {
    ".heic",
    ".jpg",
    ".jpeg",
    ".png",
    ".mov",
    ".mp4",
    ".m4v",
    ".avi",
    ".mkv",
    ".tiff",
    ".tif",
    ".webp",
    ".dng",
}


class FileExifState(str, Enum):
    ALREADY_VALID = "already_valid"
    NEEDS_UPDATE = "needs_update"
    UPDATED = "updated"
    SKIPPED_NOT_FILE = "skipped_not_file"
    SKIPPED_UNSUPPORTED_EXT = "skipped_unsupported_ext"
    SKIPPED_NO_DATE_IN_FILENAME = "skipped_no_date_in_filename"
    ERROR = "error"


@dataclass
class ExifItemResult:
    path: str
    file_name: str
    state: FileExifState
    parsed_datetime: datetime | None = None
    missing_tags: list[str] = field(default_factory=list)
    error_message: str | None = None


@dataclass
class ExifFixSummary:
    total_scanned: int = 0
    already_valid: int = 0
    needs_update: int = 0
    updated: int = 0
    skipped_no_date: int = 0
    skipped_unsupported: int = 0
    errors: int = 0
    items: list[ExifItemResult] = field(default_factory=list)


class ExifFixService:
    def __init__(self, exiftool_bin: str | None = None) -> None:
        self._exiftool_bin = exiftool_bin or shutil.which("exiftool")

    @property
    def exiftool_bin(self) -> str | None:
        return self._exiftool_bin

    @staticmethod
    def parse_filename_datetime(filename: str) -> datetime | None:
        """Parse datetime from filename matching YYYYMMDD_HHMMSS prefix."""
        base = os.path.basename(filename)
        match = re.match(r"^(\d{8})_(\d{6})", base)
        if not match:
            return None
        date_str, time_str = match.groups()
        try:
            return datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
        except ValueError:
            return None

    def get_exif_tags(self, file_path: str, tags: Iterable[str] = DEFAULT_TARGET_TAGS) -> dict[str, str]:
        """Read requested EXIF tags for a file using exiftool JSON output."""
        if not self._exiftool_bin or not os.path.isfile(file_path):
            return {}

        tag_args = [f"-{tag}" for tag in tags]
        cmd = [self._exiftool_bin, "-j", "-fast2", *tag_args, file_path]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(proc.stdout)
            if isinstance(data, list) and len(data) > 0:
                return {k: str(v).strip() for k, v in data[0].items() if v is not None}
        except Exception as e:
            logger.warning("Error reading exif tags for %s: %s", file_path, e)
        return {}

    def inspect_file(
        self,
        file_path: str,
        target_tags: Iterable[str] = DEFAULT_TARGET_TAGS,
        allowed_extensions: set[str] = DEFAULT_EXTENSIONS,
    ) -> ExifItemResult:
        """Inspect a single file to determine if its EXIF tags match the filename datetime."""
        file_name = os.path.basename(file_path)

        if not os.path.isfile(file_path):
            return ExifItemResult(
                path=file_path,
                file_name=file_name,
                state=FileExifState.SKIPPED_NOT_FILE,
            )

        _, ext = os.path.splitext(file_name)
        if ext.lower() not in allowed_extensions:
            return ExifItemResult(
                path=file_path,
                file_name=file_name,
                state=FileExifState.SKIPPED_UNSUPPORTED_EXT,
            )

        dt = self.parse_filename_datetime(file_name)
        if dt is None:
            return ExifItemResult(
                path=file_path,
                file_name=file_name,
                state=FileExifState.SKIPPED_NO_DATE_IN_FILENAME,
            )

        existing_tags = self.get_exif_tags(file_path, tags=target_tags)
        missing_tags: list[str] = []

        for tag in target_tags:
            val = existing_tags.get(tag, "")
            # Some formats return "Create Date" or "CreateDate"
            if not val:
                normalized_key = tag.replace(" ", "")
                val = next((v for k, v in existing_tags.items() if k.replace(" ", "") == normalized_key), "")

            if not val or val in INVALID_DATES or val.startswith("0000:00:00") or val.startswith("1970:01:01"):
                missing_tags.append(tag)

        if not missing_tags:
            return ExifItemResult(
                path=file_path,
                file_name=file_name,
                state=FileExifState.ALREADY_VALID,
                parsed_datetime=dt,
            )

        return ExifItemResult(
            path=file_path,
            file_name=file_name,
            state=FileExifState.NEEDS_UPDATE,
            parsed_datetime=dt,
            missing_tags=missing_tags,
        )

    def apply_tags(self, file_path: str, tags: list[str], dt: datetime) -> bool:
        """Write specified tags to the file using exiftool in a single atomic invocation."""
        if not self._exiftool_bin or not tags:
            return False

        formatted_dt = dt.strftime("%Y:%m:%d %H:%M:%S")
        args = [f"-{tag}={formatted_dt}" for tag in tags]
        cmd = [self._exiftool_bin, "-overwrite_original", *args, file_path]

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return True
        except subprocess.CalledProcessError as e:
            logger.error("Failed to update EXIF for %s: %s", file_path, e.stderr)
            return False

    def process_path(
        self,
        target_path: str,
        recursive: bool = True,
        dry_run: bool = False,
        target_tags: Iterable[str] = DEFAULT_TARGET_TAGS,
        allowed_extensions: set[str] = DEFAULT_EXTENSIONS,
        progress_callback: Callable[[ExifItemResult], None] | None = None,
    ) -> ExifFixSummary:
        """Scan a file or directory, inspect EXIF metadata, and apply missing tags from filename."""
        summary = ExifFixSummary()

        if not self._exiftool_bin:
            raise RuntimeError("exiftool is required but was not found on system PATH.")

        if os.path.isfile(target_path):
            candidates = [target_path]
        elif os.path.isdir(target_path):
            candidates = []
            if recursive:
                for root, _, files in os.walk(target_path):
                    for f in sorted(files):
                        candidates.append(os.path.join(root, f))
            else:
                for entry in sorted(os.listdir(target_path)):
                    full_p = os.path.join(target_path, entry)
                    if os.path.isfile(full_p):
                        candidates.append(full_p)
        else:
            raise FileNotFoundError(f"Path does not exist: {target_path}")

        for p in candidates:
            summary.total_scanned += 1
            res = self.inspect_file(p, target_tags=target_tags, allowed_extensions=allowed_extensions)

            if res.state == FileExifState.ALREADY_VALID:
                summary.already_valid += 1
            elif res.state == FileExifState.NEEDS_UPDATE:
                summary.needs_update += 1
                if not dry_run and res.parsed_datetime:
                    success = self.apply_tags(p, res.missing_tags, res.parsed_datetime)
                    if success:
                        res.state = FileExifState.UPDATED
                        summary.updated += 1
                    else:
                        res.state = FileExifState.ERROR
                        res.error_message = "exiftool command failed"
                        summary.errors += 1
            elif res.state == FileExifState.SKIPPED_NO_DATE_IN_FILENAME:
                summary.skipped_no_date += 1
            elif res.state == FileExifState.SKIPPED_UNSUPPORTED_EXT:
                summary.skipped_unsupported += 1
            elif res.state == FileExifState.ERROR:
                summary.errors += 1

            summary.items.append(res)
            if progress_callback:
                progress_callback(res)

        return summary
