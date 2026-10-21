# Milestone 2D: bounded HTTP client verified offline

Verification date: 2026-10-21. Windows x64, CPython 3.14.4, project-local
uv 0.13.4. Starting checkpoint:
`7dc14d2ce942f07a5fedf9ac5761355cac268b48` on `main`; working tree initially clean.
The user authorized 2D and prohibited pushing. This milestone creates no commit,
remote, live weather request, service, database, or global configuration change.

## Scope and design

Implemented the 2D proposal recorded in `milestone-2c.md`: a small synchronous
HTTP client consuming existing request specifications, with injected streaming
transport, explicit timeout and response limits, strict JSON decoding, typed
status/transport/response failures, and deterministic offline tests.

Python's standard-library `http.client.HTTPSConnection` provides the concrete
adapter, avoiding a new dependency. It uses the fixed archive host and path,
verified TLS, exact planned query parameters, one request per call, no redirects,
and no automatic retries. It closes resources on success and failure. The result
is decoded JSON, leaving weather normalization and retention filtering separate.

The default body cap is 2 MiB (configurable up to 16 MiB), the read chunk cap is
64 KiB, and JSON nesting is limited to 64 containers. The default 10-second socket
timeout is **not** a total deadline and does not bound system DNS resolution.
No performance or live compatibility claim follows from these tests. See
`data-contract.md#implemented-http-retrieval` for the full API and limitations.

New files:

- `src/hellas_dataops/sources/open_meteo_client.py`
- `tests/unit/test_open_meteo_client.py`
- `docs/milestone-2d.md`

Updated files: `AGENTS.md`, `README.md`, `docs/architecture.md`,
`docs/data-contract.md`, `docs/development.md`, `sources/__init__.py` under the
package, and `scripts/verify_installed.py`. Existing domain, planner, normalizer,
tests, synthetic JSON fixtures, and network guard are preserved. `pyproject.toml`,
`uv.lock`, and `MANIFEST.in` are unchanged. Generated artifacts and environments
remain in ignored repository-local directories.

## Quality evidence

- Baseline: 414 tests passed before changes.
- Final suite: **511 passed, 0 failed**, including 97 new client cases.
- Client: 149/149 statements and 68/68 branches covered (100%).
- Entire implemented package: 467/467 statements and 184/184 branches (100%).
- Ruff lint passed; all 34 Python files passed formatting checks.
- Offline lock check, locked sync, and installed-package compatibility checks
  passed for 18 packages. Lock SHA-256 is unchanged:
  `820DA87A498095A57895A38548EB46C4D07AB7ECE3B64FA9B21E0D9ED3A86390`.

Tests cover complete synthetic weather retrieval and subsequent full-interval
normalization, preservation of nulls and Unicode, exact body limits, oversized
declared/streamed responses, malformed framing, duplicate headers, content types,
compression rejection, invalid JSON/UTF-8/numbers/duplicate keys/deep nesting,
HTTP errors including 429, Retry-After metadata, network/TLS/timeouts, cleanup,
and invalid limits before I/O. Default-adapter tests feed real `HTTPResponse`
parsing from in-memory wire bytes for content-length, chunked, and truncated
responses; socket/DNS guards remain enabled. No provider fixture was fetched.

An initial test assumed Python would reject JSON with 2,000 nested arrays; that
assumption did not hold on this interpreter. The client now checks an explicit
64-container limit before decoding, with boundary and escaped-string tests.
No test or safety guard was removed to obtain the passing result. Coverage
demonstrates exercised code paths, not all input combinations or live correctness.

Run from the repository root in PowerShell, stopping on any command failure:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider -p pytest_cov --cov=hellas_dataops --cov-report=term-missing --cov-report=json:artifacts/coverage-2d.json --cov-report=xml:coverage.xml -q
& ./.venv/Scripts/ruff.exe check .
& ./.venv/Scripts/ruff.exe format --check .
```

## Distribution verification procedure

Both wheel and source distributions built and passed offline installation
verification in fresh environments. Archive checks matched **43 source assets**
byte-for-byte (including 3 JSON fixtures and 5 documentation files) and **15 wheel
modules**. Each fresh installation passed 18 isolated module imports, the
configuration/planner/mock-client smoke checks, and dependency compatibility
checks for 8 installed runtime packages. No forbidden archive paths were found.

The source archive contains docs, tests, JSON fixtures, lockfile, and verification
scripts; the wheel contains runtime modules and README metadata. Use fresh
verification directory names on repeat runs; do not overwrite environments.
Stop after any failed command. No downloads or global installations are needed.

```powershell
function Invoke-Checked {
    $command, $arguments = $args
    & $command @arguments
    if ($LASTEXITCODE -ne 0) { throw "Verification command failed: $command" }
}
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_LINK_MODE = 'copy'
$env:TEMP = Join-Path $PWD '.tools/tmp'
$env:TMP = $env:TEMP
Invoke-Checked ./.tools/uv/bin/uv.exe lock --check --offline
Invoke-Checked ./.tools/uv/bin/uv.exe sync --locked --offline
Invoke-Checked ./.tools/uv/bin/uv.exe pip check --python .venv/Scripts/python.exe
Invoke-Checked ./.tools/uv/bin/uv.exe build --offline --no-build-isolation --python .venv/Scripts/python.exe --out-dir dist/milestone-2d
Invoke-Checked ./.venv/Scripts/python.exe -I -B scripts/verify_distributions.py dist/milestone-2d
Invoke-Checked ./.tools/uv/bin/uv.exe export --locked --offline --no-dev --no-emit-project --format requirements.txt --output-file .tools/runtime-requirements-2d.txt | Out-Null

foreach ($kind in @('wheel', 'sdist')) {
    $envPath = ".tools/$kind-check-2d"
    if (Test-Path $envPath) { throw 'Use a fresh verification directory' }
    Invoke-Checked ./.tools/uv/bin/uv.exe venv $envPath --python .venv/Scripts/python.exe --no-managed-python --offline
    $checkPython = "$envPath/Scripts/python.exe"
    Invoke-Checked $checkPython -I -B -c "import importlib.util as u; assert all(u.find_spec(n) is None for n in ['hellas_dataops','pydantic','pytest']); print('Fresh environment confirmed')"
    Invoke-Checked ./.tools/uv/bin/uv.exe pip sync --python $checkPython --offline --require-hashes .tools/runtime-requirements-2d.txt
    $package = if ($kind -eq 'wheel') { 'dist/milestone-2d/hellas_dataops-0.1.0.dev0-py3-none-any.whl' } else { 'dist/milestone-2d/hellas_dataops-0.1.0.dev0.tar.gz' }
    Invoke-Checked ./.tools/uv/bin/uv.exe pip install --python $checkPython --offline --no-deps $package
    Invoke-Checked $checkPython -I -B scripts/verify_installed.py
    Invoke-Checked ./.tools/uv/bin/uv.exe pip check --python $checkPython
}
```

`verify_installed.py` rejects editable/source-tree/user-site imports and exercises
configuration, planning, and a mocked HTTP success in each installed package.
These are installation checks on this interpreter/platform, not byte-for-byte
archive reproducibility guarantees or live-provider checks.

## Remaining work

Live calls and provider/terms verification, automatic retry/backoff policies,
retention filtering and ingestion orchestration, response provenance persistence,
and PostgreSQL schema/migrations remain unimplemented. The existing roadmap's
next numbered milestone is 3 (PostgreSQL design and persistence); starting it
requires separate authorization. No implicit continuation is part of 2D.
