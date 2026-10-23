# Hellas DataOps

A Python data engineering portfolio project for reliable historical weather
workflows across selected Greek cities.

The intended platform will ingest Open-Meteo ERA5 data, validate and normalize it,
store it in PostgreSQL, and expose bounded, documented queries through FastAPI.
Its engineering focus is idempotency, backfills, provenance, failure recovery,
deterministic testing, and measurable performance.

## Current status

**Milestone 3D.2 ACCEPTED: a fresh synthetic end-to-end demonstration and 972 offline tests pass.**
The previously accepted PostgreSQL integration suite has 133 passing tests; it was
not rerun during this acceptance review. See the
[verified demonstration evidence](docs/development.md#milestone-3d2-final-acceptance).
See [API contracts and local demonstration](docs/api.md).
Milestone 3D.2 adds a one-command **synthetic end-to-end demonstration**, verified
on Windows with PostgreSQL 18.6. It runs the existing ingestion CLI, verifies 168 persisted
hours and unchanged replay, serves the actual FastAPI app with Uvicorn, checks real
HTTP responses and measures local latency. Fresh results and limitations are recorded
in the runbook; all source measurements are synthetic.

From ordinary Windows PowerShell with Docker Desktop running:

```powershell
& .\scripts\demo.ps1
```

See the concise [demonstration runbook](docs/demo.md) for locked setup, expected
results, generated reports, API requests and benchmark limitations. The script
creates a fresh isolated database, stops only its API child and retains Docker
resources. It never calls Open-Meteo. All demonstration values are invented.
Transactional chunk persistence, replay, correction and tested writer contention
are verified on PostgreSQL 18.6. Milestone 3C.1 adds a synthetic-only sequential
ingestion command and explicit resume, verified against PostgreSQL 18.6.
This is not a live weather platform. See the [operational guide](docs/ingestion.md),
[persistence contract](docs/persistence.md) and
[acceptance evidence](docs/development.md#milestone-3c1-final-acceptance).

Implemented:

- A `src/` Python package with explicit module boundaries.
- Validated environment and log-level settings, loaded on demand.
- Offline configuration unit tests with a network guard.
- Immutable canonical weather objects and pure hourly coverage validation.
- Deterministic synthetic domain tests, including nulls and leap-day boundaries.
- Pure normalization of synthetic Open-Meteo JSON into canonical observations,
  with explicit units, UTC coverage checks, and separate grid metadata.
- Versioned city coordinates and deterministic ERA5 request specifications with
  bounded complete-day chunks, partial-day retention boundaries, and canonical
  JSON request provenance.
- A synchronous standard-library HTTPS client with injected test transport,
  bounded streamed responses, strict JSON decoding, and typed retrieval errors.
- An in-memory source workflow combining planning, retrieval, complete-response
  normalization, hourly retention, final coverage validation, and chunk provenance.
- Five SQLAlchemy Core tables with explicit identity, lineage, UTC, and measurement
  constraints; a frozen initial Alembic revision with offline SQL generation.
- A guarded host-assisted PostgreSQL test workflow, isolated Compose configuration,
  and opt-in database tests, verified on PostgreSQL 18.6 with pg8000 1.31.5.
- Architecture, data, safety, and contribution instructions.
- Explicit domain/storage mapping, atomic chunk writes, unchanged replay, and
  opt-in corrections with prior-value lineage, verified against PostgreSQL.
- Read-only liveness/readiness, city catalogs and bounded hourly queries with keyset pagination.
- A uv-managed local environment and resolved dependency lockfile.
- Verified linting, formatting, coverage, and source/wheel packaging on Windows.

Implemented and verified in 3C.1: sequential chunk ingestion,
durable status/counts, and explicit resume through a synthetic-only Python CLI.

Not yet implemented: live operational ingestion, automated
retries/recovery, write endpoints, logging configuration, orchestration,
CI/CD, dashboards, or deployment. No live weather data has been fetched.

## Intended data scope

Start with Athens from **2025-01-01 00:00 UTC to 2025-01-08 00:00 UTC exclusive**.
Expand to Athens, Thessaloniki, Patras, Heraklion, and Larissa for calendar year
2025. The full target contains 43,800 hourly city records if coverage is complete.

Use ERA5 explicitly and retain temperature, relative humidity, precipitation,
and wind speed in fixed units. These are historical reanalysis estimates for
configured coordinates, not direct city-station measurements.

See [the data contract](docs/data-contract.md) for exact boundaries, units,
missing-data rules, and provenance requirements.

## Architecture

One modular monolith shares domain and storage services between the synthetic CLI
and read-only API entry points. The coordinator retrieves injected synthetic data,
validates and persists one chunk at a time; the API queries stored data without calling
Open-Meteo on each request.

| Module | Responsibility | Current state |
| --- | --- | --- |
| `config` | Validated settings | Implemented |
| `domain` | Immutable weather objects and pure validation | Implemented |
| `sources` | Request planning, HTTP retrieval, response parsing | ERA5 planning, bounded HTTP client, pure normalization; verified offline |
| `ingestion` | Application workflow and explicit lifecycle | Sequential synthetic coordinator and start/status/resume CLI; accepted after host verification |
| `storage` | PostgreSQL schema, mapping and transactions | Accepted schema/migrations and transactional chunk persistence |
| `api/routes` | Read-only FastAPI endpoints | Accepted after real PostgreSQL verification |

See [architecture decisions](docs/architecture.md) and the
[synthetic CLI commands](docs/ingestion.md#local-commands). Observability beyond
safe errors and persisted status remains planned.

## Canonical domain

`hellas_dataops.domain` exports `DatasetIdentity`, `HourlyInterval`,
`HourlyObservation`, `validate_batch`, and `DomainValidationError`. Models use
frozen, slotted standard-library dataclasses; domain code has no framework,
network, configuration, or database dependency.

Constructors validate identities, UTC hour alignment, finite numeric values,
and physical bounds. `validate_batch` requires the requested city, dataset, and
half-open interval. It accepts unsorted sequences and returns a sorted tuple
without mutating input. Duplicates, missing hours, and unexpected hours fail the
whole batch. All-null observations still cover their hour; null is never zero.

Dataset/provider identifiers are validated syntactically, not looked up in a
provider registry. These objects identify a series; they do not yet implement
full ingestion provenance. See [implemented contracts and error codes](docs/data-contract.md#implemented-domain-api).

## Offline source normalization

`hellas_dataops.sources.open_meteo.normalize_open_meteo(payload, *, city_id,
dataset, interval)` accepts an already decoded single-location response plus
explicit domain request context. It returns frozen `NormalizedWeather` containing
sorted canonical observations and `OpenMeteoMetadata`. Required arrays and units
are checked before row construction; domain rules validate values and coverage.
Nulls remain null. Returned grid coordinates never replace requested city points.

The 146 normalization tests remain unchanged. Milestone 2C's 414-test suite is
retained alongside the new client tests. The normalizer retains 100% statement and branch coverage.
This is evidence from synthetic
inputs, not verification against the live service. The response cannot establish
which historical model was selected. See [the response contract](docs/data-contract.md#implemented-open-meteo-normalization)
and [verification results](docs/milestone-2c.md).

## Offline request planning

```python
from datetime import UTC, datetime

from hellas_dataops.domain import HourlyInterval
from hellas_dataops.sources.cities import CITY_REGISTRY
from hellas_dataops.sources.open_meteo_requests import (
    ERA5_DATASET,
    plan_open_meteo_requests,
)

requests = plan_open_meteo_requests(
    CITY_REGISTRY["athens"],
    dataset=ERA5_DATASET,
    interval=HourlyInterval(
        start=datetime(2025, 1, 1, 5, tzinfo=UTC),
        end=datetime(2025, 1, 16, 3, tzinfo=UTC),
    ),
)
request_url = requests[0].url  # A string only; no HTTP request.
provenance_json = requests[0].to_json()
```

Each frozen specification preserves the original interval, a complete-day
retrieval interval, and the retained hourly subset. The example produces two
requests covering January 1-14 and January 15-16, retaining 358 hours overall.
The planner accepts only the registered `gr-cities-v1` coordinates and
`open-meteo/era5_hourly-v1` dataset. It explicitly selects units, ERA5, GMT,
Unix seconds, land cells, native grid elevation, and JSON output.

The source pipeline normalizes each complete response using `retrieval_interval`,
then retains only `retain_interval` and validates the subset and assembled result.
The separate HTTP client below accepts these specifications.
See [planning contracts](docs/data-contract.md#implemented-request-planning)
and [Milestone 2C evidence and packaging commands](docs/milestone-2c.md).

## HTTP client (offline verification only)

`hellas_dataops.sources.open_meteo_client.fetch_open_meteo(request, *, transport=None,
timeout_seconds=10.0, max_response_bytes=2097152)` fetches one planned chunk and
returns a decoded JSON object. Tests supply a context-managed streaming transport;
omitting it selects the standard-library HTTPS adapter. Calling that default
adapter would access the live service and requires separate approval for this
project. Imports and request planning do not open connections.

The adapter uses certificate-verified HTTPS to the fixed archive endpoint,
requests uncompressed JSON, and makes one attempt without redirects or retries.
Non-200 statuses (including 429) raise `OpenMeteoStatusError`; safe `Retry-After`
metadata is exposed for a future retry policy. Timeouts and transport failures
have separate exception types. Oversized, truncated, compressed, or malformed
responses fail without returning partial data or echoing upstream diagnostics.

The default body limit is 2 MiB, configurable up to 16 MiB; reads request at most
64 KiB and consume at most one excess byte to detect overflow. Strict UTF-8 JSON
rejects duplicate keys, nonfinite numbers, and nesting beyond 64 containers.
The 10-second timeout is per blocking socket operation, **not a total deadline**;
system DNS resolution is not bounded by it. Decoded Python memory can exceed the
body-byte limit. No live compatibility, availability, or performance claim is made.

The client does not normalize, filter, persist, or schedule observations.
See [the retrieval contract](docs/data-contract.md#implemented-http-retrieval)
and [Milestone 2D verification](docs/milestone-2d.md).

## Integrated source pipeline

`hellas_dataops.ingestion.source_pipeline.collect_open_meteo(city, *, dataset,
interval, fetch)` requires an explicitly supplied fetch callable. Configure the
existing client with `functools.partial(fetch_open_meteo, transport=synthetic_transport)`
for offline use; the workflow has no implicit live client. Its frozen result
contains ordered observations and one request/returned-metadata pair per chunk.
It returns only after complete coverage validation; failures stop processing
and identify the stage and chunk without returning earlier successful rows.

All 511 existing tests and 55 new component integration cases pass. Tests use
synthetic byte streams with the real client, normalizer, planner, and validators.
The socket/DNS guard remains enabled. Live service compatibility, automatic
retries and total-request deadlines remain unverified or unimplemented. The
separate storage service now supplies accepted chunk persistence; operational
integration is now verified by the 3C.1 host run. See [the pipeline contract](docs/data-contract.md#implemented-source-pipeline)
and [Milestone 2E checks](docs/development.md#milestone-2e-offline-source-pipeline).

## Development environment

Verified on **Windows x64, CPython 3.14.4, and uv 0.13.4**. `.python-version`
records that interpreter; no other Python version or operating system has been
tested. No Python download or global package installation is required.

Milestones 2A-2C rechecked `.tools/uv/bin/uv.exe`: it reports **0.13.4**. No uv
executable was on this execution session's PATH. An earlier report of 0.13.3 in
another PowerShell session cannot be confirmed here; commands below explicitly
select the verified project-local executable and do not change other installs.

Runtime dependencies are Pydantic, pydantic-settings, SQLAlchemy, Alembic, and the
pure-Python PostgreSQL driver pg8000, FastAPI and Uvicorn. The separate `dev`
dependency group contains httpx2 for in-process ASGI tests, pytest, pytest-cov, Ruff, and the pinned setuptools
build backend. `uv.lock` records resolved versions and package hashes. Keep the
lockfile under version control. The HTTP client uses the standard library;
the API never invokes it. httpx2 is development-only, not an ingestion client. pg8000 replaces
Psycopg after Windows Application Control rejected its native library. Driver
imports and compatibility checks pass on Python 3.14.4. The host workflow passed
54 tests on PostgreSQL 18.6 without security-policy changes. See the
[acceptance evidence](docs/development.md#milestone-3b1-final-acceptance).

SQLAlchemy also uses its officially supported **pure-Python source build** at
version **2.1.6**, because Windows Application Control rejected its optional
Cython extension. `pyproject.toml` makes this the project-wide uv policy; it does
not change dependency versions or security settings. See the
[compatibility remediation](docs/development.md#sqlalchemy-pure-python-remediation)
for evidence and source-build/cache requirements. The flag is a build setting;
setting it only at runtime does not fix an installed native wheel.

### Windows setup and installation

Run from the repository root in PowerShell, using an already installed Python
3.14.4. These settings affect only this shell session. Activation and execution
policy changes are unnecessary. If `python --version` reports another version,
stop and select an already approved interpreter explicitly.

```powershell
python --version
$basePython = (Get-Command python).Source
New-Item -ItemType Directory -Force -Path .tools/tmp | Out-Null
$env:TEMP = Join-Path $PWD '.tools/tmp'
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_LINK_MODE = 'copy'
```

Bootstrap uv locally **once**, if `.tools/uv/bin/uv.exe` does not exist. The
initial resolution selected uv 0.13.4; the repeatable bootstrap pins that version:

```powershell
& $basePython -I -B -m pip --isolated --disable-pip-version-check install --index-url https://pypi.org/simple --only-binary=:all: --no-deps --no-cache-dir --retries 0 --timeout 15 --target .tools/uv uv==0.13.4
```

Install the project and development tools into `.venv` from the existing lock:

```powershell
& ./.tools/uv/bin/uv.exe --version
& ./.tools/uv/bin/uv.exe sync --locked --python $basePython --no-managed-python --default-index https://pypi.org/simple
& ./.venv/Scripts/python.exe -I -B scripts/verify_sqlalchemy.py
& ./.venv/Scripts/python.exe -I -B -c "import hellas_dataops; print('Installed package import OK')"
```

Stop if a command fails. `--locked` rejects stale dependency metadata rather
than silently updating the lock. Initial setup needs PyPI access. Once the local
cache is populated, this verification also works offline:

```powershell
& ./.tools/uv/bin/uv.exe lock --check --offline
$lockBefore = (Get-FileHash uv.lock -Algorithm SHA256).Hash
& ./.tools/uv/bin/uv.exe sync --locked --offline
if ($LASTEXITCODE -ne 0) { throw 'Locked sync failed' }
if ($lockBefore -ne (Get-FileHash uv.lock -Algorithm SHA256).Hash) { throw 'Lockfile changed' }
```

To replace an older compiled SQLAlchemy installation once (project root, same
local cache/temp settings), run:

```powershell
& ./.tools/uv/bin/uv.exe sync --locked --no-binary-package sqlalchemy --reinstall-package sqlalchemy --python .venv/Scripts/python.exe
if ($LASTEXITCODE -ne 0) { throw 'Compatible dependency installation failed' }
& ./.venv/Scripts/python.exe -I -B scripts/verify_sqlalchemy.py
if ($LASTEXITCODE -ne 0) { throw 'Pure-Python SQLAlchemy verification failed' }
```

This downloads/builds only as needed from the configured standard package index.
Offline operation requires the source archive, build requirements and compatible
built wheel already cached. Ordinary future `uv sync --locked` uses the same
project policy. The PostgreSQL runner forces an **offline** SQLAlchemy reinstall
and verifies it before importing storage or contacting Docker. It stops safely
if the required cache is missing. Generic pip installation outside this project
does not inherit uv settings; follow the documented project setup.

Only a deliberate dependency update should run `uv lock` without `--check`.
The original lock was generated with `uv lock --python $basePython
--no-managed-python --default-index https://pypi.org/simple`, then regenerated
after pinning setuptools to its resolved version. It was not handwritten.

### Offline tests and quality checks

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider -p pytest_cov --cov=src/hellas_dataops --cov-report=term-missing --cov-report=xml:coverage.xml
& ./.venv/Scripts/ruff.exe check .
& ./.venv/Scripts/ruff.exe format --check .
```

pytest uses importlib mode with **no `pythonpath` injection**. Python `-I` ignores
PYTHONPATH and user-site packages. Plugin autoloading is disabled and pytest-cov
is loaded explicitly; the network guard remains enabled. No database, container,
credential, or weather API is needed. Generated coverage reports are ignored.
Coverage uses the source directory so Alembic's dynamically loaded environment
and revision modules are measured alongside ordinary package imports.

### Build and verify distributions

After locked synchronization, build offline using the backend in `.venv`:

```powershell
& ./.tools/uv/bin/uv.exe build --offline --no-build-isolation --python .venv/Scripts/python.exe --out-dir dist
```

This produces `dist/hellas_dataops-0.1.0.dev0.tar.gz` and
`dist/hellas_dataops-0.1.0.dev0-py3-none-any.whl`; uv builds the wheel from the
source distribution. `--no-build-isolation` here deliberately uses the locked
backend in the already isolated project environment, not global Python packages.
The source distribution includes documentation, tests, the lockfile, and the
installation verification script through `MANIFEST.in`.

Both distributions were installed separately into fresh local environments using
locked runtime dependencies, then checked with `python -I`. See
[the exact distribution verification commands and results](docs/development.md).
These checks validate installation and imports, not byte-for-byte identical
archives or compatibility with untested platforms.
Milestone 1B artifacts are historical. Milestone 2C builds use a separate
`dist/milestone-2c/` directory and verify source assets (including JSON fixtures
and documentation), wheel modules, and fresh isolated installations. The exact
procedure is in [the Milestone 2C record](docs/milestone-2c.md). Milestone 2D uses
`dist/milestone-2d/` and fresh installation checks as recorded in
[the Milestone 2D record](docs/milestone-2d.md). Tests and full
documentation belong in the source distribution; the wheel contains runtime
code and README metadata. Rebuild before distributing any later source changes.
Milestone 3A uses `dist/milestone-3a/`; its wheel also packages the Alembic
environment, frozen revision, and revision template. See the current commands in
[development documentation](docs/development.md#milestone-3a-schema-and-migration-foundation).

## Configuration

The [storage contract](docs/storage-schema.md) documents the five tables,
coordinate versioning, nullable measurements, lineage, and database-test limits.
Generate PostgreSQL SQL without a server or driver:

```powershell
& ./.venv/Scripts/python.exe -I -B -m alembic -c alembic.ini upgrade head --sql
```

This emits SQL only. Generic online CLI commands still fail; online execution is
restricted to the explicit disposable-test API used by the host verification script.
See [development verification](docs/development.md#milestone-3a-schema-and-migration-foundation)
for dependency versions, migration checks, and fresh distribution installations.

For Milestone 3D.1 verification, open ordinary Windows PowerShell in the repository root and run:

```powershell
& .\scripts\verify-postgres.ps1
```

This creates a new isolated PostgreSQL test environment only after preflight,
runs migration lifecycle and constraint tests, and writes a sanitized report under
`artifacts/postgres/`. It never deletes volumes or touches other projects. Read
[PostgreSQL testing](docs/postgres-testing.md) before execution, including driver
policy failures and retained-resource limits. The runner recognizes the accepted 3C.1 checkpoint
`231058887e9e8c3bb20a5c0484679b0276bc47b4` and includes API query tests.
All 133 PostgreSQL tests passed in the accepted host run; see [API verification](docs/api.md).
After the local checkpoint, the frozen runner HEAD gate requires a deliberate
review in the next authorized milestone before another database run.

| Environment variable | Default | Accepted values |
| --- | --- | --- |
| `HELLAS_ENVIRONMENT` | `development` | `development`, `test`, `production` |
| `HELLAS_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

Variable names are case-insensitive; values follow the exact spelling above.
Explicit `Settings(...)` arguments take precedence over environment variables.
Call `load_settings()` at a future application entry point; imports do not load
settings, and settings are not cached. The environment label does not activate
production infrastructure, and the log level does not configure logging yet.

[`.env.example`](.env.example) contains safe examples. Dotenv files are **not
automatically loaded**. Database connection settings and provider credentials
are deliberately absent until their respective features are implemented.
Text validation errors hide raw inputs. Do not log raw validation error payloads
or environment dumps; structured validation errors may still contain inputs.

## Roadmap

| Milestone | Scope | Status |
| --- | --- | --- |
| 1A | Repository foundation, configuration, contracts | Implemented in this scaffold |
| 1B | Isolated environment, dependency lock, packaging and tooling checks | Verified on Windows / Python 3.14.4 |
| 2A | Canonical weather objects, pure validation, synthetic tests | Implemented offline |
| 2B | Pure Open-Meteo payload normalization with synthetic JSON; no HTTP | Implemented offline |
| 2C | Offline request planning and explicit request/dataset provenance; no HTTP | Implemented offline |
| 2D | HTTP client with injected mocked transport, bounded responses, and error tests | Implemented and verified offline; no live calls |
| 2E | In-memory source pipeline, retained-hour filtering, complete coverage, chunk provenance | Implemented and verified offline; no persistence |
| 3A | PostgreSQL Core schema, constraints, initial offline Alembic migration | Implemented and verified offline; no database execution |
| 3B.1 | Disposable PostgreSQL, guarded migrations, real constraint tests | ACCEPTED: 54 PostgreSQL tests and 699 offline tests pass |
| 3B.2 | Transactional idempotent writes and rollback | ACCEPTED: 83 PostgreSQL tests and 750 offline tests pass |
| 3C.1 | Sequential synthetic CLI ingestion, durable status and explicit resume | ACCEPTED: 837 offline tests and 109 PostgreSQL tests pass |
| 3D.1 | Small read-only FastAPI layer, bounded queries, health/readiness and OpenAPI | ACCEPTED: 916 offline and 133 PostgreSQL tests pass |
| 3D.2 | Synthetic CLI-to-PostgreSQL-to-real-HTTP demonstration and bounded measurements | ACCEPTED: fresh host demonstration and 972 offline tests pass |
| 4 | Controlled live ingestion, operational configuration and full backfill | Separately gated; requires approval and service-terms review |

Disposable PostgreSQL already uses Docker Compose. GitHub Actions, scheduling
and a second data source remain later decisions. Kubernetes, Spark, microservices, and a custom frontend
are outside the current scope.

## Source terms and attribution

Open-Meteo's **API service usage terms** and the **weather data licence** are
separate. Free service access is restricted to non-commercial use; the weather
data is distributed under CC BY 4.0 with attribution requirements. A permissive
data licence does not remove service-access restrictions. Eligibility of a
publicly promoted live portfolio demo remains an explicit pre-publication
decision. No live service access is required for this milestone.

Before distributing actual data, include source and licence links and identify
normalization or other modifications. See the [licensing and provenance
contract](docs/data-contract.md#usage-terms-and-data-licence).

The repository's own code licence has not yet been selected. Do not assume that
the weather data licence licenses this code.

## Engineering guidance

Read [AGENTS.md](AGENTS.md) before changes. Keep secrets, personal information,
local databases, and generated data out of the repository. Document implemented
behaviour accurately, and provide evidence for reliability and performance
claims.

Develop in the existing checkout with the [VS Code workspace guide](docs/vscode.md).
The 3D.2 Windows launcher/PID defect has been corrected and real HTTP checks
and the fresh full demonstration pass; see [the runbook](docs/demo.md).
The host runner intentionally pins a reviewed Git checkpoint. After creating the
3D.2 commit, its checkpoint policy needs review before another host run; it will
fail safely at the repository check until then. Release preparation must address
this limitation before claiming setup works from arbitrary public clones.
