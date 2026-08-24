# 因子有效性测评报告（2024 / 2025 / 2026）

生成时间: 2026-08-23 19:40

## 测评配置

| 项 | 值 |
|---|---|
| 主报告口径 | cond_p5（预测周期 5 日，条件宇宙） |
| 条件宇宙 | Top3 板块 ∩ 主板非 ST（sector 层全市场） |
| 极性取反 | f34 多窗口 / f30 / f36 |
| 分层数 | 5 |
| 因子字段数 | 50（sector 15 + dragon 20 + force 15） |

## 测评区间

- **2024**: 2024-01-01 ~ 2024-12-31（`output/eval_batch_20260823_160640/2024`）
- **2025**: 2025-01-02 ~ 2025-12-31（`output/eval_batch_20260823_160640/2025`）
- **2026**: 2026-01-01 ~ 2026-07-27（`output/eval_2026_YTD_fixed（turnover_rate 修复后重算）`）

## 各年评级统计

| 年份 | A | B | C | D | N/A | 合计 |
|------|---|---|---|---|---|------|
| 2024 | 1 | 12 | 20 | 16 | 1 | 50 |
| 2025 | 0 | 9 | 14 | 27 | 0 | 50 |
| 2026 | 0 | 1 | 7 | 42 | 0 | 50 |

## 各年 A/B 级因子

### 2024（13 个）

| 因子 | 得分 | 评级 | Rank IC Mean | Rank ICIR | N |
|------|------|------|--------------|-----------|---|
| dragon.f33_consecutive_boards | 83 | A | -0.1015 | -0.696 | 242 |
| sector.f30_sector_concentration | 66 | B | -0.0534 | -0.650 | 237 |
| dragon.f31_excess_return | 65 | B | -0.0533 | -0.269 | 242 |
| dragon.f31_excess_return_10d | 65 | B | -0.0534 | -0.269 | 242 |
| dragon.f31_excess_return_5d | 65 | B | -0.0533 | -0.269 | 242 |
| dragon.f31_excess_return_15d | 65 | B | -0.0584 | -0.286 | 242 |
| dragon.f31_excess_return_20d | 65 | B | -0.0798 | -0.375 | 242 |
| dragon.f32_amount | 65 | B | -0.0874 | -0.389 | 242 |
| dragon.f35_bollinger_trend | 65 | B | -0.0702 | -0.320 | 242 |
| dragon.f36_identity_premium | 65 | B | 0.0879 | 0.443 | 242 |
| dragon.f37_relative_strength | 65 | B | -0.0528 | -0.266 | 242 |
| force.fcoop1_main_net_ratio | 65 | B | -0.0355 | -0.300 | 242 |
| force.f_mean_reversion_signal | 65 | B | -0.0620 | -0.327 | 242 |

### 2025（9 个）

| 因子 | 得分 | 评级 | Rank IC Mean | Rank ICIR | N |
|------|------|------|--------------|-----------|---|
| dragon.f33_consecutive_boards | 73 | B | -0.0757 | -0.575 | 242 |
| sector.f30_sector_concentration | 66 | B | 0.0447 | 0.658 | 237 |
| force.f_mean_reversion_signal | 66 | B | -0.0748 | -0.533 | 242 |
| dragon.f38_turnover_anomaly | 66 | B | -0.0631 | -0.511 | 242 |
| dragon.f31_excess_return | 65 | B | -0.0648 | -0.432 | 242 |
| dragon.f31_excess_return_10d | 65 | B | -0.0686 | -0.483 | 242 |
| dragon.f31_excess_return_5d | 65 | B | -0.0648 | -0.432 | 242 |
| dragon.f37_relative_strength | 65 | B | -0.0624 | -0.420 | 242 |
| dragon.f36_identity_premium | 65 | B | 0.0543 | 0.304 | 242 |

### 2026（1 个）

| 因子 | 得分 | 评级 | Rank IC Mean | Rank ICIR | N |
|------|------|------|--------------|-----------|---|
| force.dragon_consistency_5d | 65 | B | 0.0560 | 0.378 | 105 |

## 三年均为 B 级及以上

