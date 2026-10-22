"""Alembic entry point: offline SQL or explicitly guarded test-database execution."""

from alembic import context

from hellas_dataops.storage.migration import run_migrations

run_migrations(context)
