# Host-assisted PostgreSQL verification (Milestone 3B.1)

Status: **Milestone 3B.1 ACCEPTED**, based on the owner-host run of 54 passing
PostgreSQL tests and final review on 2026-10-22. Codex
cannot access Docker Desktop's Linux named pipe. The approved pure-Python pg8000
driver now imports successfully, replacing Psycopg's blocked native component.
No security policy was changed. Upgrade/downgrade/re-upgrade and the covered
constraint cases passed on PostgreSQL 18.6. See the
[acceptance record](development.md#milestone-3b1-final-acceptance).

## One host command

Keep Docker Desktop running with Linux containers. From an ordinary Windows
PowerShell window in the Hellas DataOps repository root:

```powershell
& .\scripts\verify-postgres.ps1
```

The wrapper invokes `.venv/Scripts/python.exe -I -B scripts/verify_postgres.py`.
It does not elevate, change execution policy, modify Docker permissions, or
expose the Docker API over TCP. If local policy blocks the PowerShell wrapper,
the equivalent command in the same directory is:

```powershell
& .\.venv\Scripts\python.exe -I -B .\scripts\verify_postgres.py
```

The selected driver is `pg8000 1.31.5`, with explicit SQLAlchemy dialect
`postgresql+pg8000`. Psycopg and psycopg-binary are removed from the required
environment. If driver preflight fails, share the sanitized report. Do not change
Application Control or install unreviewed native alternatives.

The [SQLAlchemy 2.1 dialect documentation](https://docs.sqlalchemy.org/en/21/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.pg8000)
supports pg8000, parameter binding and transactional isolation. The
[pg8000 package documentation](https://pypi.org/project/pg8000/1.31.5/) describes
a pure-Python DB-API driver and timezone-aware timestamp mapping. Package metadata
allows Python >=3.9, but does not explicitly classify Python 3.14. Local imports,
real dialect argument handling, binding and timestamp codecs were tested on
3.14.4. The subsequent host suite verified the required functionality against
PostgreSQL 18.6 with 54 passing tests. Alembic uses the existing
SQLAlchemy connection and needs no driver-specific migration changes.

Use Windows x64 / CPython 3.14.4 with the existing local uv 0.13.4 and locked
environment. The script verifies the main branch and committed 3A checkpoint
`6a0439522cd9d1bd17005850a5b718730ef44c28`. Only reviewed 3B.1 file paths may be
modified/untracked; unexpected paths stop execution. The baseline check must be
reviewed after a future owner-created checkpoint rather than silently bypassed.
The frozen runner intentionally refuses a different HEAD after the 3B.1 commit;
review that gate before the next authorized database run. The accepted host run
does not need to be repeated merely to create the checkpoint.

## Infrastructure and preflight

`compose.postgres-test.yaml` uses the official **`postgres:18.6-bookworm`** image.
The versioned tag is explicit; it is not a digest pin. The host report records the
actual image ID and PostgreSQL server version. Official references:
[image tags and configuration](https://hub.docker.com/_/postgres),
[PostgreSQL 18 volume layout](https://github.com/docker-library/docs/blob/master/postgres/README.md).
The image is downloaded only on the host after environment and collision checks.

Each invocation creates a fresh name `hellas-dataops-test-<12 random hex digits>`:

| Resource | Name/configuration |
| --- | --- |
| Compose project | `hellas-dataops-test-<suffix>` |
| Container | `<project>-postgres` |
| Network | `<project>-network` |
| Named volume | `<project>-pgdata` |
| Database / database user | `hellas_dataops_test` inside that isolated instance |
| Host address | `127.0.0.1` only; OS-selected available high port |
| Volume mount | `/var/lib/postgresql` |
| PGDATA | `/var/lib/postgresql/18/docker` |

The script checks the exact intended names; it refuses reuse even if an existing
resource appears to belong to this project. It does not enumerate unrelated
container contents. Port selection uses an exclusive local bind; a later race
causes startup failure, never eviction of another process. Compose's health wait
is bounded to 120 seconds; image download and subprocess execution also have
timeouts. Restart policy is `no`.

Random test credentials and an independent ownership marker are written only to
ignored `.tools/postgres/<project>/`. The password is supplied through a read-only
Compose secret file, not the process command line or a committed environment file.
A read-only initialization SQL file sets the database comment to the run-specific
ownership marker. No credentials are printed. Local files still rely on the
owner's existing Windows account protections; these are test-only credentials.

After health succeeds, inspect the exact container/network/volume to verify
ownership labels, image selection, mount layout, read-only binds, localhost port,
non-privileged mode, and exclusive network membership. Other projects' containers,
images, networks, and volumes are never started, stopped, reset, or deleted.

## Connection and migration safeguards

`storage.test_database.TestDatabaseConfig` is frozen and rejects non-loopback
hosts, arbitrary database/user names, invalid ports, unknown configuration keys,
and malformed instance/credential fields. No URL input or environment fallback
is accepted. JSON loading is explicit through `load_test_database(path)`.

`test_connection(target)` constructs a synchronous SQLAlchemy/pg8000 engine only
when called, uses `NullPool`, hides SQL parameters, fixes the host to `127.0.0.1`,
and passes `timeout=5`, `ssl_context=False`, and explicit `startup_params` for
UTC, public search path, `statement_timeout=15000`, and `lock_timeout=5000`.
The driver timeout applies to socket operations, not a whole-request deadline;
it may expire before the server statement timeout. TLS is disabled solely for
this local disposable instance. This is not production database configuration.
The bootstrap database role is a superuser confined to this disposable container;
production least-privilege roles are outside 3B.1.

Before any test write, a read-only query verifies database, current user,
PostgreSQL major version 18, and the exact run-specific ownership marker. A name
or localhost port alone is never treated as evidence of ownership. These guards
prevent accidental targeting; a hostile local administrator could deliberately
forge the marker and is outside this safety boundary.

`storage.migration.migrate_test_database(target)` upgrades to `20261022_0001`;
`migrate_test_database(target, downgrade=True)` downgrades to base. The initial
revision SQL and schema are unchanged. Online migrations refuse unexpected user
tables/views and populated application tables. Existing allowed tables are locked
before emptiness checks, inside the same migration transaction. Generic online
Alembic CLI commands and configured database URLs remain rejected.

Offline commands remain available without a driver import or server:

```powershell
& ./.venv/Scripts/python.exe -I -B -m alembic -c alembic.ini upgrade head --sql
& ./.venv/Scripts/python.exe -I -B -m alembic -c alembic.ini downgrade 20261022_0001:base --sql
```

## Database tests and reports

The default `pytest` suite selects only `tests/unit`. The socket/DNS blocker and
settings isolation fixtures are unchanged. Integration tests require both their
explicit directory and `--postgres-config` pointing to the newly generated target.
The host runner supplies these inputs; absent opt-in is a pytest usage error.
pg8000 uses Python sockets. Only this integration subtree overrides the network
fixture: it permits the exact validated numeric localhost address and port,
supplies that address without DNS, and rejects every other destination. The
restriction covers session migration setup, each test, and teardown. Offline
tests verify both rejected destinations and the unchanged root offline blocker.

The session fixture requires a fresh database, upgrades, checks revision/table
creation, downgrades, checks that only Alembic's empty version table remains,
then re-upgrades. Each constraint
test uses synthetic rows in its own rollback-only transaction. Teardown verifies
application tables are empty and the initial revision remains installed. No
application persistence service or idempotent upsert implementation is introduced.

Tests cover live catalog types/keys/indexes/check names, Alembic metadata comparison,
unique configuration/hour keys, coordinate versions, null values, finite numeric
bounds, missing and mismatched lineage, interval alignment/order, timezone round
trips, and migration target guards. TIMESTAMPTZ normalizes instants and cannot
prove that the original caller supplied UTC. Complete coverage, immutable
configuration, and cross-row interval semantics still require future application
validation. Successful metadata comparison alone does not exercise every CHECK;
the individual insertion/update tests supply enforcement evidence once executed.
The 52 original database cases are preserved, with two added cases for pg8000
bound values/session settings and savepoint rollback. pg8000 raises DB-API
`DatabaseError` for constraint violations rather than `IntegrityError`; tests
still require the exact PostgreSQL SQLSTATE and a constraint-name field, so an
unrelated database failure cannot satisfy a rejection assertion.

The runner writes ignored `artifacts/postgres/<project>/report.json`, sanitized
`pytest.txt`, and integration `coverage.json` when tests run. Reports distinguish
preflight stage failures from test failures and record exit codes. Driver/dialect
imports are checked before Docker creation. Secrets/raw connection diagnostics
are never included. Review the report and test counts before declaring 3B.1 complete.

No automatic cleanup runs, on success or failure. Containers/volumes created by
the invocation remain for inspection and consume local resources. Review the
reported unique prefix before manually stopping/removing **only that instance**;
do not use global prune or remove existing volumes automatically. Repeated runs
create additional isolated environments, not resets of the prior one.

## Remaining work

The host suite passed and its evidence was reviewed. Milestone 3B.1 is complete
for its defined schema/migration scope. Concurrency, interrupted migrations,
network-loss recovery, production permissions/TLS, and application persistence
are not established by this test run. Future defects require focused corrections
and repeat verification.

Milestone 3B.2 needs separate approval: domain-to-storage mappings, transactional
idempotent chunk writes, deliberate correction/lineage policy, atomic completion,
concurrency, and injected failure/recovery tests. No live weather requests,
FastAPI endpoints, scheduling, or deployment belong to this milestone.
