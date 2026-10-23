# Synthetic end-to-end demonstration

Hellas DataOps validates hourly weather, stores it transactionally in PostgreSQL,
and exposes bounded read-only FastAPI queries. This demo connects the existing
components over a real local HTTP connection. **All weather values are synthetic.**
No Open-Meteo access, public deployment or production-scale performance is claimed.

Status: **Milestone 3D.2 ACCEPTED** after a fresh successful host demonstration
and 972 offline tests. See the final acceptance section for fresh measured results.
The separately accepted PostgreSQL integration suite has 133 passing tests.

## Prerequisites and execution

Use Windows x64, installed Python 3.14.4, project-local uv 0.13.4 and running Docker
Desktop with its Linux engine. Use the locked environment and pure-Python
SQLAlchemy build described in [README setup](../README.md#windows-setup-and-installation).
No administrator, execution-policy or firewall changes are required by the demo.
The initial setup may download packages; subsequent preflight is offline and
requires the source-build cache. PostgreSQL may download the official pinned
`postgres:18.6-bookworm` image. Stop on prerequisite failures rather than bypassing
Windows Application Control. Keep sufficient disk space for retained test volumes.

From the repository root in ordinary PowerShell:

```powershell
& .\.tools\uv\bin\uv.exe sync --locked --python .venv/Scripts/python.exe
if ($LASTEXITCODE -ne 0) { throw 'Locked setup failed' }
& .\.venv\Scripts\python.exe -I -B scripts/verify_sqlalchemy.py
if ($LASTEXITCODE -ne 0) { throw 'Dependency preflight failed' }
& .\scripts\demo.ps1
```

The single demonstration command from any directory is:

```powershell
& 'C:\Users\User\Documents\ChatGPT\hellas-dataops\scripts\demo.ps1'
```

No command changes global settings or execution policy. If `.ps1` execution is
blocked, the equivalent entry point from the project root is
`& .\.venv\Scripts\python.exe -I -B scripts/demo.py`; check `$LASTEXITCODE`.
Do not bypass a policy that also blocks this supported invocation.

## Scenario and acceptance checks

Athens, `gr-cities-v1`, `open-meteo/era5_hourly-v1`, from
`2025-01-01T00:00:00Z` through `2025-01-08T00:00:00Z` exclusive: **168 hours**.
The unchanged synthetic generator provides temperature **10 C** and intentional
null humidity, precipitation and wind speed. These four canonical fields exercise
non-null and null storage/serialization; nulls are never filled with zero. The
ERA5 label is configured dataset identity, not a claim of real provider data.

The guarded workflow:

1. Verify main at `231058887e9e8c3bb20a5c0484679b0276bc47b4`, reviewed paths,
   locked dependencies, pure SQLAlchemy, Docker Linux and resource ownership.
2. Create a new uniquely named PostgreSQL environment using the existing Compose
   file, localhost-only port, PostgreSQL 18 volume layout and ignored credentials.
   Existing containers/volumes are never reset, removed or reused.
3. Upgrade the empty database using the accepted revision `20261022_0001`.
4. Invoke the actual ingestion CLI start/status. Require succeeded run and chunk,
   168 inserted, zero updated/unchanged, complete durable receipts and provenance.
5. Start a second run with identical values: require zero inserts/updates and
   168 unchanged; weather rows and original lineage must be identical. Resume the
   completed first run and verify all persisted state stays unchanged.
6. Run the real FastAPI factory with Uvicorn in a hidden child on an exclusive
   `127.0.0.1` socket. The kernel selects its port; the socket remains held.
7. Check health/readiness, city catalog, weather pages (100 + 68), units, timestamps,
   nulls, identity filtering, exclusive end, empty results, 404 and 422 errors,
   Swagger HTML and OpenAPI JSON. No CDN asset is downloaded by the checks.
8. Measure bounded sequential HTTP latency and verify all five application tables
   are unchanged by the HTTP workload. Stop only the spawned API process.

Any mismatch, child failure or prerequisite failure produces a nonzero exit and
sanitized stage report. There is no misleading partial-success result. On a normal
exit or handled error, child cleanup runs; abrupt termination of the parent/host
is not proven cleanup. Docker resources remain for inspection on every outcome.

## Reports and inspecting JSON

The script prints the exact prefix and writes ignored
`artifacts/demo/<reported-project>/report.json`, `weather-page.json` and
`openapi.json` when those stages succeed. The report records versions, migration,
CLI results, observed row count, real HTTP outcomes, timings and API cleanup.
Require `status=passed`, `stage=complete` and `api_process_stopped=true` before
acceptance. A partial report is useful diagnosis, not acceptance evidence.

The automated API stops at completion. To view Swagger interactively afterwards,
start the existing launcher against **that exact run's** retained configuration:

```powershell
$demoConfig = '.tools/postgres/<reported-project>/target.json'
& .\.venv\Scripts\python.exe -I -B -m hellas_dataops.api --database-config $demoConfig --port 8000
```

Choose another unused local port if 8000 is occupied; never stop an unrelated
service. In a second shell:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/health/live'
Invoke-RestMethod 'http://127.0.0.1:8000/health/ready'
Invoke-RestMethod 'http://127.0.0.1:8000/v1/cities'
$query = 'http://127.0.0.1:8000/v1/weather?city_id=athens&coordinate_version=gr-cities-v1&provider_id=open-meteo&dataset_id=era5_hourly-v1&start=2025-01-01T00:00:00Z&end=2025-01-08T00:00:00Z&limit=100'
$page = Invoke-RestMethod $query
$page.observations.Count # Expected 100
Invoke-RestMethod ($query + '&after=' + [uri]::EscapeDataString($page.next_after))
Invoke-RestMethod 'http://127.0.0.1:8000/openapi.json'
```

Open `/docs` in a browser; its interactive assets require the framework's external
CDN. The saved OpenAPI JSON is available offline. Weather responses contain `city`,
`dataset`, `units`, `start`, `end`, `observations` and `next_after`. Each observation
contains a UTC timestamp and four nullable measurements; see [API examples](api.md).
Ctrl-C stops this manually launched API; no automatic Docker cleanup is provided.

## Benchmark methodology and limitations

Two fixed queries use the exact seven-day weather URL above with limits **100**
and **500** (100 and 168 returned rows). Each uses **five warm-ups, 30 measured
requests and concurrency one**, new HTTP connections, no proxy, and a quiescent
168-row database populated by two runs. The query service uses its existing
NullPool: connection/authentication, SQL, JSON encoding and HTTP transfer are
included. `perf_counter` measures request through complete response-body receipt;
client JSON decoding/validation is outside the recorded latency.

Report p50 is the median; p95 uses nearest rank `ceil(0.95 * n)`. Record every
measured latency, response byte size, expected row count, completed sample count
and errors. A failure aborts and retains partial samples without invented percentiles.
The first weather request is recorded separately after readiness/catalog probes;
it is **not** an OS/PostgreSQL cold-cache benchmark. No cache flush is performed.
The report includes software versions, OS/architecture and logical CPU count,
without hostname or user identity. Docker memory limits and host contention are
uncontrolled. A 120-second loop budget plus in-flight socket operations bounds
the small workload; it is not an application whole-request deadline.

Fresh host latency values are recorded in the acceptance section below. Do not use offline mocked
timings as performance evidence or extrapolate this small local sample to scale.
Offline tests retain socket/DNS blocking and exercise orchestration and failures
with doubles. Real PostgreSQL/Uvicorn execution is explicitly opt-in via this
script, separate from normal pytest. The existing 133-test PostgreSQL suite can
still be run with `scripts/verify-postgres.ps1` against a different fresh database.

After successful host review, prepare the local checkpoint and a concise portfolio
evidence summary. Review repository licence, attribution and secret hygiene before
any separately authorized publication. Live weather access, security hardening
for hosting and production roles remain separately gated; no new infrastructure
is needed to present this demonstration.

## Startup remediation and VS Code (2026-10-23)

The first host report `hellas-dataops-test-530fde9f5644` failed at API startup.
Its migration succeeded, the real CLI inserted 168 rows, replay returned 168
unchanged, and completed-run resume preserved state. No HTTP/benchmark success
was recorded. Original child stdout/stderr were discarded, so that report alone
could not identify the exception.

The failure was reproduced as **API child identity mismatch**. On this Windows
Python installation the virtual-environment executable is a redirector: Popen's
PID belongs to the launcher, whereas the listener JSON contains the interpreter's
PID. Standalone Uvicorn and readiness both worked; this was a runner identity and
process-ownership defect, not a SQLAlchemy, socket-handoff or application defect.

The child launch now follows the installed CPython standard-library
`multiprocessing.popen_spawn_win32` approach: launch the current interpreter's
base executable with child-only `__PYVENV_LAUNCHER__` pointing to the project
interpreter. This preserves isolated `.venv` imports and lets Popen own the actual
server process. The child explicitly verifies its project virtual environment;
the PID guard remains enabled. No global Python configuration is modified.

Sanitized reports now include startup phases, attempts, last readiness HTTP status,
child exit code before cleanup, and bounded exception type/code-location records.
Raw messages, locals, absolute paths, environment values and connection secrets
are omitted. Child diagnostics live beside the ignored target configuration and
are copied into the final sanitized report. Raw stdout/stderr remain suppressed.

A real read-only probe against the exact retained demo database succeeded from
Codex without Docker access. Evidence: `artifacts/demo/remediation-verified/report.json`.
PostgreSQL 18.6 served all endpoint smoke checks, nulls, pagination, errors and
OpenAPI. All five application tables were unchanged. The actual API process
terminated and its listener was confirmed closed. This is **not** a fresh full
demonstration: Docker provisioning and ingestion were not rerun during this probe.
The subsequent fresh host run passed; see the final acceptance section below.

Measured retained-database results (30 requests plus five warm-ups per query,
concurrency one, 168 stored hours, same method as above):

| Returned rows | p50 ms | p95 ms | Body bytes | Errors |
| --- | ---: | ---: | ---: | ---: |
| 100 | 61.15 | 64.76 | 14,078 | 0 |
| 168 | 60.09 | 62.57 | 23,308 | 0 |

These are actual local remediation measurements, not a new-host-run result or
production scalability evidence. [VS Code setup and explicit tasks](vscode.md)
use the existing checkout and environment.


## Final acceptance: fresh host demonstration

**MILESTONE 3D.2: ACCEPTED** on 2026-10-23. The actual report is
`artifacts/demo/hellas-dataops-test-dbccaf1157c8/report.json`; its status is
`passed`, final stage `complete`, and the reviewed successful runner path returns
zero. The report does not have a separate numeric top-level exit-code field.

A new isolated official `postgres:18.6-bookworm` instance passed health, resource
ownership, localhost binding and database marker guards. Server version was
18.6; migration revision was `20261022_0001`. Initial CLI ingestion persisted
168 Athens hours for `[2025-01-01,2025-01-08)` UTC and succeeded. Identical replay
returned 168 unchanged, with zero inserts/updates and preserved source lineage.
Completed-run resume preserved all stored state.

Real Uvicorn readiness succeeded on the first probe. Smoke checks passed for
health, readiness, city catalog, weather values/units/nulls, two pages (100 + 68),
exclusive end, empty results, 404/422 inputs, Swagger HTML and OpenAPI JSON.
All five application tables remained unchanged by HTTP. The report confirms
the actual child process stopped; strict PID matching and a single owned socket
support cleanup. A later independent TCP probe from Codex timed out, so it did
not independently prove listener closure. No process-crash cleanup claim is made.

Fresh measurements (not the earlier remediation measurements):

| Returned rows | p50 ms | p95 ms | Body bytes | Measured requests | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| 100 | 49.42 | 63.63 | 14,078 | 30 | 0 |
| 168 | 59.62 | 62.95 | 23,308 | 30 | 0 |

Each query used five warm-ups and concurrency one. The first weather request
was 43.38 ms. Percentiles were independently recalculated from all recorded
samples. Windows 11 AMD64, 16 logical CPUs, Python 3.14.4, PostgreSQL 18.6,
SQLAlchemy 2.1.6 (pure Python), pg8000 1.31.5, Alembic 1.20.0, FastAPI 0.159.1
and Uvicorn 0.54.0 were recorded. Docker Server was 29.7.2; Compose was 5.4.0.

The 168-row warm, sequential workload does not establish production capacity.
Swagger CDN rendering and VS Code graphical debugging were not verified.
No unrelated-resource audit is implied: reviewed commands operated only on the
fresh project prefix and performed no cleanup. Docker resources remain retained.

The runner's Git gate remains pinned to the reviewed pre-checkpoint HEAD. It will
refuse a later HEAD until its checkpoint policy is reviewed. Address portable
clone/setup validation during the final portfolio audit; do not silently relax
the guard or treat the local checkpoint as publication approval.
