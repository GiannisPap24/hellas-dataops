"""Opt-in real PostgreSQL API tests using accepted persistence and synthetic rows."""

from dataclasses import replace
from uuid import UUID

import pytest
import sqlalchemy as sa

from hellas_dataops.api.app import create_app
from hellas_dataops.storage.mapping import WritePolicy
from hellas_dataops.storage.persistence import persist_chunk
from hellas_dataops.storage.queries import QueryUnavailable, read_connection
from hellas_dataops.storage.schema import (
    city_configs,
    datasets,
    ingestion_chunks,
    ingestion_runs,
    weather_hourly,
)
from hellas_dataops.storage.test_database import test_connection

TABLES = (weather_hourly, ingestion_chunks, ingestion_runs, city_configs, datasets)
PARAMS = dict(
    city_id="athens",
    coordinate_version="gr-cities-v1",
    provider_id="open-meteo",
    dataset_id="era5_hourly-v1",
    start="2025-01-01T05:00:00Z",
    end="2025-01-01T10:00:00Z",
)


def snapshot(target):
    with test_connection(target) as connection:
        return {
            table.name: [
                dict(r)
                for r in connection.execute(
                    sa.select(table).order_by(*table.primary_key.columns)
                ).mappings()
            ]
            for table in TABLES
        }


@pytest.fixture
def populated(database_target, persistence_case):
    assert all(not rows for rows in snapshot(database_target).values())
    try:
        persist_chunk(database_target, persistence_case(number=301, hours=5))
        persist_chunk(
            database_target,
            persistence_case(number=302, city="patras", hours=2, temperature=21),
        )
        # Additional synthetic stored identities: existing writer intentionally only
        # accepts the current registry/source contract. These catalog fixtures test
        # reader isolation across legitimate historical versions, without changing it.
        with test_connection(database_target) as connection, connection.begin():
            city_key = connection.scalar(
                city_configs.insert()
                .values(
                    city_id="athens",
                    coordinate_version="gr-cities-v2",
                    latitude=38,
                    longitude=24,
                )
                .returning(city_configs.c.city_config_id)
            )
            old_dataset = dict(connection.execute(sa.select(datasets)).mappings().one())
            old_dataset.pop("dataset_key")
            dataset_key = connection.scalar(
                datasets.insert()
                .values(**{**old_dataset, "dataset_id": "era5_hourly-v2"})
                .returning(datasets.c.dataset_key)
            )
            run = dict(
                connection.execute(
                    sa.select(ingestion_runs).where(
                        ingestion_runs.c.run_id == UUID(int=301)
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                ingestion_runs.insert().values(
                    **{
                        **run,
                        "run_id": UUID(int=303),
                        "city_config_id": city_key,
                        "dataset_key": dataset_key,
                    }
                )
            )
            chunk = dict(
                connection.execute(
                    sa.select(ingestion_chunks).where(
                        ingestion_chunks.c.run_id == UUID(int=301)
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                ingestion_chunks.insert().values(**{**chunk, "run_id": UUID(int=303)})
            )
            rows = (
                connection.execute(
                    sa.select(weather_hourly).where(
                        weather_hourly.c.source_run_id == UUID(int=301)
                    )
                )
                .mappings()
                .all()
            )
            connection.execute(
                weather_hourly.insert(),
                [
                    {
                        **row,
                        "city_config_id": city_key,
                        "dataset_key": dataset_key,
                        "source_run_id": UUID(int=303),
                        "temperature_c": 42,
                    }
                    for row in rows
                ],
            )
        yield database_target
    finally:
        ids = [UUID(int=n) for n in range(301, 305)]
        with test_connection(database_target) as connection, connection.begin():
            runs = (
                connection.execute(
                    sa.select(ingestion_runs).where(ingestion_runs.c.run_id.in_(ids))
                )
                .mappings()
                .all()
            )
            city_keys = {r["city_config_id"] for r in runs}
            dataset_keys = {r["dataset_key"] for r in runs}
            connection.execute(
                weather_hourly.delete().where(weather_hourly.c.source_run_id.in_(ids))
            )
            connection.execute(
                ingestion_chunks.delete().where(ingestion_chunks.c.run_id.in_(ids))
            )
            connection.execute(
                ingestion_runs.delete().where(ingestion_runs.c.run_id.in_(ids))
            )
            connection.execute(
                city_configs.delete().where(
                    city_configs.c.city_config_id.in_(city_keys)
                )
            )
            connection.execute(
                datasets.delete().where(datasets.c.dataset_key.in_(dataset_keys))
            )
        assert all(not rows for rows in snapshot(database_target).values())


@pytest.fixture
def client(populated, api_client):
    before = snapshot(populated)
    with api_client(create_app(populated)) as client:
        yield client
    # All ordinary GET tests must leave observations AND operational state intact.
    assert snapshot(populated) == before


def test_health_and_city_versions(client):
    assert client.get("/health/live").json() == {"status": "alive"}
    assert client.get("/health/ready").json() == {"status": "ready"}
    cities = client.get("/v1/cities").json()
    assert len(cities["supported"]) == 5 and len(cities["persisted"]) == 3
    assert [(c["city_id"], c["coordinate_version"]) for c in cities["persisted"]] == [
        ("athens", "gr-cities-v1"),
        ("athens", "gr-cities-v2"),
        ("patras", "gr-cities-v1"),
    ]


@pytest.mark.parametrize("limit", [1, 2, 5, 500])
def test_page_traversal_no_duplicates_or_omissions(client, limit):
    params = {**PARAMS, "limit": limit}
    times = []
    for _ in range(6):
        response = client.get("/v1/weather", params=params)
        assert response.status_code == 200
        page = response.json()
        assert len(page["observations"]) <= limit
        times.extend(r["timestamp_utc"] for r in page["observations"])
        assert all(r["relative_humidity_pct"] is None for r in page["observations"])
        assert all(r["temperature_c"] == 10 for r in page["observations"])
        if page["next_after"] is None:
            break
        params["after"] = page["next_after"]
    assert times == [f"2025-01-01T{hour:02d}:00:00Z" for hour in range(5, 10)]


@pytest.mark.parametrize(
    "changes,count,temp",
    [
        ({"start": "2025-01-01T06:00:00Z", "end": "2025-01-01T08:00:00Z"}, 2, 10),
        ({"start": "2025-01-01T10:00:00Z", "end": "2025-01-01T11:00:00Z"}, 0, 10),
        ({"after": "2025-01-01T09:00:00Z"}, 0, 10),
        ({"city_id": "patras"}, 2, 21),
        ({"coordinate_version": "gr-cities-v2"}, 0, 10),
        ({"dataset_id": "era5_hourly-v2"}, 0, 10),
        ({"dataset_id": "era5_hourly-v2", "coordinate_version": "gr-cities-v2"}, 5, 42),
    ],
)
def test_filtering_and_identity_isolation(client, changes, count, temp):
    result = client.get("/v1/weather", params={**PARAMS, **changes})
    assert result.status_code == 200
    assert len(result.json()["observations"]) == count
    assert all(r["temperature_c"] == temp for r in result.json()["observations"])
    assert result.json()["next_after"] is None


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"city_id": "unknown"}, 404),
        ({"city_id": "larissa"}, 404),
        ({"coordinate_version": "unknown"}, 404),
        ({"dataset_id": "unknown"}, 404),
        ({"provider_id": "unknown"}, 404),
        ({"city_id": "athens' OR 1=1--"}, 422),
        ({"coordinate_version": "gr-cities-v1'; DROP TABLE datasets;--"}, 422),
    ],
)
def test_unknown_and_unsafe_identity(client, changes, status):
    assert client.get("/v1/weather", params={**PARAMS, **changes}).status_code == status


