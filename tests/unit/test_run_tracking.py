"""Pure receipt validation and lease/transaction guards; no database emulation."""

from dataclasses import asdict
from datetime import timedelta
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import sqlalchemy as sa

from hellas_dataops.storage import run_tracking as t
from hellas_dataops.storage.mapping import (
    PersistenceError,
    metadata_values,
    request_values,
)


@pytest.fixture
def records(persistence_case):
    write = persistence_case()
    plan, request = write.run, write.request
    run = dict(
        run_id=plan.run_id,
        requested_start=plan.interval.start,
        requested_end=plan.interval.end,
        created_at=plan.created_at,
        status="succeeded",
        finished_at=write.completed_at,
    )
    city = asdict(plan.city)
    dataset = dict(
        provider_id=plan.dataset.provider_id,
        dataset_id=plan.dataset.dataset_id,
        historical_model="era5",
        source_contract_version=request.contract_version,
        temperature_unit="celsius",
        humidity_unit="percent",
        precipitation_unit="mm",
        wind_speed_unit="m/s",
    )
    chunk = dict(
        run_id=plan.run_id,
        chunk_index=1,
        retrieval_start=request.retrieval_interval.start,
        retrieval_end=request.retrieval_interval.end,
        retain_start=request.retain_interval.start,
        retain_end=request.retain_interval.end,
        status="succeeded",
        retrieved_at=write.retrieved_at,
        **metadata_values(write.metadata),
    )
    chunk["request_provenance"] = request_values(request, plan.policy) | {
        "operation": t.OPERATION,
        "failures": [],
        "receipt": dict(
            fingerprint=write.fingerprint,
            inserted=3,
            updated=0,
            unchanged=0,
            completed_at=write.completed_at.isoformat(),
            corrections=[],
        ),
    }
    return run, city, dataset, [chunk]


def test_progress_roundtrip(records, persistence_case):
    result = t._decode(*records)
    assert result.plan == persistence_case().run
    assert result.completed_chunks == 1 and result.failed_chunks == 0
    assert result.counts == (3, 0, 0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("fingerprint", "broken"),
        ("fingerprint", None),
        ("inserted", True),
        ("inserted", -1),
        ("inserted", 2),
        ("updated", 1),
        ("completed_at", "2026-01-01T00:02:00"),
        ("completed_at", "bad"),
        ("completed_at", "2020-01-01T00:00:00+00:00"),
        ("corrections", None),
        ("corrections", [{}]),
    ],
)
def test_bad_receipts_rejected(records, field, value):
    records[3][0]["request_provenance"]["receipt"][field] = value
    with pytest.raises(PersistenceError, match="invalid_progress"):
        t._decode(*records)


@pytest.mark.parametrize(
    "defect",
    [
        "coordinate",
        "dataset",
        "model",
        "policy",
        "missing_chunk",
        "extra_chunk",
        "index",
        "request",
        "operation",
        "interval",
        "run_id",
        "chunk_status",
        "receipt_pending",
        "missing_receipt",
        "run_status",
        "missing_finish",
        "wrong_finish",
        "naive_finish",
        "old_finish",
        "failed_run",
        "grid",
        "retrieved",
        "history",
        "history_stage",
        "history_time",
    ],
)
def test_corrupt_progress_rejected(records, defect):
    run, city, dataset, chunks = records
    chunk = chunks[0]
    provenance = chunk["request_provenance"]
    if defect == "coordinate":
        city["coordinate_version"] = "changed-v2"
    elif defect == "dataset":
        dataset["dataset_id"] = "other-v1"
    elif defect == "model":
        dataset["historical_model"] = "other"
    elif defect == "policy":
        provenance["policy"] = "unknown"
    elif defect == "missing_chunk":
        chunks.clear()
    elif defect == "extra_chunk":
        chunks.append(dict(chunk))
    elif defect == "index":
        chunk["chunk_index"] = 2
    elif defect == "request":
        provenance["request"] = {}
    elif defect == "operation":
        provenance.pop("operation")
    elif defect == "interval":
        chunk["retain_end"] += timedelta(hours=1)
    elif defect == "run_id":
        chunk["run_id"] = UUID(int=9)
    elif defect == "chunk_status":
        chunk["status"] = "running"
    elif defect == "receipt_pending":
        chunk["status"] = "planned"
    elif defect == "missing_receipt":
        provenance.pop("receipt")
    elif defect == "run_status":
        run["status"] = "unknown"
    elif defect == "missing_finish":
        run["finished_at"] = None
    elif defect == "wrong_finish":
        run["finished_at"] += timedelta(seconds=1)
    elif defect == "naive_finish":
        run["finished_at"] = run["finished_at"].replace(tzinfo=None)
    elif defect == "old_finish":
        run["finished_at"] = run["created_at"] - timedelta(seconds=1)
    elif defect == "failed_run":
        run["status"] = "failed"
    elif defect == "grid":
        chunk["grid_latitude"] = None
    elif defect == "retrieved":
        chunk["retrieved_at"] = None
    elif defect == "history":
        provenance["failures"] = None
    elif defect == "history_stage":
        provenance["failures"] = [{"stage": "bad", "at": run["created_at"].isoformat()}]
    elif defect == "history_time":
        provenance["failures"] = [
            {"stage": "persist", "at": "2020-01-01T00:00:00+00:00"}
        ]
    with pytest.raises(PersistenceError, match="invalid_progress"):
        t._decode(*records)


