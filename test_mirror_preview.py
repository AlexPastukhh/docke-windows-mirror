"""Read-only Windows preview bootstrap configuration (no Docker/Windows runtime required)."""
import contextlib
import io
import json
from unittest import mock
import unittest
from mirror_api import operations
from mirror_api import server


class PreviewTests(unittest.TestCase):
    def test_safe_source_selects_task_and_still_uses_read_only_docker(self):
        original_exporter=operations.EXPORTER
        original_reader=operations.GITHUB_READER
        try:
            operations.set_preview_source('mirror-api-workflow-20261107')
            self.assertEqual('/workspace/repo/Worktrees/docke-windows-mirror/mirror-api-workflow-20261107/tools/Export-Docker-Branch-Workflow.py', operations.EXPORTER)
            self.assertEqual('/workspace/repo/Worktrees/docke-windows-mirror/mirror-api-workflow-20261107/tools/Export-Mirror-GitHub-Read.py', operations.GITHUB_READER)
            with mock.patch.object(operations,'run',return_value=json.dumps({'schema':'fortis.docker.worktrees.v1','repositories':{}})) as cmd:
                data=operations.docker_inventory()
                self.assertEqual({},data)
            called=cmd.call_args.args[0]
            self.assertIn(operations.EXPORTER,called)
            self.assertIn('--inventory-json',called)
            self.assertNotIn('git add',str(called))
        finally:
            operations.EXPORTER=original_exporter
            operations.GITHUB_READER=original_reader

    def test_invalid_preview_task_rejected(self):
        for task in ('../../main', '../unsafe', 'contains space', '', '/workspace/repo', 'task/anything'):
            with self.subTest(task=task):
                with self.assertRaises(operations.ObservationError):
                    operations.set_preview_source(task)

    def test_preview_forbids_write_grants_and_gateway_listener(self):
        for params in (
            ['--preview-source-task','mirror-api-workflow-20261107','--owner-git-repo','fixture'],
            ['--preview-source-task','mirror-api-workflow-20261107','--gateway-listen'],
        ):
            with self.subTest(params=params), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    server.main(params)
                self.assertEqual(2,result.exception.code)


if __name__ == '__main__':
    unittest.main()
