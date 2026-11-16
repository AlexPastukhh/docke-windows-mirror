"""Restricted Windows fast-forward worker with durable no-blind-retry receipts.

Mutations originate either from a separate authenticated Gateway listener
or from an explicit owner-local browser preview + confirmation. Both call the
same restricted executor. A private Git fetch uses the owner's existing Windows
Git authorization; Gateway GitHub credentials are never copied here.
"""
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
from .operations import ObservationError, SHA, checked_at, git, github_main, windows_status, docker_inventory
from .git_errors import fetch_failure

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")


class PullDenied(ValueError):
    pass


class PullReadbackUnconfirmed(ObservationError):
    """A successful fast-forward was not confirmed by bounded Git readbacks."""
    def __init__(self, issues):
        super().__init__('WINDOWS_READBACK_UNCONFIRMED')
        self.issues = issues


class PullFetchFailed(ObservationError):
    def __init__(self, result):
        super().__init__('WINDOWS_FETCH_FAILED')
        self.diagnostic = fetch_failure(result.stderr, result.returncode)


class PullWorker:
    """One serial lock/SQLite store per Windows Mirror Service process."""
    def __init__(self, mirror, db_path):
        self.mirror = mirror
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.repo_locks = {}
        self._initialize()

    def _repo_lock(self, repo):
        # Clone-specific serialization: independent Windows primaries can Pull
        # simultaneously, while the same primary is never executed twice.
        with self.lock:
            return self.repo_locks.setdefault(repo, threading.RLock())

    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _initialize(self):
        with self._db() as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE IF NOT EXISTS pull_receipts (request_id TEXT PRIMARY KEY, "
                       "payload TEXT NOT NULL, state TEXT NOT NULL, result TEXT)")

    @staticmethod
    def payload(value):
        required = {"requestId", "repoId", "expectedHead", "targetSha", "origin"}
        if not isinstance(value, dict) or set(value) != required:
            raise PullDenied("PULL_PAYLOAD_INVALID")
        if (not UUID.fullmatch(str(value["requestId"])) or
                not all(isinstance(value[k], str) and SHA.fullmatch(value[k])
                        for k in ("expectedHead", "targetSha")) or
                not isinstance(value["repoId"], str) or
                not isinstance(value["origin"], str)):
            raise PullDenied("PULL_PAYLOAD_INVALID")
        return value

    def status(self, request_id, repo_id=None):
        if not UUID.fullmatch(request_id):
            raise PullDenied("REQUEST_ID_INVALID")
        with self._db() as db:
            row = db.execute("SELECT payload,state,result FROM pull_receipts WHERE request_id=?",
                             (request_id,)).fetchone()
        if not row:
            return {"requestId": request_id, "state": "NOT_FOUND", "head": None}
        payload, state, saved = row
        original = json.loads(payload)
        if repo_id is not None and original["repoId"] != repo_id:
            raise PullDenied("RECEIPT_REPOSITORY_MISMATCH")
        receipt = json.loads(saved) if saved else {'requestId': request_id, 'state': state, 'head': None}
        if state not in ('UNCERTAIN', 'FAILED'):
            return receipt
        lock = self._repo_lock(original['repoId'])
        if not lock.acquire(blocking=False):
            return {**receipt, 'workerActive': True}
        try:
            try:
                observed = windows_status(self.mirror.windows_root, original['repoId'], original['origin'])
                observation = {key: observed.get(key) for key in ('head', 'branch', 'clean', 'originValid', 'error')}
            except (ObservationError, OSError):
                observation = {'head': None, 'branch': None, 'clean': None,
                               'originValid': False, 'error': 'WINDOWS_READ_UNAVAILABLE'}
            observation['checkedAt'] = checked_at()
            result = {**receipt, 'observation': observation, 'expectedHead': original['expectedHead'],
                      'targetSha': original['targetSha']}
            safe = (not observation['error'] and observation['branch'] == 'main' and
                    observation['clean'] is True and observation['originValid'] is True)
            # The historical fixed FETCH_FAILED code is emitted before merge
            # in all retained workers. It is not evidence about raw fetch cause.
            before_merge = (receipt.get('error') == 'WINDOWS_FETCH_FAILED' or
                receipt.get('workerFinished') is True and receipt.get('mergeStarted') is False and
                receipt.get('knownFailure') is True)
            if state == 'UNCERTAIN' and before_merge and safe and observation['head'] in (
                    original['expectedHead'], original['targetSha']):
                result.update(state='FAILED', head=observation['head'], phase=receipt.get('phase', 'FETCH'),
                              evidence='PRE_MERGE_FAILURE_AND_READBACK',
                              checkoutChanged=observation['head'] != original['expectedHead'])
                if receipt.get('error') == 'WINDOWS_FETCH_FAILED':
                    result.setdefault('diagnostic', {'cause': 'FETCH_REASON_NOT_RECORDED', 'exitCode': None})
            elif state == 'UNCERTAIN' and not before_merge and safe and observation['head'] == original['targetSha']:
                result.update(state='EXECUTED', head=observation['head'], evidence='TARGET_READBACK')
            if result['state'] != state:
                result['previousState'] = state
                result['checkedAt'] = observation['checkedAt']
                with self._db() as db:
                    changed = db.execute('UPDATE pull_receipts SET state=?,result=? WHERE request_id=? AND state=? AND result IS ?',
                        (result['state'], json.dumps(result, sort_keys=True), request_id, state, saved)).rowcount
                if not changed:
                    return self.status(request_id, repo_id=repo_id)
            return result
        finally:
            lock.release()

    def _pending(self, repo_id):
        selected = []
        with self._db() as db:
            for index, row in enumerate(db.execute("SELECT request_id,payload FROM pull_receipts WHERE state IN ('DISPATCHED','UNCERTAIN')")):
                if index >= 4096:
                    raise PullDenied('PULL_HISTORY_LIMIT_REACHED')
                if json.loads(row[1])['repoId'] == repo_id:
                    selected.append(row[0])
        return selected

    def safety(self, repo_id):
        # Internal repo-scoped recovery exposes no old-session receipt/UUID.
        pending = self._pending(repo_id)
        for ident in pending[:64]:
            self.status(ident, repo_id=repo_id)
        remaining = self._pending(repo_id)
        return {'blocked': bool(remaining), 'pendingCount': len(remaining), 'checkedAt': checked_at()}

    def _checkpoint(self, value, phase, merge_started):
        progress = {'requestId': value['requestId'], 'state': 'DISPATCHED', 'head': None,
                    'phase': phase, 'mergeStarted': merge_started}
        with self._db() as db:
            changed = db.execute("UPDATE pull_receipts SET result=? WHERE request_id=? AND state='DISPATCHED'",
                (json.dumps(progress), value['requestId'])).rowcount
        if changed != 1:
            raise PullDenied('PULL_RECEIPT_RESERVATION_REQUIRED')

    def run(self, body, *, repo_id, allowed_repos):
        value = self.payload(body)
        if value["repoId"] != repo_id or repo_id not in allowed_repos:
            raise PullDenied("REPOSITORY_NOT_ALLOWED")
        frozen = json.dumps(value, sort_keys=True, separators=(",", ":"))
        with self._repo_lock(repo_id):
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                # Reservation is durable before touching Git. Any repeated request
                # becomes status-only, including after crash or timeout.
                row = db.execute("SELECT payload FROM pull_receipts WHERE request_id=?",
                                 (value["requestId"],)).fetchone()
                if row:
                    if row[0] != frozen:
                        raise PullDenied("REQUEST_ID_CONFLICT")
                else:
                    # A separate Mirror process using the same receipt DB must not
                    # start another Pull on this clone while the first is dispatched.
                    pending = db.execute("SELECT payload FROM pull_receipts WHERE state IN ('DISPATCHED','UNCERTAIN')").fetchall()
                    if any(json.loads(item[0])["repoId"] == repo_id for item in pending):
                        raise PullDenied("REPOSITORY_OPERATION_PENDING")
                    progress = {'requestId': value['requestId'], 'state': 'DISPATCHED', 'head': None,
                                'phase': 'PRECHECK', 'mergeStarted': False}
                    db.execute("INSERT INTO pull_receipts VALUES(?,?,?,?)",
                               (value["requestId"], frozen, "DISPATCHED", json.dumps(progress)))
            if row:
                return self.status(value["requestId"], repo_id=repo_id)
            timings = {}
            started = time.perf_counter()
            try:
                receipt = self._execute(value, timings=timings)
            except (PullDenied, ObservationError) as exc:
                # After reservation, any error might have interrupted the effect.
                # Never retry it automatically. Readback/reconcile may resolve it.
                receipt = {"requestId": value["requestId"], "state": "UNCERTAIN",
                           "head": None, "error": str(exc), "checkedAt": checked_at()}
                with self._db() as db:
                    progress = json.loads(db.execute('SELECT result FROM pull_receipts WHERE request_id=?',
                                                     (value['requestId'],)).fetchone()[0])
                receipt.update(phase=progress['phase'], mergeStarted=progress['mergeStarted'],
                               workerFinished=True, knownFailure=not isinstance(exc, PullReadbackUnconfirmed))
                if isinstance(exc, PullFetchFailed):
                    receipt['diagnostic'] = exc.diagnostic
                if isinstance(exc, PullReadbackUnconfirmed):
                    receipt['readbackIssues'] = exc.issues
            except Exception:
                receipt = {"requestId": value["requestId"], "state": "UNCERTAIN",
                           "head": None, "error": "PULL_EXECUTION_UNCONFIRMED", "checkedAt": checked_at()}
            if timings:
                # Only bounded durations, no Git paths/credentials/CLI output.
                timings['executeTotal'] = round((time.perf_counter() - started) * 1000)
                receipt['timingsMs'] = timings
            with self._db() as db:
                db.execute("UPDATE pull_receipts SET state=?,result=? WHERE request_id=?",
                           (receipt["state"], json.dumps(receipt, sort_keys=True), value["requestId"]))
            # Readback can confirm a pre-merge failure, but never dispatch Git.
            return self.status(value['requestId'], repo_id=repo_id) if receipt['state'] == 'UNCERTAIN' else receipt

    def _confirmed_readback(self, repo, origin, target):
        """Retry only Git observation after a successful ff-only; never Git write.

        A Windows checkout may transiently expose a status/index mismatch while
        file metadata settles. The resulting reason codes are safe to display;
        changed file names or local paths are not copied into the receipt.
        """
        issues = []
        for delay in (0, 0.2, 0.5):
            if delay:
                time.sleep(delay)
            observed = windows_status(self.mirror.windows_root, repo, origin)
            issues = []
            if observed.get('error'):
                issues.append('GIT_READ_FAILED')
            if observed.get('head') != target:
                issues.append('HEAD_MISMATCH')
            if observed.get('branch') != 'main':
                issues.append('BRANCH_MISMATCH')
            if observed.get('clean') is not True:
                issues.append('WORKTREE_NOT_CLEAN')
            if observed.get('originValid') is not True:
                issues.append('ORIGIN_MISMATCH')
            if not issues:
                return
        raise PullReadbackUnconfirmed(issues)

    def _execute(self, p, *, timings=None):
        if timings is None:
            timings = {}

        def measured(stage, operation):
            started = time.perf_counter()
            try:
                return operation()
            finally:
                timings[stage] = round((time.perf_counter() - started) * 1000)

        repo = p["repoId"]
        origin = p["origin"]
        # No raw path or ref from request; enforce the configured Windows root.
        root = self.mirror.windows_root / repo
        registered = measured('dockerInventory', lambda: docker_inventory(repo).get(repo))
        if not isinstance(registered, list):
            raise PullDenied("DOCKER_REPOSITORY_NOT_REGISTERED")
        primary = next((row for row in registered if row.get("kind") == "primary"), None)
        if primary is None or primary.get("origin") != origin:
            raise PullDenied("DOCKER_ORIGIN_MISMATCH")
        if (primary.get("branch") != "main" or primary.get("clean") is not True or
                primary.get("targetEligible") is not True or
                primary.get("head") != p["targetSha"]):
            raise PullDenied("DOCKER_MAIN_NOT_AT_TARGET")
        current = measured('windowsPreflight',
            lambda: windows_status(self.mirror.windows_root, repo, origin))
        if (current["error"] or current["branch"] != "main" or
                current["clean"] is not True or current["head"] != p["expectedHead"] or
                not current["originValid"]):
            raise PullDenied("WINDOWS_PRECONDITIONS_CHANGED")
        # Pull authorization needs a fresh exact main SHA, not PR/CI history.
        remote = measured('gatewayMain', lambda: github_main(repo, origin, details=False))
        if remote["error"] or remote["head"] != p["targetSha"]:
            raise PullDenied("GITHUB_MAIN_CHANGED")
        # A GitHub credential, if needed, must already be configured for this
        # Windows clone. Never accept one in the request or read Gateway secrets.
        self._checkpoint(p, 'FETCH', False)
        fetched = measured('windowsFetch', lambda: subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                 "-c", "protocol.file.allow=never", "-C", str(root), "fetch", "--no-tags",
                 "--no-recurse-submodules", "origin", "main"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=180, env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")))
        if fetched.returncode:
            raise PullFetchFailed(fetched)
        fetched_head = measured('fetchHeadRead',
            lambda: git(root, "rev-parse", "--verify", "FETCH_HEAD"))
        if fetched_head != p["targetSha"]:
            raise PullDenied("FETCHED_TARGET_CHANGED")
        again = measured('windowsAfterFetch',
            lambda: windows_status(self.mirror.windows_root, repo, origin))
        if (again["error"] or again["branch"] != "main" or
                again["head"] != p["expectedHead"] or
                again["clean"] is not True or not again["originValid"]):
            raise PullDenied("WINDOWS_CHANGED_AFTER_FETCH")
        # A pinned SHA is required, never mutable FETCH_HEAD as the merge target.
        ancestry = measured('gitAncestry', lambda: subprocess.run(
                ["git", "-C", str(root), "merge-base", "--is-ancestor",
                 p["expectedHead"], p["targetSha"]], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10))
        if ancestry.returncode:
            raise PullDenied("NOT_FAST_FORWARD")
        self._checkpoint(p, 'MERGE', True)
        update = measured('fastForward', lambda: subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                 "-c", "protocol.file.allow=never", "-C", str(root), "merge", "--ff-only",
                 p["targetSha"]], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=180))
        if update.returncode:
            raise ObservationError("WINDOWS_FF_FAILED")
        self._checkpoint(p, 'READBACK', True)
        measured('windowsReadback',
            lambda: self._confirmed_readback(repo, origin, p["targetSha"]))
        return {"requestId": p["requestId"], "state": "EXECUTED",
                "head": p["targetSha"], "checkedAt": checked_at()}
