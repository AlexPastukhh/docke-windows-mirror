# Windows-owned GitHub reads — P1 source

P1 replaces the browser's Docker/Gateway GitHub reader with Windows `gh api`.
Source/fixture CI does not prove installed Windows authentication or runtime.
The unified design owner remains [MIRROR_API_CONTRACT_V1](MIRROR_API_CONTRACT_V1.md);
outstanding stage/acceptance gates remain in [the backlog](BACKLOG-IMPROVEMENTS.md).

## Source ownership

| Component | Responsibility |
| --- | --- |
| `mirror_api/windows_github.py` | Windows-only gh identity, closed GET routes, safe environment, time/output limits and projected responses |
| `mirror_api/github_snapshot.py` | Shared bounded branch/PR/CI collection and exact-head/closed-PR evidence, without credentials or a transport |
| `mirror_api/operations.py:github_main` | Registered Docker origin → fixed owner/repository identity; one reader for browser observations and fresh Pull preflight |
| `tools/Export-Mirror-GitHub-Read.py` | Retained typed Gateway diagnostic helper using the same collector; never a browser fallback |

Docker still supplies registered primary/worktree inventory. Windows still
requires an exact matching registered origin and all existing clean-main,
SHA, ancestry, CSRF, one-use Preview and durable-receipt Pull guards.
No new machine Pull route/grant/provider or signing key is enabled by P1.
Legacy machine GET v1 still calls remote=False: GitHub NOT_CHECKED and Pull
unavailable. Windows main-only and detailed browser observations use the new
reader; the shared PullWorker rechecks main through that same Windows path.

## Authentication and request bounds

Git/GCM and gh are separate Windows owner credentials. REST reads use the gh
credential store for github.com; Git fetch continues to use the clone owner's
existing Git configuration. No token is requested from Repo or copied from
Gateway. Ambient GH/GITHUB token, alternate host/config, debug and proxy
overrides are removed from the gh child environment. Interactive prompts and
the pager are disabled. Missing gh/auth/scope gives a fixed GITHUB_* error.
No fallback and no automatic login, approval, GitHub write or hidden READ retry.

Only fixed owner/repository GET endpoints are constructed in trusted code.
Repo comes from the approved origin, never a caller URL/path/command. Each
snapshot verifies the canonical GitHub repository identity first; redirects
to a renamed/different repository fail that identity check. Main-only reads
then address main directly. Worktree detail checks address main plus registered
task branches; unrelated branch inventories are not needed in that mode.
Ancestry reads use the captured main SHA, not the mutable default branch.

Limits: one snapshot budget 60 seconds, each gh call at most 25 seconds,
stdout at most 1 MiB and stderr at most 16 KiB, bounded while draining pipes.
Timeout/overflow kills the child and returns a typed failure. JSON duplicate
keys, nonfinite values, invalid SHA/types, cross-repo identity and oversized
pages fail closed. Lists/PR detail/check fan-out retain collector bounds and
explicit truncation. No raw stderr, headers or auth output is returned.

Foreign/deleted fork PRs remain visible in the bounded GitHub observation but
cannot be matched to an owner repository's local worktree by branch/SHA alone.
Exact-head observed runs never imply the required CI/review gate passed.

Browser diagnostics retain legacy key names `gatewayTimingsMs`,
`readTimingsMs.githubGateway`, `previewGateway` and `gatewayMain` for receipt/UI
compatibility. They measure the GitHub stage; labels now say GitHub.
`githubTimingsMs` is the current snapshot diagnostic field. Source provenance
for a performed read is `windows-gh`; NOT_CHECKED remains an observation limit.

## Owner Windows qualification after accepted source delivery

Use the same Windows account as the Mirror process, from its reviewed clone:

```powershell
Get-Command git, gh | Select-Object Name, Source
gh auth status --hostname github.com
```

Do not run `gh auth token` or paste tokens/config into chat. If login or scopes
are missing, owner-managed setup is separate. Successful gh auth does not prove
Git/GCM fetch rights. Do not change existing credentials as part of source tests.

In a separately agreed owner test session, verify browser main/PR/CI, exact
origin/local-folder aliases, source=windows-gh, missing/denied auth, failures
and unchanged GitHub/Pull guard semantics. Prove independence from Gateway
using a reviewed process-local network test; do not stop the working Gateway
or modify shared firewall/service configuration for this check. Docker remains
required for Docker inventory. An existing healthy Gateway alone does not prove
the unavailable-Gateway acceptance case.

Record the installed source SHA, process identity, observed repository/SHA,
PASS/FAIL/NOT_TESTED and safe readbacks without credentials. Actual Windows
browser/Git/GCM/gh and Gateway-unavailability acceptance remains NOT_TESTED
until those owner results exist. P2/live cutover follows the contract gates.

Offline verification: `python3 -B -m unittest discover -q -p 'test_mirror*.py'`
and `node --check mirror_api/static/app.js`. Fixtures execute no live GitHub
or Gateway calls; subprocess limit tests run disposable local Python children.

