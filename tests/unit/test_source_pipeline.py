"""Offline component integration using invented weather and injected byte streams.

These tests run the real planner, HTTP client, normalizer, and domain validators.
Only the transport is replaced, except explicit final-coverage fault injection.
"""

import io
import json
import traceback
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import pytest

from hellas_dataops.domain import DatasetIdentity, DomainValidationError, HourlyInterval
from hellas_dataops.ingestion import source_pipeline as pipeline
from hellas_dataops.sources.cities import CITY_REGISTRY
from hellas_dataops.sources.open_meteo_client import (
    OpenMeteoStatusError,
    fetch_open_meteo,
)
from hellas_dataops.sources.open_meteo_requests import (
    ERA5_DATASET,
    plan_open_meteo_requests,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/open_meteo/seven_days.json"
HOUR = timedelta(hours=1)


def interval(start, end):
    return HourlyInterval(
        start=datetime.fromisoformat(start).replace(tzinfo=UTC),
        end=datetime.fromisoformat(end).replace(tzinfo=UTC),
    )


ONE_HOUR = interval("2025-01-01T05:00", "2025-01-01T06:00")
FIFTEEN_DAYS = interval("2025-01-01", "2025-01-16")


def synthetic_payload(request):
    """Extend the labelled synthetic fixture schema to any planned UTC interval."""
    payload = json.loads(FIXTURE.read_bytes())
    start = request.retrieval_interval.start
    hours = request.retrieval_interval.expected_hours
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    times = [int((start + i * HOUR - epoch).total_seconds()) for i in range(hours)]
    payload["hourly"] = {
        "time": times,
        "temperature_2m": [10 + t // 3600 % 24 / 10 for t in times],
        "relative_humidity_2m": [40 + t // 3600 % 40 for t in times],
        "precipitation": [0.5 if t // 3600 % 12 == 0 else 0.0 for t in times],
        "wind_speed_10m": [2 + t // 3600 % 5 for t in times],
    }
    return payload


class SyntheticResponse:
    def __init__(self, payload, *, status=200):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.stream = io.BytesIO(body)
        self.status = status

    def getheaders(self):
        return [("Content-Type", "application/json"), ("Retry-After", "30")]

    def read1(self, size):
        return self.stream.read(min(size, 97))


class SyntheticTransport:
    """Use the same context-managed streaming boundary as the existing client tests."""

    def __init__(self, response_factory=None):
        self.response_factory = response_factory or (
            lambda request, index: SyntheticResponse(synthetic_payload(request))
        )
        self.calls = []
        self.responses = []

    @contextmanager
    def __call__(self, request, *, timeout_seconds):
        self.calls.append((request, timeout_seconds))
        response = self.response_factory(request, len(self.calls))
        self.responses.append(response)
        try:
            yield response
        finally:
            response.stream.close()


def collect(requested, transport, *, city="athens", **client_options):
    return pipeline.collect_open_meteo(
        CITY_REGISTRY[city],
        dataset=ERA5_DATASET,
        interval=requested,
        fetch=partial(
            fetch_open_meteo,
            transport=transport,
            timeout_seconds=2.5,
            **client_options,
        ),
    )


@pytest.mark.parametrize(
    "start, end, hours, retrieval_hours",
    [
        ("2025-01-01T05:00", "2025-01-01T06:00", 1, [24]),
        ("2025-01-01", "2025-01-08", 168, [168]),
        ("2025-01-01", "2025-01-15", 336, [336]),
        ("2025-01-01", "2025-01-16", 360, [336, 24]),
        ("2025-01-01T05:00", "2025-01-15T05:00", 336, [336, 24]),
        ("2025-01-01T05:00", "2025-01-16T03:00", 358, [336, 48]),
        ("2024-02-28T23:00", "2024-03-01T01:00", 26, [72]),
        ("2024-12-25", "2025-01-09", 360, [336, 24]),
        ("2025-03-29", "2025-03-31", 48, [48]),
        ("2025-10-25", "2025-10-27", 48, [48]),
    ],
)
def test_full_component_pipeline_boundaries(start, end, hours, retrieval_hours):
    requested = interval(start, end)
    transport = SyntheticTransport()
    result = collect(requested, transport)
    assert len(result.observations) == hours
    assert [row.timestamp_utc for row in result.observations] == [
        requested.start + i * HOUR for i in range(hours)
    ]
    assert all(row.timestamp_utc.tzinfo is UTC for row in result.observations)
    assert [c.request.retrieval_interval.expected_hours for c in result.chunks] == (
        retrieval_hours
    )
    assert sum(c.request.retain_interval.expected_hours for c in result.chunks) == hours
    assert all(c.request.requested_interval == requested for c in result.chunks)
    assert [r for r, _ in transport.calls] == [c.request for c in result.chunks]
    assert all(timeout == 2.5 for _, timeout in transport.calls)
    assert all(response.stream.closed for response in transport.responses)
    # Timestamp labels, including precipitation's preceding-hour meaning, stay intact.
    for row in result.observations:
        assert row.temperature_c == 10 + row.timestamp_utc.hour / 10
        assert row.precipitation_mm == (0.5 if row.timestamp_utc.hour % 12 == 0 else 0)


@pytest.mark.parametrize("city", list(CITY_REGISTRY))
def test_all_cities_preserve_canonical_identity(city):
    result = collect(ONE_HOUR, SyntheticTransport(), city=city)
    (row,) = result.observations
    assert (row.city_id, row.dataset) == (city, ERA5_DATASET)
    assert result.chunks[0].request.city == CITY_REGISTRY[city]


def test_original_seven_day_json_fixture_through_real_client(seven_day_interval):
    body = FIXTURE.read_bytes()
    result = collect(
        seven_day_interval,
        SyntheticTransport(lambda request, index: SyntheticResponse(body)),
    )
    payload = json.loads(body)
    assert len(result.observations) == 168
    assert [r.temperature_c for r in result.observations] == payload["hourly"][
        "temperature_2m"
    ]
    assert FIXTURE.read_bytes() == body


def test_chunk_provenance_keeps_distinct_grid_metadata_and_request_intent():
    def response(request, index):
        payload = synthetic_payload(request)
        payload.update(latitude=37 + index / 4, longitude=23.5, elevation=50 + index)
        payload["model"] = "untrusted-model-label"
        return SyntheticResponse(payload)

    result = collect(FIFTEEN_DAYS, SyntheticTransport(response))
    assert [c.metadata.grid_latitude for c in result.chunks] == [37.25, 37.5]
    assert [c.metadata.grid_elevation_m for c in result.chunks] == [51, 52]
    plans = plan_open_meteo_requests(
        CITY_REGISTRY["athens"], dataset=ERA5_DATASET, interval=FIFTEEN_DAYS
    )
    for chunk, plan in zip(result.chunks, plans, strict=True):
        assert chunk.request.to_json() == plan.to_json()
        assert chunk.request.city.latitude == 37.9838
        assert chunk.metadata.grid_longitude == 23.5
        assert chunk.metadata.timezone == "GMT"
        assert dict(chunk.request.parameters)["models"] == "era5"


def test_null_measurements_survive_retention_and_final_validation():
    def response(request, index):
        payload = synthetic_payload(request)
        for key, values in payload["hourly"].items():
            if key != "time":
                values[5] = None
        return SyntheticResponse(payload)

    (row,) = collect(ONE_HOUR, SyntheticTransport(response)).observations
    assert row.timestamp_utc == ONE_HOUR.start
    assert (
        row.temperature_c,
        row.relative_humidity_pct,
        row.precipitation_mm,
        row.wind_speed_ms,
    ) == (None, None, None, None)


def test_unsorted_responses_keep_values_paired_and_produce_identical_results():
    def reverse_response(request, index):
        payload = synthetic_payload(request)
        for values in payload["hourly"].values():
            values.reverse()
        return SyntheticResponse(payload)

    expected = collect(FIFTEEN_DAYS, SyntheticTransport())
    assert collect(FIFTEEN_DAYS, SyntheticTransport(reverse_response)) == expected
    assert collect(FIFTEEN_DAYS, SyntheticTransport()) == expected
    with pytest.raises(FrozenInstanceError):
        expected.observations = ()
    with pytest.raises(FrozenInstanceError):
        expected.chunks[0].metadata = None


@pytest.mark.parametrize("position", [0, 5, 23])
@pytest.mark.parametrize(
    "problem, code",
    [("missing", "coverage_mismatch"), ("duplicate", "duplicate_observation")],
)
def test_invalid_hours_fail_even_outside_retained_hour(position, problem, code):
    def response(request, index):
        payload = synthetic_payload(request)
        for values in payload["hourly"].values():
            if problem == "missing":
                values.pop(position)
            else:
                values.insert(position, values[position])
        return SyntheticResponse(payload)

    with pytest.raises(pipeline.SourcePipelineError) as caught:
        collect(ONE_HOUR, SyntheticTransport(response))
    error = caught.value
    assert error.code == code
    assert error.stage == "normalization"
    assert error.chunk_index == 1
    assert error.request.retrieval_interval.expected_hours == 24
    assert error.interval == ONE_HOUR
    assert (error.city_id, error.dataset) == ("athens", ERA5_DATASET)
    assert isinstance(error.__cause__, DomainValidationError)


@pytest.mark.parametrize(
    "problem, code",
    [
        ("empty", "invalid_metadata"),
        ("unit", "invalid_unit"),
        ("metadata", "invalid_metadata"),
        ("array", "invalid_array"),
        ("length", "array_length_mismatch"),
        ("numeric", "invalid_numeric_type"),
        ("epoch", "invalid_epoch"),
        ("unexpected_hour", "coverage_mismatch"),
    ],
)
def test_malformed_complete_responses_fail_before_filtering(problem, code):
    def response(request, index):
        payload = synthetic_payload(request)
        if problem == "empty":
            payload = {}
        elif problem == "unit":
            payload["hourly_units"]["wind_speed_10m"] = "km/h"
        elif problem == "metadata":
            payload["utc_offset_seconds"] = 3600
        elif problem == "array":
            del payload["hourly"]["precipitation"]
        elif problem == "length":
            payload["hourly"]["precipitation"].pop()
        elif problem == "numeric":
            payload["hourly"]["temperature_2m"][0] = True
        elif problem == "epoch":
            payload["hourly"]["time"][0] = None
        else:
            payload["hourly"]["time"][0] -= 3600
        return SyntheticResponse(payload)

    with pytest.raises(pipeline.SourcePipelineError) as caught:
        collect(ONE_HOUR, SyntheticTransport(response))
    assert (caught.value.stage, caught.value.code) == ("normalization", code)


@pytest.mark.parametrize("failure_chunk", [1, 2])
@pytest.mark.parametrize(
    "problem, code",
    [
        ("http", "http_status"),
        ("rate_limit", "http_status"),
        ("timeout", "timeout"),
        ("connection", "transport"),
        ("json", "invalid_json"),
        ("size", "response_too_large"),
        ("weather", "invalid_metadata"),
    ],
)
def test_chunk_failure_is_contextual_and_stops_without_partial_success(
    failure_chunk, problem, code
):
    def response(request, index):
        if index != failure_chunk:
            return SyntheticResponse(synthetic_payload(request))
        if problem == "timeout":
            raise TimeoutError("private network diagnostics")
        if problem == "connection":
            raise ConnectionResetError("private network diagnostics")
        if problem == "http":
            return SyntheticResponse(b"private upstream diagnostics", status=503)
        if problem == "rate_limit":
            return SyntheticResponse(b"private upstream diagnostics", status=429)
        if problem == "json":
            return SyntheticResponse(b'{"private":')
        if problem == "size":
            return SyntheticResponse(b"x" * 50_001)
        return SyntheticResponse({})

    # Three chunks prove a second-chunk failure does not fetch the remaining chunk.
    requested = interval("2025-01-01", "2025-02-01")
    transport = SyntheticTransport(response)
    with pytest.raises(pipeline.SourcePipelineError) as caught:
        collect(requested, transport, max_response_bytes=50_000)
    error = caught.value
    assert error.chunk_index == failure_chunk
    assert error.request == transport.calls[-1][0]
    assert error.code == code
    assert error.stage == ("normalization" if problem == "weather" else "retrieval")
    assert len(transport.calls) == failure_chunk
    assert all(response.stream.closed for response in transport.responses)
    assert not hasattr(error, "observations")
    assert not hasattr(error, "result")
    assert f"chunk {failure_chunk}" in str(error)
    assert "private" not in "".join(traceback.format_exception(error))
    if problem in ("http", "rate_limit"):
        assert isinstance(error.__cause__, OpenMeteoStatusError)
        assert error.__cause__.retry_after_seconds == 30
        assert error.__cause__.status == (429 if problem == "rate_limit" else 503)


@pytest.mark.parametrize(
    "problem, code",
    [
        ("gap", "coverage_mismatch"),
        ("duplicate", "duplicate_observation"),
        ("overlap", "duplicate_observation"),
    ],
)
def test_final_validation_defends_against_incorrect_chunk_assembly(
    monkeypatch, problem, code
):
    plans = plan_open_meteo_requests(
        CITY_REGISTRY["athens"], dataset=ERA5_DATASET, interval=FIFTEEN_DAYS
    )
    if problem == "gap":
        broken = plans[:1]
    elif problem == "duplicate":
        broken = (plans[0], plans[0], plans[1])
    else:
        broken = (
            plans[0],
            replace(plans[1], retrieval_interval=interval("2025-01-14", "2025-01-16")),
        )
    monkeypatch.setattr(pipeline, "plan_open_meteo_requests", lambda *a, **kw: broken)
    with pytest.raises(pipeline.SourcePipelineError) as caught:
        collect(FIFTEEN_DAYS, SyntheticTransport())
    assert caught.value.stage == "final_validation"
    assert caught.value.code == code
    assert caught.value.chunk_index is None
    assert caught.value.request is None
    assert "assembled result" in str(caught.value)


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"city": "athens"}, "invalid_city_configuration"),
        (
            {"dataset": DatasetIdentity(provider_id="synthetic", dataset_id="test")},
            "incompatible_dataset",
        ),
        ({"interval": None}, "invalid_interval"),
    ],
)
def test_invalid_planning_inputs_preserve_domain_errors_before_io(overrides, code):
    transport = SyntheticTransport()
    kwargs = {
        "city": CITY_REGISTRY["athens"],
        "dataset": ERA5_DATASET,
        "interval": ONE_HOUR,
        "fetch": partial(fetch_open_meteo, transport=transport),
        **overrides,
    }
    with pytest.raises(DomainValidationError) as caught:
        pipeline.collect_open_meteo(**kwargs)
    assert caught.value.code == code
    assert transport.calls == []


def test_noncallable_client_fails_without_implicit_live_fallback():
    with pytest.raises(TypeError, match="fetch must be a callable"):
        pipeline.collect_open_meteo(
            CITY_REGISTRY["athens"], dataset=ERA5_DATASET, interval=ONE_HOUR, fetch=None
        )


def test_invalid_http_configuration_propagates_before_io():
    transport = SyntheticTransport()
    with pytest.raises(ValueError, match="max_response_bytes"):
        collect(ONE_HOUR, transport, max_response_bytes=0)
    assert transport.calls == []
