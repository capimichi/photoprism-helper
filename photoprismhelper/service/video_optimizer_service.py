from __future__ import annotations

import logging
import os
import shutil
import subprocess
from injector import inject

from photoprismhelper.model.media_stat import OptimizationCandidate
from photoprismhelper.repository.media_repository import MediaRepository
from photoprismhelper.service.storage_analysis_service import StorageAnalysisService

logger = logging.getLogger(__name__)


class VideoOptimizerService:
    @inject
    def __init__(
        self,
        repository: MediaRepository,
        storage_analysis_service: StorageAnalysisService,
    ) -> None:
        self._repository = repository
        self._storage_analysis_service = storage_analysis_service

    def find_candidates(
        self,
        min_size_mb: int = 50,
        limit: int = 25,
    ) -> list[OptimizationCandidate]:
        """Find video files that are heavy and candidates for re-encoding."""
        min_size_bytes = min_size_mb * 1024 * 1024
        session = self._repository.get_session()
        try:
            items = self._repository.find_video_candidates_for_optimization(
                session, min_size_bytes=min_size_bytes, limit=limit
            )
            candidates: list[OptimizationCandidate] = []
            for item in items:
                # Estimate 50% savings using modern H.265 / AV1 compression
                est_savings = int(item.file_size * 0.50)
                reason = f"Video exceeds {min_size_mb} MB ({self._storage_analysis_service.format_bytes(item.file_size)})"
                if item.codec:
                    reason += f", codec: {item.codec}"

                candidates.append(
                    OptimizationCandidate(
                        uid=item.uid,
                        file_name=item.file_name,
                        file_path=item.file_path,
                        file_size_bytes=item.file_size,
                        media_type=item.media_type,
                        duration=item.duration,
                        codec=item.codec,
                        estimated_savings_bytes=est_savings,
                        reason=reason,
                    )
                )
            return candidates
        finally:
            session.close()

    def generate_ffmpeg_command(
        self,
        input_path: str,
        output_path: str,
        crf: int = 26,
        preset: str = "medium",
    ) -> str:
        """
        Generate FFmpeg command to re-encode video with H.265 preserving all metadata and EXIF.
        -map 0: include all streams (video, audio, subtitles)
        -map_metadata 0: keep all metadata tags
        -c:v libx265 -crf {crf} -preset {preset}: high efficiency compression
        -c:a copy: keep audio without recompression loss
        """
        return (
            f'ffmpeg -y -i "{input_path}" -map 0 -map_metadata 0 '
            f'-c:v libx265 -crf {crf} -preset {preset} -tag:v hvc1 -c:a copy "{output_path}"'
        )

    def optimize_file(
        self,
        input_file: str,
        output_file: str,
        crf: int = 26,
    ) -> tuple[bool, int, int]:
        """
        Run FFmpeg optimization preserving metadata and file timestamps.
        Returns: (success, original_size, optimized_size)
        """
        if not shutil.which("ffmpeg"):
            raise RuntimeError("FFmpeg is not installed or not in system PATH.")

        if not os.path.exists(input_file):
            raise FileNotFoundError(f"Input file not found: {input_file}")

        orig_size = os.path.getsize(input_file)
        cmd = [
            "ffmpeg",
            "-y",
            "-i", input_file,
            "-map", "0",
            "-map_metadata", "0",
            "-c:v", "libx265",
            "-crf", str(crf),
            "-preset", "medium",
            "-tag:v", "hvc1",
            "-c:a", "copy",
            output_file,
        ]

        logger.info("Executing command: %s", " ".join(cmd))
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            logger.error("FFmpeg failed: %s", result.stderr)
            return False, orig_size, 0

        # Preserve original modification and access times
        stat = os.stat(input_file)
        os.utime(output_file, (stat.st_atime, stat.st_mtime))

        optimized_size = os.path.getsize(output_file)
        return True, orig_size, optimized_size