def test_read_only_transaction_enforced(populated):
    with pytest.raises(QueryUnavailable), read_connection(populated) as connection:
        assert connection.scalar(sa.text("SHOW transaction_read_only")) == "on"
        assert (
            connection.scalar(sa.text("SHOW transaction_isolation"))
            == "repeatable read"
        )
        # No rows can change, even if a future accidental statement is added.
        connection.execute(weather_hourly.update().values(temperature_c=999))
    assert all(r["temperature_c"] != 999 for r in snapshot(populated)["weather_hourly"])


@pytest.mark.parametrize("field", ["marker", "password"])
def test_wrong_server_identity_is_unready(populated, api_client, field):
    target = replace(populated, **{field: "0" * 64})
    with api_client(create_app(target)) as client:
        for path in ("/health/ready", "/v1/cities"):
            response = client.get(path)
            assert response.status_code == 503 and target.marker not in response.text
        assert client.get("/health/live").status_code == 200


def test_schema_revision_mismatch_is_unready(populated, api_client):
    try:
        with test_connection(populated) as connection, connection.begin():
            connection.execute(
                sa.text(
                    "UPDATE alembic_version SET version_num = 'synthetic-incompatible'"
                )
            )
        with api_client(create_app(populated)) as client:
            assert client.get("/health/ready").status_code == 503
    finally:
        with test_connection(populated) as connection, connection.begin():
            connection.execute(
                sa.text("UPDATE alembic_version SET version_num = '20261022_0001'")
            )


def test_corrections_between_pages_are_current_not_snapshot(
    populated, persistence_case, api_client
):
    with api_client(create_app(populated)) as client:
        first = client.get("/v1/weather", params={**PARAMS, "limit": 2}).json()
        persist_chunk(
            populated,
            persistence_case(
                number=304, hours=5, age=1, temperature=12, policy=WritePolicy.REPLACE
            ),
        )
        second = client.get(
            "/v1/weather", params={**PARAMS, "after": first["next_after"]}
        ).json()
        assert [r["temperature_c"] for r in first["observations"]] == [10, 10]
        assert [r["temperature_c"] for r in second["observations"]] == [12, 12, 12]
        assert second["observations"][0]["timestamp_utc"] == "2025-01-01T07:00:00Z"
