# docke-windows-mirror

Standalone Docker–Windows Mirror application.

Начать с [локального реестра Mirror](docs/use-cases/README.md) и короткого входа
[главного реестра Fortis](https://github.com/AlexPastukhh/fortis-container/blob/main/docs/use-cases/README.md).
Каждый новый чат читает полностью UC-CONT-002/003; остальные UC — по задаче,
просматривать весь каталог не требуется.

Canonical source for the independent Windows PowerShell 5.1 / WinForms interface for checking Docker,
GitHub, and Windows Git mirrors. The primary Git repositories live inside
Fortis Docker; Windows is a receiver. All actions are owner-triggered.

## Paths

Docker source: `/workspace/repo/Repos/docke-windows-mirror`

Windows clone: `%USERPROFILE%\Repos\docke-windows-mirror`

GitHub: https://github.com/AlexPastukhh/docke-windows-mirror

## Repository boundaries

All Mirror UI sources, Docker exporters, Git actions, legacy implementation,
tests, and Mirror-specific documentation are maintained **here**.

The stable Script Center launcher at
`%USERPROFILE%\Repos\script-control-center\Fortis-Windows-Mirror-Status.ps1`
is a thin compatibility wrapper which runs the standalone launcher from
`%USERPROFILE%\Repos\docke-windows-mirror`. It does not compile or run
a local copy of the old UI. Keep both Windows clones alongside one another.

[Detailed Mirror workflow and security behavior](docs/WORKFLOW.md).

## API-first browser source (Windows owner acceptance is separate)

The Windows API + local browser UI implementation is documented in [API-FIRST](docs/API-FIRST.md). Its primary view now follows the WinForms-style **13-column Docker/GitHub/Windows repository table**, with a full-copyable **«Строка»** lower panel and an optional **«Worktrees»** lower table for the selected primary repository. It reads authoritative Docker worktree inventory and Windows primary state, plus read-only GitHub main/PR/CI through Windows owner-managed gh. See [Windows GitHub reader](docs/WINDOWS-GITHUB-READ.md) for authentication, bounds and separate owner qualification. The accepted Gateway Mirror provider supports only strict `get_repository` v1 READ (GitHub NOT_CHECKED); machine ASK Pull is absent. See [API-FIRST](docs/API-FIRST.md) for current source limits and [the accepted P0 contract](docs/MIRROR_API_CONTRACT_V1.md) for versioned migration and subsequent activation gates. The owner reported basic Browser/Pull operation after updating Windows; complete negative/crash/rollback qualification and machine ASK acceptance remain **pending**. Six source-only observation READ v2 operations are described in [READ-V2](docs/READ-V2.md); no launcher, credential or v2 grant is automatically activated. Source includes an independent owner-local Browser Pull path with single-click exact-SHA preflight, not a Gateway ASK. The existing WinForms launcher is retained until Windows owner acceptance. Do not interpret the source candidate as a released replacement.

## Windows

From the Windows clone in PowerShell 5.1:

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\Test-MirrorWorkflowCompile.ps1
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\Fortis-Windows-Mirror-Status.ps1
```

Windows runtime requires `docker.exe`, `git.exe`, a running container called
`fortis-repo-test`, and the matching Docker files at the path above.
The launcher checks the Docker repo list on startup; GitHub and Windows
checks happen only after clicking a check button. Browser **Check selected/all**
uses an owner-session CSRF-protected POST: after the Windows gh main read it
may fetch Windows `origin main` Git metadata for a clean registered checkout
and prove ancestry. It never merges or checks out files. GET observations and
Worktrees PR/CI detail reads remain read-only; only explicit Pull can fast-forward.

## GitHub publication and legacy compatibility

Browser GitHub PR/CI/main reads use Windows gh authentication. Docker GitHub
publication continues through the existing typed Fortis Gateway client, with
its provider credentials confined to Gateway. The
Browser UI offers one **owner-local Windows main Pull** route: protected
Preview + single-click owner action with fresh exact-SHA preflight + restricted `PullWorker` (`git fetch` followed
by `merge --ff-only` on the Windows receiver). This is not a GitHub PR/push route,
not a Gateway ASK, and it never writes Docker Git state. Other Browser Git writes
(Stage/Commit/Push) remain absent. Local Pull requires a fresh Windows-gh-read GitHub
main, matching clean Docker main and clean Windows main, matching origin and
verified fast-forward **after** fetching the pinned SHA. The retained
WinForms source and `tools/Docker-Mirror-Git-Actions.py` still support local
stage/commit logic, but the helper fails closed on network GitHub fetch,
`ls-remote` and push. Do not attempt to recover the old PAT helper as a bypass.
See [WORKFLOW](docs/WORKFLOW.md) for the authorized handoff.

## Tests

From the Docker project directory:

```bash
python3 -B -m unittest -q test_mirror_workflow_source test_mirror_docker_actions
```

Offline Browser Pull security and idempotence checks run in `test_mirror_browser_pull.py`; the Node fixture tests the one-click path and absence of a second modal. The Windows compilation test must also pass on an actual Windows system.
Windows PowerShell 5.1 compilation and GUI behavior still require owner
verification on Windows after the two repository updates.


## Будущие улучшения

Единый список задач и технического долга: [BACKLOG-IMPROVEMENTS](docs/BACKLOG-IMPROVEMENTS.md). Он не заменяет действующие source/owner runbooks.

## Optional Owner Portal PowerShell executor (not activated)

`Start-OwnerPowerShellExecutor.ps1` is a **separate, opt-in Windows process**,
not part of `Start-MirrorAPI.ps1` or Script Center's Browser launcher. The
executor serves **only mTLS + bearer authenticated** HTTPS requests from the
trusted Owner Portal transport, using a dedicated secret and SQLite receipt
store. Its commands run as its **non-admin Windows process user**, not in Docker;
it is **not a sandbox**. The executor cannot independently prove human intent:
protect the TLS client key and bearer, use a dedicated low-privilege Windows
account and reviewed bind/firewall rules, and never grant these credentials
to AI/Gateway machine callers. Analyzer and external AI checks are not part of
this initial source. See `docs/OWNER-POWERSHELL.md` before operator setup.
