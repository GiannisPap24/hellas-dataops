"""Offline persistence boundary validation; no database substitute."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone
from uuid import UUID

import pytest

from hellas_dataops.domain import DomainValidationError
from hellas_dataops.storage.mapping import (
    PersistenceError,
    WritePolicy,
    measurement_values,
    metadata_values,
    observation_values,
    request_values,
)


def test_mapping_preserves_identity_nulls_and_grid(persistence_case):
    chunk = persistence_case(temperature=None)
    values = observation_values(
        chunk.observations[0],
        city_key=1,
        dataset_key=2,
        run_id=chunk.run.run_id,
        chunk_index=1,
    )
    assert values["source_run_id"] == chunk.run.run_id
    assert values["city_config_id"] == 1 and values["dataset_key"] == 2
    assert all(v is None for v in measurement_values(chunk.observations[0]).values())
    assert metadata_values(chunk.metadata)["grid_latitude"] != chunk.run.city.latitude
    provenance = request_values(chunk.request, chunk.run.policy)
    assert provenance["request"]["parameters"]["models"] == "era5"
    assert provenance["storage_contract"] == "chunk-write-v1"
    with pytest.raises(FrozenInstanceError):
        chunk.chunk_index = 2


def test_digest_order_and_operational_completion(persistence_case):
    chunk = persistence_case()
    reordered = replace(chunk, observations=list(reversed(chunk.observations)))
    assert reordered == chunk
    assert chunk.fingerprint == reordered.fingerprint == persistence_case().fingerprint
    assert len(chunk.fingerprint) == 64
    assert (
        replace(chunk, completed_at=chunk.completed_at + timedelta(hours=1)).fingerprint
        == chunk.fingerprint
    )
    assert (
        replace(
            chunk, retrieved_at=chunk.retrieved_at + timedelta(seconds=1)
        ).fingerprint
        != chunk.fingerprint
    )
    assert (
        replace(chunk, metadata=replace(chunk.metadata, grid_latitude=39)).fingerprint
        != chunk.fingerprint
    )
    assert persistence_case(policy=WritePolicy.REPLACE).fingerprint != chunk.fingerprint


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": "bad"},
        {"run_id": UUID(int=0)},
        {"policy": "replace"},
        {"created_at": None},
    ],
)
def test_invalid_run(persistence_case, changes):
    with pytest.raises(PersistenceError):
        replace(persistence_case().run, **changes)


@pytest.mark.parametrize(
    "field,value",
    [
        ("chunk_index", True),
        ("chunk_index", 0),
        ("chunk_index", 2),
        ("run", None),
        ("metadata", None),
        ("retrieved_at", None),
        ("completed_at", "bad"),
    ],
)
def test_invalid_chunk(persistence_case, field, value):
    with pytest.raises(PersistenceError):
        replace(persistence_case(), **{field: value})


@pytest.mark.parametrize("kind", ["naive", "offset", "before_run", "before_retrieval"])
def test_execution_times(persistence_case, kind):
    chunk = persistence_case()
    changes = {
        "naive": {"retrieved_at": chunk.retrieved_at.replace(tzinfo=None)},
        "offset": {
            "retrieved_at": chunk.retrieved_at.astimezone(timezone(timedelta(hours=2)))
        },
        "before_run": {"retrieved_at": chunk.run.created_at - timedelta(seconds=1)},
        "before_retrieval": {"completed_at": chunk.run.created_at},
    }
    with pytest.raises(PersistenceError, match="invalid_time"):
        replace(chunk, **changes[kind])


@pytest.mark.parametrize(
    "field,value",
    [
        ("grid_latitude", 91),
        ("grid_longitude", -181),
        ("grid_latitude", None),
        ("grid_longitude", True),
        ("grid_elevation_m", float("nan")),
        ("grid_elevation_m", float("inf")),
        ("grid_elevation_m", 10**400),
        ("grid_latitude", "38"),
        ("timezone", "Europe/Athens"),
        ("utc_offset_seconds", True),
        ("utc_offset_seconds", 3600),
        ("timezone_abbreviation", "EET"),
    ],
)
def test_invalid_metadata(persistence_case, field, value):
    chunk = persistence_case()
    with pytest.raises(PersistenceError, match="invalid_metadata"):
        replace(chunk, metadata=replace(chunk.metadata, **{field: value}))


@pytest.mark.parametrize("kind", ["missing", "duplicate", "unexpected", "city"])
def test_domain_coverage_reused(persistence_case, kind):
    chunk = persistence_case()
    rows = chunk.observations
    cases = {
        "missing": rows[:-1],
        "duplicate": rows + rows[:1],
        "unexpected": rows
        + (replace(rows[0], timestamp_utc=chunk.request.retain_interval.end),),
        "city": (replace(rows[0], city_id="patras"), *rows[1:]),
    }
    with pytest.raises(DomainValidationError):
        replace(chunk, observations=cases[kind])


@pytest.mark.parametrize("hours,expected", [(1, 1), (168, 1), (336, 2), (360, 2)])
def test_partial_day_run_preserves_full_plan(persistence_case, hours, expected):
    chunk = persistence_case(hours=hours)
    assert len(chunk.run.requests) == expected  # starts 05:00, not midnight
    assert sum(r.retain_interval.expected_hours for r in chunk.run.requests) == hours
