"""Bounded Core reads over the existing disposable PostgreSQL contract."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from hellas_dataops.domain import DatasetIdentity, HourlyInterval
from hellas_dataops.sources.cities import CITY_REGISTRY
from hellas_dataops.storage.schema import (
    city_configs,
    datasets,
    metadata,
    weather_hourly,
)
from hellas_dataops.storage.test_database import (
    TestDatabaseConfig,
    TestDatabaseError,
    test_connection,
)

MAX_QUERY_DAYS = 31
SLUG = r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*"


class QueryUnavailable(RuntimeError):
    """Safe error; underlying database diagnostics must not reach API clients."""


class QueryNotFound(ValueError):
    """Requested series identity is not available."""


@dataclass(frozen=True, slots=True, kw_only=True)
class WeatherQuery:
    city_id: str
    coordinate_version: str
    dataset: DatasetIdentity
    interval: HourlyInterval
    limit: int = 100
    after: datetime | None = None

    def __post_init__(self) -> None:
        for value in (self.city_id, self.coordinate_version):
            if (
                not isinstance(value, str)
                or len(value) > 64
                or not re.fullmatch(SLUG, value)
            ):
                raise ValueError("Identity must be a 1-64 character lowercase slug")
        if not isinstance(self.dataset, DatasetIdentity) or not isinstance(
            self.interval, HourlyInterval
        ):
            raise ValueError("Explicit dataset and UTC hourly interval required")
        if self.interval.end - self.interval.start > timedelta(days=MAX_QUERY_DAYS):
            raise ValueError("Query interval cannot exceed 31 days")
        if type(self.limit) is not int or not 1 <= self.limit <= 500:
            raise ValueError("Page size must be between 1 and 500")
        if self.after is not None:
            # Reuse domain UTC/hour validation without allocating a timeline.
            cursor = HourlyInterval(start=self.after, end=self.interval.end)
            if cursor.start < self.interval.start:
                raise ValueError("after must lie inside the requested interval")


def weather_statement(
    query: WeatherQuery, city_key: int, dataset_key: int
) -> sa.Select:
    """The primary-key prefix and timestamp support bounded keyset traversal."""
    statement = sa.select(
        weather_hourly.c.timestamp_utc,
        weather_hourly.c.temperature_c,
        weather_hourly.c.relative_humidity_pct,
        weather_hourly.c.precipitation_mm,
        weather_hourly.c.wind_speed_ms,
    ).where(
        weather_hourly.c.city_config_id == city_key,
        weather_hourly.c.dataset_key == dataset_key,
        weather_hourly.c.timestamp_utc >= query.interval.start,
        weather_hourly.c.timestamp_utc < query.interval.end,
    )
    if query.after is not None:
        statement = statement.where(weather_hourly.c.timestamp_utc > query.after)
    return statement.order_by(weather_hourly.c.timestamp_utc).limit(query.limit + 1)


@contextmanager
def read_connection(target: TestDatabaseConfig | None) -> Iterator[Connection]:
    """Verified ownership, read-only snapshot per request, always end and close."""
    if target is None:
        raise QueryUnavailable("Database is not configured")
    try:
        with test_connection(target) as connection, connection.begin():
            connection.execute(
                sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            connection.execute(sa.text("SET LOCAL statement_timeout = '5000'"))
            connection.execute(sa.text("SET LOCAL lock_timeout = '1000'"))
            yield connection
    except sa.exc.SQLAlchemyError, TestDatabaseError:
        raise QueryUnavailable("Database unavailable or incompatible") from None


class ReadQueries:
    """One short-lived verified connection per operation; no engine on import."""

    def __init__(self, target: TestDatabaseConfig | None = None):
        self.target = target

    def ready(self) -> None:
        with read_connection(self.target) as connection:
            revisions = (
                connection.execute(sa.text("SELECT version_num FROM alembic_version"))
                .scalars()
                .all()
            )
            if revisions != ["20261022_0001"]:
                raise QueryUnavailable("Database unavailable or incompatible")
            inspector = sa.inspect(connection)
            for table in metadata.sorted_tables:
                actual = {
                    c["name"]: c
                    for c in inspector.get_columns(table.name, schema="public")
                }
                for column in table.columns:
                    found = actual.get(column.name)
                    if (
                        found is None
                        or found["type"].compile(dialect=connection.dialect)
                        != column.type.compile(dialect=connection.dialect)
                        or found["nullable"] != column.nullable
                    ):
                        raise QueryUnavailable("Database unavailable or incompatible")
                connection.execute(sa.select(table).limit(0))

    def cities(self) -> dict:
        with read_connection(self.target) as connection:
            persisted = (
                connection.execute(
                    sa.select(
                        city_configs.c.city_id,
                        city_configs.c.coordinate_version,
                        city_configs.c.latitude,
                        city_configs.c.longitude,
                    ).order_by(
                        city_configs.c.city_id, city_configs.c.coordinate_version
                    )
                )
                .mappings()
                .all()
            )
            return {
                "supported": [
                    asdict(CITY_REGISTRY[key]) for key in sorted(CITY_REGISTRY)
                ],
                "persisted": [dict(row) for row in persisted],
            }

    def weather(self, query: WeatherQuery) -> dict:
        if query.city_id not in CITY_REGISTRY:
            raise QueryNotFound("Unknown city")
        with read_connection(self.target) as connection:
            city = (
                connection.execute(
                    sa.select(city_configs).where(
                        city_configs.c.city_id == query.city_id,
                        city_configs.c.coordinate_version == query.coordinate_version,
                    )
                )
                .mappings()
                .one_or_none()
            )
            dataset = (
                connection.execute(
                    sa.select(datasets).where(
                        datasets.c.provider_id == query.dataset.provider_id,
                        datasets.c.dataset_id == query.dataset.dataset_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if city is None or dataset is None:
                raise QueryNotFound("City configuration or dataset is not persisted")
            rows = (
                connection.execute(
                    weather_statement(
                        query, city["city_config_id"], dataset["dataset_key"]
                    )
                )
                .mappings()
                .all()
            )
            selected = rows[: query.limit]
            return {
                "city": {
                    key: city[key]
                    for key in (
                        "city_id",
                        "coordinate_version",
                        "latitude",
                        "longitude",
                    )
                },
                "dataset": {
                    key: dataset[key]
                    for key in (
                        "provider_id",
                        "dataset_id",
                        "historical_model",
                        "source_contract_version",
                    )
                },
                "units": {
                    field: dataset[column]
                    for field, column in (
                        ("temperature_c", "temperature_unit"),
                        ("relative_humidity_pct", "humidity_unit"),
                        ("precipitation_mm", "precipitation_unit"),
                        ("wind_speed_ms", "wind_speed_unit"),
                    )
                },
                "start": query.interval.start,
                "end": query.interval.end,
                "observations": [dict(row) for row in selected],
                "next_after": selected[-1]["timestamp_utc"]
                if len(rows) > query.limit
                else None,
            }
