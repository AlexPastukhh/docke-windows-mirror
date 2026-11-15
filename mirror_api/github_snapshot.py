"""Shared bounded GitHub snapshot collector, independent of credentials/transport.

Clients expose closed projected reads. Windows gh and the retained typed Gateway
helper supply their own transport; no GitHub/Gateway authorization lives here.
"""
from concurrent.futures import ThreadPoolExecutor
import re
import time

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


def read_error(error, generic, prefix='GATEWAY'):
    """Expose fixed error codes, never raw provider messages."""
    if str(error) in (
            'FORTIS_HTTP_403', 'FORTIS_HTTP_404', 'FORTIS_HTTP_429', 'FORTIS_HTTP_500'):
        return prefix + '_HTTP_' + str(error)[-3:]
    if prefix == 'GITHUB' and str(error) in {
            'GITHUB_HTTP_401', 'GITHUB_HTTP_403', 'GITHUB_HTTP_404', 'GITHUB_HTTP_429',
            'GITHUB_HTTP_500', 'GITHUB_HTTP_502', 'GITHUB_HTTP_503', 'GITHUB_HTTP_504',
            'GITHUB_TIMEOUT', 'GITHUB_CLI_UNAVAILABLE', 'GITHUB_RESPONSE_TOO_LARGE',
            'GITHUB_AUTH_OR_READ_FAILED'}:
        return str(error)
    return generic


def pages(fetch):
    items = []
    for page in range(1, MAX_PAGES + 1):
        batch = fetch(page)
        if type(batch) is not dict or type(batch.get('items')) is not list:
            raise ValueError('GITHUB_RESPONSE_INVALID')
        rows = batch['items']
        if len(rows) > PAGE_SIZE:
            raise ValueError('GITHUB_PAGE_INVALID')
        items.extend(rows)
        if len(rows) < PAGE_SIZE:
            return items, False
    return items, True


def collect(client, repo, *, worktree_branches=None, main_only=False, main_ancestor_sha=None,
            error_types=(ValueError, TypeError, KeyError), source='fortis-gateway',
            error_prefix='GATEWAY', targeted_reads=False):
    """Snapshot independent projected reads concurrently; retain all unknowns.

    The three independent list reads may run together; individual closed PR
    detail and exact-head checks are still scoped to visible local worktrees.
    No cache, GitHub token, write operation, or inferred CI success is used.
    """
    def code(name):
        return error_prefix + '_' + name

    started = time.monotonic()
    results = dict(schema='fortis.github.mirror.v1', repoId=repo,
                   head=None, branches={}, pullRequests=None, workflowRuns=None,
                   checkRuns={}, errors={}, truncated={}, mainHistory=None, source=source)
    scope = None if worktree_branches is None else frozenset(worktree_branches)
    timings = {}

    def timed(fn):
        began = time.monotonic()
        return fn(), round((time.monotonic() - began) * 1000)

    def read_branches():
        # A client may expose fixed target reads; otherwise enumerate bounded pages.
        branches = {}
        if targeted_reads and (main_only or scope is not None):
            for name in ['main'] + sorted(scope or ()):
                if name in branches:
                    continue
                found = client.get_branch(repo, name)
                if found['found']:
                    branches[name] = found['branch']['sha']
            return branches, False
        truncated = True
        for page in range(1, MAX_BRANCH_PAGES + 1):
            response = client.list_branches(repo, page=page, per_page=BRANCH_PAGE_SIZE)
            if type(response) is not dict or type(response.get('items')) is not list:
                raise ValueError(code('BRANCH_RESPONSE_INVALID'))
            rows = response['items']
            if len(rows) > BRANCH_PAGE_SIZE:
                raise ValueError(code('BRANCH_PAGE_INVALID'))
            for row in rows:
                name, sha = row['name'], row['sha']
                if (type(name) is not str or not name or len(name) > 200 or
                        type(sha) is not str or not SHA.fullmatch(sha) or name in branches):
                    raise ValueError(code('BRANCH_INVALID'))
                branches[name] = sha
            if len(rows) < BRANCH_PAGE_SIZE:
                truncated = False
                break
        return branches, truncated

    def read_main_history(head):
        """Prove only ancestry on pinned default/main; absence is never divergence."""
        if not SHA.fullmatch(head) or not SHA.fullmatch(main_ancestor_sha):
            raise ValueError(code('MAIN_HISTORY_INPUT_INVALID'))
        examined = 0
        for page in range(1, MAX_MAIN_HISTORY_PAGES + 1):
            batch = client.list_commits(repo, page=page, per_page=MAX_MAIN_HISTORY_PAGE_SIZE)
            if type(batch) is not dict or type(batch.get('items')) is not list:
                raise ValueError(code('MAIN_HISTORY_INVALID'))
            items = batch['items']
            if len(items) > MAX_MAIN_HISTORY_PAGE_SIZE or not items:
                raise ValueError(code('MAIN_HISTORY_INVALID'))
            shas = [row.get('sha') if type(row) is dict else None for row in items]
            if any(type(sha) is not str or not SHA.fullmatch(sha) for sha in shas):
                raise ValueError(code('MAIN_HISTORY_INVALID'))
            # list_commits uses GitHub's default branch. If it moved or is
            # different from main, no claim about the checked main is safe.
            if page == 1 and shas[0] != head:
                raise ValueError(code('MAIN_HISTORY_HEAD_CHANGED'))
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
        except error_types:
            results['errors']['mainHistory'] = code('MAIN_HISTORY_UNAVAILABLE')
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
                results['errors']['branches'] = code('MAIN_UNVERIFIED')
        except error_types as exc:
            results['errors']['branches'] = read_error(exc, code('BRANCH_READ_UNAVAILABLE'), error_prefix)
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
                results['errors']['branches'] = code('MAIN_UNVERIFIED')
        except error_types as exc:
            results['errors']['branches'] = read_error(exc, code('BRANCH_READ_UNAVAILABLE'), error_prefix)
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
                        detail.get('headRepository') != pr.get('headRepository') or
                        detail['state'] != 'closed'):
                    raise ValueError(code('PR_IDENTITY_CHANGED'))
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
                        except error_types:
                            results['errors']['pullRequestDetails'] = code('PR_DETAILS_PARTIAL')
            timings['closedPrDetails'] = round((time.monotonic() - detail_start) * 1000)
            results['pullRequests'] = pulls
        except error_types:
            results['errors']['pullRequests'] = code('PR_READ_UNAVAILABLE')
        try:
            (runs, limited), timings['workflowRuns'] = runs_job.result()
            results['workflowRuns'] = runs
            results['truncated']['workflowRuns'] = limited
        except error_types:
            results['errors']['workflowRuns'] = code('CI_READ_UNAVAILABLE')

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
                    raise ValueError(code('CHECKS_INVALID'))
                checks = value['items']
                if any(row['headSha'] != sha for row in checks):
                    raise ValueError(code('CHECK_SHA_MISMATCH'))
                results['checkRuns'][sha] = {'items': checks,
                    'totalCount': value['totalCount'],
                    'truncated': value['totalCount'] > len(checks)}
            except error_types:
                results['checkRuns'][sha] = {'items': None,
                    'totalCount': None, 'error': code('CHECKS_UNAVAILABLE')}
    timings['checkRuns'] = round((time.monotonic() - checks_start) * 1000)
    timings['total'] = round((time.monotonic() - started) * 1000)
    results['timingsMs'] = timings
    return results

