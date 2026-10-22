"""Driver compatibility and opt-in network guards without real connections."""

import importlib.metadata as metadata
import importlib.util
import inspect
import socket
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pg8000
import pg8000.converters as converters
import pg8000.dbapi as dbapi
import pytest
import sqlalchemy as sa
from pg8000.core import CoreConnection
from sqlalchemy.dialects.postgresql.pg8000 import PGDialect_pg8000

from hellas_dataops.storage import test_database as db


@pytest.fixture
def target():
    return db.TestDatabaseConfig(
        project="hellas-dataops-test-abcdef123456",
        port=15432,
        password="a" * 64,
        marker="b" * 64,
    )


@pytest.fixture
def integration_config():
    path = Path(__file__).resolve().parents[1] / "integration/conftest.py"
    spec = importlib.util.spec_from_file_location("integration_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_driver_and_dialect_accept_the_configured_connection_arguments(
    monkeypatch, target
):
    """Stop at the real DBAPI boundary: reject unsupported options without networking."""
    captured = {}
    signature = inspect.signature(dbapi.connect)

    def intercepted(*args, **kwargs):
        captured.update(signature.bind(*args, **kwargs).arguments)
        raise dbapi.InterfaceError("synthetic pre-network stop")

    monkeypatch.setattr(pg8000, "connect", intercepted)
    with pytest.raises(db.TestDatabaseError, match="connection failed"):
        with db.test_connection(target):
            pytest.fail("The intercepted connection must never succeed")
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == target.port
    assert captured["user"] == target.user and captured["database"] == target.database
    assert captured["timeout"] == 5 and captured["ssl_context"] is False
    assert captured["startup_params"]["TimeZone"] == "UTC"


def test_selected_driver_dependency_closure_has_no_native_extensions():
    for name in ("pg8000", "scramp", "asn1crypto", "python-dateutil", "six", "tzdata"):
        dist = metadata.distribution(name)
        assert "Root-Is-Purelib: true" in dist.read_text("WHEEL")
        assert not any(
            Path(str(f)).suffix.lower() in {".pyd", ".dll", ".so"} for f in dist.files
        )
    for name in ("psycopg", "psycopg-binary"):
        with pytest.raises(metadata.PackageNotFoundError):
            metadata.distribution(name)


def test_bound_values_are_not_interpolated_into_sql():
    value = "synthetic '; SELECT 1; --"
    dialect = PGDialect_pg8000(dbapi=PGDialect_pg8000.import_dbapi())
    compiled = (
        sa.text("SELECT CAST(:value AS text)")
        .bindparams(value=value)
        .compile(dialect=dialect)
    )
    assert value not in str(compiled) and "%s" in str(compiled)
    sql, values = dbapi.convert_paramstyle("format", str(compiled), (value,))
    assert "$1" in sql and value not in sql and tuple(values) == (value,)


@pytest.mark.parametrize("offset", [0, 2, 3, -4])
def test_timestamp_conversion_preserves_utc_instant(offset):
    instant = datetime(2025, 10, 26, 1, tzinfo=UTC)
    supplied = instant.astimezone(timezone(timedelta(hours=offset)))
    encoded = converters.datetime_out(supplied)
    assert datetime.fromisoformat(encoded) == instant
    received = converters.timestamptz_in(supplied.strftime("%Y-%m-%d %H:%M:%S%z"))
    assert received.astimezone(UTC) == instant


def test_constraint_error_fields_survive_sqlalchemy_wrapping():
    conn = CoreConnection.__new__(CoreConnection)
    conn._client_encoding = "utf8"
    context = SimpleNamespace(error=None)
    # Synthetic PostgreSQL wire error; no server is involved.
    conn.handle_ERROR_RESPONSE(
        b"SERROR\0C23505\0nsynthetic_constraint\0Mduplicate\0\0", context
    )
    wrapped = sa.exc.DBAPIError.instance("synthetic", {}, context.error, dbapi.Error)
    assert isinstance(wrapped, sa.exc.DatabaseError)
    assert wrapped.orig.args[0]["C"] == "23505"
    assert wrapped.orig.args[0]["n"] == "synthetic_constraint"


@pytest.mark.parametrize(
    "method", ["create_connection", "getaddrinfo", "connect", "connect_ex"]
)
@pytest.mark.parametrize(
    "address", [("example.invalid", 15432), ("127.0.0.1", 5432), ("::1", 15432)]
)
def test_integration_guard_rejects_other_destinations(
    integration_config, target, monkeypatch, method, address
):
    integration_config.restrict_database_network(monkeypatch, target)
    with pytest.raises(AssertionError, match="explicit disposable"):
        if method == "getaddrinfo":
            socket.getaddrinfo(*address)
        elif method == "create_connection":
            socket.create_connection(address)
        else:
            with socket.socket() as sock:
                getattr(sock, method)(address)


def test_guard_allows_only_exact_target_without_dns(
    integration_config, target, monkeypatch
):
    create, connect, connect_ex = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(socket, "create_connection", create)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    integration_config.restrict_database_network(monkeypatch, target)
    address = (target.host, target.port)
    assert socket.getaddrinfo(*address) == [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", address)
    ]
    socket.create_connection(address, timeout=5)
    with socket.socket() as sock:
        sock.connect(address)
        sock.connect_ex(address)
    create.assert_called_once_with(address, timeout=5)
    assert connect.call_args.args[-1] == connect_ex.call_args.args[-1] == address


def test_default_suite_still_blocks_all_network():
    with pytest.raises(AssertionError, match="offline unit tests"):
        socket.create_connection(("127.0.0.1", 15432))
