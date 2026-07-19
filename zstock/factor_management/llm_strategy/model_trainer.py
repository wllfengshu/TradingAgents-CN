"""
模型训练模块

支持多种机器学习模型（LightGBM、Linear、MLP）的训练、优化和版本管理。

核心流程：
1. 特征准备：组织特征和标签
2. 滚动训练：使用历史数据进行动态训练
3. 超参数优化：贝叶斯搜索寻找最优参数
4. 模型评估：计算 IC、Sharpe 等指标
5. 模型保存：持久化模型和元数据
"""

import logging
import pickle
import json
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
import joblib
import lightgbm as lgb
from sklearn.linear_model import LinearRegression, Ridge, Lasso
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler
from scipy.stats import spearmanr
import asyncio

from zstock.data_management import DatabaseService

logger = logging.getLogger(__name__)


class ModelTrainer:
    """
    模型训练器

    职责：
    - 支持多种模型类型（LightGBM、Linear、MLP）
    - 实现滚动训练策略
    - 执行超参数优化
    - 计算模型性能指标
    - 管理模型版本

    属性：
        models: 训练的模型字典
        model_metadata: 模型元数据
        training_history: 训练历史
    """

    # 支持的模型类型
    SUPPORTED_MODELS = ['lightgbm', 'linear', 'mlp']
    # 模型集合名称
    MODEL_COLLECTION = 'zstock_models'

    def __init__(self):
        """
        初始化模型训练器
        """
        # 训练的模型
        self.models = {}  # {model_name: model_object}

        # 模型元数据
        self.model_metadata = {}

        # 训练历史
        self.training_history = []

        # 获取数据库服务（异步）
        self.database_service = None

        logger.info("✅ ModelTrainer 初始化完成")

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

    def prepare_training_data(self,
                             factors_df: pd.DataFrame,
                             market_data: pd.DataFrame,
                             lookforward_period: int = 5) -> Tuple[np.ndarray, np.ndarray]:
        """
        准备训练数据

        特征：因子 DataFrame
        标签：未来 N 个交易日的收益率

        模型的目标是预测未来收益，所以标签是向前看的收益率：
        label = (close_t+N / close_t) - 1

        Args:
            factors_df: 因子 DataFrame（特征矩阵）
            market_data: 市场数据（用于构造标签）
            lookforward_period: 预测期限（天），默认 5 天

        Returns:
            Tuple[np.ndarray, np.ndarray]: (特征矩阵 X, 标签数组 y)

        示例：
            如果今天是 2024-01-01，lookforward_period=5
            那么标签是 2024-01-08 的收益率
        """
        logger.info(f"📊 准备训练数据（预测期限：{lookforward_period}天）")

        if factors_df.empty or market_data.empty:
            logger.error("❌ 输入数据为空")
            return np.array([]), np.array([])

        try:
            # 特征矩阵：因子值
            X = factors_df.values

            # 构造标签：未来 lookforward_period 日收益率
            # 按股票分组，避免跨股票偏移
            if 'code' in market_data.columns:
                group_col = 'code'
            elif 'stock_code' in market_data.columns:
                group_col = 'stock_code'
            elif isinstance(market_data.index, pd.MultiIndex):
                # MultiIndex: 假设第一层是股票代码
                market_data = market_data.copy()
                market_data['_stock_code'] = market_data.index.get_level_values(0)
                group_col = '_stock_code'
            else:
                group_col = None

            if group_col:
                future_close = market_data.groupby(group_col)['close'].shift(-lookforward_period)
                current_close = market_data['close']
                y = np.where(
                    current_close > 0,
                    (future_close.fillna(current_close) / current_close) - 1,
                    0
                )
            else:
                # 单股票回退：使用位置偏移
                future_close = market_data['close'].shift(-lookforward_period)
                current_close = market_data['close']
                y = np.where(
                    current_close > 0,
                    (future_close.fillna(current_close) / current_close) - 1,
                    0
                )

            y = np.array(y, dtype=float)

            logger.info(f"✅ 训练数据准备完成: X.shape={X.shape}, y.shape={y.shape}")

            return X, y

        except Exception as e:
            logger.error(f"❌ 训练数据准备失败: {e}")
            return np.array([]), np.array([])

    def train_lightgbm(self,
                      X: np.ndarray,
                      y: np.ndarray,
                      hyperparams: Optional[Dict] = None,
                      early_stopping_rounds: int = 50) -> Any:
        """
        训练 LightGBM 模型

        LightGBM 是一个快速、分布式的梯度提升框架，特别适合：
        - 大数据集
        - 高维特征
        - 需要快速训练的场景

        主要参数：
        - num_leaves: 树的叶子数（默认 31，范围 4-100）
        - learning_rate: 学习率（默认 0.1，范围 0.01-1）
        - n_estimators: 树的数量（默认 100）
        - max_depth: 树的最大深度（用于控制模型复杂度）

        Args:
            X: 特征矩阵，形状 (样本数, 特征数)
            y: 标签数组
            hyperparams: 超参数字典
            early_stopping_rounds: 早停轮数（防止过拟合）

        Returns:
            Any: 训练好的模型对象
        """
        try:
            logger.info("🤖 开始训练 LightGBM 模型")

            # 默认超参数
            if hyperparams is None:
                hyperparams = {
                    'num_leaves': 31,
                    'learning_rate': 0.05,
                    'n_estimators': 100,
                    'objective': 'regression',
                    'metric': 'mse',
                    'verbose': -1,
                }

            # 分割训练集和验证集（80/20）
            split_idx = int(0.8 * len(X))
            X_train, X_val = X[:split_idx], X[split_idx:]
            y_train, y_val = y[:split_idx], y[split_idx:]

            # 创建 LightGBM 数据集
            train_data = lgb.Dataset(X_train, label=y_train)
            val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)

            # 训练模型
            model = lgb.train(
                hyperparams,
                train_data,
                valid_sets=[val_data],
                num_boost_round=hyperparams.get('n_estimators', 100),
                callbacks=[lgb.early_stopping(early_stopping_rounds)]
            )

            logger.info("✅ LightGBM 模型训练完成")

            return model

        except ImportError:
            logger.error("❌ 未安装 LightGBM，请运行: pip install lightgbm")
            return None
        except Exception as e:
            logger.error(f"❌ LightGBM 训练失败: {e}")
            return None

    def train_linear_model(self,
                          X: np.ndarray,
                          y: np.ndarray,
                          model_type: str = 'ridge') -> Any:
        """
        训练线性模型

        线性模型包括：
        - OLS (Ordinary Least Squares): 最小二乘法
        - Ridge: 岭回归（L2 正则化，防止过拟合）
        - Lasso: 套索回归（L1 正则化，进行特征选择）

        Args:
            X: 特征矩阵
            y: 标签数组
            model_type: 模型类型（ols / ridge / lasso）

        Returns:
            Any: 训练好的模型对象
        """
        try:
            logger.info(f"🤖 开始训练 {model_type.upper()} 模型")

            # 分割训练集和验证集
            split_idx = int(0.8 * len(X))
            X_train = X[:split_idx]
            y_train = y[:split_idx]

            # 选择模型
            if model_type == 'ols':
                model = LinearRegression()
            elif model_type == 'ridge':
                model = Ridge(alpha=1.0)  # 正则化参数
            elif model_type == 'lasso':
                model = Lasso(alpha=0.01)  # 正则化参数
            else:
                logger.error(f"❌ 未知的线性模型类型: {model_type}")
                return None

            # 训练
            model.fit(X_train, y_train)

            # 计算性能指标
            train_score = model.score(X_train, y_train)  # R²分数
            logger.info(f"✅ {model_type.upper()} 模型训练完成，R²={train_score:.4f}")

            return model

        except ImportError:
            logger.error("❌ 未安装 scikit-learn，请运行: pip install scikit-learn")
            return None
        except Exception as e:
            logger.error(f"❌ 线性模型训练失败: {e}")
            return None

    def train_mlp(self,
                 X: np.ndarray,
                 y: np.ndarray,
                 hidden_layers: Optional[List[int]] = None,
                 epochs: int = 100) -> Any:
        """
        训练 MLP（多层感知机）神经网络

        MLP 是一种前馈神经网络，可以学习非线性关系。

        网络结构：
        输入层 → 隐藏层1 → 隐藏层2 → ... → 输出层

        Args:
            X: 特征矩阵
            y: 标签数组
            hidden_layers: 隐藏层神经元数列表，例如 [64, 32]
            epochs: 训练轮数

        Returns:
            Any: 训练好的模型对象
        """
        try:
            logger.info("🤖 开始训练 MLP 模型")

            if hidden_layers is None:
                hidden_layers = (64, 32)

            # 分割训练集
            split_idx = int(0.8 * len(X))
            X_train = X[:split_idx]
            y_train = y[:split_idx]

            # 特征标准化（神经网络需要）
            scaler = StandardScaler()
            X_train_scaled = scaler.fit_transform(X_train)

            # 创建模型
            model = MLPRegressor(
                hidden_layer_sizes=hidden_layers,
                max_iter=epochs,
                learning_rate='adaptive',
                learning_rate_init=0.001,
                random_state=42,
            )

            # 训练
            model.fit(X_train_scaled, y_train)

            # 计算性能指标
            train_score = model.score(X_train_scaled, y_train)
            logger.info(f"✅ MLP 模型训练完成，R²={train_score:.4f}")

            # 返回包含 scaler 的模型对象
            return {'model': model, 'scaler': scaler}

        except ImportError:
            logger.error("❌ 未安装 scikit-learn，请运行: pip install scikit-learn")
            return None
        except Exception as e:
            logger.error(f"❌ MLP 训练失败: {e}")
            return None

    def rolling_train(self,
                     factors_df: pd.DataFrame,
                     market_data: pd.DataFrame,
                     model_type: str = 'lightgbm',
                     train_window: int = 252,
                     val_window: int = 63,
                     retraining_interval: int = 21) -> Dict:
        """
        滚动训练

        滚动训练是时间序列建模的关键技术。
        它模拟了实盘中不断有新数据加入的情况。

        训练窗口设置：
        - 训练窗口：过去 252 个交易日（约 1 年）
        - 验证窗口：未来 63 个交易日（约 3 个月）
        - 再向前 63 个交易日作为测试集

        每 retraining_interval 天重新训练一次模型。

        Args:
            factors_df: 因子 DataFrame
            market_data: 市场数据
            model_type: 模型类型
            train_window: 训练窗口大小（交易日数）
            val_window: 验证窗口大小
            retraining_interval: 重新训练间隔（天）

        Returns:
            Dict: 滚动训练结果
        """
        logger.info(f"🔄 开始滚动训练（模型：{model_type}）")

        if factors_df.empty or market_data.empty:
            logger.error("❌ 输入数据为空")
            return {}

        results = {
            'models': [],
            'performances': [],
            'timestamps': [],
        }

        # 获取时间序列
        if isinstance(factors_df.index, pd.MultiIndex):
            dates = factors_df.index.get_level_values(0).unique()
        else:
            dates = factors_df.index.unique()

        # 滚动训练
        for i in range(train_window + val_window, len(dates), retraining_interval):
            # 确定窗口
            train_end_idx = i - val_window
            train_start_idx = max(0, train_end_idx - train_window)

            train_dates = dates[train_start_idx:train_end_idx]
            val_dates = dates[train_end_idx:min(train_end_idx + val_window, len(dates))]

            # 提取训练数据
            if isinstance(factors_df.index, pd.MultiIndex):
                X_train = factors_df.loc[train_dates].values
                train_data = market_data.loc[train_dates]
                y_train = train_data.groupby(level=0)['close'].pct_change().shift(-1).dropna()
                # Align X_train with y_train
                common_idx = y_train.index
                X_train = factors_df.loc[common_idx].values
                y_train = y_train.values
            else:
                X_train = factors_df.iloc[train_start_idx:train_end_idx].values
                close_train = market_data.iloc[train_start_idx:train_end_idx]['close']
                y_train = close_train.pct_change().shift(-1).fillna(0).values

            # 训练模型
            if model_type == 'lightgbm':
                model = self.train_lightgbm(X_train, y_train)
            elif model_type == 'linear':
                model = self.train_linear_model(X_train, y_train)
            elif model_type == 'mlp':
                model = self.train_mlp(X_train, y_train)
            else:
                logger.error(f"❌ 未知模型类型: {model_type}")
                continue

            if model is None:
                continue

            # 验证集性能评估
            if isinstance(factors_df.index, pd.MultiIndex):
                X_val = factors_df.loc[val_dates].values
                val_data = market_data.loc[val_dates]
                y_val = val_data.groupby(level=0)['close'].pct_change().shift(-1).dropna()
                common_idx = y_val.index
                X_val = factors_df.loc[common_idx].values
                y_val = y_val.values
            else:
                X_val = factors_df.iloc[train_end_idx:train_end_idx + val_window].values
                close_val = market_data.iloc[train_end_idx:train_end_idx + val_window]['close']
                y_val = close_val.pct_change().shift(-1).fillna(0).values

            # 计算预测性能
            try:
                y_pred = model.predict(X_val)
                mse = np.mean((y_pred - y_val) ** 2)
                r2 = 1 - (np.sum((y_val - y_pred) ** 2) / np.sum((y_val - np.mean(y_val)) ** 2))

                performance = {
                    'mse': float(mse),
                    'r2': float(r2),
                    'period': f"{train_dates[0]} ~ {val_dates[-1]}",
                }

                results['models'].append(model)
                results['performances'].append(performance)
                results['timestamps'].append(datetime.utcnow())

                logger.info(f"✅ 第 {len(results['models'])} 个模型训练完成，R²={r2:.4f}")

            except Exception as e:
                logger.error(f"⚠️ 验证失败: {e}")

        logger.info(f"✅ 滚动训练完成，共训练 {len(results['models'])} 个模型")

        return results

    def save_model_to_mongodb(self,
                                   model: Any,
                                   model_name: str,
                                   model_type: str,
                                   metadata: Optional[Dict] = None,
                                   performance: Optional[Dict] = None) -> bool:
        """
        保存模型到 MongoDB（同步方法）

        通过 DatabaseService 存储模型二进制和元数据

        Args:
            model: 模型对象
            model_name: 模型名称
            model_type: 模型类型
            metadata: 元数据
            performance: 性能指标

        Returns:
            bool: 保存是否成功
        """
        try:
            db_service = self._get_database_service()
            if not db_service:
                logger.warning("⚠️ 数据库服务不可用，跳过 MongoDB 存储")
                return False

            # 序列化模型
            model_bytes = joblib.dumps(model)
            file_size = len(model_bytes)

            # 获取版本号（同步调用）
            try:
                existing = db_service.query(
                    self.MODEL_COLLECTION,
                    {'model_name': model_name},
                    limit=1
                )
            except Exception:
                # 如果数据库操作失败（可能是初次保存），继续进行
                existing = None

            # 获取最高版本号
            max_version = 0
            if existing:
                try:
                    versions = db_service.query(
                        self.MODEL_COLLECTION,
                        {'model_name': model_name}
                    )
                    if versions:
                        max_version = max([v.get('version', 0) for v in versions])
                except Exception:
                    max_version = 0

            version = max_version + 1

            # 将之前的版本标记为非最新
            if version > 1:
                try:
                    db_service.update_many(
                        self.MODEL_COLLECTION,
                        {'model_name': model_name, 'version': {'$lt': version}},
                        {'is_latest': False}
                    )
                except Exception as e:
                    logger.warning(f"⚠️ 更新旧版本标记失败: {e}")

            # 构建文档
            doc = {
                'model_name': model_name,
                'model_type': model_type,
                'version': version,
                'created_at': datetime.utcnow(),
                'file_size_kb': file_size / 1024,
                'model_binary': model_bytes,
                'is_latest': True,
                'metadata': metadata or {},
                'performance': performance or {},
            }

            # 插入文档
            try:
                model_id = db_service.insert_one(self.MODEL_COLLECTION, doc)
                logger.info(f"✅ 模型已保存到 MongoDB (版本: {version}, ID: {model_id})")
                return True
            except Exception as e:
                logger.warning(f"⚠️ MongoDB 插入失败: {e}，将继续流程")
                return False

        except Exception as e:
            logger.error(f"❌ 保存模型失败: {e}")
            return False

    async def load_model_from_mongodb(self, model_name: str, version: int = -1) -> Optional[Any]:
        """
        异步从 MongoDB 加载模型

        Args:
            model_name: 模型名称
            version: 版本号（-1 表示最新版本）

        Returns:
            Any: 模型对象或 None
        """
        try:
            db_service = self._get_database_service()
            if not db_service:
                logger.error("⚠️ 数据库服务不可用")
                return None

            # 查询条件
            query = {'model_name': model_name}

            if version == -1:
                # 加载最新版本
                doc = await db_service.query_one(
                    self.MODEL_COLLECTION,
                    {**query, 'is_latest': True}
                )
                if not doc:
                    # 备用：直接查最高版本
                    docs = await db_service.query(self.MODEL_COLLECTION, query)
                    if docs:
                        doc = max(docs, key=lambda x: x.get('version', 0))
            else:
                # 加载指定版本
                doc = await db_service.query_one(self.MODEL_COLLECTION, {**query, 'version': version})

            if not doc:
                logger.error(f"⚠️ 模型未找到: {model_name}")
                return None

            # 反序列化
            logger.info(f"📂 从 MongoDB 加载模型 (版本: {doc.get('version', '?')})")
            model = joblib.loads(doc['model_binary'])
            self.models[model_name] = model

            return model

        except Exception as e:
            logger.error(f"❌ MongoDB 加载失败: {e}")
            return None

    async def get_model_versions(self, model_name: str) -> list:
        """
        获取模型所有版本信息

        Args:
            model_name: 模型名称

        Returns:
            list: 模型版本信息列表
        """
        try:
            db_service = self._get_database_service()
            if not db_service:
                logger.error("⚠️ 数据库服务不可用")
                return []

            versions = await db_service.query(
                self.MODEL_COLLECTION,
                {'model_name': model_name}
            )

            # 移除二进制数据，按版本倒序排列
            return sorted(
                [
                    {k: v for k, v in v.items() if k != 'model_binary'}
                    for v in versions
                ],
                key=lambda x: x.get('version', 0),
                reverse=True
            )

        except Exception as e:
            logger.error(f"❌ 获取版本失败: {e}")
            return []

    async def get_model_info(self, model_name: str, version: int = -1) -> Optional[Dict]:
        """
        获取模型信息（不含二进制）

        Args:
            model_name: 模型名称
            version: 版本号（-1 表示最新版本）

        Returns:
            Dict: 模型信息或 None
        """
        try:
            db_service = self._get_database_service()
            if not db_service:
                logger.error("⚠️ 数据库服务不可用")
                return None

            query = {'model_name': model_name}
            if version == -1:
                doc = await db_service.query_one(
                    self.MODEL_COLLECTION,
                    {**query, 'is_latest': True}
                )
            else:
                doc = await db_service.query_one(self.MODEL_COLLECTION, {**query, 'version': version})

            if doc:
                # 移除二进制数据
                doc.pop('model_binary', None)
                return doc

            return None

        except Exception as e:
            logger.error(f"❌ 获取信息失败: {e}")
            return None

    def evaluate_model(self, model: Any, X_test: np.ndarray, y_test: np.ndarray) -> Dict:
        """
        评估模型性能

        计算的指标：
        - MSE (Mean Squared Error): 平均平方误差
        - RMSE (Root Mean Squared Error): 均方根误差
        - MAE (Mean Absolute Error): 平均绝对误差
        - R² (Coefficient of Determination): 决定系数
        - IC (Information Coefficient): 信息系数（预测值与实际值的相关性）

        Args:
            model: 模型对象
            X_test: 测试特征
            y_test: 测试标签

        Returns:
            Dict: 性能指标字典
        """
        try:
            logger.info("📊 开始模型评估")

            # 预测
            y_pred = model.predict(X_test)

            # 计算指标
            mse = np.mean((y_pred - y_test) ** 2)
            rmse = np.sqrt(mse)
            mae = np.mean(np.abs(y_pred - y_test))

            # R²
            ss_res = np.sum((y_test - y_pred) ** 2)
            ss_tot = np.sum((y_test - np.mean(y_test)) ** 2)
            r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

            # IC（信息系数）
            ic, _ = spearmanr(y_pred, y_test)
            ic = float(ic) if not np.isnan(ic) else 0.0

            metrics = {
                'mse': float(mse),
                'rmse': float(rmse),
                'mae': float(mae),
                'r2': float(r2),
                'ic': ic,
                'evaluated_at': datetime.utcnow().isoformat(),
            }

            logger.info(f"✅ 模型评估完成:")
            logger.info(f"   - R² = {r2:.4f}")
            logger.info(f"   - RMSE = {rmse:.4f}")
            logger.info(f"   - IC = {ic:.4f}")

            return metrics

        except Exception as e:
            logger.error(f"❌ 模型评估失败: {e}")
            return {}
