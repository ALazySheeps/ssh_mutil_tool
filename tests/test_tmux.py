"""Keep-alive sessions retain their identity across local disconnects."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_multi_tool.broker import Broker, request
from ssh_multi_tool.launcher import Launcher
from ssh_multi_tool.storage import Store
from test_models import profile


class KeepAliveTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(); self.addCleanup(self.broker.close)
        self.calls = []
        self.launch = Launcher('hosts', preflight_fn=lambda *a, **kw: None,
                               broker_factory=lambda: self.broker, open_fn=lambda *a: self.calls.append(a), tmux_check_fn=lambda *a, **kw: None)
        self.addCleanup(self.launch.close)
        self.launch.keep_alive = True
        self.target = {'id': 't', 'name': 'T', 'host': 'localhost', 'port': 22, 'username': 'u'}

    def claim(self):
        return request(self.broker.path, {'op': 'claim', 'token': self.calls[-1][1]})

    def release(self, snapshot):
        request(self.broker.path, {'op': 'released', 'token': snapshot['control_token']})

    def test_mode_is_frozen_before_connection_validation(self):
        for initial in (False, True):
            with self.subTest(initial=initial):
                checked = []
                self.launch.keep_alive = initial
                self.launch.preflight_fn = lambda *a, **kw: setattr(self.launch, 'keep_alive', not initial)
                self.launch.tmux_check_fn = lambda *a, **kw: checked.append(True)
                self.launch.open_remote_terminal(self.target, 'pw')
                snapshot = self.claim()
                self.assertEqual(bool(snapshot.get('tmux_session')), initial)
                self.assertEqual(bool(checked), initial)
                self.release(snapshot)

    def test_missing_tmux_aborts_before_creating_terminal_or_management_record(self):
        def unavailable(*args, **kwargs): raise RuntimeError('目标未安装 tmux')
        self.launch.tmux_check_fn = unavailable
        with self.assertRaisesRegex(RuntimeError, '未安装 tmux'):
            self.launch.open_remote_terminal(self.target, 'pw')
        self.assertFalse(self.launch.all_tasks())
        self.assertFalse(self.calls)

    def test_manual_disconnect_keeps_record_and_reconnects_same_session(self):
        identifier = self.launch.open_remote_terminal(self.target, 'pw', profile=profile())
        first = self.claim()
        self.assertTrue(first.get('tmux_session'), 'Keep-alive snapshot must identify its remote session')
        self.launch.close_window(identifier); self.release(first)
        self.assertEqual(self.launch.poll()[identifier]['state'], 'detached')
        self.launch.keep_alive = False  # Current preferences must not change an existing session.
        self.launch.reconnect_window(identifier, 'pw')
        second = self.claim()
        self.assertEqual(second['tmux_session'], first['tmux_session'])
        self.assertEqual(self.launch.poll()[identifier]['state'], 'opening')
        self.assertNotIn('pw', repr(self.launch.shell_specs))

    def test_configured_session_is_stable_across_launcher_runs(self):
        self.launch.start_window(self.target, 'pw', profile(), 'a'); first = self.claim()
        self.assertTrue(first.get('tmux_session'))
        self.launch.close_window('a'); self.release(first)
        other = Launcher('hosts', preflight_fn=lambda *a, **kw: None,
                         broker_factory=lambda: self.broker, open_fn=lambda *a: self.calls.append(a), tmux_check_fn=lambda *a, **kw: None)
        self.addCleanup(other.close); other.keep_alive = True
        other.start_window(self.target, 'pw', profile(), 'a'); second = self.claim()
        self.assertEqual(first['tmux_session'], second['tmux_session'])

    def test_detached_configured_process_cannot_be_overwritten_by_new_run(self):
        self.launch.start_window(self.target, 'pw', profile(), 'a'); first = self.claim()
        self.launch.close_window('a'); self.release(first); self.launch.poll()
        self.assertTrue(self.launch.active(), 'Disconnected managed programs still occupy their batch')
        other = dict(self.target, id='other', host='other-host')
        with self.assertRaises(ValueError): self.launch.start_window(other, 'pw', profile(), 'a')
        self.launch.keep_alive = False
        with self.assertRaises(ValueError): self.launch.start_window(self.target, 'pw', profile(), 'a')
        self.assertEqual(self.launch.window_specs['a']['target'], self.target)
        self.assertEqual(self.launch.poll()['a']['tmux_session'], first['tmux_session'])

    def test_stopped_configuration_requires_restart_instead_of_reconnect(self):
        self.launch.start_window(self.target, 'pw', profile(), 'a'); first = self.claim()
        self.launch.close_window('a'); self.release(first); self.launch.poll()
        with patch('ssh_multi_tool.launcher.stop_session'):
            self.launch.stop_window('a', 'pw')
        self.assertEqual(self.launch.poll()['a']['state'], 'closed')
        with self.assertRaisesRegex(ValueError, '已停止'):
            self.launch.reconnect_window('a', 'pw')

    def test_stop_failure_preserves_reconnectable_record(self):
        identifier = self.launch.open_remote_terminal(self.target, 'pw'); first = self.claim()
        self.assertTrue(first.get('tmux_session'))
        self.launch.close_window(identifier); self.release(first); self.launch.poll()
        with patch('ssh_multi_tool.launcher.stop_session', side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                self.launch.stop_window(identifier, 'pw')
        self.assertEqual(self.launch.poll()[identifier]['state'], 'detached')

    def test_successful_stop_removes_manual_record(self):
        identifier = self.launch.open_remote_terminal(self.target, 'pw'); first = self.claim()
        self.assertTrue(first.get('tmux_session'))
        self.launch.close_window(identifier); self.release(first); self.launch.poll()
        with patch('ssh_multi_tool.launcher.stop_session') as stop:
            self.launch.stop_window(identifier, 'pw')
        self.assertEqual(stop.call_args.args[3], first['tmux_session'])
        self.assertNotIn(identifier, self.launch.poll())

    def test_debug_slot_retains_identity_after_client_detaches_on_result_screen(self):
        slot = {"name": "HTOP", "commands": "htop"}
        identifier = self.launch.open_debug_terminal(self.target, 'pw', profile(), slot, slot_index=0)
        first = self.claim()
        self.broker.events.put({'run_id': first['run_id'], 'task_id': identifier, 'state': 'succeeded'})
        self.launch.poll()
        self.assertEqual(self.launch.find_debug_terminal(self.target, slot, 0), identifier)
        self.release(first)

    def test_debug_button_reconnects_detached_slot_instead_of_new_session(self):
        slot = {"name": "HTOP", "commands": "htop"}
        identifier = self.launch.open_debug_terminal(self.target, 'pw', profile(), slot, slot_index=0)
        first = self.claim(); self.launch.close_window(identifier); self.release(first); self.launch.poll()
        result = self.launch.open_debug_terminal(self.target, 'pw', profile(), slot, slot_index=0)
        self.assertEqual(result, identifier)
        self.assertEqual(len(self.calls), 2, 'Detached reuse must open a new SSH attachment')
        second = self.claim()
        self.assertEqual(second['tmux_session'], first['tmux_session'])
        self.assertTrue(second.get('tmux_attach_only'))

    def test_tmux_preference_roundtrip_and_invalid_values(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)); store.save_preferences({'keep_alive': '1'})
            self.assertEqual(store.preferences()['keep_alive'], '1')
            with self.assertRaises(ValueError): store.save_preferences({'keep_alive': 'maybe'})

class TmuxScriptTests(unittest.TestCase):
    def test_attach_only_never_relaunches_a_missing_session(self):
        from ssh_multi_tool import tmux
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            # A tiny external-command fixture reports an absent session.
            binary = Path(directory) / 'tmux'; binary.write_text('#!/bin/sh\nexit 1\n'); binary.chmod(0o755)
            result = subprocess.run(['/bin/bash', '-c', tmux.build_tmux_script('touch executed', 'smt_test', 'a' * 32, attach_only=True)],
                                    env={'PATH': directory + ':/usr/bin:/bin', 'HOME': directory}, cwd=directory, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(directory) / 'executed').exists())
            self.assertIn('不存在', result.stderr.decode())

    def test_attach_only_does_not_recreate_session_lost_during_attachment(self):
        from ssh_multi_tool import tmux
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'tmux'
            binary.write_text(f"""#!/bin/bash
