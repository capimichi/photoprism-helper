from __future__ import annotations

import click
from injector import inject
from tabulate import tabulate

from photoprismhelper.command.abstract_command import AbstractCommand
from photoprismhelper.service.storage_analysis_service import StorageAnalysisService
from photoprismhelper.service.video_optimizer_service import VideoOptimizerService


class VideoOptimizeCommand(AbstractCommand):
    command_name = "video:optimize"

    @inject
    def __init__(
        self,
        optimizer_service: VideoOptimizerService,
        storage_analysis_service: StorageAnalysisService,
    ) -> None:
        self._optimizer_service = optimizer_service
        self._storage_analysis_service = storage_analysis_service

    def register_options(self, fn):
        fn = click.option("--limit", "-l", default=1, type=int, help="Number of videos to optimize (default: 1).")(fn)
        fn = click.option("--min-size-mb", default=10, type=int, help="Minimum file size in MB to qualify (default: 10).")(fn)
        fn = click.option("--dry-run", is_flag=True, default=False, help="List candidates without converting.")(fn)
        fn = click.option("--interactive/--no-interactive", "-i/-y", default=True, help="Prompt before converting and replacing.")(fn)
        fn = click.option("--keep-backup/--no-backup", default=True, help="Keep .bak of original file (default: True).")(fn)
        fn = click.option("--notify/--no-notify", default=True, help="Notify PhotoPrism to re-index the folder (default: True).")(fn)
        fn = click.option("--revert", "revert_id", default=None, type=int, help="Revert a conversion by its ID.")(fn)
        fn = click.option("--history", is_flag=True, default=False, help="Show conversion history.")(fn)
        return fn

    def run(
        self,
        limit: int = 1,
        min_size_mb: int = 10,
        dry_run: bool = False,
        interactive: bool = True,
        keep_backup: bool = True,
        notify: bool = True,
        revert_id: int | None = None,
        history: bool = False,
    ) -> None:
        """Optimize heavy videos ordered by size, with verification and tracking."""
        # Handle revert
        if revert_id is not None:
            click.echo(f"Attempting to revert conversion #{revert_id}...")
            ok, msg = self._optimizer_service.revert_conversion(revert_id, notify_photoprism=notify)
            if ok:
                click.secho(f"✓ {msg}", fg="green")
            else:
                click.secho(f"✗ Revert failed: {msg}", fg="red")
            return

        # Handle history
        if history:
            self._show_history()
            return

        candidates = self._optimizer_service.get_candidate_videos(min_size_mb=min_size_mb, limit=limit)
        if not candidates:
            click.echo(f"No unoptimized videos found exceeding {min_size_mb} MB.")
            return

        # Prepare summary table
        rows = []
        for c in candidates:
            duration_str = "-"
            if c.duration:
                sec = c.duration / 1e9 if c.duration > 100_000 else c.duration
                if sec >= 60:
                    duration_str = f"{int(sec // 60)}m {int(sec % 60)}s"
                else:
                    duration_str = f"{sec:.1f}s"
            rows.append([
                c.uid[:12],
                c.file_name[:35],
                c.extension.upper(),
                self._storage_analysis_service.format_bytes(c.file_size),
                duration_str,
                c.folder_path[:25],
            ])

        click.echo("\n### Unoptimized Video Candidates")
        click.echo(tabulate(rows, headers=["UID", "File Name", "Ext", "Size", "Duration", "Folder"], tablefmt="github"))
        click.echo()

        if dry_run:
            click.secho("Dry-run mode active: no files were converted.", fg="yellow")
            return

        # Process candidates
        for idx, item in enumerate(candidates, start=1):
            size_fmt = self._storage_analysis_service.format_bytes(item.file_size)
            click.echo(f"\n[{idx}/{len(candidates)}] Candidate: {item.file_name} ({size_fmt})")

            disk_path = self._optimizer_service.resolve_disk_path(item.file_path)
            click.echo(f"  • Disk path: {disk_path}")

            if interactive:
                confirm = click.confirm(f"  Start 1080p HEVC optimization for {item.file_name}?", default=True)
                if not confirm:
                    click.echo("  Skipped.")
                    continue

            click.echo("  Encoding and cloning metadata with ffmpeg + exiftool...")
            conv = self._optimizer_service.optimize_media(
                item,
                keep_backup=keep_backup,
                replace_in_place=True,
                max_height=1080,
                notify_photoprism=notify,
            )

            if conv.status == "completed":
                opt_fmt = self._storage_analysis_service.format_bytes(conv.optimized_size or 0)
                saved_fmt = self._storage_analysis_service.format_bytes(conv.original_size - (conv.optimized_size or 0))
                pct = ((conv.original_size - (conv.optimized_size or 0)) / conv.original_size) * 100
                click.secho(f"  ✓ Conversion #{conv.id} succeeded in {conv.duration_seconds:.1f}s!", fg="green")
                click.echo(f"    - Original size: {size_fmt}")
                click.echo(f"    - Optimized size: {opt_fmt} (-{pct:.1f}%, saved {saved_fmt})")
                if keep_backup:
                    click.echo(f"    - Backup saved at: {conv.backup_file_path}")
                if notify:
                    click.echo("    - PhotoPrism re-index triggered.")
            else:
                click.secho(f"  ✗ Conversion failed: {conv.error_message}", fg="red")

    def _show_history(self) -> None:
        session = self._optimizer_service._media_repository.get_session()
        try:
            conversions = self._optimizer_service._conversion_repository.list_conversions(session, limit=20)
            if not conversions:
                click.echo("No conversion history found.")
                return

            rows = []
            for cv in conversions:
                saved = (cv.original_size - (cv.optimized_size or 0)) if cv.optimized_size else 0
                saved_fmt = self._storage_analysis_service.format_bytes(saved) if saved > 0 else "-"
                dt = cv.created_at.strftime("%Y-%m-%d %H:%M") if cv.created_at else "-"
                rows.append([
                    cv.id,
                    cv.media_uid[:12],
                    cv.status,
                    f"{cv.original_extension} -> {cv.optimized_extension}",
                    self._storage_analysis_service.format_bytes(cv.original_size),
                    self._storage_analysis_service.format_bytes(cv.optimized_size or 0) if cv.optimized_size else "-",
                    saved_fmt,
                    dt,
                ])

            click.echo("\n### Recent Conversions")
            click.echo(tabulate(rows, headers=["ID", "Media UID", "Status", "Format", "Original", "Optimized", "Saved", "Date"], tablefmt="github"))
        finally:
            session.close()
