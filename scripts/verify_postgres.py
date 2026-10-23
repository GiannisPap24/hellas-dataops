"""Host-only, non-destructive PostgreSQL verification; called by verify-postgres.ps1."""

import importlib.metadata
import json
import os
import secrets
import socket
import subprocess
import sys
import uuid
from pathlib import Path

BASELINE = "231058887e9e8c3bb20a5c0484679b0276bc47b4"
IMAGE = "postgres:18.6-bookworm"
ROOT = Path(__file__).resolve().parents[1]


class RunnerError(RuntimeError):
    """Only prewritten, credential-free diagnostics may be stored in reports."""

    def __init__(self, message: str, exit_code: int | None = None):
        super().__init__(message)
        self.exit_code = exit_code


def command(args, *, env=None, timeout=60):
    result = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        # Never print raw Docker/driver diagnostics or subprocess environments.
        raise RunnerError(
            f"Command failed (exit {result.returncode}); stage recorded in report",
            exit_code=result.returncode,
        )
    return result.stdout.strip()


def verify_repository():
    git = ["git", "--no-optional-locks"]
    if Path(command([*git, "rev-parse", "--show-toplevel"])).resolve() != ROOT:
        raise RunnerError("Wrong repository root")
    if command([*git, "branch", "--show-current"]) != "main":
        raise RunnerError("Expected main branch")
    if command([*git, "rev-parse", "HEAD"]) != BASELINE:
        raise RunnerError(
            "Checkpoint changed; review the host runner baseline before use"
        )
    status = command([*git, "status", "--porcelain=v1", "-z", "--untracked-files=all"])
    return [entry.lstrip() for entry in status.split("\0") if entry]


def verify_container(data, target):
    labels = data["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != target.project
        or labels.get("io.hellas-dataops.instance") != target.project
        or labels.get("io.hellas-dataops.purpose") != "disposable-integration"
        or data["Config"]["Image"] != IMAGE
        or data["Name"] != f"/{target.project}-postgres"
        or data["State"].get("Health", {}).get("Status") != "healthy"
        or data["HostConfig"].get("Privileged")
        or set(data["NetworkSettings"]["Networks"]) != {f"{target.project}-network"}
        or data["NetworkSettings"]["Ports"]
        != {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(target.port)}]}
    ):
        raise RunnerError(
            "Container identity, health, networking, or privilege mismatch"
        )
    volumes = [m for m in data["Mounts"] if m["Type"] == "volume"]
    if (
        len(volumes) != 1
        or volumes[0]["Name"] != f"{target.project}-pgdata"
        or volumes[0]["Destination"] != "/var/lib/postgresql"
    ):
        raise RunnerError("Unexpected database volume")
    expected = {
        "/run/secrets/postgres_password",
        "/docker-entrypoint-initdb.d/001-ownership.sql",
    }
    binds = [m for m in data["Mounts"] if m["Type"] == "bind"]
    if (
        len(binds) != 2
        or {m["Destination"] for m in binds} != expected
        or any(m["RW"] for m in binds)
    ):
        raise RunnerError("Unexpected writable or missing bind mounts")
    # Desktop may translate Windows bind paths into VM paths. Compose inputs are
    # built from this newly created local directory; do not compare VM path syntax.


