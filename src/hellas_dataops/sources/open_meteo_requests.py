"""Offline ERA5 request planning; no HTTP, observation filtering, or clock reads."""

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from urllib.parse import urlencode

from hellas_dataops.domain import DatasetIdentity, DomainValidationError, HourlyInterval
from hellas_dataops.sources.cities import CITY_REGISTRY, CityCoordinates

ERA5_DATASET = DatasetIdentity(provider_id="open-meteo", dataset_id="era5_hourly-v1")
_DAY = timedelta(days=1)
_MAX_CHUNK = 14 * _DAY
_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
)
_OPTIONS = (
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


def _validate_context(
    city: CityCoordinates, dataset: DatasetIdentity, interval: HourlyInterval
) -> None:
    if not isinstance(city, CityCoordinates):
        raise DomainValidationError(
            "invalid_city_configuration",
            "city",
            "provide a CityCoordinates registry entry",
        )
    if city != CITY_REGISTRY[city.city_id]:
        raise DomainValidationError(
            "unsupported_city_configuration",
            "city",
            "coordinates and version must match the approved registry; version changes require review",
        )
    if not isinstance(dataset, DatasetIdentity):
        raise DomainValidationError(
            "invalid_dataset", "dataset", "provide DatasetIdentity"
        )
    if dataset != ERA5_DATASET:
        raise DomainValidationError(
            "incompatible_dataset", "dataset", "expected open-meteo/era5_hourly-v1"
        )
    if not isinstance(interval, HourlyInterval):
        raise DomainValidationError(
            "invalid_interval", "interval", "provide HourlyInterval"
        )


def _complete_days(interval: HourlyInterval) -> HourlyInterval:
    start = interval.start.replace(hour=0)
    end = interval.end.replace(hour=0)
    if interval.end.hour:
        try:
            end += _DAY
        except OverflowError as error:
            raise DomainValidationError(
                "unrepresentable_retrieval_interval",
                "interval.end",
                "rounding to a complete provider day would exceed datetime year 9999",
            ) from error
    return HourlyInterval(start=start, end=end)


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenMeteoRequest:
    """One validated retrieval chunk plus the unchanged original hourly request.

    Parameters and retention boundaries are derived, so they cannot disagree
    with the stored intervals. This is request intent, not response provenance.
    """

    city: CityCoordinates
    dataset: DatasetIdentity
    requested_interval: HourlyInterval
    retrieval_interval: HourlyInterval
    contract_version: str = field(default="open-meteo-request-v1", init=False)
    endpoint: str = field(
        default="https://archive-api.open-meteo.com/v1/archive", init=False
    )

    def __post_init__(self) -> None:
        _validate_context(self.city, self.dataset, self.requested_interval)
        retrieval = self.retrieval_interval
        if not isinstance(retrieval, HourlyInterval):
            raise DomainValidationError(
                "invalid_interval", "retrieval_interval", "provide HourlyInterval"
            )
        if retrieval.start.hour or retrieval.end.hour:
            raise DomainValidationError(
                "invalid_retrieval_interval",
                "retrieval_interval",
                "provider retrieval must start and end at UTC midnight",
            )
        if retrieval.end - retrieval.start > _MAX_CHUNK:
            raise DomainValidationError(
                "invalid_retrieval_interval",
                "retrieval_interval",
                "maximum 14 UTC calendar days",
            )
        envelope = _complete_days(self.requested_interval)
        if retrieval.start < envelope.start or retrieval.end > envelope.end:
            raise DomainValidationError(
                "invalid_retrieval_interval",
                "retrieval_interval",
                "chunk must lie inside the complete-day envelope of the original request",
            )

    @property
    def retain_interval(self) -> HourlyInterval:
        """The hourly subset future ingestion must keep after full validation."""
        return HourlyInterval(
            start=max(self.requested_interval.start, self.retrieval_interval.start),
            end=min(self.requested_interval.end, self.retrieval_interval.end),
        )

    @property
    def start_date(self) -> date:
        return self.retrieval_interval.start.date()

    @property
    def end_date(self) -> date:
        """Inclusive provider date; midnight at the exclusive end is not fetched."""
        return (self.retrieval_interval.end - _DAY).date()

    @property
    def parameters(self) -> tuple[tuple[str, str], ...]:
        """Fixed-order string pairs, including all selected units and options."""
        return (
            ("latitude", str(self.city.latitude)),
            ("longitude", str(self.city.longitude)),
            ("start_date", self.start_date.isoformat()),
            ("end_date", self.end_date.isoformat()),
            ("hourly", ",".join(_VARIABLES)),
            *_OPTIONS,
        )

    @property
    def query_string(self) -> str:
        """Percent-encoded parameters; never performs an HTTP operation."""
        return urlencode(self.parameters)

    @property
    def url(self) -> str:
        return f"{self.endpoint}?{self.query_string}"

    def to_json(self) -> str:
        """Canonical request provenance: sorted keys, compact JSON, explicit UTC."""

        def interval_data(interval: HourlyInterval) -> dict[str, str]:
            return {
                "start": interval.start.isoformat(),
                "end": interval.end.isoformat(),
            }

        return json.dumps(
            {
                "contract_version": self.contract_version,
                "endpoint": self.endpoint,
                "city": asdict(self.city),
                "dataset": asdict(self.dataset),
                "requested_interval": interval_data(self.requested_interval),
                "retrieval_interval": interval_data(self.retrieval_interval),
                "retain_interval": interval_data(self.retain_interval),
                "parameters": dict(self.parameters),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )


def plan_open_meteo_requests(
    city: CityCoordinates, *, dataset: DatasetIdentity, interval: HourlyInterval
) -> tuple[OpenMeteoRequest, ...]:
    """Plan chronological, nonoverlapping complete-day chunks of at most 14 days.

    Partial days are expanded only for retrieval. Each chunk preserves the
    original interval and identifies its retained intersection. Availability
    and upstream model selection cannot be established by offline planning.
    """
    _validate_context(city, dataset, interval)
    envelope = _complete_days(interval)
    cursor = envelope.start
    requests = []
    while cursor < envelope.end:
        end = cursor + min(_MAX_CHUNK, envelope.end - cursor)
        requests.append(
            OpenMeteoRequest(
                city=city,
                dataset=dataset,
                requested_interval=interval,
                retrieval_interval=HourlyInterval(start=cursor, end=end),
            )
        )
        cursor = end
    return tuple(requests)
