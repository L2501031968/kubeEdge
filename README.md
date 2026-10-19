# 星地融合通信异构资源协同管理平台

面向 KubeEdge 的星地融合通信异构资源协同管理平台：把 NTN-RAN、核心网、UE、
AI 模型与加速模型按业务分面呈现，并把组件分配 / 回收、调度落点、UE 上下电、
异构资源监控和执行审计收敛到同一套图形界面。

后端只用 Python 3 标准库，前端是原生 HTML/CSS/JS：从 GitHub 克隆下来就能运行，
不需要 pip、npm 或任何编译步骤。同一套程序有两种角色：

- `host`：部署在云主机上，用 kubectl 操作集群，负责流程编排与资源视图。
- `edge`：部署在边缘从机上，负责 UE 上下电、Amarisoft 模式切换、设备与服务检查。

界面使用 `云端控制节点 / NTN-RAN / UE` 等展示名，不会改写集群里的真实节点名、
Deployment 名或标签值。因此真实设备、KubeEdge 对象和命令行仍保持原有命名。

「运行实例」页的节点、工作负载和 Pod 默认只列业务条目，系统命名空间（`kube-system`、
`kube-public`、`kube-node-lease`、`kubeedge`）的条目要点选「显示系统工作负载」/
「显示系统 Pod」才会出现，保证业务组件不被系统项淹没。

主机界面的“执行位置”会新开标签页跳到 `edge` 角色的控制台，边缘控制台的“执行位置”里固定有
一项“云主机控制台”，能再跳回来，所以一台机器就能点完整个流程，来回切换也不会把当前页面顶掉。

## 能做什么

| 入口 | 按钮 | 实际动作 |
| --- | --- | --- |
| 环境准备 | 一键执行本机环境准备 | 在页面里执行 `amari-host-prepare.sh` / `amari-ue-prepare.sh`（`sudo -n`，以脚本所在目录为工作目录） |
| 云侧资源 | 启动完整业务链路 | 按 core → ims → ran → sat → ue 顺序 `kubectl scale --replicas=1` |
| 云侧资源 | 启动 U3 四个组件 | 只拉起 core → ims → ran → sat，不碰 UE |
| 云侧资源 | 启动核心网 Core / IMS服务 / 基站 RAN / 卫星 SAT | 单独把对应 Deployment 的副本数设为 1，适合按需启动（UE 跟“启动完整业务链路”一起拉起） |
| 云侧资源 | 回收业务组件 | 按 ue → sat → ran → ims → core 顺序缩到 0，并等待 Pod 回收完成 |
| 云侧资源 | 添加工作负载 | 表单生成 YAML（可编辑），经 stdin 执行 `kubectl apply -f -` |
| 组件与副本 | 调整组件副本数 | `kubectl scale deployment/<组件> --replicas=N` |
| 组件与副本 | 滚动重启组件 | `kubectl rollout restart`，不改变副本数 |
| 组件与副本 | 指定组件调度到节点 | 写 `nodeSelector`（`kb.local/name`）后等待滚动更新 |
| 边缘设备 | UE 上电 / 下电 | `amari-ue-power on\|off 1` |
| 边缘设备 | 读取 UE 状态 | `amari-ue-status --once` |
| 边缘设备 | 切换 Amarisoft 模式 | `amari-mode status\|edge\|local` |
| 边缘设备 | 切换 UE 网络模式 | `amari-ue-network-mode direct\|bridge` |
| 维护 | 检查边缘服务与端口 | `systemctl is-active tailscaled/containerd/edgecore`、`ss -ntp`、`fuser -v /dev/sdr0` |
| 维护 | 列出边缘容器 | 自动按 `nerdctl` → `crictl` → `ctr` 选择可用工具；三者都缺失时提示这是可选诊断，不影响 KubeEdge |
| 维护 | 设备与网络探测 | `/dev/sdr0`、KYSEC `getstatus`、`tailscale status` |
| 维护 | 集群总览命令 | `kubectl get nodes -o wide`、最近集群事件 |
| 维护 | 关闭云侧主机 | `sudo shutdown -h now`（默认禁用，需显式打开） |

