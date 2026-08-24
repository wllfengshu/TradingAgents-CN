"""量化任务资源预算：默认最多占用约一半 CPU / 内存，避免拖垮整机。"""

from __future__ import annotations

import ctypes
import logging
import os
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

# 主进程预加载 OHLCV 预留（GB）
_PRELOAD_RESERVE_GB = 6.0
# 每个因子计算子进程估算内存（GB）
_WORKER_MEM_GB = 0.55


class ResourceBudget(NamedTuple):
    resource_fraction: float
    total_memory_gb: float
    cpu_cores: int
    compute_workers: int
    load_workers: int
    query_workers: int


def _total_memory_bytes() -> int:
    try:
        import psutil

        return int(psutil.virtual_memory().total)
    except ImportError:
        pass

    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return int(stat.ullTotalPhys)

    return 16 * 1024**3


def get_resource_fraction() -> float:
    raw = os.environ.get("ZSTOCK_RESOURCE_FRACTION", "0.5")
    try:
        frac = float(raw)
    except ValueError:
        logger.warning("忽略无效 ZSTOCK_RESOURCE_FRACTION=%r，使用 0.5", raw)
        frac = 0.5
    return min(1.0, max(0.1, frac))


def compute_resource_budget(
    fraction: Optional[float] = None,
) -> ResourceBudget:
    """按 CPU 核数与物理内存推导并发上限。"""
    frac = get_resource_fraction() if fraction is None else min(1.0, max(0.1, fraction))
    cpu = os.cpu_count() or 8
    total_gb = _total_memory_bytes() / (1024**3)

    max_by_cpu = max(1, int(cpu * frac))
    preload_reserve = min(_PRELOAD_RESERVE_GB, total_gb * 0.25)
    worker_budget_gb = max(0.5, total_gb * frac - preload_reserve)
    max_by_mem = max(1, int(worker_budget_gb / _WORKER_MEM_GB))

    compute = max(1, min(max_by_cpu, max_by_mem))
    load = max(2, min(8, max(1, compute // 2)))
    query = max(4, min(12, max(1, int(cpu * frac * 0.75))))

    return ResourceBudget(
        resource_fraction=frac,
        total_memory_gb=round(total_gb, 1),
        cpu_cores=cpu,
        compute_workers=compute,
        load_workers=load,
        query_workers=query,
    )


def cap_worker_count(
    requested: Optional[int],
    budget_value: int,
    *,
    name: str,
) -> int:
    """显式指定的并发数也会被资源预算封顶。"""
    if requested is None:
        return budget_value
    if requested > budget_value:
        logger.warning(
            "%s=%d 超过资源预算上限 %d，已自动限制",
            name,
            requested,
            budget_value,
        )
    return max(1, min(requested, budget_value))


def default_query_concurrency() -> int:
    return compute_resource_budget().query_workers
