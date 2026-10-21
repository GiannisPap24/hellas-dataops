"""Initial PostgreSQL schema snapshot; no live metadata imports or data changes.

Revision: 20261022_0001
Parent: none
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261022_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Frozen schema snapshot; changes require a new revision after release.
    op.create_table(
        "city_configs",
        sa.Column(
            "city_config_id", sa.BigInteger(), sa.Identity(always=True), nullable=False
        ),
        sa.Column("city_id", sa.String(length=64), nullable=False),
        sa.Column("coordinate_version", sa.String(length=64), nullable=False),
        sa.Column("latitude", sa.Double(), nullable=False),
        sa.Column("longitude", sa.Double(), nullable=False),
        sa.CheckConstraint(
            "city_id IN ('athens', 'thessaloniki', 'patras', 'heraklion', 'larissa')",
            name=op.f("ck_city_configs_known_city"),
        ),
        sa.CheckConstraint(
            "coordinate_version ~ '^[a-z][a-z0-9]*([-_][a-z0-9]+)*$'",
            name=op.f("ck_city_configs_coordinate_version_slug"),
        ),
        sa.CheckConstraint(
            "latitude BETWEEN -90 AND 90", name=op.f("ck_city_configs_latitude_bounds")
        ),
        sa.CheckConstraint(
            "longitude BETWEEN -180 AND 180",
            name=op.f("ck_city_configs_longitude_bounds"),
        ),
        sa.PrimaryKeyConstraint("city_config_id", name=op.f("pk_city_configs")),
        sa.UniqueConstraint(
            "city_id",
            "coordinate_version",
            name=op.f("uq_city_configs_city_id_coordinate_version"),
        ),
    )
    op.create_table(
        "datasets",
        sa.Column(
            "dataset_key", sa.BigInteger(), sa.Identity(always=True), nullable=False
        ),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("historical_model", sa.String(length=64), nullable=False),
        sa.Column("source_contract_version", sa.String(length=64), nullable=False),
        sa.Column("temperature_unit", sa.String(length=16), nullable=False),
        sa.Column("humidity_unit", sa.String(length=16), nullable=False),
        sa.Column("precipitation_unit", sa.String(length=16), nullable=False),
        sa.Column("wind_speed_unit", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "dataset_id ~ '^[a-z][a-z0-9]*([-_][a-z0-9]+)*$'",
            name=op.f("ck_datasets_dataset_id_slug"),
        ),
        sa.CheckConstraint(
            "historical_model ~ '^[a-z][a-z0-9]*([-_][a-z0-9]+)*$'",
            name=op.f("ck_datasets_historical_model_slug"),
        ),
        sa.CheckConstraint(
            "provider_id ~ '^[a-z][a-z0-9]*([-_][a-z0-9]+)*$'",
            name=op.f("ck_datasets_provider_id_slug"),
        ),
        sa.CheckConstraint(
            "source_contract_version ~ '^[a-z][a-z0-9]*([-_][a-z0-9]+)*$'",
            name=op.f("ck_datasets_source_contract_version_slug"),
        ),
        sa.CheckConstraint(
            "temperature_unit = 'celsius' AND humidity_unit = 'percent' AND precipitation_unit = 'mm' AND wind_speed_unit = 'm/s'",
            name=op.f("ck_datasets_canonical_units"),
        ),
        sa.PrimaryKeyConstraint("dataset_key", name=op.f("pk_datasets")),
        sa.UniqueConstraint(
            "provider_id", "dataset_id", name=op.f("uq_datasets_provider_id_dataset_id")
        ),
    )
    op.create_table(
        "ingestion_runs",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("city_config_id", sa.BigInteger(), nullable=False),
        sa.Column("dataset_key", sa.BigInteger(), nullable=False),
        sa.Column("requested_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default="planned", nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)",
            name=op.f("ck_ingestion_runs_finished_status"),
        ),
        sa.CheckConstraint(
            "isfinite(requested_end) AND date_trunc('hour', requested_end AT TIME ZONE 'UTC') = requested_end AT TIME ZONE 'UTC'",
            name=op.f("ck_ingestion_runs_requested_end_hour"),
        ),
        sa.CheckConstraint(
            "isfinite(requested_start) AND date_trunc('hour', requested_start AT TIME ZONE 'UTC') = requested_start AT TIME ZONE 'UTC'",
            name=op.f("ck_ingestion_runs_requested_start_hour"),
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'running', 'succeeded', 'failed')",
            name=op.f("ck_ingestion_runs_status"),
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR (isfinite(finished_at) AND finished_at >= created_at)",
            name=op.f("ck_ingestion_runs_finished_at_order"),
        ),
        sa.CheckConstraint(
            "isfinite(created_at)", name=op.f("ck_ingestion_runs_created_at_finite")
        ),
        sa.CheckConstraint(
            "requested_start < requested_end",
            name=op.f("ck_ingestion_runs_requested_order"),
        ),
        sa.ForeignKeyConstraint(
            ["city_config_id"],
            ["city_configs.city_config_id"],
            name=op.f("fk_ingestion_runs_city_config_id_city_configs"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_key"],
            ["datasets.dataset_key"],
            name=op.f("fk_ingestion_runs_dataset_key_datasets"),
        ),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_ingestion_runs")),
        sa.UniqueConstraint(
            "run_id", "city_config_id", "dataset_key", name="uq_ingestion_runs_identity"
        ),
    )
    op.create_table(
        "ingestion_chunks",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("retrieval_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieval_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "request_provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=16), server_default="planned", nullable=False
        ),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grid_latitude", sa.Double(), nullable=True),
        sa.Column("grid_longitude", sa.Double(), nullable=True),
        sa.Column("grid_elevation_m", sa.Double(), nullable=True),
        sa.Column("source_timezone", sa.String(length=16), nullable=True),
        sa.Column("utc_offset_seconds", sa.Integer(), nullable=True),
        sa.Column("timezone_abbreviation", sa.String(length=8), nullable=True),
        sa.CheckConstraint(
            "grid_elevation_m > '-Infinity'::double precision AND grid_elevation_m < 'Infinity'::double precision",
            name=op.f("ck_ingestion_chunks_grid_elevation_finite"),
        ),
        sa.CheckConstraint(
            "isfinite(retain_end) AND date_trunc('hour', retain_end AT TIME ZONE 'UTC') = retain_end AT TIME ZONE 'UTC'",
            name=op.f("ck_ingestion_chunks_retain_end_hour"),
        ),
        sa.CheckConstraint(
            "isfinite(retain_start) AND date_trunc('hour', retain_start AT TIME ZONE 'UTC') = retain_start AT TIME ZONE 'UTC'",
            name=op.f("ck_ingestion_chunks_retain_start_hour"),
        ),
        sa.CheckConstraint(
            "isfinite(retrieval_end) AND date_trunc('day', retrieval_end AT TIME ZONE 'UTC') = retrieval_end AT TIME ZONE 'UTC'",
            name=op.f("ck_ingestion_chunks_retrieval_end_day"),
        ),
        sa.CheckConstraint(
            "isfinite(retrieval_start) AND date_trunc('day', retrieval_start AT TIME ZONE 'UTC') = retrieval_start AT TIME ZONE 'UTC'",
            name=op.f("ck_ingestion_chunks_retrieval_start_day"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(request_provenance) = 'object'",
            name=op.f("ck_ingestion_chunks_request_object"),
        ),
        sa.CheckConstraint(
            "retrieval_start < retrieval_end AND retrieval_end - retrieval_start <= INTERVAL '336 hours'",
            name=op.f("ck_ingestion_chunks_retrieval_span"),
        ),
        sa.CheckConstraint(
            "source_timezone IN ('GMT', 'UTC', 'Etc/GMT', 'Etc/UTC')",
            name=op.f("ck_ingestion_chunks_utc_timezone"),
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR (retrieved_at IS NOT NULL AND grid_latitude IS NOT NULL)",
            name=op.f("ck_ingestion_chunks_success_metadata"),
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'running', 'succeeded', 'failed')",
            name=op.f("ck_ingestion_chunks_status"),
        ),
        sa.CheckConstraint(
            "timezone_abbreviation IN ('GMT', 'UTC')",
            name=op.f("ck_ingestion_chunks_utc_abbreviation"),
        ),
        sa.CheckConstraint(
            "(grid_latitude IS NULL AND grid_longitude IS NULL AND source_timezone IS NULL AND utc_offset_seconds IS NULL AND grid_elevation_m IS NULL AND timezone_abbreviation IS NULL) OR (grid_latitude IS NOT NULL AND grid_longitude IS NOT NULL AND source_timezone IS NOT NULL AND utc_offset_seconds IS NOT NULL)",
            name=op.f("ck_ingestion_chunks_metadata_complete"),
        ),
        sa.CheckConstraint(
            "chunk_index > 0", name=op.f("ck_ingestion_chunks_positive_index")
        ),
        sa.CheckConstraint(
            "grid_latitude BETWEEN -90 AND 90",
            name=op.f("ck_ingestion_chunks_grid_latitude_bounds"),
        ),
        sa.CheckConstraint(
            "grid_longitude BETWEEN -180 AND 180",
            name=op.f("ck_ingestion_chunks_grid_longitude_bounds"),
        ),
        sa.CheckConstraint(
            "retrieval_start <= retain_start AND retain_start < retain_end AND retain_end <= retrieval_end",
            name=op.f("ck_ingestion_chunks_retained_bounds"),
        ),
        sa.CheckConstraint(
            "retrieved_at IS NULL OR isfinite(retrieved_at)",
            name=op.f("ck_ingestion_chunks_retrieved_at_finite"),
        ),
        sa.CheckConstraint(
            "utc_offset_seconds = 0", name=op.f("ck_ingestion_chunks_utc_offset")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ingestion_runs.run_id"],
            name=op.f("fk_ingestion_chunks_run_id_ingestion_runs"),
        ),
        sa.PrimaryKeyConstraint(
            "run_id", "chunk_index", name=op.f("pk_ingestion_chunks")
        ),
    )
    op.create_table(
        "weather_hourly",
        sa.Column(
            "city_config_id", sa.BigInteger(), autoincrement=False, nullable=False
        ),
        sa.Column("dataset_key", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("timestamp_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temperature_c", sa.Double(), nullable=True),
        sa.Column("relative_humidity_pct", sa.Double(), nullable=True),
        sa.Column("precipitation_mm", sa.Double(), nullable=True),
        sa.Column("wind_speed_ms", sa.Double(), nullable=True),
        sa.Column("source_run_id", sa.UUID(), nullable=False),
        sa.Column("source_chunk_index", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "isfinite(timestamp_utc) AND date_trunc('hour', timestamp_utc AT TIME ZONE 'UTC') = timestamp_utc AT TIME ZONE 'UTC'",
            name=op.f("ck_weather_hourly_timestamp_utc_hour"),
        ),
        sa.CheckConstraint(
            "precipitation_mm >= 0 AND precipitation_mm < 'Infinity'::double precision",
            name=op.f("ck_weather_hourly_precipitation_bounds"),
        ),
        sa.CheckConstraint(
            "temperature_c >= -273.15 AND temperature_c < 'Infinity'::double precision",
            name=op.f("ck_weather_hourly_temperature_bounds"),
        ),
        sa.CheckConstraint(
            "wind_speed_ms >= 0 AND wind_speed_ms < 'Infinity'::double precision",
            name=op.f("ck_weather_hourly_wind_bounds"),
        ),
        sa.CheckConstraint(
            "relative_humidity_pct BETWEEN 0 AND 100",
            name=op.f("ck_weather_hourly_humidity_bounds"),
        ),
        sa.ForeignKeyConstraint(
            ["city_config_id"],
            ["city_configs.city_config_id"],
            name=op.f("fk_weather_hourly_city_config_id_city_configs"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_key"],
            ["datasets.dataset_key"],
            name=op.f("fk_weather_hourly_dataset_key_datasets"),
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id", "city_config_id", "dataset_key"],
            [
                "ingestion_runs.run_id",
                "ingestion_runs.city_config_id",
                "ingestion_runs.dataset_key",
            ],
            name=op.f("fk_weather_hourly_source_run_id_ingestion_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id", "source_chunk_index"],
            ["ingestion_chunks.run_id", "ingestion_chunks.chunk_index"],
            name=op.f("fk_weather_hourly_source_run_id_ingestion_chunks"),
        ),
        sa.PrimaryKeyConstraint(
            "city_config_id",
            "dataset_key",
            "timestamp_utc",
            name=op.f("pk_weather_hourly"),
        ),
    )
    op.create_index(
        "ix_weather_hourly_source_chunk",
        "weather_hourly",
        ["source_run_id", "source_chunk_index"],
        unique=False,
    )


def downgrade() -> None:
    # Reverse dependency order. This destructive SQL is only emitted in 3A.
    op.drop_index("ix_weather_hourly_source_chunk", table_name="weather_hourly")
    op.drop_table("weather_hourly")
    op.drop_table("ingestion_chunks")
    op.drop_table("ingestion_runs")
    op.drop_table("datasets")
    op.drop_table("city_configs")
