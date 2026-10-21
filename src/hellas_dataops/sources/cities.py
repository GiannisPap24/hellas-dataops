"""Versioned requested city points, independent of returned provider grid cells."""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from hellas_dataops.domain import DomainValidationError
from hellas_dataops.domain.models import validate_city_id

_VERSION = re.compile(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*")


@dataclass(frozen=True, slots=True, kw_only=True)
class CityCoordinates:
    """A validated requested WGS84 point; membership is checked by the planner."""

    city_id: str
    latitude: float
    longitude: float
    coordinate_version: str

    def __post_init__(self) -> None:
        validate_city_id(self.city_id)
        if (
            not isinstance(self.coordinate_version, str)
            or not 1 <= len(self.coordinate_version) <= 64
            or _VERSION.fullmatch(self.coordinate_version) is None
        ):
            raise DomainValidationError(
                "invalid_coordinate_version",
                "coordinate_version",
                "use a 1-64 character lowercase ASCII version slug starting with a letter",
            )
        for field, bound in (("latitude", 90), ("longitude", 180)):
            value = getattr(self, field)
            if type(value) not in (int, float):
                raise DomainValidationError(
                    "invalid_coordinate",
                    field,
                    "provide a finite built-in int or float",
                )
            try:
                number = float(value)
            except OverflowError as error:
                raise DomainValidationError(
                    "invalid_coordinate", field, "coordinate must fit in a finite float"
                ) from error
            if not math.isfinite(number) or not -bound <= number <= bound:
                raise DomainValidationError(
                    "invalid_coordinate",
                    field,
                    f"provide a finite number in [-{bound}, {bound}]",
                )
            object.__setattr__(self, field, number)


# Coordinates are copied from docs/data-contract.md, not geocoded or inferred.
CITY_REGISTRY: Mapping[str, CityCoordinates] = MappingProxyType(
    {
        city_id: CityCoordinates(
            city_id=city_id,
            latitude=latitude,
            longitude=longitude,
            coordinate_version="gr-cities-v1",
        )
        for city_id, latitude, longitude in (
            ("athens", 37.9838, 23.7275),
            ("thessaloniki", 40.6401, 22.9444),
            ("patras", 38.2466, 21.7346),
            ("heraklion", 35.3387, 25.1442),
            ("larissa", 39.6390, 22.4191),
        )
    }
)
