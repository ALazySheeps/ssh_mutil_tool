import copy
import unittest

from ssh_multi_tool.models import validate_profile, validate_target


def profile():
    return {"schema_version": 1, "id": "demo", "name": "开发", "description": "",
            "common_init": "export A=10\n", "windows": [
                {"id": "a", "name": "节点", "enabled": True,
                 "working_directory": "~/robot ws", "commands": "echo 中文",
                 "start_delay_seconds": 0}]}


class ModelsTests(unittest.TestCase):
    def test_preserves_multiline_commands_and_returns_detached_data(self):
        original = profile()
        validated = validate_profile(original)
        self.assertEqual(validated["common_init"], "export A=10\n")
        validated["windows"][0]["name"] = "changed"
        self.assertEqual(original["windows"][0]["name"], "节点")

    def test_rejects_invalid_profiles(self):
        invalid = []
        for field, value in [("schema_version", 2), ("schema_version", True),
                             ("unexpected", 1), ("name", " "), ("id", "../x")]:
            data = profile(); data[field] = value; invalid.append(data)
        for field, value in [("start_delay_seconds", float("nan")),
                             ("start_delay_seconds", True), ("start_delay_seconds", 3601),
                             ("start_delay_seconds", 10 ** 400),
                             ("working_directory", "relative"), ("commands", ""),
                             ("enabled", "yes")]:
            data = profile(); data["windows"][0][field] = value; invalid.append(data)
        data = profile(); data["windows"] *= 2; invalid.append(data)
        data = profile(); data["windows"] = [dict(data["windows"][0], id=str(i)) for i in range(33)]; invalid.append(data)
        data = profile(); data["windows"][0]["enabled"] = False; invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_profile(data)

    def test_target_rejects_password_and_invalid_port(self):
        target = {"id": "x5", "name": "X5", "host": "192.168.1.10", "port": 22, "username": "sunrise"}
        self.assertEqual(validate_target(target), target)
        for extra in [{"password": "secret"}, {"port": 0}, {"host": ""}, {"port": True}]:
            with self.assertRaises(ValueError): validate_target(dict(target, **extra))
