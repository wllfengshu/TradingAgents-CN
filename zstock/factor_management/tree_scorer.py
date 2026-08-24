"""
决策树评分器（TreeScorer）

替代线性加权打分，用 DecisionTreeRegressor 捕捉因子非线性交互。
龙头层专用：f33_consecutive_boards + f34_resonance_pct_5d + f36_identity_premium

模型文件：zstock/common/models/dragon_tree_v1.pkl
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_PATH = Path(__file__).resolve().parent.parent / "common" / "models" / "dragon_tree_v1.pkl"


class TreeScorer:
    """决策树评分器。

    用法：
        scorer = TreeScorer()  # 默认加载 dragon_tree_v1.pkl
        scores = scorer.score(field_values={
            "f33_consecutive_boards": {"000001": 2, "000002": 0},
            "f34_resonance_pct_5d": {"000001": 0.8, "000002": 0.3},
            "f36_identity_premium": {"000001": 1.5, "000002": 2.0},
        })
        # scores = {"000001": 75.3, "000002": 22.1}
    """

    def __init__(self, model_path: Optional[str | Path] = None):
        import joblib

        path = Path(model_path) if model_path else _DEFAULT_MODEL_PATH
        if not path.exists():
            raise FileNotFoundError(f"决策树模型不存在: {path}")

        data = joblib.load(path)
        self.tree = data["tree"]
        self.feature_names: List[str] = data["feature_names"]
        self.trained_date: str = data.get("trained_date", "unknown")
        self.train_period: str = data.get("train_period", "unknown")
        self.leaf_stats: Dict[int, Dict] = data.get("leaf_stats", {})

        # 计算叶节点得分映射（用于 rank-based 映射到 [0, 100]）
        self._leaf_scores = self._compute_leaf_scores()
        logger.info(
            f"TreeScorer 加载: {path.name}, "
            f"trained={self.trained_date}, "
            f"features={self.feature_names}, "
            f"leaves={self.tree.get_n_leaves()}"
        )

    def _compute_leaf_scores(self) -> Dict[int, float]:
        """将叶节点的平均收益映射到 [0, 100] 分制（反转极性：高收益→高分）。"""
        if not self.leaf_stats:
            return {}

        leaf_ids = list(self.leaf_stats.keys())
        means = np.array([self.leaf_stats[lid]["mean_return"] for lid in leaf_ids])

        # min-max 归一化到 [0, 100]
        vmin, vmax = means.min(), means.max()
        rng = vmax - vmin if vmax > vmin else 1.0
        scores = {}
        for lid, mean in zip(leaf_ids, means):
            scores[lid] = float((mean - vmin) / rng * 100)

        return scores

    def score(self, field_values: Dict[str, Dict[str, float]]) -> Dict[str, float]:
        """从原始因子值打分。

        Args:
            field_values: {factor_name: {code: raw_value}}
                例如: {"f33_consecutive_boards": {"000001": 2, ...}, ...}

        Returns:
            {code: score} — score 在 [0, 100] 范围内
        """
        if not field_values or not any(field_values.values()):
            return {}

        # 找到所有有完整特征的 code
        codes = set.intersection(
            *[set(v.keys()) for v in field_values.values() if v]
        ) if any(field_values.values()) else set()

        if not codes:
            return {}

        codes_list = sorted(codes)
        n = len(codes_list)

        # 构建特征矩阵（顺序与 feature_names 一致）
        X = np.zeros((n, len(self.feature_names)), dtype=float)
        valid_mask = np.ones(n, dtype=bool)

        for j, fname in enumerate(self.feature_names):
            fv = field_values.get(fname, {})
            for i, code in enumerate(codes_list):
                val = fv.get(code)
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    valid_mask[i] = False
                else:
                    X[i, j] = float(val)

        if not valid_mask.any():
            return {}

        # 预测
        predictions = self.tree.predict(X)

        # 获取叶节点并映射到 [0, 100]
        leaf_ids = self.tree.apply(X)

        result = {}
        for i, code in enumerate(codes_list):
            if not valid_mask[i]:
                continue
            lid = int(leaf_ids[i])
            score = self._leaf_scores.get(lid, 50.0)
            result[code] = score

        return result

    def predict_raw(self, field_values: Dict[str, Dict[str, float]]) -> Dict[str, float]:
        """返回原始预测值（10d 前瞻收益预测），不做 [0, 100] 映射。

        用于分析/调试。
        """
        if not field_values or not any(field_values.values()):
            return {}

        codes = set.intersection(
            *[set(v.keys()) for v in field_values.values() if v]
        ) if any(field_values.values()) else set()

        if not codes:
            return {}

        codes_list = sorted(codes)
        n = len(codes_list)
        X = np.zeros((n, len(self.feature_names)), dtype=float)
        valid_mask = np.ones(n, dtype=bool)

        for j, fname in enumerate(self.feature_names):
            fv = field_values.get(fname, {})
            for i, code in enumerate(codes_list):
                val = fv.get(code)
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    valid_mask[i] = False
                else:
                    X[i, j] = float(val)

        if not valid_mask.any():
            return {}

        predictions = self.tree.predict(X)
        return {
            code: float(predictions[i])
            for i, code in enumerate(codes_list)
            if valid_mask[i]
        }


# 单例缓存
_instance: Optional[TreeScorer] = None


def get_tree_scorer(model_path: Optional[str | Path] = None) -> TreeScorer:
    """获取 TreeScorer 单例（懒加载）。"""
    global _instance
    if _instance is None or (model_path and str(model_path) != str(_DEFAULT_MODEL_PATH)):
        _instance = TreeScorer(model_path)
    return _instance
