"""Offline PostgreSQL metadata/DDL contracts, not server enforcement tests."""

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from hellas_dataops import storage


def checks(table):
    return {
        str(c.name): str(c.sqltext)
        for c in table.constraints
        if isinstance(c, sa.CheckConstraint)
    }


def foreign_keys(table):
    return {
        (tuple(c.columns.keys()), tuple(e.target_fullname for e in c.elements))
        for c in table.foreign_key_constraints
    }


def test_schema_inventory_and_exports():
    assert set(storage.metadata.tables) == {
        "city_configs",
        "datasets",
        "ingestion_runs",
        "ingestion_chunks",
        "weather_hourly",
    }
    assert all(
        getattr(storage, name) is table
        for name, table in storage.metadata.tables.items()
    )
    assert len(storage.metadata.sorted_tables) == 5


@pytest.mark.parametrize(
    "table, expected",
    [
        (storage.city_configs, ["city_config_id"]),
        (storage.datasets, ["dataset_key"]),
        (storage.ingestion_runs, ["run_id"]),
        (storage.ingestion_chunks, ["run_id", "chunk_index"]),
        (storage.weather_hourly, ["city_config_id", "dataset_key", "timestamp_utc"]),
    ],
)
def test_primary_keys_and_nonnull_identity(table, expected):
    assert list(table.primary_key.columns.keys()) == expected
    assert table.primary_key.name == f"pk_{table.name}"
    assert all(not c.nullable for c in table.primary_key.columns)


@pytest.mark.parametrize(
    "table, expected",
    [
        (storage.city_configs, {("city_id", "coordinate_version")}),
        (storage.datasets, {("provider_id", "dataset_id")}),
        (storage.ingestion_runs, {("run_id", "city_config_id", "dataset_key")}),
        (storage.ingestion_chunks, set()),
        (storage.weather_hourly, set()),
    ],
)
def test_unique_constraints_do_not_repeat_observation_primary_key(table, expected):
    assert {
        tuple(c.columns.keys())
        for c in table.constraints
        if isinstance(c, sa.UniqueConstraint)
    } == expected


def test_foreign_keys_bind_observation_identity_to_source_run_and_chunk():
    assert foreign_keys(storage.weather_hourly) == {
        (("city_config_id",), ("city_configs.city_config_id",)),
        (("dataset_key",), ("datasets.dataset_key",)),
        (
            ("source_run_id", "source_chunk_index"),
            ("ingestion_chunks.run_id", "ingestion_chunks.chunk_index"),
        ),
        (
            ("source_run_id", "city_config_id", "dataset_key"),
            (
                "ingestion_runs.run_id",
                "ingestion_runs.city_config_id",
                "ingestion_runs.dataset_key",
            ),
        ),
    }
    assert foreign_keys(storage.ingestion_chunks) == {
        (("run_id",), ("ingestion_runs.run_id",))
    }
    assert foreign_keys(storage.ingestion_runs) == {
        (("city_config_id",), ("city_configs.city_config_id",)),
        (("dataset_key",), ("datasets.dataset_key",)),
    }
    assert all(
        f.ondelete is None and f.onupdate is None
        for t in storage.metadata.tables.values()
        for f in t.foreign_key_constraints
    )


@pytest.mark.parametrize(
    "column",
    ["temperature_c", "relative_humidity_pct", "precipitation_mm", "wind_speed_ms"],
)
def test_measurements_are_nullable_floats_without_zero_defaults(column):
    field = storage.weather_hourly.c[column]
    assert isinstance(field.type, sa.Double)
    assert field.nullable and field.default is None and field.server_default is None


def test_physical_bounds_reject_infinities_and_postgresql_nan_in_generated_checks():
    constraints = checks(storage.weather_hourly)
    assert (
        constraints["ck_weather_hourly_temperature_bounds"]
        == "temperature_c >= -273.15 AND temperature_c < 'Infinity'::double precision"
    )
    assert (
        constraints["ck_weather_hourly_humidity_bounds"]
        == "relative_humidity_pct BETWEEN 0 AND 100"
    )
    assert (
        constraints["ck_weather_hourly_precipitation_bounds"]
        == "precipitation_mm >= 0 AND precipitation_mm < 'Infinity'::double precision"
    )
    assert (
        constraints["ck_weather_hourly_wind_bounds"]
        == "wind_speed_ms >= 0 AND wind_speed_ms < 'Infinity'::double precision"
    )
    # SQL compilation alone cannot demonstrate PostgreSQL NaN comparison semantics.


def test_requested_and_returned_coordinates_live_in_different_tables():
    assert {"latitude", "longitude", "coordinate_version"} <= set(
        storage.city_configs.c.keys()
    )
    assert "grid_latitude" not in storage.city_configs.c
    for name in ("grid_latitude", "grid_longitude", "grid_elevation_m"):
        assert storage.ingestion_chunks.c[name].nullable
        assert name not in storage.weather_hourly.c
    assert (
        "BETWEEN -90 AND 90"
        in checks(storage.city_configs)["ck_city_configs_latitude_bounds"]
    )
    assert (
        "BETWEEN -180 AND 180"
        in checks(storage.ingestion_chunks)["ck_ingestion_chunks_grid_longitude_bounds"]
    )
    assert (
        "'-Infinity'"
        in checks(storage.ingestion_chunks)["ck_ingestion_chunks_grid_elevation_finite"]
    )


