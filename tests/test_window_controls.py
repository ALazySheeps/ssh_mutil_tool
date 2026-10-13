import os
import queue
import select
import pty
import subprocess
import tempfile
from pathlib import Path
import threading
import unittest

from ssh_multi_tool.broker import Broker, request
from ssh_multi_tool.launcher import Launcher
from test_models import profile
from ssh_multi_tool.scripts import build_script
from ssh_multi_tool.ssh import UnknownHost, trust_host
from ssh_multi_tool.terminal import helper_argv
from ssh_server import SSHServer


class WindowControlsTests(unittest.TestCase):
    def launcher(self, data=None):
        broker = Broker(); calls = []
        launch = Launcher('hosts', preflight_fn=lambda *a, **kw: None,
                          broker_factory=lambda: broker, open_fn=lambda *a: calls.append(a))
        self.addCleanup(launch.close)
        launch.start({'id': 't', 'name': 'T', 'host': 'localhost', 'port': 22, 'username': 'u'},
                     'secret', data or profile())
        return launch, broker, calls

    def test_close_waiting_window_prevents_delayed_open(self):
        data = profile(); data['windows'][0]['start_delay_seconds'] = .1
        launch, broker, calls = self.launcher(data)
        launch.close_window('a'); launch.thread.join(1)
        self.assertEqual(calls, [])
        self.assertEqual(launch.poll()['a']['state'], 'closed')

    def test_authenticated_close_survives_final_event(self):
        launch, broker, calls = self.launcher(); launch.thread.join(1)
        snapshot = request(broker.path, {'op': 'claim', 'token': calls[0][1]})
        request(broker.path, {'op': 'event', 'token': snapshot['event_token'],
                            'event': {'run_id': launch.run_id, 'task_id': 'a', 'state': 'succeeded'}})
        launch.close_window('a')
        self.assertEqual(request(broker.path, {'op': 'control', 'token': snapshot['control_token']})['close'], True)
        with self.assertRaises(ValueError):
            request(broker.path, {'op': 'control', 'token': snapshot['event_token']})
        request(broker.path, {'op': 'released', 'token': snapshot['control_token']})
        self.assertEqual(launch.poll()['a']['state'], 'closed')

    def test_restart_uses_snapshot_and_ignores_old_events(self):
        data = profile(); launch, broker, calls = self.launcher(data); launch.thread.join(1)
        old_run = launch.run_id
        data['windows'][0]['commands'] = 'changed'
        launch.restart_window('a', 'new-secret')
        self.assertEqual(len(calls), 2)
        snapshot = request(broker.path, {'op': 'claim', 'token': calls[-1][1]})
        self.assertEqual(snapshot['password'], 'new-secret')
        self.assertNotIn('changed', snapshot['script'])
        broker.events.put({'run_id': old_run, 'task_id': 'a', 'state': 'failed'})
        self.assertEqual(launch.poll()['a']['state'], 'opening')

    def test_close_all_cancels_pending_and_closes_opened(self):
        data = profile(); data['windows'].append(dict(data['windows'][0], id='b', start_delay_seconds=10))
        launch, broker, calls = self.launcher(data)
        # Synchronize with the immediate dispatch without sleeping.
        with launch.lock:
            pass
        launch.close_all(); launch.thread.join(1)
        self.assertFalse(launch.active())
        self.assertTrue(all(t['state'] == 'closed' for t in launch.poll().values()))

    def test_real_helper_closes_finished_result_screen(self):
        self._check_real_helper_close(hold=False)

    def test_real_helper_closes_running_ssh_session(self):
        self._check_real_helper_close(hold=True)

    def _check_real_helper_close(self, hold):
        broker = Broker(); self.addCleanup(broker.close)
        server = SSHServer(hold=hold); self.addCleanup(server.close)
        with tempfile.TemporaryDirectory() as tmp:
            hosts = Path(tmp) / 'hosts'
            trust_host(hosts, UnknownHost(f'[127.0.0.1]:{server.port}', server.key))
            data = profile(); marker = 'a' * 32
            token = broker.issue({'run_id': 'run', 'task_id': 'a', 'target': server.target(),
                                  'password': 'test-password', 'known_hosts': str(hosts),
                                  'script': build_script(data, data['windows'][0], marker), 'marker': marker})
            master, slave = pty.openpty()
            process = subprocess.Popen(helper_argv(broker.path, token), stdin=slave, stdout=slave, stderr=slave,
                                       start_new_session=True)
            os.close(slave)
            try:
                received = []
                while True:
                    try: event = broker.events.get(timeout=5)
                    except queue.Empty:
                        ready, _, _ = select.select([master], [], [], .1)
                        output = os.read(master, 65536) if ready else b""
                        self.fail(f"Missing helper event; received={received}; exit={process.poll()}; output={output!r}")
                    received.append(event)
                    if hold and event['state'] == 'running': break
                    if event['state'] in {'succeeded', 'failed', 'disconnected'}:
                        self.assertFalse(hold, 'running session unexpectedly ended')
                        self.assertEqual(event['state'], 'succeeded', event); break
                self.assertIsNone(process.poll(), 'result screen should wait for input')
                broker.cancel(token)
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertTrue(broker.is_closed(token))
            finally:
                if process.poll() is None: process.kill(); process.wait()
                os.close(master)

    def test_restart_waits_for_old_session_release(self):
        launch, broker, calls = self.launcher(); launch.thread.join(1)
        snapshot = request(broker.path, {'op': 'claim', 'token': calls[0][1]})
        result = []
        worker = threading.Thread(target=lambda: result.append(launch.restart_window('a', 'secret')))
        worker.start()
        try:
            # Wait until the authenticated helper receives the close command.
            import time
            deadline = time.monotonic() + 2
            while not request(broker.path, {'op': 'control', 'token': snapshot['control_token']})['close']:
                self.assertLess(time.monotonic(), deadline)
            self.assertEqual(len(calls), 1)
            request(broker.path, {'op': 'released', 'token': snapshot['control_token']})
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result, [None]); self.assertEqual(len(calls), 2)
        finally:
            launch.close_all(); worker.join(2)

    def test_close_all_during_preflight_prevents_launch(self):
        launch, broker, calls = self.launcher(); launch.thread.join(1)
        launch.close_all()
        entered = threading.Event(); release = threading.Event(); errors = []
        def preflight(*a, **kw): entered.set(); release.wait(2)
        launch.preflight_fn = preflight
        def restart():
            try: launch.restart_window('a', 'secret')
            except ValueError as exc: errors.append(str(exc))
        worker = threading.Thread(target=restart); worker.start()
        self.assertTrue(entered.wait(1)); launch.close_all(); release.set(); worker.join(2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(errors, ['重启已取消'])

    def test_close_all_includes_previous_result_windows(self):
        launch, broker, calls = self.launcher(); launch.thread.join(1)
        old = request(broker.path, {'op': 'claim', 'token': calls[0][1]})
        request(broker.path, {'op': 'event', 'token': old['event_token'],
                            'event': {'run_id': launch.run_id, 'task_id': 'a', 'state': 'succeeded'}})
        launch.poll()
        launch.start(launch.run_target, 'secret', profile()); launch.thread.join(1)
        launch.close_all()
        self.assertTrue(request(broker.path, {'op': 'control', 'token': old['control_token']})['close'])

    def test_cancelled_operation_cannot_start_a_new_run(self):
        launch, broker, calls = self.launcher(); launch.thread.join(1)
        epoch = launch.control_epoch
        launch.close_all()
        with self.assertRaisesRegex(ValueError, '启动已取消'):
            launch.start(launch.run_target, 'secret', profile(), expected_epoch=epoch)
        self.assertEqual(len(calls), 1)

    def test_crashed_helper_is_recognized_as_closed(self):
        broker = Broker(); self.addCleanup(broker.close)
        token = broker.issue({'task_id': 'a', 'run_id': 'r'})
        import sys
        code = "from ssh_multi_tool.broker import request; import sys,time; request(sys.argv[1], {'op':'claim','token':sys.argv[2]}); print('ready',flush=True); time.sleep(30)"
        process = subprocess.Popen([sys.executable, '-c', code, broker.path, token], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            process.kill(); process.wait(timeout=2)
            self.assertTrue(broker.is_closed(token))
        finally:
            if process.poll() is None: process.kill(); process.wait()
            process.stdout.close()

    def test_old_dispatcher_cleanup_does_not_cancel_new_run(self):
        entered = threading.Event(); release = threading.Event()
        class PausedLauncher(Launcher):
            def _dispatch(self, *args):
                if not entered.is_set(): entered.set(); release.wait(2)
                return super()._dispatch(*args)
        launch = PausedLauncher('hosts', preflight_fn=lambda *a: None, open_fn=lambda *a: None)
        self.addCleanup(launch.close)
        data = profile(); data['windows'][0]['start_delay_seconds'] = 30
        target = {'id': 't', 'name': 'T', 'host': 'localhost', 'port': 22, 'username': 'u'}
        launch.start(target, 'secret', data); self.assertTrue(entered.wait(1))
        old_thread = launch.thread
        launch.close_all(); launch.start(target, 'secret', data)
        release.set(); old_thread.join(1)
        self.assertEqual(launch.poll()['a']['state'], 'waiting')

    def test_closed_waiting_window_cannot_dispatch_into_new_run(self):
        launch, broker, calls = self.launcher(dict(profile(), windows=[dict(profile()['windows'][0], start_delay_seconds=.1)]))
        old_thread = launch.thread
        launch.close_window('a')
        data = profile(); data['windows'][0]['start_delay_seconds'] = 30
        launch.start(launch.run_target, 'secret', data)
        old_thread.join(1)
        self.assertEqual(calls, [])
        self.assertEqual(launch.poll()['a']['state'], 'waiting')
