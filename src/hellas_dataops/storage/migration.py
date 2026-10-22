"""Offline SQL generation and guarded online disposable-database migrations."""

from typing import TextIO

from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.util import CommandError

from hellas_dataops.storage.schema import metadata
from hellas_dataops.storage.test_database import (
    TestDatabaseConfig,
    require_empty_database,
    test_connection,
)


def migration_config(output_buffer: TextIO | None = None) -> Config:
    """Locate packaged revisions even outside the checkout; perform no I/O."""
    config = Config(output_buffer=output_buffer)
    config.set_main_option("script_location", "hellas_dataops.storage:migrations")
    config.set_main_option("path_separator", "os")
    return config


def run_migrations(context: EnvironmentContext) -> None:
    """Preserve offline SQL; online mode requires a verified empty disposable target."""
    if context.config.get_main_option("sqlalchemy.url"):
        raise CommandError(
            "Do not configure a database URL; use explicit disposable test configuration."
        )
    if not context.is_offline_mode():
        target = context.config.attributes.get("test_database")
        if not isinstance(target, TestDatabaseConfig):
            raise CommandError(
                "Online migrations require explicit disposable test configuration"
            )
        with test_connection(target) as connection, connection.begin():
            require_empty_database(connection)
            context.configure(connection=connection, target_metadata=metadata)
            with context.begin_transaction():
                context.run_migrations()
        return
    context.configure(
        dialect_name="postgresql",
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def migrate_test_database(
    target: TestDatabaseConfig, *, downgrade: bool = False
) -> None:
    """Upgrade to the single reviewed revision, or downgrade a verified empty test DB."""
    from alembic import command

    config = migration_config()
    config.attributes["test_database"] = target
    if downgrade:
        command.downgrade(config, "base")
    else:
        command.upgrade(config, "20261022_0001")
