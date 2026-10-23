# Development and verification

For the current accepted API and database verification, see
[Milestone 3D.1 final acceptance](#milestone-3d1-final-acceptance).
Earlier source integration is recorded in [Milestone 2E](#milestone-2e-offline-source-pipeline).
The schema checkpoint is recorded in
[Milestone 3A](#milestone-3a-schema-and-migration-foundation).
Earlier records below remain historical.

The Milestone 1B record below is historical. See the later sections for Milestone
2A domain checks and Milestone 2B source normalization checks, and the separate
[Milestone 2C record](milestone-2c.md) for planning and rebuilt distributions. The earlier
configuration-only coverage and 13-test count are not the current full-suite totals.

Verified on 2026-10-20: Windows x64, CPython 3.14.4. Only this platform/interpreter
combination has been tested. Python was already installed; no global packages,
Python installations, services, or system settings were changed.

## Resolved tools and dependencies

| Component | Verified version |
| --- | --- |
| Local uv bootstrap | 0.13.4 |
| Python | 3.14.4 |
| Pydantic | 2.14.1 |
| pydantic-settings | 2.16.1 |
| pydantic-core | 2.50.1 |
| pytest | 9.1.1 |
| pytest-cov | 7.1.0 |
| coverage | 7.16.2 |
| Ruff | 0.17.1 |
| setuptools build backend | 84.2.0 |

`uv.lock` contains 18 resolved packages including the local project. Seven
third-party runtime packages are required; the remaining third-party packages
are development tools or their dependencies. uv itself lives in `.tools/uv`
and is bootstrapped separately, with its version pinned in the README command.

The initial bootstrap command used `uv` without a version and selected 0.13.4:

```powershell
python -I -B -m pip --isolated --disable-pip-version-check install --index-url https://pypi.org/simple --only-binary=:all: --no-deps --no-cache-dir --retries 0 --timeout 15 --target .tools/uv uv
```

Do not rerun the unpinned command for routine setup. Follow the pinned bootstrap,
project-local cache/temp settings, synchronization, tests, linting, and build
commands in [README](../README.md).

## Results and boundaries

- `uv lock --check --offline` passed.
- A repeated `uv sync --locked --offline` checked all 18 installed packages and
  left the lockfile's SHA-256 unchanged:
  `820DA87A498095A57895A38548EB46C4D07AB7ECE3B64FA9B21E0D9ED3A86390`.
- The existing offline suite passed all 13 tests under the installed package,
  without pytest `pythonpath` configuration. The network guard was retained.
- Ruff lint and formatting checks passed without changing application or test
  behaviour.
- Coverage measured **8 of 8 executable statements in `config.py` (100%)**.
  There are zero measured branches; seven empty package files were skipped.
  This result is limited to the implemented configuration layer. It is not
  coverage of a working weather pipeline or of third-party validation internals.
- Both source distribution and wheel built successfully with setuptools 84.2.0.
- Separate fresh wheel and source-distribution environments initially contained
  neither the project nor Pydantic nor pytest. Runtime dependencies were installed
  from a hash-bearing export of the lock, then the distribution was installed.
- `scripts/verify_installed.py` checked 11 imports in each environment: all eight
  project modules plus Pydantic, pydantic-settings, and pydantic-core. Imports
  resolved inside the fresh environment's `Lib/site-packages`, not the source
  checkout or global installation. Configuration construction also passed.
- `uv pip check` passed for both eight-package runtime installations.

Virtual environments have `include-system-site-packages = false`. Python `-I`
also excludes PYTHONPATH and user-site packages. The editable development install
is expected to reference `src`; the distribution check rejects editable installs.

## Exact distribution verification procedure

Run from the repository root after the README's session settings, locked sync,
and build. `$basePython` is the existing Python 3.14.4 executable selected in
that setup. No environment activation is needed. Stop after any failed command.

Export only locked runtime dependencies (with their hashes), excluding the
editable project:

```powershell
& ./.tools/uv/bin/uv.exe export --locked --offline --no-dev --no-emit-project --format requirements.txt --output-file .tools/runtime-requirements.txt | Out-Null
```

Create a fresh wheel check environment. The target must not already exist; use
a new local directory name for a later repeat rather than overwriting an
environment containing unrelated work.

```powershell
& ./.tools/uv/bin/uv.exe venv .tools/wheel-check --python $basePython --no-managed-python --offline
& ./.tools/wheel-check/Scripts/python.exe -I -B -c "import importlib.util as u, sys; assert sys.prefix != sys.base_prefix; assert all(u.find_spec(n) is None for n in ['hellas_dataops','pydantic','pytest']); print('Fresh environment confirmed')"
& ./.tools/uv/bin/uv.exe pip sync --python .tools/wheel-check/Scripts/python.exe --offline --require-hashes .tools/runtime-requirements.txt
& ./.tools/uv/bin/uv.exe pip install --python .tools/wheel-check/Scripts/python.exe --offline --no-deps dist/hellas_dataops-0.1.0.dev0-py3-none-any.whl
& ./.tools/wheel-check/Scripts/python.exe -I -B scripts/verify_installed.py
& ./.tools/uv/bin/uv.exe pip check --python .tools/wheel-check/Scripts/python.exe
```

Independently install the source distribution. Its pinned backend is provisioned
by uv in an isolated temporary build environment using the populated local cache:

```powershell
& ./.tools/uv/bin/uv.exe venv .tools/sdist-check --python $basePython --no-managed-python --offline
& ./.tools/sdist-check/Scripts/python.exe -I -B -c "import importlib.util as u; assert all(u.find_spec(n) is None for n in ['hellas_dataops','pydantic','pytest']); print('Fresh environment confirmed')"
& ./.tools/uv/bin/uv.exe pip sync --python .tools/sdist-check/Scripts/python.exe --offline --require-hashes .tools/runtime-requirements.txt
& ./.tools/uv/bin/uv.exe pip install --python .tools/sdist-check/Scripts/python.exe --offline --no-deps dist/hellas_dataops-0.1.0.dev0.tar.gz
& ./.tools/sdist-check/Scripts/python.exe -I -B scripts/verify_installed.py
& ./.tools/uv/bin/uv.exe pip check --python .tools/sdist-check/Scripts/python.exe
```

The generated requirements export is a disposable verification input, not a
second maintained lockfile. All bootstrap tools, temporary environments, build
outputs, caches, and coverage reports are ignored by Git. The verification script
is deliberately scoped to Windows, the platform actually tested.

## Remaining limits

- Dependency resolution and installation were verified, not byte-for-byte build
  reproducibility. Archive timestamps can differ between builds.
- A cold machine needs access to PyPI and the pinned interpreter. Offline commands
  rely on the populated local cache; the repository is not a vendored dependency
  bundle.
- The lock contains artifacts for other platforms, but those platforms have not
  been tested. Future runtime dependencies need separate compatibility checks.
- Keeping the cache inside the repository can trigger uv's generic packaging
  warning. Archive contents must exclude `.uv-cache`, `.tools`, `.venv`, local
  credentials, and generated runtime data; `.gitignore` alone is not a packaging
  exclusion mechanism.
- No weather HTTP, database, migration, endpoint, Docker, PostgreSQL, or cloud
  functionality was added. The next suggested work is offline domain validation
  and synthetic weather contracts, subject to separate approval.

## Milestone 2A verification

Foundation discovery confirmed that README already contains Windows setup,
locked synchronization, testing, linting, formatting, and build instructions.
The original 13 configuration tests passed before implementation.

The explicit project tool `.tools/uv/bin/uv.exe` reports 0.13.4. `Get-Command uv
-All` found no executable on PATH in this execution session. A reported 0.13.3
installation in another PowerShell session was not visible here and was not
changed. No dependency or interpreter installation is part of Milestone 2A.

Run the documented quality command with an additional JSON coverage report to
inspect domain metrics separately:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider -p pytest_cov --cov=hellas_dataops --cov-report=term-missing --cov-report=xml:coverage.xml --cov-report=json:artifacts/coverage-2a.json
& ./.venv/Scripts/ruff.exe check .
& ./.venv/Scripts/ruff.exe format --check .
```

Results on Windows x64 / Python 3.14.4: **165 passed, 0 failed** (13 existing
configuration tests and 152 new parametrized domain cases). The network-blocking
protections are unchanged. One unused test import found by Ruff was removed;
no test behaviour or check was disabled.

Final Ruff lint and formatting checks passed. `uv lock --check --offline`,
`uv sync --locked --offline`, and `uv pip check` also passed using the explicit
project-local executable. All 18 installed packages remained compatible; the
lockfile SHA-256 stayed identical to the Milestone 1B value above. No changes
were made to `pyproject.toml`, `uv.lock`, the original configuration tests, or
the root network-blocking fixtures.

| Domain module | Covered statements | Covered branches |
| --- | --- | --- |
| `domain/models.py` | 68/68 (100%) | 26/26 (100%) |
| `domain/validation.py` | 41/41 (100%) | 26/26 (100%) |
| `domain/errors.py` | 5/5 (100%) | No branches |
| `domain/__init__.py` | 4/4 (100%) | No branches |

Coverage describes exercised statements and branches, not proof that all future
input combinations are correct. No domain performance benchmark, upstream
compatibility, database behaviour, or fresh distribution build is claimed in
this milestone. Milestone 1B distribution artifacts are not automatically
updated by these tests.

## Milestone 2B verification

Verified on 2026-10-20 in the existing Windows x64 / CPython 3.14.4 environment.
Foundation review read `AGENTS.md`, package/dependency configuration, domain
models and validators, tests, and architecture/data/development documentation.
The README already documented Windows setup, installation, test, lint, format,
and build commands. No missing Milestone 1B setup instructions were found.

The explicit `.tools/uv/bin/uv.exe` reports **0.13.4**; `Get-Command uv -All`
again found no PATH executable in this session. No global PATH or installation
was changed. The baseline **165 tests passed** before implementation.

`git status --short --untracked-files=all` showed the expected 30 untracked
project files, with no unexpected changes. The repository has no commits, so
there is no historical Git diff against which to independently prove Milestone
2A changes. The inventory below was recovered from the milestone record and
verified against the real files. The Milestone 2B inventory was checked against
a pre-change file/hash snapshot, not inferred from Git's untracked status.

### Recovered Milestone 2A inventory

Created:

- `src/hellas_dataops/domain/models.py`
- `src/hellas_dataops/domain/validation.py`
- `src/hellas_dataops/domain/errors.py`
- `tests/unit/conftest.py`
- `tests/unit/test_domain_models.py`
- `tests/unit/test_domain_validation.py`

Modified:

- `src/hellas_dataops/domain/__init__.py`
- `AGENTS.md`
- `README.md`
- `docs/architecture.md`
- `docs/data-contract.md`
- `docs/development.md`
- `tests/fixtures/README.md`

### Milestone 2B inventory

Created:

- `src/hellas_dataops/sources/open_meteo.py`
- `tests/unit/test_open_meteo.py`
- `tests/fixtures/open_meteo/seven_days.json`
- `tests/fixtures/open_meteo/null_measurements.json`
- `tests/fixtures/open_meteo/leap_day.json`

Modified:

- `src/hellas_dataops/sources/__init__.py`
- `AGENTS.md`
- `MANIFEST.in` (include synthetic JSON fixtures in future source distributions)
- `README.md`
- `docs/architecture.md`
- `docs/data-contract.md`
- `docs/development.md`
- `tests/fixtures/README.md`

Ignored local outputs include `.tools/milestone-2b-before.json` (verification
hash snapshot), `.coverage`, `coverage.xml`, and `artifacts/coverage-2b.json`.
They are not source deliverables or changes to the dependency contract.

### Commands and results

The following baseline test command passed all 165 existing tests:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider
```

After implementation, run the complete suite with statement/branch coverage:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider -p pytest_cov --cov=hellas_dataops --cov-report=term-missing --cov-report=xml:coverage.xml --cov-report=json:artifacts/coverage-2b.json
& ./.venv/Scripts/ruff.exe check .
& ./.venv/Scripts/ruff.exe format --check .
```

Results: **311 passed, 0 failed**: 13 configuration tests, 152 existing domain
tests, and 146 new source normalization cases. Ruff lint passed and all 26 Python
files passed formatting checks. No existing tests, domain implementation,
dependency specification, or root network-blocking fixtures were changed.

Branch measurement remains enabled in `pyproject.toml`. The JSON report exposes
module-specific metrics rather than only an overall percentage:

| Module | Covered statements | Covered branches |
| --- | --- | --- |
| `sources/open_meteo.py` | 87/87 (100%) | 34/34 (100%) |
| `domain/models.py` | 68/68 (100%) | 26/26 (100%) |
| `domain/validation.py` | 41/41 (100%) | 26/26 (100%) |
| Whole implemented package | 213/213 (100%) | 86/86 (100%) |

The new tests cover units and array structure, invalid/missing metadata, epoch
type/range/alignment, all-null rows, leap day, missing/duplicate/unexpected hours,
unsorted data, explicit context, deterministic errors, immutability, no input
mutation, and extra-field compatibility. Full coverage is not proof of live
API compatibility or of all possible input combinations.

Locked verification used the existing populated project cache:

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_LINK_MODE = 'copy'
$env:TEMP = Join-Path $PWD '.tools/tmp'
$env:TMP = $env:TEMP
$lockBefore = (Get-FileHash uv.lock -Algorithm SHA256).Hash
& ./.tools/uv/bin/uv.exe lock --check --offline
if ($LASTEXITCODE -ne 0) { throw 'Lock check failed' }
& ./.tools/uv/bin/uv.exe sync --locked --offline
if ($LASTEXITCODE -ne 0) { throw 'Locked sync failed' }
& ./.tools/uv/bin/uv.exe pip check --python .venv/Scripts/python.exe
if ($LASTEXITCODE -ne 0) { throw 'Environment check failed' }
if ($lockBefore -ne (Get-FileHash uv.lock -Algorithm SHA256).Hash) { throw 'Lockfile changed' }
```

All checks passed for 18 packages; the lockfile SHA-256 remains the Milestone 1B
value documented above. No packages were added, upgraded, or downloaded. The
normalizer depends only on the standard library and the existing domain code.

No build or fresh distribution-install verification was rerun in Milestone 2B.
`MANIFEST.in` now declares JSON fixture inclusion; archive inclusion remains to
be checked on the next build. Existing Milestone 1B artifacts are historical.
No benchmark, live HTTP compatibility, or full provenance capture is claimed.

Proposed Milestone 2C, subject to approval: offline request planning and explicit
request/dataset provenance. Bind ERA5, variables, units, UTC dates, and versioned
requested city coordinates; test whole-day/half-open interval conversion and
bounded chunks. HTTP execution, retries, persistence, and API routes remain
separate future work.

## Milestone 2E: offline source pipeline

Verified on 2026-10-22 (Europe/Athens), Windows x64 / CPython 3.14.4, using
project-local uv 0.13.4 and the existing locked environment. The repository was
clean on `main` at `2ba9ae6b4b66c48bd37ad462d15526fc92d78acd` before implementation.
No Git metadata writes, commits, external calls, installations, services, or
global configuration changes were performed in this milestone.

`ingestion.source_pipeline.collect_open_meteo` plans requests and invokes a
required fetch callable. Tests configure the existing standard-library client
using `functools.partial(fetch_open_meteo, transport=synthetic_transport)`.
Synthetic context-managed byte streams exercise real HTTP envelope/JSON parsing,
normalization, complete-day validation, retained-hour filtering, and final domain
validation. The existing socket/DNS guard remains active.

The frozen result retains ordered observations and separate immutable request
and returned-grid metadata per chunk. Expected client/domain errors preserve
their original cause with stage, chunk, and requested-interval context. Nothing
is returned until every chunk and the final assembly pass. Full details are in
[the data contract](data-contract.md#implemented-source-pipeline).

Files created:

- `src/hellas_dataops/ingestion/source_pipeline.py`
- `tests/unit/test_source_pipeline.py`

Files modified: `src/hellas_dataops/ingestion/__init__.py`, `AGENTS.md`,
`README.md`, `docs/architecture.md`, `docs/data-contract.md`, and this document.
Existing domain/source implementations, public interfaces, tests, synthetic JSON
fixtures, dependency specification, lockfile, and network guard are unchanged.

Baseline: **511 passed**. Final suite: **566 passed, 0 failed**, including **55 new
offline component integration cases**. They cover one-hour/seven-day/14-day/15-day
requests, partial days, leap/year boundaries, UTC across Greek DST transitions,
all five cities, nulls, unsorted input, repeatability, per-chunk grid metadata,
missing/duplicate/unexpected hours, malformed weather/JSON, HTTP errors/429,
timeouts, body limits, and failures after earlier successful chunks. Explicit
planner fault injection verifies final rejection of gaps/duplicates/overlaps;
normal operation uses the unchanged real planner. No existing test was weakened.

| Coverage scope | Statements | Branches |
| --- | --- | --- |
| `ingestion/source_pipeline.py` | 46/46 (100%) | 4/4 (100%) |
| Whole implemented package | 513/513 (100%) | 188/188 (100%) |

Coverage is measured code-path execution, not proof of all inputs or live API
compatibility. Ruff lint passes; `ruff format --check .` reports 37 files already
formatted in this checkout. An initial B007 diagnostic was corrected by naming
the loop index `_chunk_index`, which is consumed by the surrounding error handler;
no lint rule was disabled. Offline lock verification and installed dependency
compatibility checks pass for 18 packages. Lock SHA-256 remains
`820DA87A498095A57895A38548EB46C4D07AB7ECE3B64FA9B21E0D9ED3A86390`.

Commands executed from the repository root in PowerShell:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider -q
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider tests/unit/test_source_pipeline.py -q
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider -p pytest_cov --cov=hellas_dataops --cov-report=term-missing --cov-report=json:artifacts/coverage-2e.json -q
& ./.venv/Scripts/ruff.exe check .
& ./.venv/Scripts/ruff.exe format --check .
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:TEMP = Join-Path $PWD '.tools/tmp'
$env:TMP = $env:TEMP
& ./.tools/uv/bin/uv.exe --version
& ./.tools/uv/bin/uv.exe lock --check --offline
& ./.tools/uv/bin/uv.exe pip check --offline --python .venv/Scripts/python.exe
```

No distribution rebuild/install verification was requested or performed in 2E;
2D artifacts do not contain this new workflow and must be rebuilt before use.
No live-provider, database, performance, persistence, or deployment claim is made.
Socket-operation timeouts do not establish a whole-request, whole-pipeline, or
DNS deadline. The complete retained result accumulates in memory, with no total
range/row cap. Response hashes, clocks, raw-response replay, retry/backoff, durable
provenance, recovery, and grid-consistency policy remain future decisions.

Recommended Milestone 3A, requiring approval: design PostgreSQL keys and schema
for versioned requested cities/datasets, hourly observations, and per-chunk request
and returned-grid provenance. Define constraints, null/UTC semantics, transactional
idempotent upsert behavior, and minimal query indexes. Review driver/migration
dependencies and a disposable local PostgreSQL test setup before installation or
service startup. Preserve the offline default suite; database tests must be an
explicit separate selection. HTTP ingestion scheduling and API routes stay out.

## Milestone 3A: schema and migration foundation

Verified on 2026-10-22 (Europe/Athens), Windows x64 / CPython **3.14.4**, using
project-local uv **0.13.4**. The initial gate passed: `main` was clean at
`00f23cdeacab8d610630a08e86dfee7493945240`, the committed Milestone 2E checkpoint.
Its exact Git inventory confirms the two created files and six modified files
listed in the 2E section above. This verification uses a real commit, not an
invented diff from the earlier uncommitted repository. No Git metadata is written
in 3A; this milestone remains uncommitted for owner review.

The baseline **566 tests passed**. The new suite passes **610 tests, 0 failures**,
including **44 new storage/migration cases**. Domain, planner, normalizer, HTTP
client, source workflow, existing tests, and socket/DNS guard are unchanged.
Tests check PostgreSQL metadata/DDL, frozen migration parity, upgrade/downgrade
SQL emission, explicit configuration failures, and engine-free execution. They
do not execute SQL or establish database constraint enforcement.

| New storage code | Covered statements | Covered branches |
| --- | --- | --- |
| `storage/schema.py` | 15/15 | No branches |
| `storage/migration.py` | 18/18 | 4/4 |
| `storage/migrations/env.py` | 3/3 | No branches |
| `storage/migrations/versions/20261022_0001_initial_schema.py` | 21/21 | No branches |
| `storage/__init__.py` | 2/2 | No branches |
| Whole application package | 572/572 | 192/192 |

Coverage is 100% of measured statements/branches, not proof of all inputs or
database behavior. The initial package-name coverage selection missed dynamically
loaded Alembic files despite their tests running. Coverage now selects the source
directory; no code or branches are excluded to improve the result. The initial
revision rendering contained an unqualified `Text()` argument in JSONB metadata;
lint and SQL generation caught it, and it was corrected to `sa.Text()`. Import
ordering and formatting diagnostics were corrected without disabling checks.

### Dependencies and Python compatibility

Runtime additions resolved through the standard package index:

| Package | Locked version | Role |
| --- | --- | --- |
| SQLAlchemy | 2.1.6 | PostgreSQL Core metadata and SQL compilation |
| Alembic | 1.20.0 | Packaged, versioned offline migration tooling |
| Mako | 1.4.3 | Alembic revision template dependency |
| MarkupSafe | 3.0.4 | Mako dependency |

Existing locked package versions are unchanged. Runtime and development groups
remain separate; no ORM entities, async extra, greenlet, or database driver is
introduced. SQLAlchemy declares Python >=3.11 and Alembic >=3.10; the actual
resolved set is tested here only on Windows x64 / CPython 3.14.4. The declared
project interpreter range remains >=3.14,<3.15.

Compatibility review used [SQLAlchemy's published package](https://pypi.org/project/SQLAlchemy/),
[Alembic's package](https://pypi.org/project/alembic/), and installed metadata.
[Psycopg binary 3.3.6](https://pypi.org/project/psycopg-binary/3.3.6/) publishes a
`cp314-cp314-win_amd64` wheel. Its [installation documentation](https://www.psycopg.org/psycopg3/docs/basic/install.html)
distinguishes the self-contained binary extra from pure Python (requires local
libpq) and local C builds (also require build tools). Psycopg was reviewed, not
installed or locally tested. Select and lock a driver only with 3B approval.

The authorized dependency update used these session-local settings and command:

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_LINK_MODE = 'copy'
$env:TEMP = Join-Path $PWD '.tools/tmp'
$env:TMP = $env:TEMP
& ./.tools/uv/bin/uv.exe add 'sqlalchemy>=2.1.5,<2.2' 'alembic>=1.20,<2' --default-index https://pypi.org/simple
```

Do not repeat `uv add` for routine setup. Use the existing reviewed lock. Offline
verification and builds use the populated cache; a cold machine needs approved
package-index access. No global environment/configuration changes are required.

### Quality and offline migration commands

Run from the repository root, stopping on any command failure:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider tests/unit/test_storage_schema.py tests/unit/test_migrations.py -q
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider -p pytest_cov --cov=src/hellas_dataops --cov-report=term-missing --cov-report=json:artifacts/coverage-3a.json -q
& ./.venv/Scripts/ruff.exe check .
& ./.venv/Scripts/ruff.exe format --check .
$lockBefore = (Get-FileHash uv.lock -Algorithm SHA256).Hash
& ./.tools/uv/bin/uv.exe lock --check --offline
& ./.tools/uv/bin/uv.exe sync --locked --offline
& ./.tools/uv/bin/uv.exe pip check --offline --python .venv/Scripts/python.exe
if ($lockBefore -ne (Get-FileHash uv.lock -Algorithm SHA256).Hash) { throw 'Lockfile changed' }
& ./.venv/Scripts/python.exe -I -B -m alembic -c alembic.ini upgrade head --sql
& ./.venv/Scripts/python.exe -I -B -m alembic -c alembic.ini downgrade 20261022_0001:base --sql
```

The single base/head revision is `20261022_0001`. It contains frozen, reviewed
operations, independent of current metadata. Initial operations were rendered
from the in-memory schema without database autogeneration, then reviewed and
compared in tests. Upgrade SQL includes five application tables plus Alembic's
version table; downgrade SQL is dependency ordered. Neither command connects to
a database. Commands without `--sql`, or a configured database URL, deliberately
fail. See [the storage contract](storage-schema.md) for schema and mapping details.

### Distribution verification procedure

Ruff lint passes; formatting reports **46 files already formatted**. Offline
lock verification, locked sync, and compatibility checks pass for **22 development
environment packages**. Locked sync leaves the resolved lock unchanged, SHA-256
`BE8EBABF44AFFDCBB13A08D5A091DEF58CA9BE90BC8CD2E0F94831FD1B4E8A36`.

Both source distribution and wheel built successfully. Archive checks verified
**56 source assets**, including **3 JSON fixtures and 6 documentation files**,
and **22 wheel modules plus the Alembic template**. Both fresh isolated installs
passed **25 imports**, offline migration generation, and dependency checks for
**12 runtime environment packages**. Generated SQL/build/coverage outputs and
verification environments remain ignored. No forbidden archive paths were found.

The wheel packages all runtime modules, Alembic environment/revision/template,
and README metadata. The source archive additionally contains the root INI,
documentation, tests, three synthetic JSON fixtures, and lockfile. Archive checks
compare required files byte-for-byte and reject environment/cache/private paths.
Fresh installations check 25 isolated imports and installed offline migration
generation, in addition to configuration, planning, and mocked HTTP smoke checks.
This is not a byte-for-byte reproducible-build guarantee or PostgreSQL test.

The exact procedure uses fresh local directories; choose new names for a repeat
rather than overwriting an existing verification environment:

```powershell
function Invoke-Checked {
    $command, $arguments = $args
    & $command @arguments
    if ($LASTEXITCODE -ne 0) { throw "Verification command failed: $command" }
}
Invoke-Checked ./.tools/uv/bin/uv.exe build --offline --no-build-isolation --python .venv/Scripts/python.exe --out-dir dist/milestone-3a
Invoke-Checked ./.venv/Scripts/python.exe -I -B scripts/verify_distributions.py dist/milestone-3a
Invoke-Checked ./.tools/uv/bin/uv.exe export --locked --offline --no-dev --no-emit-project --format requirements.txt --output-file .tools/runtime-requirements-3a.txt | Out-Null
foreach ($kind in @('wheel', 'sdist')) {
    $envPath = ".tools/$kind-check-3a"
    if (Test-Path $envPath) { throw 'Use a fresh verification directory' }
    Invoke-Checked ./.tools/uv/bin/uv.exe venv $envPath --python .venv/Scripts/python.exe --no-managed-python --offline
    $checkPython = "$envPath/Scripts/python.exe"
    Invoke-Checked $checkPython -I -B -c "import importlib.util as u; assert all(u.find_spec(n) is None for n in ['hellas_dataops','pydantic','pytest','sqlalchemy','alembic']); print('Fresh environment confirmed')"
    Invoke-Checked ./.tools/uv/bin/uv.exe pip sync --python $checkPython --offline --require-hashes .tools/runtime-requirements-3a.txt
    $package = if ($kind -eq 'wheel') { 'dist/milestone-3a/hellas_dataops-0.1.0.dev0-py3-none-any.whl' } else { 'dist/milestone-3a/hellas_dataops-0.1.0.dev0.tar.gz' }
    Invoke-Checked ./.tools/uv/bin/uv.exe pip install --python $checkPython --offline --no-deps $package
    Invoke-Checked $checkPython -I -B scripts/verify_installed.py
    Invoke-Checked ./.tools/uv/bin/uv.exe pip check --offline --python $checkPython
}
```

### Limits and proposed Milestone 3B

No PostgreSQL server/driver, live weather calls, transactional writes, endpoints,
services, or deployment were introduced. Online migration execution remains
intentionally blocked. The schema defines row-level constraints, not a complete
cross-row validation or immutable-configuration enforcement mechanism.

With separate approval, 3B should choose a disposable PostgreSQL setup and driver,
add guarded opt-in integration tests, run upgrade/downgrade/re-upgrade, and verify
real NULL/NaN/infinity/UTC/foreign-key behavior. Add explicit domain-to-storage
mapping and chunk transactions with idempotent writes, conflict handling, atomic
completion, and injected failure/recovery tests. Preserve the offline default
suite and keep HTTP retrieval outside transactions. No service may be started
merely to run the default tests. Whole-request/DNS deadlines, upstream service
eligibility, raw-response retention, and a repository code licence remain separate
unresolved decisions. Do not start 3B without owner approval.

## Milestone 3B.1: host-assisted PostgreSQL verification

Historical initial Psycopg implementation record. The pg8000 remediation section
below supersedes its driver selection, setup commands, counts, and limitations.

This section supersedes the historical 3A limitations above. Implementation is
prepared, but real PostgreSQL verification is **pending**. The repository was
clean on `main` at `6a0439522cd9d1bd17005850a5b718730ef44c28` before these edits.
No Git metadata was written. The existing domain, source pipeline, Core schema,
initial revision `20261022_0001`, and socket/DNS test guard remain unchanged.

The new guarded migration API accepts only an explicit disposable test target.
The host runner creates fresh uniquely named Compose resources and verifies
server identity before writes. See [PostgreSQL verification](postgres-testing.md)
for the single host command, connection safeguards, migration procedures,
reports, and cleanup boundaries. No container or database was created from Codex.

### Dependencies and environment

Verified interpreter: CPython **3.14.4, Windows AMD64**, local uv **0.13.4**.
Added **psycopg 3.3.6**, **psycopg-binary 3.3.6**, and its timezone dependency
**tzdata 2026.5** using the standard package index:

```powershell
& ./.tools/uv/bin/uv.exe add 'psycopg[binary]>=3.3.6,<3.4' --default-index https://pypi.org/simple
```

The lock was genuinely resolved; every pre-existing package version is unchanged.
SQLAlchemy **2.1.6**, Alembic **1.20.0**, pytest **9.1.1**, pytest-cov **7.1.0**,
Ruff **0.17.1**, and setuptools **84.2.0** remain installed. Metadata resolution
and compatibility checks pass for **25 development-environment packages**.

The available CPython 3.14 Windows x64 binary wheel installed, but **native
Psycopg loading failed with an Application Control policy error inside Codex**.
Installation and matching wheel tags do not prove operational compatibility.
The host runner performs an actual driver import before any Docker operation.
If it also fails on the host, stop for a supported policy/installation review;
do not change Windows security controls or install alternate binaries to evade it.

### Checks executed in Codex

The complete offline suite passes **676 tests, 0 failures**: the 610-test baseline
plus 66 new configuration/host-runner tests. Package coverage is **662/662
statements and 218/218 branches (100%)**. The new database safety module covers
**74/74 statements and 22/22 branches**; migration orchestration covers **34/34
statements and 8/8 branches**. Mocked host-runner tests separately cover **131/137
statements (95.6%) and 29/34 branches (85.3%)** in `scripts/verify_postgres.py`.
These mocks verify safety/control flow, not Docker or database correctness.

Ruff lint passes; formatting checks **53 Python files**. Offline lock checking,
locked sync, and dependency compatibility pass without changing the lock hash:
`1580C986BDD76D5A57BEF263E76B2A376407B88EDCC790370681222CB306F8BC`.
Client-side Compose validation and PowerShell syntax parsing pass without engine
access. The actual host-runner preflight was exercised in Codex and safely stopped
at `psycopg-driver-import`, before any Docker command or credential generation.

**52 PostgreSQL cases are collected but have not run against a database.**
The opt-in gate rejects their execution without `--postgres-config`. Upgrade,
downgrade, re-upgrade, real constraints, and TIMESTAMPTZ behavior remain unverified
until the host run passes. Review of installed Alembic confirmed that downgrade
to base retains an empty `alembic_version` table; the integration assertion
expects that behavior, rather than incorrectly requiring no tables whatsoever.
No schema defect or real database result is claimed from this review.

Run quality checks from the repository root, stopping on nonzero exit codes:

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_LINK_MODE = 'copy'
$env:TEMP = Join-Path $PWD '.tools/tmp'
$env:TMP = $env:TEMP
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider -p pytest_cov --cov=src/hellas_dataops --cov-report=term-missing --cov-report=json:artifacts/coverage-3b1.json -q
& ./.venv/Scripts/ruff.exe check .
& ./.venv/Scripts/ruff.exe format --check .
$lockBefore = (Get-FileHash uv.lock -Algorithm SHA256).Hash
& ./.tools/uv/bin/uv.exe lock --check --offline
& ./.tools/uv/bin/uv.exe sync --locked --offline
& ./.tools/uv/bin/uv.exe pip check --offline --python .venv/Scripts/python.exe
if ($lockBefore -ne (Get-FileHash uv.lock -Algorithm SHA256).Hash) { throw 'Lockfile changed' }
```

### Distribution procedure

Both distributions built successfully. Archive verification compared **65 source
assets**, including **3 JSON fixtures and 7 documentation files**, and **23 wheel
modules plus the Alembic template** byte-for-byte. Compose and both host scripts
are included in the source distribution; local configuration, secrets, reports,
environments, and caches are excluded. Both fresh isolated installations passed
**26 imports**, offline migration generation, configuration/planning/mocked HTTP
smoke checks, and compatibility checks for **15 runtime-environment packages**.
These results do not include native Psycopg loading or any database connection.

Use fresh project-local verification directories; do not overwrite an existing
environment. `Invoke-Checked` is defined in the historical distribution procedure
above and stops on any command failure. These checks intentionally import storage
without connecting or importing the blocked native driver. Driver loading has a
separate, failed verification result; fresh package installation cannot override it.

```powershell
Invoke-Checked ./.tools/uv/bin/uv.exe build --offline --no-build-isolation --python .venv/Scripts/python.exe --out-dir dist/milestone-3b1
Invoke-Checked ./.venv/Scripts/python.exe -I -B scripts/verify_distributions.py dist/milestone-3b1
Invoke-Checked ./.tools/uv/bin/uv.exe export --locked --offline --no-dev --no-emit-project --format requirements.txt --output-file .tools/runtime-requirements-3b1.txt | Out-Null
foreach ($kind in @('wheel', 'sdist')) {
    $envPath = ".tools/$kind-check-3b1"
    if (Test-Path $envPath) { throw 'Use a fresh verification directory' }
    Invoke-Checked ./.tools/uv/bin/uv.exe venv $envPath --python .venv/Scripts/python.exe --no-managed-python --offline
    $checkPython = "$envPath/Scripts/python.exe"
    Invoke-Checked $checkPython -I -B -c "import importlib.util as u; assert all(u.find_spec(n) is None for n in ['hellas_dataops','pydantic','pytest','sqlalchemy','alembic','psycopg']); print('Fresh environment confirmed')"
    Invoke-Checked ./.tools/uv/bin/uv.exe pip sync --python $checkPython --offline --require-hashes .tools/runtime-requirements-3b1.txt
    $package = if ($kind -eq 'wheel') { 'dist/milestone-3b1/hellas_dataops-0.1.0.dev0-py3-none-any.whl' } else { 'dist/milestone-3b1/hellas_dataops-0.1.0.dev0.tar.gz' }
    Invoke-Checked ./.tools/uv/bin/uv.exe pip install --python $checkPython --offline --no-deps $package
    Invoke-Checked $checkPython -I -B scripts/verify_installed.py
    Invoke-Checked ./.tools/uv/bin/uv.exe pip check --offline --python $checkPython
}
```

Milestone 3B.1 can be declared complete only after reviewing successful real host
results. Milestone 3B.2 requires separate approval for domain/storage mappings,
transactional idempotent chunk writes, lineage/correction policy, concurrency,
and atomic failure/recovery behavior. No application persistence was implemented.

## Milestone 3B.1 driver remediation: pg8000

Historical remediation record; the final acceptance section below supersedes
its pending host-verification statements. The preceding Psycopg record is historical.
The owner-host report and Codex preflight stopped at `psycopg-driver-import`.
A controlled local import reproduced `ImportError` with an Application Control
DLL rejection. This establishes a native-loading policy failure, not an SQL,
authentication, or migration failure; it does not identify which DLL or policy
rule was responsible. No security settings, file permissions, or policies changed.

The approved alternative **pg8000 1.31.5** now imports successfully on **CPython
3.14.4 Windows AMD64**. SQLAlchemy **2.1.6**, Alembic **1.20.0**, uv **0.13.4**,
and all pre-existing non-driver package versions remain unchanged. Psycopg and
psycopg-binary are removed from the lock and active environment. Added dependency
versions are pg8000 1.31.5, scramp 1.4.17, asn1crypto 1.5.1, python-dateutil 3.0.0,
and six 1.17.0. Existing tzdata 2026.5 remains required by python-dateutil.
All six packages in this driver's dependency closure have pure-Python wheel
metadata and contain no `.pyd`, `.dll`, or `.so` files. Existing unrelated native
dependencies are unchanged.

Official references and compatibility limits are in
[PostgreSQL testing](postgres-testing.md). Local tests exercise real driver
imports, SQLAlchemy dialect construction, DBAPI argument validation, bound-value
conversion, UTC timestamp codecs, and decoding of synthetic server error fields.
They do not simulate successful PostgreSQL enforcement or establish real database
transaction correctness. The host suite is still required.

### Minimal implementation changes

- Use `postgresql+pg8000`, `timeout`, `ssl_context`, and `startup_params` instead
  of Psycopg/libpq-specific options. Preserve numeric loopback target validation,
  ownership verification, timeouts, UTC, public search path, and migration guards.
- Preflight imports the actual pg8000 driver and SQLAlchemy dialect before Docker.
  The existing PowerShell entry point is unchanged and invokes the updated runner.
- Integration tests alone permit their configured numeric localhost port. DNS and
  other destinations remain blocked; default offline networking protection is unchanged.
- Constraint assertions require the exact server SQLSTATE and constraint name from
  pg8000's `DatabaseError` payload. No broad success-on-any-error behavior is used.
- Add real-database tests for binding/session settings and savepoint rollback.
  Preserve all 52 original PostgreSQL cases, schema definitions, and initial revision.

### Verification and exact commands

**699 offline tests pass, 0 failures**, including 23 new remediation tests. Package
coverage remains **662/662 statements and 218/218 branches (100%)**. Database safety
configuration covers **74/74 statements and 22/22 branches**; migration code covers
**34/34 statements and 8/8 branches**. Ruff lint and formatting pass (54 Python
files). The **54 PostgreSQL cases are collected only; none has run on a server**.
Client-side Compose validation and PowerShell syntax checks pass without Docker
engine access. No container or database was created during remediation.

Offline lock checking, locked synchronization, and compatibility pass for **28
development-environment packages**, without changing lock SHA-256
`576C2E67E36C516B2A763BD5BCF199525ED89875BDC7A382178779379EA52AB9`.

Separate offline coverage of the host runner is **136/140 statements (97.1%) and
29/34 branches (85.3%)**. The integration network restriction helper and its
callbacks have full statement/branch coverage; integration fixture lifecycle
execution remains pending real PostgreSQL. Both distributions build and pass
byte-for-byte checks for **66 source assets**, **3 JSON fixtures**, **7 documentation
files**, and **23 wheel modules plus the Alembic template**. Both fresh isolated
installations pass **32 imports**, including the real pg8000 driver and dependencies,
dialect construction, offline migration/smoke checks, and compatibility checks for
**18 runtime-environment packages**. No PostgreSQL connection is implied by these
installation results. Archive checks exclude local secrets and generated artifacts.

Run commands from the repository root, stopping on any failure. The first install
attempt resolved packages but failed to build the editable project because its
default temporary directory was not writable. Using the already-approved
project-local temporary directory resolved that build-path issue; no permission
or security setting was changed.

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_LINK_MODE = 'copy'
$env:TEMP = Join-Path $PWD '.tools/tmp'
$env:TMP = $env:TEMP
# Already executed dependency migration; do not repeat during routine verification:
# ./.tools/uv/bin/uv.exe add 'pg8000>=1.31.5,<1.32' --default-index https://pypi.org/simple
# ./.tools/uv/bin/uv.exe remove psycopg --offline
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider -p pytest_cov --cov=src/hellas_dataops --cov-report=term-missing --cov-report=json:artifacts/coverage-pg8000.json -q
& ./.venv/Scripts/ruff.exe check .
& ./.venv/Scripts/ruff.exe format --check .
& ./.tools/uv/bin/uv.exe lock --check --offline
& ./.tools/uv/bin/uv.exe sync --locked --offline
& ./.tools/uv/bin/uv.exe pip check --offline --python .venv/Scripts/python.exe
& ./.venv/Scripts/python.exe -I -B -c "import pg8000, sqlalchemy, alembic; print(pg8000.__version__, sqlalchemy.__version__, alembic.__version__)"
```

Build/install verification uses the earlier `Invoke-Checked` helper and fresh
directories, with `UV_*` and temporary-directory settings above:

```powershell
Invoke-Checked ./.tools/uv/bin/uv.exe build --offline --no-build-isolation --python .venv/Scripts/python.exe --out-dir dist/milestone-3b1-pg8000
Invoke-Checked ./.venv/Scripts/python.exe -I -B scripts/verify_distributions.py dist/milestone-3b1-pg8000
Invoke-Checked ./.tools/uv/bin/uv.exe export --locked --offline --no-dev --no-emit-project --format requirements.txt --output-file .tools/runtime-requirements-pg8000.txt | Out-Null
foreach ($kind in @('wheel', 'sdist')) {
    $envPath = ".tools/$kind-check-pg8000"
    if (Test-Path $envPath) { throw 'Use a fresh verification directory' }
    Invoke-Checked ./.tools/uv/bin/uv.exe venv $envPath --python .venv/Scripts/python.exe --no-managed-python --offline
    $checkPython = "$envPath/Scripts/python.exe"
    Invoke-Checked $checkPython -I -B -c "import importlib.util as u; assert all(u.find_spec(n) is None for n in ['hellas_dataops','pg8000','sqlalchemy','alembic']); print('Fresh environment confirmed')"
    Invoke-Checked ./.tools/uv/bin/uv.exe pip sync --python $checkPython --offline --require-hashes .tools/runtime-requirements-pg8000.txt
    $package = if ($kind -eq 'wheel') { 'dist/milestone-3b1-pg8000/hellas_dataops-0.1.0.dev0-py3-none-any.whl' } else { 'dist/milestone-3b1-pg8000/hellas_dataops-0.1.0.dev0.tar.gz' }
    Invoke-Checked ./.tools/uv/bin/uv.exe pip install --python $checkPython --offline --no-deps $package
    Invoke-Checked $checkPython -I -B scripts/verify_installed.py
    Invoke-Checked ./.tools/uv/bin/uv.exe pip check --offline --python $checkPython
}
```

Run `& .\scripts\verify-postgres.ps1` in normal host PowerShell for real PostgreSQL
verification. Docker named-pipe restrictions inside Codex remain unchanged and
were not retried or escalated. Review the generated sanitized report before
declaring 3B.1 complete. Milestone 3B.2 remains unimplemented and requires approval.

## Milestone 3B.1 final acceptance

**ACCEPTED on 2026-10-22.** There are no remaining acceptance blockers for the
schema/migration/integration scope. This acceptance does not authorize Milestone
3B.2 or claim application ingestion, idempotency, recovery, or production readiness.
Only documentation changed during this acceptance review; tested runtime code,
schema, migration revision, dependency lock, and test assertions were preserved.

### Actual host evidence

Reviewed local evidence directory:
`artifacts/postgres/hellas-dataops-test-2d8dbdd628c8/`.

| Evidence | Verified result |
| --- | --- |
| `report.json` | `status=passed`, `stage=complete`, `pytest_exit_code=0` |
| `pytest.txt` | 54 passed, zero failures/skips, 2.64 seconds |
| PostgreSQL | 18.6 (Debian 18.6-1.pgdg12+2) |
| Driver | postgresql+pg8000; pg8000 1.31.5 |
| SQLAlchemy / Alembic | 2.1.6 / 1.20.0 |
| Docker Engine / Compose | 29.7.2 / 5.4.0 |
| Official image | postgres:18.6-bookworm |
| Image ID | sha256:bbf83594798e144bff365a9c191c5d779f181fb3135902064ab8ff11918020fc |
| Host binding | 127.0.0.1:53584, container port 5432 |
| Initial Git checkpoint | 6a0439522cd9d1bd17005850a5b718730ef44c28 |

The report stores the pytest exit code explicitly. The reviewed runner reaches
`stage=complete` only after all checks and returns zero on that path; the owner
also reported no PowerShell error. A separate wrapper process exit-code field
was not recorded. No claim is made that this is a signed execution attestation.

SHA-256 evidence fingerprints (raw reports remain ignored):

```text
report.json  a149151d5be3a4690a2718af53296d1865f2800438f0c7a6f425d861264fe679
pytest.txt   352f3a4d517acd3df0babfc4e93d77cdb8a8c762624108ef51ecd16a1b15a4be
coverage.json fd64f7c6ce45680e64bb850c3531b7cea0aa3f98a679b97bea0926dfe5e1a046
```

The real session fixture required an empty, verified disposable database; upgraded
to `20261022_0001`; checked six tables including Alembic's version table; downgraded
to base; verified only the empty version table remained; and re-upgraded. Session
teardown verified empty application tables and the installed revision. Every
constraint test used a real pg8000 connection with transaction rollback. There is
no mocked database or SQLite fallback in this integration suite.

The 54 cases cover catalog/metadata agreement (types, nullable flags, keys, checks,
and nonredundant indexes), duplicate city/version and dataset identities, distinct
coordinate versions, duplicate hourly keys, all-null measurements, numerical
bounds including NaN/infinities, foreign keys and mismatched run lineage, interval
order/alignment, metadata rules, UTC instants under three session timezones,
ownership/populated-table/unknown-table guards, parameter binding, session settings,
and savepoint rollback. Metadata agreement alone is not interpreted as enforcement
of every conceivable CHECK/input combination.

### Isolation review

The accepted runner checks Linux Engine availability and health, refuses resource
name collisions, validates labels/image/mounts/network and localhost-only mapping,
and verifies database/user/version plus a per-run ownership marker before writes.
All Docker mutations target the unique `hellas-dataops-test-2d8dbdd628c8` project;
there are no commands to start, stop, delete, or reset EREN or other project
resources. No cleanup was performed, and no Docker access was attempted during
this review. Source review and successful guard execution support this isolation
conclusion; no global before/after Docker inventory was captured, so unrelated
activity by other host processes cannot be independently audited from this report.

### Coverage and repeated quality checks

The host's rounded **88%** is combined statement/branch coverage:
**135/149 statements (90.60%) + 22/30 branches (73.33%) = 157/179 (87.71%)**.
All initial revision operations and schema-definition statements executed.
The 14 missing statements are offline SQL generation and rejection/error paths:
arbitrary migration URL, missing explicit configuration, invalid identifiers or
credentials, duplicate/malformed JSON, wrong target object, and connection failure.
These paths are covered in the offline suite, rather than by successful host setup.

The acceptance rerun passed **699 offline tests, zero failures**, in 2.54 seconds,
with **662/662 package statements and 218/218 branches (100%)**. Ruff lint passed,
and format checking reported 54 files already formatted. Offline `uv lock --check`
and `uv pip check` passed for 28 installed packages; lock SHA-256 remains
`576C2E67E36C516B2A763BD5BCF199525ED89875BDC7A382178779379EA52AB9`.
Python is 3.14.4; uv 0.13.4; pytest 9.1.1; pytest-cov 7.1.0; Ruff 0.17.1.
Before documentation updates, the existing distribution verifier confirmed all
66 source assets and 23 wheel modules matched the reviewed working files exactly.
The prior fresh-installation checks remain valid for unchanged runtime code;
the old archives contain pre-acceptance documentation and should be rebuilt for
any future release. No release or publication is part of this checkpoint.

Actual database connection-loss/timeout behavior, crash-interrupted DDL,
concurrency, and production role/TLS configuration remain untested. TIMESTAMPTZ
preserves instants, not the original caller's offset; UTC input requirements remain
domain rules. The explicit image tag can change upstream; the observed image ID
is recorded above. Retained test resources consume local disk/memory until the
owner separately reviews cleanup. These are nonblocking limitations for 3B.1.

### Complete 3B.1 checkpoint inventory

Relative to the initial 3A commit: **15 modified and 10 new files**, none deleted.

```text
Modified:
AGENTS.md
MANIFEST.in
README.md
alembic.ini
docs/architecture.md
docs/data-contract.md
docs/development.md
docs/storage-schema.md
pyproject.toml
scripts/verify_distributions.py
scripts/verify_installed.py
src/hellas_dataops/storage/migration.py
src/hellas_dataops/storage/migrations/env.py
tests/unit/test_migrations.py
uv.lock

New:
compose.postgres-test.yaml
docs/postgres-testing.md
scripts/verify-postgres.ps1
scripts/verify_postgres.py
src/hellas_dataops/storage/test_database.py
tests/integration/conftest.py
tests/integration/test_postgres.py
tests/unit/test_pg8000.py
tests/unit/test_postgres_runner.py
tests/unit/test_test_database.py
```

Git metadata is read-only in Codex. No metadata writes or escalation were attempted.
An ignored owner-side `artifacts/checkpoint-3b1.ps1` and SHA-256 manifest prepare
one local commit, validating root, main branch, baseline HEAD, clean index,
approved noreply author/committer, exact reviewed working files, staged inventory,
and cached whitespace checks. The script neither changes Git identity nor pushes.
Run it from normal PowerShell only after reviewing this acceptance record:

```powershell
& .\artifacts\checkpoint-3b1.ps1
```

After that commit, the host verification runner's frozen 3A HEAD gate intentionally
requires review before another database run. Update it within the next authorized
milestone rather than bypassing it. Recommended 3B.2 scope: explicit domain/storage
mappings, idempotent transactional chunk writes, correction/lineage policy, atomic
completion, concurrency, and injected failure/recovery tests on disposable
PostgreSQL. No live weather calls, API endpoints, or deployment are implied.

## Milestone 3B.2 implementation record (before host acceptance)

This section records the earlier offline implementation handoff. Its pending-host
statements are superseded by the final acceptance record below.

Baseline verified on 2026-10-22: main at
`779313504a658ea5e7768c5e98f420944d47e4ea`, initially clean. The accepted host
3B.1 report remains historical evidence: 54 PostgreSQL tests, server 18.6,
pg8000 1.31.5, pytest exit code zero. No new Docker access was attempted from
Codex; its named-pipe restriction remains in force.

New `storage.mapping` and `storage.persistence` modules implement the
[chunk write contract](persistence.md). No dependency, schema, migration, domain,
source adapter or source-pipeline changes are made. Python remains 3.14.4,
SQLAlchemy 2.1.6, Alembic 1.20.0, pg8000 1.31.5 and project-local uv 0.13.4.

The offline suite passed **750 tests** (699 regressions plus 51 new tests), with
the network guard intact. Mapping coverage is **82/82 statements, 22/22 branches**.
Persistence orchestration coverage offline is **51/95 statements, 18/38 branches**,
51.88% combined. Missing paths are primarily actual run/chunk/weather SQL writes;
they belong to the pending PostgreSQL run, not an in-memory database substitute.
Overall package coverage is **795/839 statements, 258/278 branches**, 94.27%
combined. Previously accepted runtime modules retain full offline coverage.

An initial invocation without the project-local TEMP/TMP settings produced five
existing temporary-directory fixture errors (740 tests passed). Re-running with
the documented settings below resolved them without changing tests or policies.

The opt-in suite **collects 83 tests: 54 accepted schema/driver cases and 29 new
persistence cases**. Collection is not execution. None of the new database cases
has run in Codex. They exercise committed writes, mixed counts, overlapping
intervals, corrections/NULLs, lineage, input drift, rollback/retry, full chunk
plans, independent city series and concurrent writers with an observed server
lock wait. The unchanged session migration lifecycle runs before these cases.

Commands used from the project root:

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_LINK_MODE = 'copy'
$env:TEMP = Join-Path $PWD '.tools/tmp'
$env:TMP = $env:TEMP
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider -p pytest_cov --cov=src/hellas_dataops --cov-report=term-missing --cov-report=json:artifacts/coverage-3b2-offline.json -q
& ./.venv/Scripts/ruff.exe check .
& ./.venv/Scripts/ruff.exe format --check .
& ./.tools/uv/bin/uv.exe lock --check --offline
& ./.tools/uv/bin/uv.exe pip check --offline --python .venv/Scripts/python.exe
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider tests/integration --postgres-config .tools/not-executed.json --collect-only -q
```

The last command deliberately performs collection only and never reads a test
target or connects to a database. The real host command is:

```powershell
& .\scripts\verify-postgres.ps1
```

The runner's frozen HEAD gate now uses the reviewed 3B.1 commit. Its permitted
changes are limited to 3B.2 paths; schema/migration/dependency modifications fail
preflight. Existing resource isolation, localhost binding, driver preflight,
bounded startup, sanitized reports and non-destructive resource retention remain.
Do not reuse the older checkpoint script for this milestone or commit these
changes without separate authorization.

Distribution verification commands (use the `Invoke-Checked` helper above to
stop immediately on any failed command):

```powershell
Invoke-Checked ./.tools/uv/bin/uv.exe build --offline --no-build-isolation --python .venv/Scripts/python.exe --out-dir dist/milestone-3b2
Invoke-Checked ./.venv/Scripts/python.exe -I -B scripts/verify_distributions.py dist/milestone-3b2
Invoke-Checked ./.tools/uv/bin/uv.exe export --locked --offline --no-dev --no-emit-project --format requirements.txt --output-file .tools/runtime-requirements-3b2.txt | Out-Null
foreach ($kind in @('wheel', 'sdist')) {
    $envPath = ".tools/$kind-check-3b2-verified"
    if (Test-Path $envPath) { throw 'Use a fresh verification directory' }
    Invoke-Checked ./.tools/uv/bin/uv.exe venv $envPath --python .venv/Scripts/python.exe --no-managed-python --offline
    $checkPython = "$envPath/Scripts/python.exe"
    Invoke-Checked $checkPython -I -B -c "import importlib.util as u; assert all(u.find_spec(n) is None for n in ['hellas_dataops','pg8000','sqlalchemy','alembic'])"
    Invoke-Checked ./.tools/uv/bin/uv.exe pip sync --python $checkPython --offline --require-hashes .tools/runtime-requirements-3b2.txt
    $package = if ($kind -eq 'wheel') { 'dist/milestone-3b2/hellas_dataops-0.1.0.dev0-py3-none-any.whl' } else { 'dist/milestone-3b2/hellas_dataops-0.1.0.dev0.tar.gz' }
    Invoke-Checked ./.tools/uv/bin/uv.exe pip install --python $checkPython --offline --no-deps $package
    Invoke-Checked $checkPython -I -B scripts/verify_installed.py
    Invoke-Checked ./.tools/uv/bin/uv.exe pip check --offline --python $checkPython
}
```

Acceptance remains pending the new sanitized host report, actual database test
counts and targeted coverage. Remaining limitations include broad per-city locking,
no whole-run transaction/deadline, ambiguous network failure during commit, no
automatic recovery and no production connection configuration. No live weather
requests, commits, pushes or next-milestone work are part of this change.

## Milestone 3B.2 final acceptance

**MILESTONE 3B.2: ACCEPTED** following the 2026-10-22 host evidence review and
fresh offline checks. Acceptance covers transactional chunk persistence and the
tested cooperating-writer scenarios, not a live or production ingestion platform.
No runtime, schema, migration or dependency changes were needed during this review.

### Real PostgreSQL evidence

The exact ignored local evidence directory is
`artifacts/postgres/hellas-dataops-test-309003857997/`:

- `report.json`: `status=passed`, `stage=complete`, `pytest_exit_code=0`.
- `pytest.txt`: **83 passed in 14.53 seconds**, no failures/skips/errors reported.
- `coverage.json`: targeted storage execution recorded on the real database run.
- Baseline: main at `779313504a658ea5e7768c5e98f420944d47e4ea`.
- PostgreSQL **18.6 (Debian 18.6-1.pgdg12+2)**; image `postgres:18.6-bookworm`.
- Image ID: `sha256:bbf83594798e144bff365a9c191c5d779f181fb3135902064ab8ff11918020fc`.
- Driver **pg8000 1.31.5**, dialect `postgresql+pg8000`; SQLAlchemy **2.1.6**,
  Alembic **1.20.0**, Python **3.14.4**. Docker Server **29.7.2**, Compose **5.4.0**.

Evidence SHA-256 values, recorded without credentials:

| File | SHA-256 |
| --- | --- |
| report.json | 66098e2c40fcc75b414d3c74f98599c82691dcd5c4fc10ff7491aa65f8a98506 |
| pytest.txt | f383671a4a41ac2d106e223407262fcbdda62d81f53c1b8ec84e49b2f8164dca |
| coverage.json | 1c3dae19cf5e055bf26e30a71d77b6aa71be66c1b2698af4bc61096e60cae906 |

The 83 cases comprise **54 existing schema/driver tests + 29 persistence tests**.
The reviewed runner executes the real integration subtree, not mocks or SQLite.
Its successful path requires Docker Linux Server access, resource ownership and
health checks, the exact loopback port **127.0.0.1:49326**, database/user
`hellas_dataops_test`, PostgreSQL major version 18 and the secret ownership marker.
Container, network and volume names use only the reported unique project prefix.
The session fixture checks a fresh database, upgrade to `20261022_0001`, downgrade
to base, re-upgrade, and final revision/empty application tables after test cleanup.
Passing teardown is included in the zero pytest exit code.

The runner's completed path returns zero. The report explicitly records pytest's
exit code; it does not contain a separate shell-process exit-code field. This
distinction does not change the successful evidence from the wrapper and tests.
The report is local execution evidence, not a signed build attestation. The
pre-review distributions matched all current source/test files byte-for-byte.

No command in the reviewed workflow modifies unrelated Docker resources. No
Docker operations were performed in this acceptance review. The evidence is not
a Docker-wide before/after inventory and does not establish what independent
processes did. Containers/volumes from the host run remain retained; none were
deleted. EREN or any other project was not accessed by this review.

### Demonstrated persistence guarantees and limits

- One transaction covers identities, run/chunk plan, weather, receipt and status.
  A return value is delivered only after successful context-manager commit.
- Identical completed-chunk replay leaves snapshots unchanged; new-run equal
  values keep original weather lineage. Grid-only changes do not rewrite weather.
- Default changed-value writes fail atomically. Explicit replacement requires a
  newer retrieval time; correction receipts preserve prior values and lineage,
  including NULL transitions. An old replay cannot undo a later correction.
- Exact mixed insert/update/unchanged counts and partial overlaps passed.
- A conflict after an earlier insert rolls back the entire chunk. An injected
  later-chunk failure preserves earlier committed chunks; retry succeeds after
  removing that fault. This is manual retry evidence, not automated recovery.
- City-configuration locks, then run/weather locks under READ COMMITTED, serialize
  cooperating writers. Four two-connection cases observe a real PostgreSQL lock
  wait and verify unchanged replay, conflict rejection or stale-refresh rejection.
- Out-of-order completion, 1/168/336/360-hour requests and five distinct city
  series pass. The 336-hour fixture starts at 05:00 UTC and therefore spans two
  provider calendar-day chunks; it is not a midnight-aligned single-chunk test.

HTTP retrieval is outside storage transactions. Canonical domain, source pipeline,
accepted schema, initial migration and dependency files are unchanged from HEAD.
Arbitrary SQL writers can bypass application policies. Row locking is broader
than an interval lock and serializes disjoint writes for the same city/version.
Freshness compares the current value origin, not every later unchanged retrieval.
No whole-run atomicity, process-crash test, lost-commit-acknowledgement test,
automatic retry/recovery, throughput benchmark or whole-operation deadline is
claimed. Production credentials/roles/TLS and non-disposable connections remain
out of scope. These are nonblocking limits for the accepted milestone.

### Coverage and quality

| Scope | Statements | Branches | Combined coverage |
| --- | --- | --- | --- |
| Host storage suite | 295/326 (90.49%) | 67/90 (74.44%) | 87.02% |
| Host persistence module | 89/95 (93.68%) | 32/38 (84.21%) | 90.98% |
| Fresh offline full package | 795/839 (94.76%) | 258/278 (92.81%) | 94.27% |
| Offline mapping module | 82/82 | 22/22 | 100% |
| Union of host/offline storage coverage | 325/326 | 89/90 | 99.52% |

The union is a line/branch-set calculation from the two JSON reports, not a new
combined test run. Most host gaps are intentionally offline input/configuration
rejections and offline SQL generation. The remaining unexecuted persistence path
rejects an externally inconsistent run/chunk status (`chunk_state`, line 173 in
the reviewed source). Normal service transitions do not produce that state. It is
a documented nonblocking defensive-test gap, not evidence of complete coverage.

Fresh verification: **750 offline tests passed in 2.69 seconds**, comprising 699
existing cases and 51 added in 3B.2; network blocking remained enabled. Ruff lint
and formatting passed (60 Python files), offline uv lock verification passed, and
all 28 installed development packages are compatible. The lockfile is unchanged.
Commands are those in the implementation record above, with acceptance coverage
written to `artifacts/coverage-3b2-acceptance-offline.json`. Build the reviewed
documentation with `uv build --offline --no-build-isolation --python
.venv/Scripts/python.exe --out-dir dist/milestone-3b2-accepted`, then run
`python -I -B scripts/verify_distributions.py dist/milestone-3b2-accepted` using the
documented project-local executables and temporary/cache settings.

### Exact milestone inventory and checkpoint

Created (6):

- `docs/persistence.md`
- `src/hellas_dataops/storage/mapping.py`
- `src/hellas_dataops/storage/persistence.py`
- `tests/integration/test_persistence.py`
- `tests/unit/test_persistence.py`
- `tests/unit/test_persistence_mapping.py`

Modified (10):

- `AGENTS.md`
- `README.md`
- `docs/architecture.md`
- `docs/data-contract.md`
- `docs/development.md`
- `docs/postgres-testing.md`
- `docs/storage-schema.md`
- `scripts/verify_installed.py`
- `scripts/verify_postgres.py`
- `tests/conftest.py`

The acceptance review updates only the eight instruction/documentation files in
that inventory; runtime code and tests retain the host-verified content. All 73
candidate repository files were checked for accidental credentials, personal
information, absolute user paths, private infrastructure and generated artifacts.
No blocking finding was identified. Synthetic fixtures, the lockfile and packaging
inputs remain included; secrets, environments, reports and distributions remain
ignored. Pattern scanning is a review aid, not a proof of absence of all secrets.

Git metadata is read-only in this Codex workspace. No Git write or escalation is
attempted. The ignored `artifacts/checkpoint-3b2.ps1` and hash-bound manifest prepare
one local checkpoint, checking main/HEAD, all reviewed file hashes, the exact
16-file change inventory, approved noreply author/committer, hooks and staged
blob identities before commit. They do not change identity/configuration or push.

Run once from normal PowerShell:

```powershell
& .\artifacts\checkpoint-3b2.ps1
```

The expected commit message is
`feat: add transactional idempotent PostgreSQL persistence`. Stop on any failed
guard; obtain a fresh review rather than editing the manifest or broadening staging.
After committing, the PostgreSQL runner's frozen baseline deliberately requires
review during the next authorized milestone. Do not rerun an older checkpoint.

### Recommended next milestone (not started)

Recommend **Milestone 3C.1: sequential operational ingestion and explicit resume**:
one small command/application coordinator joining existing planning, injected
retrieval, full normalization, retention and `persist_chunk`; explicit run UUID,
UTC interval, policy and safe status/count output. Test an interrupted later chunk,
restart with the same plan, skip validated completed chunks and finish remaining
ones without changing prior lineage. Define how canonical input/retrieval metadata
is retained for an ambiguous commit; do not silently reconstruct a different input
under a completed chunk identity. Use synthetic transport and the disposable
PostgreSQL target initially. No scheduler, worker queue or new schema by default.

After that, the remaining MVP work is a separately authorized bounded live-source
smoke test after usage-term review, intentional operational database configuration,
five-city 2025 backfills, and bounded read-only FastAPI endpoints with documentation
and tests. Basic structured run diagnostics and failure handling are MVP needs.
Scheduling, dashboards, cloud deployment, additional sources, distributed workers
and high-volume performance tuning remain optional enhancements. No next-milestone
implementation is authorized by this acceptance record.

## Milestone 3C.1: sequential synthetic ingestion and explicit resume

Implementation is complete for host verification, **not yet accepted**. Baseline
verified clean on main at `34a9c087adfa28c783dc8e319c6ca6c278b2e49e` before edits.
The existing 3B.2 report records 83 real PostgreSQL tests, exit zero, and server
18.6. Those results do not validate new lifecycle code. No Docker or live weather
operation was attempted in the sandbox, and no Git metadata was written.

See [ingestion.md](ingestion.md) for start/status/resume commands, status semantics,
transaction boundaries, correction policy, safe failure history and limitations.
No schema migration or dependency change is needed. The accepted domain, source
pipeline, mapping contract, initial revision, database target safeguards and
existing test files are unchanged. The Core writer shares internal identity/plan
helpers with lifecycle registration and preserves additive operational provenance.

### Verification performed

- **837 offline tests passed, zero failures**: 750 existing plus 87 new cases.
  Network/DNS blocking remains enabled. This run took 2.86 seconds; no performance
  claim is inferred from unit-test time.
- Ruff lint and formatting pass (68 Python files).
- Full-package combined statement/branch coverage: **90.75%** (1,050/1,142
  statements, 313/360 branches).
- Coordinator: **63/63 statements, 12/12 branches (100%)**.
- Run tracking: **132/178 statements, 36/62 branches (70% combined)**. SQL-backed
  state transitions, durable coverage queries and database failure paths require
  the pending host run. Offline mocks are not evidence of SQL correctness.
- Synthetic adapter: 19/19 statements; CLI: 39/40 statements and 7/8 branches.
  The CLI module launcher is separately checked with `python -m ... --help`.
- **109 PostgreSQL tests collected only**: 83 existing plus 26 new lifecycle cases.
  No new database test has been executed in Codex. The host command below must
  pass before acceptance. Its targeted coverage includes storage and coordinator.
- Offline lock verification and compatibility of all 28 installed development
  packages pass. `pyproject.toml` and `uv.lock` are unchanged.

Toolchain remains Python 3.14.4, uv 0.13.4, pg8000 1.31.5, SQLAlchemy 2.1.6,
Alembic 1.20.0, pytest 9.1.1, pytest-cov 7.1.0, Ruff 0.17.1, setuptools 84.2.0.
PostgreSQL 18.6 is the previously verified and host-pinned version, not a new
server measurement in this milestone.

Commands from repository root (PowerShell, no activation or PYTHONPATH):

```powershell
$env:TEMP = Join-Path $PWD '.tools/tmp'
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_LINK_MODE = 'copy'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& .\.venv\Scripts\python.exe -I -B -m pytest -p no:cacheprovider -p pytest_cov --cov=src/hellas_dataops --cov-report=term --cov-report=json:artifacts/coverage-3c1-offline.json -q
& .\.venv\Scripts\ruff.exe check .
& .\.venv\Scripts\ruff.exe format --check .
& .\.tools\uv\bin\uv.exe lock --check --offline
& .\.tools\uv\bin\uv.exe pip check --offline --python .venv/Scripts/python.exe
& .\.venv\Scripts\python.exe -I -B -m pytest tests/integration --postgres-config .tools/not-executed.json --collect-only -p no:cacheprovider -q
& .\.venv\Scripts\python.exe -I -B -m hellas_dataops.ingestion --help
& .\.tools\uv\bin\uv.exe build --offline --no-build-isolation --python .venv/Scripts/python.exe --out-dir dist/milestone-3c1
& .\.venv\Scripts\python.exe -I -B scripts/verify_distributions.py dist/milestone-3c1
```

Check `$LASTEXITCODE` after each command and stop on failure. Source distribution
verification compares every required source/test/document/script asset against
the working tree and checks wheel runtime content. Fixtures remain source-archive
assets; synthetic runtime execution does not depend on a checkout fixture path.
Fresh wheel/sdist environments use `.tools/verify-3c1-wheel` and
`.tools/verify-3c1-sdist`, the same established installation procedure above,
with `uv export --locked --offline --no-dev --no-emit-project --format
requirements.txt --output-file .tools/runtime-requirements-3c1.txt`, hashed
`uv pip sync`, offline `uv pip install --no-deps`, and `verify_installed.py`.
Both fresh installations passed 38 isolated imports and compatibility checks
for all 18 runtime packages including the project. Wheel and source builds
passed; archive verification matched 80 required source assets (3 JSON fixtures,
9 documentation files) and 29 wheel modules plus the Alembic template. No
forbidden generated/credential paths were included. The PowerShell wrapper parsed
successfully, and the host runner's real read-only repository gate accepted the
current baseline and exact 19-path inventory. The actual database phase remains
pending; the runner itself was not started in the sandbox.

### Exact implementation inventory

Created (8):

- `docs/ingestion.md`
- `src/hellas_dataops/ingestion/__main__.py`
- `src/hellas_dataops/ingestion/coordinator.py`
- `src/hellas_dataops/sources/synthetic.py`
- `src/hellas_dataops/storage/run_tracking.py`
- `tests/integration/test_operational_ingestion.py`
- `tests/unit/test_coordinator.py`
- `tests/unit/test_run_tracking.py`

Modified (11):

- `AGENTS.md`
- `README.md`
- `docs/architecture.md`
- `docs/data-contract.md`
- `docs/development.md`
- `docs/persistence.md`
- `docs/postgres-testing.md`
- `docs/storage-schema.md`
- `scripts/verify_installed.py`
- `scripts/verify_postgres.py`
- `src/hellas_dataops/storage/persistence.py`

Generated coverage, distributions and isolated environments are ignored locally.
No application credentials, datasets, global settings or unrelated resources are
included. There is no automatic commit.

### Required host step

From normal Windows PowerShell in this repository, with Docker Desktop running:

```powershell
& .\scripts\verify-postgres.ps1
```

The runner recognizes the accepted 3B.2 HEAD and reviewed 3C.1 path inventory,
uses the existing disposable-target ownership checks, and creates fresh uniquely
named resources. It does not delete containers or volumes. Review its sanitized
report and actual 109-test result before accepting 3C.1. Simulated lost
acknowledgements, Ctrl-C and connection invalidation do not establish actual
process-crash, OS failure or network-partition recovery. Production targeting,
automatic retry, raw-response retention and whole-operation deadlines remain
absent. After acceptance, scope the smallest controlled operational source/config
step, then bounded read-only queries; no next-milestone work is started here.

## Milestone 3C.1 final acceptance

**MILESTONE 3C.1: ACCEPTED.** The 2026-10-22 review confirms the host evidence,
implementation contracts and fresh offline quality checks. Acceptance covers
sequential synthetic ingestion, durable progress and explicit resume in the
verified disposable PostgreSQL environment. No runtime/test/schema/dependency
change was needed during acceptance; only documentation records the outcome.
The implementation-time pending notes above are historical and superseded here.

### Actual host evidence

The exact local evidence directory is
`artifacts/postgres/hellas-dataops-test-4a43cbabd8ba/`:

- `report.json`: `status=passed`, `stage=complete`, `pytest_exit_code=0`.
- `pytest.txt`: **109 passed in 33.10 seconds**, no failures/skips/errors reported.
- `coverage.json`: real storage/coordinator execution, detailed below.
- Main baseline: `34a9c087adfa28c783dc8e319c6ca6c278b2e49e`.
- Server: **PostgreSQL 18.6 (Debian 18.6-1.pgdg12+2)**.
- Image: `postgres:18.6-bookworm`; ID
  `sha256:bbf83594798e144bff365a9c191c5d779f181fb3135902064ab8ff11918020fc`.
- Python 3.14.4, pg8000 1.31.5 (`postgresql+pg8000`), SQLAlchemy 2.1.6,
  Alembic 1.20.0; Docker Server 29.7.2 and Compose 5.4.0.

| Evidence | SHA-256 |
| --- | --- |
| report.json | d50424cf79dcbcbc69c2343e0a63a3faabedaea1d560c934046b241ae3e14409 |
| pytest.txt | 99276576abcbf427411450e4531f6023d9593f4aaeab65e35913d81a5ac55d62 |
| coverage.json | c0bdfc8982a8026096b6d5c41f7f8004fed981f76e39e6b82cf2928df44f11e1 |

The successful runner path returns zero; the report explicitly records pytest's
exit code, not a separate shell-process exit-code field. Its completed path
requires Docker Linux Server access, healthy owned resources and validated mounts,
loopback binding at `127.0.0.1:52692`, database/user `hellas_dataops_test`, PostgreSQL
major 18 and the independent ownership marker. Only resources prefixed
`hellas-dataops-test-4a43cbabd8ba` are addressed. No Docker operation was performed
in this review. The script does not modify EREN or other project resources; this
is a workflow/code review conclusion, not a Docker-wide before/after attestation.
Resources remain retained; none were deleted.

The integration session verifies fresh upgrade to `20261022_0001`, downgrade on
empty application tables, re-upgrade, final revision and cleanup of synthetic
records. Its successful teardown is included in pytest exit zero. All 83 existing
schema/driver/persistence cases and all 26 new operational cases ran. Tests use
real SQLAlchemy/pg8000 connections; there is no SQLite substitution or mocked
persistence in this subtree. Before documentation edits, existing distributions
matched all current source/test/docs byte-for-byte. The report does not include
source hashes or a signed attestation; evidence is local host execution plus the
reviewed implementation and archives, not cryptographic proof of execution input.

### Verified guarantees and limits

- Durable registration precedes retrieval; the next chunk is fetched only after
  the previous chunk commits. Tests inspect persisted completed counts at fetch
  boundaries and check for absence of idle database transactions during retrieval.
- Complete provider-day normalization precedes retention and domain validation.
  One-hour, seven-day, exact 14/15-day, partial-day, leap-day, year and UTC DST
  boundary cases pass. Nullable measurements remain NULL.
- Weather, lineage, receipt and successful chunk/run status commit together.
  Later-chunk failures preserve prior commits without reporting whole-run success.
- Explicit resume reconstructs stored intent, verifies receipts/current key
  coverage, skips completed chunks and retains their original receipts and rows.
  Repeated completed start/resume leaves snapshots unchanged. Changed plan,
  coordinate/dataset configuration, corrupt receipts and missing weather fail.
- The same run's competing coordinator connection gets `run_busy` under the
  session advisory lock. Tests verify ownership release and later resume. Existing
  persistence concurrency tests still pass for the broader city/version lock.
- Default changed values are rejected; a new explicitly authorized replacement
  run retains prior values/lineage. Counts and NULL handling preserve the accepted
  writer contract. The frozen migration, domain/source APIs, mapping and dependency
  files are unchanged from the baseline.
- Injected pre-commit failure rolls back the chunk. Simulated acknowledgement loss
  after a real commit reconciles its exact receipt without refetching. Deliberate
  connection invalidation prevents silent reconnection; explicit resume succeeds.
  Simulated Ctrl-C leaves an incomplete running state that can be resumed.

These tests do not prove actual process-crash, OS failure or network-partition
recovery, whole-run atomicity, sustained-contention performance or automatic retry.
A `running` status is not a heartbeat. Arbitrary SQL writers can violate
application-owned JSON contracts. The city lock serializes disjoint writes for
that city/version. Only disposable configuration exists; production roles/TLS,
raw-response retention, retry scheduling and whole-operation/DNS deadlines remain
absent. These are nonblocking limits for this milestone.

The CLI always binds `fetch_synthetic`, uses in-memory transport and has no live
flag. Default tests retain network blocking. The programmatic coordinator accepts
an explicitly injected callable, so it is not a security boundary preventing a
caller from supplying arbitrary Python code. No live request was made or authorized.

### Coverage and fresh quality checks

| Scope | Statements | Branches | Combined coverage |
| --- | --- | --- | --- |
| Host storage + coordinator | 518/570 | 124/164 | 87.47% |
| Host coordinator | 59/63 | 9/12 | 90.67% |
| Host run tracking | 161/178 | 48/62 | 87.08% |
| Fresh offline full package | 1,050/1,142 | 313/360 | 90.75% |
| Offline coordinator | 63/63 | 12/12 | 100% |
| Union of host/offline targeted scope | 566/570 | 160/164 | 98.91% |

The union is a set calculation across the two JSON reports, not another test run.
Most host gaps are input/configuration rejection and offline SQL-generation paths
covered offline. Four defensive branches remain unexecuted in both reports:
`persistence.py` rejects an invalid planned-chunk state; `run_tracking.py` rejects
an existing start with a different plan, invalid failure index/time, and marking
an already completed chunk failed. Resume-plan conflicts and real success/failure
transitions are tested. These defensive gaps are recorded, not hidden or excluded.
No claim of full storage coverage is made.

Fresh results: **837 offline tests passed in 2.93 seconds**, zero failures; 750
baseline cases plus 87 new cases. Ruff lint and formatting pass (68 Python files).
Offline `uv lock --check --offline` and compatibility of all 28 installed
packages pass. uv remains 0.13.4. The documented commands were rerun, with coverage
written to `artifacts/coverage-3c1-acceptance-offline.json`. Pre-review archives
passed byte comparisons; acceptance documentation is rebuilt into
`dist/milestone-3c1-accepted` using the same offline build/verification commands.
No dependency installation or global configuration change is required for review.

### Reviewed inventory and local checkpoint

The exact milestone inventory remains the **8 created and 11 modified files** in
[the implementation inventory](#exact-implementation-inventory). The acceptance
review changes only instruction/documentation content in that inventory. All 81
version-controlled candidate files and all 19 changed paths are bound by the
checkpoint manifest. Credentials, local test configuration, reports, environments,
builds and datasets remain ignored. Content/path review and pattern scanning found
no blocking privacy issue; scanning is not a proof that all possible secrets are
absent. Synthetic fixtures, source, tests, documentation and uv.lock remain included.

Git metadata is read-only in this workspace. No staging/commit/escalation is
attempted. The ignored files `artifacts/checkpoint-3c1.ps1` and
`artifacts/checkpoint-3c1-manifest.json` prepare exactly one local commit. They
verify root, main, expected HEAD, reviewed file SHA-256 values and inventory,
unchanged host evidence, approved noreply author/committer, empty initial staging,
hooks/filters, staged blobs and diff checks, then verify parent/message/identity,
committed inventory and clean status. Any failed guard stops the operation.

From ordinary Windows PowerShell in the repository:

```powershell
& .\artifacts\checkpoint-3c1.ps1
```

Message: `feat: add resumable operational weather ingestion`. The script does not
change Git identity/configuration, push, create remotes or change security policy.
A `-ReviewOnly` invocation performs all pre-staging checks without Git writes.
If execution policy blocks the script, do not bypass it; report that blocker.
After the checkpoint, the frozen PostgreSQL runner HEAD must be reviewed during
the next authorized milestone, not silently bypassed. No new database run is
required merely to create this checkpoint.

### Recommended next milestone (not started)

**Milestone 3D.1: read-only FastAPI queries over stored synthetic weather.** Reuse
SQLAlchemy Core and the existing schema. Provide supported-city listing and hourly
queries with explicit city/dataset/coordinate identity and UTC half-open intervals,
stable cursor pagination, a bounded page size and documented result limits.
Expose separate process liveness and bounded database/schema readiness checks,
returning safe failures without connection details. Include OpenAPI examples,
offline endpoint/validation tests and opt-in PostgreSQL-backed query tests against
the existing disposable target. Use a small application factory and explicit
connection injection; no public ingestion endpoint or repository framework.

This is the shortest next step to a demonstrable ingestion-to-query portfolio MVP.
The API should serve persisted rows, never synchronously retrieve weather. Live
Open-Meteo access, production database configuration and the five-city 2025 backfill
remain separately approved MVP work after service-term/operating-condition review.
Scheduling, cloud deployment, background workers and extra sources are optional.
No API implementation or next-milestone dependency change is part of this review.

## Milestone 3D.1 read-only FastAPI implementation

Historical implementation record: offline checks passed before host acceptance.
See the final 3D.1 acceptance record below for the subsequent verified result.
The clean starting checkpoint was main at
`9d4c4b037eaa13e2fff42cce77a1fcf0505e9de1`. No Git metadata, Docker resources,
global configuration or live weather services were changed. The existing domain,
source, coordinator, storage mapping/writer, schema and initial revision remain
unchanged. See [api.md](api.md) for contracts, error semantics and the synthetic
local demonstration.

### Dependencies and compatibility

The project-local uv 0.13.4 resolved and installed FastAPI **0.159.1**, Uvicorn
**0.54.0** and development-only HTTPX2 **2.16.0** on CPython **3.14.4 Windows x64**.
Starlette **1.9.0** and AnyIO **4.15.1** are transitive framework dependencies.
The installed Starlette TestClient selects httpx2; its older httpx fallback is
deprecated. Only plain Uvicorn is installed, without optional standard extras.
No native driver or database dependency changed: pg8000 **1.31.5**, SQLAlchemy
**2.1.6**, Alembic **1.20.0**, Pydantic **2.14.1**. All previously locked versions
are preserved. uv.lock now resolves 41 packages across platform markers; 40 are
installed in this Windows development environment. Other Python versions are not
claimed supported or verified.

Actual dependency commands, with the README's local cache/temp environment:

```powershell
& .\.tools\uv\bin\uv.exe add 'fastapi>=0.120,<1' 'uvicorn>=0.38,<1' --python .venv/Scripts/python.exe
& .\.tools\uv\bin\uv.exe add --dev 'httpx2>=2,<3' --python .venv/Scripts/python.exe
```

Existing dependency version equality was checked by parsing HEAD:uv.lock and the
new lock, not by assuming uv preserved versions. Repeated `sync --locked --offline`
succeeded and left the lockfile hash unchanged. Runtime imports, generated
OpenAPI and in-process HTTP execution succeed; installation alone is not claimed
to prove real database queries.

### Offline verification

**909 tests pass, zero failures: 837 regressions plus 72 new API/query tests.**
Ruff lint and formatting pass. The socket/DNS guard is unchanged. Windows' default
TestClient initially failed because asyncio creates an internal loopback wake-up
socket under the guard. Tests now use HTTPX2 ASGITransport with a session event
loop initialized before guards; HTTP request execution remains guarded. A test
explicitly demonstrates that a socket attempt inside an endpoint still fails.
No test listener, external HTTP request or default-suite PostgreSQL connection
is used. Integration tests retain their exact validated database-port guard.

Coverage from `artifacts/coverage-3d1-offline.json`:

| Scope | Statements | Branches | Combined |
| --- | --- | --- | --- |
| API factory, models, routes | 74/74 | 4/4 | 100% |
| Core query module | 81/81 | 30/30 | 100% |
| API launcher | 17/18 | 1/2 | 90% |
| New runtime modules together | 172/173 | 35/36 | 99.04% |
| Full package | 1222/1315 | 348/396 | 91.76% |

The uncovered new line/branch is the `__main__` launcher guard; its `--help` command
was executed separately without coverage. Actual Uvicorn serving and PostgreSQL
query semantics are not established by these unit counts. Older persistence/run
tracking database paths remain uncovered offline; their prior accepted host
coverage is historical, not combined into these new percentages.

Commands actually used (run from project root; check LASTEXITCODE after each):

```powershell
$env:TEMP = Join-Path $PWD '.tools/tmp'
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_LINK_MODE = 'copy'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& .\.venv\Scripts\python.exe -I -B -m pytest -p no:cacheprovider -p pytest_cov --cov=src/hellas_dataops --cov-report=term --cov-report=json:artifacts/coverage-3d1-offline.json -q
& .\.venv\Scripts\ruff.exe check .
& .\.venv\Scripts\ruff.exe format --check .
& .\.tools\uv\bin\uv.exe lock --check --offline
& .\.tools\uv\bin\uv.exe sync --locked --offline --python .venv/Scripts/python.exe
& .\.tools\uv\bin\uv.exe pip check --offline --python .venv/Scripts/python.exe
& .\.venv\Scripts\python.exe -I -B -m hellas_dataops.api --help
& .\.venv\Scripts\python.exe -I -B -m pytest tests/integration --postgres-config .tools/not-executed.json --collect-only -p no:cacheprovider -q
```

The last command only collected **133 tests**, opening no database: 109 accepted
regression cases and **24 new API/PostgreSQL cases**. All 133 remain unexecuted
for this change set. The new cases cover real persistence-backed reads, city and
historical coordinate/dataset version isolation, UTC/null JSON, bounded keyset
traversal, empty windows, rejected injection-shaped inputs, read-only enforcement,
revision mismatch, actual authentication/ownership rejection, and correction
visibility between pages. Ordinary GET cases snapshot all five tables before and
after. Special synthetic catalog rows exercise historical identities outside the
current source planner without broadening the accepted writer.

### Distribution verification

The wheel and source distribution built offline successfully in
`dist/milestone-3d1/`. `verify_distributions.py` compared **89 source assets**,
including 3 synthetic JSON fixtures and 10 documentation files, and **34 wheel
modules** plus the Alembic template byte-for-byte. README metadata is present;
no forbidden credential, cache, data or environment paths were packaged.

Both artifacts were installed separately into fresh `.tools/verify-3d1-wheel`
and `.tools/verify-3d1-sdist` environments. Before installation, imports confirmed
that the project, FastAPI, SQLAlchemy and pytest were absent. Hashed runtime
requirements exported from uv.lock installed offline. Each distribution then
passed **45 isolated module imports**, generated-OpenAPI validation, installed
planning/client/migration smoke checks, and compatibility checks for **27 runtime
packages including the project**. Development HTTPX2 is not needed by the wheel.
No global packages or manual PYTHONPATH were used.

Exact commands (substitute `wheel`/`sdist` and the corresponding artifact):

```powershell
& .\.tools\uv\bin\uv.exe build --offline --no-build-isolation --python .venv/Scripts/python.exe --out-dir dist/milestone-3d1
& .\.venv\Scripts\python.exe -I -B scripts/verify_distributions.py dist/milestone-3d1
& .\.tools\uv\bin\uv.exe export --locked --offline --no-dev --no-emit-project --format requirements.txt --output-file .tools/runtime-requirements-3d1.txt
& .\.tools\uv\bin\uv.exe venv .tools/verify-3d1-wheel --python .venv/Scripts/python.exe --offline --no-managed-python
& .\.tools\uv\bin\uv.exe pip sync --python .tools/verify-3d1-wheel/Scripts/python.exe --offline --require-hashes .tools/runtime-requirements-3d1.txt
& .\.tools\uv\bin\uv.exe pip install --python .tools/verify-3d1-wheel/Scripts/python.exe --offline --no-deps dist/milestone-3d1/hellas_dataops-0.1.0.dev0-py3-none-any.whl
& .\.tools\verify-3d1-wheel\Scripts\python.exe -I -B scripts/verify_installed.py
& .\.tools\uv\bin\uv.exe pip check --python .tools/verify-3d1-wheel/Scripts/python.exe --offline
```

Use a new environment directory for subsequent checks, not one containing old
installations. The sdist command used `hellas_dataops-0.1.0.dev0.tar.gz`; uv built
it with the cached pinned backend. Final documentation updates were rebuilt and
archive comparisons rerun. The PowerShell wrapper parsed successfully; it was
not executed against Docker. `git diff --check` passed, and a path-restricted
HEAD comparison confirms accepted domain/source/ingestion/schema/writer modules
are unchanged.

### Host acceptance gate

From ordinary Windows PowerShell, with Docker Desktop running:

```powershell
& .\scripts\verify-postgres.ps1
```

The reviewed Python runner recognizes the accepted 3C.1 HEAD and the exact current
23-path change allowlist. It includes API/storage/coordinator coverage and records
FastAPI/Uvicorn/HTTPX2 versions in the sanitized report. The wrapper, Docker image,
localhost binding, unique ownership labels/marker, volume layout, resource
collision checks and cleanup policy are unchanged. No sandbox escalation or
Docker access was attempted. A read-only invocation of its repository gate
confirmed the baseline; it did not run the Docker phase.

Acceptance requires the owner's actual new report with successful pytest exit,
all database tests passed, and review of any failures. Do not reuse the older
109-test report as API evidence. No commit is created automatically.

### Exact milestone inventory

Created (9):

- `docs/api.md`
- `src/hellas_dataops/api/__main__.py`
- `src/hellas_dataops/api/app.py`
- `src/hellas_dataops/api/models.py`
- `src/hellas_dataops/api/routes/read.py`
- `src/hellas_dataops/storage/queries.py`
- `tests/integration/test_api_postgres.py`
- `tests/unit/test_api.py`
- `tests/unit/test_queries.py`

Modified (14):

- `AGENTS.md`
- `README.md`
- `docs/architecture.md`
- `docs/data-contract.md`
- `docs/development.md`
- `docs/postgres-testing.md`
- `pyproject.toml`
- `uv.lock`
- `scripts/verify_installed.py`
- `scripts/verify_postgres.py`
- `src/hellas_dataops/api/__init__.py`
- `src/hellas_dataops/api/routes/__init__.py`
- `tests/conftest.py`
- `tests/integration/conftest.py`

Reports, builds, exported runtime requirements and fresh environments remain
ignored. No secrets or local test target JSON are included.

### Remaining boundaries and next step

First obtain and review real PostgreSQL acceptance. Then prioritize a small
synthetic end-to-end demonstration/runbook with measured query behavior and
explicit operational configuration/security decisions. Live Open-Meteo access
still needs separate service-term and operating-condition review; no API handler
fetches upstream weather. Authentication, rate limiting, a read-only database role,
TLS and production targeting remain prerequisites for a future hosted service,
not implied by this localhost demonstration. Snapshot exports, connection pooling,
schedulers, workers and additional infrastructure are optional future work.

## SQLAlchemy pure-Python remediation

2026-10-23: the native SQLAlchemy wheel failed with Windows Application Control
rejecting `sqlalchemy.sql._cache_key_cy`. The same failure was reproduced in
Codex before any database call. Distribution metadata showed SQLAlchemy 2.1.6,
`Root-Is-Purelib: false`, `cp314-cp314-win_amd64`, and eight `.pyd` files.
The previous host runner imported storage/SQLAlchemy before entering its main
reporting block, explaining why no new report was produced for that failure.
No Docker operation is needed to diagnose or repair this installation.

### Supported build policy

The official [SQLAlchemy source-build option](https://docs.sqlalchemy.org/en/21/intro.html#building-the-cython-extensions)
is `DISABLE_SQLALCHEMY_CEXT=1`. It affects building, not an already installed wheel.
The [uv settings reference](https://docs.astral.sh/uv/reference/settings/)
documents `no-binary-package` and `extra-build-variables`. The installed uv 0.13.4
accepts both, and actual installations verified their effect:

```toml
[tool.uv]
no-binary-package = ["sqlalchemy"]
extra-build-variables = { sqlalchemy = { DISABLE_SQLALCHEMY_CEXT = "1" } }
```

This is a deliberately small project-wide policy for SQLAlchemy only, rather
than another platform-specific requirements file or driver framework. It applies
to the project's uv sync and uv pip installation workflows on every platform.
Only Windows x64/Python 3.14.4 was tested. Other native dependencies are unchanged;
this does not claim the entire environment is pure Python. SQLAlchemy optional
acceleration is lost; no performance difference has been measured.

The first forced source rebuild used a new ignored
`.tools/sqlalchemy-pure-cache-v1` cache, preventing reuse of any earlier native
wheel or compiled source build. The official 2.1.6 source archive is pinned by
hash in the unchanged uv.lock. Its setup.py contains the supported build flag.
The same policy was then built and verified in the normal `.uv-cache`, so the
host preflight can operate offline. uv replaced whole distributions normally;
no `.pyd` file was manually removed and no SQLAlchemy implementation was patched.

Actual source build requirements resolved setuptools 84.2.1 and Cython 3.3.0
inside transient isolated build environments. They were not installed in the
application environment. These upstream build-tool resolutions are not runtime
lockfile pins; byte-for-byte reproducible SQLAlchemy wheel builds are not claimed.
The project's own build backend stays locked to setuptools 84.2.0.

### Commands and installed-build evidence

From project root, with the documented project-local TEMP/TMP settings:

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.tools/sqlalchemy-pure-cache-v1'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_LINK_MODE = 'copy'
& .\.tools\uv\bin\uv.exe sync --locked --no-binary-package sqlalchemy --reinstall-package sqlalchemy --python .venv/Scripts/python.exe
& .\.venv\Scripts\python.exe -I -B scripts/verify_sqlalchemy.py
# Prepare the normal cache once, using the same source-build policy:
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
& .\.tools\uv\bin\uv.exe sync --locked --no-binary-package sqlalchemy --reinstall-package sqlalchemy --python .venv/Scripts/python.exe
# Verify host preflight can replace the package without network access:
& .\.tools\uv\bin\uv.exe sync --locked --offline --no-binary-package sqlalchemy --reinstall-package sqlalchemy --python .venv/Scripts/python.exe
& .\.venv\Scripts\python.exe -I -B scripts/verify_sqlalchemy.py
# Ordinary future setup retains the compatible build:
& .\.tools\uv\bin\uv.exe sync --locked --offline --python .venv/Scripts/python.exe
```

Every command above succeeded. Stop on nonzero LASTEXITCODE. The build variable
comes from package-scoped uv configuration; no global or runtime-only environment
change is needed. `--reinstall-package` replaces an existing native installation;
`--no-binary-package` selects the source build, while `--locked` preserves resolved
versions. The normal cache now contains the verified pure build. A cold machine
needs the online setup step for source/build requirements before offline use.

The probe checks the actual distribution in the active virtual environment,
pure wheel metadata, all physical files for native components and all eight
extension-capable modules' implementations. It then imports Core, Alembic and
the pg8000 dialect and compiles a parameterized SELECT without opening a database.
Its sanitized result is:

```json
{"sqlalchemy":"2.1.6","pure_python":true,"native_files":0,"python_extension_modules":8,"wheel_tag":"py3-none-any","installation":"active-virtual-environment","alembic":"1.20.0","pg8000":"1.31.5","dialect":"pg8000","status":"passed"}
```

Version-specific internal inspection is diagnostic only; a future SQLAlchemy
upgrade must review this probe. It does not modify internal modules or fabricate
fallback imports. A regular pip install outside this project's uv configuration
will not inherit the build policy from a published project wheel; use the
repository setup instructions on policy-restricted hosts.

### Runner and verification changes

The PowerShell wrapper is unchanged. The Python runner is now importable using
only the standard library. After branch/HEAD/path checks, it performs locked,
offline synchronization with explicit SQLAlchemy source replacement, confines
environment/cache/temp paths to the project, verifies dependency compatibility,
and runs the isolated pure-build probe. Only then does it import storage and
pg8000 and proceed to Docker. Failures are sanitized under `locked-environment`,
`sqlalchemy-pure-python` or `pg8000-driver-import`. No resource is created when
preflight fails. The report includes `sqlalchemy_build` on success. Existing
Docker collision, ownership, database marker and localhost safeguards remain.

**916 offline tests pass, zero failures**: all 909 prior tests plus seven new
preflight cases. The new cases verify native-wheel/file rejection, actual pure
installation, sanitized failures, runner import with SQLAlchemy blocked, and
sync/probe failures stopping before Docker. The existing mocked workflow tests
were adjusted for the additional preflight operations; database and application
assertions were not weakened. Root socket/DNS blocking remains enabled.

Application coverage remains **91.76% combined** (1222/1315 statements,
348/396 branches); query module and API factory/models/routes remain 100% in the
offline suite. Pure Python does not change the application source. SQLAlchemy
third-party implementation coverage and real database correctness are not implied.
Ruff lint/formatting and compatibility of 40 installed development packages pass.
Offline lock checks, repeated sync and a byte-level comparison confirm uv.lock
is unchanged by this remediation; all previously locked versions are preserved.
Offline Alembic upgrade and downgrade SQL generation pass; revision
`20261022_0001` and all schema/domain/API/ingestion source files are unchanged.
The PostgreSQL suite still collects **133 tests**, with actual host execution
pending. Migration SQL generation is not proof of real migration success.

The rebuilt wheel and source distribution pass the distribution verifier:
90 source assets (including three synthetic JSON fixtures and ten documentation
files), 34 wheel modules and the Alembic template match the working tree. No
forbidden generated or credential paths are packaged. Fresh, separate wheel and
sdist environments each pass 45 isolated module imports, the pure SQLAlchemy
probe, application smoke checks and dependency compatibility (27 runtime packages).
Dependencies were installed from the unchanged hashed lock export using ordinary
`uv pip sync --offline --require-hashes` from the repository root, confirming that
the project build policy also applies to this uv installation path.

```powershell
& .\.tools\uv\bin\uv.exe build --offline --no-build-isolation --python .venv/Scripts/python.exe --out-dir dist/milestone-3d1-pure
& .\.venv\Scripts\python.exe -I -B scripts/verify_distributions.py dist/milestone-3d1-pure
& .\.tools\verify-3d1-pure-wheel\Scripts\python.exe -I -B scripts/verify_installed.py
& .\.tools\verify-3d1-pure-sdist\Scripts\python.exe -I -B scripts/verify_installed.py
```

The final documentation-only update was followed by another distribution build
and byte-for-byte asset check; it does not change the runtime code verified in
those fresh installations.

### Remediation file inventory

Created: `scripts/verify_sqlalchemy.py`.

Modified during this remediation:

- `AGENTS.md`
- `README.md`
- `docs/development.md`
- `docs/postgres-testing.md`
- `pyproject.toml` (uv build policy only)
- `scripts/verify_installed.py`
- `scripts/verify_postgres.py`
- `tests/unit/test_postgres_runner.py`

All other uncommitted changes are the previously reviewed 3D.1 implementation.
In particular, uv.lock was already modified for 3D.1 and did not change again.
Ignored build logs, coverage and fresh environments remain local. No commit,
security-policy change, unrelated resource access or live weather call occurred.

### Required Windows-host verification

Run the same single command in ordinary Windows PowerShell:

```powershell
& 'C:\Users\User\Documents\ChatGPT\hellas-dataops\scripts\verify-postgres.ps1'
```

The source build/import and all available offline checks now work in Codex.
Windows host policy and actual PostgreSQL/API behavior still require the owner's
execution. Do not declare 3D.1 accepted until its new sanitized report and all
133 PostgreSQL results have passed normal acceptance review. No next milestone
is authorized by this remediation.

## Milestone 3D.1 final acceptance

**MILESTONE 3D.1: ACCEPTED** on 2026-10-23. This record supersedes the historical
pending-host statuses above. No application code, dependency version, schema or
migration was changed during this acceptance review.

### Host evidence and scope

Reviewed `artifacts/postgres/hellas-dataops-test-e8c550622181/report.json`,
`pytest.txt` and `coverage.json`. The report records `status=passed`,
`stage=complete`, and `pytest_exit_code=0`; the captured output records
**133 passed in 41.34 seconds**, with no failures or skips. This comprises 109
existing PostgreSQL cases and 24 new API cases. The runner's successful complete
path returns zero; a separate PowerShell process exit-code field is not recorded.

| Evidence | SHA-256 |
| --- | --- |
| report.json | b1c2a97e0fbf6cf31f22ce6433e299e332bec49696dcaf8fdcf533164ad08eec |
| pytest.txt | 9910113c99dc4ede8d6be4897287591870483b5e797ab16c9dc01d3409547347 |
| coverage.json | 029dfafb39470515452f8dffa581952ad6de679cfdffe41b8c36f1407524825d |

The actual server was **PostgreSQL 18.6 (Debian 18.6-1.pgdg12+2)** from
`postgres:18.6-bookworm`. The report records Docker Server 29.7.2, Compose 5.4.0,
the image ID, and the unique project identifier. The reviewed runner requires
collision-free project-specific container/network/volume names, matching ownership
labels, a healthy Linux container, a localhost-only binding (this run used port
57380), the PostgreSQL 18 volume layout and a database ownership marker. Test
connections verify the exact database/user, PostgreSQL major version and marker;
the integration network guard permits only that explicit database port.

Successful fixture setup proves fresh upgrade to `20261022_0001`, five application
tables plus Alembic tracking, downgrade to base on empty application tables and
re-upgrade. Teardown verifies empty application tables and the expected revision.
The initial migration and accepted writer/domain/source implementations are
unchanged from the previous checkpoint. No SQLite substitution is used.

The reviewed commands only create and inspect resources belonging to this new
test project; no delete, prune or unrelated resource mutation is present. The
report records no resource removal. This is evidence about this runner, not an
independent audit of all host activity or unrelated Docker projects. No Docker
operation was executed from Codex during this review.

Verified host dependency versions: SQLAlchemy 2.1.6, Alembic 1.20.0, pg8000 1.31.5,
FastAPI 0.159.1, Uvicorn 0.54.0 and development-only httpx2 2.16.0. The host pure-build
probe confirms SQLAlchemy `py3-none-any`, zero native files and eight Python
extension implementations. CPython is 3.14.4 on Windows x64; project uv is 0.13.4.

### API review and demonstrated behaviour

- Application construction/import opens no database connection. Query methods
  use scoped NullPool connections and read-only REPEATABLE READ transactions;
  cleanup occurs on success and failure. No route invokes the weather client.
- Core queries bind client values, resolve city/version/provider/dataset identity
  explicitly and use the existing weather primary key. UTC intervals are hourly,
  inclusive-start/exclusive-end, nonempty and at most 31 days. Pages default to
  100, permit 1-500 and fetch at most limit + 1 rows.
- PostgreSQL tests exercise timestamp ordering, exclusive-end filtering, keyset
  traversal without duplicates/omissions on a static dataset, empty pages, city,
  coordinate-version and dataset isolation, null/UTC JSON, and corrections
  between pages. Later pages intentionally see newly committed corrections.
- Liveness is database-independent. Readiness verifies ownership, revision,
  columns/types/nullability and SELECT access. Host tests reject wrong ownership,
  credentials and revision; offline tests additionally cover column defects.
- Invalid parameters map to 422, unknown/unpersisted identities to 404, and
  database failures to sanitized 503 responses. OpenAPI declares the actual
  models, page limits and weather error responses. Arbitrary error inputs and
  database diagnostics are not echoed. Unsupported write methods return 405.
- Ordinary GET tests compare all five tables before and after requests. A real
  attempted UPDATE inside the reader transaction is rejected by PostgreSQL.
  This verifies API read-only behaviour, not a least-privilege database account.

The existing `httpx2` dependency is intentional and remains development-only.
Installed Starlette 1.9.0 metadata and TestClient implementation select httpx2;
the old httpx fallback is deprecated there. Our fixtures use HTTPX2 AsyncClient
and ASGITransport with proxy/environment trust disabled, so API tests run in
process under the network guards. Its necessity is the in-process test transport,
not runtime API serving or upstream ingestion. No replacement was warranted.

### Quality and coverage

The acceptance review reran **916 offline tests: zero failures**, in 3.70 seconds.
This is the 837 accepted baseline, 72 API/query tests and seven dependency-preflight
tests. Ruff lint and formatting pass (78 files). Offline uv lock verification
passes (41 resolved entries), and all 40 installed development packages are
compatible. Versions present at the previous checkpoint remain unchanged.

| Measurement | Statements | Branches | Combined |
| --- | --- | --- | --- |
| Complete offline application suite | 1222/1315 | 348/396 | 91.76% |
| Host storage/API/coordinator suite | 666/743 | 149/200 | 86.43% |
| Offline query service | 81/81 | 30/30 | 100% |
| Offline API factory/models/routes | 74/74 | 4/4 | 100% |

These coverage scopes differ and are not averaged. The host-only percentage
includes the uninvoked API launcher and validation/defensive paths exercised
offline. Comparing missing-line sets leaves the API module-entry exit and existing
storage guards for invalid chunk state, conflicting reconstructed run plan and
invalid/completed failure marking uncovered in both runs. This comparison is not
a merged coverage measurement. No untested normal API query path was found.
Coverage is not proof against all race conditions or operational failures.

Wheel and source packaging were rebuilt under `dist/acceptance-3d1` and passed
the existing verifier: 90 source assets, three synthetic JSON fixtures, ten
documentation files, 34 runtime modules and the Alembic template. No forbidden
paths were packaged. Two new isolated environments each installed hashed locked
runtime requirements and the wheel or sdist offline, passed 45 isolated module
imports, pure-SQLAlchemy/OpenAPI/migration smoke checks and compatibility checks
for all 27 runtime packages. No editable/source-path fallback was used.

Acceptance commands (project-local cache/TEMP/TMP and disabled pytest plugin
autoload as documented above):

```powershell
& .\.venv\Scripts\python.exe -I -B -m pytest -q -p no:cacheprovider -p pytest_cov --cov=src/hellas_dataops --cov-branch --cov-report=term --cov-report=json:artifacts/acceptance-3d1/offline-coverage.json
& .\.venv\Scripts\ruff.exe check .
& .\.venv\Scripts\ruff.exe format --check .
& .\.tools\uv\bin\uv.exe lock --check --offline
& .\.tools\uv\bin\uv.exe pip check --offline --python .venv/Scripts/python.exe
& .\.venv\Scripts\python.exe -I -B scripts/verify_sqlalchemy.py
& .\.tools\uv\bin\uv.exe build --offline --no-build-isolation --python .venv/Scripts/python.exe --out-dir dist/acceptance-3d1
& .\.venv\Scripts\python.exe -I -B scripts/verify_distributions.py dist/acceptance-3d1
& .\.tools\uv\bin\uv.exe export --locked --offline --no-dev --no-emit-project --format requirements-txt --output-file .tools/runtime-requirements-acceptance-3d1.txt
```

Each fresh environment used `uv venv --offline --python .venv/Scripts/python.exe`,
`uv pip sync --offline --require-hashes --python <fresh-python>` with that export,
then `uv pip install --offline --no-deps --python <fresh-python> <archive>` and
`<fresh-python> -I -B scripts/verify_installed.py`. Environments were
`.tools/acceptance-3d1-wheel` and `.tools/acceptance-3d1-sdist`. Native exit codes
were checked after each command. Final documentation-only edits were followed
by rebuilding and verifying archive contents; runtime code was unchanged.

### Reviewed checkpoint inventory

25 changed paths: **10 created and 15 modified**. No deletions, staged changes,
schema changes or new migration. The review found no credentials, personal email,
generated dataset or unexpected artifact in the intended commit. The one absolute
Windows command in the historical remediation uses the owner's specified generic
workspace path, not a secret. Local reports, caches, target configuration and
checkpoint helpers remain ignored by Git.

Created:

- `docs/api.md`
- `scripts/verify_sqlalchemy.py`
- `src/hellas_dataops/api/__main__.py`
- `src/hellas_dataops/api/app.py`
- `src/hellas_dataops/api/models.py`
- `src/hellas_dataops/api/routes/read.py`
- `src/hellas_dataops/storage/queries.py`
- `tests/integration/test_api_postgres.py`
- `tests/unit/test_api.py`
- `tests/unit/test_queries.py`

Modified:

- `AGENTS.md`
- `README.md`
- `docs/architecture.md`
- `docs/data-contract.md`
- `docs/development.md`
- `docs/postgres-testing.md`
- `pyproject.toml`
- `scripts/verify_installed.py`
- `scripts/verify_postgres.py`
- `src/hellas_dataops/api/__init__.py`
- `src/hellas_dataops/api/routes/__init__.py`
- `tests/conftest.py`
- `tests/integration/conftest.py`
- `tests/unit/test_postgres_runner.py`
- `uv.lock`

The owner-side `artifacts/checkpoint-3d1.ps1` verifies main at
`9d4c4b037eaa13e2fff42cce77a1fcf0505e9de1`, all 91 repository file hashes, this exact
25-path change inventory, three host evidence hashes, approved noreply identities,
an initially empty index and staged blob/mode consistency. It stages only the
reviewed paths and creates one local commit with message
`feat: add read-only FastAPI weather queries`, then verifies parent, contents,
identity and a clean working tree. `-ReviewOnly` performs no staging or commit.
No sandbox Git metadata write, escalation, push or publication is part of this review.
The host verification runner deliberately retains its old HEAD gate: after the
checkpoint, review it under the next authorized scope before rerunning tests.

### Remaining limits and recommended next milestone

Acceptance covers a local synthetic, read-only API. It does not prove live Uvicorn
socket serving, browser CDN availability, sustained load, production deployment,
crash recovery or a whole-request deadline. PostgreSQL statement/lock and driver
socket timeouts are not a total HTTP deadline. Readiness does not audit every
constraint on each request. Catalog listing assumes a small registry.

Separate pages do not share a snapshot; corrections and late inserts can alter
traversal results. There is no authentication, TLS, rate limiting or dedicated
read-only database role. The disposable account has test write privileges, and
retained Docker resources consume disk/memory. The SQLAlchemy pure build has not
been performance-benchmarked; cold setup needs cached source/build prerequisites
for subsequent offline use. No real provider access or terms approval is implied.

Recommend **Milestone 3D.2: reproducible synthetic end-to-end demonstration**:
one guarded runbook from synthetic ingestion CLI to the existing PostgreSQL
database and loopback Uvicorn API; endpoint/OpenAPI smoke tests; pagination checks
and measured warm/cold local query latency with dataset size, environment, sample
count and p50/p95 clearly recorded. Reuse existing infrastructure and retain
resources for inspection. Portfolio presentation and reproducible evidence are
the goal; no new endpoints, scheduler, cache or cloud service are needed.
Live Open-Meteo access remains separately gated. This next milestone is not started.
