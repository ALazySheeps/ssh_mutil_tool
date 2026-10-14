#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$root"
python_executable="${SSHMT_PYTHON:-python3}"
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo "请在Ubuntu x86_64构建。PyInstaller不能在Windows生成Ubuntu包。" >&2; exit 1
fi
if ! command -v objdump >/dev/null; then
  echo "构建需要binutils，请先运行：sudo apt install binutils" >&2; exit 1
fi
"$python_executable" -c 'import tkinter; import sys; assert sys.version_info >= (3,8)'
if [[ -x .build-venv/bin/python ]]; then
  selected_version="$("$python_executable" -c 'import sys; print(sys.version_info[:2])')"
  build_version="$(.build-venv/bin/python -c 'import sys; print(sys.version_info[:2])')"
  if [[ "$selected_version" != "$build_version" ]]; then
    echo "构建环境Python版本不同，请移走 .build-venv 后重新构建。" >&2; exit 1
  fi
fi
if [[ ! -x .build-venv/bin/python ]]; then "$python_executable" -m venv .build-venv; fi
.build-venv/bin/python -m pip install --timeout 120 --retries 5 --upgrade "pip>=23.1"
.build-venv/bin/python -m pip install --timeout 120 --retries 5 -r requirements.txt 'pyinstaller>=6,<7'
.build-venv/bin/python -m unittest discover -s tests -v
.build-venv/bin/python -m PyInstaller --noconfirm --clean --onedir --console \
  --name ssh-multi-tool --collect-all paramiko --add-data 'examples:examples' \
  --add-data 'ssh_multi_tool/assets:ssh_multi_tool/assets' run.py
cp README.md LICENSE dist/ssh-multi-tool/
cp ssh_multi_tool/assets/app-icon.svg dist/ssh-multi-tool/app-icon.svg
cp docs/quickstart.md dist/ssh-multi-tool/QUICKSTART.md
cp docs/verification.md dist/ssh-multi-tool/VERIFICATION.md
.build-venv/bin/python packaging/licenses.py dist/ssh-multi-tool/THIRD_PARTY_LICENSES
.build-venv/bin/python -m pip freeze > dist/ssh-multi-tool/BUILD_DEPENDENCIES.txt
cp packaging/install-desktop.sh dist/ssh-multi-tool/
chmod +x dist/ssh-multi-tool/ssh-multi-tool dist/ssh-multi-tool/install-desktop.sh
platform_tag="$(.build-venv/bin/python -m ssh_multi_tool.compat)"
archive="dist/ssh-multi-tool-0.1.0-${platform_tag}-x86_64.tar.gz"
tar -czf "$archive" -C dist ssh-multi-tool
echo "已生成 $archive；发布前须完成docs/verification.md的平台验收。"