普通操作会列出实际执行的完整命令，需要确认的操作会弹二次确认，并写入审计日志
`~/.kb/audit.jsonl`。每个按钮按下后都会给出明确的成功 / 失败反馈，失败时附一句简短原因，
完整输出留在执行记录里。启动类操作不会相信命令瞬间返回的副本数，而是之后每 2 秒读一次真实
Deployment 与 Pod 状态，最多等 120 秒；回收类操作只显示最终回收进度，不展开中间命令。

### 环境体检与一键准备

顶部「环境体检」面板把文档里的准备阶段检查做成了常驻三色灯，打开页面就会自动执行，不用再回终端：

- 云主机（`host`）：`kubectl`、集群可达、`tailscaled / containerd / kubelet` 三条
  `systemctl is-active`、`kubeedge` 命名空间云侧组件、节点 Ready、metrics-server、
  Device CRD、Amarisoft 工作负载是否存在。
- 边缘从机（`edge`）：`tailscaled / containerd / edgecore` 三条 `systemctl is-active`、
  `/dev/sdr0` 是否存在、`fuser -v /dev/sdr0` 占用情况（未启动 UE 时通常应为未占用）、
  KYSEC 首行是否 `disable`、EdgeCore 到云端的 `:10000 / :10004` 连接、
  `amari-ue-power / amari-ue-status / amari-mode / amari-ue-network-mode` 是否可用、
  容器查询工具（`nerdctl / crictl / ctr`）是否可用、环境准备脚本的定位结果。

环境体检不区分演示环境或真实环境：`systemctl is-active` 等检测命令返回什么，
页面就按什么状态显示。服务不存在、处于 `inactive` / `failed`，或 `kubeedge`
命名空间中没有 Ready 的云侧组件时都会显示红灯，不会为了 `kind` 模拟环境把
缺失项目改成绿灯。

容器查询工具只负责在页面里列出边缘容器，不是 KubeEdge、EdgeCore 或业务 Pod 的依赖。
三者都没有时该体检项显示黄灯，并明确提示“不影响 KubeEdge，仅无法从页面列容器”；
“列出边缘容器”按钮会在执行前给出同样的说明，而不是显示裸的 `127`。

边缘页的体检面板右上角有「一键执行本机环境准备」，点击后在页面内运行
`amari-host-prepare.sh`（U3）或 `amari-ue-prepare.sh`（sdr），执行结果、失败原因和完整输出
都留在页面上，不需要自己敲 `sudo ./amari-*.sh`。控制台按下面顺序找脚本，也可以显式指定：

1. `kb.json` 的 `prepare_script`（绝对路径，建议长期部署用这个）；
2. `~/amari-edge/amari-host-prepare.sh`；
3. `~/amari-edge/amari-ue-prepare.sh` 或 `~/amari-ue-edge/amari-ue-prepare.sh`；
4. `/opt/amari-edge/amari-host-prepare.sh` 或 `/opt/amari-ue-edge/amari-ue-prepare.sh`。

页面里的准备和检测命令都走 `sudo -n`（非交互），所以每台从机需要一次性给控制台用户配置
免密 sudo。不要给整个 Python 进程 root 权限，只授权会用到的具体命令，例如：

```
# /etc/sudoers.d/kb-board （用 visudo -f /etc/sudoers.d/kb-board 编辑，脚本必须 root 所有且不可被普通用户改写）
kb ALL=(root) NOPASSWD: /usr/bin/bash /home/kb/amari-edge/amari-host-prepare.sh
kb ALL=(root) NOPASSWD: /usr/local/bin/amari-ue-power on 1
kb ALL=(root) NOPASSWD: /usr/local/bin/amari-ue-power off 1
kb ALL=(root) NOPASSWD: /usr/local/bin/amari-mode status
kb ALL=(root) NOPASSWD: /usr/local/bin/amari-mode edge
kb ALL=(root) NOPASSWD: /usr/local/bin/amari-mode local
kb ALL=(root) NOPASSWD: /usr/local/bin/amari-ue-network-mode direct
kb ALL=(root) NOPASSWD: /usr/local/bin/amari-ue-network-mode bridge
kb ALL=(root) NOPASSWD: /usr/bin/fuser -v /dev/sdr0
kb ALL=(root) NOPASSWD: /usr/bin/ctr --namespace k8s.io containers list
```

