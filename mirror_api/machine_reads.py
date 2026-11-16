"""Principal-bound READ metadata in the existing Core worker's SQLite ledger.

No effects or machine binding creation exist here. P4 alone may create a binding
atomically with its signed dispatch reservation. Lookup never adopts old receipts.
"""
from datetime import datetime, timezone
import json
import re
import time
import uuid

from .read_v2 import ReadError, digest, require, source
from .operations import REPO, SHA

ACTOR = re.compile(r'[A-Za-z0-9_.:-]{1,120}\Z')
UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')
GENERATION = re.compile(r'[0-9a-f]{64}\Z')


def principal(value):
    require(type(value) is str and ACTOR.fullmatch(value), 'PERMISSION_DENIED')
    return value


def identifier(value):
    require(type(value) is str and UUID4.fullmatch(value))
    return value


def timestamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec='seconds')


def blocker(reason):
    return {'PREVIOUS_PULL_UNRESOLVED': 'OUTCOME_UNCONFIRMED',
            'DOCKER_MAIN_NOT_VERIFIED': 'DOCKER_MAIN_MISMATCH',
            'DOCKER_MAIN_NOT_AT_GITHUB_MAIN': 'DOCKER_MAIN_MISMATCH',
            'DOCKER_ORIGIN_UNVERIFIED': 'ORIGIN_MISMATCH',
            'WINDOWS_ORIGIN_MISMATCH': 'ORIGIN_MISMATCH',
            'WINDOWS_NOT_ON_MAIN': 'WINDOWS_NOT_ON_MAIN',
            'WINDOWS_NOT_CLEAN': 'WINDOWS_NOT_CLEAN',
            'WINDOWS_ALREADY_CURRENT': 'WINDOWS_ALREADY_CURRENT',
            'WINDOWS_NOT_BEHIND_GITHUB': 'NOT_FAST_FORWARD',
            'GITHUB_MAIN_UNVERIFIED': 'GITHUB_MAIN_UNVERIFIED',
            'MIRROR_WRITER_BUSY': 'WRITER_BUSY'}.get(reason, 'SOURCE_UNAVAILABLE')


