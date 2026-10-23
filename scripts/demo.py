"""Host-only synthetic CLI -> PostgreSQL -> Uvicorn -> real HTTP verification.

Standard-library imports only until the shared guarded dependency preflight.
No alternative ingestion, persistence, endpoint or database provisioning code.
"""

import hashlib
import http.client
import importlib.util
import json
import logging
import math
import os
import platform
import socket
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PARAMS = {
    "city_id": "athens",
    "coordinate_version": "gr-cities-v1",
    "provider_id": "open-meteo",
    "dataset_id": "era5_hourly-v1",
    "start": "2025-01-01T00:00:00Z",
    "end": "2025-01-08T00:00:00Z",
}
MAX_BODY = 1024 * 1024


class DemoError(RuntimeError):
    """Diagnostics are intentionally fixed and contain no connection secrets."""


def require(condition, message):
    if not condition:
        raise DemoError(message)


def command(args, env):
    result = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    require(result.returncode == 0, "Application command failed")
    return json.loads(result.stdout)


def weather_path(**changes):
    return "/v1/weather?" + urlencode({**PARAMS, **changes})


def request(port, path):
    """Numeric loopback only, no proxies/redirects, bounded body and socket time."""
    require(type(port) is int and 1024 <= port <= 65535, "Invalid API port")
    require(path.startswith("/") and not path.startswith("//"), "Invalid local path")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    started = time.perf_counter()
    try:
        connection.request("GET", path, headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read(MAX_BODY + 1)
        require(len(body) <= MAX_BODY, "HTTP body exceeds demonstration bound")
        elapsed = (time.perf_counter() - started) * 1000
        return response.status, body, elapsed
    finally:
        connection.close()


def get_json(port, path, status=200):
    actual, body, elapsed = request(port, path)
    require(actual == status, "Unexpected HTTP status")
    return json.loads(body), len(body), elapsed


def expected_observations():
    """Reuse the real synthetic adapter and canonical validation as the oracle."""
    from hellas_dataops.domain import HourlyInterval
    from hellas_dataops.ingestion.source_pipeline import collect_open_meteo
    from hellas_dataops.sources.cities import CITY_REGISTRY
    from hellas_dataops.sources.open_meteo_requests import ERA5_DATASET
    from hellas_dataops.sources.synthetic import fetch_synthetic

    result = collect_open_meteo(
        CITY_REGISTRY["athens"],
        dataset=ERA5_DATASET,
        interval=HourlyInterval(
            start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2025, 1, 8, tzinfo=UTC)
        ),
        fetch=fetch_synthetic,
    )
    return [
        dict(
            timestamp_utc=row.timestamp_utc.isoformat().replace("+00:00", "Z"),
            temperature_c=row.temperature_c,
            relative_humidity_pct=row.relative_humidity_pct,
            precipitation_mm=row.precipitation_mm,
            wind_speed_ms=row.wind_speed_ms,
        )
        for row in result.observations
    ]


def snapshot(target):
    import sqlalchemy as sa

    from hellas_dataops.storage.schema import metadata
    from hellas_dataops.storage.test_database import test_connection

    with test_connection(target) as connection:
        return {
            table.name: [
                dict(row)
                for row in connection.execute(
                    sa.select(table).order_by(*table.primary_key.columns)
                ).mappings()
            ]
            for table in metadata.sorted_tables
        }


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def verify_progress(result, run_id, counts):
    require(
        result["run_id"] == str(run_id)
        and result["mode"] == "synthetic"
        and result["status"] == "succeeded"
        and result["planned_chunks"] == 1
        and result["completed_chunks"] == 1
        and result["failed_chunks"] == 0
        and result["finished_at"] is not None
        and result["city"] == "athens"
        and result["coordinate_version"] == PARAMS["coordinate_version"]
        and result["dataset"] == "open-meteo/era5_hourly-v1"
        and datetime.fromisoformat(result["start"])
        == datetime.fromisoformat(PARAMS["start"])
        and datetime.fromisoformat(result["end"])
        == datetime.fromisoformat(PARAMS["end"])
        and tuple(result[k] for k in ("inserted", "updated", "unchanged")) == counts
        and result["chunks"]
        == [
            dict(
                index=1,
                status="succeeded",
                inserted=counts[0],
                updated=counts[1],
                unchanged=counts[2],
            )
        ],
        "Persisted ingestion progress mismatch",
    )


