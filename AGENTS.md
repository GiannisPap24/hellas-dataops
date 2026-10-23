# Hellas DataOps engineering instructions

## Project and boundaries

Hellas DataOps is an independent public portfolio project: a Python modular
monolith for reliable historical weather ingestion, PostgreSQL storage, and a
FastAPI query interface. Work exclusively in this repository. Do not inspect,
copy, or modify other projects. Use English for code and documentation.

Milestone 3D.1 is **ACCEPTED** after 133 host PostgreSQL tests and 916 offline
tests. Its previous checkpoint is `9d4c4b037eaa13e2fff42cce77a1fcf0505e9de1`.
Current authorized scope is acceptance documentation and preparation of a guarded,
owner-run local checkpoint script. Do not write Git metadata from the sandbox,
push, perform live weather requests, or begin another milestone.
API handlers must only read stored data using bounded,
parameterized Core queries and the verified disposable configuration. Keep
liveness independent of PostgreSQL and sanitize readiness/query failures.
Keep source retrieval outside database transactions. Operational commands must
remain synthetic-only and use the verified disposable target. Preserve the
initial migration and existing domain/source interfaces. Run receipts and weather
must commit together; ambiguous outcomes require durable reconciliation, never
an assumption that an exception proves rollback. See docs/ingestion.md.
Configuration, immutable domain objects, source normalization, versioned city
coordinates, request planning, and the synchronous HTTP client are implemented.
The application workflow joins these components, validates full retrieval chunks,
then retains and validates requested hours. SQLAlchemy Core tables and an initial
offline Alembic migration define the storage contract. Guarded execution against
new disposable test databases is authorized. Persistence uses SQLAlchemy Core and
pg8000 without changing the accepted schema. Live weather calls and write API endpoints
remain outside scope. See the final 3B.2 acceptance record in docs/development.md
for demonstrated guarantees, coverage gaps and operational limitations.
Obtain explicit approval before starting the next milestone. User instructions
determine the active scope; update this section when it changes.

Default tests and SQL generation remain offline. The owner authorized project-local
pg8000 installation and host-assisted official PostgreSQL 18 test containers.
The sandbox cannot access Docker's named pipe; do not retry escalation or change
permissions. Native Psycopg was rejected by Application Control and is replaced
by the approved pure-Python pg8000 driver. Never bypass security policy or claim
database validation succeeded before the real host tests pass.
SQLAlchemy 2.1.6 must use its supported pure-Python source build. The project uv
policy selects no binary SQLAlchemy wheel and supplies DISABLE_SQLALCHEMY_CEXT=1
at build time. Do not patch installed packages, remove individual native files,
or change Application Control. Run scripts/verify_sqlalchemy.py with Python -I
to verify the actual installed build before database operations. The host runner
must remain importable without SQLAlchemy so dependency failures are reported
before any Docker operation. This compatibility remediation does not authorize
application/schema changes or the next milestone.

Use project-local uv and
the locked `.venv`. Keep tooling,
caches, and temporary build environments inside this repository. Do not install or switch
Python versions. Do not change global Python configuration, start services,
pull container images, create remote repositories, commit, push, publish, or
deploy without separate authorization. Do not delete existing files without
authorization. Use `.tools/uv/bin/uv.exe` explicitly; its verified version is
0.13.4. A different uv installation in another shell is not the project tool.

## Architecture

- Keep one package under `src/hellas_dataops/` and a modular monolith.
- Keep domain rules independent of HTTP, database access, and FastAPI.
- Source adapters own provider-specific parsing and HTTP behaviour. Ingestion
  coordinates work; storage owns SQL and transactions; API routes stay thin.
- Do not perform configuration loading, networking, database connections, file
  writes, or service startup at import time.
- Domain models use frozen standard-library dataclasses. Keep validation pure,
  preserve null measurements, and do not mutate caller-owned observation lists.
- Batch validation requires an explicit city, provider-qualified dataset, and
  nonempty UTC interval. Accept unsorted input, return an ordered immutable tuple,
  and reject duplicates, missing hours, and unexpected hours. Complete hourly
  coverage does not imply complete measurements.
- Introduce dependencies and abstractions only for an implemented need. Explain
  why each addition is necessary. Avoid generic plugin frameworks and wrappers
  without a concrete use case.
- No scheduling, cloud deployment, Kubernetes, Spark, or custom frontend in the
  current scope.

## Data and reliability contracts

Treat `docs/data-contract.md` as the agreed contract. Discuss and document
changes to semantics before changing behaviour.

- Select ERA5 explicitly; use UTC-aware timestamps and explicit units.
- Preserve missing measurements as null, never zero. Reject malformed data;
  report incomplete data honestly.
