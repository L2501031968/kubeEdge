"""操作目录：把 KubeEdge 实验环境的常用流程映射成可执行的命令序列。

设计要点：

* 目录只描述“做什么”，不直接执行，方便界面展示与单元测试。
* 命令统一以 argv 列表给出，执行时不经 shell，避免拼接注入。
* 触碰真实设备、需要 sudo 的动作带有能力标记，由调用方（界面 / CLI）决定是否放行。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .health import select_container_tool
from .prepare import resolve_prepare_script


DEFAULT_COMPONENTS: List[Dict[str, str]] = [
    {"name": "amari-core", "namespace": "amari-d2000", "label": "核心网"},
    {"name": "amari-ims", "namespace": "amari-d2000", "label": "IMS服务"},
    {"name": "amari-ran", "namespace": "amari-d2000", "label": "基站"},
    {"name": "amari-sat", "namespace": "amari-d2000", "label": "卫星"},
    {"name": "amari-ue", "namespace": "amari-edge", "label": "UE"},
]

GROUPS: Sequence[Dict[str, str]] = (
    {"id": "prep", "title": "环境准备"},
    {"id": "flow", "title": "云侧资源"},
    {"id": "scale", "title": "组件与副本"},
    {"id": "device", "title": "边缘设备"},
    {"id": "maint", "title": "维护"},
)

STARTUP_ORDER = ("amari-core", "amari-ims", "amari-ran", "amari-sat", "amari-ue")
U3_STARTUP_ORDER = ("amari-core", "amari-ims", "amari-ran", "amari-sat")
SHUTDOWN_ORDER = ("amari-ue", "amari-sat", "amari-ran", "amari-ims", "amari-core")


class ActionError(RuntimeError):
    """带 HTTP 语义的操作异常。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class UnknownAction(ActionError):
    def __init__(self, action_id: str) -> None:
        super().__init__("未知操作：%s" % action_id, status=404)


class ActionRejected(ActionError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status=409)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Step:
    """一条待执行的命令。"""

    argv: List[str]
    note: str = ""
    timeout: float = 20.0
    optional: bool = False
    simulated: bool = False
    cwd: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"argv": list(self.argv), "note": self.note}
        if self.optional:
            payload["optional"] = True
        if self.simulated:
            payload["simulated"] = True
        if self.cwd:
            payload["cwd"] = self.cwd
        return payload


@dataclass
class ActionParam:
    name: str
    label: str
    kind: str = "choice"  # choice | int
    choices: List[Dict[str, str]] = field(default_factory=list)
    default: Any = None
    minimum: int = 0
    maximum: int = 5


@dataclass
class ActionSpec:
    id: str
    title: str
    group: str
    roles: Sequence[str]
    description: str
    planner: Callable[[Dict[str, Any]], List[Step]]
    params: List[ActionParam] = field(default_factory=list)
    confirm: bool = False
    requires_hardware: bool = False
    requires_allow_real: bool = False

    def to_dict(self, blocked: Optional[str] = None) -> Dict[str, Any]:
        params: List[Dict[str, Any]] = []
        for item in self.params:
            default = item.default
            if default is None and item.choices:
                default = item.choices[0]["value"]
            params.append({
                "name": item.name,
                "label": item.label,
                "kind": item.kind,
                "choices": [dict(choice) for choice in item.choices],
                "default": default,
                "minimum": item.minimum,
                "maximum": item.maximum,
            })
        return {
            "id": self.id,
            "title": self.title,
            "group": self.group,
            "roles": list(self.roles),
            "description": self.description,
            "params": params,
            "confirm": self.confirm,
            "requires_hardware": self.requires_hardware,
            "requires_allow_real": self.requires_allow_real,
            "blocked": blocked,
        }


def _kubectl(cfg: Any, args: Sequence[str]) -> List[str]:
    argv = ["kubectl"]
    kubeconfig = getattr(cfg, "kubeconfig", None)
    context = getattr(cfg, "context", None)
    if kubeconfig:
        argv += ["--kubeconfig", str(kubeconfig)]
    if context:
        argv += ["--context", str(context)]
    # 命名空间按需由调用方通过 -n / -A 指定
    return argv + [str(item) for item in args]


