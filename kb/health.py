"""云主机与边缘节点共用的服务状态判定。"""
from __future__ import annotations

from typing import Any, Dict, Optional

WARN_STATES = ("activating", "deactivating", "reloading", "unknown", "")

# 容器查询只用于页面诊断，不属于 KubeEdge 运行依赖。
CONTAINER_TOOLS = (
    ("nerdctl", ("nerdctl", "--namespace", "k8s.io", "ps")),
    ("crictl", ("crictl", "ps")),
    ("ctr", ("sudo", "-n", "ctr", "--namespace", "k8s.io", "containers", "list")),
)


def service_check_status(state: str) -> str:
    """把 ``systemctl is-active`` 的输出映射成三色灯状态。"""

    text = (state or "").strip().lower()
    if text == "active":
        return "ok"
    if text in WARN_STATES:
        return "warn"
    return "fail"


def select_container_tool(runner: Any) -> Optional[Dict[str, Any]]:
    """按稳定优先级选择可用的容器查询命令。"""

    for name, argv in CONTAINER_TOOLS:
        path = runner.which(name)
        if path:
            return {
                "name": name,
                "path": path,
                "argv": list(argv),
                "display": " ".join(argv),
            }
    return None
