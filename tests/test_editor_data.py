import unittest
from ssh_multi_tool.editor import EditorData
from test_models import profile


class EditorDataTests(unittest.TestCase):
    def test_edits_are_detached_and_duplicate_gets_new_id(self):
        original = profile(); data = EditorData(original)
        data.profile["name"] = "edited"
        copied = data.duplicate_window(0)
        self.assertNotEqual(copied["id"], "a")
        self.assertEqual(copied["commands"], "echo 中文")
        self.assertEqual(len(original["windows"]), 1)
        data.move_window(1, -1)
        self.assertEqual(data.profile["windows"][0]["id"], copied["id"])
        data.delete_window(0)
        self.assertEqual(data.profile["windows"][0]["id"], "a")

    def test_new_window_has_stable_id(self):
        data = EditorData(); first = data.add_window()
        self.assertTrue(first["id"]); self.assertEqual(first["start_delay_seconds"], 0)
        first["commands"] = "echo ok"
        self.assertEqual(data.profile["windows"][0]["commands"], "echo ok")
