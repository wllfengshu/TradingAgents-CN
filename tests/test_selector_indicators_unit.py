"""
selector 指标模块单元测试

测试策略：
- 用 monkeypatch 替换 akshare 模块，不发起真实网络请求
- 用简易 FakeRedis 替换 Redis，测试可在无 Redis 环境下运行
- 每个 compute_* 入口函数 + 关键私有函数都有覆盖
"""

import importlib
import os
import sys
import types
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

# ── 项目根路径 ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── FakeRedis ───────────────────────────────────────────────────────────────
class FakeRedis:
    def __init__(self):
        self.store: dict = {}

    def get(self, key: str) -> Any:
        return self.store.get(key)

    def setex(self, key: str, expire: int, value: str) -> None:
        self.store[key] = value

    def ping(self):
        return True


# ── fixture：mock app 依赖，reload api_cache ─────────────────────────────────
@pytest.fixture(autouse=True)
def _mock_app_and_cache(monkeypatch):
    """
    1. 用假模块替换所有 app.* 和 redis 依赖
    2. reload api_cache 使其使用 FakeRedis，彻底隔离 Redis
    3. 每次测试前后清空本地缓存
    """
    fake_redis = FakeRedis()

    # ── mock redis 包（api_cache 在模块顶层 from redis import Redis）────────
    redis_mod = types.ModuleType("redis")
    redis_mod.Redis = type("Redis", (), {
        "from_url": staticmethod(lambda *a, **kw: fake_redis),
    })
    monkeypatch.setitem(sys.modules, "redis", redis_mod)

    # ── 构造最小化 app 模块树 ────────────────────────────────────────────────
    app_mod         = types.ModuleType("app")
    core_mod        = types.ModuleType("app.core")
    config_mod      = types.ModuleType("app.core.config")
    db_mod          = types.ModuleType("app.core.database")
    utils_mod       = types.ModuleType("app.utils")
    stock_utils_mod = types.ModuleType("app.utils.stock_utils")

    config_mod.settings = types.SimpleNamespace(REDIS_URL="redis://localhost:6379/0")
    db_mod.init_database  = lambda: None
    db_mod.close_database = lambda: None

    for name, mod in [
        ("app", app_mod), ("app.core", core_mod),
        ("app.core.config", config_mod), ("app.core.database", db_mod),
        ("app.utils", utils_mod), ("app.utils.stock_utils", stock_utils_mod),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    # ── reload api_cache，绑定 FakeRedis ──────────────────────────────────────
    sys.modules.pop("tradingagents.utils.api_cache", None)
    import tradingagents.utils.api_cache as api_cache
    api_cache = importlib.reload(api_cache)
    monkeypatch.setattr(api_cache, "_redis", fake_redis)
    api_cache._local_cache.clear()
    api_cache._stats["hits"] = 0
    api_cache._stats["misses"] = 0

    yield api_cache

    api_cache._local_cache.clear()


# ── 辅助：构造 fake_ak ───────────────────────────────────────────────────────
def _fake_ak(**attrs):
    return types.SimpleNamespace(**attrs)


# ════════════════════════════════════════════════════════════════════════════
# common_utils 工具函数
# ════════════════════════════════════════════════════════════════════════════
class TestToYi:
    def test_none_returns_zero(self):
        from tradingagents.utils.common_utils import to_yi
        assert to_yi(None) == 0.0

    def test_numeric_passthrough(self):
        from tradingagents.utils.common_utils import to_yi
        assert to_yi(3.5) == pytest.approx(3.5)
        assert to_yi(100) == pytest.approx(100.0)

    def test_yi_suffix(self):
        from tradingagents.utils.common_utils import to_yi
        assert to_yi("2.5亿元") == pytest.approx(2.5)
        assert to_yi("2.5亿")   == pytest.approx(2.5)

    def test_wan_suffix(self):
        from tradingagents.utils.common_utils import to_yi
        assert to_yi("5000万元") == pytest.approx(0.5)
        assert to_yi("5000万")   == pytest.approx(0.5)

    def test_yuan_suffix(self):
        from tradingagents.utils.common_utils import to_yi
        # 1亿元 = 1e8 元
        assert to_yi("100000000元") == pytest.approx(1.0)

    def test_comma_number(self):
        from tradingagents.utils.common_utils import to_yi
        assert to_yi("1,234,567,890") == pytest.approx(1234567890.0)

    def test_invalid_string_returns_zero(self):
        from tradingagents.utils.common_utils import to_yi
        assert to_yi("N/A") == 0.0


class TestIsMainBoardStock:
    def test_sh_main(self):
        from tradingagents.utils.common_utils import is_main_board_stock
        assert is_main_board_stock("600519")
        assert is_main_board_stock("601318")
        assert is_main_board_stock("603288")

    def test_sz_main(self):
        from tradingagents.utils.common_utils import is_main_board_stock
        assert is_main_board_stock("000001")
        assert is_main_board_stock("002594")

    def test_chinext_excluded(self):
        from tradingagents.utils.common_utils import is_main_board_stock
        assert not is_main_board_stock("300750")  # 创业板

    def test_star_market_excluded(self):
        from tradingagents.utils.common_utils import is_main_board_stock
        assert not is_main_board_stock("688599")  # 科创板

    def test_bse_excluded(self):
        from tradingagents.utils.common_utils import is_main_board_stock
        assert not is_main_board_stock("830946")  # 北交所


# ════════════════════════════════════════════════════════════════════════════
# market_indicators
# ════════════════════════════════════════════════════════════════════════════
class TestMarketIndicators:
    @pytest.fixture(autouse=True)
    def _reload_mod(self):
        sys.modules.pop("tradingagents.dataflows.selector.market_indicators", None)
        import tradingagents.dataflows.selector.market_indicators as m
        self.mod = importlib.reload(m)

    def _index_df(self, base):
        return pd.DataFrame({
            "date":   pd.date_range("2026-06-02", periods=6, freq="D"),
            "close":  [base + i for i in range(6)],
            "volume": [1000 + i * 100 for i in range(6)],
        })

    def test_full_report_contains_all_sections(self, monkeypatch):
        fake_ak = _fake_ak(
            stock_zh_index_daily=lambda symbol: self._index_df(3000),
            stock_hsgt_hist_em=lambda symbol: pd.DataFrame({
                "日期": ["2026-06-09"],
                "当日成交净买额": [1_230_000_000],
            }),
            stock_zh_a_spot_em=lambda: pd.DataFrame({"涨跌幅": [1.2, -0.6, 0.3, -1.1, 2.0]}),
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod.compute_market_indicators("2026-06-09")
        assert "上证指数" in out
        assert "深证成指" in out
        assert "创业板指" in out
        assert "北向资金" in out
        assert "涨跌统计" in out

    def test_index_change_pct_positive(self, monkeypatch):
        """今日收盘 > 昨日收盘 → 涨幅为正"""
        fake_ak = _fake_ak(stock_zh_index_daily=lambda symbol: self._index_df(3000))
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._index_daily_simple("sh000001", "上证指数", "2026-06-07")
        assert "上证指数：收盘 3005.00" in out
        # 每日+1，今日涨幅 1/3004 ≈ +0.03%
        assert "+0.03%" in out

    def test_northbound_net_inflow(self, monkeypatch):
        """净买额 > 0 → 净流入"""
        fake_ak = _fake_ak(stock_hsgt_hist_em=lambda symbol: pd.DataFrame({
            "日期": ["2026-06-09"],
            "当日成交净买额": [2_000_000_000],
        }))
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._northbound_simple("2026-06-09")
        assert "20.00 亿元" in out
        assert "净流入" in out

    def test_northbound_net_outflow(self, monkeypatch):
        """净买额 < 0 → 净流出"""
        fake_ak = _fake_ak(stock_hsgt_hist_em=lambda symbol: pd.DataFrame({
            "日期": ["2026-06-09"],
            "当日成交净买额": [-500_000_000],
        }))
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._northbound_simple("2026-06-09")
        assert "净流出" in out

    def test_northbound_alt_column_name(self, monkeypatch):
        """列名为'净流入额'时同样能识别"""
        fake_ak = _fake_ak(stock_hsgt_hist_em=lambda symbol: pd.DataFrame({
            "日期": ["2026-06-09"],
            "净流入额": [800_000_000],
        }))
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._northbound_simple("2026-06-09")
        assert "8.00 亿元" in out

    def test_breadth_ratio(self, monkeypatch):
        """涨3跌2 → 涨跌比 1.50"""
        fake_ak = _fake_ak(
            stock_zh_a_spot_em=lambda: pd.DataFrame({"涨跌幅": [1.0, -0.5, 2.0, -0.3, 3.0]})
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._breadth_simple()
        assert "上涨 3 只" in out
        assert "下跌 2 只" in out
        assert "1.50" in out

    def test_index_data_unavailable(self, monkeypatch):
        """API 返回空 DataFrame 时提示数据不可用"""
        fake_ak = _fake_ak(stock_zh_index_daily=lambda symbol: pd.DataFrame())
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._index_daily_simple("sh000001", "上证指数", "2026-06-09")
        assert "数据不可用" in out

    def test_northbound_api_error(self, monkeypatch):
        """API 异常时返回失败提示而非崩溃"""
        fake_ak = _fake_ak(stock_hsgt_hist_em=lambda symbol: (_ for _ in ()).throw(RuntimeError("timeout")))
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._northbound_simple("2026-06-09")
        assert "失败" in out or "不可用" in out


# ════════════════════════════════════════════════════════════════════════════
# sector_indicators
# ════════════════════════════════════════════════════════════════════════════
class TestSectorIndicators:
    @pytest.fixture(autouse=True)
    def _reload_mod(self):
        sys.modules.pop("tradingagents.dataflows.selector.sector_indicators", None)
        import tradingagents.dataflows.selector.sector_indicators as m
        self.mod = importlib.reload(m)

    def _full_fake_ak(self):
        return _fake_ak(
            stock_board_industry_summary_ths=lambda: pd.DataFrame({
                "板块名称": ["半导体", "算力", "汽车"],
                "涨跌幅": [3.2, 2.1, 1.0],
            }),
            stock_zt_pool_em=lambda date: pd.DataFrame({
                "代码":     ["000001", "000002", "000003"],
                "连板数":   [2, 1, 3],
                "所属行业": ["半导体", "半导体", "算力"],
                "封板资金": [10.0, 2.0, 9.0],
                "成交额":   [8.0, 4.0, 6.0],
            }),
            stock_zt_pool_strong_em=lambda date: pd.DataFrame({
                "代码":     ["000001", "000003", "000004"],
                "所属行业": ["半导体", "算力", "汽车"],
            }),
            stock_zt_pool_dtgc_em=lambda date: pd.DataFrame({"代码": ["000010"]}),
        )

    def test_full_report_all_sections(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "akshare", self._full_fake_ak())
        out = self.mod.compute_sector_indicators("2026-06-09")
        assert "涨幅前10板块" in out
        assert "涨停统计" in out
        assert "强势股池统计" in out
        assert "封板比统计" in out
        assert "炸板率统计" in out

    def test_sector_rank_order_and_format(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "akshare", self._full_fake_ak())
        out = self.mod._sector_rank_simple()
        assert "1. 半导体：+3.20%" in out
        assert "2. 算力：+2.10%" in out
        assert "3. 汽车：+1.00%" in out

    def test_zt_pool_count_and_multi_board(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "akshare", self._full_fake_ak())
        result, count = self.mod._zt_pool_simple("20260609")
        assert count == 3
        assert "涨停股总数：3 只" in result
        assert "连板股数量：2 只" in result
        assert "最高连板数：3 连板" in result

    def test_zt_pool_industry_concentration(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "akshare", self._full_fake_ak())
        result, _ = self.mod._zt_pool_simple("20260609")
        assert "半导体：2 只" in result

    def test_lb_pool_total_and_sector(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "akshare", self._full_fake_ak())
        out = self.mod._lb_pool_simple("20260609")
        assert "强势股池总数：3 只" in out
        assert "半导体" in out

    def test_seal_ratio_calculation(self, monkeypatch):
        """封板比 = 封板资金/成交额 均值：(10/8 + 2/4 + 9/6)/3 = (1.25+0.5+1.5)/3 ≈ 1.08"""
        monkeypatch.setitem(sys.modules, "akshare", self._full_fake_ak())
        out = self.mod._seal_ratio_simple("20260609")
        assert "1.08" in out
        assert "主力锁仓意愿强" in out

    def test_broken_rate_calculation(self, monkeypatch):
        """炸板1只，涨停3只 → 炸板率 1/(3+1)*100 = 25.0%"""
        monkeypatch.setitem(sys.modules, "akshare", self._full_fake_ak())
        out = self.mod._broken_rate_simple("20260609", zt_count=3)
        assert "炸板家数：1 只" in out
        assert "炸板率：25.0%" in out
        assert "情绪偏弱" in out

    def test_broken_rate_fallback_on_30day_window_error(self, monkeypatch):
        """超出30日窗口时回退到最新日期"""
        call_log = []

        def fake_dtgc(date):
            call_log.append(date)
            if date == "20240101":
                raise Exception("最近 30 个交易日限制")
            return pd.DataFrame({"代码": ["000001", "000002"]})

        fake_ak = _fake_ak(stock_zt_pool_dtgc_em=fake_dtgc)
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        # 替换 common_utils 中的 _latest_date_fmt
        import tradingagents.utils.common_utils as cu
        monkeypatch.setattr(cu, "_latest_date_fmt", lambda: "20260609")

        out = self.mod._broken_rate_simple("20240101", zt_count=5)
        assert call_log == ["20240101", "20260609"]
        assert "注：20240101 超出可查窗口" in out
        assert "炸板家数：2 只" in out

    def test_sector_rank_api_error_returns_failure_msg(self, monkeypatch):
        fake_ak = _fake_ak(
            stock_board_industry_summary_ths=lambda: (_ for _ in ()).throw(RuntimeError("连接超时"))
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._sector_rank_simple()
        assert "获取失败" in out


# ════════════════════════════════════════════════════════════════════════════
# force_indicators
# ════════════════════════════════════════════════════════════════════════════
class TestForceIndicators:
    @pytest.fixture(autouse=True)
    def _reload_mod(self):
        sys.modules.pop("tradingagents.dataflows.selector.force_indicators", None)
        import tradingagents.dataflows.selector.force_indicators as m
        self.mod = importlib.reload(m)

    def test_full_report_sections(self, monkeypatch):
        fake_ak = _fake_ak(
            stock_fund_flow_industry=lambda symbol: pd.DataFrame({
                "行业":   ["AI算力", "汽车"],
                "净流入额": [250_000_000, 120_000_000],
            }),
            stock_fund_flow_individual=lambda symbol: pd.DataFrame({
                "股票代码": ["600519", "300750"],
                "股票简称": ["贵州茅台", "宁德时代"],
                "净流入额": [80_000_000, 90_000_000],
            }),
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod.compute_force_indicators("2026-06-09", ["AI"])
        assert "板块资金流向" in out
        assert "个股资金流向" in out
        assert "合力股票筛选建议" in out

    def test_industry_flow_star_mark_on_confirmed_sector(self, monkeypatch):
        """确认主线板块应带 ★ 标记"""
        fake_ak = _fake_ak(
            stock_fund_flow_industry=lambda symbol: pd.DataFrame({
                "行业":   ["AI算力", "汽车"],
                "净流入额": [250_000_000, 120_000_000],
            }),
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._industry_flow_simple(["AI"])
        assert "AI算力 ★" in out
        assert "汽车 ★" not in out

    def test_industry_flow_amount_in_yi(self, monkeypatch):
        """金额原始值为元时（数字型），to_yi 直接当数字处理，显示原始值"""
        fake_ak = _fake_ak(
            stock_fund_flow_industry=lambda symbol: pd.DataFrame({
                "行业":   ["半导体"],
                "净流入额": [5.0],  # 已是亿元单位
            }),
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._industry_flow_simple([])
        assert "5.00 亿元" in out

    def test_individual_flow_filters_main_board_only(self, monkeypatch):
        """非主板股票（创业板300750）应被过滤掉"""
        fake_ak = _fake_ak(
            stock_fund_flow_individual=lambda symbol: pd.DataFrame({
                "股票代码": ["600519", "300750", "000001"],
                "股票简称": ["贵州茅台", "宁德时代", "平安银行"],
                "净流入额": [80_000_000, 90_000_000, 70_000_000],
            }),
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._individual_flow_simple()
        assert "贵州茅台" in out
        assert "平安银行" in out
        assert "宁德时代" not in out

    def test_individual_flow_sort_by_net_inflow(self, monkeypatch):
        """按净流入降序，第1名应为净流入最大的股票"""
        fake_ak = _fake_ak(
            stock_fund_flow_individual=lambda symbol: pd.DataFrame({
                "股票代码": ["600519", "601318"],
                "股票简称": ["贵州茅台", "中国平安"],
                "净流入额": [50_000_000, 200_000_000],
            }),
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._individual_flow_simple()
        idx_maotai = out.index("贵州茅台")
        idx_pingan  = out.index("中国平安")
        assert idx_pingan < idx_maotai  # 平安排前面

    def test_industry_flow_api_error(self, monkeypatch):
        fake_ak = _fake_ak(
            stock_fund_flow_industry=lambda symbol: (_ for _ in ()).throw(RuntimeError("网络错误"))
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._industry_flow_simple([])
        assert "获取失败" in out


# ════════════════════════════════════════════════════════════════════════════
# leader_indicators
# ════════════════════════════════════════════════════════════════════════════
class TestLeaderIndicators:
    @pytest.fixture(autouse=True)
    def _reload_mod(self):
        sys.modules.pop("tradingagents.dataflows.selector.leader_indicators", None)
        import tradingagents.dataflows.selector.leader_indicators as m
        self.mod = importlib.reload(m)

    def _fake_ak_full(self):
        return _fake_ak(
            stock_zt_pool_em=lambda date: pd.DataFrame({
                "代码":     ["000001", "000002", "000003"],
                "名称":     ["平安银行", "万科A", "招商银行"],
                "所属行业": ["银行", "地产", "银行"],
                "连板数":   [3, 1, 2],
                "成交额":   [1_000_000, 2_000_000, 500_000],
            }),
            stock_zt_pool_strong_em=lambda date: pd.DataFrame({
                "代码": ["000001", "600519"],
                "名称": ["平安银行", "贵州茅台"],
            }),
        )

    def test_full_report_sections(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "akshare", self._fake_ak_full())
        out = self.mod.compute_leader_indicators("2026-06-09", [{"code": "000001"}])
        assert "涨停股连板统计" in out
        assert "强势股排名" in out
        assert "龙头股筛选建议" in out

    def test_zt_leader_shows_multi_board_stocks(self, monkeypatch):
        """只展示连板数 >= 2 的股票"""
        monkeypatch.setitem(sys.modules, "akshare", self._fake_ak_full())
        out = self.mod._zt_leader_simple("20260609")
        assert "000001 平安银行（银行）：3 连板" in out
        assert "000003 招商银行（银行）：2 连板" in out
        # 万科A连板数=1，不应出现
        assert "万科A" not in out

    def test_zt_leader_sort_descending(self, monkeypatch):
        """连板数高的排在前面"""
        monkeypatch.setitem(sys.modules, "akshare", self._fake_ak_full())
        out = self.mod._zt_leader_simple("20260609")
        pos_3 = out.index("3 连板")
        pos_2 = out.index("2 连板")
        assert pos_3 < pos_2

    def test_zt_leader_filters_zero_amount(self, monkeypatch):
        """成交额为0的行（集合竞价异常）应被过滤"""
        fake_ak = _fake_ak(
            stock_zt_pool_em=lambda date: pd.DataFrame({
                "代码":     ["000001", "000002"],
                "名称":     ["平安银行", "测试股"],
                "所属行业": ["银行", "测试"],
                "连板数":   [2, 3],
                "成交额":   [1_000_000, 0],  # 000002成交额为0
            }),
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._zt_leader_simple("20260609")
        assert "平安银行" in out
        assert "测试股" not in out

    def test_strong_rank_matched_stocks(self, monkeypatch):
        """优质标的在强势股池中时，应标记 ✅"""
        monkeypatch.setitem(sys.modules, "akshare", self._fake_ak_full())
        out = self.mod._strong_rank_simple("20260609", ["000001", "600519"])
        assert "✅ 000001 平安银行：已进入强势股池" in out
        assert "✅ 600519 贵州茅台：已进入强势股池" in out

    def test_strong_rank_unmatched_stocks(self, monkeypatch):
        """优质标的未进入强势股池时，有对应提示"""
        monkeypatch.setitem(sys.modules, "akshare", self._fake_ak_full())
        out = self.mod._strong_rank_simple("20260609", ["999999"])
        assert "未进入强势股池" in out

    def test_zt_leader_fallback_on_window_error(self, monkeypatch):
        """超出30日窗口时回退到最新日期"""
        call_log = []

        def fake_zt(date):
            call_log.append(date)
            if date == "20240101":
                raise Exception("最近30个交易日限制")
            return pd.DataFrame({
                "代码": ["000001"], "名称": ["平安银行"],
                "所属行业": ["银行"], "连板数": [2], "成交额": [1_000_000],
            })

        fake_ak = _fake_ak(stock_zt_pool_em=fake_zt)
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        import tradingagents.utils.common_utils as cu
        monkeypatch.setattr(cu, "_latest_date_fmt", lambda: "20260609")

        out = self.mod._zt_leader_simple("20240101")
        assert call_log == ["20240101", "20260609"]
        assert "注：20240101 超出可查窗口" in out


# ════════════════════════════════════════════════════════════════════════════
# risk_indicators
# ════════════════════════════════════════════════════════════════════════════
class TestRiskIndicators:
    @pytest.fixture(autouse=True)
    def _reload_mod(self, monkeypatch):
        # mock AKShareProvider，避免真实网络连接
        provider_mod = types.ModuleType("tradingagents.dataflows.providers.china.akshare")

        class FakeProvider:
            def get_stock_basic_info(self, code):
                return {
                    "name": f"测试股{code}", "industry": "测试行业",
                    "area": "测试地区", "list_date": "2020-01-01",
                }

            def get_financial_data(self, code):
                return {
                    "main_indicators": [
                        {"指标": "市盈率(PE)", "2026-06-09": 12.3},
                        {"指标": "市净率(PB)", "2026-06-09": 1.8},
                        {"指标": "资产负债率", "2026-06-09": 45.6},
                    ],
                    "balance_sheet": [
                        {"total_assets": 100.0, "total_liab": 45.6}
                    ],
                }
        provider_mod.AKShareProvider = FakeProvider
        monkeypatch.setitem(sys.modules,
                            "tradingagents.dataflows.providers.china.akshare",
                            provider_mod)

        sys.modules.pop("tradingagents.dataflows.selector.risk_indicators", None)
        import tradingagents.dataflows.selector.risk_indicators as m
        self.mod = importlib.reload(m)

    def test_full_report_sections(self, monkeypatch):
        fake_ak = _fake_ak(
            stock_zh_a_new=lambda: pd.DataFrame({
                "代码": ["688001"], "名称": ["科创新股"],
                "上市日期": [pd.Timestamp("2026-05-20")],
            }),
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod.compute_risk_indicators("2026-06-09", [{"code": "000001"}])
        assert "名称前缀高风险过滤" in out
        assert "排除新股" in out
        assert "基本面分析" in out
        assert "风险评估建议" in out

    def test_new_stock_recent_30days(self, monkeypatch):
        """近30天上市的新股应出现在列表中"""
        fake_ak = _fake_ak(
            stock_zh_a_new=lambda: pd.DataFrame({
                "代码": ["688001", "000002"],
                "名称": ["新股A", "老股B"],
                "上市日期": [
                    pd.Timestamp("2026-05-25"),  # 在30天内
                    pd.Timestamp("2025-01-01"),  # 超过30天
                ],
            }),
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._new_stock_simple(["000001", "688001"])
        assert "新股A" in out
        assert "000001" not in out

    def test_new_stock_none_in_30days(self, monkeypatch):
        """候选股不在新股列表中时有对应提示"""
        fake_ak = _fake_ak(
            stock_zh_a_new=lambda: pd.DataFrame({
                "代码": ["000001"],
                "名称": ["老股"],
                "上市日期": [pd.Timestamp("2025-01-01")],
            }),
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._new_stock_simple(["000002"])
        assert "候选股不在新股列表中" in out

    def test_new_stock_no_date_column(self, monkeypatch):
        """接口不返回日期列时，只要有代码字段仍可判断"""
        fake_ak = _fake_ak(
            stock_zh_a_new=lambda: pd.DataFrame({
                "代码": ["688001", "688002"],
                "名称": ["新股A", "新股B"],
            }),
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._new_stock_simple(["688001"])
        assert "新股A" in out

    def test_fundamentals_returns_all_fields(self, monkeypatch):
        """基本面数据应包含名称、行业、地区、上市日期"""
        out = self.mod._fundamentals_simple(["000001"])
        assert "名称" in out
        assert "行业" in out
        assert "地区" in out
        assert "上市日期" in out
        assert "PE" in out
        assert "PB" in out
        assert "资产负债率" in out

    def test_fundamentals_max_three_stocks(self, monkeypatch):
        """最多只处理3只股票"""
        out = self.mod._fundamentals_simple(["000001", "600519", "300750", "688001"])
        # 第4只不应出现
        assert out.count("基本面数据") == 3

    def test_no_stocks_returns_early(self, monkeypatch):
        out = self.mod.compute_risk_indicators("2026-06-09", [])
        assert "无候选股票" in out

    def test_new_stock_api_error(self, monkeypatch):
        fake_ak = _fake_ak(
            stock_zh_a_new=lambda: (_ for _ in ()).throw(RuntimeError("接口异常"))
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        out = self.mod._new_stock_simple(["000001"])
        assert "获取失败" in out
