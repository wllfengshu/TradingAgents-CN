"""QMTUtil 更多分支：无 token、卖出无持仓、金额不足一手。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from zstock.common.utils.qmt_util import QMTUtil


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        method = body.get("method")
        if method == "ping":
            payload = {"id": body.get("id"), "ok": True, "result": {"pong": True}}
        elif method == "query_stock_positions":
            payload = {"id": body.get("id"), "ok": True, "result": []}
        elif method == "get_full_tick":
            payload = {"id": body.get("id"), "ok": True, "result": {
                "603201.SH": {"lastPrice": 0.0},
            }}
        else:
            payload = {"id": body.get("id"), "ok": False, "error": method}
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_connect_without_token(monkeypatch):
    QMTUtil._instance = None
    monkeypatch.delenv("QMT_BRIDGE_TOKEN", raising=False)
    monkeypatch.setattr(
        "zstock.common.utils.qmt_util._read_bridge_setting",
        lambda *_a, **_k: "",
    )
    client = QMTUtil()
    client.token = ""
    assert client.connect() is False
    QMTUtil._instance = None


def test_sell_without_position_and_buy_too_small(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    QMTUtil._instance = None
    try:
        client = QMTUtil()
        client.host = "127.0.0.1"
        client.port = port
        client.token = "t"
        client.account_id = "1"
        client._connected = False
        try:
            client.get_account_info()
            assert False, "应要求先 connect"
        except RuntimeError:
            pass
        client._connected = True
        assert client.sell("603201", volume=None, price=10.0) is None
        assert client.buy("603201", amount=1.0, price=10.5) is None
        # 无显式价格且 tick=0 → 取消买入
        assert client.buy("603201", amount=10000.0, price=None) is None
        with client:
            pass
    finally:
        server.shutdown()
        QMTUtil._instance = None
