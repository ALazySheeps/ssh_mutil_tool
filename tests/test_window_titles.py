"""WSLg's legacy WM_NAME reader requires ASCII window titles."""
import ast
from pathlib import Path
import unittest


class WindowTitleTests(unittest.TestCase):
    def test_all_native_window_titles_are_ascii(self):
        for name in ('gui.py', 'editor.py'):
            source = Path(__file__).resolve().parents[1] / 'ssh_multi_tool' / name
            for node in ast.walk(ast.parse(source.read_text(encoding='utf-8'))):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                titles = []
                if node.func.attr == 'title' and node.args:
                    titles.append(node.args[0])
                if isinstance(node.func.value, ast.Name) and node.func.value.id == 'messagebox' and node.args:
                    titles.append(node.args[0])
                titles.extend(k.value for k in node.keywords if k.arg == 'title')
                for title in titles:
                    self.assertIsInstance(title, ast.Constant)
                    self.assertTrue(title.value.isascii(), (name, node.lineno, title.value))
