from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from photoprismhelper.entity.media_file import MediaFile


@dataclass
class StackDuplicateCandidate:
    media_uid: str
    media_id: int
    photo_title: str | None
    taken_at: datetime | None

    file1: MediaFile
    file2: MediaFile

    master_file: MediaFile
    proxy_file: MediaFile

    duplicate_type: str  # "master_proxy", "numbered_copy", "same_duration"
    duration_diff: float
    size_ratio: float
    all_files: list[MediaFile] = field(default_factory=list)

    @property
    def is_master_primary(self) -> bool:
        return bool(self.master_file.is_primary)

    @property
    def is_proxy_primary(self) -> bool:
        return bool(self.proxy_file.is_primary)
