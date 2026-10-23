"""Explicit PostgreSQL opt-in; all test records are rolled back."""

import socket
from pathlib import Path

import pytest
import sqlalchemy as sa

from hellas_dataops.storage.migration import migrate_test_database
from hellas_dataops.storage.test_database import (
    load_test_database,
    require_empty_database,
    test_connection,
)


def pytest_addoption(parser):
    parser.addoption(
        "--postgres-config", help="Explicit host-generated disposable test target"
    )


def pytest_collection_modifyitems(config, items):
    if items and not config.getoption("--postgres-config"):
        raise pytest.UsageError(
            "PostgreSQL tests require --postgres-config from the host verification runner"
        )
    for item in items:
        item.add_marker(pytest.mark.integration)


def restrict_database_network(monkeypatch, target):
    """Permit only this validated IPv4 test port; never perform DNS resolution."""
    original_create = socket.create_connection
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def check(address):
        if address != (target.host, target.port):
            raise AssertionError(
                "Only the explicit disposable database target is allowed"
            )

    def create(address, *args, **kwargs):
        check(address)
        return original_create(address, *args, **kwargs)

    def resolve(host, port, *args, **kwargs):
        check((host, port))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (host, port))
        ]

    def connect(sock, address):
        check(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check(address)
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket, "create_connection", create)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


@pytest.fixture(scope="session")
def database_config(request):
    return load_test_database(Path(request.config.getoption("--postgres-config")))


@pytest.fixture(scope="session", autouse=True)
def block_network(database_config, api_event_loop):
    # Override only in this opt-in subtree. Root offline protection is unchanged.
    with pytest.MonkeyPatch.context() as patch:
        restrict_database_network(patch, database_config)
        yield


@pytest.fixture(scope="session")
def database_target(database_config, block_network):
    target = database_config
    with test_connection(target) as connection:
        require_empty_database(connection)
        if sa.inspect(connection).get_table_names():
            pytest.fail(
                "Fresh database required; refusing to reuse a previously migrated database"
            )
    migrate_test_database(target)
    with test_connection(target) as connection:
        assert (
            connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            == "20261022_0001"
        )
        assert len(sa.inspect(connection).get_table_names()) == 6
    migrate_test_database(target, downgrade=True)
    with test_connection(target) as connection:
        # Alembic keeps its empty version table when downgrading online to base.
        assert sa.inspect(connection).get_table_names() == ["alembic_version"]
        assert connection.scalar(sa.text("SELECT count(*) FROM alembic_version")) == 0
    migrate_test_database(target)
    yield target
    with test_connection(target) as connection:
        require_empty_database(connection)
        assert (
            connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            == "20261022_0001"
        )


@pytest.fixture
def connection(database_target):
    with test_connection(database_target) as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
