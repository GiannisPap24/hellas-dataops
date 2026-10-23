# Architecture and implementation boundaries

Status: accepted architectural direction. Repository foundation, configuration,
and an isolated development environment were implemented through Milestone 1B.
Milestone 2A adds canonical weather objects and pure validation. Milestone 2B
adds pure Open-Meteo JSON normalization. Milestone 2C adds a versioned city
registry and deterministic request planning. Milestone 2D adds a bounded HTTP
client verified with offline transports; no live requests or database integration.
Milestone 2E connects planning, retrieval, normalization, retention, and validation
in an in-memory application workflow with explicit client injection.
Milestone 3A adds five PostgreSQL Core tables and a packaged initial Alembic
revision, tested through offline SQL generation only.
Milestone 3B.1 adds guarded test connections and a host-assisted PostgreSQL
verification workflow. It is accepted after 54 real PostgreSQL 18.6 tests passed,
including upgrade/downgrade/re-upgrade, constraints, and pg8000 transactions.
Milestone 3B.2 adds explicit mapping and transactional chunk persistence in
`storage.mapping` and `storage.persistence`, accepted after 83 real PostgreSQL
tests and 750 offline tests. This verifies the tested chunk and writer scenarios,
not arbitrary crash recovery or an operational ingestion command.
The storage service owns transactions; source retrieval stays outside them.
See [persistence contracts](persistence.md) for locking, replay, correction and
provenance decisions. Automated recovery remains unimplemented.
Milestone 3C.1 implements explicit synthetic start/status/resume operations; its
837 offline tests and 109 real PostgreSQL tests pass. Milestone 3C.1 is accepted.
This document is a design contract, not a claim that the proposed pipeline is
running.

## Decisions

Use a Python modular monolith with PostgreSQL persistence and a FastAPI query
interface. Operate synthetic ingestion through the implemented Python CLI. Begin with sequential,
synchronous work and bounded batches; introduce concurrency only for a measured
need. There are no workers, queues, schedulers, or external infrastructure in the
current implementation.

The initial Python target is 3.14 because that interpreter is available locally.
The current locked development dependencies and distributions have been tested
on Windows x64 with Python 3.14.4. Other platforms, Python releases, and future
HTTP/database dependencies remain untested. Broaden or change the supported
interpreter range only after verification.
PostgreSQL 18.6 with the guarded official-image host workflow is verified through
3C.1, including the new sequential lifecycle and explicit resume cases.

## Module dependencies

| Boundary | May depend on | Owns |
| --- | --- | --- |
| `domain` | Standard library and deliberate validation dependencies | Canonical records and pure data rules |
| `sources` | Domain contracts, standard library | City configuration, request planning, bounded HTTPS retrieval, response normalization |
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
from canonical measurements. The source layer handles registry lookup and HTTP
transport; storage now persists request provenance and canonical observations.

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
No payload byte/row limit is enforced inside the normalizer; the planner bounds
requests to 14 days and the HTTP client bounds response bytes. Direct callers
of the normalizer remain responsible for input size. The single-location, integer-Unix-seconds response contract and
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
the HTTP client now enforces response size limits. Availability remains unverified.
Coordinate changes must be reviewed and versioned, with future persistence keys
or dataset versions preventing cross-version overwrites.

For partial days, the source pipeline first passes a complete provider response
to the unchanged normalizer with the request's **retrieval interval**. After
successful full coverage validation, it must retain the specified hourly subset
and validate that subset (and eventually the assembled original interval).
Passing a partial retained interval to the normalizer with an unfiltered full
response correctly fails. These retention steps are implemented in Milestone 2E;
the new sequential coordinator now persists these retained chunks; automated orchestration remains future work.

## Implemented HTTP retrieval boundary

`sources/open_meteo_client.py` consumes one `OpenMeteoRequest` and returns a
decoded JSON object. `ArchiveTransport` and `ArchiveResponse` are the small
streaming test boundary; injected transports own cleanup, bounded reads, and
timeout enforcement. The default context manager opens one `HTTPSConnection`,
verifies TLS using Python's default context, and closes response and connection
on success or failure. It does not read proxy settings or follow redirects.

Only HTTP 200 with uncompressed `application/json` is decoded. Content-Length
is checked when present; actual streamed bytes are always bounded, including
chunked and connection-close framing. JSON syntax, finite numbers, unique keys,
UTF-8, a single object, and maximum nesting are enforced before returning data.
Weather semantics remain the normalizer's responsibility. Exception messages
omit raw bodies, headers, and underlying network errors. Parsed Retry-After
seconds/dates are advisory; there is no retry loop, sleeping, or clock read.

