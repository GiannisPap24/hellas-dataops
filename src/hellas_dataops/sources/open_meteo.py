"""Pure normalization of one decoded Open-Meteo hourly archive response."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hellas_dataops.domain import (
    DatasetIdentity,
    DomainValidationError,
    HourlyInterval,
    HourlyObservation,
    validate_batch,
)
from hellas_dataops.domain.models import validate_city_id

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_FIELDS = (
    ("temperature_2m", "temperature_c", "°C"),
    ("relative_humidity_2m", "relative_humidity_pct", "%"),
    ("precipitation", "precipitation_mm", "mm"),
    ("wind_speed_10m", "wind_speed_ms", "m/s"),
)
_SOURCE_FIELDS = {canonical: source for source, canonical, _ in _FIELDS}
_SOURCE_FIELDS["timestamp_utc"] = "time"


class OpenMeteoNormalizationError(DomainValidationError):
    """A source structure, unit, epoch, or metadata error with a field path."""


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenMeteoMetadata:
    """Returned location and timezone, validated by the normalizer.

    These are provider grid coordinates, never the requested city coordinates.
    Absence and explicit null both mean unavailable for optional metadata.
    """

    grid_latitude: float
    grid_longitude: float
    grid_elevation_m: float | None
    timezone: str
    utc_offset_seconds: int
    timezone_abbreviation: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class NormalizedWeather:
    """Complete sorted observations and validated metadata; no raw input aliases."""

    observations: tuple[HourlyObservation, ...]
    metadata: OpenMeteoMetadata


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise OpenMeteoNormalizationError(
            "invalid_structure", field, "provide a decoded JSON object"
        )
    return value


def _coordinate(value: object, field: str, bound: float | None = None) -> float:
    if type(value) not in (int, float):
        raise OpenMeteoNormalizationError(
            "invalid_metadata", field, "provide a finite JSON number, not null or bool"
        )
    try:
        number = float(value)
    except OverflowError as error:
        raise OpenMeteoNormalizationError(
            "invalid_metadata", field, "number must fit in a finite float"
        ) from error
    if not math.isfinite(number):
        raise OpenMeteoNormalizationError(
            "invalid_metadata", field, "NaN and infinity are not valid metadata"
        )
    if bound is not None and not -bound <= number <= bound:
        raise OpenMeteoNormalizationError(
            "invalid_metadata", field, f"coordinate must be within [-{bound}, {bound}]"
        )
    return number


def _metadata(payload: Mapping[str, object]) -> OpenMeteoMetadata:
    latitude = _coordinate(payload.get("latitude"), "latitude", 90)
    longitude = _coordinate(payload.get("longitude"), "longitude", 180)
    elevation = payload.get("elevation")
    if elevation is not None:
        elevation = _coordinate(elevation, "elevation")
    timezone = payload.get("timezone")
    if not isinstance(timezone, str) or timezone not in (
        "GMT",
        "UTC",
        "Etc/GMT",
        "Etc/UTC",
    ):
        raise OpenMeteoNormalizationError(
            "invalid_metadata", "timezone", "expected GMT, UTC, Etc/GMT, or Etc/UTC"
        )
    offset = payload.get("utc_offset_seconds")
    if type(offset) is not int or offset != 0:
        raise OpenMeteoNormalizationError(
            "invalid_metadata", "utc_offset_seconds", "expected integer 0 for UTC"
        )
    abbreviation = payload.get("timezone_abbreviation")
    if abbreviation is not None and (
        not isinstance(abbreviation, str) or abbreviation not in ("GMT", "UTC")
    ):
        raise OpenMeteoNormalizationError(
            "invalid_metadata", "timezone_abbreviation", "expected GMT, UTC, or null"
        )
    return OpenMeteoMetadata(
        grid_latitude=latitude,
        grid_longitude=longitude,
        grid_elevation_m=elevation,
        timezone=timezone,
        utc_offset_seconds=offset,
        timezone_abbreviation=abbreviation,
    )


def _timestamp(value: object, index: int) -> datetime:
    field = f"hourly.time[{index}]"
    if type(value) is not int:
        raise OpenMeteoNormalizationError(
            "invalid_epoch", field, "expected integer Unix seconds, not float or bool"
        )
    try:
        # Arithmetic also handles pre-1970 values consistently on Windows.
        return _EPOCH + timedelta(seconds=value)
    except OverflowError as error:
        raise OpenMeteoNormalizationError(
            "invalid_epoch", field, "Unix seconds must fit Python datetime years 1-9999"
        ) from error


def normalize_open_meteo(
    payload: Mapping[str, object],
    *,
    city_id: str,
    dataset: DatasetIdentity,
    interval: HourlyInterval,
) -> NormalizedWeather:
    """Validate and normalize one response without I/O or input mutation.

    The caller supplies dataset identity; a response cannot prove ERA5 selection.
    Required arrays are JSON lists. Extra fields are ignored. A failure rejects
    the entire result. Source errors subclass DomainValidationError; canonical
    measurement, identity, and coverage errors retain the domain error codes.
    """
    validate_city_id(city_id)
    if not isinstance(dataset, DatasetIdentity):
        raise DomainValidationError(
            "invalid_dataset", "dataset", "provide DatasetIdentity"
        )
    if not isinstance(interval, HourlyInterval):
        raise DomainValidationError(
            "invalid_interval", "interval", "provide HourlyInterval"
        )

    payload = _mapping(payload, "response")
    metadata = _metadata(payload)
    hourly = _mapping(payload.get("hourly"), "hourly")
    units = _mapping(payload.get("hourly_units"), "hourly_units")
    arrays = {}
    for source, expected in (("time", "unixtime"), *((s, u) for s, _, u in _FIELDS)):
        if units.get(source) != expected:
            raise OpenMeteoNormalizationError(
                "invalid_unit",
                f"hourly_units.{source}",
                f"expected exact unit {expected!r}",
            )
        values = hourly.get(source)
        if not isinstance(values, list):
            raise OpenMeteoNormalizationError(
                "invalid_array",
                f"hourly.{source}",
                "required field must be a JSON array",
            )
        arrays[source] = values

    count = len(arrays["time"])
    for source, values in arrays.items():
        if len(values) != count:
            raise OpenMeteoNormalizationError(
                "array_length_mismatch",
                f"hourly.{source}",
                f"expected {count} entries to match hourly.time; received {len(values)}",
            )

    rows = []
    for index, epoch in enumerate(arrays["time"]):
        timestamp = _timestamp(epoch, index)
        try:
            rows.append(
                HourlyObservation(
                    city_id=city_id,
                    dataset=dataset,
                    timestamp_utc=timestamp,
                    **{
                        canonical: arrays[source][index]
                        for source, canonical, _ in _FIELDS
                    },
                )
            )
        except DomainValidationError as error:
            source = _SOURCE_FIELDS[error.field]
            raise DomainValidationError(
                error.code,
                f"hourly.{source}[{index}]",
                f"invalid source value; {error}",
            ) from error

    observations = validate_batch(
        rows, city_id=city_id, dataset=dataset, interval=interval
    )
    return NormalizedWeather(observations=observations, metadata=metadata)
