"""Verify the shipped icon is used and portable desktop entries actually launch."""
import configparser
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tkinter as tk
import unittest

from ssh_multi_tool.gui import App
from ssh_multi_tool.icons import WINDOW_CLASS
from ssh_multi_tool.storage import Store

ROOT = Path(__file__).resolve().parent.parent


class WindowIconTests(unittest.TestCase):
    def test_application_sets_native_window_icon(self):
        if not shutil.which('xprop') or not shutil.which('xwininfo'): self.skipTest('Native X11 icon inspection needs xprop')
        try: root = tk.Tk(className=WINDOW_CLASS)
        except tk.TclError: self.skipTest('No desktop display')
        with tempfile.TemporaryDirectory() as directory:
            app = App(root, Store(Path(directory)))
            try:
                root.update()
                import re
                tree = subprocess.run(['xwininfo', '-id', hex(root.winfo_id()), '-tree'], capture_output=True, text=True, check=True).stdout
                client = re.search(r'Parent window id: (0x[0-9a-f]+)', tree).group(1)
                result = subprocess.run(['xprop', '-id', client, '-f', '_NET_WM_ICON', '32c', '_NET_WM_ICON', 'WM_CLASS'],
                                        capture_output=True, text=True, check=True)
                self.assertIn('= ', result.stdout, 'Tk window must expose its custom icon to the desktop')
                self.assertIn('256, 256,', result.stdout, 'High-DPI icon must be provided')
                data = Path(directory) / 'desktop-data'
                subprocess.run(['bash', str(ROOT / 'packaging/install-desktop.sh')],
                    env=dict(os.environ, XDG_DATA_HOME=str(data)), capture_output=True, check=True)
                entry = configparser.ConfigParser(interpolation=None)
                entry.read(data / 'applications/ssh-multi-tool.desktop')
                wm_class = next(line for line in result.stdout.splitlines() if line.startswith('WM_CLASS'))
                self.assertIn('"' + entry['Desktop Entry']['StartupWMClass'] + '"', wm_class,
                              'Desktop entry must match the actual native WM_CLASS')
            finally:
                app.launcher.close(); root.destroy()
                self.assertFalse(app._window_icons)
                del app
                import gc
                gc.collect()


class DesktopIconTests(unittest.TestCase):
    def test_packaged_launcher_installs_icon_and_launches_from_special_path(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package = base / 'Tool 中文 "$cash" 50%'
            package.mkdir(); data = base / 'data'
            shutil.copyfile(ROOT / 'packaging/install-desktop.sh', package / 'install-desktop.sh')
            shutil.copyfile(ROOT / 'ssh_multi_tool/assets/app-icon.svg', package / 'app-icon.svg')
            binary = package / 'ssh-multi-tool'; binary.write_text('#!/bin/sh\nprintf ran > "$0.ran"\n'); binary.chmod(0o755)
            subprocess.run(['bash', str(package / 'install-desktop.sh')], env=dict(os.environ, XDG_DATA_HOME=str(data)),
                           capture_output=True, text=True, check=True)
            entry = data / 'applications/ssh-multi-tool.desktop'
            contents = configparser.ConfigParser(interpolation=None); contents.read(entry)
            self.assertEqual(contents['Desktop Entry'].get('Icon'), 'ssh-multi-tool')
            self.assertEqual(contents['Desktop Entry'].get('StartupWMClass'), 'Sshmultitool')
            self.assertTrue((data / 'icons/hicolor/scalable/apps/ssh-multi-tool.svg').is_file())
            if shutil.which('desktop-file-validate'):
                subprocess.run(['desktop-file-validate', str(entry)], check=True)
            probe = 'import gi,sys; from gi.repository import Gio; app=Gio.DesktopAppInfo.new_from_filename(sys.argv[1]); assert app; app.launch([],None)'
            # Ubuntu's desktop parser, not shell parsing, must execute the installed entry.
            result = subprocess.run(['/usr/bin/python3', '-c', probe, str(entry)], capture_output=True, text=True)
            if 'No module named' in result.stderr: self.skipTest('Gio desktop parsing needs system PyGObject')
            self.assertEqual(result.returncode, 0, result.stderr)
            import time
            deadline = time.monotonic() + 2
            while not binary.with_name(binary.name + '.ran').exists() and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(binary.with_name(binary.name + '.ran').exists(), 'Desktop entry must run its bundled binary')

    def test_source_launcher_registers_source_script_and_icon(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); source = base / 'source'; (source / 'packaging').mkdir(parents=True)
            assets = source / 'ssh_multi_tool/assets'; assets.mkdir(parents=True)
            shutil.copyfile(ROOT / 'packaging/install-desktop.sh', source / 'packaging/install-desktop.sh')
            shutil.copyfile(ROOT / 'ssh_multi_tool/assets/app-icon.svg', assets / 'app-icon.svg')
            (source / 'run.py').write_text('')
            (source / 'start.sh').write_text('#!/bin/sh\nexit 0\n')
            data = base / 'data'
            subprocess.run(['bash', str(source / 'packaging/install-desktop.sh')], env=dict(os.environ, XDG_DATA_HOME=str(data)),
                           capture_output=True, check=True)
            contents = configparser.ConfigParser(interpolation=None)
            contents.read(data / 'applications/ssh-multi-tool.desktop')
            self.assertIn('start.sh', contents['Desktop Entry']['Exec'])
            self.assertTrue((data / 'icons/hicolor/scalable/apps/ssh-multi-tool.svg').exists())
