from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from photoprismhelper.service.metadata_extractor import MetadataExtractor

logger = logging.getLogger(__name__)


@dataclass
class EncoderConfig:
    name: str
    is_hardware: bool
    description: str
    global_args: list[str] = field(default_factory=list)
    input_args: list[str] = field(default_factory=list)
    scale_filter_template: str = "scale=-2:{height}"
    output_args: list[str] = field(default_factory=list)


@dataclass
class ConversionResult:
    success: bool
    output_path: str
    original_size: int
    optimized_size: int
    duration_seconds: float
    encoder_used: str = ""
    is_hardware: bool = False
    fallback_triggered: bool = False
    fallback_reason: str | None = None
    audio_transcoded: bool = False
    source_audio_codec: str | None = None
    source_video_codec: str | None = None
    peculiarities: list[str] = field(default_factory=list)
    error_message: str | None = None
    integrity_message: str = ""


class VideoConverter:
    def __init__(self, metadata_extractor: MetadataExtractor | None = None) -> None:
        self._extractor = metadata_extractor or MetadataExtractor()
        self._ffmpeg_bin = shutil.which("ffmpeg")
        self._ffprobe_bin = shutil.which("ffprobe")
        self._cached_encoder: EncoderConfig | None = None

    def _probe_encoder(self, cfg: EncoderConfig) -> bool:
        """Run a minimal 1-frame in-memory probe to verify if the encoder actually works."""
        if not self._ffmpeg_bin:
            return False

        if cfg.name == "hevc_vaapi":
            probe_global = ["-vaapi_device", "/dev/dri/renderD128"]
            probe_vf = "format=nv12,hwupload"
        else:
            probe_global = cfg.global_args
            probe_vf = cfg.scale_filter_template.format(height=128)

        test_cmd = [
            self._ffmpeg_bin,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=128x128:d=0.04",
            *probe_global,
            "-vf",
            probe_vf,
            *cfg.output_args,
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ]
        try:
            res = subprocess.run(test_cmd, capture_output=True, text=True, timeout=6)
            return res.returncode == 0
        except Exception as e:
            logger.debug("Encoder probe failed for %s: %s", cfg.name, e)
            return False

    def get_software_encoder(self) -> EncoderConfig:
        """Return the CPU fallback encoder configuration (libx265)."""
        return EncoderConfig(
            name="libx265",
            is_hardware=False,
            description="Software CPU fallback (libx265)",
            scale_filter_template="scale=-2:{height}",
            output_args=["-c:v", "libx265", "-crf", "22", "-preset", "medium", "-pix_fmt", "yuv420p10le"],
        )

    def detect_encoder(self) -> EncoderConfig:
        """Detect the best available H.265/HEVC encoder (Hardware GPU or CPU fallback)."""
        if self._cached_encoder is not None:
            return self._cached_encoder

        if not self._ffmpeg_bin:
            logger.warning("ffmpeg binary not found. Falling back to default CPU config.")
            self._cached_encoder = self.get_software_encoder()
            return self._cached_encoder

        # Candidate encoders ordered by preference
        candidates: list[EncoderConfig] = []

        # 1. Linux VAAPI (Intel / AMD via /dev/dri/renderD128)
        vaapi_dev = "/dev/dri/renderD128"
        if os.path.exists(vaapi_dev):
            candidates.append(
                EncoderConfig(
                    name="hevc_vaapi",
                    is_hardware=True,
                    description=f"Linux VAAPI Hardware Acceleration ({vaapi_dev})",
                    global_args=[],
                    input_args=["-hwaccel", "vaapi", "-hwaccel_device", vaapi_dev, "-hwaccel_output_format", "vaapi"],
                    scale_filter_template="scale_vaapi=w=-2:h={height}",
                    output_args=["-c:v", "hevc_vaapi", "-rc_mode", "VBR", "-b:v", "5000k", "-maxrate", "8000k"],
                )
            )

        # 2. Apple Silicon VideoToolbox (macOS)
        candidates.append(
            EncoderConfig(
                name="hevc_videotoolbox",
                is_hardware=True,
                description="Apple Silicon VideoToolbox Hardware Acceleration",
                scale_filter_template="scale=-2:{height}",
                output_args=["-c:v", "hevc_videotoolbox", "-b:v", "5000k", "-pix_fmt", "p010le"],
            )
        )

        # 3. NVIDIA NVENC (CUDA / NVENC)
        candidates.append(
            EncoderConfig(
                name="hevc_nvenc",
                is_hardware=True,
                description="NVIDIA NVENC Hardware Acceleration",
                scale_filter_template="scale=-2:{height}",
                output_args=["-c:v", "hevc_nvenc", "-cq", "24", "-b:v", "5000k", "-maxrate", "8000k", "-preset", "p5", "-pix_fmt", "p010le"],
            )
        )

        # Try hardware candidates first
        for candidate in candidates:
            logger.debug("Probing hardware encoder '%s'...", candidate.name)
            if self._probe_encoder(candidate):
                logger.info("Hardware acceleration detected and verified: %s (%s)", candidate.name, candidate.description)
                self._cached_encoder = candidate
                return self._cached_encoder

        # Safe fallback: Software CPU (libx265)
        logger.info("No compatible GPU accelerator detected. Using CPU encoder: libx265 (preset medium, crf 22).")
        self._cached_encoder = self.get_software_encoder()
        return self._cached_encoder

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

        msg = f"duration: {out_duration:.1f}s, streams verified"
        return True, msg

    def _execute_ffmpeg(
        self,
        encoder_cfg: EncoderConfig,
        input_path: str,
        output_path: str,
        max_height: int,
        total_duration: float,
        in_info: dict[str, Any] | None = None,
    ) -> tuple[int, str]:
        """Execute ffmpeg process with real-time progress logging and return (returncode, stderr)."""
        scale_filter = encoder_cfg.scale_filter_template.format(height=max_height)

        # Check audio codec compatibility for MP4 container
        audio_args = ["-c:a", "copy"]
        if in_info:
            for s in in_info.get("streams", []):
                if s.get("codec_type") == "audio":
                    acodec = str(s.get("codec_name", "")).lower()
                    if acodec not in ("aac", "mp3", "alac", "ac3", "eac3"):
                        logger.info(
                            "Audio codec '%s' cannot be directly copied into MP4 container. Transcoding audio to AAC...",
                            acodec,
                        )
                        audio_args = ["-c:a", "aac", "-b:a", "128k"]
                    break

        cmd = [
            self._ffmpeg_bin,
            "-y",
            *encoder_cfg.global_args,
            *encoder_cfg.input_args,
            "-i",
            input_path,
            "-vf",
            scale_filter,
            *encoder_cfg.output_args,
            "-tag:v",
            "hvc1",
            *audio_args,
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            output_path,
        ]

        logger.info(
            "Starting ffmpeg conversion: %s -> %s (encoder: %s, hw: %s)",
            input_path,
            output_path,
            encoder_cfg.name,
            encoder_cfg.is_hardware,
        )

        fps = "0"
        speed = "1.0x"

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        if proc.stdout:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("fps="):
                    fps = line.split("=")[1].strip()
                elif line.startswith("speed="):
                    speed = line.split("=")[1].strip()
                elif line.startswith("out_time="):
                    t_str = line.split("=")[1].strip()
                    parts = t_str.split(":")
                    if len(parts) == 3 and total_duration > 0:
                        try:
                            curr_sec = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                            pct = min(100.0, (curr_sec / total_duration) * 100)
                            bar_len = 20
                            filled = int(bar_len * pct / 100)
                            bar = "=" * filled + "-" * (bar_len - filled)
                            curr_m, curr_s = int(curr_sec // 60), int(curr_sec % 60)
                            tot_m, tot_s = int(total_duration // 60), int(total_duration % 60)
                            sys.stdout.write(
                                f"\r  [Encoding ({encoder_cfg.name})] {pct:5.1f}% [{bar}] {curr_m:02d}:{curr_s:02d}/{tot_m:02d}:{tot_s:02d} ({fps} fps, {speed})"
                            )
                            sys.stdout.flush()
                        except Exception:
                            pass

        proc.wait()
        sys.stdout.write("\n")
        sys.stdout.flush()

        err_text = proc.stderr.read() if proc.stderr else ""
        return proc.returncode, err_text

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

        encoder_cfg = self.detect_encoder()
        scale_filter = encoder_cfg.scale_filter_template.format(height=max_height)

        # Get total duration and inspect streams
        in_info = self.get_stream_info(input_path)
        total_duration = float(in_info.get("format", {}).get("duration", 0) or 0)

        fallback_triggered = False
        fallback_reason: str | None = None
        audio_transcoded = False
        source_audio_codec: str | None = None
        source_video_codec: str | None = None
        peculiarities: list[str] = []

        for s in in_info.get("streams", []):
            if s.get("codec_type") == "video" and not source_video_codec:
                source_video_codec = s.get("codec_name")
                tags = s.get("tags", {})
                sd_list = s.get("side_data_list", [])
                if "rotate" in tags or any("rotation" in str(sd) for sd in sd_list):
                    peculiarities.append("rotated")
            elif s.get("codec_type") == "audio" and not source_audio_codec:
                source_audio_codec = str(s.get("codec_name", "")).lower()
                if source_audio_codec not in ("aac", "mp3", "alac", "ac3", "eac3"):
                    audio_transcoded = True
                    peculiarities.append(f"audio_{source_audio_codec}_to_aac")

        try:
            returncode, err_text = self._execute_ffmpeg(
                encoder_cfg, input_path, output_path, max_height, total_duration, in_info=in_info
            )

            # Automatic fallback to software CPU encoder (libx265) if hardware acceleration fails
            if returncode != 0 and encoder_cfg.is_hardware:
                fallback_triggered = True
                fallback_reason = (err_text[-180:] if err_text else "hardware encode failed").strip()
                peculiarities.append("gpu_fallback_to_cpu")

                fallback_cfg = self.get_software_encoder()
                logger.warning(
                    "Hardware encoder '%s' failed on '%s' (code %d). Falling back to software CPU (%s)...",
                    encoder_cfg.name,
                    input_path,
                    returncode,
                    fallback_cfg.name,
                )
                sys.stdout.write(
                    f"  ⚠ Hardware encoder ({encoder_cfg.name}) failed. Falling back to CPU ({fallback_cfg.name})...\n"
                )
                sys.stdout.flush()

                if os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass

                encoder_cfg = fallback_cfg
                returncode, err_text = self._execute_ffmpeg(
                    encoder_cfg, input_path, output_path, max_height, total_duration, in_info=in_info
                )

            if returncode != 0:
                logger.error("ffmpeg failed with code %d: %s", returncode, err_text)
                return ConversionResult(
                    success=False,
                    output_path=output_path,
                    original_size=orig_size,
                    optimized_size=0,
                    duration_seconds=time.time() - start_time,
                    encoder_used=encoder_cfg.name,
                    is_hardware=encoder_cfg.is_hardware,
                    fallback_triggered=fallback_triggered,
                    fallback_reason=fallback_reason,
                    audio_transcoded=audio_transcoded,
                    source_audio_codec=source_audio_codec,
                    source_video_codec=source_video_codec,
                    peculiarities=peculiarities,
                    error_message=f"ffmpeg error: {err_text[-300:] if err_text else 'unknown error'}",
                )
        except Exception as e:
            logger.error("ffmpeg process failed: %s", e)
            return ConversionResult(
                success=False,
                output_path=output_path,
                original_size=orig_size,
                optimized_size=0,
                duration_seconds=time.time() - start_time,
                encoder_used=encoder_cfg.name,
                is_hardware=encoder_cfg.is_hardware,
                fallback_triggered=fallback_triggered,
                fallback_reason=fallback_reason,
                audio_transcoded=audio_transcoded,
                source_audio_codec=source_audio_codec,
                source_video_codec=source_video_codec,
                peculiarities=peculiarities,
                error_message=str(e),
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
                encoder_used=encoder_cfg.name,
                is_hardware=encoder_cfg.is_hardware,
                fallback_triggered=fallback_triggered,
                fallback_reason=fallback_reason,
                audio_transcoded=audio_transcoded,
                source_audio_codec=source_audio_codec,
                source_video_codec=source_video_codec,
                peculiarities=peculiarities,
                error_message=f"Verification failed: {msg}",
            )

        opt_size = os.path.getsize(output_path)
        if opt_size >= orig_size:
            logger.warning(
                "Optimization aborted: converted file (%d bytes) is not smaller than original (%d bytes).",
                opt_size,
                orig_size,
            )
            if os.path.exists(output_path):
                os.remove(output_path)
            return ConversionResult(
                success=False,
                output_path=output_path,
                original_size=orig_size,
                optimized_size=opt_size,
                duration_seconds=elapsed,
                encoder_used=encoder_cfg.name,
                is_hardware=encoder_cfg.is_hardware,
                fallback_triggered=fallback_triggered,
                fallback_reason=fallback_reason,
                audio_transcoded=audio_transcoded,
                source_audio_codec=source_audio_codec,
                source_video_codec=source_video_codec,
                peculiarities=peculiarities,
                error_message=(
                    f"Optimized file is not smaller than original "
                    f"({self._format_bytes(opt_size)} >= {self._format_bytes(orig_size)}). Optimization aborted."
                ),
            )

        logger.info(
            "Conversion successful! Original: %d bytes, Optimized: %d bytes (elapsed: %.1fs, encoder: %s)",
            orig_size,
            opt_size,
            elapsed,
            encoder_cfg.name,
        )
        return ConversionResult(
            success=True,
            output_path=output_path,
            original_size=orig_size,
            optimized_size=opt_size,
            duration_seconds=elapsed,
            encoder_used=encoder_cfg.name,
            is_hardware=encoder_cfg.is_hardware,
            fallback_triggered=fallback_triggered,
            fallback_reason=fallback_reason,
            audio_transcoded=audio_transcoded,
            source_audio_codec=source_audio_codec,
            source_video_codec=source_video_codec,
            peculiarities=peculiarities,
            integrity_message=msg,
        )

    @staticmethod
    def _format_bytes(size: int | float) -> str:
        s = float(size)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if abs(s) < 1024.0:
                return f"{s:.2f} {unit}"
            s /= 1024.0
        return f"{s:.2f} PB"

