from pathlib import Path

log_file = Path(r"E:\TradingAgents-CN\zstock\strategy_management\script\output\overnight_opt_20261008_225018\overnight_run.log")
lines = log_file.read_text(encoding="utf-8").splitlines()

for i, line in enumerate(lines):
    if "opt=5 final=1" in line:
        start = max(0, i - 15)
        end = min(len(lines), i + 5)
        print("--- MATCH AT LINE", i, "---")
        for j in range(start, end):
            print(lines[j].encode("ascii", "replace").decode("ascii"))
        break
