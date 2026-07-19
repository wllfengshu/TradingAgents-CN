"""
xtquant model
"""
from __future__ import annotations

import logging
import sys
import time
from pydantic import BaseModel, Field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 账户 & 持仓
# ---------------------------------------------------------------------------

class AccountInfo(BaseModel):
    cash: float = Field(description="可用资金(元)")
    total_value: float = Field(description="总资产(元)")
    frozen_cash: float = Field(default=0.0, description="冻结资金(元)")


class Position(BaseModel):
    code: str = Field(description="股票代码，如 600000.SH")
    name: str = Field(default="", description="股票名称")
    volume: int = Field(description="持仓数量(股)")
    cost_price: float = Field(description="持仓成本价")
    current_price: float = Field(default=0.0, description="当前价格")

    @property
    def market_value(self) -> float:
        return self.volume * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.cost_price) * self.volume

