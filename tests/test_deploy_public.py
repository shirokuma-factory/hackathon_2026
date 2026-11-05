"""公開版（Cloud Run。SPEC §15.1「公開版」、docs/DEPLOY.md）の試験。

--public のポート・Host・OSRM に触れないこと・来訪者ごとの状態・gzip・アップロードに入れないものを確かめる。
"""

from __future__ import annotations

import fnmatch
import gzip
import http.client
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_server():
    spec = importlib.util.spec_from_file_location("serve_web_public", ROOT / "tools" / "serve_web.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["serve_web_public"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_server()


@pytest.fixture(scope="module")
def pub(mod):
    httpd = mod.make_server(0, public=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def req(port, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = dict(headers or {})
    if host:
        h["Host"] = host
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=h)
    r = conn.getresponse()
    raw = r.read()
    out = (r.status, dict(r.getheaders()), raw)
    conn.close()
    return out


# ---------------------------------------------------------------- ポートと待ち受け

def test_resolve_port(mod):
    assert mod.resolve_port(False, None, {"PORT": "9999"}) == mod.DEFAULT_PORT  # 手元の版は PORT を見ない
    assert mod.resolve_port(True, None, {"PORT": "9999"}) == 9999
    assert mod.resolve_port(True, None, {}) == 8080
    assert mod.resolve_port(True, None, {"PORT": "abc"}) == 8080
    assert mod.resolve_port(True, 7000, {"PORT": "9999"}) == 7000  # --port が優先


def test_public_binds_all_interfaces_and_local_binds_loopback(mod, pub):
    assert pub.server_address[0] == "0.0.0.0"
    local = mod.make_server(0)
    try:
        assert local.server_address[0] == "127.0.0.1"
        assert local.public is False
    finally:
        local.server_close()


def test_env_public(mod, monkeypatch):
    monkeypatch.setenv("SHIPCOMPARE_PUBLIC", "1")
    assert mod.env_public() is True
    monkeypatch.delenv("SHIPCOMPARE_PUBLIC")
    assert mod.env_public() is False


def test_host_check_skipped_only_in_public(mod, pub):
    st, _, _ = req(pub.server_address[1], "GET", "/api/scenarios", host="backfull-abc.a.run.app")
    assert st == 200
    local = mod.make_server(0)
    t = threading.Thread(target=local.serve_forever, daemon=True)
    t.start()
    try:
        st, _, _ = req(local.server_address[1], "GET", "/api/scenarios", host="evil.example.com")
        assert st == 400
    finally:
        local.shutdown()
        local.server_close()


# ---------------------------------------------------------------- /api/route は通信も書き込みもしない

def test_route_public_never_calls_network_or_writes(mod, pub, monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("公開版が OSRM に問い合わせた")

    monkeypatch.setattr(mod.route_lib._ON_DEMAND_CLIENT, "route", boom)
    monkeypatch.setattr(mod, "ROUTE_BASE", tmp_path)  # cache が空の場所
    st, _, raw = req(pub.server_address[1], "GET", "/api/route?from=13104&to=27128")
    body = json.loads(raw)
    assert st == 200
    assert body["fallback"] is True and body["ok"] is False and body["message"] == "経路を取得できませんでした"
    assert list(tmp_path.rglob("*")) == []  # 何も書かない


def test_route_public_serves_existing_cache(mod, pub, monkeypatch, tmp_path):
    points = mod.geo.load_points()
    o, d = points["13104"], points["27128"]
    origin, dest = (o["lon"], o["lat"]), (d["lon"], d["lat"])
    cdir = mod.route_lib.cache_dir(tmp_path)
    cdir.mkdir(parents=True)
    (cdir / mod.route_lib._cache_name(origin, dest)).write_text(
        json.dumps({"km": 10.0, "minutes": 20.0, "coordinates": [list(origin), list(dest)]}), encoding="utf-8")
    monkeypatch.setattr(mod.route_lib._ON_DEMAND_CLIENT, "route",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("通信した")))
    monkeypatch.setattr(mod, "ROUTE_BASE", tmp_path)
    st, _, raw = req(pub.server_address[1], "GET", "/api/route?from=13104&to=27128")
    body = json.loads(raw)
    assert st == 200 and body["ok"] is True and body["cached"] is True and body["km"] == 10.0


# ---------------------------------------------------------------- /api/b の来訪者ごとの状態

B_SHIPMENT = {"shipment": {"items": [{"count": 1, "length_cm": 30, "width_cm": 30, "height_cm": 30, "weight_kg": 5}]},
              "dest_code": "28110"}


def _holds(port, sid):
    h = {"X-Demo-Session": sid} if sid else {}
    st, _, raw = req(port, "GET", "/api/b/trips", headers=h)
    assert st == 200
    return json.loads(raw)


def test_b_state_isolated_per_session(pub):
    port = pub.server_address[1]
    a, b = "session-aaaaaaaa", "session-bbbbbbbb"
    trips = _holds(port, a)["trips"]
    trip_id = next(t["id"] for t in trips if t["dest_code"] == "28110")
    st, _, raw = req(port, "POST", "/api/b/hold", {**B_SHIPMENT, "trip_id": trip_id}, {"X-Demo-Session": a})
    assert st == 200, raw
    assert len(_holds(port, a)["holds"]) == 1
    assert _holds(port, b)["holds"] == []
    # 形の違う ID と ID なしは共通の "anon" になり、a の状態は見えない
    assert _holds(port, "x")["holds"] == []
    assert _holds(port, None)["holds"] == []
    st, _, _ = req(port, "POST", "/api/b/hold", {**B_SHIPMENT, "trip_id": trip_id}, {"X-Demo-Session": "bad id!"})
    assert st == 200
    assert len(_holds(port, None)["holds"]) == 1  # anon に積まれた
    # a の reset は b と anon に響かない
    req(port, "POST", "/api/b/reset", {}, {"X-Demo-Session": a})
    assert _holds(port, a)["holds"] == []
    assert len(_holds(port, None)["holds"]) == 1


def test_board_store_lru_and_ttl(mod):
    now = [0.0]
    store = mod.BoardStore(max_items=3, ttl=100, clock=lambda: now[0])
    first = store.get("a")
    for k in "bcd":
        store.get(k)
    assert len(store) == 3
    assert store.get("a") is not first  # a は押し出されて作り直し
    b = store.get("b")
    now[0] = 50
    assert store.get("b") is b  # 使い続ければ残る
    now[0] = 200
    assert store.get("b") is not b  # 100 秒使われなければ捨てる


def test_local_mode_keeps_single_shared_board(mod):
    mod._CTX.public = False
    mod._CTX.sid = "whatever-1234"
    assert mod._board() is mod.BOARD_B


# ---------------------------------------------------------------- 静的ファイル

def test_gzip_json_and_cache_headers(pub):
    port = pub.server_address[1]
    st, h, raw = req(port, "GET", "/data/meta.json", headers={"Accept-Encoding": "gzip"})
    assert st == 200 and h["Content-Encoding"] == "gzip"
    assert h["Cache-Control"] == "public, max-age=3600"
    assert json.loads(gzip.decompress(raw)) == json.loads((ROOT / "web" / "data" / "meta.json").read_bytes())
    st, h, raw = req(port, "GET", "/data/meta.json")  # gzip を受けない相手には素のまま
    assert st == 200 and "Content-Encoding" not in h
    assert json.loads(raw)


def test_html_js_are_no_cache_and_etag_304(pub):
    port = pub.server_address[1]
    for path in ("/index.html", "/", "/work.js"):
        st, h, _ = req(port, "GET", path, headers={"Accept-Encoding": "gzip"})
        assert st == 200 and h["Cache-Control"] == "no-cache", path
    st, h, _ = req(port, "GET", "/work.js")
    st2, _, body = req(port, "GET", "/work.js", headers={"If-None-Match": h["ETag"]})
    assert st2 == 304 and body == b""


def test_local_data_not_served_in_public(pub):
    st, _, _ = req(pub.server_address[1], "GET", "/data/local/p31.json")
    assert st == 404


def test_path_traversal_blocked(pub):
    st, _, _ = req(pub.server_address[1], "GET", "/..%2f..%2fpyproject.toml")
    assert st == 404


def test_hide_local_env_in_local_mode(mod, monkeypatch):
    monkeypatch.setenv("SHIPCOMPARE_HIDE_LOCAL", "1")
    httpd = mod.make_server(0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        assert req(httpd.server_address[1], "GET", "/data/local/p31.json")[0] == 404
        assert req(httpd.server_address[1], "GET", "/index.html")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------- アップロードの除外

def _patterns(name: str) -> list[str]:
    return [ln.strip() for ln in (ROOT / name).read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def ignored(rel: str, patterns: list[str]) -> bool:
    """.gitignore に近い単純な判定（この試験で使う書き方だけ）。後ろの規則が勝つ。"""
    parts = rel.split("/")
    # 親のフォルダが除外されていれば、中のファイルは「!」で戻せない（gitignore と同じ）
    for n in range(1, len(parts)):
        if _hit("/".join(parts[:n]), patterns, is_dir=True):
            return True
    return _hit(rel, patterns, is_dir=False)


def _match_segments(parts: list[str], pat: str, anywhere: bool) -> bool:
    segs = pat.split("/")
    starts = range(len(parts) - len(segs) + 1) if anywhere else range(1)
    return any(len(parts) >= i + len(segs) and all(
        fnmatch.fnmatchcase(parts[i + k], s) for k, s in enumerate(segs)) and len(parts) == i + len(segs)
        for i in starts)


def _hit(rel: str, patterns: list[str], is_dir: bool) -> bool:
    parts = rel.split("/")
    result = False
    for pat in patterns:
        neg = pat.startswith("!")
        p = pat[1:] if neg else pat
        dir_only = p.endswith("/")
        p = p.rstrip("/")
        if dir_only and not is_dir:
            continue
        # 「/」を含まない規則はどの深さにも、含む規則はルートから
        if _match_segments(parts, p, anywhere="/" not in p):
            result = not neg
    return result


def upload_files() -> list[Path]:
    """.gcloudignore を適用したときにアップロードされるファイル（.git などは最初から除く）。"""
    pats = _patterns(".gcloudignore")
    out = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(".git/") or "/__pycache__/" in rel or "/.venv/" in rel or rel.startswith(".venv/"):
            continue
        if not ignored(rel, pats):
            out.append(p)
    return out


def test_ignore_files_are_identical():
    assert _patterns(".dockerignore") == _patterns(".gcloudignore")


@pytest.mark.parametrize("rel", [
    "web/data/local/p31.json", "data/raw/p31/x.zip", "outputs/a.png", ".env", ".env.local", ".claude/settings.json",
    "docs/DEPLOY.md", "assets3d/a.blend", "tests/conftest.py", "web/perf1.cpuprofile", "tools/x.cpuprofile",
    "data/stats/muni_estimate.json", "data/geo/municipalities_N03-21_s0010.geojson",
])
def test_not_uploaded(rel):
    for name in (".gcloudignore", ".dockerignore"):
        assert ignored(rel, _patterns(name)), (name, rel)


@pytest.mark.parametrize("rel", [
    "web/index.html", "web/data/meta.json", "web/models/truck.glb", "src/shipcompare/spaceb.py", "tools/serve_web.py",
    "data/geo/municipality_points.json", "data/geo/osrm_13104.json", "data/geo/routes.json",
    "data/tariffs/routes.json", "data/scenarios/S1.json", "data/demo/carriers.json", "data/contacts.json",
    "data/transit_days.json", "data/guide/other_modes.json", "data/routes/cache/a.json", "Dockerfile", ".env.example",
])
def test_uploaded(rel):
    assert not ignored(rel, _patterns(".gcloudignore")), rel


def test_upload_set_has_no_forbidden_files():
    for p in upload_files():
        rel = p.relative_to(ROOT).as_posix()
        assert not rel.startswith(("web/data/local/", "data/raw/", "outputs/", ".claude/", "docs/", "assets3d/", "tests/")), rel
        assert not rel.endswith(".cpuprofile"), rel
        assert not (rel == ".env" or rel.startswith(".env.") and rel != ".env.example"), rel


def test_dockerfile_is_stdlib_only():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.13-slim" in text and "--public" in text and "pip install" not in text
    assert "streamlit" not in (ROOT / "tools" / "serve_web.py").read_text(encoding="utf-8")
