from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .actions import DEFAULT_COMPONENTS


@dataclass
class AgentTarget:
    name: str
    url: str


@dataclass
class Config:
    role: str = "host"
    listen: str = "127.0.0.1:8080"
    kubeconfig: Optional[str] = None
    context: Optional[str] = None
    demo: bool = False
    dry_run: bool = False
    sim: bool = False
    allow_real_hardware: bool = False
    audit_log: Path = field(default_factory=lambda: Path.home() / ".kb" / "audit.jsonl")
    agents: List[AgentTarget] = field(default_factory=list)
    components: List[Dict[str, str]] = field(default_factory=lambda: [dict(item) for item in DEFAULT_COMPONENTS])

    @classmethod
    def load(cls, path: Optional[str], args: Any = None) -> "Config":
        data: Dict[str, Any] = {}
        cfg_path = Path(path) if path else Path.cwd() / "kb.json"
        if cfg_path.is_file():
            data = json.loads(cfg_path.read_text(encoding="utf-8"))

        cfg = cls()
        for key in ("role", "listen", "kubeconfig", "context", "demo", "dry_run", "sim", "allow_real_hardware"):
            if key in data:
                setattr(cfg, key, data[key])
        if "audit_log" in data:
            cfg.audit_log = Path(data["audit_log"]).expanduser()
        cfg.agents = [AgentTarget(**item) for item in data.get("agents", [])]
        if data.get("components"):
            cfg.components = [dict(item) for item in data["components"]]

        if args is not None:
            for key in ("role", "listen", "kubeconfig", "context"):
                value = getattr(args, key, None)
                if value:
                    setattr(cfg, key, value)
            for flag in ("demo", "dry_run", "sim", "allow_real_hardware"):
                if getattr(args, flag, False):
                    setattr(cfg, flag, True)
        return cfg
