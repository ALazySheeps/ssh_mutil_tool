import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ssh_multi_tool.scripts import build_script
from test_models import profile

BASH = os.environ.get("SSHMT_TEST_BASH") or (shutil.which("bash") if os.name == "posix" else None)


def bash_path(path):
    if os.name != "nt": return str(path)
    result = subprocess.run([BASH, "-c", 'cygpath -u "$1"', "--", str(path)], text=True, encoding="utf-8", capture_output=True, check=True)
    return result.stdout.strip()


class ScriptsTests(unittest.TestCase):
    def test_fields_are_quoted_without_expanding_shell_syntax(self):
        data = profile(); window = data["windows"][0]
        window["working_directory"] = "~/weird $(touch injected) ' folder"
        script = build_script(data, window)
        self.assertIn("set -e", script)
        self.assertNotIn("cd -- ~/", script)
        self.assertIn('"$HOME"/', script)
        self.assertIn("echo 中文", script)

    @unittest.skipUnless(BASH, "Bash unavailable; use Linux or SSHMT_TEST_BASH")
    def test_environment_source_and_directory_survive_in_one_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); work = root / "中文 space $(touch injection)"; work.mkdir()
            init = root / "setup.bash"; init.write_bytes(b"export FROM_SOURCE=yes\n")
            data = profile(); data["common_init"] = f"source '{bash_path(init)}'\nexport A=10"
            window = data["windows"][0]; window["working_directory"] = bash_path(work)
            window["commands"] = 'printf "%s:%s:%s" "$A" "$FROM_SOURCE" "$PWD"'
            result = subprocess.run([BASH, "--noprofile", "--norc"], input=build_script(data, window).encode("utf-8"),
                                    capture_output=True, env={**os.environ, "HOME": bash_path(root)})
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
            self.assertEqual(result.stdout.decode("utf-8"), f"10:yes:{bash_path(work)}")
            self.assertFalse((root / "injection").exists())

    @unittest.skipUnless(BASH, "Bash unavailable; use Linux or SSHMT_TEST_BASH")
    def test_failed_init_prevents_task(self):
        data = profile(); data["common_init"] = "false"
        window = data["windows"][0]; window["working_directory"] = ""
        window["commands"] = "echo SHOULD_NOT_RUN"
        result = subprocess.run([BASH, "--noprofile", "--norc"], input=build_script(data, window).encode("utf-8"),
                                capture_output=True, env={**os.environ, "HOME": bash_path(Path.cwd())})
        self.assertNotEqual(result.returncode, 0); self.assertNotIn(b"SHOULD_NOT_RUN", result.stdout)
        self.assertEqual(result.stderr, b"")
