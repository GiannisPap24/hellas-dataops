# VS Code workspace

From the existing checkout root after [setup](setup.md), open it without moving
or duplicating files:

```powershell
code --reuse-window .
```

The launcher and extensions were found locally, but Codex's sandbox reported
EPERM when the launcher accessed VS Code's application/log directories. A desktop
window was not opened or verified from Codex. The owner subsequently reported
that the command completed without a CLI error in ordinary Windows PowerShell.
That confirms the reported command outcome, not visual inspection or debugger use.
No global editor settings or security policies were changed.

## Python, tests and formatting

Use **Python: Select Interpreter** and select `.venv/Scripts/python.exe` if an
older saved interpreter overrides the workspace default. The verified environment
is Python 3.14.4 with the existing locked dependencies and pure-Python SQLAlchemy.
No interpreter or dependency installation runs automatically on folder open.

The Testing view discovers only `tests/unit`. `.vscode/testing.env` disables
pytest plugin autoload; it contains no credentials. The existing test fixtures
still block socket/DNS calls. Coverage is explicitly enabled by the documented
command in development.md, not by automatic plugin discovery.

Use **Tasks: Run Task**:

- **Hellas: offline tests**: project Python, isolated mode, all unit tests.
- **Hellas: Ruff quality checks**: lint followed by formatting verification.
- **Hellas: PostgreSQL verification (host, explicit)**: opt-in disposable database
  tests via `verify-postgres.ps1`; requires Docker Desktop's Linux Engine.
- **Hellas: end-to-end demo (host, explicit)**: `demo.ps1`; creates a new dedicated
  disposable environment and performs the synthetic demo.

Tasks are process tasks with separately passed arguments, workspace-relative paths,
project-local executables and no automatic execution. The two host tasks use
PowerShell `-NoProfile -File`; neither changes execution policy nor deletes volumes.
If local script policy blocks execution, use the existing documented Python entry
point rather than altering Windows policy. Ruff is the Python formatter; use
**Format Document** deliberately. Formatting-on-save is not imposed.

## Debug the actual local API

After a demo supplies a retained target configuration, choose
**Hellas: local synthetic database API** in Run and Debug. Enter that exact run's
`.tools/postgres/<reported-project>/target.json` path at the prompt. It is a file
path, not a password or connection URL. The existing application launcher validates
the disposable target and binds only `127.0.0.1:8000`. No database is populated and
no migration runs merely by starting the debugger. The debug configuration uses
the project Python, isolated mode and the accepted application module.

If port 8000 is occupied, stop your own debug session or use the documented manual
launcher with another unused port; never terminate an unrelated process.
Visit `http://127.0.0.1:8000/docs` or `/openapi.json`; `/health/ready` verifies the
configured database. Swagger browser assets use an external CDN. No weather API
is called. Stop Debugging stops the debug session; Docker resources are retained.
GUI debugging itself still requires verification in the owner's VS Code session.

## Codex

The normal launcher listed `openai.chatgpt@26.901.22334`, along with Python,
Python Debugger and Ruff. No extension was installed or upgraded. Open the Codex
panel in this workspace, sign in through its normal UI if required, and begin with
AGENTS.md and the current milestone. Keep requests limited to this repository;
use host tasks for Docker and retain normal approval/security controls.

Official reference: [OpenAI Codex IDE documentation](https://developers.openai.com/codex/ide).
Workspace recommendations contain only Python, Python Debugger, Ruff and Codex.
Extension listing does not prove authentication, GUI availability or debugger
execution. Credentials must never be placed in settings, launch configurations,
tasks, testing.env, or versioned files.
