# Architecture and implementation boundaries

Status: accepted architectural direction. Repository foundation, configuration,
and an isolated development environment were implemented through Milestone 1B.
Milestone 2A adds canonical weather objects and pure validation. Milestone 2B
adds pure Open-Meteo JSON normalization. Milestone 2C adds a versioned city
registry and deterministic request planning, without network or database integration.
This document is a design contract, not a claim that the proposed pipeline is
running.

## Decisions

Use a Python modular monolith with PostgreSQL persistence and a FastAPI query
interface. Operate ingestion through a future CLI. Begin with sequential,
synchronous work and bounded batches; introduce concurrency only for a measured
need. There are no workers, queues, schedulers, or external infrastructure in the
current implementation.

The initial Python target is 3.14 because that interpreter is available locally.
The current locked development dependencies and distributions have been tested
on Windows x64 with Python 3.14.4. Other platforms, Python releases, and future
HTTP/database dependencies remain untested. Broaden or change the supported
interpreter range only after verification.
PostgreSQL is approved; its concrete server version and provisioning method
remain to be verified in a later milestone.

## Module dependencies

| Boundary | May depend on | Owns |
| --- | --- | --- |
| `domain` | Standard library and deliberate validation dependencies | Canonical records and pure data rules |
| `sources` | Domain contracts, standard library; future HTTP client | City configuration, request planning, response normalization; transport deferred |
| `storage` | Domain contracts, database libraries | SQL, mappings, transactions |
| `ingestion` | Domain, source adapter, storage | Work planning and lifecycle |
| `api` | Query services/storage, response schemas | HTTP request/response behaviour |
| Future CLI | Configuration and ingestion | Operator inputs and exit codes |

Domain code must not import source, storage, ingestion, or API code. Keep provider
payload shapes outside the canonical domain. Add interfaces only where testing
or actual alternate implementations justify them; no generic repository or
plugin framework is required.

## Implemented domain boundary

The domain package uses only the Python standard library:

- `models.py`: frozen, slotted `DatasetIdentity`, `HourlyInterval`, and
  `HourlyObservation` dataclasses, with construction-time invariant checks.
- `validation.py`: `validate_batch` for one explicitly requested city/dataset
  and interval; returns a sorted tuple and never changes the input sequence.
- `errors.py`: `DomainValidationError`, a `ValueError` with a stable code and
  field path plus an actionable message.

Single-record invariants are checked by constructors; batch validation operates
on those typed records. Dataclass immutability is a programming contract, not a
security boundary against deliberate bypass with Python reflection.

Coverage checks count and inspect received timestamps instead of allocating the
entire requested timeline. Sorting costs O(n log n), with O(n) auxiliary memory
in the received observation count. No benchmark or production throughput is
claimed. Unsorted input is accepted; duplicates are never silently removed.

Canonical records contain city and provider-qualified dataset identity, not
provider response fields or coordinate metadata. Source metadata is separate
from canonical measurements. Registry lookup, persistence, HTTP transport,
and request provenance persistence remain unimplemented.

## Implemented source boundary

`sources/open_meteo.py` accepts one already decoded mapping, an explicit city,
`DatasetIdentity`, and `HourlyInterval`. It validates source structure, required
parallel arrays, declared units, Unix seconds, and grid/timezone metadata. It
delegates measurement and hourly alignment rules to `HourlyObservation` and
coverage/duplicate rules to `validate_batch`; the domain interface is unchanged.

The returned frozen `NormalizedWeather` contains an ordered observation tuple
and frozen `OpenMeteoMetadata`. Returned grid coordinates and optional elevation
remain source metadata. The normalizer neither looks up nor replaces requested
city coordinates. The planner now captures request coordinates and options;
retrieval times and raw-response retention belong to future provenance work.
Caller-supplied dataset identity is
not inferred from response fields and is not proof of upstream ERA5 selection.

No I/O, wall-clock reads, configuration loading, unit conversion, imputation, or
input mutation occurs. Source failures use `OpenMeteoNormalizationError`, a
subclass of the existing `DomainValidationError`. Row failures retain domain
codes and include provider field/index context; batch errors retain the domain
coverage and duplicate messages. No partial result is returned.

Source parsing is O(n) before domain sorting, which remains O(n log n); auxiliary
memory is O(n) in received rows. These are algorithmic bounds, not benchmarks.
No payload byte/row limit is enforced here; a future transport/planner must bound
request sizes. The single-location, integer-Unix-seconds response contract and
its compatibility assumptions are documented in `data-contract.md`.

## Implemented request planning boundary

`sources/cities.py` defines validated frozen `CityCoordinates` objects in a
read-only five-city registry. `gr-cities-v1` preserves the existing documented
points. The source layer owns these requested coordinates, keeping provider
sampling configuration outside the canonical measurement domain.

`sources/open_meteo_requests.py` accepts a registry city, the explicit
`open-meteo/era5_hourly-v1` identity, and a domain `HourlyInterval`. A fixed
source contract selects model, variables, units, GMT, Unix seconds, land cells,
native grid elevation (no elevation downscaling), and JSON output. No HTTP
dependency is needed; `urllib.parse.urlencode` performs serialization only.

