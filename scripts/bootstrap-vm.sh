#!/usr/bin/env bash
# 在 Ubuntu 虚拟机上准备 kind 测试环境：
#   docker + kubectl + kind + 三节点集群 + 占位工作负载 + metrics-server + 模拟 amari 命令
#
# 用法： sudo bash scripts/bootstrap-vm.sh
# 常用环境变量：
#   KB_NODES=1|3              强制节点数（默认按内存自动选择）
#   KB_DOCKER_MIRRORS=a,b     docker 守护进程镜像加速（宿主机拉取 kind 节点镜像用）
#   KB_INNER_MIRROR=url       集群内 containerd 镜像加速（节点内拉取镜像用）
#   KB_KIND_IMAGE=...         kind 节点镜像，留空则用 kind 默认镜像
set -euo pipefail

CLUSTER_NAME="${KB_CLUSTER_NAME:-kb-sim}"
KUBECTL_VERSION="${KB_KUBECTL_VERSION:-v1.30.14}"
KIND_VERSION="${KB_KIND_VERSION:-v0.30.0}"
KIND_IMAGE="${KB_KIND_IMAGE-kindest/node:v1.30.14}"
METRICS_IMAGE="registry.k8s.io/metrics-server/metrics-server:v0.7.2"
BUSYBOX_IMAGE="busybox:1.36"
NODE_COUNT="${KB_NODES:-}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[warn] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[error] %s\033[0m\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
  die "请用 root 运行：sudo bash scripts/bootstrap-vm.sh"
fi

TARGET_USER="${SUDO_USER:-root}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
TARGET_GROUP="$(id -gn "$TARGET_USER")"

case "$(uname -m)" in
  x86_64)  ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) die "暂不支持的架构：$(uname -m)" ;;
esac

log "环境检查"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  echo "系统: ${PRETTY_NAME:-unknown}  架构: ${ARCH}"
  case "${VERSION_ID:-}" in
    22.04|24.04) : ;;
    *) warn "当前系统版本为 ${VERSION_ID:-unknown}，脚本按 Ubuntu 通用流程执行；25.10 等新版本如有报错请把输出发出来。" ;;
  esac
fi
echo "仓库目录: $REPO_DIR"
echo "操作用户: $TARGET_USER"

log "安装 docker / 基础工具"
export DEBIAN_FRONTEND=noninteractive
apt-get update
if ! apt-get install -y --no-install-recommends ca-certificates curl conntrack iptables docker.io; then
  die "安装 docker.io 失败。Ubuntu 25.10 等新版本如果缺 universe 源，请先执行：
  sudo add-apt-repository -y universe && sudo apt-get update
然后重新运行本脚本。"
fi
systemctl enable --now docker

if [ -n "${KB_DOCKER_MIRRORS:-}" ]; then
  log "写入 docker 镜像加速：$KB_DOCKER_MIRRORS"
  mkdir -p /etc/docker
  python3 - "$KB_DOCKER_MIRRORS" > /etc/docker/daemon.json <<'PY'
import json
import sys

mirrors = [item.strip() for item in sys.argv[1].split(",") if item.strip()]
print(json.dumps({"registry-mirrors": mirrors}, indent=2, ensure_ascii=False))
PY
  systemctl restart docker
fi

log "安装 kubectl ${KUBECTL_VERSION}"
if command -v kubectl > /dev/null 2>&1; then
  echo "已存在：$(kubectl version --client=true --output=yaml 2>/dev/null | awk '/gitVersion/{print $2; exit}' || true)"
else
  KUBECTL_URL="${KB_KUBECTL_URL:-https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl}"
  curl -fsSL -o /usr/local/bin/kubectl "$KUBECTL_URL" \
    || die "下载 kubectl 失败：$KUBECTL_URL （国内网络可用 KB_KUBECTL_URL 指向镜像地址）"
  chmod +x /usr/local/bin/kubectl
fi

log "安装 kind ${KIND_VERSION}"
if command -v kind > /dev/null 2>&1; then
  echo "已存在：$(kind version)"
