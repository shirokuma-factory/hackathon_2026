"""SPEC §31.1・§31.5 #6・#15 地図の荷待ちのタブをブラウザの中だけで計算する（受入条件 G137、G141 の #6・#15）。

- 静的な確認: own.js が /api/waiting を呼ばず、WaitingShared.calc を使うこと。告示の表の静的ファイルが元のファイルと同じこと。
- 画面の確認（ヘッドレス Edge + tools/serve_web.py）: 例を読み込んで表を出すまでに /api/waiting へのリクエストが 0 件、数字は G132 と同じ。
  点の平均が場所ごと・月ごとの表と同じ。400px で点が地図の端と出典の文字に重ならない。
  Edge が無い環境だけ飛ばす。Edge があるのに起動・接続・読み込みが時間切れになったときは失敗にし、画面写真と window.__errs を残す（SPEC §38 TQ-6）。
"""

from __future__ import annotations

import json
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
OWN_JS = (WEB / "own.js").read_text(encoding="utf-8")
MAP_JS = (WEB / "waiting_map.js").read_text(encoding="utf-8")
SHARED = (WEB / "waiting_calc.js").read_text(encoding="utf-8")
TARIFF = ROOT / "data" / "tariffs" / "mlit_standard_fare_waiting.json"

# fetch と XMLHttpRequest の呼び出し先を記録する（ページの読み込み前に入れる）
NET_HOOK = """
window.__reqs = [];
(function(){
  const f = window.fetch;
  window.fetch = function(u){ try { window.__reqs.push(String(u && u.url ? u.url : u)); } catch (e) {} return f.apply(this, arguments); };
  const o = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(m, u){ try { window.__reqs.push(String(u)); } catch (e) {} return o.apply(this, arguments); };
})();
"""

PASTE_JS = """(function(){const t=document.querySelector('#w-paste');
  t.value=['日付\\t車種\\t契約の相手\\t場所\\t到着\\t荷役開始','2026-10-21\\t中型\\t架空 貼り付け\\t江東区 地点07\\t09:00\\t10:00'].join('\\n');
  document.querySelector('[data-w=paste]').click();})()"""


def test_own_js_computes_in_the_browser_and_never_names_the_waiting_api():
    code = OWN_JS.replace("サーバーの /api/waiting は呼ばない", "")
    assert "/api/waiting" not in code and "/api/waiting" not in MAP_JS.replace("サーバーには送らない", "")
    assert "WaitingShared.calc" in OWN_JS and "WaitingShared.parsePasteFull" in OWN_JS
    assert "function calcRow(" in SHARED and "function parsePasteFull(" in SHARED


def test_static_tariff_for_the_map_matches_the_source_file():
    out = json.loads((WEB / "data" / "waiting_tariff.json").read_text(encoding="utf-8"))
    src = json.loads(TARIFF.read_text(encoding="utf-8"))
    # 照合の記録だけ、リポジトリの中のファイル名を外している（SPEC §31.5 #17）。値はすべて元の表と同じ
    assert "docs/" not in out["verified_by"]
    assert {k: v for k, v in out.items() if k != "verified_by"} == {k: v for k, v in src.items() if k != "verified_by"}


def test_point_average_uses_the_report_definition_in_the_script():
    assert "r.report_wait_min" in MAP_JS
    assert "国への定期報告と同じ数え方" in MAP_JS


