"""Local-only demonstration launcher with explicit disposable configuration."""

import argparse
from pathlib import Path

import uvicorn

from hellas_dataops.api.app import create_app
from hellas_dataops.storage.test_database import TestDatabaseError, load_test_database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local read-only synthetic weather API"
    )
    parser.add_argument("--database-config", required=True, type=Path)
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        choices=range(1024, 65536),
        metavar="1024-65535",
    )
    args = parser.parse_args(argv)
    try:
        target = load_test_database(args.database_config)
    except TestDatabaseError:
        parser.error("Invalid disposable database configuration")
    uvicorn.run(create_app(target), host="127.0.0.1", port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
