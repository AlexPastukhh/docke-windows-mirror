"""Real bounded child fixtures and Windows pre-GO assignment fault injection."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from mirror_api.commands import command, budget, OutputLimit


class CommandTests(unittest.TestCase):
    def test_captures_bounded_stdout_and_stderr(self):
        r=command([sys.executable,'-c','import sys;print("ok");sys.stderr.write("error")'])
        self.assertEqual((0,b'ok\n',b'error'),(r.returncode,r.stdout.replace(b'\r\n',b'\n'),r.stderr))

    def test_stdout_and_stderr_overflow_abort_without_leaking_output(self):
        for stream in ('stdout','stderr'):
            with self.subTest(stream=stream):
                with self.assertRaises(OutputLimit) as error:
                    command([sys.executable,'-c',f'import sys;sys.{stream}.write("PRIVATE"*20000)'],
                            max_stdout=100,max_stderr=100)
                self.assertEqual('COMMAND_OUTPUT_LIMIT',str(error.exception))

    def test_timeout_terminates_child_and_drains_pipes(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            command([sys.executable,'-c','import time;print("start",flush=True);time.sleep(20)'],timeout=.1)

    def test_expired_request_never_launches_a_child(self):
        with budget(0), mock.patch('mirror_api.commands.subprocess.Popen') as spawn:
            with self.assertRaises(subprocess.TimeoutExpired):command(['git','status'])
        spawn.assert_not_called()

    def test_nested_budget_cannot_extend_parent_deadline(self):
        from mirror_api.commands import remaining
        with budget(.2):
            with budget(60):self.assertLess(remaining(),.21)

    def test_discarded_streams_return_no_captured_data(self):
        r=command([sys.executable,'-c','print("data")'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.assertIsNone(r.stdout);self.assertIsNone(r.stderr)

    @unittest.skipUnless(os.name=='nt','Windows Git PATH/CRT transport qualification')
    def test_real_windows_git_read_through_guarded_helper(self):
        result=command(['git','--version'])
        self.assertEqual(0,result.returncode)
        self.assertIn(b'git version ',result.stdout)

    def test_private_helper_waits_and_propagates_real_child_failure(self):
        helper=Path(__file__).parent/'mirror_api'/'command_child.py'
        frame=json.dumps({'protocol':'FORTIS_COMMAND_V1','argv':[
            sys.executable,'-I','-B','-c','import sys;print("child");sys.exit(7)']}).encode()+b'\n'
        result=subprocess.run([sys.executable,'-I','-S','-B',str(helper)],input=frame,capture_output=True,timeout=3)
        self.assertEqual(7,result.returncode)
        self.assertEqual(b'child\n',result.stdout.replace(b'\r\n',b'\n'))

    def test_helper_eof_or_invalid_frame_never_executes(self):
        helper=Path(__file__).parent/'mirror_api'/'command_child.py'
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/'must-not-exist'
            payload={'protocol':'INVALID','argv':[sys.executable,'-c',f'open({str(marker)!r},"w").write("bad")']}
            for data in (b'',json.dumps(payload).encode()+b'\n',b'x'*4097):
                r=subprocess.run([sys.executable,'-I','-B',str(helper)],input=data,capture_output=True,timeout=3)
                self.assertNotEqual(0,r.returncode);self.assertFalse(marker.exists())


class WindowsHandshakeTests(unittest.TestCase):
    def fixture(self, fail=False):
        events=[]
        class Input(io.BytesIO):
            def write(self,data):events.append(('GO',data));return super().write(data)
        class Process:
            _handle=77;pid=999
            def __init__(self):
                self.stdin=Input();self.stdout=io.BytesIO(b'ok');self.stderr=io.BytesIO();self.returncode=0
                if fail:self.returncode=None
            def poll(self):return self.returncode
            def wait(self,timeout=None):return self.returncode
            def kill(self):events.append(('kill',77));self.returncode=-9
        class Job:
            def assign(self,process):
                events.append(('assign',process._handle))
                if fail:raise OSError('ASSIGN_FAILED')
            def stop(self,timeout=3):events.append(('stop',))
            def close(self):events.append(('close',))
        return events,Process(),Job

    def test_no_command_frame_before_successful_job_assignment(self):
        events,process,job=self.fixture(fail=True)
        with mock.patch('mirror_api.commands.IS_WINDOWS',True), \
             mock.patch('mirror_api.commands.Job',job), \
             mock.patch('mirror_api.commands.subprocess.Popen',return_value=process):
            with self.assertRaisesRegex(OSError,'ASSIGN_FAILED'):command(['git','fetch','origin','main'])
        self.assertFalse(any(x[0]=='GO' for x in events))
        self.assertIn(('kill',77),events)

    def test_go_follows_assignment_and_no_job_handle_is_inherited(self):
        events,process,job=self.fixture()
        with mock.patch('mirror_api.commands.IS_WINDOWS',True), \
             mock.patch('mirror_api.commands.Job',job), \
             mock.patch('mirror_api.commands.subprocess.Popen',return_value=process) as spawn:
            result=command(['git','status'])
        self.assertEqual(b'ok',result.stdout)
        self.assertEqual(('assign',77),events[0]);self.assertEqual('GO',events[1][0])
        self.assertEqual([sys.executable,'-I','-S','-B'],spawn.call_args.args[0][:4])
        self.assertTrue(spawn.call_args.kwargs['close_fds'])
        self.assertNotIn('handle_list',spawn.call_args.kwargs)


    def test_pipe_failure_never_becomes_a_successful_partial_result(self):
        events,process,job=self.fixture()
        class Broken:
            def read(self,n):raise OSError('PRIVATE_DETAIL')
            def close(self):pass
        process.stdout=Broken()
        with mock.patch('mirror_api.commands.IS_WINDOWS',True), \
             mock.patch('mirror_api.commands.Job',job), \
             mock.patch('mirror_api.commands.subprocess.Popen',return_value=process):
            with self.assertRaisesRegex(OSError,'^COMMAND_PIPE_FAILED$'):command(['git','status'])
