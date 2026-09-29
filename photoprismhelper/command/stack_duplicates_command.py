from __future__ import annotations

import os
import click
from injector import inject
from tabulate import tabulate

from photoprismhelper.client.photoprism_client import PhotoprismClient
from photoprismhelper.command.abstract_command import AbstractCommand
from photoprismhelper.entity.media_file import MediaFile
from photoprismhelper.repository.media_file_repository import MediaFileRepository
from photoprismhelper.repository.media_repository import MediaRepository
from photoprismhelper.service.preview_server import VideoPreviewServer
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
        preview_server: VideoPreviewServer | None = None,
        photoprism_client: PhotoprismClient | None = None,
    ) -> None:
        self._file_repository = file_repository
        self._media_repository = media_repository
        self._optimizer_service = optimizer_service
        self._storage_analysis_service = storage_analysis_service
        self._preview_server = preview_server
        self._photoprism_client = photoprism_client

    def register_options(self, fn):
        fn = click.option("--clean", is_flag=True, default=False, help="Delete detected duplicate files from disk and PhotoPrism.")(fn)
        fn = click.option("--preview", is_flag=True, default=False, help="Launch ephemeral web page to compare primary vs duplicate videos side-by-side.")(fn)
        fn = click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt before cleaning.")(fn)
        fn = click.option("--keep-backup", is_flag=True, default=False, help="Rename to .bak instead of deleting from disk.")(fn)
        return fn

    def run(
        self,
        clean: bool = False,
        preview: bool = False,
        yes: bool = False,
        keep_backup: bool = False,
    ) -> None:
        """Scan and manage stacked duplicate video files (.00001.mov, etc.)."""
        session = self._file_repository.get_session()
        try:
            stacked_uids = self._file_repository.find_stacked_video_media_uids(session)
            if not stacked_uids:
                click.echo("No stacked video media items found in the database.")
                click.echo("Tip: Run 'media:sync' to index all stack files first.")
                return

            duplicates: list[tuple[str, MediaFile, str, str]] = []
            total_wasted_bytes = 0

            for uid in stacked_uids:
                media_item = self._media_repository.get_by_uid(session, uid)
                primary_name = media_item.file_name if media_item else "unknown"
                primary_path = media_item.file_path if media_item else ""
                dups = self._optimizer_service.get_stack_duplicates(uid, primary_file_name=primary_name)
                for d in dups:
                    duplicates.append((uid, d, primary_name, primary_path))
                    total_wasted_bytes += d.file_size

            if not duplicates:
                click.echo("✓ All stacked videos are clean: no duplicate video files detected.")
                return

            rows = []
            pp_base = self._photoprism_client.base_url if self._photoprism_client else ""
            for uid, d, primary_name, _ in duplicates:
                size_str = self._storage_analysis_service.format_bytes(d.file_size)
                pp_link = f"{pp_base}/library/photos/{uid}" if pp_base else uid
                rows.append([
                    uid,
                    d.file_name,
                    size_str,
                    primary_name,
                    d.file_path,
                ])

            wasted_fmt = self._storage_analysis_service.format_bytes(total_wasted_bytes)
            click.echo(f"\n### Detected Video Stack Duplicates ({len(duplicates)} files, {wasted_fmt} wasted)")
            click.echo(tabulate(rows, headers=["Media UID", "Duplicate File", "Size", "Primary File", "Relative Path"], tablefmt="github"))
            click.echo()

            if pp_base:
                click.echo("PhotoPrism links for inspection:")
                for uid, d, primary_name, _ in duplicates:
                    click.echo(f"  • {d.file_name}: {pp_base}/library/photos/{uid}")
                click.echo()

            # Preview server support
            if preview and self._preview_server and duplicates:
                first_uid, first_dup, first_primary, first_prim_path = duplicates[0]
                prim_disk = self._optimizer_service.resolve_disk_path(first_prim_path)
                dup_disk = self._optimizer_service.resolve_disk_path(first_dup.file_path)

                if os.path.isfile(prim_disk) and os.path.isfile(dup_disk):
                    prim_size = self._storage_analysis_service.format_bytes(os.path.getsize(prim_disk))
                    dup_size = self._storage_analysis_service.format_bytes(first_dup.file_size)

                    preview_url = self._preview_server.start(
                        file1_path=prim_disk,
                        file2_path=dup_disk,
                        title=f"{first_primary} vs {first_dup.file_name}",
                        subtitle=f"UID: {first_uid} | Duplicato da eliminare: {dup_size}",
                        label1=f"Primario ({first_primary})",
                        label2=f"Duplicato ({first_dup.file_name})",
                        info1=f"Dimensione: {prim_size}",
                        info2=f"Dimensione: {dup_size}",
                        badge1=f"Primario: {prim_size}",
                        badge2=f"Duplicato: {dup_size}",
                    )
                    click.secho("  📺 Anteprima Web attiva per il confronto:", fg="cyan", bold=True)
                    click.secho(f"     👉 {preview_url}", fg="cyan", underline=True)
                    click.echo("     (I video sono sincronizzati nello scrub. Aprilo nel browser per visualizzarli)\n")

            if not clean:
                click.echo(f"Tip: Run 'video:duplicates --clean' to remove these {len(duplicates)} duplicate files and free {wasted_fmt}.")
                if preview and self._preview_server:
                    click.prompt("Premi Invio per chiudere l'anteprima web", default="", show_default=False)
                    self._preview_server.stop()
                return

            try:
                if not yes:
                    confirm = click.confirm(f"Are you sure you want to delete these {len(duplicates)} duplicate file(s) and free {wasted_fmt}?", default=False)
                    if not confirm:
                        click.echo("Operation cancelled.")
                        return

                click.echo("\nCleaning duplicate files from disk and database...")
                removed_count = 0
                freed_bytes = 0
                for uid, d, _, _ in duplicates:
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
                if preview and self._preview_server:
                    self._preview_server.stop()
        finally:
            session.close()
