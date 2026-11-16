"""Bounded internal argv execution; never a caller-selected command surface."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from .process_guard import CURRENT_WRITER
from .windows_job import Job

IS_WINDOWS = os.name == 'nt'
DEADLINE = ContextVar('mirror_command_deadline', default=None)


class OutputLimit(OSError):
    pass


class ChildrenUnconfirmed(OSError):
    pass


def remaining(default=60):
    deadline = DEADLINE.get()
    return max(0, min(default, deadline - time.monotonic())) if deadline is not None else default


@contextmanager
def budget(seconds=60):
    token = DEADLINE.set(time.monotonic() + remaining(seconds))
    try:
        yield
    finally:
        DEADLINE.reset(token)


def command(argv, *, timeout=25, env=None, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            max_stdout=1000000, max_stderr=16384):
    if (type(argv) not in (list, tuple) or not argv or len(argv) > 128 or
            any(type(x) is not str or '\0' in x for x in argv) or
            stdin != subprocess.DEVNULL or check or stdout not in (subprocess.PIPE, subprocess.DEVNULL) or
            stderr not in (subprocess.PIPE, subprocess.DEVNULL)):
        raise ValueError('COMMAND_CONTRACT_INVALID')
    duration = min(timeout, remaining(60) - 3)
    if duration <= 0:
        raise subprocess.TimeoutExpired('mirror-command', duration)
    writer = CURRENT_WRITER.get()
    if writer is not None:
        writer.require()
    child_job = Job() if IS_WINDOWS else None
    process = None
    chunks, counts = [[], []], [0, 0]
    limits = (max_stdout, max_stderr)
    overflow = threading.Event()
    pipe_failed = threading.Event()
    threads = []
    stopped = False
    assigned = False
    cleanup_deadline = None
    deadline = time.monotonic() + duration

    def drain(stream, index):
        try:
            while True:
                block = stream.read(4096)
                if not block:
                    break
                space = max(0, limits[index] - counts[index])
                if space:
                    chunks[index].append(block[:space])
                counts[index] += len(block)
                if counts[index] > limits[index]:
                    overflow.set()
        except Exception:
            pipe_failed.set()
        finally:
            try:
                stream.close()
            except OSError:
                pipe_failed.set()

    def stop():
        nonlocal stopped, cleanup_deadline
        if stopped or process is None:
            return
        if cleanup_deadline is None:
            cleanup_deadline = time.monotonic() + 3
        if child_job is not None:
            if not assigned:
                if process.poll() is None:
                    process.kill()  # Retained handle; the helper never got GO.
            else:
                child_job.stop(timeout=max(0, cleanup_deadline - time.monotonic()))
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=max(0.01, cleanup_deadline - time.monotonic()))
        stopped = True

    try:
        kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      env=env, close_fds=True)
        launch = list(argv)
        if IS_WINDOWS:
            frame = json.dumps({'protocol': 'FORTIS_COMMAND_V1', 'argv': list(argv)},
                               ensure_ascii=True, separators=(',', ':')).encode('ascii') + b'\n'
            if len(frame) > 4096:
                raise ValueError('COMMAND_INPUT_LIMIT')
            launch = [sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name('command_child.py'))]
            kwargs.update(stdin=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
        else:
            kwargs['start_new_session'] = True
            if writer is not None:
                kwargs['pass_fds'] = (writer.fd,)
        process = subprocess.Popen(launch, **kwargs)
        if child_job is not None:
            if writer is not None:
                writer.attach(process, child_job)
            else:
                child_job.assign(process)
            assigned = True
            # The helper blocks on this private pipe until both Jobs own it.
            process.stdin.write(frame)
            process.stdin.close()
        for index, stream in enumerate((process.stdout, process.stderr)):
            t = threading.Thread(target=drain, args=(stream, index), daemon=True)
            threads.append(t)
            t.start()
        expired = False
        def leader_finished():
            if not IS_WINDOWS and hasattr(os, 'WNOWAIT'):
                # Observe without reaping: retain the process-group identity
                # through killpg, so a recycled PID cannot select another group.
                return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
            return process.poll() is not None
        while not leader_finished():
            if overflow.is_set() or pipe_failed.is_set() or time.monotonic() >= deadline:
                expired = not overflow.is_set()
                break
            time.sleep(0.01)
        # Even a successful leader must leave no unbounded descendants holding
        # pipes or writing after completion. Stop only this command's child Job.
        stop()
        for t in threads:
            t.join(timeout=max(0, cleanup_deadline - time.monotonic()))
        if any(t.is_alive() for t in threads):
            raise ChildrenUnconfirmed('COMMAND_PIPES_UNCONFIRMED')
        if pipe_failed.is_set():
            raise OSError('COMMAND_PIPE_FAILED')
        if overflow.is_set():
            raise OutputLimit('COMMAND_OUTPUT_LIMIT')
        if expired:
            raise subprocess.TimeoutExpired('mirror-command', duration)
        return subprocess.CompletedProcess(list(argv), process.returncode,
            b''.join(chunks[0]) if stdout == subprocess.PIPE else None,
            b''.join(chunks[1]) if stderr == subprocess.PIPE else None)
    finally:
        failure = False
        if process is not None:
            try:
                stop()
            except (OSError, subprocess.TimeoutExpired):
                failure = True
                if writer is not None:
                    writer.poison()
            if process.stdin is not None:
                process.stdin.close()
            for stream in (process.stdout, process.stderr):
                if stream is not None and not any(t.is_alive() for t in threads):
                    stream.close()
        if child_job is not None:
            child_job.close()
        if failure:
            raise ChildrenUnconfirmed('COMMAND_CHILDREN_UNCONFIRMED') from None
