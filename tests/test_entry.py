import subprocess
import sys
import unittest


class EntryTests(unittest.TestCase):
    def test_help_works_without_display(self):
        result = subprocess.run([sys.executable, "-m", "ssh_multi_tool", "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--helper", result.stdout)
