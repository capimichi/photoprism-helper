from __future__ import annotations

import click
from injector import inject

from photoprismhelper.command.abstract_command import AbstractCommand
from photoprismhelper.manager.db_manager import DbManager


class DbInitCommand(AbstractCommand):
    command_name = "db:init"

    @inject
    def __init__(self, db_manager: DbManager) -> None:
        self._db_manager = db_manager

    def run(self) -> None:
        """Initialize MariaDB database tables."""
        click.echo("Initializing MariaDB schema...")
        self._db_manager.create_tables()
        click.echo("✓ MariaDB database tables initialized successfully.")
