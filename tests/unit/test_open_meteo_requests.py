"""Synthetic offline plans and exact request/provenance serialization."""

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

from hellas_dataops.domain import DatasetIdentity, DomainValidationError, HourlyInterval
from hellas_dataops.sources.cities import CITY_REGISTRY
from hellas_dataops.sources.open_meteo import normalize_open_meteo
from hellas_dataops.sources.open_meteo_requests import (
    ERA5_DATASET,
    OpenMeteoRequest,
    plan_open_meteo_requests,
)


def interval(start, end):
    return HourlyInterval(
        start=datetime.fromisoformat(start).replace(tzinfo=UTC),
        end=datetime.fromisoformat(end).replace(tzinfo=UTC),
    )


SINGLE_DAY = interval("2025-01-01", "2025-01-02")


def plan(requested=SINGLE_DAY, *, city=CITY_REGISTRY["athens"], dataset=ERA5_DATASET):
    return plan_open_meteo_requests(city, dataset=dataset, interval=requested)


def test_exact_parameters_and_encoded_query():
    (request,) = plan()
    expected = (
        ("latitude", "37.9838"),
        ("longitude", "23.7275"),
        ("start_date", "2025-01-01"),
        ("end_date", "2025-01-01"),
        ("hourly", "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m"),
        ("models", "era5"),
        ("timezone", "GMT"),
        ("timeformat", "unixtime"),
        ("temperature_unit", "celsius"),
        ("wind_speed_unit", "ms"),
        ("precipitation_unit", "mm"),
        ("cell_selection", "land"),
        ("elevation", "nan"),
        ("format", "json"),
    )
    query = (
        "latitude=37.9838&longitude=23.7275&start_date=2025-01-01&end_date=2025-01-01"
        "&hourly=temperature_2m%2Crelative_humidity_2m%2Cprecipitation%2Cwind_speed_10m"
        "&models=era5&timezone=GMT&timeformat=unixtime&temperature_unit=celsius"
        "&wind_speed_unit=ms&precipitation_unit=mm&cell_selection=land&elevation=nan&format=json"
    )
    assert request.parameters == expected
    assert request.query_string == query
    assert request.url == "https://archive-api.open-meteo.com/v1/archive?" + query
    assert (
        tuple(parse_qsl(urlsplit(request.url).query, strict_parsing=True)) == expected
    )
    assert len(dict(request.parameters)) == len(request.parameters)
    assert request.start_date == request.end_date == date(2025, 1, 1)


@pytest.mark.parametrize("city_id", tuple(CITY_REGISTRY))
def test_every_city_has_explicit_versioned_provenance(city_id):
    city = CITY_REGISTRY[city_id]
    (request,) = plan(city=city)
    assert request.city is city
    assert request.dataset == ERA5_DATASET
    document = json.loads(request.to_json())
    assert document["city"] == {
        "city_id": city_id,
        "coordinate_version": "gr-cities-v1",
        "latitude": city.latitude,
        "longitude": city.longitude,
    }
    assert document["dataset"] == {
        "provider_id": "open-meteo",
        "dataset_id": "era5_hourly-v1",
    }
    assert float(document["parameters"]["latitude"]) == city.latitude
    assert float(document["parameters"]["longitude"]) == city.longitude


@pytest.mark.parametrize(
    "start, end, dates, retained_hours",
    [
        ("2025-01-01", "2025-01-02", [("2025-01-01", "2025-01-01")], [24]),
        ("2025-01-01", "2025-01-08", [("2025-01-01", "2025-01-07")], [168]),
        ("2025-01-01", "2025-01-15", [("2025-01-01", "2025-01-14")], [336]),
        (
            "2025-01-01",
            "2025-01-16",
            [("2025-01-01", "2025-01-14"), ("2025-01-15", "2025-01-15")],
            [336, 24],
        ),
        ("2025-01-01T05:00", "2025-01-01T06:00", [("2025-01-01", "2025-01-01")], [1]),
        ("2025-01-01T05:00", "2025-01-02", [("2025-01-01", "2025-01-01")], [19]),
        ("2025-01-01", "2025-01-02T01:00", [("2025-01-01", "2025-01-02")], [25]),
        ("2025-01-01T23:00", "2025-01-02T01:00", [("2025-01-01", "2025-01-02")], [2]),
        (
            "2025-01-01T05:00",
            "2025-01-15T05:00",
            [("2025-01-01", "2025-01-14"), ("2025-01-15", "2025-01-15")],
            [331, 5],
        ),
        ("2024-02-28T23:00", "2024-03-01T01:00", [("2024-02-28", "2024-03-01")], [26]),
        ("2024-02-16", "2024-03-01", [("2024-02-16", "2024-02-29")], [336]),
        (
            "2024-12-25",
            "2025-01-09",
            [("2024-12-25", "2025-01-07"), ("2025-01-08", "2025-01-08")],
            [336, 24],
        ),
    ],
)
def test_date_boundaries_and_chunks(start, end, dates, retained_hours):
    requested = interval(start, end)
    requests = plan(requested)
    assert [
        (r.start_date.isoformat(), r.end_date.isoformat()) for r in requests
    ] == dates
    assert [r.retain_interval.expected_hours for r in requests] == retained_hours
    assert sum(retained_hours) == requested.expected_hours
    assert requests[0].retain_interval.start == requested.start
    assert requests[-1].retain_interval.end == requested.end
    for request in requests:
        assert request.requested_interval is requested
        assert (
            request.retrieval_interval.start.hour
            == request.retrieval_interval.end.hour
            == 0
        )
        assert 24 <= request.retrieval_interval.expected_hours <= 336
        assert request.retrieval_interval.start <= request.retain_interval.start
        assert request.retain_interval.end <= request.retrieval_interval.end
    for left, right in pairwise(requests):
        assert left.retrieval_interval.end == right.retrieval_interval.start
        assert left.retain_interval.end == right.retain_interval.start


