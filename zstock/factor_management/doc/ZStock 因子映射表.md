# ZStock 因子映射表 - 完整版

> 核心映射：因子编码 + 因子名称 + 计算公式 + MongoDB字段 + 用途

---

## M1 市场因子 (MarketFactors)

**存储表**：`zstock_factor_market` | **唯一键**：`trade_date`

| 因子编码 | 因子名称 | 权重 | 计算公式 | MongoDB字段 | 用途 |
|---------|---------|------|---------|-----------|------|
| MF1 | 市场趋势强度 | 30% | 20日MA的5日斜率 → Sigmoid(k=800) | `mf1_slope_pct` | 判断市场中期方向 |
| MF2 | 市场布林位置 | 25% | (close - lower) / (upper - lower) [0,1] | `mf2_boll_pct` | 判断超买/超卖位置 |
| MF3 | 市场成交量状态 | 20% | today_volume / MA(vol_ma_window)_volume → 折线映射 | `mf3_vol_ratio` | 判断成交量热度 |
| MF4 | 市场N日动量 | 15% | N日收益率 → Sigmoid(k=30) | `mf4_momentum_5d` | 捕捉短期上升动量 |
| MF5 | 市场波动率抑制 | 10% | ATR(atr_window)/MA(ma_window) → 折线映射 | `mf5_atr_ratio` | 判断市场稳定性 |

**多窗口字段**（网格搜索用）：
- **MF1 趋势强度**：`mf1_slope_pct_5d` / `mf1_slope_pct_10d` / `mf1_slope_pct_20d`
- **MF2 布林位置**：`mf2_boll_pct_10d` / `mf2_boll_pct_20d` / `mf2_boll_pct_30d`
- **MF3 成交量**：`mf3_vol_ratio_5d` / `mf3_vol_ratio_10d` / `mf3_vol_ratio_20d`
- **MF4 动量**：`mf4_momentum_3d` / `mf4_momentum_5d` / `mf4_momentum_10d`
- **MF5 波动率**：`mf5_atr_ratio_10d` / `mf5_atr_ratio_20d` / `mf5_atr_ratio_30d`

**最终输出**：
- `market_composite_score` [0,100] - 综合得分
- `market_risk_level` - "green"(1.0x仓位) / "yellow"(0.4x) / "red"(0仓)
- `position_scale_factor` - 仓位缩放因子

---

## M2 板块因子 (SectorFactors)

**存储表**：`zstock_factor_sector` | **唯一键**：`(trade_date, sector_code)`

| 因子编码 | 因子名称 | 权重 | 计算公式 | MongoDB字段 | 用途 |
|---------|---------|------|---------|-----------|------|
| F2.1 | 板块RPS动量 | 15% | (sector_close[-1] / sector_close[-window] - 1) × 100 | `f21_rps` | 板块相对强度排名 |
| F2.2 | 板块资金净流入 | 15% | sum(member_stocks_main_net × 10000) [元] | `f22_main_flow` | 判断主力资金方向 |
| F2.5 | 成交占比斜率 | 10% | 板块MA(window)_ratio的最近5日斜率 | `f25_volume_slope` | 判断成交额趋势 |
| F2.6 | 成交额增长 | 25% | MA(short_window) / MA(long_window) - 1 | `f26_volume_growth` | 捕捉成交加速信号 |
| **φ门槛** |  |  |  |  |  |
| F2.3 | 涨停浓度 | — | count(limit_up) / total_members | `f23_limit_up_density` | 判断板块热度（必须>0） |
| F2.8 | 10日持续性 | — | 10日内Top10%排名的天数 | `f28_consistency` | 判断板块稳定性（必须≥3） |

**多窗口字段**（网格搜索用）：
- **F2.1 RPS**：`f21_rps_10d` / `f21_rps_20d` / `f21_rps_60d`
- **F2.5 成交占比斜率**：`f25_volume_slope_3d` / `f25_volume_slope_5d` / `f25_volume_slope_10d`
- **F2.6 成交额增长**：`f26_volume_growth_5d` / `f26_volume_growth_20d`