else
  KIND_URL="${KB_KIND_URL:-https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-${ARCH}}"
  curl -fsSL -o /usr/local/bin/kind "$KIND_URL" \
    || die "下载 kind 失败：$KIND_URL （可用 KB_KIND_URL 指定镜像下载地址）"
  chmod +x /usr/local/bin/kind
fi

if [ -z "$NODE_COUNT" ]; then
  mem_kb="$(awk '/MemTotal/{print $2}' /proc/meminfo)"
  if [ "$mem_kb" -lt 6291456 ]; then
    NODE_COUNT=1
    warn "内存不足 6G，改用单节点集群（KB_NODES=3 可强制三节点）"
  else
    NODE_COUNT=3
  fi
fi

CONFIG_SRC="$REPO_DIR/deploy/kind/kind-cluster.yaml"
if [ "$NODE_COUNT" = "1" ]; then
  CONFIG_SRC="$REPO_DIR/deploy/kind/kind-cluster-1node.yaml"
fi

TMP_CONFIG="$(mktemp)"
cp "$CONFIG_SRC" "$TMP_CONFIG"

if [ -n "${KB_INNER_MIRROR:-}" ]; then
  log "写入集群内镜像加速：$KB_INNER_MIRROR"
  cat >> "$TMP_CONFIG" <<EOF
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry.mirrors."docker.io"]
      endpoint = ["$KB_INNER_MIRROR"]
    [plugins."io.containerd.grpc.v1.cri".registry.mirrors."registry.k8s.io"]
      endpoint = ["$KB_INNER_MIRROR"]
EOF
fi

log "创建 kind 集群（$NODE_COUNT 节点，名称 $CLUSTER_NAME）"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  echo "集群已存在，跳过创建"
else
  created=0
  if [ -n "$KIND_IMAGE" ]; then
    if kind create cluster --name "$CLUSTER_NAME" --config "$TMP_CONFIG" --image "$KIND_IMAGE"; then
      created=1
    else
      warn "使用 $KIND_IMAGE 创建失败，可能该版本镜像不存在或拉取被限速，改用 kind 默认镜像重试"
      kind delete cluster --name "$CLUSTER_NAME" > /dev/null 2>&1 || true
    fi
  fi
  if [ "$created" -eq 0 ]; then
    kind create cluster --name "$CLUSTER_NAME" --config "$TMP_CONFIG" \
      || die "创建集群失败。国内网络建议：先用 KB_DOCKER_MIRRORS 配置 docker 加速，或预先 docker pull 对应的 kindest/node 镜像。"
  fi
fi

kubectl config use-context "kind-$CLUSTER_NAME" > /dev/null

log "等待节点就绪"
kubectl wait --for=condition=Ready nodes --all --timeout=240s

# kind 给节点起的名字是 <集群名>-control-plane / -worker / -worker2，
# 控制台里的身份是 kb.local/name 标签（真实集群同样按这个标签对接）。
resolve_node() {
  local found
  found="$(kubectl get nodes -l "$1" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  printf '%s' "${found:-$2}"
}

log "调整节点标签与污点"
CONTROL_NODE="$(resolve_node 'kb.local/name=cloud' "$CLUSTER_NAME-control-plane")"
kubectl label node "$CONTROL_NODE" "kb.local/name=cloud" "kb.local/role=cloud" --overwrite > /dev/null

if [ "$NODE_COUNT" = "3" ]; then
  EDGE_NODE="$(resolve_node 'kb.local/name=d2000-edge' "$CLUSTER_NAME-worker")"
  SDR_NODE="$(resolve_node 'kb.local/name=sdr' "$CLUSTER_NAME-worker2")"
  kubectl label node "$EDGE_NODE" "kb.local/name=d2000-edge" "kb.local/role=edge" --overwrite > /dev/null
  kubectl label node "$SDR_NODE" "kb.local/name=sdr" "kb.local/role=edge" --overwrite > /dev/null
  kubectl taint node "$EDGE_NODE" "$SDR_NODE" kb.local/edge=true:NoSchedule --overwrite > /dev/null
  echo "云侧节点: $CONTROL_NODE"
  echo "边缘节点: $EDGE_NODE（基站） / $SDR_NODE（UE）"