def test_full_mvp_year_has_exact_coverage_without_hour_overlap():
    requested = interval("2025-01-01", "2026-01-01")
    requests = plan(requested)
    assert len(requests) == 27  # 26 x 14 days plus one day.
    actual = []
    for request in requests:
        actual.extend(
            request.retain_interval.start + timedelta(hours=i)
            for i in range(request.retain_interval.expected_hours)
        )
    expected = [requested.start + timedelta(hours=i) for i in range(8760)]
    assert actual == expected
    assert len(set(actual)) == 8760
    assert requests[-1].end_date == date(2025, 12, 31)


@pytest.mark.parametrize(
    "start, end", [("2025-03-29", "2025-03-31"), ("2025-10-25", "2025-10-27")]
)
def test_utc_days_remain_24_hours_across_greek_dst_transition_dates(start, end):
    requested = interval(start, end)
    (request,) = plan(requested)
    assert request.retrieval_interval.expected_hours == 48
    assert request.retain_interval.expected_hours == 48
    assert dict(request.parameters)["timezone"] == "GMT"
    assert request.retain_interval.start.tzinfo is UTC


@pytest.mark.parametrize("offset", [2, 3])
def test_local_greek_offsets_are_rejected_at_domain_boundary(offset):
    with pytest.raises(DomainValidationError, match="non_utc_timestamp"):
        HourlyInterval(
            start=datetime(2025, 3, 30, tzinfo=timezone(timedelta(hours=offset))),
            end=datetime(2025, 3, 31, tzinfo=UTC),
        )


def test_repeatable_immutable_plan_and_canonical_json():
    requested = interval("2025-01-01T03:00", "2025-01-17T06:00")
    first = plan(requested)
    second = plan(requested)
    assert first == second
    assert [r.to_json() for r in first] == [r.to_json() for r in second]
    assert hash(first) == hash(second)
    request = first[0]
    with pytest.raises(FrozenInstanceError):
        request.requested_interval = SINGLE_DAY
    with pytest.raises(FrozenInstanceError):
        request.city.coordinate_version = "changed"
    with pytest.raises(TypeError):
        request.parameters[0] = ("latitude", "0")
    encoded = request.to_json()
    document = json.loads(encoded)
    assert encoded == json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    assert document == {
        "contract_version": "open-meteo-request-v1",
        "endpoint": "https://archive-api.open-meteo.com/v1/archive",
        "city": {
            "city_id": "athens",
            "coordinate_version": "gr-cities-v1",
            "latitude": 37.9838,
            "longitude": 23.7275,
        },
        "dataset": {"provider_id": "open-meteo", "dataset_id": "era5_hourly-v1"},
        "requested_interval": {
            "start": "2025-01-01T03:00:00+00:00",
            "end": "2025-01-17T06:00:00+00:00",
        },
        "retrieval_interval": {
            "start": "2025-01-01T00:00:00+00:00",
            "end": "2025-01-15T00:00:00+00:00",
        },
        "retain_interval": {
            "start": "2025-01-01T03:00:00+00:00",
            "end": "2025-01-15T00:00:00+00:00",
        },
        "parameters": dict(request.parameters),
    }
    document["city"]["latitude"] = 0
    assert request.to_json() == encoded
    assert requested == interval("2025-01-01T03:00", "2025-01-17T06:00")