This standard-library implementation needs no dependency or lockfile changes.
The configurable socket timeout is not an end-to-end deadline or DNS deadline.
The byte cap bounds the retained body, not all HTTP header/framing overhead or
the decoded Python object's memory. Tests exercise the real HTTP response parser
over in-memory wire bytes as well as injected responses. Live TLS, DNS, provider
compatibility, and service eligibility have not been exercised. Persistent
ingestion, provenance persistence, and retries remain later work.

## Implemented source pipeline

`ingestion/source_pipeline.py` is the application boundary. Its
`collect_open_meteo(city, *, dataset, interval, fetch)` function calls the existing
planner, then sequentially invokes the supplied client for each request. No
default client, configuration loading, network call on import, new dependency,
concurrency, scheduler, or persistence is introduced. Tests bind the real
`fetch_open_meteo` client to a synthetic `ArchiveTransport` using `functools.partial`.

For each chunk, normalize the entire `retrieval_interval` before filtering to
`retain_interval`. Validate the retained subset, then validate all assembled rows
against the original interval. Existing domain checks reject missing hours,
duplicates, overlaps, and wrong identities and return chronological tuples.
Bad data outside the retained hours still rejects the complete provider response.
Null values remain null and precipitation timestamp labels are never shifted.

The frozen `SourcePipelineResult` contains `observations` and ordered `chunks`.
Each frozen `ChunkProvenance` pairs an unchanged `OpenMeteoRequest` with its
validated `OpenMeteoMetadata`. Grid metadata may vary between chunks and is
preserved per response; it never replaces versioned requested coordinates.
Request model selection records intent, not independent proof of ERA5 delivery.

Expected client/domain failures become `SourcePipelineError` with the original
cause, stage, 1-based chunk index/request, and original city/dataset/interval.
Final assembly errors have no individual chunk index/request. Invalid planning
inputs and client programming/configuration errors propagate unchanged. Processing
stops on failure; no partial result is returned or persisted. Already completed
fetches cannot be undone. There is no automatic retry or resumable run state.

Memory grows with retained observations and chunk metadata across the requested
interval; full provider chunks are normalized individually. The HTTP byte limit
applies per response, not to all decoded objects or the full result. There is no
total interval cap or whole-request/DNS deadline. Deterministic output requires
identical inputs and response contents; repeatable upstream values are not assured.

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

The in-memory source workflow remains unchanged. Milestone 3C.1 adds a separate
sequential coordinator and synthetic-only CLI with explicit durable resume. See
[operational ingestion](ingestion.md); its 26 new real database tests pass. Automatic
retries and background recovery remain future work.

## Implemented storage foundation

`storage.schema` exports SQLAlchemy Core metadata and five tables: `city_configs`,
`datasets`, `weather_hourly`, `ingestion_runs`, and `ingestion_chunks`. Canonical
domain dataclasses remain unchanged. There are no ORM entities or generic repositories. The accepted Core writer
owns atomic chunk transactions, and the operational session adds run ownership. The separate `storage.test_database` module
provides explicit test-only configuration and guarded connections using pg8000.

Versioned requested coordinates and provider-qualified dataset identities define
the observation key. Weather rows reference a source chunk and must match its
run's city/dataset through composite foreign keys. Request intent is retained as
JSONB on the chunk; returned grid metadata has separate typed columns. Run/chunk
status is operational state, separate from immutable-by-contract configuration.
See [the complete relational contract](storage-schema.md) for keys, checks,
indexes, future mapping responsibilities, and intentionally unenforced cross-row
rules. No separate attempt/event table, raw-response table, partition, or extension
is justified in this milestone.

`storage.migration.migration_config()` locates packaged Alembic resources without
checkout paths or credentials. Revision `20261022_0001` is an explicit frozen
snapshot, not an import of mutable application tables. Upgrade and downgrade SQL
compile for PostgreSQL without a driver. Arbitrary database URLs are rejected.
Online execution requires typed disposable configuration, verified server identity
and ownership, and empty application tables. Source archives include the root
INI; wheels include revisions, environment entry point, and the revision template.

Do not use arbitrary JSON for the normalized weather query model. Bounded raw
responses may be retained separately for replay
and diagnosis; retention, storage location, and size limits require a later
decision. A checksum alone cannot reproduce a historical upstream response.

Start with indexes justified by city/dataset/time queries. Partitioning, a data
lake, object storage, and a time-series extension are unnecessary at MVP scale.

## Query interface

