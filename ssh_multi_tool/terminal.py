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
