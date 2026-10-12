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