@pytest.mark.parametrize("city", [None, "athens", {}, []])
def test_invalid_city_object(city):
    with pytest.raises(DomainValidationError) as caught:
        plan(city=city)
    assert (caught.value.code, caught.value.field) == (
        "invalid_city_configuration",
        "city",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"latitude": 37.95},
        {"longitude": 23.75},
        {"coordinate_version": "gr-cities-v2"},
        {"city_id": "patras"},
    ],
)
def test_valid_but_unregistered_city_configuration_rejected(changes):
    city = replace(CITY_REGISTRY["athens"], **changes)
    with pytest.raises(DomainValidationError) as caught:
        plan(city=city)
    assert caught.value.code == "unsupported_city_configuration"


@pytest.mark.parametrize("dataset", [None, "era5", {}])
def test_invalid_dataset_object(dataset):
    with pytest.raises(DomainValidationError, match="invalid_dataset"):
        plan(dataset=dataset)


@pytest.mark.parametrize(
    "provider, name",
    [
        ("synthetic", "era5_hourly-v1"),
        ("open-meteo", "era5-land_hourly-v1"),
        ("open-meteo", "best-match"),
        ("open-meteo", "era5_hourly-v2"),
    ],
)
def test_dataset_mismatch_cannot_silently_select_era5(provider, name):
    with pytest.raises(DomainValidationError) as caught:
        plan(dataset=DatasetIdentity(provider_id=provider, dataset_id=name))
    assert (caught.value.code, caught.value.field) == (
        "incompatible_dataset",
        "dataset",
    )
    assert "open-meteo/era5_hourly-v1" in str(caught.value)


@pytest.mark.parametrize(
    "value", [None, [], "2025-01-01", (SINGLE_DAY.start, SINGLE_DAY.end)]
)
def test_invalid_interval_object(value):
    with pytest.raises(DomainValidationError, match="invalid_interval"):
        plan(value)


def test_equivalent_approved_values_are_accepted():
    assert (
        plan(city=replace(CITY_REGISTRY["athens"]), dataset=replace(ERA5_DATASET))
        == plan()
    )


@pytest.mark.parametrize(
    "retrieval, code",
    [
        (None, "invalid_interval"),
        (interval("2025-01-01T01:00", "2025-01-02"), "invalid_retrieval_interval"),
        (interval("2025-01-01", "2025-01-02T01:00"), "invalid_retrieval_interval"),
        (interval("2025-01-01", "2025-01-16"), "invalid_retrieval_interval"),
        (interval("2024-12-31", "2025-01-01"), "invalid_retrieval_interval"),
        (interval("2025-01-02", "2025-01-03"), "invalid_retrieval_interval"),
    ],
)
def test_direct_request_construction_cannot_bypass_chunk_contract(retrieval, code):
    with pytest.raises(DomainValidationError) as caught:
        OpenMeteoRequest(
            city=CITY_REGISTRY["athens"],
            dataset=ERA5_DATASET,
            requested_interval=SINGLE_DAY,
            retrieval_interval=retrieval,
        )
    assert (caught.value.code, caught.value.field) == (code, "retrieval_interval")


def test_unrepresentable_complete_day_returns_clear_deterministic_error():
    requested = interval("9999-12-31T01:00", "9999-12-31T23:00")
    errors = []
    for _ in range(2):
        with pytest.raises(DomainValidationError) as caught:
            plan(requested)
        errors.append((caught.value.code, caught.value.field, str(caught.value)))
    assert errors[0] == errors[1]
    assert errors[0][:2] == ("unrepresentable_retrieval_interval", "interval.end")


@pytest.mark.parametrize(
    "start, end", [("0001-01-01", "0001-01-02"), ("9999-12-30", "9999-12-31")]
)
def test_datetime_edges_do_not_overflow_chunk_arithmetic(start, end):
    (request,) = plan(interval(start, end))
    assert request.start_date.isoformat() == start
    assert request.end_date.isoformat() == start


def test_full_response_normalizes_against_retrieval_not_retained_interval():
    requested = interval("2025-01-01T05:00", "2025-01-07T03:00")
    (request,) = plan(requested)
    path = Path(__file__).parents[1] / "fixtures/open_meteo/seven_days.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = normalize_open_meteo(
        payload,
        city_id=request.city.city_id,
        dataset=request.dataset,
        interval=request.retrieval_interval,
    )
    assert len(result.observations) == 168
    assert result.metadata.grid_latitude != request.city.latitude
    assert request.retain_interval.expected_hours == 142
    with pytest.raises(DomainValidationError, match="coverage_mismatch"):
        normalize_open_meteo(
            payload,
            city_id=request.city.city_id,
            dataset=request.dataset,
            interval=request.retain_interval,
        )
    # Deliberately no filtering or ingestion implementation in this milestone.
