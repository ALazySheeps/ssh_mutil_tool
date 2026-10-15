"""Compatibility behavior for older Python and Ubuntu installations."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ssh_multi_tool import broker


class CompatibilityTests(unittest.TestCase):
    def test_helper_death_is_detected_without_python_pidfd_api(self):
        vault = broker.TokenVault()
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            with patch.object(broker.os, 'pidfd_open', side_effect=OSError('unavailable'), create=True):
                token = vault.issue({'run_id': 'r', 'task_id': 'w'})
                vault.claim(token, pid=process.pid)
                self.assertFalse(vault.is_closed(token))
                process.terminate(); process.wait(timeout=3)
                self.assertTrue(vault.is_closed(token), 'Dead helpers must release management records on Python 3.8')
        finally:
            if process.poll() is None: process.kill(); process.wait()
            vault.close()

    def test_os_release_parser_preserves_quoted_values_without_executing_them(self):
        from ssh_multi_tool.compat import platform_tag, read_os_release
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory) / 'os-release'
            release.write_text('# comment\nID=ubuntu\nVERSION_ID="20.04"\nNAME="Ubuntu Linux"\nEVIL="$(touch injected)"\n', encoding='utf-8')
            data = read_os_release(release)
            self.assertEqual(data['NAME'], 'Ubuntu Linux')
            self.assertEqual(data['EVIL'], '$(touch injected)')
            self.assertEqual(platform_tag(release), 'ubuntu20.04')
            self.assertFalse((Path(directory) / 'injected').exists())

    def test_pid_reuse_or_zombie_does_not_keep_old_session_alive(self):
        from ssh_multi_tool.compat import process_identity, process_is_alive
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory); (proc / '42').mkdir()
            stat = proc / '42/stat'
            def write(state, started):
                fields = [state] + ['0'] * 18 + [str(started)]
                stat.write_text('42 (a tricky ) process) ' + ' '.join(fields), encoding='utf-8')
            write('S', 123)
            identity = process_identity(42, proc)
            self.assertTrue(process_is_alive(42, identity, proc))
            write('S', 124); self.assertFalse(process_is_alive(42, identity, proc))
            write('Z', 123); self.assertFalse(process_is_alive(42, identity, proc))
