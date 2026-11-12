"""QMT 桥客户端与 stockdb 工具的离线测试。不连接真桥、不连接 stockdb。"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pandas as pd

from zstock.common.utils.stockdb_utils import StockDbClient
from zstock.common.utils.xtquant_util import QMTUtil, to_qmt_code


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        method = body.get("method")
        if self.headers.get("X-Token") != "test-token":
            payload = {"id": body.get("id"), "ok": False, "error": "unauthorized"}
            code = 401
        elif method == "ping":
            payload = {"id": body.get("id"), "ok": True, "result": {"pong": True}}
            code = 200
        elif method == "query_stock_asset":
            payload = {"id": body.get("id"), "ok": True, "result": {
                "cash": 1000.0, "total_asset": 5000.0, "frozen_cash": 10.0,
            }}
            code = 200
        elif method == "get_full_tick":
            payload = {"id": body.get("id"), "ok": True, "result": {
                "600000.SH": {"lastPrice": 10.5},
            }}
            code = 200
        elif method == "order_stock":
            payload = {"id": body.get("id"), "ok": True, "result": {
                "queued": True, "order_id": -7,
            }}
            code = 200
        else:
            payload = {"id": body.get("id"), "ok": False, "error": "unexpected " + str(method)}
            code = 400
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_to_qmt_code_suffix():
    assert to_qmt_code("600000") == "600000.SH"
    assert to_qmt_code("159915") == "159915.SZ"
    assert to_qmt_code("511260") == "511260.SH"
    assert to_qmt_code("512480.SH") == "512480.SH"


def test_qmt_util_talks_to_bridge(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    QMTUtil._instance = None
    try:
        monkeypatch.setenv("QMT_BRIDGE_TOKEN", "test-token")
        monkeypatch.setenv("QMT_BRIDGE_HOST", "127.0.0.1")
        monkeypatch.setenv("QMT_BRIDGE_PORT", str(port))
        monkeypatch.setenv("QMT_BRIDGE_ACCOUNT_ID", "1")
        client = QMTUtil()
        client.host = "127.0.0.1"
        client.port = port
        client.token = "test-token"
        client.account_id = "1"
        assert client.connect() is True
        info = client.get_account_info()
        assert info.cash == 1000.0
        assert info.total_value == 5000.0
        quote = client.get_realtime_quote(["600000"])
        assert quote["600000"]["lastPrice"] == 10.5
        order_id = client.buy("600000", amount=0, price=10.5, volume=100)
        assert order_id == -7
    finally:
        server.shutdown()
        QMTUtil._instance = None


class _Sdk:
    def __init__(self):
        self.rd = self
        self.calls = []

    def init(self, host, port):
        self.calls.append(("init", host, port))

    def get_data(self, code, start, end, frequency, fq, as_df):
        assert frequency == "1d"
        assert fq == "qfq"
        assert as_df is False
        return {
            "600000": [{"date": "20260105", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100}],
            "000001": [{"trade_date": "2026-01-05", "close": 12}],
        }

    def get_money_flow(self, security_list, start_date, end_date):
        assert start_date == "2026-01-01"
        return [{
            "sec_code": "600000",
            "date": "2026-01-05",
            "net_amount_main": 123.5,
            "net_pct_main": 1.5,
            "net_amount_xl": 80.0,
            "net_pct_xl": 0.8,
        }]


def test_stockdb_bars_and_money_flow_units():
    sdk = _Sdk()
    client = StockDbClient(sdk=sdk)
    assert client.connect() is True
    bars = client.get_bars(["600000", "000001"], "2026-01-01", "2026-01-05")
    assert list(bars["600000"]["close"]) == [10.5]
    assert bars["600000"]["trade_date"].iloc[0] == "2026-01-05"
    flow = client.get_money_flow(["600000"], "2026-01-01", "2026-01-05")
    row = flow["600000"].iloc[0]
    assert row["main_net"] == 123.5
    assert row["main_net_pct"] == 1.5
    assert row["main_net_ratio"] == pd.Series([0.015]).iloc[0]
    assert abs(row["xl_net_ratio"] - 0.008) < 1e-12
