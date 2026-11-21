"""程序执行日志统一落盘到项目根 ``logs/``。"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]


def project_root() -> Path:
    # zstock/common/logging_setup.py → 项目根
    return Path(__file__).resolve().parents[2]


def logs_dir() -> Path:
    d = project_root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_log_path(script_name: str, *, stamped: bool = True) -> Path:
    safe = str(script_name).strip().replace(" ", "_") or "zstock"
    if stamped:
        return logs_dir() / f"{safe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    return logs_dir() / f"{safe}.log"


def setup_logging(
    script_name: str,
    *,
    log_file: Optional[PathLike] = None,
    level: int = logging.INFO,
    console: bool = True,
    stamped: bool = True,
    file_fmt: Optional[str] = None,
    console_fmt: Optional[str] = None,
) -> Path:
    """配置 root logger：可选控制台 + 文件（默认 ``logs/<script>_时间戳.log``）。

    ``log_file`` 显式传入时按该路径写（单测可用临时目录）。
    """
    path = Path(log_file) if log_file is not None else default_log_path(
        script_name, stamped=stamped
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    datefmt = "%H:%M:%S"
    f_fmt = logging.Formatter(
        file_fmt or "%(asctime)s %(levelname)-7s %(message)s", datefmt=datefmt
    )
    c_fmt = logging.Formatter(console_fmt or "%(message)s", datefmt=datefmt)

    if console:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(c_fmt)
        root.addHandler(sh)

    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(f_fmt)
    root.addHandler(fh)
    return path
