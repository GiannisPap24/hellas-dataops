"""Real source components with an explicit in-memory lifecycle test double.

This double tests orchestration, not PostgreSQL transaction guarantees.
"""

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from hellas_dataops.ingestion import __main__ as cli
from hellas_dataops.ingestion import coordinator as c
from hellas_dataops.sources.open_meteo_client import OpenMeteoStatusError
from hellas_dataops.sources.synthetic import fetch_synthetic, synthetic_payload
from hellas_dataops.storage.mapping import PersistenceError
from hellas_dataops.storage.run_tracking import ChunkProgress, RunProgress


class MemorySession:
    def __init__(self, plan):
        self.state = RunProgress(
            plan,
            "running",
            None,
            tuple(
                ChunkProgress(i, "planned") for i in range(1, len(plan.requests) + 1)
            ),
        )
        self.events = []
        self.writes = []

    def create(self, plan):
        assert plan == self.state.plan
        return self.state

    def resume(self, expected):
        if expected is not None and expected != self.state.plan:
            raise PersistenceError("plan_conflict", "Mismatch")
        if self.state.status != "succeeded":
            self.state = replace(
                self.state,
                status="running",
                finished_at=None,
                chunks=tuple(
                    replace(chunk, status="planned")
                    if chunk.status == "failed"
                    else chunk
                    for chunk in self.state.chunks
                ),
            )
        return self.state

    def load(self):
        return self.state

    def write(self, chunk):
        self.events.append(("write", chunk.chunk_index))
        self.writes.append(chunk)
        chunks = list(self.state.chunks)
        chunks[chunk.chunk_index - 1] = ChunkProgress(
            chunk.chunk_index,
            "succeeded",
            inserted=len(chunk.observations),
            fingerprint=chunk.fingerprint,
        )
        done = all(item.status == "succeeded" for item in chunks)
        self.state = replace(
            self.state,
            chunks=tuple(chunks),
            status="succeeded" if done else "running",
            finished_at=chunk.completed_at if done else None,
        )

    def fail(self, index, stage, when):
        chunks = list(self.state.chunks)
        chunks[index - 1] = ChunkProgress(index, "failed")
        self.state = replace(
            self.state, chunks=tuple(chunks), status="failed", finished_at=when
        )


@pytest.fixture
def setup(monkeypatch, persistence_case):
    def make(**kwargs):
        plan = persistence_case(**kwargs).run
        session = MemorySession(plan)

        @contextmanager
        def opened(target, run_id):
            assert run_id == plan.run_id
            yield session

        monkeypatch.setattr(c, "run_session", opened)
        return plan, session, lambda: plan.created_at + timedelta(minutes=1)

    return make


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
def test_sequential_boundaries_and_nulls(setup, start, hours):
    plan, session, clock = setup(
        start=datetime.fromisoformat(start).replace(tzinfo=UTC), hours=hours
    )

    def fetch(request):
        index = plan.requests.index(request) + 1
        session.events.append(("fetch", index))
        return fetch_synthetic(request)

    state = c.start_run(None, plan, fetch=fetch, clock=clock)
    assert state.status == "succeeded" and state.counts == (hours, 0, 0)
    assert session.events == [
        (event, i)
        for i in range(1, len(plan.requests) + 1)
        for event in ("fetch", "write")
    ]
    rows = [row for write in session.writes for row in write.observations]
    assert len(rows) == len({row.timestamp_utc for row in rows}) == hours
    assert rows[0].timestamp_utc == plan.interval.start
    assert rows[-1].timestamp_utc == plan.interval.end - timedelta(hours=1)
    assert all(row.relative_humidity_pct is None for row in rows)
    assert all(write.run.interval == plan.interval for write in session.writes)


@pytest.mark.parametrize("fail_index", [1, 2])
@pytest.mark.parametrize(
    "defect", ["http", "missing", "duplicate", "bad_unit", "unexpected"]
)
def test_failure_and_explicit_resume(setup, fail_index, defect):
    plan, session, clock = setup(hours=360)

    def fetch(request):
        if plan.requests.index(request) + 1 == fail_index:
            if defect == "http":
                raise OpenMeteoStatusError(429, "1")
            if defect == "unexpected":
                raise RuntimeError("sensitive diagnostic")
            payload = synthetic_payload(request)
            if defect == "missing":
                for array in payload["hourly"].values():
                    array.pop()
            elif defect == "duplicate":
                payload["hourly"]["time"][1] = payload["hourly"]["time"][0]
            else:
                payload["hourly_units"]["temperature_2m"] = "F"
            return payload
        return fetch_synthetic(request)

    with pytest.raises(c.IngestionError) as error:
        c.start_run(None, plan, fetch=fetch, clock=clock)
    assert error.value.chunk_index == fail_index
    assert not error.value.outcome_unknown
    assert "sensitive" not in str(error.value)
    assert session.state.status == "failed" and session.state.failed_chunks == 1
    assert session.state.completed_chunks == fail_index - 1
    calls = []

    def resume_fetch(request):
        calls.append(request)
        return fetch_synthetic(request)

    result = c.resume_run(
        None, plan.run_id, fetch=resume_fetch, expected=plan, clock=clock
    )
    assert result.status == "succeeded" and result.counts == (360, 0, 0)
    assert calls == list(plan.requests[fail_index - 1 :])
    never = MagicMock(side_effect=AssertionError("Completed run fetched"))
    assert c.resume_run(None, plan.run_id, fetch=never) == result
    assert c.start_run(None, plan, fetch=never) == result
    never.assert_not_called()


