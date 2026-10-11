from __future__ import annotations

import os
import click
from injector import inject
from tabulate import tabulate

from photoprismhelper.command.abstract_command import AbstractCommand
from photoprismhelper.config.app_config import AppConfig
from photoprismhelper.service.exif_fix_service import ExifFixService, FileExifState


class ExifFixCommand(AbstractCommand):
    command_name = "exif:fix"

    @inject
    def __init__(self, exif_fix_service: ExifFixService, app_config: AppConfig) -> None:
        self._service = exif_fix_service
        self._app_config = app_config

    def register_options(self, fn):
        fn = click.argument("path", type=click.Path(exists=True), required=False)(fn)
        fn = click.option(
            "--dry-run",
            "-n",
            is_flag=True,
            default=False,
            help="Simulate the fix and show which files/tags would be updated without writing.",
        )(fn)
        fn = click.option(
            "--recursive/--no-recursive",
            default=True,
            help="Scan directories recursively (default: True).",
        )(fn)
        fn = click.option(
            "--yes",
            "-y",
            is_flag=True,
            default=False,
            help="Apply updates automatically without confirmation prompt.",
        )(fn)
        fn = click.option(
            "--verbose",
            "-v",
            is_flag=True,
            default=False,
            help="Print info for every file, including those already valid.",
        )(fn)
        return fn

    def run(
        self,
        path: str | None = None,
        dry_run: bool = False,
        recursive: bool = True,
        yes: bool = False,
        verbose: bool = False,
    ) -> None:
        """Inspect media files in PATH and populate missing or invalid EXIF date tags from filename timestamp."""
        target_path = path
        if not target_path:
            if os.path.exists(self._app_config.photoprism_originals_path):
                target_path = self._app_config.photoprism_originals_path
            else:
                target_path = os.getcwd()

        click.secho(f"\n📂 Scanning: {target_path} (recursive={recursive}, dry_run={dry_run})", bold=True)

        if not self._service.exiftool_bin:
            click.secho("❌ Error: 'exiftool' executable was not found on system PATH.", fg="red", bold=True)
            return

        # First pass in inspection mode (dry-run style) to collect what needs update
        summary = self._service.process_path(
            target_path=target_path,
            recursive=recursive,
            dry_run=True,  # always inspect first
        )

        items_needing_update = [item for item in summary.items if item.state == FileExifState.NEEDS_UPDATE]

        if verbose:
            verbose_rows = [
                [
                    item.file_name[:40],
                    item.state.value,
                    item.parsed_datetime.strftime("%Y-%m-%d %H:%M:%S") if item.parsed_datetime else "-",
                    ", ".join(item.missing_tags) if item.missing_tags else "-",
                ]
                for item in summary.items
            ]
            click.echo("\n" + tabulate(
                verbose_rows,
                headers=["File", "Status", "Parsed Date", "Missing Tags"],
                tablefmt="github",
            ) + "\n")

        click.secho("\n--- Scan Results ---", bold=True)
        click.echo(f"  Total scanned:          {summary.total_scanned}")
        click.secho(f"  Already valid EXIF:     {summary.already_valid}", fg="green")
        click.secho(f"  Missing EXIF tags:      {len(items_needing_update)}", fg="yellow" if items_needing_update else "green")
        click.echo(f"  Skipped (no date in name): {summary.skipped_no_date}")
        click.echo(f"  Skipped (unsupported ext): {summary.skipped_unsupported}")

        if not items_needing_update:
            click.secho("\n✨ All media with recognized date format already have valid EXIF tags. Nothing to do!", fg="green", bold=True)
            return

        # Show table of files to be updated
        table_rows = [
            [
                item.file_name[:40],
                item.parsed_datetime.strftime("%Y-%m-%d %H:%M:%S") if item.parsed_datetime else "-",
                ", ".join(item.missing_tags),
            ]
            for item in items_needing_update[:25]
        ]
        click.echo("\nFiles to update (showing up to 25):")
        click.echo(tabulate(
            table_rows,
            headers=["File Name", "Extracted Date", "Tags to Populate"],
            tablefmt="github",
        ))
        if len(items_needing_update) > 25:
            click.echo(f"... and {len(items_needing_update) - 25} more files.")

        if dry_run:
            click.secho(f"\n🔍 Dry-run complete. {len(items_needing_update)} files would be updated.", fg="cyan", bold=True)
            return

        if not yes:
            if not click.confirm(f"\nProceed with updating EXIF for {len(items_needing_update)} files in-place?"):
                click.echo("Aborted by user.")
                return

        click.echo("\nUpdating EXIF metadata...")
        updated_count = 0
        error_count = 0
        with click.progressbar(items_needing_update, label="Processing files") as bar:
            for item in bar:
                if item.parsed_datetime:
                    ok = self._service.apply_tags(item.path, item.missing_tags, item.parsed_datetime)
                    if ok:
                        updated_count += 1
                    else:
                        error_count += 1

        if error_count > 0:
            click.secho(f"\n⚠️  Completed with warnings: {updated_count} files updated, {error_count} failed.", fg="yellow", bold=True)
        else:
            click.secho(f"\n✅ Successfully updated {updated_count} files!", fg="green", bold=True)
