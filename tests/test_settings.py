import unittest
from ssh_multi_tool.settings import connection_options


class SettingsTests(unittest.TestCase):
    def test_default_and_configured_timeouts(self):
        self.assertEqual(connection_options({}), {"connect_timeout": 10, "auth_timeout": 15})
        self.assertEqual(connection_options({"connect_timeout": "30", "auth_timeout": "45"}),
                         {"connect_timeout": 30, "auth_timeout": 45})

    def test_rejects_invalid_timeout(self):
        for value in ("0", "121", "nan", "", "10.5"):
            with self.assertRaises(ValueError): connection_options({"connect_timeout": value})
