import unittest
from ssh_multi_tool import terminal_focus


class FocusTests(unittest.TestCase):
    def test_focus_matches_target_process_and_exact_tab(self):
        class API:
            def desktop(self): return 'desktop'
            def children(self, node): return {'desktop': ['other', 'app'], 'other': ['wrong'], 'app': ['frame'], 'frame': ['tab']} .get(node, [])
            def pid(self, node): return 1 if node == 'other' else 2
            def role(self, node): return 23 if node == 'frame' else 37 if node in ('tab', 'wrong') else 0
            def name(self, node): return 'Target [abcd]' if node in ('tab', 'wrong') else ''
            def activate(self, node): self.activated = node; return True
            def focus(self, node): self.focused = node; return True
            def release(self, node): pass
        api = API()
        self.assertTrue(terminal_focus.focus_tree(api, 2, 'Target [abcd]'))
        self.assertEqual(api.activated, 'tab'); self.assertEqual(api.focused, 'frame')
        self.assertFalse(terminal_focus.focus_tree(api, 2, 'Other title'))

    def test_pid_namespace_difference_uses_unique_title_in_terminal_app(self):
        class API:
            def desktop(self): return 'desktop'
            def children(self, node): return {'desktop': ['app'], 'app': ['frame'], 'frame': ['tab']}.get(node, [])
            def pid(self, node): return 999
            def role(self, node): return 23 if node == 'frame' else 37 if node == 'tab' else 0
            def name(self, node): return 'gnome-terminal-server' if node == 'app' else 'Target [abcd]' if node == 'tab' else ''
            def activate(self, node): return True
            def focus(self, node): return True
            def release(self, node): pass
        self.assertTrue(terminal_focus.focus_tree(API(), 2, 'Target [abcd]'))
