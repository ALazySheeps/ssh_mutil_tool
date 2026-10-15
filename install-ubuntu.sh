#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_executable="${SSHMT_PYTHON:-python3}"
if [[ "$(uname -s)" != Linux ]]; then
  echo "此安装脚本仅用于Ubuntu/Linux。" >&2; exit 1
fi
if ! command -v "$python_executable" >/dev/null || ! "$python_executable" -c 'import tkinter, venv; import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then
  echo "缺少Python 3.8+、Tk或venv。Ubuntu请先运行：" >&2
  echo "  sudo apt install python3 python3-tk python3-venv gnome-terminal fonts-noto-cjk libglib2.0-bin libatspi2.0-0" >&2
  exit 1
fi
if [[ -x "$root/.venv/bin/python" ]]; then
  selected_version="$("$python_executable" -c 'import sys; print(sys.version_info[:2])')"
  installed_version="$("$root/.venv/bin/python" -c 'import sys; print(sys.version_info[:2])')"
  if [[ "$selected_version" != "$installed_version" ]]; then
    echo "现有 .venv 的Python版本不同，请移走 .venv 后重新安装。" >&2; exit 1
  fi
fi
"$python_executable" -m venv "$root/.venv"
"$root/.venv/bin/python" -m pip install --timeout 120 --retries 5 --upgrade "pip>=23.1"
"$root/.venv/bin/python" -m pip install --timeout 120 --retries 5 -r "$root/requirements.txt"
echo "安装完成。启动：bash \"$root/start.sh\""
