#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_executable="${SSHMT_PYTHON:-python3}"
if [[ -z "${SSHMT_PYTHON:-}" && -x "$root/.venv/bin/python" ]]; then
  exec "$root/.venv/bin/python" "$root/run.py" "$@"
fi
if ! command -v "$python_executable" >/dev/null; then
  echo "源码版需要 Python 3.8+。请阅读 README.md 或使用 Ubuntu 预构建包。" >&2
  exit 1
fi
if ! "$python_executable" -c 'import tkinter, paramiko; import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then
  echo "请先运行：bash install-ubuntu.sh（仅用于源码版首次安装）" >&2
  exit 1
fi
exec "$python_executable" "$root/run.py" "$@"
