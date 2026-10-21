"""Alembic execution entry point; deliberately supports offline SQL only."""

from alembic import context

from hellas_dataops.storage.migration import run_migrations

run_migrations(context)