**φ 过滤条件**：`f23_limit_up_density > 0` AND `f28_consistency >= 3`

---

## M3 龙头因子 (DragonFactors)

**存储表**：`zstock_factor_dragon` | **唯一键**：`(trade_date, sector_code, code)`

| 因子编码 | 因子名称 | 权重 | 计算公式 | MongoDB字段 | 用途 |
|---------|---------|------|---------|-----------|------|
| F3.1b | RPS分位 | 40% | percentile_rank(stock_return in sector) × 100 | `f31b_rps_percentile` | 个股相对板块排名（新） |
| F3.2 | 成交额当日 | 35% | today_amount [元]（打分时**取反**） | `f32_amount` | 人气度（反向：低额→高分） |
| F3.4 | 量价共振度 | 25% | (price_up_vol_up + price_down_vol_down + limit_up) / window | `f34_resonance_pct` | 量价一致性（打分时**取反**） |
| **φ门槛** |  |  |  |  |  |
| F3.3 | 连板基因 | — | 从末行往回扫，连续涨停天数 | `f33_consecutive_boards` | 连板高度（条件1：≥1） |
| F3.5 | 布林趋势 | — | position_score(30分) + slope_score(40分) [0-100绝对分] | `f35_bollinger_trend` | 趋势强度（必须≥40） |
| F3.5 | 上升通道 | — | (close > MA20) AND (MA_slope > 0) → {0/1} | `f35_bollinger_pass` | 通道判定（条件2：与连板OR） |

**多窗口字段**（网格搜索用）：
- `f31_excess_return_5d` / `f31_excess_return_10d` / `f31_excess_return_20d` - 超额收益窗口
- `f34_resonance_pct_3d` / `f34_resonance_pct_5d` / `f34_resonance_pct_10d` - 共振窗口

**φ 过滤条件**：`f35_bollinger_trend >= 40` AND (`f33_consecutive_boards >= 1` OR `f35_bollinger_pass >= 1`)

---

## M4 合力因子 (ForceFactors)

**存储表**：`zstock_factor_force` | **唯一键**：`(trade_date, code)`

| 因子编码 | 因子名称 | 用途 | 计算公式 | MongoDB字段 | 单位 |
|---------|---------|------|---------|-----------|------|
| **φ门槛** |  |  |  |  |  |
| F_coop1 | 主力净流入占比 | 验证资金进场 | main_flow / total_volume | `fcoop1_main_net_ratio` | [0,1]（必须≥5%） |
| F_coop3 | 持续性天数 | 验证资金坚守 | count(近window日 main_net > 0) | `fcoop3_sustained_days` | 天数（必须≥2） |
| **α排序** |  |  |  |  |  |
| F_coop4 | 换手率质量 | 评估交割质量 | 分段映射：[3-5%]=0.1→1.0 [5-20%]=1.0 [20-30%]=1.0→0.1（**打分时取反**） | `fcoop4_turnover_quality` | [0,1] |
| **P1a加分** |  |  |  |  |  |
| LHB加分 | 龙虎榜加分 | 捕捉游资热点 | 基础+5 + 机构比>30%+10 + 买一<30%+8 = [0,~23] | `longhu_board_bonus` | 分数 |

**多窗口字段**（网格搜索用）：
- `fcoop3_sustained_days_3d` / `fcoop3_sustained_days_5d` / `fcoop3_sustained_days_10d` - 持续窗口变体

**原始值记录**（供追溯）：
- `main_flow` - 主力净流入（元）
- `retail_flow` - 散户净流入（元）
- `total_volume` - 总成交额（元）

**φ 过滤条件**：
```
total_volume > 0 AND 
main_flow > 0 AND 
fcoop1_main_net_ratio >= 0.05 AND 
fcoop3_sustained_days >= 2
```

