from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import parse_qs, urlparse

from .. import fixtures
from ..actions import ActionError, UnknownAction, apply_yaml, catalog, get_action, run_action
from ..agent import EdgeAgent
from ..config import Config
from ..discovery import Collector
from ..domain import build_domain
from ..kube import Kubectl
from ..runner import CommandRunner

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _forward(agent_url: str, payload: Dict[str, Any], timeout: float = 180.0) -> Dict[str, Any]:
    """把操作转发给边缘节点上的控制台。"""

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urlrequest.Request(
        agent_url.rstrip("/") + "/api/run",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        message = detail
        try:
            message = json.loads(detail).get("error") or detail
        except (json.JSONDecodeError, AttributeError):
            pass
        raise ActionError("边缘控制台返回错误：%s" % (message or exc), status=502)
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        raise ActionError("无法连接边缘控制台 %s：%s" % (agent_url, exc), status=502)


class AppState:
    def __init__(self, cfg: Config, runner: CommandRunner) -> None:
        self.cfg = cfg
        self.runner = runner
        if cfg.role == "edge":
            self.agent: Optional[EdgeAgent] = EdgeAgent(
                runner,
                sim=cfg.sim or cfg.demo,
                prepare_script=cfg.prepare_script,
            )
            self.collector: Optional[Collector] = None
        else:
            self.agent = None
            kubectl = None if cfg.demo else Kubectl(runner, kubeconfig=cfg.kubeconfig, context=cfg.context)
            self.collector = Collector(kubectl=kubectl, demo=cfg.demo)
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_at = 0.0
        self._user_source_error: Optional[str] = None
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None
            self._cache_at = 0.0

    def snapshot(self, max_age: float = 5.0) -> Dict[str, Any]:
        with self._lock:
            current = time.monotonic()
            if self._cache is not None and current - self._cache_at < max_age:
                return self._cache
            if self.cfg.role == "edge" and self.agent is not None:
                data = self.agent.collect()
            elif self.collector is not None:
                data = self.collector.collect()
            else:
                data = {"kind": "host", "error": "未配置数据源", "generated_at": _now()}
            if data.get("kind") == "host":
                self._attach_domain(data)
            self._cache = data
            self._cache_at = current
            return data

    def _user_source(self) -> Optional[Dict[str, Any]]:
        """读取用户接入数据源；演示模式用内置样例，真实模式读 kb.json 里的 JSON。"""

        self._user_source_error = None
        if self.cfg.demo:
            return fixtures.demo_users()
        path = self.cfg.user_source
        if not path:
            return None
        source_path = Path(str(path)).expanduser()
        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
        except OSError as exc:
            self._user_source_error = "无法读取 user_source %s：%s" % (source_path, exc)
            return None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._user_source_error = "user_source %s 不是合法 JSON：%s" % (source_path, exc)
            return None
        if not isinstance(payload, dict):
            self._user_source_error = "user_source %s 需要是 JSON 对象" % source_path
            return None
        return payload

    def _attach_domain(self, data: Dict[str, Any]) -> None:
        try:
            users_source = self._user_source()
            domain = build_domain(
                data.get("nodes") or [],
                data.get("pods") or [],
                data.get("deployments") or [],
                users_source,
                self.cfg.ue_capacity,
                data.get("mode") or ("demo" if self.cfg.demo else "live"),
                self.cfg.node_arch_overrides,
            )
            if self._user_source_error:
                domain["user_source_error"] = self._user_source_error
            data["domain"] = domain
        except Exception as exc:  # 业务分面失败不影响原有集群视图
            data["domain"] = None
            data["domain_error"] = "业务视图聚合失败：%s" % exc

    def action_catalog(self) -> Dict[str, Any]:
        data = catalog(self.cfg)
        data["agents"] = self.agents()
        data["host_console"] = self.cfg.host_console
        data["user_source"] = self.cfg.user_source
        data["ue_capacity"] = self.cfg.ue_capacity
        data["mode"] = "demo" if self.cfg.demo else ("sim" if self.cfg.sim else "live")
        data["dry_run"] = bool(self.runner.dry_run)
        return data

    def agents(self) -> List[Dict[str, str]]:
        return [{"name": item.name, "url": item.url} for item in self.cfg.agents]

    def recent_log(self, limit: int = 40) -> List[Dict[str, Any]]:
        path = self.cfg.audit_log
        if not path.is_file():
            return []
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        items: List[Dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            items.append({
                "time": record.get("time"),
                "argv": record.get("argv") or [],
                "returncode": record.get("returncode"),
                "duration_ms": record.get("duration_ms"),
                "error": record.get("error"),
                "stdout": (record.get("stdout") or "")[:400],
                "stderr": (record.get("stderr") or "")[:400],
            })
        return items

    def dispatch(self, action_id: str, params: Optional[Dict[str, Any]], agent: Optional[str] = None) -> Dict[str, Any]:
        if agent:
            target = next((item for item in self.cfg.agents if item.name == agent or item.url == agent), None)
            url = target.url if target else str(agent)
            if not url.startswith("http"):
                url = "http://" + url
            data = _forward(url, {"id": action_id, "params": params or {}})
            data["forwarded_to"] = target.name if target else url
            data["local"] = False
            return data

        data = run_action(self.cfg, self.runner, action_id, params, components=self.cfg.components)
        data["forwarded_to"] = None
        data["local"] = True
        self.invalidate()
        return data

    def apply_yaml(self, text: Any) -> Dict[str, Any]:
        data = apply_yaml(self.cfg, self.runner, text)
        self.invalidate()
        return data


def _parse_listen(value: str) -> Tuple[str, int]:
    text = value.strip()
    if ":" not in text:
        return "127.0.0.1", int(text)
    host, port = text.rsplit(":", 1)
    return host or "127.0.0.1", int(port)


def make_handler(state: AppState):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

        def log_message(self, fmt, *args):  # quieter, single-line logs
            print("[kb] %s - %s" % (self.address_string(), fmt % args), flush=True)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/health":
                self._write_json({
                    "status": "ok",
                    "role": state.cfg.role,
                    "mode": "demo" if state.cfg.demo else ("sim" if state.cfg.sim else "live"),
                    "role_detail": state.cfg.role,
                    "allow_real_hardware": state.cfg.allow_real_hardware,
                    "dry_run": bool(state.runner.dry_run),
                    "time": _now(),
                })
                return
            if path == "/api/actions":
                self._write_json(state.action_catalog())
                return
            if path == "/api/agents":
                self._write_json({"items": state.agents()})
                return
            if path == "/api/log":
                query = parse_qs(urlparse(self.path).query)
                limit = 40
                try:
                    limit = min(200, max(1, int((query.get("limit") or ["40"])[0])))
                except (TypeError, ValueError):
                    limit = 40
                self._write_json({"items": state.recent_log(limit)})
                return
            if path == "/api/discovery":
                try:
                    data = state.snapshot()
                except Exception as exc:  # keep the UI alive on unexpected errors
                    data = {
                        "kind": state.cfg.role,
                        "error": "环境体检失败：%s" % exc,
                        "generated_at": _now(),
                        "checks": [],
                        "nodes": [],
                        "pods": [],
                        "deployments": [],
                    }
                self._write_json(data)
                return
            if path == "/":
                self.path = "/index.html"
            super().do_GET()

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in ("/api/run", "/api/apply"):
                self._write_json({"error": "未知接口：%s" % path}, status=404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 512 * 1024:
                    raise ValueError("请求体超过 512 KiB 限制")
                raw = self.rfile.read(length) if length > 0 else b"{}"
                payload = json.loads(raw.decode("utf-8") or "{}")
                if not isinstance(payload, dict):
                    raise ValueError("请求体需要是 JSON 对象")
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._write_json({"error": "请求体解析失败：%s" % exc}, status=400)
                return

            if path == "/api/apply":
                try:
                    data = state.apply_yaml(payload.get("yaml"))
                except ActionError as exc:
                    self._write_json({"error": str(exc), "id": "apply_yaml"}, status=exc.status)
                    return
                except Exception as exc:
                    self._write_json({"error": "应用 YAML 失败：%s" % exc, "id": "apply_yaml"}, status=500)
                    return
                self._write_json(data)
                return

            action_id = str(payload.get("id") or "")
            try:
                spec = get_action(action_id)
                if spec is None:
                    raise UnknownAction(action_id)
                if spec.confirm and not payload.get("confirm"):
                    raise ActionError("该操作需要确认后才能执行", status=428)
                data = state.dispatch(action_id, payload.get("params") or {}, payload.get("agent"))
            except ActionError as exc:
                self._write_json({"error": str(exc), "id": action_id}, status=exc.status)
                return
            except Exception as exc:  # 避免界面卡死，返回可读错误
                self._write_json({"error": "执行失败：%s" % exc, "id": action_id}, status=500)
                return
            self._write_json(data)

        def _write_json(self, payload: Dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(cfg: Config, runner: CommandRunner) -> None:
    host, port = _parse_listen(cfg.listen)
    state = AppState(cfg, runner)
    httpd = ThreadingHTTPServer((host, port), make_handler(state))
    httpd.daemon_threads = True

    print("KubeEdge Board 已启动", flush=True)
    print("  角色: %s    模式: %s" % (cfg.role, "demo" if cfg.demo else ("sim" if cfg.sim else "live")), flush=True)
    print("  监听: http://%s:%d" % (host, port), flush=True)
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("  注意: 当前监听所有网卡，请确认所在网络可信或启用访问控制", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
