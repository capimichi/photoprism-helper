import os
import click
from alembic import command
from alembic.config import Config
from injector import inject

from photoprismhelper.command.abstract_command import AbstractCommand
from photoprismhelper.config.app_config import AppConfig
from photoprismhelper.manager.db_manager import DbManager


class DbInitCommand(AbstractCommand):
    command_name = "db:init"

    @inject
    def __init__(self, db_manager: DbManager, app_config: AppConfig) -> None:
        self._db_manager = db_manager
        self._app_config = app_config

    def run(self) -> None:
        """Initialize MariaDB database tables using Alembic migrations."""
        click.echo("Applying Alembic migrations to MariaDB...")
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        alembic_ini_path = os.path.join(root_dir, "alembic.ini")
        alembic_cfg = Config(alembic_ini_path)
        alembic_cfg.set_main_option("sqlalchemy.url", self._app_config.database_url)
        command.upgrade(alembic_cfg, "head")
        click.echo("✓ MariaDB database tables and migrations applied successfully (head).")

