"""Offline contracts using invented JSON, never captured provider responses."""

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pytest

from hellas_dataops.domain import (
    DatasetIdentity,
    DomainValidationError,
    HourlyInterval,
    validate_batch,
)
from hellas_dataops.sources.open_meteo import (
    NormalizedWeather,
    OpenMeteoMetadata,
    OpenMeteoNormalizationError,
    normalize_open_meteo,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "open_meteo"
DATASET = DatasetIdentity(provider_id="synthetic", dataset_id="hourly-v1")
WEEK = HourlyInterval(
    start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2025, 1, 8, tzinfo=UTC)
)
COLUMNS = (
    "time",
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
)


def load_fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def payload():
    return load_fixture("seven_days")


def normalize(payload, *, city_id="athens", dataset=DATASET, interval=WEEK):
    return normalize_open_meteo(
        payload, city_id=city_id, dataset=dataset, interval=interval
    )


def test_seven_day_mapping_matches_independent_domain_fixture(payload, synthetic_week):
    result = normalize(payload)
    assert isinstance(result, NormalizedWeather)
    assert result.observations == synthetic_week
    assert len(result.observations) == 168
    assert result.observations[0].timestamp_utc == WEEK.start
    assert result.observations[-1].timestamp_utc == WEEK.end - timedelta(hours=1)
    assert all(row.timestamp_utc.tzinfo is UTC for row in result.observations)
    assert result.metadata == OpenMeteoMetadata(
        grid_latitude=37.95,
        grid_longitude=23.75,
        grid_elevation_m=70.0,
        timezone="GMT",
        utc_offset_seconds=0,
        timezone_abbreviation="GMT",
    )
    assert result.metadata.grid_latitude != 37.9838  # Requested Athens point.
    assert result.metadata.grid_longitude != 23.7275


def test_null_rows_cover_hours_without_imputation():
    payload = load_fixture("null_measurements")
    interval = HourlyInterval(start=WEEK.start, end=WEEK.start + timedelta(hours=3))
    rows = normalize(payload, interval=interval).observations
    assert len(rows) == 3
    assert (
        rows[0].temperature_c,
        rows[0].relative_humidity_pct,
        rows[0].precipitation_mm,
        rows[0].wind_speed_ms,
    ) == (None,) * 4
    assert rows[1].temperature_c is None
    assert rows[1].precipitation_mm is None
    assert rows[1].relative_humidity_pct == 41.0
    assert rows[2].precipitation_mm == 0.0


def test_leap_day_coverage():
    interval = HourlyInterval(
        start=datetime(2024, 2, 28, 23, tzinfo=UTC),
        end=datetime(2024, 3, 1, 1, tzinfo=UTC),
    )
    rows = normalize(load_fixture("leap_day"), interval=interval).observations
    assert len(rows) == 26
    assert rows[0].timestamp_utc == interval.start
    assert rows[-1].timestamp_utc == datetime(2024, 3, 1, tzinfo=UTC)
    assert sum(row.timestamp_utc.day == 29 for row in rows) == 24


def test_unsorted_rows_sort_with_measurements_attached(payload):
    expected = normalize(payload)
    for column in COLUMNS:
        payload["hourly"][column].reverse()
    snapshot = deepcopy(payload)
    assert normalize(payload) == expected
    assert payload == snapshot


def test_repeated_normalization_is_equal_and_does_not_mutate_or_alias(payload):
    snapshot = deepcopy(payload)
    first = normalize(payload)
    assert normalize(payload) == first
    assert payload == snapshot
    assert (
        validate_batch(
            first.observations, city_id="athens", dataset=DATASET, interval=WEEK
        )
        == first.observations
    )
    payload["hourly"]["temperature_2m"][0] = 99
    payload["latitude"] = 1
    assert first.observations[0].temperature_c == 10.0
    assert first.metadata.grid_latitude == 37.95


def test_returned_objects_are_immutable(payload):
    result = normalize(payload)
    assert isinstance(result.observations, tuple)
    with pytest.raises(FrozenInstanceError):
        result.metadata = None
    with pytest.raises(FrozenInstanceError):
        result.metadata.grid_latitude = 0
    with pytest.raises(FrozenInstanceError):
        result.observations[0].temperature_c = 0


