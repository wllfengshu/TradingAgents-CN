import json
from pathlib import Path

base_path = Path("zstock/common/config/strategy_params_v128_cap40.json")
base_cfg = json.loads(base_path.read_text(encoding="utf-8"))

def save_cfg(name, desc, mutator):
    cfg = json.loads(json.dumps(base_cfg)) # deepcopy
    cfg["description"] = desc
    mutator(cfg)
    out_path = Path(f"zstock/common/config/{name}.json")
    out_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Created {out_path.name}")

# 1. v129_cap45
def mut_cap45(c):
    c["portfolio_optimization"]["max_single_stock_cap"] = 0.45

save_cfg("strategy_params_v129_cap45", "v1.29.0: fill_weights + max_single_stock_cap 0.45", mut_cap45)

# 2. v129_cap50
def mut_cap50(c):
    c["portfolio_optimization"]["max_single_stock_cap"] = 0.50

save_cfg("strategy_params_v129_cap50", "v1.29.0: fill_weights + max_single_stock_cap 0.50", mut_cap50)

# 3. v129_cap55
def mut_cap55(c):
    c["portfolio_optimization"]["max_single_stock_cap"] = 0.55

save_cfg("strategy_params_v129_cap55", "v1.29.0: fill_weights + max_single_stock_cap 0.55", mut_cap55)

# 4. v129_dyn_g55_y40
def mut_dyn(c):
    c["portfolio_optimization"]["max_single_stock_cap"] = 0.45
    c["portfolio_optimization"]["single_stock_cap_by_grade"] = {
        "green": 0.55,
        "yellow": 0.40,
        "red": 0.0
    }

save_cfg("strategy_params_v129_dyn_g55_y40", "v1.29.0: fill_weights + dynamic cap green 0.55, yellow 0.40", mut_dyn)

# 5. v129_cap50_stop6
def mut_cap50_stop6(c):
    c["portfolio_optimization"]["max_single_stock_cap"] = 0.50
    c["exit_rules"]["hard_stop_loss_pct"] = -0.06

save_cfg("strategy_params_v129_cap50_stop6", "v1.29.0: cap 0.50 + hard stop -6%", mut_cap50_stop6)

# 6. v129_cap50_te_boost
def mut_cap50_te_boost(c):
    c["portfolio_optimization"]["max_single_stock_cap"] = 0.50
    te = c.setdefault("target_equity", {}).setdefault("by_context", {})
    te["neutral_green"] = 1.0
    te["reversal_green"] = 0.85
    te["volatile_green"] = 0.85

save_cfg("strategy_params_v129_cap50_te_boost", "v1.29.0: cap 0.50 + target_equity green boost", mut_cap50_te_boost)
