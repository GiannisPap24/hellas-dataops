"""Durable synthetic-run lifecycle on the existing five-table contract."""

import hashlib
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from hellas_dataops.domain import DatasetIdentity, HourlyInterval
from hellas_dataops.sources.cities import CityCoordinates
from hellas_dataops.sources.open_meteo import OpenMeteoMetadata
from hellas_dataops.storage.mapping import (
    ChunkWrite,
    PersistenceError,
    RunPlan,
    WritePolicy,
    _utc,
    metadata_values,
    request_values,
)
from hellas_dataops.storage.persistence import _identities, _prepare_plan, _write
from hellas_dataops.storage.schema import (
    city_configs,
    datasets,
    ingestion_chunks,
    ingestion_runs,
    weather_hourly,
)
from hellas_dataops.storage.test_database import TestDatabaseConfig, test_connection

OPERATION = "synthetic-sequential-v1"


@dataclass(frozen=True, slots=True)
class ChunkProgress:
    index: int
    status: str
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class RunProgress:
    plan: RunPlan
    status: str
    finished_at: datetime | None
    chunks: tuple[ChunkProgress, ...]

    @property
    def completed_chunks(self) -> int:
        return sum(c.status == "succeeded" for c in self.chunks)

    @property
    def failed_chunks(self) -> int:
        return sum(c.status == "failed" for c in self.chunks)

    @property
    def counts(self) -> tuple[int, int, int]:
        return tuple(
            sum(getattr(c, name) for c in self.chunks)
            for name in ("inserted", "updated", "unchanged")
        )


def _run_id(run_id: UUID) -> None:
    if not isinstance(run_id, UUID) or run_id.int == 0:
        raise PersistenceError("invalid_run", "Supply a nonzero run UUID")


def _decode(run, city, dataset, chunks) -> RunProgress:
    """Fail closed on incompatible plans or incomplete success receipts."""
    try:
        plan = RunPlan(
            run_id=run["run_id"],
            city=CityCoordinates(
                **{
                    k: city[k]
                    for k in ("city_id", "coordinate_version", "latitude", "longitude")
                }
            ),
            dataset=DatasetIdentity(
                provider_id=dataset["provider_id"], dataset_id=dataset["dataset_id"]
            ),
            interval=HourlyInterval(
                start=run["requested_start"], end=run["requested_end"]
            ),
            created_at=run["created_at"],
            policy=WritePolicy(chunks[0]["request_provenance"]["policy"]),
        )
        if (
            dataset["historical_model"],
            dataset["source_contract_version"],
            dataset["temperature_unit"],
            dataset["humidity_unit"],
            dataset["precipitation_unit"],
            dataset["wind_speed_unit"],
        ) != (
            "era5",
            plan.requests[0].contract_version,
            "celsius",
            "percent",
            "mm",
            "m/s",
        ):
            raise ValueError
        progress = []
        completions = []
        for index, (chunk, request) in enumerate(
            zip(chunks, plan.requests, strict=True), 1
        ):
            provenance = chunk["request_provenance"]
            failures = provenance["failures"]
            if not isinstance(failures, list):
                raise ValueError
            for failure in failures:
                failed_at = datetime.fromisoformat(failure["at"])
                _utc(failed_at)
                if (
                    failure["stage"] not in ("retrieve", "normalize", "persist")
                    or failed_at < plan.created_at
                ):
                    raise ValueError
            if (
                chunk["chunk_index"] != index
                or chunk["run_id"] != plan.run_id
                or any(
                    provenance.get(k) != v
                    for k, v in request_values(request, plan.policy).items()
                )
                or provenance.get("operation") != OPERATION
                or (
                    chunk["retrieval_start"],
                    chunk["retrieval_end"],
                    chunk["retain_start"],
                    chunk["retain_end"],
                )
                != (
                    request.retrieval_interval.start,
                    request.retrieval_interval.end,
                    request.retain_interval.start,
                    request.retain_interval.end,
                )
                or chunk["status"] not in ("planned", "failed", "succeeded")
            ):
                raise ValueError
            if chunk["status"] != "succeeded":
                if "receipt" in provenance:
                    raise ValueError
                progress.append(ChunkProgress(index, chunk["status"]))
                continue
            receipt = provenance["receipt"]
            counts = tuple(receipt[k] for k in ("inserted", "updated", "unchanged"))
            finished = datetime.fromisoformat(receipt["completed_at"])
            _utc(finished)
            _utc(chunk["retrieved_at"])
            metadata_values(
                OpenMeteoMetadata(
                    grid_latitude=chunk["grid_latitude"],
                    grid_longitude=chunk["grid_longitude"],
                    grid_elevation_m=chunk["grid_elevation_m"],
                    timezone=chunk["source_timezone"],
                    utc_offset_seconds=chunk["utc_offset_seconds"],
                    timezone_abbreviation=chunk["timezone_abbreviation"],
                )
            )
            if (
                any(type(n) is not int or n < 0 for n in counts)
                or sum(counts) != request.retain_interval.expected_hours
                or re.fullmatch(r"[0-9a-f]{64}", receipt["fingerprint"]) is None
                or not plan.created_at <= chunk["retrieved_at"] <= finished
                or not isinstance(receipt["corrections"], list)
                or len(receipt["corrections"]) != receipt["updated"]
            ):
                raise ValueError
            completions.append(finished)
            progress.append(
                ChunkProgress(index, "succeeded", *counts, receipt["fingerprint"])
            )
        status = run["status"]
        if run["finished_at"] is not None:
            _utc(run["finished_at"])
            if run["finished_at"] < plan.created_at:
                raise ValueError
        complete = len(completions) == len(chunks)
        if (
            status not in ("planned", "running", "failed", "succeeded")
            or (status == "succeeded") != complete
            or (status in ("succeeded", "failed")) != (run["finished_at"] is not None)
            or (status == "succeeded" and run["finished_at"] != max(completions))
            or (status == "failed") != any(c.status == "failed" for c in progress)
            or (status == "planned" and completions)
        ):
            raise ValueError
        return RunProgress(plan, status, run["finished_at"], tuple(progress))
    except ValueError, TypeError, KeyError, IndexError, AttributeError:
        raise PersistenceError(
            "invalid_progress",
            "Stored operational plan or success receipt is inconsistent; investigate before resume",
        ) from None