case "$*" in
  *has-session*) if [ ! -e '{directory}/checked' ]; then touch '{directory}/checked'; exit 0; else exit 1; fi ;;
  *new-session*) touch '{directory}/recreated'; exit 0 ;;
  *) exit 1 ;;
esac
""")
            binary.chmod(0o755)
            subprocess.run(['/bin/bash', '-c', tmux.build_tmux_script('echo payload', 'smt_test', 'a' * 32, attach_only=True)],
                           env={'PATH': directory + ':/usr/bin:/bin', 'HOME': directory}, cwd=directory, capture_output=True)
            self.assertFalse((Path(directory) / 'recreated').exists(), 'Reconnect must never create a remote session')

    def test_missing_tmux_fails_without_executing_payload(self):
        from ssh_multi_tool import tmux
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['/bin/bash', '-c', tmux.build_tmux_script('touch executed', 'smt_test', 'a' * 32)],
                                    env={'PATH': directory, 'HOME': directory}, cwd=directory, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'tmux', result.stderr)
            self.assertFalse((Path(directory) / 'executed').exists())

class ActualTmuxTests(unittest.TestCase):
    def setUp(self):
        import os
        import shutil
        executable = shutil.which('tmux') or os.environ.get('SMT_TEST_TMUX')
        if not executable: self.skipTest('tmux is required for real keep-alive verification')
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.env = dict(os.environ, HOME=str(self.home), TMUX_TMPDIR=str(self.home), TERM='xterm-256color',
                        PATH=str(Path(executable).parent) + os.pathsep + os.environ['PATH'])
        from ssh_multi_tool.tmux import TMUX
        self.tmux = TMUX
        self.addCleanup(lambda: self.command('kill-server', check=False))

    def command(self, command, check=True):
        import subprocess
        return subprocess.run(['/bin/bash', '-c', self.tmux + ' ' + command], env=self.env,
                              capture_output=True, timeout=4, check=check)

    def attach(self, payload, *, existing=False):
        import os
        import pty
        import select
        import subprocess
        import time
        from ssh_multi_tool.helper import PhaseParser, loader_command
        from ssh_multi_tool.tmux import build_tmux_script
        marker = 'a' * 32
        script = build_tmux_script(payload, 'smt_test', marker)
        master, slave = pty.openpty()
        import fcntl
        import termios
        import struct
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
        process = subprocess.Popen(['/bin/bash', '-c', loader_command(script.encode(), marker)],
                                   stdin=slave, stdout=slave, stderr=slave, env=self.env, start_new_session=True)
        os.close(slave)
        parser = PhaseParser(marker); output = bytearray(); phases = []; sent = False
        try:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if select.select([master], [], [], .05)[0]:
                    try: raw = os.read(master, 65536)
                    except OSError: break
                    visible, events = parser.feed(raw); output.extend(visible); phases.extend(events)
                    if 'ready' in events and not sent:
                        data = script.encode()
                        while data:
                            count = os.write(master, data[:4096]); data = data[count:]
                        sent = True
                    if 'interactive' in phases and self.home.joinpath('ticks').exists():
                        time.sleep(.1); break
                if process.poll() is not None: break
            self.assertIn('interactive', phases, bytes(output))
            self.assertIsNone(process.poll(), bytes(output))
        finally:
            # Losing the client's PTY models SSH connection loss. The remote server survives.
            os.close(master)
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2)

    def test_disconnect_keeps_program_and_reattach_does_not_repeat_init(self):
        import time
        payload = f'''printf 'init\\n' >> {self.home}/init
export VALUE=inherited
while true; do printf "$VALUE\\n" >> {self.home}/ticks; sleep .05; done
'''
        self.attach(payload)
        before = self.home.joinpath('ticks').stat().st_size
        time.sleep(.2)
        self.assertGreater(self.home.joinpath('ticks').stat().st_size, before)
        self.command('has-session -t =smt_test')
        self.attach(f'touch {self.home}/executed_again', existing=True)
        self.assertFalse(self.home.joinpath('executed_again').exists())
        self.assertEqual(self.home.joinpath('init').read_text(), 'init\n')
        self.command('kill-session -t =smt_test')
        time.sleep(.1); size = self.home.joinpath('ticks').stat().st_size; time.sleep(.2)
        self.assertEqual(self.home.joinpath('ticks').stat().st_size, size)

    def test_stop_over_real_ssh_kills_only_the_named_session(self):
        import subprocess
        import time
        from ssh_server import SSHServer
        from ssh_multi_tool.ssh import UnknownHost, trust_host
        from ssh_multi_tool.tmux import stop_session, check_tmux
        env = dict(self.env)
        class ExecutionServer(SSHServer):
            def execute(self, channel, command):
                result = subprocess.run(['/bin/bash', '-c', command.decode()], env=env, capture_output=True, timeout=5)
                if result.stdout: channel.sendall(result.stdout)
                if result.stderr: channel.sendall_stderr(result.stderr)
                channel.send_exit_status(result.returncode); channel.close()
        self.attach(f'while true; do echo tick >> {self.home}/ticks; sleep .05; done')
        self.command("new-session -d -s smt_other 'sleep 30'")
        server = ExecutionServer(); self.addCleanup(server.close)
        hosts = self.home / 'hosts'
        trust_host(hosts, UnknownHost(f'[127.0.0.1]:{server.port}', server.key))
        check_tmux(server.target(), 'test-password', hosts)
        stop_session(server.target(), 'test-password', hosts, 'smt_test')
        self.assertNotEqual(self.command('has-session -t =smt_test', check=False).returncode, 0)
        self.command('has-session -t =smt_other')
        stop_session(server.target(), 'test-password', hosts, 'smt_test')  # Idempotent absence.
        env['PATH'] = '/nonexistent'
        with self.assertRaisesRegex(RuntimeError, '未安装 tmux'):
            check_tmux(server.target(), 'test-password', hosts)
        time.sleep(.1); size = self.home.joinpath('ticks').stat().st_size; time.sleep(.15)
        self.assertEqual(self.home.joinpath('ticks').stat().st_size, size)

    def test_large_script_and_finished_output_are_preserved(self):
        payload = '#' + 'x' * (150 * 1024) + f'\nprintf done > {self.home}/ticks\nprintf RESULT_OK\\n\n'
        self.attach(payload)
        self.assertEqual(self.home.joinpath('ticks').read_text(), 'done')
        result = self.command('capture-pane -p -S - -t =smt_test:0.0')
        self.assertIn(b'RESULT_OK', result.stdout)
        self.assertEqual(self.command("display-message -p -t =smt_test:0.0 '#{pane_dead}'").stdout.strip(), b'1')
