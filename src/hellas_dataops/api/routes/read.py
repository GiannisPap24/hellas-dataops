"""Thin read-only endpoints with an injectable query service."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from hellas_dataops.api.models import (
    Cities,
    ErrorResponse,
    Health,
    WeatherPage,
    WeatherParameters,
)
from hellas_dataops.storage.queries import ReadQueries

router = APIRouter(
    responses={
        503: {
            "model": ErrorResponse,
            "description": "Database unavailable or incompatible",
        }
    }
)


def get_queries(request: Request) -> ReadQueries:
    return request.app.state.queries


@router.get("/health/live", response_model=Health, tags=["health"])
def live() -> dict:
    return {"status": "alive"}


@router.get("/health/ready", response_model=Health, tags=["health"])
def ready(queries: Annotated[ReadQueries, Depends(get_queries)]) -> dict:
    queries.ready()
    return {"status": "ready"}


@router.get("/v1/cities", response_model=Cities, tags=["weather"])
def cities(queries: Annotated[ReadQueries, Depends(get_queries)]) -> dict:
    """Supported registry points versus actually persisted configuration versions."""
    return queries.cities()


@router.get(
    "/v1/weather",
    response_model=WeatherPage,
    tags=["weather"],
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Unknown city or unpersisted configuration/dataset",
        },
        422: {
            "model": ErrorResponse,
            "description": "Invalid query, UTC hour, interval, or page size",
        },
    },
)
def weather(
    parameters: Annotated[WeatherParameters, Query()],
    queries: Annotated[ReadQueries, Depends(get_queries)],
) -> dict:
    """Current stored values, ordered by UTC hour; no cross-page snapshot guarantee."""
    return queries.weather(parameters.to_query())