def test_explicit_identity_is_preserved_not_inferred_from_response(payload):
    dataset = DatasetIdentity(provider_id="open-meteo", dataset_id="era5_hourly-v1")
    payload["model"] = "untrusted-extra-field"
    rows = normalize(payload, city_id="patras", dataset=dataset).observations
    assert all(row.dataset is dataset and row.city_id == "patras" for row in rows)


def test_additional_fields_and_read_only_mappings_are_compatible(payload):
    expected = normalize(payload)
    payload["future_field"] = {"not": ["parsed"]}
    payload["hourly"]["unrequested_variable"] = ["invalid", "length"]
    payload["hourly_units"]["unrequested_variable"] = "unknown"
    payload["hourly"] = MappingProxyType(payload["hourly"])
    payload["hourly_units"] = MappingProxyType(payload["hourly_units"])
    assert normalize(MappingProxyType(payload)) == expected


@pytest.mark.parametrize("value", [None, [], "{}", 1, True])
def test_malformed_top_level_response(value):
    with pytest.raises(OpenMeteoNormalizationError) as caught:
        normalize(value)
    assert (caught.value.code, caught.value.field) == ("invalid_structure", "response")


def test_empty_response_is_rejected():
    with pytest.raises(OpenMeteoNormalizationError) as caught:
        normalize({})
    assert (caught.value.code, caught.value.field) == ("invalid_metadata", "latitude")


@pytest.mark.parametrize("field", ["hourly", "hourly_units"])
@pytest.mark.parametrize("value", [None, [], "object", 123])
def test_malformed_nested_object(payload, field, value):
    payload[field] = value
    with pytest.raises(OpenMeteoNormalizationError) as caught:
        normalize(payload)
    assert (caught.value.code, caught.value.field) == ("invalid_structure", field)


@pytest.mark.parametrize("field", ["hourly", "hourly_units"])
def test_absent_nested_object(payload, field):
    del payload[field]
    with pytest.raises(OpenMeteoNormalizationError) as caught:
        normalize(payload)
    assert (caught.value.code, caught.value.field) == ("invalid_structure", field)


@pytest.mark.parametrize("field", COLUMNS)
@pytest.mark.parametrize("remove", [False, True])
def test_wrong_or_missing_units(payload, field, remove):
    if remove:
        del payload["hourly_units"][field]
    else:
        payload["hourly_units"][field] = "wrong-unit"
    with pytest.raises(OpenMeteoNormalizationError) as caught:
        normalize(payload)
    assert (caught.value.code, caught.value.field) == (
        "invalid_unit",
        f"hourly_units.{field}",
    )
    assert "expected exact unit" in str(caught.value)


@pytest.mark.parametrize("field", COLUMNS)
def test_missing_array_is_not_a_null_measurement(payload, field):
    del payload["hourly"][field]
    with pytest.raises(OpenMeteoNormalizationError) as caught:
        normalize(payload)
    assert (caught.value.code, caught.value.field) == (
        "invalid_array",
        f"hourly.{field}",
    )


@pytest.mark.parametrize("value", [None, {}, "null", 0, (1, 2)])
def test_arrays_must_be_json_lists(payload, value):
    payload["hourly"]["temperature_2m"] = value
    with pytest.raises(OpenMeteoNormalizationError) as caught:
        normalize(payload)
    assert caught.value.code == "invalid_array"


@pytest.mark.parametrize("field", COLUMNS[1:])
@pytest.mark.parametrize("delta", [-1, 1])
def test_unequal_lengths_are_never_silently_truncated(payload, field, delta):
    values = payload["hourly"][field]
    if delta == -1:
        values.pop()
    else:
        values.append(None)
    with pytest.raises(OpenMeteoNormalizationError) as caught:
        normalize(payload)
    assert (caught.value.code, caught.value.field) == (
        "array_length_mismatch",
        f"hourly.{field}",
    )
    assert "expected 168 entries" in str(caught.value)


def test_missing_hour(payload):
    for field in COLUMNS:
        payload["hourly"][field].pop(10)
    with pytest.raises(DomainValidationError) as caught:
        normalize(payload)
    assert caught.value.code == "coverage_mismatch"
    assert "missing 1 hour(s), first 2025-01-01T10:00:00+00:00" in str(caught.value)


def test_duplicate_hour_is_rejected_even_when_identical(payload):
    for field in COLUMNS:
        payload["hourly"][field].append(payload["hourly"][field][0])
    with pytest.raises(DomainValidationError) as caught:
        normalize(payload)
    assert (caught.value.code, caught.value.field) == (
        "duplicate_observation",
        "observations[168].timestamp_utc",
    )


