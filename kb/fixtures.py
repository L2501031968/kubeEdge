"""Built-in demo data used by --demo and unit tests.

The fixtures intentionally mimic raw kubectl -o json output so the same parsing
code path runs in demo mode and against a real cluster.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _node(name: str, role: str, cpu: str, memory: str, labels: Optional[Dict[str, str]] = None,
          taints: Optional[List[Dict[str, str]]] = None, ready: bool = True) -> Dict[str, Any]:
    merged = {
        "kubernetes.io/hostname": name,
        "kubernetes.io/os": "linux",
        "kubernetes.io/arch": "amd64",
        "kb.local/role": role,
    }
    if labels:
        merged.update(labels)
    return {
        "metadata": {
            "name": name,
            "labels": merged,
            "creationTimestamp": "2026-10-08T09:00:00Z",
        },
        "spec": {"taints": taints or []},
        "status": {
            "allocatable": {"cpu": cpu, "memory": memory},
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
        },
    }


def _container(cpu_request: str, cpu_limit: str, mem_request: str, mem_limit: str) -> Dict[str, Any]:
    return {
        "name": "main",
        "image": "busybox:1.36",
        "resources": {
            "requests": {"cpu": cpu_request, "memory": mem_request},
            "limits": {"cpu": cpu_limit, "memory": mem_limit},
        },
    }


def _pod(namespace: str, name: str, node: str, deployment: str, cpu_request: str, cpu_limit: str,
         mem_request: str, mem_limit: str, restarts: int = 0) -> Dict[str, Any]:
    return {
        "metadata": {
            "namespace": namespace,
            "name": name,
            "ownerReferences": [{"kind": "ReplicaSet", "name": "%s-7d9f8c5b4" % deployment}],
        },
        "spec": {
            "nodeName": node,
            "containers": [_container(cpu_request, cpu_limit, mem_request, mem_limit)],
        },
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [{"name": "main", "ready": True, "restartCount": restarts}],
        },
    }


def _deployment(namespace: str, name: str, node: str, cpu_request: str, cpu_limit: str,
                mem_request: str, mem_limit: str, replicas: int = 1) -> Dict[str, Any]:
    return {
        "metadata": {"namespace": namespace, "name": name},
        "spec": {
            "replicas": replicas,
            "template": {
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
        _node("cloud", "cloud", "4", "8Gi"),
        _node("d2000-edge", "edge", "4", "8Gi", labels={"kb.local/name": "d2000-edge"}, taints=edge_taint),
        _node("sdr", "edge", "4", "8Gi", labels={"kb.local/name": "sdr"}, taints=edge_taint),
    ]

    pods = [
        _pod("amari-d2000", "amari-core-7d9f8c5b4-a1b2c", "d2000-edge", "amari-core", "300m", "600m", "256Mi", "512Mi"),
        _pod("amari-d2000", "amari-ims-7d9f8c5b4-d3e4f", "d2000-edge", "amari-ims", "200m", "400m", "192Mi", "384Mi"),
        _pod("amari-d2000", "amari-ran-7d9f8c5b4-g5h6i", "d2000-edge", "amari-ran", "500m", "1", "384Mi", "768Mi"),
        _pod("amari-d2000", "amari-sat-7d9f8c5b4-j7k8l", "d2000-edge", "amari-sat", "300m", "600m", "256Mi", "512Mi"),
        _pod("amari-d2000", "amari-load-7d9f8c5b4-m9n0p", "d2000-edge", "amari-load", "400m", "500m", "128Mi", "256Mi"),
        _pod("amari-edge", "amari-ue-7d9f8c5b4-q1r2s", "sdr", "amari-ue", "200m", "400m", "192Mi", "384Mi"),
    ]

    deployments = [
        _deployment("amari-d2000", "amari-core", "d2000-edge", "300m", "600m", "256Mi", "512Mi"),
        _deployment("amari-d2000", "amari-ims", "d2000-edge", "200m", "400m", "192Mi", "384Mi"),
        _deployment("amari-d2000", "amari-ran", "d2000-edge", "500m", "1", "384Mi", "768Mi"),
        _deployment("amari-d2000", "amari-sat", "d2000-edge", "300m", "600m", "256Mi", "512Mi"),
        _deployment("amari-d2000", "amari-load", "d2000-edge", "400m", "500m", "128Mi", "256Mi"),
        _deployment("amari-edge", "amari-ue", "sdr", "200m", "400m", "192Mi", "384Mi"),
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
