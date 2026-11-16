"""Owner-local Browser Pull binding for the existing guarded Windows PullWorker.

No arbitrary Git commands, paths, branches or URLs are accepted from HTTP.
The owner authorizes one exact preview on the local Windows browser; remote
AI effects remain isolated behind the separately configured Gateway listener.
"""
import threading
import time
import uuid

from .operations import REPO, SHA, ObservationError
from .pull import PullDenied, UUID


class BrowserPull:
    PREVIEW_SECONDS = 120
    STATUS_SECONDS = 3600
    MAX_PENDING = 64
    MAX_REQUESTS = 256

    def __init__(self, mirror, receipt_db=None, *, clock=None, core=None):
        from .core import MirrorCore
        self.core = core if core is not None else MirrorCore(mirror, receipt_db)
        self.mirror = self.core.backend
        self.receipt_db = self.core.receipt_db
        self.clock = clock or time.monotonic
        self.lock = threading.Lock()
        self.previews = {}
        self.requests = {}

    @property
    def configured(self):
        return self.core.configured

    @property
    def worker(self):
        return self.core._worker

    @worker.setter
    def worker(self, value):
        self.core._worker = value

    def _worker(self):
        return self.core.worker()

    def _purge(self, now):
        for key, item in list(self.previews.items()):
            if item['expires'] < now:
                del self.previews[key]
        for key, item in list(self.requests.items()):
            if item['time'] + self.STATUS_SECONDS < now:
                del self.requests[key]

    def safety(self, repo_id):
        return self.core.safety(repo_id)

    def preview(self, repo_id, session):
        if type(repo_id) is not str or not REPO.fullmatch(repo_id) or not session:
            raise PullDenied('REPOSITORY_NOT_ALLOWED')
        with self.lock:
            self._purge(self.clock())
            if len(self.requests) >= self.MAX_REQUESTS:
                raise PullDenied('PULL_RECEIPT_LOOKUP_LIMIT_REACHED')
        result = self.core.preview(repo_id)
        if not result['available']:
            return result
        head, target, origin = result['windowsHead'], result['targetSha'], result['origin']
        item = {
            'session': session, 'repoId': repo_id, 'windowsHead': head,
            'targetSha': target, 'origin': origin, 'expires': self.clock() + self.PREVIEW_SECONDS,
        }
        with self.lock:
            now = self.clock()
            self._purge(now)
            if len(self.previews) >= self.MAX_PENDING:
                raise PullDenied('PULL_PREVIEW_LIMIT_REACHED')
            if len(self.requests) >= self.MAX_REQUESTS:
                raise PullDenied('PULL_RECEIPT_LOOKUP_LIMIT_REACHED')
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
        return self.core.execute(body, repo_id)

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
