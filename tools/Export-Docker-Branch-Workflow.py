#!/usr/bin/env python3
"""Fortis Docker-first Git branch snapshot for the Windows workflow inspector.

Read-only --stdout is the default. --switch requires explicit owner action
through the Windows UI and strictly checks current branch, SHA and cleanliness.
No network, fetch, push, checkout on read, Docker socket or credentials.
"""
import argparse
import base64
from datetime import datetime, timezone
import os
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path("/workspace/repo/Repos")
NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}\Z")
BRANCH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,159}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
ORIGIN = re.compile(
    r"(?:https://github\.com/|git@github\.com:)AlexPastukhh/[A-Za-z0-9_.-]+(?:\.git)?\Z",
    re.I,
)
def valid_branch(s):
    return bool(BRANCH.fullmatch(s) and not s.startswith("-") and
                not s.endswith("/") and ".." not in s and
                "//" not in s and "@{" not in s and
                not s.endswith(".lock") and "\\" not in s)


def safe_repo(root, name):
    if not NAME.fullmatch(name) or name in (".", ".."):
        return None
    path = root / name
    if path.is_symlink() or not path.is_dir():
        return None
    # Reject .git indirection and symlinked .git for branch switching.
    dotgit = path / ".git"
    if not dotgit.is_dir() or dotgit.is_symlink():
        return None
    return path


def git(repo, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
               GCM_INTERACTIVE="never", GIT_CONFIG_NOSYSTEM="1")
    try:
        p = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, env=env,
            check=False,
        )
        return p.stdout.decode("utf-8", errors="replace").rstrip("\r\n") if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None



def inventory_worktrees(root, name):
    """Git-authoritative inventory including noncanonical existing worktrees."""
    primary = safe_repo(root, name)
    if primary is None:
        raise ValueError("unsafe or missing primary")
    raw = git(primary, "worktree", "list", "--porcelain", "-z")
    if raw is None:
        raise ValueError("worktree inventory unavailable")
    parent = (root.parent / "Worktrees" / name).resolve()
    result = []
    for record in raw.split("\0\0"):
        if not record:
            continue
        attrs = {}
        for item in record.split("\0"):
            key, _, value = item.partition(" ")
            attrs[key] = value
        value = attrs.get("worktree", "")
        location = Path(value)
        primary_row = location == primary.resolve()
        branch = attrs.get("branch", "").removeprefix("refs/heads/")
        head = attrs.get("HEAD", "")
        state = ("prunable" if "prunable" in attrs else
                 "locked" if "locked" in attrs else
                 "bare" if "bare" in attrs else "registered")
        canonical = (not primary_row and location.is_absolute() and
                     not location.is_symlink() and location.parent == parent and
                     bool(NAME.fullmatch(location.name)) and location.is_dir() and
                     (location / ".git").is_file() and
                     not (location / ".git").is_symlink())
        if canonical:
            common = git(location, "rev-parse", "--path-format=absolute", "--git-common-dir")
            top = git(location, "rev-parse", "--show-toplevel")
            canonical = (common is not None and
                         Path(common).resolve() == (primary / ".git").resolve() and
                         top == str(location))
        eligible = state == "registered" and (primary_row or canonical)
        task = None if primary_row else (location.name if canonical else None)
        status = git(location, "status", "--porcelain=v1", "--untracked-files=all") if eligible else None
        origin = git(location, "remote", "get-url", "origin") if eligible else None
        result.append(dict(repoId=name, task=task, path=value,
                           kind="primary" if primary_row else "worktree",
                           branch=branch if valid_branch(branch) else None,
                           head=head if SHA.fullmatch(head) else None,
                           clean=None if status is None else not bool(status),
                           changed=None if status is None else len(status.splitlines()),
                           changedFiles=[] if status is None else status.splitlines()[:200],
                           changedTruncated=status is not None and len(status.splitlines()) > 200,
                           origin=origin if origin and ORIGIN.fullmatch(origin) else None,
                           registered=True, targetEligible=eligible,
                           status=state if eligible else "unsupported_" + state))
    if not any(x["kind"] == "primary" and x["targetEligible"] for x in result):
        raise ValueError("primary worktree missing from Git inventory")
    return result


def exact_target(root, name, task):
    if task is None:
        repo = safe_repo(root, name)
        if repo is None:
            raise ValueError("unsafe primary repository")
        return repo
    if not NAME.fullmatch(task):
        raise ValueError("invalid task")
    rows = inventory_worktrees(root, name)
    matches = [x for x in rows if x["task"] == task and
               x["kind"] == "worktree" and x["targetEligible"]]
    if len(matches) != 1:
        raise ValueError("selected task is not a registered canonical worktree")
    return Path(matches[0]["path"])


def snapshot_one(repo):
    origin = git(repo, "remote", "get-url", "origin")
    if not origin or not ORIGIN.fullmatch(origin):
        origin = "-"
    head = git(repo, "rev-parse", "--verify", "HEAD")
    active = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not head or not SHA.fullmatch(head.lower()):
        return None
    if not active or not valid_branch(active):
        active = "-"
    changes = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    changed = -1 if changes is None else len(changes.splitlines()) if changes else 0
    rows = ["R\t{}\t{}\t{}\t{}\t{}".format(repo.name, active, head.lower(), changed, origin)]
    # F records are base64-encoded single porcelain-v1 lines so that spaces,
    # tabs, renames and quoted paths cannot corrupt the tab-separated protocol.
    # R.changed remains the FULL count; details are bounded for UI safety.
    if changes is not None:
        for entry in changes.splitlines()[:500]:
            if entry:
                encoded = base64.b64encode(entry.encode("utf-8")).decode("ascii")
                rows.append("F\t{}\t{}".format(repo.name, encoded))
    listing = git(repo, "for-each-ref", "--format=%(refname:short)%09%(objectname)", "refs/heads")
    if listing is None:
        return rows
    for line in listing.splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not valid_branch(parts[0]) or not SHA.fullmatch(parts[1].lower()):
            continue
        rows.append("B\t{}\t{}\t{}".format(repo.name, parts[0], parts[1].lower()))
    return rows


