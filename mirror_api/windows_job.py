"""Small Win32 Job Object boundary. No process is addressed by a reused PID.

https://learn.microsoft.com/windows/win32/procthread/job-objects
Job handles are never inheritable. Children cannot request breakaway.
Windows runtime qualification is separate from the portable fixture suite.
"""
import ctypes as C
import os
import time


class JobError(OSError):
    pass


class BasicLimits(C.Structure):
    _fields_ = [('process_time', C.c_int64), ('job_time', C.c_int64),
                ('flags', C.c_uint32), ('min_working_set', C.c_size_t),
                ('max_working_set', C.c_size_t), ('process_limit', C.c_uint32),
                ('affinity', C.c_size_t), ('priority', C.c_uint32),
                ('scheduling', C.c_uint32)]


class IOCounts(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in
                ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]


class ExtendedLimits(C.Structure):
    _fields_ = [('basic', BasicLimits), ('io', IOCounts),
                ('process_memory', C.c_size_t), ('job_memory', C.c_size_t),
                ('peak_process_memory', C.c_size_t), ('peak_job_memory', C.c_size_t)]


class Accounting(C.Structure):
    _fields_ = [(n, C.c_int64) for n in ('user', 'kernel', 'period_user', 'period_kernel')] + [
        (n, C.c_uint32) for n in ('faults', 'total', 'active', 'terminated')]


class WinJobAPI:
    def __init__(self):
        if os.name != 'nt':
            raise JobError('WINDOWS_JOB_UNAVAILABLE')
        self.k = C.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([C.c_void_p, C.c_wchar_p], C.c_void_p),
            'SetInformationJobObject': ([C.c_void_p, C.c_int32, C.c_void_p, C.c_uint32], C.c_int32),
            'QueryInformationJobObject': ([C.c_void_p, C.c_int32, C.c_void_p, C.c_uint32, C.c_void_p], C.c_int32),
            'AssignProcessToJobObject': ([C.c_void_p, C.c_void_p], C.c_int32),
            'TerminateJobObject': ([C.c_void_p, C.c_uint32], C.c_int32),
            'CloseHandle': ([C.c_void_p], C.c_int32),
        }
        for name, (args, result) in signatures.items():
            f = getattr(self.k, name)
            f.argtypes, f.restype = args, result

    def create(self, name):
        C.set_last_error(0)
        handle = self.k.CreateJobObjectW(None, name)
        if not handle:
            raise JobError('WINDOWS_JOB_CREATE_FAILED')
        return handle, C.get_last_error() == 183

    def configure(self, handle):
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; neither breakaway flag.
        if not self.k.SetInformationJobObject(handle, 9, C.byref(limits), C.sizeof(limits)):
            raise JobError('WINDOWS_JOB_LIMIT_FAILED')

    def active(self, handle):
        value = Accounting()
        if not self.k.QueryInformationJobObject(handle, 1, C.byref(value), C.sizeof(value), None):
            raise JobError('WINDOWS_JOB_QUERY_FAILED')
        return value.active

    def assign(self, handle, process_handle):
        if not self.k.AssignProcessToJobObject(handle, process_handle):
            raise JobError('WINDOWS_JOB_ASSIGN_FAILED')

    def terminate(self, handle):
        if not self.k.TerminateJobObject(handle, 125):
            raise JobError('WINDOWS_JOB_TERMINATE_FAILED')

    def close(self, handle):
        self.k.CloseHandle(handle)


class Job:
    def __init__(self, name=None, *, api=None):
        self.api = api or WinJobAPI()
        self.handle = None
        handle, existing = self.api.create(name)
        try:
            if existing:
                # A dead controller may have released its root lock while its
                # job's children are still terminating. Never start a new writer.
                if self.api.active(handle):
                    raise JobError('WINDOWS_PREVIOUS_CHILDREN_ACTIVE')
                self.api.close(handle)
                handle = None
                handle, existing = self.api.create(name)
                if existing:
                    raise JobError('WINDOWS_PREVIOUS_JOB_UNCONFIRMED')
            self.api.configure(handle)
            self.handle = handle
        except BaseException:
            if handle is not None:
                self.api.close(handle)
            raise

    def assign(self, process):
        # CPython's retained CreateProcess handle binds the exact child. Do not
        # use OpenProcess(pid), which could select a recycled, unrelated process.
        handle = getattr(process, '_handle', None)
        if handle is None or self.handle is None:
            raise JobError('WINDOWS_CHILD_HANDLE_UNAVAILABLE')
        self.api.assign(self.handle, int(handle))

    def stop(self, timeout=3):
        if self.handle is None:
            return
        if self.api.active(self.handle):
            self.api.terminate(self.handle)
        deadline = time.monotonic() + timeout
        while self.api.active(self.handle):
            if time.monotonic() >= deadline:
                raise JobError('WINDOWS_CHILDREN_UNCONFIRMED')
            time.sleep(0.01)

    def close(self):
        if self.handle is not None:
            self.api.close(self.handle)
            self.handle = None
