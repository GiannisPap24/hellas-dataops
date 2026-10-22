"""Atomic chunk writes to the explicitly verified disposable PostgreSQL target."""

from dataclasses import asdict, dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from hellas_dataops.storage.mapping import (
    MEASUREMENTS,
    ChunkWrite,
    PersistenceError,
    WritePolicy,
    measurement_values,
    metadata_values,
    observation_values,
    request_values,
)
from hellas_dataops.storage.schema import (
    city_configs,
    datasets,
    ingestion_chunks,
    ingestion_runs,
    weather_hourly,
)
from hellas_dataops.storage.test_database import TestDatabaseConfig, test_connection


@dataclass(frozen=True, slots=True)
class WriteResult:
    inserted: int
    updated: int
    unchanged: int
    replayed: bool
    run_status: str


def _identity(
    connection: Connection,
    table: sa.Table,
    values: dict,
    keys: tuple[str, ...],
    *,
    lock: bool = False,
):
    predicate = sa.and_(*(table.c[name] == values[name] for name in keys))
    statement = sa.select(table).where(predicate)
    if lock:
        statement = statement.with_for_update()
    existing = connection.execute(statement).mappings().one_or_none()
    if existing is None:
        connection.execute(
            insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=list(keys))
        )
        existing = connection.execute(statement).mappings().one()
    if any(existing[key] != value for key, value in values.items()):
        raise PersistenceError(
            "identity_conflict", f"Immutable {table.name} identity differs"
        )
    return existing


def _prepare_run(
    connection: Connection, chunk: ChunkWrite, city_key: int, dataset_key: int
):
    run = chunk.run
    values = dict(
        run_id=run.run_id,
        city_config_id=city_key,
        dataset_key=dataset_key,
        requested_start=run.interval.start,
        requested_end=run.interval.end,
        created_at=run.created_at,
    )
    existing = _identity(connection, ingestion_runs, values, ("run_id",), lock=True)
    if existing["status"] not in ("planned", "running", "succeeded"):
        raise PersistenceError("run_state", "Failed runs require a new run identity")
    stored = (
        connection.execute(
            sa.select(ingestion_chunks)
            .where(ingestion_chunks.c.run_id == run.run_id)
            .order_by(ingestion_chunks.c.chunk_index)
        )
        .mappings()
        .all()
    )
    planned = [
        dict(
            run_id=run.run_id,
            chunk_index=index,
            retrieval_start=request.retrieval_interval.start,
            retrieval_end=request.retrieval_interval.end,
            retain_start=request.retain_interval.start,
            retain_end=request.retain_interval.end,
            request_provenance=request_values(request, run.policy),
        )
        for index, request in enumerate(run.requests, 1)
    ]
    if not stored:
        if existing["status"] != "planned":
            raise PersistenceError(
                "run_state", "Existing active run has no planned chunks"
            )
        connection.execute(ingestion_chunks.insert(), planned)
        stored = (
            connection.execute(
                sa.select(ingestion_chunks)
                .where(ingestion_chunks.c.run_id == run.run_id)
                .order_by(ingestion_chunks.c.chunk_index)
            )
            .mappings()
            .all()
        )
    if len(stored) != len(planned):
        raise PersistenceError(
            "plan_conflict", "Stored chunk count differs from run plan"
        )
    for actual, expected in zip(stored, planned, strict=True):
        if any(
            actual[k] != v for k, v in expected.items() if k != "request_provenance"
        ) or any(
            actual["request_provenance"].get(k) != v
            for k, v in expected["request_provenance"].items()
        ):
            raise PersistenceError(
                "plan_conflict", "Stored chunk request or policy differs"
            )
    return existing, stored


