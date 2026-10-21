"""Synthetic batch coverage, identity, ordering, and deterministic failures."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hellas_dataops.domain import (
    DomainValidationError,
    HourlyInterval,
    HourlyObservation,
    validate_batch,
)


def test_seven_days_complete_and_repeatable(
    synthetic_week, synthetic_dataset, seven_day_interval
):
    result = validate_batch(
        synthetic_week,
        city_id="athens",
        dataset=synthetic_dataset,
        interval=seven_day_interval,
    )
    repeated = validate_batch(
        result, city_id="athens", dataset=synthetic_dataset, interval=seven_day_interval
    )
    assert len(result) == seven_day_interval.expected_hours == 168
    assert result == repeated == synthetic_week
    assert isinstance(result, tuple)


def test_unsorted_input_is_sorted_without_mutation(
    synthetic_week, synthetic_dataset, seven_day_interval
):
    rows = list(reversed(synthetic_week))
    original = rows.copy()
    result = validate_batch(
        rows, city_id="athens", dataset=synthetic_dataset, interval=seven_day_interval
    )
    assert rows == original
    assert result == synthetic_week


def test_all_null_hours_have_coverage_but_no_measurements(
    synthetic_dataset, seven_day_interval
):
    interval = replace(
        seven_day_interval, end=seven_day_interval.start + timedelta(hours=1)
    )
    row = HourlyObservation(
        city_id="athens", dataset=synthetic_dataset, timestamp_utc=interval.start
    )
    result = validate_batch(
        (row,), city_id="athens", dataset=synthetic_dataset, interval=interval
    )
    assert result == (row,)
    assert result[0].temperature_c is None
    assert result[0].relative_humidity_pct is None
    assert result[0].precipitation_mm is None
    assert result[0].wind_speed_ms is None
    with pytest.raises(DomainValidationError, match="missing 1 hour"):
        validate_batch(
            (), city_id="athens", dataset=synthetic_dataset, interval=interval
        )


@pytest.mark.parametrize("missing_index", [0, 81, 167])
def test_missing_first_middle_or_last_hour(
    missing_index, synthetic_week, synthetic_dataset, seven_day_interval
):
    rows = synthetic_week[:missing_index] + synthetic_week[missing_index + 1 :]
    with pytest.raises(DomainValidationError) as error:
        validate_batch(
            rows,
            city_id="athens",
            dataset=synthetic_dataset,
            interval=seven_day_interval,
        )
    assert error.value.code == "coverage_mismatch"
    assert "missing 1 hour(s)" in str(error.value)
    assert synthetic_week[missing_index].timestamp_utc.isoformat() in str(error.value)


@pytest.mark.parametrize("changed_value", [None, 99.0])
def test_duplicate_hour_rejected_even_with_identical_values(
    changed_value, synthetic_week, synthetic_dataset, seven_day_interval
):
    duplicate = (
        synthetic_week[0]
        if changed_value is None
        else replace(synthetic_week[0], temperature_c=changed_value)
    )
    rows = (*synthetic_week, duplicate)
    with pytest.raises(DomainValidationError) as error:
        validate_batch(
            rows,
            city_id="athens",
            dataset=synthetic_dataset,
            interval=seven_day_interval,
        )
    assert error.value.code == "duplicate_observation"
    assert error.value.field == "observations[168].timestamp_utc"


@pytest.mark.parametrize("boundary", ["before_start", "at_end"])
def test_unexpected_boundary_hour(
    boundary, synthetic_week, synthetic_dataset, seven_day_interval
):
    timestamp = (
        seven_day_interval.start - timedelta(hours=1)
        if boundary == "before_start"
        else seven_day_interval.end
    )
    extra = replace(synthetic_week[0], timestamp_utc=timestamp)
    with pytest.raises(DomainValidationError) as error:
        validate_batch(
            (*synthetic_week, extra),
            city_id="athens",
            dataset=synthetic_dataset,
            interval=seven_day_interval,
        )
    assert error.value.code == "coverage_mismatch"
    assert "unexpected 1 hour(s)" in str(error.value)
    assert "missing" not in str(error.value)


def test_equal_row_count_does_not_hide_gap(
    synthetic_week, synthetic_dataset, seven_day_interval
):
    rows = (
        *synthetic_week[:-1],
        replace(synthetic_week[-1], timestamp_utc=seven_day_interval.end),
    )
    messages = []
    for _ in range(2):
        with pytest.raises(DomainValidationError) as error:
            validate_batch(
                rows,
                city_id="athens",
                dataset=synthetic_dataset,
                interval=seven_day_interval,
            )
        messages.append(str(error.value))
    assert messages[0] == messages[1]
    assert "missing 1 hour(s)" in messages[0]
    assert "unexpected 1 hour(s)" in messages[0]


@pytest.mark.parametrize("identity", ["city", "dataset", "provider"])
def test_batch_identity_mismatch(
    identity, synthetic_week, synthetic_dataset, seven_day_interval
):
    if identity == "city":
        bad_row = replace(synthetic_week[1], city_id="patras")
    else:
        field = "provider_id" if identity == "provider" else "dataset_id"
        bad_row = replace(
            synthetic_week[1], dataset=replace(synthetic_dataset, **{field: "other"})
        )
    rows = (synthetic_week[0], bad_row, *synthetic_week[2:])
    with pytest.raises(DomainValidationError) as error:
        validate_batch(
            rows,
            city_id="athens",
            dataset=synthetic_dataset,
            interval=seven_day_interval,
        )
    assert error.value.code == (
        "city_mismatch" if identity == "city" else "dataset_mismatch"
    )
    assert error.value.field.startswith("observations[1].")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("city_id", "unknown", "invalid_city_id"),
        ("dataset", None, "invalid_dataset"),
        ("interval", None, "invalid_interval"),
    ],
)
def test_invalid_batch_request(
    field, value, code, synthetic_week, synthetic_dataset, seven_day_interval
):
    request = {
        "city_id": "athens",
        "dataset": synthetic_dataset,
        "interval": seven_day_interval,
    }
    request[field] = value
    with pytest.raises(DomainValidationError) as error:
        validate_batch(synthetic_week, **request)
    assert error.value.code == code


@pytest.mark.parametrize("rows", [None, "invalid", b"invalid", iter(())])
def test_invalid_batch_container(rows, synthetic_dataset, seven_day_interval):
    with pytest.raises(DomainValidationError) as error:
        validate_batch(
            rows,
            city_id="athens",
            dataset=synthetic_dataset,
            interval=seven_day_interval,
        )
    assert error.value.code == "invalid_batch"


def test_invalid_observation_in_batch(synthetic_dataset, seven_day_interval):
    with pytest.raises(DomainValidationError) as error:
        validate_batch(
            [{}],
            city_id="athens",
            dataset=synthetic_dataset,
            interval=seven_day_interval,
        )
    assert error.value.code == "invalid_observation"
    assert error.value.field == "observations[0]"


def test_leap_day_and_half_open_boundaries(synthetic_dataset):
    start = datetime(2024, 2, 28, 23, tzinfo=UTC)
    interval = HourlyInterval(start=start, end=datetime(2024, 3, 1, 1, tzinfo=UTC))
    rows = tuple(
        HourlyObservation(
            city_id="athens",
            dataset=synthetic_dataset,
            timestamp_utc=start + timedelta(hours=hour),
        )
        for hour in range(26)
    )
    result = validate_batch(
        rows, city_id="athens", dataset=synthetic_dataset, interval=interval
    )
    assert len(result) == interval.expected_hours == 26
    assert sum(row.timestamp_utc.day == 29 for row in result) == 24
    assert result[0].timestamp_utc == interval.start
    assert result[-1].timestamp_utc == interval.end - timedelta(hours=1)


def test_adjacent_intervals_share_no_hour(
    synthetic_week, synthetic_dataset, seven_day_interval
):
    split = seven_day_interval.start + timedelta(hours=84)
    left = replace(seven_day_interval, end=split)
    right = replace(seven_day_interval, start=split)
    results = [
        validate_batch(
            rows, city_id="athens", dataset=synthetic_dataset, interval=interval
        )
        for rows, interval in [
            (synthetic_week[:84], left),
            (synthetic_week[84:], right),
        ]
    ]
    assert results[0] + results[1] == synthetic_week
    assert not set(results[0]).intersection(results[1])


def test_large_empty_interval_does_not_allocate_expected_timeline(synthetic_dataset):
    interval = HourlyInterval(
        start=datetime(1, 1, 1, tzinfo=UTC), end=datetime(9999, 12, 31, 23, tzinfo=UTC)
    )
    with pytest.raises(DomainValidationError) as error:
        validate_batch(
            (), city_id="athens", dataset=synthetic_dataset, interval=interval
        )
    assert f"missing {interval.expected_hours} hour(s)" in str(error.value)
