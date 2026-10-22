"""Offline safety and orchestration tests; no PostgreSQL enforcement claims."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from alembic import command

from hellas_dataops.storage import migration
from hellas_dataops.storage import test_database as db


@pytest.fixture
def target():
    return db.TestDatabaseConfig(
        project="hellas-dataops-test-abcdef123456",
        port=15432,
        password="a" * 64,
        marker="b" * 64,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"project": "eren"},
        {"project": None},
        {"project": "hellas-dataops-test-../bad"},
        {"host": "localhost"},
        {"host": "192.168.1.1"},
        {"database": "postgres"},
        {"user": "postgres"},
        {"port": True},
        {"port": "15432"},
        {"port": 0},
        {"port": 65536},
        {"password": ""},
        {"password": None},
        {"marker": "wrong"},
    ],
)
def test_unsafe_targets_fail_without_secrets(target, changes):
    with pytest.raises(db.TestDatabaseError) as caught:
        replace(target, **changes)
    assert target.password not in str(caught.value) and target.marker not in str(
        caught.value
    )


def test_target_url_and_repr(target):
    assert target.password not in repr(target) and target.marker not in repr(target)
    assert target.password not in str(target.url())
    assert target.url().drivername == "postgresql+pg8000"
    assert target.url().host == "127.0.0.1" and not target.url().query


@pytest.mark.parametrize(
    "content",
    [
        "null",
        "[]",
        "{",
        "{}",
        '{"url":"postgresql://unsafe"}',
        '{"port":15432,"port":15433}',
    ],
)
def test_bad_files_fail_safely(monkeypatch, content):
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: content)
    with pytest.raises(db.TestDatabaseError, match="configuration file"):
        db.load_test_database(Path("synthetic.json"))


def test_file_read_failure(monkeypatch):
    def fail(*a, **k):
        raise OSError("private details")

    monkeypatch.setattr(Path, "read_text", fail)
    with pytest.raises(db.TestDatabaseError, match="configuration file"):
        db.load_test_database(Path("synthetic.json"))


def test_load_explicit_valid_file(monkeypatch, target):
    import json
    from dataclasses import asdict

    monkeypatch.setattr(Path, "read_text", lambda *a, **k: json.dumps(asdict(target)))
    assert db.load_test_database(Path("synthetic.json")) == target


@pytest.mark.parametrize(
    "index,value",
    [(0, "other"), (1, "other"), (2, 170000), (2, 190000), (3, "wrong-marker")],
)
def test_server_mismatch_rejected(target, index, value):
    row = [target.database, target.user, 180006, target.ownership]
    row[index] = value
    connection = MagicMock()
    connection.execute.return_value.one.return_value = row
    with pytest.raises(db.TestDatabaseError, match="ownership mismatch"):
        db.verify_database(connection, target)


def test_verified_connection_lifecycle(monkeypatch, target):
    engine = MagicMock()
    connection = engine.connect.return_value
    connection.__enter__.return_value = connection
    connection.execute.return_value.one.return_value = [
        target.database,
        target.user,
        180006,
        target.ownership,
    ]
    factory = MagicMock(return_value=engine)
    monkeypatch.setattr(sa, "create_engine", factory)
    with db.test_connection(target) as actual:
        assert actual is connection
        connection.rollback.assert_called_once()
    engine.dispose.assert_called_once()
    options = factory.call_args.kwargs
    assert options["hide_parameters"] and not options["echo"]
    assert options["connect_args"]["timeout"] == 5
    assert options["connect_args"]["ssl_context"] is False
    assert options["connect_args"]["startup_params"] == {
        "search_path": "public",
        "TimeZone": "UTC",
        "statement_timeout": "15000",
        "lock_timeout": "5000",
    }


def test_connect_failure_does_not_leak_credentials(monkeypatch, target):
    engine = MagicMock()
    engine.connect.side_effect = sa.exc.SQLAlchemyError(target.password)
    monkeypatch.setattr(sa, "create_engine", lambda *a, **k: engine)
    with pytest.raises(db.TestDatabaseError, match="connection failed") as caught:
        with db.test_connection(target):
            pytest.fail("Must not yield")
    assert target.password not in str(caught.value)
    engine.dispose.assert_called_once()


def test_no_implicit_database_target():
    with pytest.raises(db.TestDatabaseError, match="Explicit"):
        with db.test_connection(None):
            pytest.fail("Must not yield")


@pytest.mark.parametrize(
    "relations",
    [
        [("private", "weather_hourly", "r")],
        [("public", "unknown", "r")],
        [("public", "weather_hourly", "v")],
    ],
)
def test_unknown_relations_refuse_migrations(relations):
    connection = MagicMock()
    connection.execute.return_value.all.return_value = relations
    with pytest.raises(db.TestDatabaseError, match="Unexpected relations"):
        db.require_empty_database(connection)


def test_empty_database_and_version_row_are_allowed():
    connection = MagicMock()
    connection.execute.return_value.all.return_value = [
        ("public", "alembic_version", "r"),
        ("public", "city_configs", "r"),
    ]
    connection.execute.return_value.first.return_value = None
    db.require_empty_database(connection)
    assert any(
        "ACCESS EXCLUSIVE" in str(c.args[0]) for c in connection.execute.call_args_list
    )


def test_nonempty_database_refused():
    connection = MagicMock()
    connection.execute.return_value.all.return_value = [("public", "city_configs", "r")]
    connection.execute.return_value.first.return_value = (1,)
    with pytest.raises(db.TestDatabaseError, match="empty application tables"):
        db.require_empty_database(connection)


@pytest.mark.parametrize("downgrade", [False, True])
def test_migration_wrapper_only_targets_reviewed_revision(
    monkeypatch, target, downgrade
):
    upgrade, down = MagicMock(), MagicMock()
    monkeypatch.setattr(command, "upgrade", upgrade)
    monkeypatch.setattr(command, "downgrade", down)
    migration.migrate_test_database(target, downgrade=downgrade)
    called = down if downgrade else upgrade
    assert called.call_args.args[0].attributes["test_database"] == target
    assert called.call_args.args[1] == ("base" if downgrade else "20261022_0001")


def test_online_context_uses_verified_connection_and_transaction(monkeypatch, target):
    context = MagicMock()
    context.is_offline_mode.return_value = False
    context.config = migration.migration_config()
    context.config.attributes["test_database"] = target
    holder, connection = MagicMock(), MagicMock()
    holder.__enter__.return_value = connection
    monkeypatch.setattr(migration, "test_connection", lambda t: holder)
    guard = MagicMock()
    monkeypatch.setattr(migration, "require_empty_database", guard)
    migration.run_migrations(context)
    guard.assert_called_once_with(connection)
    context.configure.assert_called_once_with(
        connection=connection, target_metadata=migration.metadata
    )
    context.run_migrations.assert_called_once()


def test_online_context_stops_before_migration_on_guard_failure(monkeypatch, target):
    context = MagicMock()
    context.is_offline_mode.return_value = False
    context.config = migration.migration_config()
    context.config.attributes["test_database"] = target
    holder = MagicMock()
    monkeypatch.setattr(migration, "test_connection", lambda t: holder)

    def fail(connection):
        raise db.TestDatabaseError("Unexpected relations")

    monkeypatch.setattr(migration, "require_empty_database", fail)
    with pytest.raises(db.TestDatabaseError):
        migration.run_migrations(context)
    context.run_migrations.assert_not_called()