---

## M5 最终信号 (Pipeline)

**存储表**：`zstock_factor_force` | **字段**：`strategy_signal_score`

| 字段名 | 数值 | 计算公式 | 用途 |
|-------|------|---------|------|
| **综合得分** |  |  |  |
| `strategy_signal_score` | [0,100] | (0.25 × sector_rank + 0.35 × m3_score + 0.40 × force_score) | **最终排序字段** |
| **子成分** |  |  |  |
| `force_composite_score` | [0,100] | coop4_norm(0-100) + lhb_bonus(0-23) [上限100] | M4合力分 |
| `dragon_composite_score` | [0,100] | (0.40 × f31b_norm + 0.35 × (-f32_norm) + 0.25 × (-f34_norm)) | M3龙头分 |
| `coop4_norm` | [0,100] | min-max(fcoop4 取反) | 换手质量标准化 |
| `longhu_board_bonus` | [0,~23] | 龙虎榜加分 | P1a加分 |

**权重说明**：
- **板块排名 25%**：Top K板块反向映射(100-0)
- **龙头综合 35%**：M3三个α因子加权
- **合力综合 40%**：M4换手+龙虎榜加分（权重最高）

---

## 📋 快速查询

### 查某日信号（Top 5）
```python
from zstock.data_management.query_service import get_data_query_service

qs = get_data_query_service()
forces = await qs.get_factor_forces('2026-08-08')

# 按 strategy_signal_score 排序取前5
signals = sorted(forces, 
    key=lambda x: x.get('strategy_signal_score', 0), 
    reverse=True)[:5]

for sig in signals:
    print(f"{sig['code']}: {sig['strategy_signal_score']:.1f}")
```

### 查某日某板块的龙头（前3）
```python
dragons = await qs.get_factor_dragons('2026-08-08', sector_codes=['SW201010'])

# 按 dragon_composite_score 排序取前3
valid = [d for d in dragons 
    if d['f35_bollinger_trend'] >= 40 
    and (d.get('f33_consecutive_boards', 0) >= 1 or d.get('f35_bollinger_pass', 0) >= 1)]

top3 = sorted(valid, 
    key=lambda x: x.get('dragon_composite_score', 0), 
    reverse=True)[:3]
```

### 查某日市场情绪
```python
market = await qs.get_factor_market('2026-08-08')

print(f"综合得分: {market['market_composite_score']:.1f}")
print(f"风险等级: {market['market_risk_level']}")
print(f"仓位缩放: {market['position_scale_factor']}")
```

---

## 🔑 关键概念

### 单位对齐（重要！）
```
主力净流入: L2万元 → ×10000 → 元
成交额:     L2元   → 无需转换 → 元
计算比值:   都用元，比值为纯数字（无单位）✓
```

### φ 硬门槛（必须全部通过）
```
M2: f23_limit_up_density > 0 AND f28_consistency >= 3
M3: f35_bollinger_trend >= 40 AND (f33_consecutive_boards >= 1 OR f35_bollinger_pass >= 1)
M4: total_volume > 0 AND main_flow > 0 AND fcoop1 >= 0.05 AND fcoop3_sustained_days >= 2
```

### α 软权重（按权重加权平均，仅对过滤后的候选）
```
M1: MF1(30%) + MF2(25%) + MF3(20%) + MF4(15%) + MF5(10%)
M2: F2.1(15%) + F2.2(15%) + F2.5(10%) + F2.6(25%)
M3: F3.1b(40%) + F3.2(35%) + F3.4(25%)
M5: 板块(25%) + 龙头(35%) + 合力(40%)
```

### 取反因子（基于RankIC显著负相关）
```
F3.2 成交额：取反（成交额越低得分越高）
F3.4 量价共振：取反（共振度越低得分越高）
F_coop4 换手质量：取反（换手越低得分越高）
```

---

**最后更新** | 2026-08-08 | ZStock量化系统