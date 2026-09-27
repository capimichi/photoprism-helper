from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable

from photoprismhelper.service.metadata_extractor import MetadataExtractor

logger = logging.getLogger(__name__)


@dataclass
class ConversionResult:
    success: bool
    output_path: str
    original_size: int
    optimized_size: int
    duration_seconds: float
    error_message: str | None = None


class VideoConverter:
    def __init__(self, metadata_extractor: MetadataExtractor | None = None) -> None:
        self._extractor = metadata_extractor or MetadataExtractor()
        self._ffmpeg_bin = shutil.which("ffmpeg")
        self._ffprobe_bin = shutil.which("ffprobe")

    def _detect_encoder(self) -> tuple[str, list[str]]:
        """Detect the best available H.265/HEVC encoder and parameters."""
        if not self._ffmpeg_bin:
            return "libx265", ["-crf", "25", "-preset", "fast"]

        try:
            res = subprocess.run([self._ffmpeg_bin, "-encoders"], capture_output=True, text=True)
            output = res.stdout
            if "hevc_videotoolbox" in output:
                # Apple Silicon hardware encoder
                return "hevc_videotoolbox", ["-b:v", "4500k", "-pix_fmt", "p010le"]
            if "libx265" in output:
                # Standard high quality CPU encoder
                return "libx265", ["-crf", "25", "-preset", "fast", "-pix_fmt", "yuv420p10le"]
        except Exception as e:
            logger.warning("Error detecting ffmpeg encoders: %s", e)

        return "libx265", ["-crf", "25", "-preset", "fast"]

    def get_stream_info(self, file_path: str) -> dict[str, Any]:
        """Inspect video streams using ffprobe."""
        if not self._ffprobe_bin or not os.path.isfile(file_path):
            return {}

        cmd = [
            self._ffprobe_bin,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            file_path,
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return json.loads(res.stdout)
        except Exception as e:
            logger.warning("ffprobe failed on %s: %s", file_path, e)
            return {}

    def verify(self, input_path: str, output_path: str) -> tuple[bool, str]:
        """Verify the integrity, duration, and streams of the converted file."""
        if not os.path.isfile(output_path):
            return False, "Output file does not exist."

        out_size = os.path.getsize(output_path)
        if out_size == 0:
            return False, "Output file is empty (0 bytes)."

        in_info = self.get_stream_info(input_path)
        out_info = self.get_stream_info(output_path)

        in_format = in_info.get("format", {})
        out_format = out_info.get("format", {})

        in_duration = float(in_format.get("duration", 0) or 0)
        out_duration = float(out_format.get("duration", 0) or 0)

        # Check duration matches within 1.0s tolerance
        if in_duration > 0 and out_duration > 0:
            diff = abs(in_duration - out_duration)
            if diff > 1.0:
                return False, f"Duration mismatch: input {in_duration:.1f}s vs output {out_duration:.1f}s (diff: {diff:.1f}s)"

        # Check audio stream presence if input had audio
        in_has_audio = any(s.get("codec_type") == "audio" for s in in_info.get("streams", []))
        out_has_audio = any(s.get("codec_type") == "audio" for s in out_info.get("streams", []))
        if in_has_audio and not out_has_audio:
            return False, "Input video has audio but converted video has none."

        return True, "Verification passed."

    def convert(
        self,
        input_path: str,
        output_path: str,
        max_height: int = 1080,
        progress_callback: Callable[[float], None] | None = None,
    ) -> ConversionResult:
        """Convert video to optimized 1080p MP4 preserving audio, 60fps, and cloning metadata."""
        if not self._ffmpeg_bin:
            return ConversionResult(
                success=False,
                output_path=output_path,
                original_size=os.path.getsize(input_path) if os.path.isfile(input_path) else 0,
                optimized_size=0,
                duration_seconds=0,
                error_message="ffmpeg binary not found.",
            )

        start_time = time.time()
        orig_size = os.path.getsize(input_path)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        encoder, encoder_args = self._detect_encoder()
        # Scale to max 1080 vertical/horizontal while maintaining aspect ratio and even dimensions
        scale_filter = f"scale=-2:{max_height}"

        cmd = [
            self._ffmpeg_bin,
            "-y",
            "-i",
            input_path,
            "-vf",
            scale_filter,
            "-c:v",
            encoder,
            *encoder_args,
            "-tag:v",
            "hvc1",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            output_path,
        ]

        logger.info("Starting ffmpeg conversion: %s -> %s (encoder: %s)", input_path, output_path, encoder)
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            logger.error("ffmpeg failed with code %d: %s", e.returncode, e.stderr)
            return ConversionResult(
                success=False,
                output_path=output_path,
                original_size=orig_size,
                optimized_size=0,
                duration_seconds=time.time() - start_time,
                error_message=f"ffmpeg error: {e.stderr[-300:] if e.stderr else 'unknown error'}",
            )

        # Clone metadata with exiftool
        self._extractor.clone_metadata(input_path, output_path)

        # Verify output
        valid, msg = self.verify(input_path, output_path)
        elapsed = time.time() - start_time
        if not valid:
            logger.error("Verification failed for %s: %s", output_path, msg)
            if os.path.exists(output_path):
                os.remove(output_path)
            return ConversionResult(
                success=False,
                output_path=output_path,
                original_size=orig_size,
                optimized_size=0,
                duration_seconds=elapsed,
                error_message=f"Verification failed: {msg}",
            )

        opt_size = os.path.getsize(output_path)
        logger.info("Conversion successful! Original: %d bytes, Optimized: %d bytes (elapsed: %.1fs)", orig_size, opt_size, elapsed)
        return ConversionResult(
            success=True,
            output_path=output_path,
            original_size=orig_size,
            optimized_size=opt_size,
            duration_seconds=elapsed,
        )
