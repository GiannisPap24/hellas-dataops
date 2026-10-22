"""Synthetic-only local CLI: python -m hellas_dataops.ingestion --help."""

import argparse
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from hellas_dataops.domain import DomainValidationError, HourlyInterval
from hellas_dataops.ingestion.coordinator import (
    IngestionError,
    resume_run,
    start_run,
    utc_now,
)
from hellas_dataops.sources.cities import CITY_REGISTRY
from hellas_dataops.sources.open_meteo_requests import ERA5_DATASET
from hellas_dataops.sources.synthetic import fetch_synthetic
from hellas_dataops.storage.mapping import PersistenceError, RunPlan, WritePolicy
from hellas_dataops.storage.run_tracking import inspect_run
from hellas_dataops.storage.test_database import TestDatabaseError, load_test_database


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Synthetic ingestion into a verified disposable database ONLY"
    )
    parser.add_argument("--database-config", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--run-id", required=True, type=UUID)
    start.add_argument("--city", required=True, choices=tuple(CITY_REGISTRY))
    start.add_argument("--start", required=True, type=datetime.fromisoformat)
    start.add_argument("--end", required=True, type=datetime.fromisoformat)
    start.add_argument(
        "--policy", choices=[p.value for p in WritePolicy], default="reject_changes"
    )
    for command in ("status", "resume"):
        sub.add_parser(command).add_argument("--run-id", required=True, type=UUID)
    args = parser.parse_args(argv)
    try:
        target = load_test_database(args.database_config)
        if args.command == "start":
            result = start_run(
                target,
                RunPlan(
                    run_id=args.run_id,
                    city=CITY_REGISTRY[args.city],
                    dataset=ERA5_DATASET,
                    interval=HourlyInterval(start=args.start, end=args.end),
                    created_at=utc_now(),
                    policy=WritePolicy(args.policy),
                ),
                fetch=fetch_synthetic,
            )
        elif args.command == "resume":
            result = resume_run(target, args.run_id, fetch=fetch_synthetic)
        else:
            result = inspect_run(target, args.run_id)
        print(
            json.dumps(
                {
                    "run_id": str(result.plan.run_id),
                    "mode": "synthetic",
                    "status": result.status,
                    "city": result.plan.city.city_id,
                    "coordinate_version": result.plan.city.coordinate_version,
                    "dataset": f"{result.plan.dataset.provider_id}/{result.plan.dataset.dataset_id}",
                    "start": result.plan.interval.start.isoformat(),
                    "end": result.plan.interval.end.isoformat(),
                    "created_at": result.plan.created_at.isoformat(),
                    "finished_at": result.finished_at.isoformat()
                    if result.finished_at
                    else None,
                    "planned_chunks": len(result.chunks),
                    "completed_chunks": result.completed_chunks,
                    "failed_chunks": result.failed_chunks,
                    "policy": result.plan.policy.value,
                    "chunks": [
                        {
                            "index": chunk.index,
                            "status": chunk.status,
                            "inserted": chunk.inserted,
                            "updated": chunk.updated,
                            "unchanged": chunk.unchanged,
                        }
                        for chunk in result.chunks
                    ],
                    "inserted": result.counts[0],
                    "updated": result.counts[1],
                    "unchanged": result.counts[2],
                },
                sort_keys=True,
            )
        )
        return 0
    except (
        PersistenceError,
        TestDatabaseError,
        DomainValidationError,
        IngestionError,
    ) as error:
        # Do not echo driver errors, paths, injected payloads or credentials.
        print(
            json.dumps(
                {
                    "error": "operation_failed",
                    "stage": error.stage
                    if isinstance(error, IngestionError)
                    else "configuration_or_state",
                    "chunk_index": error.chunk_index
                    if isinstance(error, IngestionError)
                    else None,
                    "outcome_unknown": error.outcome_unknown
                    if isinstance(error, IngestionError)
                    else None,
                    "run_id": str(args.run_id),
                    "action": "inspect status; correct the cause before explicit resume",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
