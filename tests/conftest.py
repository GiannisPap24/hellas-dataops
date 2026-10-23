"""Offline protection for the default unit test suite."""

import asyncio
import os
import socket
from contextlib import contextmanager
from typing import NoReturn

import pytest


@pytest.fixture(scope="session")
def api_event_loop():
    """Initialize Windows' internal wake-up socket before per-test network guards.

    No HTTP or database connection is opened. All request execution still occurs
    under the unchanged unit or exact-database-target integration network guard.
    """
    with asyncio.Runner() as runner:
        runner.get_loop()
        yield runner


@pytest.fixture
def api_client(api_event_loop):
    """Synchronous facade over the supported in-memory async ASGI test transport."""
    import httpx2

    @contextmanager
    def make(app):
        class Client:
            def request(self, method, path, **kwargs):
                async def send():
                    async with httpx2.AsyncClient(
                        transport=httpx2.ASGITransport(app=app),
                        base_url="http://testserver",
                        trust_env=False,
                    ) as client:
                        return await client.request(method, path, **kwargs)

                return api_event_loop.run(send())

            def get(self, path, **kwargs):
                return self.request("GET", path, **kwargs)

            def post(self, path, **kwargs):
                return self.request("POST", path, **kwargs)

        yield Client()

    return make


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail fast if a unit test accidentally attempts network access."""

    def denied(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("Network access is disabled in offline unit tests")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture(autouse=True)
def clean_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep configuration tests independent of the developer's shell settings."""
    for name in tuple(os.environ):
        if name.upper().startswith("HELLAS_"):
            monkeypatch.delenv(name)


@pytest.fixture
def persistence_case():
    """Deterministic invented inputs shared by offline and opt-in storage tests."""
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from hellas_dataops.domain import HourlyInterval, HourlyObservation
    from hellas_dataops.sources.cities import CITY_REGISTRY
    from hellas_dataops.sources.open_meteo import OpenMeteoMetadata
    from hellas_dataops.sources.open_meteo_requests import ERA5_DATASET
    from hellas_dataops.storage.mapping import ChunkWrite, RunPlan, WritePolicy

    def make(
        *,
        hours=3,
        start=datetime(2025, 1, 1, 5, tzinfo=UTC),
        number=101,
        policy=WritePolicy.REJECT_CHANGES,
        index=1,
        temperature=10.0,
        age=0,
        city="athens",
    ):
        created = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=age)
        run = RunPlan(
            run_id=UUID(int=number),
            city=CITY_REGISTRY[city],
            dataset=ERA5_DATASET,
            interval=HourlyInterval(start=start, end=start + timedelta(hours=hours)),
            created_at=created,
            policy=policy,
        )
        retained = run.requests[index - 1].retain_interval
        rows = tuple(
            HourlyObservation(
                city_id=city,
                dataset=ERA5_DATASET,
                timestamp_utc=retained.start + timedelta(hours=i),
                temperature_c=temperature,
            )
            for i in range(retained.expected_hours)
        )
        return ChunkWrite(
            run=run,
            chunk_index=index,
            observations=rows,
            metadata=OpenMeteoMetadata(
                grid_latitude=38.0,
                grid_longitude=23.75,
                grid_elevation_m=None,
                timezone="GMT",
                utc_offset_seconds=0,
                timezone_abbreviation=None,
            ),
            retrieved_at=created + timedelta(minutes=1),
            completed_at=created + timedelta(minutes=2),
        )

    return make