@pytest.mark.parametrize("ack", [False, True])
def test_write_failure_reconciles_before_retry(setup, monkeypatch, ack):
    plan, session, clock = setup()
    write = session.write

    def failed(chunk):
        if ack:
            write(chunk)
        raise RuntimeError("driver details")

    monkeypatch.setattr(session, "write", failed)
    if ack:
        result = c.start_run(None, plan, fetch=fetch_synthetic, clock=clock)
        assert result.status == "succeeded"
    else:
        with pytest.raises(c.IngestionError):
            c.start_run(None, plan, fetch=fetch_synthetic, clock=clock)
        assert session.state.status == "failed" and session.state.counts == (0, 0, 0)


@pytest.mark.parametrize("conflict", [False, True])
def test_unverifiable_outcome_never_returns_success(setup, monkeypatch, conflict):
    plan, session, clock = setup()
    write = session.write

    def failed(chunk):
        if conflict:
            write(
                replace(chunk, retrieved_at=chunk.retrieved_at - timedelta(seconds=1))
            )
        raise RuntimeError

    monkeypatch.setattr(session, "write", failed)
    if not conflict:
        monkeypatch.setattr(session, "load", MagicMock(side_effect=RuntimeError))
    with pytest.raises(c.IngestionError) as error:
        c.start_run(None, plan, fetch=fetch_synthetic, clock=clock)
    assert error.value.outcome_unknown


def test_resume_plan_mismatch(setup):
    plan, session, clock = setup()
    with pytest.raises(PersistenceError, match="plan_conflict"):
        c.resume_run(
            None,
            plan.run_id,
            fetch=fetch_synthetic,
            expected=replace(plan, created_at=plan.created_at + timedelta(seconds=1)),
        )
    assert not session.writes


@pytest.mark.parametrize("fetch,clock", [(None, lambda: None), (fetch_synthetic, None)])
def test_invalid_callables_do_not_connect(fetch, clock):
    with pytest.raises(PersistenceError, match="invalid_callable"):
        c.start_run(None, None, fetch=fetch, clock=clock)


def test_invalid_plan_does_not_connect():
    with pytest.raises(PersistenceError, match="invalid_run"):
        c.start_run(None, None, fetch=fetch_synthetic)


@pytest.mark.parametrize("command", ["start", "status", "resume"])
def test_cli_safe_commands(monkeypatch, capsys, persistence_case, command):
    plan = persistence_case().run
    state = RunProgress(
        plan, "succeeded", plan.created_at, (ChunkProgress(1, "succeeded", 3),)
    )
    monkeypatch.setattr(cli, "load_test_database", lambda path: object())
    called = MagicMock(return_value=state)
    monkeypatch.setattr(
        cli,
        {"start": "start_run", "status": "inspect_run", "resume": "resume_run"}[
            command
        ],
        called,
    )
    args = ["--database-config", "ignored.json", command, "--run-id", str(plan.run_id)]
    if command == "start":
        args += [
            "--city",
            "athens",
            "--start",
            plan.interval.start.isoformat(),
            "--end",
            plan.interval.end.isoformat(),
        ]
    assert cli.main(args) == 0
    output = capsys.readouterr().out
    assert '"mode": "synthetic"' in output and '"inserted": 3' in output
    if command != "status":
        assert called.call_args.kwargs["fetch"] is fetch_synthetic


def test_cli_failure_is_sanitized(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "load_test_database",
        MagicMock(side_effect=cli.TestDatabaseError("private")),
    )
    assert (
        cli.main(
            [
                "--database-config",
                "private",
                "status",
                "--run-id",
                "00000000-0000-0000-0000-000000000001",
            ]
        )
        == 1
    )
    assert "private" not in capsys.readouterr().out


def test_cli_rejects_live_flag():
    with pytest.raises(SystemExit) as result:
        cli.main(
            [
                "--database-config",
                "unused",
                "resume",
                "--run-id",
                "00000000-0000-0000-0000-000000000001",
                "--live",
            ]
        )
    assert result.value.code == 2


@pytest.mark.parametrize("unknown", [False, True])
def test_cli_reports_contextual_failure(monkeypatch, capsys, persistence_case, unknown):
    plan = persistence_case().run
    monkeypatch.setattr(cli, "load_test_database", lambda _: object())
    monkeypatch.setattr(
        cli,
        "resume_run",
        MagicMock(
            side_effect=c.IngestionError(plan.run_id, 2, "persist", unknown=unknown)
        ),
    )
    assert (
        cli.main(
            ["--database-config", "unused", "resume", "--run-id", str(plan.run_id)]
        )
        == 1
    )
    import json

    result = json.loads(capsys.readouterr().out)
    assert result["chunk_index"] == 2 and result["stage"] == "persist"
    assert result["outcome_unknown"] is unknown
