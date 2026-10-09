#!/usr/bin/env bash
# 启动 KubeEdge Board 控制台。
#
# 常用环境变量：
#   KB_ROLE=edge            以边缘节点角色启动
#   KB_SIM=1                使用模拟设备命令
#   KB_DEMO=1               使用内置演示数据（不需要集群）
#   KB_DRY_RUN=1            只打印命令不执行
#   KB_LISTEN=0.0.0.0:8080  监听地址（不设置时用 kb.json 里的 listen）
#   KB_KUBECONFIG=...       指定 kubeconfig
#   KB_ALLOW_REAL_HARDWARE=1 允许操作真实设备（默认关闭）
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

command -v python3 > /dev/null 2>&1 || { echo "缺少 python3，请先安装" >&2; exit 1; }

ROLE="${KB_ROLE:-host}"
LISTEN="${KB_LISTEN:-}"

ARGS=(--role "$ROLE")
[ -n "$LISTEN" ] && ARGS+=(--listen "$LISTEN")
[ "${KB_SIM:-0}" = "1" ] && ARGS+=(--sim)
[ "${KB_DEMO:-0}" = "1" ] && ARGS+=(--demo)
[ "${KB_DRY_RUN:-0}" = "1" ] && ARGS+=(--dry-run)
[ "${KB_ALLOW_REAL_HARDWARE:-0}" = "1" ] && ARGS+=(--allow-real-hardware)
[ -n "${KB_KUBECONFIG:-}" ] && ARGS+=(--kubeconfig "$KB_KUBECONFIG")

if [ ! -f "$REPO_DIR/kb.json" ]; then
  echo "提示: 未发现 kb.json，使用默认配置；需要边缘转发时执行 cp kb.json.example kb.json"
fi

VM_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -n "$LISTEN" ]; then
  echo "角色: $ROLE    监听: $LISTEN"
  if [ -n "$VM_IP" ]; then
    echo "浏览器访问: http://$VM_IP:${LISTEN##*:}"
  fi
else
  echo "角色: $ROLE    监听: 由 kb.json 决定（默认 127.0.0.1:8080）"
fi

exec python3 -m kb "${ARGS[@]}" "$@"
