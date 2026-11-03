"""SPEC §17.2 の見せ方の試験: 画面用の経路データ（tools/build_web_data.py の出力）と /api/route（通信は差し替え）。

画面の描画そのものは 1920x1080 の写真で確かめる（docs/assets/2026-11-04_*.jpg）。ここでは、画面が読むデータの条件を機械で確かめる。
"""

from __future__ import annotations

import colorsys
import http.client
import importlib.util
import json
import re
import sys
import threading
import urllib.error
from pathlib import Path

import pytest

from shipcompare import routes as route_lib

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bwd():
    return _load("build_web_data", ROOT / "tools" / "build_web_data.py")


@pytest.fixture(scope="module")
def built(bwd, tmp_path_factory):
    """routes_* だけを一時の場所に作る（web/data は触らない）。"""
    out = tmp_path_factory.mktemp("webdata")
    old = bwd.OUT
    bwd.OUT = out
    try:
        bwd.build_real_routes()
    finally:
        bwd.OUT = old
    return out


def _read(p: Path):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _all_legs(doc: dict):
    """routes_<出発地> / routes_pairs / routes_trips のすべての区間を (場所, 区間) で返す。"""
    items = doc["dests"].items() if "dests" in doc else doc.items()
    for key, d in items:
        if "m" in d:
            for mode, m in d["m"].items():
                for leg in (m or {}).get("legs", []):
                    yield f"{key}/{mode}", leg
        elif "truck" in d:
            for leg in (d["truck"] or {}).get("legs", []):
                yield f"{key}/truck", leg


# ---------------------------------------------------------------- データ

def test_spec17_view_data_files_exist_and_are_compact(built):
    names = {p.name for p in built.iterdir()}
    assert {"routes_meta.json", "routes_pairs.json", "routes_trips.json", "routes_rail.json"} <= names
    assert sum(1 for n in names if re.fullmatch(r"routes_\d{5}\.json", n)) == 7  # 関東 7 出発地
    for p in built.iterdir():
        assert p.stat().st_size < 1_000_000, f"{p.name} が大きすぎます（ブラウザへ送るので 1MB 未満）"


def test_spec17_view_no_two_point_legs(bwd, built):
    """どの手段の線も直線の 2 点だけでできていない（G42）。座標は差分の整数で、3 点以上に戻せる。"""
    checked = 0
    for p in sorted(built.glob("routes_*.json")):
        if p.name in ("routes_meta.json", "routes_rail.json"):
            continue
        for where, leg in _all_legs(_read(p)):
            pts = bwd.dec_line(leg["c"])
            assert len(pts) >= 3, f"{p.name} {where} が {len(pts)} 点の線です"
            assert leg["m"] in {"t", "r", "f", "a"}
            checked += 1
    assert checked > 1000
    for line in _read(built / "routes_rail.json")["lines"]:
        assert len(line) >= 4  # 2 点以上（x, y の組）


def test_spec17_view_encoding_roundtrip(bwd):
    src = [[139.7091, 35.7009], [139.7105, 35.701], [130.3787, 33.5692], [-0.0001, 0.0]]
    back = bwd.dec_line(bwd.enc_line(src))
    assert all(abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9 for a, b in zip(src, back))


def test_spec17_view_modes_and_legs_follow_the_source(built):
    d = _read(built / "routes_13104.json")
    assert len(d["dests"]) == 47
    fk = d["dests"]["福岡県"]
    assert set(fk["m"]) == {"truck", "rail", "ferry", "air"}
    assert [l["m"] for l in fk["m"]["truck"]["legs"]] == ["t"]
    assert [l["m"] for l in fk["m"]["rail"]["legs"]] == ["t", "r", "t"]
    assert [l["m"] for l in fk["m"]["air"]["legs"]] == ["t", "a", "t"]
    for mode in ("rail", "ferry", "air"):  # 非トラック区間は所要時間を持たない
        for leg in fk["m"][mode]["legs"]:
            if leg["m"] != "t":
                assert "min" not in leg
    assert fk["m"]["rail"]["hubs"] and fk["m"]["air"]["hubs"]


def test_spec17_view_trips_t1_to_t6_are_on_roads(bwd, built):
    t = _read(built / "routes_trips.json")
    assert sorted(t) == ["T1", "T2", "T3", "T4", "T5", "T6"]
    for tid, v in t.items():
        legs = v["truck"]["legs"]
        assert len(legs) == 1 and len(bwd.dec_line(legs[0]["c"])) > 20, tid


def test_spec17_view_pairs_keyed_by_prefecture_names(built):
    p = _read(built / "routes_pairs.json")
    assert "兵庫県>東京都" in p
    assert p["兵庫県>東京都"]["m"]["truck"]["legs"]


# ---------------------------------------------------------------- 色

def _hue_sat(rgb):
    h, s, _v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    return h * 360, s


