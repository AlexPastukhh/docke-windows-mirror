"""Lifetime writer exclusion keyed by canonical Windows repository root.

The lock is independent of receipt DB and HTTP port. Never unlink its file.
Manual owner Git/PowerShell and already-running pre-P2 binaries are outside
this guard; owner cutover must quiesce those writers separately.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import stat
import threading

from .windows_job import Job, JobError

CURRENT_WRITER = ContextVar('mirror_writer', default=None)


class GuardError(OSError):
    pass


def reparse(path):
    value = path.lstat()
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, 'st_file_attributes', 0) & 0x400)


class WriterGuard:
    def __init__(self, root, *, job_factory=Job):
        self.root = Path(root).resolve()
        self.key = hashlib.sha256(os.path.normcase(str(self.root)).encode('utf-8')).hexdigest()
        self.path = self.root / '.fortis-mirror-writer.lock'
        self.lock = threading.RLock()
        self.fd = None
        self.job = None
        self.job_factory = job_factory
        self.closed = False
        self.poisoned = False
        self._store = None

    def bind_store(self, path, store_id):
        value = os.path.normcase(str(Path(path).resolve())) + '\n' + store_id
        identity = hashlib.sha256(value.encode('utf-8')).hexdigest()
        with self.lock:
            if self._store is not None and self._store != identity:
                raise GuardError('MIRROR_RECEIPT_STORE_MISMATCH')
            self._store = identity

    def _binding(self, fd, *, initialize):
        if self._store is None:
            return  # Primitive guard fixture; all production PullWorkers bind a store.
        os.lseek(fd, 1, os.SEEK_SET)
        raw = os.read(fd, 4097)
        if not raw and initialize:
            data = json.dumps({'schemaVersion':1,'receiptStore':self._store},
                              separators=(',', ':')).encode('ascii')
            os.lseek(fd, 1, os.SEEK_SET)
            if os.write(fd, data) != len(data):
                raise GuardError('MIRROR_RECEIPT_STORE_UNCONFIRMED')
            os.fsync(fd)
            return
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate binding field')
                result[key] = value
            return result
        try:
            value = json.loads(raw, object_pairs_hook=unique)
        except (ValueError, UnicodeError):
            raise GuardError('MIRROR_RECEIPT_STORE_UNCONFIRMED') from None
        if (len(raw)>4096 or type(value) is not dict or set(value)!={'schemaVersion','receiptStore'} or
                type(value['schemaVersion']) is not int or value['schemaVersion'] != 1 or value['receiptStore'] != self._store):
            raise GuardError('MIRROR_RECEIPT_STORE_MISMATCH')

    def acquire(self):
        with self.lock:
            if self.closed or self.poisoned:
                raise GuardError('MIRROR_WRITER_UNAVAILABLE')
            if self.fd is not None:
                self.require()
                return
            if not self.root.is_dir():
                raise GuardError('WINDOWS_ROOT_UNAVAILABLE')
            if self.path.is_symlink() or (self.path.exists() and reparse(self.path)):
                raise GuardError('MIRROR_WRITER_LOCK_UNSAFE')
            flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
            fd = os.open(self.path, flags, 0o600)
            try:
                value = os.fstat(fd)
                if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or reparse(self.path):
                    raise GuardError('MIRROR_WRITER_LOCK_UNSAFE')
                if value.st_size == 0:
                    os.write(fd, b'\0')
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise GuardError('MIRROR_WRITER_BUSY') from None
                self._binding(fd, initialize=True)
                if os.name == 'nt':
                    self.job = self.job_factory('Global\\FortisMirrorWriter-' + self.key)
                self.fd = fd
            except BaseException:
                os.close(fd)
                raise

    def require(self):
        with self.lock:
            if self.fd is None or self.closed or self.poisoned:
                raise GuardError('MIRROR_WRITER_UNAVAILABLE')
            a, b = os.fstat(self.fd), self.path.stat()
            if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino) or reparse(self.path):
                self.poisoned = True
                raise GuardError('MIRROR_WRITER_LOCK_CHANGED')
            self._binding(self.fd, initialize=False)

    @contextmanager
    def scope(self):
        self.acquire()
        token = CURRENT_WRITER.set(self)
        try:
            yield
        finally:
            CURRENT_WRITER.reset(token)

    def attach(self, process, child_job):
        with self.lock:
            self.require()
            if self.job is not None:
                self.job.assign(process)
            child_job.assign(process)

    def poison(self):
        with self.lock:
            self.poisoned = True

    def close(self):
        with self.lock:
            self.closed = True
            if self.job is not None:
                # Keep the lock if quiescence cannot be established. At process
                # death the named job also fences the next controller startup.
                self.job.stop()
                self.job.close()
                self.job = None
            if self.fd is not None:
                # POSIX: close, not LOCK_UN. A surviving child inherits this
                # open-file description and keeps exclusion until it exits.
                os.close(self.fd)
                self.fd = None
