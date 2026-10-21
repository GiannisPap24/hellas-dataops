# Milestone 2C: offline request planning and provenance

Verification date: 2026-10-21. Windows x64, CPython 3.14.4, local uv 0.13.4,
Ruff 0.17.1, pytest 9.1.1, pytest-cov 7.1.0. No new dependencies, network
requests, services, or global configuration changes.

## Foundation review and previous inventory

Reviewed instructions, README, dependency/packaging configuration, architecture
and data contracts, domain/normalization code, and tests. Baseline: 311 passed.
Offline lock validation, locked sync, and compatibility checks passed for 18
packages. The lock SHA-256 remains
`820DA87A498095A57895A38548EB46C4D07AB7ECE3B64FA9B21E0D9ED3A86390`.

Git initially listed 35 expected untracked files and no HEAD commit. No unexpected
files were found. File presence and `.tools/milestone-2b-before.json` hashes support
the inventory below; this is not a commit-based diff or independent evidence of
historical edit times. The recovered 2A inventory remains in `development.md`.

Milestone 2B created:

```text
src/hellas_dataops/sources/open_meteo.py
tests/unit/test_open_meteo.py
tests/fixtures/open_meteo/seven_days.json
tests/fixtures/open_meteo/null_measurements.json
tests/fixtures/open_meteo/leap_day.json
```

Milestone 2B modified:

```text
src/hellas_dataops/sources/__init__.py
AGENTS.md
MANIFEST.in
README.md
docs/architecture.md
docs/data-contract.md
docs/development.md
tests/fixtures/README.md
```

## Milestone 2C inventory

Created:

```text
src/hellas_dataops/sources/cities.py
src/hellas_dataops/sources/open_meteo_requests.py
tests/unit/test_cities.py
tests/unit/test_open_meteo_requests.py
scripts/verify_distributions.py
docs/milestone-2c.md
```

Modified:

```text
AGENTS.md
README.md
docs/architecture.md
docs/data-contract.md
docs/development.md
src/hellas_dataops/sources/__init__.py
scripts/verify_installed.py
```

The pre-change `.tools/milestone-2c-before.json` snapshot permits hash comparison
of all existing domain code, normalizer, tests, JSON fixtures, network guard,
`pyproject.toml`, `uv.lock`, and `MANIFEST.in`. These remain unchanged. Generated
coverage, cache/build files, snapshots, and verification environments are ignored
local artifacts. All 41 intended project files remain untracked until separately
authorized Git work; nothing was committed or deleted.

## Implemented contract and quality results

`CityCoordinates` and the read-only `CITY_REGISTRY` preserve the five documented
points as `gr-cities-v1`. `plan_open_meteo_requests(city, *, dataset, interval)`
accepts only approved city configurations and `open-meteo/era5_hourly-v1`.
It returns frozen `OpenMeteoRequest` objects retaining the original interval,
complete-day retrieval chunk, derived retained subset, inclusive provider dates,
explicit parameters, encoded query/URL, and canonical request provenance JSON.

Jan 1 00:00 to Jan 15 00:00 requests Jan 1-14 only. A 15-day request yields two
chunks. Jan 1 05:00 to Jan 15 05:00 touches 15 calendar days and retains 331 + 5
hours. Future ingestion must normalize full retrieval coverage before filtering
and validating retained hours; no filtering or HTTP integration was implemented.
See `data-contract.md` for all options, error codes, and provenance semantics.

Full suite: **414 passed, 0 failed**. All 311 prior cases pass unchanged, plus
49 city and 54 planner cases. Ruff lint and formatting checks pass. One initial
lint finding in an adjacent-chunk test was corrected with `itertools.pairwise`;
no test or quality rule was disabled. The network guard remains enabled.

| Module | Covered statements | Covered branches |
| --- | --- | --- |
| `sources/cities.py` | 26/26 (100%) | 8/8 (100%) |
| `sources/open_meteo_requests.py` | 79/79 (100%) | 22/22 (100%) |
| Unchanged `sources/open_meteo.py` | 87/87 (100%) | 34/34 (100%) |
| Whole implemented package | 318/318 (100%) | 116/116 (100%) |

