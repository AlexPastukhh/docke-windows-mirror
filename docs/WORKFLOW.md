# Mirror Workflow: поведение и безопасность

## Docker → GitHub → Windows: branch-aware workflow inspector (new default)

The standalone `Fortis-Windows-Mirror-Status.ps1` GUI loads
`Mirror-Workflow.UI.cs`. The original main-only `Mirror-Status.UI.cs` is retained
as historical/source reference, and `-Cli` still runs the old text-only
`Mirror-Status.Report.ps1` (main-only). The new UI is **Windows PowerShell 5.1 / WinForms**,
not JavaScript. It uses the Docker-first read-only exporter
`tools/Export-Docker-Branch-Workflow.py` inside `fortis-repo-test`; keep that
exporter in the Docker Git checkout. **Docker changes reach Windows only after Git push and a Windows pull.**

**Source of truth and screen:** the table enumerates Git repositories under
Docker `/workspace/repo/Repos/` (not Windows folders), one row per repository.
If Docker is unavailable, it shows an error instead of a misleading empty list.
Windows clone is expected under `%USERPROFILE%\Repos\<docker-folder-name>`;
missing clones remain visible as `НЕТ КЛОНА WINDOWS`. Windows-only folders are
not added. The UI shows a compact toolbar on top, a full-width ListView with
13 columns separated by `Docker · ...`, `Windows · ...`, and `Workflow · ...`,
then a multiline scrollable full-detail panel **below** the table. Every table
column is represented as an untruncated detail line in the identical order.
Both `Другие ветки Docker` and `Другие ветки Windows` show the same 13 full
fields for each other branch after the selected branch's primary details.

**Two independent branch selectors per environment:** `Ветка для просмотра`
changes only the branch whose SHA/status is displayed; **no Git checkout**.
The Docker view initially shows Docker's active branch. The Windows view is
**not inferred from Docker**: until Windows is checked it is unknown; on Check
it defaults to the actual Windows active branch (often `main`). Only a view
branch explicitly selected by the user persists across further checks. In the
details pane, a dedicated read-only `MAIN` block always shows known GitHub,
Docker-local and Windows-local `main` SHAs separately from the active Docker
branch. Dirty Docker feature branches must never be presented as a reason to
push their changes into GitHub `main`. When clean Windows `main` is verified
behind GitHub `main`, the workflow offers `Pull Windows main` even if Docker
is editing an unrelated feature branch; the guarded Windows fast-forward rules
are unchanged.

`Активная ветка` always reports actual `symbolic-ref HEAD` from that environment;
clicking it offers local existing branches for an explicit Git switch (with
confirmation and fresh preflight). Switching does **not** change the branch
selected for viewing. The overall workflow state is always derived from the
actual active Docker and Windows branches, never from their view selectors.
All-`main` clean equality is green `АКТУАЛЕН`; equality on a non-main branch is
`АКТУАЛЕН · НЕ MAIN` (warning rather than error). It also distinguishes
Docker unstaged/uncommitted files, Docker commits ahead of GitHub, Windows
behind, unexpected Windows commits ahead (error), divergent history, missing
Windows clones and unknown/unverified state. A different SHA alone is **not**
called ahead or behind. On user-triggered Check, GitHub branches are obtained
via verified `ls-remote --heads`; Docker ancestry is checked in its Git object
database with exact GitHub SHA and Windows ancestry in the Windows clone. If
required Git objects are missing, direction remains `НЕИЗВЕСТНО` instead of
guessed. Windows may fetch **Git metadata only** for its active branch to get
exact counts, without changing worktree files or its checked-out branch.

**Operating behavior:** opening the window reads the Docker inventory only;
remote GitHub/Windows checks require `Проверить выбранные` or `Проверить все`.
Checks execute off the UI thread and can be cancelled. Status and row
contents refresh **after each repository completes** (GitHub, Windows and the
Docker ancestry check), showing `Проверяется N из M` / `Проверено N из M`
during the run instead of waiting for the final repository. On cancellation,
already completed rows remain visible; unprocessed rows retain their previous
state. Choosing the already-active branch in either `Активная ветка` selector
is a no-op with no Git call. The branch-menu cleanup is deferred until after
its WinForms close/click handlers return. Docker read-only export never
fetches, pushes or changes a worktree. The owner-only actions are:

- `Stage Docker` opens a checklist of current **active Docker** working-tree
  changes (nothing selected by default). Only checked Git-reported paths are
  staged with literal Git pathspecs. The preview includes an SHA-256 digest of
  the complete NUL-delimited porcelain status; Stage rechecks active branch,
  HEAD and that exact digest before writing. No blanket `git add -A` and no
  Windows Git write. For safety, more than 500 changed entries require direct
  owner-controlled Docker Git instead of using the GUI.
