from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from .runner import CommandResult, CommandRunner


class KubectlError(RuntimeError):
    def __init__(self, message: str, result: Optional[CommandResult] = None) -> None:
        super().__init__(message)
        self.result = result


class Kubectl:
    """Thin wrapper around the kubectl CLI."""

    def __init__(
        self,
        runner: CommandRunner,
        kubeconfig: Optional[str] = None,
        context: Optional[str] = None,
        default_timeout: float = 20.0,
    ) -> None:
        self.runner = runner
        self.kubeconfig = kubeconfig
        self.context = context
        self.default_timeout = default_timeout

    def base_args(self) -> List[str]:
        args = ["kubectl"]
        if self.kubeconfig:
            args += ["--kubeconfig", str(self.kubeconfig)]
        if self.context:
            args += ["--context", self.context]
        return args

    def run(self, args: Sequence[str], timeout: Optional[float] = None) -> CommandResult:
        return self.runner.run(self.base_args() + [str(item) for item in args], timeout=timeout or self.default_timeout)

    def get_json(self, args: Sequence[str], timeout: Optional[float] = None) -> Dict[str, Any]:
        result = self.run(list(args) + ["-o", "json"], timeout=timeout)
        if not result.ok:
            detail = (result.stderr or result.stdout or result.error or "kubectl 执行失败").strip()
            raise KubectlError(detail, result)
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise KubectlError("kubectl 输出不是合法 JSON：%s" % exc, result)