def ingest(target, config_file, env, report):
    from hellas_dataops.storage.run_tracking import inspect_run

    require(
        all(not rows for rows in snapshot(target).values()),
        "Demo database is not empty",
    )
    # Called only after the reviewed migration in workflow().
    run_id, replay_id = uuid4(), uuid4()
    prefix = [
        sys.executable,
        "-I",
        "-B",
        "-m",
        "hellas_dataops.ingestion",
        "--database-config",
        str(config_file),
    ]
    first_weather = None
    for name, identity, counts in (
        ("initial", run_id, (168, 0, 0)),
        ("replay", replay_id, (0, 0, 168)),
    ):
        started = time.perf_counter()
        result = command(
            prefix
            + [
                "start",
                "--run-id",
                str(identity),
                "--city",
                "athens",
                "--start",
                PARAMS["start"],
                "--end",
                PARAMS["end"],
            ],
            env,
        )
        verify_progress(result, identity, counts)
        status = command(prefix + ["status", "--run-id", str(identity)], env)
        require(status == result, "CLI status disagrees with completed execution")
        durable = inspect_run(target, identity)  # Validates receipts and coverage.
        require(
            durable.status == "succeeded" and durable.counts == counts,
            "Durable receipts disagree with CLI",
        )
        data = snapshot(target)
        weather = data["weather_hourly"]
        require(len(weather) == 168, "Unexpected weather row count")
        require(
            all(row["source_run_id"] == run_id for row in weather),
            "Unchanged replay replaced observation lineage",
        )
        require(
            all(row["status"] == "succeeded" for row in data["ingestion_chunks"]),
            "Unfinished chunk",
        )
        require(
            all(
                row["request_provenance"].get("operation") == "synthetic-sequential-v1"
                for row in data["ingestion_chunks"]
            ),
            "Synthetic provenance missing",
        )
        if first_weather is None:
            first_weather = digest(weather)
        else:
            require(
                digest(weather) == first_weather, "Replay changed stored observations"
            )
        report[name] = {
            "progress": result,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
        }
    before = snapshot(target)
    resumed = command(prefix + ["resume", "--run-id", str(run_id)], env)
    verify_progress(resumed, run_id, (168, 0, 0))
    require(snapshot(target) == before, "Completed resume changed persisted state")
    report["resume"] = "verified completed run; no changes"
    report["weather_rows"] = 168
    return before


def exception_diagnostic(error):
    """Keep exception types and code locations, never messages, locals or paths."""
    frames = []
    trace = error.__traceback__
    while trace is not None:
        frames.append(
            {
                "file": Path(trace.tb_frame.f_code.co_filename).name,
                "function": trace.tb_frame.f_code.co_name,
                "line": trace.tb_lineno,
            }
        )
        trace = trace.tb_next
    return {"type": type(error).__name__, "frames": frames[-8:]}


def serve(config_file, port_file):
    """Persist sanitized child diagnostics even when console output is unavailable."""
    destination = Path(port_file).with_name("api-diagnostics.json")
    state = {"phase": "imports", "events": []}

    def publish(phase):
        state["phase"] = phase
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(destination)

    class Diagnostics(logging.Handler):
        def emit(self, record):
            event = {"level": record.levelname}
            if record.exc_info and record.exc_info[1] is not None:
                event["exception"] = exception_diagnostic(record.exc_info[1])
            state["events"] = (state["events"] + [event])[-8:]
            publish(state["phase"])

    handler = Diagnostics(level=logging.ERROR)
    logger = logging.getLogger("uvicorn.error")
    logger.addHandler(handler)
    try:
        publish("imports")
        serve_application(config_file, port_file, publish)
    except BaseException as error:
        state["failed_phase"] = state["phase"]
        state["exception"] = exception_diagnostic(error)
        publish("failed")
        raise
    finally:
        logger.removeHandler(handler)


def serve_application(config_file, port_file, publish):
    """Actual app/Uvicorn on a held exclusive socket: no close/rebind port race."""
    import uvicorn

    from hellas_dataops.api.app import create_app
    from hellas_dataops.storage.test_database import load_test_database

    require(
        Path(sys.prefix).resolve() == (ROOT / ".venv").resolve(),
        "Wrong API environment",
    )
    publish("configuration")
    target = load_test_database(Path(config_file))
    publish("socket-bind")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        Path(port_file).write_text(
            json.dumps({"port": listener.getsockname()[1], "pid": os.getpid()}),
            encoding="utf-8",
        )
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(target),
                host="127.0.0.1",
                workers=1,
                log_level="error",
                log_config=None,
                access_log=False,
            )
        )
        publish("server-run")
        server.run(sockets=[listener])
        publish("server-returned")


