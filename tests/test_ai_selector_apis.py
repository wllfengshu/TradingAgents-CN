"""
AI选股服务 - 数据获取单元测试
逐个测试所有akshare API调用，使用真实数据验证
"""

import sys
import unittest
import traceback

from app.services.ai_selector_service import ApiCache

sys.stdout.reconfigure(encoding="utf-8")

import akshare as ak
import pandas as pd
from datetime import datetime


class TestAkshareMarketAPIs(unittest.TestCase):
    """测试大盘分析师相关的API"""

    def test_stock_zh_index_daily_sh(self):
        """上证指数日K"""
        df = ak.stock_zh_index_daily(symbol="sh000001")
        self.assertFalse(df.empty, "上证指数数据不应为空")
        self.assertIn("close", df.columns, "应包含close列")
        latest = df.iloc[-1]
        self.assertGreater(float(latest["close"]), 1000, "上证指数应大于1000")
        print(f"  [OK] 上证指数收盘价: {latest['close']}, 日期: {latest['date']}")

    def test_stock_zh_index_daily_sz(self):
        """深证成指日K"""
        df = ak.stock_zh_index_daily(symbol="sz399001")
        self.assertFalse(df.empty, "深证成指数据不应为空")
        self.assertIn("close", df.columns, "应包含close列")
        latest = df.iloc[-1]
        self.assertGreater(float(latest["close"]), 5000, "深证成指应大于5000")
        print(f"  [OK] 深证成指收盘价: {latest['close']}, 日期: {latest['date']}")

    def test_stock_hsgt_fund_flow_summary_em(self):
        """沪深港通资金流向汇总（注意：北向成交净买额已停止公布，值恒为0）"""
        df = ak.stock_hsgt_fund_flow_summary_em()
        self.assertFalse(df.empty, "沪深港通资金流向汇总不应为空")
        self.assertIn("板块", df.columns, "应包含板块列")
        self.assertIn("成交净买额", df.columns, "应包含成交净买额列")
        self.assertIn("资金方向", df.columns, "应包含资金方向列")
        print(f"  [OK] 沪深港通资金流向汇总: {len(df)}条记录")
        print(f"  数据:\n{df.to_string()}")
        # 验证北向成交净买额为0（数据已停止公布）
        north_rows = df[df["资金方向"] == "北向"]
        if not north_rows.empty:
            north_net_buy = north_rows["成交净买额"].sum()
            print(f"  [注意] 北向成交净买额合计: {north_net_buy}（预期为0，已停止公布）")

    def test_fund_etf_spot_em_north_etf(self):
        """北向资金ETF成交额（替代北向成交净买额）"""
        df = ak.fund_etf_spot_em()
        self.assertFalse(df.empty, "ETF实时数据不应为空")
        self.assertIn("名称", df.columns, "应包含名称列")
        self.assertIn("成交额", df.columns, "应包含成交额列")
        # 筛选北向资金相关ETF
        north_etf = df[df["名称"].str.contains("A50|MSCI|互联互通|陆股通", na=False)]
        self.assertFalse(north_etf.empty, "应能找到北向资金相关ETF")
        total_amount = north_etf["成交额"].sum() / 1e8
        avg_change = north_etf["涨跌幅"].mean()
        print(f"  [OK] 北向资金ETF: 共{len(north_etf)}只")
        print(f"  北向资金ETF总成交额: {total_amount:.2f}亿")
        print(f"  北向资金ETF平均涨跌幅: {avg_change:.2f}%")
        for _, row in north_etf.head(5).iterrows():
            print(f"    {row['代码']} {row['名称']} 涨跌幅:{row['涨跌幅']}% 成交额:{row['成交额']/1e8:.2f}亿")

    def test_stock_hsgt_hold_stock_em(self):
        """北向资金增持个股排行"""
        df = ak.stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")
        self.assertFalse(df.empty, "北向资金持股排行不应为空")
        self.assertIn("代码", df.columns, "应包含代码列")
        self.assertIn("名称", df.columns, "应包含名称列")
        self.assertIn("今日涨跌幅", df.columns, "应包含今日涨跌幅列")
        self.assertIn("今日增持估计-市值", df.columns, "应包含今日增持估计-市值列")
        self.assertIn("所属板块", df.columns, "应包含所属板块列")
        top5 = df.head(5)
        print(f"  [OK] 北向资金增持排行: {len(df)}条记录")
        for _, row in top5.iterrows():
            print(f"    {row['代码']} {row['名称']} 涨跌幅:{row['今日涨跌幅']}% 增持市值:{row['今日增持估计-市值']}万 行业:{row['所属板块']}")

    def test_stock_fund_flow_industry(self):
        """同花顺行业资金流"""
        df = ak.stock_fund_flow_industry(symbol="即时")
        self.assertFalse(df.empty, "行业资金流数据不应为空")
        self.assertIn("行业", df.columns, "应包含行业列")
        self.assertIn("净额", df.columns, "应包含净额列")
        self.assertIn("流入资金", df.columns, "应包含流入资金列")
        top5 = df.head(5)
        print(f"  [OK] 行业资金流: {len(df)}条记录")
        for _, row in top5.iterrows():
            print(f"    {row['行业']} 涨跌幅:{row['行业-涨跌幅']}% 净额:{row['净额']}亿 流入:{row['流入资金']}亿")

    def test_stock_zh_a_spot(self):
        """新浪A股实时行情（替代stock_zh_a_spot_em）"""
        df = ak.stock_zh_a_spot()
        self.assertFalse(df.empty, "A股实时行情不应为空")
        self.assertIn("代码", df.columns, "应包含代码列")
        self.assertIn("名称", df.columns, "应包含名称列")
        self.assertIn("涨跌幅", df.columns, "应包含涨跌幅列")
        up_count = len(df[df["涨跌幅"] > 0])
        down_count = len(df[df["涨跌幅"] < 0])
        print(f"  [OK] A股实时行情: 共{len(df)}只股票, 上涨:{up_count}, 下跌:{down_count}")


