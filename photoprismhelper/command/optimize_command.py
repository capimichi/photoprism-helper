from __future__ import annotations

import tempfile
import click
from injector import inject
from tabulate import tabulate

from photoprismhelper.command.abstract_command import AbstractCommand
from photoprismhelper.service.preview_server import VideoPreviewServer
from photoprismhelper.service.storage_analysis_service import StorageAnalysisService
from photoprismhelper.service.video_optimizer_service import VideoOptimizerService


class VideoOptimizeCommand(AbstractCommand):
    command_name = "video:optimize"

    @inject
    def __init__(
        self,
        optimizer_service: VideoOptimizerService,
        storage_analysis_service: StorageAnalysisService,
        preview_server: VideoPreviewServer | None = None,
    ) -> None:
        self._optimizer_service = optimizer_service
        self._storage_analysis_service = storage_analysis_service
        self._preview_server = preview_server

    def register_options(self, fn):
        fn = click.option("--uid", "-u", default=None, type=str, help="Target a specific media item by its UID.")(fn)
        fn = click.option("--limit", "-l", default=1, type=int, help="Maximum number of videos to optimize.")(fn)
        fn = click.option("--min-size-mb", default=10, type=int, help="Minimum file size in MB to qualify (default: 10).")(fn)
        fn = click.option("--dry-run", is_flag=True, default=False, help="List candidates without converting.")(fn)
        fn = click.option("--interactive/--no-interactive", "-i/-y", default=True, help="Prompt before converting and replacing.")(fn)
        fn = click.option("--keep-backup/--no-backup", default=True, help="Keep .bak of original file (default: True).")(fn)
        fn = click.option("--notify/--no-notify", default=True, help="Notify PhotoPrism to re-index the folder (default: True).")(fn)
        fn = click.option("--preview/--no-preview", default=False, help="Launch ephemeral web page to compare videos side-by-side.")(fn)
        fn = click.option("--revert", "revert_id", default=None, type=int, help="Revert a conversion by its ID.")(fn)
        fn = click.option("--history", is_flag=True, default=False, help="Show conversion history.")(fn)
        fn = click.option("--only-peculiar", is_flag=True, default=False, help="Filter history to only show conversions with peculiarities (e.g. CPU fallback, audio transcode).")(fn)
        return fn

    def run(
        self,
        uid: str | None = None,
        limit: int = 1,
        min_size_mb: int = 10,
        dry_run: bool = False,
        interactive: bool = True,
        keep_backup: bool = True,
        notify: bool = True,
        preview: bool = False,
        revert_id: int | None = None,
        history: bool = False,
        only_peculiar: bool = False,
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
        if history or only_peculiar:
            self._show_history(only_peculiar=only_peculiar)
            return

        candidates = self._optimizer_service.get_candidate_videos(min_size_mb=min_size_mb, limit=limit, uid=uid)
        if not candidates:
            if uid:
                click.secho(f"Media item with UID '{uid}' not found.", fg="red")
            else:
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
                c.uid,
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

            if not os.path.isfile(disk_path):
                click.secho(f"  ⚠ File not found on disk (already optimized or moved): {disk_path}. Skipping.", fg="yellow")
                continue

            if interactive:
                confirm = click.confirm(f"  Start 1080p HEVC optimization for {item.file_name}?", default=True)
                if not confirm:
                    click.echo("  Skipped.")
                    continue

            try:
                with tempfile.TemporaryDirectory(prefix="pp_opt_") as tmp_dir:
                    click.echo("  Encoding and cloning metadata in local staging (/tmp)...")
                    draft = self._optimizer_service.prepare_optimization(
                        item,
                        tmp_dir=tmp_dir,
                        max_height=1080,
                    )

                    if not draft.success:
                        click.secho(f"  ✗ Conversion failed: {draft.error_message}", fg="red")
                        continue

                opt_fmt = self._storage_analysis_service.format_bytes(draft.optimized_size)
                saved_fmt = self._storage_analysis_service.format_bytes(draft.original_size - draft.optimized_size)
                pct = ((draft.original_size - draft.optimized_size) / draft.original_size) * 100

                click.secho(
                    f"  ✓ Transcoding finished in {draft.duration_seconds:.1f}s (encoder: {draft.encoder_used})",
                    fg="green",
                )
                click.echo(f"    - Original size:  {size_fmt}")
                click.secho(f"    - Optimized size: {opt_fmt} (-{pct:.1f}% | risparmiati: {saved_fmt})", fg="cyan", bold=True)
                if preview and self._preview_server:
                    preview_url = self._preview_server.start(
                        file1_path=disk_path,
                        file2_path=draft.temp_output_path,
                        title=item.file_name,
                        subtitle=f"UID: {item.uid} | Risparmio stimato: {saved_fmt} (-{pct:.1f}%)",
                        label1=f"Originale ({item.extension.upper()})",
                        label2="Ottimizzato (1080p HEVC)",
                        info1=f"Dimensione: {size_fmt}",
                        info2=f"Dimensione: {opt_fmt} (-{pct:.1f}%)",
                        badge1=f"Originale: {size_fmt}",
                        badge2=f"Ottimizzato: {opt_fmt}",
                        badge3=f"Risparmiati: {saved_fmt}",
                    )
                    click.secho("\n  📺 Anteprima Web attiva per il confronto:", fg="cyan", bold=True)
                    click.secho(f"     👉 {preview_url}", fg="cyan", underline=True)
                    click.echo("     (I video sono sincronizzati nello scrub. Aprilo nel browser per visualizzarli)\n")

                try:
                    if interactive:
                        confirm_apply = click.confirm(f"  Apply replacement on NAS for {item.file_name}?", default=True)
                        if not confirm_apply:
                            click.echo("  Cancelled. Local temp discarded, NAS untouched.")
                            continue

                    conv = self._optimizer_service.apply_optimization(
                        draft,
                        keep_backup=keep_backup,
                        notify_photoprism=notify,
                    )
                finally:
                    if preview and self._preview_server:
                        self._preview_server.stop()

                click.secho(f"  ✓ Conversion #{conv.id} applied to NAS!", fg="green", bold=True)
                click.echo(f"    - New file size: {opt_fmt} (saved {saved_fmt})")
                if keep_backup:
                    click.echo(f"    - Backup saved at: {conv.backup_file_path}")
                if notify:
                    click.echo("    - PhotoPrism re-index triggered.")

                # Check for stack duplicates (e.g. .00001.mov)
                duplicates = self._optimizer_service.get_stack_duplicates(item.uid, primary_file_name=item.file_name)
                if duplicates:
                    click.secho(f"\n  ⚠ Found {len(duplicates)} duplicate/stacked video(s) in this media item:", fg="yellow")
                    for d in duplicates:
                        d_size = self._storage_analysis_service.format_bytes(d.file_size)
                        click.echo(f"    • {d.file_name} ({d_size})")

                    clean_dups = False
                    if interactive:
                        clean_dups = click.confirm("    Remove stack duplicate(s) to free up extra NAS space?", default=True)
                    else:
                        clean_dups = True

                    if clean_dups:
                        for d in duplicates:
                            ok, msg = self._optimizer_service.remove_duplicate_file(
                                d, keep_backup=keep_backup, notify_photoprism=notify
                            )
                            if ok:
                                click.secho(f"    ✓ {msg}", fg="green")
                            else:
                                click.secho(f"    ✗ Failed to remove {d.file_name}: {msg}", fg="red")
            except Exception as e:
                click.secho(f"  ✗ Unexpected error processing {item.file_name}: {e}. Skipping.", fg="red")

    def _show_history(self, only_peculiar: bool = False) -> None:
        session = self._optimizer_service._media_repository.get_session()
        try:
            limit = 50 if only_peculiar else 20
            conversions = self._optimizer_service._conversion_repository.list_conversions(session, limit=limit)
            if not conversions:
                click.echo("No conversion history found.")
                return

            rows = []
            for cv in conversions:
                has_peculiar = bool(cv.fallback_triggered or cv.audio_transcoded or cv.peculiarities)
                if only_peculiar and not has_peculiar:
                    continue

                saved = (cv.original_size - (cv.optimized_size or 0)) if cv.optimized_size else 0
                saved_fmt = self._storage_analysis_service.format_bytes(saved) if saved > 0 else "-"
                dt = cv.created_at.strftime("%Y-%m-%d %H:%M") if cv.created_at else "-"

                # Encoder label
                enc = cv.encoder_used or "-"
                if cv.is_hardware:
                    enc_lbl = f"{enc} (GPU)"
                elif cv.encoder_used:
                    enc_lbl = f"{enc} (CPU)"
                else:
                    enc_lbl = "-"

                # Format peculiarities badge
                pec_labels = []
                if cv.fallback_triggered:
                    pec_labels.append("🖥️ CPU Fallback")
                if cv.audio_transcoded:
                    src_a = cv.source_audio_codec or "legacy"
                    pec_labels.append(f"🔊 Audio ({src_a} -> aac)")
                if cv.peculiarities:
                    for p in cv.peculiarities:
                        if p == "rotated":
                            pec_labels.append("🔄 Rotated")
                        elif p not in ("gpu_fallback_to_cpu",) and not p.startswith("audio_"):
                            pec_labels.append(p)

                pec_str = "\n".join(pec_labels) if pec_labels else "Standard"
                backup_str = os.path.basename(cv.backup_file_path) if cv.backup_file_path else "-"
                orig_file = os.path.basename(cv.original_file_path)

                rows.append([
                    cv.id,
                    f"{orig_file}\n({cv.media_uid})",
                    cv.status,
                    self._storage_analysis_service.format_bytes(cv.original_size),
                    self._storage_analysis_service.format_bytes(cv.optimized_size or 0) if cv.optimized_size else "-",
                    saved_fmt,
                    enc_lbl,
                    pec_str,
                    backup_str,
                    dt,
                ])

            if not rows:
                click.echo("No conversions with peculiarities found.")
                return

            title = "### Conversions with Peculiarities (CPU Fallback, Audio Transcode, etc.)" if only_peculiar else "### Recent Conversions"
            click.echo(f"\n{title}")
            click.echo(tabulate(
                rows,
                headers=["ID", "File (UID)", "Status", "Original", "Optimized", "Saved", "Encoder", "Peculiarities", "Backup (.bak)", "Date"],
                tablefmt="github",
            ))
        finally:
            session.close()
