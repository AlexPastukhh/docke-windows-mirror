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
from .operations import ObservationError, SHA, checked_at, git, github_main, windows_status, docker_inventory

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")


class PullDenied(ValueError):
    pass


class PullWorker:
    """One serial lock/SQLite store per Windows Mirror Service process."""
    def __init__(self, mirror, db_path):
        self.mirror = mirror
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._initialize()

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
        if state in ("DISPATCHED", "UNCERTAIN"):
            # No new Git effect: reconcile an ambiguous result against its
            # original target. On mismatch remain uncertain (never retry).
            observed = windows_status(self.mirror.windows_root, original["repoId"], original["origin"])
            if (observed["error"] is None and observed["head"] == original["targetSha"] and
                    observed["branch"] == "main" and observed["clean"] is True and
                    observed["originValid"]):
                receipt = {"requestId": request_id, "state": "EXECUTED",
                           "head": original["targetSha"], "checkedAt": checked_at()}
                with self._db() as db:
                    db.execute("UPDATE pull_receipts SET state=?,result=? WHERE request_id=? AND state IN ('DISPATCHED','UNCERTAIN')",
                               ("EXECUTED", json.dumps(receipt, sort_keys=True), request_id))
                return receipt
        return json.loads(saved) if saved else {"requestId": request_id, "state": state,
                                                "head": None}

    def run(self, body, *, repo_id, allowed_repos):
        value = self.payload(body)
        if value["repoId"] != repo_id or repo_id not in allowed_repos:
            raise PullDenied("REPOSITORY_NOT_ALLOWED")
        frozen = json.dumps(value, sort_keys=True, separators=(",", ":"))
        with self.lock:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                # Reservation is durable before touching Git. Any repeated request
                # becomes status-only, including after crash or timeout.
                row = db.execute("SELECT payload FROM pull_receipts WHERE request_id=?",
                                 (value["requestId"],)).fetchone()
                if row:
                    if row[0] != frozen:
                        raise PullDenied("REQUEST_ID_CONFLICT")
                    return self.status(value["requestId"], repo_id=repo_id)
                # A separate Mirror process using the same receipt DB must not
                # start another Pull on this clone while the first is dispatched.
                pending = db.execute("SELECT payload FROM pull_receipts WHERE state='DISPATCHED'").fetchall()
                if any(json.loads(item[0])["repoId"] == repo_id for item in pending):
                    raise PullDenied("REPOSITORY_OPERATION_PENDING")
                db.execute("INSERT INTO pull_receipts VALUES(?,?,?,?)",
                           (value["requestId"], frozen, "DISPATCHED", None))
            try:
                receipt = self._execute(value)
            except (PullDenied, ObservationError) as exc:
                # After reservation, any error might have interrupted the effect.
                # Never retry it automatically. Readback/reconcile may resolve it.
                receipt = {"requestId": value["requestId"], "state": "UNCERTAIN",
                           "head": None, "error": str(exc), "checkedAt": checked_at()}
            except Exception:
                receipt = {"requestId": value["requestId"], "state": "UNCERTAIN",
                           "head": None, "error": "PULL_EXECUTION_UNCONFIRMED", "checkedAt": checked_at()}
            with self._db() as db:
                db.execute("UPDATE pull_receipts SET state=?,result=? WHERE request_id=?",
                           (receipt["state"], json.dumps(receipt, sort_keys=True), value["requestId"]))
            return receipt

    def _execute(self, p):
        repo = p["repoId"]
        origin = p["origin"]
        # No raw path or ref from request; enforce the configured Windows root.
        root = self.mirror.windows_root / repo
        registered = docker_inventory().get(repo)
        if not isinstance(registered, list):
            raise PullDenied("DOCKER_REPOSITORY_NOT_REGISTERED")
        primary = next((row for row in registered if row.get("kind") == "primary"), None)
        if primary is None or primary.get("origin") != origin:
            raise PullDenied("DOCKER_ORIGIN_MISMATCH")
        if (primary.get("branch") != "main" or primary.get("clean") is not True or
                primary.get("targetEligible") is not True or
                primary.get("head") != p["targetSha"]):
            raise PullDenied("DOCKER_MAIN_NOT_AT_TARGET")
        current = windows_status(self.mirror.windows_root, repo, origin)
        if (current["error"] or current["branch"] != "main" or
                current["clean"] is not True or current["head"] != p["expectedHead"] or
                not current["originValid"]):
            raise PullDenied("WINDOWS_PRECONDITIONS_CHANGED")
        remote = github_main(repo, origin)
        if remote["error"] or remote["head"] != p["targetSha"]:
            raise PullDenied("GITHUB_MAIN_CHANGED")
        # A GitHub credential, if needed, must already be configured for this
        # Windows clone. Never accept one in the request or read Gateway secrets.
        fetched = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                "-c", "protocol.file.allow=never", "-C", str(root), "fetch", "--no-tags",
                "--no-recurse-submodules", "origin", "main"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=180, env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never"))
        if fetched.returncode:
            raise ObservationError("WINDOWS_FETCH_FAILED")
        fetched_head = git(root, "rev-parse", "--verify", "FETCH_HEAD")
        if fetched_head != p["targetSha"]:
            raise PullDenied("FETCHED_TARGET_CHANGED")
        again = windows_status(self.mirror.windows_root, repo, origin)
        if (again["error"] or again["branch"] != "main" or
                again["head"] != p["expectedHead"] or
                again["clean"] is not True or not again["originValid"]):
            raise PullDenied("WINDOWS_CHANGED_AFTER_FETCH")
        # A pinned SHA is required, never mutable FETCH_HEAD as the merge target.
        ancestry = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor",
                p["expectedHead"], p["targetSha"]], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if ancestry.returncode:
            raise PullDenied("NOT_FAST_FORWARD")
        update = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                "-c", "protocol.file.allow=never", "-C", str(root), "merge", "--ff-only",
                p["targetSha"]], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=180)
        if update.returncode:
            raise ObservationError("WINDOWS_FF_FAILED")
        readback = windows_status(self.mirror.windows_root, repo, origin)
        if (readback["error"] or readback["head"] != p["targetSha"] or
                readback["branch"] != "main" or readback["clean"] is not True or
                not readback["originValid"]):
            raise ObservationError("WINDOWS_READBACK_UNCONFIRMED")
        return {"requestId": p["requestId"], "state": "EXECUTED",
                "head": p["targetSha"], "checkedAt": checked_at()}
