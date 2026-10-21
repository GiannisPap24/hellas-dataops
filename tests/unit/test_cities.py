"""Approved requested points and coordinate validation; no geocoding."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from hellas_dataops.domain import DomainValidationError
from hellas_dataops.domain.models import SUPPORTED_CITY_IDS
from hellas_dataops.sources.cities import CITY_REGISTRY, CityCoordinates


@pytest.mark.parametrize(
    "city_id, latitude, longitude",
    [
        ("athens", 37.9838, 23.7275),
        ("thessaloniki", 40.6401, 22.9444),
        ("patras", 38.2466, 21.7346),
        ("heraklion", 35.3387, 25.1442),
        ("larissa", 39.6390, 22.4191),
    ],
)
def test_registry_matches_documented_coordinates(city_id, latitude, longitude):
    city = CITY_REGISTRY[city_id]
    assert city == CityCoordinates(
        city_id=city_id,
        latitude=latitude,
        longitude=longitude,
        coordinate_version="gr-cities-v1",
    )
    assert set(CITY_REGISTRY) == SUPPORTED_CITY_IDS


def test_registry_and_coordinates_are_immutable():
    city = CITY_REGISTRY["athens"]
    with pytest.raises(TypeError):
        CITY_REGISTRY["athens"] = None
    with pytest.raises(FrozenInstanceError):
        city.latitude = 0
    assert city == replace(city)
    assert hash(city) == hash(replace(city))


@pytest.mark.parametrize("city_id", ["Athens", "unknown", "athens ", None, []])
def test_invalid_city_identifier(city_id):
    with pytest.raises(DomainValidationError) as caught:
        replace(CITY_REGISTRY["athens"], city_id=city_id)
    assert (caught.value.code, caught.value.field) == ("invalid_city_id", "city_id")


@pytest.mark.parametrize(
    "version", [None, 1, [], "", "V1", "v 1", "v--1", "1", "v" * 65]
)
def test_invalid_coordinate_version(version):
    with pytest.raises(DomainValidationError) as caught:
        replace(CITY_REGISTRY["athens"], coordinate_version=version)
    assert (caught.value.code, caught.value.field) == (
        "invalid_coordinate_version",
        "coordinate_version",
    )


@pytest.mark.parametrize("field", ["latitude", "longitude"])
@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "1",
        [],
        {},
        Decimal("1"),
        float("nan"),
        float("inf"),
        float("-inf"),
        10**400,
    ],
)
def test_invalid_coordinate_values(field, value):
    with pytest.raises(DomainValidationError) as caught:
        replace(CITY_REGISTRY["athens"], **{field: value})
    assert (caught.value.code, caught.value.field) == ("invalid_coordinate", field)


@pytest.mark.parametrize(
    "field, value",
    [
        ("latitude", -90.01),
        ("latitude", 90.01),
        ("longitude", -180.01),
        ("longitude", 180.01),
    ],
)
def test_coordinate_bounds(field, value):
    with pytest.raises(DomainValidationError, match="invalid_coordinate"):
        replace(CITY_REGISTRY["athens"], **{field: value})


@pytest.mark.parametrize("latitude, longitude", [(-90, -180), (90, 180), (0, 0)])
def test_valid_boundary_coordinates_are_floats(latitude, longitude):
    city = CityCoordinates(
        city_id="athens",
        latitude=latitude,
        longitude=longitude,
        coordinate_version="v" * 64,
    )
    assert city.latitude == latitude
    assert city.longitude == longitude
    assert type(city.latitude) is float
    assert type(city.longitude) is float
