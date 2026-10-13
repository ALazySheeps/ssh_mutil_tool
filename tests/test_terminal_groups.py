import subprocess
import unittest
from ssh_multi_tool import terminal


class TerminalGroupTests(unittest.TestCase):
    def test_tabs_group_by_target_and_survive_anchor_close(self):
        calls = []; live = set()
        def run(args, **kwargs):
            calls.append((args, kwargs))
            if args[0] == 'gdbus':
                path = args[args.index('--object-path') + 1]
                return subprocess.CompletedProcess(args, 0, 'org.gnome.Terminal.Terminal0' if path in live else '', '')
            token = args[-1]; path = '/org/gnome/Terminal/screen/' + token; live.add(path)
            return subprocess.CompletedProcess(args, 0, 'GNOME_TERMINAL_SERVICE=:1.20\nGNOME_TERMINAL_SCREEN=' + path + '\n', '')
        groups = terminal.TerminalGroups(run=run, check=lambda: None)
        a = {'host': 'board-a', 'port': 22, 'username': 'u'}
        b = {'host': 'board-b', 'port': 22, 'username': 'u'}
        groups.open(a, '/tmp/sock', 'one', 'First')
        groups.open(b, '/tmp/sock', 'two', 'Other board')
        groups.open(a, '/tmp/sock', 'three', 'Second')
        launches = [(args, kw) for args, kw in calls if args[0] == 'gnome-terminal']
        self.assertIn('--window', launches[0][0]); self.assertIn('--window', launches[1][0])
        self.assertIn('--tab', launches[2][0])
        self.assertEqual(launches[2][1]['env']['GNOME_TERMINAL_SCREEN'], '/org/gnome/Terminal/screen/one')
        live.remove('/org/gnome/Terminal/screen/one')
        groups.open(a, '/tmp/sock', 'four', 'Third')
        self.assertIn('--tab', calls[-1][0])
        self.assertEqual(calls[-1][1]['env']['GNOME_TERMINAL_SCREEN'], '/org/gnome/Terminal/screen/three')
        live.clear(); groups.open(a, '/tmp/sock', 'five', 'Reopened')
        self.assertIn('--window', calls[-1][0])

    def test_inherited_terminal_context_is_cleared_for_first_window(self):
        from unittest.mock import patch
        calls = []
        def run(args, **kwargs):
            calls.append(kwargs['env'])
            return subprocess.CompletedProcess(args, 0, 'GNOME_TERMINAL_SERVICE=:1.20\nGNOME_TERMINAL_SCREEN=/org/gnome/Terminal/screen/a\n', '')
        with patch.dict('os.environ', GNOME_TERMINAL_SCREEN='/unrelated/screen', GNOME_TERMINAL_SERVICE=':1.99'):
            groups = terminal.TerminalGroups(run=run, check=lambda: None)
            groups.open({'host': 'board-a', 'port': 22, 'username': 'u'}, '/tmp/sock', 'one', 'A')
        self.assertNotIn('GNOME_TERMINAL_SCREEN', calls[0]); self.assertNotIn('GNOME_TERMINAL_SERVICE', calls[0])
