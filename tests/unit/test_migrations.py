"""Exercise Alembic SQL emission and frozen revision parity without a database."""

import ast
import io
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from hellas_dataops.storage import metadata
from hellas_dataops.storage.migration import migration_config

REVISION = "20261022_0001"


@pytest.fixture(autouse=True)
def forbid_database_engines(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline migrations must not construct database engines")

    monkeypatch.setattr(sa, "create_engine", forbidden)
    monkeypatch.setattr(sa, "engine_from_config", forbidden)


def emitted_sql(direction):
    buffer = io.StringIO()
    config = migration_config(buffer)
    if direction == "upgrade":
        command.upgrade(config, "head", sql=True)
    else:
        command.downgrade(config, f"{REVISION}:base", sql=True)
    return buffer.getvalue()


def test_initial_revision_has_one_head_and_no_live_metadata_dependency():
    scripts = ScriptDirectory.from_config(migration_config())
    assert scripts.get_heads() == [REVISION]
    assert scripts.get_bases() == [REVISION]
    revision = scripts.get_revision(REVISION)
    assert revision.down_revision is None
    assert revision.module.branch_labels is None
    assert revision.module.depends_on is None
    tree = ast.parse(Path(revision.path).read_text(encoding="utf-8"))
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not any(name and name.startswith("hellas_dataops") for name in imports)
    assert callable(revision.module.upgrade) and callable(revision.module.downgrade)


def test_upgrade_sql_contains_postgresql_schema_without_connecting():
    sql = emitted_sql("upgrade")
    assert sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")
    assert sql.count("CREATE TABLE ") == 6  # Five application tables + Alembic history.
    assert "JSONB NOT NULL" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "GENERATED ALWAYS AS IDENTITY" in sql
    assert "CREATE INDEX ix_weather_hourly_source_chunk" in sql
    assert f"INSERT INTO alembic_version (version_num) VALUES ('{REVISION}')" in sql
    assert "CREATE EXTENSION" not in sql
    assert sql == emitted_sql("upgrade")


def test_downgrade_sql_drops_children_first_without_executing_sql():
    sql = emitted_sql("downgrade")
    names = [
        "weather_hourly",
        "ingestion_chunks",
        "ingestion_runs",
        "datasets",
        "city_configs",
    ]
    positions = [sql.index(f"DROP TABLE {name}") for name in names]
    assert positions == sorted(positions)
    assert sql.index("DROP INDEX ix_weather_hourly_source_chunk") < positions[0]
    assert sql.rstrip().endswith("COMMIT;")


def test_checkout_ini_and_packaged_configuration_generate_the_same_sql():
    buffer = io.StringIO()
    config = Config("alembic.ini", output_buffer=buffer)
    assert not config.get_main_option("sqlalchemy.url")
    command.upgrade(config, "head", sql=True)
    assert buffer.getvalue() == emitted_sql("upgrade")


def test_online_migration_is_explicitly_disabled_before_connection():
    with pytest.raises(
        CommandError,
        match="Online migrations require explicit disposable test configuration",
    ):
        command.upgrade(migration_config(), "head")


def test_explicit_connection_url_is_rejected_without_echoing_it():
    config = migration_config(io.StringIO())
    config.set_main_option(
        "sqlalchemy.url", "postgresql://example.invalid/offline-placeholder"
    )
    with pytest.raises(CommandError, match="Do not configure a database URL") as caught:
        command.upgrade(config, "head", sql=True)
    assert "example.invalid" not in str(caught.value)


def test_unknown_revision_fails_clearly():
    with pytest.raises(CommandError, match="Can't locate revision"):
        command.upgrade(migration_config(io.StringIO()), "not-a-revision", sql=True)


def table_signature(table):
    dialect = postgresql.dialect()
    columns = [
        (
            c.name,
            c.type.compile(dialect=dialect),
            c.nullable,
            str(c.server_default.arg)
            if c.server_default is not None and c.identity is None
            else None,
            c.identity.always if c.identity is not None else None,
        )
        for c in table.c
    ]
    constraints = set()
    for c in table.constraints:
        constraints.add(
            (
                type(c).__name__,
                str(c.name),
                tuple(c.columns.keys()),
                str(c.sqltext) if isinstance(c, sa.CheckConstraint) else None,
                tuple(e.target_fullname for e in c.elements)
                if isinstance(c, sa.ForeignKeyConstraint)
                else (),
                (c.ondelete, c.onupdate)
                if isinstance(c, sa.ForeignKeyConstraint)
                else None,
            )
        )
    indexes = {str(CreateIndex(i).compile(dialect=dialect)) for i in table.indexes}
    return columns, constraints, indexes


def test_frozen_upgrade_metadata_matches_current_schema_and_downgrade_inventory(
    monkeypatch,
):
    revision = (
        ScriptDirectory.from_config(migration_config()).get_revision(REVISION).module
    )
    captured = sa.MetaData()
    order = []
    drops = []

    def create_table(name, *elements, **kwargs):
        order.append(name)
        return sa.Table(name, captured, *elements, **kwargs)

    def create_index(name, table_name, columns, **kwargs):
        return sa.Index(
            name, *(captured.tables[table_name].c[c] for c in columns), **kwargs
        )

    monkeypatch.setattr(revision.op, "f", sa.schema.conv)
    monkeypatch.setattr(revision.op, "create_table", create_table)
    monkeypatch.setattr(revision.op, "create_index", create_index)
    monkeypatch.setattr(revision.op, "drop_table", lambda name: drops.append(name))
    monkeypatch.setattr(revision.op, "drop_index", lambda name, **kw: None)
    revision.upgrade()
    assert set(captured.tables) == set(metadata.tables)
    for name, table in metadata.tables.items():
        assert table_signature(captured.tables[name]) == table_signature(table), name
    revision.downgrade()
    assert drops == list(reversed(order))
