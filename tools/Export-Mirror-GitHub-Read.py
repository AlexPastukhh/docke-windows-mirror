#!/usr/bin/env python3
"""Read only GitHub PR/Actions/check status through the authenticated Fortis Gateway.

Docker Repo worker only. Never uses GitHub PAT/helper or action/owner routes.
The accepted dynamic native Gateway grants token-scoped repository reads.
"""
import argparse
import json
import re
import sys

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


def collect(client, repo):
    results = dict(schema='fortis.github.mirror.v1', repoId=repo,
                   head=None, branches={}, pullRequests=None, workflowRuns=None, checkRuns={},
                   errors={}, truncated={}, source='fortis-gateway')
    # GitHub refs and main are read through the authenticated typed Gateway,
    # never via a local Git credential/helper or Windows network connection.
    try:
        branches = {}
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
                break
        else:
            results['truncated']['branches'] = True
        results['branches'] = branches
        results['head'] = branches.get('main')
        if results['head'] is None:
            results['errors']['branches'] = 'GATEWAY_MAIN_UNVERIFIED'
    except (Denied, ValueError, TypeError, KeyError):
        results['errors']['branches'] = 'GATEWAY_BRANCH_READ_UNAVAILABLE'
    try:
        pulls, limited = pages(lambda page: client.list_pull_requests(
            repo, state='all', page=page, per_page=PAGE_SIZE))
        # The GitHub PR *list* often omits the merged boolean on closed PRs.
        # Only the individual GET /pulls/<number> proves merged vs closed.
        checked = 0
        for index, pr in enumerate(pulls):
            if pr['state'] != 'closed':
                pr['mergeVerified'] = True
                continue
            pr['mergeVerified'] = False
            if checked >= MAX_VERIFY_CLOSED:
                results['truncated']['pullRequestDetails'] = True
                continue
            checked += 1
            try:
                detail = client.get_pull_request(repo, pr['number'])['pullRequest']
                if (detail['number'] != pr['number'] or detail['head'] != pr['head'] or
                        detail['base']['ref'] != pr['base']['ref'] or
                        detail['state'] != 'closed'):
                    raise ValueError('GATEWAY_PR_IDENTITY_CHANGED')
                pulls[index] = {**detail, 'mergeVerified': True}
            except (Denied, ValueError, TypeError, KeyError):
                results['errors']['pullRequestDetails'] = 'GATEWAY_PR_DETAILS_PARTIAL'
        results['pullRequests'] = pulls
        results['truncated']['pullRequests'] = limited
    except (Denied, ValueError, TypeError, KeyError):
        results['errors']['pullRequests'] = 'GATEWAY_PR_READ_UNAVAILABLE'
    try:
        runs, limited = pages(lambda page: client.list_workflow_runs(
            repo, page=page, per_page=PAGE_SIZE))
        results['workflowRuns'] = runs
        results['truncated']['workflowRuns'] = limited
    except (Denied, ValueError, TypeError, KeyError):
        results['errors']['workflowRuns'] = 'GATEWAY_CI_READ_UNAVAILABLE'
    if results['pullRequests'] is not None:
        live = [pr for pr in results['pullRequests'] if pr['state'] == 'open']
        # Only exact PR head SHAs; never the most recent successful run from another commit.
        heads = list(dict.fromkeys(pr['head']['sha'] for pr in live if
                                   SHA.fullmatch(pr['head']['sha'])))
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
    return results


def inspect(repo):
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
        return collect(GitHubClient(gateway=transport), repo)
    except (Denied, ValueError, KeyError, TypeError, OSError):
        return dict(schema='fortis.github.mirror.v1', repoId=repo,
                    head=None, branches={}, pullRequests=None, workflowRuns=None, checkRuns={},
                    errors={'gateway':'GATEWAY_READ_UNAVAILABLE'},
                    truncated={}, source='fortis-gateway')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    args = parser.parse_args()
    if not NAME.fullmatch(args.repo) or args.repo in ('.', '..'):
        parser.error('invalid repository')
    print(json.dumps(inspect(args.repo), separators=(',', ':'), ensure_ascii=True,
                     sort_keys=True))


if __name__ == '__main__':
    main()