- `Commit Docker` previews **all currently staged/indexed files**, asks for an
  explicit message (UTF-8; may contain newlines), and requires confirmation.
  The helper checks the SHA-256 of the raw staged index diff plus active
  Docker branch and HEAD before running normal `git commit` with hooks and
  signing disabled. A Commit does not stage remaining unstaged files. Changes
  appearing between preview and commit invalidate the digest.
- `Push Docker` previews Docker HEAD and the exact current GitHub branch SHA
  via `ls-remote`, then requests confirmation. It rechecks active branch,
  HEAD and GitHub SHA; pushes **only** `HEAD:refs/heads/<active-branch>`
  using standard non-forcing `git push`. No force, automatic Windows Pull,
  branch switch, tagging or service restart. Uncommitted working-tree files
  are never included in a Push. A not-yet-published branch requires its own
  confirmation as part of the same Push dialog. Authentication errors stop
  the operation rather than prompting through the UI.
- These three buttons call `tools/Docker-Mirror-Git-Actions.py` **inside**
  `fortis-repo-test` as uid 1000; the Windows process is a UI/controller only.
  The operation target is always the **actual active Docker branch**, not
  the branch selected for viewing. Stage/Commit/Push are blocked for protected
  infrastructure names (`tunnel`, `rd-graphs`, `script-control-center`,
  `docke-windows-mirror`, `security`) in this workflow, to avoid changing service-critical
  repositories through a general status UI. Use a deliberate Docker
  development/review workflow to modify those repositories.
- `Pull Windows` only for one verified clean Windows clone, the currently
  active **and viewed** Windows branch, approved Docker/Windows origin, and
  confirmed `WindowsAhead == 0`, `WindowsBehind > 0`. It confirms the exact
  displayed GitHub SHA, repeats GitHub/Windows preflight, fetches the selected
  branch, checks `FETCH_HEAD` still equals that SHA, checks HEAD/branch/clean
  worktree again, and performs **`git merge --ff-only FETCH_HEAD`**. The button
  is called Pull but uses exact-SHA fetch + fast-forward merge to prevent
  accidentally importing a newer unreviewed GitHub commit. No forced reset,
  stash, force-push, auto-restart or deploy. Configured Git file filters may
  still run on Git writes.
- Windows branch switch requires clean tree and an existing **local** target
  branch, approved matching remote, user confirmation, and a fresh branch,
  HEAD, dirty-status and target SHA check; command is `git switch --no-guess`.
- Docker branch switch is **explicit owner action**, never automatic, and
  requires a clean Docker Git tree, existing local target branch, owner
  confirmation and reverified active branch/HEAD via exporter `--switch-*`.
  Switching the shared infrastructure repositories `tunnel`, `rd-graphs`,
  `script-control-center`, `docke-windows-mirror` and `security` is blocked in the GUI/exporter.
  It can still affect files used by other running programs; do not treat the
  GUI confirmation as approval to alter protected live infrastructure.

**Windows is a receiver, not an authoring environment:** no Windows commit-all
or push button in the new default screen. Unexpected local edits or Windows
commits ahead of GitHub stop the normal workflow. Source updates do not
restart any running Fortis/RDG services. The historical TSV import and
old main-only Windows commit-all remain in the unused legacy UI source; the
new GUI does not import old `FORTIS_DOCKER_STATUS_V1` TSV files. The new
Docker branch snapshot is `FORTIS_DOCKER_BRANCH_V2` (name, active branch,
HEAD, dirty count, local branches, approved origin) and is not stored in Git.
In addition to the unchanged 13 column-detail lines, the details area lists
changed files **separately for the active Docker and Windows worktrees**, below
`ДОПОЛНИТЕЛЬНЫЕ СВЕДЕНИЯ`. Each item includes the Git porcelain status
(` M` = modified, `A ` = staged addition, ` D` = deletion, `??` = untracked,
etc.) and its path. The Docker exporter carries each status record as a
base64-encoded `F` line, so tabs, spaces and Git-quoted filenames do not
corrupt the snapshot. Windows reads `git status --porcelain=v1` locally,
without changing files. Both lists describe the **currently active branches**
regardless of which branches are selected for viewing. File lists are
limited to the first 500 entries per environment/repository; if there are
more entries or a status read fails, the UI explicitly marks the list as
partial/unavailable instead of reporting a clean worktree.

**Verification:** offline Linux source/fixture tests and Docker exporter can be
checked without touching live state:

```bash
python3 -B -m unittest -q test_mirror_docker_actions test_mirror_workflow_source test_mirror_status_source test_mirror_git_history
python3 -B tools/Export-Docker-Branch-Workflow.py --stdout --repo visual-workspace
```

For **real Windows WinForms compilation**, run from the reviewed Windows Git
clone only after owner-approved source propagation:

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\Test-MirrorWorkflowCompile.ps1
```

The compiler test does not open the UI, check GitHub, run Docker, switch
branches or pull. Windows UI compilation and end-to-end owner acceptance
remain **not verified from the Docker environment**. Treat the newly
implemented UI as a source candidate until those tests pass.
