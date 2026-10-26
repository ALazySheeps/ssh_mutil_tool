"""Linux-only execution of the exact remote loader in a real PTY."""
import os
import errno
import select
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ssh_multi_tool.helper import PhaseParser, loader_command
from ssh_multi_tool import scripts
from ssh_multi_tool.scripts import build_script
from test_models import profile


@unittest.skipUnless(sys.platform.startswith("linux"), "Actual PTY loader needs Linux")
class PtyTests(unittest.TestCase):
    def execute(self, script, marker, home, interrupt=False, keys=None):
        import pty
        pid, fd = pty.fork()
        if pid == 0:
            os.environ["HOME"] = str(home)
            os.execlp("bash", "bash", "--noprofile", "--norc", "-c", loader_command(script.encode(), marker))
        parser = PhaseParser(marker); output = bytearray(); phases = []; sent = False; interrupted = False
        deadline = time.monotonic() + 8; status = None; pty_closed = False
        try:
            while time.monotonic() < deadline:
                readable, _, _ = select.select([] if pty_closed else [fd], [], [], .05)
                if readable:
                    try: raw = os.read(fd, 65536)
                    except OSError as exc:
                        if exc.errno != errno.EIO: raise
                        raw = b""  # Closing all slave descriptors does not imply process exit.
                    if not raw:
                        pty_closed = True
                        continue
                    visible, events = parser.feed(raw); output.extend(visible); phases.extend(events)
                    if "ready" in events and not sent:
                        payload = script.encode()
                        while payload:
                            count = os.write(fd, payload[:4096]); payload = payload[count:]
                        sent = True
                    if keys and "interactive" in events:
                        os.write(fd, keys.encode()); keys = None
                    if interrupt and "running" in phases and not interrupted:
                        os.write(fd, b"\x03"); interrupted = True
                done, result = os.waitpid(pid, os.WNOHANG)
                if done: status = result; break
            if status is None:
                done, result = os.waitpid(pid, os.WNOHANG)
                if done: status = result
            self.assertIsNotNone(status, "PTY command must terminate before timeout")
            output.extend(parser.finish())
            code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -os.WTERMSIG(status)
            return code, bytes(output).replace(b"\r\n", b"\n"), phases
        finally:
            os.close(fd)
            if status is None:
                import signal
                try: os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
                except ProcessLookupError: pass

    def test_closed_terminal_descriptors_do_not_imply_process_has_exited(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = 'b' * 32
            code, _, _ = self.execute('exec </dev/null >/dev/null 2>&1; sleep 0.25; exit 0',
                                      marker, Path(directory))
        self.assertEqual(code, 0)

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

    def test_interactive_shell_preserves_source_environment_and_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); work = root / "work space"; work.mkdir()
            init = root / 'setup.bash'; init.write_text('export FROM_SOURCE=yes\n')
            data = profile(); data['common_init'] = f"source '{init}'\nexport A=10\ncd '{work}'"
            marker = 'a' * 32
            script = scripts.build_interactive_script(data, marker)
            keys = 'printf "VALUE=%s:%s:%s\\n" "$A" "$FROM_SOURCE" "$PWD"\nexit\n'
            code, output, phases = self.execute(script, marker, root, keys=keys)
            self.assertEqual(code, 0)
            self.assertIn(f'VALUE=10:yes:{work}'.encode(), output)
            self.assertIn('interactive', phases)

    def test_interactive_prompt_shows_user_host_and_tracks_directory_changes(self):
        import pwd
        import socket
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'work space' / 'child').mkdir(parents=True)
            data = profile(); data['common_init'] = "unset PS1; cd 'work space'"
            marker = 'c' * 32
            code, output, _ = self.execute(scripts.build_interactive_script(data, marker), marker, root,
                                           keys='cd child\nexit\n')
            self.assertEqual(code, 0)
            prefix = pwd.getpwuid(os.getuid()).pw_name + '@' + socket.gethostname().split('.')[0]
            self.assertIn((prefix + ':~/work space').encode(), output)
            self.assertIn((prefix + ':~/work space/child').encode(), output)

    def test_interactive_shell_keeps_prompt_set_by_common_init(self):
        with tempfile.TemporaryDirectory() as directory:
            data = profile(); data['common_init'] = "PS1='board-debug> '"
            marker = 'd' * 32
            code, output, _ = self.execute(scripts.build_interactive_script(data, marker), marker, Path(directory),
                                           keys='exit\n')
            self.assertEqual(code, 0)
            self.assertIn(b'board-debug> ', output)

    def test_failed_common_init_does_not_enter_interactive_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = profile(); data['common_init'] = 'false'
            marker = 'b' * 32
            code, _, phases = self.execute(scripts.build_interactive_script(data, marker), marker, Path(tmp))
            self.assertNotEqual(code, 0); self.assertNotIn('interactive', phases)

    def test_ctrl_c_interrupts_foreground_program_after_loader_restores_pty(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = profile(); data["common_init"] = "export A=10"
            window = data["windows"][0]; window["working_directory"] = ""
            window["commands"] = "sleep 30"
            marker = "f" * 32
            code, _, phases = self.execute(build_script(data, window, marker), marker, Path(tmp), interrupt=True)
            self.assertNotEqual(code, 0); self.assertIn("running", phases)