def main(*, workflow=None) -> int:
    project = f"hellas-dataops-test-{uuid.uuid4().hex[:12]}"
    report_dir = ROOT / "artifacts" / ("demo" if workflow else "postgres") / project
    report_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "project": project,
        "status": "failed",
        "stage": "repository",
        "image": IMAGE,
    }
    directory = ROOT / ".tools" / "postgres" / project
    try:
        report["working_tree"] = verify_repository()
        report["checkpoint"] = BASELINE
        # Reviewed 3D.2 paths; accepted application/schema/dependencies stay unchanged.
        allowed = {
            ".gitignore",
            ".vscode/settings.json",
            ".vscode/tasks.json",
            ".vscode/launch.json",
            ".vscode/extensions.json",
            ".vscode/testing.env",
            "docs/vscode.md",
            "AGENTS.md",
            "README.md",
            "docs/architecture.md",
            "docs/development.md",
            "docs/postgres-testing.md",
            "docs/demo.md",
            "scripts/demo.ps1",
            "scripts/demo.py",
            "scripts/verify_postgres.py",
            "tests/unit/test_demo.py",
            "tests/unit/test_postgres_runner.py",
        }
        if any(
            line.split(" ", 1)[-1] not in allowed or line.startswith(("D ", "R ", "C "))
            for line in report["working_tree"]
        ):
            raise RunnerError(
                "Unexpected project changes; review before database execution"
            )
        report["stage"] = "locked-environment"
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.upper().startswith(("PG", "HELLAS_", "COMPOSE_", "DOCKER_"))
        }
        env.update(UV_CACHE_DIR=str(ROOT / ".uv-cache"), UV_PYTHON_DOWNLOADS="never")
        temporary = ROOT / ".tools/tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        env.update(
            TEMP=str(temporary),
            TMP=str(temporary),
            UV_LINK_MODE="copy",
            UV_PROJECT_ENVIRONMENT=str(ROOT / ".venv"),
        )
        env["COMPOSE_DISABLE_ENV_FILE"] = "1"
        uv = str(ROOT / ".tools/uv/bin/uv.exe")
        command([uv, "lock", "--check", "--offline"], env=env)
        # Build policy lives in pyproject.toml. Force replacement of older native
        # installations before importing any application/SQLAlchemy modules.
        command(
            [
                uv,
                "sync",
                "--locked",
                "--offline",
                "--python",
                sys.executable,
                "--no-binary-package",
                "sqlalchemy",
                "--reinstall-package",
                "sqlalchemy",
            ],
            env=env,
            timeout=180,
        )
        command([uv, "pip", "check", "--offline", "--python", sys.executable], env=env)
        report["stage"] = "sqlalchemy-pure-python"
        report["sqlalchemy_build"] = json.loads(
            command(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(ROOT / "scripts/verify_sqlalchemy.py"),
                ],
                env=env,
            )
        )
        if report["sqlalchemy_build"].get("status") != "passed":
            raise RunnerError("SQLAlchemy pure-Python preflight failed")
        report["stage"] = "pg8000-driver-import"
        try:
            import pg8000
            from sqlalchemy.dialects.postgresql.pg8000 import PGDialect_pg8000

            driver = PGDialect_pg8000.import_dbapi()
            dialect = PGDialect_pg8000(dbapi=driver)
            from hellas_dataops.storage.test_database import TestDatabaseConfig
        except ImportError:
            raise RunnerError(
                "pg8000 or its SQLAlchemy dialect could not be imported; verify the locked project environment."
            ) from None

        report["versions"] = {
            n: importlib.metadata.version(n)
            for n in ("pg8000", "sqlalchemy", "alembic", "fastapi", "uvicorn", "httpx2")
        }
        report["driver_implementation"] = "postgresql+pg8000"
        assert pg8000.__version__ == report["versions"]["pg8000"]
        assert dialect.driver == "pg8000"
        report["stage"] = "docker-linux-engine"
        docker = ["docker", "--context", "desktop-linux"]
        version = json.loads(
            command([*docker, "version", "--format", "{{json .}}"], env=env)
        )
        if (
            not version.get("Server")
            or command([*docker, "info", "--format", "{{.OSType}}"], env=env) != "linux"
        ):
            raise RunnerError("Docker Linux Server is unavailable")
        report["docker_server"] = version["Server"]["Version"]
        report["compose"] = command([*docker, "compose", "version", "--short"], env=env)
        report["stage"] = "resource-collision-check"
        for args in (
            [
                "container",
                "ls",
                "--all",
                "--filter",
                f"name=^/{project}-postgres$",
                "--format",
                "{{.Names}}",
            ],
            [
                "network",
                "ls",
                "--filter",
                f"name=^{project}-network$",
                "--format",
                "{{.Name}}",
            ],
            [
                "volume",
                "ls",
                "--filter",
                f"name=^{project}-pgdata$",
                "--format",
                "{{.Name}}",
            ],
        ):
            if command([*docker, *args], env=env):
                raise RunnerError(
                    "Resource already exists; no existing resource will be reused"
                )
        with socket.socket() as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        target = TestDatabaseConfig(
            project=project,
            port=port,
            password=secrets.token_hex(32),
            marker=secrets.token_hex(32),
        )
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "password.txt").write_text(target.password, encoding="utf-8")
        (directory / "init.sql").write_text(
            f"COMMENT ON DATABASE hellas_dataops_test IS '{target.ownership}';\n",
            encoding="utf-8",
        )
        config_file = directory / "target.json"
        config_file.write_text(
            json.dumps(
                {
                    "project": project,
                    "port": port,
                    "password": target.password,
                    "marker": target.marker,
                }
            ),
            encoding="utf-8",
        )
        # Secrets never enter command-line arguments or Compose environment output.
        env.update(
            HELLAS_TEST_PROJECT=project,
            HELLAS_TEST_PORT=str(port),
            HELLAS_TEST_DIRECTORY=directory.as_posix(),
        )
        compose = [
            *docker,
            "compose",
            "--project-name",
            project,
            "--file",
            str(ROOT / "compose.postgres-test.yaml"),
        ]
        report["stage"] = "compose-validation"
        command([*compose, "config", "--quiet"], env=env)
        report["stage"] = "official-image-pull"
        command([*compose, "pull", "postgres"], env=env, timeout=600)
        report["stage"] = "postgres-start-and-health"
        # A port race fails here; never stop another process to claim the port.
        command(
            [*compose, "up", "--detach", "--wait", "--wait-timeout", "120", "postgres"],
            env=env,
            timeout=180,
        )
        report["stage"] = "resource-ownership-verification"
        data = json.loads(
            command([*docker, "container", "inspect", f"{project}-postgres"], env=env)
        )[0]
        verify_container(data, target)
        for kind, name in (
            ("volume", f"{project}-pgdata"),
            ("network", f"{project}-network"),
        ):
            resource = json.loads(command([*docker, kind, "inspect", name], env=env))[0]
            if any(
                resource.get("Labels", {}).get(k) != v
                for k, v in {
                    "io.hellas-dataops.instance": project,
                    "io.hellas-dataops.purpose": "disposable-integration",
                    "com.docker.compose.project": project,
                }.items()
            ):
                raise RunnerError("Resource ownership mismatch")
        report["image_id"] = data["Image"]
        report["port"] = port
        if workflow is not None:
            workflow(target, config_file, env, report, report_dir)
        else:
            report["stage"] = "postgres-integration-tests"
            env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            # The explicit option is consumed by the integration plugin only.
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-m",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                    "-p",
                    "pytest_cov",
                    "tests/integration",
                    "--postgres-config",
                    str(config_file),
                    "--cov=src/hellas_dataops/storage",
                    "--cov=hellas_dataops.ingestion.coordinator",
                    "--cov=src/hellas_dataops/api",
                    "--cov-report=term-missing",
                    f"--cov-report=json:{report_dir / 'coverage.json'}",
                    "--tb=no",
                    "-q",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
            output = (
                (result.stdout + result.stderr)
                .replace(target.password, "[redacted]")
                .replace(target.marker, "[redacted]")
                .replace(str(ROOT), "<project>")
            )
            (report_dir / "pytest.txt").write_text(output, encoding="utf-8")
            report["pytest_exit_code"] = result.returncode
            print(output)
            if result.returncode:
                raise RunnerError(
                    "PostgreSQL integration tests failed; see sanitized pytest.txt"
                )
        report["postgres_version"] = command(
            [
                *docker,
                "exec",
                f"{project}-postgres",
                "psql",
                "-U",
                "hellas_dataops_test",
                "-d",
                "hellas_dataops_test",
                "-Atc",
                "SHOW server_version",
            ],
            env=env,
        )
        report["status"] = "passed"
        report["stage"] = "complete"
        return 0
    except (Exception, KeyboardInterrupt) as error:
        # Exception details may contain connection data; only known safe classes/stage.
        report["error_type"] = type(error).__name__
        if isinstance(error, RunnerError):
            report["error"] = str(error)
            report["command_exit_code"] = error.exit_code
            print(str(error))
        print(f"Verification stopped at {report['stage']} ({type(error).__name__}).")
        return 1
    finally:
        report["cleanup"] = (
            "No resources removed. Newly created resources, if any, retained for owner review."
        )
        (report_dir / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(f"Sanitized report: {report_dir.relative_to(ROOT)}\\report.json")
        print(f"Only this run's resource prefix: {project}")


if __name__ == "__main__":
    raise SystemExit(main())
