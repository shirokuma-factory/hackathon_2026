"""公開版のサーバーを、来訪者 1 人が壊したり他の人の状態を消したりできないことの試験（2026-11-06 のセキュリティ点検）。

- 仮予約は 1 人 MAX_HOLDS 件まで、荷主の項目は数と長さに上限（メモリを使い切らせない）
- NUL を含むパスは 404（例外で接続が切れない）
- 応答に nosniff と X-Frame-Options、Server に Python の版を出さない
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import threading
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import spaceb  # noqa: E402


def _load_server():
    spec = importlib.util.spec_from_file_location("serve_web_hardening", ROOT / "tools" / "serve_web.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["serve_web_hardening"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_server()


@pytest.fixture(scope="module", params=[True, False], ids=["public", "local"])
def srv(mod, request):
    httpd = mod.make_server(0, public=request.param)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {"Host": "127.0.0.1", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=h)
    r = conn.getresponse()
    raw = r.read()
    out = (r.status, r.getheaders(), raw)
    conn.close()
    return out


def _hold_body(name="A"):
    return {"trip_id": "T7", "dest_code": "28110", "is_box": True,
            "shipment": {"items": [{"count": 1, "length_cm": 1, "width_cm": 1, "height_cm": 1, "weight_kg": 0.001}]},
            "shipper": {"name": name}}


def test_holds_per_board_are_capped():
    board = spaceb.Board()
    trip = board.trips()[0]["id"]
    ship = spaceb.BShipment(pieces=(spaceb.Piece(1, *(spaceb.Decimal("10"),) * 3, spaceb.Decimal("0.001")),),
                            dest_code="28110")
    made = 0
    with pytest.raises(spaceb.BoardError, match="件までです"):
        for _ in range(spaceb.MAX_HOLDS + 5):
            try:
                board.hold(trip, ship, 60)
            except spaceb.BoardError as e:
                if "件までです" in str(e):
                    raise
                pytest.skip(f"この便に小さな荷が載らない: {e}")
            made += 1
    assert made == spaceb.MAX_HOLDS
    board.reset()
    assert board.holds() == []


def test_long_shipper_is_rejected(srv):
    port = srv.server_address[1]
    sid = {"X-Demo-Session": uuid.uuid4().hex}
    st, _, raw = req(port, "POST", "/api/b/hold", _hold_body("A" * 5000), sid)
    assert st == 400 and "文字まで" in json.loads(raw)["error"]
    many = _hold_body()
    many["shipper"] = {f"k{i}": "x" for i in range(20)}
    st, _, raw = req(port, "POST", "/api/b/hold", many, sid)
    assert st == 400


def test_nul_in_path_is_404_not_a_dropped_connection(srv):
    port = srv.server_address[1]
    st, _, _ = req(port, "GET", "/data/local%00.json")
    assert st == 404
    st, _, _ = req(port, "HEAD", "/index%00.html")
    assert st == 404


def test_security_headers_and_server_banner(srv):
    port = srv.server_address[1]
    st, headers, _ = req(port, "GET", "/api/scenarios")
    h = {k.lower(): v for k, v in headers}
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options") == "SAMEORIGIN"
    assert "python" not in h.get("server", "").lower()
