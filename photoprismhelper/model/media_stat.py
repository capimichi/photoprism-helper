from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StorageSummary:
    total_files: int
    total_size_bytes: int
    image_count: int
    image_size_bytes: int
    video_count: int
    video_size_bytes: int
    other_count: int
    other_size_bytes: int

    @property
    def total_size_mb(self) -> float:
        return self.total_size_bytes / (1024 * 1024)

    @property
    def total_size_gb(self) -> float:
        return self.total_size_bytes / (1024 * 1024 * 1024)

    @property
    def video_size_gb(self) -> float:
        return self.video_size_bytes / (1024 * 1024 * 1024)

    @property
    def image_size_gb(self) -> float:
        return self.image_size_bytes / (1024 * 1024 * 1024)


@dataclass
class BreakdownStat:
    category: str
    count: int
    total_bytes: int

    @property
    def total_mb(self) -> float:
        return self.total_bytes / (1024 * 1024)

    @property
    def total_gb(self) -> float:
        return self.total_bytes / (1024 * 1024 * 1024)


@dataclass
class OptimizationCandidate:
    uid: str
    file_name: str
    file_path: str
    file_size_bytes: int
    media_type: str
    duration: float | None
    codec: str | None
    estimated_savings_bytes: int
    reason: str
