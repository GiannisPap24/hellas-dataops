# Windows development setup

Verified on **Windows x64, CPython 3.14.4, and uv 0.13.4**. `.python-version`
records that interpreter; no other Python version or operating system has been
tested. No Python download or global package installation is required.

Commands explicitly select the verified project-local uv executable. They do
not depend on whichever uv happens to be on PATH.

Runtime dependencies are Pydantic, pydantic-settings, SQLAlchemy, Alembic, and the
pure-Python PostgreSQL driver pg8000, FastAPI and Uvicorn. The separate `dev`
dependency group contains httpx2 for in-process ASGI tests, pytest, pytest-cov, Ruff, and the pinned setuptools
build backend. `uv.lock` records resolved versions and package hashes. Keep the
lockfile under version control. The HTTP client uses the standard library;
the API never invokes it. httpx2 is development-only, not an ingestion client. pg8000 replaces
Psycopg after Windows Application Control rejected its native library. Driver
imports and compatibility checks pass on Python 3.14.4. The host workflow passed
54 tests on PostgreSQL 18.6 without security-policy changes. See the
[acceptance evidence](development.md#milestone-3b1-final-acceptance).

SQLAlchemy also uses its officially supported **pure-Python source build** at
version **2.1.6**, because Windows Application Control rejected its optional
Cython extension. `pyproject.toml` makes this the project-wide uv policy; it does
not change dependency versions or security settings. See the
[compatibility remediation](development.md#sqlalchemy-pure-python-remediation)
for evidence and source-build/cache requirements. The flag is a build setting;
setting it only at runtime does not fix an installed native wheel.

### Windows setup and installation

Run from the repository root in PowerShell, using an already installed Python
3.14.4. These settings affect only this shell session. Activation and execution
policy changes are unnecessary. If `python --version` reports another version,
stop and select an already approved interpreter explicitly.

```powershell
python --version
$basePython = (Get-Command python).Source
New-Item -ItemType Directory -Force -Path .tools/tmp | Out-Null
$env:TEMP = Join-Path $PWD '.tools/tmp'
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_LINK_MODE = 'copy'
```

Bootstrap uv locally **once**, if `.tools/uv/bin/uv.exe` does not exist. The
initial resolution selected uv 0.13.4; the repeatable bootstrap pins that version:

```powershell
& $basePython -I -B -m pip --isolated --disable-pip-version-check install --index-url https://pypi.org/simple --only-binary=:all: --no-deps --no-cache-dir --retries 0 --timeout 15 --target .tools/uv uv==0.13.4
```

Install the project and development tools into `.venv` from the existing lock:

```powershell
& ./.tools/uv/bin/uv.exe --version
& ./.tools/uv/bin/uv.exe sync --locked --python $basePython --no-managed-python --default-index https://pypi.org/simple
& ./.venv/Scripts/python.exe -I -B scripts/verify_sqlalchemy.py
& ./.venv/Scripts/python.exe -I -B -c "import hellas_dataops; print('Installed package import OK')"
```

Stop if a command fails. `--locked` rejects stale dependency metadata rather
than silently updating the lock. Initial setup needs PyPI access. Once the local
cache is populated, this verification also works offline:

```powershell
& ./.tools/uv/bin/uv.exe lock --check --offline
$lockBefore = (Get-FileHash uv.lock -Algorithm SHA256).Hash
& ./.tools/uv/bin/uv.exe sync --locked --offline
if ($LASTEXITCODE -ne 0) { throw 'Locked sync failed' }
if ($lockBefore -ne (Get-FileHash uv.lock -Algorithm SHA256).Hash) { throw 'Lockfile changed' }
```

To replace an older compiled SQLAlchemy installation once (project root, same
local cache/temp settings), run:

```powershell
& ./.tools/uv/bin/uv.exe sync --locked --no-binary-package sqlalchemy --reinstall-package sqlalchemy --python .venv/Scripts/python.exe
if ($LASTEXITCODE -ne 0) { throw 'Compatible dependency installation failed' }
& ./.venv/Scripts/python.exe -I -B scripts/verify_sqlalchemy.py
if ($LASTEXITCODE -ne 0) { throw 'Pure-Python SQLAlchemy verification failed' }
```

This downloads/builds only as needed from the configured standard package index.
Offline operation requires the source archive, build requirements and compatible
built wheel already cached. Ordinary future `uv sync --locked` uses the same
project policy. The PostgreSQL runner forces an **offline** SQLAlchemy reinstall
and verifies it before importing storage or contacting Docker. It stops safely
if the required cache is missing. Generic pip installation outside this project
does not inherit uv settings; follow the documented project setup.

Only a deliberate dependency update should run `uv lock` without `--check`.
The original lock was generated with `uv lock --python $basePython
--no-managed-python --default-index https://pypi.org/simple`, then regenerated
after pinning setuptools to its resolved version. It was not handwritten.

### Offline tests and quality checks

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
& ./.venv/Scripts/python.exe -I -B -m pytest -p no:cacheprovider -p pytest_cov --cov=src/hellas_dataops --cov-report=term-missing --cov-report=xml:coverage.xml
& ./.venv/Scripts/ruff.exe check .
& ./.venv/Scripts/ruff.exe format --check .
```

pytest uses importlib mode with **no `pythonpath` injection**. Python `-I` ignores
PYTHONPATH and user-site packages. Plugin autoloading is disabled and pytest-cov
is loaded explicitly; the network guard remains enabled. No database, container,
credential, or weather API is needed. Generated coverage reports are ignored.
Coverage uses the source directory so Alembic's dynamically loaded environment
and revision modules are measured alongside ordinary package imports.

### Build and verify distributions

After locked synchronization, build offline using the backend in `.venv`:

```powershell
& ./.tools/uv/bin/uv.exe build --offline --no-build-isolation --python .venv/Scripts/python.exe --out-dir dist
```

This produces `dist/hellas_dataops-0.1.0.dev0.tar.gz` and
`dist/hellas_dataops-0.1.0.dev0-py3-none-any.whl`; uv builds the wheel from the
source distribution. `--no-build-isolation` here deliberately uses the locked
backend in the already isolated project environment, not global Python packages.
The source distribution includes documentation, tests, the lockfile, and the
installation verification script through `MANIFEST.in`.

Both distributions were installed separately into fresh local environments using
locked runtime dependencies, then checked with `python -I`. See
[the exact distribution verification commands and results](development.md).
These checks validate installation and imports, not byte-for-byte identical
archives or compatibility with untested platforms.

## Checkout and host prerequisites

Use a full-history Git checkout on `main` containing accepted checkpoint
`652aba66b980e97330cdfc2a372515d03b95f0a3`. The host runners accept this checkpoint
or descendants and record the actual HEAD. They reject an unrelated repository,
missing ancestry, another branch, or unreviewed dirty paths before Docker.
Source archives and shallow checkouts lacking the anchor can build and run
unit tests, but cannot run the guarded Docker workflows. Obtain full history
through the owner's ordinary authorized Git workflow; do not remove the guard.
Ancestry identifies project history, not the trustworthiness of future commits:
review code before executing it. No absolute checkout location is required.

Install Python 3.14.4 x64 and Docker Desktop through their supported host setup
before these commands; system installation is not automated by this project.
Docker Desktop must report a Linux Engine accessible through `desktop-linux`.
Only the explicit host demo/database tasks contact Docker. A fresh machine needs
network access to the standard package index and official PostgreSQL image;
"offline tests" describes test execution, not a cache-free installation.

Open VS Code with `code --reuse-window .` after setup. Follow [VS Code](vscode.md),
[the demonstration](demo.md), and [PostgreSQL tests](postgres-testing.md).
Other platforms and GUI debugger interaction remain unverified. The full setup
was not replayed on a new Windows machine during the final audit; isolated
cache-backed installations provide the narrower package verification recorded
in [release readiness](release-readiness.md).
