# PostgreSQL storage contract

Milestone 3A established the schema and migration through offline checks.
Milestone 3B.1 is now accepted after 54 tests passed against disposable PostgreSQL
18.6: migration lifecycle, catalog agreement, covered constraints, and rollback
were verified. Application persistence and concurrency remain future work.
The initial revision SQL is unchanged. See the
[acceptance record](development.md#milestone-3b1-final-acceptance).

## Tables and identity

| Table | Primary key | Additional identity and relationships |
| --- | --- | --- |
| `city_configs` | Generated bigint `city_config_id` | Unique `(city_id, coordinate_version)`; requested latitude/longitude |
| `datasets` | Generated bigint `dataset_key` | Unique `(provider_id, dataset_id)`; explicit model, source contract version, and units |
| `ingestion_runs` | Application-supplied UUID `run_id` | Foreign keys to city configuration and dataset; original requested UTC interval |
| `ingestion_chunks` | `(run_id, chunk_index)` | Foreign key to run; positive 1-based chunk number; retrieval/retention intervals and provenance |
| `weather_hourly` | `(city_config_id, dataset_key, timestamp_utc)` | Foreign keys to city/dataset, source chunk, and matching run identity |

Surrogate configuration keys keep observation indexes compact. They do not
replace public identifiers. The future mapper resolves the existing canonical
`DatasetIdentity(provider_id, dataset_id)` and the city configuration's
`(city_id, coordinate_version)`
to these keys. No configuration data is seeded in the migration.

The city registry remains authoritative for the five approved cities. A new
coordinate configuration version creates a new `city_configs` row and therefore
a distinct weather series. The future writer must reject reusing an existing
city/version with different coordinates. Dataset model, units, and source contract
version likewise must not be changed in place for an existing provider/dataset
identity; incompatible semantics require a new dataset ID. These are immutable
application contracts, not UPDATE-prevention triggers in this migration.

`dataset_key` is the storage surrogate; `dataset_id` is the domain identifier.
Explicit canonical units are `celsius`, `percent`, `mm`, and `m/s`. These differ
from some provider parameter spellings and display labels. The existing request
provenance retains those provider-specific spellings without conversion.

## Observation and UTC rules

All timestamp columns use PostgreSQL `TIMESTAMP WITH TIME ZONE`. Hourly labels
and interval boundaries must be finite and aligned to the UTC hour. Retrieval
boundaries must be UTC midnight. CHECK expressions use `AT TIME ZONE 'UTC'`
explicitly rather than depending on the connection's session timezone.

PostgreSQL stores instants; it cannot recover whether an input was originally
naive or supplied with a nonzero offset. Future writers must preserve the domain's
UTC-aware input validation before binding parameters. Database output should be
converted to UTC explicitly before constructing domain objects.

The four `DOUBLE PRECISION` measurements are nullable, with no Python/server zero
defaults. A missing row means a missing observation; a row containing four SQL
NULLs still represents its hour. No imputation or unit conversion is introduced.

- Temperature must be at least absolute zero (-273.15 degrees Celsius) and finite;
  there is no arbitrary meteorological upper threshold.
- Humidity is within 0 through 100 percent.
- Precipitation and wind speed are finite and nonnegative.
- Range checks and explicit infinity comparisons are intended to reject PostgreSQL
  floating-point NaN/infinity; real-server enforcement remains a 3B test obligation.

Precipitation retains the provider's preceding-hour accumulation label. No
timestamp shift is made by storage definitions. Coverage remains an application
validation rule; a primary key prevents duplicate keys but does not ensure all
requested hours exist.

## Provenance and operational state

The run identifies one city configuration, dataset, and original half-open
requested interval. UUID and creation time are explicit future application inputs;
there is no database clock, UUID extension, or hidden source identity default.

Each chunk stores the complete-day retrieval interval and retained half-open
subset. Retrieval is nonempty and at most 336 hours; retained hours are nonempty
and contained within retrieval. `request_provenance` is a required JSONB object
holding the planner's canonical request representation: endpoint, parameters,
selected model, versioned coordinates, units, variables, and requested dates.
JSONB preserves semantic content, not original JSON whitespace/key order. Future
mapping must parse the planner's JSON string into an object rather than store it
as a JSON string value.

Returned `grid_latitude`, `grid_longitude`, optional `grid_elevation_m`,
`source_timezone`, `utc_offset_seconds`, and optional `timezone_abbreviation`
are separate columns. Grid coordinates need not equal requested coordinates.
Metadata is either absent before normalization or includes both grid coordinates,
timezone, and zero UTC offset. A succeeded chunk requires metadata and an explicit
`retrieved_at`; neither is synthesized by this schema. Request model selection
records intent, not independent proof that a response contains ERA5 data.

Statuses are limited to `planned`, `running`, `succeeded`, and `failed`; only
status defaults to `planned`. A finished run requires `finished_at >= created_at`
and a terminal status, and conversely terminal status requires a finish time.
No execution, retries, state-transition engine, or recovery policy is implemented.
Separate attempt/event history, response checksums, raw-body retention, and error
payload storage are deferred until they have concrete consumers and privacy rules.

Every weather row references a source `(run_id, chunk_index)`. A second composite
foreign key binds that source run to the weather row's city configuration and
dataset. All foreign keys use PostgreSQL's default NO ACTION semantics; deleting
provenance must not cascade into silent weather deletion. A weather row tracks
one source chunk, not a complete history of every value ever supplied. Replay,
correction, unchanged-row lineage, and audit retention policies need explicit 3B
tests before a writer is introduced.

## Indexes and naming

Each primary key and unique constraint creates its PostgreSQL index; no duplicate
standalone indexes are declared. The weather primary key serves expected
city-configuration/dataset/time-range queries. City/version and provider/dataset
unique indexes support configuration resolution; the chunk primary key supports
ordered per-run lookup.

One additional unique constraint, `uq_ingestion_runs_identity` on
`(run_id, city_config_id, dataset_key)`, is necessary as the target of the composite
weather lineage foreign key. Its wider index is intentional despite `run_id`
already being unique. One explicit secondary index,
`ix_weather_hourly_source_chunk(source_run_id, source_chunk_index)`, supports reverse
lineage lookup. No status, time-only, JSONB GIN, or speculative query indexes exist.

Constraint naming follows `pk_<table>`, `uq_<table>_<columns>`,
`fk_<table>_<first-column>_<target-table>`, and `ck_<table>_<rule>`;
the run identity unique constraint has the explicit name above. PostgreSQL-dialect
compilation handles identifier-length limits deterministically.

## Migrations and remaining enforcement

`storage.schema.metadata` owns current Core table definitions. The initial
`20261022_0001` revision (base revision, no parent) contains explicit frozen Alembic
operations. Tests compare its columns, defaults, identity definitions, keys,
checks, and indexes against current metadata without constructing a database
engine. Changes after a released checkpoint require a new reviewed migration.

The packaged `storage.migration.migration_config(output_buffer=None)` supports
installed distributions without a checkout. Root `alembic.ini` points to the same
packaged scripts. Generic online CLI commands and database URLs fail clearly
without exposing values. `migrate_test_database(target, downgrade=False)` permits
online execution only after typed configuration, server identity/ownership, and
empty-database checks. `upgrade head --sql` and
`downgrade 20261022_0001:base --sql` only emit PostgreSQL SQL. The downgrade drops
weather, chunks, runs, datasets, and cities in dependency order; it is destructive
when executed. The accepted host fixture performed this downgrade on its empty
disposable database, verified the empty Alembic version table, then re-upgraded.

Further database tests must use disposable PostgreSQL, explicit opt-in and target
safeguards. Beyond the covered server constraints and foreign keys,
future transactional code must validate rules that this row-level schema does not:

- Chunk intervals and JSON provenance agree with the parent run and request.
- Each weather timestamp belongs to the referenced retained chunk.
- Completed chunks/runs have exact domain coverage, including null rows.
- Configuration redefinitions are rejected, and updates preserve source identity.
- Idempotent writes, chunk completion, and failure/recovery state commit atomically.
- Conflicting concurrent writes have a deliberate, tested outcome.

No PostgreSQL driver import is required to define metadata or emit offline SQL.
pg8000 1.31.5 is selected for integration testing after Application Control blocked
Psycopg's native library. Driver imports, dialect construction, and the covered
real PostgreSQL behavior pass. The host runner verifies driver loading
before creating Docker resources. See [PostgreSQL testing](postgres-testing.md).
