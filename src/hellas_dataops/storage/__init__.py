"""PostgreSQL Core schema; importing it never opens a database connection."""

from hellas_dataops.storage.schema import (
    city_configs,
    datasets,
    ingestion_chunks,
    ingestion_runs,
    metadata,
    weather_hourly,
)

__all__ = [
    "city_configs",
    "datasets",
    "ingestion_chunks",
    "ingestion_runs",
    "metadata",
    "weather_hourly",
]