@pytest.mark.parametrize("index, shift", [(0, -3600), (-1, 3600)])
def test_outside_interval_with_same_row_count_fails(payload, index, shift):
    payload["hourly"]["time"][index] += shift
    with pytest.raises(DomainValidationError) as caught:
        normalize(payload)
    assert caught.value.code == "coverage_mismatch"
    assert "missing 1 hour(s)" in str(caught.value)
    assert "unexpected 1 hour(s)" in str(caught.value)


def test_empty_arrays_do_not_pass_coverage(payload):
    for field in COLUMNS:
        payload["hourly"][field] = []
    with pytest.raises(DomainValidationError) as caught:
        normalize(payload)
    assert caught.value.code == "coverage_mismatch"
    assert "missing 168 hour(s)" in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "1735689600",
        1735689600.0,
        0.5,
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        10**100,
        -(10**100),
        253402300800,
        -62135596801,
        1735689600000,
    ],
)
def test_invalid_unix_seconds(payload, value):
    payload["hourly"]["time"][3] = value
    with pytest.raises(OpenMeteoNormalizationError) as caught:
        normalize(payload)
    assert (caught.value.code, caught.value.field) == (
        "invalid_epoch",
        "hourly.time[3]",
    )


def test_unaligned_epoch_uses_domain_rule_and_source_index(payload):
    payload["hourly"]["time"][2] += 1
    with pytest.raises(DomainValidationError) as caught:
        normalize(payload)
    assert (caught.value.code, caught.value.field) == (
        "unaligned_timestamp",
        "hourly.time[2]",
    )
    assert isinstance(caught.value.__cause__, DomainValidationError)


@pytest.mark.parametrize("epoch", [-3600, 0, -62135596800, 253402297200])
def test_representable_epoch_boundaries_and_negative_values(payload, epoch):
    start = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=epoch)
    # Last representable UTC hour has no representable exclusive endpoint;
    # request the preceding hour and verify a coverage error instead of overflow.
    if start.year == 9999:
        interval = HourlyInterval(start=start - timedelta(hours=1), end=start)
    else:
        interval = HourlyInterval(start=start, end=start + timedelta(hours=1))
    for field in COLUMNS:
        payload["hourly"][field] = payload["hourly"][field][:1]
    payload["hourly"]["time"] = [epoch]
    if start.year == 9999:
        with pytest.raises(DomainValidationError, match="coverage_mismatch"):
            normalize(payload, interval=interval)
    else:
        assert (
            normalize(payload, interval=interval).observations[0].timestamp_utc == start
        )


@pytest.mark.parametrize(
    "value, code",
    [
        (True, "invalid_numeric_type"),
        ("10", "invalid_numeric_type"),
        ([], "invalid_numeric_type"),
        ({}, "invalid_numeric_type"),
        (Decimal("10"), "invalid_numeric_type"),
        (float("nan"), "non_finite_measurement"),
        (float("inf"), "non_finite_measurement"),
        (float("-inf"), "non_finite_measurement"),
        (10**400, "non_finite_measurement"),
        (-274, "out_of_range"),
    ],
)
def test_malformed_measurement_uses_domain_errors(payload, value, code):
    payload["hourly"]["temperature_2m"][5] = value
    with pytest.raises(DomainValidationError) as caught:
        normalize(payload)
    assert (caught.value.code, caught.value.field) == (code, "hourly.temperature_2m[5]")


@pytest.mark.parametrize(
    "field, value",
    [
        ("relative_humidity_2m", -1),
        ("relative_humidity_2m", 101),
        ("precipitation", -0.1),
        ("wind_speed_10m", -0.1),
    ],
)
def test_physical_bounds_remain_domain_owned(payload, field, value):
    payload["hourly"][field][1] = value
    with pytest.raises(DomainValidationError) as caught:
        normalize(payload)
    assert (caught.value.code, caught.value.field) == (
        "out_of_range",
        f"hourly.{field}[1]",
    )


def test_no_arbitrary_temperature_filter_or_precipitation_timestamp_shift(payload):
    payload["hourly"]["temperature_2m"][:2] = [-100, 70]
    payload["hourly"]["precipitation"][1] = 3.25
    rows = normalize(payload).observations
    assert [row.temperature_c for row in rows[:2]] == [-100.0, 70.0]
    assert rows[1].precipitation_mm == 3.25
    assert rows[1].timestamp_utc == WEEK.start + timedelta(hours=1)