@pytest.mark.parametrize("status", ["planned", "running", "failed"])
def test_incomplete_progress(records, status):
    run, _, _, chunks = records
    run["status"] = status
    run["finished_at"] = run["finished_at"] if status == "failed" else None
    chunks[0]["status"] = "failed" if status == "failed" else "planned"
    chunks[0]["request_provenance"].pop("receipt")
    result = t._decode(*records)
    assert result.completed_chunks == 0 and result.counts == (0, 0, 0)
    assert result.failed_chunks == (status == "failed")


@pytest.mark.parametrize("run_id", [None, "uuid", UUID(int=0)])
def test_invalid_identity_never_connects(run_id):
    with (
        pytest.raises(PersistenceError, match="invalid_run"),
        t.run_session(None, run_id),
    ):
        pytest.fail("Invalid identity acquired lock")
    with pytest.raises(PersistenceError, match="invalid_run"):
        t.inspect_run(None, run_id)


@pytest.fixture
def connection(monkeypatch):
    connection = MagicMock()
    connection.closed = connection.invalidated = False
    connection.get_isolation_level.return_value = "READ COMMITTED"
    connection.scalar.return_value = True
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr(t, "test_connection", lambda _: context)
    return connection


def test_lease_ends_transaction_before_yield(connection):
    with t.run_session(None, UUID(int=1)) as session:
        assert session.connection is connection
        connection.rollback.assert_called_once()
        assert "pg_try_advisory_lock" in str(connection.scalar.call_args.args[0])
        assert type(connection.scalar.call_args.args[1]["key"]) is int


@pytest.mark.parametrize(
    "defect,code",
    [
        ("busy", "run_busy"),
        ("isolation", "isolation_level"),
        ("database", "outcome_unknown"),
    ],
)
def test_lease_rejections(connection, defect, code):
    if defect == "busy":
        connection.scalar.return_value = False
    elif defect == "isolation":
        connection.get_isolation_level.return_value = "REPEATABLE READ"
    else:
        connection.scalar.side_effect = sa.exc.OperationalError(
            "secret", {}, Exception()
        )
    with (
        pytest.raises(PersistenceError, match=code) as error,
        t.run_session(None, UUID(int=1)),
    ):
        pytest.fail("Acquired invalid lease")
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("flag", ["closed", "invalidated"])
def test_lost_lease_never_reconnects(connection, flag):
    setattr(connection, flag, True)
    with pytest.raises(PersistenceError, match="outcome_unknown"):
        t.RunSession(connection, UUID(int=1)).load()
    connection.begin.assert_not_called()


def test_session_write_commits_or_propagates(connection, monkeypatch, persistence_case):
    chunk = persistence_case()
    write = MagicMock(return_value="result")
    monkeypatch.setattr(t, "_write", write)
    session = t.RunSession(connection, chunk.run.run_id)
    assert session.write(chunk) == "result"
    connection.begin.return_value.__exit__.assert_called_once_with(None, None, None)
    with pytest.raises(PersistenceError, match="plan_conflict"):
        t.RunSession(connection, UUID(int=1)).write(chunk)
    with pytest.raises(PersistenceError, match="plan_conflict"):
        t.RunSession(connection, UUID(int=1)).create(chunk.run)


def test_status_error_sanitized(connection):
    connection.execute.side_effect = sa.exc.OperationalError("secret", {}, Exception())
    with pytest.raises(PersistenceError, match="status_unavailable") as error:
        t.inspect_run(None, UUID(int=1))
    assert "secret" not in str(error.value)


def test_missing_run(connection):
    connection.execute.return_value.mappings.return_value.one_or_none.return_value = (
        None
    )
    with pytest.raises(PersistenceError, match="run_not_found"):
        t.RunSession(connection, UUID(int=1)).load()


def test_invalid_failure_stage(connection):
    from datetime import UTC, datetime

    with pytest.raises(PersistenceError, match="invalid_failure"):
        t.RunSession(connection, UUID(int=1)).fail(1, "secret", datetime.now(UTC))
