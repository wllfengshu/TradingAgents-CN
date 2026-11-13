"""StrategyPipeline._generate_signals：回放真实截面，验证预计算优先与 live 回退。"""

import pandas as pd
import pytest

from zstock.strategy_management.pipeline import StrategyPipeline


class _ReplayPipeline:
    def __init__(self, df: pd.DataFrame, *, fail_precomputed: bool = False):
        self._df = df
        self.fail_precomputed = fail_precomputed
        self.score_calls = 0
        self.live_calls = 0

    async def compute_signals(
        self, trade_date: str = None, prebuilt_data=None, prefer_precomputed: bool = True, **kwargs
    ) -> pd.DataFrame:
        if prebuilt_data is None and prefer_precomputed:
            try:
                return await self.score_signals(trade_date)
            except ValueError:
                pass
        return await self.score_signals_live(**kwargs)

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
    fp = _ReplayPipeline(real_signals)
    pipe = StrategyPipeline(factor_pipeline=fp)  # type: ignore[arg-type]
    df = await pipe._generate_signals(trade_date=real_trade_date)
    assert df is real_signals
    assert fp.score_calls == 1
    assert fp.live_calls == 0


@pytest.mark.asyncio
async def test_falls_back_to_live_on_missing_precompute(real_signals, real_trade_date):
    fp = _ReplayPipeline(real_signals, fail_precomputed=True)
    pipe = StrategyPipeline(factor_pipeline=fp)  # type: ignore[arg-type]
    df = await pipe._generate_signals(trade_date=real_trade_date, prefer_precomputed=True)
    assert df is real_signals
    assert fp.score_calls == 1
    assert fp.live_calls == 1


@pytest.mark.asyncio
async def test_prebuilt_skips_precomputed(real_signals, real_trade_date):
    fp = _ReplayPipeline(real_signals)
    pipe = StrategyPipeline(factor_pipeline=fp)  # type: ignore[arg-type]
    df = await pipe._generate_signals(
        trade_date=real_trade_date, prebuilt_data={"trade_date": real_trade_date}
    )
    assert df is real_signals
    assert fp.score_calls == 0
    assert fp.live_calls == 1
