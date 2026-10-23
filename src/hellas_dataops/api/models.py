"""Public API schemas; canonical domain objects remain framework independent."""

from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hellas_dataops.domain import DatasetIdentity, HourlyInterval
from hellas_dataops.storage.queries import SLUG, WeatherQuery

Slug = Annotated[str, Field(min_length=1, max_length=64, pattern=f"^{SLUG}$")]


class WeatherParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city_id: Slug
    coordinate_version: Slug
    provider_id: Slug
    dataset_id: Slug
    start: datetime = Field(description="Inclusive UTC hour, ISO 8601 with Z or +00:00")
    end: datetime = Field(description="Exclusive UTC hour; at most 31 days after start")
    limit: int = Field(default=100, ge=1, le=500)
    after: datetime | None = Field(
        default=None, description="Exclusive UTC hour cursor inside the same interval"
    )

    @field_validator("start", "end", "after", mode="before")
    @classmethod
    def iso_timestamp(cls, value: object) -> object:
        if value is None or isinstance(value, datetime):
            return value
        if not isinstance(value, str) or "T" not in value:
            raise ValueError(
                "Use an ISO 8601 UTC timestamp, not epoch seconds or a date"
            )
        return datetime.fromisoformat(value)

    @model_validator(mode="after")
    def valid_query(self) -> Self:
        self.to_query()
        return self

    def to_query(self) -> WeatherQuery:
        return WeatherQuery(
            city_id=self.city_id,
            coordinate_version=self.coordinate_version,
            dataset=DatasetIdentity(
                provider_id=self.provider_id, dataset_id=self.dataset_id
            ),
            interval=HourlyInterval(start=self.start, end=self.end),
            limit=self.limit,
            after=self.after,
        )


class Health(BaseModel):
    status: str


class ErrorResponse(BaseModel):
    detail: str


class City(BaseModel):
    city_id: str
    coordinate_version: str
    latitude: float
    longitude: float


class Cities(BaseModel):
    supported: list[City]
    persisted: list[City]


class Dataset(BaseModel):
    provider_id: str
    dataset_id: str
    historical_model: str
    source_contract_version: str


class Units(BaseModel):
    temperature_c: str
    relative_humidity_pct: str
    precipitation_mm: str
    wind_speed_ms: str


class Observation(BaseModel):
    timestamp_utc: datetime
    temperature_c: float | None
    relative_humidity_pct: float | None
    precipitation_mm: float | None
    wind_speed_ms: float | None


class WeatherPage(BaseModel):
    city: City
    dataset: Dataset
    units: Units
    start: datetime
    end: datetime
    observations: list[Observation]
    next_after: datetime | None
