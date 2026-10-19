from __future__ import annotations

import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .health import select_container_tool, service_check_status
from .prepare import resolve_prepare_script
from .runner import CommandRunner

WATCHED_SERVICES = ("tailscaled", "containerd", "edgecore")
WATCHED_COMMANDS = ("amari-ue-power", "amari-ue-status", "amari-mode", "amari-ue-network-mode")
CLOUD_PORTS = ("10000", "10004")
SERVICE_LABELS = {
    "tailscaled": "tailscaled 服务",
    "containerd": "containerd 服务",
    "edgecore": "edgecore 服务",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class EdgeAgent:
    """Runs on an edge node and reports local capabilities."""

    def __init__(
        self,
        runner: CommandRunner,
        sim: bool = False,
        state_dir: Optional[str] = None,
        prepare_script: Optional[str] = None,
    ) -> None:
        self.runner = runner
        self.sim = sim
        self.state_dir = Path(state_dir or "/var/lib/kb-sim")
        self.prepare_script = prepare_script

    def collect(self) -> Dict[str, Any]:
        hostname = socket.gethostname()
        if self.sim:
            return self._sim_snapshot(hostname)
        return self._live_snapshot(hostname)

    def _live_snapshot(self, hostname: str) -> Dict[str, Any]:
        services = {service: self._service_state(service) for service in WATCHED_SERVICES}
        commands = {name: bool(self.runner.which(name)) for name in WATCHED_COMMANDS}
        devices = {"sdr0": Path("/dev/sdr0").exists()}
        tailscale_ip = self._tailscale_ip()
        kysec = self._kysec_state()
        ports = self._cloud_ports()
        sdr_usage = self._sdr_usage(devices["sdr0"])
        script = resolve_prepare_script(self.prepare_script)
        container_tool = select_container_tool(self.runner)

        checks: List[Dict[str, str]] = []
        for service in WATCHED_SERVICES:
            state = services.get(service, "unknown")
            checks.append({
                "id": "service-" + service,
                "title": SERVICE_LABELS[service],
                "status": service_check_status(state),
                "detail": state,
            })
        checks.append({
            "id": "sdr0",
            "title": "SDR 设备 /dev/sdr0",
            "status": "ok" if devices["sdr0"] else "warn",
            "detail": "已发现" if devices["sdr0"] else "未发现（UE 组件未启动时也可能如此）",
        })
        checks.append({
            "id": "sdr-busy",
            "title": "SDR 占用状态",
            "status": sdr_usage["status"],
            "detail": sdr_usage["detail"],
        })
        checks.append({
            "id": "kysec",
            "title": "KYSEC 状态",
            "status": kysec["status"],
            "detail": kysec["detail"],
        })
        checks.append({
            "id": "cloud-ports",
            "title": "EdgeCore 云端连接",
            "status": ports["status"],
            "detail": ports["detail"],
        })
        missing = [name for name, found in commands.items() if not found]
        checks.append({
            "id": "amari",
            "title": "amari 控制命令",
            "status": "ok" if not missing else "warn",
            "detail": "全部可用" if not missing else "缺少：" + ", ".join(missing),
        })
        checks.append({
            "id": "container-tool",
            "title": "容器查询工具",
            "status": "ok" if container_tool else "warn",
            "detail": (
                "已选择：" + container_tool["display"]
                if container_tool
                else "未找到 nerdctl / crictl / ctr；不影响 KubeEdge，仅无法从页面列容器"
            ),
        })
        checks.append({
            "id": "prepare-script",
            "title": "环境准备脚本",
            "status": "ok" if script.found else ("fail" if script.source == "configured-missing" else "warn"),
            "detail": script.detail,
        })

        return {
            "kind": "edge",
            "generated_at": _now(),
            "mode": "live",
            "agent": {
                "hostname": hostname,
                "sim": False,
                "services": services,
                "commands": commands,
                "devices": devices,
                "tailscale_ip": tailscale_ip,
                "ue_power": None,
                "mode": None,
                "network_mode": None,
                "prepare_script": script.path,
                "container_tool": container_tool["display"] if container_tool else None,
            },
            "checks": checks,
        }

    def _sim_snapshot(self, hostname: str) -> Dict[str, Any]:
        state = self.state_dir
        ue_power = self._read_state("ue-power", "off")
        sim_mode = self._read_state("amari-mode", "edge-container")
        network_mode = self._read_state("ue-network-mode", "direct")

        services = {name: "active" for name in WATCHED_SERVICES}
        commands = {name: True for name in WATCHED_COMMANDS}
        checks = [
            {
                "id": "service-" + name,
                "title": SERVICE_LABELS[name],
                "status": "ok",
                "detail": "active（模拟）",
            }
            for name in WATCHED_SERVICES
        ]
        checks.extend([
            {"id": "sdr0", "title": "SDR 设备 /dev/sdr0", "status": "ok", "detail": "已发现（模拟）"},
            {"id": "sdr-busy", "title": "SDR 占用状态", "status": "ok", "detail": "未被占用（模拟）"},
            {"id": "kysec", "title": "KYSEC 状态", "status": "ok", "detail": "disable（模拟）"},
            {
                "id": "cloud-ports",
                "title": "EdgeCore 云端连接",
                "status": "ok",
                "detail": "已建立 :10000 / :10004 云端连接（模拟）",
            },
            {"id": "amari", "title": "amari 控制命令", "status": "ok", "detail": "全部可用（模拟）"},
            {
                "id": "container-tool",
                "title": "容器查询工具",
                "status": "ok",
                "detail": "nerdctl --namespace k8s.io ps（模拟）",
            },
            {
                "id": "prepare-script",
                "title": "环境准备脚本",
                "status": "warn",
                "detail": "模拟环境跳过 amari-host-prepare.sh / amari-ue-prepare.sh",
            },
        ])
        return {
            "kind": "edge",
            "generated_at": _now(),
            "mode": "sim",
            "agent": {
                "hostname": hostname,
                "sim": True,
                "services": services,
                "commands": commands,
                "devices": {"sdr0": True},
                "tailscale_ip": "100.72.31.108",
                "ue_power": ue_power,
                "mode": sim_mode,
                "network_mode": network_mode,
                "prepare_script": None,
                "container_tool": "nerdctl --namespace k8s.io ps（模拟）",
            },
            "checks": checks,
        }

    def _service_state(self, service: str) -> str:
        result = self.runner.run(["systemctl", "is-active", service], timeout=5)
        output = result.stdout.strip() or result.stderr.strip() or "unknown"
        return output.splitlines()[0].strip()

    def _kysec_state(self) -> Dict[str, str]:
        if self.runner.which("getstatus") is None:
            return {"status": "warn", "detail": "未找到 getstatus，KYSEC 未安装或未配置"}
        result = self.runner.run(["getstatus"], timeout=8)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return {
                "status": "warn",
                "detail": (result.stderr.strip().splitlines() or [""])[0] or "getstatus 没有输出",
            }
        first = lines[0]
        if "disable" in first.lower():
            return {"status": "ok", "detail": first}
        return {"status": "fail", "detail": first}

    def _cloud_ports(self) -> Dict[str, str]:
        outputs: List[str] = []
        for argv in (["ss", "-ntp"], ["sudo", "-n", "ss", "-ntp"]):
            result = self.runner.run(argv, timeout=6)
            if result.stdout.strip():
                outputs.append(result.stdout)
        if not outputs:
            return {"status": "warn", "detail": "无法读取端口连接，请确认 ss 可用"}
        combined = "\n".join(outputs)
        missing = [port for port in CLOUD_PORTS if (":%s" % port) not in combined]
        if not missing:
            return {"status": "ok", "detail": "已建立 :10000 / :10004 云端连接"}
        return {
            "status": "warn",
            "detail": "未看到 :%s 连接（edgecore 未启动或连接尚未建立）" % ", :".join(missing),
        }

    def _sdr_usage(self, exists: bool) -> Dict[str, str]:
        if not exists:
            return {"status": "warn", "detail": "设备不存在，无法检查占用"}
        result = self.runner.run(["sudo", "-n", "fuser", "-v", "/dev/sdr0"], timeout=8)
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        if output:
            first = output.splitlines()[0]
            lowered = output.lower()
            if "sudo:" in lowered or "password" in lowered:
                return {"status": "warn", "detail": "无法读取（需要 sudo 免密）：" + first}
            if "lteue" in lowered:
                return {"status": "fail", "detail": "被 UE 进程占用：" + first}
            return {"status": "warn", "detail": "被占用：" + first}
        if result.returncode == 1:
            return {"status": "ok", "detail": "未被占用"}
        if result.ok:
            return {"status": "warn", "detail": "命令完成但没有输出"}
        return {"status": "warn", "detail": result.error or "无法读取占用状态"}

    def _read_state(self, name: str, default: str) -> str:
        path = self.state_dir / name
        if not self.state_dir.is_dir() or not path.is_file():
            return default
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            return default
        return value or default

    def _tailscale_ip(self) -> Optional[str]:
        result = self.runner.run(["tailscale", "ip", "-4"], timeout=5)
        if not result.ok:
            return None
        return result.stdout.strip().splitlines()[0] if result.stdout.strip() else None
