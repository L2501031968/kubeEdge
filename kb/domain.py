"""业务视角聚合：业务模块、用户接入、异构资源。

本模块只做纯计算：输入 ``discovery.build_snapshot`` 已经整理好的节点 / Pod /
Deployment 视图，输出界面直接可用的业务分面数据。demo 与真实集群共用同一条
代码路径，单元测试不需要 kubectl。

三条原则：

* 集群里不存在的东西不显示，也不补零（例如没有 GPU 就不出现 GPU 行）。
* 拿不到真实指标的字段返回 ``None``，由界面显示为 N/A，而不是伪造 0。
* 用户侧的折算值必须带出来源说明，避免把 Pod 申请量当成单用户真实占用。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

NODE_ALIASES = {
    "cloud": "云端控制节点",
    "d2000-edge": "NTN-RAN",
    "sdr": "UE",
}

COMPONENT_LABELS = {
    "amari-core": "核心网 Core",
    "amari-ims": "IMS服务",
    "amari-ran": "基站 RAN",
    "amari-sat": "卫星 SAT",
    "amari-ue": "UE 终端",
    "amari-load": "诊断负载",
}

# 业务模块定义。组件名、标签、命名空间三类匹配方式取并集，
# 因此真实环境里只要按约定打标签部署，模块卡片会自动出现。
BUSINESS_MODULES: Sequence[Dict[str, Any]] = (
    {
        "id": "ntn-ran",
        "title": "NTN-RAN",
        "subtitle": "基站 + 卫星载荷",
        "description": "边缘侧基站与卫星链路，占用 SDR 设备、CPU 与内存。",
        "components": ("amari-ran", "amari-sat"),
        "labels": (("kb.local/module", "ntn-ran"),),
        "namespaces": (),
        "pools": ("cpu", "memory", "storage", "network"),
    },
    {
        "id": "core",
        "title": "核心网",
        "subtitle": "Core + IMS服务",
        "description": "控制面与语音业务，通常部署在云侧或高可靠边缘节点。",
        "components": ("amari-core", "amari-ims"),
        "labels": (("kb.local/module", "core"),),
        "namespaces": (),
        "pools": ("cpu", "memory", "storage", "network"),
    },
    {
        "id": "ue",
        "title": "UE",
        "subtitle": "终端仿真与接入",
        "description": "UE 侧容器与 SDR 上电状态，用户接入统计从这里读取。",
        "components": ("amari-ue",),
        "labels": (("kb.local/module", "ue"),),
        "namespaces": (),
        "pools": ("cpu", "memory", "storage"),
    },
    {
        "id": "ai-model",
        "title": "AI 模型",
        "subtitle": "推理 / 训练负载",
        "description": "打上 kb.local/module=ai-model 标签，或放进 ai-model 命名空间即可纳入本模块。",
        "components": (),
        "labels": (("kb.local/module", "ai-model"),),
        "namespaces": ("ai-model", "ai"),
        "pools": ("cpu", "memory", "storage", "accelerator"),
    },
    {
        "id": "accelerator",
        "title": "加速模型",
        "subtitle": "FPGA / NPU / GPU 卸载",
        "description": "打上 kb.local/module=accelerator 标签，或放进 accelerator 命名空间即可纳入本模块。",
        "components": (),
        "labels": (("kb.local/module", "accelerator"),),
        "namespaces": ("accelerator", "accel"),
        "pools": ("cpu", "memory", "storage", "accelerator"),
    },
)

# 加速资源识别：按 vendors 关键字匹配扩展资源键（例如 nvidia.com/gpu）。
ACCELERATOR_KINDS: Sequence[Dict[str, Any]] = (
    {"id": "gpu", "label": "GPU", "vendors": ("gpu",)},
    {"id": "npu", "label": "NPU", "vendors": ("npu", "ascend", "npu.huawei")},
    {"id": "fpga", "label": "FPGA", "vendors": ("fpga",)},
    {"id": "dpu", "label": "DPU", "vendors": ("dpu",)},
    {"id": "asic", "label": "ASIC", "vendors": ("asic",)},
)

IGNORED_RESOURCE_KEYS = (
    "cpu",
    "memory",
    "pods",
    "ephemeral-storage",
    "hugepages-",
)

USER_STATE_LABELS = {
    "connected": "在线",
    "active": "在线",
    "idle": "空闲",
    "handover": "切换中",
    "offline": "离线",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def node_alias(name: str, labels: Optional[Dict[str, str]] = None) -> str:
    """节点展示名：优先 kb.local/name 别名表，其次原始名字。"""

    key = (labels or {}).get("kb.local/name") or name or ""
    return NODE_ALIASES.get(key, key)


def accelerator_kind(key: str) -> Dict[str, str]:
    """把扩展资源键归类到 GPU / NPU / FPGA / DPU / ASIC / 其他。"""

    lowered = (key or "").lower()
    for kind in ACCELERATOR_KINDS:
        for vendor in kind["vendors"]:
            if vendor in lowered:
                return {"id": kind["id"], "label": kind["label"], "key": key}
    return {"id": "other", "label": "其他加速资源", "key": key}


def is_accelerator_key(key: str) -> bool:
    text = (key or "").strip()
    if not text or "/" not in text:
        return False
    lowered = text.lower()
    # 控制台自用的标签式资源（例如 kb.local/bandwidth-mbps）不是加速资源。
    if lowered.startswith("kb.local/"):
        return False
    for ignored in IGNORED_RESOURCE_KEYS:
        if lowered.startswith(ignored):
            return False
    if lowered.endswith("/" + "pods"):
        return False
    return True


def _label_matches(labels: Dict[str, str], selector: Tuple[str, str]) -> bool:
    if not selector:
        return False
    key, value = selector
    return str(labels.get(key, "")).strip() == value


def _pod_in_module(module: Dict[str, Any], pod: Dict[str, Any]) -> bool:
    deployment = pod.get("deployment")
    if deployment and deployment in module["components"]:
        return True
    labels = pod.get("labels") or {}
    for selector in module["labels"]:
        if _label_matches(labels, selector):
            return True
    if module["namespaces"] and pod.get("namespace") in module["namespaces"]:
        return True
    return False


def _deployment_in_module(module: Dict[str, Any], deployment: Dict[str, Any]) -> bool:
    if deployment.get("name") in module["components"]:
        return True
    if module["namespaces"] and deployment.get("namespace") in module["namespaces"]:
        return True
    return False


def _sum_optional(values: Iterable[Optional[float]]) -> Optional[float]:
    numbers = [float(item) for item in values if item is not None]
    return sum(numbers) if numbers else None


def _usage_ratio(value: Optional[float], pool: Optional[float]) -> Optional[float]:
    if value is None or pool is None or pool <= 0:
        return None
    return max(0.0, min(1.0, float(value) / float(pool)))


def _pool_totals(nodes: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    cpu = sum(float(node.get("cpu_allocatable_cores") or 0) for node in nodes)
    memory = sum(int(node.get("mem_allocatable_bytes") or 0) for node in nodes)
    storage = sum(int(node.get("storage_allocatable_bytes") or 0) for node in nodes)
    cpu_used = _sum_optional(node.get("cpu_used_cores") for node in nodes)
    mem_used = _sum_optional(node.get("mem_used_bytes") for node in nodes)
    return {
        "cpu": cpu,
        "memory": memory,
        "storage": storage,
        "cpu_used": cpu_used,
        "mem_used": mem_used,
        "cpu_requested": sum(float(node.get("cpu_requested_cores") or 0) for node in nodes),
        "mem_requested": sum(int(node.get("mem_requested_bytes") or 0) for node in nodes),
        "storage_requested": sum(int(node.get("storage_requested_bytes") or 0) for node in nodes),
    }


def _accelerator_totals(nodes: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        allocatable = node.get("extended_allocatable") or {}
        capacity = node.get("extended_capacity") or {}
        requested = node.get("extended_requested") or {}
        for key, amount in allocatable.items():
            if not is_accelerator_key(key):
                continue
            kind = accelerator_kind(key)
            entry = result.setdefault(kind["id"], {
                "id": kind["id"],
                "label": kind["label"],
                "keys": [],
                "capacity": 0.0,
                "allocatable": 0.0,
                "requested": 0.0,
                "nodes": [],
            })
            if key not in entry["keys"]:
                entry["keys"].append(key)
            entry["capacity"] += float(capacity.get(key) or 0)
            entry["allocatable"] += float(amount or 0)
            entry["requested"] += float(requested.get(key) or 0)
            entry["nodes"].append(node.get("alias") or node.get("name"))
    for entry in result.values():
        entry["nodes"] = sorted(set(entry["nodes"]))
        entry["ratio"] = _usage_ratio(entry["requested"], entry["allocatable"])
    return result


def _module_resource_rows(
    module: Dict[str, Any],
    pods: Sequence[Dict[str, Any]],
    pool: Dict[str, Any],
    accelerators: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    cpu_request = sum(float(pod.get("cpu_request_cores") or 0) for pod in pods)
    cpu_limit = sum(float(pod.get("cpu_limit_cores") or 0) for pod in pods)
    cpu_used = _sum_optional(pod.get("cpu_used_cores") for pod in pods)
    mem_request = sum(int(pod.get("mem_request_bytes") or 0) for pod in pods)
    mem_limit = sum(int(pod.get("mem_limit_bytes") or 0) for pod in pods)
    mem_used = _sum_optional(pod.get("mem_used_bytes") for pod in pods)
    storage_request = sum(int(pod.get("storage_request_bytes") or 0) for pod in pods)

    if "cpu" in module["pools"]:
        rows.append({
            "key": "cpu",
            "label": "CPU",
            "unit": "cores",
            "request": cpu_request,
            "limit": cpu_limit,
            "used": cpu_used,
            "pool": pool.get("cpu"),
            "ratio": _usage_ratio(cpu_request, pool.get("cpu")),
            "used_ratio": _usage_ratio(cpu_used, pool.get("cpu")),
            "source": "request" if cpu_used is None else "usage",
        })
    if "memory" in module["pools"]:
        rows.append({
            "key": "memory",
            "label": "内存",
            "unit": "bytes",
            "request": mem_request,
            "limit": mem_limit,
            "used": mem_used,
            "pool": pool.get("memory"),
            "ratio": _usage_ratio(mem_request, pool.get("memory")),
            "used_ratio": _usage_ratio(mem_used, pool.get("memory")),
            "source": "request" if mem_used is None else "usage",
        })
    if "storage" in module["pools"] and pool.get("storage"):
        rows.append({
            "key": "storage",
            "label": "存储",
            "unit": "bytes",
            "request": storage_request,
            "limit": None,
            "used": None,
            "pool": pool.get("storage"),
            "ratio": _usage_ratio(storage_request, pool.get("storage")),
            "used_ratio": None,
            "source": "request",
        })
    if "accelerator" in module["pools"]:
        for kind_id, entry in sorted(accelerators.items()):
            requested = sum(
                float((pod.get("extended_requests") or {}).get(key) or 0)
                for pod in pods
                for key in entry["keys"]
            )
            if not requested and not entry.get("allocatable"):
                continue
            rows.append({
                "key": "accel-" + kind_id,
                "label": entry["label"],
                "unit": "cards",
                "request": requested,
                "limit": None,
                "used": None,
                "pool": entry.get("allocatable"),
                "ratio": _usage_ratio(requested, entry.get("allocatable")),
                "used_ratio": None,
                "source": "request",
            })
    if "network" in module["pools"]:
        bandwidth = _sum_optional(
            float((pod.get("extended_requests") or {}).get("kb.local/bandwidth-mbps") or 0) or None
            for pod in pods
        )
        network_pool = pool.get("network")
        if network_pool:
            rows.append({
                "key": "network",
                "label": "网络带宽",
                "unit": "mbps",
                "request": bandwidth,
                "limit": None,
                "used": None,
                "pool": network_pool,
                "ratio": _usage_ratio(bandwidth, network_pool),
                "used_ratio": None,
                "source": "request",
            })
    return rows


def _module_status(deployments: Sequence[Dict[str, Any]], pods: Sequence[Dict[str, Any]]) -> Tuple[str, str]:
    if not deployments and not pods:
        return "not-deployed", "未接入"
    desired = sum(int(item.get("replicas") or 0) for item in deployments)
    ready = sum(int(item.get("ready") or 0) for item in deployments)
    if not pods and desired == 0:
        return "stopped", "已停止"
    if pods and any(pod.get("status") != "Running" for pod in pods):
        return "degraded", "异常"
    if desired and ready < desired:
        return "degraded", "副本不足"
    if not desired and pods:
        return "degraded", "缺少控制器"
    return "running", "运行中"


def build_modules(
    nodes: Sequence[Dict[str, Any]],
    pods: Sequence[Dict[str, Any]],
    deployments: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """按业务模块聚合组件、Pod 与资源占用。"""

    pool = _pool_totals(nodes)
    accelerators = _accelerator_totals(nodes)
    pool["network"] = sum(
        float(node.get("network_bandwidth_mbps") or 0) for node in nodes
    ) or None
    result: List[Dict[str, Any]] = []

    for module in BUSINESS_MODULES:
        module_pods = [pod for pod in pods if _pod_in_module(module, pod)]
        matched = [item for item in deployments if _deployment_in_module(module, item)]
        # 被 Pod 反查出来的控制器（例如按标签匹配的 AI 工作负载）也要计入。
        for pod in module_pods:
            name = pod.get("deployment")
            if not name:
                continue
            if any(item["name"] == name and item["namespace"] == pod["namespace"] for item in matched):
                continue
            found = next(
                (item for item in deployments
                 if item["name"] == name and item["namespace"] == pod["namespace"]),
                None,
            )
            if found is not None:
                matched.append(found)

        status, status_label = _module_status(matched, module_pods)
        components: List[Dict[str, Any]] = []
        ordered_names = list(module["components"]) + [
            item["name"] for item in matched if item["name"] not in module["components"]
        ]
        for name in ordered_names:
            item = next((entry for entry in matched if entry["name"] == name), None)
            if item is None:
                components.append({
                    "name": name,
                    "label": COMPONENT_LABELS.get(name, name),
                    "namespace": None,
                    "replicas": 0,
                    "ready": 0,
                    "pods": 0,
                    "missing": True,
                    "nodes": [],
                })
                continue
            related = [
                pod for pod in module_pods
                if pod.get("deployment") == name and pod.get("namespace") == item["namespace"]
            ]
            components.append({
                "name": name,
                "label": COMPONENT_LABELS.get(name, name),
                "namespace": item["namespace"],
                "replicas": int(item.get("replicas") or 0),
                "ready": int(item.get("ready") or 0),
                "pods": len(related),
                "missing": False,
                "nodes": sorted({
                    pod.get("node_alias") or pod.get("display_node") or pod.get("node")
                    for pod in related if pod.get("node")
                }),
                "cpu_request_cores": float(item.get("cpu_request_per_pod_cores") or 0)
                * int(item.get("replicas") or 0),
                "mem_request_bytes": int(item.get("mem_request_per_pod_bytes") or 0)
                * int(item.get("replicas") or 0),
            })

        cpu_request = sum(float(pod.get("cpu_request_cores") or 0) for pod in module_pods)
        cpu_used = _sum_optional(pod.get("cpu_used_cores") for pod in module_pods)
        mem_request = sum(int(pod.get("mem_request_bytes") or 0) for pod in module_pods)
        mem_used = _sum_optional(pod.get("mem_used_bytes") for pod in module_pods)
        result.append({
            "id": module["id"],
            "title": module["title"],
            "subtitle": module["subtitle"],
            "description": module["description"],
            "status": status,
            "status_label": status_label,
            "components": components,
            "pods": [
                {
                    "name": pod.get("name"),
                    "namespace": pod.get("namespace"),
                    "status": pod.get("status"),
                    "node": pod.get("node_alias") or pod.get("display_node") or pod.get("node"),
                    "cpu_request_cores": pod.get("cpu_request_cores"),
                    "cpu_used_cores": pod.get("cpu_used_cores"),
                    "mem_request_bytes": pod.get("mem_request_bytes"),
                    "qos": pod.get("qos"),
                }
                for pod in sorted(module_pods, key=lambda item: (item.get("namespace") or "", item.get("name") or ""))
            ],
            "pods_total": len(module_pods),
            "pods_running": len([pod for pod in module_pods if pod.get("status") == "Running"]),
            "replicas": sum(int(item.get("replicas") or 0) for item in matched),
            "ready": sum(int(item.get("ready") or 0) for item in matched),
            "nodes": sorted({
                pod.get("node_alias") or pod.get("display_node") or pod.get("node")
                for pod in module_pods if pod.get("node")
            }),
            "cpu_request_cores": cpu_request,
            "cpu_used_cores": cpu_used,
            "mem_request_bytes": mem_request,
            "mem_used_bytes": mem_used,
            "storage_request_bytes": sum(int(pod.get("storage_request_bytes") or 0) for pod in module_pods),
            "resources": _module_resource_rows(module, module_pods, pool, accelerators),
            "metric_hint": (
                "使用量来自 metrics-server（kubectl top pods）"
                if cpu_used is not None
                else "未接入实时使用量，当前显示调度申请量"
            ),
        })
    return result


def build_users(
    source: Optional[Dict[str, Any]],
    capacity: int,
    module: Optional[Dict[str, Any]],
    mode: str,
) -> Dict[str, Any]:
    """用户接入分面。

    ``source`` 由外部适配器（AMF / SMF / 设备模型导出）提供，格式：

    ``{"generated_at": "...", "capacity": 64, "users": [{"id": "ue-1", ...}]}``
    """

    capacity_value = int((source or {}).get("capacity") or capacity or 0)
    module = module or {}
    ue_pods = list(module.get("pods") or [])
    ue_pod_count = len([pod for pod in ue_pods if pod.get("status") == "Running"]) or len(ue_pods)
    cpu_request = float(module.get("cpu_request_cores") or 0)
    mem_request = int(module.get("mem_request_bytes") or 0)

    if not source:
        return {
            "available": False,
            "mode": mode,
            "capacity": capacity_value or None,
            "online": None,
            "detail": "未接入用户数据源，配置 kb.json 的 user_source 后显示实时接入统计",
            "generated_at": None,
            "users": [],
            "sessions": None,
            "bandwidth_mbps": None,
            "prb_percent": None,
            "ue_pods": ue_pods,
            "ue_pod_count": ue_pod_count,
            "cpu_request_cores": cpu_request,
            "mem_request_bytes": mem_request,
            "per_user_cpu_request_cores": None,
            "per_user_mem_request_bytes": None,
            "share_note": "UE 模块未运行或未发现 Pod，无法折算单用户资源",
            "cells": [],
        }

    raw_users = list((source or {}).get("users") or [])
    users: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_users):
        state = str(item.get("state") or "connected").strip().lower()
        users.append({
            "id": str(item.get("id") or "ue-%d" % (index + 1)),
            "state": state,
            "state_label": USER_STATE_LABELS.get(state, state or "未知"),
            "sessions": item.get("sessions"),
            "bandwidth_mbps": item.get("bandwidth_mbps"),
            "prb_percent": item.get("prb_percent"),
            "rsrp_dbm": item.get("rsrp_dbm"),
            "cell": item.get("cell"),
            "pod": item.get("pod"),
            "node": item.get("node"),
            "node_alias": node_alias(str(item.get("node") or "")) if item.get("node") else None,
            "ip": item.get("ip"),
            "note": item.get("note"),
        })

    online = len([user for user in users if user["state"] in ("connected", "active")])
    sessions = _sum_optional(user.get("sessions") for user in users)
    bandwidth = _sum_optional(user.get("bandwidth_mbps") for user in users)
    prb_values = [float(user["prb_percent"]) for user in users if user.get("prb_percent") is not None]
    per_user_cpu = cpu_request / online if online else None
    per_user_mem = mem_request / online if online else None
    shared = bool(online and ue_pod_count and ue_pod_count < online)

    if not ue_pods:
        share_note = "未发现 UE Pod，单用户资源无法折算"
    elif shared:
        share_note = (
            "当前 %d 个用户共享 %d 个 UE Pod，单用户实时 CPU 不可得；"
            "下表按 Pod 申请量折算为平均值" % (online, ue_pod_count)
        )
    else:
        share_note = "按 UE 模块 Pod 申请量除以在线用户数折算，不代表单用户真实占用"

    cells = sorted({
        str(user.get("cell")) for user in users if user.get("cell")
    })
    return {
        "available": True,
        "mode": mode,
        "capacity": capacity_value or None,
        "online": online,
        "detail": "数据源 %s，共 %d 条记录" % (
            (source or {}).get("source") or "user_source",
            len(users),
        ),
        "generated_at": (source or {}).get("generated_at"),
        "users": users,
        "sessions": sessions,
        "bandwidth_mbps": bandwidth,
        "prb_percent": (sum(prb_values) / len(prb_values)) if prb_values else None,
        "ue_pods": ue_pods,
        "ue_pod_count": ue_pod_count,
        "cpu_request_cores": cpu_request,
        "mem_request_bytes": mem_request,
        "per_user_cpu_request_cores": per_user_cpu,
        "per_user_mem_request_bytes": per_user_mem,
        "share_note": share_note,
        "cells": cells,
    }


def build_resources(
    nodes: Sequence[Dict[str, Any]],
    pods: Sequence[Dict[str, Any]],
    mode: str,
) -> Dict[str, Any]:
    """异构资源分面：CPU / 内存 / 存储 / 网络 + 已发现加速资源。"""

    pool = _pool_totals(nodes)
    accelerators = _accelerator_totals(nodes)
    network_total = sum(float(node.get("network_bandwidth_mbps") or 0) for node in nodes)

    cpu_used = _sum_optional(node.get("cpu_used_cores") for node in nodes)
    mem_used = _sum_optional(node.get("mem_used_bytes") for node in nodes)
    pools: List[Dict[str, Any]] = [
        {
            "key": "cpu",
            "label": "CPU",
            "unit": "cores",
            "capacity": pool["cpu"],
            "allocatable": pool["cpu"],
            "requested": pool["cpu_requested"],
            "used": cpu_used,
            "free_schedulable": max(0.0, pool["cpu"] - pool["cpu_requested"]),
            "free_actual": None if cpu_used is None else max(0.0, pool["cpu"] - cpu_used),
            "source": "node status + metrics-server",
        },
        {
            "key": "memory",
            "label": "内存",
            "unit": "bytes",
            "capacity": pool["memory"],
            "allocatable": pool["memory"],
            "requested": pool["mem_requested"],
            "used": mem_used,
            "free_schedulable": max(0, pool["memory"] - pool["mem_requested"]),
            "free_actual": None if mem_used is None else max(0.0, pool["memory"] - mem_used),
            "source": "node status + metrics-server",
        },
        {
            "key": "storage",
            "label": "存储",
            "unit": "bytes",
            "capacity": pool["storage"],
            "allocatable": pool["storage"],
            "requested": pool["storage_requested"],
            "used": None,
            "free_schedulable": max(0, pool["storage"] - pool["storage_requested"]),
            "free_actual": None,
            "source": "node status ephemeral-storage（未接入磁盘使用量采集）",
        },
        {
            "key": "network",
            "label": "网络带宽",
            "unit": "mbps",
            "capacity": network_total or None,
            "allocatable": network_total or None,
            "requested": None,
            "used": None,
            "free_schedulable": None,
            "free_actual": None,
            "source": "节点标签 kb.local/net-bandwidth-mbps（未配置则显示 N/A）",
        },
    ]
    for kind_id in sorted(accelerators):
        entry = accelerators[kind_id]
        pools.append({
            "key": "accel-" + kind_id,
            "label": entry["label"],
            "unit": "cards",
            "capacity": entry["capacity"],
            "allocatable": entry["allocatable"],
            "requested": entry["requested"],
            "used": None,
            "free_schedulable": max(0.0, entry["allocatable"] - entry["requested"]),
            "free_actual": None,
            "source": "节点扩展资源 " + ", ".join(entry["keys"]),
        })

    node_views: List[Dict[str, Any]] = []
    for node in nodes:
        accelerators_on_node = []
        for key, amount in (node.get("extended_allocatable") or {}).items():
            if not is_accelerator_key(key):
                continue
            kind = accelerator_kind(key)
            accelerators_on_node.append({
                "key": key,
                "label": kind["label"],
                "allocatable": amount,
                "requested": (node.get("extended_requested") or {}).get(key, 0),
            })
        node_views.append({
            "name": node.get("name"),
            "alias": node.get("alias") or node.get("display_name") or node.get("name"),
            "roles": node.get("roles") or [],
            "ready": bool(node.get("ready")),
            "pods": node.get("pods") or 0,
            "arch": (node.get("labels") or {}).get("kubernetes.io/arch"),
            "os": (node.get("labels") or {}).get("kubernetes.io/os"),
            "cpu": {
                "allocatable": node.get("cpu_allocatable_cores"),
                "requested": node.get("cpu_requested_cores"),
                "used": node.get("cpu_used_cores"),
                "free_schedulable": max(
                    0.0,
                    float(node.get("cpu_allocatable_cores") or 0)
                    - float(node.get("cpu_requested_cores") or 0),
                ),
            },
            "memory": {
                "allocatable": node.get("mem_allocatable_bytes"),
                "requested": node.get("mem_requested_bytes"),
                "used": node.get("mem_used_bytes"),
                "free_schedulable": max(
                    0,
                    int(node.get("mem_allocatable_bytes") or 0)
                    - int(node.get("mem_requested_bytes") or 0),
                ),
            },
            "storage": {
                "allocatable": node.get("storage_allocatable_bytes"),
                "requested": node.get("storage_requested_bytes"),
                "used": None,
            },
            "network": {
                "bandwidth_mbps": node.get("network_bandwidth_mbps"),
                "addresses": node.get("addresses") or [],
            },
            "accelerators": accelerators_on_node,
            "taints": node.get("taints") or [],
        })

    missing: List[str] = []
    for kind in ACCELERATOR_KINDS:
        if kind["id"] not in accelerators:
            missing.append("%s（集群节点未上报该扩展资源）" % kind["label"])

    return {
        "mode": mode,
        "generated_at": _now(),
        "pools": pools,
        "nodes": node_views,
        "accelerators": list(accelerators.values()),
        "missing_accelerators": missing,
        "pod_count": len(pods),
        "sources": [
            "节点可分配量来自 node.status.allocatable",
            "调度申请量来自 Pod container requests",
            "实时使用量来自 metrics-server（缺失时显示 N/A）",
        ],
    }


def build_domain(
    nodes: Sequence[Dict[str, Any]],
    pods: Sequence[Dict[str, Any]],
    deployments: Sequence[Dict[str, Any]],
    users_source: Optional[Dict[str, Any]],
    ue_capacity: int,
    mode: str,
) -> Dict[str, Any]:
    """一次性产出界面需要的三个业务分面和总览补充指标。"""

    modules = build_modules(nodes, pods, deployments)
    ue_module = next((item for item in modules if item["id"] == "ue"), None)
    users = build_users(users_source, ue_capacity, ue_module, mode)
    resources = build_resources(nodes, pods, mode)
    pool = _pool_totals(nodes)
    return {
        "modules": modules,
        "users": users,
        "resources": resources,
        "extra_summary": {
            "modules_total": len(modules),
            "modules_running": len([item for item in modules if item["status"] == "running"]),
            "modules_degraded": len([item for item in modules if item["status"] == "degraded"]),
            "modules_absent": len([item for item in modules if item["status"] == "not-deployed"]),
            "users_online": users.get("online"),
            "users_capacity": users.get("capacity"),
            "users_available": users.get("available"),
            "cpu_requested_cores": pool["cpu_requested"],
            "cpu_allocatable_cores": pool["cpu"],
            "cpu_used_cores": pool["cpu_used"],
            "cpu_free_schedulable_cores": max(0.0, pool["cpu"] - pool["cpu_requested"]),
            "mem_allocatable_bytes": pool["memory"],
            "mem_requested_bytes": pool["mem_requested"],
            "mem_used_bytes": pool["mem_used"],
            "mem_free_schedulable_bytes": max(0, pool["memory"] - pool["mem_requested"]),
            "storage_allocatable_bytes": pool["storage"],
            "accelerator_labels": [item["label"] for item in resources["accelerators"]],
            "accelerator_total": sum(item["allocatable"] for item in resources["accelerators"]),
        },
    }
