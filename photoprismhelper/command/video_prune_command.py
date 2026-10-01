from __future__ import annotations

import os
import click
from injector import inject
from tabulate import tabulate

from photoprismhelper.client.photoprism_client import PhotoprismClient
from photoprismhelper.command.abstract_command import AbstractCommand
from photoprismhelper.entity.media_item import MediaItem
from photoprismhelper.repository.media_repository import MediaRepository
from photoprismhelper.service.preview_server import VideoPreviewServer
from photoprismhelper.service.storage_analysis_service import StorageAnalysisService
from photoprismhelper.service.video_optimizer_service import VideoOptimizerService


class VideoPruneCommand(AbstractCommand):
    command_name = "video:prune"

    @inject
    def __init__(
        self,
        media_repository: MediaRepository,
        optimizer_service: VideoOptimizerService,
        storage_analysis_service: StorageAnalysisService,
        preview_server: VideoPreviewServer | None = None,
        photoprism_client: PhotoprismClient | None = None,
    ) -> None:
        self._media_repository = media_repository
        self._optimizer_service = optimizer_service
        self._storage_analysis_service = storage_analysis_service
        self._preview_server = preview_server
        self._photoprism_client = photoprism_client

    def register_options(self, fn):
        fn = click.option("--uid", "-u", default=None, type=str, help="Target a specific media item by its UID.")(fn)
        fn = click.option("--limit", "-l", default=10, type=int, help="Number of heaviest videos to inspect (default: 10).")(fn)
        fn = click.option("--min-size-mb", default=50, type=int, help="Minimum file size in MB to qualify (default: 50).")(fn)
        fn = click.option("--preview/--no-preview", default=True, help="Launch ephemeral web player for video preview before confirmation (default: True).")(fn)
        fn = click.option("--keep-backup", is_flag=True, default=False, help="Rename to .bak instead of permanently deleting.")(fn)
        fn = click.option("--notify/--no-notify", default=True, help="Notify PhotoPrism to prune index (default: True).")(fn)
        return fn

    def run(
        self,
        uid: str | None = None,
        limit: int = 10,
        min_size_mb: int = 50,
        preview: bool = True,
        keep_backup: bool = False,
        notify: bool = True,
    ) -> None:
        """Interactively inspect and delete heavy unwanted videos ordered by size or by specific UID."""
        session = self._media_repository.get_session()
        try:
            if uid:
                item = self._media_repository.get_by_uid(session, uid)
                if not item:
                    click.secho(f"Media item with UID '{uid}' not found.", fg="red")
                    return
                candidates: list[MediaItem] = [item]
            else:
                min_size_bytes = min_size_mb * 1024 * 1024
                candidates = self._media_repository.find_video_candidates_for_optimization(
                    session, min_size_bytes=min_size_bytes, limit=limit
                )
        finally:
            session.close()

        if not candidates:
            if uid:
                click.secho(f"No media item found with UID '{uid}'.", fg="red")
            else:
                click.echo(f"No videos found larger than {min_size_mb} MB.")
            return

        total_bytes = sum(c.file_size for c in candidates)
        total_fmt = self._storage_analysis_service.format_bytes(total_bytes)
        click.secho(
            f"\n### Heavy Videos Inspection ({len(candidates)} videos, {total_fmt} total)",
            fg="yellow",
            bold=True,
        )

        rows = []
        pp_base = self._photoprism_client.base_url if self._photoprism_client else ""
        for idx, item in enumerate(candidates, 1):
            size_fmt = self._storage_analysis_service.format_bytes(item.file_size)
            dim_str = f"{item.width}x{item.height}" if item.width and item.height else "unknown"
            dur_str = f"{item.duration}s" if item.duration else "unknown"
            rows.append([
                idx,
                item.file_name,
                size_fmt,
                dim_str,
                dur_str,
                item.codec or "unknown",
                item.file_path,
            ])

        click.echo(
            tabulate(
                rows,
                headers=["#", "Filename", "Size", "Resolution", "Duration", "Codec", "Path"],
                tablefmt="github",
            )
        )
        click.echo()

        deleted_count = 0
        freed_bytes = 0

        for idx, item in enumerate(candidates, 1):
            disk_path = self._optimizer_service.resolve_disk_path(item.file_path)
            size_fmt = self._storage_analysis_service.format_bytes(item.file_size)
            pp_link = f"{pp_base}/library/photos/{item.uid}" if pp_base else ""

            click.secho(f"\n[{idx}/{len(candidates)}] Inspecting: {item.file_name} ({size_fmt})", fg="cyan", bold=True)
            click.echo(f"  • Percorso: {disk_path}")
            if pp_link:
                click.echo(f"  • PhotoPrism: {pp_link}")

            if not os.path.exists(disk_path):
                click.secho("  ✗ File non trovato su disco. Salto.", fg="yellow")
                continue

            preview_url = ""
            if preview and self._preview_server:
                dur_fmt = f"{item.duration:.0f}s" if item.duration else ""
                dim_fmt = f"{item.width}x{item.height}" if item.width and item.height else ""
                sub = f"UID: {item.uid}"
                if dim_fmt or dur_fmt:
                    sub += f" | {dim_fmt} | {dur_fmt}"

                preview_url = self._preview_server.start(
                    file1_path=disk_path,
                    file2_path="",
                    title=f"Eliminazione Video: {item.file_name}",
                    subtitle=sub,
                    label1=item.file_name,
                    info1=f"Dimensione: {size_fmt}",
                    badge1=size_fmt,
                    badge2=f"Codec: {item.codec or 'sconosciuto'}",
                )
                click.secho(f"  📺 Anteprima Web attiva: 👉 {preview_url}", fg="green", bold=True)

            try:
                prompt_text = f"  Eliminare definitivamente '{item.file_name}' ({size_fmt})? [y=elimina, s=salta, q=esci]"
                choice = click.prompt(
                    prompt_text,
                    type=click.Choice(["y", "s", "q", "n"], case_sensitive=False),
                    default="s",
                    show_choices=False,
                ).lower()

                if choice == "q":
                    click.echo("Uscita dal comando.")
                    break
                elif choice in ("s", "n"):
                    click.echo("  -> Saltato.")
                    continue
                elif choice == "y":
                    click.echo(f"  Eliminazione di {item.file_name}...")
                    ok, msg = self._optimizer_service.delete_media_item(
                        item, keep_backup=keep_backup, notify_photoprism=notify
                    )
                    if ok:
                        click.secho(f"  ✓ {msg}", fg="green")
                        deleted_count += 1
                        freed_bytes += item.file_size
                    else:
                        click.secho(f"  ✗ Errore: {msg}", fg="red")
            finally:
                if preview and self._preview_server:
                    self._preview_server.stop()

        freed_fmt = self._storage_analysis_service.format_bytes(freed_bytes)
        click.secho(
            f"\n✓ Sessione completata: {deleted_count} video eliminati, liberati {freed_fmt}!",
            fg="green",
            bold=True,
        )