无因子在 2024/2025/2026 三年同时达到 B+。

## 2024 或 2025 为 B+ 但 2026 未达 B（风格切换）

| 因子 | 2024 | 2025 | 2026 |
|------|------|------|------|
| dragon.f33_consecutive_boards | 83/A | 73/B | 38/D |
| sector.f30_sector_concentration | 66/B | 66/B | 40/C |
| force.f_mean_reversion_signal | 65/B | 66/B | 8/D |
| dragon.f31_excess_return | 65/B | 65/B | 0/D |
| dragon.f31_excess_return_10d | 65/B | 65/B | 8/D |
| dragon.f31_excess_return_5d | 65/B | 65/B | 0/D |
| dragon.f36_identity_premium | 65/B | 65/B | 31/D |
| dragon.f37_relative_strength | 65/B | 65/B | 0/D |
| force.fcoop1_main_net_ratio | 65/B | 58/C | 15/D |
| dragon.f31_excess_return_15d | 65/B | 55/C | 0/D |
| dragon.f31_excess_return_20d | 65/B | 48/C | 0/D |
| dragon.f35_bollinger_trend | 65/B | 48/C | 8/D |
| dragon.f32_amount | 65/B | 46/C | 8/D |
| dragon.f38_turnover_anomaly | 0/N/A | 66/B | 23/D |

## 全因子三年对比（50 字段）

### SECTOR 层

| 因子 | 2024 得分/评级 | 2025 得分/评级 | 2026 得分/评级 | 2024 RankIC | 2025 RankIC | 2026 RankIC |
|------|----------------|----------------|----------------|-------------|-------------|-------------|
| f21_rps_10d | 31/D | 0/D | 8/D | -0.0587 | -0.0147 | 0.0229 |
| f21_rps_20d | 55/C | 16/D | 8/D | -0.0720 | -0.0204 | 0.0004 |
| f21_rps_60d | 55/C | 24/D | 0/D | -0.0903 | -0.0387 | -0.0290 |
| f22_main_flow | 41/C | 48/C | 23/D | -0.0429 | -0.0621 | 0.0232 |
| f23_limit_up_density | 58/C | 48/C | 41/C | -0.0375 | -0.0239 | -0.0212 |
| f24_max_consecutive | 58/C | 58/C | 48/C | -0.0427 | -0.0190 | -0.0179 |
| f25_volume_slope_10d | 8/D | 8/D | 8/D | -0.0274 | -0.0214 | 0.0053 |
| f25_volume_slope_3d | 8/D | 8/D | 16/D | -0.0305 | -0.0333 | -0.0036 |
| f25_volume_slope_5d | 8/D | 8/D | 8/D | -0.0293 | -0.0185 | 0.0023 |
| f26_volume_growth_20d | 48/C | 0/D | 8/D | -0.0498 | -0.0078 | -0.0329 |
| f26_volume_growth_5d | 8/D | 0/D | 16/D | -0.0289 | 0.0038 | -0.0121 |
| f27_new_high_ratio | 48/C | 31/D | 0/D | -0.0587 | -0.0168 | 0.0069 |
| f28_consistency | 38/D | 0/D | 15/D | 0.0246 | -0.0192 | -0.0391 |
| f29_sector_breadth | 0/D | 24/D | 8/D | -0.0157 | -0.0269 | 0.0123 |
| f30_sector_concentration | 66/B | 66/B | 40/C | -0.0534 | 0.0447 | 0.0429 |

### DRAGON 层

