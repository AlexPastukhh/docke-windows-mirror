#!/usr/bin/env python3
"""Read only GitHub PR/Actions/check status through the authenticated Fortis Gateway.

Docker Repo worker only. Never uses GitHub PAT/helper or action/owner routes.
The accepted dynamic native Gateway grants token-scoped repository reads.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import re
import sys
import time

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

NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')
MAX_PAGES = 4
PAGE_SIZE = 30
MAX_CHECK_HEADS = 10
MAX_VERIFY_CLOSED = 25
BRANCH_PAGE_SIZE = 100
MAX_BRANCH_PAGES = 5
MAX_MAIN_HISTORY_PAGES = 3
MAX_MAIN_HISTORY_PAGE_SIZE = 50


def read_error(error, generic):
    """Expose a bounded Gateway HTTP status, never raw provider messages."""
    if isinstance(error, Denied) and str(error) in (
            'FORTIS_HTTP_403', 'FORTIS_HTTP_404', 'FORTIS_HTTP_429', 'FORTIS_HTTP_500'):
        return 'GATEWAY_HTTP_' + str(error)[-3:]
    return generic


def pages(fetch):
    items = []
    for page in range(1, MAX_PAGES + 1):
        batch = fetch(page)
        if type(batch) is not dict or type(batch.get('items')) is not list:
            raise ValueError('GATEWAY_RESPONSE_INVALID')
        rows = batch['items']
        if len(rows) > PAGE_SIZE:
            raise ValueError('GATEWAY_PAGE_INVALID')
        items.extend(rows)
        if len(rows) < PAGE_SIZE:
            return items, False
    return items, True


def collect(client, repo, *, worktree_branches=None, main_only=False, main_ancestor_sha=None):
    """Snapshot independent Gateway reads concurrently; retain all unknowns.

    Each typed read uses a fresh HTTPS request and its own immutable request ID.
    The three independent list reads may run together; individual closed PR
    detail and exact-head checks are still scoped to visible local worktrees.
    No cache, GitHub token, write operation, or inferred CI success is used.
    """
    started = time.monotonic()
    results = dict(schema='fortis.github.mirror.v1', repoId=repo,
                   head=None, branches={}, pullRequests=None, workflowRuns=None,
                   checkRuns={}, errors={}, truncated={}, mainHistory=None, source='fortis-gateway')
    scope = None if worktree_branches is None else frozenset(worktree_branches)
    timings = {}

    def timed(fn):
        began = time.monotonic()
        return fn(), round((time.monotonic() - began) * 1000)

    def read_branches():
        # GitHub refs and main only through the authenticated typed Gateway.
        branches = {}
        truncated = True
        for page in range(1, MAX_BRANCH_PAGES + 1):
            response = client.list_branches(repo, page=page, per_page=BRANCH_PAGE_SIZE)
            if type(response) is not dict or type(response.get('items')) is not list:
                raise ValueError('GATEWAY_BRANCH_RESPONSE_INVALID')
            rows = response['items']
            if len(rows) > BRANCH_PAGE_SIZE:
                raise ValueError('GATEWAY_BRANCH_PAGE_INVALID')
            for row in rows:
                name, sha = row['name'], row['sha']
                if (type(name) is not str or not name or len(name) > 200 or
                        type(sha) is not str or not SHA.fullmatch(sha) or name in branches):
                    raise ValueError('GATEWAY_BRANCH_INVALID')
                branches[name] = sha
            if len(rows) < BRANCH_PAGE_SIZE:
                truncated = False
                break
        return branches, truncated

    def read_main_history(head):
        """Prove only ancestry on pinned default/main; absence is never divergence."""
        if not SHA.fullmatch(head) or not SHA.fullmatch(main_ancestor_sha):
            raise ValueError('GATEWAY_MAIN_HISTORY_INPUT_INVALID')
        examined = 0
        for page in range(1, MAX_MAIN_HISTORY_PAGES + 1):
            batch = client.list_commits(repo, page=page, per_page=MAX_MAIN_HISTORY_PAGE_SIZE)
            if type(batch) is not dict or type(batch.get('items')) is not list:
                raise ValueError('GATEWAY_MAIN_HISTORY_INVALID')
            items = batch['items']
            if len(items) > MAX_MAIN_HISTORY_PAGE_SIZE or not items:
                raise ValueError('GATEWAY_MAIN_HISTORY_INVALID')
            shas = [row.get('sha') if type(row) is dict else None for row in items]
            if any(type(sha) is not str or not SHA.fullmatch(sha) for sha in shas):
                raise ValueError('GATEWAY_MAIN_HISTORY_INVALID')
            # list_commits uses GitHub's default branch. If it moved or is
            # different from main, no claim about the checked main is safe.
            if page == 1 and shas[0] != head:
                raise ValueError('GATEWAY_MAIN_HISTORY_HEAD_CHANGED')
            examined += len(shas)
            if main_ancestor_sha in shas:
                return {'head': head, 'candidate': main_ancestor_sha,
                        'relation': 'behind', 'examined': examined}
            if len(items) < MAX_MAIN_HISTORY_PAGE_SIZE:
                break
        return {'head': head, 'candidate': main_ancestor_sha,
                'relation': 'unknown', 'examined': examined}

    def attach_main_history():
        if (main_ancestor_sha is None or results['head'] is None or
                main_ancestor_sha == results['head']):
            return
        began = time.monotonic()
        try:
            results['mainHistory'] = read_main_history(results['head'])
        except (Denied, ValueError, TypeError, KeyError):
            results['errors']['mainHistory'] = 'GATEWAY_MAIN_HISTORY_UNAVAILABLE'
        timings['mainHistory'] = round((time.monotonic() - began) * 1000)

    def read_pulls():
        return pages(lambda page: client.list_pull_requests(
            repo, state='all', page=page, per_page=PAGE_SIZE))

    def read_runs():
        return pages(lambda page: client.list_workflow_runs(
            repo, page=page, per_page=PAGE_SIZE))

    # The primary-row check / Windows Pull preflight only requires fresh main
    # SHA. Do not initiate expensive unrelated PR and CI reads. They are still
    # available by explicit separate worktree-detail inspection in the UI.
    if main_only:
        try:
            (branches, limited), timings['branches'] = timed(read_branches)
            results['branches'] = branches
            results['head'] = branches.get('main')
            if limited:
                results['truncated']['branches'] = True
            if results['head'] is None:
                results['errors']['branches'] = 'GATEWAY_MAIN_UNVERIFIED'
        except (Denied, ValueError, TypeError, KeyError) as exc:
            results['errors']['branches'] = read_error(exc, 'GATEWAY_BRANCH_READ_UNAVAILABLE')
        attach_main_history()
        timings['total'] = round((time.monotonic() - started) * 1000)
        results['timingsMs'] = timings
        return results

    # Bounded concurrency for just three independent read-only operations per
    # repository. The UI already limits the number of repositories in flight.
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix='mirror-read') as executor:
        branches_job = executor.submit(timed, read_branches)
        pulls_job = executor.submit(timed, read_pulls)
        runs_job = executor.submit(timed, read_runs)
        try:
            (branches, limited), timings['branches'] = branches_job.result()
            results['branches'] = branches
            results['head'] = branches.get('main')
            if limited:
                results['truncated']['branches'] = True
            if results['head'] is None:
                results['errors']['branches'] = 'GATEWAY_MAIN_UNVERIFIED'
        except (Denied, ValueError, TypeError, KeyError) as exc:
            results['errors']['branches'] = read_error(exc, 'GATEWAY_BRANCH_READ_UNAVAILABLE')
        try:
            (pulls, limited), timings['pullRequests'] = pulls_job.result()
            results['truncated']['pullRequests'] = limited
            # The list of closed PRs does NOT prove their merged status. Only
            # verify the closed PRs relevant to currently visible worktrees.
            eligible = []
            detail_start = time.monotonic()
            for index, pr in enumerate(pulls):
                if pr['state'] != 'closed':
                    pr['mergeVerified'] = True
                    continue
                pr['mergeVerified'] = False
                if scope is not None and pr['head']['ref'] not in scope:
                    continue
                if len(eligible) >= MAX_VERIFY_CLOSED:
                    results['truncated']['pullRequestDetails'] = True
                    continue
                eligible.append((index, pr))

            def verify_closed(item):
                index, pr = item
                detail = client.get_pull_request(repo, pr['number'])['pullRequest']
                if (detail['number'] != pr['number'] or detail['head'] != pr['head'] or
                        detail['base']['ref'] != pr['base']['ref'] or
                        detail['state'] != 'closed'):
                    raise ValueError('GATEWAY_PR_IDENTITY_CHANGED')
                return index, {**detail, 'mergeVerified': True}

            # Each PR is an independent read. Bound detail fan-out to three
            # to avoid overload while reducing long serial worktree checks.
            if eligible:
                with ThreadPoolExecutor(max_workers=min(3, len(eligible)),
                                        thread_name_prefix='mirror-pr') as details:
                    tasks = [details.submit(verify_closed, item) for item in eligible]
                    for job in tasks:
                        try:
                            index, verified = job.result()
                            pulls[index] = verified
                        except (Denied, ValueError, TypeError, KeyError):
                            results['errors']['pullRequestDetails'] = 'GATEWAY_PR_DETAILS_PARTIAL'
            timings['closedPrDetails'] = round((time.monotonic() - detail_start) * 1000)
            results['pullRequests'] = pulls
        except (Denied, ValueError, TypeError, KeyError):
            results['errors']['pullRequests'] = 'GATEWAY_PR_READ_UNAVAILABLE'
        try:
            (runs, limited), timings['workflowRuns'] = runs_job.result()
            results['workflowRuns'] = runs
            results['truncated']['workflowRuns'] = limited
        except (Denied, ValueError, TypeError, KeyError):
            results['errors']['workflowRuns'] = 'GATEWAY_CI_READ_UNAVAILABLE'

    attach_main_history()
    checks_start = time.monotonic()
    if results['pullRequests'] is not None:
        live = [pr for pr in results['pullRequests'] if pr['state'] == 'open']
        # Only exact current PR-head SHA for visible task worktrees. Other PRs
        # remain in the PR list without a fabricated check/CI status.
        heads = list(dict.fromkeys(pr['head']['sha'] for pr in live if
                                   SHA.fullmatch(pr['head']['sha']) and
                                   (scope is None or pr['head']['ref'] in scope)))
        if len(heads) > MAX_CHECK_HEADS:
            results['truncated']['checkRuns'] = True
        for sha in heads[:MAX_CHECK_HEADS]:
            try:
                value = client.list_check_runs(repo, sha)
                if type(value) is not dict or type(value.get('items')) is not list:
                    raise ValueError('GATEWAY_CHECKS_INVALID')
                checks = value['items']
                if any(row['headSha'] != sha for row in checks):
                    raise ValueError('GATEWAY_CHECK_SHA_MISMATCH')
                results['checkRuns'][sha] = {'items': checks,
                    'totalCount': value['totalCount'],
                    'truncated': value['totalCount'] > len(checks)}
            except (Denied, ValueError, TypeError, KeyError):
                results['checkRuns'][sha] = {'items': None,
                    'totalCount': None, 'error': 'GATEWAY_CHECKS_UNAVAILABLE'}
    timings['checkRuns'] = round((time.monotonic() - checks_start) * 1000)
    timings['total'] = round((time.monotonic() - started) * 1000)
    results['timingsMs'] = timings
    return results

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