Coverage measures exercised statements/branches, not live compatibility or all
input combinations. Packaging scripts are verified by execution, outside package
coverage. Run from the repository root in PowerShell:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider -q
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider -p pytest_cov --cov=hellas_dataops --cov-report=term-missing --cov-report=json:artifacts/coverage-2c.json --cov-report=xml:coverage.xml
& ./.venv/Scripts/ruff.exe check .
& ./.venv/Scripts/ruff.exe format --check .
```

## Offline packaging verification procedure

Use the existing populated local cache. Stop after any command failure. Use fresh
verification directory names on future runs rather than overwriting environments.
The project Python selects the already approved interpreter; downloads are disabled.

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_LINK_MODE = 'copy'
$env:TEMP = Join-Path $PWD '.tools/tmp'
$env:TMP = $env:TEMP
$lockBefore = (Get-FileHash uv.lock -Algorithm SHA256).Hash
& ./.tools/uv/bin/uv.exe --version
& ./.tools/uv/bin/uv.exe lock --check --offline
& ./.tools/uv/bin/uv.exe sync --locked --offline
& ./.tools/uv/bin/uv.exe pip check --python .venv/Scripts/python.exe
if ($lockBefore -ne (Get-FileHash uv.lock -Algorithm SHA256).Hash) { throw 'Lockfile changed' }
& ./.tools/uv/bin/uv.exe build --offline --no-build-isolation --python .venv/Scripts/python.exe --out-dir dist/milestone-2c
& ./.venv/Scripts/python.exe -I -B scripts/verify_distributions.py dist/milestone-2c
```

`verify_distributions.py` compares required source assets byte-for-byte with the
checkout, including all JSON fixtures and Markdown documents, and checks every
runtime wheel module plus README metadata. Full docs and tests belong in the
source archive, not the runtime wheel. Cache, environment, raw-data, and credential
paths are rejected. The existing `MANIFEST.in` needs no change.

Export locked runtime requirements and independently verify wheel and sdist
installations from cache:

```powershell
& ./.tools/uv/bin/uv.exe export --locked --offline --no-dev --no-emit-project --format requirements.txt --output-file .tools/runtime-requirements-2c.txt | Out-Null
& ./.tools/uv/bin/uv.exe venv .tools/wheel-check-2c --python .venv/Scripts/python.exe --no-managed-python --offline
& ./.tools/wheel-check-2c/Scripts/python.exe -I -B -c "import importlib.util as u; assert all(u.find_spec(n) is None for n in ['hellas_dataops','pydantic','pytest']); print('Fresh environment confirmed')"
& ./.tools/uv/bin/uv.exe pip sync --python .tools/wheel-check-2c/Scripts/python.exe --offline --require-hashes .tools/runtime-requirements-2c.txt
& ./.tools/uv/bin/uv.exe pip install --python .tools/wheel-check-2c/Scripts/python.exe --offline --no-deps dist/milestone-2c/hellas_dataops-0.1.0.dev0-py3-none-any.whl
& ./.tools/wheel-check-2c/Scripts/python.exe -I -B scripts/verify_installed.py
& ./.tools/uv/bin/uv.exe pip check --python .tools/wheel-check-2c/Scripts/python.exe

& ./.tools/uv/bin/uv.exe venv .tools/sdist-check-2c --python .venv/Scripts/python.exe --no-managed-python --offline
& ./.tools/sdist-check-2c/Scripts/python.exe -I -B -c "import importlib.util as u; assert all(u.find_spec(n) is None for n in ['hellas_dataops','pydantic','pytest']); print('Fresh environment confirmed')"
& ./.tools/uv/bin/uv.exe pip sync --python .tools/sdist-check-2c/Scripts/python.exe --offline --require-hashes .tools/runtime-requirements-2c.txt
& ./.tools/uv/bin/uv.exe pip install --python .tools/sdist-check-2c/Scripts/python.exe --offline --no-deps dist/milestone-2c/hellas_dataops-0.1.0.dev0.tar.gz
& ./.tools/sdist-check-2c/Scripts/python.exe -I -B scripts/verify_installed.py
& ./.tools/uv/bin/uv.exe pip check --python .tools/sdist-check-2c/Scripts/python.exe
```

The install check verifies 17 imports inside each environment's site-packages,
rejects editable/user-site imports, and exercises configuration and partial-day
planning. These checks validate packaging on this platform/interpreter; they
do not establish byte-for-byte reproducible build archives.

## Remaining assumptions and proposed Milestone 2D

Plans record intent only. Provider option compatibility, availability, and actual
ERA5 delivery remain unverified live. No fresh documentation or terms review
was performed offline. Service eligibility for a promoted public portfolio is
separate from the weather data licence; the code licence remains unselected.

Recommended next scope, requiring approval: a small HTTP client consuming these
specifications with injected mocked transport, explicit timeouts/response size
bounds, JSON decoding, typed status/transport errors, and deterministic success,
malformed-response, rate-limit, and failure tests. Any HTTP dependency requires
approval. Live calls, persistence, ingestion/filtering integration, and Git commits
remain excluded unless separately authorized.