| 因子 | 2024 得分/评级 | 2025 得分/评级 | 2026 得分/评级 | 2024 RankIC | 2025 RankIC | 2026 RankIC |
|------|----------------|----------------|----------------|-------------|-------------|-------------|
| f31_excess_return | 65/B | 65/B | 0/D | -0.0533 | -0.0648 | -0.0193 |
| f31_excess_return_10d | 65/B | 65/B | 8/D | -0.0534 | -0.0686 | -0.0117 |
| f31_excess_return_15d | 65/B | 55/C | 0/D | -0.0584 | -0.0677 | -0.0225 |
| f31_excess_return_20d | 65/B | 48/C | 0/D | -0.0798 | -0.0697 | -0.0259 |
| f31_excess_return_5d | 65/B | 65/B | 0/D | -0.0533 | -0.0648 | -0.0193 |
| f31b_rps_percentile | 41/C | 48/C | 8/D | -0.0506 | -0.0637 | -0.0191 |
| f32_amount | 65/B | 46/C | 8/D | -0.0874 | -0.0958 | -0.0589 |
| f33_consecutive_boards | 83/A | 73/B | 38/D | -0.1015 | -0.0757 | -0.0387 |
| f34_resonance_pct | 58/C | 8/D | 48/C | -0.0600 | -0.0200 | -0.0511 |
| f34_resonance_pct_10d | 58/C | 8/D | 31/D | 0.0634 | 0.0258 | 0.0536 |
| f34_resonance_pct_3d | 58/C | 8/D | 48/C | 0.0564 | 0.0187 | 0.0499 |
| f34_resonance_pct_5d | 58/C | 8/D | 48/C | 0.0600 | 0.0200 | 0.0511 |
| f35_bollinger_pass | 58/C | 30/D | 8/D | -0.0529 | -0.0420 | -0.0023 |
| f35_bollinger_trend | 65/B | 48/C | 8/D | -0.0702 | -0.0748 | -0.0294 |
| f36_identity_premium | 65/B | 65/B | 31/D | 0.0879 | 0.0543 | 0.0626 |
| f37_relative_strength | 65/B | 65/B | 0/D | -0.0528 | -0.0624 | -0.0176 |
| f38_turnover_anomaly | 0/N/A | 66/B | 23/D | - | -0.0631 | -0.0241 |
| f39_pb | 48/C | 40/C | 23/D | -0.0639 | -0.0314 | -0.0476 |
| f40_holder_change | 8/D | 8/D | 48/C | -0.0106 | -0.0199 | -0.0170 |

### FORCE 层

| 因子 | 2024 得分/评级 | 2025 得分/评级 | 2026 得分/评级 | 2024 RankIC | 2025 RankIC | 2026 RankIC |
|------|----------------|----------------|----------------|-------------|-------------|-------------|
| dragon_consistency_5d | 25/D | 41/C | 65/B | 0.0094 | 0.0375 | 0.0560 |
| f_main_force_persistence | 8/D | 16/D | 0/D | -0.0009 | -0.0053 | 0.0008 |
| f_mean_reversion_signal | 65/B | 66/B | 8/D | -0.0620 | -0.0748 | -0.0290 |
| f_power_divergence | 0/D | 8/D | 15/D | -0.0029 | -0.0181 | -0.0151 |
| fcoop1_main_net_ratio | 65/B | 58/C | 15/D | -0.0355 | -0.0373 | -0.0169 |
| fcoop2_main_retail_ratio | 48/C | 30/D | 8/D | -0.0288 | -0.0288 | -0.0145 |
| fcoop3_sustained_days | 58/C | 40/C | 0/D | -0.0255 | -0.0308 | 0.0019 |
| fcoop3_sustained_days_10d | 58/C | 16/D | 8/D | -0.0201 | -0.0316 | 0.0070 |
| fcoop3_sustained_days_3d | 30/D | 23/D | 8/D | -0.0245 | -0.0211 | -0.0113 |
| fcoop3_sustained_days_5d | 58/C | 40/C | 0/D | -0.0255 | -0.0308 | 0.0019 |
| fcoop4_turnover_quality | 0/D | 31/D | 31/D | - | -0.0723 | -0.0715 |
| fcoop5_main_flow_acceleration | 48/C | 8/D | 0/D | -0.0207 | -0.0225 | -0.0092 |
| fcoop6_main_force_aggression | 8/D | 15/D | 15/D | -0.0303 | -0.0381 | -0.0135 |
| fcoop7_super_large_net_ratio | 58/C | 58/C | 8/D | -0.0230 | -0.0167 | -0.0118 |
| fcoop8_main_flow_trend_5d | 8/D | 0/D | 0/D | -0.0217 | -0.0143 | -0.0120 |
| longhu_board_bonus | 0/D | 0/D | 0/D | - | - | - |

