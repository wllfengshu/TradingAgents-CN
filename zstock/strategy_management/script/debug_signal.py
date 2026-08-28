"""单日信号排查 CLI：python -m zstock.strategy_management.script.debug_signal 2024-04-01 601211"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def debug(trade_date: str, code: str | None = None) -> None:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.data_management.query_service import get_data_query_service
    from zstock.factor_management.dragon_factors import DragonFactors
    from zstock.factor_management.force_factors import ForceFactors
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.factor_management.sector_factors import SectorFactors

    await init_zstock_database()
    try:
        pipe = CrossSectionStrategyPipeline()
        qs = get_data_query_service()
        regime = "neutral"
        active = pipe._get_active_factors(regime)
        top_sectors_n = pipe._cfg_top_sectors()
        top_per = pipe.config.get("dragon_layer", {}).get("top_per_sector", 2)
        thr = pipe.config.get("cooperative_force", {}).get("threshold_pct", 0.01)
        weights = pipe.config.get("final_score", {}).get("weights", {})

        sector_docs = await qs.get_factor_sectors(trade_date)
        regime = "neutral"
        m2_scores = SectorFactors.scores_from_raw({
            "f21_rps_20d": {d["sector_code"]: d.get("f21_rps_20d", d.get("f21_rps", float("nan"))) for d in sector_docs},
            "f22_main_flow": {d["sector_code"]: d.get("f22_main_flow", 0.0) for d in sector_docs},
            "f23_limit_up_density": {d["sector_code"]: d.get("f23_limit_up_density", 0.0) for d in sector_docs},
            "f28_consistency": {d["sector_code"]: d.get("f28_consistency", 0) for d in sector_docs},
            "f30_sector_concentration": {d["sector_code"]: d.get("f30_sector_concentration", float("nan")) for d in sector_docs},
        }, regime=regime, active_factors=active, top_n=top_sectors_n)
        top_sectors = sorted(m2_scores.items(), key=lambda x: x[1], reverse=True)[:top_sectors_n]

        print(f"=== {trade_date} M1/M6 (score_signals) ===")
        df = await pipe.score_signals(trade_date)
        print(json.dumps({k: df.attrs.get(k) for k in df.attrs}, ensure_ascii=False, indent=2, default=str))
        print(f"buy count: {(df['signal_type'] == 'buy').sum()}, universe: {len(df)}")
        print(df[["code", "sector_code", "final_score", "dragon_score", "force_composite_score", "rank"]].to_string())

        print(f"\n=== M2 top {top_sectors_n} sectors ===")
        for s, sc in top_sectors:
            print(f"  {s}: {sc:.2f}")

        dragon_docs = await qs.get_factor_dragons(trade_date)
        all_candidates = []
        for sector_code, m2_score in top_sectors:
            in_sec = [d for d in dragon_docs if d.get("sector_code") == sector_code]
            sm = {d["code"]: d for d in in_sec}
            m3_scores = DragonFactors.scores_from_raw(sm, regime=regime, active_factors=active)
            cands = pipe._top_by_score(m3_scores, top_per)
            print(f"\n=== M3 {sector_code} top{top_per} (pool={len(in_sec)}) ===")
            for c, sc in cands:
                print(f"  {c} {sm[c].get('stock_name', '')}: {sc:.2f}")
                all_candidates.append(
                    {"code": c, "sector_code": sector_code, "dragon_composite_score": sc, "m2_score": m2_score}
                )

        all_candidates = pipe._dedupe_candidates_by_code(all_candidates)
        print(f"\n=== M3 candidates total: {len(all_candidates)} ===")

        force_docs = await qs.get_factor_forces(trade_date)
        force_map = {d["code"]: d for d in force_docs}
        filtered = []
        dropped = []
        for c in all_candidates:
            fd = force_map.get(c["code"])
            if fd is None:
                dropped.append((c["code"], "no M4"))
                continue
            if not ForceFactors.passes_precomputed_m4_gate(fd, thr):
                dropped.append((c["code"], f"M4 gate fcoop1={fd.get('fcoop1_main_net_ratio')}"))
                continue
            filtered.append(c)

        print(f"\n=== M4 dropped ({len(dropped)}) sample ===")
        for item in dropped[:15]:
            print(f"  {item[0]}: {item[1]}")
        if len(dropped) > 15:
            print(f"  ... +{len(dropped) - 15} more")

        print(f"\n=== M4 passed: {len(filtered)} ===")
        merged = []
        for c in filtered:
            fd = force_map[c["code"]]
            m = {"code": c["code"], "sector_code": c["sector_code"], "dragon_composite_score": c["dragon_composite_score"]}
            m.update({k: v for k, v in fd.items() if k not in ("code", "sector_code")})
            merged.append(m)

        adj = ForceFactors._adjust_weights_by_style({"regime": regime}, active_factors=active)
        composite = ForceFactors._composite_force_scores(merged, adj)

        print("\n=== M5 breakdown ===")
        for c in sorted(filtered, key=lambda x: -x["dragon_composite_score"]):
            cid = c["code"]
            sector_s = c["m2_score"]
            dragon_s = c["dragon_composite_score"]
            force_s = composite.get(cid, 0.0)
            final = (
                sector_s * weights.get("sector", 0.4)
                + dragon_s * weights.get("dragon", 0.35)
                + force_s * weights.get("cooperative", 0.25)
            )
            mark = " <-- TARGET" if code and cid == code else ""
            print(
                f"  {cid} [{c['sector_code']}] sector={sector_s:.1f} dragon={dragon_s:.1f} "
                f"force={force_s:.1f} final~{final:.1f}{mark}"
            )

        if code:
            print(f"\n=== Raw factors: {code} ===")
            d3 = next((d for d in dragon_docs if str(d["code"]) == code), None)
            f4 = force_map.get(code)
            if d3:
                print("M3:", json.dumps(d3, ensure_ascii=False, indent=2, default=str))
            if f4:
                print("M4:", json.dumps(f4, ensure_ascii=False, indent=2, default=str))
    finally:
        await close_zstock_database()


def main() -> None:
    td = sys.argv[1] if len(sys.argv) > 1 else "2024-04-01"
    code = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(debug(td, code))


if __name__ == "__main__":
    main()
