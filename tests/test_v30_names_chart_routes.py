"""SPEC §30.1（実在の会社名を外す）、§30.5（横棒の図）、§30.6（経路が取れないとき）の試験。受入条件 G129・G133（図の部分）・G134。"""

from __future__ import annotations

import http.client
import importlib.util
import json
import re
import sys
import threading
from pathlib import Path

import pytest

from shipcompare.estimate import ANON_SOURCE_NAME, estimate_all, round_100
from shipcompare.storage import load_scenario, shipment_to_dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
NAMES = re.compile("佐川|日本通運|日通|名鉄|ヤマト|西濃|JITBOX|こぐま")


def _load_server():
    spec = importlib.util.spec_from_file_location("serve_web_v30", ROOT / "tools" / "serve_web.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["serve_web_v30"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pub():
    mod = _load_server()
    httpd = mod.make_server(0, public=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _call(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    hdr, data = {}, None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdr["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=hdr)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    return r.status, raw


# ---------------------------------------------------------------- G129 会社名

def test_labels_and_sources_follow_spec_30_1():
    sc = load_scenario("S1")
    rows = {r.mode: r for r in estimate_all(sc["shipment"])}
    assert rows["takuhai"].label == "宅配便（大手の公表運賃からの試算）"
    assert rows["rosen"].label == "路線便（積合せの公表運賃からの試算）"
    assert rows["jitbox"].label == "カゴ台車 1 本単位（公表運賃からの試算）"
    for m in ("takuhai", "rosen", "jitbox"):
        assert rows[m].source.name == ANON_SOURCE_NAME and not rows[m].source.url
    assert ANON_SOURCE_NAME == "大手の公表運賃をもとにした試算。会社名と各社の表は載せません（各社の利用条件のため）"
    for r in rows.values():
        text = json.dumps([r.label, r.reasons, r.notes, r.missing, [i.message for i in r.issues],
                           r.source.name if r.source else ""], ensure_ascii=False)
        assert not NAMES.search(text), text


def test_round_100():
    assert round_100(12349) == 12300 and round_100(12350) == 12400 and round_100(None) is None


def test_oversize_takuhai_reason_states_limit_without_names():
    from shipcompare.models import Item, Shipment
    sh = Shipment(items=[Item(1, 200, 100, 100, 60)])
    r = next(x for x in estimate_all(sh) if x.mode == "takuhai")
    assert "大手の宅配の上限（3辺合計 260cm・50kg まで）" in "".join(r.reasons) and not NAMES.search("".join(r.reasons))


def test_public_api_estimate_has_no_company_names(pub):
    for sid in ("S1", "S2", "S5", "S6"):
        sh = shipment_to_dict(load_scenario(sid)["shipment"])
        for body in ({"shipment": sh}, {"shipment": sh, "origin_code": "13104", "dest_code": "27128"},
                     {"shipment": sh, "origin_code": "13104", "dest_code": "15202"}):
            st, raw = _call(pub, "POST", "/api/estimate", body)
            assert st == 200
            assert not NAMES.search(raw.decode("utf-8")), NAMES.search(raw.decode("utf-8"))
    st, raw = _call(pub, "POST", "/api/request", {"shipment": sh})
    assert st == 200
    assert not NAMES.search(json.loads(raw)["text"])  # 依頼文には会社名が出ない（窓口の欄 contacts は別）


@pytest.mark.parametrize("path", ["/data/tariffs/sagawa_kanto.json", "/data/tariffs/nittsu_tsumiawase.json",
                                  "/data/tariffs/yamato_jitbox.json", "/data/tariffs/mlit_standard_fare_kanto.json",
                                  "/data/tariffs/", "/tariffs/sagawa_kanto.json"])
def test_tariffs_are_not_served_in_public_mode(pub, path):
    assert _call(pub, "GET", path)[0] == 404


def test_screen_sources_have_no_company_names():
    files = [ROOT / "web" / n for n in ("app.js", "routes.js", "compare_chart.js", "compare.css", "index.html")]
    for p in files:
        assert not NAMES.search(p.read_text(encoding="utf-8")), p.name
    for p in (ROOT / "tools" / "compare_lite_template.html", ROOT / "web" / "compare_lite.html",
              ROOT / "web" / "publish" / "compare_lite_artifact.html"):
        # 窓口の一覧（data/contacts.json を埋め込んだ行）は「見積を頼む先」の欄で、運賃ではないので除く
        text = "\n".join(x for x in p.read_text(encoding="utf-8").splitlines() if not x.startswith("const CONTACTS"))
        assert not NAMES.search(text), (p.name, NAMES.search(text).group(0))
    html = (ROOT / "web" / "compare_lite.html").read_text(encoding="utf-8")
    assert '<a href="index.html">地図で送り方を比べる</a>' in html
    art = (ROOT / "web" / "publish" / "compare_lite_artifact.html").read_text(encoding="utf-8")
    assert "地図で送り方を比べる（" in art


# ---------------------------------------------------------------- G133 横棒の図 / G134 経路

def _edge():
    import capture_screens as cs
    try:
        cs.find_edge()
    except Exception:
        pytest.skip("Edge がありません")
    return cs


def test_chart_html_ratio_and_marks():
    _edge()
    import capture_web as cw
    with cw.edge_page(900, 700) as page:
        page.goto((ROOT / "web" / "compare_lite.html").as_uri() + "?selftest=0")
        page.js((ROOT / "web" / "compare_chart.js").read_text(encoding="utf-8").replace("const CompareChart", "window.CompareChart"))
        out = page.js("""(function(){
          const cards = [
            {label: '宅配', amount: 8400, est: true, status: '公開条件に合う', days: '発送日+1日（午後以降の配達）'},
            {label: '貸切', sub: '4t車', amount: 126100, status: '公開条件に合う', days: '運転だけで1日（積込み・荷待ちを除く）'},
            {label: '路線便', amount: null, status: '使えない'},
            {label: '鉄道', amount: null, status: '計算できない', dim: true}];
          const h = CompareChart.html(cards), d = document.createElement('div'); d.innerHTML = h;
          return {ratio: CompareChart.ratioOf(cards).ratio, text: CompareChart.ratioText(CompareChart.ratioOf(cards)),
            rows: d.querySelectorAll('.cc-row').length, cheap: d.querySelectorAll('.cc-row.cheap').length,
            off: [...d.querySelectorAll('.cc-off')].map((e) => e.textContent), mark: d.querySelector('.cc-mark').textContent,
            est: d.querySelector('.cc-val em').textContent, days: [...d.querySelectorAll('.cc-days')].map((e) => e.textContent),
            one: CompareChart.ratioOf([cards[0], cards[2]])};
        })()""")
    assert round(out["ratio"], 2) == 15.01 and "約 15 倍" in out["text"]
    assert out["rows"] == 4 and out["cheap"] == 1 and out["off"] == ["使えない", "問い合わせ"] and out["mark"] == "最安"
    assert out["est"] == "試算" and out["days"][:2] == ["発送日+1日", "運転のみ 1 日"] and out["one"] is None


def test_routes_js_has_final_failure_message_and_straight_label():
    js = (ROOT / "web" / "routes.js").read_text(encoding="utf-8")
    assert "経路を取得できませんでした（直線の弧で表示しています）" in js
    # 取得が終わったあとに「取得しています」を残さない: ループのあとで note を必ず置き換える
    tail = js[js.index("// すべての取得が終わったあと"):]
    assert "cur.note = " in tail.split("render();")[0] and "直線 " in js
