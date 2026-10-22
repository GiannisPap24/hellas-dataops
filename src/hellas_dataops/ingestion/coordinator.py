"""Sequential, explicit synthetic ingestion; no implicit live client or retries."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID

from hellas_dataops.sources.open_meteo import normalize_open_meteo
from hellas_dataops.sources.open_meteo_requests import OpenMeteoRequest
from hellas_dataops.storage.mapping import ChunkWrite, PersistenceError, RunPlan
from hellas_dataops.storage.run_tracking import RunProgress, run_session
from hellas_dataops.storage.test_database import TestDatabaseConfig

type Fetch = Callable[[OpenMeteoRequest], Mapping[str, object]]
type Clock = Callable[[], datetime]


class IngestionError(RuntimeError):
    """Safe contextual failure. Progress must be inspected, never inferred."""

    def __init__(self, run_id: UUID, index: int, stage: str, *, unknown=False):
        self.run_id, self.chunk_index, self.stage = run_id, index, stage
        self.outcome_unknown = unknown
        action = (
            "outcome unknown; inspect and explicitly resume"
            if unknown
            else "run failed; inspect and explicitly resume"
        )
        super().__init__(f"Run {run_id}, chunk {index}, {stage}: {action}")


def utc_now() -> datetime:
    return datetime.now(UTC)


def _process(session, state: RunProgress, fetch: Fetch, clock: Clock) -> RunProgress:
    plan = state.plan
    for index, request in enumerate(plan.requests, 1):
        if state.chunks[index - 1].status == "succeeded":
            continue
        stage = "retrieve"
        chunk = None
        try:
            payload = fetch(request)
            retrieved = clock()
            stage = "normalize"
            normalized = normalize_open_meteo(
                payload,
                city_id=plan.city.city_id,
                dataset=plan.dataset,
                interval=request.retrieval_interval,
            )
            retained = request.retain_interval
            chunk = ChunkWrite(
                run=plan,
                chunk_index=index,
                observations=tuple(
                    row
                    for row in normalized.observations
                    if retained.start <= row.timestamp_utc < retained.end
                ),
                metadata=normalized.metadata,
                retrieved_at=retrieved,
                completed_at=clock(),
            )
            # Release provider payload and full-day rows before the next chunk.
            del payload, normalized
            stage = "persist"
            session.write(chunk)
        except Exception:
            # A commit acknowledgement can fail after the server committed. Only
            # a validated durable receipt matching this input resolves that case.
            try:
                state = session.load()
                completed = state.chunks[index - 1]
                if stage == "persist" and completed.status == "succeeded":
                    if completed.fingerprint != chunk.fingerprint:
                        raise PersistenceError(
                            "replay_conflict", "Unexpected committed input"
                        )
                    continue
                session.fail(index, stage, clock())
            except Exception:
                raise IngestionError(plan.run_id, index, stage, unknown=True) from None
            raise IngestionError(plan.run_id, index, stage) from None
        state = session.load()
        # No whole-run weather buffer: only current chunk plus O(chunks) progress.
        del chunk
    return state


def _inputs(fetch, clock):
    if not callable(fetch) or not callable(clock):
        raise PersistenceError(
            "invalid_callable", "Supply explicit synthetic fetch and clock callables"
        )


def start_run(
    target: TestDatabaseConfig, plan: RunPlan, *, fetch: Fetch, clock: Clock = utc_now
) -> RunProgress:
    """Register before retrieval. Existing incomplete identities require resume_run.

    The injected fetch must provide synthetic responses in this milestone. The
    CLI has no live option. No external operation occurs in a DB transaction.
    """
    _inputs(fetch, clock)
    if not isinstance(plan, RunPlan):
        raise PersistenceError("invalid_run", "Supply a validated RunPlan")
    with run_session(target, plan.run_id) as session:
        return _process(session, session.create(plan), fetch, clock)


def resume_run(
    target: TestDatabaseConfig,
    run_id: UUID,
    *,
    fetch: Fetch,
    expected: RunPlan | None = None,
    clock: Clock = utc_now,
) -> RunProgress:
    """Reconstruct persisted intent; optional expected plan must match every field."""
    _inputs(fetch, clock)
    with run_session(target, run_id) as session:
        return _process(session, session.resume(expected), fetch, clock)
