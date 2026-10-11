import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_multi_tool.storage import Store
from test_models import profile


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))

    def test_roundtrip_and_import_copy_and_export(self):
        data = profile(); self.store.save_profile(data)
        self.assertEqual(self.store.load_profile("demo"), data)
        file = Path(self.tmp.name) / "export.json"
        self.store.export_profile("demo", file)
        imported = self.store.import_profile(file)
        self.assertNotEqual(imported["id"], "demo")
        self.assertEqual(imported["windows"], data["windows"])
        self.assertNotIn("password", file.read_text(encoding="utf-8"))

    def test_failed_replace_preserves_original(self):
        self.store.save_profile(profile())
        changed = profile(); changed["name"] = "new"
        with patch("ssh_multi_tool.storage.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError): self.store.save_profile(changed)
        self.assertEqual(self.store.load_profile("demo")["name"], "开发")

    def test_rejects_bad_import_without_overwriting(self):
        self.store.save_profile(profile())
        file = Path(self.tmp.name) / "bad.json"
        for content in ['{', '{"schema_version": 2}', ' ' * (1048576 + 1)]:
            file.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError): self.store.import_profile(file)
        self.assertEqual(self.store.load_profile("demo"), profile())

    def test_preferences_and_corrupt_profile_recovery(self):
        self.store.save_preferences({"last_profile_id": "demo"})
        self.assertEqual(Store(Path(self.tmp.name)).preferences()["last_profile_id"], "demo")
        self.store.save_profile(profile())
        (self.store.profiles_dir / "corrupt.json").write_text("{", encoding="utf-8")
        profiles, errors = self.store.list_profiles()
        self.assertEqual(len(profiles), 1); self.assertEqual(len(errors), 1)

    def test_deeply_nested_json_is_skipped_without_crash(self):
        file = self.store.profiles_dir / "deep.json"
        original = "[" * 10000 + "0" + "]" * 10000
        file.write_text(original)
        profiles, errors = self.store.list_profiles()
        self.assertEqual(profiles, []); self.assertEqual(len(errors), 1)
        self.assertEqual(file.read_text(), original)

    def test_unreadable_targets_and_preferences_backed_up_before_replacement(self):
        for name, original in [("targets.json", b'[{"id":"old"},{"invalid":true}]'),
                               ("preferences.json", b'{broken')]:
            path = self.store.root / name; path.write_bytes(original)
            if name == "targets.json":
                self.store.save_targets([{"id": "new", "name": "T", "host": "localhost", "port": 22, "username": "u"}])
            else: self.store.save_preferences({"last_profile_id": "demo"})
            backups = list(self.store.root.glob(name + ".recovery-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
