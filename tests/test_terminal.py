import unittest
from unittest.mock import patch

from ssh_multi_tool.terminal import terminal_argv, helper_argv


class TerminalTests(unittest.TestCase):
    def test_title_and_path_are_literal_arguments(self):
        args = terminal_argv(["/path with 中文/tool", "--helper", "--socket", "/tmp/sock", "--token", "abc"],
                             "$(touch nope); title")
        self.assertEqual(args[0], "gnome-terminal")
        self.assertIn("$(touch nope); title", args)
        self.assertEqual(args[args.index("--") + 1], "/path with 中文/tool")
        self.assertNotIn("bash", args)

    def test_helper_arguments_contain_only_locator_and_token(self):
        args = helper_argv("/tmp/socket", "one-time-token")
        self.assertEqual(args[-5:], ["--helper", "--socket", "/tmp/socket", "--token", "one-time-token"])

    def test_installed_package_uses_module_entry_without_source_runner(self):
        with patch("ssh_multi_tool.terminal.Path.is_file", return_value=False):
            args = helper_argv("/tmp/socket", "one-time-token")
        self.assertEqual(args[1:3], ["-m", "ssh_multi_tool"])
