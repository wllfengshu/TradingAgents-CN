"""
主线板块层因子计算模块（M2）

黑盒设计：
- 公开接口：calculate_all_sector_factors()
- 所有实现细节都隐藏在私有方法中
- 调用方只需提供原始数据，获得成品得分

入参说明：
  - sectors: 板块列表，每个元素包含 sector_code 字段
  - sector_stocks: Dict[sector_code] → List[stock_code]，板块成分股映射
  - sector_ohlcv: Dict[sector_code] → pd.DataFrame，板块K线数据（包含close列）
  - sector_capital_flow: Dict[sector_code] → {'main_flow': float, 'retail_flow': float, 'total_volume': float}，板块聚合资金流

计算流程（内部自动处理）：
  1. 收集所有板块的原始因子数据
     F2.1: 板块RPS（20日收益率）
     F2.2: 板块资金净流入
     F2.3: 涨停浓度（成分股中涨停的比例）
     F2.4: 连板高度（最高连板天数）
     F2.5: 成交占比斜率（成交额占全市的变化趋势）
  2. 对5个因子分别进行 cross-sectional rank
  3. 将所有因子转换为 0-100 分数
  4. 等权平均合成 M2 得分

出参说明：
  Dict[sector_code] → float
  - 键：板块代码
  - 值：M2 综合得分（0-100，值越大表示板块越强）

使用示例：
  m2_scores = SectorFactors.calculate_all_sector_factors(
      sectors, sector_stocks, sector_ohlcv, sector_capital_flow
  )
  # m2_scores = {'SC001': 75.2, 'SC002': 82.5, ...}
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ===================== 板块因子权重 =====================
# 等权为默认基线，可按需调整（如看好资金流可上调 _W_F22）
_W_F21 = 0.30   # F2.1 板块RPS（20日收益率），动量核心
_W_F22 = 0.20   # F2.2 板块资金净流入
_W_F23 = 0.30   # F2.3 涨停浓度
_W_F24 = 0.10   # F2.4 连板高度
_W_F25 = 0.10   # F2.5 成交占比斜率

class SectorFactors:
    """板块层因子计算器。黑盒设计，所有实现隐藏，只暴露一个统一入口"""

    # ===================== 公开接口（唯一入口） =====================

    @staticmethod
    def calculate_all_sector_factors(
        sectors: List[Dict],
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        stock_flow_recent: Optional[Dict[str, List[Dict]]] = None,
    ) -> Dict[str, float]:
        """
        【公开接口】完整计算M2所有板块因子，返回0-100的板块综合得分

        接受原始个股数据，内部自动完成：
        1. 从 stock_ohlcv 聚合板块 OHLCV（等权收益率 + 量额求和）
        2. 从 stock_flow_recent 聚合板块资金流（成分股当日主力净流入求和）
        3. 从 stock_ohlcv 计算涨停标志与连板天数（日涨幅 ≥ 9.5%）
        4. 计算5个因子原始值 → min-max 归一化 → 等权合成 M2

        返回：Dict[sector_code] → float (0-100)
        """
        # 内部聚合板块 OHLCV + 资金流
        sector_ohlcv, sector_capital_flow = SectorFactors._aggregate_sectors_from_stocks(
            sector_stocks, stock_ohlcv, stock_flow_recent or {},
        )

        # 从 OHLCV 计算涨停标志与连板天数
        all_stocks_limit_up = SectorFactors._compute_limit_up_from_ohlcv(stock_ohlcv)
        all_stocks_consecutive_boards = SectorFactors._compute_consecutive_boards_from_ohlcv(stock_ohlcv)

        # 收集所有原始因子数据
        valid_sector_codes = {s['sector_code'] for s in sectors if s.get('sector_code') is not None}
        sector_rps_raw = SectorFactors._collect_sector_rps(sector_ohlcv, sector_codes=valid_sector_codes)
        sector_volume_slope_raw = SectorFactors._collect_volume_ratio_slope(sector_ohlcv, sector_codes=valid_sector_codes)

        ohlcv_valid_codes = set(sector_rps_raw.keys()) & set(sector_volume_slope_raw.keys())
        valid_sectors_filtered = [s for s in sectors if s.get('sector_code') in ohlcv_valid_codes]

        sector_capital_flow_raw = SectorFactors._collect_sector_capital_flow(
            {k: v for k, v in sector_capital_flow.items() if k in ohlcv_valid_codes}
        )
        sector_limit_up_densities_raw = SectorFactors._collect_limit_up_densities(
            valid_sectors_filtered, sector_stocks, all_stocks_limit_up
        )
        sector_consecutive_boards_raw = SectorFactors._collect_consecutive_boards_max(
            valid_sectors_filtered, sector_stocks, all_stocks_consecutive_boards
        )

        rps_ranked = SectorFactors._minmax_normalize(sector_rps_raw)
        capital_flow_ranked = SectorFactors._minmax_normalize(sector_capital_flow_raw)
        limit_up_ranked = SectorFactors._minmax_normalize(sector_limit_up_densities_raw)
        consecutive_boards_ranked = SectorFactors._minmax_normalize(sector_consecutive_boards_raw)
        volume_slope_ranked = SectorFactors._minmax_normalize(sector_volume_slope_raw)

        m2_scores = SectorFactors._combine_five_factors(
            rps_ranked, capital_flow_ranked, limit_up_ranked,
            consecutive_boards_ranked, volume_slope_ranked,
        )

        logger.info(f"✅ M2 完整计算完成: {len(m2_scores)} 个板块，平均分 {np.mean(list(m2_scores.values())) if m2_scores else 0:.2f}")
        return m2_scores

    # ===================== 私有方法（实现细节，对外隐藏）=====================

    @staticmethod
    def _collect_sector_rps(sector_ohlcv: Dict[str, pd.DataFrame], window: int = 20, sector_codes: set = None) -> Dict[str, float]:
        """【私有】收集板块RPS原始值。F2.1  板块RPS的意思：Relative Price Strength，相对价格强度，用于衡量板块的相对表现"""
        sector_rps_scores = {}
        for sector_code, df in sector_ohlcv.items():
            if sector_codes is not None and sector_code not in sector_codes:
                continue
            if len(df) >= window + 1:
                base = float(df['close'].iloc[-window - 1])
                last = float(df['close'].iloc[-1])
                if base <= 0 or np.isnan(base) or np.isnan(last):
                    continue  # 避免除以 0 或负价或 NaN
                cumulative_return = last / base - 1
                sector_rps_scores[sector_code] = cumulative_return
        return sector_rps_scores

    @staticmethod
    def _collect_sector_capital_flow(sector_capital_flow: Dict[str, Dict[str, float]]) -> Dict[str, float]:
        """【私有】收集板块资金流原始值。F2.2，只取主力净流入"""
        sector_flow_scores = {}
        for sector_code, flow_info in sector_capital_flow.items():
            sector_flow_scores[sector_code] = float(flow_info.get('main_flow', 0.0))
        return sector_flow_scores

    # ---------- 从 OHLCV 衍生涨停 / 连板 ----------

    _LIMIT_UP_THRESHOLD = 0.095  # 日涨幅 ≥ 9.5% 视为涨停（兼容四舍五入；与 DragonFactors 一致）

    @staticmethod
    def _compute_limit_up_from_ohlcv(stock_ohlcv: Dict[str, pd.DataFrame]) -> Dict[str, bool]:
        """【私有】从 OHLCV 计算当日涨停标志。F2.3 使用"""
        result = {}
        for code, df in stock_ohlcv.items():
            if len(df) < 2 or 'close' not in df.columns:
                result[code] = False
                continue
            closes = df['close'].astype(float).values
            prev_close = closes[-2]
            if prev_close <= 0:
                result[code] = False
                continue
            daily_return = closes[-1] / prev_close - 1.0
            result[code] = daily_return >= SectorFactors._LIMIT_UP_THRESHOLD
        return result

    @staticmethod
    def _compute_consecutive_boards_from_ohlcv(stock_ohlcv: Dict[str, pd.DataFrame]) -> Dict[str, int]:
        """【私有】从 OHLCV 计算最近连续涨停天数（从最新交易日往回数）。F2.4 使用"""
        result = {}
        for code, df in stock_ohlcv.items():
            if len(df) < 2 or 'close' not in df.columns:
                continue
            closes = df['close'].astype(float).values
            count = 0
            for i in range(len(closes) - 1, 0, -1):
                prev = closes[i - 1]
                if prev <= 0:
                    break
                daily_return = closes[i] / prev - 1.0
                if daily_return >= SectorFactors._LIMIT_UP_THRESHOLD:
                    count += 1
                else:
                    break
            result[code] = count
        return result

    # ---------- 板块 OHLCV / 资金流聚合（从个股 → 板块）----------

    @staticmethod
    def _aggregate_sectors_from_stocks(
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        stock_flow_recent: Dict[str, List[Dict]],
    ) -> tuple:
        """
        【私有】从个股 OHLCV + 资金流聚合出板块层 OHLCV 和板块层资金流。

        聚合策略：
        - close: 等权收益率聚合（避免高价股主导）
        - volume/amount: 直接求和
        - 资金流: 成分股当日主力净流入求和

        返回: (sector_ohlcv, sector_capital_flow)
        """
        sector_ohlcv: Dict[str, pd.DataFrame] = {}
        sector_capital_flow: Dict[str, Dict[str, float]] = {}

        for sector_code, codes in sector_stocks.items():
            members = [c for c in codes if c in stock_ohlcv]
            if not members:
                continue

            # ── 板块 OHLCV ──
            frames = []
            for c in members:
                df = stock_ohlcv[c]
                if 'trade_date' not in df.columns or 'close' not in df.columns:
                    continue
                tmp = df[['trade_date', 'close', 'volume', 'amount']].copy()
                tmp['trade_date'] = pd.to_datetime(tmp['trade_date'], errors='coerce')
                frames.append(tmp)
            if not frames:
                continue

            cat = pd.concat(frames, ignore_index=True)
            vol_amt_agg = cat.groupby('trade_date', as_index=False).agg({
                'volume': 'sum', 'amount': 'sum',
            }).sort_values('trade_date').reset_index(drop=True)

            # 等权收益率聚合 close
            ret_frames = []
            for c in members:
                df = stock_ohlcv[c]
                if 'trade_date' not in df.columns or 'close' not in df.columns:
                    continue
                tmp = df[['trade_date', 'close']].copy().sort_values('trade_date')
                tmp['trade_date'] = pd.to_datetime(tmp['trade_date'], errors='coerce')
                tmp['ret'] = tmp['close'].astype(float).pct_change()
                ret_frames.append(tmp[['trade_date', 'ret']])

            if not ret_frames:
                continue
            ret_cat = pd.concat(ret_frames, ignore_index=True)
            sector_ret = ret_cat.groupby('trade_date', as_index=False)['ret'].mean()
            sector_ret = sector_ret.sort_values('trade_date').reset_index(drop=True)
            sector_ret['ret'] = sector_ret['ret'].fillna(0.0)
            sector_ret['close'] = 100.0 * (1.0 + sector_ret['ret']).cumprod()

            sector_df = sector_ret[['trade_date', 'close', 'ret']].merge(
                vol_amt_agg, on='trade_date', how='inner'
            )
            # 估算 open/high/low：避免全部设为 close 导致波动率指标失真
            ret_abs = sector_df['ret'].abs().fillna(0)
            sector_df['open'] = sector_df['close'].shift(1).fillna(sector_df['close'])
            sector_df['high'] = sector_df[['close', 'open']].max(axis=1) * (1 + ret_abs * 0.5 + 0.001)
            sector_df['low'] = sector_df[['close', 'open']].min(axis=1) / (1 + ret_abs * 0.5 + 0.001)
            sector_df = sector_df.drop(columns=['ret'])
            sector_ohlcv[sector_code] = sector_df.reset_index(drop=True)

            # ── 板块资金流：成分股当日 main_inflow / turnover_amount 求和 ──
            main_sum = 0.0
            total_amt = 0.0
            for c in members:
                if c not in stock_flow_recent or not stock_flow_recent[c]:
                    continue
                today_doc = stock_flow_recent[c][-1]  # 升序排列，最后一条为当日
                try:
                    # xtquant L2：main_net 净额(万元)、turnover 成交额(元)，净额 ×10000 统一到元
                    main_sum += float(today_doc.get('main_net', 0.0) or 0.0) * 10000.0
                    total_amt += float(today_doc.get('turnover', 0.0) or 0.0)
                except (ValueError, TypeError):
                    pass
            sector_capital_flow[sector_code] = {
                'main_flow': main_sum,
                'retail_flow': 0.0,
                'total_amount': total_amt,
            }

        return sector_ohlcv, sector_capital_flow

    @staticmethod
    def _collect_limit_up_densities(sectors: List[Dict], sector_stocks: Dict[str, List[str]], all_stocks_limit_up: Dict[str, bool]) -> Dict[str, float]:
        """【私有】收集涨停浓度原始值（0-1）。F2.3"""
        sector_limit_up_densities = {}
        for sector in sectors:
            sector_code = sector.get('sector_code')
            if sector_code is None:
                continue
            stocks_in_sector = sector_stocks.get(sector_code, [])
            if stocks_in_sector:
                limit_ups = [all_stocks_limit_up.get(s, False) for s in stocks_in_sector]
                density = sum(limit_ups) / len(limit_ups)
                sector_limit_up_densities[sector_code] = density
        return sector_limit_up_densities

    @staticmethod
    def _collect_consecutive_boards_max(sectors: List[Dict], sector_stocks: Dict[str, List[str]], all_stocks_consecutive_boards: Dict[str, int]) -> Dict[str, int]:
        """【私有】收集连板高度原始值（0-N天）。F2.4"""
        sector_consecutive_boards = {}
        for sector in sectors:
            sector_code = sector.get('sector_code')
            if sector_code is None:
                continue
            stocks_in_sector = sector_stocks.get(sector_code, [])
            if stocks_in_sector:
                consecutive_counts = [all_stocks_consecutive_boards.get(s, 0) for s in stocks_in_sector]
                max_boards = max(consecutive_counts) if consecutive_counts else 0
                sector_consecutive_boards[sector_code] = max_boards
        return sector_consecutive_boards

    @staticmethod
    def _collect_volume_ratio_slope(sector_ohlcv: Dict[str, pd.DataFrame], ma_window: int = 5, sector_codes: set = None) -> Dict[str, float]:
        """
        【私有】收集板块成交占比5日MA斜率。F2.5

        核心逻辑：
        公式：(板块成交/全板块成交总和) 的 5日MA，取一阶线性回归斜率。
        衡量该板块成交额占比的变化趋势——斜率为正说明市场资金正在向该板块集中。

        数据说明：
        板块 OHLCV 数据由个股聚合而来，是真实数据。但不同板块间的成分股存在重叠
        （一只股票可能同时属于多个概念板块），因此所有板块 volume 之和 > 真实全市场成交量。
        这里的 total_vol 是"参与评价的全部板块成交之和"，用作归一化分母：
        我们关注的是单板块在所有候选板块中的成交占比变化趋势（相对排名），
        而非绝对占比数值，因此重叠不影响板块间的横向比较有效性。

        对齐策略：
        只保留 K 线长度 >= ma_window+1 的板块参与计算，避免数据不足的冷门板块
        拖累其他板块的可用长度。对齐时取满足条件的板块中的最短长度作为公共窗口。
        """
        if not sector_ohlcv:
            return {}

        # 只保留 volume 列存在且长度充足的板块
        vol_series: Dict[str, pd.Series] = {}
        for code, df in sector_ohlcv.items():
            if 'volume' in df.columns and len(df) >= ma_window + 1:
                vol_series[code] = df['volume']

        if not vol_series:
            return {}

        # 先过滤只保留 sector_codes 中的板块，避免无关板块稀释 total_vol
        if sector_codes is not None:
            vol_series = {code: s for code, s in vol_series.items() if code in sector_codes}
        if not vol_series:
            return {}

        # 取满足条件的板块中的最短长度作为公共对齐窗口
        min_len = min(len(s) for s in vol_series.values())

        # 全板块成交总和（用作归一化分母，衡量相对占比变化趋势）
        aligned_vols = {code: s.iloc[-min_len:].values.astype(float) for code, s in vol_series.items()}
        total_vol = np.zeros(min_len, dtype=float)
        for arr in aligned_vols.values():
            # 将 NaN 视为 0 参与求和，避免单个板块缺失数据污染全局
            total_vol += np.nan_to_num(arr, nan=0.0)

        result = {}
        for code, arr in aligned_vols.items():
            if sector_codes is not None and code not in sector_codes:
                continue
            # 计算占比序列，分母为0时置为 NaN（避免除零）
            with np.errstate(divide='ignore', invalid='ignore'):
                ratio = np.where(total_vol > 0, arr / total_vol, np.nan)
            ratio_series = pd.Series(ratio)
            ma = ratio_series.rolling(window=ma_window, min_periods=ma_window).mean().dropna()
            if len(ma) < 2:
                result[code] = float('nan')
                continue
            # 一阶线性回归斜率：正值 = 占比上升趋势，资金正在向该板块集中
            x = np.arange(len(ma), dtype=float)
            slope = float(np.polyfit(x, ma.values, 1)[0])
            result[code] = slope

        return result

    @staticmethod
    def _minmax_normalize(values_dict: Dict[str, float]) -> Dict[str, float]:
        """
        【私有】min-max归一化转0-100

        原始值范围任意，转换为 0-100：
        - 最大值 → 100
        - 最小值 → 0
        - 中间值 → 线性插值
        """
        if not values_dict:
            return {}
        min_val = min(values_dict.values())
        max_val = max(values_dict.values())
        if max_val == min_val:
            return {k: 50.0 for k in values_dict.keys()}
        return {k: 100 * (v - min_val) / (max_val - min_val) for k, v in values_dict.items()}

    @staticmethod
    def _combine_five_factors(rps_scores: Dict[str, float], capital_flow_scores: Dict[str, float], limit_up_scores: Dict[str, float], consecutive_boards_scores: Dict[str, float], volume_slope_scores: Dict[str, float]) -> Dict[str, float]:
        """
        【私有】加权合成5个因子为M2得分

        权重定义在类常量 _W_F21 ~ _W_F25，默认等权各 0.20。

        逻辑：
        1. 只合成同时拥有 F2.1(RPS) 和 F2.5(volume_slope) 的板块——两者都依赖
           OHLCV，缺失说明该板块数据不足，不应进入选板块环节
        2. F2.2/F2.3/F2.4 缺失时该因子不参与加权（按实际可用因子归一化权重）
        3. M2 = Σ(score_i × weight_i) / Σ(weight_i)（仅对可用因子）
        """
        # 必须同时拥有 F2.1 和 F2.5（都依赖 OHLCV，缺失 = 数据不足）
        valid_sectors = set(rps_scores.keys()) & set(volume_slope_scores.keys())

        if not valid_sectors:
            logger.error("⚠️ M2 合成失败：没有同时具备 F2.1+F2.5 的板块（OHLCV 数据不足）")
            return {}

        result = {}
        for sector_code in valid_sectors:
            missing = [name for name, d in [
                ('F2.2_capital_flow', capital_flow_scores),
                ('F2.3_limit_up', limit_up_scores),
                ('F2.4_consecutive_boards', consecutive_boards_scores),
            ] if sector_code not in d]
            if missing:
                logger.warning(f"⚠️ 板块 {sector_code} 缺少因子 {missing}，按实际可用因子加权平均")

            # 按实际可用因子动态加权，缺失因子不参与分母
            scores = [rps_scores[sector_code], volume_slope_scores[sector_code]]
            weights = [SectorFactors._W_F21, SectorFactors._W_F25]
            if sector_code in capital_flow_scores:
                scores.append(capital_flow_scores[sector_code]); weights.append(SectorFactors._W_F22)
            if sector_code in limit_up_scores:
                scores.append(limit_up_scores[sector_code]); weights.append(SectorFactors._W_F23)
            if sector_code in consecutive_boards_scores:
                scores.append(consecutive_boards_scores[sector_code]); weights.append(SectorFactors._W_F24)

            score = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
            result[sector_code] = score

        return result
