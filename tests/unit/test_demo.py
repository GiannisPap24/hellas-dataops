"""Offline host-demo orchestration and failure guards; never a real listener/DB."""

import importlib.util
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest


@pytest.fixture
def demo():
    spec = importlib.util.spec_from_file_location(
        "demo", Path(__file__).resolve().parents[2] / "scripts/demo.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_oracle_is_deterministic_canonical_and_nullable(demo):
    rows = demo.expected_observations()
    assert rows == demo.expected_observations()
    assert len(rows) == 168
    assert len({r["timestamp_utc"] for r in rows}) == 168
    assert rows[0]["timestamp_utc"] == "2025-01-01T00:00:00Z"
    assert rows[-1]["timestamp_utc"] == "2025-01-07T23:00:00Z"
    assert all(r["temperature_c"] == 10 and r["precipitation_mm"] is None for r in rows)


def test_query_url_encodes_utc_plus(demo):
    path = demo.weather_path(start="2025-01-01T00:00:00+00:00")
    assert "%2B00%3A00" in path
    assert parse_qs(urlsplit(path).query)["start"] == ["2025-01-01T00:00:00+00:00"]


@pytest.mark.parametrize("failure", [False, True])
def test_http_loopback_bounded_and_closed(demo, monkeypatch, failure):
    connection = MagicMock()
    connection.getresponse.return_value.status = 200
    connection.getresponse.return_value.read.return_value = b"x" * (
        demo.MAX_BODY + 1 if failure else 2
    )
    constructor = MagicMock(return_value=connection)
    monkeypatch.setattr(demo.http.client, "HTTPConnection", constructor)
    if failure:
        with pytest.raises(demo.DemoError, match="bound"):
            demo.request(12345, "/health/live")
    else:
        status, body, elapsed = demo.request(12345, "/health/live")
        assert status == 200 and body == b"xx" and elapsed >= 0
    constructor.assert_called_once_with("127.0.0.1", 12345, timeout=5)
    connection.close.assert_called_once()
    connection.getresponse.return_value.read.assert_called_once_with(demo.MAX_BODY + 1)


@pytest.mark.parametrize(
    "port,path",
    [(80, "/"), (True, "/"), (12345, "https://remote"), (12345, "//remote")],
)
def test_no_arbitrary_http_destinations(demo, port, path):
    with pytest.raises(demo.DemoError):
        demo.request(port, path)


def test_http_socket_errors_still_close(demo, monkeypatch):
    connection = MagicMock()
    connection.request.side_effect = OSError("private")
    monkeypatch.setattr(demo.http.client, "HTTPConnection", lambda *a, **k: connection)
    with pytest.raises(OSError):
        demo.request(12345, "/health/live")
    connection.close.assert_called_once()


@pytest.mark.parametrize("code,body", [(503, b"{}"), (200, b"not json")])
def test_json_rejects_wrong_status_and_malformed_payload(demo, monkeypatch, code, body):
    monkeypatch.setattr(demo, "request", lambda *a: (code, body, 1))
    with pytest.raises((demo.DemoError, ValueError)):
        demo.get_json(12345, "/health/ready")


@pytest.fixture
def fake_http(demo, monkeypatch):
    rows = demo.expected_observations()

    def get(port, path):
        if path == "/health/live":
            data = {"status": "alive"}
        elif path == "/health/ready":
            data = {"status": "ready"}
        elif path == "/v1/cities":
            data = {"supported": [None] * 5, "persisted": [{"city_id": "athens"}]}
        elif path == "/docs":
            return 200, b"<html>swagger</html>", 1
        elif path == "/openapi.json":
            data = {
                "paths": dict.fromkeys(
                    ["/health/live", "/health/ready", "/v1/cities", "/v1/weather"]
                )
            }
        else:
            params = {k: v[0] for k, v in parse_qs(urlsplit(path).query).items()}
            if any(
                params[k] != demo.PARAMS[k]
                for k in ("city_id", "dataset_id", "coordinate_version", "provider_id")
            ):
                return 404, b"{}", 1
            if (
                int(params.get("limit", 100)) > 500
                or not params["start"].endswith("Z")
                or params["end"] <= params["start"]
            ):
                return 422, b"{}", 1
            selected = [
                r
                for r in rows
                if params["start"] <= r["timestamp_utc"] < params["end"]
                and r["timestamp_utc"] > params.get("after", "")
            ]
            limit = int(params.get("limit", 100))
            data = {
                "observations": selected[:limit],
                "next_after": selected[limit - 1]["timestamp_utc"]
                if len(selected) > limit
                else None,
                "city": {"coordinate_version": demo.PARAMS["coordinate_version"]},
                "dataset": {
                    "provider_id": "open-meteo",
                    "dataset_id": "era5_hourly-v1",
                    "historical_model": "era5",
                },
                "units": dict(
                    temperature_c="celsius",
                    relative_humidity_pct="percent",
                    precipitation_mm="mm",
                    wind_speed_ms="m/s",
                ),
            }
        return 200, json.dumps(data).encode(), 1

    monkeypatch.setattr(demo, "request", get)
    return get


def test_smoke_validates_pages_errors_and_saves_only_public_json(
    demo, fake_http, tmp_path
):
    result = demo.smoke(12345, demo.expected_observations(), tmp_path)
    assert result["paginated_rows"] == 168 and result["pages"] == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "openapi.json",
        "weather-page.json",
    ]


