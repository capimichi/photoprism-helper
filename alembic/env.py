from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from photoprismhelper.entity.base import Base  # noqa: E402
from photoprismhelper.entity.media_item import MediaItem  # noqa: F401,E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

load_dotenv()

target_metadata = Base.metadata


def _get_database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "mysql+pymysql://photoprismhelper:photoprismhelper@localhost:3306/photoprismhelper",
    )


def run_migrations_offline() -> None:
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section)
    if configuration is None:
        configuration = {}
    configuration["sqlalchemy.url"] = _get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
