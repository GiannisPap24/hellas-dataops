"""Connection-free proof of the selected SQLAlchemy pure-Python installation.

Run with the intended environment's Python -I. Imports are deliberately deferred
so a blocked native package produces a sanitized failure instead of a traceback.
"""

import importlib.metadata
import json
import sys
import sysconfig
from pathlib import Path


def verify() -> dict:
    if not sys.flags.isolated or sys.prefix == sys.base_prefix:
        raise RuntimeError("An isolated virtual-environment interpreter is required")
    distribution = importlib.metadata.distribution("sqlalchemy")
    package = Path(distribution.locate_file("sqlalchemy")).resolve()
    site_packages = Path(sysconfig.get_path("purelib")).resolve()
    if distribution.version != "2.1.6" or not package.is_relative_to(site_packages):
        raise RuntimeError("Unexpected SQLAlchemy version or installation location")
    wheel = distribution.read_text("WHEEL") or ""
    if "Root-Is-Purelib: true" not in wheel or "Tag: py3-none-any" not in wheel:
        raise RuntimeError("SQLAlchemy must be installed from a pure-Python build")
    if any(p.suffix.lower() in {".pyd", ".so", ".dll"} for p in package.rglob("*")):
        raise RuntimeError("SQLAlchemy contains native components")

    import alembic
    import pg8000
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql.pg8000 import PGDialect_pg8000
    from sqlalchemy.util._has_cython import HAS_CYEXTENSION, _all_cython_modules

    modules = _all_cython_modules()
    if (
        Path(sa.__file__).resolve().parent != package
        or sa.__version__ != distribution.version
        or HAS_CYEXTENSION
        or len(modules) != 8
        or any(
            m._is_compiled()
            or Path(m.__file__).suffix != ".py"
            or not Path(m.__file__).resolve().is_relative_to(package)
            for m in modules
        )
    ):
        raise RuntimeError("Unexpected SQLAlchemy module implementation")
    dialect = PGDialect_pg8000(dbapi=PGDialect_pg8000.import_dbapi())
    if dialect.driver != "pg8000" or "SELECT" not in str(
        sa.select(sa.literal(1)).compile(dialect=dialect)
    ):
        raise RuntimeError("SQLAlchemy Core or PostgreSQL dialect verification failed")
    return {
        "status": "passed",
        "sqlalchemy": sa.__version__,
        "pure_python": True,
        "native_files": 0,
        "python_extension_modules": len(modules),
        "wheel_tag": "py3-none-any",
        "installation": "active-virtual-environment",
        "alembic": alembic.__version__,
        "pg8000": pg8000.__version__,
        "dialect": dialect.driver,
    }


def main() -> int:
    try:
        print(json.dumps(verify(), sort_keys=True))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": "SQLAlchemy pure-Python environment verification failed",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
