"""SPEC §31.3（鉄道・船の比べへの入口）と §31.5 #5・#7・#8・#10・#11・#12・#14（受入条件 G139・G141 の一部）。

- 静的な確認（文字列）: 入口の条件、見出しの期間、折り返しの CSS など。
- 画面の確認: tools/serve_web.py --public とヘッドレス Edge で、兵庫県→東京都の入口、余白、距離のそろい、
  経路が取れない着地の最後の区間、狭い画面の先頭位置を確かめる。Edge が無い環境では飛ばす。
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
WEB = ROOT / "web"

REASON = "発地に選べるのは関東の 7 か所だけなので、この区間は送り方の比べに移れません。"


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 静的な確認
def test_g139_clo_entry_rule_is_in_market_js():
    mk = read("market.js")
    assert "const CLO_KM = 400;" in mk
    assert "function cloTarget(" in mk
    # 発地に選べる県の端があるときだけ入口を出す。それ以外は理由の文を常に出す（トーストだけにしない）
    assert REASON in mk
    assert "toast(note)" not in mk
    assert "鉄道・船を含めて送り方を比べる</span><span class=\"mp-go-sub\">発地 ${esc(clo.oName)} → 着地 ${esc(clo.dName)}" in mk


def test_g141_5_traffic_heading_shows_the_data_period_not_this_week():
    mk, html = read("market.js"), read("index.html")
    assert "今週" not in mk and "今週" not in html
    assert "weekText()" in mk and 'id="mlg-jartic-t"' in html
    p = json.loads((ROOT / "data" / "traffic" / "heavy_updown.json").read_text(encoding="utf-8"))["period"]
    assert (p["start"], p["end"], p["hours"]) == ("2026-10-27", "2026-11-02", 168)  # 見出しは「10/27〜11/2 の 1 週間」になる
    web_p = json.loads((WEB / "data" / "jartic.json").read_text(encoding="utf-8"))["period"]
    assert web_p == p


def test_g141_8_fit_padding_uses_the_real_size_of_the_summary_and_the_right_column():
    mk = read("market.js")
    body = mk[mk.index("const padding = () => {"):mk.index("function fitPair")]
    assert "#m-summary" in body and "#layers" in body and "#m-right" in body
    assert "getClientRects" in body


def test_g141_10_11_routes_js_uses_the_road_km_and_the_capital_tail():
    rt = read("routes.js")
    assert "県庁所在地の着地: 上の欄（詳細パネル）に道路距離があるときは、トラックのカードも同じ値にする" in rt
    assert "（県庁所在地）まで" in rt and "function capitalName(" in rt


def test_g141_12_days_text_wraps_and_is_not_clipped():
    css, js = read("compare.css"), read("compare_chart.js")
    row = re.search(r"\.cc-days \{[^}]*\}", css).group(0)
    assert "text-overflow" not in row and "white-space: normal" in row and "overflow: hidden" not in row
    assert "まで翌日、超は翌々日" in js


def test_g141_7_find_view_height_stops_above_the_notes():
    css, app = read("style.css"), read("app.js")
    line = next(x for x in css.splitlines() if x.startswith("#find-view {"))
    assert "var(--fn-h" in line
    assert "--fn-h" in app and "ResizeObserver" in app


def test_g141_14_narrow_select_scrolls_the_sheet_to_the_top():
    mk = read("market.js")
    assert "Sheet.narrow()" in mk and "p.scrollTop = 0" in mk


# ---------------------------------------------------------------- 画面（Edge）
@contextlib.contextmanager
def _browser(width: int, height: int):
    import capture_screens as cs
    try:
        edge = cs.find_edge()
    except (Exception, SystemExit):  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")
    port, dbg = cs.free_port(), cs.free_port()
    server = subprocess.Popen([sys.executable, str(ROOT / "tools" / "serve_web.py"), "--public", "--port", str(port)], cwd=str(ROOT),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    profile = tempfile.mkdtemp(prefix="edge_profile_v31mr_")
    proc = subprocess.Popen(
        [edge, "--headless=new", f"--remote-debugging-port={dbg}", f"--window-size={width},{height}", f"--user-data-dir={profile}", "--hide-scrollbars",
         "--force-device-scale-factor=1", "--no-first-run", "--disable-extensions", "--disable-sync", "--enable-webgl", "--ignore-gpu-blocklist",
         "--enable-unsafe-swiftshader", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        targets = []
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{dbg}/json/list", timeout=2) as r:
                    targets = [t for t in json.load(r) if t.get("type") == "page" and t.get("url") == "about:blank"]
                if targets:
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        if not targets:
            pytest.skip("Edge のページに接続できませんでした")
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=2)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        page = cs.Page(targets[0]["webSocketDebuggerUrl"])
        page.send("Page.addScriptToEvaluateOnNewDocument", source="window.__errs=[];window.addEventListener('error',e=>window.__errs.push(e.message));"
                  "window.addEventListener('unhandledrejection',e=>window.__errs.push(String(e.reason)));")
        page.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)

        def open_(hash_: str) -> None:
            page.send("Page.navigate", url="about:blank")
            time.sleep(0.4)
            page.send("Page.navigate", url=f"http://127.0.0.1:{port}/index.html{hash_}")
            try:
                page.wait(lambda: page.js("!!window.__app && !document.querySelector('#loading')"), 180, "起動")
            except Exception as e:  # noqa: BLE001
                pytest.skip(f"地図の画面が起動しません（地図の部品を取得できない環境）: {e}")
            time.sleep(2.5)

        yield page, open_
    finally:
        cs.stop_tree(proc)
        cs.stop_edge_profile(profile)
        cs.stop_tree(server)
        shutil.rmtree(profile, ignore_errors=True)


ENDS_JS = """(function(){const M=__app.Market.state, r=M.rows.find(x=>x.k==='27-12'), m=__app.map();
  const pt=[M.d.cap[r.fi], M.d.cap[r.ti]].map(c=>m.project(c));
  return JSON.stringify({pts:pt.map(p=>[p.x,p.y]), layers:document.querySelector('#layers').getBoundingClientRect().left,
    sum:document.querySelector('#m-summary').getBoundingClientRect().bottom, rt:document.querySelector('#m-right').getBoundingClientRect().left});})()"""


def test_g139_hyogo_tokyo_has_the_entry_and_the_compare_uses_the_two_prefectures():
    with _browser(1920, 1080) as (page, open_):
        open_("#v=market&p=27-12")
        label = page.js("(function(){const b=document.querySelector('#m-pair [data-act=clo]');return b?b.textContent:null})()")
        assert label == "鉄道・船を含めて送り方を比べる発地 新宿区 → 着地 神戸市中央区"
        # 区間の詳細に大型車の折れ線は出さない（§32 #2）。層の凡例にはデータの期間が入る（§31 #5）
        assert not page.js("document.querySelector('#m-pair .mp-j')")
        assert "10/27〜11/2 の 1 週間" in page.js("document.querySelector('#mlg-jartic-t').textContent")
        # 区間の両端が、上の要約と右の切り替えの列に隠れない（#8）。地図の移動が終わるのを待つ
        time.sleep(4)
        ends = json.loads(page.js(ENDS_JS))
        for x, y in ends["pts"]:
            assert x < min(ends["layers"], ends["rt"]) and y > ends["sum"], ends
        page.js("document.querySelector('#m-pair [data-act=clo]').click()")
        page.wait(lambda: "v=compare" in page.js("location.hash"), 30, "比べるへ移る")
        h = page.js("location.hash")
        assert "o=13104" in h and "d=28110" in h  # 発地 新宿区、着地 兵庫県の県庁所在地（神戸市中央区）
        assert not page.js("window.__errs.length")


def test_g139_pair_without_kanto_end_shows_the_reason_always():
    with _browser(1920, 1080) as (page, open_):
        open_("#v=market")
        k = page.js("(function(){const M=__app.Market.state; const K=['茨城県','栃木県','群馬県','埼玉県','千葉県','東京都','神奈川県'];"
                    "const r=M.rows.find(x=>x.km>=400&&!K.includes(M.d.prefs[x.fi])&&!K.includes(M.d.prefs[x.ti])); return r?r.k:null;})()")
        assert k, "関東を含まない 400km 以上の区間が上位にありません"
        page.js(f"__app.Market.select({json.dumps(k)})")
        page.wait(lambda: page.js("!document.querySelector('#m-pair').hidden"), 20, "区間の詳細")
        assert not page.js("!!document.querySelector('#m-pair [data-act=clo]')")
        assert REASON in page.js("document.querySelector('#m-pair .mp-clo-why').textContent")


def test_g141_10_12_capital_distance_matches_and_days_are_not_clipped():
    with _browser(1920, 1080) as (page, open_):
        open_("#v=compare&o=13104&l=km&d=27128")  # 新宿区→大阪市中央区（県庁所在地）
        page.wait(lambda: page.js("!!document.querySelector('.rt-card[data-mode=truck]') && !!document.querySelector('.cc')"), 60, "カード")
        top = page.js("document.querySelector('#s-km').textContent.replace(/[^0-9.]/g,'')")
        card = page.js("document.querySelector('.rt-card[data-mode=truck] .rt-km').textContent.replace(/[^0-9.]/g,'')")
        assert top and top == card, (top, card)
        clip = page.js("[...document.querySelectorAll('.cc-days')].some(e=>e.scrollWidth>e.clientWidth+1||e.scrollHeight>e.clientHeight+2)")
        assert clip is False
        assert "900kmまで翌日、超は翌々日" in page.js("document.querySelector('.cc').textContent")
        assert not page.js("window.__errs.length")


def test_g141_11_fallback_destination_says_the_tail_stops_at_the_capital():
    with _browser(1920, 1080) as (page, open_):
        open_("#v=compare&o=13104&l=km&d=27205")  # 吹田市。公開版では cache に無く、経路が取れない
        page.wait(lambda: "経路を取得できませんでした" in page.js("document.querySelector('#rt-modes').textContent"), 90, "経路の取得の終わり")
        t = page.js("document.querySelector('#rt-modes').textContent")
        assert "大阪市中央区（県庁所在地）まで" in t
        assert "吹田市" in page.js("document.querySelector('.rt-card[data-mode=truck] .rt-legs').textContent")
        assert not page.js("window.__errs.length")


def test_g141_7_carrier_role_left_panel_does_not_overlap_the_legend():
    with _browser(1920, 1080) as (page, open_):
        open_("#v=market&p=27-12")
        page.js("document.querySelector('#m-pair [data-act=carrier]').click()")
        page.wait(lambda: page.js("document.body.classList.contains('view-find')"), 30, "運送会社の役")
        time.sleep(1.5)
        r = json.loads(page.js("JSON.stringify([document.querySelector('#find-view'),document.querySelector('#find-notes')].map(e=>{const b=e.getBoundingClientRect();return [b.top,b.bottom]}))"))
        assert r[0][1] <= r[1][0], r


def test_g141_14_narrow_corridor_opens_with_the_name_and_numbers_visible():
    with _browser(400, 860) as (page, open_):
        open_("#v=market&p=27-12")
        time.sleep(1)
        assert page.js("document.querySelector('#m-right').scrollTop") == 0
        top = page.js("document.querySelector('.mp-title').getBoundingClientRect().top")
        sheet = page.js("document.querySelector('#m-right').getBoundingClientRect().top")
        assert sheet <= top < page.js("innerHeight")
        assert not page.js("window.__errs.length")