`api.app.create_app(target=None)` constructs the application without I/O.
Synchronous routes depend on `get_queries`; tests override that dependency or use
in-memory ASGI transport against the real query service. `api.models` owns HTTP
schemas; `storage.queries.WeatherQuery` reuses domain UTC/interval validation.
`ReadQueries` resolves public identities, projects canonical fields and fetches
at most limit + 1 rows with Core bound parameters and the existing weather PK.

Each database operation opens the existing verified disposable connection,
starts a read-only REPEATABLE READ transaction, and closes it on every outcome.
No engine exists at import time and no API route retrieves weather or writes
application rows. A page is internally consistent; separate pages are current
reads, not one retained snapshot. Readiness checks revision, column types,
nullability and SELECT availability, not every constraint definition on each call.
See [the API contract](api.md) for exact errors, limits, configuration and demos.
PostgreSQL-backed query verification passed: 24 API cases plus 109 existing
database cases. See the final 3D.1 acceptance record in development.md.

## Configuration and dependencies

`config.Settings` validates the application environment and intended log level.
`load_settings()` explicitly reads the current process environment; no global
instance, cache, dotenv discovery, logging setup, or I/O is performed on import.
Database and source settings will be added with their consuming features.

Current runtime dependencies are Pydantic, pydantic-settings, SQLAlchemy,
Alembic, and pg8000 (pure Python). Alembic is runtime tooling because installed distributions expose offline
migration generation. pytest, pytest-cov, and Ruff are development tools.
FastAPI/Uvicorn serve the local read-only API; httpx2 is development-only ASGI test tooling. Driver imports are deferred until
explicit database access. pg8000 avoids the native Psycopg component rejected by
Windows Application Control. Integration tests allow only their explicit test
database port; default unit tests still block all network access.
HTTP retrieval uses the standard library; no third-party HTTP client is needed.
uv manages `.venv` and the resolved `uv.lock`. Development tools are a dependency
group, not runtime requirements or a published extra. setuptools 84.2.0 is both
the declared build backend and a locked development dependency. Offline builds
use that backend in `.venv`; source and wheel installations are checked in fresh
environments without system-site packages. pytest does not inject `src` into
its import path. See `development.md` for commands and validation limits.

## Validation and observability

Current configuration, domain, normalization, planning, and HTTP client tests run offline with a socket/DNS guard and
isolated application environment variables. Synthetic domain fixtures are in
`tests/unit/conftest.py`; source JSON fixtures are in `tests/fixtures/open_meteo/`.
No fixture was fetched from a provider. Transport tests inject HTTP responses;
future retry tests will inject clocks and delays. Offline component integration
tests live in `tests/unit/test_source_pipeline.py` and run in the default suite.
Database integration tests require real, disposable PostgreSQL and explicit
opt-in; SQLite cannot validate PostgreSQL semantics.

Future structured logs should include run/chunk IDs, city, requested interval,
attempt, outcome, row counts, and elapsed time. Do not log credentials, full
connection strings, or unrestricted provider payloads. Track inserted, updated,
unchanged, incomplete, and rejected records distinctly.

Before portfolio performance claims, document hardware, software versions,
dataset size, concurrency, warm-up, request counts, and p50/p95 latency. Separate
upstream waiting from normalization and database timings. No throughput, latency,
uptime, or production-readiness result has been established here. Configuration
and domain/source coverage are reported separately by milestone in `development.md`
and do not establish live-provider or database behaviour.

## Deferred decisions

- A reproducible synthetic CLI-to-API demonstration and intentional production configuration.
- Raw-payload retention, automatic interruption detection and retry policy.
- Public demo service eligibility and repository code licence.
- CI/CD, scheduling, cloud hosting, and additional sources; disposable test containers already exist.

## Milestone 3C.1 operational boundary

`ingestion.coordinator` plans and processes one chunk at a time. It reuses the
normalizer and `ChunkWrite` validation, and calls the existing storage writer
inside a transaction owned by `storage.run_tracking.RunSession`. Registration,
status reads, failure recording and deliberate resume are separate short
transactions. A session advisory lock owns one run across these transactions;
there is no open transaction during retrieval. Failed connections cannot silently
reconnect under an expired ownership lock. Different runs retain the existing
city/version write serialization.

The source pipeline, canonical models, schema and frozen initial migration are
unchanged. Shared private persistence helpers now accept a run plan without
requiring weather retrieval, and success updates preserve lifecycle provenance.
No general repository framework, new dependency, scheduler or concurrent worker
is introduced. The synthetic-only CLI is `python -m hellas_dataops.ingestion`.
See [status, resume and limits](ingestion.md) for the exact contract.
