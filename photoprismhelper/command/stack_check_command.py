from __future__ import annotations

import json
import logging
import os
import click
from injector import inject
from tabulate import tabulate

from photoprismhelper.client.photoprism_client import PhotoprismClient
from photoprismhelper.command.abstract_command import AbstractCommand
from photoprismhelper.service.preview_server import VideoPreviewServer
from photoprismhelper.service.stack_analysis_service import (
    InvalidVideoStack,
    StackAnalysisService,
)
from photoprismhelper.service.storage_analysis_service import StorageAnalysisService

logger = logging.getLogger(__name__)


class VideoStackCheckCommand(AbstractCommand):
    command_name = "video:stack-check"

    @inject
    def __init__(
        self,
        stack_service: StackAnalysisService,
        storage_analysis_service: StorageAnalysisService,
        preview_server: VideoPreviewServer | None = None,
        photoprism_client: PhotoprismClient | None = None,
    ) -> None:
        self._stack_service = stack_service
        self._storage_analysis_service = storage_analysis_service
        self._preview_server = preview_server
        self._photoprism_client = photoprism_client

    def register_options(self, fn):
        fn = click.option(
            "--min-diff-sec",
            default=2.0,
            type=float,
            help="Soglia minima di differenza durata in secondi per considerare lo stack errato (default: 2.0s).",
        )(fn)
        fn = click.option(
            "--limit",
            "-l",
            default=None,
            type=int,
            help="Numero massimo di stack da visualizzare o processare (es. 20).",
        )(fn)
        fn = click.option(
            "--unstack",
            is_flag=True,
            default=False,
            help="Esegui unstack automatico via PhotoPrism API per tutti i video identificati.",
        )(fn)
        fn = click.option(
            "--interactive",
            "-i",
            is_flag=True,
            default=False,
            help="Modalità interattiva: controlla ogni stack uno ad uno con possibilità di unstack.",
        )(fn)
        fn = click.option(
            "--preview/--no-preview",
            default=True,
            help="In modalità interattiva, avvia anteprima web side-by-side per confrontare i video (default: True).",
        )(fn)
        fn = click.option(
            "--yes",
            "-y",
            is_flag=True,
            default=False,
            help="Salta la conferma prima di eseguire l'unstack automatico in batch.",
        )(fn)
        fn = click.option(
            "--json",
            "output_json",
            is_flag=True,
            default=False,
            help="Output in formato JSON.",
        )(fn)
        return fn

    def run(
        self,
        min_diff_sec: float = 2.0,
        limit: int | None = None,
        unstack: bool = False,
        interactive: bool = False,
        preview: bool = True,
        yes: bool = False,
        output_json: bool = False,
    ) -> None:
        """Individua e separa stack di video errati con durate differenti raggruppati da PhotoPrism."""
        limit_str = f" (limit={limit})" if limit else ""
        if not output_json:
            click.secho(
                f"\n🔍 Ricerca stack di video errati (durata diff >= {min_diff_sec}s){limit_str}...",
                fg="cyan",
            )

        stacks = self._stack_service.find_invalid_video_stacks(
            min_diff_sec=min_diff_sec, limit=limit
        )

        if not stacks:
            if output_json:
                click.echo(json.dumps([]))
            else:
                click.secho(
                    f"✓ Nessuno stack di video errato rilevato con differenza >= {min_diff_sec}s.",
                    fg="green",
                )
            return

        total_invalid_files = sum(len(s.invalid_files) for s in stacks)

        if output_json:
            self._output_json(stacks)
            return

        # 1. Interactive Mode
        if interactive:
            self._handle_interactive(stacks, preview=preview)
            return

        # 2. Batch Unstack Mode
        if unstack:
            self._handle_batch_unstack(stacks, total_invalid_files, yes=yes)
            return

        # 3. Default: Report Table Mode
        self._handle_report(stacks, min_diff_sec=min_diff_sec, limit=limit)

    def _handle_report(
        self,
        stacks: list[InvalidVideoStack],
        min_diff_sec: float,
        limit: int | None = None,
    ) -> None:
        total_invalid_files = sum(len(s.invalid_files) for s in stacks)
        limit_str = f" [limit={limit}]" if limit else ""

        click.secho(
            f"\n### Trovati {len(stacks)} stack errati ({total_invalid_files} video secondari da separare){limit_str}:\n",
            fg="yellow",
            bold=True,
        )

        rows = []
        for idx, s in enumerate(stacks, 1):
            date_str = s.taken_at.strftime("%Y-%m-%d %H:%M") if s.taken_at else "sconosciuta"
            p = s.primary_file
            p_size = self._storage_analysis_service.format_bytes(p.file_size)
            p_dur = f"{p.duration:.1f}s" if p.duration else "?"
            prim_desc = f"{p.file_name}\n({p_dur}, {p_size})"

            for inv in s.invalid_files:
                inv_size = self._storage_analysis_service.format_bytes(inv.file_size)
                inv_dur = f"{inv.duration:.1f}s" if inv.duration else "?"
                diff_val = abs((inv.duration or 0.0) - (p.duration or 0.0))
                sec_desc = f"{inv.file_name}\n({inv_dur}, {inv_size})"

                rows.append([
                    idx,
                    s.media_uid,
                    date_str,
                    prim_desc,
                    sec_desc,
                    f"{diff_val:.1f}s",
                    inv.file_uid,
                ])

        click.echo(
            tabulate(
                rows,
                headers=[
                    "#",
                    "Media UID",
                    "Data",
                    "Video Primario",
                    "Video Secondario (separabile)",
                    "Diff Durata",
                    "File UID",
                ],
                tablefmt="github",
            )
        )
        click.echo()
        click.secho("Azioni consigliate:", fg="cyan", bold=True)
        click.echo(
            "  • Per controllare interattivamente con preview side-by-side: "
            + click.style("photoprismhelper video:stack-check -i", fg="yellow")
        )
        click.echo(
            "  • Per separare (unstack) tutti i video automaticamente via API: "
            + click.style("photoprismhelper video:stack-check --unstack", fg="green")
        )
        click.echo()

    def _handle_interactive(
        self, stacks: list[InvalidVideoStack], preview: bool = True
    ) -> None:
        pp_base = self._photoprism_client.base_url if self._photoprism_client else ""
        unstacked_count = 0

        for s_idx, s in enumerate(stacks, 1):
            p = s.primary_file
            date_str = s.taken_at.strftime("%Y-%m-%d %H:%M") if s.taken_at else "sconosciuta"
            p_size = self._storage_analysis_service.format_bytes(p.file_size)
            p_dur = f"{p.duration:.1f}s" if p.duration else "?"

            click.secho(
                f"\n[{s_idx}/{len(stacks)}] Stack UID: {s.media_uid} ({date_str})",
                fg="cyan",
                bold=True,
            )
            if pp_base:
                click.echo(f"  🔗 PhotoPrism: {pp_base}/library/browse?q=uid:{s.media_uid}")
            click.echo(f"  • Primario: {p.file_name} ({p_dur}, {p_size})")

            for inv in s.invalid_files:
                inv_size = self._storage_analysis_service.format_bytes(inv.file_size)
                inv_dur = f"{inv.duration:.1f}s" if inv.duration else "?"
                diff_val = abs((inv.duration or 0.0) - (p.duration or 0.0))

                click.echo(
                    f"  • Secondario: {inv.file_name} ({inv_dur}, {inv_size}) | Diff: {diff_val:.1f}s"
                )

                if preview and self._preview_server:
                    p1_disk = self._stack_service.resolve_disk_path(p.file_path)
                    p2_disk = self._stack_service.resolve_disk_path(inv.file_path)

                    if os.path.exists(p1_disk) and os.path.exists(p2_disk):
                        preview_url = self._preview_server.start(
                            file1_path=p1_disk,
                            file2_path=p2_disk,
                            title=f"Stack Check: {p.file_name} vs {inv.file_name}",
                            subtitle=f"UID: {s.media_uid} | Primario: {p_dur} vs Secondario: {inv_dur}",
                            label1=f"[Primario] {p.file_name}",
                            label2=f"[Secondario] {inv.file_name}",
                            info1=f"Durata: {p_dur} | Dim: {p_size}",
                            info2=f"Durata: {inv_dur} | Dim: {inv_size}",
                            badge1=f"Prim: {p_dur}",
                            badge2=f"Sec: {inv_dur}",
                            badge3=f"Diff: {diff_val:.1f}s",
                        )
                        click.secho(f"  📺 Anteprima Web: 👉 {preview_url}", fg="green", bold=True)

                try:
                    choice = click.prompt(
                        "  Azione [u=unstack via API, s=salta, q=esci]",
                        type=click.Choice(["u", "s", "q"], case_sensitive=False),
                        default="s",
                        show_choices=False,
                    ).lower()

                    if choice == "q":
                        click.echo("Uscita dal controllo interattivo.")
                        return
                    elif choice == "u":
                        click.echo(f"  Separazione (unstack) di {inv.file_name} in corso...")
                        ok = self._stack_service.unstack_file(s.media_uid, inv.file_uid)
                        if ok:
                            click.secho(f"  ✓ Separato con successo via API!", fg="green")
                            unstacked_count += 1
                        else:
                            click.secho(f"  ✗ Errore durante l'unstack via API.", fg="red")
                    elif choice == "s":
                        click.echo("  -> Saltato.")
                except (KeyboardInterrupt, click.Abort):
                    click.echo("\nInterrotto.")
                    return

        click.secho(
            f"\n✓ Sessione completata: {unstacked_count} video separati con successo!",
            fg="green",
            bold=True,
        )

    def _handle_batch_unstack(
        self, stacks: list[InvalidVideoStack], total_invalid_files: int, yes: bool = False
    ) -> None:
        if not yes:
            confirm = click.confirm(
                f"Trovati {total_invalid_files} video in {len(stacks)} stack errati.\n"
                f"Confermi l'unstack automatico via PhotoPrism API per tutti questi video?",
                default=False,
            )
            if not confirm:
                click.echo("Operazione annullata.")
                return

        success_count = 0
        error_count = 0

        with click.progressbar(
            length=total_invalid_files, label="Separazione video in corso"
        ) as bar:
            for s in stacks:
                for inv in s.invalid_files:
                    ok = self._stack_service.unstack_file(s.media_uid, inv.file_uid)
                    if ok:
                        success_count += 1
                    else:
                        error_count += 1
                    bar.update(1)

        click.secho(
            f"\n✓ Batch unstack completato: {success_count} separati con successo, {error_count} errori.",
            fg="green" if error_count == 0 else "yellow",
            bold=True,
        )

    def _output_json(self, stacks: list[InvalidVideoStack]) -> None:
        data = []
        for s in stacks:
            data.append({
                "media_id": s.media_id,
                "media_uid": s.media_uid,
                "photo_title": s.photo_title,
                "taken_at": s.taken_at.isoformat() if s.taken_at else None,
                "max_duration_diff": s.max_duration_diff,
                "primary_file": {
                    "file_uid": s.primary_file.file_uid,
                    "file_name": s.primary_file.file_name,
                    "file_size": s.primary_file.file_size,
                    "duration": s.primary_file.duration,
                },
                "invalid_files": [
                    {
                        "file_uid": inv.file_uid,
                        "file_name": inv.file_name,
                        "file_size": inv.file_size,
                        "duration": inv.duration,
                    }
                    for inv in s.invalid_files
                ],
            })
        click.echo(json.dumps(data, indent=2))
