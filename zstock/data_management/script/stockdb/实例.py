# 接口文档：https://dns.novapi.cc/docs/index.html

import sys

from stockdb import StockDb

db = StockDb(port=7899)

rows = db.get_daily("600000", "2026-06-01", "2026-06-23")
minutes = db.get_minute("600000", "20260623", "20260623093500", fields="date,close")
codes = db.get_codes("sh")
factors = db.get_adjust_factors("600000")