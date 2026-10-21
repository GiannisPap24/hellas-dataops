"""Immutable canonical weather objects, independent of transport and storage."""

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hellas_dataops.domain.errors import DomainValidationError

SUPPORTED_CITY_IDS = frozenset(
    {"athens", "thessaloniki", "patras", "heraklion", "larissa"}
)
HOUR = timedelta(hours=1)
_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*")


def validate_city_id(value: str) -> None:
    """Require a stable identifier from the agreed project city catalogue."""
    if not isinstance(value, str) or value not in SUPPORTED_CITY_IDS:
        raise DomainValidationError(
            "invalid_city_id",
            "city_id",
            f"use one of: {', '.join(sorted(SUPPORTED_CITY_IDS))}",
        )


def _validate_identifier(value: str, field: str) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or _IDENTIFIER.fullmatch(value) is None
    ):
        raise DomainValidationError(
            "invalid_identifier",
            field,
            "use 1-64 lowercase ASCII letters/digits, starting with a letter; "
            "single hyphens or underscores may separate nonempty segments",
        )


def _utc_hour(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainValidationError("invalid_timestamp", field, "provide a datetime")
    offset = value.utcoffset()
    if offset is None:
        raise DomainValidationError(
            "naive_timestamp", field, "provide a timezone-aware UTC datetime"
        )
    if offset != timedelta(0):
        raise DomainValidationError(
            "non_utc_timestamp", field, "convert the timestamp to UTC before validation"
        )
    if value.minute or value.second or value.microsecond:
        raise DomainValidationError(
            "unaligned_timestamp",
            field,
            "minutes, seconds and microseconds must be zero",
        )
    return value.replace(tzinfo=UTC, fold=0)


def _measurement(
    value: float | None, field: str, minimum: float, maximum: float | None = None
) -> float | None:
    if value is None:
        return None
    if type(value) not in (int, float):
        raise DomainValidationError(
            "invalid_numeric_type", field, "provide a built-in int, float, or None"
        )
    try:
        normalized = float(value)
    except OverflowError as error:
        raise DomainValidationError(
            "non_finite_measurement", field, "value must fit in a finite float"
        ) from error
    if not math.isfinite(normalized):
        raise DomainValidationError(
            "non_finite_measurement",
            field,
            "NaN and infinity are not valid measurements",
        )
    if normalized < minimum or (maximum is not None and normalized > maximum):
        bound = (
            f"at least {minimum}" if maximum is None else f"in [{minimum}, {maximum}]"
        )
        raise DomainValidationError("out_of_range", field, f"value must be {bound}")
    return normalized


@dataclass(frozen=True, slots=True, kw_only=True)
class DatasetIdentity:
    """A versioned dataset identifier scoped by its provider; no registry lookup."""

    provider_id: str
    dataset_id: str

    def __post_init__(self) -> None:
        _validate_identifier(self.provider_id, "provider_id")
        _validate_identifier(self.dataset_id, "dataset_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class HourlyInterval:
    """Nonempty, hour-aligned UTC interval: start inclusive, end exclusive."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", _utc_hour(self.start, "start"))
        object.__setattr__(self, "end", _utc_hour(self.end, "end"))
        if self.start >= self.end:
            raise DomainValidationError(
                "invalid_interval", "end", "end must be strictly later than start"
            )

    @property
    def expected_hours(self) -> int:
        """Expected observation count without materializing a timeline."""
        return (self.end - self.start) // HOUR


@dataclass(frozen=True, slots=True, kw_only=True)
class HourlyObservation:
    """One city/dataset/hour; precipitation accumulates over the preceding hour."""

    city_id: str
    dataset: DatasetIdentity
    timestamp_utc: datetime
    temperature_c: float | None = None
    relative_humidity_pct: float | None = None
    precipitation_mm: float | None = None
    wind_speed_ms: float | None = None

    def __post_init__(self) -> None:
        validate_city_id(self.city_id)
        if not isinstance(self.dataset, DatasetIdentity):
            raise DomainValidationError(
                "invalid_dataset", "dataset", "provide a DatasetIdentity"
            )
        object.__setattr__(
            self, "timestamp_utc", _utc_hour(self.timestamp_utc, "timestamp_utc")
        )
        for field, minimum, maximum in (
            ("temperature_c", -273.15, None),
            ("relative_humidity_pct", 0.0, 100.0),
            ("precipitation_mm", 0.0, None),
            ("wind_speed_ms", 0.0, None),
        ):
            object.__setattr__(
                self, field, _measurement(getattr(self, field), field, minimum, maximum)
            )
