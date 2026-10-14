#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_root="${XDG_DATA_HOME:-$HOME/.local/share}"
destination="$data_root/applications"
icon_directory="$data_root/icons/hicolor/scalable/apps"

# Escape both the Exec argument and the surrounding desktop-entry string layer.
escape_exec() {
  local escaped="$1"
  escaped="${escaped//\\/\\\\}"
  escaped="${escaped//\"/\\\"}"
  escaped="${escaped//\`/\\\`}"
  escaped="${escaped//\$/\\\$}"
  escaped="${escaped//%/%%}"
  escaped="${escaped//\\/\\\\}"
  printf '"%s"' "$escaped"
}

if [[ -x "$root/ssh-multi-tool" ]]; then
  exec_line="$(escape_exec /usr/bin/env) $(escape_exec "$root/ssh-multi-tool")"
  icon="$root/app-icon.svg"
else
  source_root="$(cd -- "$root/.." && pwd)"
  if [[ ! -f "$source_root/run.py" || ! -f "$source_root/start.sh" ]]; then
    echo "未找到可执行程序或源码启动脚本。请在解压目录或源码 packaging 目录运行。" >&2; exit 1
  fi
  exec_line="$(escape_exec /usr/bin/env) $(escape_exec bash) $(escape_exec "$source_root/start.sh")"
  icon="$source_root/ssh_multi_tool/assets/app-icon.svg"
fi
if [[ ! -f "$icon" ]]; then echo "缺少应用图标：$icon" >&2; exit 1; fi
mkdir -p -- "$destination" "$icon_directory"
cp -- "$icon" "$icon_directory/ssh-multi-tool.svg"
cat > "$destination/ssh-multi-tool.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SSH 多终端启动工具
Comment=一键启动远端开发环境与多个程序
Exec=$exec_line
Icon=ssh-multi-tool
StartupWMClass=Sshmultitool
Terminal=false
Categories=Development;
EOF
if command -v gtk-update-icon-cache >/dev/null; then
  gtk-update-icon-cache -f -t "$data_root/icons/hicolor" >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null; then
  update-desktop-database "$destination" >/dev/null 2>&1 || true
fi
echo "已安装应用图标并添加菜单入口。移动程序目录后请重新运行此脚本。"