- Preserve source, request, grid-location, retrieval, and transformation
  provenance. Do not describe reanalysis as station observations.
- Keep normalization separate from transport. Validate declared units and UTC
  metadata; preserve returned grid coordinates without comparing them to the
  requested city point. Dataset identity is caller-supplied; the response alone
  does not prove that ERA5 was requested or delivered.
- Keep original requested hours, complete-day retrieval chunks, and retained
  hourly subsets distinct. Retrieval chunks cover at most 14 UTC calendar days.
  The source pipeline must normalize against the full retrieval interval before
  filtering and validating retained hours; do not weaken strict coverage checks.
- Require explicit client injection at the application workflow boundary; tests
  configure the existing HTTP client with synthetic transports. Return complete
  validated results only, keeping request intent separate from returned metadata.
  Preserve contextual chunk/stage failures and do not return partial success.
- Coordinate changes require an explicit reviewed version and compatible dataset
  semantics. Do not silently replace registry points or substitute grid locations.
- Future ingestion must support repeatable backfills, bounded retries,
  idempotent writes, transactional chunks, and recovery after interruption.
- Keep network requests outside database transactions. Use database constraints
  as well as application validation when persistence is introduced.
- Use reviewed migrations for future schema changes. Do not silently recreate
  databases or substitute SQLite for PostgreSQL integration tests.
- Define storage using SQLAlchemy Core, without replacing domain dataclasses with
  ORM models. Freeze migration operations independently of current metadata.
- Keep offline migration generation connection-free. Online migrations must use
  explicit disposable configuration, verify the database ownership marker, and
  refuse unknown relations or populated application tables. Never accept an
  arbitrary URL or fall back to SQLite.
- The host runner creates a new uniquely named Compose project each time. Do not
  reset existing volumes, perform global cleanup, or inspect/modify other projects.
- Keep real PostgreSQL tests opt-in under `tests/integration`; report their results
  separately from offline unit tests. The accepted 3B.1 host run passed 54 cases;
  future results must be reported from their own execution evidence.
- Treat coordinate and dataset configurations as immutable identities; future
  writers must reject conflicting redefinitions and preserve chunk lineage.
- Offline SQL compilation and metadata tests do not prove database enforcement,
  rollback, concurrency, or transactional correctness.

## Testing and quality

- Default tests must be deterministic, offline, and independent of credentials,
  Docker, PostgreSQL, or live upstream services.
- Use clearly labelled synthetic fixtures. Mock external services and control
  time, retry delays, and randomness in future ingestion tests.
- Unit tests block socket connections and DNS resolution in `tests/conftest.py`.
  Do not remove that protection just to make a test pass.
- Only the opt-in integration subtree overrides that fixture, allowing the exact
  validated numeric localhost database port and rejecting other destinations/DNS.
- Future integration tests must be explicitly selected and use a disposable
  PostgreSQL database with safeguards against non-test targets.
- Type public interfaces. Prefer focused modules, clear errors, and explicit
  behaviour over clever abstractions.
- Test meaningful contracts and failure paths. Do not pad coverage with tests
  that merely repeat implementation details.
- Synchronize with `uv sync --locked`, never silently update the lock during
  routine setup. Deliberate dependency changes require resolving and reviewing
  `uv.lock`. Never manually fabricate lockfile entries.
- Run tests with the installed package in `.venv`, without pytest `pythonpath`
  injection. Set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; explicitly load
  `pytest_cov` when measuring coverage. Preserve the network guard.
- Run Ruff lint, Ruff format checks, and coverage as documented in README.
- Build with the locked backend in `.venv`, then verify distributions in fresh
  isolated environments. Use Python `-I` for import checks to exclude PYTHONPATH
  and user-site leakage. Do not equate an editable import with wheel validation.
- Never invent a lockfile, benchmark, test result, CI status, or reproducibility
  claim. Report exact commands, results, and limitations.

## Security and public presentation

- Never commit credentials, personal information, `.env` files, local databases,
  bulk data, logs, or generated artifacts. `.env.example` contains safe examples
  only and must remain free of real credentials.
- Never log secrets or full connection strings. Configuration errors must not
  expose raw settings inputs.
- A public ingestion endpoint is not part of the MVP. Future query endpoints
  need bounded result sets, input validation, and parameterized queries.
- Distinguish Open-Meteo service usage terms from the weather data licence.
  Preserve attribution and document transformations in distributed data.
- Keep README status and milestone documentation accurate. Planned features
  must never be presented as implemented.
- Stop at the approved milestone and report changes, checks, blockers, and the
  proposed next step.
