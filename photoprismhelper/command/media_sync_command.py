from __future__ import annotations

import click
from injector import inject

from photoprismhelper.command.abstract_command import AbstractCommand
from photoprismhelper.manager.db_manager import DbManager
from photoprismhelper.service.media_sync_service import MediaSyncService
from photoprismhelper.service.storage_analysis_service import StorageAnalysisService


class MediaSyncCommand(AbstractCommand):
    command_name = "media:sync"

    @inject
    def __init__(
        self,
        db_manager: DbManager,
        media_sync_service: MediaSyncService,
        storage_analysis_service: StorageAnalysisService,
    ) -> None:
        self._db_manager = db_manager
        self._media_sync_service = media_sync_service
        self._storage_analysis_service = storage_analysis_service

    def register_options(self, fn):
        fn = click.option("--batch-size", default=100, help="Number of items to fetch per batch.")(fn)
        fn = click.option("--max-items", default=None, type=int, help="Maximum number of items to sync (default: all).")(fn)
        fn = click.option("--query", "-q", default="", help="PhotoPrism search filter (e.g. 'type:video', 'type:image').")(fn)
        return fn

    def run(self, batch_size: int = 100, max_items: int | None = None, query: str = "") -> None:
        """Fetch media from PhotoPrism and sync into MariaDB."""
        # Ensure tables exist
        self._db_manager.create_tables()

        click.echo("Connecting to PhotoPrism and syncing media catalog...")
        result = self._media_sync_service.sync(batch_size=batch_size, max_items=max_items, query=query)

        size_formatted = self._storage_analysis_service.format_bytes(result.total_size_bytes)
        click.echo("✓ Media sync completed!")
        click.echo(f"  • Total items processed: {result.total_processed:,}")
        click.echo(f"  • Images: {result.images_count:,}")
        click.echo(f"  • Videos: {result.videos_count:,}")
        click.echo(f"  • Other files: {result.other_count:,}")
        click.echo(f"  • Total indexed storage: {size_formatted}")
