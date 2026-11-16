"""Shared Windows Mirror domain Core; adapters own only transport identity/state."""
import os
import sqlite3
from pathlib import Path
import threading
import time
from .commands import budget
from .operations import REPO, SHA, ObservationError
from .process_guard import WriterGuard, GuardError
from .pull import PullWorker, PullDenied


class MirrorCore:
    def __init__(self, backend, receipt_db=None, *, read_only=False):
        self.backend = backend
        self.receipt_db = Path(receipt_db) if receipt_db is not None else None
        self.read_only = read_only
        self.lock = threading.RLock()
        self._worker = None
        self._guard = None
        self.closed = False

    @property
    def windows_root(self):
        return self.backend.windows_root

    @property
    def configured(self):
        return not self.read_only and (self.receipt_db is not None or bool(os.environ.get('LOCALAPPDATA')))

    def worker(self):
        with self.lock:
            if self.closed:
                raise PullDenied('MIRROR_WRITER_UNAVAILABLE')
            if not self.configured:
                raise PullDenied('PULL_RECEIPT_STORAGE_UNAVAILABLE')
            if self._worker is None:
                target = self.receipt_db
                if target is None:
                    root = os.environ.get('LOCALAPPDATA', '')
                    if not root or not os.path.isabs(root):
                        raise PullDenied('PULL_RECEIPT_STORAGE_UNAVAILABLE')
                    target = Path(root) / 'FortisMirror' / 'pull-receipts.sqlite3'
                self._guard = WriterGuard(self.windows_root)
                try:
                    self._worker = PullWorker(self.backend, target, guard=self._guard)
                except PullDenied:
                    raise
                except (OSError, ValueError, sqlite3.Error):
                    raise PullDenied('PULL_RECEIPT_STORAGE_UNAVAILABLE') from None
            return self._worker

    def inventory(self, remote=False, repo=None, *, details=False, fetch_windows=False):
        if fetch_windows:
            raise ObservationError('MIRROR_READ_ONLY')
        with budget():
            return self.backend.inventory(remote=remote, **({'repo':repo} if repo is not None else {}), **({'details':True} if details else {}))

    def safety(self, repo_id, *, reconcile=True):
        if not self.configured:
            return {'blocked':False, 'pendingCount':0}
        with budget():
            return self.worker().safety(repo_id, reconcile=reconcile)

    def owner_inventory(self, remote=False):
        with budget():
            data = self.inventory(remote=remote)
            for row in data.get('repositories', []):
                row['pullSafety'] = self.safety(row['repoId'])
            return data

    def check_repository(self, repo_id):
        if self.read_only or not REPO.fullmatch(repo_id):
            raise PullDenied('REPOSITORY_NOT_ALLOWED')
        worker = self.worker()
        lock = worker._repo_lock(repo_id)
        if not lock.acquire(blocking=False):
            raise PullDenied('REPOSITORY_OPERATION_PENDING')
        try:
            with budget():
                safety = self.safety(repo_id)
                if safety['blocked']:
                    data = self.inventory(remote=True, repo=repo_id)
                else:
                    try:
                        with self._guard.scope():
                            data = self.backend.check_repository(repo_id)
                    except GuardError as exc:
                        raise PullDenied(str(exc)) from None
                items = data.get('repositories', [])
                if len(items) != 1 or items[0].get('repoId') != repo_id:
                    raise PullDenied('DOCKER_REPOSITORY_NOT_REGISTERED')
                items[0]['pullSafety'] = safety
                return data
        finally:
            lock.release()

    def execute(self, body, repo_id):
        if self.read_only:
            raise PullDenied('MIRROR_READ_ONLY')
        with budget():
            return self.worker().run(body, repo_id=repo_id, allowed_repos=frozenset({repo_id}))

    def preview(self, repo_id):
        if self.read_only or type(repo_id) is not str or not REPO.fullmatch(repo_id):
            raise PullDenied('REPOSITORY_NOT_ALLOWED')
        with budget():
            return self._preview(repo_id)

    def preview_observation(self, repo_id):
        if self.read_only or type(repo_id) is not str or not REPO.fullmatch(repo_id):
            raise PullDenied('REPOSITORY_NOT_ALLOWED')
        with budget():
            return self._preview(repo_id, include_sources=True)

    def _preview(self, repo_id, *, include_sources=False):
        # Include receipt-store initialization in the server Preview duration.
        started = time.perf_counter()
        self.worker()
        data = self.backend.inventory(remote=True, repo=repo_id)
        items = data.get('repositories')
        if type(items) is not list or len(items) != 1 or items[0].get('repoId') != repo_id:
            raise PullDenied('DOCKER_REPOSITORY_NOT_REGISTERED')
        row = items[0]
        primary = row.get('docker', {}).get('primary') or {}
        windows = row.get('windows') or {}
        github = row.get('github') or {}
        reasons = ['PREVIOUS_PULL_UNRESOLVED'] if self.safety(repo_id, reconcile=False)['blocked'] else []
        if (primary.get('kind') != 'primary' or primary.get('branch') != 'main' or
                primary.get('targetEligible') is not True or
                primary.get('status') != 'registered' or primary.get('clean') is not True):
            reasons.append('DOCKER_MAIN_NOT_VERIFIED')
        origin = primary.get('origin')
        if not isinstance(origin, str) or not origin:
            reasons.append('DOCKER_ORIGIN_UNVERIFIED')
        if windows.get('error') or windows.get('exists') is not True:
            reasons.append(windows.get('error') or 'WINDOWS_CLONE_NOT_VERIFIED')
        if windows.get('branch') != 'main':
            reasons.append('WINDOWS_NOT_ON_MAIN')
        if windows.get('clean') is not True:
            reasons.append('WINDOWS_NOT_CLEAN')
        if windows.get('originValid') is not True or windows.get('origin') != origin:
            reasons.append('WINDOWS_ORIGIN_MISMATCH')
        head = windows.get('head')
        if type(head) is not str or not SHA.fullmatch(head):
            reasons.append('WINDOWS_HEAD_UNVERIFIED')
        target = github.get('head')
        if github.get('error') or type(target) is not str or not SHA.fullmatch(target):
            reasons.append('GITHUB_MAIN_UNVERIFIED')
        if primary.get('head') != target:
            reasons.append('DOCKER_MAIN_NOT_AT_GITHUB_MAIN')
        if head == target and target is not None:
            reasons.append('WINDOWS_ALREADY_CURRENT')
        relation = windows.get('relation', 'unknown')
        if relation in ('ahead', 'diverged'):
            reasons.append('WINDOWS_NOT_BEHIND_GITHUB')
        if relation == 'equal' and head != target:
            reasons.append('WINDOWS_RELATION_INCONSISTENT')
        result = {
            'repoId': repo_id, 'available': not reasons,
            'windowsHead': head, 'targetSha': target,
            'dockerHead': primary.get('head'), 'origin': origin,
            'relation': relation, 'reasons': list(dict.fromkeys(reasons)),
            'requiredCI': github.get('requiredCI', 'NOT_VERIFIED'),
            'fastForward': 'TO_BE_VERIFIED_AFTER_FETCH' if relation == 'unknown' else relation,
            'previewId': None, 'requestId': None, 'expiresInSeconds': 0,
            'timingsMs': {'previewTotal': round((time.perf_counter() - started) * 1000)},
        }
        # The underlying inventory contains independent timings for the same
        # single repo. They are diagnostic only, never part of authorization.
        observed = row.get('readTimingsMs') or {}
        for source, key in (('dockerInventory', 'previewDocker'),
                            ('windowsGit', 'previewWindows'),
                            ('githubGateway', 'previewGateway'),
                            ('gitAncestry', 'previewAncestry')):
            value = observed.get(source)
            if type(value) in (int, float) and 0 <= value <= 600000:
                result['timingsMs'][key] = round(value)
        return (result, row, data.get('checkedAt')) if include_sources else result

    def close(self):
        with self.lock:
            self.closed = True
            if self._guard is not None:
                self._guard.close()
