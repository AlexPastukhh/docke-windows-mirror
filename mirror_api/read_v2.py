"""Bounded machine READ projections over the same MirrorCore as the owner UI.

No worker, receipt lookup, writer claim, fetch or preview allocation is used.
The P0 catalog remains the design baseline; source readiness is separate.
"""
import hashlib
import json
import re

from .commands import budget
from .operations import ObservationError, REPO, SHA, checked_at

READS = ('list_repositories_v2', 'get_repository_v2', 'list_worktrees_v2',
         'get_github_status_v2', 'get_available_actions_v2', 'get_capabilities_v2')
DEFERRED = ('check_windows_history_v2', 'preview_windows_pull_v2',
            'execute_windows_pull_v2', 'get_windows_pull_result_v2')
BRANCH = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_./-]{0,159}\Z')
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
UTC = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:Z|\+00:00)\Z')


class ReadError(ValueError):
    pass


def require(value, code='SCHEMA_INVALID'):
    if not value:
        raise ReadError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def scope_digest(repos):
    return digest(sorted(repos))


def page_params(params, maximum=128):
    require(type(params) is dict and set(params) == {'offset', 'limit', 'inventoryRevision'})
    require(type(params['offset']) is int and 0 <= params['offset'] <= 10000)
    require(type(params['limit']) is int and 1 <= params['limit'] <= maximum)
    revision = params['inventoryRevision']
    require(revision is None or type(revision) is str and DIGEST.fullmatch(revision))
    require(params['offset'] == 0 or revision is not None, 'INVENTORY_CHANGED')


def page(items, params, revision):
    if params['inventoryRevision'] not in (None, revision):
        raise ReadError('INVENTORY_CHANGED')
    offset, limit = params['offset'], params['limit']
    require(offset <= len(items), 'INVENTORY_CHANGED')
    end = min(offset + limit, len(items))
    return {'items': items[offset:end], 'inventoryRevision': revision,
            'nextOffset': end if end < len(items) else None, 'truncated': end < len(items)}


def source(raw, *, kind, timestamp):
    require(type(raw) is dict)
    head, branch, clean = raw.get('head'), raw.get('branch'), raw.get('clean')
    require(head is None or type(head) is str and SHA.fullmatch(head))
    require(branch is None or type(branch) is str and BRANCH.fullmatch(branch))
    require(clean is None or type(clean) is bool)
    observed = raw.get('checkedAt', timestamp)
    require(observed is None or type(observed) is str and UTC.fullmatch(observed))
    error = raw.get('error')
    if error == 'NOT_CHECKED':
        verification, code = 'NOT_CHECKED', 'NOT_CHECKED'
    elif error:
        verification, code = 'ERROR', 'SOURCE_UNAVAILABLE'
    elif head is None:
        verification, code = 'UNKNOWN', 'SOURCE_UNAVAILABLE'
    elif kind == 'docker' and raw.get('status') != 'registered':
        verification, code = 'UNKNOWN', 'SOURCE_UNAVAILABLE'
    elif observed is None:
        verification, code = 'UNKNOWN', 'SOURCE_UNAVAILABLE'
    else:
        verification, code = 'VERIFIED', None
    if verification == 'NOT_CHECKED':
        observed, head, branch, clean = None, None, None, None
    if kind == 'github':
        branch, clean = ('main' if head is not None else None), None
    return {'verification': verification, 'checkedAt': observed, 'head': head,
            'branch': branch, 'clean': clean, 'errorCode': code}


def actions():
    # Capability describes this source surface, never the actor's Gateway grant
    # or whether a particular clone could pass a future signed Pull preflight.
    return [{'operation': op, 'available': op in READS,
             'blockers': [] if op in READS else ['POLICY_DENIED']}
            for op in READS + DEFERRED]


