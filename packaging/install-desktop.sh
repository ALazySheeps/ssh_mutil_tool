#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
destination="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p -- "$destination"
escaped="${root//\\/\\\\}"
escaped="${escaped//\"/\\\"}"
escaped="${escaped//\`/\\\`}"
escaped="${escaped//\$/\\\$}"
escaped="${escaped//%/%%}"
cat > "$destination/ssh-multi-tool.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SSH 多终端启动工具
Comment=一键启动远端开发环境与多个程序
Exec="$escaped/ssh-multi-tool"
Terminal=false
Categories=Development;Utility;
EOF
echo "已添加应用菜单入口。移动解压目录后请重新运行此脚本。"