没有配置免密时，体检里的服务灯仍然能亮，只有需要 sudo 的步骤会显示成警告并在详情里写明原因。

## 快速开始：一台 Ubuntu 虚拟机跑通全部功能

用 `kind` 在一台虚拟机里模拟三节点集群，节点身份用 `kb.local/name` 标签表达：

```
cloud        控制面 + KubeEdge 云侧，界面显示“云端控制节点”
d2000-edge   U3 基站（amari-core / ims / ran / sat 占位），界面显示“NTN-RAN”
sdr          UE 节点（amari-ue 占位），界面显示“UE”
```

建议配置：Ubuntu 22.04 / 24.04 / 25.10，4 vCPU、8 GB 内存、30 GB 磁盘、能联网。
内存小于 6 GB 时脚本会自动降级成单节点集群，所有组件挤在同一台机器上。

```bash
# 1. 把仓库拷进虚拟机，例如 ~/kubeedge_board
cd ~/kubeEdge

# 2. 一键准备环境：docker + kubectl + kind + 三节点集群 + 占位工作负载
#    + metrics-server + 模拟的 amari-* 命令
sudo bash scripts/bootstrap-vm.sh

# 3. 启动主机控制台（读取 kb.json，监听 0.0.0.0:8080）
cp kb.json.example kb.json
bash scripts/run-console.sh

# 4. 另开一个终端，启动边缘模拟控制台（监听 0.0.0.0:8081）
KB_ROLE=edge KB_SIM=1 KB_LISTEN=0.0.0.0:8081 bash scripts/run-console.sh
```

浏览器打开 `http://<虚拟机IP>:8080`。点“启动完整业务链路”，五个组件按顺序拉起；
只想拉 U3 侧就点“启动 U3 四个组件”，或者按需点单个组件按钮。

“执行位置”下拉框按当前机器的角色给出明确标签：云主机页是「本机（云主机）+ 各台边缘控制台」，
边缘页是「本机（边缘端）+ 云主机控制台」。切到另一台，会新开一个标签页跳过去，当前页面不被覆盖，
选择状态停在新选的控制台（本页按钮会提示你去新标签页操作，切回“本机”即可恢复）。
在边缘页选“云主机控制台”，就会新开标签页回到 `http://<虚拟机IP>:8080`，两个方向都能来回切。

打不开页面时，先在虚拟机里放行端口：`sudo ufw allow 8080/tcp && sudo ufw allow 8081/tcp`，
并确认虚拟机网络模式（NAT 需要端口映射，桥接直接用 `hostname -I` 显示的 IP）。

## 页面结构与功能

| 页面 | 主要内容 |
| --- | --- |
| 总览 | 环境体检三色灯、模块 / 用户 / CPU / 内存等关键指标，以及启动、回收、扩缩容、调度和边缘设备操作。 |
| 用户接入 | 在线用户、容量、会话、带宽、PRB、小区与 UE Pod 占用；支持按用户、状态、小区筛选、搜索和页内分页。 |
| 业务模块 | NTN-RAN、核心网、UE、AI 模型、加速模型分模块展示组件、Pod、落点节点、申请 / 使用量和 CPU 申请分布。 |
| 异构资源 | CPU、内存、存储、网络和集群实际上报的 GPU / NPU / FPGA / DPU / ASIC 资源池，并可按节点查看分配与剩余量。CPU 一律按「核」显示，`500m` 这类毫核会换算成 `0.5 核`。 |
| 运行实例 | 节点卡片、Deployment 和 Pod 明细；支持按节点、命名空间、状态筛选、搜索和页内分页。节点卡片默认按“业务节点 / 系统节点”分栏，工作负载与 Pod 列表同样把非系统条目排在前、系统条目单独分段。 |
| 部署与 YAML | 在云主机上填写资源类型、镜像、请求 / 限额和调度限制，生成可编辑 YAML，再执行 `kubectl apply -f -`。 |
| 执行记录 | 审计日志中最近 200 条命令、返回码和输出，支持搜索、结果筛选和页内分页。 |
| 系统设置 | 当前角色、模拟 / 实时模式、用户数据源、UE 容量和节点 / 资源标签约定。 |

