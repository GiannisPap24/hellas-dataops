"""Deterministic synthetic weather fixtures; these are not provider measurements."""

from datetime import UTC, datetime, timedelta

import pytest

from hellas_dataops.domain import DatasetIdentity, HourlyInterval, HourlyObservation


@pytest.fixture
def synthetic_dataset() -> DatasetIdentity:
    return DatasetIdentity(provider_id="synthetic", dataset_id="hourly-v1")


@pytest.fixture
def seven_day_interval() -> HourlyInterval:
    return HourlyInterval(
        start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2025, 1, 8, tzinfo=UTC)
    )


@pytest.fixture
def synthetic_week(
    synthetic_dataset: DatasetIdentity, seven_day_interval: HourlyInterval
) -> tuple[HourlyObservation, ...]:
    return tuple(
        HourlyObservation(
            city_id="athens",
            dataset=synthetic_dataset,
            timestamp_utc=seven_day_interval.start + timedelta(hours=hour),
            temperature_c=10.0 + hour % 24 / 10,
            relative_humidity_pct=40 + hour % 40,
            precipitation_mm=0.5 if hour % 12 == 0 else 0.0,
            wind_speed_ms=2.0 + hour % 5,
        )
        for hour in range(168)
    )
