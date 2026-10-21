"""Offline Alembic configuration with no engine, URL, or driver requirement."""

from typing import TextIO

from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.util import CommandError

from hellas_dataops.storage.schema import metadata


def migration_config(output_buffer: TextIO | None = None) -> Config:
    """Locate packaged revisions even outside the checkout; perform no I/O."""
    config = Config(output_buffer=output_buffer)
    config.set_main_option("script_location", "hellas_dataops.storage:migrations")
    config.set_main_option("path_separator", "os")
    return config


def run_migrations(context: EnvironmentContext) -> None:
    """Emit PostgreSQL SQL only; reject online mode and configured connection URLs."""
    if not context.is_offline_mode():
        raise CommandError("Online migrations are disabled in Milestone 3A; use --sql.")
    if context.config.get_main_option("sqlalchemy.url"):
        raise CommandError(
            "Do not configure a database URL for offline SQL generation."
        )
    context.configure(
        dialect_name="postgresql",
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()