def _read(connection, run_id):
    run = (
        connection.execute(
            sa.select(ingestion_runs).where(ingestion_runs.c.run_id == run_id)
        )
        .mappings()
        .one_or_none()
    )
    if run is None:
        raise PersistenceError("run_not_found", "Run does not exist")
    # Same lock order as the writer: wait for any in-flight chunk transaction,
    # then reread state. Reads do not infer rollback from a lost acknowledgement.
    city = (
        connection.execute(
            sa.select(city_configs)
            .where(city_configs.c.city_config_id == run["city_config_id"])
            .with_for_update()
        )
        .mappings()
        .one()
    )
    run = (
        connection.execute(
            sa.select(ingestion_runs).where(ingestion_runs.c.run_id == run_id)
        )
        .mappings()
        .one()
    )
    dataset = (
        connection.execute(
            sa.select(datasets).where(datasets.c.dataset_key == run["dataset_key"])
        )
        .mappings()
        .one()
    )
    chunks = (
        connection.execute(
            sa.select(ingestion_chunks)
            .where(ingestion_chunks.c.run_id == run_id)
            .order_by(ingestion_chunks.c.chunk_index)
        )
        .mappings()
        .all()
    )
    state = _decode(run, city, dataset, chunks)
    for chunk, request in zip(state.chunks, state.plan.requests, strict=True):
        if chunk.status == "succeeded":
            retained = request.retain_interval
            count = connection.scalar(
                sa.select(sa.func.count())
                .select_from(weather_hourly)
                .where(
                    weather_hourly.c.city_config_id == run["city_config_id"],
                    weather_hourly.c.dataset_key == run["dataset_key"],
                    weather_hourly.c.timestamp_utc >= retained.start,
                    weather_hourly.c.timestamp_utc < retained.end,
                )
            )
            if count != retained.expected_hours:
                raise PersistenceError(
                    "invalid_progress",
                    "Completed chunk has missing weather rows; investigate before resume",
                )
    return state