def test_spec17_view_mode_colors_have_no_red(bwd):
    colors = bwd.MODE_COLORS
    assert set(colors) == {"truck", "rail", "ferry", "air"}
    for mode, rgb in colors.items():
        h, s = _hue_sat(rgb)
        assert not ((h >= 335 or h <= 12) and s > 0.3), f"{mode} {rgb} は赤に近いです（色相 {h:.0f}）"
    # 橙・緑・水色・薄紫
    assert 20 <= _hue_sat(colors["truck"])[0] <= 45
    assert 100 <= _hue_sat(colors["rail"])[0] <= 160
    assert 180 <= _hue_sat(colors["ferry"])[0] <= 210
    h, s = _hue_sat(colors["air"])
    assert 250 <= h <= 285 and s < 0.35  # 白に近い薄紫
    assert len({tuple(c) for c in colors.values()}) == 4


def test_spec17_view_colors_are_the_same_in_data_and_screen(bwd, built):
    meta = _read(built / "routes_meta.json")
    assert meta["colors"] == bwd.MODE_COLORS
    js = (WEB / "routes.js").read_text(encoding="utf-8")
    m = re.search(r"const DEF_COLORS = (\{[^}]*\})", js)
    assert m, "web/routes.js に DEF_COLORS がありません"
    pairs = {k: [int(a), int(b), int(c)] for k, a, b, c in re.findall(r"(\w+): \[(\d+), (\d+), (\d+)\]", m.group(1))}
    assert pairs == bwd.MODE_COLORS


def test_spec17_view_attribution_is_always_on_screen():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    mk = (WEB / "market.js").read_text(encoding="utf-8")
    for js in (app, mk):
        assert "国土数値情報（鉄道データ）（国土交通省）を加工して作成" in js
        assert "ODbL" in js


# ---------------------------------------------------------------- /api/route（通信は差し替え）

class _Resp:
    def __init__(self, obj):
        self._b = json.dumps(obj).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _osrm_ok():
    coords = [[139.709, 35.701], [139.0, 35.2], [136.0, 34.9], [130.43, 33.654]]
    return {"code": "Ok", "routes": [{"distance": 1085700.0, "duration": 50700.0, "geometry": {"type": "LineString", "coordinates": coords}}]}


@pytest.fixture()
def api(tmp_path, monkeypatch):
    mod = _load("serve_web_realview", ROOT / "tools" / "serve_web.py")
    calls = {"n": 0, "fail": False}

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        if calls["fail"]:
            raise urllib.error.URLError("接続できません（試験）")
        return _Resp(_osrm_ok())

    client = route_lib.OsrmClient(min_interval=0.0, retries=1, retry_wait=0.0, sleep=lambda s: None, urlopen=fake_urlopen)
    monkeypatch.setattr(route_lib, "_ON_DEMAND_CLIENT", client)
    monkeypatch.setattr(mod, "ROUTE_BASE", tmp_path)
    httpd = mod.make_server(0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd.server_address[1], calls, tmp_path
    httpd.shutdown()
    httpd.server_close()


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request("GET", path)
    r = conn.getresponse()
    body = json.loads(r.read().decode("utf-8"))
    conn.close()
    return r.status, body


def test_spec17_view_api_route_returns_road_and_caches(api):
    port, calls, base = api
    st, j = _get(port, "/api/route?from=13104&to=40131")
    assert st == 200 and j["ok"] is True and j["fallback"] is False and j["message"] is None
    assert j["km"] == pytest.approx(1085.7) and j["minutes"] == pytest.approx(845.0)
    assert len(j["coordinates"]) >= 3 and j["cached"] is False
    st, j2 = _get(port, "/api/route?from=13104&to=40131")  # 同じ組は二度取らない
    assert j2["cached"] is True and calls["n"] == 1
    assert list((base / "routes" / "cache").glob("*.json"))


def test_spec17_view_api_route_from_point_uses_given_lonlat(api):
    port, calls, _ = api
    st, j = _get(port, "/api/route?to=40131&from_ll=139.7,35.7")
    assert st == 200 and j["ok"] is True and calls["n"] == 1


def test_spec17_view_api_route_failure_is_flagged_fallback_arc(api):
    port, calls, _ = api
    calls["fail"] = True
    st, j = _get(port, "/api/route?from=13104&to=40131")
    assert st == 200
    assert j["ok"] is False and j["fallback"] is True and j["message"] == "経路を取得できませんでした"
    assert len(j["coordinates"]) > 10 and j["minutes"] is None  # 直線の弧（取得に失敗した旨が画面に出る）
    assert j["km"] > 800  # 大圏の弧の長さ


@pytest.mark.parametrize("qs", ["", "?to=99999&from=13104", "?to=40131&from=00000", "?to=40131", "?to=40131&from_ll=abc",
                                "?to=40131&from_ll=10,10", "?to=13104&from=13104"])
def test_spec17_view_api_route_bad_input_is_400(api, qs):
    port, calls, _ = api
    st, j = _get(port, "/api/route" + qs)
    assert st == 400 and "error" in j and calls["n"] == 0
