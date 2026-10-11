"""Linux-only execution of the exact remote loader in a real PTY."""
import os
import select
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ssh_multi_tool.helper import PhaseParser, loader_command
from ssh_multi_tool.scripts import build_script
from test_models import profile


@unittest.skipUnless(sys.platform.startswith("linux"), "Actual PTY loader needs Linux")
class PtyTests(unittest.TestCase):
    def execute(self, script, marker, home, interrupt=False):
        import pty
        pid, fd = pty.fork()
        if pid == 0:
            os.environ["HOME"] = str(home)
            os.execlp("bash", "bash", "--noprofile", "--norc", "-c", loader_command(script.encode(), marker))
        parser = PhaseParser(marker); output = bytearray(); phases = []; sent = False; interrupted = False
        deadline = time.monotonic() + 8; status = None
        try:
            while time.monotonic() < deadline:
                readable, _, _ = select.select([fd], [], [], .05)
                if readable:
                    try: raw = os.read(fd, 65536)
                    except OSError: break  # Linux PTY returns EIO after slave exits.
                    if not raw: break
                    visible, events = parser.feed(raw); output.extend(visible); phases.extend(events)
                    if "ready" in events and not sent:
                        payload = script.encode()
                        while payload:
                            count = os.write(fd, payload[:4096]); payload = payload[count:]
                        sent = True
                    if interrupt and "running" in phases and not interrupted:
                        os.write(fd, b"\x03"); interrupted = True
                done, result = os.waitpid(pid, os.WNOHANG)
                if done: status = result; break
            if status is None:
                done, result = os.waitpid(pid, os.WNOHANG)
                if done: status = result
            self.assertIsNotNone(status, "PTY command must terminate before timeout")
            output.extend(parser.finish())
            return os.waitstatus_to_exitcode(status), bytes(output).replace(b"\r\n", b"\n"), phases
        finally:
            os.close(fd)
            if status is None:
                import signal
                try: os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
                except ProcessLookupError: pass

    def test_loader_runs_large_script_with_source_export_and_literal_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); work = root / "中文 space $(touch injected)"; work.mkdir()
            init = root / "setup.bash"; init.write_text("export FROM_SOURCE=yes\n")
            data = profile(); data["common_init"] = f"source '{init}'\nexport A=10\n#" + "a" * (150 * 1024)
            window = data["windows"][0]; window["working_directory"] = str(work)
            window["commands"] = 'printf "%s:%s:%s\\n" "$A" "$FROM_SOURCE" "$PWD"'
            marker = "e" * 32
            code, output, phases = self.execute(build_script(data, window, marker), marker, root)
            self.assertEqual(code, 0); self.assertIn(f"10:yes:{work}\n".encode(), output)
            self.assertEqual(phases, ["ready", "initializing", "running"])
            self.assertFalse((root / "injected").exists())

    def test_ctrl_c_interrupts_foreground_program_after_loader_restores_pty(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = profile(); data["common_init"] = "export A=10"
            window = data["windows"][0]; window["working_directory"] = ""
            window["commands"] = "sleep 30"
            marker = "f" * 32
            code, _, phases = self.execute(build_script(data, window, marker), marker, Path(tmp), interrupt=True)
            self.assertNotEqual(code, 0); self.assertIn("running", phases)