## Pull failure recovery — source follow-up 2026-11-16

The ordinary selected/marked/all repository check remains available with an
unresolved Pull. For its original requestId it reads the receipt and a fresh
repository snapshot, without dispatching another fetch/merge. A still-unknown
result shows the observed Windows HEAD, expected/target SHAs, observation time
and available phase/reason; the table is refreshed even without convergence.
There is no separate product button for checking a Pull result.

A nonzero fetch exit before merge records only a fixed cause category and exit
code, never raw stderr, URLs or credentials. Clean main, matching origin and an
expected/target HEAD can establish FAILED after a known pre-merge failure. An
old WINDOWS_FETCH_FAILED receipt can use the same read-only recovery, but its
original fetch cause was not saved and cannot be reconstructed. FAILED permits
a fresh explicit Preview; it never repeats the previous request automatically.
Unknown/post-merge failures remain UNCERTAIN unless safe target readback proves
convergence (evidence TARGET_READBACK). DISPATCHED stays reserved. New UUIDs for
a clone with DISPATCHED/UNCERTAIN are denied, including another worker sharing
the database. This is not the P2 process-instance guard or live qualification.

After a server/session restart the browser cannot read the previous session's
receipt. The stale UI reference is cleared only on PULL_RECEIPT_NOT_OWNED.
Internal repository-scoped reconciliation exposes only blocked/pendingCount/
checkedAt, never the old receipt or UUID. It fences both fresh Preview and
owner metadata checks while an effect remains unresolved. The receipt database
must be preserved. No deletion/edit of receipts or automatic retry is recovery.

Owner Windows acceptance remains required after merge and an agreed source
update/restart: verify an old fetch failure on unchanged HEAD clears via normal
selected check, a genuine uncertain merge still blocks Pull, fresh facts appear,
and the browser restart preserves both privacy and the unresolved-effect guard.
For the reported old failure, fetch cause is unknown. A successful gh auth
check proves REST identity only; it does not prove Git/GCM fetch authorization.

## Legacy preflight receipt recovery — source follow-up 2026-11-16

The fixed GITHUB_MAIN_CHANGED refusal occurs before Windows fetch and merge in all six retained revisions of pull.py since 4f3ce5898021. Earlier receipts omit progress fields and may refer to two obsolete SHAs after later Windows updates. With verified clean Windows main, valid origin and a valid current HEAD, this known preflight refusal can become FAILED even when HEAD differs from both original SHAs. The original error, payload and timing evidence remain; evidence is PRECHECK_REFUSAL_AND_READBACK. This does not claim execution by the old UUID.

This rule applies only to UNCERTAIN. Conflicting progress (FETCH/MERGE/READBACK, merge started, unfinished worker, false knownFailure or invalid flag types) does not gain the legacy exception. DISPATCHED remains reserved; unknown or post-merge outcomes retain existing conservative rules. No new fetch/merge, receipt deletion, owner-session access or machine permission is introduced.

Owner acceptance after reviewed source update/restart: an old GITHUB_MAIN_CHANGED with obsolete expected/target SHAs should resolve through ordinary selected check, without another Git effect. Preview still validates fresh facts; ff-only ancestry remains mandatory. An unresolved receipt takes priority in the workflow summary, disables the manual Preview assessment, and shows the pending count instead of a contradictory available Preview. Linux fixtures are not Windows runtime acceptance.


## Legacy readback after later Windows updates — source follow-up

A legacy UNCERTAIN/WINDOWS_READBACK_UNCONFIRMED receipt with all four progress
fields absent/null can describe an old successful ff-only followed by failed
observation. In all nine retained pull.py revisions from 4f3ce5898021 through
dcd9980f7a90 this fixed error is reachable only after merge returned success.
Later legitimate updates may leave HEAD beyond the old target; equality-only
recovery then blocks forever even though that target is already in history.

Recovery reads local Windows Git objects only: expectedHead must equal or be
an ancestor of targetSha, and targetSha must be a strict ancestor of the
observed HEAD. A second clean main/origin/HEAD readback must match the first.
The probe shares the request budget and has its own 20-second bound. Missing
objects, unknown ancestry, unsafe identity, readback races, contradictory
progress, DISPATCHED or other errors remain unresolved. No fetch is attempted.

The terminal receipt records EXECUTED with TARGET_INCLUDED_READBACK and
 targetIncluded=true; head/observation.head are the actual later HEAD, while
 targetSha remains the original requested commit. The original error, timing,
payload and prior state are retained. This proves convergence of the old goal,
not exclusive attribution of later commits to the old UUID. Replays are lookup
only. The browser distinguishes history inclusion from exact-target readback.
Session receipt privacy and the named machine READ v1 scope do not change.

This is source recovery support, not permission to edit/delete the journal,
repeat Pull, install code or restart a working Mirror process. Source delivery,
owner Windows update/restart and live acceptance remain separate stages.
