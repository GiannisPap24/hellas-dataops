"""Real PostgreSQL assertions. Synthetic rows; no application persistence service."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.dialects.postgresql import dialect

from hellas_dataops.storage import (
    city_configs,
    datasets,
    ingestion_chunks,
    ingestion_runs,
    metadata,
    weather_hourly,
)
from hellas_dataops.storage.test_database import TestDatabaseError as DatabaseError
from hellas_dataops.storage.test_database import require_empty_database, verify_database

START = datetime(2025, 1, 1, tzinfo=UTC)
END = START + timedelta(days=1)
RUN = UUID("00000000-0000-0000-0000-000000000001")
CITY = dict(
    city_id="athens",
    coordinate_version="gr-cities-v1",
    latitude=37.9838,
    longitude=23.7275,
)
DATASET = dict(
    provider_id="open-meteo",
    dataset_id="era5_hourly-v1",
    historical_model="era5",
    source_contract_version="open-meteo-request-v1",
    temperature_unit="celsius",
    humidity_unit="percent",
    precipitation_unit="mm",
    wind_speed_unit="m/s",
)


@pytest.fixture
def records(connection):
    city = connection.scalar(
        city_configs.insert().values(**CITY).returning(city_configs.c.city_config_id)
    )
    dataset = connection.scalar(
        datasets.insert().values(**DATASET).returning(datasets.c.dataset_key)
    )
    connection.execute(
        ingestion_runs.insert().values(
            run_id=RUN,
            city_config_id=city,
            dataset_key=dataset,
            requested_start=START,
            requested_end=END,
            created_at=START,
        )
    )
    connection.execute(
        ingestion_chunks.insert().values(
            run_id=RUN,
            chunk_index=1,
            retrieval_start=START,
            retrieval_end=END,
            retain_start=START,
            retain_end=END,
            request_provenance={"synthetic": True},
        )
    )
    return dict(
        city_config_id=city,
        dataset_key=dataset,
        timestamp_utc=START,
        source_run_id=RUN,
        source_chunk_index=1,
    )


def rejected(connection, statement, sqlstate):
    # pg8000 raises DatabaseError, not its unused IntegrityError subclass.
    # Require the exact integrity SQLSTATE below so unrelated failures cannot pass.
    with pytest.raises(sa.exc.DatabaseError) as caught:
        connection.execute(statement)
    # pg8000 exposes PostgreSQL ErrorResponse fields in the exception payload.
    fields = caught.value.orig.args[0]
    assert fields["C"] == sqlstate
    assert fields["n"]  # PostgreSQL constraint name, never parse localized text.


def test_upgrade_downgrade_reupgrade_and_catalog(connection):
    # Session fixture performs and checks the fresh upgrade / downgrade / re-upgrade.
    assert (
        connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
        == "20261022_0001"
    )
    inspector = sa.inspect(connection)
    assert set(inspector.get_table_names()) == set(metadata.tables) | {
        "alembic_version"
    }
    assert compare_metadata(MigrationContext.configure(connection), metadata) == []
    for table in metadata.tables.values():
        actual = {c["name"]: c for c in inspector.get_columns(table.name)}
        assert set(actual) == set(table.c.keys())
        for column in table.c:
            assert actual[column.name]["nullable"] == column.nullable
            assert actual[column.name]["type"].compile(
                dialect=dialect()
            ) == column.type.compile(dialect=dialect())
        assert inspector.get_pk_constraint(table.name)["constrained_columns"] == list(
            table.primary_key.columns.keys()
        )
        assert {c["name"] for c in inspector.get_check_constraints(table.name)} == {
            str(c.name) for c in table.constraints if isinstance(c, sa.CheckConstraint)
        }
        assert {
            (c["name"], tuple(c["column_names"]))
            for c in inspector.get_unique_constraints(table.name)
        } == {
            (str(c.name), tuple(c.columns.keys()))
            for c in table.constraints
            if isinstance(c, sa.UniqueConstraint)
        }
        assert {
            (
                tuple(f["constrained_columns"]),
                f["referred_table"],
                tuple(f["referred_columns"]),
            )
            for f in inspector.get_foreign_keys(table.name)
        } == {
            (
                tuple(f.columns.keys()),
                f.referred_table.name,
                tuple(e.column.name for e in f.elements),
            )
            for f in table.foreign_key_constraints
        }
        indexes = [
            i
            for i in inspector.get_indexes(table.name)
            if not i.get("duplicates_constraint")
        ]
        assert {(i["name"], tuple(i["column_names"])) for i in indexes} == {
            (i.name, tuple(i.columns.keys())) for i in table.indexes
        }


@pytest.mark.parametrize("table,values", [(city_configs, CITY), (datasets, DATASET)])
def test_configuration_unique_keys(connection, table, values):
    connection.execute(table.insert().values(**values))
    rejected(connection, table.insert().values(**values), "23505")


def test_coordinate_versioning_creates_separate_identity(connection, records):
    second = connection.scalar(
        city_configs.insert()
        .values(**(CITY | {"coordinate_version": "gr-cities-v2", "latitude": 38.0}))
        .returning(city_configs.c.city_config_id)
    )
    assert second != records["city_config_id"]


def test_duplicate_hour_rejected(connection, records):
    connection.execute(weather_hourly.insert().values(**records))
    rejected(connection, weather_hourly.insert().values(**records), "23505")


def test_null_measurements_remain_null(connection, records):
    connection.execute(weather_hourly.insert().values(**records))
    row = connection.execute(sa.select(weather_hourly)).mappings().one()
    assert all(
        row[c] is None
        for c in (
            "temperature_c",
            "relative_humidity_pct",
            "precipitation_mm",
            "wind_speed_ms",
        )
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("temperature_c", -273.16),
        ("relative_humidity_pct", -1),
        ("relative_humidity_pct", 101),
        ("precipitation_mm", -0.01),
        ("wind_speed_ms", -0.01),
        *(
            (field, number)
            for field in (
                "temperature_c",
                "relative_humidity_pct",
                "precipitation_mm",
                "wind_speed_ms",
            )
            for number in (float("nan"), float("inf"), -float("inf"))
        ),
    ],
)
def test_numeric_bounds(connection, records, field, value):
    rejected(
        connection, weather_hourly.insert().values(**records, **{field: value}), "23514"
    )


@pytest.mark.parametrize("temperature,humidity", [(-273.15, 0), (75, 100)])
def test_valid_measurement_boundaries(connection, records, temperature, humidity):
    connection.execute(
        weather_hourly.insert().values(
            **records,
            temperature_c=temperature,
            relative_humidity_pct=humidity,
            precipitation_mm=0,
            wind_speed_ms=0,
        )
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"city_config_id": 99999999},
        {"dataset_key": 99999999},
        {"source_run_id": UUID(int=99)},
        {"source_chunk_index": 2},
    ],
)
def test_weather_foreign_keys(connection, records, changes):
    rejected(connection, weather_hourly.insert().values(**(records | changes)), "23503")


@pytest.mark.parametrize("dimension", ["city", "dataset"])
def test_existing_but_wrong_run_identity_rejected(connection, records, dimension):
    if dimension == "city":
        value = connection.scalar(
            city_configs.insert()
            .values(**(CITY | {"coordinate_version": "gr-cities-v2"}))
            .returning(city_configs.c.city_config_id)
        )
        key = "city_config_id"
    else:
        value = connection.scalar(
            datasets.insert()
            .values(**(DATASET | {"dataset_id": "era5_hourly-v2"}))
            .returning(datasets.c.dataset_key)
        )
        key = "dataset_key"
    rejected(
        connection, weather_hourly.insert().values(**(records | {key: value})), "23503"
    )


@pytest.mark.parametrize(
    "table,changes",
    [
        (ingestion_runs, {"requested_end": START}),
        (ingestion_runs, {"requested_end": START - timedelta(hours=1)}),
        (ingestion_runs, {"requested_start": START + timedelta(minutes=1)}),
        (ingestion_chunks, {"retrieval_end": START}),
        (ingestion_chunks, {"retrieval_end": START + timedelta(days=15)}),
        (ingestion_chunks, {"retrieval_start": START + timedelta(hours=1)}),
        (ingestion_chunks, {"retain_end": START}),
        (ingestion_chunks, {"retain_end": END + timedelta(hours=1)}),
        (ingestion_chunks, {"retain_start": START + timedelta(minutes=30)}),
        (ingestion_chunks, {"chunk_index": 0}),
        (ingestion_chunks, {"request_provenance": []}),
        (ingestion_chunks, {"grid_latitude": 91}),
        (ingestion_chunks, {"status": "succeeded"}),
        (ingestion_runs, {"status": "succeeded"}),
    ],
)
def test_intervals_and_metadata_constraints(connection, records, table, changes):
    rejected(connection, table.update().values(**changes), "23514")


def test_non_hour_timestamp_rejected(connection, records):
    rejected(
        connection,
        weather_hourly.insert().values(
            **(records | {"timestamp_utc": START + timedelta(seconds=1)})
        ),
        "23514",
    )


@pytest.mark.parametrize(
    "session_timezone", ["UTC", "Europe/Athens", "America/New_York"]
)
def test_utc_round_trip(connection, records, session_timezone):
    connection.execute(
        sa.text("SELECT set_config('TimeZone', :zone, true)"),
        {"zone": session_timezone},
    )
    instant = datetime(2025, 10, 26, 1, tzinfo=UTC)
    # TIMESTAMPTZ accepts the equivalent nonzero-offset input. Domain rules are stricter.
    supplied = instant.astimezone(timezone(timedelta(hours=3)))
    connection.execute(
        weather_hourly.insert().values(**(records | {"timestamp_utc": supplied}))
    )
    returned = connection.scalar(sa.select(weather_hourly.c.timestamp_utc))
    assert returned.astimezone(UTC) == instant


def test_wrong_ownership_marker_rejected_before_writes(connection, database_target):
    with pytest.raises(DatabaseError, match="ownership mismatch"):
        verify_database(connection, replace(database_target, marker="0" * 64))


def test_migration_guard_refuses_populated_tables(connection, records):
    with pytest.raises(DatabaseError, match="empty application tables"):
        require_empty_database(connection)


def test_migration_guard_refuses_unknown_tables(connection):
    connection.execute(sa.text("CREATE TABLE unrelated_synthetic_table (id integer)"))
    with pytest.raises(DatabaseError, match="Unexpected relations"):
        require_empty_database(connection)


def test_pg8000_binding_and_session_settings(connection):
    assert connection.dialect.driver == "pg8000"
    value = "synthetic '; SELECT 1; -- Ελληνικά"
    assert (
        connection.scalar(sa.text("SELECT CAST(:value AS text)"), {"value": value})
        == value
    )
    for setting, expected in {
        "TimeZone": "UTC",
        "search_path": "public",
        "statement_timeout": "15s",
        "lock_timeout": "5s",
    }.items():
        assert (
            connection.scalar(
                sa.text("SELECT current_setting(:setting)"), {"setting": setting}
            )
            == expected
        )


def test_pg8000_savepoint_rollback(connection):
    with connection.begin_nested() as transaction:
        connection.execute(city_configs.insert().values(**CITY))
        assert (
            connection.scalar(sa.select(sa.func.count()).select_from(city_configs)) == 1
        )
        transaction.rollback()
    assert connection.scalar(sa.select(sa.func.count()).select_from(city_configs)) == 0
