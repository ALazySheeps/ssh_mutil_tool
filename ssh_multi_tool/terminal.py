"""Launch actual terminal windows without a local command shell."""
import os
import shutil
import subprocess
import sys
from pathlib import Path


def helper_argv(socket_path: str, token: str) -> list[str]:
    if getattr(sys, "frozen", False):
        prefix = [sys.executable]
    else:
        runner = Path(__file__).resolve().parent.parent / "run.py"
        prefix = [sys.executable, str(runner)] if runner.is_file() else [sys.executable, "-m", "ssh_multi_tool"]
    return prefix + ["--helper", "--socket", socket_path, "--token", token]


def terminal_argv(helper_args: list[str], title: str) -> list[str]:
    return ["gnome-terminal", "--window", "--title", title, "--", *helper_args]


def check_terminal() -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("SSH多终端启动目前支持Ubuntu桌面；当前平台可以编辑配置。")
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise RuntimeError("未检测到图形桌面，请在Ubuntu桌面会话中启动。")
    if not shutil.which("gnome-terminal"):
        raise RuntimeError("找不到GNOME Terminal，请安装：sudo apt install gnome-terminal")


def open_terminal(socket_path: str, token: str, title: str):
    check_terminal()
    return subprocess.Popen(terminal_argv(helper_argv(socket_path, token), title),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def target_key(target):
    return (target['host'].lower(), target['port'], target['username'])


class TerminalGroups:
    """Keep each board's tabs attached to its own GNOME Terminal window."""
    def __init__(self, *, run=subprocess.run, check=check_terminal):
        self.run = run; self.check = check; self.groups = {}; self.references = {}
        import threading
        self.lock = threading.RLock()

    def _live(self, reference):
        reply = self.run(['gdbus', 'introspect', '--session', '--dest', reference['service'],
                          '--object-path', reference['screen']], capture_output=True, text=True, timeout=3)
        return reply.returncode == 0 and 'org.gnome.Terminal.Terminal0' in reply.stdout

    def open(self, target, socket_path, token, title):
        with self.lock: return self._open(target, socket_path, token, title)

    def _open(self, target, socket_path, token, title):
        import uuid
        self.check()
        if not shutil.which('gdbus'): raise RuntimeError('按开发板管理标签页需要 gdbus，请安装 libglib2.0-bin')
        key = target_key(target); live = []
        for reference in self.groups.get(key, []):
            try: alive = self._live(reference)
            except (OSError, subprocess.TimeoutExpired): alive = False
            if alive: live.append(reference)
            else: self.references.pop(reference['token'], None)
        self.groups[key] = live
        environment = dict(os.environ)
        for name in ('GNOME_TERMINAL_SERVICE', 'GNOME_TERMINAL_SCREEN'): environment.pop(name, None)
        if live:
            environment.update(GNOME_TERMINAL_SERVICE=live[0]['service'], GNOME_TERMINAL_SCREEN=live[0]['screen'])
        tab_title = f'{title} [{uuid.uuid4().hex[:8]}]'
        args = ['gnome-terminal', '--print-environment', '--tab' if live else '--window',
                '--active', '--title', tab_title, '--', *helper_argv(socket_path, token)]
        reply = self.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                         env=environment, timeout=10, start_new_session=True)
        if reply.returncode: raise RuntimeError('无法打开开发板终端标签页：' + reply.stderr.strip())
        values = dict(line.split('=', 1) for line in reply.stdout.splitlines() if '=' in line)
        service = values.get('GNOME_TERMINAL_SERVICE', ''); screen = values.get('GNOME_TERMINAL_SCREEN', '')
        if not service.startswith(':') or not screen.startswith('/org/gnome/Terminal/'):
            raise RuntimeError('GNOME Terminal 未返回标签页标识，无法管理此会话')
        reference = dict(service=service, screen=screen, token=token, title=tab_title)
        live.append(reference); self.references[token] = reference
        return reference

    def focus(self, token):
        import re
        reference = self.references.get(token)
        if reference is None: return False
        try:
            if not self._live(reference): return False
            reply = self.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus',
                              '--object-path', '/org/freedesktop/DBus', '--method',
                              'org.freedesktop.DBus.GetConnectionUnixProcessID', reference['service']],
                             capture_output=True, text=True, timeout=3)
            match = re.search(r'uint32 (\d+)', reply.stdout)
            if reply.returncode or not match: return False
            args = helper_argv('', '')[:-5] + ['--focus-terminal', '--title=' + reference['title'], '--pid', match[1]]
            result = self.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=4)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired): return False
