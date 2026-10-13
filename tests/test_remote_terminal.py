import io
import os
import pty
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ssh_multi_tool.broker import Broker, request
from ssh_multi_tool.helper import run_session
from ssh_multi_tool.launcher import Launcher
from ssh_multi_tool.scripts import build_interactive_script
from ssh_multi_tool.ssh import UnknownHost, trust_host
from ssh_multi_tool.terminal import helper_argv
from ssh_server import SSHServer
from test_models import profile


class RemoteTerminalTests(unittest.TestCase):
    def launcher(self):
        broker = Broker(); calls = []; self.addCleanup(broker.close)
        launch = Launcher('hosts', preflight_fn=lambda *a, **kw: None,
                          broker_factory=lambda: broker, open_fn=lambda *a: calls.append(a))
        self.addCleanup(launch.close)
        target = {'id': 't', 'name': 'T', 'host': 'localhost', 'port': 22, 'username': 'u'}
        return launch, broker, calls, target

    def test_open_without_profile_and_restart_original_target(self):
        launch, broker, calls, target = self.launcher()
        identifier = launch.open_remote_terminal(target, 'secret')
        first = request(broker.path, {'op': 'claim', 'token': calls[0][1]})
        self.assertEqual(first['mode'], 'shell')
        self.assertEqual(first['target'], target)
        self.assertNotIn('secret', repr(launch.shell_specs))
        broker.events.put({'run_id': first['run_id'], 'task_id': identifier, 'state': 'succeeded'})
        request(broker.path, {'op': 'released', 'token': first['control_token']})
        target['host'] = 'changed'
        launch.restart_window(identifier, 'new-secret')
        second = request(broker.path, {'op': 'claim', 'token': calls[-1][1]})
        self.assertEqual(second['mode'], 'shell')
        self.assertEqual(second['target']['host'], 'localhost')
        self.assertEqual(second['password'], 'new-secret')
        self.assertEqual(launch.poll()[identifier]['state'], 'opening')

    def test_remote_terminal_inherits_selected_profile_and_restart_snapshot(self):
        launch, broker, calls, target = self.launcher(); data = profile()
        identifier = launch.open_remote_terminal(target, 'secret', profile=data)
        first = request(broker.path, {'op': 'claim', 'token': calls[-1][1]})
        self.assertIn('export A=10', first['script'])
        self.assertNotIn(data['windows'][0]['commands'], first['script'])
        self.assertIn('bash', first['script'])
        request(broker.path, {'op': 'released', 'token': first['control_token']})
        data['common_init'] = 'export A=20'
        launch.restart_window(identifier, 'secret')
        second = request(broker.path, {'op': 'claim', 'token': calls[-1][1]})
        self.assertIn('export A=10', second['script']); self.assertNotIn('export A=20', second['script'])

    def test_remote_terminals_coexist_with_multiple_batch_runs(self):
        launch, broker, calls, target = self.launcher()
        identifier = launch.open_remote_terminal(target, 'secret')
        launch.start(target, 'secret', profile()); launch.thread.join(1)
        self.assertIn(identifier, launch.poll())
        other = dict(target, id='other', name='Other', host='other-host')
        second = launch.open_remote_terminal(other, 'secret')
        self.assertEqual(launch.poll()['a']['state'], 'opening')
        self.assertIn(second, launch.poll())
        launch.close_window('a')
        launch.start(target, 'secret', profile()); launch.thread.join(1)
        self.assertIn(identifier, launch.poll()); self.assertIn(second, launch.poll())
        launch.close_all()
        self.assertTrue(all(t['state'] == 'closed' for t in launch.poll().values()))

    def test_close_all_during_remote_preflight_prevents_open(self):
        launch, broker, calls, target = self.launcher()
        entered = threading.Event(); release = threading.Event(); errors = []
        def preflight(*a, **kw): entered.set(); release.wait(2)
        launch.preflight_fn = preflight
        def open_window():
            try: launch.open_remote_terminal(target, 'secret')
            except ValueError as exc: errors.append(str(exc))
        worker = threading.Thread(target=open_window); worker.start()
        self.assertTrue(entered.wait(1)); launch.close_all(); release.set(); worker.join(2)
        self.assertEqual(calls, []); self.assertEqual(errors, ['启动已取消'])

    def test_remote_authentication_failure_does_not_open(self):
        launch, broker, calls, target = self.launcher()
        def fail(*a, **kw): raise ValueError('bad password')
        launch.preflight_fn = fail
        with self.assertRaisesRegex(ValueError, 'bad password'): launch.open_remote_terminal(target, 'secret')
        self.assertEqual(calls, [])

    def test_real_ssh_shell_reports_output_without_script(self):
        server = SSHServer(); self.addCleanup(server.close)
        with tempfile.TemporaryDirectory() as tmp:
            hosts = Path(tmp) / 'hosts'
            trust_host(hosts, UnknownHost(f'[127.0.0.1]:{server.port}', server.key))
            snapshot = {'target': server.target(), 'password': 'test-password', 'known_hosts': str(hosts),
                        'mode': 'shell', 'marker': 'a' * 32, 'script': ''}
            events = []; output = io.BytesIO()
            self.assertEqual(run_session(snapshot, events.append, output=output, interactive=False), 0)
            self.assertEqual(output.getvalue(), server.output)
            self.assertEqual([e['state'] for e in events], ['connecting', 'interactive', 'succeeded'])
            self.assertEqual(server.command, b''); self.assertEqual(server.script, b'')

    def test_profile_shell_uses_script_loader_and_reports_interactive(self):
        server = SSHServer(phase='interactive'); self.addCleanup(server.close)
        with tempfile.TemporaryDirectory() as tmp:
            hosts = Path(tmp) / 'hosts'
            trust_host(hosts, UnknownHost(f'[127.0.0.1]:{server.port}', server.key))
            marker = 'c' * 32; script = build_interactive_script(profile(), marker)
            snapshot = {'target': server.target(), 'password': 'test-password', 'known_hosts': str(hosts),
                        'mode': 'shell', 'marker': marker, 'script': script}
            events = []; output = io.BytesIO()
            self.assertEqual(run_session(snapshot, events.append, output=output, interactive=False), 0)
            self.assertEqual(server.script, script.encode())
            self.assertEqual([e['state'] for e in events], ['connecting', 'initializing', 'interactive', 'succeeded'])
            self.assertEqual(output.getvalue(), server.output)

    def test_real_shell_accepts_input_and_closes_through_broker(self):
        server = SSHServer(hold=True); self.addCleanup(server.close)
        broker = Broker(); self.addCleanup(broker.close)
        with tempfile.TemporaryDirectory() as tmp:
            hosts = Path(tmp) / 'hosts'
            trust_host(hosts, UnknownHost(f'[127.0.0.1]:{server.port}', server.key))
            token = broker.issue({'target': server.target(), 'password': 'test-password', 'known_hosts': str(hosts),
                                  'mode': 'shell', 'marker': 'a' * 32, 'script': '', 'run_id': 'r', 'task_id': 's'})
            master, slave = pty.openpty()
            process = subprocess.Popen(helper_argv(broker.path, token), stdin=slave, stdout=slave, stderr=slave,
                                       start_new_session=True)
            os.close(slave)
            try:
                deadline = time.monotonic() + 5
                while True:
                    self.assertLess(time.monotonic(), deadline, "shell must connect before timeout")
                    event = broker.events.get(timeout=5)
                    if event['state'] == 'interactive': break
                    self.assertNotEqual(event['state'], 'failed', event)
                os.write(master, b'echo hello\n')
                self.assertTrue(server.shell_received.wait(3))
                self.assertIn(b'echo hello', server.shell_input)
                broker.cancel(token)
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertTrue(broker.is_closed(token))
            finally:
                if process.poll() is None: process.kill(); process.wait()
                os.close(master)

    def test_closed_manual_terminal_is_removed_with_its_snapshot(self):
        launch, broker, calls, target = self.launcher()
        identifier = launch.open_remote_terminal(target, 'secret')
        launch.close_window(identifier)
        self.assertNotIn(identifier, launch.poll())
        self.assertNotIn(identifier, launch.shell_tasks)
        self.assertNotIn(identifier, launch.shell_specs)

    def test_result_screen_is_kept_until_helper_releases_window(self):
        launch, broker, calls, target = self.launcher()
        identifier = launch.open_remote_terminal(target, 'secret')
        snapshot = request(broker.path, {'op': 'claim', 'token': calls[-1][1]})
        broker.events.put({'run_id': snapshot['run_id'], 'task_id': identifier, 'state': 'succeeded'})
        self.assertIn(identifier, launch.poll())
        request(broker.path, {'op': 'released', 'token': snapshot['control_token']})
        self.assertNotIn(identifier, launch.poll())
        broker.events.put({'run_id': snapshot['run_id'], 'task_id': identifier, 'state': 'interactive'})
        self.assertNotIn(identifier, launch.poll())

    def test_poll_does_not_remove_window_during_restart(self):
        launch, broker, calls, target = self.launcher()
        identifier = launch.open_remote_terminal(target, 'secret')
        snapshot = request(broker.path, {'op': 'claim', 'token': calls[-1][1]})
        entered = threading.Event(); release = threading.Event(); errors = []
        def preflight(*a, **kw): entered.set(); release.wait(2)
        launch.preflight_fn = preflight
        def restart():
            try: launch.restart_window(identifier, 'secret')
            except Exception as exc: errors.append(exc)
        worker = threading.Thread(target=restart); worker.start()
        try:
            self.assertTrue(entered.wait(1))
            request(broker.path, {'op': 'released', 'token': snapshot['control_token']})
            self.assertIn(identifier, launch.poll())
            release.set(); worker.join(2)
            self.assertEqual(errors, []); self.assertEqual(len(calls), 2)
            self.assertEqual(launch.poll()[identifier]['state'], 'opening')
        finally: release.set(); worker.join(2)

    def test_open_failure_reports_error_before_auto_cleanup(self):
        launch, broker, calls, target = self.launcher()
        def fail(*a): raise OSError('terminal unavailable')
        launch.open_fn = fail
        with self.assertRaisesRegex(RuntimeError, '连接或本机资源失败'):
            launch.open_remote_terminal(target, 'secret')
        self.assertEqual(launch.poll(), {})
        self.assertEqual(launch.shell_specs, {})

    def test_external_helper_exit_removes_manual_record(self):
        launch, broker, calls, target = self.launcher()
        identifier = launch.open_remote_terminal(target, 'secret')
        import sys
        code = "from ssh_multi_tool.broker import request; import sys,time; request(sys.argv[1], {'op':'claim','token':sys.argv[2]}); print('ready',flush=True); time.sleep(30)"
        process = subprocess.Popen([sys.executable, '-c', code, broker.path, calls[-1][1]], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            self.assertIn(identifier, launch.poll())
            process.kill(); process.wait(timeout=2)
            self.assertNotIn(identifier, launch.poll())
            self.assertNotIn(identifier, launch.shell_specs)
        finally:
            if process.poll() is None: process.kill(); process.wait()
            process.stdout.close()

    def test_close_all_remains_responsive_during_terminal_launch(self):
        launch, broker, calls, target = self.launcher()
        entered = threading.Event(); release = threading.Event(); closed = threading.Event(); errors = []
        def opening(*a): entered.set(); release.wait(3)
        launch.open_fn = opening
        def start():
            try: launch.open_remote_terminal(target, 'secret')
            except ValueError as exc: errors.append(str(exc))
        worker = threading.Thread(target=start); worker.start()
        closer = threading.Thread(target=lambda: (launch.close_all(), closed.set()))
        try:
            self.assertTrue(entered.wait(1)); closer.start()
            self.assertTrue(closed.wait(1), 'close-all must not wait for desktop IPC')
            self.assertEqual(launch.poll(), {})
        finally:
            release.set(); worker.join(3)
            if closer.ident is not None: closer.join(3)

    def test_old_desktop_launch_failure_cannot_overwrite_restarted_session(self):
        launch, broker, calls, target = self.launcher()
        entered = threading.Event(); release = threading.Event(); errors = []
        def opening(*args):
            calls.append(args)
            if len(calls) == 1:
                entered.set(); release.wait(3); raise OSError('old launch failed')
        launch.open_fn = opening
        def start():
            try: launch.open_remote_terminal(target, 'secret')
            except RuntimeError as exc: errors.append(str(exc))
        worker = threading.Thread(target=start); worker.start()
        try:
            self.assertTrue(entered.wait(1)); identifier = next(iter(launch.shell_tasks))
            launch.close_window(identifier); launch.restart_window(identifier, 'secret')
            current_token = launch.shell_tasks[identifier]['token']
            release.set(); worker.join(3)
            self.assertTrue(errors)
            task = launch.poll()[identifier]
            self.assertEqual(task['token'], current_token)
            self.assertEqual(task['state'], 'opening')
        finally: release.set(); worker.join(3)
