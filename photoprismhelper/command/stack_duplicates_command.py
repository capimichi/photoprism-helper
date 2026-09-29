from __future__ import annotations

import click
from injector import inject
from tabulate import tabulate

from photoprismhelper.command.abstract_command import AbstractCommand
from photoprismhelper.entity.media_file import MediaFile
from photoprismhelper.repository.media_file_repository import MediaFileRepository
from photoprismhelper.repository.media_repository import MediaRepository
from photoprismhelper.service.storage_analysis_service import StorageAnalysisService
from photoprismhelper.service.video_optimizer_service import VideoOptimizerService


class VideoDuplicatesCommand(AbstractCommand):
    command_name = "video:duplicates"

    @inject
    def __init__(
        self,
        file_repository: MediaFileRepository,
        media_repository: MediaRepository,
        optimizer_service: VideoOptimizerService,
        storage_analysis_service: StorageAnalysisService,
    ) -> None:
        self._file_repository = file_repository
        self._media_repository = media_repository
        self._optimizer_service = optimizer_service
        self._storage_analysis_service = storage_analysis_service

    def register_options(self, fn):
        fn = click.option("--clean", is_flag=True, default=False, help="Delete detected duplicate files from disk and PhotoPrism.")(fn)
        fn = click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt before cleaning.")(fn)
        fn = click.option("--keep-backup", is_flag=True, default=False, help="Rename to .bak instead of deleting from disk.")(fn)
        return fn

    def run(self, clean: bool = False, yes: bool = False, keep_backup: bool = False) -> None:
        """Scan and manage stacked duplicate video files (.00001.mov, etc.)."""
        session = self._file_repository.get_session()
        try:
            stacked_uids = self._file_repository.find_stacked_video_media_uids(session)
            if not stacked_uids:
                click.echo("No stacked video media items found in the database.")
                click.echo("Tip: Run 'media:sync' to index all stack files first.")
                return

            duplicates: list[tuple[str, MediaFile, str]] = []
            total_wasted_bytes = 0

            for uid in stacked_uids:
                media_item = self._media_repository.get_by_uid(session, uid)
                primary_name = media_item.file_name if media_item else None
                dups = self._optimizer_service.get_stack_duplicates(uid, primary_file_name=primary_name)
                for d in dups:
                    duplicates.append((uid, d, primary_name or "unknown"))
                    total_wasted_bytes += d.file_size

            if not duplicates:
                click.echo("✓ All stacked videos are clean: no duplicate video files detected.")
                return

            rows = []
            for uid, d, primary in duplicates:
                size_str = self._storage_analysis_service.format_bytes(d.file_size)
                rows.append([
                    uid,
                    d.file_name,
                    size_str,
                    primary,
                    d.file_path,
                ])

            wasted_fmt = self._storage_analysis_service.format_bytes(total_wasted_bytes)
            click.echo(f"\n### Detected Video Stack Duplicates ({len(duplicates)} files, {wasted_fmt} wasted)")
            click.echo(tabulate(rows, headers=["Media UID", "Duplicate File", "Size", "Primary File", "Relative Path"], tablefmt="github"))
            click.echo()

            if not clean:
                click.echo(f"Tip: Run 'video:duplicates --clean' to remove these {len(duplicates)} duplicate files and free {wasted_fmt}.")
                return

            if not yes:
                confirm = click.confirm(f"Are you sure you want to delete these {len(duplicates)} duplicate file(s) and free {wasted_fmt}?", default=False)
                if not confirm:
                    click.echo("Operation cancelled.")
                    return

            click.echo("\nCleaning duplicate files from disk and database...")
            removed_count = 0
            freed_bytes = 0
            for uid, d, _ in duplicates:
                ok, msg = self._optimizer_service.remove_duplicate_file(d, keep_backup=keep_backup, notify_photoprism=True)
                if ok:
                    click.secho(f"  ✓ {msg}", fg="green")
                    removed_count += 1
                    freed_bytes += d.file_size
                else:
                    click.secho(f"  ✗ {msg}", fg="red")

            freed_fmt = self._storage_analysis_service.format_bytes(freed_bytes)
            click.secho(f"\n✓ Cleaned {removed_count}/{len(duplicates)} duplicate files. Freed: {freed_fmt}!", fg="green")
        finally:
            session.close()