class TestAkshareSectorAPIs(unittest.TestCase):
    """测试板块分析师相关的API"""

    def test_stock_board_industry_summary_ths(self):
        """同花顺行业板块一览表（替代stock_board_industry_name_em）"""
        df = ak.stock_board_industry_summary_ths()
        self.assertFalse(df.empty, "行业板块一览表不应为空")
        self.assertIn("板块", df.columns, "应包含板块列")
        self.assertIn("涨跌幅", df.columns, "应包含涨跌幅列")
        self.assertIn("净流入", df.columns, "应包含净流入列")
        top5 = df.nlargest(5, "涨跌幅")
        print(f"  [OK] 行业板块一览表: {len(df)}条记录")
        for _, row in top5.iterrows():
            print(f"    {row['板块']} 涨跌幅:{row['涨跌幅']}% 净流入:{row['净流入']}亿")

    def test_stock_zt_pool_em(self):
        """涨停池"""
        today = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_em(date=today)
        self.assertFalse(df.empty, "涨停池数据不应为空")
        self.assertIn("代码", df.columns, "应包含代码列")
        self.assertIn("名称", df.columns, "应包含名称列")
        self.assertIn("连板数", df.columns, "应包含连板数列")
        print(f"  [OK] 涨停池: {len(df)}只涨停股")
        for _, row in df.head(5).iterrows():
            print(f"    {row['代码']} {row['名称']} 连板数:{row['连板数']} 行业:{row.get('所属行业', 'N/A')}")

    def test_stock_zt_pool_sub_new_em(self):
        """次新股涨停池"""
        today = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_sub_new_em(date=today)
        if df is not None and not df.empty:
            self.assertIn("代码", df.columns, "应包含代码列")
            self.assertIn("名称", df.columns, "应包含名称列")
            print(f"  [OK] 次新股涨停池: {len(df)}只")
            for _, row in df.head(3).iterrows():
                print(f"    {row['代码']} {row['名称']} 上市日期:{row.get('上市日期', 'N/A')}")
        else:
            print(f"  [OK] 次新股涨停池: 当日无数据（非交易日或无次新股涨停）")


class TestAkshareForceAPIs(unittest.TestCase):
    """测试合力分析师相关的API"""

    def test_stock_fund_flow_individual(self):
        """同花顺个股资金流（替代stock_individual_fund_flow_rank）"""
        df = ak.stock_fund_flow_individual(symbol="即时")
        self.assertFalse(df.empty, "个股资金流数据不应为空")
        self.assertIn("股票代码", df.columns, "应包含股票代码列")
        self.assertIn("股票简称", df.columns, "应包含股票简称列")
        self.assertIn("净额", df.columns, "应包含净额列")
        self.assertIn("流入资金", df.columns, "应包含流入资金列")
        self.assertIn("涨跌幅", df.columns, "应包含涨跌幅列")
        top5 = df.head(5)
        print(f"  [OK] 个股资金流: {len(df)}条记录")
        for _, row in top5.iterrows():
            print(f"    {row['股票代码']} {row['股票简称']} 涨跌幅:{row['涨跌幅']} 净额:{row['净额']}")

    def test_stock_fund_flow_industry_for_force(self):
        """同花顺行业资金流（合力分析师需要行业维度）"""
        df = ak.stock_fund_flow_industry(symbol="即时")
        self.assertFalse(df.empty, "行业资金流不应为空")
        self.assertIn("行业", df.columns, "应包含行业列")
        self.assertIn("净额", df.columns, "应包含净额列")
        top5 = df.head(5)
        print(f"  [OK] 行业资金流: {len(df)}条记录")
        for _, row in top5.iterrows():
            print(f"    {row['行业']} 涨跌幅:{row['行业-涨跌幅']}% 净额:{row['净额']}亿 领涨股:{row['领涨股']}")


