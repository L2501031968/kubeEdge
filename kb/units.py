from __future__ import annotations

import re
from typing import Optional

_CPU_SUFFIX = {"n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0}
_MEM_BINARY = {"Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "Ti": 1024 ** 4, "Pi": 1024 ** 5, "Ei": 1024 ** 6}
_MEM_DECIMAL = {"k": 1000, "K": 1000, "M": 1000 ** 2, "G": 1000 ** 3, "T": 1000 ** 4, "P": 1000 ** 5, "E": 1000 ** 6}

_CPU_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(n|u|m)?$")
_MEM_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(Ki|Mi|Gi|Ti|Pi|Ei|[kKMGTPE])?$")


def parse_cpu(value: Optional[str]) -> float:
    """Parse a Kubernetes CPU quantity into cores."""
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    match = _CPU_RE.match(text)
    if not match:
        raise ValueError("无法解析 CPU 数量：%r" % value)
    return float(match.group(1)) * _CPU_SUFFIX[match.group(2) or ""]


def parse_memory(value: Optional[str]) -> int:
    """Parse a Kubernetes memory quantity into bytes."""
    if value is None:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    match = _MEM_RE.match(text)
    if not match:
        raise ValueError("无法解析内存数量：%r" % value)
    number = float(match.group(1))
    suffix = match.group(2)
    if not suffix:
        return int(number)
    factor = _MEM_BINARY.get(suffix) or _MEM_DECIMAL.get(suffix)
    return int(number * factor)


def format_cpu(cores: Optional[float]) -> str:
    if cores is None:
        return "-"
    if cores <= 0:
        return "0"
    if cores < 0.001:
        return "%dn" % round(cores * 1e9)
    if cores < 1:
        return "%dm" % round(cores * 1000)
    return ("%.2f" % cores).rstrip("0").rstrip(".")


def format_memory(num_bytes: Optional[int]) -> str:
    if num_bytes is None:
        return "-"
    if num_bytes <= 0:
        return "0"
    for unit, factor in (("Gi", 1024 ** 3), ("Mi", 1024 ** 2), ("Ki", 1024)):
        if num_bytes >= factor:
            value = num_bytes / factor
            return ("%.1f" % value).rstrip("0").rstrip(".") + unit
    return "%dB" % num_bytes