@pytest.mark.parametrize("defect", ["null", "duplicate", "cursor", "units", "identity"])
def test_smoke_rejects_misleading_success(
    demo, fake_http, monkeypatch, tmp_path, defect
):
    def corrupt(port, path):
        status, body, elapsed = fake_http(port, path)
        if path == demo.weather_path(limit=100):
            data = json.loads(body)
            if defect == "null":
                data["observations"][0]["precipitation_mm"] = 0
            elif defect == "duplicate":
                data["observations"][1] = data["observations"][0]
            elif defect == "cursor":
                data["next_after"] = None
            elif defect == "units":
                data["units"]["wind_speed_ms"] = "km/h"
            else:
                data["dataset"]["historical_model"] = "unknown"
            body = json.dumps(data).encode()
        return status, body, elapsed

    monkeypatch.setattr(demo, "request", corrupt)
    with pytest.raises(demo.DemoError):
        demo.smoke(12345, demo.expected_observations(), tmp_path)


def test_benchmark_counts_warmups_and_nearest_rank(demo, monkeypatch):
    expected = demo.expected_observations()
    calls = []

    def get(port, path):
        calls.append(path)
        limit = int(parse_qs(urlsplit(path).query)["limit"][0])
        return {"observations": expected[:limit]}, 123, len(calls)

    monkeypatch.setattr(demo, "get_json", get)
    results = []
    demo.benchmark(12345, expected, results, samples=20, warmup=2)
    assert len(calls) == 44
    assert results[0]["p50_ms"] == 12.5 and results[0]["p95_ms"] == 21
    assert results[0]["completed_samples"] == 20 and results[0]["errors"] == 0
    assert results[1]["rows_per_response"] == 168


def test_benchmark_failure_records_error_without_fabricated_metrics(demo, monkeypatch):
    monkeypatch.setattr(demo, "get_json", MagicMock(side_effect=OSError("private")))
    results = []
    with pytest.raises(demo.DemoError):
        demo.benchmark(12345, [], results)
    assert results[0]["errors"] == 1 and "p50_ms" not in results[0]


def test_benchmark_bounds_and_deadline(demo, monkeypatch):
    with pytest.raises(demo.DemoError):
        demo.benchmark(12345, [], [], samples=101)
    monkeypatch.setattr(demo.time, "monotonic", MagicMock(side_effect=[0, 121]))
    with pytest.raises(demo.DemoError, match="deadline"):
        demo.benchmark(12345, [], [])


@pytest.mark.parametrize("failure", [None, "startup", "body", "terminate_timeout"])
def test_only_owned_api_child_is_stopped(demo, monkeypatch, tmp_path, failure):
    process = MagicMock()
    process.poll.return_value = None
    process.terminate.side_effect = lambda: setattr(process.poll, "return_value", 0)
    if failure == "terminate_timeout":
        process.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 10), 0]
    popen = MagicMock(return_value=process)
    monkeypatch.setattr(demo.subprocess, "Popen", popen)
    monkeypatch.setattr(
        demo,
        "wait_api",
        MagicMock(
            side_effect=demo.DemoError("startup") if failure == "startup" else None,
            return_value=12345,
        ),
    )
    report = {}
    if failure in ("startup", "body", "terminate_timeout"):
        with (
            pytest.raises(demo.DemoError),
            demo.running_api(tmp_path / "target.json", {}, report),
        ):
            raise demo.DemoError("body")
    else:
        with demo.running_api(tmp_path / "target.json", {}, report) as port:
            assert port == 12345
    process.terminate.assert_called_once()
    assert report["api_process_stopped"]
    assert popen.call_args.kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert popen.call_args.args[0][0] == demo.sys._base_executable
    assert popen.call_args.kwargs["env"]["__PYVENV_LAUNCHER__"] == demo.sys.executable
    assert report["api_exit_before_cleanup"] is None
    if failure == "terminate_timeout":
        process.kill.assert_called_once()


