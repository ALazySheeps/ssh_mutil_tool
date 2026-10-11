import tempfile
import gc
import tkinter as tk
import unittest
from pathlib import Path

from ssh_multi_tool.gui import App
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
