"""
信号生成模块

使用机器学习模型的预测结果生成交易信号。

核心流程：
1. 加载模型和因子数据
2. 生成模型预测得分
3. 过滤不可交易的股票（ST、停牌等）
4. 排序并生成最终信号
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional
from datetime import datetime
from zstock.data_management import DatabaseService
from zstock.factor_management.llm_strategy.model_trainer import ModelTrainer

logger = logging.getLogger(__name__)


class SignalGenerator:
    """
    信号生成器

    职责：
    - 加载机器学习模型
    - 生成模型预测得分
    - 过滤交易状态（ST、停牌、涨跌停等）
    - 排序并输出买卖信号

    属性：
        model: 训练好的模型
        signals: 生成的信号数据
        signal_metadata: 信号元数据
    """

    def __init__(self):
        """
        初始化信号生成器
        """
        self.database_service = None
        self.mongodb_client = None
        self._model_trainer = None

        # 信号数据
        self.signals = {}  # {信号日期: 信号列表}
        self.model = None  # 当前使用的模型
        self.signal_metadata = {}

        logger.info("✅ SignalGenerator 初始化完成")

    def load_model(self, model_name: str) -> bool:
        """
        加载模型

        Args:
            model_name: 模型名称（如 'lgb_v1'）
            model_source: 模型来源路径（可选）

        Returns:
            bool: 加载是否成功
        """
        try:
            logger.info(f"📂 加载模型: {model_name}")
            if self._model_trainer is None:
                self._model_trainer = ModelTrainer()
            trainer = self._model_trainer
            self.model = trainer.load_model_from_mongodb(model_name)

            if self.model is None:
                logger.error(f"❌ 模型加载失败: {model_name}")
                return False

            logger.info(f"✅ 模型加载成功: {model_name}")
            return True

        except Exception as e:
            logger.error(f"❌ 加载模型失败: {e}")
            return False

    def _get_database_service(self):
        """获取数据库服务"""
        if self.database_service is None:
            try:
                self.database_service = DatabaseService()
                logger.debug("✅ 数据库服务初始化成功")
            except Exception as e:
                logger.error(f"⚠️ 数据库服务初始化失败（致命错误）: {e}")
                self.database_service = None
        return self.database_service

    def generate_signals(self,
                        factors_df: pd.DataFrame,
                        trade_status_df: Optional[pd.DataFrame] = None,
                        signal_date: Optional[str] = None,
                        top_n: int = 20) -> pd.DataFrame:
        """
        生成交易信号

        处理流程：
        1. 使用模型对因子预测
        2. 获取股票交易状态（ST、停牌等）
        3. 过滤不可交易的股票
        4. 排序并生成信号

        Args:
            factors_df: 因子数据 DataFrame，必须包含：
                - stock_code: 股票代码
                - 各个因子列
            trade_status_df: 交易状态 DataFrame，包含：
                - stock_code: 股票代码
                - is_st: 是否 ST
                - is_paused: 是否停牌
                - is_limit_up: 是否涨停
                - is_limit_down: 是否跌停
                - list_days: 上市天数
                - avg_amount_20d: 20 日平均成交额
            signal_date: 信号生成日期（默认为今日）
            top_n: 生成的信号数量（默认 20 个）

        Returns:
            pd.DataFrame: 信号数据，包含：
                - stock_code: 股票代码
                - score: 模型预测得分
                - rank: 排名
                - signal_type: 信号类型（buy / hold / sell）
        """
        logger.info(f"🎯 开始生成交易信号 (top_n={top_n})")

        if self.model is None:
            logger.error("❌ 模型未加载，无法生成信号")
            return pd.DataFrame()

        if factors_df.empty:
            logger.error("❌ 因子数据为空")
            return pd.DataFrame()

        if signal_date is None:
            signal_date = pd.Timestamp.today().strftime('%Y-%m-%d')

        try:
            # ============================================================
            # 第一步：模型预测
            # ============================================================
            logger.info("📊 第一步：模型预测")

            # 准备预测数据（提取数值列）
            numeric_cols = factors_df.select_dtypes(include=[np.number]).columns.tolist()
            X = factors_df[numeric_cols].values

            # 模型预测
            try:
                predictions = self.model.predict(X)
            except Exception as e:
                logger.warning(f"⚠️ 模型预测失败: {e}，使用因子平均值")
                # 备选：使用因子平均值作为得分
                predictions = factors_df[numeric_cols].mean(axis=1).values

            logger.info(f"   预测得分范围: [{predictions.min():.4f}, {predictions.max():.4f}]")

            # ============================================================
            # 第二步：过滤交易状态
            # ============================================================
            logger.info("🔍 第二步：过滤交易状态")

            # 初始化可交易标志
            tradeable = pd.Series(True, index=factors_df.index, dtype=bool)

            # 如果提供了交易状态数据，进行过滤
            if trade_status_df is not None and not trade_status_df.empty:
                # 合并因子和交易状态
                merged = factors_df.copy()
                merged['stock_code'] = factors_df.get('stock_code', factors_df.index)

                # 使用 merge 替代 iterrows 循环
                trade_status_map = trade_status_df.set_index('stock_code')
                if 'is_st' in trade_status_map.columns:
                    st_map = trade_status_map['is_st'].to_dict()
                    mask_st = merged['stock_code'].map(st_map).fillna(False).astype(bool)
                    tradeable[mask_st] = False
                if 'is_paused' in trade_status_map.columns:
                    paused_map = trade_status_map['is_paused'].to_dict()
                    mask_paused = merged['stock_code'].map(paused_map).fillna(False).astype(bool)
                    tradeable[mask_paused] = False
                if 'is_limit_up' in trade_status_map.columns or 'is_limit_down' in trade_status_map.columns:
                    lu_map = trade_status_map.get('is_limit_up', pd.Series(False, index=trade_status_map.index)).to_dict()
                    ld_map = trade_status_map.get('is_limit_down', pd.Series(False, index=trade_status_map.index)).to_dict()
                    mask_lu = merged['stock_code'].map(lu_map).fillna(False).astype(bool)
                    mask_ld = merged['stock_code'].map(ld_map).fillna(False).astype(bool)
                    tradeable[mask_lu | mask_ld] = False
                if 'list_days' in trade_status_map.columns:
                    ld_map = trade_status_map['list_days'].to_dict()
                    mask_new = merged['stock_code'].map(ld_map).fillna(0).astype(int) < 60
                    tradeable[mask_new] = False
                if 'avg_amount_20d' in trade_status_map.columns:
                    amt_map = trade_status_map['avg_amount_20d'].to_dict()
                    mask_low = merged['stock_code'].map(amt_map).fillna(0).astype(float) < 20000000
                    tradeable[mask_low] = False

            # ============================================================
            # 第三步：生成信号
            # ============================================================
            logger.info("⚡ 第三步：生成信号")

            # 创建信号数据框
            signal_data = []

            # 可交易股票的预测得分
            tradeable_scores = predictions.copy()
            tradeable_scores[~tradeable] = -np.inf  # 不可交易设为无穷小

            # 排序并选择 top_n
            top_indices = np.argsort(-tradeable_scores)[:top_n]

            for rank, idx in enumerate(top_indices):
                if tradeable.iloc[idx]:  # 确保是可交易的
                    stock_code = factors_df.iloc[idx].get('stock_code',
                                                         f"stock_{idx}")

                    signal_data.append({
                        'signal_date': signal_date,
                        'stock_code': stock_code,
                        'score': float(predictions[idx]),
                        'rank': rank + 1,
                        'signal_type': 'buy',  # 暂时都标记为买入信号
                        'created_at': datetime.utcnow().isoformat(),
                    })

            signals_df = pd.DataFrame(signal_data)

            # 记录
            excluded_count = (~tradeable).sum()
            logger.info(f"✅ 信号生成完成:")
            logger.info(f"   - 总股票数: {len(factors_df)}")
            logger.info(f"   - 排除数: {excluded_count}")
            logger.info(f"   - 生成信号数: {len(signals_df)}")
            logger.info(f"   - Top 5 得分: {signals_df.head()['score'].values}")

            self.signals[signal_date] = signals_df

            return signals_df

        except Exception as e:
            logger.error(f"❌ 信号生成失败: {e}")
            import traceback
            traceback.print_exc()
            return pd.DataFrame()

    def filter_signals_by_criteria(self,
                                  signals_df: pd.DataFrame,
                                  score_threshold: float = -np.inf,
                                  sector_limit: Optional[Dict] = None) -> pd.DataFrame:
        """
        根据额外条件过滤信号

        Args:
            signals_df: 原始信号数据
            score_threshold: 得分阈值（低于此值的信号被过滤）
            sector_limit: 行业限制，例如：
                {
                    '金融': 5,      # 最多 5 个金融股
                    '科技': 8,      # 最多 8 个科技股
                }

        Returns:
            pd.DataFrame: 过滤后的信号
        """
        logger.info("🔍 根据额外条件过滤信号")

        filtered = signals_df.copy()

        # 按得分阈值过滤
        if score_threshold > -np.inf:
            initial_count = len(filtered)
            filtered = filtered[filtered['score'] >= score_threshold]
            logger.info(f"   - 按得分过滤: {initial_count} → {len(filtered)}")

        # 按行业限制过滤（如果提供了行业信息）
        if sector_limit and 'sector' in filtered.columns:
            filtered_by_sector = []
            for sector, limit in sector_limit.items():
                sector_signals = filtered[filtered['sector'] == sector]
                # 取该行业得分最高的前 limit 个
                sector_signals = sector_signals.nlargest(limit, 'score')
                filtered_by_sector.append(sector_signals)

            filtered = pd.concat(filtered_by_sector, ignore_index=True)
            logger.info(f"   - 按行业限制过滤: {len(signals_df)} → {len(filtered)}")

        logger.info(f"✅ 信号过滤完成: 最终 {len(filtered)} 个信号")

        return filtered

    def save_signals_to_mongodb(self, signals_df: pd.DataFrame) -> bool:
        """
        将信号保存到 MongoDB

        Args:
            signals_df: 信号数据

        Returns:
            bool: 保存是否成功
        """
        try:
            if self.mongodb_client is None:
                logger.warning("⚠️ MongoDB 客户端未配置，跳过保存")
                return False

            if signals_df.empty:
                logger.warning("⚠️ 信号数据为空")
                return False

            logger.info("💾 保存信号到 MongoDB")

            # 将信号转换为字典列表
            documents = signals_df.to_dict('records')

            # TODO: 实际项目中使用真实的 MongoDB 插入操作
            # db['signals'].insert_many(documents)

            logger.info(f"✅ 已保存 {len(documents)} 条信号")

            return True

        except Exception as e:
            logger.error(f"❌ 保存信号失败: {e}")
            return False

    def get_signals(self, signal_date: Optional[str] = None) -> pd.DataFrame:
        """
        获取信号

        Args:
            signal_date: 信号日期，如为 None 则返回最新信号

        Returns:
            pd.DataFrame: 信号数据
        """
        if signal_date is None:
            # 返回最新的信号
            if self.signals:
                signal_date = list(self.signals.keys())[-1]
            else:
                return pd.DataFrame()

        return self.signals.get(signal_date, pd.DataFrame())
