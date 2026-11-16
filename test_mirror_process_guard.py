"""Real cross-process exclusion plus closed Win32 adapter fault fixtures."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from mirror_api.process_guard import WriterGuard, GuardError
from mirror_api.windows_job import Job, JobError, ExtendedLimits, Accounting


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_distinct_instances_exclude_and_release_without_unlink(self):
        first, second = WriterGuard(self.root), WriterGuard(self.root)
        self.addCleanup(first.close);self.addCleanup(second.close)
        first.acquire()
        with self.assertRaisesRegex(GuardError,'MIRROR_WRITER_BUSY'):second.acquire()
        first.close()
        self.assertTrue(first.path.is_file())
        second.acquire()

    @unittest.skipIf(os.name=='nt','POSIX alias fixture; Windows junction gate is owner-qualified')
    def test_root_alias_cannot_change_lock_identity(self):
        alias = self.root/'alias';real = self.root/'real';real.mkdir();alias.symlink_to(real,target_is_directory=True)
        a,b = WriterGuard(real),WriterGuard(alias)
        self.addCleanup(a.close);self.addCleanup(b.close)
        self.assertEqual(a.key,b.key)
        a.acquire()
        with self.assertRaises(GuardError):b.acquire()

    @unittest.skipIf(os.name=='nt','POSIX symlink fixture')
    def test_unsafe_lock_link_is_rejected_before_open(self):
        guard = WriterGuard(self.root);self.addCleanup(guard.close)
        target = self.root/'missing';guard.path.symlink_to(target)
        with self.assertRaisesRegex(GuardError,'MIRROR_WRITER_LOCK_UNSAFE'):guard.acquire()
        self.assertFalse(target.exists())

    @unittest.skipIf(os.name=='nt','Portable fixture for inherited open-file-description exclusion')
    def test_child_keeps_exclusion_if_controller_dies(self):
        package = str(Path(__file__).resolve().parent)
        code = '''import sys,subprocess,time
from mirror_api.process_guard import WriterGuard
g=WriterGuard(sys.argv[1]);g.acquire()
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(2)'],pass_fds=(g.fd,))
print('READY',flush=True)
time.sleep(20)
'''
        env=dict(os.environ,PYTHONPATH=package)
        parent=subprocess.Popen([sys.executable,'-B','-c',code,str(self.root)],env=env,
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(lambda: parent.poll() is None and parent.kill())
        self.assertEqual('READY',parent.stdout.readline().strip())
        parent.kill();parent.wait(timeout=3)
        guard=WriterGuard(self.root);self.addCleanup(guard.close)
        with self.assertRaisesRegex(GuardError,'MIRROR_WRITER_BUSY'):guard.acquire()
        # Wait on inherited stdout EOF from the controlled child, not a sleep
        # claiming quiescence. The file lock is then independently reacquired.
        parent.communicate(timeout=4)
        guard.acquire()


class FakeJobAPI:
    def __init__(self, *, existing=False, active=0, repeated=False):
        self.existing=existing;self.count=active;self.repeated=repeated
        self.calls=[];self.created=0
    def create(self,name):
        self.created+=1;self.calls.append(('create',name))
        return self.created,self.existing and (self.created==1 or self.repeated)
    def configure(self,h):self.calls.append(('configure',h))
    def active(self,h):return self.count
    def close(self,h):self.calls.append(('close',h))
    def assign(self,h,p):self.calls.append(('assign',h,p))
    def terminate(self,h):self.calls.append(('terminate',h));self.count=0


class WinJobBoundaryTests(unittest.TestCase):
    def test_existing_active_job_is_not_adopted(self):
        api=FakeJobAPI(existing=True,active=1)
        with self.assertRaisesRegex(JobError,'WINDOWS_PREVIOUS_CHILDREN_ACTIVE'):Job('scope',api=api)
        self.assertNotIn(('configure',1),api.calls)

    def test_existing_empty_job_must_disappear_before_new_owner(self):
        api=FakeJobAPI(existing=True)
        job=Job('scope',api=api);self.addCleanup(job.close)
        self.assertIn(('close',1),api.calls);self.assertIn(('configure',2),api.calls)
        api=FakeJobAPI(existing=True,repeated=True)
        with self.assertRaisesRegex(JobError,'WINDOWS_PREVIOUS_JOB_UNCONFIRMED'):Job('scope',api=api)

    def test_assignment_uses_retained_process_handle_not_pid(self):
        api=FakeJobAPI();job=Job(api=api);self.addCleanup(job.close)
        process=type('Process',(),{'_handle':77,'pid':999})()
        job.assign(process)
        self.assertIn(('assign',1,77),api.calls)
        with self.assertRaises(JobError):job.assign(type('Process',(),{'pid':999})())

    def test_termination_requires_observed_zero_active_processes(self):
        api=FakeJobAPI(active=1);job=Job(api=api);self.addCleanup(job.close)
        job.stop();self.assertIn(('terminate',1),api.calls)
        api.count=1
        with mock.patch.object(api,'terminate'):
            with self.assertRaisesRegex(JobError,'WINDOWS_CHILDREN_UNCONFIRMED'):job.stop(timeout=0)

    @unittest.skipUnless(ctypes.sizeof(ctypes.c_void_p)==8,'64-bit supported Windows ABI')
    def test_win32_structure_layout_matches_documented_64bit_abi(self):
        self.assertEqual(144,ctypes.sizeof(ExtendedLimits))
        self.assertEqual(48,ctypes.sizeof(Accounting))

@unittest.skipUnless(os.name=='nt','Requires actual Windows Job Objects; owner qualification gate')
class WindowsCrashQualification(unittest.TestCase):
    def test_controller_death_terminates_child_and_grandchild_before_new_writer(self):
        import ctypes as C
        import time
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            root=Path(folder)
            project=str(Path(__file__).resolve().parent)
            grand="import os,time;from pathlib import Path;Path("+repr(str(root/'grand.pid'))+").write_text(str(os.getpid()));time.sleep(25)"
            child="import os,sys,time,subprocess;from pathlib import Path;Path("+repr(str(root/'child.pid'))+").write_text(str(os.getpid()));subprocess.Popen([sys.executable,'-I','-B','-c',"+repr(grand)+"]);time.sleep(25)"
            parent="""import sys
