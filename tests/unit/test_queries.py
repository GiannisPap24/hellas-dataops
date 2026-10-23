"""Pure query validation and PostgreSQL compilation, never SQLite or a server."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from hellas_dataops.domain import HourlyInterval
from hellas_dataops.sources.open_meteo_requests import ERA5_DATASET
from hellas_dataops.storage import queries
from hellas_dataops.storage.schema import metadata


@pytest.fixture
def query():
    return queries.WeatherQuery(
        city_id="athens",
        coordinate_version="gr-cities-v1",
        dataset=ERA5_DATASET,
        interval=HourlyInterval(
            start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2025, 1, 2, tzinfo=UTC)
        ),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"city_id": "bad'"},
        {"coordinate_version": ""},
        {"city_id": None},
        {"coordinate_version": "a" * 65},
        {"dataset": None},
        {"interval": None},
        {"limit": True},
        {"limit": 501},
        {"limit": 0},
        {"after": datetime(2025, 1, 1)},
        {"after": datetime(2024, 12, 31, tzinfo=UTC)},
        {"after": datetime(2025, 1, 2, tzinfo=UTC)},
    ],
)
def test_programmatic_query_validation(query, changes):
    with pytest.raises(ValueError):
        replace(query, **changes)


def test_max_interval_and_limit_plus_one(query):
    end = query.interval.start + timedelta(days=31)
    valid = replace(
        query, interval=HourlyInterval(start=query.interval.start, end=end), limit=500
    )
    compiled = queries.weather_statement(valid, 7, 9).compile(
        dialect=postgresql.dialect()
    )
    assert 501 in compiled.params.values()
    assert "ORDER BY weather_hourly.timestamp_utc" in str(compiled)
    assert "OFFSET" not in str(compiled)
    assert str(valid.interval.start) not in str(compiled)
    with pytest.raises(ValueError):
        replace(
            valid,
            interval=HourlyInterval(
                start=query.interval.start, end=end + timedelta(hours=1)
            ),
        )


def test_cursor_compilation(query):
    query = replace(query, after=query.interval.start)
    statement = queries.weather_statement(query, 4, 5).compile(
        dialect=postgresql.dialect()
    )
    assert "timestamp_utc > " in str(statement)
    assert "timestamp_utc >= " in str(statement)
    assert "timestamp_utc < " in str(statement)
    assert 4 in statement.params.values() and 5 in statement.params.values()


def test_unknown_city_before_connection(query):
    with pytest.raises(queries.QueryNotFound):
        queries.ReadQueries().weather(replace(query, city_id="unknown"))


def test_unconfigured_connection():
    with pytest.raises(queries.QueryUnavailable), queries.read_connection(None):
        pytest.fail("yielded unconfigured connection")


def test_read_transaction_cleanup_and_safe_error(monkeypatch):
    connection = MagicMock()

    @contextmanager
    def opened(_):
        try:
            yield connection
        finally:
            connection.close()

    monkeypatch.setattr(queries, "test_connection", opened)
    with pytest.raises(queries.QueryUnavailable), queries.read_connection(object()):
        raise sa.exc.SQLAlchemyError("secret must not escape")
    statements = [str(c.args[0]) for c in connection.execute.call_args_list]
    assert "READ ONLY" in statements[0] and "REPEATABLE READ" in statements[0]
    assert "5000" in statements[1] and "1000" in statements[2]
    connection.close.assert_called_once()
    connection.begin.return_value.__exit__.assert_called_once()


@pytest.fixture
def connection_double(monkeypatch):
    connection = MagicMock()
    connection.dialect = postgresql.dialect()

    @contextmanager
    def opened(_):
        yield connection

    monkeypatch.setattr(queries, "read_connection", opened)
    return connection


@pytest.mark.parametrize("defect", [None, "revision", "missing", "type", "nullable"])
def test_readiness_contract(connection_double, monkeypatch, defect):
    connection_double.execute.return_value.scalars.return_value.all.return_value = [
        "wrong" if defect == "revision" else "20261022_0001"
    ]
    inspector = MagicMock()

    def columns(name, **kwargs):
        result = [
            dict(name=c.name, type=c.type, nullable=c.nullable)
            for c in metadata.tables[name].columns
        ]
        if defect == "missing":
            result.pop()
        elif defect == "type":
            result[0]["type"] = sa.Text()
        elif defect == "nullable":
            result[0]["nullable"] = not result[0]["nullable"]
        return result

    inspector.get_columns.side_effect = columns
    monkeypatch.setattr(queries.sa, "inspect", lambda _: inspector)
    if defect:
        with pytest.raises(queries.QueryUnavailable):
            queries.ReadQueries().ready()
    else:
        assert queries.ReadQueries().ready() is None


@pytest.mark.parametrize("missing", ["city", "dataset"])
def test_missing_identity_is_not_found(query, connection_double, missing):
    connection_double.execute.return_value.mappings.return_value.one_or_none.side_effect = [
        None if missing == "city" else {},
        None if missing == "dataset" else {},
    ]
    with pytest.raises(queries.QueryNotFound):
        queries.ReadQueries().weather(query)


@pytest.mark.parametrize(
    "count,limit,expected_cursor", [(0, 2, False), (2, 2, False), (3, 2, True)]
)
def test_page_construction_keeps_nulls_and_excludes_private_columns(
    query, connection_double, count, limit, expected_cursor
):
    city = dict(
        city_config_id=7,
        city_id="athens",
        coordinate_version="gr-cities-v1",
        latitude=37.9838,
        longitude=23.7275,
    )
    dataset = dict(
        dataset_key=8,
        provider_id="open-meteo",
        dataset_id="era5_hourly-v1",
        historical_model="era5",
        source_contract_version="open-meteo-request-v1",
        temperature_unit="celsius",
        humidity_unit="percent",
        precipitation_unit="mm",
        wind_speed_unit="m/s",
    )
    connection_double.execute.return_value.mappings.return_value.one_or_none.side_effect = [
        city,
        dataset,
    ]
    rows = [
        dict(
            timestamp_utc=query.interval.start + timedelta(hours=i),
            temperature_c=None,
            relative_humidity_pct=None,
            precipitation_mm=None,
            wind_speed_ms=None,
        )
        for i in range(count)
    ]
    connection_double.execute.return_value.mappings.return_value.all.return_value = rows
    page = queries.ReadQueries().weather(replace(query, limit=limit))
    assert page["observations"] == rows[:limit]
    assert page["next_after"] == (
        rows[limit - 1]["timestamp_utc"] if expected_cursor else None
    )
    assert "city_config_id" not in page["city"] and "dataset_key" not in page["dataset"]


def test_city_catalog_separates_supported_from_persisted(connection_double):
    connection_double.execute.return_value.mappings.return_value.all.return_value = []
    result = queries.ReadQueries().cities()
    assert len(result["supported"]) == 5 and result["persisted"] == []


def test_guarded_connection_failure_is_sanitized(monkeypatch):
    def fail(_):
        raise queries.TestDatabaseError("private")

    monkeypatch.setattr(queries, "test_connection", fail)
    with (
        pytest.raises(queries.QueryUnavailable, match="unavailable") as caught,
        queries.read_connection(object()),
    ):
        pytest.fail("failed connection was yielded")
    assert "private" not in str(caught.value)