@pytest.mark.parametrize("problem", ["exit", "pid", "timeout"])
def test_startup_rejects_exit_wrong_process_and_deadline(
    demo, monkeypatch, tmp_path, problem
):
    file = tmp_path / "port.json"
    file.write_text(json.dumps({"pid": 999, "port": 12345}))
    process = SimpleNamespace(pid=42, poll=lambda: 1 if problem == "exit" else None)
    monkeypatch.setattr(
        demo.time,
        "monotonic",
        MagicMock(side_effect=[0, 31] if problem == "timeout" else [0, 1]),
    )
    with pytest.raises(demo.DemoError):
        demo.wait_api(process, file)


def test_startup_success_after_partial_port_file(demo, monkeypatch, tmp_path):
    file = tmp_path / "port.json"
    file.write_text("{")
    process = SimpleNamespace(pid=42, poll=lambda: None)
    monkeypatch.setattr(
        demo.time,
        "sleep",
        lambda _: file.write_text(json.dumps({"pid": 42, "port": 12345})),
    )
    monkeypatch.setattr(demo, "request", lambda *a: (200, b'{"status":"ready"}', 1))
    assert demo.wait_api(process, file) == 12345


def test_command_failure_does_not_echo_diagnostics(demo, monkeypatch):
    monkeypatch.setattr(
        demo.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=1, stdout="secret", stderr="private"
        ),
    )
    with pytest.raises(demo.DemoError, match="Application command failed") as caught:
        demo.command(["synthetic"], {})
    assert "secret" not in str(caught.value)


def progress(demo, identity, counts):
    return dict(
        run_id=str(identity),
        mode="synthetic",
        status="succeeded",
        planned_chunks=1,
        completed_chunks=1,
        failed_chunks=0,
        finished_at="synthetic time",
        city="athens",
        coordinate_version=demo.PARAMS["coordinate_version"],
        dataset="open-meteo/era5_hourly-v1",
        start=demo.PARAMS["start"],
        end=demo.PARAMS["end"],
        inserted=counts[0],
        updated=counts[1],
        unchanged=counts[2],
        chunks=[
            dict(
                index=1,
                status="succeeded",
                inserted=counts[0],
                updated=counts[1],
                unchanged=counts[2],
            )
        ],
    )


@pytest.mark.parametrize(
    "change",
    [
        {"status": "running"},
        {"inserted": 167},
        {"completed_chunks": 0},
        {"city": "patras"},
        {"dataset": "unknown"},
        {"finished_at": None},
    ],
)
def test_progress_rejects_false_success(demo, change):
    identity = UUID(int=1)
    with pytest.raises(demo.DemoError):
        demo.verify_progress(
            {**progress(demo, identity, (168, 0, 0)), **change}, identity, (168, 0, 0)
        )


@pytest.mark.parametrize("mutated", [False, True])
def test_full_workflow_order_and_read_only_check(demo, monkeypatch, tmp_path, mutated):
    import sqlalchemy as sa

    import hellas_dataops.storage.migration as migration
    import hellas_dataops.storage.test_database as database

    calls = []
    connection = MagicMock()
    connection.scalar.return_value = "20261022_0001"

    @contextmanager
    def connect(_):
        yield connection

    monkeypatch.setattr(database, "test_connection", connect)
    monkeypatch.setattr(
        sa, "inspect", lambda _: SimpleNamespace(get_table_names=lambda: [])
    )
    monkeypatch.setattr(
        migration, "migrate_test_database", lambda _: calls.append("migrate")
    )
    monkeypatch.setattr(
        demo, "ingest", lambda *a: calls.append("ingest") or {"synthetic": []}
    )

    @contextmanager
    def api(*args):
        calls.append("start-api")
        yield 12345
        calls.append("stop-api")

    monkeypatch.setattr(demo, "running_api", api)
    monkeypatch.setattr(demo, "smoke", lambda *a: calls.append("http") or {})
    monkeypatch.setattr(demo, "benchmark", lambda *a: calls.append("benchmark"))
    monkeypatch.setattr(
        demo,
        "snapshot",
        lambda _: calls.append("snapshot") or {"synthetic": [1] if mutated else []},
    )
    report = {}
    if mutated:
        with pytest.raises(demo.DemoError, match="modified"):
            demo.workflow(None, tmp_path / "target.json", {}, report, tmp_path)
    else:
        demo.workflow(None, tmp_path / "target.json", {}, report, tmp_path)
    assert calls == [
        "migrate",
        "ingest",
        "start-api",
        "http",
        "benchmark",
        "stop-api",
        "snapshot",
    ]
    assert report.get("read_only_verified", False) is not mutated


