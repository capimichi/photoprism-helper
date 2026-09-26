from __future__ import annotations

import click
from injector import inject
from tabulate import tabulate

from photoprismhelper.command.abstract_command import AbstractCommand
from photoprismhelper.service.tag_service import TagService


class TagCommand(AbstractCommand):
    command_name = "tag:suggest"

    @inject
    def __init__(self, tag_service: TagService) -> None:
        self._tag_service = tag_service

    def register_options(self, fn):
        fn = click.option("--limit", default=50, help="Maximum number of items to inspect.")(fn)
        fn = click.option(
            "--apply",
            is_flag=True,
            default=False,
            help="Apply the suggested tags directly to PhotoPrism.",
        )(fn)
        return fn

    def run(self, limit: int = 50, apply: bool = False) -> None:
        """Suggest or apply tags to photos based on their import folder names."""
        click.echo("Scanning indexed media for suggested folder tags...")
        proposals = self._tag_service.find_tag_proposals(limit=limit)

        if not proposals:
            click.echo("No pending folder tag suggestions found.")
            return

        table_rows = [
            [
                p.uid[:12],
                p.file_name[:35],
                p.folder_path[:30],
                p.existing_tags[:20] or "(none)",
                ", ".join(p.new_tags_to_add),
            ]
            for p in proposals
        ]
        click.echo("\n" + tabulate(
            table_rows,
            headers=["UID", "File Name", "Folder Path", "Existing Tags", "Suggested Tags"],
            tablefmt="github",
        ) + "\n")

        if apply:
            click.confirm(
                f"Are you sure you want to apply these suggested tags to {len(proposals)} items in PhotoPrism?",
                abort=True,
            )
            count = self._tag_service.apply_tags(proposals)
            click.echo(f"✓ Successfully applied tags to {count} items in PhotoPrism.")
        else:
            click.echo(f"Found {len(proposals)} proposals. Run with --apply to apply them to PhotoPrism.")
