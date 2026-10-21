"""Sequential source integration with explicit client injection; no persistence."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from hellas_dataops.domain import (
    DatasetIdentity,
    DomainValidationError,
    HourlyInterval,
    HourlyObservation,
    validate_batch,
)
from hellas_dataops.sources.cities import CityCoordinates
from hellas_dataops.sources.open_meteo import OpenMeteoMetadata, normalize_open_meteo
from hellas_dataops.sources.open_meteo_client import OpenMeteoClientError
from hellas_dataops.sources.open_meteo_requests import (
    OpenMeteoRequest,
    plan_open_meteo_requests,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ChunkProvenance:
    """Request intent and returned grid metadata, kept as separate objects."""

    request: OpenMeteoRequest
    metadata: OpenMeteoMetadata


@dataclass(frozen=True, slots=True, kw_only=True)
class SourcePipelineResult:
    """Complete retained observations and provenance in planned chunk order."""

    observations: tuple[HourlyObservation, ...]
    chunks: tuple[ChunkProvenance, ...]


class SourcePipelineError(Exception):
    """Expected retrieval/validation failure; never carries a partial result."""

    def __init__(
        self,
        *,
        city_id: str,
        dataset: DatasetIdentity,
        interval: HourlyInterval,
        stage: str,
        chunk_index: int | None,
        request: OpenMeteoRequest | None,
        error: OpenMeteoClientError | DomainValidationError,
    ) -> None:
        self.city_id = city_id
        self.dataset = dataset
        self.interval = interval
        self.stage = stage
        self.chunk_index = chunk_index
        self.request = request
        self.code = error.code
        location = "assembled result" if chunk_index is None else f"chunk {chunk_index}"
        super().__init__(
            f"{city_id} {dataset.provider_id}/{dataset.dataset_id} "
            f"[{interval.start.isoformat()}, {interval.end.isoformat()}): "
            f"{location}, {stage}: {error}"
        )


def collect_open_meteo(
    city: CityCoordinates,
    *,
    dataset: DatasetIdentity,
    interval: HourlyInterval,
    fetch: Callable[[OpenMeteoRequest], Mapping[str, object]],
) -> SourcePipelineResult:
    """Plan, fetch, normalize full days, retain requested hours, then validate.

    Supply a configured client, for example functools.partial(fetch_open_meteo,
    transport=synthetic_transport). There is deliberately no default live client.
    Planning/input errors propagate unchanged before retrieval. Expected client
    and validation failures include stage and 1-based chunk context and preserve
    the original exception as their cause. No partial result is yielded/returned.
    Determinism requires identical supplied responses; no replay guarantee is made.
    """
    if not callable(fetch):
        raise TypeError("fetch must be a callable accepting an OpenMeteoRequest")
    requests = plan_open_meteo_requests(city, dataset=dataset, interval=interval)
    rows: list[HourlyObservation] = []
    chunks: list[ChunkProvenance] = []
    try:
        for _chunk_index, request in enumerate(requests, start=1):
            stage = "retrieval"
            payload = fetch(request)
            stage = "normalization"
            normalized = normalize_open_meteo(
                payload,
                city_id=city.city_id,
                dataset=dataset,
                interval=request.retrieval_interval,
            )
            stage = "retention"
            retain = request.retain_interval
            retained = tuple(
                row
                for row in normalized.observations
                if retain.start <= row.timestamp_utc < retain.end
            )
            rows.extend(
                validate_batch(
                    retained, city_id=city.city_id, dataset=dataset, interval=retain
                )
            )
            chunks.append(
                ChunkProvenance(request=request, metadata=normalized.metadata)
            )
        stage = "final_validation"
        _chunk_index = None
        request = None
        observations = validate_batch(
            rows, city_id=city.city_id, dataset=dataset, interval=interval
        )
    except (OpenMeteoClientError, DomainValidationError) as error:
        raise SourcePipelineError(
            city_id=city.city_id,
            dataset=dataset,
            interval=interval,
            stage=stage,
            chunk_index=_chunk_index,
            request=request,
            error=error,
        ) from error
    return SourcePipelineResult(observations=observations, chunks=tuple(chunks))
