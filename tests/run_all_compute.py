import sys
sys.stdout.reconfigure(encoding='utf-8')
from app.services.ai_selector_service import (
    compute_market_indicators,
    compute_sector_indicators,
    compute_force_indicators,
    compute_leader_indicators,
    compute_risk_indicators,
)
import json

results = {
    "market": compute_market_indicators(),
    "sector": compute_sector_indicators(),
    "force": compute_force_indicators(),
    "leader": compute_leader_indicators(),
    "risk": compute_risk_indicators(),
}

failed = {}
for name, data in results.items():
    failed_items = {k: v for k, v in data.items() if v == "获取失败"}
    if failed_items:
        failed[name] = list(failed_items.keys())

if failed:
    print("FAILED:", json.dumps(failed, ensure_ascii=False, indent=2))
else:
    print("ALL PASSED - no failures")

# Print all keys and their types
for name, data in results.items():
    print(f"\n=== {name} ===")
    for k, v in data.items():
        vtype = type(v).__name__
        if isinstance(v, list):
            vtype = f"list[{len(v)}]"
        print(f"  {k}: {vtype}")