def wait_api(process, port_file, diagnostics=None):
    diagnostics = {} if diagnostics is None else diagnostics
    diagnostics["attempts"] = 0
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        diagnostics["child_exit_code"] = process.poll()
        require(
            diagnostics["child_exit_code"] is None, "API child exited before readiness"
        )
        try:
            data = json.loads(port_file.read_text(encoding="utf-8"))
        except FileNotFoundError, json.JSONDecodeError:
            time.sleep(0.1)
            continue
        require(data["pid"] == process.pid, "API child identity mismatch")
        try:
            diagnostics["attempts"] += 1
            status, raw, _ = request(data["port"], "/health/ready")
            diagnostics["last_http_status"] = status
            require(status == 200, "API readiness HTTP status mismatch")
            body = json.loads(raw)
            require(body == {"status": "ready"}, "API readiness mismatch")
            require(process.poll() is None, "API child stopped during readiness")
            return data["port"]
        except (OSError, http.client.HTTPException, DemoError, ValueError) as error:
            diagnostics["last_probe_error"] = type(error).__name__
            time.sleep(0.1)
    raise DemoError("API startup deadline exceeded")


@contextmanager
def running_api(config_file, env, report):
    port_file = config_file.parent / "api-listener.json"
    require(not port_file.exists(), "Unexpected API listener file")
    require(
        Path(sys.prefix).resolve() == (ROOT / ".venv").resolve(),
        "Wrong API environment",
    )
    # Match CPython multiprocessing.popen_spawn_win32: a Windows venv executable
    # is a redirector which creates another process. Own the interpreter itself,
    # preserving venv discovery through CPython's launcher environment variable.
    child_python = sys.executable
    child_env = dict(env)
    if sys.platform == "win32" and sys.executable != sys._base_executable:
        child_python = sys._base_executable
        child_env["__PYVENV_LAUNCHER__"] = sys.executable
    process = subprocess.Popen(
        [
            child_python,
            "-I",
            "-B",
            str(Path(__file__).resolve()),
            "--serve",
            str(config_file),
            str(port_file),
        ],
        cwd=ROOT,
        env=child_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        port = wait_api(process, port_file, report.setdefault("api_startup", {}))
        report["api"] = {
            "host": "127.0.0.1",
            "port": port,
            "transport": "real HTTP/1.1",
        }
        yield port
        require(process.poll() is None, "API child stopped unexpectedly")
    except DemoError as error:
        # DemoError messages in this boundary are fixed strings, not raw errors.
        report["api_startup_error"] = str(error)
        raise
    finally:
        report["api_exit_before_cleanup"] = process.poll()
        diagnostic_file = port_file.with_name("api-diagnostics.json")
        try:
            report["api_child"] = json.loads(
                diagnostic_file.read_text(encoding="utf-8")
            )
        except OSError, ValueError:
            report["api_child"] = {"phase": "diagnostics-unavailable"}
        # Only the process handle returned by Popen is terminated, never a PID search.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        report["api_process_stopped"] = process.poll() is not None


def smoke(port, expected, report_dir):
    require(
        get_json(port, "/health/live")[0] == {"status": "alive"}, "Liveness mismatch"
    )
    require(
        get_json(port, "/health/ready")[0] == {"status": "ready"}, "Readiness mismatch"
    )
    cities = get_json(port, "/v1/cities")[0]
    require(
        len(cities["supported"]) == 5
        and len(cities["persisted"]) == 1
        and cities["persisted"][0]["city_id"] == "athens",
        "City catalog mismatch",
    )
    code, html, _ = request(port, "/docs")
    require(code == 200 and b"swagger" in html.lower(), "Swagger HTML unavailable")
    spec = get_json(port, "/openapi.json")[0]
    require(
        set(spec["paths"])
        == {"/health/live", "/health/ready", "/v1/cities", "/v1/weather"},
        "OpenAPI paths mismatch",
    )
    (report_dir / "openapi.json").write_text(
        json.dumps(spec, indent=2), encoding="utf-8"
    )
    first, size, latency = get_json(port, weather_path(limit=100))
    require(first["observations"] == expected[:100], "First page values mismatch")
    require(
        first["city"]["coordinate_version"] == PARAMS["coordinate_version"]
        and first["dataset"]["dataset_id"] == PARAMS["dataset_id"]
        and first["dataset"]["provider_id"] == PARAMS["provider_id"]
        and first["dataset"]["historical_model"] == "era5"
        and first["units"]
        == dict(
            temperature_c="celsius",
            relative_humidity_pct="percent",
            precipitation_mm="mm",
            wind_speed_ms="m/s",
        ),
        "Response identity or units mismatch",
    )
    require(first["next_after"] == expected[99]["timestamp_utc"], "Cursor mismatch")
    second = get_json(port, weather_path(limit=100, after=first["next_after"]))[0]
    require(
        first["observations"] + second["observations"] == expected
        and second["next_after"] is None,
        "Pagination mismatch",
    )
    one = get_json(port, weather_path(end="2025-01-01T01:00:00Z"))[0]
    require(one["observations"] == expected[:1], "Exclusive end mismatch")
    empty = get_json(
        port, weather_path(start=PARAMS["end"], end="2025-01-09T00:00:00Z")
    )[0]
    require(
        empty["observations"] == [] and empty["next_after"] is None,
        "Empty result mismatch",
    )
    for changes in (
        {"city_id": "patras"},
        {"dataset_id": "unknown"},
        {"coordinate_version": "unknown"},
        {"provider_id": "unknown"},
    ):
        get_json(port, weather_path(**changes), 404)
    for changes in (
        {"limit": 501},
        {"start": "2025-01-01T00:00:00"},
        {"end": PARAMS["start"]},
        {"start": "2025-01-01T00:00:00+02:00"},
    ):
        get_json(port, weather_path(**changes), 422)
    (report_dir / "weather-page.json").write_text(
        json.dumps(first, indent=2), encoding="utf-8"
    )
    return {
        "status": "passed",
        "paginated_rows": len(expected),
        "pages": 2,
        "first_weather_ms": latency,
        "first_page_bytes": size,
        "docs": "HTML only; CDN assets not fetched",
    }


def benchmark(port, expected, report, *, samples=30, warmup=5):
    require(1 <= samples <= 100 and 0 <= warmup <= 10, "Invalid benchmark bounds")
    deadline = time.monotonic() + 120
    for limit in (100, 500):
        path = weather_path(limit=limit)
        result = {
            "path": path,
            "requested_samples": samples,
            "warmup": warmup,
            "concurrency": 1,
            "errors": 0,
            "latencies_ms": [],
            "body_bytes": [],
            "rows_per_response": min(limit, 168),
        }
        report.append(result)
        for index in range(warmup + samples):
            require(time.monotonic() < deadline, "Benchmark deadline exceeded")
            try:
                page, size, elapsed = get_json(port, path)
                require(
                    page["observations"] == expected[:limit],
                    "Benchmark response mismatch",
                )
                if index >= warmup:
                    result["latencies_ms"].append(elapsed)
                    result["body_bytes"].append(size)
            except (
                OSError,
                http.client.HTTPException,
                ValueError,
                KeyError,
                TypeError,
                DemoError,
            ):
                result["errors"] += 1
                raise DemoError("Benchmark request failed") from None
        times = sorted(result["latencies_ms"])
        result.update(
            p50_ms=statistics.median(times),
            p95_ms=times[math.ceil(0.95 * len(times)) - 1],
            completed_samples=len(times),
            p95_method="nearest rank",
        )


def workflow(target, config_file, env, report, report_dir):
    import sqlalchemy as sa

    from hellas_dataops.storage.migration import migrate_test_database
    from hellas_dataops.storage.test_database import test_connection

    report["stage"] = "demo-migrations"
    with test_connection(target) as connection:
        require(
            not sa.inspect(connection).get_table_names(), "Fresh demo database required"
        )
    migrate_test_database(target)
    with test_connection(target) as connection:
        require(
            connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            == "20261022_0001",
            "Migration revision mismatch",
        )
    report["migration"] = "20261022_0001"
    report["stage"] = "demo-ingestion"
    report["ingestion"] = {}
    before = ingest(target, config_file, env, report["ingestion"])
    expected = expected_observations()
    require(
        len(expected) == 168 and expected == expected_observations(),
        "Synthetic determinism mismatch",
    )
    report["environment"] = {
        "python": platform.python_version(),
        "system": platform.system(),
        "system_release": platform.release(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
    }
    report["stage"] = "demo-api-start"
    with running_api(config_file, env, report) as port:
        report["stage"] = "demo-http-smoke"
        report["http"] = smoke(port, expected, report_dir)
        report["stage"] = "demo-benchmark"
        report["benchmark"] = []
        benchmark(port, expected, report["benchmark"])
    report["stage"] = "demo-read-only-verification"
    require(snapshot(target) == before, "HTTP checks modified stored state")
    report["read_only_verified"] = True


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--serve":
        serve(sys.argv[2], sys.argv[3])
        return 0
    require(len(sys.argv) == 1, "No public demonstration options are supported")
    spec = importlib.util.spec_from_file_location(
        "postgres_runner", ROOT / "scripts/verify_postgres.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner.main(workflow=workflow)


if __name__ == "__main__":
    raise SystemExit(main())
