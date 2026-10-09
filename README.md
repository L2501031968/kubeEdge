# KubeEdge Board

面向 KubeEdge 实验环境的图形化控制台：一键开机 / 关机流程编排、组件副本调整、
调度落点切换、UE 上下电、节点与资源视图、执行审计。

后端只用 Python 3 标准库，前端是原生 HTML/CSS/JS：从 GitHub 克隆下来就能运行，
不需要 pip、npm 或任何编译步骤。同一套程序有两种角色：

- `host`：部署在云主机上，用 kubectl 操作集群，负责流程编排与资源视图。
- `edge`：部署在边缘从机上，负责 UE 上下电、Amarisoft 模式切换、设备与服务检查。

主机界面上的边缘按钮可以转发到 `edge` 角色的控制台，所以一台机器就能点完整个流程。

## 能做什么

| 入口 | 按钮 | 实际动作 |
| --- | --- | --- |
| 一键流程 | 一键启动 | 按 core → ims → ran → sat → ue 顺序 `kubectl scale --replicas=1` |
| 一键流程 | 一键关机 | 按 ue → sat → ran → ims → core 顺序缩到 0，并确认 Pod 已回收 |
| 组件与副本 | 调整组件副本数 | `kubectl scale deployment/<组件> --replicas=N` |
| 组件与副本 | 滚动重启组件 | `kubectl rollout restart`，不改变副本数 |
| 组件与副本 | 指定组件调度到节点 | 写 `nodeSelector`（`kb.local/name`）后等待滚动更新 |
| 边缘设备 | UE 上电 / 下电 | `amari-ue-power on\|off 1` |
| 边缘设备 | 读取 UE 状态 | `amari-ue-status --once` |
| 边缘设备 | 切换 Amarisoft 模式 | `amari-mode status\|edge\|local` |
| 边缘设备 | 切换 UE 网络模式 | `amari-ue-network-mode direct\|bridge` |
| 维护 | 检查边缘服务与端口 | `systemctl is-active tailscaled/containerd/edgecore`、`ss -ntp` |
| 维护 | 列出边缘容器 | `nerdctl --namespace k8s.io ps` |
| 维护 | 设备与网络探测 | `/dev/sdr0`、KYSEC `getstatus`、`tailscale status` |
| 维护 | 集群总览命令 | `kubectl get nodes -o wide`、最近集群事件 |
| 维护 | 关闭云侧主机 | `sudo shutdown -h now`（默认禁用，需显式打开） |

每个按钮都会列出实际执行的完整命令，需要确认的操作会弹二次确认，结果逐条显示
stdout / stderr，并写入审计日志 `~/.kb/audit.jsonl`。

## 快速开始：一台 Ubuntu 虚拟机跑通全部功能

用 `kind` 在一台虚拟机里模拟三节点集群，节点身份用 `kb.local/name` 标签表达：

```
cloud        控制面 + KubeEdge 云侧
d2000-edge   U3 基站（amari-core / ims / ran / sat 占位）
sdr          UE 节点（amari-ue 占位）
```

建议配置：Ubuntu 22.04 / 24.04 / 25.10，4 vCPU、8 GB 内存、30 GB 磁盘、能联网。
内存小于 6 GB 时脚本会自动降级成单节点集群，所有组件挤在同一台机器上。

```bash
# 1. 把仓库拷进虚拟机，例如 ~/kubeedge_board
cd ~/kubeedge_board

# 2. 一键准备环境：docker + kubectl + kind + 三节点集群 + 占位工作负载
#    + metrics-server + 模拟的 amari-* 命令
sudo bash scripts/bootstrap-vm.sh

# 3. 启动主机控制台（读取 kb.json，监听 0.0.0.0:8080）
cp kb.json.example kb.json
bash scripts/run-console.sh

# 4. 另开一个终端，启动边缘模拟控制台（监听 0.0.0.0:8081）
KB_ROLE=edge KB_SIM=1 KB_LISTEN=0.0.0.0:8081 bash scripts/run-console.sh
```

浏览器打开 `http://<虚拟机IP>:8080`。点“一键启动”，五个组件按顺序拉起；
把“执行位置”切到 `edge-sim` 再点“UE 上电”，命令会转发给 8081 的边缘控制台执行。

打不开页面时，先在虚拟机里放行端口：`sudo ufw allow 8080/tcp && sudo ufw allow 8081/tcp`，
并确认虚拟机网络模式（NAT 需要端口映射，桥接直接用 `hostname -I` 显示的 IP）。

## 界面说明

- 环境体检：集群连通性、节点就绪、metrics 可用性等自检结果。
- 操作：按分组排列的按钮，带参数输入、二次确认和执行位置选择。
- 节点：每台节点的 CPU 申请、CPU 使用（需要 metrics-server）、内存申请、Pod 数、taint。
- 工作负载：组件副本数、每 Pod 的 CPU 申请 / 限额、申请总量、落点节点。
- Pod：状态、重启次数、CPU 申请 / 限额 / 使用、内存申请、QoS 等级。
- 执行记录：审计日志里最近 40 条命令及其返回码。

页面每 15 秒自动刷新，右上角徽章标明当前是「主机模式 / 边缘模式」以及是否模拟环境。

## 从模拟环境切到真实设备

