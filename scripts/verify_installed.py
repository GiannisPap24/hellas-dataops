"""Check a distribution installation using Python -I in a fresh environment.

Usage: <fresh-env-python> -I scripts/verify_installed.py
This is an installation check, not a replacement for the unit test suite.
"""

import importlib
import importlib.metadata
import json
import site
import sys
from pathlib import Path


def main() -> None:
    """Reject source-tree imports, global dependency leakage, and editable installs."""
    if not sys.flags.isolated or sys.prefix == sys.base_prefix:
        raise RuntimeError("Run with -I using a fresh virtual environment")
    if site.ENABLE_USER_SITE:
        raise RuntimeError("User-site packages must be disabled")

    site_packages = Path(sys.prefix).resolve() / "Lib" / "site-packages"
    modules = (
        "hellas_dataops",
        "hellas_dataops.config",
        "hellas_dataops.domain",
        "hellas_dataops.domain.models",
        "hellas_dataops.domain.validation",
        "hellas_dataops.domain.errors",
        "hellas_dataops.sources",
        "hellas_dataops.sources.open_meteo",
        "hellas_dataops.sources.cities",
        "hellas_dataops.sources.open_meteo_requests",
        "hellas_dataops.sources.open_meteo_client",
        "hellas_dataops.ingestion",
        "hellas_dataops.ingestion.source_pipeline",
        "hellas_dataops.storage",
        "hellas_dataops.storage.schema",
        "hellas_dataops.storage.migration",
        "hellas_dataops.storage.test_database",
        "hellas_dataops.storage.migrations",
        "hellas_dataops.storage.migrations.versions",
        "hellas_dataops.api",
        "hellas_dataops.api.routes",
        "pydantic",
        "pydantic_settings",
        "pydantic_core",
        "sqlalchemy",
        "alembic",
        "pg8000",
        "pg8000.dbapi",
        "scramp",
        "asn1crypto",
        "dateutil",
        "six",
    )
    for name in modules:
        module = importlib.import_module(name)
        location = Path(module.__file__).resolve()
        if not location.is_relative_to(site_packages):
            raise RuntimeError(f"{name} was imported outside this environment")

    distribution = importlib.metadata.distribution("hellas-dataops")
    # Dialect construction imports the selected DBAPI without opening a connection.
    from sqlalchemy.dialects.postgresql.pg8000 import PGDialect_pg8000

    if PGDialect_pg8000(dbapi=PGDialect_pg8000.import_dbapi()).driver != "pg8000":
        raise RuntimeError("Installed PostgreSQL dialect mismatch")
    direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
    if direct_url.get("dir_info", {}).get("editable", False):
        raise RuntimeError("Distribution verification must not use an editable install")

    from hellas_dataops.config import Settings

    settings = Settings(environment="test", log_level="WARNING")
    if settings.environment != "test" or settings.log_level != "WARNING":
        raise RuntimeError("Installed configuration contract failed")

    from datetime import UTC, datetime

    from hellas_dataops.domain import HourlyInterval
    from hellas_dataops.sources.cities import CITY_REGISTRY
    from hellas_dataops.sources.open_meteo_requests import (
        ERA5_DATASET,
        plan_open_meteo_requests,
    )

    requests = plan_open_meteo_requests(
        CITY_REGISTRY["athens"],
        dataset=ERA5_DATASET,
        interval=HourlyInterval(
            start=datetime(2025, 1, 1, 5, tzinfo=UTC),
            end=datetime(2025, 1, 16, 3, tzinfo=UTC),
        ),
    )
    if (
        len(requests) != 2
        or sum(r.retain_interval.expected_hours for r in requests) != 358
    ):
        raise RuntimeError("Installed request planning contract failed")
    if dict(requests[0].parameters)["models"] != "era5":
        raise RuntimeError("Installed request model selection failed")

    from contextlib import contextmanager
    from io import BytesIO

    from hellas_dataops.sources.open_meteo_client import fetch_open_meteo

    class Response:
        status = 200

        def __init__(self):
            self.stream = BytesIO(b'{"offline":true}')

        def getheaders(self):
            return [("Content-Type", "application/json")]

        def read1(self, size):
            return self.stream.read(size)

    @contextmanager
    def transport(request, *, timeout_seconds):
        response = Response()
        try:
            yield response
        finally:
            response.stream.close()

    if fetch_open_meteo(requests[0], transport=transport) != {"offline": True}:
        raise RuntimeError("Installed offline HTTP client contract failed")

    from io import StringIO

    from alembic import command

    from hellas_dataops.storage import metadata
    from hellas_dataops.storage.migration import migration_config

    if len(metadata.tables) != 5:
        raise RuntimeError("Installed storage schema is incomplete")
    sql = StringIO()
    command.upgrade(migration_config(sql), "head", sql=True)
    if "CREATE TABLE weather_hourly" not in sql.getvalue():
        raise RuntimeError("Installed offline migration failed")
    print(f"Verified {distribution.metadata['Name']} {distribution.version}")
    print(f"Python {sys.version.split()[0]}; {len(modules)} isolated module imports")


if __name__ == "__main__":
    main()
