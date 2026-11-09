"""Mirror observations; owner-authorized checks may fetch Git metadata only."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime, timezone

REPO = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
CONTAINER = "fortis-repo-test"
EXPORTER = "/workspace/repo/Repos/docke-windows-mirror/tools/Export-Docker-Branch-Workflow.py"
GITHUB_READER = "/workspace/repo/Repos/docke-windows-mirror/tools/Export-Mirror-GitHub-Read.py"
PREVIEW_TASK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")


def set_preview_source(task):
    """Point read-only source checks to one registered Mirror task source.

    Only the explicit local preview CLI calls this before starting the server.
    Normal release mode continues to use the approved Docker primary source.
    """
    if type(task) is not str or not PREVIEW_TASK.fullmatch(task):
        raise ObservationError("PREVIEW_TASK_INVALID")
    root = ("/workspace/repo/Worktrees/docke-windows-mirror/" + task + "/tools/")
    global EXPORTER, GITHUB_READER
    EXPORTER = root + "Export-Docker-Branch-Workflow.py"
    GITHUB_READER = root + "Export-Mirror-GitHub-Read.py"



class ObservationError(ValueError):
    """A source could not be trusted or accessed."""


def checked_at():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run(argv, timeout=25):
    # Preserve the Windows owner's effective Git line-ending configuration.
    # In particular, disabling the system config ignores core.autocrlf=true
    # from Git for Windows and invents dirty files after a clean ff-only pull.
    # This path executes read-only Git commands; write authorization remains
    # restricted to the separate guarded PullWorker.
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
               GCM_INTERACTIVE="never")
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ObservationError("COMMAND_UNAVAILABLE") from None
    if p.returncode:
        raise ObservationError("COMMAND_FAILED")
    if len(p.stdout) > 1000000:
        raise ObservationError("RESPONSE_TOO_LARGE")
    # Git porcelain's first leading space is significant (" M" != "M ").
    return p.stdout.decode("utf-8", errors="replace").rstrip("\r\n")


def docker_inventory(repo=None):
    args = ["docker", "exec", "-i", "--user", "1000:1000", CONTAINER,
            "python3", "-B", EXPORTER, "--inventory-json"]
    if repo is not None:
        if not REPO.fullmatch(repo):
            raise ObservationError("REPOSITORY_ID_INVALID")
        args.extend(["--repo", repo])
    raw = run(args, timeout=35)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise ObservationError("INVALID_DOCKER_RESPONSE") from None
    if not isinstance(data, dict) or data.get("schema") != "fortis.docker.worktrees.v1" or not isinstance(data.get("repositories"), dict):
        raise ObservationError("INVALID_DOCKER_SCHEMA")
    return data["repositories"]


def git(root, *args):
    return run(["git", "-c", "core.fsmonitor=false", "-C", str(root), *args])


def windows_status(base, repo_id, expected_origin):
    result = dict(source="windows", checkedAt=checked_at(), repoId=repo_id,
                  exists=False, branch=None, head=None, clean=None,
                  changedFiles=None, changedCount=None, origin=None,
                  originValid=False, relation="unknown", error=None)
    path = base / repo_id
    if path.is_symlink() or not path.exists():
        result["error"] = "WINDOWS_CLONE_NOT_FOUND"
        return result
    if not path.is_dir() or not (path / ".git").is_dir() or (path / ".git").is_symlink():
        result["error"] = "WINDOWS_CLONE_UNSAFE"
        return result
    try:
        actual = Path(git(path, "rev-parse", "--show-toplevel")).resolve()
        if actual != path.resolve():
            raise ObservationError("WINDOWS_CLONE_UNSAFE")
        result["exists"] = True
        result["origin"] = git(path, "remote", "get-url", "origin")
        result["originValid"] = bool(expected_origin and result["origin"] == expected_origin)
        result["branch"] = git(path, "symbolic-ref", "--quiet", "--short", "HEAD")
        head = git(path, "rev-parse", "--verify", "HEAD").lower()
        if not SHA.fullmatch(head):
            raise ObservationError("INVALID_HEAD")
        result["head"] = head
        status = git(path, "status", "--porcelain=v1", "--untracked-files=all")
        files = status.splitlines() if status else []
        result["clean"] = not bool(files)
        result["changedCount"] = len(files)
        result["changedFiles"] = files[:200]
        result["changedTruncated"] = len(files) > 200
    except ObservationError as exc:
        result["error"] = str(exc)
    return result


def github_main(repo_id, origin, *, worktree_branches=None, details=True):
    """Typed Gateway-only metadata. Invalid/mismatched origin fails closed."""
    result = dict(source="fortis-gateway", checkedAt=checked_at(), repoId=repo_id,
                  head=None, branches={}, pullRequests=None, workflowRuns=None,
                  checkRuns={}, readErrors={}, truncated={},
                  requiredCI="NOT_VERIFIED", error=None)
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:)AlexPastukhh/([A-Za-z0-9_.-]+?)(?:\.git)?",
        origin or "", re.I)
    if not match or match.group(1).casefold() != repo_id.casefold():
        result["error"] = "ORIGIN_NOT_ALLOWED"
        return result
    if not details:
        # Fresh exact main via typed Gateway. PR/CI remain explicitly unknown;
        # no stale cache or guessed merge evidence enters Pull authorization.
        data = gateway_pr_ci(repo_id, main_only=True)
    else:
        data = gateway_pr_ci(repo_id) if worktree_branches is None else gateway_pr_ci(
            repo_id, worktree_branches=worktree_branches)
    result.update(head=data["head"], branches=data["branches"],
                  pullRequests=data["pullRequests"], workflowRuns=data["workflowRuns"],
                  checkRuns=data["checkRuns"], readErrors=data["errors"],
                  truncated=data["truncated"],
                  gatewayTimingsMs=data.get("timingsMs"))
    if result["head"] is None:
        result["error"] = data["errors"].get("branches", "GATEWAY_MAIN_UNVERIFIED")
    return result


def gateway_pr_ci(repo_id, *, worktree_branches=None, main_only=False):
    """Run only the typed authenticated Gateway reader inside Fortis Repo.

    Windows has no Gateway machine grant and never receives GitHub tokens.
    """
    if not REPO.fullmatch(repo_id):
        raise ObservationError("REPOSITORY_ID_INVALID")
    # Only registered local worktree branches need individual closed-PR merge
    # proof or PR-head check runs. A full per-repo PR list remains available,
    # but expensive per-PR reads are irrelevant to absent worktrees.
    args = ["docker", "exec", "-i", "--user", "1000:1000", CONTAINER,
            "python3", "-B", GITHUB_READER, "--repo", repo_id]
    if main_only:
        args.append("--main-only")
    if worktree_branches is not None:
        if (type(worktree_branches) not in (list, tuple) or len(worktree_branches) > 100 or
                any(type(branch) is not str or not re.fullmatch(r"task/[A-Za-z0-9_][A-Za-z0-9_./-]{0,159}", branch)
                    for branch in worktree_branches)):
            raise ObservationError("GATEWAY_WORKTREE_BRANCHES_INVALID")
        args.append("--scope-worktrees")
        for branch in sorted(set(worktree_branches)):
            args.extend(["--worktree-branch", branch])
    try:
        raw = run(args, timeout=105)
        data = json.loads(raw)
        if (type(data) is not dict or data.get("schema") != "fortis.github.mirror.v1"
                or data.get("repoId") != repo_id or
                type(data.get("errors")) is not dict or
                type(data.get("truncated")) is not dict or
                type(data.get("checkRuns")) is not dict or
                (data.get("pullRequests") is not None and type(data["pullRequests"]) is not list) or
                (data.get("workflowRuns") is not None and type(data["workflowRuns"]) is not list)):
            raise ValueError()
        return data
    except (ObservationError, ValueError, TypeError, KeyError):
        return {"schema": "fortis.github.mirror.v1", "repoId": repo_id,
                "head": None, "branches": {}, "pullRequests": None, "workflowRuns": None, "checkRuns": {},
                "errors": {"gateway": "GATEWAY_READ_UNAVAILABLE"},
                "truncated": {}, "source": "fortis-gateway"}


def worktree_pr_ci(worktree, github):
    """Select only runs for this exact PR HEAD and branch, never 'latest green'."""
    result_pr = {"status": "UNVERIFIED", "data": None}
    result_ci = {"status": "UNVERIFIED", "headSha": None,
                 "workflowRuns": [], "checkRuns": [], "requiredGateVerified": False}
    branch = worktree.get("branch")
    prs = github.get("pullRequests")
    if not branch or type(prs) is not list:
        return result_pr, result_ci
    matching = [pr for pr in prs if type(pr) is dict and
                type(pr.get("head")) is dict and pr["head"].get("ref") == branch and
                type(pr.get("base")) is dict and pr["base"].get("ref") == "main"]
    if not matching:
        result_pr["status"] = ("UNKNOWN_TRUNCATED" if github.get("truncated",{}).get("pullRequests")
                               else "NONE")
        result_ci["status"] = "NO_PR" if result_pr["status"] == "NONE" else "UNVERIFIED"
        return result_pr, result_ci
    pr = next((item for item in matching if item.get("state") == "open"), matching[0])
    sha = pr["head"].get("sha")
    if type(sha) is not str or not SHA.fullmatch(sha):
        return result_pr, result_ci
    if pr.get("merged") is True:
        state = "MERGED"
    elif pr.get("state") == "open":
        state = "DRAFT" if pr.get("draft") else "OPEN"
    elif pr.get("mergeVerified") is True:
        state = "CLOSED"
    else:
        state = "CLOSED_OR_MERGED_UNVERIFIED"
    result_pr = {"status": state, "data": pr, "headMatchesPublished":
                 github.get("branches",{}).get(branch) == sha}
    result_ci["headSha"] = sha
    runs = github.get("workflowRuns")
    if type(runs) is not list:
        return result_pr, result_ci
    related = [row for row in runs if type(row) is dict and
               row.get("headSha") == sha and row.get("headBranch") == branch and
               row.get("event") == "pull_request"]
    latest = {}
    for row in related:
        name = row.get("name")
        if type(name) is str and (name not in latest or
                (row.get("runNumber", 0), row.get("runAttempt", 0), row.get("id", 0)) >
                (latest[name].get("runNumber", 0), latest[name].get("runAttempt", 0), latest[name].get("id", 0))):
            latest[name] = row
    chosen = list(latest.values())
    result_ci["workflowRuns"] = chosen
    if chosen:
        if any(row.get("status") == "completed" and row.get("conclusion") not in ("success", None)
               for row in chosen):
            result_ci["status"] = "OBSERVED_FAILURE"
        elif any(row.get("status") != "completed" for row in chosen):
            result_ci["status"] = "IN_PROGRESS"
        elif all(row.get("conclusion") == "success" for row in chosen):
            result_ci["status"] = "OBSERVED_SUCCESS_NOT_REQUIRED_GATE"
        else:
            result_ci["status"] = "UNVERIFIED"
    elif not github.get("truncated",{}).get("workflowRuns"):
        result_ci["status"] = "NO_RUNS_FOR_HEAD"
    checks = github.get("checkRuns", {}).get(sha)
    if type(checks) is dict and type(checks.get("items")) is list:
        result_ci["checkRuns"] = [item for item in checks["items"] if item.get("headSha") == sha]
        result_ci["checksTruncated"] = bool(checks.get("truncated"))
    return result_pr, result_ci


def relation_to_sha(path, source, target):
    if source is None or target is None or not SHA.fullmatch(source) or not SHA.fullmatch(target):
        return "unknown"
    if source == target:
        return "equal"
    try:
        git(path, "cat-file", "-e", target + "^{commit}")
        base = git(path, "merge-base", source, target)
    except ObservationError:
        return "unknown"
    if base == source:
        return "behind"
    if base == target:
        return "ahead"
    return "diverged"


def fetch_windows_main_history(windows_root, repo_id, expected_origin, expected_head, target_sha):
    """Owner-authorized metadata fetch only, never checkout/merge/reset.

    The caller has already verified fresh GitHub main from the typed Gateway.
    Re-validate the Windows checkout before AND after Git network activity,
    and pin FETCH_HEAD to the same Gateway SHA. Do not infer a direction on
    transport error, remote change or any unexpected local-state change.
    """
    if (not REPO.fullmatch(repo_id) or type(expected_origin) is not str or
            type(expected_head) is not str or not SHA.fullmatch(expected_head) or
            type(target_sha) is not str or not SHA.fullmatch(target_sha)):
        return 'FETCH_INPUT_INVALID'
    before = windows_status(windows_root, repo_id, expected_origin)
    if (before['error'] or before['branch'] != 'main' or
            before['head'] != expected_head or before['clean'] is not True or
            before['originValid'] is not True):
        return 'WINDOWS_CHANGED_BEFORE_FETCH'
    root = windows_root / repo_id
    argv = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
            '-c', 'protocol.file.allow=never', '-C', str(root), 'fetch',
            '--no-tags', '--no-recurse-submodules', 'origin', 'main']
    # Unlike observational Git calls, fetch MUST be allowed to write its own
    # metadata/locks. No shell, stdin, interactive credential prompts or
    # dynamic Git refspec. It may contact ONLY the verified origin/main.
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    env.pop('GIT_OPTIONAL_LOCKS', None)
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45,
            env=env, check=False)
    except subprocess.TimeoutExpired:
        return 'WINDOWS_FETCH_TIMEOUT'
    except OSError:
        return 'WINDOWS_FETCH_UNAVAILABLE'
    if result.returncode:
        return 'WINDOWS_FETCH_FAILED'
    try:
        fetched = git(root, 'rev-parse', '--verify', 'FETCH_HEAD').lower()
    except ObservationError:
        return 'WINDOWS_FETCH_HEAD_UNVERIFIED'
    if fetched != target_sha:
        return 'WINDOWS_FETCHED_MAIN_CHANGED'
    after = windows_status(windows_root, repo_id, expected_origin)
    if (after['error'] or after['branch'] != 'main' or
            after['head'] != expected_head or after['clean'] is not True or
            after['originValid'] is not True):
        return 'WINDOWS_CHANGED_AFTER_FETCH'
    return None


def evaluate_actions(windows, github, *, docker_primary=None, approved_sha=None):
    """Availability, not authorization: gateway policy is a separate gate."""
    reasons = []
    if windows.get("error"):
        reasons.append(windows["error"])
    if windows.get('fetchError'):
        reasons.append(windows['fetchError'])
    if not windows.get("originValid"):
        reasons.append("WINDOWS_ORIGIN_MISMATCH")
    if windows.get("branch") != "main":
        reasons.append("WINDOWS_NOT_ON_MAIN")
    if windows.get("clean") is not True:
        reasons.append("WINDOWS_NOT_VERIFIED_CLEAN")
    if github.get("error") or not github.get("head"):
        reasons.append("GITHUB_MAIN_UNVERIFIED")
    if windows.get("relation") != "behind":
        reasons.append("FAST_FORWARD_NOT_VERIFIED")
    if not approved_sha or approved_sha != github.get("head"):
        reasons.append("MERGE_CI_APPROVAL_EVIDENCE_REQUIRED")
    # Gateway ASK is a different authorization route and remains inactive.
    reasons.append("GATEWAY_WINDOWS_WORKER_NOT_CONNECTED")
    primary = docker_primary or {}
    local = []
    if (primary.get("kind") != "primary" or primary.get("branch") != "main" or
            primary.get("clean") is not True or primary.get("targetEligible") is not True):
        local.append("DOCKER_MAIN_NOT_VERIFIED")
    if not github.get("head") or github.get("error"):
        local.append("GITHUB_MAIN_UNVERIFIED")
    if primary.get("head") != github.get("head"):
        local.append("DOCKER_MAIN_NOT_AT_GITHUB_MAIN")
    if windows.get("error") or not windows.get("originValid"):
        local.append("WINDOWS_ORIGIN_NOT_VERIFIED")
    if windows.get('fetchError'):
        local.append(windows['fetchError'])
    if windows.get("branch") != "main":
        local.append("WINDOWS_NOT_ON_MAIN")
    if windows.get("clean") is not True:
        local.append("WINDOWS_NOT_CLEAN")
    if windows.get("head") == github.get("head") and github.get("head"):
        local.append("WINDOWS_ALREADY_CURRENT")
    if windows.get("relation") in ("ahead", "diverged"):
        local.append("WINDOWS_NOT_BEHIND_GITHUB")
    return {"pull_main": {"supported": True, "available": False,
                          "policy": "ASK", "reasons": list(dict.fromkeys(reasons))},
            "pull_main_browser": {"supported": True, "available": not bool(local),
                                  "policy": "OWNER_LOCAL_PREVIEW_CONFIRM",
                                  "requiresFreshPreview": True,
                                  "fastForwardVerified": windows.get("relation") == "behind",
                                  "reasons": local},
            "stage": {"supported": False, "available": False, "reasons": ["GATEWAY_SCOPE_NOT_CONFIGURED"]},
            "commit": {"supported": False, "available": False, "reasons": ["GATEWAY_SCOPE_NOT_CONFIGURED"]},
            "push": {"supported": False, "available": False, "reasons": ["GATEWAY_SCOPE_NOT_CONFIGURED"]}}


class Mirror:
    def __init__(self, windows_root):
        self.windows_root = Path(windows_root).expanduser().resolve()

    def check_repository(self, repo_id):
        """Explicit owner-authenticated POST; may write Windows Git metadata."""
        if type(repo_id) is not str or not REPO.fullmatch(repo_id):
            raise ObservationError('REPOSITORY_ID_INVALID')
        return self.inventory(remote=True, repo=repo_id, fetch_windows=True)

    def inventory(self, remote=False, repo=None, *, details=False, fetch_windows=False):
        if fetch_windows and (not remote or repo is None or details):
            raise ObservationError('OWNER_CHECK_SCOPE_REQUIRED')
        started = time.perf_counter()
        rows = docker_inventory(repo)
        docker_ms = round((time.perf_counter() - started) * 1000)
        output = {"schemaVersion": 1, "checkedAt": checked_at(), "repositories": [], "errors": []}
        for name, worktrees in sorted(rows.items()):
            if not REPO.fullmatch(name) or not isinstance(worktrees, list):
                output["errors"].append("INVALID_DOCKER_REPOSITORY")
                continue
            primary = next((item for item in worktrees if item.get("kind") == "primary"), None)
            if primary is None:
                output["errors"].append("PRIMARY_MISSING:" + name)
                continue
            origin = primary.get("origin")
            local_start = time.perf_counter()
            windows = windows_status(self.windows_root, name, origin)
            windows_ms = round((time.perf_counter() - local_start) * 1000)
            # The Docker inventory is authoritative for visible task rows;
            # only these branches require extra individual PR/check reads.
            task_branches = sorted({item.get("branch") for item in worktrees
                if item.get("kind") == "worktree" and item.get("targetEligible") is True and
                   type(item.get("branch")) is str and item["branch"].startswith("task/")})
            remote_start = time.perf_counter()
            github = github_main(name, origin,
                worktree_branches=task_branches if details else None,
                details=details) if remote else {
                "source": "github", "checkedAt": None, "repoId": name,
                "head": None, "branches": {}, "pullRequests": None,
                "workflowRuns": None, "checkRuns": {}, "readErrors": {"gateway":"NOT_CHECKED"},
                "truncated": {}, "requiredCI": "NOT_CHECKED", "error": "NOT_CHECKED"}
            gateway_ms = round((time.perf_counter() - remote_start) * 1000) if remote else None
            windows_fetch_ms = None
            if fetch_windows:
                windows['fetchStatus'] = 'SKIPPED'
                windows['fetchError'] = None
                # Only clean, exact-origin, registered Windows main may fetch.
                # Guard against missing/mismatched Gateway SHA; no network
                # when both Windows and GitHub already point at the same HEAD.
                if (github.get('error') is None and type(github.get('head')) is str and
                        SHA.fullmatch(github['head']) and primary.get('head') == github['head'] and
                        primary.get('clean') is True and primary.get('branch') == 'main' and
                        primary.get('targetEligible') is True and
                        windows.get('error') is None and windows.get('originValid') is True and
                        windows.get('branch') == 'main' and windows.get('clean') is True):
                    if windows['head'] == github['head']:
                        windows['fetchStatus'] = 'NOT_NEEDED'
                    else:
                        fetched_at = time.perf_counter()
                        windows['fetchError'] = fetch_windows_main_history(
                            self.windows_root, name, origin, windows['head'], github['head'])
                        windows_fetch_ms = round((time.perf_counter() - fetched_at) * 1000)
                        windows['fetchStatus'] = ('VERIFIED' if windows['fetchError'] is None
                                                  else 'FAILED')
            relation_start = time.perf_counter()
            if (github.get("head") and windows["exists"] and windows["error"] is None and
                    not windows.get('fetchError')):
                windows["relation"] = relation_to_sha(self.windows_root / name,
                                                        windows["head"], github["head"])
            relation_ms = round((time.perf_counter() - relation_start) * 1000)
            for worktree in worktrees:
                branch = worktree.get("branch")
                remote_sha = github.get("branches", {}).get(branch)
                worktree["remoteSha"] = remote_sha
                worktree["remoteRelation"] = ("equal" if remote_sha == worktree.get("head")
                    and remote_sha is not None else "unknown")
                worktree["pullRequest"], worktree["ci"] = worktree_pr_ci(worktree, github)
            actions = evaluate_actions(windows, github, docker_primary=primary)
            output["repositories"].append({"repoId": name, "docker": {"primary": primary,
                "worktrees": worktrees}, "windows": windows, "github": github, "actions": actions,
                "readTimingsMs": {"dockerInventory": docker_ms, "windowsGit": windows_ms,
                                  "githubGateway": gateway_ms, "windowsFetch": windows_fetch_ms,
                                  "gitAncestry": relation_ms}})
        return output