### 用户接入数据源

用户接入页不猜测在线人数。没有提供数据源时会明确显示「未接入用户数据源」；读取失败时
在页面上给出路径和错误，不会把缺失值补成 0。配置项放在 `kb.json`：

```json
{
  "user_source": "/var/lib/kb/users.json",
  "ue_capacity": 64
}
```

`user_source` 指向 AMF / SMF / 设备模型导出的 JSON，单个用户至少应包含 `id` 与 `state`：

```json
{
  "source": "amf-export",
  "generated_at": "2026-10-14T10:00:00Z",
  "capacity": 64,
  "users": [
    {
      "id": "ue-1",
      "state": "connected",
      "sessions": 1,
      "bandwidth_mbps": 10,
      "prb_percent": 5,
      "rsrp_dbm": -85,
      "cell": "cell-1",
      "pod": "amari-ue-...",
      "node": "sdr"
    }
  ]
}
```

`capacity` 缺省时使用 `ue_capacity`；`connected` 和 `active` 计为在线。用户页的单用户
CPU / 内存是把 UE 模块 Pod 申请量除以在线人数得到的平均值，并会明确标注，避免把它误读为
单个用户的真实占用。

### 节点架构显示

「异构资源」页节点表的「架构」列默认读节点上的 `kubernetes.io/arch` 标签，不写死。所以在
x86 虚拟机的 kind 集群里它会如实显示 `amd64`，部署到真实的 ARM 盒子上会自动变成 `arm64`。

如果只在测试环境里想让界面与真实硬件对齐，可以在 `kb.json` 里加一条展示覆盖。它只改界面上
的文字，不会修改集群标签，也不会影响调度：

```json
{
  "node_arch_overrides": { "d2000-edge": "arm64" }
}
```

真实部署到 ARM 机器后请把这项留空（`{}`），让界面如实反映节点上报的架构。

### 节点与资源标签

节点展示名由 `kb.local/name` 映射；业务模块按组件名、Pod 标签或命名空间识别：

```text
kb.local/name=cloud               # 显示为 云端控制节点
kb.local/name=d2000-edge          # 显示为 NTN-RAN
kb.local/name=sdr                 # 显示为 UE
kb.local/role=cloud|edge          # 节点角色
kb.local/module=ntn-ran|core|ue|ai-model|accelerator
kb.local/net-bandwidth-mbps=1000  # 网络资源池容量
nvidia.com/gpu=2                  # GPU / NPU / FPGA / DPU / ASIC 按扩展资源自动发现
huawei.com/Ascend310=1
```

只在集群真实上报资源时才创建资源池。GPU、NPU、FPGA、DPU、ASIC 等没有对应扩展资源时，
页面列在「未上报」提示里，而不是显示成 0。存储、网络和实时用量缺少采集来源时同样显示
`N/A`，只有调度申请量保持可计算。

页面每 15 秒自动刷新，右上角徽章标明当前是「主机模式 / 边缘模式」以及是否模拟环境。

## 从模拟环境切到真实设备

仓库不绑定具体命令实现：`sim/` 里是模拟版 `amari-*`，真实设备上换成你自己的脚本即可，
只要命令名与参数保持一致（`amari-ue-power on|off 1`、`amari-mode edge` 等）。