class MachineReads:
    TTL = 120
    MAX_PREVIEWS = 4096
    MAX_ACTIVE_PER_ACTOR = 64

    def __init__(self, core, generation, *, clock=None):
        require(type(generation) is str and GENERATION.fullmatch(generation))
        self.core, self.generation, self.clock = core, generation, clock or time.time
        # Exactly the shared Core worker/store. Initializing its ledger binds the
        # store but never claims a writer, invokes status/recovery or starts Git.
        self.worker = core.worker()
        with self.worker._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS machine_previews (
                request_id TEXT PRIMARY KEY, actor TEXT NOT NULL, repo_id TEXT NOT NULL,
                gateway_generation TEXT NOT NULL, config_generation TEXT NOT NULL,
                preview_id TEXT UNIQUE, expires_at INTEGER NOT NULL, origin TEXT, data TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS machine_bindings (
                request_id TEXT PRIMARY KEY, actor TEXT NOT NULL, repo_id TEXT NOT NULL,
                identity TEXT NOT NULL)''')

    def _existing(self, db, request_id, actor, repo, gateway_generation):
        row = db.execute('SELECT actor,repo_id,gateway_generation,config_generation,data '
                         'FROM machine_previews WHERE request_id=?', (request_id,)).fetchone()
        if row is None:
            return None
        require(row[0] == actor and row[1] == repo, 'REQUEST_ID_CONFLICT')
        require(row[2] == gateway_generation and row[3] == self._generation(gateway_generation),
                'PREVIEW_STALE')
        return json.loads(row[4])

    def _generation(self, gateway_generation):
        return digest({'windows': self.generation, 'gateway': gateway_generation})

    def _capacity(self, db, actor, request_id, now):
        require(not db.execute('SELECT 1 FROM pull_receipts WHERE request_id=?', (request_id,)).fetchone()
                and not db.execute('SELECT 1 FROM machine_bindings WHERE request_id=?', (request_id,)).fetchone(),
                'REQUEST_ID_CONFLICT')
        require(db.execute('SELECT COUNT(*) FROM machine_previews').fetchone()[0] < self.MAX_PREVIEWS,
                'LIMIT_EXCEEDED')
        require(db.execute('SELECT COUNT(*) FROM machine_previews WHERE actor=? AND expires_at>?',
                           (actor, now)).fetchone()[0] < self.MAX_ACTIVE_PER_ACTOR, 'LIMIT_EXCEEDED')

    def preview(self, repo, actor, request_id, gateway_generation):
        principal(actor); identifier(request_id)
        require(type(gateway_generation) is str and GENERATION.fullmatch(gateway_generation))
        with self.worker._db() as db:
            existing = self._existing(db, request_id, actor, repo, gateway_generation)
            if existing is not None:
                return existing
            self._capacity(db, actor, request_id, int(self.clock()))
        # No SQLite transaction/lock spans the bounded observation.
        facts, row, observed = self.core.preview_observation(repo)
        require(type(row) is dict and row.get('repoId') == repo)
        docker, windows, github = row['docker']['primary'], row['windows'], row['github']
        require(all(value.get('repoId') == repo for value in (docker, windows, github)))
        evidence = {'docker': source(docker, kind='docker', timestamp=observed),
                    'windows': source(windows, kind='windows', timestamp=observed),
                    'github': source(github, kind='github', timestamp=observed)}
        reasons = list(dict.fromkeys(blocker(item) for item in facts['reasons']))
        slug = github.get('githubRepository')
        origin = facts.get('origin')
        identity = ('AlexPastukhh/' + slug if type(slug) is str and REPO.fullmatch(slug) else None)
        if identity is None or type(origin) is not str or not 0 < len(origin) <= 512:
            identity, origin = None, None
            reasons.append('ORIGIN_MISMATCH')
        if any(value['verification'] != 'VERIFIED' for value in evidence.values()):
            reasons.append('SOURCE_UNAVAILABLE')
        now = int(self.clock())
        data = {'repoId': repo, 'previewId': str(uuid.uuid4()) if not reasons else None,
                'expectedHead': facts['windowsHead'], 'targetSha': facts['targetSha'],
                'dockerHead': facts['dockerHead'], 'originIdentity': identity,
                'sourceEvidence': evidence, 'checkedAt': timestamp(now),
                'expiresAt': timestamp(now + self.TTL), 'configGeneration': self._generation(gateway_generation),
                'blockers': list(dict.fromkeys(reasons))}
        require(all(data[key] is None or type(data[key]) is str and SHA.fullmatch(data[key])
                    for key in ('expectedHead', 'targetSha', 'dockerHead')))
        raw = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)
        require(len(raw.encode()) <= 8192, 'LIMIT_EXCEEDED')
        with self.worker._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = self._existing(db, request_id, actor, repo, gateway_generation)
            if existing is not None:
                return existing
            self._capacity(db, actor, request_id, now)
            db.execute('INSERT INTO machine_previews VALUES(?,?,?,?,?,?,?,?,?)',
                       (request_id, actor, repo, gateway_generation, data['configGeneration'],
                        data['previewId'], now + self.TTL, origin, raw))
        return data

    def lookup(self, repo, actor, request_id):
        principal(actor); identifier(request_id)
        with self.worker._db() as db:
            binding = db.execute('SELECT identity FROM machine_bindings WHERE request_id=? AND actor=? AND repo_id=?',
                                 (request_id, actor, repo)).fetchone()
            # Same response for absent, Browser-only and another actor's UUID.
            require(binding is not None, 'PERMISSION_DENIED')
            identity = json.loads(binding[0])
            require(type(identity) is dict and set(identity) ==
                    {'previewId', 'expectedHead', 'targetSha', 'configGeneration'})
            identifier(identity['previewId'])
            require(type(identity['configGeneration']) is str and GENERATION.fullmatch(identity['configGeneration']))
            require(all(type(identity[key]) is str and SHA.fullmatch(identity[key])
                        for key in ('expectedHead', 'targetSha')))
            saved = db.execute('SELECT data,origin FROM machine_previews WHERE preview_id=? AND actor=? AND repo_id=?',
                               (identity['previewId'], actor, repo)).fetchone()
            require(saved is not None, 'PERMISSION_DENIED')
            preview = json.loads(saved[0])
            require(all(identity[key] == preview[key] for key in identity), 'REQUEST_ID_CONFLICT')
            stored = db.execute('SELECT payload,state,result FROM pull_receipts WHERE request_id=?', (request_id,)).fetchone()
        target = identity['targetSha']
        result = {'requestId': request_id, 'repoId': repo, 'workerState': 'NOT_FOUND',
                  'effect': 'UNKNOWN', 'observedHead': None, 'expectedTarget': target,
                  'checkedAt': timestamp(int(self.clock())), 'errorCode': 'OUTCOME_UNCONFIRMED', 'evidence': 'NONE'}
        if stored is None:
            return result
        payload, state, raw = stored
        expected = dict(requestId=request_id, repoId=repo, expectedHead=identity['expectedHead'],
                        targetSha=target, origin=saved[1])
        require(json.loads(payload) == expected, 'REQUEST_ID_CONFLICT')
        receipt = json.loads(raw) if raw else {}
        require(type(receipt) is dict)
        if receipt:
            require(receipt.get('requestId') == request_id and receipt.get('state') == state)
        head = receipt.get('head')
        require(head is None or type(head) is str and SHA.fullmatch(head))
        result.update(workerState=state if state in ('DISPATCHED', 'UNCERTAIN') else 'UNCERTAIN', observedHead=head)
        # This READ observes stored outcomes only. It never calls worker.status,
        # probes Git or infers completion from an intermediate target readback.
        if state == 'EXECUTED' and head == target and receipt.get('workerFinished') is not False:
            result.update(workerState='EXECUTED', effect='CONFIRMED', errorCode=None,
                          evidence='TARGET_READBACK' if receipt.get('evidence') == 'TARGET_READBACK' else 'WORKER_RECEIPT')
        elif (state == 'FAILED' and receipt.get('knownFailure') is True and
              receipt.get('mergeStarted') is False and receipt.get('workerFinished') is True):
            result.update(workerState='FAILED', effect='NONE', errorCode=blocker(receipt.get('error')))
        return result