def _write(connection: Connection, chunk: ChunkWrite) -> WriteResult:
    run = chunk.run
    # Lock order: city configuration, then run, then chronological weather rows.
    # This also serializes overlapping intervals with no preexisting weather rows.
    city = _identity(
        connection,
        city_configs,
        asdict(run.city),
        ("city_id", "coordinate_version"),
        lock=True,
    )
    dataset = _identity(
        connection,
        datasets,
        dict(
            provider_id=run.dataset.provider_id,
            dataset_id=run.dataset.dataset_id,
            historical_model="era5",
            source_contract_version=chunk.request.contract_version,
            temperature_unit="celsius",
            humidity_unit="percent",
            precipitation_unit="mm",
            wind_speed_unit="m/s",
        ),
        ("provider_id", "dataset_id"),
    )
    city_key, dataset_key = city["city_config_id"], dataset["dataset_key"]
    stored_run, chunks = _prepare_run(connection, chunk, city_key, dataset_key)
    stored_chunk = chunks[chunk.chunk_index - 1]
    if stored_chunk["status"] == "succeeded":
        if (
            stored_chunk["request_provenance"].get("receipt", {}).get("fingerprint")
            != chunk.fingerprint
        ):
            raise PersistenceError(
                "replay_conflict", "Completed chunk input differs; use a new run"
            )
        return WriteResult(0, 0, len(chunk.observations), True, stored_run["status"])
    if stored_run["status"] == "succeeded" or stored_chunk["status"] != "planned":
        raise PersistenceError("chunk_state", "Only planned chunks can be written")

    interval = chunk.request.retain_interval
    predicate = sa.and_(
        weather_hourly.c.city_config_id == city_key,
        weather_hourly.c.dataset_key == dataset_key,
        weather_hourly.c.timestamp_utc >= interval.start,
        weather_hourly.c.timestamp_utc < interval.end,
    )
    existing = {
        row["timestamp_utc"]: row
        for row in connection.execute(
            sa.select(weather_hourly)
            .where(predicate)
            .order_by(weather_hourly.c.timestamp_utc)
            .with_for_update()
        ).mappings()
    }
    inserted = updated = unchanged = 0
    corrections = []
    for row in chunk.observations:
        old = existing.get(row.timestamp_utc)
        values = observation_values(
            row,
            city_key=city_key,
            dataset_key=dataset_key,
            run_id=run.run_id,
            chunk_index=chunk.chunk_index,
        )
        if old is None:
            connection.execute(weather_hourly.insert().values(**values))
            inserted += 1
        elif all(old[name] == value for name, value in measurement_values(row).items()):
            unchanged += 1
        else:
            if run.policy is WritePolicy.REJECT_CHANGES:
                raise PersistenceError(
                    "measurement_conflict",
                    "Existing values differ; explicit replacement policy required",
                )
            previous_time = connection.scalar(
                sa.select(ingestion_chunks.c.retrieved_at).where(
                    ingestion_chunks.c.run_id == old["source_run_id"],
                    ingestion_chunks.c.chunk_index == old["source_chunk_index"],
                )
            )
            if previous_time is None or chunk.retrieved_at <= previous_time:
                raise PersistenceError(
                    "stale_refresh",
                    "Corrections require a strictly newer retrieval timestamp",
                )
            corrections.append(
                dict(
                    timestamp_utc=row.timestamp_utc.isoformat(),
                    source_run_id=str(old["source_run_id"]),
                    source_chunk_index=old["source_chunk_index"],
                    measurements={name: old[name] for name in MEASUREMENTS},
                )
            )
            connection.execute(
                weather_hourly.update()
                .where(predicate, weather_hourly.c.timestamp_utc == row.timestamp_utc)
                .values(**values)
            )
            updated += 1
    receipt = dict(
        fingerprint=chunk.fingerprint,
        completed_at=chunk.completed_at.isoformat(),
        inserted=inserted,
        updated=updated,
        unchanged=unchanged,
        corrections=corrections,
    )
    provenance = request_values(chunk.request, run.policy) | {"receipt": receipt}
    connection.execute(
        ingestion_chunks.update()
        .where(
            ingestion_chunks.c.run_id == run.run_id,
            ingestion_chunks.c.chunk_index == chunk.chunk_index,
        )
        .values(
            status="succeeded",
            retrieved_at=chunk.retrieved_at,
            request_provenance=provenance,
            **metadata_values(chunk.metadata),
        )
    )
    complete = all(
        c["chunk_index"] == chunk.chunk_index or c["status"] == "succeeded"
        for c in chunks
    )
    status = "succeeded" if complete else "running"
    completion_times = [chunk.completed_at] + [
        datetime.fromisoformat(c["request_provenance"]["receipt"]["completed_at"])
        for c in chunks
        if c["status"] == "succeeded"
    ]
    connection.execute(
        ingestion_runs.update()
        .where(ingestion_runs.c.run_id == run.run_id)
        .values(status=status, finished_at=max(completion_times) if complete else None)
    )
    return WriteResult(inserted, updated, unchanged, False, status)


def persist_chunk(target: TestDatabaseConfig, chunk: ChunkWrite) -> WriteResult:
    """Own one transaction, including identity, provenance, weather, and status writes.

    Retrieval/normalization must already be complete. Only the approved disposable
    target is supported; there is no arbitrary connection URL or hidden HTTP call.
    A connection failure during commit can be ambiguous: retry the identical input
    and run identity, never assume an exception proves that the commit did not land.
    """
    if not isinstance(chunk, ChunkWrite):
        raise PersistenceError("invalid_chunk", "Supply a validated ChunkWrite")
    try:
        with test_connection(target) as connection, connection.begin():
            if connection.get_isolation_level() != "READ COMMITTED":
                raise PersistenceError(
                    "isolation_level", "READ COMMITTED isolation is required"
                )
            return _write(connection, chunk)
    except PersistenceError as error:
        raise PersistenceError(
            error.code, f"Run {chunk.run.run_id}, chunk {chunk.chunk_index}: {error}"
        ) from None
    except sa.exc.SQLAlchemyError:
        raise PersistenceError(
            "database_failure",
            f"Run {chunk.run.run_id}, chunk {chunk.chunk_index}: transaction failed; verify or replay identical input",
        ) from None
