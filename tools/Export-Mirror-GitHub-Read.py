#!/usr/bin/env python3
"""Read only GitHub PR/Actions/check status through the authenticated Fortis Gateway.

Docker Repo worker only. Never uses GitHub PAT/helper or action/owner routes.
The accepted dynamic native Gateway grants token-scoped repository reads.
"""
import argparse
import json
import re
import sys
from pathlib import Path

MIRROR_SOURCE = str(Path(__file__).resolve().parents[1])
if MIRROR_SOURCE not in sys.path:
    sys.path.insert(0, MIRROR_SOURCE)

SOURCE = '/workspace/repo/Repos/fortis-approvals'
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

try:
    from fortis_approvals.workflow.client import WorkflowTransport
    from fortis_approvals.core import Denied
    from fortis_approvals.integrations.github.repo_client.client import GitHubClient
except ModuleNotFoundError as missing:
    if not missing.name or missing.name.split('.')[0] != 'fortis_approvals':
        raise
    # GitHub Actions checks this repository independently. Runtime still requires
    # the installed Fortis Gateway typed client inside Docker; never fall back
    # to direct network Git or provider credentials.
    class Denied(Exception):
        pass
    WorkflowTransport = None
    GitHubClient = None

from mirror_api.github_snapshot import NAME, SHA, collect as _collect, read_error


def collect(client, repo, *, worktree_branches=None, main_only=False, main_ancestor_sha=None):
    """Retained typed Gateway helper; browser runtime uses Windows gh instead."""
    return _collect(client, repo, worktree_branches=worktree_branches,
                    main_only=main_only, main_ancestor_sha=main_ancestor_sha,
                    error_types=(Denied, ValueError, TypeError, KeyError))


def inspect(repo, *, worktree_branches=None, main_only=False, main_ancestor_sha=None):
    if WorkflowTransport is None or GitHubClient is None:
        return dict(schema='fortis.github.mirror.v1', repoId=repo,
                    head=None, branches={}, pullRequests=None, workflowRuns=None,
                    checkRuns={}, errors={'gateway': 'GATEWAY_TYPED_CLIENT_MISSING'},
                    truncated={}, source='fortis-gateway')
    transport = WorkflowTransport(endpoint='https://host.docker.internal:19204')
    try:
        cap = transport.post('/v1/workspace/capabilities', {})
        if cap.get('owner') != 'AlexPastukhh':
            raise ValueError('GATEWAY_OWNER_MISMATCH')
        if cap.get('dynamicRepositories') is not True and cap.get('repository') != repo:
            return dict(schema='fortis.github.mirror.v1', repoId=repo,
                        head=None, branches={}, pullRequests=None, workflowRuns=None, checkRuns={},
                        errors={'scope':'GATEWAY_REPOSITORY_NOT_REGISTERED'},
                        truncated={}, source='fortis-gateway')
        return collect(GitHubClient(gateway=transport), repo,
                       worktree_branches=worktree_branches, main_only=main_only,
                       main_ancestor_sha=main_ancestor_sha)
    except (Denied, ValueError, KeyError, TypeError, OSError) as exc:
        return dict(schema='fortis.github.mirror.v1', repoId=repo,
                    head=None, branches={}, pullRequests=None, workflowRuns=None, checkRuns={},
                    errors={'gateway': read_error(exc, 'GATEWAY_READ_UNAVAILABLE')},
                    truncated={}, source='fortis-gateway')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--main-only', action='store_true')
    parser.add_argument('--main-ancestor-sha')
    parser.add_argument('--scope-worktrees', action='store_true')
    parser.add_argument('--worktree-branch', action='append', default=[])
    args = parser.parse_args()
    if args.worktree_branch and not args.scope_worktrees:
        parser.error('worktree branches require explicit scope')
    if (len(args.worktree_branch) > 100 or
        any(not re.fullmatch(r'task/[A-Za-z0-9_][A-Za-z0-9_./-]{0,159}', b)
            for b in args.worktree_branch)):
        parser.error('invalid worktree branch')
    if args.main_ancestor_sha is not None and not SHA.fullmatch(args.main_ancestor_sha):
        parser.error('invalid main ancestor sha')
    if not NAME.fullmatch(args.repo) or args.repo in ('.', '..'):
        parser.error('invalid repository')
    print(json.dumps(inspect(args.repo,
        worktree_branches=args.worktree_branch if args.scope_worktrees else None,
        main_only=args.main_only, main_ancestor_sha=args.main_ancestor_sha),
        separators=(',', ':'), ensure_ascii=True,
                     sort_keys=True))


if __name__ == '__main__':
    main()