"""Browser Mirror 13-column primary table smoke test in existing Actions suite."""
from pathlib import Path
import shutil
import subprocess
import unittest


class BrowserPrimaryTableTests(unittest.TestCase):
    def test_primary_table_tabs_copy_and_readonly_checks(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node executable unavailable")
        root = Path(__file__).resolve().parent
        command = [node, str(root / "tests" / "mirror_table_ui_smoke.js")]
        result = subprocess.run(command, cwd=root, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=18, check=False,
                                text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("MIRROR_PRIMARY_TABLE_SMOKE_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
