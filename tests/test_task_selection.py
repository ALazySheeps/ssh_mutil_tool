import gc
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from ssh_multi_tool.broker import request
from ssh_multi_tool.gui import App
from ssh_multi_tool.launcher import Launcher
from ssh_multi_tool.storage import Store
from test_models import profile


class TaskSelectionTests(unittest.TestCase):
    def setUp(self):
        try: self.root = tk.Tk()
        except tk.TclError: self.skipTest('No desktop display available')
        self.addCleanup(self.cleanup_root)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.data = profile(); base = self.data['windows'][0]
        self.data['windows'] = [dict(base, commands='echo TASK_A'),
            dict(base, id='b', name='Second', commands='echo TASK_B', start_delay_seconds=.04),
            dict(base, id='c', name='Disabled', enabled=False, commands='')]
        self.store.save_profile(self.data)
        self.app = App(self.root, self.store); self.addCleanup(self.app.launcher.close)
        self.root.update()

    def cleanup_root(self):
        for callback in self.root.tk.call('after', 'info'): self.root.after_cancel(callback)
        self.root.destroy(); gc.collect()

    def click_checkbox(self, identifier):
        box = self.app.table.bbox(identifier, '#0')
        self.assertTrue(box, 'Task rows need a visible checkbox column on the left')
        x, y, width, height = box
        self.app.table.event_generate('<ButtonPress-1>', x=x + width // 2, y=y + height // 2)
        self.app.table.event_generate('<ButtonRelease-1>', x=x + width // 2, y=y + height // 2)
        self.root.update()

    def test_row_checkbox_and_select_all_only_include_enabled_tasks(self):
        app = self.app
        self.assertEqual(app.table.item('a', 'text'), '☑')
        self.assertEqual(app.table.item('b', 'text'), '☑')
        self.assertEqual(app.table.item('c', 'text'), '—')
        app.table.selection_set('b')
        self.click_checkbox('a')
        self.assertEqual(app.table.item('a', 'text'), '☐')
        self.assertEqual(app.table.selection(), ('b',), 'Checkboxes must not change the row used for close/restart')
        self.assertTrue(app.select_all_tasks_button.instate(['alternate']))
        self.click_checkbox('c')
        self.assertEqual(app.table.item('c', 'text'), '—')
        app.select_all_tasks_button.invoke()
        self.assertEqual([app.table.item(key, 'text') for key in ('a', 'b', 'c')], ['☑', '☑', '—'])
        app.select_all_tasks_button.invoke()
        self.assertEqual([app.table.item(key, 'text') for key in ('a', 'b', 'c')], ['☐', '☐', '—'])
        self.assertTrue(app.launch_button.instate(['disabled']))
        self.assertEqual(self.store.load_profile('demo'), self.data)

    def test_only_checked_tasks_are_dispatched_in_local_and_remote_modes(self):
        app = self.app
        for mode in ('local', 'remote'):
            with self.subTest(mode=mode):
                app.launcher.close_all(); app.launcher.close()
                snapshots = []
                def opened(path, token, title):
                    snapshots.append(request(path, {'op': 'claim', 'token': token}))
                app.launcher = Launcher(self.store.known_hosts, preflight_fn=lambda *a, **kw: None, open_fn=opened)
                self.addCleanup(app.launcher.close)
                app.execution_mode.set(mode); app._execution_mode_changed()
                for key, value in {'name': 'Test', 'host': 'example.test', 'port': '22', 'username': 'user'}.items():
                    app.target_vars[key].set(value)
                app.password.set('test-password' if mode == 'remote' else '')
                # Only B is selected, even though row A is highlighted.
                if app.table.item('a', 'text') != '☐': self.click_checkbox('a')
                app.table.selection_set('a')
                app._work = lambda work, complete: complete(work())
                with patch('ssh_multi_tool.gui.check_terminal'), patch('ssh_multi_tool.gui.messagebox.showerror') as error:
                    app.connection(True)
                error.assert_not_called()
                app.launcher.thread.join(timeout=2)
                self.assertFalse(app.launcher.thread.is_alive())
                self.assertEqual([item['task_id'] for item in snapshots], ['b'])
                self.assertEqual(set(app.launcher.tasks), {'b'})
                self.assertEqual(app.launcher.window_specs['b']['window']['start_delay_seconds'], .04)
                self.assertIn('echo TASK_B', snapshots[0]['script'])
                self.assertNotIn('echo TASK_A', snapshots[0]['script'])
                self.assertEqual(self.store.load_profile('demo'), self.data)

    def test_changing_checks_does_not_change_already_scheduled_tasks(self):
        app = self.app
        self.data['windows'][1]['start_delay_seconds'] = .3
        self.store.save_profile(self.data); app.refresh_profiles('demo')
        snapshots = []
        def opened(path, token, title):
            snapshots.append(request(path, {'op': 'claim', 'token': token}))
        app.launcher.close()
        app.launcher = Launcher(self.store.known_hosts, open_fn=opened)
        self.addCleanup(app.launcher.close)
        app.execution_mode.set('local'); app._execution_mode_changed()
        app._work = lambda work, complete: complete(work())
        with patch('ssh_multi_tool.gui.check_terminal'):
            app.connection(True)
        self.assertEqual(app.launcher.tasks['b']['state'], 'waiting')
        self.click_checkbox('b')
        self.assertEqual(app.table.item('b', 'text'), '☐')
        app.launcher.thread.join(timeout=2)
        self.assertEqual([item['task_id'] for item in snapshots], ['a', 'b'])
        self.assertEqual(self.store.load_profile('demo'), self.data)

    def test_empty_selection_is_rejected_before_target_save_or_connection(self):
        app = self.app
        self.assertEqual(app.table.item('a', 'text'), '☑')
        app.select_all_tasks_button.invoke()
        with patch('ssh_multi_tool.gui.messagebox.showerror') as error:
            app.connection(True)
        self.assertIn('勾选', error.call_args.args[1])
        self.assertIsNone(app.launcher.broker)
        self.assertFalse((self.store.root / 'targets.json').exists())
        # Poll must not accidentally enable the launch button again.
        app._poll()
        self.assertTrue(app.launch_button.instate(['disabled']))

    def test_checkbox_choices_survive_profile_switch_and_refresh(self):
        app = self.app
        self.assertEqual(app.table.item('a', 'text'), '☑')
        self.click_checkbox('a')
        other = dict(self.data, id='other', name='Other')
        self.store.save_profile(other); app.refresh_profiles('other')
        self.assertEqual(app.table.item('a', 'text'), '☑')
        app.refresh_profiles('demo')
        self.assertEqual(app.table.item('a', 'text'), '☐')
        self.assertEqual(app.table.item('b', 'text'), '☑')
        changed = self.store.load_profile('demo')
        changed['windows'].append(dict(changed['windows'][0], id='d', name='New'))
        self.store.save_profile(changed); app.refresh_profiles('demo')
        self.assertEqual(app.table.item('a', 'text'), '☐')
        self.assertEqual(app.table.item('d', 'text'), '☑')

    def test_rapid_checkbox_clicks_each_toggle_without_opening_command_preview(self):
        app = self.app
        app.table.selection_set('b')
        x, y, width, height = app.table.bbox('a', '#0')
        for timestamp, expected in ((1000, '☐'), (1100, '☑'), (1200, '☐')):
            app.table.event_generate('<ButtonPress-1>', x=x + width // 2, y=y + height // 2, time=timestamp)
            app.table.event_generate('<ButtonRelease-1>', x=x + width // 2, y=y + height // 2, time=timestamp + 1)
            self.root.update()
            self.assertEqual(app.table.item('a', 'text'), expected)
        self.assertEqual(app.table.selection(), ('b',))
        self.assertNotIn('command_preview', self.root.children)

    def test_space_toggles_task_and_busy_state_blocks_checkbox_changes(self):
        app = self.app
        self.assertEqual(app.table.item('a', 'text'), '☑')
        app.table.focus('a'); app.table.focus_force(); self.root.update()
        app.table.event_generate('<space>'); self.root.update()
        self.assertEqual(app.table.item('a', 'text'), '☐')
        app.busy = True; app._update_window_controls()
        self.click_checkbox('b')
        self.assertEqual(app.table.item('b', 'text'), '☑')
        self.assertTrue(app.select_all_tasks_button.instate(['disabled']))
