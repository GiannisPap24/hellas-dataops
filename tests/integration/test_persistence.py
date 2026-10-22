"""Real committed transactions, competing connections and synthetic weather only."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event
from time import monotonic
from uuid import UUID

import pytest
import sqlalchemy as sa

from hellas_dataops.storage.mapping import PersistenceError, WritePolicy
from hellas_dataops.storage.persistence import WriteResult, persist_chunk
from hellas_dataops.storage.schema import (
    city_configs,
    datasets,
    ingestion_chunks,
    ingestion_runs,
    weather_hourly,
)
from hellas_dataops.storage.test_database import test_connection

TABLES = (weather_hourly, ingestion_chunks, ingestion_runs, city_configs, datasets)


def snapshot(target):
    with test_connection(target) as connection:
        return {
            table.name: [
                dict(row)
                for row in connection.execute(
                    sa.select(table).order_by(*table.primary_key.columns)
                ).mappings()
            ]
            for table in TABLES
        }


@pytest.fixture(autouse=True)
def own_records(database_target):
    """Every case starts empty; delete only this file's deterministic run identities."""
    assert all(not rows for rows in snapshot(database_target).values())
    yield
    run_ids = [UUID(int=i) for i in range(101, 121)]
    with test_connection(database_target) as connection, connection.begin():
        # Retain exact configuration keys before removing this test's runs.
        runs = (
            connection.execute(
                sa.select(ingestion_runs).where(ingestion_runs.c.run_id.in_(run_ids))
            )
            .mappings()
            .all()
        )
        city_keys = {r["city_config_id"] for r in runs}
        dataset_keys = {r["dataset_key"] for r in runs}
        connection.execute(
            weather_hourly.delete().where(weather_hourly.c.source_run_id.in_(run_ids))
        )
        connection.execute(
            ingestion_chunks.delete().where(ingestion_chunks.c.run_id.in_(run_ids))
        )
        connection.execute(
            ingestion_runs.delete().where(ingestion_runs.c.run_id.in_(run_ids))
        )
        connection.execute(
            city_configs.delete().where(city_configs.c.city_config_id.in_(city_keys))
        )
        connection.execute(
            datasets.delete().where(datasets.c.dataset_key.in_(dataset_keys))
        )
    assert all(not rows for rows in snapshot(database_target).values())


@pytest.mark.parametrize("hours", [1, 168, 336, 360])
def test_complete_runs_and_exact_replay(database_target, persistence_case, hours):
    first = persistence_case(hours=hours)
    total = 0
    for index in range(1, len(first.run.requests) + 1):
        chunk = persistence_case(hours=hours, index=index)
        result = persist_chunk(database_target, chunk)
        total += result.inserted
        assert result.updated == result.unchanged == 0
        assert result.run_status == (
            "succeeded" if index == len(first.run.requests) else "running"
        )
    assert total == hours
    before = snapshot(database_target)
    assert len(before["weather_hourly"]) == hours
    for index in range(1, len(first.run.requests) + 1):
        chunk = persistence_case(hours=hours, index=index)
        assert persist_chunk(database_target, chunk) == WriteResult(
            0, 0, len(chunk.observations), True, "succeeded"
        )
    assert snapshot(database_target) == before


def test_new_run_unchanged_preserves_original_lineage(
    database_target, persistence_case
):
    persist_chunk(database_target, persistence_case())
    before = snapshot(database_target)["weather_hourly"]
    result = persist_chunk(database_target, persistence_case(number=102, age=1))
    assert result == WriteResult(0, 0, 3, False, "succeeded")
    after = snapshot(database_target)
    assert after["weather_hourly"] == before
    assert len(after["ingestion_chunks"]) == 2


def test_partial_overlap_counts_and_boundaries(database_target, persistence_case):
    chunk = persistence_case()
    persist_chunk(database_target, chunk)
    later = persistence_case(
        number=102, start=chunk.run.interval.start + timedelta(hours=2)
    )
    assert persist_chunk(database_target, later) == WriteResult(
        2, 0, 1, False, "succeeded"
    )
    rows = snapshot(database_target)["weather_hourly"]
    assert len(rows) == 5
    assert rows[-1]["timestamp_utc"] == later.run.interval.end - timedelta(hours=1)


