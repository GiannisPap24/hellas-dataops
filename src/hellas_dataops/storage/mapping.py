"""Validated, immutable write inputs and explicit domain-to-storage mappings."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from hellas_dataops.domain import (
    DatasetIdentity,
    HourlyInterval,
    HourlyObservation,
    validate_batch,
)
from hellas_dataops.sources.cities import CityCoordinates
from hellas_dataops.sources.open_meteo import OpenMeteoMetadata
from hellas_dataops.sources.open_meteo_requests import (
    OpenMeteoRequest,
    plan_open_meteo_requests,
)

MEASUREMENTS = (
    "temperature_c",
    "relative_humidity_pct",
    "precipitation_mm",
    "wind_speed_ms",
)


class PersistenceError(ValueError):
    """Stable, credential-free error code; never includes driver diagnostics."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class WritePolicy(StrEnum):
    REJECT_CHANGES = "reject_changes"
    REPLACE = "replace"


def _utc(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise PersistenceError(
            "invalid_time", "Execution timestamps must be aware UTC datetimes"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RunPlan:
    run_id: UUID
    city: CityCoordinates
    dataset: DatasetIdentity
    interval: HourlyInterval
    created_at: datetime
    policy: WritePolicy = WritePolicy.REJECT_CHANGES

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, UUID) or self.run_id.int == 0:
            raise PersistenceError("invalid_run", "Supply a nonzero UUID")
        if not isinstance(self.policy, WritePolicy):
            raise PersistenceError(
                "invalid_policy", "Supply an explicit WritePolicy enum"
            )
        _utc(self.created_at)
        _ = (
            self.requests
        )  # Validate the existing source identity and planning contract.

    @property
    def requests(self) -> tuple[OpenMeteoRequest, ...]:
        return plan_open_meteo_requests(
            self.city, dataset=self.dataset, interval=self.interval
        )


def metadata_values(metadata: OpenMeteoMetadata) -> dict[str, object]:
    """Validate manually constructed metadata as well as normalizer results."""
    if not isinstance(metadata, OpenMeteoMetadata):
        raise PersistenceError("invalid_metadata", "Supply normalized source metadata")
    for field, limit in (
        ("grid_latitude", 90),
        ("grid_longitude", 180),
        ("grid_elevation_m", None),
    ):
        value = getattr(metadata, field)
        if field == "grid_elevation_m" and value is None:
            continue
        try:
            valid = type(value) in (int, float) and math.isfinite(value)
            valid = valid and (limit is None or -limit <= value <= limit)
        except OverflowError:
            valid = False
        if not valid:
            raise PersistenceError("invalid_metadata", f"Invalid {field}")
    if (
        metadata.timezone not in ("GMT", "UTC", "Etc/GMT", "Etc/UTC")
        or type(metadata.utc_offset_seconds) is not int
        or metadata.utc_offset_seconds != 0
        or metadata.timezone_abbreviation not in (None, "GMT", "UTC")
    ):
        raise PersistenceError(
            "invalid_metadata", "Source timezone metadata must describe UTC"
        )
    result = asdict(metadata)
    result["source_timezone"] = result.pop("timezone")
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class ChunkWrite:
    run: RunPlan
    chunk_index: int
    observations: tuple[HourlyObservation, ...]
    metadata: OpenMeteoMetadata
    retrieved_at: datetime
    completed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.run, RunPlan):
            raise PersistenceError("invalid_run", "Supply a RunPlan")
        if type(self.chunk_index) is not int or not 1 <= self.chunk_index <= len(
            self.run.requests
        ):
            raise PersistenceError(
                "invalid_chunk", "Chunk index is one-based and must belong to the run"
            )
        _utc(self.retrieved_at)
        _utc(self.completed_at)
        if not self.run.created_at <= self.retrieved_at <= self.completed_at:
            raise PersistenceError(
                "invalid_time", "Require created_at <= retrieved_at <= completed_at"
            )
        metadata_values(self.metadata)
        rows = validate_batch(
            self.observations,
            city_id=self.run.city.city_id,
            dataset=self.run.dataset,
            interval=self.request.retain_interval,
        )
        object.__setattr__(self, "observations", rows)

    @property
    def request(self) -> OpenMeteoRequest:
        return self.run.requests[self.chunk_index - 1]

    @property
    def fingerprint(self) -> str:
        # This is a canonical input digest, NOT a raw HTTP response checksum.
        content = {
            "request": json.loads(self.request.to_json()),
            "policy": self.run.policy.value,
            "retrieved_at": self.retrieved_at.isoformat(),
            "metadata": metadata_values(self.metadata),
            "observations": [
                dict(timestamp=row.timestamp_utc.isoformat(), **measurement_values(row))
                for row in self.observations
            ],
        }
        encoded = json.dumps(
            content, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def measurement_values(row: HourlyObservation) -> dict[str, float | None]:
    return {name: getattr(row, name) for name in MEASUREMENTS}


def observation_values(
    row: HourlyObservation,
    *,
    city_key: int,
    dataset_key: int,
    run_id: UUID,
    chunk_index: int,
) -> dict[str, object]:
    return dict(
        city_config_id=city_key,
        dataset_key=dataset_key,
        timestamp_utc=row.timestamp_utc,
        source_run_id=run_id,
        source_chunk_index=chunk_index,
        **measurement_values(row),
    )


def request_values(request: OpenMeteoRequest, policy: WritePolicy) -> dict[str, object]:
    return {
        "storage_contract": "chunk-write-v1",
        "request": json.loads(request.to_json()),
        "policy": policy.value,
    }