@pytest.mark.parametrize("defect", [None, "duplicate", "lineage", "receipt", "resume"])
def test_cli_replay_verifies_durable_receipts_and_preserves_rows(
    demo, monkeypatch, tmp_path, defect
):
    import hellas_dataops.storage.run_tracking as tracking

    ids = [UUID(int=1), UUID(int=2)]
    monkeypatch.setattr(demo, "uuid4", MagicMock(side_effect=ids))
    rows = [dict(source_run_id=ids[0], timestamp=i) for i in range(168)]
    state = {"weather_hourly": [], "ingestion_chunks": []}
    resumed = False
    calls = []

    def snapshot(_):
        if resumed and defect == "resume":
            return {**state, "unexpected": True}
        return state

    def command(args, env):
        nonlocal state, resumed
        calls.append(args)
        identity = UUID(args[args.index("--run-id") + 1])
        counts = (168, 0, 0) if identity == ids[0] else (0, 0, 168)
        if "start" in args:
            stored_rows = rows
            if identity == ids[1] and defect == "duplicate":
                stored_rows = rows + rows
            elif identity == ids[1] and defect == "lineage":
                stored_rows = [{**r, "source_run_id": ids[1]} for r in rows]
            state = {
                "weather_hourly": stored_rows,
                "ingestion_chunks": [
                    dict(
                        status="succeeded",
                        request_provenance={"operation": "synthetic-sequential-v1"},
                    )
                ],
            }
        if "resume" in args:
            resumed = True
        return progress(demo, identity, counts)

    monkeypatch.setattr(demo, "snapshot", snapshot)
    monkeypatch.setattr(demo, "command", command)
    monkeypatch.setattr(
        tracking,
        "inspect_run",
        lambda target, identity: SimpleNamespace(
            status="failed" if defect == "receipt" else "succeeded",
            counts=(168, 0, 0) if identity == ids[0] else (0, 0, 168),
        ),
    )
    report = {}
    if defect:
        with pytest.raises(demo.DemoError):
            demo.ingest(None, tmp_path / "target.json", {}, report)
    else:
        demo.ingest(None, tmp_path / "target.json", {}, report)
        assert report["weather_rows"] == 168
        assert report["replay"]["progress"]["unchanged"] == 168
        assert len(calls) == 5  # start/status twice, then explicit completed resume.


def test_serve_uses_real_factory_and_held_exclusive_loopback_socket(
    demo, monkeypatch, tmp_path
):
    import uvicorn

    import hellas_dataops.api.app as application
    import hellas_dataops.storage.test_database as database

    target = object()
    app = object()
    listener = MagicMock()
    listener.__enter__.return_value = listener
    listener.getsockname.return_value = ("127.0.0.1", 12345)
    monkeypatch.setattr(demo.socket, "socket", lambda *a: listener)
    monkeypatch.setattr(database, "load_test_database", lambda _: target)
    factory = MagicMock(return_value=app)
    monkeypatch.setattr(application, "create_app", factory)
    config = MagicMock()
    server = MagicMock()
    monkeypatch.setattr(uvicorn, "Config", config)
    monkeypatch.setattr(uvicorn, "Server", lambda _: server)
    file = tmp_path / "port.json"
    demo.serve(tmp_path / "target.json", file)
    listener.bind.assert_called_once_with(("127.0.0.1", 0))
    listener.setsockopt.assert_called_once_with(
        demo.socket.SOL_SOCKET, demo.socket.SO_EXCLUSIVEADDRUSE, 1
    )
    factory.assert_called_once_with(target)
    server.run.assert_called_once_with(sockets=[listener])
    assert config.call_args.kwargs["access_log"] is False
    assert config.call_args.kwargs["workers"] == 1
    assert config.call_args.kwargs["log_config"] is None
    assert (
        json.loads((tmp_path / "api-diagnostics.json").read_text())["phase"]
        == "server-returned"
    )
    assert json.loads(file.read_text()) == {"pid": demo.os.getpid(), "port": 12345}


def test_cli_command_uses_project_environment_and_checks_json(demo, monkeypatch):
    run = MagicMock(
        return_value=SimpleNamespace(returncode=0, stdout='{"status":"succeeded"}')
    )
    monkeypatch.setattr(demo.subprocess, "run", run)
    assert demo.command(["synthetic-python", "-I"], {"synthetic": "env"}) == {
        "status": "succeeded"
    }
    assert run.call_args.kwargs["cwd"] == demo.ROOT
    assert run.call_args.kwargs["timeout"] == 120
    assert run.call_args.kwargs["env"] == {"synthetic": "env"}


