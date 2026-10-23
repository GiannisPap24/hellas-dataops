# Portfolio release handoff

Prepared 2026-10-24. The owner approved **MIT**, **Copyright (c) 2026 GiannisPap24**,
and one reviewed local checkpoint. **Public repository creation and push are
not authorized yet.** None of the publication steps below has been executed.

## Local release scope

The accepted synthetic MVP and its contracts remain unchanged. The outstanding
final-audit changes are included, plus the MIT licence, package metadata,
licence-distribution verification and this handoff. No dependency version,
database schema, migration, API or weather-source behaviour changes.
The accepted 133-test PostgreSQL result and fresh 168-hour real HTTP demo remain
historical evidence; no expensive host tests are repeated for this metadata change.
See [release readiness](release-readiness.md), [setup](setup.md),
[demo evidence](demo.md#final-acceptance-fresh-host-demonstration) and
[third-party notices](licensing.md).

The README presents Python/SQLAlchemy Core, PostgreSQL, FastAPI and Docker;
UTC validation, atomic chunks, replay, explicit resume, bounded read queries,
synthetic-only execution, local benchmark methodology and limitations.
The operational CLI never enables live provider access. MIT does not override
Open-Meteo service terms or data attribution requirements.

## Local checkpoint

From the existing checkout in ordinary Windows PowerShell:

```powershell
& .\artifacts\checkpoint-portfolio-release.ps1 -ReviewOnly
& .\artifacts\checkpoint-portfolio-release.ps1
```

The first command is read-only. The second creates one local commit only after
checking expected main/HEAD, every reviewed repository file and hash, exact
changed/staged inventories, approved noreply author/committer, Git overrides,
hooks/filters and staged diff. It never pushes or changes Git identity/settings.
Any mismatch stops execution. Do not replace these commands with `git add .`.
The script and manifest are ignored local artifacts; no credentials are included.
If the script is blocked by host policy, retain current files and request review;
do not change execution policy or filesystem permissions.

Expected parent: `652aba66b980e97330cdfc2a372515d03b95f0a3`.
Commit message: `chore: prepare MIT-licensed portfolio release`.
Record the resulting full commit hash and require a clean working tree before
considering publication. Do not squash away the accepted ancestor: guarded
host runners require the documented full-history lineage.

## Publication steps: only after separate owner approval

1. Run the local checkpoint and review its clean-status result. Recheck that the
   source inventory contains no local reports, target configurations or datasets.
   Publish the already reviewed documentation summaries, not the artifacts folder.
2. Sign into GitHub as **GiannisPap24**. Open the **New repository** page, select
   owner **GiannisPap24**, name **hellas-dataops**, and visibility **Public** only
   after approval. If that name already exists, stop and review it; never overwrite
   or delete an existing repository.
3. Use the description below. Leave initialization with README, .gitignore and
   licence **off**: this checkout already supplies all three. Do not choose a
   template or add unrelated files. Create the empty repository.
4. In ordinary PowerShell at the existing local checkout, run the guarded sequence
   below after inserting the full checkpoint hash printed by the script.
   It adds a remote only when none exists and pushes only main, without tags,
   force, mirror or all-branch options. If any prerequisite fails, stop; do not
   overwrite existing remote configuration or force-push.

```powershell
$releaseCommit = '<full hash printed by the local checkpoint>'
if ($releaseCommit -notmatch '^[0-9a-f]{40}$') { throw 'Set the reviewed release commit first' }
if ((git branch --show-current) -ne 'main' -or $LASTEXITCODE -ne 0) { throw 'Expected main' }
if ((git rev-parse HEAD) -ne $releaseCommit -or $LASTEXITCODE -ne 0) { throw 'Release HEAD mismatch' }
$changes = @(git status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $changes.Count) { throw 'Working tree must be clean' }
$remotes = @(git remote)
if ($LASTEXITCODE -ne 0 -or $remotes.Count) { throw 'Existing remote requires review; nothing changed' }
$approved = 'GiannisPap24 <260315283+GiannisPap24@users.noreply.github.com>'
foreach ($format in @('%an <%ae>', '%cn <%ce>')) {
    $identity = git log -1 "--format=$format"
    if ($LASTEXITCODE -ne 0 -or $identity -cne $approved) { throw 'Checkpoint identity mismatch' }
}
git remote add origin 'https://github.com/GiannisPap24/hellas-dataops.git'
if ($LASTEXITCODE -ne 0) { throw 'Remote creation failed' }
$destination = git remote get-url --push origin
if ($LASTEXITCODE -ne 0 -or $destination -cne 'https://github.com/GiannisPap24/hellas-dataops.git') { throw 'Unexpected push destination' }
# This is the publication operation; execute only with separate approval.
git -c push.followTags=false push --set-upstream origin refs/heads/main:refs/heads/main
if ($LASTEXITCODE -ne 0) { throw 'Push failed; inspect without force or automatic retry' }
```

5. Use GitHub's normal authentication flow; never paste a token into repository
   files, command URLs or screenshots. Inspect the public repository: expected
   commit, main default branch, README/diagram rendering, MIT detection, docs links,
   synthetic attribution and no generated/private files. Add topics and pin the
   repository to the profile if desired. Do not create a release tag, upload
   artifacts, enable hosting or authorize live weather access as a side effect.

Reference: [GitHub repository creation](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)
and [importing existing local code](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github).
No GitHub account, repository availability, remote or authentication was accessed
or verified during local preparation; verify those at the authorized handoff.

## Suggested profile presentation

**Description:** Reliable weather data engineering in Python: validated ingestion,
resumable PostgreSQL persistence and read-only FastAPI, with a reproducible synthetic demo.

**Topics:** `python`, `data-engineering`, `postgresql`, `fastapi`, `sqlalchemy`,
`alembic`, `pytest`, `data-validation`, `etl`, `portfolio`.

Describe this as a personal project demonstrating tested engineering contracts.
Do not claim commercial employment, live provider ingestion, hosted production
traffic or production-scale performance. Known local/Windows, timeout, process-crash
and pagination-snapshot limitations remain documented; none was removed by licensing.


## Final preparation verification

Executed on 2026-10-24 with the existing Windows x64 / Python 3.14.4 environment:

- **974 offline tests passed, zero failed**, in 2.59 seconds. Network protections
  remain enabled; PostgreSQL and demo tests were not rerun.
- Ruff lint and formatting pass. Offline uv lock consistency and locked sync
  pass with the same `uv.lock` hash and dependency versions; 40 installed
  development packages are compatible. SQLAlchemy 2.1.6 is verified pure Python.
- Source distribution and wheel built offline. The checker validates **100 source
  assets**, including 3 JSON fixtures and 16 docs, and **34 wheel modules** plus
  the packaged migration template. Both contain the approved licence and declare
  `License-Expression: MIT` and `License-File: LICENSE`.
- Separate fresh wheel/sdist environments each pass **45 isolated imports**, the
  installed MIT/copyright checks, offline migration/API checks and compatibility
  for 27 runtime packages. These are cache-backed installations, not a fresh-machine
  or bit-identical build claim. Final documentation changes are followed by a
  rebuild/content check; application code is unchanged from installed checks.
- Nine reachable commits / 184 unique historical blobs and 106 intended repository
  files reviewed; no high-confidence secret pattern found. Historical and effective
  author/committer identities use the approved noreply address. Ignore checks pass.
  This does not establish the absence of all security or privacy issues.

The checkpoint script's read-only validation and PowerShell parser must pass
before handoff. Normal Git writes are unavailable because the sandbox explicitly
mounts this repository's `.git` read-only. No escalation or trial metadata write
is attempted. Owner execution is the remaining local checkpoint step.

## Exact intended checkpoint inventory

Relative to `652aba66b980e97330cdfc2a372515d03b95f0a3`: **21 files**, comprising
the 14 reviewed audit files and seven additional affected paths for licensing
and release preparation. No application module or dependency lockfile changes.

Created (5):

- `LICENSE`
- `docs/licensing.md`
- `docs/publication.md`
- `docs/release-readiness.md`
- `docs/setup.md`

Modified (16):

- `.env.example`
- `AGENTS.md`
- `MANIFEST.in`
- `README.md`
- `docs/api.md`
- `docs/architecture.md`
- `docs/data-contract.md`
- `docs/demo.md`
- `docs/development.md`
- `docs/postgres-testing.md`
- `docs/vscode.md`
- `pyproject.toml`
- `scripts/verify_distributions.py`
- `scripts/verify_installed.py`
- `scripts/verify_postgres.py`
- `tests/unit/test_postgres_runner.py`

The guarded script and manifest are additional **ignored local artifacts**, not
commit candidates. Build outputs, environments, test reports and ownership
configuration also remain ignored. No public release tag, remote or deployment
has been created.
