"""In-process HTTP tests with injected reads; existing socket guard stays active."""

from datetime import UTC, datetime

import pytest

from hellas_dataops.api.app import create_app
from hellas_dataops.api.models import WeatherParameters
from hellas_dataops.api.routes.read import get_queries
from hellas_dataops.sources.cities import CITY_REGISTRY
from hellas_dataops.storage.queries import QueryNotFound, QueryUnavailable

PARAMS = dict(
    city_id="athens",
    coordinate_version="gr-cities-v1",
    provider_id="open-meteo",
    dataset_id="era5_hourly-v1",
    start="2025-01-01T00:00:00Z",
    end="2025-01-02T00:00:00Z",
)


class FakeQueries:
    def ready(self):
        pass

    def cities(self):
        from dataclasses import asdict

        return {
            "supported": [asdict(CITY_REGISTRY[k]) for k in sorted(CITY_REGISTRY)],
            "persisted": [],
        }

    def weather(self, query):
        self.query = query
        return {
            "city": self.cities()["supported"][0],
            "dataset": dict(
                provider_id="open-meteo",
                dataset_id="era5_hourly-v1",
                historical_model="era5",
                source_contract_version="open-meteo-request-v1",
            ),
            "units": dict(
                temperature_c="celsius",
                relative_humidity_pct="percent",
                precipitation_mm="mm",
                wind_speed_ms="m/s",
            ),
            "start": query.interval.start,
            "end": query.interval.end,
            "observations": [
                dict(
                    timestamp_utc=query.interval.start,
                    temperature_c=10.0,
                    relative_humidity_pct=None,
                    precipitation_mm=None,
                    wind_speed_ms=None,
                )
            ],
            "next_after": None,
        }


@pytest.fixture
def api(api_client):
    app = create_app()
    fake = FakeQueries()
    app.dependency_overrides[get_queries] = lambda: fake
    with api_client(app) as client:
        yield client, fake


def test_liveness_without_config_or_database(api_client):
    with api_client(create_app()) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        assert client.get("/health/ready").status_code == 503
        assert client.get("/v1/cities").status_code == 503


def test_ready_and_cities(api):
    client, _ = api
    assert client.get("/health/ready").json() == {"status": "ready"}
    result = client.get("/v1/cities").json()
    assert result["persisted"] == []
    assert [c["city_id"] for c in result["supported"]] == sorted(CITY_REGISTRY)
    assert all(c["coordinate_version"] == "gr-cities-v1" for c in result["supported"])


@pytest.mark.parametrize("limit", [1, 100, 500])
def test_valid_request_nulls_units_and_cursor(api, limit):
    client, fake = api
    result = client.get(
        "/v1/weather",
        params={**PARAMS, "limit": limit, "after": "2025-01-01T03:00:00+00:00"},
    )
    assert result.status_code == 200
    data = result.json()
    assert data["observations"][0]["relative_humidity_pct"] is None
    assert data["observations"][0]["timestamp_utc"] == PARAMS["start"]
    assert data["units"]["wind_speed_ms"] == "m/s"
    assert "request_provenance" not in result.text and "run_id" not in result.text
    assert fake.query.limit == limit
    assert fake.query.after == datetime(2025, 1, 1, 3, tzinfo=UTC)


@pytest.mark.parametrize(
    "field,value",
    [
        ("start", "2025-01-01T00:00:00"),
        ("start", "2025-01-01T00:00:00+02:00"),
        ("start", "2025-01-01T00:30:00Z"),
        ("start", "2025-01-01"),
        ("start", "1735689600"),
        ("start", "nonsense"),
        ("end", "2025-01-01T00:00:00Z"),
        ("end", "2024-12-31T00:00:00Z"),
        ("end", "2025-02-02T00:00:00Z"),
        ("end", "2025-01-02T00:00:00-01:00"),
        ("after", "2024-12-31T23:00:00Z"),
        ("after", "2025-01-02T00:00:00Z"),
        ("after", "2025-01-01T00:00:01Z"),
        ("after", "2025-01-01T00:00:00"),
        ("limit", "0"),
        ("limit", "501"),
        ("limit", "1.2"),
        ("limit", "true"),
        ("city_id", "athens'; DROP TABLE weather_hourly;--"),
        ("city_id", "A"),
        ("coordinate_version", ""),
        ("provider_id", "a" * 65),
        ("dataset_id", "a/b"),
        ("unknown", "ignored?"),
    ],
)
def test_invalid_queries_never_reach_storage(api, field, value):
    client, fake = api
    response = client.get("/v1/weather", params={**PARAMS, field: value})
    assert response.status_code == 422
    assert not hasattr(fake, "query")


@pytest.mark.parametrize("missing", list(PARAMS))
def test_required_parameters(api, missing):
    client, _ = api
    assert (
        client.get(
            "/v1/weather", params={k: v for k, v in PARAMS.items() if k != missing}
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "error,status", [(QueryUnavailable, 503), (QueryNotFound, 404)]
)
def test_safe_error_mapping(api, error, status):
    client, fake = api

    def fail(*args):
        raise error("private diagnostics must never escape")

    fake.weather = fail
    result = client.get("/v1/weather", params=PARAMS)
    assert result.status_code == status
    assert "private" not in result.text


def test_readiness_failure_is_safe(api):
    client, fake = api

    def fail():
        raise QueryUnavailable("internal address")

    fake.ready = fail
    result = client.get("/health/ready")
    assert result.status_code == 503 and "internal" not in result.text


def test_asgi_execution_still_blocks_network(api):
    import socket

    client, fake = api

    def forbidden():
        socket.create_connection(("127.0.0.1", 12345))

    fake.ready = forbidden
    with pytest.raises(AssertionError, match="Network access is disabled"):
        client.get("/health/ready")


def test_openapi_documents_actual_limits_and_errors(api):
    client, _ = api
    spec = client.get("/openapi.json").json()
    assert set(spec["paths"]) == {
        "/health/live",
        "/health/ready",
        "/v1/cities",
        "/v1/weather",
    }
    operation = spec["paths"]["/v1/weather"]["get"]
    limit = next(p for p in operation["parameters"] if p["name"] == "limit")
    assert limit["schema"]["maximum"] == 500
    assert {"200", "404", "422", "503"} <= operation["responses"].keys()
    assert client.get("/docs").status_code == 200
    assert client.post("/v1/weather", json={}).status_code == 405


def test_python_parameters_preserve_domain_types():
    params = WeatherParameters(**{**PARAMS, "start": datetime(2025, 1, 1, tzinfo=UTC)})
    assert params.to_query().interval.expected_hours == 24


def test_launcher_is_loopback_only(monkeypatch):
    from hellas_dataops.api import __main__ as launcher

    captured = {}
    monkeypatch.setattr(launcher, "load_test_database", lambda _: None)
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kw: captured.update(kw))
    assert launcher.main(["--database-config", "synthetic.json"]) == 0
    assert captured == {"host": "127.0.0.1", "port": 8000, "access_log": False}
    with pytest.raises(SystemExit):
        launcher.main(["--database-config", "synthetic.json", "--host", "0.0.0.0"])


def test_launcher_rejects_invalid_config():
    from hellas_dataops.api.__main__ import main

    with pytest.raises(SystemExit) as caught:
        main(["--database-config", "missing-synthetic-file.json"])
    assert caught.value.code == 2
