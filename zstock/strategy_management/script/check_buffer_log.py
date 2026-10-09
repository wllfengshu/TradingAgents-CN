import re
from pathlib import Path

log_file = Path(r"E:\TradingAgents-CN\zstock\strategy_management\script\output\overnight_opt_20261008_225018\overnight_run.log")

lines = log_file.read_text(encoding="utf-8").splitlines()
samples = []
for i, line in enumerate(lines):
    if "完成: signals=" in line:
        # e.g. "✅ 完成: signals=4 opt=3 final=1 turnover=25.00%"
        m = re.search(r"signals=(\d+)\s+opt=(\d+)\s+final=(\d+)", line)
        if m:
            sig, opt, fin = int(m.group(1)), int(m.group(2)), int(m.group(3))
            samples.append((sig, opt, fin, line))

print(f"Total rebalance points: {len(samples)}")
opt_gt_fin = [s for s in samples if s[1] > s[2]]
print(f"Cases where opt > final (candidates were discarded by buffer/min_hold): {len(opt_gt_fin)} / {len(samples)} ({len(opt_gt_fin)/len(samples):.1%})")

print("\nSample cases where opt > final:")
for s in opt_gt_fin[:20]:
    print(f"signals={s[0]}, opt={s[1]} -> final={s[2]}")
