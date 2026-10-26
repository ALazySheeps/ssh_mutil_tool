import tempfile
import gc
import tkinter as tk
import unittest
import time
from unittest.mock import patch
from pathlib import Path

from ssh_multi_tool.gui import App
from ssh_multi_tool.launcher import Launcher
from ssh_multi_tool.editor import ProfileEditor
from ssh_multi_tool.storage import Store
from test_models import profile


class GuiTests(unittest.TestCase):
    def setUp(self):
        try: self.root = tk.Tk()
        except tk.TclError: self.skipTest("No desktop display available")
        self.root.withdraw(); self.addCleanup(self.cleanup_root)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)); self.store.save_profile(profile())

    def cleanup_root(self):
        self.root.destroy(); gc.collect()

    def test_double_click_previews_clicked_windows_literal_commands_and_copies_them(self):
        from tkinter import ttk
        data = profile()
        data['windows'].append(dict(data['windows'][0], id='b', name='第二窗口',
                                    commands="printf '%s\\n' 中文 $HOME\necho done\n", working_directory=''))
        self.store.save_profile(data)
        app = App(self.root, self.store); self.addCleanup(app.launcher.close)
        self.root.deiconify(); self.root.update()
        app.table.selection_set('a')
        x, y, width, height = app.table.bbox('b', 'name')
        for timestamp in (1000, 1100):
            app.table.event_generate('<ButtonPress-1>', x=x + 10, y=y + height // 2, time=timestamp)
            app.table.event_generate('<ButtonRelease-1>', x=x + 10, y=y + height // 2, time=timestamp + 1)
        self.root.update()
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        dialogs = [child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel)]
        self.assertEqual(len(dialogs), 1)
        dialog = dialogs[0]
        texts = [child for child in descendants(dialog) if isinstance(child, tk.Text)]
        self.assertEqual([text.get('1.0', 'end-1c') for text in texts],
                         ["printf '%s\\n' 中文 $HOME\necho done\n"])
        self.assertEqual(str(texts[0]['state']), 'disabled')
        copy_button = next(child for child in descendants(dialog)
                           if isinstance(child, ttk.Button) and child['text'] == '复制启动命令')
        copy_button.invoke(); self.root.update()
        self.assertEqual(self.root.clipboard_get(), "printf '%s\\n' 中文 $HOME\necho done\n")
        dialog.destroy()
        self.assertEqual(self.store.load_profile('demo')['windows'][1]['commands'],
                         "printf '%s\\n' 中文 $HOME\necho done\n")

    def test_collapsing_log_keeps_new_events_and_restores_history(self):
        from tkinter import ttk
        app = App(self.root, self.store); self.addCleanup(app.launcher.close)
        self.root.deiconify(); self.root.update()
        def buttons(parent):
            for child in parent.winfo_children():
                if isinstance(child, ttk.Button): yield child
                yield from buttons(child)
        toggle = next((button for button in buttons(app) if str(button['text']) in {'收起日志', '展开日志'}), None)
        self.assertIsNotNone(toggle, 'The event log needs a visibility control')
        if not app.events_list.winfo_ismapped(): toggle.invoke(); self.root.update_idletasks()
        app.log('before folding'); toggle.invoke(); self.root.update_idletasks()
        self.assertFalse(app.events_list.winfo_ismapped())
        app.log('while folded'); toggle.invoke(); self.root.update_idletasks()
        self.assertTrue(app.events_list.winfo_ismapped())
        self.assertEqual(app.events_list.get(0, 'end')[-2:], ('before folding', 'while folded'))

    def test_bulk_tmux_stop_collects_each_boards_password_and_unlocks_start(self):
        from ssh_multi_tool.terminal import target_key
        app = App(self.root, self.store); self.addCleanup(app.launcher.close)
        calls = []
        app.launcher = Launcher(self.store.known_hosts, preflight_fn=lambda *a, **kw: None,
            tmux_check_fn=lambda *a, **kw: None, open_fn=lambda *a: None)
        self.addCleanup(app.launcher.close); app.launcher.keep_alive = True
        app.target_id = 't'
        for key, value in {"name": "T", "host": "board-a", "port": "22", "username": "u"}.items():
            app.target_vars[key].set(value)
        app.password.set('alpha'); target = app.target()
        app.launcher.start_window(target, 'alpha', app.selected_profile(), 'a')
        other = dict(target, id='other', name='Other', host='board-b')
        manual = app.launcher.open_remote_terminal(other, 'beta')
        app._update_window_controls()
        self.assertTrue(app.stop_tmux_all_button.instate(["!disabled"]))
        app._work = lambda action, complete: complete(action())
        def stopped(target, password, *args, **kwargs): calls.append((target_key(target), password))
        with patch('ssh_multi_tool.gui.simpledialog.askstring', return_value='beta') as prompt, patch('ssh_multi_tool.launcher.stop_session', side_effect=stopped):
            app.stop_tmux_all_button.invoke()
        self.assertEqual(prompt.call_count, 1)
        self.assertEqual(set(calls), {(target_key(target), 'alpha'), (target_key(other), 'beta')})
        app._poll()
        self.assertFalse(app.launcher.active())
        self.assertTrue(app.launch_button.instate(["!disabled"]))
        self.assertNotIn(manual, app.launcher.poll())
        self.assertNotIn('alpha', repr(self.store.preferences()))
        self.assertEqual(str(app.close_all_button['text']), '一键断开')

    def test_renaming_target_does_not_block_stop_of_original_endpoint(self):
        app = App(self.root, self.store); self.addCleanup(app.launcher.close)
        app.launcher = Launcher(self.store.known_hosts, preflight_fn=lambda *a, **kw: None,
                                open_fn=lambda *a: None, tmux_check_fn=lambda *a, **kw: None)
        self.addCleanup(app.launcher.close); app.launcher.keep_alive = True
        app.target_id = 't'
        for key, value in {"name": "Old", "host": "localhost", "port": "22", "username": "u"}.items():
            app.target_vars[key].set(value)
        app.password.set('pw')
        identifier = app.launcher.open_remote_terminal(app.target(), 'pw')
        app.launcher.close_window(identifier); app.launcher.poll()
        app._sync_remote_windows(app.launcher.poll()); app.window_tabs.select(app.manual_page)
        app.manual_table.selection_set(identifier); app.target_vars['name'].set('Renamed')
        app._work = lambda action, complete: complete(action())
        with patch('ssh_multi_tool.launcher.stop_session'), patch('ssh_multi_tool.gui.messagebox.showerror') as error:
            app.control_persistent_window(True)
        self.assertFalse(error.called)
        self.assertNotIn(identifier, app.launcher.poll())

    def test_keep_alive_preference_and_detached_controls(self):
        self.store.save_preferences({"keep_alive": "1"})
        app = App(self.root, self.store); self.addCleanup(app.launcher.close)
        self.assertTrue(app.launcher.keep_alive)
        app.launcher.profile_id = app.selected_profile()["id"]
        app.launcher.tasks = {"a": {"name": "A", "state": "detached", "tmux_session": "smt_test"}}
        data = app.selected_profile()
        app.launcher.window_specs = {"a": {"target": {"id": "t", "name": "T", "host": "localhost", "port": 22, "username": "u"},
            "profile": data, "window": data["windows"][0], "connect_options": {}, "tmux_session": "smt_test"}}
        app.table.selection_set("a"); app._update_window_controls()
        self.assertTrue(app.reconnect_window_button.instate(["!disabled"]))
        self.assertTrue(app.stop_window_button.instate(["!disabled"]))
        self.assertTrue(app.close_window_button.instate(["disabled"]))
        app.launcher.tasks['a'].update(state='closed', stopped=True)
        app._update_window_controls()
        self.assertTrue(app.reconnect_window_button.instate(["disabled"]))
        app.keep_alive.set(False); app.save_keep_alive()
        self.assertFalse(app.launcher.keep_alive)
        self.assertEqual(self.store.preferences()["keep_alive"], "0")

    def test_window_controls_only_enable_for_current_run(self):
        app = App(self.root, self.store); self.addCleanup(app.launcher.close)
        self.assertTrue(app.close_window_button.instate(["disabled"]))
        app.launcher.profile_id = app.selected_profile()["id"]
        app.launcher.tasks = {"a": {"name": "A", "state": "waiting"}}
        app.table.selection_set("a"); app._update_window_controls()
        self.assertTrue(app.close_window_button.instate(["!disabled"]))
        app.close_selected_window()
        self.assertEqual(app.launcher.tasks["a"]["state"], "closed")
        self.assertTrue(app.restart_window_button.instate(["!disabled"]))
        self.assertTrue(app.close_all_button.instate(["!disabled"]))

    def test_open_remote_terminal_without_profile_and_keep_row_on_profile_switch(self):
        app = App(self.root, self.store); calls = []
        app.launcher = Launcher(self.store.known_hosts, preflight_fn=lambda *a, **kw: None,
                                open_fn=lambda *a: calls.append(a))
        self.addCleanup(app.launcher.close)
        app.profiles = []; app.profile_combo.set(""); app._profile_selected()
        for key, value in {"name": "T", "host": "localhost", "port": "22", "username": "u"}.items():
            app.target_vars[key].set(value)
        app.password.set("secret")
        with patch("ssh_multi_tool.gui.check_terminal"):
            app.remote_terminal_button.invoke()
        deadline = time.monotonic() + 2
        while app.busy:
            self.assertLess(time.monotonic(), deadline)
            app._poll(); time.sleep(.01)
        self.assertEqual(len(calls), 1)
        identifier = next(iter(app.launcher.shell_tasks))
        self.assertFalse(app.table.exists(identifier))
        self.assertTrue(app.manual_table.exists(identifier))
        app.refresh_profiles(); self.assertTrue(app.manual_table.exists(identifier))
        app.window_tabs.select(app.manual_page)
        app.manual_table.selection_set(identifier); app._update_window_controls()
        self.assertTrue(app.close_window_button.instate(["!disabled"]))
        self.assertTrue(app.restart_window_button.instate(["!disabled"]))
        app.close_selected_window()
        self.assertNotIn(identifier, app.launcher.poll())
        app._poll()
        self.assertFalse(app.manual_table.exists(identifier))
        self.assertTrue(app.table.exists("a"))
        self.assertTrue(app.remote_terminal_button.instate(["!disabled"]))

    def test_start_selected_window_and_disable_duplicate_launch(self):
        app = App(self.root, self.store); calls = []
        app.launcher = Launcher(self.store.known_hosts, preflight_fn=lambda *a, **kw: None,
                                open_fn=lambda *a: calls.append(a))
        self.addCleanup(app.launcher.close)
        for key, value in {"name": "T", "host": "localhost", "port": "22", "username": "u"}.items():
            app.target_vars[key].set(value)
        app.password.set("secret"); app.table.selection_set('a'); app._update_window_controls()
        self.assertTrue(app.start_window_button.instate(["!disabled"]))
        with patch("ssh_multi_tool.gui.check_terminal"):
            app.start_window_button.invoke()
        deadline = time.monotonic() + 2
        while app.busy:
            self.assertLess(time.monotonic(), deadline)
            app._poll(); time.sleep(.01)
        self.assertEqual(len(calls), 1); self.assertEqual(set(app.launcher.tasks), {'a'})
        self.assertTrue(app.start_window_button.instate(["disabled"]))
        app.close_selected_window(); self.assertTrue(app.start_window_button.instate(["!disabled"]))

    def test_new_remote_window_uses_current_profile_common_init(self):
        app = App(self.root, self.store); calls = []
        app.launcher = Launcher(self.store.known_hosts, preflight_fn=lambda *a, **kw: None,
                                open_fn=lambda *a: calls.append(a))
        self.addCleanup(app.launcher.close)
        for key, value in {"name": "T", "host": "localhost", "port": "22", "username": "u"}.items():
            app.target_vars[key].set(value)
        app.password.set("secret")
        with patch("ssh_multi_tool.gui.check_terminal"): app.remote_terminal_button.invoke()
        deadline = time.monotonic() + 2
        while app.busy:
            self.assertLess(time.monotonic(), deadline)
            app._poll(); time.sleep(.01)
        spec = next(iter(app.launcher.shell_specs.values()))
        self.assertEqual(spec['profile']['common_init'], app.selected_profile()['common_init'])
        self.assertEqual(app.manual_table.item(next(iter(app.launcher.shell_tasks)), 'values')[1], '继承公共初始化目录')

    def test_manual_tab_actions_and_close_all_preserve_configuration_rows(self):
        app = App(self.root, self.store)
        app.launcher = Launcher(self.store.known_hosts, preflight_fn=lambda *a, **kw: None, open_fn=lambda *a: None)
        self.addCleanup(app.launcher.close)
        target = {'id': app.target_id, 'name': 'T', 'host': 'localhost', 'port': 22, 'username': 'u'}
        app.launcher.start_window(target, 'secret', app.selected_profile(), 'a')
        first = app.launcher.open_remote_terminal(target, 'secret')
        second = app.launcher.open_remote_terminal(target, 'secret')
        app._poll()
        self.assertEqual([app.window_tabs.tab(tab, 'text') for tab in app.window_tabs.tabs()], ['配置窗口', '手动终端'])
        self.assertEqual(set(app.table.get_children()), {'a'})
        self.assertEqual(set(app.manual_table.get_children()), {first, second})
        app.table.selection_set('a'); app.manual_table.selection_set(first)
        app.window_tabs.select(app.manual_page); app._update_window_controls()
        self.assertTrue(app.start_window_button.instate(['disabled']))
        app.close_selected_window(); app._poll()
        self.assertFalse(app.manual_table.exists(first))
        self.assertTrue(app.manual_table.exists(second))
        self.assertEqual(app.launcher.tasks['a']['state'], 'opening')
        app.close_all_button.invoke(); app._poll()
        self.assertEqual(app.manual_table.get_children(), ())
        self.assertEqual(app.launcher.shell_specs, {})
        self.assertTrue(app.table.exists('a'))
        self.assertEqual(app.table.set('a', 'state'), '已关闭')

    def test_debug_buttons_save_and_launch_current_global_config(self):
        app = App(self.root, self.store); calls = []
        app.launcher = Launcher(self.store.known_hosts, preflight_fn=lambda *a, **kw: None,
                                open_fn=lambda *a: calls.append(a))
        self.addCleanup(app.launcher.close)
        self.assertEqual(len(app.debug_buttons), 4)
        self.assertEqual(app.debug_slots[0]['commands'], 'htop')
        self.assertTrue(app.debug_buttons[1].instate(['disabled']))
        slots = [dict(slot) for slot in app.debug_slots]
        slots[1] = {'name': '进程', 'commands': 'ps aux'}
        app.save_debug_slots(slots)
        from ssh_multi_tool.settings import debug_slots
        self.assertEqual(debug_slots(self.store.preferences()), slots)
        self.assertEqual(self.store.load_profile('demo'), profile())
        for key, value in {'name': 'T', 'host': 'localhost', 'port': '22', 'username': 'u'}.items():
            app.target_vars[key].set(value)
        app.password.set('secret')
        with patch('ssh_multi_tool.gui.check_terminal'): app.debug_buttons[1].invoke()
        deadline = time.monotonic() + 2
        while app.busy:
            self.assertLess(time.monotonic(), deadline)
            app._poll(); time.sleep(.01)
        self.assertEqual(len(calls), 1)
        identifier = next(iter(app.launcher.shell_specs)); spec = app.launcher.shell_specs[identifier]
        self.assertEqual(spec['debug_slot'], slots[1])
        self.assertEqual(spec['profile']['common_init'], app.selected_profile()['common_init'])
        self.assertEqual(app.window_tabs.select(), str(app.manual_page))
        self.assertTrue(app.manual_table.exists(identifier))

    def test_debug_slot_save_failure_preserves_previous_settings(self):
        app = App(self.root, self.store); self.addCleanup(app.launcher.close)
        previous = [dict(slot) for slot in app.debug_slots]
        slots = [dict(slot) for slot in previous]; slots[0]['commands'] = 'new-command'
        with patch.object(self.store, 'save_preferences', side_effect=OSError('disk')):
            with self.assertRaises(OSError): app.save_debug_slots(slots)
        self.assertEqual(app.debug_slots, previous)

    def test_debug_editor_saves_four_names_and_multiline_commands(self):
        from ssh_multi_tool.debug_editor import DebugSlotsEditor
        from ssh_multi_tool.settings import debug_slots
        results = []; editor = DebugSlotsEditor(self.root, debug_slots({}), results.append)
        editor.names[2].set('日志')
        editor.commands[2].insert('1.0', 'echo first\necho second')
        self.assertTrue(editor._save())
        self.assertEqual(len(results[0]), 4)
        self.assertEqual(results[0][2], {'name': '日志', 'commands': 'echo first\necho second'})

    def test_debug_button_reuses_session_without_password_and_can_force_new(self):
        app = App(self.root, self.store); calls = []
        app.launcher = Launcher(self.store.known_hosts, preflight_fn=lambda *a, **kw: None,
                                open_fn=lambda *a: calls.append(a))
        self.addCleanup(app.launcher.close)
        for key, value in {'name': 'T', 'host': 'localhost', 'port': '22', 'username': 'u'}.items():
            app.target_vars[key].set(value)
        def finish():
            deadline = time.monotonic() + 2
            while app.busy:
                self.assertLess(time.monotonic(), deadline)
                app._poll(); time.sleep(.01)
        app.password.set('secret')
        with patch('ssh_multi_tool.gui.check_terminal'): app.debug_buttons[0].invoke()
        finish(); identifier = next(iter(app.launcher.shell_tasks))
        app.password.set('')
        with patch('ssh_multi_tool.gui.check_terminal'), patch.object(app.launcher, 'focus_window', return_value=True) as focus:
            app.debug_buttons[0].invoke(); finish()
            focus.assert_called_once_with(identifier)
        self.assertEqual(len(calls), 1)
        self.assertEqual(app.manual_table.selection(), (identifier,))
        app.password.set('secret')
        with patch('ssh_multi_tool.gui.check_terminal'): app.open_debug_terminal(0, force_new=True)
        finish(); self.assertEqual(len(calls), 2)

    def test_restores_nonfirst_profile_without_overwriting_selection(self):
        second = profile(); second.update(id="z-second", name="Second")
        self.store.save_profile(second)
        self.store.save_preferences({"last_profile_id": "z-second", "geometry": "980x700"})
        self.store.save_targets([{"id": "t", "name": "T", "host": "localhost", "port": 22, "username": "u"}])
        app = App(self.root, self.store)
        self.assertEqual(app.selected_profile()["id"], "z-second")
        self.assertEqual(self.store.preferences()["last_profile_id"], "z-second")
        app.launcher.close()

    def test_restores_profile_and_saves_target_without_password(self):
        self.store.save_preferences({"last_profile_id": "demo"})
        app = App(self.root, self.store)
        self.assertEqual(app.selected_profile()["id"], "demo")
        for key, value in {"name": "X5", "host": "192.168.1.10", "port": "22", "username": "sunrise"}.items():
            app.target_vars[key].set(value)
        app.password.set("do-not-save")
        app.save_target(quiet=True)
        self.assertEqual(self.store.targets()[0]["username"], "sunrise")
        self.assertNotIn("do-not-save", (self.store.root / "targets.json").read_text())
        app.launcher.close()

    def test_editor_save_produces_valid_profile(self):
        results = []; editor = ProfileEditor(self.root, profile(), results.append)
        editor.window_name.set("edited")
        self.assertTrue(editor._save())
        self.assertEqual(results[0]["windows"][0]["name"], "edited")
