# -*- coding: utf-8 -*-
"""兼容入口：转发到每晚流水线（stockdb 同步 + 因子预计算）。

历史名称含 QMT，实际日线/资金流已改走 stockdb。
新代码请直接：
    python -m zstock.data_management.script.nightly_pipeline
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.data_management.script.nightly_pipeline import main


if __name__ == "__main__":
    raise SystemExit(main())
