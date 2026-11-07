"""「いまの大型車」ページ（web/trucks_now.html）の試験。データの形、計算の部分（node）、画面（ヘッドレス Edge）の 3 段。
通信は JARTIC の API にはしない（ページは取得済みの JSON だけを読む）。画面の試験は地図の部品を CDN から読むので、届かなければ飛ばす。"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import edge_page, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web" / "data" / "trucks_now.json"
HTML = ROOT / "web" / "trucks_now.html"
JS = ROOT / "web" / "trucks_now.js"


def _no_nan(name):
    raise ValueError(f"JSON に {name} があります")


@pytest.fixture(scope="module")
def data():
    return json.loads(DATA.read_text(encoding="utf-8"), parse_constant=_no_nan)


# ---------------------------------------------------------------- データの形
def test_data_shape_and_missing_is_null(data):
    assert len(data["hours"]) == 24
    assert data["points"] and data["prefs"]
    for p in data["points"]:
        assert len(p["series"]) == 24
        for v in p["series"]:
            assert v is None or (isinstance(v, (int, float)) and math.isfinite(v) and v >= 0)
    # 欠測は null のまま。最新の時間に値が無い地点数が、集計の「全地点 − 観測できた地点」と一致する
    miss = sum(1 for p in data["points"] if p["series"][-1] is None)
    assert miss == data["national"]["n_points"] - data["national"]["n_observed"]
    assert miss > 0  # 欠測があるデータで確かめている


def test_page_files_keep_attribution_and_do_not_call_the_api():
    js = JS.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    assert "jartic" not in js.lower().replace("fetch_trucks_now", "")  # ブラウザから API を呼ばない
    assert "data/trucks_now.json" in js
    assert "attr2" in html and "<details>" in html  # 免責は閉じた details の中
    assert "attribution" in js


# ---------------------------------------------------------------- 計算の部分（node）
NODE = shutil.which("node")

CORE_SCRIPT = """
const c = require('./web/trucks_now_core.js'); const d = require('./web/data/trucks_now.json');
const s = [10, null, 30, 40];
const alt = Object.assign({}, d, { points: d.points.map((p, i) => i === 0 ? Object.assign({}, p, { series: p.series.map(v => (v || 0) + 1) }) : p) });
const res = {
  exact_null: c.valueAt(s, 1), mid_with_null: c.valueAt(s, 0.5), mid: c.valueAt(s, 2.5), exact: c.valueAt(s, 3),
  zero_kept: c.valueAt([0, 0], 0.5),
  ratio_null: c.changeRatio(null, 5), ratio_null2: c.changeRatio(5, null),
  col_none: c.changeColor(null), col_less: c.changeColor(0.5), col_more: c.changeColor(2), col_same: c.changeColor(1.02),
  has_radius: typeof c.radiusPx,
  bins: c.STATE_BINS.map(b => [b.center, c.stateColor(b.center), c.stateBin(b.center), c.stateBin(b.max - 1e-6), b.rgb]),
  nodata: c.stateColor(null), bin_null: c.stateBin(null),
  idx_zero_ref: c.stateIndex([0, 0, 0], 1), idx_miss: c.stateIndex([5, null, 7], 1), idx_small: c.stateIndex([2, 10], 0), idx_mid: c.stateIndex([50, 100], 0),
  dens: [0, 50, 100, 200, 300, 599, 600, 900, 5000].map(v => c.particleDensity(v)), dens_null: c.particleDensity(null), dens_neg: c.particleDensity(-3),
  speed: [0, 0.5, 1].map(v => c.particleSpeed(v)),
  blend_skip: c.blend([[0, 0.5], [1, 0.5]], [0.2, NaN]), blend_all_miss: c.blend([[0, 1]], [NaN]), blend_w: c.blend([[0, 0.25], [1, 0.75]], [0.2, 0.6]),
  pm: c.pipelineModel(d, 23), pm_mid: c.pipelineModel(d, 9),
  rank: c.rankPrefs(d, 23).rows.map(r => [r.pref, r.v]),
  eff_late: c.effectiveMode('week', 23, 24), eff_early: c.effectiveMode('week', 5, 24), eff_prev: c.effectiveMode('prev', 23, 24),
  spark: c.sparkPath([1, null, 3], 100, 10, 3, 0),
  newer_same: c.isNewer(d, d), newer_diff: c.isNewer(d, Object.assign({}, d, { fetched_at: 'x' })),
  changed: [...c.changedIds(d, alt)].length,
};
console.log(JSON.stringify(res));
"""


@pytest.mark.skipif(NODE is None, reason="node が入っていません")
def test_core_value_color_and_pipeline(data):
    r = subprocess.run([NODE, "-e", CORE_SCRIPT], capture_output=True, text=True, encoding="utf-8", timeout=60, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["exact_null"] is None and out["mid_with_null"] is None
    assert out["mid"] == 35 and out["exact"] == 40
    assert out["zero_kept"] == 0  # 0 台は 0 のまま（欠測と区別する）
    assert out["ratio_null"] is None and out["ratio_null2"] is None
    assert max(abs(a - b) for a, b in zip(out["col_none"], out["col_same"])) < 6
    assert out["col_less"][2] > out["col_less"][0]  # 青
    assert out["col_more"][0] > out["col_more"][2]  # 橙
    assert out["has_radius"] == "undefined"  # 台数で円の大きさを変える関数は残っていない
    # 色は混み具合の区分に対応する。各区分の中心でその区分の色、区分は 4 つで上へいくほど赤い
    assert [b[2] for b in out["bins"]] == [0, 1, 2, 3] and [b[3] for b in out["bins"]][:3] == [0, 1, 2]
    assert all(b[1] == b[4] for b in out["bins"])
    assert out["bins"][0][4][1] > out["bins"][0][4][0] and out["bins"][3][4][0] > out["bins"][3][4][1] + 100  # 緑 → 赤
    # データなしは灰色で、混み具合の色（緑・黄・橙・赤）とは別。0 とは扱わない
    assert out["nodata"] == [110, 120, 135] and out["bin_null"] == -1
    assert all(b[4] != out["nodata"] for b in out["bins"])
    assert out["idx_miss"] is None and out["idx_zero_ref"] == 0 and out["idx_small"] == 0.1 and out["idx_mid"] == 0.5
    assert out["blend_skip"] == 0.2 and out["blend_all_miss"] is None and abs(out["blend_w"] - 0.5) < 1e-9
    # 流れる点の密度は台数に対して単調（上限まで厳密に増える）で、欠測・0 台以下は 0
    d = out["dens"]
    assert d[0] == 0 and out["dens_null"] == 0 and out["dens_neg"] == 0
    assert all(b >= a for a, b in zip(d, d[1:])) and all(b > a for a, b in zip(d[:6], d[1:6])) and d[-1] == d[-2] == 0.6
    assert out["speed"][0] > out["speed"][1] > out["speed"][2] > 0  # 混んでいるほど遅い
    assert out["eff_late"] == "week" and out["eff_early"] == "prev" and out["eff_prev"] == "prev"
    assert out["spark"].count("M") == 2  # 欠測で線が切れる
    assert out["newer_same"] is False and out["newer_diff"] is True and out["changed"] == 1
    # 流れの帯の数字が JSON と一致する
    pm, nat = out["pm"], data["national"]
    assert pm["api"]["points"] == len(data["points"]) == nat["n_points"]
    assert pm["fetch"]["requests"] == data["requests"]
    assert pm["fetch"]["at"] == data["fetched_at"][11:16]
    assert pm["missing"]["count"] == nat["n_points"] - nat["n_observed"]
    assert pm["agg"]["prefs"] == len(data["prefs"])
    assert pm["draw"]["drawn"] == nat["n_observed"]
    assert out["pm_mid"]["missing"]["count"] == sum(1 for p in data["points"] if p["series"][9] is None)
    # 順位は 1 地点あたりの大型車が多い順で、最新の値は JSON の avg_heavy と同じ
    vals = [v for _, v in out["rank"] if v is not None]
    assert vals == sorted(vals, reverse=True)
    by = {p["pref"]: p["avg_heavy"] for p in data["prefs"]}
    assert all(by[n] == v for n, v in out["rank"] if v is not None)


# ---------------------------------------------------------------- 画面（Edge）
READY = "!!(window.__trucks && window.__trucks.ready && window.__trucks.frame >= 1)"

RELOAD_JS = """(async () => {
  const S = window.__trucks, base = JSON.parse(JSON.stringify(S.data)), real = window.fetch;
  const mk = (d, ok = true) => () => Promise.resolve({ ok, status: ok ? 200 : 503, json: () => Promise.resolve(d) });
  const out = {};
  const nd = JSON.parse(JSON.stringify(base)); nd.fetched_at = '2026-11-06T23:01:00+09:00'; nd.points[3].series[23] = (nd.points[3].series[23] || 0) + 50;
  window.fetch = mk(nd); await S.api.check();
  out.new_state = S.checkState; out.new_at = S.data.fetched_at; out.reloads = S.reloads; out.changed = S.changed.size;
  out.toast = document.querySelector('#toast').textContent; out.pulse = !!S.pulse; out.fresh = document.querySelector('#fresh').textContent;
  window.fetch = mk(nd); await S.api.check(); out.same_state = S.checkState; out.reloads2 = S.reloads;
  window.fetch = mk(null, false); await S.api.check(); out.ng_state = S.checkState; out.keeps = S.data.fetched_at;
  out.next = S.nextPollAt > Date.now();
  window.fetch = real; return out; })()"""

RENDER_JS = """(() => { const S = window.__trucks, C = window.TrucksCore; const out = { badCol: 0, grey: 0, colored: 0, pcs: S.roads.pieces.length, nan: 0, partBad: 0, ring: 0, dens: 0, com: 0 };
  S.roads.pieces.forEach((pc, i) => {
    const has = !Number.isNaN(S.pIdx[i]);
    const rgb = [S.pC[i * 4], S.pC[i * 4 + 1], S.pC[i * 4 + 2]];
    const want = C.stateColor(has ? S.pIdx[i] : null);
    if (has && !(pc.conf < 1) && rgb.some((v, k) => Math.abs(v - want[k]) > 1)) out.badCol++;
    if (has) { out.colored++; if (!isFinite(S.pIdx[i]) || S.pIdx[i] < 0 || S.pIdx[i] > 1.0001) out.nan++; } else { out.grey++; if (S.pW[i] !== undefined && S.pW[i] > 0 && pc.w.length === 0 && false) out.nan++; }
    if (S.pShare[i] < 0 || S.pShare[i] > 1) out.partBad++;
    if (i % 5 === 0) { // 上り・下りの帯の密度 = それぞれの台数から決まる（台数は上り・下りの比で分ける）
      const sh = S.pShare[i], c = has ? S.pCnt[i] : 0;
      const want = has ? [C.particleDensity(5 * c * sh) * pc.L, C.particleDensity(5 * c * (1 - sh)) * pc.L] : [0, 0];
      for (let d = 0; d < 2; d++) if (Math.abs(S.pLaneDens[2 * i + d] - want[d]) > 1e-3 + 1e-4 * want[d]) out.partBad++;
    }
  });
  out.com = S.nCom;
  for (let q = 0; q < S.nCom * 10; q++) { const x = S.tailPos[2 * q], y = S.tailPos[2 * q + 1]; if (!(x > 122 && x < 146.5 && y > 24 && y < 46)) out.partBad++; }
  for (let i = 0; i < S.pts.length; i++) out.ring += S.PR[i];
  return out; })()"""


ORDER_JS = """[...document.querySelectorAll('#rank .rk')].sort((a, b) => parseFloat(a.style.transform.replace(/[^0-9.]/g, '')) - parseFloat(b.style.transform.replace(/[^0-9.]/g, ''))).map(e => e.dataset.pref).join(',')"""


def _open(page):
    port = server_port("local")
    open_app(page, f"http://127.0.0.1:{port}/trucks_now.html", ready_js=READY, wait_loading=False, start_timeout=90,
             what="いまの大型車の画面が起動しません", tag="trucks_now")
    page.wait(lambda: page.js("window.__trucks.map && window.__trucks.map.loaded()"), 60, "地図の読み込み")


def test_page_numbers_scrubber_missing_and_reload(data):
    with edge_page(1920, 1080) as page:
        _open(page)
        assert page.js("window.__errs.length") == 0
        nat = data["national"]
        # 流れの帯の数字が JSON と一致する
        assert page.js("document.querySelector('#s-api').textContent").replace(",", "").startswith(str(nat["n_points"]))
        assert page.js("document.querySelector('#s-fetch').textContent").startswith(str(data["requests"]))
        assert data["fetched_at"][11:16] in page.js("document.querySelector('#s-fetch2').textContent")
        miss = nat["n_points"] - nat["n_observed"]
        assert page.js("document.querySelector('#s-missing').textContent").startswith(str(miss))
        assert page.js("document.querySelector('#s-agg').textContent").startswith(str(len(data["prefs"])))
        # 道路の色は混み具合の区分どおり。データなしは灰色。欠測の観測点は輪で数える
        r = page.js(RENDER_JS)
        assert r["badCol"] == 0 and r["nan"] == 0 and r["partBad"] == 0, r
        assert r["grey"] > 0 and r["colored"] > r["grey"] // 40 and r["ring"] == miss and r["com"] > 0, r
        # 時間のつまみで、表示の時間・流れの帯・順位が変わる
        h0 = page.js("document.querySelector('#hour').textContent")
        rank0 = page.js("document.querySelector('#rank .rk').dataset.pref + document.querySelector('#rank .rk .v').textContent")
        order0 = page.js(ORDER_JS)
        page.js("window.__trucks.api.setT(5)")
        page.wait(lambda: page.js("window.__trucks.lastIdx === 5"), 10, "時間の切り替え")
        assert page.js("document.querySelector('#hour').textContent") != h0
        assert page.js(ORDER_JS) == order0  # つまみを動かしても県の並びは動かない（並べ替えを押したときだけ）
        assert page.js("document.querySelector('#slider').value") == "5"
        m5 = sum(1 for p in data["points"] if p["series"][5] is None)
        assert page.js("document.querySelector('#s-missing').textContent").startswith(str(m5))
        r5 = page.js(RENDER_JS)  # 別の時間でも、色は混み具合の区分どおりで、流れる点の数は台数に合う
        assert r5["badCol"] == 0 and r5["partBad"] == 0, r5
        page.js("window.__trucks.api.setT(5.5)")
        page.wait(lambda: page.js("document.querySelector('#hour small').textContent.includes('補って')"), 10, "補間")
        page.js("window.__trucks.api.setT(23)")
        page.wait(lambda: page.js("window.__trucks.lastIdx === 23"), 10, "最新へ")
        assert page.js("document.querySelector('#rank .rk').dataset.pref + document.querySelector('#rank .rk .v').textContent") == rank0
        # 段をクリック: 欠測の段が選ばれる
        page.js("document.querySelector('.stage[data-stage=missing]').click()")
        page.wait(lambda: page.js("window.__trucks.focus === 'missing'"), 10, "段の選択")
        assert page.js("document.querySelector('.stage[data-stage=missing]').getAttribute('aria-pressed')") == "true"
        page.js("document.querySelector('.stage[data-stage=missing]').click()")
        # 地点のカード: 欠測の地点は「欠測」と出て 0 台とは出さない
        card = page.js("(() => { const S = window.__trucks; const p = S.pts.find(q => q.series[23] == null); S.T = 23; return S.api.cardHtml(p, true); })()")
        assert "欠測" in card and "0 台ではありません" in card and 'class="big">0' not in card
        # 自動で読み直す（fetch を差し替える）: 新しければ差し替え、同じなら変更なし、失敗でも画面は残る
        res = page.js(RELOAD_JS)
        assert res["new_state"] == "new" and res["new_at"].startswith("2026-11-06T23:01") and res["reloads"] == 1
        assert res["changed"] == 1 and "新しいデータ" in res["toast"]
        assert "23:01" in res["fresh"]
        page.wait(lambda: page.js("!!window.__trucks.pulse"), 10, "変わった地点の脈動")  # 帯が順に光ったあとに始まる
        assert res["same_state"] == "same" and res["reloads2"] == 1
        assert res["ng_state"] == "ng" and res["keeps"].startswith("2026-11-06T23:01") and res["next"] is True
        assert page.js("window.__errs.length") == 0


def test_page_phone_layout_has_no_horizontal_scroll_and_chips_scroll():
    with edge_page(390, 844) as page:
        page.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
        _open(page)
        r = page.js("""({ sw: document.documentElement.scrollWidth, iw: innerWidth,
          mapH: document.querySelector('.mapwrap').getBoundingClientRect().height,
          chips: (() => { const e = document.querySelector('#stages'); return e.scrollWidth > e.clientWidth; })(),
          sheet: document.querySelector('#side').getBoundingClientRect().height })""")
        assert r["sw"] <= r["iw"] + 1, r
        assert r["chips"] is True  # 流れの帯は横にスクロールするチップ
        assert r["mapH"] >= 300, r  # 地図が画面の上の主役
        assert r["sheet"] <= 60  # 欄は閉じて下に畳まれている
        page.js("document.querySelector('#handle').click()")
        page.wait(lambda: page.js("document.querySelector('#side').getBoundingClientRect().height") > 300, 10, "欄が開く")


def test_roads_file_and_no_volume_sized_circles(data):
    path = ROOT / "web" / "data" / "trucks_roads.json"
    assert path.stat().st_size <= 3_500_000  # 画面で読む道路のファイルは 3.5 MB まで
    roads = json.loads(path.read_text(encoding="utf-8"), parse_constant=_no_nan)
    keys, sc = roads["keys"], roads["scale"]
    assert roads["pieces"] and "OpenStreetMap" in roads["source"]
    for pc in roads["pieces"]:
        assert pc["k"] in (1, 2) and pc["km"] > 0 and len(pc["c"]) >= 4 and len(pc["c"]) % 2 == 0  # 観測点どうしを結ぶ直線（k=0）は無い
        x, y = pc["c"][0], pc["c"][1]
        for i in range(0, len(pc["c"]), 2):
            if i:
                x += pc["c"][i]
                y += pc["c"][i + 1]
            assert 122 < x / sc < 146.5 and 24 < y / sc < 46
        if pc["w"]:  # 重みの合計は 1
            assert abs(sum(w for _, w in pc["w"]) - 1) < 0.02 and all(0 <= ki < len(keys) for ki, _ in pc["w"])
    assert any(not pc["w"] for pc in roads["pieces"])  # 灰色（データなし）の区間がある
    st = roads["stats"]
    assert st["snapped"] + st["unsnapped"] == len(data["points"]) == len(keys) and len(roads["unsnapped"]) == st["unsnapped"]
    js = JS.read_text(encoding="utf-8")
    core = (ROOT / "web" / "trucks_now_core.js").read_text(encoding="utf-8")
    assert "radiusPx" not in js and "radiusPx" not in core and "Math.sqrt" not in js  # 台数で円の大きさを変えない
    assert "getRadius: (p, { index }) => (S.PR[index] ? 5 : 3)" in js  # 観測点の円の大きさは一定（欠測の輪だけ少し大きい）
    assert "flow-tail" in js and "flow-head" in js and "S.upShare" in js  # 流れの帯は上り・下りの比で分ける
    html = HTML.read_text(encoding="utf-8")
    assert "速度ではありません" in html and "OpenStreetMap contributors" in html and "直線で結んだ" not in html
