"""Transaction ownership, safe errors and PostgreSQL SQL shape, all offline."""

from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import dialect

from hellas_dataops.storage import persistence as p
from hellas_dataops.storage.mapping import PersistenceError, request_values
from hellas_dataops.storage.schema import city_configs


@pytest.fixture
def connection(monkeypatch):
    connection = MagicMock()
    connection.get_isolation_level.return_value = "READ COMMITTED"
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr(p, "test_connection", lambda target: context)
    return connection


def test_transaction_commits_result(connection, monkeypatch, persistence_case):
    result = p.WriteResult(3, 0, 0, False, "succeeded")
    monkeypatch.setattr(p, "_write", lambda c, chunk: result)
    assert p.persist_chunk(object(), persistence_case()) == result
    connection.begin.return_value.__exit__.assert_called_once_with(None, None, None)


@pytest.mark.parametrize(
    "error,code",
    [
        (PersistenceError("measurement_conflict", "safe"), "measurement_conflict"),
        (
            sa.exc.OperationalError("private-sql", {}, Exception("secret")),
            "database_failure",
        ),
    ],
)
def test_failure_rolls_back_and_hides_driver_details(
    connection, monkeypatch, persistence_case, error, code
):
    def fail(*args):
        raise error

    monkeypatch.setattr(p, "_write", fail)
    with pytest.raises(PersistenceError) as caught:
        p.persist_chunk(object(), persistence_case())
    assert caught.value.code == code
    assert "chunk 1" in str(caught.value) and "secret" not in str(caught.value)
    assert connection.begin.return_value.__exit__.call_args.args[0] is type(error)


def test_commit_failure_is_not_success(connection, monkeypatch, persistence_case):
    monkeypatch.setattr(
        p, "_write", lambda *args: p.WriteResult(3, 0, 0, False, "succeeded")
    )
    connection.begin.return_value.__exit__.side_effect = sa.exc.OperationalError(
        "secret", {}, Exception()
    )
    with pytest.raises(PersistenceError, match="database_failure"):
        p.persist_chunk(object(), persistence_case())


def test_invalid_input_does_not_connect(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(p, "test_connection", connect)
    with pytest.raises(PersistenceError, match="invalid_chunk"):
        p.persist_chunk(object(), None)
    connect.assert_not_called()


def test_unsupported_isolation_rejected(connection, persistence_case):
    connection.get_isolation_level.return_value = "REPEATABLE READ"
    with pytest.raises(PersistenceError, match="isolation_level"):
        p.persist_chunk(object(), persistence_case())


@pytest.mark.parametrize(
    "exists,conflict", [(False, False), (True, False), (True, True)]
)
def test_identity_resolution_parameterized_and_locked(exists, conflict):
    conn = MagicMock()
    values = dict(
        city_id="athens",
        coordinate_version="gr-cities-v1",
        latitude=37.9838,
        longitude=23.7275,
    )
    stored = values | {"city_config_id": 1}
    if conflict:
        stored["latitude"] = 0
    result = conn.execute.return_value.mappings.return_value
    result.one_or_none.return_value = stored if exists else None
    result.one.return_value = stored
    if conflict:
        with pytest.raises(PersistenceError, match="identity_conflict"):
            p._identity(
                conn, city_configs, values, ("city_id", "coordinate_version"), lock=True
            )
    else:
        assert (
            p._identity(
                conn, city_configs, values, ("city_id", "coordinate_version"), lock=True
            )
            == stored
        )
    sql = [
        str(call.args[0].compile(dialect=dialect()))
        for call in conn.execute.call_args_list
    ]
    assert sql[0].endswith("FOR UPDATE") and "athens" not in sql[0]
    if not exists:
        assert "ON CONFLICT (city_id, coordinate_version) DO NOTHING" in sql[1]


@pytest.mark.parametrize(
    "defect,code",
    [
        ("failed_run", "run_state"),
        ("missing_chunks", "run_state"),
        ("wrong_count", "plan_conflict"),
        ("interval", "plan_conflict"),
        ("policy", "plan_conflict"),
    ],
)
def test_existing_run_contract_rejects_conflicts(
    monkeypatch, persistence_case, defect, code
):
    chunk = persistence_case()
    request = chunk.request
    stored = dict(
        run_id=chunk.run.run_id,
        chunk_index=1,
        retrieval_start=request.retrieval_interval.start,
        retrieval_end=request.retrieval_interval.end,
        retain_start=request.retain_interval.start,
        retain_end=request.retain_interval.end,
        request_provenance=request_values(request, chunk.run.policy),
    )
    status = "failed" if defect == "failed_run" else "running"
    monkeypatch.setattr(p, "_identity", lambda *a, **k: {"status": status})
    if defect == "interval":
        stored["retain_start"] = request.retrieval_interval.start
    if defect == "policy":
        stored["request_provenance"]["policy"] = "unknown"
    rows = [] if defect == "missing_chunks" else [stored]
    if defect == "wrong_count":
        rows += [stored]
    conn = MagicMock()
    conn.execute.return_value.mappings.return_value.all.return_value = rows
    with pytest.raises(PersistenceError) as caught:
        p._prepare_run(conn, chunk, 1, 2)
    assert caught.value.code == code
    assert all(
        isinstance(call.args[0], sa.sql.Select) for call in conn.execute.call_args_list
    )