else
  echo "单节点: $CONTROL_NODE（所有组件都调度到这一台机器）"
fi
# 去掉控制面污点，便于把组件手动调度到云侧节点做实验
kubectl taint node --all node-role.kubernetes.io/control-plane- > /dev/null 2>&1 || true

log "部署占位工作负载"
kubectl apply -f "$REPO_DIR/deploy/k8s/00-namespaces.yaml"
kubectl apply -f "$REPO_DIR/deploy/k8s/10-workloads.yaml"

if [ "$NODE_COUNT" = "1" ]; then
  warn "单节点模式：移除工作负载的 nodeSelector，让所有组件落在同一个节点上"
  for ns in amari-d2000 amari-edge; do
    kubectl -n "$ns" patch deployment --all --type=json \
      -p='[{"op":"remove","path":"/spec/template/spec/nodeSelector"}]' > /dev/null 2>&1 || true
  done
fi

preload_image() {
  local image="$1"
  docker pull "$image" > /dev/null 2>&1 || return 1
  kind load docker-image "$image" --name "$CLUSTER_NAME" > /dev/null 2>&1 || return 1
  return 0
}

log "安装 metrics-server"
if preload_image "$METRICS_IMAGE"; then
  echo "镜像已在宿主机拉取并导入集群"
else
  warn "宿主机拉取 metrics-server 镜像失败，交给集群自行拉取；若失败界面会显示「实时监控未启用」"
fi
kubectl apply -f "$REPO_DIR/deploy/k8s/30-metrics-server.yaml"
if ! kubectl -n kube-system rollout status deployment/metrics-server --timeout=180s; then
  warn "metrics-server 未在 3 分钟内就绪，可以先继续使用其它功能"
fi

log "可选：加载 CPU 压力 Pod"
if preload_image "$BUSYBOX_IMAGE"; then
  kubectl apply -f "$REPO_DIR/deploy/k8s/20-load-generator.yaml"
else
  warn "跳过 amari-burn（busybox 镜像不可用）"
fi

log "安装模拟 amari 命令"
bash "$REPO_DIR/sim/install-sim.sh"

log "同步 kubeconfig 给 $TARGET_USER"
if [ "$TARGET_USER" != "root" ]; then
  install -d -o "$TARGET_USER" -g "$TARGET_GROUP" -m 0700 "$TARGET_HOME/.kube"
  install -o "$TARGET_USER" -g "$TARGET_GROUP" -m 0600 /root/.kube/config "$TARGET_HOME/.kube/config"
  echo "已写入 $TARGET_HOME/.kube/config"
else
  echo "以 root 运行，kubeconfig 位于 /root/.kube/config"
fi

VM_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

cat <<EOF

$(printf '\033[1;32m环境准备完成\033[0m')

接下来（在仓库目录 $REPO_DIR 下执行）：

  1. 主机控制台
     cp kb.json.example kb.json   # 单机测试不用改，agents 已经指向本机 8081
     bash scripts/run-console.sh

  2. 边缘模拟控制台（另开一个终端）
     KB_ROLE=edge KB_SIM=1 KB_LISTEN=0.0.0.0:8081 bash scripts/run-console.sh

  3. 浏览器打开 http://${VM_IP:-<虚拟机IP>}:8080
     主机页面上的边缘按钮会转发到 8081 的模拟边缘控制台。

查看集群： kubectl get nodes -o wide
自检命令： kubectl get pods -A | grep amari; amari-ue-status --once

提示：如果 Pod 一直 Pending/ImagePullBackOff，先看 kubectl get events -A --sort-by=.lastTimestamp，
      国内网络可以在重跑本脚本时加 KB_INNER_MIRROR=<镜像加速地址>。
EOF
