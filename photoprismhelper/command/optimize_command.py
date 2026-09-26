from __future__ import annotations

import os
import click
from injector import inject
from tabulate import tabulate

from photoprismhelper.command.abstract_command import AbstractCommand
from photoprismhelper.service.storage_analysis_service import StorageAnalysisService
from photoprismhelper.service.video_optimizer_service import VideoOptimizerService


class VideoOptimizeCommand(AbstractCommand):
    command_name = "optimize:videos"

    @inject
    def __init__(
        self,
        optimizer_service: VideoOptimizerService,
        storage_analysis_service: StorageAnalysisService,
    ) -> None:
        self._optimizer_service = optimizer_service
        self._storage_analysis_service = storage_analysis_service

    def register_options(self, fn):
        fn = click.option(
            "--min-size-mb",
            default=50,
            help="Minimum file size in MB to qualify as candidate (default: 50).",
        )(fn)
        fn = click.option(
            "--limit",
            default=25,
            help="Maximum number of candidates to list (default: 25).",
        )(fn)
        fn = click.option(
            "--generate-script",
            "script_file",
            default=None,
            type=click.Path(dir_okay=False, writable=True),
            help="Path to generate a shell script with FFmpeg commands.",
        )(fn)
        fn = click.option(
            "--crf",
            default=26,
            help="FFmpeg H.265 CRF value (default: 26). Lower is higher quality, 24-28 recommended.",
        )(fn)
        return fn

    def run(
        self,
        min_size_mb: int = 50,
        limit: int = 25,
        script_file: str | None = None,
        crf: int = 26,
    ) -> None:
        """Scan for heavy videos and generate FFmpeg optimization commands preserving metadata."""
        candidates = self._optimizer_service.find_candidates(min_size_mb=min_size_mb, limit=limit)

        if not candidates:
            click.echo(f"No video files found exceeding {min_size_mb} MB.")
            return

        total_curr_size = sum(c.file_size_bytes for c in candidates)
        total_est_savings = sum(c.estimated_savings_bytes for c in candidates)

        table_rows = [
            [
                c.uid[:12],
                c.file_name[:35],
                self._storage_analysis_service.format_bytes(c.file_size_bytes),
                f"{c.duration:.1f}s" if c.duration else "-",
                c.codec or "-",
                self._storage_analysis_service.format_bytes(c.estimated_savings_bytes),
                c.file_path[:35],
            ]
            for c in candidates
        ]

        click.echo(f"\nFound {len(candidates)} heavy video candidates:")
        click.echo(tabulate(
            table_rows,
            headers=["UID", "File Name", "Current Size", "Duration", "Codec", "Est. Savings (~50%)", "Path"],
            tablefmt="github",
        ))
        click.echo(f"\nTotal Candidate Size: {self._storage_analysis_service.format_bytes(total_curr_size)}")
        click.echo(f"Total Estimated Savings: {self._storage_analysis_service.format_bytes(total_est_savings)}\n")

        if script_file:
            lines = [
                "#!/usr/bin/env bash",
                "# Auto-generated FFmpeg video optimization script by photoprism-helper",
                "# Preserves all metadata, audio quality, and file modification timestamps.",
                "set -euo pipefail\n",
            ]
            for c in candidates:
                input_p = c.file_path
                output_p = f"{input_p}.optimized.mp4"
                backup_p = f"{input_p}.bak"
                cmd = self._optimizer_service.generate_ffmpeg_command(input_p, output_p, crf=crf)
                lines.append(f'echo "Optimizing: {input_p}"')
                lines.append(cmd)
                lines.append(f'touch -r "{input_p}" "{output_p}"')
                lines.append(f'mv "{input_p}" "{backup_p}"')
                lines.append(f'mv "{output_p}" "{input_p}"')
                lines.append(f'echo "✓ Replaced original with optimized version (backup: {backup_p})"\n')

            with open(script_file, "w") as f:
                f.write("\n".join(lines))
            os.chmod(script_file, 0o755)
            click.echo(f"✓ Optimization bash script generated at: {script_file}")
        else:
            click.echo("Tip: Use --generate-script optimize_videos.sh to generate an executable batch FFmpeg script.")
