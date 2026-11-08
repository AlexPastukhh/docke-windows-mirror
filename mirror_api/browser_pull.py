"""Owner-local Browser Pull binding for the existing guarded Windows PullWorker.

No arbitrary Git commands, paths, branches or URLs are accepted from HTTP.
The owner authorizes one exact preview on the local Windows browser; remote
AI effects remain isolated behind the separately configured Gateway listener.
"""
import os
import sqlite3
from pathlib import Path
import threading
import time
import uuid

from .operations import REPO, SHA, ObservationError
from .pull import PullDenied, PullWorker, UUID


class BrowserPull:
    PREVIEW_SECONDS = 120
    STATUS_SECONDS = 3600
    MAX_PENDING = 64

    def __init__(self, mirror, receipt_db=None, *, clock=None):
        self.mirror = mirror
        self.receipt_db = Path(receipt_db) if receipt_db is not None else None
        self.clock = clock or time.monotonic
        self.lock = threading.Lock()
        self.previews = {}
        self.requests = {}
        self.worker = None

    @property
    def configured(self):
        # The receipt store must be durable and user-local, never in Git source.
        return self.receipt_db is not None or bool(os.environ.get('LOCALAPPDATA'))

    def _worker(self):
        with self.lock:
            if self.worker is None:
                target = self.receipt_db
                if target is None:
                    root = os.environ.get('LOCALAPPDATA', '')
                    if not root or not os.path.isabs(root):
                        raise PullDenied('PULL_RECEIPT_STORAGE_UNAVAILABLE')
                    target = Path(root) / 'FortisMirror' / 'pull-receipts.sqlite3'
                try:
                    self.worker = PullWorker(self.mirror, target)
                except (OSError, ValueError, sqlite3.Error):
                    raise PullDenied('PULL_RECEIPT_STORAGE_UNAVAILABLE') from None
            return self.worker

    def _purge(self, now):
        for key, item in list(self.previews.items()):
            if item['expires'] < now:
                del self.previews[key]
        for key, item in list(self.requests.items()):
            if item['time'] + self.STATUS_SECONDS < now:
                del self.requests[key]

    def preview(self, repo_id, session):
        if type(repo_id) is not str or not REPO.fullmatch(repo_id) or not session:
            raise PullDenied('REPOSITORY_NOT_ALLOWED')
        # Ensure durable receipt storage is available before offering approval.
        self._worker()
        data = self.mirror.inventory(remote=True, repo=repo_id)
        items = data.get('repositories')
        if type(items) is not list or len(items) != 1 or items[0].get('repoId') != repo_id:
            raise PullDenied('DOCKER_REPOSITORY_NOT_REGISTERED')
        row = items[0]
        primary = row.get('docker', {}).get('primary') or {}
        windows = row.get('windows') or {}
        github = row.get('github') or {}
        reasons = []
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
        }
        if reasons:
            return result
        item = {
            'session': session, 'repoId': repo_id, 'windowsHead': head,
            'targetSha': target, 'origin': origin, 'expires': self.clock() + self.PREVIEW_SECONDS,
        }
        with self.lock:
            now = self.clock()
            self._purge(now)
            if len(self.previews) >= self.MAX_PENDING:
                raise PullDenied('PULL_PREVIEW_LIMIT_REACHED')
            preview_id = str(uuid.uuid4())
            request_id = str(uuid.uuid4())
            item['requestId'] = request_id
            self.previews[preview_id] = item
            self.requests[request_id] = {'session': session, 'repoId': repo_id,
                                         'time': now, 'state': 'PREVIEWED'}
        result.update(previewId=preview_id, requestId=request_id,
                      expiresInSeconds=self.PREVIEW_SECONDS)
        return result

    def execute(self, repo_id, session, preview_id, confirm):
        if confirm != 'PULL_WINDOWS_MAIN' or not isinstance(preview_id, str) or not UUID.fullmatch(preview_id):
            raise PullDenied('PULL_CONFIRMATION_REQUIRED')
        with self.lock:
            now = self.clock()
            self._purge(now)
            item = self.previews.get(preview_id)
            if item is None:
                raise PullDenied('PULL_PREVIEW_EXPIRED_OR_USED')
            if item['session'] != session or item['repoId'] != repo_id:
                raise PullDenied('PULL_PREVIEW_BINDING_MISMATCH')
            # One-use approval: consume before dispatch. No browser retry on
            # network timeout, and another session cannot reuse the preview.
            del self.previews[preview_id]
            self.requests[item['requestId']]['state'] = 'DISPATCHING'
        body = {'requestId': item['requestId'], 'repoId': repo_id,
                'expectedHead': item['windowsHead'],
                'targetSha': item['targetSha'], 'origin': item['origin']}
        return self._worker().run(body, repo_id=repo_id, allowed_repos=frozenset({repo_id}))

    def status(self, repo_id, session, request_id):
        if type(request_id) is not str or not UUID.fullmatch(request_id):
            raise PullDenied('REQUEST_ID_INVALID')
        with self.lock:
            self._purge(self.clock())
            item = self.requests.get(request_id)
            if item is None or item['repoId'] != repo_id or item['session'] != session:
                raise PullDenied('PULL_RECEIPT_NOT_OWNED')
            if item['state'] == 'PREVIEWED':
                return {'requestId': request_id, 'state': 'PREVIEWED', 'head': None}
        result = self._worker().status(request_id, repo_id=repo_id)
        if result['state'] == 'NOT_FOUND':
            return {'requestId': request_id, 'state': 'DISPATCHING', 'head': None}
        return result
