# Read-only weather API

Milestone 3D.1 is **ACCEPTED** after 916 offline tests and 133 real PostgreSQL
tests (including 24 API cases). Milestone 3D.2 subsequently verified the real
local Uvicorn demonstration; see [the actual evidence](demo.md#final-acceptance-fresh-host-demonstration). No schema, migration, canonical domain,
ingestion or transactional writer changes are required.

## Endpoints

| Method/path | Success | Errors |
| --- | --- | --- |
| GET `/health/live` | `{"status":"alive"}`; no database or upstream access | Process must be running |
| GET `/health/ready` | `{"status":"ready"}` after database and schema checks | 503 unavailable/incompatible/unconfigured |
| GET `/v1/cities` | `supported` registry points and separate `persisted` configurations | 503 database failure |
| GET `/v1/weather` | Current canonical observations and `next_after` | 422 invalid input, 404 unknown/unpersisted identity, 503 database failure |

OpenAPI is at `/openapi.json`; Swagger UI is at `/docs` and ReDoc at `/redoc`.
Interactive documentation uses the framework's external CDN assets; JSON OpenAPI
and API requests themselves work locally. No weather source is contacted.
Only GET business endpoints exist; unsupported write methods return 405.
No 400 business response is defined: malformed query parameters consistently use
422. Validation errors do not echo submitted values. Database error bodies are
fixed safe text and never include connection URLs or driver diagnostics.

## Query parameters

All six identity/interval parameters are required:

| Parameter | Meaning |
| --- | --- |
| `city_id` | e.g. `athens`; unknown valid slug returns 404 |
| `coordinate_version` | e.g. `gr-cities-v1`; must exist in storage |
| `provider_id` | e.g. `open-meteo` |
| `dataset_id` | e.g. `era5_hourly-v1`; provider-qualified persisted identity |
| `start` | Inclusive UTC hour, e.g. `2025-01-01T00:00:00Z` |
| `end` | Exclusive UTC hour; greater than start, at most 31 days later |
| `limit` | Optional integer, default 100, minimum 1, maximum 500 |
| `after` | Optional exclusive UTC hour within `[start,end)` |

Identifiers are lowercase ASCII slugs of at most 64 characters. Unknown query
parameters fail validation. Timestamps must be ISO 8601 datetime strings, with
zero UTC offset (`Z` or encoded `+00:00`); naive timestamps, epoch numbers,
nonzero offsets and non-hour boundaries are rejected. URI-encode a literal `+`.
An interval of one hour is valid. Query windows can span UTC daylight-saving,
leap-day and year boundaries without changing their meaning.

City listing is sorted by city/version. `supported` means configurations currently
accepted by the source planner. `persisted` means configurations in PostgreSQL,
which can include previous versions and does not imply that weather rows exist.
Queries resolve stored versions explicitly. If city configuration and dataset
exist independently but no rows match their combination, return 200 with an
empty list, not 404. A missing configuration/dataset returns 404. No gaps are
filled, and an empty result does not trigger ingestion.

## Response and pagination

Example response for an invented one-hour stored observation:

```json
{
  "city": {"city_id":"athens","coordinate_version":"gr-cities-v1","latitude":37.9838,"longitude":23.7275},
  "dataset": {"provider_id":"open-meteo","dataset_id":"era5_hourly-v1","historical_model":"era5","source_contract_version":"open-meteo-request-v1"},
  "units": {"temperature_c":"celsius","relative_humidity_pct":"percent","precipitation_mm":"mm","wind_speed_ms":"m/s"},
  "start":"2025-01-01T00:00:00Z",
  "end":"2025-01-01T01:00:00Z",
  "observations":[{"timestamp_utc":"2025-01-01T00:00:00Z","temperature_c":10.0,"relative_humidity_pct":null,"precipitation_mm":null,"wind_speed_ms":null}],
  "next_after":null
}
```

The actual source-contract string is read from the stored dataset. Requested city
coordinates are not returned grid coordinates; the API does not expose raw grid
or correction provenance. A model label records configured identity, not proof
that a provider supplied that model. Demo measurements are synthetic.

Rows are ordered by timestamp. Keep the identity, start and end unchanged and
pass `next_after` as `after` to retrieve the next page. `null` means no further row
was visible in this request. The cursor is an explicit timestamp, not an opaque,
signed token; changing other parameters intentionally starts a different query.
The service fetches at most `limit + 1` rows and never materializes a whole year
or computes a total count. The existing `(city_config_id,dataset_key,timestamp_utc)`
primary-key index supports the range and cursor; no new index is added.

Each request uses a REPEATABLE READ snapshot, but separate pages do not share a
snapshot. Corrections can change subsequent pages, and earlier late inserts may
be missed. Existing timestamps are neither duplicated nor revisited during a
forward traversal. Restart on a quiescent dataset for consistent export; no
snapshot/export service is implemented. Committed chunks are queryable even
when their overall ingestion run is incomplete. NULL rows and absent hours are
different and remain distinguishable.

## Connections, readiness and security

`create_app(target=None)` performs no I/O. Missing configuration permits liveness
but database routes return 503. The CLI requires an explicit ignored `target.json`
created by the host runner. It supports only the existing disposable PostgreSQL
18 target, exact localhost port, test database/user and independent ownership
marker; arbitrary URLs and environment-based fallback are not accepted.

`ReadQueries` owns one verified NullPool connection per operation. The data
transaction is `READ ONLY, REPEATABLE READ`; it ends and closes on success or
failure. SQLAlchemy binds all client-derived values. The existing connection has
a five-second socket timeout; reads additionally set a five-second per-statement
timeout and one-second lock timeout. These are **not a whole-request deadline**.
Readiness checks the exact Alembic revision, expected columns/types/nullability
and zero-row SELECT access to all five tables. It is not a full catalog constraint
audit or a guarantee about future connectivity. Database tests remain responsible
for constraints and migration correctness.

The launcher binds only `127.0.0.1`, disables access logging and has no host/live
option. No authentication, authorization, rate limiting, TLS, production database
role or public deployment is provided. The disposable account still has write
privileges for tests; read-only transactions protect this API path, not arbitrary
code using those credentials. A future hosted API needs a dedicated read-only
role and an explicit security/configuration review. City catalog responses assume
a small configuration registry; weather responses are strictly bounded.

## Windows synthetic demonstration

From the repository root, run the guarded host verification in ordinary Windows
PowerShell with Docker Desktop running:

```powershell
& .\scripts\verify-postgres.ps1
```

After it passes, use **that run's** reported prefix to select its retained, empty
disposable database. Do not guess an existing personal database or print secrets.
Replace `<reported-project>` below with the report's exact project identifier:

```powershell
$testConfig = '.tools/postgres/<reported-project>/target.json'
$runId = [guid]::NewGuid().ToString()
& .\.venv\Scripts\python.exe -I -B -m hellas_dataops.ingestion --database-config $testConfig start --run-id $runId --city athens --start '2025-01-01T00:00:00Z' --end '2025-01-08T00:00:00Z'
if ($LASTEXITCODE -ne 0) { throw 'Synthetic ingestion failed' }
& .\.venv\Scripts\python.exe -I -B -m hellas_dataops.api --database-config $testConfig --port 8000
```

In a second ordinary PowerShell window:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/health/live'
Invoke-RestMethod 'http://127.0.0.1:8000/health/ready'
Invoke-RestMethod 'http://127.0.0.1:8000/v1/cities'
$query = 'http://127.0.0.1:8000/v1/weather?city_id=athens&coordinate_version=gr-cities-v1&provider_id=open-meteo&dataset_id=era5_hourly-v1&start=2025-01-01T00:00:00Z&end=2025-01-08T00:00:00Z&limit=100'
$page = Invoke-RestMethod $query
$page
if ($null -ne $page.next_after) {
    Invoke-RestMethod ($query + '&after=' + [uri]::EscapeDataString($page.next_after))
}
# Open http://127.0.0.1:8000/docs in a browser, or inspect:
Invoke-RestMethod 'http://127.0.0.1:8000/openapi.json'
```

Ctrl-C stops only the local API. Containers/volumes are not deleted. The full
demo was verified on the Windows host, separately from offline/API unit tests.
The host runner deliberately creates a fresh database each invocation and never
resets a retained demonstration database.

## Tests and limits

Default tests use injected query results and HTTPX2 `ASGITransport`, with no real
HTTP listener or database. Windows asyncio's internal wake-up socket is created
once before per-test guards; all request execution remains under the unchanged
socket/DNS guard. Integration tests permit only the exact verified database port.
No broader loopback exception is added. The app has no lifespan I/O; ASGITransport
does not itself run lifespan hooks (revisit this if hooks are later introduced).

The opt-in API suite uses accepted `persist_chunk` for ordinary records and
additional synthetic SQL fixtures for historical identities that the current
writer intentionally does not create. It tests reads, paging, NULL/UTC output,
identity isolation, read-only enforcement, revision mismatch, identity/authentication
failures and corrections between pages. Ordinary GET cases compare all five
tables before/after. Migration lifecycle and the 109 accepted database regression
tests passed in the same guarded host run, in 41.34 seconds. See
[verified acceptance and limitations](development.md#milestone-3d1-final-acceptance).

Upstream reference: [FastAPI in-process testing](https://fastapi.tiangolo.com/advanced/async-tests/)
and [SQLAlchemy PostgreSQL transactions](https://docs.sqlalchemy.org/en/21/dialects/postgresql.html).
The installed Starlette 1.9.0 implementation selects httpx2; no deprecated httpx
fallback or new ingestion HTTP client is used.