def test_dataset_units_and_identifiers_are_explicit_without_provider_defaults():
    table = storage.datasets
    for name in (
        "provider_id",
        "dataset_id",
        "historical_model",
        "source_contract_version",
        "temperature_unit",
        "humidity_unit",
        "precipitation_unit",
        "wind_speed_unit",
    ):
        assert not table.c[name].nullable
        assert table.c[name].server_default is None
    assert checks(table)["ck_datasets_canonical_units"] == (
        "temperature_unit = 'celsius' AND humidity_unit = 'percent' AND "
        "precipitation_unit = 'mm' AND wind_speed_unit = 'm/s'"
    )
    assert all(
        f"ck_datasets_{name}_slug" in checks(table)
        for name in (
            "provider_id",
            "dataset_id",
            "historical_model",
            "source_contract_version",
        )
    )


def test_all_temporal_columns_are_timezone_aware():
    found = []
    for table in storage.metadata.tables.values():
        for column in table.c:
            if isinstance(column.type, sa.DateTime):
                found.append(f"{table.name}.{column.name}")
                assert column.type.timezone
                assert (
                    column.type.compile(dialect=postgresql.dialect())
                    == "TIMESTAMP WITH TIME ZONE"
                )
    assert len(found) == 10


@pytest.mark.parametrize(
    "table, column, unit",
    [
        (storage.weather_hourly, "timestamp_utc", "hour"),
        (storage.ingestion_runs, "requested_start", "hour"),
        (storage.ingestion_runs, "requested_end", "hour"),
        (storage.ingestion_chunks, "retrieval_start", "day"),
        (storage.ingestion_chunks, "retrieval_end", "day"),
        (storage.ingestion_chunks, "retain_start", "hour"),
        (storage.ingestion_chunks, "retain_end", "hour"),
    ],
)
def test_timestamp_alignment_is_explicitly_utc_not_session_timezone(
    table, column, unit
):
    sql = checks(table)[f"ck_{table.name}_{column}_{unit}"]
    assert sql == (
        f"isfinite({column}) AND date_trunc('{unit}', {column} AT TIME ZONE 'UTC') = {column} AT TIME ZONE 'UTC'"
    )


def test_nonempty_intervals_retention_and_fourteen_day_bound():
    runs, chunks = checks(storage.ingestion_runs), checks(storage.ingestion_chunks)
    assert (
        runs["ck_ingestion_runs_requested_order"] == "requested_start < requested_end"
    )
    assert chunks["ck_ingestion_chunks_retrieval_span"] == (
        "retrieval_start < retrieval_end AND retrieval_end - retrieval_start <= INTERVAL '336 hours'"
    )
    assert chunks["ck_ingestion_chunks_retained_bounds"] == (
        "retrieval_start <= retain_start AND retain_start < retain_end AND retain_end <= retrieval_end"
    )


def test_operational_status_and_metadata_constraints_are_separate_from_source_identity():
    for table in (storage.ingestion_runs, storage.ingestion_chunks):
        assert table.c.status.server_default.arg == "planned"
        assert (
            "'planned', 'running', 'succeeded', 'failed'"
            in checks(table)[f"ck_{table.name}_status"]
        )
    assert "status" not in storage.city_configs.c and "status" not in storage.datasets.c
    assert (
        "finished_at IS NOT NULL"
        in checks(storage.ingestion_runs)["ck_ingestion_runs_finished_status"]
    )
    assert (
        "grid_latitude IS NOT NULL"
        in checks(storage.ingestion_chunks)["ck_ingestion_chunks_success_metadata"]
    )
    assert (
        "source_timezone IS NOT NULL"
        in checks(storage.ingestion_chunks)["ck_ingestion_chunks_metadata_complete"]
    )
    assert isinstance(
        storage.ingestion_chunks.c.request_provenance.type, postgresql.JSONB
    )
    assert (
        checks(storage.ingestion_chunks)["ck_ingestion_chunks_request_object"]
        == "jsonb_typeof(request_provenance) = 'object'"
    )


def test_indexes_are_minimal_and_do_not_duplicate_keys():
    indexes = [
        (t.name, i.name, tuple(i.columns.keys()))
        for t in storage.metadata.tables.values()
        for i in t.indexes
    ]
    assert indexes == [
        (
            "weather_hourly",
            "ix_weather_hourly_source_chunk",
            ("source_run_id", "source_chunk_index"),
        )
    ]
    for table in storage.metadata.tables.values():
        keys = {
            tuple(c.columns.keys())
            for c in table.constraints
            if isinstance(c, (sa.PrimaryKeyConstraint, sa.UniqueConstraint))
        }
        assert all(tuple(i.columns.keys()) not in keys for i in table.indexes)


@pytest.mark.parametrize(
    "table", list(storage.metadata.sorted_tables), ids=lambda t: t.name
)
def test_postgresql_ddl_compiles_with_named_constraints(table):
    sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert f"CREATE TABLE {table.name}" in sql
    names = [str(c.name) for c in table.constraints]
    assert len(names) == len(set(names))
    assert all(name and len(name) <= 63 and name in sql for name in names)
    if table.name in ("city_configs", "datasets"):
        assert "GENERATED ALWAYS AS IDENTITY" in sql
    for index in table.indexes:
        assert index.name in str(
            CreateIndex(index).compile(dialect=postgresql.dialect())
        )


def test_schema_imports_do_not_create_engine_or_connect(monkeypatch):
    import runpy

    import sqlalchemy.engine.create

    def forbidden(*args, **kwargs):
        raise AssertionError("Database engine creation is forbidden in offline tests")

    monkeypatch.setattr(sa, "create_engine", forbidden)
    monkeypatch.setattr(sqlalchemy.engine.create, "create_engine", forbidden)
    # Import through the public storage boundary; the socket/DNS guard is also active.
    namespace = runpy.run_path(storage.schema.__file__)
    assert len(namespace["metadata"].tables) == 5
