# Milestone 1B development and distribution verification

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