def _edge_or_skip():
    import capture_screens as cs
    try:
        return cs, cs.find_edge()
    except Exception:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _session(cs, edge, w, h):
    profile = tempfile.mkdtemp(prefix="edge_profile_")
    dbg = cs.free_port()
    proc = subprocess.Popen(
        [edge, "--headless=new", f"--remote-debugging-port={dbg}", f"--window-size={w},{h}", f"--user-data-dir={profile}", "--hide-scrollbars",
         "--force-device-scale-factor=1", "--no-first-run", "--enable-webgl", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader",
         *cs.EDGE_CLEAN_FLAGS, "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, profile, dbg


def _connect(cs, dbg):
    targets = []
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{dbg}/json/list", timeout=2) as r:
                targets = cs.blank_targets(json.load(r))
            if targets:
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    if not targets:
        pytest.fail("Edge のページ（about:blank）に接続できませんでした")
    return cs.Page(targets[0]["webSocketDebuggerUrl"])


def _wait_server(port, timeout=60):
    """index.html が 200 を返すまで待つ。返らなければ失敗（飛ばさない）。"""
    end = time.time() + timeout
    err = None
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=3) as r:
                if r.status == 200:
                    return
        except Exception as e:  # noqa: BLE001
            err = e
        time.sleep(0.5)
    pytest.fail(f"サーバーが {timeout} 秒以内に index.html を返しません: {err}")


def _fail_with_evidence(page, what, exc):
    """時間切れのとき、画面写真と window.__errs を残して失敗にする。"""
    import base64
    shot, errs = "", ""
    try:
        data = page.send("Page.captureScreenshot", format="png").get("data", "")
        out = Path(tempfile.gettempdir()) / f"waiting_local_fail_{int(time.time())}.png"
        out.write_bytes(base64.b64decode(data))
        shot = str(out)
    except Exception as e:  # noqa: BLE001
        shot = f"(画面写真を取れません: {e})"
    try:
        errs = json.dumps(page.js("window.__errs || []"), ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        errs = f"(window.__errs を読めません: {e})"
    pytest.fail(f"{what}: {exc} / 画面写真 {shot} / window.__errs {errs}")


def _open_wait(page, port, w, h):
    import capture_web as cw
    page.send("Page.addScriptToEvaluateOnNewDocument", source=cw.ERR_HOOK)
    page.send("Page.addScriptToEvaluateOnNewDocument", source=NET_HOOK)
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
    page.goto(f"http://localhost:{port}/index.html#v=own&ot=wait")
    try:
        page.wait(lambda: page.js("!!window.__app"), 60, "アプリの起動")
        page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 60, "読み込み")
        page.wait(lambda: page.js("!!document.querySelector('[data-w=example]')"), 60, "例を入れるボタン")
        page.js("document.querySelector('[data-w=example]').click()")
        page.wait(lambda: page.js("!!document.querySelector('#hotspots') && document.querySelectorAll('.wm-pt').length > 0"), 60, "表と点")
    except Exception as e:  # noqa: BLE001
        _fail_with_evidence(page, "地図の画面が起動しません", e)
    time.sleep(3.0)  # 地図の移動が終わるまで


def test_g137_map_wait_tab_sends_nothing_to_the_waiting_api_and_numbers_match_g132():
    cs, edge = _edge_or_skip()
    import capture_web as cw

    port = cs.free_port()
    server = cw.serve(port)
    _wait_server(port)
    proc, profile, dbg = _session(cs, edge, 1920, 1080)
    try:
        time.sleep(1.5)
        page = _connect(cs, dbg)
        _open_wait(page, port, 1920, 1080)
        # 貼り付けも外に送らない
        page.js(PASTE_JS)
        page.wait(lambda: "1 行を表に入れました" in page.js("document.querySelector('#w-paste-msg').textContent"), 20, "貼り付け")
        time.sleep(1.0)
        reqs = page.js("window.__reqs")
        perf = page.js("performance.getEntriesByType('resource').map(e => e.name)")
        assert not [u for u in reqs + perf if "/api/waiting" in u], (reqs, perf)
        assert any("waiting_tariff.json" in u for u in perf)  # 告示の表は静的なファイルから読む
        assert not page.js("window.__errs.length")
        # 例に戻して、G132 と同じ数字を確かめる
        page.js("document.querySelector('[data-w=example]').click()")
        page.wait(lambda: page.js("document.querySelectorAll('#placemonth tbody tr').length") == 3, 20, "例の表")
        amounts = page.js("[...document.querySelectorAll('table.w-par:not(.wm-card) tbody tr')].map(tr => tr.children[4].textContent.replace(/,/g, '')).join(',')")
        assert sum(int(x) for x in amounts.split(",")) == 46_780
        row = page.js("[...document.querySelectorAll('#hotspots tbody tr')].map(tr => tr.textContent.replace(/\\s+/g, ' ')).find(t => t.includes('江東区 地点07') && t.includes('8時台'))")
        assert row and "98 分" in row
        pm = page.js("[...document.querySelectorAll('#placemonth tbody tr')].map(tr => [tr.children[0].textContent, tr.children[3].textContent.replace(/[^0-9]/g, '')])")
        table = {k: v for k, v in pm}
        assert table == {"江東区 地点07": "58", "市川市 地点03": "60", "川崎市 地点12": "32"}
        # #6: 点の中の平均は、場所ごと・月ごとの表と同じ数字。凡例に同じ数え方だと書いてある
        pts = page.js("[...document.querySelectorAll('.wm-pt')].map(e => [e.dataset.place, e.querySelector('.wm-n').textContent])")
        assert {k: v for k, v in pts} == table
        assert "国への定期報告と同じ数え方" in page.js("document.querySelector('.wm-lg').textContent")
        assert not [u for u in page.js("window.__reqs") if "/api/waiting" in u]
    finally:
        cs.stop_tree(proc)
        cs.stop_edge_profile(profile)
        cs.stop_tree(server)
        shutil.rmtree(profile, ignore_errors=True)


def test_g141_15_points_stay_inside_the_map_and_clear_of_the_attribution_at_400px():
    cs, edge = _edge_or_skip()
    import capture_web as cw

    port = cs.free_port()
    server = cw.serve(port)
    _wait_server(port)
    proc, profile, dbg = _session(cs, edge, 400, 800)
    try:
        time.sleep(1.5)
        page = _connect(cs, dbg)
        _open_wait(page, port, 400, 800)
        info = page.js("""(function(){
          const at = document.querySelector('.maplibregl-ctrl-attrib').getBoundingClientRect();
          return {at: [at.left, at.top, at.right, at.bottom], w: innerWidth,
            pts: [...document.querySelectorAll('.wm-pt')].map(e => { const r = e.getBoundingClientRect(), t = e.querySelector('.wm-t').getBoundingClientRect(); return [e.dataset.place, Math.min(r.left, t.left), r.top, Math.max(r.right, t.right), Math.max(r.bottom, t.bottom)]; })};})()""")
        assert info["pts"]
        al, at_, ar, ab = info["at"]
        for name, l, t, r, b in info["pts"]:
            assert l >= 8 and r <= info["w"] - 8, (name, l, r)  # 地図の左右の端に寄らない
            assert t >= 8, (name, t)
            overlap = not (r <= al or l >= ar or b <= at_ or t >= ab)
            assert not overlap, (name, [l, t, r, b], info["at"])  # 出典の文字に重ならない
    finally:
        cs.stop_tree(proc)
        cs.stop_edge_profile(profile)
        cs.stop_tree(server)
        shutil.rmtree(profile, ignore_errors=True)
