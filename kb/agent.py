from __future__ import annotations

import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .runner import CommandRunner

WATCHED_SERVICES = ("tailscaled", "containerd", "edgecore")
WATCHED_COMMANDS = ("amari-ue-power", "amari-ue-status", "amari-mode", "amari-ue-network-mode")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class EdgeAgent:
    """Runs on an edge node and reports local capabilities."""

    def __init__(self, runner: CommandRunner, sim: bool = False, state_dir: Optional[str] = None) -> None:
        self.runner = runner
        self.sim = sim
        self.state_dir = Path(state_dir or "/var/lib/kb-sim")

    def collect(self) -> Dict[str, Any]:
        hostname = socket.gethostname()
        if self.sim:
            return self._sim_snapshot(hostname)
        return self._live_snapshot(hostname)

    def _live_snapshot(self, hostname: str) -> Dict[str, Any]:
        services = {}
        for service in WATCHED_SERVICES:
            result = self.runner.run(["systemctl", "is-active", service], timeout=5)
            services[service] = (result.stdout.strip() or result.stderr.strip() or "unknown")

        commands = {name: bool(self.runner.which(name)) for name in WATCHED_COMMANDS}
        devices = {"sdr0": Path("/dev/sdr0").exists()}
        tailscale_ip = self._tailscale_ip()

        checks: List[Dict[str, str]] = []
        edge_state = services.get("edgecore", "unknown")
        checks.append({
            "id": "edgecore",
            "title": "edgecore 服务",
            "status": "ok" if edge_state == "active" else "fail",
            "detail": edge_state,
        })
        checks.append({
            "id": "tailscale",
            "title": "tailscaled 服务",
            "status": "ok" if services.get("tailscaled") == "active" else "warn",
            "detail": services.get("tailscaled", "unknown"),
        })
        checks.append({
            "id": "sdr0",
            "title": "SDR 设备 /dev/sdr0",
            "status": "ok" if devices["sdr0"] else "warn",
            "detail": "已发现" if devices["sdr0"] else "未发现（UE 组件未启动时也可能如此）",
        })
        missing = [name for name, found in commands.items() if not found]
        checks.append({
            "id": "amari",
            "title": "amari 控制命令",
            "status": "ok" if not missing else "warn",
            "detail": "全部可用" if not missing else "缺少：" + ", ".join(missing),
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
            {"id": "edgecore", "title": "edgecore 服务", "status": "ok", "detail": "active（模拟）"},
            {"id": "tailscale", "title": "tailscaled 服务", "status": "ok", "detail": "active（模拟）"},
            {"id": "sdr0", "title": "SDR 设备 /dev/sdr0", "status": "ok", "detail": "已发现（模拟）"},
            {"id": "amari", "title": "amari 控制命令", "status": "ok", "detail": "全部可用（模拟）"},
        ]
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
            },
            "checks": checks,
        }

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
