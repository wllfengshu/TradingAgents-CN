"""SignalGenerator：回放 Mongo 导出的真实截面，验证预计算优先与 live 回退。"""

import pandas as pd
import pytest

from zstock.strategy_management.signal_generator import SignalGenerator


class _ReplayPipeline:
    def __init__(self, df: pd.DataFrame, *, fail_precomputed: bool = False):
        self._df = df
        self.fail_precomputed = fail_precomputed
        self.score_calls = 0
        self.live_calls = 0

    async def score_signals(self, trade_date: str) -> pd.DataFrame:
        self.score_calls += 1
        if self.fail_precomputed:
            raise ValueError(f"无预计算 M1 数据: {trade_date}")
        return self._df

    async def score_signals_live(self, **kwargs) -> pd.DataFrame:
        self.live_calls += 1
        return self._df


@pytest.mark.asyncio
async def test_prefers_precomputed(real_signals, real_trade_date):
    pipe = _ReplayPipeline(real_signals)
    gen = SignalGenerator(factor_pipeline=pipe)  # type: ignore[arg-type]
    df = await gen.generate_signals(trade_date=real_trade_date)
    assert df is real_signals
    assert pipe.score_calls == 1
    assert pipe.live_calls == 0
    assert real_trade_date in gen.signals_history


@pytest.mark.asyncio
async def test_falls_back_to_live_on_missing_precompute(real_signals, real_trade_date):
    pipe = _ReplayPipeline(real_signals, fail_precomputed=True)
    gen = SignalGenerator(factor_pipeline=pipe)  # type: ignore[arg-type]
    df = await gen.generate_signals(trade_date=real_trade_date, prefer_precomputed=True)
    assert df is real_signals
    assert pipe.score_calls == 1
    assert pipe.live_calls == 1


@pytest.mark.asyncio
async def test_prebuilt_skips_precomputed(real_signals, real_trade_date):
    pipe = _ReplayPipeline(real_signals)
    gen = SignalGenerator(factor_pipeline=pipe)  # type: ignore[arg-type]
    df = await gen.generate_signals(
        trade_date=real_trade_date, prebuilt_data={"trade_date": real_trade_date}
    )
    assert df is real_signals
    assert pipe.score_calls == 0
    assert pipe.live_calls == 1
