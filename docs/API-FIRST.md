# Mirror API-first — source candidate (2026-11-07)

This document owns the new service/frontend source usage and its current limits. The accepted target workflow remains [WORKFLOW](WORKFLOW.md); common authorization and release rules are in the container use-case registry (UC-CONT-002/005/007/009/010/012/016/019). This is **not** live Windows/Gateway acceptance.

## Components and available actions

- `mirror_api.operations` reads Docker's **Git-registered** linked worktrees, Windows primary clones, and exact GitHub heads. Ordinary GETs never fetch; the separately authorized owner check POST may fetch Windows Git metadata (`origin main`) to prove ancestry without changing Windows HEAD, index or worktree. Noncanonical worktrees remain visible, but are not eligible for native Gateway worktree mutations. An unknown status is never reported as clean/current.
- `mirror_api.server` exposes a local browser UI and JSON API on **127.0.0.1 only**; a one-use console URL establishes a SameSite/HttpOnly cookie. Normal GETs are observational. Owner-local POSTs are: **`/api/v1/repositories/<repo>/check-windows`** for bounded Git metadata fetch only and Preview → one-use Execute for guarded Windows `main` Pull. Both require exact Host, Origin, session-derived CSRF and the normal (non-preview) runtime; only the Pull Execute may move HEAD/worktree. Other browser writes are denied. No browser session grants access to the separate Gateway listener.
- `mirror_api.static` is the browser frontend. It shows primary/worktrees, Windows, branch SHA, dirty files, action blockers, targeted GitHub checks, progress/cancellation. Read-only PR metadata and Actions/Checks observations come from the **existing authenticated GitHub Gateway typed client**, invoked from the Docker Repo worker by `tools/Export-Mirror-GitHub-Read.py`. Per-repo branch SHAs also come from typed Gateway `list_branches`, never local `git ls-remote` or a Windows Git credential. PRs match the exact task branch; closed PRs get a separate typed `get_pull_request` to distinguish **merged** from merely **closed** (or remain `CLOSED_OR_MERGED_UNVERIFIED` if unavailable). Workflow runs require the **PR head SHA**, matching branch and `pull_request` event; the latest attempt per workflow is selected, not the last successful run. `requiredCI` remains `NOT_VERIFIED`: read-only workflow runs do not prove
  the GitHub UI's required checks/reviews; the owner verifies those before manual Merge. Unknown scope, missing permissions, errors and pagination truncation remain explicit rather than implying no PR/green CI.
