"""Built-in demo data used by --demo and unit tests.

The fixtures intentionally mimic raw kubectl -o json output so the same parsing
code path runs in demo mode and against a real cluster.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _node(name: str, role: str, cpu: str, memory: str, labels: Optional[Dict[str, str]] = None,
          taints: Optional[List[Dict[str, str]]] = None, ready: bool = True,
          storage: str = "60Gi", extended: Optional[Dict[str, str]] = None,
          addresses: Optional[List[str]] = None, arch: str = "amd64") -> Dict[str, Any]:
    merged = {
        "kubernetes.io/hostname": name,
        "kubernetes.io/os": "linux",
        "kubernetes.io/arch": arch,
        "kb.local/role": role,
    }
    if labels:
        merged.update(labels)
    allocatable = {"cpu": cpu, "memory": memory, "ephemeral-storage": storage}
    capacity = {"cpu": cpu, "memory": memory, "ephemeral-storage": storage}
    allocatable.update(extended or {})
    capacity.update(extended or {})
    return {
        "metadata": {
            "name": name,
            "labels": merged,
            "creationTimestamp": "2026-10-08T09:00:00Z",
        },
        "spec": {"taints": taints or []},
        "status": {
            "capacity": capacity,
            "allocatable": allocatable,
            "addresses": [
                {"type": "InternalIP", "address": address}
                for address in (addresses or [])
            ],
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
        },
    }


def _container(cpu_request: str, cpu_limit: str, mem_request: str, mem_limit: str,
               storage_request: Optional[str] = None,
               extra_requests: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    requests: Dict[str, str] = {"cpu": cpu_request, "memory": mem_request}
    if storage_request:
        requests["ephemeral-storage"] = storage_request
    requests.update(extra_requests or {})
    return {
        "name": "main",
        "image": "busybox:1.36",
        "resources": {
            "requests": requests,
            "limits": {"cpu": cpu_limit, "memory": mem_limit},
        },
    }


def _pod(namespace: str, name: str, node: str, deployment: str, cpu_request: str, cpu_limit: str,
         mem_request: str, mem_limit: str, restarts: int = 0,
         labels: Optional[Dict[str, str]] = None,
         storage_request: Optional[str] = None) -> Dict[str, Any]:
    merged_labels = {"app": deployment}
    merged_labels.update(labels or {})
    return {
        "metadata": {
            "namespace": namespace,
            "name": name,
            "labels": merged_labels,
            "ownerReferences": [{"kind": "ReplicaSet", "name": "%s-7d9f8c5b4" % deployment}],
        },
        "spec": {
            "nodeName": node,
            "containers": [
                _container(cpu_request, cpu_limit, mem_request, mem_limit,
                           storage_request=storage_request)
            ],
        },
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [{"name": "main", "ready": True, "restartCount": restarts}],
        },
    }


def _deployment(namespace: str, name: str, node: str, cpu_request: str, cpu_limit: str,
                mem_request: str, mem_limit: str, replicas: int = 1,
                labels: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    merged_labels = {"app": name}
    merged_labels.update(labels or {})
    return {
        "metadata": {"namespace": namespace, "name": name},
        "spec": {
            "replicas": replicas,
            "template": {
                "metadata": {"labels": merged_labels},
                "spec": {
                    "nodeSelector": {"kb.local/name": node},
                    "tolerations": [
                        {"key": "kb.local/edge", "operator": "Exists", "effect": "NoSchedule"}
                    ],
                    "containers": [_container(cpu_request, cpu_limit, mem_request, mem_limit)],
                }
            },
        },
        "status": {"readyReplicas": replicas, "availableReplicas": replicas},
    }


def demo_raw() -> Dict[str, Any]:
    edge_taint = [{"key": "kb.local/edge", "value": "true", "effect": "NoSchedule"}]
    nodes = [
        _node(
            "cloud", "cloud", "4", "8Gi",
            labels={
                "kb.local/name": "cloud",
                "kb.local/net-bandwidth-mbps": "1000",
                "node-role.kubernetes.io/control-plane": "",
            },
            extended={"nvidia.com/gpu": "2"},
            addresses=["10.10.0.10"],
        ),
        _node(
            "d2000-edge", "edge", "4", "8Gi",
            labels={"kb.local/name": "d2000-edge", "kb.local/net-bandwidth-mbps": "1000"},
            taints=edge_taint,
            extended={"huawei.com/Ascend310": "1"},
            addresses=["10.10.0.20"],
            arch="arm64",
        ),
        _node(
            "sdr", "edge", "4", "8Gi",
            labels={"kb.local/name": "sdr", "kb.local/net-bandwidth-mbps": "500"},
            taints=edge_taint,
            addresses=["10.10.0.30"],
        ),
    ]

    pods = [
        _pod("amari-d2000", "amari-core-7d9f8c5b4-a1b2c", "d2000-edge", "amari-core", "300m", "600m", "256Mi", "512Mi"),
        _pod("amari-d2000", "amari-ims-7d9f8c5b4-d3e4f", "d2000-edge", "amari-ims", "200m", "400m", "192Mi", "384Mi"),
        _pod("amari-d2000", "amari-ran-7d9f8c5b4-g5h6i", "d2000-edge", "amari-ran", "500m", "1", "384Mi", "768Mi"),
        _pod("amari-d2000", "amari-sat-7d9f8c5b4-j7k8l", "d2000-edge", "amari-sat", "300m", "600m", "256Mi", "512Mi"),
        _pod("amari-d2000", "amari-load-7d9f8c5b4-m9n0p", "d2000-edge", "amari-load", "400m", "500m", "128Mi", "256Mi"),
        _pod("amari-edge", "amari-ue-7d9f8c5b4-q1r2s", "sdr", "amari-ue", "200m", "400m", "192Mi", "384Mi"),
        _pod("kube-system", "coredns-55cb58b774-9gx45", "cloud", "coredns", "100m", "0.2", "70Mi", "170Mi"),
        _pod("kube-system", "metrics-server-7887b478d6-hfh4c", "cloud", "metrics-server", "100m", "0.2", "200Mi", "400Mi"),
    ]

    deployments = [
        _deployment("amari-d2000", "amari-core", "d2000-edge", "300m", "600m", "256Mi", "512Mi"),
        _deployment("amari-d2000", "amari-ims", "d2000-edge", "200m", "400m", "192Mi", "384Mi"),
        _deployment("amari-d2000", "amari-ran", "d2000-edge", "500m", "1", "384Mi", "768Mi"),
        _deployment("amari-d2000", "amari-sat", "d2000-edge", "300m", "600m", "256Mi", "512Mi"),
        _deployment("amari-d2000", "amari-load", "d2000-edge", "400m", "500m", "128Mi", "256Mi"),
        _deployment("amari-edge", "amari-ue", "sdr", "200m", "400m", "192Mi", "384Mi"),
        _deployment("kube-system", "coredns", "cloud", "100m", "0.2", "70Mi", "170Mi", replicas=2),
        _deployment("kube-system", "metrics-server", "cloud", "100m", "0.2", "200Mi", "400Mi"),
    ]

    top_nodes = "\n".join([
        "cloud         120m   3%    900Mi   12%",
        "d2000-edge    1450m  36%   1.1Gi   15%",
        "sdr           210m   5%    480Mi   6%",
    ])
    top_pods = "\n".join([
        "amari-d2000   amari-core-7d9f8c5b4-a1b2c   290m   230Mi",
        "amari-d2000   amari-ims-7d9f8c5b4-d3e4f    180m   170Mi",
        "amari-d2000   amari-ran-7d9f8c5b4-g5h6i    480m   350Mi",
        "amari-d2000   amari-sat-7d9f8c5b4-j7k8l    300m   240Mi",
        "amari-d2000   amari-load-7d9f8c5b4-m9n0p   495m   120Mi",
        "amari-edge    amari-ue-7d9f8c5b4-q1r2s      90m   140Mi",
        "kube-system   coredns-55cb58b774-9gx45      5m    14Mi",
        "kube-system   metrics-server-7887b478d6-hfh4c  3m  20Mi",
    ])

    return {
        "nodes": {"items": nodes},
        "pods": {"items": pods},
        "deployments": {"items": deployments},
        "top_nodes": top_nodes,
        "top_pods": top_pods,
        "metrics_available": True,
        "device_crd": True,
        "kubectl_version": "v1.30.14",
        "server_version": "v1.30.14",
        "context": "kind-kb-sim",
        "error": None,
    }


def demo_users() -> Dict[str, Any]:
    """演示用的用户接入数据源，字段与 ``kb.json`` 的 ``user_source`` 保持一致。

    真实环境里由 AMF / SMF / 设备模型导出同名 JSON；这里用固定时间戳，
    保证演示与单元测试的输出是确定性的。
    """

    users = [
        {"id": "ue-1001", "state": "connected", "sessions": 2, "bandwidth_mbps": 24.5,
         "prb_percent": 12, "rsrp_dbm": -82, "cell": "cell-1",
         "pod": "amari-ue-7d9f8c5b4-q1r2s", "node": "sdr", "ip": "10.60.0.11",
         "note": "视频回传"},
        {"id": "ue-1002", "state": "connected", "sessions": 1, "bandwidth_mbps": 8.0,
         "prb_percent": 5, "rsrp_dbm": -88, "cell": "cell-1",
         "pod": "amari-ue-7d9f8c5b4-q1r2s", "node": "sdr", "ip": "10.60.0.12",
         "note": "遥测"},
        {"id": "ue-1003", "state": "active", "sessions": 3, "bandwidth_mbps": 41.2,
         "prb_percent": 21, "rsrp_dbm": -76, "cell": "cell-1",
         "pod": "amari-ue-7d9f8c5b4-q1r2s", "node": "sdr", "ip": "10.60.0.13",
         "note": "图像传输"},
        {"id": "ue-1004", "state": "idle", "sessions": 0, "bandwidth_mbps": 0.4,
         "prb_percent": 1, "rsrp_dbm": -95, "cell": "cell-1",
         "pod": "amari-ue-7d9f8c5b4-q1r2s", "node": "sdr", "ip": "10.60.0.14",
         "note": "待机"},
        {"id": "ue-1005", "state": "connected", "sessions": 1, "bandwidth_mbps": 12.8,
         "prb_percent": 8, "rsrp_dbm": -84, "cell": "cell-2",
         "pod": "amari-ue-7d9f8c5b4-q1r2s", "node": "sdr", "ip": "10.60.0.15",
         "note": "语音"},
        {"id": "ue-1006", "state": "handover", "sessions": 1, "bandwidth_mbps": 6.5,
         "prb_percent": 4, "rsrp_dbm": -91, "cell": "cell-2",
         "pod": "amari-ue-7d9f8c5b4-q1r2s", "node": "sdr", "ip": "10.60.0.16",
         "note": "小区切换中"},
        {"id": "ue-1007", "state": "connected", "sessions": 2, "bandwidth_mbps": 33.0,
         "prb_percent": 17, "rsrp_dbm": -79, "cell": "cell-2",
         "pod": "amari-ue-7d9f8c5b4-q1r2s", "node": "sdr", "ip": "10.60.0.17",
         "note": "边缘推理回传"},
        {"id": "ue-1008", "state": "idle", "sessions": 0, "bandwidth_mbps": 0.2,
         "prb_percent": 1, "rsrp_dbm": -97, "cell": "cell-2",
         "pod": "amari-ue-7d9f8c5b4-q1r2s", "node": "sdr", "ip": "10.60.0.18",
         "note": "待机"},
        {"id": "ue-1009", "state": "connected", "sessions": 4, "bandwidth_mbps": 58.6,
         "prb_percent": 28, "rsrp_dbm": -73, "cell": "cell-2",
         "pod": "amari-ue-7d9f8c5b4-q1r2s", "node": "sdr", "ip": "10.60.0.19",
         "note": "高清图传"},
        {"id": "ue-1010", "state": "offline", "sessions": 0, "bandwidth_mbps": 0.0,
         "prb_percent": 0, "rsrp_dbm": None, "cell": "cell-1",
         "pod": "amari-ue-7d9f8c5b4-q1r2s", "node": "sdr", "ip": "10.60.0.20",
         "note": "已掉线"},
        {"id": "ue-1011", "state": "connected", "sessions": 1, "bandwidth_mbps": 15.4,
         "prb_percent": 9, "rsrp_dbm": -86, "cell": "cell-1",
         "pod": "amari-ue-7d9f8c5b4-q1r2s", "node": "sdr", "ip": "10.60.0.21",
         "note": "环境监测"},
        {"id": "ue-1012", "state": "active", "sessions": 2, "bandwidth_mbps": 27.9,
         "prb_percent": 14, "rsrp_dbm": -80, "cell": "cell-1",
         "pod": "amari-ue-7d9f8c5b4-q1r2s", "node": "sdr", "ip": "10.60.0.22",
         "note": "数传"},
    ]
    return {
        "source": "demo-fixture",
        "generated_at": "2026-10-08T09:00:00Z",
        "capacity": 64,
        "users": users,
    }
