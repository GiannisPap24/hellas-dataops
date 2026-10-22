"""Host-runner guard tests with synthetic Docker inspection objects only."""

import copy
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hellas_dataops.storage.test_database import TestDatabaseConfig


@pytest.fixture
def runner():
    path = Path(__file__).resolve().parents[2] / "scripts/verify_postgres.py"
    spec = importlib.util.spec_from_file_location("postgres_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def target():
    return TestDatabaseConfig(
        project="hellas-dataops-test-abcdef123456",
        port=15432,
        password="a" * 64,
        marker="b" * 64,
    )


@pytest.fixture
def container(target, runner):
    return {
        "Name": f"/{target.project}-postgres",
        "Config": {
            "Image": runner.IMAGE,
            "Labels": {
                "com.docker.compose.project": target.project,
                "io.hellas-dataops.instance": target.project,
                "io.hellas-dataops.purpose": "disposable-integration",
            },
        },
        "State": {"Health": {"Status": "healthy"}},
        "HostConfig": {"Privileged": False},
        "NetworkSettings": {
            "Networks": {f"{target.project}-network": {}},
            "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "15432"}]},
        },
        "Mounts": [
            {
                "Type": "volume",
                "Name": f"{target.project}-pgdata",
                "Destination": "/var/lib/postgresql",
            },
            {
                "Type": "bind",
                "Destination": "/run/secrets/postgres_password",
                "RW": False,
            },
            {
                "Type": "bind",
                "Destination": "/docker-entrypoint-initdb.d/001-ownership.sql",
                "RW": False,
            },
        ],
    }


def test_valid_isolated_container(runner, container, target):
    runner.verify_container(container, target)


@pytest.mark.parametrize(
    "path,value",
    [
        (("Config", "Image"), "postgres:latest"),
        (("Config", "Labels", "com.docker.compose.project"), "other-project"),
        (("Config", "Labels", "io.hellas-dataops.instance"), "other-instance"),
        (("Config", "Labels", "io.hellas-dataops.purpose"), "development"),
        (("Name",), "/another-container"),
        (("State", "Health", "Status"), "unhealthy"),
        (("HostConfig", "Privileged"), True),
        (("NetworkSettings", "Networks"), {"other-network": {}}),
        (("NetworkSettings", "Ports", "5432/tcp", 0, "HostIp"), "0.0.0.0"),
        (("NetworkSettings", "Ports", "5432/tcp", 0, "HostPort"), "5432"),
        (("Mounts", 0, "Name"), "other-volume"),
        (("Mounts", 0, "Destination"), "/var/lib/postgresql/data"),
        (("Mounts", 1, "RW"), True),
        (("Mounts", 2, "Destination"), "/unexpected"),
    ],
)
def test_container_mismatches_are_rejected(runner, container, target, path, value):
    changed = copy.deepcopy(container)
    cursor = changed
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(runner.RunnerError):
        runner.verify_container(changed, target)


def test_no_volume_rejected(runner, container, target):
    container["Mounts"] = container["Mounts"][1:]
    with pytest.raises(runner.RunnerError, match="volume"):
        runner.verify_container(container, target)


def test_command_failure_is_sanitized(runner, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=1, stdout="", stderr="private-credential"
        ),
    )
    with pytest.raises(runner.RunnerError) as caught:
        runner.command(["synthetic"])
    assert "private-credential" not in str(caught.value)


def test_command_success(runner, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=" linux\n"),
    )
    assert runner.command(["synthetic"]) == "linux"


@pytest.mark.parametrize(
    "bad_index,value",
    [(0, "C:/not-this-repository"), (1, "other-branch"), (2, "other-commit")],
)
def test_repository_gate(runner, monkeypatch, bad_index, value):
    outputs = [str(runner.ROOT), "main", runner.BASELINE]
    outputs[bad_index] = value
    monkeypatch.setattr(runner, "command", MagicMock(side_effect=outputs))
    with pytest.raises(runner.RunnerError):
        runner.verify_repository()


