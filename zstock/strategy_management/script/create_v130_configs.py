import json
from pathlib import Path

base_path = Path("zstock/common/config/strategy_params_v128_cap40.json")
base_cfg = json.loads(base_path.read_text(encoding="utf-8"))

def make_cfg(name, desc, cap, fill_slots=True):
    cfg = json.loads(json.dumps(base_cfg))
    cfg["description"] = desc
    cfg.setdefault("turnover_control", {})["fill_open_slots"] = fill_slots
    cfg.setdefault("portfolio_optimization", {})["fill_weights_to_position_scale"] = True
    cfg["portfolio_optimization"]["max_single_stock_cap"] = cap
    out_path = Path(f"zstock/common/config/{name}.json")
    out_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Created {out_path.name}")

make_cfg("strategy_params_v130_cap40_fill_slots", "v1.30.0: cap=0.40 + fill_open_slots=True", 0.40, True)
make_cfg("strategy_params_v130_cap45_fill_slots", "v1.30.0: cap=0.45 + fill_open_slots=True", 0.45, True)
make_cfg("strategy_params_v130_cap50_fill_slots", "v1.30.0: cap=0.50 + fill_open_slots=True", 0.50, True)