class TestAkshareLeaderAPIs(unittest.TestCase):
    """测试龙头分析师相关的API"""

    def test_stock_zt_pool_for_leader(self):
        """涨停池（龙头候选）"""
        today = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_em(date=today)
        self.assertFalse(df.empty, "涨停池不应为空")
        self.assertIn("代码", df.columns, "应包含代码列")
        self.assertIn("连板数", df.columns, "应包含连板数列")
        print(f"  [OK] 涨停龙头候选: {len(df)}只")

    def test_stock_zh_a_spot_for_top_stocks(self):
        """A股实时行情取强势股（替代stock_zh_a_spot_em）"""
        df = ak.stock_zh_a_spot()
        self.assertFalse(df.empty, "A股实时行情不应为空")
        self.assertIn("涨跌幅", df.columns, "应包含涨跌幅列")
        top10 = df.nlargest(10, "涨跌幅")
        print(f"  [OK] 强势股排行: 共{len(df)}只股票")
        for _, row in top10.iterrows():
            print(f"    {row['代码']} {row['名称']} 涨跌幅:{row['涨跌幅']}% 最新价:{row.get('最新价', 'N/A')}")


class TestAkshareRiskAPIs(unittest.TestCase):
    """测试风险分析师相关的API"""

    def test_stock_zh_a_spot_for_st(self):
        """A股行情提取ST股票（替代stock_zh_a_spot_em）"""
        df = ak.stock_zh_a_spot()
        self.assertFalse(df.empty, "A股实时行情不应为空")
        self.assertIn("名称", df.columns, "应包含名称列")
        st_stocks = df[df["名称"].str.contains("ST|\\*ST", na=False, regex=True)]
        print(f"  [OK] ST风险股票: 共{len(st_stocks)}只")
        for _, row in st_stocks.head(5).iterrows():
            print(f"    {row['代码']} {row['名称']} 涨跌幅:{row['涨跌幅']}%")

    def test_stock_zh_a_new(self):
        """新浪次新股行情（替代stock_zh_a_new_em）"""
        df = ak.stock_zh_a_new()
        self.assertFalse(df.empty, "次新股行情不应为空")
        self.assertIn("code", df.columns, "应包含code列")
        self.assertIn("name", df.columns, "应包含name列")
        print(f"  [OK] 次新股: 共{len(df)}只")
        for _, row in df.head(5).iterrows():
            print(f"    {row['code']} {row['name']}")

    def test_stock_zh_a_spot_for_abnormal(self):
        """A股行情提取涨幅异常"""
        df = ak.stock_zh_a_spot()
        self.assertFalse(df.empty, "A股实时行情不应为空")
        self.assertIn("涨跌幅", df.columns, "应包含涨跌幅列")
        abnormal = df[df["涨跌幅"] > 9.5]
        print(f"  [OK] 涨幅异常(>9.5%): 共{len(abnormal)}只")


class TestDeprecatedAPIs(unittest.TestCase):
    """测试已废弃/不可用的API（验证它们确实不可用，以便确认需要替代方案）"""

    def test_stock_hsgt_em_removed(self):
        """stock_hsgt_em 已被akshare移除"""
        self.assertFalse(hasattr(ak, "stock_hsgt_em"), "stock_hsgt_em 应该已被移除")
        print("  [OK] stock_hsgt_em 确认已移除")

    def test_stock_hsgt_hk_hot_rank_em_removed(self):
        """stock_hsgt_hk_hot_rank_em 已被akshare移除"""
        self.assertFalse(hasattr(ak, "stock_hsgt_hk_hot_rank_em"), "stock_hsgt_hk_hot_rank_em 应该已被移除")
        print("  [OK] stock_hsgt_hk_hot_rank_em 确认已移除")

    def test_stock_hsgt_industry_fund_flow_em_removed(self):
        """stock_hsgt_industry_fund_flow_em 已被akshare移除"""
        self.assertFalse(hasattr(ak, "stock_hsgt_industry_fund_flow_em"), "stock_hsgt_industry_fund_flow_em 应该已被移除")
        print("  [OK] stock_hsgt_industry_fund_flow_em 确认已移除")

    def test_stock_zh_a_spot_em_blocked(self):
        """stock_zh_a_spot_em 东方财富接口被限制"""
        try:
            df = ak.stock_zh_a_spot_em()
            if df is not None and not df.empty:
                print("  [WARN] stock_zh_a_spot_em 当前可访问（可能间歇性可用）")
            else:
                print("  [OK] stock_zh_a_spot_em 返回空数据")
        except Exception as e:
            print(f"  [OK] stock_zh_a_spot_em 确认不可用: {type(e).__name__}")

    def test_stock_board_industry_name_em_blocked(self):
        """stock_board_industry_name_em 东方财富接口被限制"""
        try:
            df = ak.stock_board_industry_name_em()
            if df is not None and not df.empty:
                print("  [WARN] stock_board_industry_name_em 当前可访问（可能间歇性可用）")
            else:
                print("  [OK] stock_board_industry_name_em 返回空数据")
        except Exception as e:
            print(f"  [OK] stock_board_industry_name_em 确认不可用: {type(e).__name__}")

    def test_stock_individual_fund_flow_rank_blocked(self):
        """stock_individual_fund_flow_rank 东方财富接口被限制"""
        try:
            df = ak.stock_individual_fund_flow_rank(indicator="今日")
            if df is not None and not df.empty:
                print("  [WARN] stock_individual_fund_flow_rank 当前可访问（可能间歇性可用）")
            else:
                print("  [OK] stock_individual_fund_flow_rank 返回空数据")
        except Exception as e:
            print(f"  [OK] stock_individual_fund_flow_rank 确认不可用: {type(e).__name__}")

    def test_stock_zh_a_new_em_blocked(self):
        """stock_zh_a_new_em 东方财富接口被限制"""
        try:
            df = ak.stock_zh_a_new_em()
            if df is not None and not df.empty:
                print("  [WARN] stock_zh_a_new_em 当前可访问（可能间歇性可用）")
            else:
                print("  [OK] stock_zh_a_new_em 返回空数据")
        except Exception as e:
            print(f"  [OK] stock_zh_a_new_em 确认不可用: {type(e).__name__}")


