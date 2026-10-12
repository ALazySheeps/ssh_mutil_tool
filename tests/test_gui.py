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
        self.assertTrue(app.table.exists(identifier))
        app.refresh_profiles(); self.assertTrue(app.table.exists(identifier))
        app.table.selection_set(identifier); app._update_window_controls()
        self.assertTrue(app.close_window_button.instate(["!disabled"]))
        self.assertTrue(app.restart_window_button.instate(["!disabled"]))
        app.close_selected_window()
        self.assertEqual(app.launcher.poll()[identifier]["state"], "closed")
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
        self.assertEqual(app.table.item(next(iter(app.launcher.shell_tasks)), 'values')[1], '继承公共初始化目录')

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