def _scale_step(cfg: Any, component: Dict[str, str], replicas: int) -> Step:
    name = component["name"]
    namespace = component["namespace"]
    argv = ["kubectl"]
    if getattr(cfg, "kubeconfig", None):
        argv += ["--kubeconfig", str(cfg.kubeconfig)]
    if getattr(cfg, "context", None):
        argv += ["--context", str(cfg.context)]
    argv += ["scale", "deployment/%s" % name, "-n", namespace, "--replicas=%d" % replicas]
    return Step(
        argv=argv,
        note="%s（%s）副本数设为 %d" % (component.get("label", name), name, replicas),
        timeout=30.0,
    )


def _component_map(components: Sequence[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    return {item["name"]: dict(item) for item in components}


def _require_component(context: Dict[str, Any], name: str) -> Dict[str, str]:
    mapping = _component_map(context["components"])
    component = mapping.get(name)
    if component is None:
        raise ActionError("未知组件：%s" % name)
    return component


def _plan_startup(context: Dict[str, Any]) -> List[Step]:
    cfg = context["cfg"]
    steps: List[Step] = []
    for name in STARTUP_ORDER:
        step = _scale_step(cfg, _require_component(context, name), 1)
        if name == "amari-ue":
            step.note = "最后启动 UE 容器（启动前请确认 UE 已下电）"
        steps.append(step)
    return steps


def _plan_start_u3(context: Dict[str, Any]) -> List[Step]:
    cfg = context["cfg"]
    return [
        _scale_step(cfg, _require_component(context, name), 1)
        for name in U3_STARTUP_ORDER
    ]


def _plan_start_named(context: Dict[str, Any], name: str) -> List[Step]:
    cfg = context["cfg"]
    return [_scale_step(cfg, _require_component(context, name), 1)]


def _plan_start_core(context: Dict[str, Any]) -> List[Step]:
    return _plan_start_named(context, "amari-core")


def _plan_start_ims(context: Dict[str, Any]) -> List[Step]:
    return _plan_start_named(context, "amari-ims")


def _plan_start_ran(context: Dict[str, Any]) -> List[Step]:
    return _plan_start_named(context, "amari-ran")


def _plan_start_sat(context: Dict[str, Any]) -> List[Step]:
    return _plan_start_named(context, "amari-sat")


def _plan_shutdown(context: Dict[str, Any]) -> List[Step]:
    cfg = context["cfg"]
    steps: List[Step] = []
    for name in SHUTDOWN_ORDER:
        step = _scale_step(cfg, _require_component(context, name), 0)
        if name == "amari-ue":
            step.note = "先停 UE 容器，再停基站侧组件"
        steps.append(step)
    return steps


def _plan_scale(context: Dict[str, Any]) -> List[Step]:
    cfg = context["cfg"]
    params = context["params"]
    component = _require_component(context, str(params.get("component") or "amari-core"))
    replicas = int(params.get("replicas", 1))
    return [_scale_step(cfg, component, replicas)]


def _plan_rollout_restart(context: Dict[str, Any]) -> List[Step]:
    cfg = context["cfg"]
    component = _require_component(context, str(context["params"].get("component") or "amari-ran"))
    argv = ["kubectl"]
    if getattr(cfg, "kubeconfig", None):
        argv += ["--kubeconfig", str(cfg.kubeconfig)]
    if getattr(cfg, "context", None):
        argv += ["--context", str(cfg.context)]
    argv += ["rollout", "restart", "deployment/%s" % component["name"], "-n", component["namespace"]]
    return [Step(argv=argv, note="滚动重启 %s（不改变副本数）" % component["name"], timeout=45.0)]


def _plan_migrate(context: Dict[str, Any]) -> List[Step]:
    """把组件临时固定到指定节点：先打 nodeSelector，再触发滚动重启。"""

    cfg = context["cfg"]
    params = context["params"]
    component = _require_component(context, str(params.get("component") or "amari-ran"))
    node = str(params.get("node") or "").strip()
    if not node:
        raise ActionError("请选择目标节点")
    base = ["kubectl"]
    if getattr(cfg, "kubeconfig", None):
        base += ["--kubeconfig", str(cfg.kubeconfig)]
    if getattr(cfg, "context", None):
        base += ["--context", str(cfg.context)]
    patch = '{"spec":{"template":{"spec":{"nodeSelector":{"kb.local/name":"%s"}}}}}' % node
    return [
        Step(
            argv=base + ["patch", "deployment/%s" % component["name"], "-n", component["namespace"],
                         "--type", "merge", "-p", patch],
            note="把 %s 固定到节点 %s" % (component["name"], node),
            timeout=30.0,
        ),
        Step(
            argv=base + ["rollout", "status", "deployment/%s" % component["name"], "-n", component["namespace"],
                         "--timeout=120s"],
            note="等待滚动更新完成",
            timeout=150.0,
        ),
    ]


def _edge_argv(context: Dict[str, Any], args: Sequence[str], need_sudo: bool = False) -> List[str]:
    argv: List[str] = []
    if need_sudo and not context["cfg"].sim:
        argv += ["sudo", "-n"]
    return argv + [str(item) for item in args]


def _plan_ue_power_on(context: Dict[str, Any]) -> List[Step]:
    return [Step(
        argv=_edge_argv(context, ["amari-ue-power", "on", "1"]),
        note="UE 上电（真实设备会拉起 SDR）",
        timeout=30.0,
    )]


def _plan_ue_power_off(context: Dict[str, Any]) -> List[Step]:
    return [Step(
        argv=_edge_argv(context, ["amari-ue-power", "off", "1"]),
        note="UE 下电（关机流程第一步）",
        timeout=30.0,
    )]


def _plan_ue_status(context: Dict[str, Any]) -> List[Step]:
    return [Step(
        argv=_edge_argv(context, ["amari-ue-status", "--once"]),
        note="读取一次 UE 状态",
        timeout=15.0,
    )]


def _plan_check_services(context: Dict[str, Any]) -> List[Step]:
    steps = [
        Step(
            argv=_edge_argv(context, ["systemctl", "is-active", name]),
            note="%s 服务状态" % name,
            timeout=8.0,
            optional=True,
        )
        for name in ("tailscaled", "containerd", "edgecore")
    ]
    steps.append(Step(
        argv=_edge_argv(context, ["ss", "-ntp"]),
        note="查看 10000 / 10004 端口连接",
        timeout=8.0,
        optional=True,
    ))
    steps.append(Step(
        argv=_edge_argv(context, ["fuser", "-v", "/dev/sdr0"], need_sudo=True),
        note="查看 /dev/sdr0 是否被占用",
        timeout=8.0,
        optional=True,
    ))
    return steps


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def _plan_prepare(context: Dict[str, Any]) -> List[Step]:
    """在页面内执行本机准备脚本，不再要求用户回终端敲 sudo。"""

    cfg = context["cfg"]
    if getattr(cfg, "sim", False) or getattr(cfg, "demo", False):
        return [Step(
            argv=["echo", "模拟环境：跳过真实准备脚本"],
            note="模拟环境不需要执行 amari-host-prepare.sh / amari-ue-prepare.sh",
            timeout=10.0,
            simulated=True,
        )]

    script = resolve_prepare_script(getattr(cfg, "prepare_script", None))
    if script.source == "configured-missing":
        raise ActionRejected("kb.json 里配置的 prepare_script 不存在：%s" % script.path)
    if not script.found or not script.path:
        raise ActionRejected(
            "未找到环境准备脚本。请把 amari-host-prepare.sh / amari-ue-prepare.sh "
            "放到文档约定的目录，或在 kb.json 里用 prepare_script 指定绝对路径。"
        )

    argv = ["bash", script.path]
    if not _is_root():
        argv = ["sudo", "-n"] + argv
    return [Step(
        argv=argv,
        note="执行本机环境准备脚本 %s（需要 sudo 免密）" % Path(script.path).name,
        timeout=1800.0,
        cwd=str(Path(script.path).parent),
    )]


def _mode_param() -> ActionParam:
    return ActionParam(
        name="mode",
        label="模式",
        kind="choice",
        choices=[
            {"value": "status", "label": "查看当前模式"},
            {"value": "edge", "label": "切换到边缘模式"},
            {"value": "local", "label": "切换到本地模式"},
        ],
        default="status",
    )


def _plan_mode(context: Dict[str, Any]) -> List[Step]:
    mode = str(context["params"].get("mode") or "status")
    if mode not in ("status", "edge", "local"):
        raise ActionError("模式只能是 status / edge / local")
    step = Step(
        argv=_edge_argv(context, ["amari-mode", mode], need_sudo=mode != "status"),
        note="amari-mode %s" % mode,
        timeout=90.0,
    )
    if mode != "status":
        step.note += "（真实设备会重启，输出会中断）"
    return [step]


def _network_param() -> ActionParam:
    return ActionParam(
        name="mode",
        label="网络模式",
        kind="choice",
        choices=[
            {"value": "direct", "label": "直连 direct"},
            {"value": "bridge", "label": "桥接 bridge"},
        ],
        default="direct",
    )


def _plan_network_mode(context: Dict[str, Any]) -> List[Step]:
    mode = str(context["params"].get("mode") or "direct")
    if mode not in ("direct", "bridge"):
        raise ActionError("网络模式只能是 direct / bridge")
    return [Step(
        argv=_edge_argv(context, ["amari-ue-network-mode", mode], need_sudo=True),
        note="切换 UE 网络模式为 %s" % mode,
        timeout=60.0,
    )]


def _plan_containers(context: Dict[str, Any]) -> List[Step]:
    cfg = context["cfg"]
    if getattr(cfg, "sim", False) or getattr(cfg, "demo", False):
        return [Step(
            argv=["echo", "模拟环境：不查询真实边缘容器"],
            note="模拟环境跳过容器查询",
            timeout=10.0,
            simulated=True,
        )]

    tool = select_container_tool(context["runner"])
    if tool is None:
        raise ActionRejected(
            "未找到 nerdctl、crictl 或 ctr。该工具只用于页面诊断，不影响 KubeEdge "
            "的运行；如需在页面列出边缘容器，请安装其中任意一个。"
        )

    return [Step(
        argv=list(tool["argv"]),
        note="使用 %s 查看边缘节点容器" % tool["name"],
        timeout=20.0,
    )]


def _plan_device_probe(context: Dict[str, Any]) -> List[Step]:
    return [
        Step(argv=_edge_argv(context, ["ls", "-l", "/dev/sdr0"]), note="SDR 设备节点", optional=True),
        Step(argv=_edge_argv(context, ["getstatus"]), note="KYSEC 设备状态（未安装会跳过）", optional=True),
        Step(argv=_edge_argv(context, ["tailscale", "status"]), note="Tailscale 网络状态", optional=True),
    ]


def _plan_host_shutdown(context: Dict[str, Any]) -> List[Step]:
    return [Step(
        argv=["sudo", "-n", "shutdown", "-h", "now"],
        note="关闭云侧主机（控制台会随之中断）",
        timeout=15.0,
    )]


def _plan_host_update(context: Dict[str, Any]) -> List[Step]:
    cfg = context["cfg"]
    return [
        Step(
            argv=_kubectl(cfg, ["get", "nodes", "-o", "wide"]),
            note="查看节点状态",
            optional=True,
        ),
        Step(
            argv=_kubectl(cfg, ["get", "events", "-A", "--sort-by=.lastTimestamp"]),
            note="最近集群事件",
            optional=True,
            timeout=30.0,
        ),
    ]


def _component_param(default: str = "amari-core") -> ActionParam:
    choices = [{"value": item["name"], "label": "%s（%s）" % (item["label"], item["name"])}
               for item in DEFAULT_COMPONENTS]
    return ActionParam(name="component", label="组件", kind="choice", choices=choices, default=default)


ACTIONS: Sequence[ActionSpec] = (
    ActionSpec(
        id="prepare_node",
        title="一键执行本机环境准备",
        group="prep",
        roles=("edge",),
        description=(
            "在页面里执行 U3 的 amari-host-prepare.sh 或 sdr 的 amari-ue-prepare.sh，"
            "以脚本所在目录为工作目录；脚本路径可用 kb.json 的 prepare_script 指定。"
        ),
        planner=_plan_prepare,
        confirm=True,
        requires_hardware=True,
    ),
    ActionSpec(
        id="startup_stack",
        title="启动完整业务链路",
        group="flow",
        roles=("host",),
        description="按 core → ims → ran → sat → ue 顺序启动。执行前请确认 UE 已下电；页面会等待真实副本和 Pod 就绪。",
        planner=_plan_startup,
        confirm=True,
    ),
    ActionSpec(
        id="startup_u3",
        title="启动 U3 四个组件",
        group="flow",
        roles=("host",),
        description="按 core → ims → ran → sat 顺序启动 U3 侧组件，不启动 UE。",
        planner=_plan_start_u3,
        confirm=True,
    ),
    ActionSpec(
        id="start_core",
        title="启动核心网 Core",
        group="flow",
        roles=("host",),
        description="仅把 amari-core 副本数设为 1，适合按需单独启动。",
        planner=_plan_start_core,
    ),
    ActionSpec(
        id="start_ims",
        title="启动 IMS服务",
        group="flow",
        roles=("host",),
        description="仅把 amari-ims 副本数设为 1，适合按需单独启动。",
        planner=_plan_start_ims,
    ),
    ActionSpec(
        id="start_ran",
        title="启动基站 RAN",
        group="flow",
        roles=("host",),
        description="仅把 amari-ran 副本数设为 1，适合按需单独启动。",
        planner=_plan_start_ran,
    ),
    ActionSpec(
        id="start_sat",
        title="启动卫星 SAT",
        group="flow",
        roles=("host",),
        description="仅把 amari-sat 副本数设为 1，适合按需单独启动。",
        planner=_plan_start_sat,
    ),
    ActionSpec(
        id="shutdown_stack",
        title="回收业务组件",
        group="flow",
        roles=("host",),
        description="云主机保持常驻；按 ue → sat → ran → ims → core 将业务组件副本归零，并等待 Pod 回收完成。",
        planner=_plan_shutdown,
        confirm=True,
    ),
    ActionSpec(
        id="scale_component",
        title="调整组件副本数",
        group="scale",
        roles=("host",),
        description="直接调用 kubectl scale，用于把某个组件扩容或缩容到指定副本数。",
        planner=_plan_scale,
        params=[
            _component_param(),
            ActionParam(name="replicas", label="副本数", kind="int", default=1, minimum=0, maximum=5),
        ],
    ),
    ActionSpec(
        id="rollout_restart",
        title="滚动重启组件",
        group="scale",
        roles=("host",),
        description="kubectl rollout restart，不改变副本数，用于让 Pod 重新调度。",
        planner=_plan_rollout_restart,
        params=[_component_param("amari-ran")],
    ),
    ActionSpec(
        id="migrate_component",
        title="指定组件调度到节点",
        group="scale",
        roles=("host",),
        description="给 Deployment 写入 nodeSelector 并等待滚动更新，用于手动调整组件落点。",
        planner=_plan_migrate,
        params=[
            _component_param("amari-ran"),
            ActionParam(
                name="node",
                label="目标节点",
                kind="choice",
                choices=[
                    {"value": "d2000-edge", "label": "NTN-RAN（d2000-edge）"},
                    {"value": "sdr", "label": "UE（sdr）"},
                    {"value": "cloud", "label": "云端控制节点（cloud）"},
                ],
                default="d2000-edge",
            ),
        ],
        confirm=True,
    ),
    ActionSpec(
        id="ue_power_on",
        title="UE 上电",
        group="device",
        roles=("edge",),
        description="amari-ue-power on 1，拉起 SDR 与 UE 侧容器。",
        planner=_plan_ue_power_on,
        requires_hardware=True,
    ),
    ActionSpec(
        id="ue_power_off",
        title="UE 下电",
        group="device",
        roles=("edge",),
        description="amari-ue-power off 1，关机流程的第一步。",
        planner=_plan_ue_power_off,
        requires_hardware=True,
    ),
    ActionSpec(
        id="ue_status",
        title="读取 UE 状态",
        group="device",
        roles=("edge",),
        description="amari-ue-status --once，读取一次同步状态。",
        planner=_plan_ue_status,
    ),
    ActionSpec(
        id="amari_mode",
        title="切换 Amarisoft 模式",
        group="device",
        roles=("edge",),
        description="amari-mode status / edge / local，切换边缘容器模式与本地模式。",
        planner=_plan_mode,
        params=[_mode_param()],
        confirm=True,
        requires_hardware=True,
    ),
    ActionSpec(
        id="ue_network_mode",
        title="切换 UE 网络模式",
        group="device",
        roles=("edge",),
        description="amari-ue-network-mode direct / bridge。",
        planner=_plan_network_mode,
        params=[_network_param()],
        confirm=True,
        requires_hardware=True,
    ),
    ActionSpec(
        id="check_services",
        title="检查边缘服务与端口",
        group="maint",
        roles=("edge",),
        description="systemctl is-active tailscaled/containerd/edgecore，并查看 10000 端口连接。",
        planner=_plan_check_services,
    ),
    ActionSpec(
        id="list_containers",
        title="列出边缘容器",
        group="maint",
        roles=("edge",),
        description="自动选择 nerdctl / crictl / ctr 查询边缘容器；属于可选诊断。",
        planner=_plan_containers,
    ),
    ActionSpec(
        id="device_probe",
        title="设备与网络探测",
        group="maint",
        roles=("edge",),
        description="检查 /dev/sdr0、KYSEC getstatus、tailscale status。",
        planner=_plan_device_probe,
    ),
    ActionSpec(
        id="cluster_overview",
        title="集群总览命令",
        group="maint",
        roles=("host",),
        description="kubectl get nodes -o wide 与最近事件，用于排查调度问题。",
        planner=_plan_host_update,
    ),
    ActionSpec(
        id="host_shutdown",
        title="关闭云侧主机",
        group="maint",
        roles=("host",),
        description="sudo shutdown -h now，仅当 allow_real_hardware 打开时可用。",
        planner=_plan_host_shutdown,
        confirm=True,
        requires_hardware=True,
        requires_allow_real=True,
    ),
)


def get_action(action_id: str) -> Optional[ActionSpec]:
    for spec in ACTIONS:
        if spec.id == action_id:
            return spec
    return None


def guard(cfg: Any, spec: ActionSpec) -> Optional[str]:
    """返回禁止执行的原因；None 表示可执行。"""

    if getattr(cfg, "dry_run", False) or getattr(cfg, "demo", False):
        return None
    allow_real = bool(getattr(cfg, "allow_real_hardware", False))
    sim = bool(getattr(cfg, "sim", False))
    if spec.requires_allow_real and not allow_real:
        return "需要在 kb.json 中打开 allow_real_hardware 才能执行"
    if spec.requires_hardware and not (sim or allow_real):
        return "实时模式下默认禁用，请使用 --sim（模拟）或打开 allow_real_hardware"
    return None


def catalog(cfg: Any, role: Optional[str] = None) -> Dict[str, Any]:
    current_role = role or getattr(cfg, "role", "host")
    items: List[Dict[str, Any]] = []
    for spec in ACTIONS:
        if current_role not in spec.roles:
            continue
        items.append(spec.to_dict(blocked=guard(cfg, spec)))
    return {
        "role": current_role,
        "groups": [dict(group) for group in GROUPS],
        "items": items,
    }


def _coerce_params(spec: ActionSpec, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    provided = dict(params or {})
    merged: Dict[str, Any] = {}
    for item in spec.params:
        if item.name in provided and provided[item.name] is not None:
            value = provided[item.name]
        elif item.default is not None:
            value = item.default
        elif item.choices:
            value = item.choices[0]["value"]
        elif item.kind == "int":
            value = item.minimum
        else:
            raise ActionError("缺少参数：%s" % item.name)

        if item.kind == "int":
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ActionError("%s 需要是整数" % item.label)
            if number < item.minimum or number > item.maximum:
                raise ActionError("%s 需要在 %d - %d 之间" % (item.label, item.minimum, item.maximum))
            merged[item.name] = number
        else:
            text = str(value)
            allowed = {choice["value"] for choice in item.choices}
            if allowed and text not in allowed:
                raise ActionError("%s 取值只能是：%s" % (item.label, ", ".join(sorted(allowed))))
            merged[item.name] = text
    for key, value in provided.items():
        merged.setdefault(key, value)
    return merged


def run_action(
    cfg: Any,
    runner: Any,
    action_id: str,
    params: Optional[Dict[str, Any]] = None,
    components: Optional[Sequence[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    spec = get_action(action_id)
    if spec is None:
        raise UnknownAction(action_id)
    role = getattr(cfg, "role", "host")
    if role not in spec.roles:
        raise ActionRejected("当前角色 %s 不能执行该操作，请在对应节点的控制台上操作" % role)
    blocked = guard(cfg, spec)
    if blocked:
        raise ActionRejected(blocked)

    merged = _coerce_params(spec, params)
    context = {
        "cfg": cfg,
        "runner": runner,
        "params": merged,
        "components": list(components or DEFAULT_COMPONENTS),
    }
    steps = spec.planner(context)

    started = _now()
    results: List[Dict[str, Any]] = []
    ok = True
    for step in steps:
        result = runner.run(step.argv, timeout=step.timeout, cwd=step.cwd)
        payload = dict(result.to_dict())
        payload["note"] = step.note or " ".join(step.argv)
        payload["optional"] = step.optional
        if step.cwd:
            payload["cwd"] = step.cwd
        if step.simulated:
            payload["simulated"] = True
        payload["ok"] = result.ok
        results.append(payload)
        if not result.ok and not step.optional:
            ok = False
            break

    return {
        "id": spec.id,
        "title": spec.title,
        "params": merged,
        "ok": ok,
        "aborted": not ok and len(results) < len(steps),
        "started_at": started,
        "finished_at": _now(),
        "dry_run": bool(getattr(runner, "dry_run", False)),
        "steps": results,
    }


MAX_APPLY_BYTES = 256 * 1024


def apply_yaml(cfg: Any, runner: Any, text: Any) -> Dict[str, Any]:
    """通过 stdin 执行 kubectl apply -f -，避免把 YAML 写入命令行和审计记录。"""

    if getattr(cfg, "role", "host") != "host":
        raise ActionRejected("只有云主机控制台可以应用 Kubernetes YAML")
    if not isinstance(text, str):
        raise ActionError("YAML 内容必须是字符串")
    if not text.strip():
        raise ActionError("YAML 内容不能为空")
    if "\x00" in text:
        raise ActionError("YAML 内容不能包含 NUL 字符")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_APPLY_BYTES:
        raise ActionError("YAML 内容不能超过 %d KiB" % (MAX_APPLY_BYTES // 1024))

    started = _now()
    step = Step(
        argv=_kubectl(cfg, ["apply", "-f", "-"]),
        note="应用用户编辑的 Kubernetes YAML",
        timeout=90.0,
    )
    result = runner.run(step.argv, timeout=step.timeout, input_text=text)
    payload = dict(result.to_dict())
    payload["note"] = step.note
    payload["optional"] = False
    payload["ok"] = result.ok
    return {
        "id": "apply_yaml",
        "title": "应用工作负载 YAML",
        "params": {},
        "ok": result.ok,
        "aborted": False,
        "started_at": started,
        "finished_at": _now(),
        "dry_run": bool(getattr(runner, "dry_run", False)),
        "steps": [payload],
    }