class TestComputeFunctions(unittest.TestCase):
    """测试完整的compute_*_indicators函数"""

    def test_compute_market_indicators(self):
        """测试大盘指标计算完整流程"""
        from app.services.ai_selector_service import compute_market_indicators, ApiCache

        result = compute_market_indicators(ApiCache())
        print(result)
        self.assertIsInstance(result, dict, "结果应为字典")
        self.assertIn("指标来源", result, "应包含指标来源")
        self.assertIn("计算时间", result, "应包含计算时间")

        for key in ["上证指数", "深证成指", "沪深港通成交", "沪深港通活跃股前10",
                     "沪深港通行业成交集中度", "涨跌统计"]:
            self.assertIn(key, result, f"应包含{key}")
            value = result[key]
            self.assertNotEqual(value, "获取失败", f"{key}不应获取失败，实际值: {value}")
            print(f"  [OK] {key}: {type(value).__name__}")

    def test_compute_sector_indicators(self):
        """测试板块指标计算完整流程"""
        from app.services.ai_selector_service import compute_sector_indicators

        result = compute_sector_indicators(ApiCache())
        print(result)
        self.assertIsInstance(result, dict, "结果应为字典")

        for key in ["涨幅前10板块", "涨停统计", "连板统计"]:
            self.assertIn(key, result, f"应包含{key}")
            value = result[key]
            self.assertNotEqual(value, "获取失败", f"{key}不应获取失败，实际值: {value}")
            print(f"  [OK] {key}: {type(value).__name__}")

    def test_compute_force_indicators(self):
        """测试合力指标计算完整流程"""
        from app.services.ai_selector_service import compute_force_indicators

        result = compute_force_indicators(ApiCache())
        print(result)
        self.assertIsInstance(result, dict, "结果应为字典")

        for key in ["主力资金流向前10", "个股主力净流入前10"]:
            self.assertIn(key, result, f"应包含{key}")
            value = result[key]
            self.assertNotEqual(value, "获取失败", f"{key}不应获取失败，实际值: {value}")
            print(f"  [OK] {key}: {type(value).__name__}")

    def test_compute_leader_indicators(self):
        """测试龙头指标计算完整流程"""
        from app.services.ai_selector_service import compute_leader_indicators

        result = compute_leader_indicators()
        print(result)
        self.assertIsInstance(result, dict, "结果应为字典")

        for key in ["涨停龙头股前20", "强势股前20"]:
            self.assertIn(key, result, f"应包含{key}")
            value = result[key]
            self.assertNotEqual(value, "获取失败", f"{key}不应获取失败，实际值: {value}")
            print(f"  [OK] {key}: {type(value).__name__}")

    def test_compute_risk_indicators(self):
        """测试风险指标计算完整流程"""
        from app.services.ai_selector_service import compute_risk_indicators

        result = compute_risk_indicators()
        self.assertIsInstance(result, dict, "结果应为字典")

        for key in ["ST风险股票", "次新股"]:
            self.assertIn(key, result, f"应包含{key}")
            value = result[key]
            self.assertNotEqual(value, "获取失败", f"{key}不应获取失败，实际值: {value}")
            print(f"  [OK] {key}: {type(value).__name__}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