云主机（`host` 角色）：

```bash
# 先给真实集群的节点打上控制台认识的标签
kubectl label node <基站节点> kb.local/name=d2000-edge kb.local/role=edge --overwrite
kubectl label node <UE节点>   kb.local/name=sdr        kb.local/role=edge --overwrite
kubectl label node <云端控制节点> kb.local/name=cloud      kb.local/role=cloud --overwrite

cp kb.json.example kb.json     # agents 填边缘从机地址，host_console 填云主机自己的地址
KB_LISTEN=0.0.0.0:8080 bash scripts/run-console.sh
```

边缘从机（`edge` 角色，每台机器一份仓库）：

```bash
sudo bash sim/install-sim.sh           # 先用模拟命令验证界面与流程
# 换成真实脚本后，再显式打开真实设备权限：
KB_ROLE=edge KB_LISTEN=0.0.0.0:8081 KB_ALLOW_REAL_HARDWARE=1 bash scripts/run-console.sh
```

`kb.json` 里登记两台机器互相跳转的地址。每台机器读的是同一份文件：`agents` 是云主机页面上能跳过去
的边缘控制台，`host_console` 是边缘页面上“云主机控制台”对应的地址。

```json
{
  "role": "host",
  "listen": "0.0.0.0:8080",
  "allow_real_hardware": false,
  "host_console": "http://192.168.1.10:8080",
  "agents": [
    { "name": "d2000-edge", "url": "http://192.168.1.50:8081" },
    { "name": "sdr",        "url": "http://192.168.1.51:8081" }
  ]
}
```

单机测试时 `kb.json.example` 已经填好 `http://127.0.0.1:8080` 与 `http://127.0.0.1:8081`，
页面会自动把 `127.0.0.1` 换成本机在浏览器里使用的主机名，不用改。

真实边缘节点在 `kb.json` 里补上准备脚本路径（U3 与 sdr 各自的路径不一样），
体检面板和「一键执行本机环境准备」都会用它：

```json
{
  "role": "edge",
  "listen": "0.0.0.0:8081",
  "allow_real_hardware": true,
  "prepare_script": "/home/kb/amari-edge/amari-host-prepare.sh",
  "host_console": "http://192.168.1.10:8080"
}
```

### ARM64 真实设备

控制台是纯 Python 标准库加原生前端，不需要编译、不带二进制依赖，ARM64 主机和从机上直接
`bash scripts/run-console.sh` 就能跑。`bootstrap-vm.sh` 按 `uname -m` 自动选择 `amd64 / arm64`
的 kubectl 与 kind 安装包，ARM 机器上不用改脚本。

要留意的是镜像：本仓库的占位工作负载用 `registry.k8s.io/pause:3.9`（多架构 manifest），
真实业务组件要自己构建 arm64 镜像，并在 YAML 里写清镜像地址。用「添加工作负载」时，调度限制
可以选 `kubernetes.io/arch=arm64`，把 Pod 固定到 ARM 节点上。

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
- 只想调某个组件而不走整套流程：用「启动核心网 Core」这类单组件按钮，或「调整组件副本数」。
- ARM 节点上 Pod 报 `exec format error`：镜像是 amd64 的，换成 arm64 或多架构镜像后重新部署，
  或者把调度限制设成 `kubernetes.io/arch=arm64` 再验证一次。
- 点按钮没反应、提示“当前已选择其他控制台”：执行位置停在了另一个控制台。切回“本机”再执行，
  或者到刚打开的新标签页里操作。
- 改了代码但界面没变化：先 `Ctrl+Shift+R` 强制刷新，浏览器会缓存旧的 `app.js`。仍不对就确认
  虚拟机上的前端文件已经更新（`grep -c '核' kb/webui/static/app.js` 应远大于 0），并在
  「系统设置 → 运行信息」里核对「界面版本」是否和当前代码一致。

## License

MIT