@pytest.mark.parametrize(
    "field", ["latitude", "longitude", "timezone", "utc_offset_seconds"]
)
def test_required_metadata_cannot_be_absent(payload, field):
    del payload[field]
    with pytest.raises(OpenMeteoNormalizationError) as caught:
        normalize(payload)
    assert (caught.value.code, caught.value.field) == ("invalid_metadata", field)


@pytest.mark.parametrize(
    "field, value",
    [
        ("latitude", None),
        ("latitude", True),
        ("longitude", "23.75"),
        ("latitude", []),
        ("longitude", {}),
        ("latitude", 90.01),
        ("latitude", -90.01),
        ("longitude", 180.01),
        ("longitude", -180.01),
        ("latitude", float("nan")),
        ("longitude", float("inf")),
        ("longitude", float("-inf")),
        ("latitude", 10**400),
        ("elevation", False),
        ("elevation", "70"),
        ("elevation", float("nan")),
        ("elevation", float("inf")),
        ("elevation", 10**400),
        ("timezone", None),
        ("timezone", []),
        ("timezone", "Europe/Athens"),
        ("timezone", "Europe/London"),
        ("timezone", "gmt"),
        ("utc_offset_seconds", 3600),
        ("utc_offset_seconds", -3600),
        ("utc_offset_seconds", True),
        ("utc_offset_seconds", False),
        ("utc_offset_seconds", 0.0),
        ("utc_offset_seconds", "0"),
        ("utc_offset_seconds", None),
        ("timezone_abbreviation", []),
        ("timezone_abbreviation", "EET"),
        ("timezone_abbreviation", ""),
    ],
)
def test_invalid_metadata_is_actionable(payload, field, value):
    payload[field] = value
    with pytest.raises(OpenMeteoNormalizationError) as caught:
        normalize(payload)
    assert (caught.value.code, caught.value.field) == ("invalid_metadata", field)


@pytest.mark.parametrize("timezone", ["GMT", "UTC", "Etc/GMT", "Etc/UTC"])
def test_supported_utc_names_preserved(payload, timezone):
    payload["timezone"] = timezone
    payload["timezone_abbreviation"] = "UTC"
    result = normalize(payload)
    assert result.metadata.timezone == timezone
    assert result.metadata.timezone_abbreviation == "UTC"


@pytest.mark.parametrize("remove", [False, True])
def test_optional_metadata_missing_or_null(payload, remove):
    for field in ("elevation", "timezone_abbreviation"):
        if remove:
            del payload[field]
        else:
            payload[field] = None
    metadata = normalize(payload).metadata
    assert metadata.grid_elevation_m is None
    assert metadata.timezone_abbreviation is None


@pytest.mark.parametrize("latitude, longitude", [(-90, -180), (90, 180), (0, 0)])
def test_coordinate_bounds_and_negative_elevation_allowed(payload, latitude, longitude):
    payload.update(latitude=latitude, longitude=longitude, elevation=-430)
    metadata = normalize(payload).metadata
    assert metadata.grid_latitude == latitude
    assert metadata.grid_longitude == longitude
    assert metadata.grid_elevation_m == -430.0


@pytest.mark.parametrize(
    "argument, value, code",
    [
        ("city_id", "unknown", "invalid_city_id"),
        ("dataset", "era5", "invalid_dataset"),
        ("interval", None, "invalid_interval"),
    ],
)
def test_invalid_explicit_context(payload, argument, value, code):
    with pytest.raises(DomainValidationError) as caught:
        normalize(payload, **{argument: value})
    assert (caught.value.code, caught.value.field) == (code, argument)


def test_errors_are_deterministic_and_do_not_return_partial_results(payload):
    payload["hourly"]["wind_speed_10m"][-1] = -1
    snapshot = deepcopy(payload)
    errors = []
    for _ in range(2):
        with pytest.raises(DomainValidationError) as caught:
            normalize(payload)
        errors.append((caught.value.code, caught.value.field, str(caught.value)))
    assert errors[0] == errors[1]
    assert errors[0][1] == "hourly.wind_speed_10m[167]"
    assert payload == snapshot


@pytest.mark.parametrize("name", ["seven_days", "null_measurements", "leap_day"])
def test_json_fixtures_clearly_declare_synthetic_provenance(name):
    assert load_fixture(name)["_fixture"].startswith("SYNTHETIC:")
