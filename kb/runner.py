from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


@dataclass
class CommandResult:
    argv: List[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out and self.returncode == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "argv": self.argv,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "error": self.error,
        }


@dataclass
class CommandRunner:
    """Executes local commands without a shell, with timeout and audit logging."""

    dry_run: bool = False
    audit_path: Optional[Path] = None
    default_timeout: float = 20.0
    extra_env: Mapping[str, str] = field(default_factory=dict)

    def which(self, name: str) -> Optional[str]:
        return shutil.which(name)

    def run(
        self,
        argv: Sequence[str],
        timeout: Optional[float] = None,
        input_text: Optional[str] = None,
    ) -> CommandResult:
        argv_list = [str(item) for item in argv]
        started = time.monotonic()

        if self.dry_run:
            result = CommandResult(
                argv=argv_list,
                returncode=0,
                stdout="[dry-run] " + " ".join(argv_list),
                error=None,
            )
            result.duration_ms = int((time.monotonic() - started) * 1000)
            self._audit(result)
            return result

        env = None
        if self.extra_env:
            import os

            env = dict(os.environ)
            env.update(self.extra_env)

        try:
            completed = subprocess.run(
                argv_list,
                capture_output=True,
                text=True,
                timeout=timeout or self.default_timeout,
                input=input_text,
                env=env,
                check=False,
            )
            result = CommandResult(
                argv=argv_list,
                returncode=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
            )
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(
                argv=argv_list,
                returncode=124,
                stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "",
                timed_out=True,
                error="命令执行超时",
            )
        except FileNotFoundError:
            result = CommandResult(
                argv=argv_list,
                returncode=127,
                error="命令不存在：%s" % argv_list[0],
            )
        except OSError as exc:
            result = CommandResult(
                argv=argv_list,
                returncode=126,
                error="命令执行失败：%s" % exc,
            )

        result.duration_ms = int((time.monotonic() - started) * 1000)
        self._audit(result)
        return result

    def _audit(self, result: CommandResult) -> None:
        if not self.audit_path:
            return
        record = {
            "time": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "argv": result.argv,
            "returncode": result.returncode,
            "duration_ms": result.duration_ms,
            "timed_out": result.timed_out,
            "error": result.error,
            "stdout": result.stdout[:4000],
            "stderr": result.stderr[:4000],
        }
        try:
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