sys.path.insert(0,sys.argv[3])
from mirror_api.process_guard import WriterGuard
from mirror_api.commands import command
g=WriterGuard(sys.argv[1])
with g.scope():command([sys.executable,'-I','-B','-c',sys.argv[2]],timeout=25)
"""
            proc=subprocess.Popen([sys.executable,'-I','-B','-c',parent,folder,child,project],
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            def cleanup():
                if proc.poll() is None:proc.kill()
                proc.communicate(timeout=5)
            stack.callback(cleanup)
            deadline=time.monotonic()+8
            while not all((root/n).exists() for n in ('child.pid','grand.pid')):
                if proc.poll() is not None:self.fail('Fixture controller exited before children started')
                if time.monotonic()>deadline:self.fail('Fixture startup timeout')
                time.sleep(.01)
            api=C.WinDLL('kernel32',use_last_error=True)
            api.OpenProcess.argtypes=[C.c_uint32,C.c_int32,C.c_uint32];api.OpenProcess.restype=C.c_void_p
            api.WaitForSingleObject.argtypes=[C.c_void_p,C.c_uint32];api.WaitForSingleObject.restype=C.c_uint32
            api.CloseHandle.argtypes=[C.c_void_p];api.CloseHandle.restype=C.c_int32
            handles=[]
            for name in ('child.pid','grand.pid'):
                handle=api.OpenProcess(0x100000|0x1000,False,int((root/name).read_text()))
                self.assertTrue(handle,'Cannot observe fixture child termination')
                stack.callback(api.CloseHandle,handle);handles.append(handle)
            proc.kill();proc.wait(timeout=5)  # Retained parent handle, never taskkill/PID targeting.
            for handle in handles:self.assertEqual(0,api.WaitForSingleObject(handle,5000))
            guard=WriterGuard(root);stack.callback(guard.close)
            deadline=time.monotonic()+5
            while True:
                try:guard.acquire();break
                except (GuardError,JobError):
                    if time.monotonic()>deadline:raise
                    time.sleep(.01)
            guard.require()
