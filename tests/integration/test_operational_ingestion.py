"""Opt-in real PostgreSQL lifecycle tests with synthetic source responses only."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa

from hellas_dataops.ingestion.coordinator import IngestionError, resume_run, start_run
from hellas_dataops.sources.cities import CITY_REGISTRY
from hellas_dataops.sources.open_meteo_client import OpenMeteoStatusError
from hellas_dataops.sources.synthetic import fetch_synthetic, synthetic_payload
from hellas_dataops.storage import run_tracking as tracking
from hellas_dataops.storage.mapping import PersistenceError, WritePolicy
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
    assert all(not rows for rows in snapshot(database_target).values())
    yield
    ids = [UUID(int=i) for i in range(201, 221)]
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
            city_configs.delete().where(city_configs.c.city_config_id.in_(city_keys))
        )
        connection.execute(
            datasets.delete().where(datasets.c.dataset_key.in_(dataset_keys))
        )
    assert all(not rows for rows in snapshot(database_target).values())


@pytest.fixture
def plan_case(persistence_case):
    return lambda **kwargs: persistence_case(number=201, **kwargs).run


def clock(plan):
    return lambda: plan.created_at + timedelta(minutes=1)


@pytest.mark.parametrize(
    "start,hours",
    [
        ("2025-01-01T05:00", 1),
        ("2025-01-01", 168),
        ("2025-01-01", 336),
        ("2025-01-01", 360),
        ("2025-01-01T05:00", 358),
        ("2024-02-28", 72),
        ("2024-12-31T23:00", 27),
        ("2025-03-29", 72),
        ("2025-10-25", 72),
    ],
)
def test_sequential_commits_and_durable_counts(
    database_target, plan_case, start, hours
):
    plan = plan_case(
        start=datetime.fromisoformat(start).replace(tzinfo=UTC), hours=hours
    )

    def fetch(request):
        state = tracking.inspect_run(database_target, plan.run_id)
        assert state.status == "running"
        assert state.completed_chunks == plan.requests.index(request)
        # The active coordinator session must be idle, not idle-in-transaction.
        with test_connection(database_target) as connection:
            idle_tx = connection.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
                )
            )
            assert idle_tx == 0
        return fetch_synthetic(request)

    result = start_run(database_target, plan, fetch=fetch, clock=clock(plan))
    assert result.status == "succeeded" and result.counts == (hours, 0, 0)
    assert result.completed_chunks == len(plan.requests)
    state = snapshot(database_target)
    assert len(state["weather_hourly"]) == hours
    assert all(row["relative_humidity_pct"] is None for row in state["weather_hourly"])
    assert all(
        row["request_provenance"]["operation"] == tracking.OPERATION
        for row in state["ingestion_chunks"]
    )
    assert tracking.inspect_run(database_target, plan.run_id) == result

    def forbidden(_):
        pytest.fail("Completed run was retrieved again")

    assert start_run(database_target, plan, fetch=forbidden) == result
    assert resume_run(database_target, plan.run_id, fetch=forbidden) == result
    assert snapshot(database_target) == state


@pytest.mark.parametrize("index", [1, 2])
def test_failed_chunk_resume_preserves_committed_lineage(
    database_target, plan_case, index
):
    plan = plan_case(hours=360)

    def fetch(request):
        if plan.requests.index(request) + 1 == index:
            raise OpenMeteoStatusError(429, "1")
        return fetch_synthetic(request)

    with pytest.raises(IngestionError) as caught:
        start_run(database_target, plan, fetch=fetch, clock=clock(plan))
    assert not caught.value.outcome_unknown
    before = snapshot(database_target)
    result = tracking.inspect_run(database_target, plan.run_id)
    assert result.status == "failed" and result.failed_chunks == 1
    assert result.completed_chunks == index - 1
    with pytest.raises(PersistenceError, match="resume_required"):
        start_run(database_target, plan, fetch=fetch_synthetic)
    calls = []

    def resumed(request):
        calls.append(request)
        return fetch_synthetic(request)

    result = resume_run(
        database_target, plan.run_id, expected=plan, fetch=resumed, clock=clock(plan)
    )
    assert result.status == "succeeded" and result.counts == (360, 0, 0)
    assert calls == list(plan.requests[index - 1 :])
    after = snapshot(database_target)
    assert (
        after["weather_hourly"][: len(before["weather_hourly"])]
        == before["weather_hourly"]
    )
    if index == 2:
        assert after["ingestion_chunks"][0] == before["ingestion_chunks"][0]
    assert after["ingestion_chunks"][index - 1]["request_provenance"]["failures"] == [
        {"stage": "retrieve", "at": clock(plan)().isoformat()}
    ]


def test_new_run_unchanged_and_explicit_correction(database_target, plan_case):
    plan = plan_case()
    start_run(database_target, plan, fetch=fetch_synthetic, clock=clock(plan))
    original = snapshot(database_target)["weather_hourly"]
    replay = replace(
        plan, run_id=UUID(int=202), created_at=plan.created_at + timedelta(hours=1)
    )
    result = start_run(
        database_target, replay, fetch=fetch_synthetic, clock=clock(replay)
    )
    assert result.counts == (0, 0, 3)
    assert snapshot(database_target)["weather_hourly"] == original

    def changed(request):
        payload = synthetic_payload(request)
        payload["hourly"]["temperature_2m"] = [11.0] * len(payload["hourly"]["time"])
        return payload

    rejected = replace(replay, run_id=UUID(int=203))
    with pytest.raises(IngestionError):
        start_run(database_target, rejected, fetch=changed, clock=clock(rejected))
    assert snapshot(database_target)["weather_hourly"] == original
    correction = replace(replay, run_id=UUID(int=204), policy=WritePolicy.REPLACE)
    assert start_run(
        database_target, correction, fetch=changed, clock=clock(correction)
    ).counts == (0, 3, 0)
    rows = snapshot(database_target)
    receipt = rows["ingestion_chunks"][-1]["request_provenance"]["receipt"]
    assert len(receipt["corrections"]) == 3
    assert all(
        item["source_run_id"] == str(plan.run_id)
        and item["measurements"]["temperature_c"] == 10
        for item in receipt["corrections"]
    )
    # Resuming an old completed run never restores pre-correction values.
    resume_run(database_target, plan.run_id, fetch=fetch_synthetic)
    assert snapshot(database_target) == rows


def test_failure_before_commit_rolls_back_and_recovers(
    database_target, plan_case, monkeypatch
):
    plan = plan_case(hours=360)
    write = tracking._write

    def fail(connection, chunk):
        result = write(connection, chunk)
        if chunk.chunk_index == 2:
            raise RuntimeError("Injected before context-manager commit")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(tracking, "_write", fail)
        with pytest.raises(IngestionError):
            start_run(database_target, plan, fetch=fetch_synthetic, clock=clock(plan))
    before = snapshot(database_target)
    assert (
        len(before["weather_hourly"]) == plan.requests[0].retain_interval.expected_hours
    )
    assert "receipt" not in before["ingestion_chunks"][1]["request_provenance"]
    assert tracking.inspect_run(database_target, plan.run_id).status == "failed"
    assert resume_run(
        database_target, plan.run_id, fetch=fetch_synthetic, clock=clock(plan)
    ).counts == (360, 0, 0)


def test_simulated_lost_ack_reconciles_without_fetch_retry(
    database_target, plan_case, monkeypatch
):
    plan = plan_case(hours=360)
    write = tracking.RunSession.write

    def lost_ack(session, chunk):
        write(
            session, chunk
        )  # Real commit; simulation is only the caller's acknowledgement.
        raise RuntimeError("Simulated acknowledgement loss, not a killed process")

    monkeypatch.setattr(tracking.RunSession, "write", lost_ack)
    calls = []

    def fetch(request):
        calls.append(request)
        return fetch_synthetic(request)

    assert start_run(database_target, plan, fetch=fetch, clock=clock(plan)).counts == (
        360,
        0,
        0,
    )
    assert calls == list(plan.requests)


def test_interruption_leaves_running_then_explicit_resume(database_target, plan_case):
    plan = plan_case(hours=360)

    def interrupted(request):
        if request == plan.requests[1]:
            raise KeyboardInterrupt
        return fetch_synthetic(request)

    with pytest.raises(KeyboardInterrupt):
        start_run(database_target, plan, fetch=interrupted, clock=clock(plan))
    state = tracking.inspect_run(database_target, plan.run_id)
    assert state.status == "running" and state.completed_chunks == 1
    assert state.finished_at is None
    assert (
        resume_run(
            database_target, plan.run_id, fetch=fetch_synthetic, clock=clock(plan)
        ).status
        == "succeeded"
    )


def test_competing_coordinators_rejected_and_lock_released(database_target, plan_case):
    plan = plan_case()
    with tracking.run_session(database_target, plan.run_id) as owner:
        owner.create(plan)
        for action in (
            lambda: start_run(database_target, plan, fetch=fetch_synthetic),
            lambda: resume_run(database_target, plan.run_id, fetch=fetch_synthetic),
        ):
            with pytest.raises(PersistenceError, match="run_busy"):
                action()
        assert tracking.inspect_run(database_target, plan.run_id).status == "running"
    assert (
        resume_run(
            database_target, plan.run_id, fetch=fetch_synthetic, clock=clock(plan)
        ).status
        == "succeeded"
    )


@pytest.mark.parametrize("defect", ["city", "interval", "policy", "created"])
def test_conflicting_plan_rejected_without_changes(database_target, plan_case, defect):
    plan = plan_case()
    with tracking.run_session(database_target, plan.run_id) as session:
        session.create(plan)
    changed = {
        "city": lambda: replace(plan, city=CITY_REGISTRY["patras"]),
        "interval": lambda: replace(
            plan,
            interval=replace(plan.interval, end=plan.interval.end + timedelta(hours=1)),
        ),
        "policy": lambda: replace(plan, policy=WritePolicy.REPLACE),
        "created": lambda: replace(
            plan, created_at=plan.created_at + timedelta(seconds=1)
        ),
    }[defect]()
    before = snapshot(database_target)
    with pytest.raises(PersistenceError, match="plan_conflict"):
        resume_run(
            database_target, plan.run_id, expected=changed, fetch=fetch_synthetic
        )
    assert snapshot(database_target) == before


@pytest.mark.parametrize("defect", ["receipt", "weather", "coordinate", "dataset"])
def test_corrupt_persisted_progress_cannot_skip(database_target, plan_case, defect):
    plan = plan_case()
    start_run(database_target, plan, fetch=fetch_synthetic, clock=clock(plan))
    with test_connection(database_target) as connection, connection.begin():
        if defect == "receipt":
            row = connection.scalar(
                sa.select(ingestion_chunks.c.request_provenance).where(
                    ingestion_chunks.c.run_id == plan.run_id
                )
            )
            row["receipt"]["inserted"] = 0
            connection.execute(
                ingestion_chunks.update()
                .where(ingestion_chunks.c.run_id == plan.run_id)
                .values(request_provenance=row)
            )
        elif defect == "weather":
            connection.execute(
                weather_hourly.delete().where(
                    weather_hourly.c.source_run_id == plan.run_id
                )
            )
        elif defect == "coordinate":
            connection.execute(
                city_configs.update()
                .where(city_configs.c.city_id == "athens")
                .values(latitude=37.0)
            )
        else:
            connection.execute(
                datasets.update()
                .where(datasets.c.provider_id == "open-meteo")
                .values(source_contract_version="changed-v2")
            )
    with pytest.raises(PersistenceError, match="invalid_progress"):
        resume_run(database_target, plan.run_id, fetch=fetch_synthetic)


def test_unknown_run_does_not_create_records(database_target):
    with pytest.raises(PersistenceError, match="run_not_found"):
        resume_run(database_target, UUID(int=201), fetch=fetch_synthetic)
    assert all(not rows for rows in snapshot(database_target).values())


def test_lost_session_requires_explicit_resume(database_target, plan_case, monkeypatch):
    plan = plan_case()
    create = tracking.RunSession.create
    active = []

    def capture(session, plan):
        active.append(session)
        return create(session, plan)

    monkeypatch.setattr(tracking.RunSession, "create", capture)

    def lose_connection(request):
        active[0].connection.invalidate()
        return fetch_synthetic(request)

    with pytest.raises(IngestionError) as error:
        start_run(database_target, plan, fetch=lose_connection, clock=clock(plan))
    assert error.value.outcome_unknown
    state = tracking.inspect_run(database_target, plan.run_id)
    assert state.status == "running" and state.counts == (0, 0, 0)
    assert (
        resume_run(
            database_target, plan.run_id, fetch=fetch_synthetic, clock=clock(plan)
        ).status
        == "succeeded"
    )
