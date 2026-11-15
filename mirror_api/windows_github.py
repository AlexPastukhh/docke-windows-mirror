"""Closed GitHub GETs using the Windows owner's stored gh authentication.

No Gateway, Docker exec, PAT import, shell, login, fallback or Git writes.
This module refuses live transport outside Windows; tests inject at its edges.
"""
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import quote, urlencode

from .github_snapshot import collect

OWNER = 'AlexPastukhh'
NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')
MAX_OUTPUT = 1024 * 1024
MAX_STDERR = 16384
SNAPSHOT_SECONDS = 60
ERROR_CODES = frozenset({
    'GITHUB_READ_UNAVAILABLE', 'GITHUB_CLI_UNAVAILABLE', 'GITHUB_RESPONSE_TOO_LARGE',
    'GITHUB_TIMEOUT', 'GITHUB_AUTH_OR_READ_FAILED', 'GITHUB_RESPONSE_INVALID',
    'GITHUB_WINDOWS_REQUIRED', 'GITHUB_REPOSITORY_INVALID', 'GITHUB_REPOSITORY_MISMATCH',
    'GITHUB_BRANCH_INVALID', 'GITHUB_PAGE_INVALID', 'GITHUB_SCOPE_INVALID',
    *('GITHUB_HTTP_' + str(code) for code in (401, 403, 404, 429, 500, 502, 503, 504))})


class GitHubReadError(ValueError):
    """Fixed safe code only; never include gh output or a credential."""

    def __init__(self, code):
        super().__init__(code if code in ERROR_CODES else 'GITHUB_READ_UNAVAILABLE')


def is_windows():
    return os.name == 'nt'


