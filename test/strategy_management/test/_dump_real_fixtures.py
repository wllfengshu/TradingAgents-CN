"""从 Mongo 导出真实 score_signals / OHLCV 到 test/fixtures（不编造）。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.test.conftest import (  # noqa: E402
    CANDIDATE_SIGNAL_DATES,
    FIXTURES_DIR,
    OHLCV_FIXTURE,
    SIGNALS_FIXTURE,
    signals_to_payload,
)


async def main() -> int:
    from zstock.common.utils.common_utils import normalize_date
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.data_management.query_service import get_data_query_service
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

    await init_zstock_database()
    try:
        pipe = CrossSectionStrategyPipeline()
        days = {}
        nonempty = None
        for td in CANDIDATE_SIGNAL_DATES:
            try:
                df = await pipe.score_signals(td)
            except Exception as exc:
                print(f"skip {td}: {exc}")
                continue
            days[td] = signals_to_payload(df, td)
            n = 0 if df is None else len(df)
            print(f"{td}: n={n} grade={getattr(df, 'attrs', {}).get('market_grade')} empty={df is None or df.empty}")
            if df is not None and not df.empty and nonempty is None:
                nonempty = td
        if not days:
            print("no signals dumped")
            return 1
        primary = max(days, key=lambda d: len(days[d].get("records") or []))
        bundle = {"days": days, "primary_date": primary}
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        SIGNALS_FIXTURE.write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print("wrote", SIGNALS_FIXTURE, "primary", bundle["primary_date"])

        codes = sorted(
            {
                r["code"]
                for payload in days.values()
                for r in payload.get("records") or []
                if r.get("code")
            }
        )
        qs = get_data_query_service()
        payload = {"windows": [("2024-01-02", "2024-01-16"), ("2024-05-27", "2024-06-14")], "by_code": {}}
        for start, end in payload["windows"]:
            batch = await qs.get_ohlcv_batch(codes, start, end)
            for code, df in (batch or {}).items():
                if df is None or df.empty:
                    continue
                work = df.copy()
                if "trade_date" in work.columns:
                    work["trade_date"] = work["trade_date"].apply(normalize_date)
                keep = [c for c in ("trade_date", "open", "high", "low", "close", "volume") if c in work.columns]
                rows = work[keep].to_dict(orient="records")
                existing = payload["by_code"].setdefault(code, [])
                seen = {r["trade_date"] for r in existing}
                for row in rows:
                    if row["trade_date"] not in seen:
                        existing.append(row)
                        seen.add(row["trade_date"])
        OHLCV_FIXTURE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print("wrote", OHLCV_FIXTURE, "codes", len(payload["by_code"]))
        return 0
    finally:
        await close_zstock_database()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
