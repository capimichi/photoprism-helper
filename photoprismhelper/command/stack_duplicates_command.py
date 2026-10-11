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
            help="Detection mode: 'cross' (cross-UID duplicates), 'stack' (intra-stack duplicates), or 'all' (default: cross).",
        )(fn)
        fn = click.option(
            "--keep",
            "-k",
            type=click.Choice(["1", "2"], case_sensitive=False),
            default=None,
            help="Non-interactive batch mode: keep '1' (Video 1) or '2' (Video 2) and delete the other automatically.",
        )(fn)
        fn = click.option(
            "--auto-prefer",
            type=click.Choice(["master", "proxy"], case_sensitive=False),
            default=None,
            help="Non-interactive batch mode for stack duplicates: 'master' (keep highest quality) or 'proxy' (keep lowest quality).",
        )(fn)
        fn = click.option("--limit", "-l", default=None, type=int, help="Limit number of duplicates to inspect (e.g. 20).")(fn)
        fn = click.option("--min-size-mb", default=10, type=int, help="Minimum file size in MB to qualify (default: 10).")(fn)
        fn = click.option("--clean", is_flag=True, default=False, help="Batch mode for stack duplicates (alias for --auto-prefer master).")(fn)
        fn = click.option("--preview/--no-preview", default=True, help="Launch ephemeral web page to compare videos side-by-side (default: True).")(fn)
        fn = click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt before batch cleaning.")(fn)
        fn = click.option("--keep-backup", is_flag=True, default=False, help="Rename to .dup.bak instead of deleting from disk.")(fn)
        fn = click.option("--notify/--no-notify", default=True, help="Notify PhotoPrism to re-index the folder (default: True).")(fn)
        return fn

    def run(
        self,
        mode: str = "cross",
        keep: str | None = None,
        auto_prefer: str | None = None,
        limit: int | None = None,
        min_size_mb: int = 10,
        clean: bool = False,
        preview: bool = True,
        yes: bool = False,
        keep_backup: bool = False,
        notify: bool = True,
    ) -> None:
        """Scan and manage duplicate videos (cross-media identical files or intra-stack duplicate files)."""
        mode = mode.lower()

        # Backward compatibility: if --clean is passed without --auto-prefer, default to 'master'
        if clean and not auto_prefer:
            auto_prefer = "master"

        # 1. Stack duplicates (intra-stack)
        if mode in ("stack", "all"):
            self._handle_stack_duplicates(
                auto_prefer=auto_prefer,
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
        auto_prefer: str | None = None,
        limit: int | None = None,
        clean: bool = False,
        preview: bool = True,
        yes: bool = False,
        keep_backup: bool = False,
        notify: bool = True,
    ) -> None:
        click.secho("\nRicerca duplicati intra-stack (versioni multiple, master vs proxy, copie .00001)...", fg="cyan")
        candidates = self._optimizer_service.find_stack_duplicates(limit=limit)
        if not candidates:
            click.echo("✓ Nessun duplicato intra-stack rilevato: tutti gli stack video sono puliti.")
            return

        total_wasted = sum(c.proxy_file.file_size for c in candidates)
        wasted_fmt = self._storage_analysis_service.format_bytes(total_wasted)
        limit_str = f" (limitati a {limit})" if limit else ""
        click.secho(
            f"\n### Rilevati Duplicati Video Intra-Stack{limit_str} ({len(candidates)} stack, {wasted_fmt} recuperabili)",
            fg="yellow",
            bold=True,
        )

        rows = []
        for idx, c in enumerate(candidates, 1):
            m_size = self._storage_analysis_service.format_bytes(c.master_file.file_size)
            p_size = self._storage_analysis_service.format_bytes(c.proxy_file.file_size)
            m_res = f"{c.master_file.width}x{c.master_file.height}" if c.master_file.width else "N/D"
            p_res = f"{c.proxy_file.width}x{c.proxy_file.height}" if c.proxy_file.width else "N/D"
            primary_label = "Master [1]" if c.master_file.is_primary else ("Proxy [2]" if c.proxy_file.is_primary else "N/D")

            rows.append([
                idx,
                c.media_uid,
                f"{c.master_file.file_name}\n({m_size}, {m_res})",
                f"{c.proxy_file.file_name}\n({p_size}, p: {p_res})",
                primary_label,
                c.duplicate_type,
                p_size,
            ])

        click.echo(
            tabulate(
                rows,
                headers=["#", "Media UID", "Master [1] (Migliore)", "Proxy/Copia [2]", "Primario PhotoPrism", "Tipo", "Risparmio"],
                tablefmt="github",
            )
        )
        click.echo()

        # Batch autonomous mode via --auto-prefer master / proxy (or --clean)
        if auto_prefer:
            auto_prefer_lower = auto_prefer.lower()
            prefer_label = "Master [1] (qualità migliore)" if auto_prefer_lower == "master" else "Proxy [2] (dimensione minore)"
            discard_label = "Proxy [2]" if auto_prefer_lower == "master" else "Master [1]"

            if not yes:
                confirm = click.confirm(
                    f"Rilevati {len(candidates)} stack duplicati ({wasted_fmt} recuperabili).\n"
                    f"Confermi l'elaborazione automatica in blocco mantenendo {prefer_label} ed eliminando {discard_label}?",
                    default=False,
                )
                if not confirm:
                    click.echo("Operazione annullata.")
                    return

            click.secho(f"\nPulizia batch in corso (--auto-prefer {auto_prefer_lower})...", fg="cyan", bold=True)
            success_count = 0
            freed_bytes = 0

            for idx, cand in enumerate(candidates, 1):
                ok, msg = self._optimizer_service.resolve_stack_duplicate(
                    cand,
                    keep=auto_prefer_lower,
                    keep_backup=keep_backup,
                    notify_photoprism=notify,
                )
                if ok:
                    discarded_sz = cand.proxy_file.file_size if auto_prefer_lower == "master" else cand.master_file.file_size
                    freed_bytes += discarded_sz
                    success_count += 1
                    click.secho(f"  [{idx}/{len(candidates)}] ✓ {msg}", fg="green")
                else:
                    click.secho(f"  [{idx}/{len(candidates)}] ✗ {msg}", fg="red")

            freed_fmt = self._storage_analysis_service.format_bytes(freed_bytes)
            click.secho(
                f"\n✓ Pulizia batch completata: {success_count}/{len(candidates)} stack risolti, liberati {freed_fmt}!",
                fg="green",
                bold=True,
            )
            return

        # Interactive Mode per stack
        click.secho("--- Avvio modalità interattiva per ciascuno stack duplicato ---", fg="cyan", bold=True)
        resolved_count = 0
        freed_bytes = 0

        for idx, cand in enumerate(candidates, 1):
            p_master = self._optimizer_service.resolve_disk_path(cand.master_file.file_path)
            p_proxy = self._optimizer_service.resolve_disk_path(cand.proxy_file.file_path)
            m_size = self._storage_analysis_service.format_bytes(cand.master_file.file_size)
            p_size = self._storage_analysis_service.format_bytes(cand.proxy_file.file_size)
            dur_str = f"{cand.master_file.duration:.1f}s" if cand.master_file.duration else "N/D"
            m_res = f"{cand.master_file.width}x{cand.master_file.height}" if cand.master_file.width else "N/D"
            p_res = f"{cand.proxy_file.width}x{cand.proxy_file.height}" if cand.proxy_file.width else "N/D"
            m_primary_tag = " [★ PRIMARIO PhotoPrism]" if cand.master_file.is_primary else " [Secondario]"
            p_primary_tag = " [★ PRIMARIO PhotoPrism]" if cand.proxy_file.is_primary else " [Secondario]"

            click.secho(
                f"\n[{idx}/{len(candidates)}] Stack UID: {cand.media_uid} | Titolo: {cand.photo_title or 'N/D'} | Durata: {dur_str} | Tipo: {cand.duplicate_type}",
                fg="cyan",
                bold=True,
            )
            click.echo(f"  • [1] Master: {cand.master_file.file_name} ({m_size}, {m_res}, {cand.master_file.codec or 'video'}){m_primary_tag}")
            click.echo(f"  • [2] Proxy:  {cand.proxy_file.file_name} ({p_size}, {p_res}, {cand.proxy_file.codec or 'video'}){p_primary_tag}")

            if preview and self._preview_server and os.path.isfile(p_master) and os.path.isfile(p_proxy):
                preview_url = self._preview_server.start(
                    file1_path=p_master,
                    file2_path=p_proxy,
                    title=f"Stack: {cand.media_uid}",
                    subtitle=f"Master: {cand.master_file.file_name} vs Proxy: {cand.proxy_file.file_name}",
                    label1=f"[1] Master ({m_res})",
                    label2=f"[2] Proxy ({p_res})",
                    info1=f"Dim: {m_size} | {m_primary_tag.strip()}",
                    info2=f"Dim: {p_size} | {p_primary_tag.strip()}",
                    badge1=f"Master: {m_size}",
                    badge2=f"Proxy: {p_size}",
                    badge3=f"Durata: {dur_str}",
                )
                click.secho(f"  📺 Anteprima Web attiva: 👉 {preview_url}", fg="green", bold=True)

            try:
                choice = click.prompt(
                    "  Azione [1=Tieni Master, 2=Tieni Proxy, o=Ottimizza Master, u=Separa (unstack), s=Salta, q=Esci]",
                    type=click.Choice(["1", "2", "o", "u", "s", "q"], case_sensitive=False),
                    default="1",
                    show_choices=False,
                ).lower()

                if choice == "q":
                    click.echo("Uscita dal comando.")
                    break
                elif choice == "s":
                    click.echo("  -> Stack saltato.")
                    continue
                elif choice == "1":
                    ok, msg = self._optimizer_service.resolve_stack_duplicate(
                        cand, keep="master", keep_backup=keep_backup, notify_photoprism=notify
                    )
                    if ok:
                        click.secho(f"  ✓ {msg}", fg="green")
                        resolved_count += 1
                        freed_bytes += cand.proxy_file.file_size
                    else:
                        click.secho(f"  ✗ {msg}", fg="red")
                elif choice == "2":
                    ok, msg = self._optimizer_service.resolve_stack_duplicate(
                        cand, keep="proxy", keep_backup=keep_backup, notify_photoprism=notify
                    )
                    if ok:
                        click.secho(f"  ✓ {msg}", fg="green")
                        resolved_count += 1
                        freed_bytes += cand.master_file.file_size
                    else:
                        click.secho(f"  ✗ {msg}", fg="red")
                elif choice == "o":
                    click.echo("  Avvio ottimizzazione del Master a 1080p...")
                    ok, msg = self._optimizer_service.optimize_and_resolve_stack(
                        cand, keep_backup=keep_backup, notify_photoprism=notify
                    )
                    if ok:
                        click.secho(f"  ✓ {msg}", fg="green")
                        resolved_count += 1
                        freed_bytes += cand.proxy_file.file_size
                    else:
                        click.secho(f"  ✗ {msg}", fg="red")
                elif choice == "u":
                    click.echo("  Separazione dello stack in PhotoPrism via API...")
                    ok, msg = self._optimizer_service.unstack_duplicate(cand, notify_photoprism=notify)
                    if ok:
                        click.secho(f"  ✓ {msg}", fg="green")
                        resolved_count += 1
                    else:
                        click.secho(f"  ✗ {msg}", fg="red")
            finally:
                if preview and self._preview_server:
                    self._preview_server.stop()

        freed_fmt = self._storage_analysis_service.format_bytes(freed_bytes)
        click.secho(
            f"\n✓ Sessione completata: {resolved_count}/{len(candidates)} stack gestiti, liberati {freed_fmt}!",
            fg="green",
            bold=True,
        )
