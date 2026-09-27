from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class MetadataExtractor:
    @staticmethod
    def calculate_file_hash(file_path: str, block_size: int = 65536) -> str:
        """Calculate SHA-256 hash of a file."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for block in iter(lambda: f.read(block_size), b""):
                hasher.update(block)
        return hasher.hexdigest()

    @staticmethod
    def extract_metadata(file_path: str) -> dict[str, Any]:
        """Extract metadata tags using exiftool."""
        if not os.path.isfile(file_path):
            return {}

        exiftool_bin = shutil.which("exiftool")
        if not exiftool_bin:
            logger.warning("exiftool not found on system PATH.")
            return {}

        try:
            cmd = [exiftool_bin, "-j", file_path]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(proc.stdout)
            if isinstance(data, list) and len(data) > 0:
                tags = data[0]
                # Filter out heavy binary or raw file path tags
                cleaned = {
                    k: v
                    for k, v in tags.items()
                    if k not in ("Directory", "SourceFile", "ExifToolVersion", "FilePermissions")
                }
                return cleaned
        except Exception as e:
            logger.warning("Failed to extract metadata with exiftool for %s: %s", file_path, e)

        return {}

    @classmethod
    def clone_metadata(cls, source_path: str, target_path: str) -> bool:
        """Clone metadata from source to target using exiftool, ignoring rotation matrix."""
        exiftool_bin = shutil.which("exiftool")
        if not exiftool_bin:
            return False

        try:
            # -x Rotation -x MatrixStructure avoids rotating a video that was already physically scaled
            cmd = [
                exiftool_bin,
                "-tagsFromFile",
                source_path,
                "-all:all",
                "-x",
                "Rotation",
                "-x",
                "MatrixStructure",
                "-overwrite_original",
                target_path,
            ]
            subprocess.run(cmd, capture_output=True, text=True, check=True)

            # Preserve filesystem modification/creation time
            if os.path.exists(source_path) and os.path.exists(target_path):
                shutil.copystat(source_path, target_path)

            return True
        except Exception as e:
            logger.warning("Failed to clone metadata from %s to %s: %s", source_path, target_path, e)
            return False