def test_mixed_insert_update_unchanged_counts(database_target, persistence_case):
    original = persistence_case()
    persist_chunk(database_target, original)
    next_chunk = persistence_case(
        number=102,
        age=1,
        policy=WritePolicy.REPLACE,
        start=original.run.interval.start + timedelta(hours=1),
    )
    rows = list(next_chunk.observations)
    rows[1] = replace(rows[1], temperature_c=11)
    next_chunk = replace(next_chunk, observations=rows)
    assert persist_chunk(database_target, next_chunk) == WriteResult(
        1, 1, 1, False, "succeeded"
    )
    state = snapshot(database_target)
    assert len(state["weather_hourly"]) == 4
    assert state["weather_hourly"][1]["source_run_id"] == original.run.run_id
    assert state["weather_hourly"][2]["source_run_id"] == next_chunk.run.run_id


def test_grid_metadata_change_does_not_rewrite_equal_weather(
    database_target, persistence_case
):
    persist_chunk(database_target, persistence_case())
    before = snapshot(database_target)["weather_hourly"]
    next_chunk = persistence_case(number=102, age=1)
    next_chunk = replace(
        next_chunk, metadata=replace(next_chunk.metadata, grid_latitude=38.5)
    )
    assert persist_chunk(database_target, next_chunk).unchanged == 3
    state = snapshot(database_target)
    assert state["weather_hourly"] == before
    assert state["ingestion_chunks"][-1]["grid_latitude"] == 38.5


def test_cities_are_separate_weather_series(database_target, persistence_case):
    for number, city in enumerate(
        ("athens", "thessaloniki", "patras", "heraklion", "larissa"), 101
    ):
        assert (
            persist_chunk(
                database_target, persistence_case(number=number, city=city)
            ).inserted
            == 3
        )
    state = snapshot(database_target)
    assert len(state["city_configs"]) == 5 and len(state["weather_hourly"]) == 15
    assert len(state["datasets"]) == 1


@pytest.mark.parametrize(
    "changed", ["values", "metadata", "retrieval", "policy", "interval", "created"]
)
def test_reused_run_rejects_changed_intent(database_target, persistence_case, changed):
    chunk = persistence_case()
    persist_chunk(database_target, chunk)
    before = snapshot(database_target)
    cases = {
        "values": persistence_case(temperature=11),
        "metadata": replace(chunk, metadata=replace(chunk.metadata, grid_latitude=39)),
        "retrieval": replace(
            chunk, retrieved_at=chunk.retrieved_at + timedelta(seconds=1)
        ),
        "policy": persistence_case(policy=WritePolicy.REPLACE),
        "interval": persistence_case(hours=4),
        "created": replace(
            chunk,
            run=replace(
                chunk.run, created_at=chunk.run.created_at - timedelta(seconds=1)
            ),
        ),
    }
    with pytest.raises(PersistenceError):
        persist_chunk(database_target, cases[changed])
    assert snapshot(database_target) == before


def test_conflict_rolls_back_earlier_insert_and_all_provenance(
    database_target, persistence_case
):
    chunk = persistence_case()
    persist_chunk(database_target, chunk)
    before = snapshot(database_target)
    conflict = persistence_case(
        number=102, start=chunk.run.interval.start - timedelta(hours=1), temperature=12
    )
    with pytest.raises(PersistenceError, match="measurement_conflict"):
        persist_chunk(database_target, conflict)
    assert snapshot(database_target) == before


@pytest.mark.parametrize("old,new", [(10.0, 11.0), (None, 10.0), (10.0, None)])
def test_explicit_correction_and_audit(database_target, persistence_case, old, new):
    original = persistence_case(temperature=old)
    persist_chunk(database_target, original)
    correction = persistence_case(
        number=102, age=1, policy=WritePolicy.REPLACE, temperature=new
    )
    assert persist_chunk(database_target, correction) == WriteResult(
        0, 3, 0, False, "succeeded"
    )
    state = snapshot(database_target)
    assert all(
        r["temperature_c"] == new and r["source_run_id"] == correction.run.run_id
        for r in state["weather_hourly"]
    )
    receipt = state["ingestion_chunks"][-1]["request_provenance"]["receipt"]
    assert receipt["updated"] == 3 and len(receipt["corrections"]) == 3
    assert all(
        r["measurements"]["temperature_c"] == old
        and r["source_run_id"] == str(original.run.run_id)
        for r in receipt["corrections"]
    )
    # Replaying an older completed chunk must never undo a later correction.
    persist_chunk(database_target, original)
    assert snapshot(database_target) == state


@pytest.mark.parametrize("age", [-1, 0])
def test_stale_or_equal_time_refresh_rejected(database_target, persistence_case, age):
    persist_chunk(database_target, persistence_case())
    before = snapshot(database_target)
    with pytest.raises(PersistenceError, match="stale_refresh"):
        persist_chunk(
            database_target,
            persistence_case(
                number=102, age=age, policy=WritePolicy.REPLACE, temperature=11
            ),
        )
    assert snapshot(database_target) == before


