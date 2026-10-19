"""定位边缘节点上的环境准备脚本。

真实设备按文档把准备脚本放在 ``~/amari-edge`` 或 ``~/amari-ue-edge``：

* U3 基站：``amari-host-prepare.sh``
* sdr UE：``amari-ue-prepare.sh``

控制台不需要用户先手工执行，只在页面上定位脚本并通过 ``sudo -n`` 运行。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_CANDIDATES = (
    "~/amari-edge/amari-host-prepare.sh",
    "~/amari-edge/amari-ue-prepare.sh",
    "~/amari-ue-edge/amari-ue-prepare.sh",
    "/opt/amari-edge/amari-host-prepare.sh",
    "/opt/amari-ue-edge/amari-ue-prepare.sh",
)


@dataclass
class PrepareScript:
    path: Optional[str]
    source: str  # configured | auto | configured-missing | missing

    @property
    def found(self) -> bool:
        return bool(self.path) and self.source in ("configured", "auto")

    @property
    def detail(self) -> str:
        if self.source == "configured":
            return "使用 kb.json 配置：%s" % self.path
        if self.source == "auto":
            return "自动发现：%s" % self.path
        if self.source == "configured-missing":
            return "kb.json 配置的脚本不存在：%s" % self.path
        return "未找到，可把脚本放到 %s 或在 kb.json 里设置 prepare_script" % DEFAULT_CANDIDATES[0]


def resolve_prepare_script(configured: Optional[str] = None) -> PrepareScript:
    """按配置或默认路径定位准备脚本，不检查可执行位。"""

    if configured:
        path = Path(str(configured)).expanduser()
        if path.is_file():
            return PrepareScript(path=str(path), source="configured")
        return PrepareScript(path=str(path), source="configured-missing")

    for candidate in DEFAULT_CANDIDATES:
        path = Path(candidate).expanduser()
        if path.is_file():
            return PrepareScript(path=str(path), source="auto")
    return PrepareScript(path=None, source="missing")
