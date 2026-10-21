"""Canonical value contracts, tested with explicitly synthetic inputs."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from fractions import Fraction

import pytest

from hellas_dataops.domain import (
    DatasetIdentity,
    DomainValidationError,
    HourlyInterval,
)


@pytest.mark.parametrize(
    "city_id", ["athens", "thessaloniki", "patras", "heraklion", "larissa"]
)
def test_supported_city_observations(city_id, synthetic_week):
    observation = replace(synthetic_week[0], city_id=city_id)
    assert observation.city_id == city_id
    assert observation.temperature_c == 10.0
    assert observation.timestamp_utc.tzinfo is UTC


@pytest.mark.parametrize(
    "city_id", ["", "Athens", "athens ", "unknown", None, 1, [], True]
)
def test_invalid_city_is_rejected(city_id, synthetic_week):
    with pytest.raises(DomainValidationError) as error:
        replace(synthetic_week[0], city_id=city_id)
    assert error.value.code == "invalid_city_id"
    assert error.value.field == "city_id"
    assert "athens" in str(error.value)


@pytest.mark.parametrize("field", ["provider_id", "dataset_id"])
@pytest.mark.parametrize(
    "value", ["", "ERA5", " a", "a/1", "a--b", "a_", "1a", "α", "a" * 65, None, 7, []]
)
def test_invalid_dataset_identifier(field, value, synthetic_dataset):
    with pytest.raises(DomainValidationError) as error:
        replace(synthetic_dataset, **{field: value})
    assert error.value.code == "invalid_identifier"
    assert error.value.field == field


def test_dataset_identity_is_explicit_and_hashable():
    dataset = DatasetIdentity(provider_id="open-meteo", dataset_id="era5_hourly-v1")
    assert dataset != replace(dataset, provider_id="synthetic")
    assert dataset != replace(dataset, dataset_id="era5_hourly-v2")
    assert dataset == replace(dataset)
    assert len({dataset, replace(dataset)}) == 1
    assert DatasetIdentity(provider_id="a", dataset_id="a" * 64).dataset_id == "a" * 64


def test_invalid_dataset_object(synthetic_week):
    with pytest.raises(DomainValidationError) as error:
        replace(synthetic_week[0], dataset="hourly-v1")
    assert error.value.code == "invalid_dataset"


class UnspecifiedOffset(tzinfo):
    def utcoffset(self, dt):
        return None


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("2025-01-01T00:00:00Z", "invalid_timestamp"),
        (None, "invalid_timestamp"),
        (datetime(2025, 1, 1), "naive_timestamp"),
        (datetime(2025, 1, 1, tzinfo=UnspecifiedOffset()), "naive_timestamp"),
        (
            datetime(2025, 1, 1, tzinfo=timezone(timedelta(hours=2))),
            "non_utc_timestamp",
        ),
        (
            datetime(2025, 1, 1, tzinfo=timezone(timedelta(hours=-5))),
            "non_utc_timestamp",
        ),
        (datetime(2025, 1, 1, minute=1, tzinfo=UTC), "unaligned_timestamp"),
        (datetime(2025, 1, 1, second=1, tzinfo=UTC), "unaligned_timestamp"),
        (datetime(2025, 1, 1, microsecond=1, tzinfo=UTC), "unaligned_timestamp"),
    ],
)
@pytest.mark.parametrize("field", ["timestamp_utc", "start", "end"])
def test_invalid_timestamps_in_records_and_intervals(
    value, code, field, synthetic_week, seven_day_interval
):
    original = synthetic_week[0] if field == "timestamp_utc" else seven_day_interval
    with pytest.raises(DomainValidationError) as error:
        replace(original, **{field: value})
    assert error.value.code == code
    assert error.value.field == field


def test_equivalent_zero_offset_is_canonicalized(synthetic_week):
    named_zero = timezone(timedelta(0), "GMT")
    timestamp = datetime(2025, 1, 1, tzinfo=named_zero, fold=1)
    row = replace(synthetic_week[0], timestamp_utc=timestamp)
    assert row.timestamp_utc.tzinfo is UTC
    assert row.timestamp_utc.fold == 0
    assert row == synthetic_week[0]


MEASUREMENT_FIELDS = (
    "temperature_c",
    "relative_humidity_pct",
    "precipitation_mm",
    "wind_speed_ms",
)


@pytest.mark.parametrize("field", MEASUREMENT_FIELDS)
@pytest.mark.parametrize(
    "value", [True, False, "12", [], 1j, Decimal("1.0"), Fraction(1, 2)]
)
def test_invalid_numeric_types(field, value, synthetic_week):
    with pytest.raises(DomainValidationError) as error:
        replace(synthetic_week[0], **{field: value})
    assert error.value.code == "invalid_numeric_type"
    assert error.value.field == field


@pytest.mark.parametrize("field", MEASUREMENT_FIELDS)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 10**400])
def test_non_finite_or_unrepresentable_numbers(field, value, synthetic_week):
    with pytest.raises(DomainValidationError) as error:
        replace(synthetic_week[0], **{field: value})
    assert error.value.code == "non_finite_measurement"
    assert error.value.field == field


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature_c", -273.151),
        ("relative_humidity_pct", -0.001),
        ("relative_humidity_pct", 100.001),
        ("precipitation_mm", -0.001),
        ("wind_speed_ms", -0.001),
    ],
)
def test_physical_bounds(field, value, synthetic_week):
    with pytest.raises(DomainValidationError) as error:
        replace(synthetic_week[0], **{field: value})
    assert error.value.code == "out_of_range"
    assert error.value.field == field


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature_c", -273.15),
        ("temperature_c", -90),
        ("temperature_c", 60),
        ("relative_humidity_pct", 0),
        ("relative_humidity_pct", 100),
        ("precipitation_mm", 0),
        ("wind_speed_ms", 0),
    ],
)
def test_valid_boundaries_and_unusual_temperature(field, value, synthetic_week):
    row = replace(synthetic_week[0], **{field: value})
    assert getattr(row, field) == value
    assert type(getattr(row, field)) is float


def test_nulls_are_preserved_and_not_equivalent_to_zero(synthetic_week):
    null_row = replace(synthetic_week[0], **dict.fromkeys(MEASUREMENT_FIELDS))
    zero_row = replace(synthetic_week[0], **dict.fromkeys(MEASUREMENT_FIELDS, 0.0))
    assert all(getattr(null_row, field) is None for field in MEASUREMENT_FIELDS)
    assert null_row != zero_row


@pytest.mark.parametrize("offset", [0, -1])
def test_empty_or_reversed_interval(offset):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    with pytest.raises(DomainValidationError) as error:
        HourlyInterval(start=start, end=start + timedelta(hours=offset))
    assert error.value.code == "invalid_interval"
    assert error.value.field == "end"


def test_objects_are_frozen(synthetic_dataset, seven_day_interval, synthetic_week):
    for obj, field, value in [
        (synthetic_dataset, "provider_id", "changed"),
        (seven_day_interval, "end", seven_day_interval.start),
        (synthetic_week[0], "temperature_c", 999),
    ]:
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field, value)
    assert len({synthetic_week[0], replace(synthetic_week[0])}) == 1
