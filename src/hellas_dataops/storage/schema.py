"""PostgreSQL Core schema only: importing this module never creates an engine."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = sa.MetaData(
    naming_convention={
        "pk": "pk_%(table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    }
)


def _slug(column: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} ~ '^[a-z][a-z0-9]*([-_][a-z0-9]+)*$'", name=f"{column}_slug"
    )


def _aligned(column: str, unit: str = "hour") -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"isfinite({column}) AND "
        f"date_trunc('{unit}', {column} AT TIME ZONE 'UTC') = "
        f"{column} AT TIME ZONE 'UTC'",
        name=f"{column}_{unit}",
    )


def _status() -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "status IN ('planned', 'running', 'succeeded', 'failed')", name="status"
    )


city_configs = sa.Table(
    "city_configs",
    metadata,
    sa.Column(
        "city_config_id", sa.BigInteger, sa.Identity(always=True), primary_key=True
    ),
    sa.Column("city_id", sa.String(64), nullable=False),
    sa.Column("coordinate_version", sa.String(64), nullable=False),
    sa.Column("latitude", sa.Double, nullable=False),
    sa.Column("longitude", sa.Double, nullable=False),
    sa.UniqueConstraint("city_id", "coordinate_version"),
    sa.CheckConstraint(
        "city_id IN ('athens', 'thessaloniki', 'patras', 'heraklion', 'larissa')",
        name="known_city",
    ),
    _slug("coordinate_version"),
    sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_bounds"),
    sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_bounds"),
)

datasets = sa.Table(
    "datasets",
    metadata,
    sa.Column("dataset_key", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("provider_id", sa.String(64), nullable=False),
    sa.Column("dataset_id", sa.String(64), nullable=False),
    sa.Column("historical_model", sa.String(64), nullable=False),
    sa.Column("source_contract_version", sa.String(64), nullable=False),
    sa.Column("temperature_unit", sa.String(16), nullable=False),
    sa.Column("humidity_unit", sa.String(16), nullable=False),
    sa.Column("precipitation_unit", sa.String(16), nullable=False),
    sa.Column("wind_speed_unit", sa.String(16), nullable=False),
    sa.UniqueConstraint("provider_id", "dataset_id"),
    _slug("provider_id"),
    _slug("dataset_id"),
    _slug("historical_model"),
    _slug("source_contract_version"),
    sa.CheckConstraint(
        "temperature_unit = 'celsius' AND humidity_unit = 'percent' AND "
        "precipitation_unit = 'mm' AND wind_speed_unit = 'm/s'",
        name="canonical_units",
    ),
)

ingestion_runs = sa.Table(
    "ingestion_runs",
    metadata,
    sa.Column("run_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("city_config_id", sa.BigInteger, nullable=False),
    sa.Column("dataset_key", sa.BigInteger, nullable=False),
    sa.Column("requested_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("requested_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="planned"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.ForeignKeyConstraint(["city_config_id"], ["city_configs.city_config_id"]),
    sa.ForeignKeyConstraint(["dataset_key"], ["datasets.dataset_key"]),
    # Required target for the weather FK that binds lineage to the same identity.
    sa.UniqueConstraint(
        "run_id", "city_config_id", "dataset_key", name="uq_ingestion_runs_identity"
    ),
    _aligned("requested_start"),
    _aligned("requested_end"),
    sa.CheckConstraint("requested_start < requested_end", name="requested_order"),
    _status(),
    sa.CheckConstraint("isfinite(created_at)", name="created_at_finite"),
    sa.CheckConstraint(
        "finished_at IS NULL OR (isfinite(finished_at) AND finished_at >= created_at)",
        name="finished_at_order",
    ),
    sa.CheckConstraint(
        "(status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)",
        name="finished_status",
    ),
)

ingestion_chunks = sa.Table(
    "ingestion_chunks",
    metadata,
    sa.Column("run_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("chunk_index", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("retrieval_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("retrieval_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("retain_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("retain_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("request_provenance", JSONB, nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="planned"),
    sa.Column("retrieved_at", sa.DateTime(timezone=True)),
    sa.Column("grid_latitude", sa.Double),
    sa.Column("grid_longitude", sa.Double),
    sa.Column("grid_elevation_m", sa.Double),
    sa.Column("source_timezone", sa.String(16)),
    sa.Column("utc_offset_seconds", sa.Integer),
    sa.Column("timezone_abbreviation", sa.String(8)),
    sa.ForeignKeyConstraint(["run_id"], ["ingestion_runs.run_id"]),
    sa.CheckConstraint("chunk_index > 0", name="positive_index"),
    _aligned("retrieval_start", "day"),
    _aligned("retrieval_end", "day"),
    _aligned("retain_start"),
    _aligned("retain_end"),
    sa.CheckConstraint(
        "retrieval_start < retrieval_end AND "
        "retrieval_end - retrieval_start <= INTERVAL '336 hours'",
        name="retrieval_span",
    ),
    sa.CheckConstraint(
        "retrieval_start <= retain_start AND retain_start < retain_end AND "
        "retain_end <= retrieval_end",
        name="retained_bounds",
    ),
    sa.CheckConstraint(
        "jsonb_typeof(request_provenance) = 'object'", name="request_object"
    ),
    _status(),
    sa.CheckConstraint(
        "retrieved_at IS NULL OR isfinite(retrieved_at)", name="retrieved_at_finite"
    ),
    sa.CheckConstraint("grid_latitude BETWEEN -90 AND 90", name="grid_latitude_bounds"),
    sa.CheckConstraint(
        "grid_longitude BETWEEN -180 AND 180", name="grid_longitude_bounds"
    ),
    sa.CheckConstraint(
        "grid_elevation_m > '-Infinity'::double precision AND "
        "grid_elevation_m < 'Infinity'::double precision",
        name="grid_elevation_finite",
    ),
    sa.CheckConstraint(
        "source_timezone IN ('GMT', 'UTC', 'Etc/GMT', 'Etc/UTC')", name="utc_timezone"
    ),
    sa.CheckConstraint("utc_offset_seconds = 0", name="utc_offset"),
    sa.CheckConstraint(
        "timezone_abbreviation IN ('GMT', 'UTC')", name="utc_abbreviation"
    ),
    sa.CheckConstraint(
        "(grid_latitude IS NULL AND grid_longitude IS NULL AND source_timezone IS NULL "
        "AND utc_offset_seconds IS NULL AND grid_elevation_m IS NULL "
        "AND timezone_abbreviation IS NULL) OR "
        "(grid_latitude IS NOT NULL AND grid_longitude IS NOT NULL "
        "AND source_timezone IS NOT NULL AND utc_offset_seconds IS NOT NULL)",
        name="metadata_complete",
    ),
    sa.CheckConstraint(
        "status <> 'succeeded' OR (retrieved_at IS NOT NULL AND grid_latitude IS NOT NULL)",
        name="success_metadata",
    ),
)

weather_hourly = sa.Table(
    "weather_hourly",
    metadata,
    sa.Column("city_config_id", sa.BigInteger, primary_key=True, autoincrement=False),
    sa.Column("dataset_key", sa.BigInteger, primary_key=True, autoincrement=False),
    sa.Column("timestamp_utc", sa.DateTime(timezone=True), primary_key=True),
    sa.Column("temperature_c", sa.Double),
    sa.Column("relative_humidity_pct", sa.Double),
    sa.Column("precipitation_mm", sa.Double),
    sa.Column("wind_speed_ms", sa.Double),
    sa.Column("source_run_id", UUID(as_uuid=True), nullable=False),
    sa.Column("source_chunk_index", sa.Integer, nullable=False),
    sa.ForeignKeyConstraint(["city_config_id"], ["city_configs.city_config_id"]),
    sa.ForeignKeyConstraint(["dataset_key"], ["datasets.dataset_key"]),
    sa.ForeignKeyConstraint(
        ["source_run_id", "source_chunk_index"],
        ["ingestion_chunks.run_id", "ingestion_chunks.chunk_index"],
    ),
    sa.ForeignKeyConstraint(
        ["source_run_id", "city_config_id", "dataset_key"],
        [
            "ingestion_runs.run_id",
            "ingestion_runs.city_config_id",
            "ingestion_runs.dataset_key",
        ],
    ),
    _aligned("timestamp_utc"),
    sa.CheckConstraint(
        "temperature_c >= -273.15 AND temperature_c < 'Infinity'::double precision",
        name="temperature_bounds",
    ),
    sa.CheckConstraint(
        "relative_humidity_pct BETWEEN 0 AND 100", name="humidity_bounds"
    ),
    sa.CheckConstraint(
        "precipitation_mm >= 0 AND precipitation_mm < 'Infinity'::double precision",
        name="precipitation_bounds",
    ),
    sa.CheckConstraint(
        "wind_speed_ms >= 0 AND wind_speed_ms < 'Infinity'::double precision",
        name="wind_bounds",
    ),
)

# Reverse lineage lookup; city/dataset/time queries use the primary-key index.
sa.Index(
    "ix_weather_hourly_source_chunk",
    weather_hourly.c.source_run_id,
    weather_hourly.c.source_chunk_index,
)