- Browser Stage/Commit/Push write routes and their owner allowlist remain **disabled**. The old WinForms helper remains for local source compatibility, but its network `ls-remote`/fetch/push are blocked (`GATEWAY_ONLY_GITHUB_OPERATION`). GitHub publishing and PRs use the existing native typed Gateway client. Owner-local Windows check and Pull are separate from GitHub publication; both may perform constrained Windows `git fetch origin main` under the owner's Git credentials, but **only Pull** can run `merge --ff-only`.
- `mirror_api.pull`: shared Windows `main`-only fast-forward worker with exact SHA, clean Docker `main` == verified GitHub `main`, matching trusted Docker/Windows origin, repeated Windows/GitHub preflight, fixed `git fetch` and pinned `merge --ff-only`, readback and durable SQLite receipt. `mirror_api.browser_pull` authorizes a one-shot owner-local browser invocation; an additional authenticated HTTPS Gateway listener is separately configurable and disabled by default. A request with the same UUID is never executed twice after dispatch. No reset, stash, branch switch, deploy or restart.
- **Owner-local Pull latency:** the server Preview returns `timingsMs.previewTotal` plus available Docker/Windows/Gateway/ancestry observations. Execute rechecks only the selected registered Docker primary (`docker_inventory(repo)`, never the whole workspace) and fetches fresh exact Gateway **main-only** SHA (`github_main(..., details=False)`; no PR/CI fanout). All original clean/branch/origin/SHA guards, Windows Git `fetch`, `merge-base --is-ancestor`, pinned `merge --ff-only` and readback remain mandatory. The durable SQLite receipt stores bounded integer `timingsMs` for Docker, preflight, Gateway main, Windows fetch/FETCH_HEAD, post-fetch status, ancestry, merge, final readback and Execute total; even an ambiguous result keeps completed-stage timings without a Git retry. A confirmed receipt is shown immediately: the following **read-only** table refresh happens asynchronously, so a slow Gateway GET cannot hold the Pull action open. The UI displays server and browser elapsed times separately (these overlap and must not be summed). Durations from source/fixtures are not evidence of live Windows speed; owner acceptance needs an actual Windows Pull after updating installed source.
- **Mirror-provider не является действующим Gateway provider:** его исходники
  (`fortis_approvals/integrations/mirror/`) находятся как **untracked** файлы
  только в `fortis-approvals` task worktree `task/mirror-provider-20261107`;
  в принятом `fortis-approvals/main` этого каталога нет. Ни установка,
  ни регистрация, ни TLS/Windows Pull acceptance этим не подтверждаются.
  В текущем source candidate операция `pull_main` требует Gateway
  `mergeRequestId` от `github.merge_pull_request` и **не совместима** с принятым
  ручным Merge через GitHub UI. Для Docker main существует отдельная
  работающая `workspace.update_main_from_pr`, но она не включает Windows Pull.
  Provider следует отдельно переработать под проверенное owner UI merge
  (точный PR HEAD/merge SHA/текущий GitHub main, CI и строгие Windows
  preconditions), протестировать и принять через
  [UC-GW-002](https://github.com/AlexPastukhh/fortis-approvals/blob/main/docs/use-cases/integrations.md#uc-gw-002)
  и [UC-GW-005](https://github.com/AlexPastukhh/fortis-approvals/blob/main/docs/use-cases/releases.md#uc-gw-005).
  До этого **не активировать** старую реализацию как Windows Pull provider.

## Browser UI — primary table + optional worktrees (2026-11-08 source candidate)

The browser now preserves the **old WinForms presentation hierarchy**:

1. The main screen is a dense **13-column, one-row-per-Docker-primary** table:
   primary branch, GitHub comparison, Docker changes/SHA, Windows primary branch,
   Windows/GitHub state, changes/SHA, workflow status and recommended action.
   Task worktrees never become extra primary rows. Local refresh reads Docker
   and Windows without triggering GitHub; targeted and all-repo checks update
   individual rows without discarding completed results on cancellation.
2. Selecting a primary row fills the lower **«Строка»** panel with all 13
   *untruncated* field values, SHA and paths, separately labelled GitHub/Docker/
   Windows main states and complete available changed-file lists. **«Копировать
   весь блок»** copies the selected panel's whole text, not a summary.
3. The lower **«Worktrees»** tab is **opt-in only** and lists the selected
   repository's linked Docker task worktrees in a separate table. Selecting a
   worktree displays its full Git metadata, changed files and matching PR/CI
   evidence below that table. Switching tabs/rows does not run `git switch`.
4. The primary table's branch-for-view fields intentionally show the actual
   primary branch rather than offering a task-branch selector: task branches
   are exclusively in the opt-in Worktrees tab. Ahead/behind are only shown
   where the backend confirms Git ancestry; mismatching SHAs otherwise remain
   `НАПРАВЛЕНИЕ НЕИЗВЕСТНО`. The owner check POST may fetch missing Windows
   history to classify behind/ahead/diverged before offering a Pull; regular
   GET and the PR/CI-details GET remain read-only.
5. **Pull Windows is now an owner-local action**: the selected Docker primary
   and Windows `main` must both be clean, Docker `main` must match fresh GitHub
   `main`, and the Windows origin must match. Clicking **Pull Windows** once
   authorizes server Preview and one-use Execute, with no secondary modal.
   If Windows lacks the target Git objects,
   direction remains unknown until guarded `fetch`, then `merge --ff-only` is
   verified before any worktree change. **Gateway ASK Pull remains unactivated**;
   old WinForms Stage/Commit/Push/switch must not be re-enabled in the browser.

The JS behavior is covered by the dependency-free Node fixture in
`tests/mirror_table_ui_smoke.js`; `test_mirror_table_ui.py` includes that smoke
check in the existing Python unittest discovery/CI without editing workflow
permissions. Owner still needs to review rendering on actual Windows/Chrome.

## First manual test on Windows **without merging into main**

<a id="first-manual-test-on-windows-without-merging-into-main"></a>

## Manual Windows browser check

Accepted browser source is already in `main`. Use the single
[manual preview runbook](MANUAL-PREVIEW.md) for prerequisites, startup and the
reusable check procedure. `Open-MirrorPreview.ps1` is a retained task-specific
bootstrap pinned to the old `mirror-gateway-ci-20261107` worktree; it is not the
routine startup path after source merge. Do not make cleanup of an old worktree
a prerequisite for normal delivery.

## Source-only local browser run

When launched through the new `script-control-center/Fortis-Mirror-Browser.ps1`
wrapper, `Start-MirrorAPI.ps1 -OpenBrowser` asks Python to open the **same
one-use loopback session URL** in the Windows default browser. Normal direct
launch continues to print the URL without opening a browser. If browser auto-
open fails, the process remains running and the printed URL can be used manually.
No external listener, GitHub publishing endpoint or service is enabled.
Owner-local Windows Pull is available after the user's single click triggers
fresh server Preview and Execute, independent of Gateway ASK. The retained `--preview-source-task`
developer preview remains **strictly read-only** and denies browser Pull.

The browser process uses reviewed source from the Windows primary
clone and matching accepted exporters in Docker. [MANUAL-PREVIEW](MANUAL-PREVIEW.md)
owns the command and checklist; no second copy is maintained here. PR/CI/branch
reads use the existing Repo typed Gateway client and machine grant, never a
Windows PAT fallback. Observed CI success does not certify the required gate.

## Manual Windows Pull — owner-local Browser UI

On accepted Windows source (`Start-MirrorAPI.ps1`, normally via Script Center)
select one Docker primary and click **Pull Windows**. The browser first retrieves
a same-session CSRF token and POSTs `{}` to
`/api/v1/repositories/<repo>/pull-preview`. The Windows API re-reads Docker,
Windows and **fresh GitHub `main` via the existing typed Gateway reader**;
it accepts only matching registered clean Docker primary `main`, clean Windows
primary `main` with the exact expected origin, and non-equal heads with no
known ahead/diverged history. It requires Docker `main` == GitHub `main`.

If Windows does not yet have the Git object for the target SHA, **Preview does
not claim fast-forward is proven**: the worker checks ancestry after fetch. The browser
performs fresh server Preview after the owner's single click on **Pull Windows**,
then sends a one-use `previewId` and fixed confirmation marker to
`/api/v1/repositories/<repo>/pull-execute`. The server binds the preview to the
cookie session, target repo, expected Windows SHA, target SHA, origin and 120s
TTL. The click on the Pull button is the owner's confirmation; the second
browser modal is intentionally omitted. Arbitrary Git paths, branch names, commands, URLs, tokens or SHAs cannot
be supplied through Execute. This does **not** create a Fortis Gateway ASK.

The shared `PullWorker` records a durable receipt before any Git effect under
`%LOCALAPPDATA%\FortisMirror\pull-receipts.sqlite3`, repeats trust/dirty/HEAD
checks, uses Windows-owner Git authorization only for `git fetch origin main`,
compares exact `FETCH_HEAD` with approved target and proves `merge-base` ancestry.
Only then it attempts `git merge --ff-only <pinned SHA>` and checks Windows
branch/HEAD/origin/clean readback. No switch/reset/stash/force/auto-deploy.
Receipt status is bound to the initiating browser session at
`GET /api/v1/repositories/<repo>/pull-results/<requestId>`. On `UNCERTAIN` or
connection loss **do not issue a new Pull automatically**: inspect Windows and
reconcile/read back the stored result. The worker makes three bounded **Git
status readbacks only** after successful ff-only, never an additional fetch or
merge. An unconfirmed receipt includes `readbackIssues` (HEAD, branch, dirty,
origin, or Git error), without leaking file paths. The browser immediately
re-reads the original receipt once, then offers **«Проверить результат
предыдущего Pull»** as a read-only action with the original request UUID.
An unresolved UUID is kept in tab `sessionStorage` across reloads (without
credentials); the backend's session ownership remains memory-only and does
**not** survive a Mirror service restart. After process restart, operator
recovery must use the protected receipt DB, not a new Pull or a bypass of
browser session binding. The browser preview is disabled whenever
`--preview-source-task` is used. When the separate Gateway listener is enabled,
it uses the **same worker and receipt database** as owner-local Pull.

**Required Windows owner acceptance:** verify read-only access and the blocked
scenarios first, then run a controlled actual fast-forward on a reviewed safe
repo. Linux offline tests and green CI do not prove Windows credential behavior,
NTFS writes, Windows error paths, or Script Center lifecycle. Use
[MANUAL-PREVIEW](MANUAL-PREVIEW.md) for the owner test procedure.

## Browser table layout and consistent Git observation

The primary 13-column table preserves its data contract regardless of visual
layout. The owner may hide the Docker group, the Windows group, or both; with
both hidden only repository names remain (Workflow columns also hide). While
at least one group is visible the Workflow conclusion/action remains visible.
Every column has a draggable border and keyboard left/right resizing (12 px
steps); double-click resets one column, **Сбросить ширину** resets all widths.
Widths and group toggles are stored in browser-local `localStorage`, never in
Git or Gateway; invalid/out-of-range persisted widths are rejected. Search and
"Требуют внимания" are view-only and do not change the underlying rows or Git
state. The GitHub check failure explicitly invalidates the corresponding
row's remote freshness; local refresh intentionally marks remote data as not
checked. Source timestamps are shown in the selected row's detail block. Group
folding buttons are placed directly above the first respective Docker/Windows
column and remain visible even when both groups are hidden. The redundant
header guidance lines are removed.

## Fast primary inspection and explicitly detailed worktree PR/CI

The owner **«Проверить выбранный»** and **«Проверить все»** call the same
CSRF-protected `POST /api/v1/repositories/<repo>/check-windows` for one or
up to four repositories concurrently. For each repo, Docker inventory, Windows
Git and a **fresh Gateway-verified GitHub `main` branch SHA** are checked.
If Windows clean `main` differs from that SHA, the owner check may perform
bounded `git fetch --no-tags --no-recurse-submodules origin main` in the Windows
clone (45 s limit, no interactive credentials), **even if Docker main is behind
GitHub**. This metadata-only observation independently requires the verified
Windows clone/origin/main/clean state and exact fresh Gateway main; it proves
`FETCH_HEAD` equals the pinned SHA, rechecks Windows branch/HEAD/origin/clean,
and reads `merge-base`. **Pull authorization has not been relaxed:** the fresh
server Preview still requires registered clean Docker main at the exact GitHub
main SHA, along with all Windows and origin/ancestry guards.

When Docker primary main differs, the fast Gateway reader additionally uses
bounded typed `list_commits` reads (at most three pages of 50) pinned by the
first returned SHA to the separately verified GitHub `main`. Finding the exact
Docker SHA in this reachable history proves `Docker behind`; missing history,
moving main or an out-of-window commit never proves `ahead` or `diverged`.
Mirror may also inspect locally present Docker Git objects with read-only
`merge-base`, without `fetch`, to prove `ahead` or `diverged` where possible.
The primary row and detail block display proven direction, evidence, and
specific Windows Pull blockers such as `DOCKER_MAIN_NOT_AT_GITHUB_MAIN`.

**Local folder vs. GitHub repository:** the table, selected checks, Windows clone
path, linked worktrees and Pull receipt retain the Docker primary folder name
(`repoId`). The Gateway READ target is separately derived from the primary's
actual `origin`, which must be a complete canonical HTTPS or SSH GitHub URL
under the fixed trusted owner `AlexPastukhh`. Thus Docker `ai-conspects` may
read GitHub `theory_conspects_and_banks` without renaming local folders or
switching Git refs. The selected row explicitly shows the verified GitHub
repository slug. Missing, foreign-owner, unrecognized or unsafe origins produce
`ORIGIN_MISSING` / `ORIGIN_NOT_ALLOWED` and **never** issue a Gateway request.
Windows origin must still equal the exact Docker origin, Windows main must be
clean, and Pull still requires exact GitHub main, fresh server Preview,
fast-forward proof, pinned SHA and durable receipt; origin mapping grants no
new write route or automatic action.

**Failures are observations, not successes:** a successful Browser HTTP response
can still contain a denied or unavailable Gateway `main` read. The reader
reports recognized Gateway statuses (for example `GATEWAY_HTTP_403`) as bounded
codes without raw provider errors, and never assumes why the 403 occurred.
The browser names the failure in the GitHub column and lower details while
blocking Pull. Owner check progress counts completed requests separately from
rows with unverified GitHub main, Windows fetch errors and HTTP/request failures.
A completed count of all Docker rows is not evidence all GitHub refs were read.
The check can update Git objects/FETCH_HEAD and possibly remote tracking refs,
**not** Windows HEAD/index/worktree. `fetch` error, timeout, moving remote or
changed Windows status stays unknown, displays an explicit error and blocks
Pull until fresh verification. A match needs no fetch; unsafe clones never fetch.
Normal `GET /api/v1/repositories/<repo>?remote=true` remains read-only for
callers and Pull post-result refresh. Developer-preview mode denies this POST. The fast Gateway reader calls typed `list_branches` plus bounded
`list_commits` only for Docker-main discrepancies; it does not fetch closed PR
details, PR lists, workflow history or check runs. All local SHA, origin, clean, branch and
ancestry checks, and owner-local Pull fresh Preview, are unchanged. In this
mode PR and CI fields remain explicitly `UNVERIFIED` / `NOT_VERIFIED`, never
silently `NONE`, `MERGED` or `SUCCESS`. The route never caches remote SHA.

The **Worktrees** tab offers **«Проверить PR/CI»** for the selected repository.
This uses the separate owner-session-protected read-only URL
`GET /api/v1/repositories/<repo>?remote=details`, not an implicit expensive
operation for 31 rows. Only this explicit read fetches the full bounded PR and
Actions lists and per-head check runs. Expensive individual `get_pull_request`
verification of closed PRs is limited to task branches from the **current
registered Docker worktree inventory**, preserving precise merged/unmerged
provenance for visible worktrees without guessing the status of absent ones.
Three independent read groups (branches, PR list, workflow runs) are issued
concurrently; individual closed-PR detail reads are independently bounded to
three. The Gateway remains the sole GitHub read source. Bulk deep inspection
is explicitly denied; no PR or CI statuses are upgraded without evidence.

The selected-row detail block reports Docker inventory, Windows Git, Gateway
and ancestry read timings in milliseconds, plus the Gateway reader's per-stage
durations. The check progress banner reports total elapsed time, while the selected row
also exposes Windows fetch status/error and measured fetch time. Cancelling
stops scheduling new checks, but cannot promise to abort an already running
server-side Git fetch. These are
observations, not guarantees of Windows wall-clock timing or GitHub latencies.
A local Docker exporter scan of 31 repos / 118 worktrees took ~2.8 s in the
2026-11-09 test environment; Windows Git and actual browser layout must be
verified on the owner's Windows session.

**Read-only Gateway sample timings (2026-11-09):** before the optimization,
`docke-windows-mirror` full PR/CI inspection took 28.7 seconds including ten
sequential closed PR detail reads. After the change, fresh `main`-only reads
took ~2.0 s per tested repository. Four independent main-only reads completed
in ~2.2 s with four concurrent Gateway requests **before Windows fetch was
added**; that Gateway-only baseline is not the latency of the new owner check.
A detailed Worktrees PR/CI
read is intentionally slower, opt-in, and can return partial/unknown evidence
if Gateway checks fail. No Git effects are ever performed by these reads.


The shared Windows Git observer now reads with the Windows owner's **effective
system Git config** rather than disabling it with `GIT_CONFIG_NOSYSTEM=1`,
because `core.autocrlf=true` is commonly configured system-wide by Git for
Windows. Readback and `git fetch`/`merge --ff-only` thus share the same line-
ending interpretation; existing Git fsmonitor and interactive prompting
restrictions stay in place. Porcelain status leading whitespace is preserved.
This fix is source-tested using a standalone CRLF fixture, but must still be
accepted on Windows, without overwriting live data.

## Gateway listener (not deployed)

Optional listener requires a private Windows TLS key/cert, a 32+-character random service secret stored outside repositories with owner-only ACL, a protected durable receipt DB under `%LOCALAPPDATA%`, an explicit repo allowlist, and a Windows certificate chain trusted by the **Gateway container** for `host.docker.internal`. Use `--gateway-listen --gateway-bind <reviewed-interface> --gateway-port 443 --gateway-cert <cert.pem> --gateway-key <key.pem> --gateway-secret-file <owner-protected-path> --gateway-receipt-db <owner-protected-path> --gateway-repo <repoId>`. No listener is exposed by default. Do not bind `0.0.0.0` or open firewall access without an explicit owner/network review. TLS/credential installation is an owner task, not an AI task. The one service token authorizes Gateway↔Windows requests; it is **not** a GitHub provider token. Private Git fetch requires Windows Git's existing authorized credential, or a separately reviewed brokered Git transport; credentials are never copied from Gateway into Mirror.

The shared Gateway Extension `JSONAPI` supports fixed HTTPS on port 443 with system-trusted certificate. No insecure fallback, direct AI-to-Windows bypass, arbitrary origin/port or token in caller payload is supported. Gateway config initially should contain the read operation only; add ASK Pull only after the owner confirms TLS/client identity, durable receipt behavior, exact approved merge evidence, Windows Git auth, and real fast-forward/reject/replay tests. Use the existing `Manage-Integrations.ps1` release controller; never redeploy Production as part of source testing.

## GitHub Actions source checks

`.github/workflows/mirror-source-ci.yml` provides source-only Python offline
regression and Node syntax checks. It does not install Windows or enable the
Gateway Pull listener. Publishing changes beneath `.github/workflows` uses
`github.push_commits` through native Gateway **ASK** and requires owner WebAuthn;
local tests and an open PR do not by themselves satisfy branch-required checks.
The test suite runs independently of the private Fortis Gateway repo; the actual
runtime reader still fails closed if the typed client is unavailable.

<a id="checks--outstanding-acceptance"></a>

## Source checks and acceptance boundary

Run `python3 -B -m unittest -q test_mirror_pr_ci test_mirror_api test_mirror_worktrees test_mirror_docker_actions test_mirror_workflow_source test_mirror_git_history test_mirror_status_source`
from the Mirror task worktree. Include `test_mirror_browser_pull` and `test_mirror_table_ui` in the offline
suite. Tests cover owner preview/session/CSRF/replay and the Node button flow,
but cannot prove Windows Git credentials, NTFS or live Gateway ASK. All other
browser Git writes remain disabled; pending acceptance is maintained only
in [BACKLOG-IMPROVEMENTS](BACKLOG-IMPROVEMENTS.md). The reusable owner check
procedure belongs to [MANUAL-PREVIEW](MANUAL-PREVIEW.md).
