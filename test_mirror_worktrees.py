"""Real disposable linked-worktree targeting; no network or actual repo writes."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent

def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

class WorktreeTargetTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)/'Repos'
        self.root.mkdir()
        self.primary = self.root/'fixture'
        self.primary.mkdir()
        self.tasks = self.root.parent/'Worktrees'/'fixture'
        self.tasks.mkdir(parents=True)
        self.mirror = module(HERE/'tools/Export-Docker-Branch-Workflow.py', 'mirror_fixture')
        self.actions = module(HERE/'tools/Docker-Mirror-Git-Actions.py', 'actions_fixture')
        self.git('init','-q')
        self.git('config','user.name','Fixture')
        self.git('config','user.email','fixture@example.invalid')
        self.git('branch','-m','main')
        (self.primary/'README').write_text('original\n')
        self.git('add','README')
        self.git('commit','-qm','base')
        self.task = self.tasks/'task-one'
        self.git('worktree','add','-q','-b','task/task-one',str(self.task))

    def git(self,*args):
        return subprocess.run(['git','-C',str(self.primary),*args],check=True,
            text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10).stdout.strip()

    def test_registered_target_and_separate_status(self):
        (self.task/'task.txt').write_text('task changes')
        result = self.mirror.inventory_worktrees(self.root,'fixture')
        self.assertEqual(2,len(result))
        self.assertEqual([None,'task-one'],[item['task'] for item in result])
        self.assertEqual(True,result[0]['clean'])
        self.assertEqual(False,result[1]['clean'])
        self.assertEqual(self.task,self.mirror.exact_target(self.root,'fixture','task-one'))
        self.assertEqual(self.primary,self.mirror.exact_target(self.root,'fixture',None))
        self.assertEqual(self.task,self.mirror.exact_target(self.root,'fixture','task-one'))

    def test_mismatched_task_or_unregistered_directory_is_denied(self):
        (self.tasks/'not-registered').mkdir()
        for slug in ('not-registered','../../fixture','task-two'):
            with self.assertRaises(ValueError):
                self.mirror.exact_target(self.root,'fixture',slug)
        with self.assertRaises(ValueError):
            self.mirror.inventory_worktrees(self.root,'unknown')

    def test_linked_worktree_has_independent_index(self):
        (self.task/'task.txt').write_text('task changes')
        self.actions.mirror = self.mirror
        # Network origin is intentionally absent: action must deny, not write primary.
        with self.assertRaisesRegex(ValueError,'origin'):
            self.actions.stage_preview(self.root,'fixture','task-one')
        self.assertEqual('',self.git('status','--porcelain'))

if __name__ == '__main__':
    unittest.main()
