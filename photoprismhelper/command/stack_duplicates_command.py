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
        fn = click.option(
            "--mode",
            "-m",
            type=click.Choice(["cross", "stack", "all"], case_sensitive=False),
            default="cross",
            help="Detection mode: 'cross' (cross-UID duplicates), 'stack' (intra-stack .00001 files), or 'all' (default: cross).",
        )(fn)
        fn = click.option(
            "--keep",
            "-k",
            type=click.Choice(["1", "2"], case_sensitive=False),
            default=None,
            help="Non-interactive batch mode: keep '1' (Video 1) or '2' (Video 2) and delete the other automatically.",
        )(fn)
        fn = click.option("--limit", "-l", default=None, type=int, help="Limit number of duplicates to inspect (e.g. 20).")(fn)
        fn = click.option("--min-size-mb", default=10, type=int, help="Minimum file size in MB to qualify (default: 10).")(fn)
        fn = click.option("--clean", is_flag=True, default=False, help="Delete detected stack duplicate files without prompting.")(fn)
        fn = click.option("--preview/--no-preview", default=True, help="Launch ephemeral web page to compare videos side-by-side (default: True).")(fn)
        fn = click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt before batch cleaning.")(fn)
        fn = click.option("--keep-backup", is_flag=True, default=False, help="Rename to .dup.bak instead of deleting from disk.")(fn)
        fn = click.option("--notify/--no-notify", default=True, help="Notify PhotoPrism to re-index the folder (default: True).")(fn)
        return fn

    def run(
        self,
        mode: str = "cross",
        keep: str | None = None,
        limit: int | None = None,
        min_size_mb: int = 10,
        clean: bool = False,
        preview: bool = True,
        yes: bool = False,
        keep_backup: bool = False,
        notify: bool = True,
    ) -> None:
        """Scan and manage duplicate videos (cross-media identical files or intra-stack .00001 files)."""
        mode = mode.lower()

        # 1. Stack duplicates (intra-stack)
        if mode in ("stack", "all"):
            self._handle_stack_duplicates(
                limit=limit,
                clean=clean,
                preview=preview,
                yes=yes,
                keep_backup=keep_backup,
                notify=notify,
            )

        # 2. Cross-media duplicates (separate PhotoPrism items)
        if mode in ("cross", "all"):
            self._handle_cross_duplicates(
                keep=keep,
                limit=limit,
                min_size_mb=min_size_mb,
                preview=preview,
                yes=yes,
                keep_backup=keep_backup,
                notify=notify,
            )

    def _handle_cross_duplicates(
        self,
        keep: str | None = None,
        limit: int | None = None,
        min_size_mb: int = 10,
        preview: bool = True,
        yes: bool = False,
        keep_backup: bool = False,
        notify: bool = True,
    ) -> None:
        click.secho(f"\nScanning library for cross-media duplicate videos (min size: {min_size_mb} MB)...", fg="cyan")
        pairs = self._optimizer_service.find_cross_media_duplicates(min_size_mb=min_size_mb, limit=limit)
        if not pairs:
            click.echo(f"✓ No cross-media duplicate videos found exceeding {min_size_mb} MB.")
            return

        total_wasted = sum(min(a.file_size, b.file_size) for a, b in pairs)
        wasted_fmt = self._storage_analysis_service.format_bytes(total_wasted)
        limit_str = f" (limited to {limit})" if limit else ""
        click.secho(
            f"\n### Detected Cross-Media Video Duplicates{limit_str} ({len(pairs)} pairs, {wasted_fmt} wasted)",
            fg="yellow",
            bold=True,
        )

        rows = []
        for idx, (a, b) in enumerate(pairs, 1):
            dur_fmt = f"{a.duration:.0f}s" if a.duration else "unknown"
            size_fmt = self._storage_analysis_service.format_bytes(a.file_size)
            rows.append([
                idx,
                f"{a.file_name}\n({a.uid})",
                f"{b.file_name}\n({b.uid})",
                size_fmt,
                dur_fmt,
                f"{a.taken_at}\nvs {b.taken_at}",
            ])

        click.echo(
            tabulate(
                rows,
                headers=["#", "Video 1 (Primary)", "Video 2 (Duplicate)", "Size", "Duration", "Dates"],
                tablefmt="github",
            )
        )
        click.echo()

        # Batch non-interactive mode via --keep 1 or --keep 2
        if keep:
            keep_label = "Video 1" if keep == "1" else "Video 2"
            delete_label = "Video 2" if keep == "1" else "Video 1"

            if not yes:
                confirm = click.confirm(
                    f"Trovate {len(pairs)} coppie duplicate ({wasted_fmt} da liberare).\n"
                    f"Confermi l'eliminazione automatica di {delete_label} mantenendo sempre {keep_label} per tutte le coppie?",
                    default=False,
                )
                if not confirm:
                    click.echo("Operazione annullata.")
                    return

            click.secho(f"\nEliminazione automatica in blocco ({delete_label} -> eliminato, {keep_label} -> mantenuto)...", fg="cyan", bold=True)
            deleted_count = 0
            freed_bytes = 0

            for idx, (a, b) in enumerate(pairs, 1):
                to_delete = b if keep == "1" else a
                to_keep = a if keep == "1" else b
                p_del = self._optimizer_service.resolve_disk_path(to_delete.file_path)
                size_del = self._storage_analysis_service.format_bytes(to_delete.file_size)

                if not os.path.exists(p_del):
                    continue

                ok, msg = self._optimizer_service.delete_media_item(
                    to_delete, keep_backup=keep_backup, notify_photoprism=notify
                )
                if ok:
                    click.secho(f"  [{idx}/{len(pairs)}] ✓ Eliminato: {to_delete.file_name} ({size_del}) | Mantenuto: {to_keep.file_name}", fg="green")
                    deleted_count += 1
                    freed_bytes += to_delete.file_size
                else:
                    click.secho(f"  [{idx}/{len(pairs)}] ✗ {msg}", fg="red")

            freed_fmt = self._storage_analysis_service.format_bytes(freed_bytes)
            click.secho(
                f"\n✓ Pulizia automatica completata: {deleted_count} video duplicati eliminati, liberati {freed_fmt}!",
                fg="green",
                bold=True,
            )
            return

        # Interactive mode with web preview
        deleted_count = 0
        freed_bytes = 0

        for idx, (a, b) in enumerate(pairs, 1):
            p1 = self._optimizer_service.resolve_disk_path(a.file_path)
            p2 = self._optimizer_service.resolve_disk_path(b.file_path)
            size1_fmt = self._storage_analysis_service.format_bytes(a.file_size)
            size2_fmt = self._storage_analysis_service.format_bytes(b.file_size)
            dur_fmt = f"{a.duration:.0f}s" if a.duration else ""

            click.secho(f"\n[{idx}/{len(pairs)}] Confronto duplicati ({size1_fmt}):", fg="cyan", bold=True)
            click.echo(f"  • [1] {a.file_name} (UID: {a.uid}, Data: {a.taken_at})")
            click.echo(f"  • [2] {b.file_name} (UID: {b.uid}, Data: {b.taken_at})")

            if not os.path.exists(p1) or not os.path.exists(p2):
                click.secho("  ✗ Uno dei due file non esiste più su disco. Salto.", fg="yellow")
                continue

            if preview and self._preview_server:
                preview_url = self._preview_server.start(
                    file1_path=p1,
                    file2_path=p2,
                    title=f"Duplicato: {a.file_name} vs {b.file_name}",
                    subtitle=f"UID 1: {a.uid} | UID 2: {b.uid} | Durata: {dur_fmt}",
                    label1=f"[1] {a.file_name}",
                    label2=f"[2] {b.file_name}",
                    info1=f"Data: {a.taken_at} | Dim: {size1_fmt}",
                    info2=f"Data: {b.taken_at} | Dim: {size2_fmt}",
                    badge1=f"1: {size1_fmt}",
                    badge2=f"2: {size2_fmt}",
                    badge3=f"Durata: {dur_fmt}",
                )
                click.secho(f"  📺 Anteprima Web attiva: 👉 {preview_url}", fg="green", bold=True)

            try:
                choice = click.prompt(
                    "  Quale video vuoi eliminare? [1=elimina 1, 2=elimina 2, s=salta, q=esci]",
                    type=click.Choice(["1", "2", "s", "q"], case_sensitive=False),
                    default="s",
                    show_choices=False,
                ).lower()

                if choice == "q":
                    click.echo("Uscita dal comando.")
                    break
                elif choice == "s":
                    click.echo("  -> Coppia saltata.")
                    continue
                elif choice == "1":
                    click.echo(f"  Eliminazione di [1] {a.file_name}...")
                    ok, msg = self._optimizer_service.delete_media_item(
                        a, keep_backup=keep_backup, notify_photoprism=notify
                    )
                    if ok:
                        click.secho(f"  ✓ {msg} (mantenuto {b.file_name})", fg="green")
                        deleted_count += 1
                        freed_bytes += a.file_size
                    else:
                        click.secho(f"  ✗ {msg}", fg="red")
                elif choice == "2":
                    click.echo(f"  Eliminazione di [2] {b.file_name}...")
                    ok, msg = self._optimizer_service.delete_media_item(
                        b, keep_backup=keep_backup, notify_photoprism=notify
                    )
                    if ok:
                        click.secho(f"  ✓ {msg} (mantenuto {a.file_name})", fg="green")
                        deleted_count += 1
                        freed_bytes += b.file_size
                    else:
                        click.secho(f"  ✗ {msg}", fg="red")
            finally:
                if preview and self._preview_server:
                    self._preview_server.stop()

        freed_fmt = self._storage_analysis_service.format_bytes(freed_bytes)
        click.secho(
            f"\n✓ Sessione duplicati cross-media completata: {deleted_count} video eliminati, liberati {freed_fmt}!",
            fg="green",
            bold=True,
        )

    def _handle_stack_duplicates(
        self,
        limit: int | None = None,
        clean: bool = False,
        preview: bool = True,
        yes: bool = False,
        keep_backup: bool = False,
        notify: bool = True,
    ) -> None:
        session = self._file_repository.get_session()
        try:
            stacked_uids = self._file_repository.find_stacked_video_media_uids(session)
            if not stacked_uids:
                click.echo("No stacked video media items found in the database.")
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
                click.echo("✓ All stacked videos are clean: no stack duplicate files detected.")
                return

            if limit and limit > 0:
                duplicates = duplicates[:limit]
                total_wasted_bytes = sum(d.file_size for _, d, _, _ in duplicates)

            rows = []
            pp_base = self._photoprism_client.base_url if self._photoprism_client else ""
            for uid, d, primary_name, _ in duplicates:
                size_str = self._storage_analysis_service.format_bytes(d.file_size)
                rows.append([
                    uid,
                    d.file_name,
                    size_str,
                    primary_name,
                    d.file_path,
                ])

            wasted_fmt = self._storage_analysis_service.format_bytes(total_wasted_bytes)
            limit_str = f" (limited to {limit})" if limit else ""
            click.echo(f"\n### Detected Video Stack Duplicates{limit_str} ({len(duplicates)} files, {wasted_fmt} wasted)")
            click.echo(tabulate(rows, headers=["Media UID", "Duplicate File", "Size", "Primary File", "Relative Path"], tablefmt="github"))
            click.echo()

            # Preview server support for stack duplicates
            if preview and self._preview_server and duplicates:
                first_uid, first_dup, first_primary, first_prim_path = duplicates[0]
                stack_videos = self._optimizer_service.get_stack_video_files(first_uid)
                prim_file = next((f for f in stack_videos if f.file_uid != first_dup.file_uid), None)

                prim_disk = self._optimizer_service.resolve_disk_path(prim_file.file_path) if prim_file else self._optimizer_service.resolve_disk_path(first_prim_path)
                dup_disk = self._optimizer_service.resolve_disk_path(first_dup.file_path)

                if os.path.isfile(prim_disk) and os.path.isfile(dup_disk):
                    prim_size = self._storage_analysis_service.format_bytes(os.path.getsize(prim_disk))
                    dup_size = self._storage_analysis_service.format_bytes(first_dup.file_size)
                    prim_name = prim_file.file_name if prim_file else first_primary

                    preview_url = self._preview_server.start(
                        file1_path=prim_disk,
                        file2_path=dup_disk,
                        title=f"{prim_name} vs {first_dup.file_name}",
                        subtitle=f"UID: {first_uid} | Duplicato da eliminare: {dup_size}",
                        label1=f"Primario ({prim_name})",
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
                    ok, msg = self._optimizer_service.remove_duplicate_file(d, keep_backup=keep_backup, notify_photoprism=notify)
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
