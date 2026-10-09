#!/usr/bin/env bash
# 安装模拟的 amari-* 命令，供 kind 测试环境联调使用。
set -euo pipefail

PREFIX="${KB_SIM_PREFIX:-/usr/local/bin}"
STATE_DIR="${KB_SIM_STATE:-/var/lib/kb-sim}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SUDO=()
if [ "$(id -u)" -ne 0 ]; then
  SUDO=(sudo)
fi

COMMANDS=(amari-ue-power amari-ue-status amari-mode amari-ue-network-mode)

"${SUDO[@]}" install -d -m 1777 "$STATE_DIR"
for name in "${COMMANDS[@]}"; do
  "${SUDO[@]}" install -m 0755 "$HERE/$name" "$PREFIX/$name"
done

set_default() {
  local file="$1"
  local value="$2"
  if [ ! -f "$STATE_DIR/$file" ]; then
    "${SUDO[@]}" install -m 0666 /dev/null "$STATE_DIR/$file"
    printf '%s\n' "$value" | "${SUDO[@]}" tee "$STATE_DIR/$file" > /dev/null
  fi
}

# 状态文件保持世界可写，普通用户运行控制台时才能改状态。
set_default ue-power off
set_default amari-mode edge-container
set_default ue-network-mode direct

echo "已安装模拟命令到 $PREFIX"
for name in "${COMMANDS[@]}"; do
  echo "  - $name"
done
echo "状态目录: $STATE_DIR（世界可写，仅用于测试环境）"
echo
echo "快速自检："
echo "  amari-ue-power on 1"
echo "  amari-ue-status --once"
echo "  amari-mode status"