def test_snapshot_reads_every_application_table_in_primary_key_order(demo, monkeypatch):
    import hellas_dataops.storage.test_database as database
    from hellas_dataops.storage.schema import metadata

    connection = MagicMock()
    connection.execute.return_value.mappings.return_value = [{"synthetic": "row"}]
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr(database, "test_connection", lambda _: context)
    result = demo.snapshot(object())
    assert set(result) == set(metadata.tables)
    assert connection.execute.call_count == 5
    for call in connection.execute.call_args_list:
        assert "ORDER BY" in str(call.args[0]) and str(call.args[0]).startswith(
            "SELECT"
        )
    context.__exit__.assert_called_once()


@pytest.mark.parametrize("mode", ["host", "serve", "unknown"])
def test_entrypoint_dispatch_does_not_accidentally_run_other_modes(
    demo, monkeypatch, mode
):
    calls = []
    serve = MagicMock()
    monkeypatch.setattr(demo, "serve", serve)
    argv = ["demo.py"] + (
        {"host": [], "serve": ["--serve", "target", "listener"], "unknown": ["--live"]}[
            mode
        ]
    )
    monkeypatch.setattr(demo.sys, "argv", argv)
    runner = SimpleNamespace()

    def execute(module):
        module.main = lambda **kwargs: calls.append(kwargs) or 0

    monkeypatch.setattr(
        demo.importlib.util,
        "spec_from_file_location",
        lambda *a: SimpleNamespace(loader=SimpleNamespace(exec_module=execute)),
    )
    monkeypatch.setattr(demo.importlib.util, "module_from_spec", lambda _: runner)
    if mode == "unknown":
        with pytest.raises(demo.DemoError):
            demo.main()
        assert not calls and not serve.called
    elif mode == "serve":
        assert demo.main() == 0
        serve.assert_called_once_with("target", "listener")
        assert not calls
    else:
        assert demo.main() == 0
        assert calls == [{"workflow": demo.workflow}]


@pytest.mark.parametrize("failure", ["exception", "logged"])
def test_child_diagnostics_exclude_messages_paths_and_locals(
    demo, monkeypatch, tmp_path, failure
):
    import logging

    def fail(config, listener, publish):
        publish("server-run")
        try:
            raise RuntimeError("postgresql://private-password@private-host/private-db")
        except RuntimeError:
            if failure == "exception":
                raise
            logging.getLogger("uvicorn.error").exception("secret raw diagnostic")

    monkeypatch.setattr(demo, "serve_application", fail)
    if failure == "exception":
        with pytest.raises(RuntimeError):
            demo.serve("private-config", tmp_path / "listener.json")
    else:
        demo.serve("private-config", tmp_path / "listener.json")
    text = (tmp_path / "api-diagnostics.json").read_text()
    assert "private" not in text and "secret" not in text
    assert str(tmp_path) not in text
    result = json.loads(text)
    if failure == "exception":
        assert result["failed_phase"] == "server-run"
        assert result["exception"]["type"] == "RuntimeError"
    else:
        assert result["events"][0]["exception"]["type"] == "RuntimeError"


def test_readiness_retry_records_status_without_body(demo, monkeypatch, tmp_path):
    file = tmp_path / "port.json"
    file.write_text(json.dumps({"pid": 42, "port": 12345}))
    process = SimpleNamespace(pid=42, poll=lambda: None)
    responses = iter([(503, b"private response", 1), (200, b'{"status":"ready"}', 1)])
    monkeypatch.setattr(demo, "request", lambda *a: next(responses))
    monkeypatch.setattr(demo.time, "sleep", lambda _: None)
    diagnostics = {}
    assert demo.wait_api(process, file, diagnostics) == 12345
    assert diagnostics["attempts"] == 2
    assert diagnostics["last_http_status"] == 200
    assert "private" not in json.dumps(diagnostics)


def test_parent_records_child_failure_and_diagnostic(demo, monkeypatch, tmp_path):
    process = SimpleNamespace(pid=42, poll=lambda: 7)
    monkeypatch.setattr(demo.subprocess, "Popen", lambda *a, **k: process)
    (tmp_path / "api-diagnostics.json").write_text('{"phase":"failed"}')
    report = {}
    with pytest.raises(demo.DemoError, match="exited"):
        with demo.running_api(tmp_path / "target.json", {}, report):
            pytest.fail("A failed startup must not yield success")
    assert report["api_exit_before_cleanup"] == 7
    assert report["api_child"] == {"phase": "failed"}
    assert report["api_process_stopped"]
