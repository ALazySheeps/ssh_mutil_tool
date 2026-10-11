#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$(uname -s)" != Linux ]]; then
  echo "此安装脚本仅用于Ubuntu/Linux。" >&2; exit 1
fi
if ! command -v python3 >/dev/null || ! python3 -c 'import tkinter, venv; import sys; assert sys.version_info >= (3,10)' 2>/dev/null; then
  echo "缺少Python 3.10+、Tk或venv。Ubuntu请先运行：" >&2
  echo "  sudo apt install python3 python3-tk python3-venv gnome-terminal fonts-noto-cjk" >&2
  exit 1
fi
python3 -m venv "$root/.venv"
"$root/.venv/bin/python" -m pip install --timeout 120 --retries 5 -r "$root/requirements.txt"
echo "安装完成。启动：bash \"$root/start.sh\""
