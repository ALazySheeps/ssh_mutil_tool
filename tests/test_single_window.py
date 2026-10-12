import unittest
import threading
from ssh_multi_tool.broker import Broker, request
from ssh_multi_tool.launcher import Launcher
from test_models import profile


class SingleWindowTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(); self.addCleanup(self.broker.close); self.calls = []
        self.launcher = Launcher('hosts', preflight_fn=lambda *a, **kw: None,
                                 broker_factory=lambda: self.broker, open_fn=lambda *a: self.calls.append(a))
        self.addCleanup(self.launcher.close)
        self.target = {'id': 't', 'name': 'T', 'host': 'localhost', 'port': 22, 'username': 'u'}

    def test_start_only_selected_window_immediately(self):
        data = profile(); data['windows'].append(dict(data['windows'][0], id='b', start_delay_seconds=30))
        self.launcher.start_window(self.target, 'secret', data, 'b')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(set(self.launcher.tasks), {'b'})
        snapshot = request(self.broker.path, {'op': 'claim', 'token': self.calls[0][1]})
        self.assertIn(data['common_init'], snapshot['script'])
        self.assertEqual(snapshot['task_id'], 'b')

    def test_each_window_restart_keeps_its_own_config_snapshot(self):
        data = profile(); data['windows'].append(dict(data['windows'][0], id='b'))
        self.launcher.start_window(self.target, 'secret', data, 'a')
        data['common_init'] = 'export A=20'
        self.launcher.start_window(self.target, 'secret', data, 'b')
        self.assertEqual(len(self.calls), 2)
        self.launcher.restart_window('a', 'secret')
        old = request(self.broker.path, {'op': 'claim', 'token': self.calls[-1][1]})
        self.assertIn('export A=10', old['script']); self.assertNotIn('export A=20', old['script'])
        with self.assertRaisesRegex(ValueError, '活动'):
            self.launcher.start_window(self.target, 'secret', data, 'b')
        self.assertEqual(len(self.calls), 3)

    def test_disabled_or_missing_window_cannot_start(self):
        data = profile(); data['windows'].append(dict(data['windows'][0], id='b', enabled=False))
        for identifier in ('b', 'missing'):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.launcher.start_window(self.target, 'secret', data, identifier)
        self.assertEqual(self.calls, [])

    def test_stale_operation_does_not_launch(self):
        epoch = self.launcher.control_epoch; self.launcher.close_all()
        with self.assertRaises(ValueError):
            self.launcher.start_window(self.target, 'secret', profile(), 'a', expected_epoch=epoch)
        self.assertEqual(self.calls, [])

    def test_close_all_during_single_window_preflight_cancels_start(self):
        entered = threading.Event(); release = threading.Event(); errors = []
        def preflight(*a, **kw): entered.set(); release.wait(2)
        self.launcher.preflight_fn = preflight
        def start():
            try: self.launcher.start_window(self.target, 'secret', profile(), 'a')
            except ValueError as exc: errors.append(str(exc))
        worker = threading.Thread(target=start); worker.start()
        self.assertTrue(entered.wait(1)); self.launcher.close_all(); release.set(); worker.join(2)
        self.assertEqual(errors, ['启动已取消']); self.assertEqual(self.calls, [])
        self.assertFalse(self.launcher.active())
