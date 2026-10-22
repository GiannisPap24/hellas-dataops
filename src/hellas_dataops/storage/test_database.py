"""Explicit disposable-test targets; never application database configuration."""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import URL, Connection
from sqlalchemy.pool import NullPool

from hellas_dataops.storage.schema import metadata


class TestDatabaseError(ValueError):
    """Safe diagnostic that never includes credentials or a connection URL."""


@dataclass(frozen=True, slots=True)
class TestDatabaseConfig:
    """Host-runner configuration, bound to an independently checked server marker."""

    __test__ = False
    project: str
    port: int
    password: str = field(repr=False)
    marker: str = field(repr=False)
    host: str = "127.0.0.1"
    database: str = "hellas_dataops_test"
    user: str = "hellas_dataops_test"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.project, str)
            or re.fullmatch(r"hellas-dataops-test-[0-9a-f]{12}", self.project) is None
            or type(self.port) is not int
            or not 1024 <= self.port <= 65535
            or self.host != "127.0.0.1"
            or self.database != "hellas_dataops_test"
            or self.user != "hellas_dataops_test"
        ):
            raise TestDatabaseError("Unsafe or incomplete disposable database target")
        for secret in (self.password, self.marker):
            if (
                not isinstance(secret, str)
                or re.fullmatch(r"[0-9a-f]{64}", secret) is None
            ):
                raise TestDatabaseError(
                    "Invalid disposable database credentials or marker"
                )

    @property
    def ownership(self) -> str:
        return f"{self.project}:{self.marker}"

    def url(self) -> URL:
        return URL.create(
            "postgresql+pg8000",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate configuration field")
        result[key] = value
    return result


def load_test_database(path: Path) -> TestDatabaseConfig:
    """Read only the explicitly supplied local JSON file; reject unknown keys."""
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields
        )
        if not isinstance(data, dict):
            raise TypeError
        return TestDatabaseConfig(**data)
    except OSError, ValueError, TypeError:
        raise TestDatabaseError("Invalid test database configuration file") from None


def verify_database(connection: Connection, target: TestDatabaseConfig) -> None:
    """Check identity before any test write; a localhost address alone is insufficient."""
    row = connection.execute(
        sa.text(
            "SELECT current_database(), current_user, "
            "current_setting('server_version_num')::int, "
            "shobj_description(oid, 'pg_database') FROM pg_database "
            "WHERE datname = current_database()"
        )
    ).one()
    if (
        row[0] != target.database
        or row[1] != target.user
        or not 180000 <= row[2] < 190000
        or row[3] != target.ownership
    ):
        raise TestDatabaseError(
            "Server identity, PostgreSQL version, or ownership mismatch"
        )


@contextmanager
def test_connection(target: TestDatabaseConfig) -> Iterator[Connection]:
    """Open one bounded, verified connection; caller controls transactions."""
    if not isinstance(target, TestDatabaseConfig):
        raise TestDatabaseError("Explicit disposable test configuration required")
    engine = sa.create_engine(
        target.url(),
        poolclass=NullPool,
        hide_parameters=True,
        echo=False,
        connect_args={
            "timeout": 5,
            "ssl_context": False,
            "startup_params": {
                "search_path": "public",
                "TimeZone": "UTC",
                "statement_timeout": "15000",
                "lock_timeout": "5000",
            },
        },
    )
    try:
        try:
            connection = engine.connect()
        except sa.exc.SQLAlchemyError:
            raise TestDatabaseError("Disposable PostgreSQL connection failed") from None
        with connection:
            verify_database(connection, target)
            connection.rollback()
            yield connection
    finally:
        engine.dispose()


test_connection.__test__ = False


def require_empty_database(connection: Connection) -> None:
    """Refuse unknown relations and populated tables before migration lifecycle tests."""
    relations = connection.execute(
        sa.text(
            "SELECT n.nspname, c.relname, c.relkind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' "
            "AND c.relkind IN ('r', 'p', 'v', 'm', 'f')"
        )
    ).all()
    allowed = set(metadata.tables) | {"alembic_version"}
    if any(
        schema != "public" or name not in allowed or kind != "r"
        for schema, name, kind in relations
    ):
        raise TestDatabaseError(
            "Unexpected relations in disposable database; refusing migrations"
        )
    for _, name, _ in sorted(relations):
        # Names come exclusively from the allowlist above, never arbitrary input.
        connection.execute(
            sa.text(f'LOCK TABLE public."{name}" IN ACCESS EXCLUSIVE MODE')
        )
        if (
            name != "alembic_version"
            and connection.execute(
                sa.text(f'SELECT 1 FROM public."{name}" LIMIT 1')
            ).first()
            is not None
        ):
            raise TestDatabaseError(
                "Migration verification requires empty application tables"
            )
