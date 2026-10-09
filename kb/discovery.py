from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import fixtures
from .kube import Kubectl, KubectlError
from .units import format_cpu, format_memory, parse_cpu, parse_memory

EXPECTED_DEPLOYMENTS = ("amari-core", "amari-ims", "amari-ran", "amari-sat", "amari-ue")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _labels(meta: Dict[str, Any]) -> Dict[str, str]:
    return meta.get("labels") or {}


def node_display_name(name: str, labels: Dict[str, str]) -> str:
    return labels.get("kb.local/name") or name


def node_roles(labels: Dict[str, str]) -> List[str]:
    roles: List[str] = []
    prefix = "node-role.kubernetes.io/"
    for key in labels:
        if key.startswith(prefix) and len(key) > len(prefix):
            roles.append(key[len(prefix):])
    local_role = labels.get("kb.local/role")
    if local_role and local_role not in roles:
        roles.append(local_role)
    return sorted(set(roles))


def _condition(status: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
    for item in status.get("conditions") or []:
        if item.get("type") == kind:
            return item
    return None


def node_ready(node: Dict[str, Any]) -> bool:
    condition = _condition(node.get("status") or {}, "Ready")
    return bool(condition and condition.get("status") == "True")


def node_taints(node: Dict[str, Any]) -> List[str]:
    result = []
    for taint in (node.get("spec") or {}).get("taints") or []:
        key = taint.get("key", "")
        value = taint.get("value")
        effect = taint.get("effect", "")
        if value:
            result.append("%s=%s:%s" % (key, value, effect))
        else:
            result.append("%s:%s" % (key, effect))
    return result


def _containers(pod: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (pod.get("spec") or {}).get("containers") or []


def container_resources(container: Dict[str, Any]) -> Tuple[float, float, int, int]:
    resources = container.get("resources") or {}
    requests = resources.get("requests") or {}
    limits = resources.get("limits") or {}
    return (
        parse_cpu(requests.get("cpu")),
        parse_cpu(limits.get("cpu")),
        parse_memory(requests.get("memory")),
        parse_memory(limits.get("memory")),
    )


def pod_resources(pod: Dict[str, Any]) -> Tuple[float, float, int, int]:
    cpu_request = cpu_limit = 0.0
    mem_request = mem_limit = 0
    for container in _containers(pod):
        c_req, c_lim, m_req, m_lim = container_resources(container)
        cpu_request += c_req
        cpu_limit += c_lim
        mem_request += m_req
        mem_limit += m_lim
    return cpu_request, cpu_limit, mem_request, mem_limit


def pod_qos(cpu_request: float, cpu_limit: float, mem_request: int, mem_limit: int) -> str:
    if cpu_request == 0 and cpu_limit == 0 and mem_request == 0 and mem_limit == 0:
        return "BestEffort"
    if cpu_limit > 0 and mem_limit > 0 and cpu_request == cpu_limit and mem_request == mem_limit:
        return "Guaranteed"
    return "Burstable"


def pod_status(pod: Dict[str, Any]) -> Tuple[str, bool]:
    status = pod.get("status") or {}
    phase = status.get("phase") or "Unknown"
    ready_condition = _condition(status, "Ready")
    ready = bool(ready_condition and ready_condition.get("status") == "True")
    for container_status in status.get("containerStatuses") or []:
        waiting = (container_status.get("state") or {}).get("waiting") or {}
        reason = waiting.get("reason")
        if reason:
            return reason, ready
    if phase == "Running" and not ready:
        return "Running（未就绪）", ready
    return phase, ready


def pod_owner_deployment(pod: Dict[str, Any]) -> Optional[str]:
    for owner in (pod.get("metadata") or {}).get("ownerReferences") or []:
        kind = owner.get("kind")
        name = owner.get("name") or ""
        if kind == "ReplicaSet" and "-" in name:
            return name.rsplit("-", 1)[0]
        if kind in ("StatefulSet", "DaemonSet"):
            return name
    return None


def pod_restarts(pod: Dict[str, Any]) -> int:
    total = 0
    for container_status in (pod.get("status") or {}).get("containerStatuses") or []:
        total += int(container_status.get("restartCount") or 0)
    return total


def parse_top_nodes(text: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """Parse `kubectl top nodes` output."""
    result: Dict[str, Dict[str, Any]] = {}
    if not text:
        return result
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] == "NAME":
            continue
        try:
            cpu = parse_cpu(parts[1])
            mem_index = 3 if "%" in parts[2] else 2
            memory = parse_memory(parts[mem_index])
        except (ValueError, IndexError):
            continue
        result[parts[0]] = {"cpu_cores": cpu, "mem_bytes": memory}
    return result


def parse_top_pods(text: Optional[str]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Parse `kubectl top pods -A` output."""
    result: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if not text:
        return result
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] == "NAMESPACE":
            continue
        try:
            cpu = parse_cpu(parts[2])
            memory = parse_memory(parts[3])
        except (ValueError, IndexError):
            continue
        result[(parts[0], parts[1])] = {"cpu_cores": cpu, "mem_bytes": memory}
    return result


def _deployment_resources(deployment: Dict[str, Any]) -> Tuple[float, float, int, int]:
    template = (deployment.get("spec") or {}).get("template") or {}
    pod_spec = template.get("spec") or {}
    cpu_request = cpu_limit = 0.0
    mem_request = mem_limit = 0
    for container in pod_spec.get("containers") or []:
        c_req, c_lim, m_req, m_lim = container_resources(container)
        cpu_request += c_req
        cpu_limit += c_lim
        mem_request += m_req
        mem_limit += m_lim
    return cpu_request, cpu_limit, mem_request, mem_limit


def _deployment_images(deployment: Dict[str, Any]) -> List[str]:
    template = (deployment.get("spec") or {}).get("template") or {}
    pod_spec = template.get("spec") or {}
    images = []
    for container in pod_spec.get("containers") or []:
        if container.get("image"):
            images.append(container["image"])
    return images


def build_snapshot(raw: Dict[str, Any], mode: str) -> Dict[str, Any]:
    error = raw.get("error")
    version_meta = {
        "kubectl_version": raw.get("kubectl_version"),
        "server_version": raw.get("server_version"),
        "context": raw.get("context"),
    }

    if error:
        checks = [{
            "id": "cluster",
            "title": "Kubernetes 集群",
            "status": "fail",
            "detail": str(error),
        }]
        return {
            "kind": "host",
            "generated_at": _now(),
            "mode": mode,
            "error": str(error),
            "kubernetes": dict(version_meta, reachable=False, metrics_available=False),
            "summary": {"nodes_total": 0, "nodes_ready": 0, "pods_total": 0, "pods_running": 0, "deployments_total": 0},
            "checks": checks,
            "nodes": [],
            "pods": [],
            "deployments": [],
            "kubeedge": {"device_crd": False, "edge_nodes": []},
        }

    nodes_raw = (raw.get("nodes") or {}).get("items") or []
    pods_raw = (raw.get("pods") or {}).get("items") or []
    deployments_raw = (raw.get("deployments") or {}).get("items") or []
    top_nodes = parse_top_nodes(raw.get("top_nodes"))
    top_pods = parse_top_pods(raw.get("top_pods"))
    metrics_available = bool(raw.get("metrics_available"))

    node_infos: List[Dict[str, Any]] = []
    node_by_name: Dict[str, Dict[str, Any]] = {}
    for node in nodes_raw:
        meta = node.get("metadata") or {}
        status = node.get("status") or {}
        name = meta.get("name") or ""
        labels = _labels(meta)
        usage = top_nodes.get(name) or {}
        info = {
            "name": name,
            "display_name": node_display_name(name, labels),
            "roles": node_roles(labels),
            "ready": node_ready(node),
            "taints": node_taints(node),
            "labels": labels,
            "cpu_allocatable_cores": parse_cpu((status.get("allocatable") or {}).get("cpu")),
            "mem_allocatable_bytes": parse_memory((status.get("allocatable") or {}).get("memory")),
            "cpu_requested_cores": 0.0,
            "cpu_limited_cores": 0.0,
            "cpu_used_cores": usage.get("cpu_cores"),
            "mem_requested_bytes": 0,
            "mem_limited_bytes": 0,
            "mem_used_bytes": usage.get("mem_bytes"),
            "pods": 0,
        }
        node_infos.append(info)
        node_by_name[name] = info

    pod_infos: List[Dict[str, Any]] = []
    for pod in pods_raw:
        meta = pod.get("metadata") or {}
        spec = pod.get("spec") or {}
        namespace = meta.get("namespace") or "default"
        name = meta.get("name") or ""
        node_name = spec.get("nodeName") or ""
        cpu_request, cpu_limit, mem_request, mem_limit = pod_resources(pod)
        usage = top_pods.get((namespace, name)) or {}
        status_text, ready = pod_status(pod)
        deployment = pod_owner_deployment(pod)
        display_node = node_by_name.get(node_name, {}).get("display_name") or node_name
        pod_infos.append({
            "namespace": namespace,
            "name": name,
            "node": node_name,
            "display_node": display_node,
            "deployment": deployment,
            "status": status_text,
            "ready": ready,
            "restarts": pod_restarts(pod),
            "cpu_request_cores": cpu_request,
            "cpu_limit_cores": cpu_limit,
            "cpu_used_cores": usage.get("cpu_cores"),
            "mem_request_bytes": mem_request,
            "mem_limit_bytes": mem_limit,
            "mem_used_bytes": usage.get("mem_bytes"),
            "qos": pod_qos(cpu_request, cpu_limit, mem_request, mem_limit),
            "image": (_containers(pod)[0].get("image") if _containers(pod) else None),
        })

        node_info = node_by_name.get(node_name)
        if node_info is not None:
            node_info["pods"] += 1
            node_info["cpu_requested_cores"] += cpu_request
            node_info["cpu_limited_cores"] += cpu_limit
            node_info["mem_requested_bytes"] += mem_request
            node_info["mem_limited_bytes"] += mem_limit

    deployment_infos: List[Dict[str, Any]] = []
    for deployment in deployments_raw:
        meta = deployment.get("metadata") or {}
        spec = deployment.get("spec") or {}
        status = deployment.get("status") or {}
        namespace = meta.get("namespace") or "default"
        name = meta.get("name") or ""
        replicas = int(spec.get("replicas") or 0)
        cpu_request, cpu_limit, mem_request, mem_limit = _deployment_resources(deployment)
        related = [p for p in pod_infos if p["namespace"] == namespace and p["deployment"] == name]
        deployment_infos.append({
            "namespace": namespace,
            "name": name,
            "replicas": replicas,
            "ready": int(status.get("readyReplicas") or 0),
            "available": int(status.get("availableReplicas") or 0),
            "cpu_request_per_pod_cores": cpu_request,
            "cpu_limit_per_pod_cores": cpu_limit,
            "cpu_request_total_cores": cpu_request * replicas,
            "mem_request_per_pod_bytes": mem_request,
            "mem_limit_per_pod_bytes": mem_limit,
            "images": _deployment_images(deployment),
            "nodes": sorted({p["display_node"] for p in related if p["node"]}),
            "node_selector": ((spec.get("template") or {}).get("spec") or {}).get("nodeSelector") or {},
        })

    node_infos.sort(key=lambda item: (item["roles"], item["name"]))
    pod_infos.sort(key=lambda item: (item["namespace"], item["name"]))
    deployment_infos.sort(key=lambda item: (item["namespace"], item["name"]))

    ready_nodes = [n for n in node_infos if n["ready"]]
    running_pods = [p for p in pod_infos if p["status"] == "Running"]
    edge_nodes = sorted({
        n["display_name"] for n in node_infos
        if "edge" in n["roles"] or (n.get("labels") or {}).get("kb.local/role") == "edge"
    })
    found_deployments = {d["name"] for d in deployment_infos}
    missing_expected = [name for name in EXPECTED_DEPLOYMENTS if name not in found_deployments]

    checks: List[Dict[str, str]] = []
    checks.append({
        "id": "kubectl",
        "title": "kubectl 可用",
        "status": "ok" if raw.get("kubectl_version") else "warn",
        "detail": raw.get("kubectl_version") or "未读取到客户端版本",
    })
    checks.append({
        "id": "cluster",
        "title": "集群可达",
        "status": "ok",
        "detail": "服务端 %s" % (raw.get("server_version") or "未知版本"),
    })
    checks.append({
        "id": "nodes",
        "title": "节点就绪",
        "status": "ok" if len(ready_nodes) == len(node_infos) else "warn",
        "detail": "%d/%d 节点 Ready" % (len(ready_nodes), len(node_infos)),
    })
    checks.append({
        "id": "metrics",
        "title": "实时资源监控",
        "status": "ok" if metrics_available else "warn",
        "detail": "metrics.k8s.io 可用" if metrics_available else "未检测到 metrics-server，CPU 使用量显示为空",
    })
    checks.append({
        "id": "device-crd",
        "title": "KubeEdge 设备接口",
        "status": "ok" if raw.get("device_crd") else "warn",
        "detail": "devices.devices.kubeedge.io 可用" if raw.get("device_crd") else "未检测到 Device CRD，设备页后续不可用",
    })
    checks.append({
        "id": "amari",
        "title": "Amarisoft 工作负载",
        "status": "ok" if not missing_expected else "warn",
        "detail": "全部存在" if not missing_expected else "未发现：" + ", ".join(missing_expected),
    })

    return {
        "kind": "host",
        "generated_at": _now(),
        "mode": mode,
        "error": None,
        "kubernetes": dict(
            version_meta,
            reachable=True,
            metrics_available=metrics_available,
        ),
        "summary": {
            "nodes_total": len(node_infos),
            "nodes_ready": len(ready_nodes),
            "pods_total": len(pod_infos),
            "pods_running": len(running_pods),
            "deployments_total": len(deployment_infos),
            "cpu_requested_cores": sum(n["cpu_requested_cores"] for n in node_infos),
            "cpu_allocatable_cores": sum(n["cpu_allocatable_cores"] for n in node_infos),
        },
        "checks": checks,
        "nodes": node_infos,
        "pods": pod_infos,
        "deployments": deployment_infos,
        "kubeedge": {
            "device_crd": bool(raw.get("device_crd")),
            "edge_nodes": edge_nodes,
        },
    }


class Collector:
    def __init__(self, kubectl: Optional[Kubectl] = None, demo: bool = False) -> None:
        self.kubectl = kubectl
        self.demo = demo

    def collect(self) -> Dict[str, Any]:
        if self.demo:
            return build_snapshot(fixtures.demo_raw(), "demo")
        return self._collect_live()

    def _collect_live(self) -> Dict[str, Any]:
        if self.kubectl is None:
            return build_snapshot({"error": "未配置 kubectl"}, "live")
        runner = self.kubectl.runner
        if runner.which("kubectl") is None:
            return build_snapshot({"error": "未找到 kubectl，请先安装并配置 kubeconfig"}, "live")

        raw: Dict[str, Any] = {
            "error": None,
            "metrics_available": False,
            "device_crd": False,
            "top_nodes": None,
            "top_pods": None,
        }

        try:
            version = self.kubectl.get_json(["version"], timeout=15)
            raw["kubectl_version"] = ((version.get("clientVersion") or {}).get("gitVersion"))
            raw["server_version"] = ((version.get("serverVersion") or {}).get("gitVersion"))
        except (KubectlError, ValueError):
            raw["kubectl_version"] = None
            raw["server_version"] = None

        context_result = self.kubectl.run(["config", "current-context"], timeout=10)
        raw["context"] = context_result.stdout.strip() if context_result.ok else None

        try:
            raw["nodes"] = self.kubectl.get_json(["get", "nodes"], timeout=20)
        except KubectlError as exc:
            raw["error"] = "无法读取节点列表：%s" % exc
            return build_snapshot(raw, "live")

        try:
            raw["pods"] = self.kubectl.get_json(["get", "pods", "-A"], timeout=25)
        except KubectlError:
            raw["pods"] = {"items": []}

        try:
            raw["deployments"] = self.kubectl.get_json(["get", "deployments", "-A"], timeout=25)
        except KubectlError:
            raw["deployments"] = {"items": []}

        top_nodes = self.kubectl.run(["top", "nodes", "--no-headers"], timeout=10)
        top_pods = self.kubectl.run(["top", "pods", "-A", "--no-headers"], timeout=10)
        if top_nodes.ok and top_pods.ok:
            raw["top_nodes"] = top_nodes.stdout
            raw["top_pods"] = top_pods.stdout
            raw["metrics_available"] = True

        device_crd = self.kubectl.run(
            ["get", "crd", "devices.devices.kubeedge.io", "-o", "name"],
            timeout=10,
        )
        raw["device_crd"] = device_crd.ok

        return build_snapshot(raw, "live")