仓库不绑定具体命令实现：`sim/` 里是模拟版 `amari-*`，真实设备上换成你自己的脚本即可，
只要命令名与参数保持一致（`amari-ue-power on|off 1`、`amari-mode edge` 等）。

云主机（`host` 角色）：

```bash
# 先给真实集群的节点打上控制台认识的标签
kubectl label node <基站节点> kb.local/name=d2000-edge kb.local/role=edge --overwrite
kubectl label node <UE节点>   kb.local/name=sdr        kb.local/role=edge --overwrite
kubectl label node <云侧节点> kb.local/name=cloud      kb.local/role=cloud --overwrite

cp kb.json.example kb.json     # 把 agents 改成边缘从机的地址
KB_LISTEN=0.0.0.0:8080 bash scripts/run-console.sh
```

边缘从机（`edge` 角色，每台机器一份仓库）：

```bash
sudo bash sim/install-sim.sh           # 先用模拟命令验证界面与流程
# 换成真实脚本后，再显式打开真实设备权限：
KB_ROLE=edge KB_LISTEN=0.0.0.0:8081 KB_ALLOW_REAL_HARDWARE=1 bash scripts/run-console.sh
```

`kb.json` 里把边缘从机登记成主机可转发的目标：

```json
{
  "role": "host",
  "listen": "0.0.0.0:8080",
  "allow_real_hardware": false,
  "agents": [
    { "name": "d2000-edge", "url": "http://192.168.1.50:8081" },
    { "name": "sdr",        "url": "http://192.168.1.51:8081" }
  ]
}
```

### 环境变量

| 变量 | 作用 |
| --- | --- |
| `KB_ROLE` | `host` / `edge`，部署角色 |
| `KB_LISTEN` | 监听地址，不设置时用 `kb.json` 里的 `listen` |
| `KB_SIM` | `1` = 边缘命令走 `sim/` 的模拟实现 |
| `KB_DEMO` | `1` = 用内置演示数据启动，完全不需要集群 |
| `KB_DRY_RUN` | `1` = 只打印命令不执行 |
| `KB_KUBECONFIG` | 指定 kubeconfig 路径 |
| `KB_ALLOW_REAL_HARDWARE` | `1` = 允许操作真实设备（默认关闭） |

`bootstrap-vm.sh` 另外接受 `KB_NODES=1|3`、`KB_DOCKER_MIRRORS`、`KB_INNER_MIRROR`、
`KB_IMAGE_MIRRORS`、`KB_KIND_IMAGE`、`KB_KUBECTL_URL`、`KB_KIND_URL`，
国内网络可以先配镜像源再执行。默认会通过 `m.daocloud.io` 兜底拉取 Docker Hub
与 `registry.k8s.io` 镜像；也可以改成逗号分隔的镜像前缀列表。

## 不用集群也能看界面

```bash
python3 -m kb --demo --listen 127.0.0.1:8080   # 内置演示数据
python3 -m kb --print-actions                  # 打印当前角色的操作目录（JSON）
python3 -m kb --print-discovery                # 打印一次环境体检结果（JSON）
```

## 安全边界

- 默认只监听 `127.0.0.1`；`run-console.sh` 为方便局域网访问默认监听 `0.0.0.0:8080`，
  请只在可信网络里使用，或自行加反向代理与鉴权。
- 触碰真实硬件的动作（上下电、模式切换、整机关机）在实时模式下默认禁用，需要
  `--sim` 或 `allow_real_hardware` 才放行；`host_shutdown` 必须显式打开
  `allow_real_hardware`。
- 命令一律以 argv 列表执行，不经过 shell 拼接；执行记录写入 `~/.kb/audit.jsonl`。

## 目录结构

```
kb/                 控制台程序（host / edge 两种角色）
  actions.py        操作目录：把流程翻译成命令序列
  webui/static/     前端页面与样式
sim/                模拟的 amari-* 命令，供虚拟机与演示使用
deploy/k8s/         测试环境占位工作负载、metrics-server
deploy/kind/        kind 集群配置（三节点 / 单节点）
scripts/            VM 引导与控制台启动脚本
tests/              单元测试（python3 -m unittest discover -s tests）
```

## 常见问题

- Pod 一直 Pending：五个组件的 CPU 申请加起来约 2 核，虚拟机至少给 4 vCPU；
  用 `kubectl get events -A --sort-by=.lastTimestamp` 看调度原因。
- 创建集群时报 `registry-1.docker.io ... connection refused`：这是 Docker Hub
  网络被阻断或 DNS 被污染。新版脚本会自动尝试 `m.daocloud.io`；如仍失败可指定
  `KB_IMAGE_MIRRORS=<镜像站1>,<镜像站2>` 后重跑 `bootstrap-vm.sh`。
- Pod 卡在 ImagePullBackOff：重跑 `bootstrap-vm.sh`，脚本会预拉 pause、busybox 和
  metrics-server 并导入 kind；也可以手动执行 `docker pull <镜像>` 后
  `kind load docker-image <镜像> --name kb-sim`。
- 显示「实时监控未启用」：metrics-server 还没就绪，等一两分钟刷新，其余功能不受影响。
- 只想调某个组件而不走整套流程：用「调整组件副本数」或「滚动重启组件」。

## License

MIT
