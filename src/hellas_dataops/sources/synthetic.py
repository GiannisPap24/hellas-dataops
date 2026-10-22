"""Invented constant weather for disposable demonstrations; never provider data."""

import io
import json
from contextlib import contextmanager
from datetime import timedelta

from hellas_dataops.sources.open_meteo_client import fetch_open_meteo
from hellas_dataops.sources.open_meteo_requests import OpenMeteoRequest


def synthetic_payload(request: OpenMeteoRequest) -> dict:
    hours = request.retrieval_interval.expected_hours
    return {
        "synthetic": True,
        "latitude": request.city.latitude,
        "longitude": request.city.longitude,
        "timezone": "GMT",
        "timezone_abbreviation": "GMT",
        "utc_offset_seconds": 0,
        "hourly_units": {
            "time": "unixtime",
            "temperature_2m": "°C",
            "relative_humidity_2m": "%",
            "precipitation": "mm",
            "wind_speed_10m": "m/s",
        },
        "hourly": {
            "time": [
                int((request.retrieval_interval.start + timedelta(hours=i)).timestamp())
                for i in range(hours)
            ],
            "temperature_2m": [10.0] * hours,
            "relative_humidity_2m": [None] * hours,
            "precipitation": [None] * hours,
            "wind_speed_10m": [None] * hours,
        },
    }


class _Response(io.BytesIO):
    status = 200

    def getheaders(self):
        return [("Content-Type", "application/json")]


@contextmanager
def synthetic_transport(request, *, timeout_seconds):
    with _Response(json.dumps(synthetic_payload(request)).encode()) as response:
        yield response


def fetch_synthetic(request: OpenMeteoRequest):
    """Exercise the accepted HTTP decoding boundary entirely in memory."""
    return fetch_open_meteo(request, transport=synthetic_transport)
