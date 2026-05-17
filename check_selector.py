import ast
with open('app/services/ai_selector_service.py', encoding='utf-8') as f:
    src = f.read()
ast.parse(src)
print('语法检查通过，总行数:', src.count('\n')+1)

checks = [
    ('北向ETF方向信号_涨跌方向', '3a2数据字段'),
    ('北向资金净持仓聚合', '3b聚合字段'),
    ('封板比统计', '封板比字段'),
    ('炸板统计', '炸板率字段'),
    ('候选标的基本面', '基本面字段'),
    ('stock_zt_pool_dtgc_em', '炸板API'),
    ('stock_individual_info_em', '基本面API'),
    ('position_risk', 'LEADER JSON字段'),
    ('t1_risk_note', 'LEADER JSON字段'),
    ('T+1', 'T+1风险关键词'),
    ('risk_notes', 'RISK safe_stocks字段'),
    ('5个bar区间', '5日涨跌幅口径说明'),
    ('avg_seal_ratio', 'SECTOR JSON字段'),
    ('broken_limit_rate', 'SECTOR JSON字段'),
    ('北向资金ETF成交额(亿)', '旧字段（应为0次）'),
]
all_ok = True
for kw, desc in checks:
    count = src.count(kw)
    if kw == '北向资金ETF成交额(亿)':
        status = 'OK(已清除)' if count == 0 else f'WARN 旧字段残留{count}次'
        if count > 0: all_ok = False
    else:
        status = 'OK' if count > 0 else 'MISSING'
        if count == 0: all_ok = False
    print(f'  [{status}]  {desc}: "{kw}" 出现{count}次')

print()
print('全部检查通过!' if all_ok else '存在问题，请检查上方MISSING/WARN项')

