"""Pure coverage validation for a single explicitly requested city and dataset."""

from collections.abc import Sequence

from hellas_dataops.domain.errors import DomainValidationError
from hellas_dataops.domain.models import (
    HOUR,
    DatasetIdentity,
    HourlyInterval,
    HourlyObservation,
    validate_city_id,
)


def validate_batch(
    observations: Sequence[HourlyObservation],
    *,
    city_id: str,
    dataset: DatasetIdentity,
    interval: HourlyInterval,
) -> tuple[HourlyObservation, ...]:
    """Return complete coverage in timestamp order, without mutating input.

    All-null observations still cover an hour. Duplicate identities are errors,
    even if their values match. Errors fail the whole batch; no partial result
    is returned. Coverage checks allocate space for received observations only,
    not for every hour of a potentially very large requested interval.
    """
    validate_city_id(city_id)
    if not isinstance(dataset, DatasetIdentity):
        raise DomainValidationError(
            "invalid_dataset", "dataset", "provide the requested DatasetIdentity"
        )
    if not isinstance(interval, HourlyInterval):
        raise DomainValidationError(
            "invalid_interval", "interval", "provide the requested HourlyInterval"
        )
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
        raise DomainValidationError(
            "invalid_batch", "observations", "provide a sequence of HourlyObservation"
        )

    rows = tuple(observations)
    seen = set()
    for index, row in enumerate(rows):
        field = f"observations[{index}]"
        if not isinstance(row, HourlyObservation):
            raise DomainValidationError(
                "invalid_observation", field, "provide an HourlyObservation"
            )
        if row.city_id != city_id:
            raise DomainValidationError(
                "city_mismatch", f"{field}.city_id", f"expected city {city_id}"
            )
        if row.dataset != dataset:
            raise DomainValidationError(
                "dataset_mismatch",
                f"{field}.dataset",
                f"expected {dataset.provider_id}/{dataset.dataset_id}",
            )
        if row.timestamp_utc in seen:
            raise DomainValidationError(
                "duplicate_observation",
                f"{field}.timestamp_utc",
                f"duplicate hour {row.timestamp_utc.isoformat()}; remove the duplicate",
            )
        seen.add(row.timestamp_utc)

    ordered = tuple(sorted(rows, key=lambda row: row.timestamp_utc))
    within = tuple(
        row for row in ordered if interval.start <= row.timestamp_utc < interval.end
    )
    unexpected = tuple(
        row for row in ordered if not interval.start <= row.timestamp_utc < interval.end
    )
    missing_count = interval.expected_hours - len(within)
    if missing_count or unexpected:
        problems = [f"expected {interval.expected_hours} hourly observations"]
        if missing_count:
            first_missing = interval.start
            for row in within:
                if row.timestamp_utc != first_missing:
                    break
                first_missing += HOUR
            problems.append(
                f"missing {missing_count} hour(s), first {first_missing.isoformat()}"
            )
        if unexpected:
            problems.append(
                f"unexpected {len(unexpected)} hour(s), "
                f"first {unexpected[0].timestamp_utc.isoformat()}"
            )
        raise DomainValidationError(
            "coverage_mismatch", "observations", "; ".join(problems)
        )
    return ordered
