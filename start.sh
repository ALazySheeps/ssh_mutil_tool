#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$root/.venv/bin/python" ]]; then
  exec "$root/.venv/bin/python" "$root/run.py" "$@"
fi
if ! command -v python3 >/dev/null; then
  echo "源码版需要 Python 3.10+。请阅读 README.md 或使用 Ubuntu 预构建包。" >&2
  exit 1
fi
if ! python3 -c 'import tkinter, paramiko' 2>/dev/null; then
  echo "请先运行：bash install-ubuntu.sh（仅用于源码版首次安装）" >&2
  exit 1
fi
exec python3 "$root/run.py" "$@"
