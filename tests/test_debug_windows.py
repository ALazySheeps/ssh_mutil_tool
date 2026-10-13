import tempfile
import unittest
from pathlib import Path

from ssh_multi_tool import settings
from ssh_multi_tool.broker import Broker, request
from ssh_multi_tool.launcher import Launcher
from ssh_multi_tool.storage import Store
from test_models import profile


class DebugWindowsTests(unittest.TestCase):
    def test_four_slots_default_and_persist_outside_profiles(self):
        slots = settings.debug_slots({})
        self.assertEqual(len(slots), 4)
        self.assertEqual(slots[0]['commands'], 'htop')
        self.assertTrue(all(not slot['commands'] for slot in slots[1:]))
        slots[1] = {'name': '日志', 'commands': 'journalctl -f\necho done'}
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)); data = profile(); store.save_profile(data)
            store.save_preferences(dict(settings.debug_preferences(slots), last_profile_id=data['id']))
            self.assertEqual(settings.debug_slots(Store(Path(tmp)).preferences()), slots)
            self.assertEqual(store.load_profile(data['id']), data)

    def test_invalid_slots_are_rejected(self):
        slots = settings.debug_slots({})
        for bad in (slots[:3], slots + [slots[0]], [dict(slots[0], name='')] + slots[1:],
                    [dict(slots[0], commands='bad\x00')] + slots[1:]):
            with self.subTest(bad=bad), self.assertRaises(ValueError): settings.debug_preferences(bad)

    def launcher(self):
        broker = Broker(); self.addCleanup(broker.close); calls = []
        launch = Launcher('hosts', preflight_fn=lambda *a, **kw: None,
                          broker_factory=lambda: broker, open_fn=lambda *a: calls.append(a))
        self.addCleanup(launch.close)
        return launch, broker, calls

    def test_debug_slot_inherits_current_init_and_runs_only_debug_command(self):
        launch, broker, calls = self.launcher(); data = profile()
        target = {'id': 't', 'name': 'T', 'host': 'localhost', 'port': 22, 'username': 'u'}
        slot = {'name': 'HTOP', 'commands': 'htop'}
        identifier = launch.open_debug_terminal(target, 'secret', data, slot)
        first = request(broker.path, {'op': 'claim', 'token': calls[-1][1]})
        self.assertIn(data['common_init'], first['script'])
        self.assertIn('htop', first['script'])
        self.assertNotIn(data['windows'][0]['commands'], first['script'])
        self.assertLess(first['script'].index(data['common_init']), first['script'].index('htop'))
        self.assertIn('HTOP', calls[-1][2])
        request(broker.path, {'op': 'released', 'token': first['control_token']})
        slot['commands'] = 'changed'; data['common_init'] = 'changed-init'
        launch.restart_window(identifier, 'new-secret')
        second = request(broker.path, {'op': 'claim', 'token': calls[-1][1]})
        self.assertIn('htop', second['script']); self.assertIn('export A=10', second['script'])
        self.assertNotIn('changed', second['script'])
        launch.close_all(); self.assertEqual(launch.poll()[identifier]['state'], 'closing')
        request(broker.path, {'op': 'released', 'token': second['control_token']})
        self.assertEqual(launch.poll(), {})

    def test_same_slot_works_on_multiple_targets(self):
        launch, broker, calls = self.launcher(); data = profile(); slot = {'name': 'HTOP', 'commands': 'htop'}
        targets = [{'id': name, 'name': name, 'host': name, 'port': 22, 'username': 'u'} for name in ('board-a', 'board-b')]
        identifiers = [launch.open_debug_terminal(target, 'secret', data, slot) for target in targets]
        snapshots = [request(broker.path, {'op': 'claim', 'token': call[1]}) for call in calls]
        self.assertNotEqual(*identifiers)
        self.assertEqual([s['target']['host'] for s in snapshots], ['board-a', 'board-b'])
        self.assertTrue(all('htop' in s['script'] for s in snapshots))
        self.assertNotIn('secret', repr(launch.shell_specs))

    def test_empty_slot_cannot_launch(self):
        launch, broker, calls = self.launcher()
        target = {'id': 't', 'name': 'T', 'host': 'localhost', 'port': 22, 'username': 'u'}
        with self.assertRaises(ValueError):
            launch.open_debug_terminal(target, 'secret', profile(), {'name': 'Empty', 'commands': ''})
        self.assertEqual(calls, [])

    def test_debug_slot_reuses_existing_session_without_authentication(self):
        launch, broker, calls = self.launcher(); data = profile()
        target = {'id': 't', 'name': 'T', 'host': 'localhost', 'port': 22, 'username': 'u'}
        slot = {'name': 'HTOP', 'commands': 'htop'}
        first = launch.open_debug_terminal(target, 'secret', data, slot, slot_index=0)
        def fail(*a, **kw): raise AssertionError('reuse must not authenticate or execute again')
        launch.preflight_fn = fail
        slot['commands'] = 'edited'; data['common_init'] = 'edited-init'
        second = launch.open_debug_terminal(target, '', data, slot, slot_index=0)
        self.assertEqual(first, second); self.assertEqual(len(calls), 1)
        self.assertEqual(launch.shell_specs[first]['debug_slot']['commands'], 'htop')

    def test_explicit_new_debug_session_and_reopen_after_close(self):
        launch, broker, calls = self.launcher(); data = profile()
        target = {'id': 't', 'name': 'T', 'host': 'localhost', 'port': 22, 'username': 'u'}
        slot = {'name': 'HTOP', 'commands': 'htop'}
        first = launch.open_debug_terminal(target, 'secret', data, slot, slot_index=0)
        second = launch.open_debug_terminal(target, 'secret', data, slot, slot_index=0, force_new=True)
        self.assertNotEqual(first, second); self.assertEqual(len(calls), 2)
        launch.close_all(); launch.poll()
        third = launch.open_debug_terminal(target, 'secret', data, slot, slot_index=0)
        self.assertNotIn(third, (first, second)); self.assertEqual(len(calls), 3)

    def test_reuse_skips_closing_session_when_another_is_active(self):
        launch, broker, calls = self.launcher(); data = profile()
        target = {'id': 't', 'name': 'T', 'host': 'localhost', 'port': 22, 'username': 'u'}
        slot = {'name': 'HTOP', 'commands': 'htop'}
        first = launch.open_debug_terminal(target, 'secret', data, slot, slot_index=0)
        snapshot = request(broker.path, {'op': 'claim', 'token': calls[0][1]})
        second = launch.open_debug_terminal(target, 'secret', data, slot, slot_index=0, force_new=True)
        launch.close_window(first)
        reused = launch.open_debug_terminal(target, '', data, slot, slot_index=0)
        self.assertEqual(reused, second); self.assertEqual(len(calls), 2)