def gh_environment():
    env = dict(os.environ)
    # Use the owner-managed gh credential store, not ambient token/debug/host
    # overrides inherited from another application or a developer shell.
    for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN',
                'GITHUB_ENTERPRISE_TOKEN', 'GH_HOST', 'GH_REPO', 'GH_DEBUG',
                'GH_CONFIG_DIR', 'GH_FORCE_TTY', 'HTTP_PROXY', 'HTTPS_PROXY',
                'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        env.pop(key, None)
    env.update(GH_PROMPT_DISABLED='1', GH_PAGER='', NO_COLOR='1',
               GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    return env


def run_gh(argv, *, timeout):
    """Drain both pipes with limits before allocation; kill on timeout/overflow."""
    if not is_windows():
        raise GitHubReadError('GITHUB_WINDOWS_REQUIRED')
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            env=gh_environment())
    except OSError:
        raise GitHubReadError('GITHUB_CLI_UNAVAILABLE') from None
    output, errors = bytearray(), bytearray()
    overflow, pipe_error = threading.Event(), threading.Event()

    def drain(pipe, target, limit):
        try:
            while True:
                chunk = pipe.read(32768)
                if not chunk:
                    break
                if len(target) + len(chunk) > limit:
                    overflow.set()
                    process.kill()
                    break
                target.extend(chunk)
        except OSError:
            pipe_error.set()
        finally:
            pipe.close()

    threads = [threading.Thread(target=drain, args=(process.stdout, output, MAX_OUTPUT), daemon=True),
               threading.Thread(target=drain, args=(process.stderr, errors, MAX_STDERR), daemon=True)]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pipe_error.set()
    finally:
        for thread in threads:
            thread.join(timeout=3)
    if any(thread.is_alive() for thread in threads) or pipe_error.is_set():
        raise GitHubReadError('GITHUB_READ_UNAVAILABLE')
    if overflow.is_set():
        raise GitHubReadError('GITHUB_RESPONSE_TOO_LARGE')
    if timed_out:
        raise GitHubReadError('GITHUB_TIMEOUT')
    if process.returncode:
        # stderr is only classified, never returned, logged or interpolated.
        message = bytes(errors).decode('utf-8', errors='replace')
        match = re.search(r'\bHTTP (401|403|404|429|500|502|503|504)\b', message)
        raise GitHubReadError('GITHUB_HTTP_' + match[1] if match else 'GITHUB_AUTH_OR_READ_FAILED')
    try:
        def strict(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        def no_constant(_):
            raise ValueError()
        def finite_float(value):
            result = float(value)
            if not math.isfinite(result):
                raise ValueError()
            return result
        return json.loads(bytes(output).decode('utf-8'), object_pairs_hook=strict,
                          parse_constant=no_constant, parse_float=finite_float)
    except (ValueError, UnicodeError, RecursionError):
        raise GitHubReadError('GITHUB_RESPONSE_INVALID') from None


def text(value, maximum=200):
    if type(value) is not str or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise GitHubReadError('GITHUB_RESPONSE_INVALID')
    return value


def sha(value):
    if type(value) is not str or not SHA.fullmatch(value):
        raise GitHubReadError('GITHUB_RESPONSE_INVALID')
    return value


def number(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise GitHubReadError('GITHUB_RESPONSE_INVALID')
    return value


def rows(value, maximum=100):
    if type(value) is not list or len(value) > maximum or any(type(x) is not dict for x in value):
        raise GitHubReadError('GITHUB_RESPONSE_INVALID')
    return value


class WindowsGitHub:
    def __init__(self, repo):
        if not is_windows():
            raise GitHubReadError('GITHUB_WINDOWS_REQUIRED')
        if type(repo) is not str or not NAME.fullmatch(repo):
            raise GitHubReadError('GITHUB_REPOSITORY_INVALID')
        executable = shutil.which('gh')
        if not executable or not os.path.isabs(executable):
            raise GitHubReadError('GITHUB_CLI_UNAVAILABLE')
        self.executable, self.repo = executable, repo
        self.deadline = time.monotonic() + SNAPSHOT_SECONDS
        self.main_sha = None
        self.verified = False

    def _read(self, resource='', **query):
        # Every caller of this internal method selects a fixed route below.
        # No URL, hostname, verb, raw CLI arguments or headers come from HTTP.
        remaining = self.deadline - time.monotonic()
        if remaining <= 10:
            raise GitHubReadError('GITHUB_TIMEOUT')
        endpoint = 'repos/' + OWNER + '/' + self.repo
        if resource:
            endpoint += '/' + resource
        if query:
            endpoint += '?' + urlencode(query)
        return run_gh([self.executable, 'api', '--hostname', 'github.com',
                       '--method', 'GET', endpoint], timeout=min(25, remaining - 10))

    def verify_repository(self):
        raw = self._read()
        if (type(raw) is not dict or type(raw.get('full_name')) is not str or raw['full_name'].casefold() !=
                (OWNER + '/' + self.repo).casefold() or
                type(raw.get('owner')) is not dict or
                type(raw['owner'].get('login')) is not str or
                raw['owner']['login'].casefold() != OWNER.casefold()):
            raise GitHubReadError('GITHUB_REPOSITORY_MISMATCH')
        self.verified = True

    def _scope(self, repo):
        if not self.verified or repo != self.repo:
            raise GitHubReadError('GITHUB_REPOSITORY_MISMATCH')

    def get_branch(self, repo, branch):
        self._scope(repo)
        if (type(branch) is not str or branch != 'main' and not re.fullmatch(
                r'task/[A-Za-z0-9_][A-Za-z0-9_./-]{0,159}', branch) or '..' in branch):
            raise GitHubReadError('GITHUB_BRANCH_INVALID')
        try:
            raw = self._read('branches/' + quote(branch, safe=''))
        except GitHubReadError as exc:
            if str(exc) == 'GITHUB_HTTP_404':
                return {'found': False, 'branch': None}
            raise
        if type(raw) is not dict or raw.get('name') != branch:
            raise GitHubReadError('GITHUB_RESPONSE_INVALID')
        head = sha(raw['commit']['sha'])
        if branch == 'main':
            self.main_sha = head
        return {'found': True, 'branch': {'name': branch, 'sha': head}}

    @staticmethod
    def _page(page, per_page):
        if type(page) is not int or not 1 <= page <= 5 or type(per_page) is not int or not 1 <= per_page <= 100:
            raise GitHubReadError('GITHUB_PAGE_INVALID')

    def list_branches(self, repo, *, page, per_page):
        self._scope(repo); self._page(page, per_page)
        raw = rows(self._read('branches', page=page, per_page=per_page), per_page)
        projected = [{'name': text(x['name']), 'sha': sha(x['commit']['sha'])} for x in raw]
        for row in projected:
            if row['name'] == 'main':
                self.main_sha = row['sha']
        return {'items': projected}

    def list_commits(self, repo, *, page, per_page):
        self._scope(repo); self._page(page, per_page)
        head = sha(self.main_sha)
        raw = rows(self._read('commits', sha=head, page=page, per_page=per_page), per_page)
        return {'items': [{'sha': sha(x['sha'])} for x in raw]}

    def _pull(self, raw, *, detailed=False):
        if type(raw) is not dict or raw.get('state') not in ('open', 'closed') or type(raw.get('draft')) is not bool:
            raise GitHubReadError('GITHUB_RESPONSE_INVALID')
        head, base = raw['head'], raw['base']
        if (type(head) is not dict or type(base) is not dict or
                type(base.get('repo')) is not dict or
                head.get('repo') is not None and type(head['repo']) is not dict):
            raise GitHubReadError('GITHUB_RESPONSE_INVALID')
        if (type(base['repo'].get('full_name')) is not str or
                base['repo']['full_name'].casefold() != (OWNER + '/' + self.repo).casefold()):
            raise GitHubReadError('GITHUB_REPOSITORY_MISMATCH')
        identifier = number(raw['number'])
        if detailed and type(raw.get('merged')) is not bool:
            raise GitHubReadError('GITHUB_RESPONSE_INVALID')
        return dict(number=identifier, title=text(raw['title'], 500), state=raw['state'],
                    draft=raw['draft'], merged=raw['merged'] if detailed else False,
                    headRepository=text(head['repo']['full_name'], 240) if head.get('repo') is not None else None,
                    url='https://github.com/'+OWNER+'/'+self.repo+'/pull/'+str(identifier),
                    head={'ref': text(head['ref']), 'sha': sha(head['sha'])},
                    base={'ref': text(base['ref']), 'sha': sha(base['sha'])})

    def list_pull_requests(self, repo, *, state, page, per_page):
        self._scope(repo); self._page(page, per_page)
        if state != 'all':
            raise GitHubReadError('GITHUB_SCOPE_INVALID')
        return {'items': [self._pull(x) for x in rows(self._read('pulls', state='all',
                         page=page, per_page=per_page), per_page)]}

    def get_pull_request(self, repo, identifier):
        self._scope(repo)
        raw = self._read('pulls/' + str(number(identifier)))
        projected = self._pull(raw, detailed=True)
        if projected['number'] != identifier:
            raise GitHubReadError('GITHUB_RESPONSE_INVALID')
        return {'pullRequest': projected}

    def list_workflow_runs(self, repo, *, page, per_page):
        self._scope(repo); self._page(page, per_page)
        raw = self._read('actions/runs', page=page, per_page=per_page)
        if type(raw) is not dict:
            raise GitHubReadError('GITHUB_RESPONSE_INVALID')
        projected = []
        for x in rows(raw['workflow_runs'], per_page):
            identifier = number(x['id'])
            projected.append(dict(id=identifier, name=text(x['name']), event=text(x['event'], 64),
                status=text(x['status'], 32), conclusion=None if x['conclusion'] is None else text(x['conclusion'], 32),
                headBranch=None if x['head_branch'] is None else text(x['head_branch']), headSha=sha(x['head_sha']),
                runNumber=number(x['run_number']), runAttempt=number(x.get('run_attempt', 1)),
                createdAt=text(x['created_at'], 40), updatedAt=text(x['updated_at'], 40),
                url='https://github.com/'+OWNER+'/'+self.repo+'/actions/runs/'+str(identifier)))
        return {'items': projected}

    def list_check_runs(self, repo, ref):
        self._scope(repo); sha(ref)
        raw = self._read('commits/' + ref + '/check-runs', per_page=100)
        if type(raw) is not dict or type(raw.get('total_count')) is not int or raw['total_count'] < 0:
            raise GitHubReadError('GITHUB_RESPONSE_INVALID')
        projected = []
        for x in rows(raw['check_runs']):
            identifier = number(x['id'])
            projected.append(dict(id=identifier, name=text(x['name']), headSha=sha(x['head_sha']),
                status=text(x['status'], 32), conclusion=None if x['conclusion'] is None else text(x['conclusion'], 32),
                startedAt=None if x.get('started_at') is None else text(x['started_at'], 40),
                completedAt=None if x.get('completed_at') is None else text(x['completed_at'], 40),
                url='https://github.com/'+OWNER+'/'+self.repo+'/runs/'+str(identifier)))
        if raw['total_count'] < len(projected):
            raise GitHubReadError('GITHUB_RESPONSE_INVALID')
        return {'items': projected, 'totalCount': raw['total_count']}


def snapshot(repo, *, worktree_branches=None, main_only=False, main_ancestor_sha=None):
    empty = dict(schema='fortis.github.mirror.v1', repoId=repo, head=None,
                 branches={}, pullRequests=None, workflowRuns=None, checkRuns={},
                 errors={}, truncated={}, mainHistory=None, source='windows-gh')
    try:
        if (worktree_branches is not None and (type(worktree_branches) not in (list, tuple) or
                len(worktree_branches) > 100 or any(type(b) is not str or not re.fullmatch(
                r'task/[A-Za-z0-9_][A-Za-z0-9_./-]{0,159}', b) or '..' in b for b in worktree_branches))):
            raise GitHubReadError('GITHUB_BRANCH_INVALID')
        if main_ancestor_sha is not None:
            sha(main_ancestor_sha)
        client = WindowsGitHub(repo)
        client.verify_repository()
        return collect(client, repo, worktree_branches=worktree_branches,
            main_only=main_only, main_ancestor_sha=main_ancestor_sha,
            source='windows-gh', error_prefix='GITHUB', targeted_reads=True)
    except GitHubReadError as exc:
        # Every GitHubReadError is constructed from a fixed code in this module.
        empty['errors']['github'] = str(exc)
        return empty
    except (ValueError, KeyError, TypeError, AttributeError):
        empty['errors']['github'] = 'GITHUB_WINDOWS_READ_UNAVAILABLE'
        return empty