class CoreReadV2:
    def __init__(self, core, *, allowed_repos, inventories):
        require(type(allowed_repos) in (list, tuple, frozenset) and
                1 <= len(allowed_repos) <= 128 and len(set(allowed_repos)) == len(allowed_repos))
        require(all(type(x) is str and REPO.fullmatch(x) for x in allowed_repos))
        require(type(inventories) is dict and 1 <= len(inventories) <= 32)
        self.allowed_repos = frozenset(allowed_repos)
        self.inventories = {}
        for name, members in inventories.items():
            require(type(name) is str and REPO.fullmatch(name))
            require(type(members) in (list, tuple) and 1 <= len(members) <= 128 and
                    all(type(x) is str and x in self.allowed_repos for x in members) and
                    len(set(members)) == len(members))
            self.inventories[name] = tuple(sorted(members))
        self.core = core

    def _row(self, repo, *, details=False):
        require(not self.core.closed, 'SOURCE_UNAVAILABLE')
        snapshot = self.core.inventory(remote=details, repo=repo, details=details)
        require(type(snapshot) is dict and snapshot.get('schemaVersion') == 1 and
                not snapshot.get('errors') and type(snapshot.get('repositories')) is list,
                'SOURCE_UNAVAILABLE')
        rows = snapshot['repositories']
        require(len(rows) == 1 and type(rows[0]) is dict and rows[0].get('repoId') == repo,
                'SOURCE_UNAVAILABLE')
        require(type(rows[0].get('docker')) is dict)
        primary = rows[0]['docker'].get('primary')
        require(type(primary) is dict and primary.get('repoId') == repo and primary.get('kind') == 'primary')
        require(all(type(rows[0].get(key)) is dict and rows[0][key].get('repoId') == repo
                    for key in ('windows', 'github')))
        require(type(snapshot.get('checkedAt')) is str and UTC.fullmatch(snapshot['checkedAt']))
        return rows[0], snapshot['checkedAt']

    def _repository(self, repo):
        row, observed = self._row(repo)
        require(type(row.get('docker')) is dict)
        result = {'repoId': repo,
                  'docker': source(row['docker'].get('primary'), kind='docker', timestamp=observed),
                  'windows': source(row.get('windows'), kind='windows', timestamp=observed),
                  'github': source(row.get('github'), kind='github', timestamp=observed),
                  'relation': 'unknown', 'actions': actions()}
        # remote=False intentionally means no fresh GitHub comparison. In
        # particular, absent SHA on both sides is never equality evidence.
        return result

    def _worktrees(self, repo, params):
        row, _ = self._row(repo)
        docker = row.get('docker')
        require(type(docker) is dict and type(docker.get('primary')) is dict)
        raw = docker.get('worktrees')
        require(type(raw) is list and len(raw) <= 129, 'LIMIT_EXCEEDED')
        primary_count, items, tasks = 0, [], set()
        for value in raw:
            require(type(value) is dict and value.get('repoId') == repo)
            if value.get('kind') == 'primary':
                require(value == docker['primary'] and primary_count == 0)
                primary_count += 1
                continue
            require(value.get('kind') == 'worktree')
            task, branch = value.get('task'), value.get('branch')
            require(type(task) is str and REPO.fullmatch(task) and task not in tasks)
            require(type(branch) is str and BRANCH.fullmatch(branch))
            require(value.get('head') is None or type(value['head']) is str and SHA.fullmatch(value['head']))
            require(value.get('clean') is None or type(value['clean']) is bool)
            require(type(value.get('targetEligible')) is bool)
            tasks.add(task)
            items.append({key: value[key] for key in ('task', 'branch', 'head', 'clean', 'targetEligible')})
        require(primary_count == 1)
        items.sort(key=lambda value: value['task'])
        return page(items, params, digest({'repo': repo, 'worktrees': items}))

    def _github(self, repo, params):
        row, observed = self._row(repo, details=True)
        raw = row.get('github')
        require(type(raw) is dict, 'SOURCE_UNAVAILABLE')
        main = source(raw, kind='github', timestamp=observed)
        require(main['verification'] == 'VERIFIED', 'GITHUB_MAIN_UNVERIFIED')
        errors = raw.get('readErrors') or {}
        require(type(errors) is dict and not any(key in errors for key in
                ('branches', 'scope', 'github', 'pullRequests', 'workflowRuns')), 'SOURCE_UNAVAILABLE')
        pulls, runs = raw.get('pullRequests'), raw.get('workflowRuns')
        require(type(pulls) is list and type(runs) is list, 'SOURCE_UNAVAILABLE')
        require(len(pulls) <= 128 and len(runs) <= 128, 'LIMIT_EXCEEDED')
        prs, workflows, ids, run_ids = [], [], set(), set()
        omitted_closed = False
        for value in pulls:
            require(type(value) is dict and type(value.get('number')) is int and
                    1 <= value['number'] <= 1000000 and value['number'] not in ids)
            require(value.get('state') in ('open', 'closed') and type(value.get('draft')) is bool)
            if value['state'] == 'closed' and value.get('mergeVerified') is not True:
                # A closed-list row cannot distinguish merged from unmerged.
                # Omit it explicitly as a partial observation, never fabricate CLOSED.
                omitted_closed = True
                continue
            head, base = value.get('head'), value.get('base')
            require(type(head) is dict and type(base) is dict)
            require(all(type(x) is str and SHA.fullmatch(x) for x in (head.get('sha'), base.get('sha'))))
            ids.add(value['number'])
            prs.append({'number': value['number'], 'head': head['sha'], 'base': base['sha'],
                        'state': 'OPEN' if value['state'] == 'open' else
                                 'MERGED' if value.get('merged') is True else 'CLOSED',
                        'draft': value['draft']})
        for value in runs:
            require(type(value) is dict and type(value.get('id')) is int and
                    value['id'] >= 1 and value['id'] not in run_ids)
            require(type(value.get('headSha')) is str and SHA.fullmatch(value['headSha']))
            state = {'queued': 'QUEUED', 'in_progress': 'IN_PROGRESS', 'completed': 'COMPLETED'}.get(value.get('status'), 'UNKNOWN')
            conclusion = value.get('conclusion')
            conclusion = (None if conclusion is None else
                          {'success': 'SUCCESS', 'failure': 'FAILURE', 'cancelled': 'CANCELLED',
                           'skipped': 'SKIPPED'}.get(conclusion, 'UNKNOWN'))
            workflows.append({'id': value['id'], 'head': value['headSha'], 'state': state,
                              'conclusion': conclusion})
            run_ids.add(value['id'])
        prs.sort(key=lambda x: str(x['number']))
        workflows.sort(key=lambda x: str(x['id']))
        truncation = raw.get('truncated') or {}
        require(type(truncation) is dict and all(type(value) is bool for value in truncation.values()))
        limited = omitted_closed or any(truncation.values())
        semantic_main = {k: v for k, v in main.items() if k != 'checkedAt'}
        revision = digest({'repo': repo, 'main': semantic_main, 'prs': prs, 'runs': workflows,
                           'limited': limited})
        # Both independent lists use the same offset/revision; exhausting one
        # list before the other must not falsely invalidate the remaining page.
        maximum = max(len(prs), len(workflows))
        require(params['offset'] <= maximum, 'INVENTORY_CHANGED')
        if params['inventoryRevision'] not in (None, revision):
            raise ReadError('INVENTORY_CHANGED')
        start, end = params['offset'], min(params['offset'] + params['limit'], maximum)
        return {'main': main, 'pullRequests': prs[start:end], 'runs': workflows[start:end],
                'requiredCI': 'NOT_VERIFIED', 'truncated': limited or end < maximum,
                'nextOffset': end if end < maximum else None, 'inventoryRevision': revision}

    def call(self, operation, target, params, *, inventory_scope=None):
        require(operation in READS + DEFERRED, 'POLICY_DENIED')
        require(type(target) is str and REPO.fullmatch(target), 'REPOSITORY_NOT_ALLOWED')
        result = {'schemaVersion': 2, 'operation': operation, 'target': target,
                  'checkedAt': checked_at(), 'data': None, 'error': None}
        try:
            if operation in ('list_repositories_v2', 'get_capabilities_v2'):
                members = self.inventories.get(target)
                require(members is not None and inventory_scope == scope_digest(members),
                        'REPOSITORY_NOT_ALLOWED')
            else:
                require(target in self.allowed_repos, 'REPOSITORY_NOT_ALLOWED')
            if operation in ('list_repositories_v2', 'list_worktrees_v2', 'get_github_status_v2'):
                page_params(params, 100 if operation == 'get_github_status_v2' else 128)
            else:
                require(type(params) is dict and not params)
            with budget():
                if operation == 'list_repositories_v2':
                    data = page(list(members), params, scope_digest(members))
                elif operation == 'get_repository_v2':
                    data = self._repository(target)
                elif operation == 'list_worktrees_v2':
                    data = self._worktrees(target, params)
                elif operation == 'get_github_status_v2':
                    data = self._github(target, params)
                elif operation == 'get_available_actions_v2':
                    self._row(target)  # A configured but missing clone is not a healthy target.
                    data = actions()
                elif operation == 'get_capabilities_v2':
                    data = [dict(item, version='2') for item in actions()]
                else:
                    raise ReadError('POLICY_DENIED')
            require(len(json.dumps(data, allow_nan=False).encode()) <= 250000, 'LIMIT_EXCEEDED')
            result['data'] = data
        except ReadError as exc:
            result['error'] = {'code': str(exc), 'retryable': False}
        except (ObservationError, OSError, ValueError, TypeError, KeyError):
            result['error'] = {'code': 'SOURCE_UNAVAILABLE', 'retryable': False}
        return result