The planner expands only the retrieval envelope to UTC midnight boundaries,
then partitions it into chronological chunks of at most 14 calendar days.
Each validated frozen `OpenMeteoRequest` stores the original hourly request and
its retrieval chunk, deriving the retained intersection and inclusive provider
dates. Derived parameters cannot contradict independently stored dates or units.
Canonical compact JSON records request intent, including a contract version;
it contains no retrieval timestamps, response hashes, or claims of model delivery.

Planning is O(k) time and memory for k chunks, without generating hourly records.
No clock-dependent availability check or arbitrary total range limit is applied;
response size limits and availability checks remain future transport concerns.
Coordinate changes must be reviewed and versioned, with future persistence keys
or dataset versions preventing cross-version overwrites.

For partial days, future ingestion must first pass a complete provider response
to the unchanged normalizer with the request's **retrieval interval**. After
successful full coverage validation, it must retain the specified hourly subset
and validate that subset (and eventually the assembled original interval).
Passing a partial retained interval to the normalizer with an unfiltered full
response correctly fails. Neither filtering nor orchestration is implemented.

## Intended ingestion lifecycle

1. Validate versioned city configuration and an explicit UTC hourly interval.
2. Plan deterministic complete-day retrieval chunks of at most 14 days, one city at a time.
3. Fetch with bounded timeouts and retries for transient errors; honour
   `Retry-After`. Do not retry permanent input or contract errors indefinitely.
4. Validate structure, units, timestamps, coverage, and value semantics.
5. Preserve response provenance; normalize full retrieval coverage, then retain
   and revalidate the requested hourly subset.
6. In one database transaction, upsert a valid chunk and mark it complete.
7. Record failure or interruption so the operator can resume deliberately.

Network requests happen outside database transactions. A unique natural key and
per-city/dataset concurrency control will protect overlapping ingestion.
Unchanged records remain unchanged on replay. Failed chunks must not leave
partially committed weather records. Recovery must handle a crash after data
commit as well as before it; completion state and data commit are atomic.

These are requirements for later milestones. There are no database models,
transaction implementations, migrations, or runnable CLI commands yet.

## Persistence direction

Separate city metadata, source/dataset configuration, normalized hourly weather,
run history, and chunk attempts conceptually. The final schema will be designed
with migrations in Milestone 3. Do not use arbitrary JSON for the normalized
weather query model. Bounded raw responses may be retained separately for replay
and diagnosis; retention, storage location, and size limits require a later
decision. A checksum alone cannot reproduce a historical upstream response.

Start with indexes justified by city/dataset/time queries. Partitioning, a data
lake, object storage, and a time-series extension are unnecessary at MVP scale.

## Query interface

Future FastAPI routes will query PostgreSQL and never synchronously fetch
weather upstream. Proposed contracts include city listing, bounded hourly
weather queries, and separate liveness/readiness endpoints. Inputs require
validation, stable ordering, and bounded pagination. Use parameterized queries.
Readiness will report database/schema availability without exposing connection
details. These endpoints do not exist yet.

## Configuration and dependencies

`config.Settings` validates the application environment and intended log level.
`load_settings()` explicitly reads the current process environment; no global
instance, cache, dotenv discovery, logging setup, or I/O is performed on import.
Database and source settings will be added with their consuming features.

Current runtime dependencies are Pydantic and pydantic-settings. pytest,
pytest-cov, and Ruff are declared development tools. FastAPI/Uvicorn, HTTPX,
SQLAlchemy/psycopg, and Alembic are planned dependencies, not current imports.
uv manages `.venv` and the resolved `uv.lock`. Development tools are a dependency
group, not runtime requirements or a published extra. setuptools 84.2.0 is both
the declared build backend and a locked development dependency. Offline builds
use that backend in `.venv`; source and wheel installations are checked in fresh
environments without system-site packages. pytest does not inject `src` into
its import path. See `development.md` for commands and validation limits.

## Validation and observability

Current configuration, domain, normalization, and planning tests run offline with a socket/DNS guard and
isolated application environment variables. Synthetic domain fixtures are in
`tests/unit/conftest.py`; source JSON fixtures are in `tests/fixtures/open_meteo/`.
No fixture was fetched from a provider. Future transport tests will inject HTTP
responses, clocks, and delays. Integration tests require real, disposable
PostgreSQL and explicit opt-in; SQLite cannot validate PostgreSQL semantics.

Future structured logs should include run/chunk IDs, city, requested interval,
attempt, outcome, row counts, and elapsed time. Do not log credentials, full
connection strings, or unrestricted provider payloads. Track inserted, updated,
unchanged, incomplete, and rejected records distinctly.

Before portfolio performance claims, document hardware, software versions,
dataset size, concurrency, warm-up, request counts, and p50/p95 latency. Separate
upstream waiting from normalization and database timings. No throughput, latency,
uptime, or production-readiness result has been established here. Configuration
and domain/source coverage are reported separately by milestone in `development.md`
and say nothing about unimplemented pipeline behaviour.

## Deferred decisions

- PostgreSQL version, local provisioning, and test database safeguards.
- Exact schema, raw-payload retention, and interruption detection.
- Public demo service eligibility and repository code licence.
- Containers, CI/CD, scheduling, cloud hosting, and additional sources.
