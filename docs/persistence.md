# Transactional chunk persistence (Milestone 3B.2)

**MILESTONE 3B.2: ACCEPTED.** The host run passed 83 PostgreSQL tests, including
29 persistence cases; final offline verification passed 750 tests. See
[the acceptance evidence](development.md#milestone-3b2-final-acceptance).
The accepted 3B.1 schema and revision `20261022_0001` remain unchanged.
The storage writer itself performs no retrieval. Milestone 3C.1 adds the separate
synthetic coordinator documented in [ingestion.md](ingestion.md); automatic retry,
scheduling and production connection configuration remain absent. Domain and
existing source pipeline interfaces are unchanged.

## Public boundary

`hellas_dataops.storage.mapping` supplies frozen `RunPlan` and `ChunkWrite`,
`WritePolicy`, and `PersistenceError`. `storage.persistence.persist_chunk(target,
chunk)` returns frozen `WriteResult(inserted, updated, unchanged, replayed,
run_status)`. The target must be the existing explicit `TestDatabaseConfig`;
connection setup checks the disposable database's user, name, version and secret
ownership marker before writes. Arbitrary URLs are unsupported.

The caller supplies a nonzero run UUID, registered city coordinates, dataset,
original requested interval, creation time and policy. Planning reuses the
existing ERA5 planner. A chunk supplies its one-based index, retained canonical
observations, normalized grid metadata, retrieval time and completion time.
Execution times must be aware UTC with creation <= retrieval <= completion.
They are caller-supplied facts, not independently verified clocks.

Perform HTTP retrieval and full-response normalization **before** persistence.
The existing source pipeline already provides validated observations and paired
request/grid metadata. Select the rows in each request's `retain_interval`, use
the same run plan, and pass the corresponding metadata. The storage boundary
again validates complete retained coverage, city/dataset identity and metadata.
It neither fetches weather nor guesses a retrieval timestamp for pipeline results.
Null measurements remain SQL NULL. Missing, extra or duplicate hours are rejected.

## Transaction and state

Each call owns one transaction: resolve immutable configuration identities,
register/check the run and all planned chunks, write weather, and record chunk
success plus run state. Requested city coordinates and returned grid coordinates
remain separate. Same identity with different configuration values is rejected.
Registry membership still limits writes to the approved coordinate version;
introducing another version needs an explicit registry review.

The first successful chunk registers the full deterministic plan. Pending chunks
are `planned`; persisted chunks are `succeeded`. The run is `running` until every
planned chunk has succeeded, then `succeeded`, with the maximum recorded completion
time. Chunks may arrive out of order. A failure rolls back that call's data and
provenance changes, leaving earlier committed chunks intact. It does not write a
separate failed status outside the transaction. Direct persistence rejects failed runs. The new operational boundary can
deliberately resume its own validated synthetic plans, resetting failed states
before calling the unchanged write rules; arbitrary legacy failed runs are not adopted. There is no background recovery worker or retry/backoff policy.

## Policies, counts and replay

| Situation | Behaviour |
| --- | --- |
| New observation key | Insert with this run/chunk lineage |
| Existing key, all four values equal (including NULL) | Count unchanged; preserve original weather row and lineage |
| Changed values, default `REJECT_CHANGES` | Reject and roll back the entire chunk |
| Changed values, explicit `REPLACE` | Require strictly newer retrieval time; replace all four values, including NULL; update lineage |
| Same completed run/chunk and identical input digest | No data/status/provenance updates; return zero inserted/updated and all input hours unchanged |
| Same completed run/chunk with different inputs | Reject; correction or new retrieval requires a new run UUID |

Counts describe the current call, not cumulative run totals. An exact replay means
no new writes even if a subsequent correction changed the current weather values;
it never restores old data. The original receipt retains the original counts.
Completion time may change on retry without changing the input digest; the stored
original completion time is preserved. Sequence allocation can leave gaps on a
failed first-insert race; surrogate IDs are not gap-free business identifiers.

Replacing a value is a deliberate correction policy, not an automatic refresh
trigger. A newer response with equal measurements records its successful attempt
but does not change the observation's value-origin lineage. Freshness is compared
to the retrieval time of that value's current origin, not to every unchanged
retrieval attempt. Equal or older retrieval times cannot overwrite changed values.

## Provenance

`ingestion_chunks.request_provenance` uses a `chunk-write-v1` envelope containing:

- `request`: the planner's canonical JSON object, unchanged.
- `policy`: the explicitly selected write policy.
- `receipt` after success: SHA-256 canonical input fingerprint, completion time,
  inserted/updated/unchanged counts and previous-value records for corrections.

The digest includes request intent, policy, retrieval time, returned metadata and
sorted measurements. It is **not an HTTP response checksum**. For each correction,
the receipt retains the previous four measurements and previous run/chunk pointer,
forming an audit chain through retained chunk receipts. This is application-owned
JSON, not a database-enforced event log or tamper-proof history. Do not delete old
provenance. The accepted schema requires no new columns or migration.

Grid metadata lives in separate typed chunk columns. Requested ERA5 selection
records intent; no payload field independently proves the historical model.

## Concurrency and failure limits

Writers acquire a city-configuration row lock before run and weather locks. This
serializes overlapping and disjoint writes for that city/version, even when no
weather rows exist yet. It is intentionally broader than an interval lock and
also serializes different datasets for the same city configuration. Different
cities can proceed independently. All statements bind values as parameters.

The supported connection uses PostgreSQL's READ COMMITTED isolation. Under the
lock, existing rows are read and classified explicitly; counts do not depend on
driver rowcount or PostgreSQL internal tuple fields. This coordination assumes
writers use this service. Arbitrary SQL writers can bypass application policies;
database constraints still apply but cannot enforce the whole provenance contract.

SQL errors are converted to credential-free `PersistenceError` with run/chunk
context. Lock/statement/socket limits are inherited from the accepted connection
helper; no automatic retry is attempted. A lost connection during commit can make
the outcome unknown. Retry identical input with the same run ID to resolve replay,
or investigate explicitly; an exception alone does not prove rollback after an
ambiguous commit. Whole-run atomicity and a whole-operation deadline are absent.

## Verification

Offline tests cover mapping, validation, input fingerprints, transaction ownership,
safe errors and PostgreSQL statement compilation. They do not simulate a database
as proof of transaction or concurrency correctness.

The opt-in PostgreSQL suite adds real committed inserts/replays, partial overlap,
correction including NULL transitions, stale refresh rejection, immutable identity
conflicts, late-failure rollback, out-of-order/multi-chunk completion, and two live
connections with a server-observed lock wait. Synthetic test records are removed
by their known identities; no volumes or unrelated resources are deleted.

Run `& .\scripts\verify-postgres.ps1` from the project in normal PowerShell.
The current runner requires the accepted 3B.2 checkpoint
`34a9c087adfa28c783dc8e319c6ca6c278b2e49e` and permits reviewed 3C.1 edits only.
It runs 83 existing database cases plus 26 operational lifecycle cases; all 109
passed in the accepted 3C.1 host run. The historical accepted 3B.2 report remains
`artifacts/postgres/hellas-dataops-test-309003857997/report.json`.

Host storage coverage is 87.02% combined (295/326 statements, 67/90 branches).
Persistence itself is 90.98% combined in that run. Offline tests exercise most
remaining validation and configuration errors. The union of both coverage reports
leaves one defensive invalid-chunk-state rejection untested. Commit-acknowledgement
loss, process crashes, sustained contention and automatic recovery were not tested.
Successful retry after an injected rollback does not establish those guarantees.

### Additive lifecycle fields

The writer preserves operational envelope fields when appending a success receipt.
`storage.run_tracking` shares identity/plan preparation and the same internal Core
writer, adding registration before retrieval and explicit failed-run resume. These
changes do not alter `persist_chunk` inputs/results or replay/correction policy.
The 3C.1 host suite verifies the documented lifecycle scenarios. See
[acceptance evidence and remaining limits](development.md#milestone-3c1-final-acceptance).