## 数据质量说明

| 因子 | 2024 | 2025 | 2026 | 说明 |
|------|------|------|------|------|
| dragon.f38_turnover_anomaly | N/A | B(66) | D(23) | 2024 缺 turnover_rate；2026 已修复 |
| force.fcoop4_turnover_quality | D(N=0) | D(31) | D(31) | 2024 常数 IC；2026 已修复 |
| force.longhu_board_bonus | D(N=0) | D(N=0) | D(N=0) | 需 QMT 环境 sync_lhb |

## 多口径对照（对齐 factor_ab_history）

数据目录: `E:\TradingAgents-CN\zstock\factor_management\script\因子测评\output\compare_eval_20260823_174008`

### 测评模式

| 模式 | period | 宇宙 |
|------|--------|------|
| cond_p5/p10/p20 | 5/10/20 | Top3板块∩主板（条件宇宙） |
| full_p5/p10/p20 | 5/10/20 | 全市场（--no-conditional） |

> 上文各节为 **cond_p5**（标准生产口径）；本节补充 period=10/20 与全市场对照。

### 历史 A 因子：各口径最高评级

| 因子 | 最优模式 | 年份 | 得分 | 评级 | Rank ICIR |
|------|----------|------|------|------|-----------|
| dragon.f33_consecutive_boards | cond_p10 | 2024 | 83 | A | -0.779 |
| dragon.f34_resonance_pct | cond_p20 | 2024 | 73 | B | -0.515 |
| dragon.f34_resonance_pct_10d | cond_p20 | 2024 | 73 | B | 0.507 |
| dragon.f34_resonance_pct_3d | full_p20 | 2024 | 66 | B | 0.613 |
| dragon.f34_resonance_pct_5d | cond_p20 | 2024 | 73 | B | 0.515 |
| dragon.f35_bollinger_trend | cond_p20 | 2024 | 83 | A | -0.593 |
| dragon.f36_identity_premium | cond_p20 | 2026_YTD | 73 | B | 0.589 |
| force.f_mean_reversion_signal | full_p20 | 2024 | 73 | B | -0.613 |

### 历史 A 因子完整矩阵（得分/评级）

#### dragon.f33_consecutive_boards

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 83/A | 73/B | 38/D |
| cond_p10 | 83/A | 73/B | 23/D |
| cond_p20 | 83/A | 73/B | 48/C |
| full_p5 | 66/B | 73/B | 8/D |
| full_p10 | 80/A | 73/B | 38/D |
| full_p20 | 80/A | 73/B | 38/D |

#### dragon.f36_identity_premium

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 65/B | 65/B | 31/D |
| cond_p10 | 65/B | 65/B | 55/C |
| cond_p20 | 65/B | 65/B | 73/B |
| full_p5 | 58/C | 58/C | 58/C |
| full_p10 | 58/C | 66/B | 66/B |
| full_p20 | 58/C | 66/B | 73/B |

#### dragon.f34_resonance_pct

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 58/C | 8/D | 48/C |
| cond_p10 | 66/B | 33/D | 8/D |
| cond_p20 | 73/B | 66/B | 48/C |
| full_p5 | 66/B | 48/C | 30/D |
| full_p10 | 66/B | 58/C | 41/C |
| full_p20 | 66/B | 58/C | 48/C |

#### dragon.f34_resonance_pct_3d

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 58/C | 8/D | 48/C |
| cond_p10 | 65/B | 30/D | 8/D |
| cond_p20 | 58/C | 48/C | 38/D |
| full_p5 | 58/C | 48/C | 23/D |
| full_p10 | 66/B | 48/C | 23/D |
| full_p20 | 66/B | 58/C | 41/C |

#### dragon.f34_resonance_pct_5d

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 58/C | 8/D | 48/C |
| cond_p10 | 66/B | 33/D | 8/D |
| cond_p20 | 73/B | 66/B | 48/C |
| full_p5 | 66/B | 48/C | 30/D |
| full_p10 | 66/B | 58/C | 41/C |
| full_p20 | 66/B | 58/C | 48/C |

