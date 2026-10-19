from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .actions import catalog
from .agent import EdgeAgent
from .config import Config
from .discovery import Collector
from .kube import Kubectl
from .runner import CommandRunner
from .webui.server import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kb",
        description="KubeEdge Board 控制台（host / edge 两种角色）",
    )
    parser.add_argument("--role", choices=("host", "edge"), default=None, help="部署角色")
    parser.add_argument("--listen", default=None, help="监听地址，例如 0.0.0.0:8080")
    parser.add_argument("--kubeconfig", default=None, help="kubeconfig 路径")
    parser.add_argument("--context", default=None, help="kubectl context")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 ./kb.json）")
    parser.add_argument("--demo", action="store_true", help="使用内置演示数据")
    parser.add_argument("--sim", action="store_true", help="边缘侧使用模拟设备")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令，不执行")
    parser.add_argument("--allow-real-hardware", action="store_true", help="允许操作真实设备")
    parser.add_argument("--print-discovery", action="store_true", help="输出环境体检 JSON 后退出")
    parser.add_argument("--print-actions", action="store_true", help="输出当前角色的操作目录后退出")
    return parser


def ensure_local_config(explicit_path: str | None) -> Path | None:
    """首次启动时自动把 kb.json.example 复制成 kb.json。

    程序只读 kb.json；没这个文件时全部走内置默认值，用户改了 .example 也不会生效。
    这里补上这一步，省得每个人都要先手动 copy 一次。已存在的 kb.json 不会被动。
    """

    if explicit_path:
        return None
    target = Path.cwd() / "kb.json"
    if target.is_file():
        return None
    template = Path.cwd() / "kb.json.example"
    if not template.is_file():
        return None
    try:
        shutil.copyfile(template, target)
    except OSError as exc:
        print("提示: 无法自动生成 kb.json（%s），将使用内置默认配置" % exc)
        return None
    return target


def main(argv=None) -> int:
    # Windows 控制台/管道默认不是 UTF-8，输出中文 JSON 时会乱码。
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass

    args = build_parser().parse_args(argv)
    created = ensure_local_config(args.config)
    if created is not None:
        print("已根据 kb.json.example 生成 %s，后续修改配置请编辑这个文件。" % created)
    cfg = Config.load(args.config, args)
    cfg.audit_log.parent.mkdir(parents=True, exist_ok=True)
    # 演示模式下所有命令都只打印不执行，方便在没有集群的电脑上展示界面。
    runner = CommandRunner(dry_run=cfg.dry_run or cfg.demo, audit_path=cfg.audit_log)

    if args.print_discovery:
        if cfg.role == "edge":
            snapshot = EdgeAgent(
                runner,
                sim=cfg.sim or cfg.demo,
                prepare_script=cfg.prepare_script,
            ).collect()
        else:
            kubectl = None
            if not cfg.demo:
                kubectl = Kubectl(runner, kubeconfig=cfg.kubeconfig, context=cfg.context)
            snapshot = Collector(kubectl=kubectl, demo=cfg.demo).collect()
        json.dump(snapshot, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.print_actions:
        json.dump(catalog(cfg), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    serve(cfg, runner)
    return 0


if __name__ == "__main__":
    sys.exit(main())
