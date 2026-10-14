"""Bulk tmux controls act on each endpoint and retain unsuccessful sessions."""
import threading
import unittest
from unittest.mock import patch

from ssh_multi_tool.broker import Broker, request
from ssh_multi_tool.launcher import Launcher
from ssh_multi_tool.terminal import target_key
from test_models import profile


class BulkTmuxTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(); self.addCleanup(self.broker.close)
        self.calls = []
        self.launch = Launcher('hosts', preflight_fn=lambda *a, **kw: None,
            tmux_check_fn=lambda *a, **kw: None, broker_factory=lambda: self.broker,
            open_fn=lambda *a: self.calls.append(a))
        self.addCleanup(self.launch.close); self.launch.keep_alive = True
        self.target = {'id': 't', 'name': 'T', 'host': 'board-a', 'port': 22, 'username': 'u'}
        self.other = dict(self.target, id='other', name='Other', host='board-b')

    def detached(self, target, *, configured=False):
        if configured: self.launch.start_window(target, 'pw', profile(), 'a'); identifier = 'a'
        else: identifier = self.launch.open_remote_terminal(target, 'pw')
        snapshot = request(self.broker.path, {'op': 'claim', 'token': self.calls[-1][1]})
        self.launch.close_window(identifier)
        request(self.broker.path, {'op': 'released', 'token': snapshot['control_token']})
        self.launch.poll()
        return identifier, snapshot

    def test_stop_waits_for_attachment_release_before_remote_kill(self):
        identifier = self.launch.open_remote_terminal(self.target, 'pw')
        snapshot = request(self.broker.path, {'op': 'claim', 'token': self.calls[-1][1]})
        killed = threading.Event(); errors = []
        def stop():
            try: self.launch.stop_window(identifier, 'pw')
            except Exception as exc: errors.append(exc)
        with patch('ssh_multi_tool.launcher.stop_session', side_effect=lambda *a, **kw: killed.set()):
            worker = threading.Thread(target=stop); worker.start()
            import time
            deadline = time.monotonic() + 1
            while not request(self.broker.path, {'op': 'control', 'token': snapshot['control_token']})['close']:
                self.assertLess(time.monotonic(), deadline); time.sleep(.01)
            self.assertFalse(killed.is_set(), 'An attachment could still create a session after the stop request')
            request(self.broker.path, {'op': 'released', 'token': snapshot['control_token']})
            worker.join(2)
        self.assertFalse(errors); self.assertTrue(killed.is_set())

    def test_bulk_stop_needs_no_reconnect_and_releases_configuration(self):
        self.detached(self.target, configured=True)
        manual, _ = self.detached(self.other)
        seen = []
        def stop(target, password, *args, **kw): seen.append((target_key(target), password))
        with patch('ssh_multi_tool.launcher.stop_session', side_effect=stop):
            report = self.launch.control_tmux_all({target_key(self.target): 'a-password', target_key(self.other): 'b-password'}, stopping=True)
        self.assertEqual(set(report['stopped']), {'a', manual})
        self.assertFalse(report['failed'])
        self.assertEqual(set(seen), {(target_key(self.target), 'a-password'), (target_key(self.other), 'b-password')})
        self.assertFalse(self.launch.active())
        self.assertNotIn(manual, self.launch.poll())
        self.assertEqual(len(self.calls), 2, 'Stopping must not open reconnect terminals')

    def test_bulk_stop_continues_after_failure_and_keeps_failed_record(self):
        first, _ = self.detached(self.target); second, _ = self.detached(self.other)
        def stop(target, *a, **kw):
            if target_key(target) == target_key(self.target): raise RuntimeError('offline')
        with patch('ssh_multi_tool.launcher.stop_session', side_effect=stop):
            report = self.launch.control_tmux_all({target_key(self.target): 'pw', target_key(self.other): 'pw'}, stopping=True)
        self.assertIn(first, report['failed']); self.assertIn(second, report['stopped'])
        self.assertEqual(self.launch.poll()[first]['state'], 'detached')
        self.assertNotIn(second, self.launch.poll())

    def test_reconnect_skips_connected_sessions_and_reuses_detached_identity(self):
        first, snapshot = self.detached(self.target)
        active = self.launch.open_remote_terminal(self.other, 'pw')
        self.launch.shell_tasks[active]['state'] = 'interactive'; self.launch.shell_tasks[active]['last_seen'] = __import__('time').monotonic()
        report = self.launch.control_tmux_all({target_key(self.target): 'pw'})
        self.assertIn(first, report['requested']); self.assertIn(active, report['skipped'])
        new = request(self.broker.path, {'op': 'claim', 'token': self.calls[-1][1]})
        self.assertEqual(new['tmux_session'], snapshot['tmux_session'])
        self.assertTrue(new['tmux_attach_only'])
        self.assertNotIn('pw', repr(report))

    def test_board_without_password_keeps_its_existing_connection(self):
        self.detached(self.target)
        active = self.launch.open_remote_terminal(self.other, 'pw')
        snapshot = request(self.broker.path, {'op': 'claim', 'token': self.calls[-1][1]})
        self.broker.events.put({'run_id': snapshot['run_id'], 'task_id': active, 'state': 'interactive'})
        self.launch.poll()
        with patch('ssh_multi_tool.launcher.stop_session'):
            report = self.launch.control_tmux_all({target_key(self.target): 'pw'}, stopping=True)
        self.assertIn(active, report['failed'])
        self.assertFalse(request(self.broker.path, {'op': 'control', 'token': snapshot['control_token']})['close'])
        self.assertEqual(self.launch.poll()[active]['state'], 'interactive')
        request(self.broker.path, {'op': 'released', 'token': snapshot['control_token']})

    def test_missing_password_does_not_use_another_boards_password(self):
        first, _ = self.detached(self.target); second, _ = self.detached(self.other)
        with patch('ssh_multi_tool.launcher.stop_session'):
            report = self.launch.control_tmux_all({target_key(self.target): 'pw'}, stopping=True)
        self.assertIn(first, report['stopped']); self.assertIn(second, report['failed'])
        self.assertIn(second, self.launch.poll())

    def test_bulk_tmux_stop_preserves_ordinary_delayed_tasks_in_mixed_batch(self):
        import time
        data = profile()
        data['windows'].append(dict(data['windows'][0], id='b', name='B', start_delay_seconds=60))
        self.launch.keep_alive = False; self.launch.start(self.target, 'pw', data)
        deadline = time.monotonic() + 1
        while not self.calls:
            self.assertLess(time.monotonic(), deadline); time.sleep(.01)
        data['windows'].append(dict(data['windows'][0], id='c', name='C'))
        self.launch.keep_alive = True; self.launch.start_window(self.target, 'pw', data, 'c')
        with patch('ssh_multi_tool.launcher.stop_session'):
            report = self.launch.control_tmux_all({target_key(self.target): 'pw'}, stopping=True)
        self.launch.thread.join(.1)
        self.assertIn('c', report['stopped'])
        self.assertEqual(self.launch.poll()['b']['state'], 'waiting')
        self.assertEqual(len(self.calls), 2)

    def test_stops_waiting_tmux_tasks_and_prevents_delayed_launch(self):
        data = profile(); data['windows'][0]['start_delay_seconds'] = 60
        self.launch.start(self.target, 'pw', data)
        with patch('ssh_multi_tool.launcher.stop_session'):
            report = self.launch.control_tmux_all({target_key(self.target): 'pw'}, stopping=True)
        self.assertIn('a', report['stopped']); self.assertFalse(self.launch.active())
        self.launch.thread.join(.3)
        self.assertFalse(self.launch.thread.is_alive(), 'Bulk stop must cancel the delayed dispatcher')
        self.assertFalse(self.calls)

    def test_bulk_reconnect_attempts_cancelled_delayed_tmux_spec_with_attach_only(self):
        data = profile(); data['windows'][0]['start_delay_seconds'] = 60
        self.launch.start(self.target, 'pw', data); self.launch.close_all(); self.launch.thread.join(1)
        self.assertIn('a', self.launch.tmux_windows(reconnect_only=True))
        report = self.launch.control_tmux_all({target_key(self.target): 'pw'})
        self.assertIn('a', report['requested'])
        snapshot = request(self.broker.path, {'op': 'claim', 'token': self.calls[-1][1]})
        self.assertTrue(snapshot['tmux_attach_only'])
        self.assertTrue(snapshot['tmux_session'])

    def test_disconnect_cancels_remaining_bulk_items(self):
        first, _ = self.detached(self.target); second, _ = self.detached(self.target)
        entered = threading.Event(); release = threading.Event(); reports = []
        def stop(*a, **kw): entered.set(); release.wait(2)
        with patch('ssh_multi_tool.launcher.stop_session', side_effect=stop):
            worker = threading.Thread(target=lambda: reports.append(self.launch.control_tmux_all({target_key(self.target): 'pw'}, stopping=True)))
            worker.start(); self.assertTrue(entered.wait(1))
            self.launch.close_all(); release.set(); worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertIn(second, reports[0]['failed'])
        self.assertIn(second, self.launch.poll())