#### dragon.f34_resonance_pct_10d

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 58/C | 8/D | 31/D |
| cond_p10 | 58/C | 48/C | 8/D |
| cond_p20 | 73/B | 66/B | 31/D |
| full_p5 | 66/B | 38/D | 31/D |
| full_p10 | 66/B | 58/C | 41/C |
| full_p20 | 66/B | 58/C | 48/C |

#### dragon.f35_bollinger_trend

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 65/B | 48/C | 8/D |
| cond_p10 | 65/B | 66/B | 0/D |
| cond_p20 | 83/A | 73/B | 8/D |
| full_p5 | 48/C | 49/C | 31/D |
| full_p10 | 65/B | 56/C | 15/D |
| full_p20 | 73/B | 49/C | 0/D |

#### force.f_mean_reversion_signal

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 65/B | 66/B | 8/D |
| cond_p10 | 65/B | 66/B | 0/D |
| cond_p20 | 73/B | 73/B | 8/D |
| full_p5 | 58/C | 66/B | 15/D |
| full_p10 | 58/C | 66/B | 8/D |
| full_p20 | 73/B | 66/B | 0/D |

### 各模式 A/B 因子数量（50 字段）

| 模式 | 年份 | A | B | C | D |
|------|------|---|---|---|---|
| cond_p10 | 2024 | 2 | 16 | 13 | 19 |
| cond_p10 | 2025 | 0 | 13 | 11 | 26 |
| cond_p10 | 2026_YTD | 0 | 2 | 4 | 44 |
| cond_p20 | 2024 | 5 | 16 | 15 | 14 |
| cond_p20 | 2025 | 0 | 20 | 12 | 18 |
| cond_p20 | 2026_YTD | 0 | 4 | 4 | 42 |
| cond_p5 | 2024 | 1 | 12 | 20 | 17 |
| cond_p5 | 2025 | 0 | 9 | 14 | 27 |
| cond_p5 | 2026_YTD | 0 | 1 | 6 | 43 |
| full_p10 | 2024 | 1 | 7 | 18 | 24 |
| full_p10 | 2025 | 0 | 13 | 18 | 19 |
| full_p10 | 2026_YTD | 0 | 3 | 11 | 36 |
| full_p20 | 2024 | 1 | 13 | 16 | 20 |
| full_p20 | 2025 | 0 | 15 | 21 | 14 |
| full_p20 | 2026_YTD | 0 | 3 | 9 | 38 |
| full_p5 | 2024 | 0 | 5 | 22 | 23 |
| full_p5 | 2025 | 0 | 9 | 18 | 23 |
| full_p5 | 2026_YTD | 0 | 1 | 8 | 41 |

### 对照说明

- 历史 `factor_ab_history.md` 的 A 多来自 **period=10/20** 或 **全市场** 口径，与 cond_p5 不可直接比。
- 标准 A 线：Total_Score ≥ 80；66~73 分为 B。
- 2026 区间截止 2026-07-27（因子/L2 数据交集）。

## 结论摘要

1. **2024（cond_p5）**：`dragon.f33_consecutive_boards` 唯一 A(83)；f31 系列、f32、f36、f37、f_mean_reversion 等多因子 B 级。
2. **2025（cond_p5）**：`f33` B(73)、`f38` B(66)、`f_mean_reversion_signal` B(66)；龙头因子仍有效。
3. **2026 YTD（cond_p5）**：仅 `force.dragon_consistency_5d` B(65)；多数历史强势龙头因子降至 C/D。
4. **板块层** `f30_sector_concentration` 三年 C~B 边缘，是相对稳定的 sector 因子。
5. **多口径对照**：2024 年 `f33` 在 cond/full × p5/p10/p20 均可达 A；`f35_bollinger_trend` 仅在 **cond_p20** 达 A(83)，cond_p5 仅 B(65)——与 `factor_ab_history` 混用 period 口径一致。
6. **历史 A 复现**：`f34` 系列、`f_mean_reversion` 在 p20/全市场最高 B(73)；2026 年仅 `f36_identity_premium` 在 cond_p20/full_p20 仍 B(73)。
