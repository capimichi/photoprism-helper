from __future__ import annotations

import click

from photoprismhelper.command.db_init_command import DbInitCommand
from photoprismhelper.command.media_stats_command import MediaStatsCommand
from photoprismhelper.command.media_sync_command import MediaSyncCommand
from photoprismhelper.command.optimize_command import VideoOptimizeCommand
from photoprismhelper.command.stack_duplicates_command import VideoDuplicatesCommand
from photoprismhelper.command.tag_command import TagCommand
from photoprismhelper.container.default_container import DefaultContainer


@click.group()
def cli() -> None:
    """CLI helper for PhotoPrism: Index, analyze storage, optimize heavy videos and tag media."""


default_container = DefaultContainer.getInstance()

cli.add_command(default_container.get(DbInitCommand).to_click_command())
cli.add_command(default_container.get(MediaSyncCommand).to_click_command())
cli.add_command(default_container.get(MediaStatsCommand).to_click_command())
cli.add_command(default_container.get(TagCommand).to_click_command())
cli.add_command(default_container.get(VideoOptimizeCommand).to_click_command())
cli.add_command(default_container.get(VideoDuplicatesCommand).to_click_command())

if __name__ == "__main__":
    cli()
