# Sequential synthetic ingestion and explicit resume

**MILESTONE 3C.1: ACCEPTED.** The host suite passed 109 PostgreSQL tests and
fresh offline verification passed 837 tests. See
[the acceptance record](development.md#milestone-3c1-final-acceptance).
No live weather retrieval, automatic retry, scheduler or production target is
enabled. The accepted five-table schema and revision `20261022_0001` are unchanged.

## Interfaces and execution

- `ingestion.coordinator.start_run(target, plan, *, fetch, clock=utc_now)`
- `ingestion.coordinator.resume_run(target, run_id, *, fetch, expected=None, clock=utc_now)`
- `storage.run_tracking.inspect_run(target, run_id)`

`target` is the existing guarded `TestDatabaseConfig`. `plan` is the existing
immutable `RunPlan`, including UUID, registered coordinates/version, dataset,
original UTC interval, creation time and explicit write policy. `expected`, when
supplied on resume, must equal the entire persisted plan, including creation time.
The fetch callable is mandatory and must be synthetic for this milestone.
There is no implicit HTTP client. Injection is a trusted programming interface,
not a security sandbox for arbitrary Python code.

The coordinator registers the complete request plan durably before fetching.
It processes one chunk at a time: retrieve, normalize the **complete provider
retrieval interval**, retain the intersection with the original requested
interval, validate through `ChunkWrite`, then atomically write weather and its
receipt. Only after commit does it request the next chunk. UTC start is inclusive
and end exclusive; day expansion never changes retained hours. Nulls remain NULL.
Memory holds one chunk of weather plus O(number of chunks) request/progress data.
The older full-result `collect_open_meteo` public interface is unchanged.

`RunProgress` and `ChunkProgress` are frozen snapshots. Run progress contains the
reconstructed plan, status, finish time, ordered chunks and original committed
inserted/updated/unchanged totals. These totals describe durable receipts, not
new writes made by the latest resume call. Repeated inspection/resume of a complete
run does not replace totals with replay counts. `created_at` is the supplied run
start time; per-chunk retrieval/completion timestamps remain in existing columns
and receipts. There is no independent trusted clock or elapsed-time benchmark.

## State and transaction boundaries

| Entity/state | Meaning |
| --- | --- |
| Run `running` | Plan registered; some or all work still pending |
| Run `failed` | A specific failure was recorded; earlier chunks remain committed |
| Run `succeeded` | Every planned chunk has a validated committed receipt |
| Chunk `planned` | Not durably complete; may currently be retrieving |
| Chunk `failed` | Retrieval, normalization or persistence failed and was recorded |
| Chunk `succeeded` | Weather writes and success receipt committed together |

`planned` remains a valid schema run state; initial operational registration sets
`running` in the same transaction. The coordinator does not use chunk `running`,
because the accepted writer consumes `planned` chunks. Failed runs have a finish
timestamp; explicit resume clears it and resets failed chunks to `planned`.
Success uses the maximum chunk completion time. A partly complete run never
returns success. **Chunk atomicity is not whole-run atomicity.**

Storage owns all transactions. A PostgreSQL session advisory lock derived from
the run UUID rejects a second coordinator immediately with `run_busy`; it is held
on the same physical connection used for writes. Each database transaction ends
before retrieval starts. A connection remains open during retrieval, but no
database transaction does. NullPool closes the physical session on exit, releasing
its lock. An invalidated connection cannot silently reconnect without ownership.
Hash collisions would conservatively reject unrelated simultaneous runs, not
allow competing ownership. The existing city/version row lock serializes writes
across overlapping runs, including runs with different UUIDs.

## Explicit resume and ambiguous outcomes

Resume reconstructs identity, version, interval, policy and requests from database
state and validates them against the current approved registry/request contract.
Changed configuration, missing chunks, incompatible operation versions, malformed
receipts and contradictory statuses fail closed. A completed chunk must have a
valid input fingerprint, counts summing to retained hours, timestamps and grid
metadata, plus complete current weather-key coverage. A current value can have
been legitimately corrected by another run; original receipt counts and lineage
history remain the evidence for the prior operation. Receipts are not signed or
tamper-proof; arbitrary SQL writers can violate application-owned contracts.

Resume skips verified successes, then retrieves only incomplete chunks in order.
It never rewrites a completed chunk, changes policy, or silently starts another
run. Starting an existing incomplete UUID returns `resume_required`. Starting
the identical completed `RunPlan` is a read-only success. The CLI constructs a new
creation timestamp for `start`, so use `status` or `resume` for an existing UUID.

If a write raises, the coordinator checks durable progress before deciding what
happened. A validated success receipt with the exact input fingerprint resolves
a simulated lost acknowledgement without refetching. A verified incomplete chunk
can be marked failed in a separate transaction. If state cannot be verified, the
error says outcome unknown and no success is returned. The operator must explicitly
resume once connectivity is restored; there is no automatic retry. New sessions
acquire ownership and read under the writer's city lock before deciding to skip
or retry. Interrupted runs may remain `running`; that status is not a heartbeat
or evidence of a still-live process. Ctrl-C/process interruption is not converted
into a fabricated terminal status.

Failure events append stage and UTC time to chunk provenance and survive resume
and successful writes. Raw exceptions, connection details and response bodies
are never stored. Existing legacy 3B.2 runs without the operational envelope are
not adopted automatically. Direct `persist_chunk` still rejects failed runs;
only the operational boundary deliberately transitions its own verified plans.

## Provenance and corrections

The existing `chunk-write-v1` envelope gains additive fields:

- `operation: "synthetic-sequential-v1"`, explicitly labelling this execution.
- `failures: [{"stage": "retrieve|normalize|persist", "at": "UTC ISO timestamp"}]`.

Request intent, write policy and success receipt retain their previous meanings.
The writer now preserves lifecycle fields when recording success. Request
coordinates remain separate from returned grid columns. ERA5 selection records
request intent, never independent response proof. Synthetic measurements are
invented and must not be published as Open-Meteo observations. Only disposable
test targets are supported.

Default changed-value writes still fail atomically. Corrections require a **new
run UUID**, explicit `WritePolicy.REPLACE`, and newer retrieval time. Resume cannot
change that choice. Previous values and source run/chunk pointers remain in the
correction receipt. Unchanged rows keep original value-origin lineage.

## Local commands

Run from the repository root after successful host verification. Select the exact
new target JSON path printed in the host report; do not guess another database.
The verified container is retained and its application tables are empty after
test cleanup. These examples subsequently create synthetic demonstration data.

```powershell
$testConfig = '.tools/postgres/<reported-project>/target.json'
$runId = [guid]::NewGuid().ToString()
& .\.venv\Scripts\python.exe -I -B -m hellas_dataops.ingestion --database-config $testConfig start --run-id $runId --city athens --start '2025-01-01T05:00:00+00:00' --end '2025-01-16T03:00:00+00:00'
& .\.venv\Scripts\python.exe -I -B -m hellas_dataops.ingestion --database-config $testConfig status --run-id $runId
& .\.venv\Scripts\python.exe -I -B -m hellas_dataops.ingestion --database-config $testConfig resume --run-id $runId
```

All three commands produce safe JSON and exit 0 on successful execution, 1 on a
handled operation failure, or 2 for invalid CLI syntax. A successful `status`
command can report a failed run; inspect its `status` field. No `--live` option
exists. Resume accepts only an existing UUID: city/dataset/range overrides are
not accepted. `--policy replace` is available only on `start` with a new UUID.
The synthetic adapter supplies constant 10 C temperatures and null other fields,
using in-memory bytes through the accepted HTTP decoder. No fixtures need to be
downloaded or packaged in the runtime wheel.

## Verification and limits

Offline tests run real planning/decoding/normalization/mapping with a labelled
lifecycle double; they do not establish SQL transactions. The opt-in host suite
adds 26 cases for real durable state, partial failures, resume, rollbacks,
unchanged/corrected lineage, ownership conflicts, corrupt progress, and simulated
lost acknowledgements/session loss. It also checks that retrieval observes no
idle database transaction. Simulated faults are not actual process-crash or
network-partition recovery tests. All 26 new cases passed in the 109-test host suite.

From normal PowerShell, use `& .\scripts\verify-postgres.ps1`; the reviewed gate
expects main at `34a9c087adfa28c783dc8e319c6ca6c278b2e49e`. It collects 109 tests
and measures storage plus coordinator coverage. It creates uniquely owned
PostgreSQL 18.6 resources and never deletes existing volumes or touches other
projects. The report under `artifacts/postgres/hellas-dataops-test-4a43cbabd8ba/`
was reviewed for acceptance. No rerun is needed for the checkpoint. After committing,
the frozen HEAD gate must be reviewed during the next authorized milestone.

Remaining limits: no automatic retry/backoff, heartbeat, whole-operation/DNS
deadline, raw-response retention, production roles/TLS/configuration, or live
provider compatibility. Failure history and plan metadata grow with attempts
and chunk count; no retention policy or throughput benchmark is claimed. Weather
memory is chunk-bounded, but no arbitrary total run-length limit is imposed.
The recommended next milestone is a small read-only FastAPI layer over stored
synthetic observations. Live source execution remains separately gated on access
terms and operating conditions. Scheduling/cloud remain optional.