class RunSession:
    """One leased connection. Every method finishes its transaction before returning."""

    def __init__(self, connection, run_id):
        self.connection = connection
        self.run_id = run_id

    @contextmanager
    def transaction(self):
        # Never reconnect silently after losing the session-level ownership lock.
        if self.connection.closed or self.connection.invalidated:
            raise PersistenceError(
                "outcome_unknown",
                "Connection lost; explicitly resume to reconcile durable progress",
            )
        with self.connection.begin():
            yield self.connection

    def load(self) -> RunProgress:
        with self.transaction() as connection:
            return _read(connection, self.run_id)

    def create(self, plan: RunPlan) -> RunProgress:
        if plan.run_id != self.run_id:
            raise PersistenceError("plan_conflict", "Run identity differs")
        with self.transaction() as connection:
            city, dataset = _identities(connection, plan)
            exists = connection.scalar(
                sa.select(ingestion_runs.c.run_id).where(
                    ingestion_runs.c.run_id == self.run_id
                )
            )
            if exists:
                stored = _read(connection, self.run_id)
                if stored.plan != plan:
                    raise PersistenceError(
                        "plan_conflict", "Existing immutable run plan differs"
                    )
                if stored.status != "succeeded":
                    raise PersistenceError(
                        "resume_required",
                        "Existing incomplete run requires explicit resume",
                    )
                return stored
            _prepare_plan(connection, plan, city, dataset)
            for index, request in enumerate(plan.requests, 1):
                connection.execute(
                    ingestion_chunks.update()
                    .where(
                        ingestion_chunks.c.run_id == self.run_id,
                        ingestion_chunks.c.chunk_index == index,
                    )
                    .values(
                        request_provenance=request_values(request, plan.policy)
                        | {"operation": OPERATION, "failures": []}
                    )
                )
            connection.execute(
                ingestion_runs.update()
                .where(ingestion_runs.c.run_id == self.run_id)
                .values(status="running")
            )
            return _read(connection, self.run_id)

    def resume(self, expected: RunPlan | None = None) -> RunProgress:
        with self.transaction() as connection:
            state = _read(connection, self.run_id)
            if expected is not None and state.plan != expected:
                raise PersistenceError(
                    "plan_conflict", "Resume must preserve the original run plan"
                )
            if state.status != "succeeded":
                connection.execute(
                    ingestion_chunks.update()
                    .where(
                        ingestion_chunks.c.run_id == self.run_id,
                        ingestion_chunks.c.status == "failed",
                    )
                    .values(status="planned")
                )
                connection.execute(
                    ingestion_runs.update()
                    .where(ingestion_runs.c.run_id == self.run_id)
                    .values(status="running", finished_at=None)
                )
            return _read(connection, self.run_id)

    def write(self, chunk: ChunkWrite):
        if chunk.run.run_id != self.run_id:
            raise PersistenceError("plan_conflict", "Chunk belongs to another run")
        with self.transaction() as connection:
            return _write(connection, chunk)

    def fail(self, index: int, stage: str, when: datetime) -> None:
        _utc(when)
        if stage not in ("retrieve", "normalize", "persist"):
            raise PersistenceError("invalid_failure", "Unknown operation stage")
        with self.transaction() as connection:
            state = _read(connection, self.run_id)
            if (
                type(index) is not int
                or not 1 <= index <= len(state.chunks)
                or when < state.plan.created_at
            ):
                raise PersistenceError(
                    "invalid_failure", "Invalid failure index or time"
                )
            if state.chunks[index - 1].status == "succeeded":
                raise PersistenceError(
                    "already_completed", "Cannot mark a committed chunk failed"
                )
            predicate = (
                ingestion_chunks.c.run_id == self.run_id,
                ingestion_chunks.c.chunk_index == index,
            )
            provenance = dict(
                connection.scalar(
                    sa.select(ingestion_chunks.c.request_provenance).where(*predicate)
                )
            )
            provenance["failures"] = [
                *provenance["failures"],
                {"stage": stage, "at": when.isoformat()},
            ]
            connection.execute(
                ingestion_chunks.update()
                .where(*predicate)
                .values(status="failed", request_provenance=provenance)
            )
            connection.execute(
                ingestion_runs.update()
                .where(ingestion_runs.c.run_id == self.run_id)
                .values(status="failed", finished_at=when)
            )


@contextmanager
def run_session(target: TestDatabaseConfig, run_id: UUID):
    """Nonblocking session lock; retained across retrieval, without an open transaction.

    NullPool closes the physical session on exit, releasing its advisory lock even
    after an error. A dead connection cannot silently regain an unowned session.
    """
    _run_id(run_id)
    key = int.from_bytes(
        hashlib.sha256(b"hellas-operation-v1" + run_id.bytes).digest()[:8], signed=True
    )
    try:
        with test_connection(target) as connection:
            if connection.get_isolation_level() != "READ COMMITTED":
                raise PersistenceError("isolation_level", "READ COMMITTED is required")
            acquired = connection.scalar(
                sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
            )
            connection.rollback()
            if not acquired:
                raise PersistenceError(
                    "run_busy",
                    "Another coordinator owns this run; retry explicitly later",
                )
            yield RunSession(connection, run_id)
    except sa.exc.SQLAlchemyError:
        raise PersistenceError(
            "outcome_unknown",
            "Database operation failed; inspect or explicitly resume this run",
        ) from None


def inspect_run(target: TestDatabaseConfig, run_id: UUID) -> RunProgress:
    """Allow status inspection during retrieval; briefly serialize with chunk writes."""
    _run_id(run_id)
    try:
        with test_connection(target) as connection, connection.begin():
            return _read(connection, run_id)
    except sa.exc.SQLAlchemyError:
        raise PersistenceError(
            "status_unavailable", "Cannot verify persisted run status"
        ) from None