def snapshot(root, names):
    if not root.is_dir() or root.is_symlink():
        raise ValueError("invalid Docker repository root")
    candidates = (sorted(set(names), key=str.lower) if names is not None
                  else sorted((p.name for p in root.iterdir()), key=str.lower))
    lines = ["FORTIS_DOCKER_BRANCH_V2\t" +
             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")]
    for name in candidates:
        path = safe_repo(root, name)
        if path is None:
            continue
        item = snapshot_one(path)
        if item:
            lines.extend(item)
    return "\n".join(lines) + "\n"


def guarded_switch(root, name, target, expected_branch, expected_head):
    """Owner-reviewed switch only, no implicit creation, no remote checkout."""
    repo = safe_repo(root, name)
    if repo is None or not valid_branch(target) or not valid_branch(expected_branch):
        raise ValueError("invalid Git repository or branch")
    if not SHA.fullmatch(expected_head):
        raise ValueError("invalid expected SHA")
    origin = git(repo, "remote", "get-url", "origin")
    if not origin or not ORIGIN.fullmatch(origin):
        raise ValueError("origin is not the approved GitHub namespace")
    current_branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    current_sha = git(repo, "rev-parse", "--verify", "HEAD")
    current_dirty = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    target_sha = git(repo, "rev-parse", "--verify", "refs/heads/" + target)
    if (current_branch != expected_branch or current_sha != expected_head or
            current_dirty is None or current_dirty != "" or
            not target_sha or not SHA.fullmatch(target_sha.lower())):
        raise ValueError("preflight failed: branch/SHA/dirty/target mismatch")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never",
               GIT_OPTIONAL_LOCKS="1", GIT_CONFIG_NOSYSTEM="1")
    try:
        p = subprocess.run([
            "git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-c", "submodule.recurse=false", "-C", str(repo),
            "switch", "--no-guess", target
        ], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("switch could not complete; inspect repository manually")
    if p.returncode != 0:
        raise ValueError("Git refused branch switch; inspect repository manually")
    if git(repo, "symbolic-ref", "--quiet", "--short", "HEAD") != target:
        raise ValueError("postcondition: unexpected branch")
    return "SWITCHED\t" + name + "\t" + target


def compare_branches(root, requests):
    """Compare Docker-local refs against exact GitHub-advertised SHA; never fetch.

    C records contain -1/-1 if GitHub objects are unavailable in Docker.
    A cached origin/* ref is never treated as authoritative for GitHub.
    """
    lines = []
    for name, branch, remote_sha in requests:
        if not NAME.fullmatch(name) or not valid_branch(branch) or not SHA.fullmatch(remote_sha):
            raise ValueError("invalid compare request")
        repo = safe_repo(root, name)
        if repo is None:
            continue
        local_sha = git(repo, "rev-parse", "--verify", "refs/heads/" + branch)
        if local_sha is None or not SHA.fullmatch(local_sha.lower()):
            continue
        ahead = behind = -1
        if local_sha == remote_sha:
            ahead = behind = 0
        else:
            counts = git(repo, "rev-list", "--left-right", "--count",
                         local_sha + "..." + remote_sha)
            if counts:
                parts = counts.split()
                if (len(parts) == 2 and all(p.isdigit() for p in parts)):
                    ahead, behind = map(int, parts)
        lines.append("C\t{}\t{}\t{}\t{}".format(name, branch, ahead, behind))
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--repo", nargs="+", default=None)
    parser.add_argument("--compare", action="append", nargs=3, metavar=("REPO", "BRANCH", "GH_SHA"))
    parser.add_argument("--inventory-json", action="store_true")
    parser.add_argument("--switch-repo")
    parser.add_argument("--switch-branch")
    parser.add_argument("--expect-branch")
    parser.add_argument("--expect-head")
    args = parser.parse_args()
    if args.repo and any(not NAME.fullmatch(n) for n in args.repo):
        parser.error("invalid repository name")
    switch_values = (args.switch_repo, args.switch_branch, args.expect_branch, args.expect_head)
    if any(v is not None for v in switch_values):
        if not all(v is not None for v in switch_values) or args.repo:
            parser.error("switch requires all four explicit arguments and no --repo")
        try:
            print(guarded_switch(args.root, *switch_values))
        except ValueError as e:
            parser.exit(2, "STOP: " + str(e) + "\n")
        return
    if args.inventory_json:
        if args.compare or args.stdout:
            parser.error("inventory is a separate read-only operation")
        names = args.repo or [x.name for x in args.root.iterdir() if safe_repo(args.root, x.name)]
        print(json.dumps(dict(schema="fortis.docker.worktrees.v1",
            repositories={name: inventory_worktrees(args.root, name) for name in names}),
            ensure_ascii=False, sort_keys=True))
        return
    if not args.stdout:
        parser.error("snapshot output requires --stdout")
    try:
        output = snapshot(args.root, args.repo)
        if args.compare:
            output += "".join(x + "\n" for x in compare_branches(args.root, args.compare))
        sys.stdout.write(output)
    except ValueError as e:
        parser.exit(2, "STOP: " + str(e) + "\n")


if __name__ == "__main__":
    main()