def test_status_retains_all_modified_filenames(runner, monkeypatch):
    outputs = [
        str(runner.ROOT),
        "main",
        runner.BASELINE,
        " M README.md\0?? scripts/verify_postgres.py\0",
    ]
    monkeypatch.setattr(runner, "command", MagicMock(side_effect=outputs))
    assert runner.verify_repository() == [
        "M README.md",
        "?? scripts/verify_postgres.py",
    ]


def test_failure_report_does_not_expose_exception_details(
    runner, monkeypatch, tmp_path
):
    monkeypatch.setattr(runner, "ROOT", tmp_path)

    def fail():
        raise ValueError("private-credential")

    monkeypatch.setattr(runner, "verify_repository", fail)
    assert runner.main() == 1
    report = next((tmp_path / "artifacts/postgres").rglob("report.json"))
    assert "private-credential" not in report.read_text()
    assert json.loads(report.read_text())["stage"] == "repository"


def test_unknown_changes_stop_before_docker(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "verify_repository", lambda: ["?? unknown.txt"])
    calls = MagicMock()
    monkeypatch.setattr(runner, "command", calls)
    assert runner.main() == 1
    calls.assert_not_called()


def test_driver_import_failure_stops_before_docker(runner, monkeypatch, tmp_path):
    import sys

    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "verify_repository", lambda: [])
    calls = MagicMock(return_value="")
    monkeypatch.setattr(runner, "command", calls)
    monkeypatch.setitem(sys.modules, "pg8000", None)
    assert runner.main() == 1
    assert len(calls.call_args_list) == 2  # Lock and package checks only.
    assert all("docker" not in call.args[0] for call in calls.call_args_list)
    report_path = next((tmp_path / "artifacts/postgres").rglob("report.json"))
    assert json.loads(report_path.read_text())["stage"] == "pg8000-driver-import"
    assert not (tmp_path / ".tools/postgres").exists()


@pytest.mark.parametrize("collision", [False, True])
def test_host_workflow_with_mocked_processes(
    runner, monkeypatch, tmp_path, container, target, collision
):
    """Exercise command order/reporting, not Docker or PostgreSQL behavior."""
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "verify_repository", lambda: ["M README.md"])
    monkeypatch.setattr(
        runner.uuid, "uuid4", lambda: SimpleNamespace(hex="abcdef1234560000")
    )
    monkeypatch.setattr(
        runner.secrets,
        "token_hex",
        MagicMock(side_effect=[target.password, target.marker]),
    )
    # Load the real pure-Python driver during preflight; all I/O below is mocked.
    probe = MagicMock()
    probe.__enter__.return_value = probe
    probe.getsockname.return_value = ("127.0.0.1", target.port)
    monkeypatch.setattr(runner.socket, "socket", lambda: probe)
    container["Image"] = "sha256:synthetic"
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if "--show-toplevel" in args:
            return str(tmp_path)
        if "version" in args and "compose" not in args:
            return json.dumps({"Server": {"Version": "synthetic"}})
        if "info" in args:
            return "linux"
        if "ls" in args:
            return "existing-resource" if collision else ""
        if "inspect" in args:
            if "container" in args:
                return json.dumps([container])
            return json.dumps([{"Labels": container["Config"]["Labels"]}])
        if "exec" in args:
            return "18.6 (synthetic)"
        return ""

    monkeypatch.setattr(runner, "command", command)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0,
            stdout=f"synthetic pytest success {target.password}",
            stderr="",
        ),
    )
    assert runner.main() == int(collision)
    report_path = next((tmp_path / "artifacts/postgres").rglob("report.json"))
    report = json.loads(report_path.read_text())
    assert report["status"] == ("failed" if collision else "passed")
    assert not any("prune" in c or "down" in c or "rm" in c for c in calls)
    if collision:
        assert report["stage"] == "resource-collision-check"
        assert not (tmp_path / ".tools").exists()
    else:
        assert report["pytest_exit_code"] == 0
        assert target.password not in (report_path.parent / "pytest.txt").read_text()
