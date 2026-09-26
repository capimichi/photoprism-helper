from __future__ import annotations

import click
from injector import inject

from photoprismhelper.command.abstract_command import AbstractCommand
from photoprismhelper.service.storage_analysis_service import StorageAnalysisService


class MediaStatsCommand(AbstractCommand):
    command_name = "media:stats"

    @inject
    def __init__(self, storage_analysis_service: StorageAnalysisService) -> None:
        self._storage_analysis_service = storage_analysis_service

    def register_options(self, fn):
        fn = click.option(
            "--top",
            default=15,
            help="Number of largest files to display.",
        )(fn)
        fn = click.option(
            "--type",
            "media_type",
            default=None,
            help="Filter largest files by type (e.g., video, image).",
        )(fn)
        return fn

    def run(self, top: int = 15, media_type: str | None = None) -> None:
        """Display storage analysis, heaviest folders, and largest files."""
        summary = self._storage_analysis_service.get_summary_report()
        largest = self._storage_analysis_service.get_largest_files_report(limit=top, media_type=media_type)

        click.echo("\n" + summary + "\n\n" + largest + "\n")