@pytest.mark.parametrize(
    "table,field,value",
    [(city_configs, "latitude", 0), (datasets, "historical_model", "other")],
)
def test_immutable_configuration_conflicts(
    database_target, persistence_case, table, field, value
):
    persist_chunk(database_target, persistence_case())
    with test_connection(database_target) as connection, connection.begin():
        connection.execute(table.update().values(**{field: value}))
    before = snapshot(database_target)
    with pytest.raises(PersistenceError, match="identity_conflict"):
        persist_chunk(database_target, persistence_case(number=102))
    assert snapshot(database_target) == before


def test_injected_late_failure_rolls_back_and_retry_succeeds(
    database_target, persistence_case
):
    first = persistence_case(hours=360)
    persist_chunk(database_target, first)
    before = snapshot(database_target)
    second = persistence_case(hours=360, index=2)

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE ingestion_chunks"):
            raise sa.exc.OperationalError("synthetic failure", {}, Exception("private"))

    sa.event.listen(sa.engine.Engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(PersistenceError, match="database_failure"):
            persist_chunk(database_target, second)
    finally:
        sa.event.remove(sa.engine.Engine, "before_cursor_execute", fail)
    assert snapshot(database_target) == before
    assert persist_chunk(database_target, second).run_status == "succeeded"


def test_out_of_order_chunks_finish_only_when_complete(
    database_target, persistence_case
):
    second = persistence_case(hours=360, index=2)
    assert persist_chunk(database_target, second).run_status == "running"
    first = persistence_case(hours=360)
    assert persist_chunk(database_target, first).run_status == "succeeded"
    assert len(snapshot(database_target)["weather_hourly"]) == 360


@pytest.mark.parametrize("scenario", ["new_run", "replay", "conflict", "stale_refresh"])
def test_competing_writers_block_then_return_exact_counts(
    database_target, persistence_case, scenario
):
    held, release = Event(), Event()
    first = persistence_case()
    second = persistence_case(number=101 if scenario == "replay" else 102)
    if scenario == "conflict":
        second = persistence_case(number=102, temperature=12)
    if scenario == "stale_refresh":
        persist_chunk(database_target, persistence_case(number=103))
        first = persistence_case(age=2, temperature=12, policy=WritePolicy.REPLACE)
        second = persistence_case(
            number=102, age=1, temperature=11, policy=WritePolicy.REPLACE
        )

    def pause(connection, cursor, statement, parameters, context, executemany):
        if (
            statement.startswith(
                ("INSERT INTO weather_hourly", "UPDATE weather_hourly")
            )
            and not held.is_set()
        ):
            held.set()
            if not release.wait(4):
                raise AssertionError("Writer coordination timed out")

    sa.event.listen(sa.engine.Engine, "before_cursor_execute", pause)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer1 = pool.submit(persist_chunk, database_target, first)
            assert held.wait(3), "First writer did not reach its transaction"
            writer2 = pool.submit(persist_chunk, database_target, second)
            try:
                # Observe an actual server lock wait, not just simultaneous thread submission.
                deadline = monotonic() + 3
                blocked = False
                with test_connection(database_target) as observer:
                    while monotonic() < deadline:
                        blocked = observer.scalar(
                            sa.text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = current_database() "
                                "AND pid <> pg_backend_pid() AND cardinality(pg_blocking_pids(pid)) > 0)"
                            )
                        )
                        observer.rollback()
                        if blocked:
                            break
                        release.wait(0.02)
                assert blocked and not writer2.done(), (
                    "Second writer did not wait on the city lock"
                )
            finally:
                release.set()
            assert writer1.result(timeout=5) == (
                WriteResult(0, 3, 0, False, "succeeded")
                if scenario == "stale_refresh"
                else WriteResult(3, 0, 0, False, "succeeded")
            )
            if scenario in ("conflict", "stale_refresh"):
                expected_code = (
                    "measurement_conflict"
                    if scenario == "conflict"
                    else "stale_refresh"
                )
                with pytest.raises(PersistenceError, match=expected_code):
                    writer2.result(timeout=5)
            else:
                assert writer2.result(timeout=5) == WriteResult(
                    0, 0, 3, scenario == "replay", "succeeded"
                )
    finally:
        release.set()
        sa.event.remove(sa.engine.Engine, "before_cursor_execute", pause)
    assert len(snapshot(database_target)["weather_hourly"]) == 3
