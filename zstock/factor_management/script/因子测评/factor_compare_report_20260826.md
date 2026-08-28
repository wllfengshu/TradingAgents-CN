# 因子多口径对照测评报告（对齐 factor_ab_history）

生成时间: 2026-08-26 22:54
数据目录: `C:\Users\wllfe\AppData\Local\Temp\pytest-of-wllfe\pytest-24\test_style_compare_and_report0\c2`

## 测评模式

| 模式 | period | 宇宙 |
|------|--------|------|
| cond_p5/p10/p20 | 5/10/20 | Top3板块∩主板（条件宇宙） |
| full_p5/p10/p20 | 5/10/20 | 全市场（--no-conditional） |

## 历史 A 因子：各口径最高评级

| 因子 | 最优模式 | 年份 | 得分 | 评级 | Rank ICIR |
|------|----------|------|------|------|-----------|
| dragon.f33_consecutive_boards | cond_p5 | 2024 | 83 | A | -0.696 |
| dragon.f34_resonance_pct | cond_p5 | 2024 | 58 | C | -0.479 |
| dragon.f34_resonance_pct_10d | cond_p5 | 2024 | 58 | C | 0.471 |
| dragon.f34_resonance_pct_3d | cond_p5 | 2024 | 58 | C | 0.434 |
| dragon.f34_resonance_pct_5d | cond_p5 | 2024 | 58 | C | 0.479 |
| dragon.f35_bollinger_trend | cond_p5 | 2024 | 65 | B | -0.320 |
| dragon.f36_identity_premium | cond_p5 | 2024 | 65 | B | 0.443 |
| force.f_mean_reversion_signal | cond_p5 | 2024 | 65 | B | -0.327 |

## 历史 A 因子完整矩阵（得分/评级）

### dragon.f33_consecutive_boards

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 83/A | - | - |
| cond_p10 | - | - | - |
| cond_p20 | - | - | - |
| full_p5 | - | - | - |
| full_p10 | - | - | - |
| full_p20 | - | - | - |

### dragon.f36_identity_premium

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 65/B | - | - |
| cond_p10 | - | - | - |
| cond_p20 | - | - | - |
| full_p5 | - | - | - |
| full_p10 | - | - | - |
| full_p20 | - | - | - |

### dragon.f34_resonance_pct

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 58/C | - | - |
| cond_p10 | - | - | - |
| cond_p20 | - | - | - |
| full_p5 | - | - | - |
| full_p10 | - | - | - |
| full_p20 | - | - | - |

### dragon.f34_resonance_pct_3d

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 58/C | - | - |
| cond_p10 | - | - | - |
| cond_p20 | - | - | - |
| full_p5 | - | - | - |
| full_p10 | - | - | - |
| full_p20 | - | - | - |

### dragon.f34_resonance_pct_5d

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 58/C | - | - |
| cond_p10 | - | - | - |
| cond_p20 | - | - | - |
| full_p5 | - | - | - |
| full_p10 | - | - | - |
| full_p20 | - | - | - |

### dragon.f34_resonance_pct_10d

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 58/C | - | - |
| cond_p10 | - | - | - |
| cond_p20 | - | - | - |
| full_p5 | - | - | - |
| full_p10 | - | - | - |
| full_p20 | - | - | - |

### dragon.f35_bollinger_trend

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 65/B | - | - |
| cond_p10 | - | - | - |
| cond_p20 | - | - | - |
| full_p5 | - | - | - |
| full_p10 | - | - | - |
| full_p20 | - | - | - |

### force.f_mean_reversion_signal

| 模式 | 2024 | 2025 | 2026/2026_YTD |
|------|------|------|----------------|
| cond_p5 | 65/B | - | - |
| cond_p10 | - | - | - |
| cond_p20 | - | - | - |
| full_p5 | - | - | - |
| full_p10 | - | - | - |
| full_p20 | - | - | - |

## 各模式 A/B 因子数量（50 字段）

| 模式 | 年份 | A | B | C | D |
|------|------|---|---|---|---|
| cond_p5 | 2024 | 1 | 12 | 20 | 17 |

## 说明

- 历史 `factor_ab_history.md` 的 A 多来自 **period=10/20** 或 **全市场** 口径，与 cond_p5 不可直接比。
- 标准 A 线：Total_Score ≥ 80；66~73 分为 B。
- 2026 区间截止 2026-07-27（因子/L2 数据交集）。
