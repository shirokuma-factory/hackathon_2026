"""乗り物と流れの粒子が、手段に合う場所だけを走るか（トラック・鉄道は陸、船は海、飛行機はどこでも）。

陸の判定は web/data/pref.geojson を 0.005 度（約 500 m）の格子にした地図。海峡をまたぐ固定の橋・トンネルは許す区間（FIXED_LINKS）。
海岸から 1.5 km 以内は陸とみなす（港・岸壁・海岸沿いの道。県の境界の図が粗いぶん）。
画面の確認は、アプリが実際に動かす先頭の乗り物（Market.headData）と、粒子の通る線（Market.state.flows.items）をそのまま測る。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from test_v31_market_routes import _browser

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DATA = WEB / "data"

X0, Y0, X1, Y1, RES = 122.0, 20.0, 154.5, 46.0, 0.005
COAST_KM = 1.5
# 固定リンク（陸の乗り物が海の上を走ってよい区間）: 名前 -> (経度の下限, 緯度の下限, 経度の上限, 緯度の上限)
FIXED_LINKS = {
    "青函トンネル": (140.10, 41.15, 141.00, 41.80),
    "関門トンネル・関門橋": (130.88, 33.90, 131.03, 34.00),
    "瀬戸大橋": (133.78, 34.30, 133.90, 34.43),
    "明石海峡大橋": (134.97, 34.58, 135.05, 34.65),
    "大鳴門橋": (134.58, 34.20, 134.66, 34.28),
    "しまなみ海道": (132.95, 34.05, 133.25, 34.42),
    "東京湾アクアライン": (139.76, 35.33, 139.95, 35.55),
}


class Mask:
    """陸の格子。land = 陸、near = 陸から 1.5 km 以内（陸を含む）。"""

    def __init__(self):
        self.w, self.h = int((X1 - X0) / RES), int((Y1 - Y0) / RES)
        im = Image.new("L", (self.w, self.h), 0)
        d = ImageDraw.Draw(im)
        g = json.loads((DATA / "pref.geojson").read_text(encoding="utf-8"))
        tx = lambda p: ((p[0] - X0) / RES, (Y1 - p[1]) / RES)  # noqa: E731
        for f in g["features"]:
            gm = f["geometry"]
            for pg in (gm["coordinates"] if gm["type"] == "MultiPolygon" else [gm["coordinates"]]):
                d.polygon([tx(p) for p in pg[0]], fill=255)
                for hole in pg[1:]:
                    d.polygon([tx(p) for p in hole], fill=0)
        self.land = np.array(im) > 0
        r = max(1, int(round(COAST_KM / (RES * 111 * 0.82))))
        self.near = np.array(im.filter(ImageFilter.MaxFilter(2 * r + 1))) > 0

    def _at(self, grid, lon, lat):
        x = ((np.asarray(lon) - X0) / RES).astype(int)
        y = ((Y1 - np.asarray(lat)) / RES).astype(int)
        ok = (x >= 0) & (x < self.w) & (y >= 0) & (y < self.h)
        out = np.zeros(np.shape(x), bool)
        out[ok] = grid[y[ok], x[ok]]
        return out

    def is_land(self, lon, lat):
        return self._at(self.land, lon, lat)

    def is_near_land(self, lon, lat):
        return self._at(self.near, lon, lat)


def in_fixed_link(lon, lat):
    lon, lat = np.asarray(lon), np.asarray(lat)
    ok = np.zeros(np.shape(lon), bool)
    for x0, y0, x1, y1 in FIXED_LINKS.values():
        ok |= (lon >= x0) & (lon <= x1) & (lat >= y0) & (lat <= y1)
    return ok


def densify(pts, step_km=1.0):
    pts = np.asarray(pts, float)
    out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        dx = (b[0] - a[0]) * 111 * math.cos(math.radians(a[1]))
        dy = (b[1] - a[1]) * 111
        n = max(1, int(math.hypot(dx, dy) / step_km))
        out.extend(a + (b - a) * i / n for i in range(1, n + 1))
    return np.array(out)


def dec(flat, scale=1e4):
    return np.array(flat, dtype=np.int64).reshape(-1, 2).cumsum(0) / scale


def violations(mask: Mask, mode: str, pts) -> int:
    """pts（経度・緯度の並び）のうち、手段に合わない場所の数。飛行機は常に 0。
    トラック・鉄道: 海岸から 1.5 km より沖で、固定リンクの外にある点。船: 海岸から 1.5 km より内陸にある点。"""
    p = np.asarray(pts, float).reshape(-1, 2)
    if mode == "air" or not len(p):
        return 0
    if mode == "ferry":
        return int((mask.is_land(p[:, 0], p[:, 1]) & _inland(mask, p)).sum())
    ok = mask.is_near_land(p[:, 0], p[:, 1]) | in_fixed_link(p[:, 0], p[:, 1])
    return int((~ok).sum())


_ER: dict = {}


def _inland(mask: Mask, p):
    """陸から海岸へ 1.5 km 入った内側（港の岸壁は海岸から 1.5 km 以内なので許す）。"""
    if "g" not in _ER:
        r = max(1, int(round(COAST_KM / (RES * 111 * 0.82))))
        im = Image.fromarray((mask.land * 255).astype("uint8")).filter(ImageFilter.MinFilter(2 * r + 1))
        _ER["g"] = np.array(im) > 0
    return mask._at(_ER["g"], p[:, 0], p[:, 1])


@pytest.fixture(scope="module")
def mask():
    return Mask()


# ---------------------------------------------------------------- 画面で測る
SAMPLE_JS = """(function(){
  const A = __app, M = A.Market.state, tracks = [], seen = new Map(), heads = [];
  for (let s = 0; s < 24; s += 0.25) for (const h of A.Market.headData(s)) heads.push([M.d.prefs[h.route.r.fi] + '>' + M.d.prefs[h.route.r.ti], +h.p[0].toFixed(5), +h.p[1].toFixed(5)]);
  for (const it of M.flows.items) { if (!seen.has(it.pts)) { seen.set(it.pts, tracks.length); tracks.push({k: M.d.prefs[it.route.r.fi] + '>' + M.d.prefs[it.route.r.ti], pts: it.pts.map(p => [+p[0].toFixed(5), +p[1].toFixed(5)])}); } }
  return JSON.stringify({rows: M.rows.length, real: M.flows.routes.filter(r => r.real).length, mode: M.mode, heads, tracks,
    arcs: M.flows.routes.filter(r => !r.real).length, unknown: M.flows.routes.filter(r => r.unknown).length, still: M.flows.routes.filter(r => !r.actTotal).length});
})()"""


def measure(page, open_, mode: str, mask: Mask) -> dict:
    """国内の傾向の画面で mode を開き、先頭の乗り物と粒子の線を測る。"""
    open_(f"#v=market&m={mode}&n=100&km=0")
    page.wait(lambda: page.js("!!(window.__app.Market.state.flows && window.__app.Market.state.flows.routes.length)"), 60, "流れの作成")
    d = json.loads(page.js(SAMPLE_JS))
    bad_h = {}
    for k, x, y in d["heads"]:
        if violations(mask, mode, [[x, y]]):
            bad_h[k] = bad_h.get(k, 0) + 1
    bad_t = {}
    for t in d["tracks"]:
        v = violations(mask, mode, densify(t["pts"], 1.0))
        if v:
            bad_t[t["k"]] = bad_t.get(t["k"], 0) + v
    d.update(bad_heads=bad_h, bad_tracks=bad_t, n_heads=len(d["heads"]), n_tracks=len(d["tracks"]))
    return d



PAIRS = json.loads((DATA / "routes_pairs.json").read_text(encoding="utf-8"))


def seg_dist_km(pts, legs):
    """点列 pts（N,2）から、折れ線の集まり legs への最短距離（km）。"""
    out = np.full(len(pts), 1e9)
    for L in legs:
        a, b = L[:-1], L[1:]
        k = math.cos(math.radians(35.0))
        for i in range(len(pts)):
            p = pts[i]
            ab = (b - a) * [k, 1]
            ap = (p - a) * [k, 1]
            t = np.clip((ap * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
            d = np.hypot(*(ap - ab * t[:, None]).T) * 111
            out[i] = min(out[i], d.min())
    return out


# ---------------------------------------------------------------- データの線そのもの（陸の手段の区間は陸、航路は海）
def test_pair_legs_land_modes_on_land_and_ferry_legs_at_sea(mask):
    n_f = 0
    for key, v in PAIRS.items():
        for mode, m in v["m"].items():
            for leg in (m["legs"] if m else []):
                pts = densify(dec(leg["c"]), 1.0)
                if leg["m"] == "f":
                    n_f += 1
                    assert violations(mask, "ferry", pts) == 0, (key, "航路が陸の上")
                    assert (~mask.is_land(pts[:, 0], pts[:, 1])).mean() > 0.6, (key, "航路の大半が海ではない")
                else:
                    assert violations(mask, "truck", pts) == 0, (key, mode, leg["m"], "陸の区間が海の上")
    assert n_f == 17


def test_other_data_lines_stay_on_land_or_fixed_links(mask):
    bad = []
    for f in sorted(DATA.glob("routes_[0-9]*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for pref, v in d["dests"].items():
            for mode, m in v["m"].items():
                for leg in (m["legs"] if m else []):
                    if leg["m"] in "tr" and violations(mask, "truck", densify(dec(leg["c"]), 1.0)):
                        bad.append((f.name, pref, mode))
    trips = json.loads((DATA / "routes_trips.json").read_text(encoding="utf-8"))
    for tid, t in trips.items():
        for leg in t["truck"]["legs"]:
            if violations(mask, "truck", densify(dec(leg["c"]), 1.0)):
                bad.append(("trip", tid))
    fl = json.loads((DATA / "flow_lines.json").read_text(encoding="utf-8"))
    for ln in fl["lines"]:
        if violations(mask, "truck", densify(dec(ln[2], fl["scale"]), 1.0)):
            bad.append(("flow_lines", ln[0], ln[1]))
    rail = json.loads((DATA / "routes_rail.json").read_text(encoding="utf-8"))
    for ln in rail["lines"]:
        if violations(mask, "rail", densify(dec(ln), 2.0)):
            bad.append(("rail",))
    assert not bad, bad[:10]


def test_fixed_links_are_the_documented_ones():
    assert set(FIXED_LINKS) == {"青函トンネル", "関門トンネル・関門橋", "瀬戸大橋", "明石海峡大橋", "大鳴門橋", "しまなみ海道", "東京湾アクアライン"}
    js = (WEB / "routes.js").read_text(encoding="utf-8")
    for x0, y0, x1, y1 in FIXED_LINKS.values():
        assert f"[{x0:.2f}, {y0:.2f}, {x1:.2f}, {y1:.2f}]" in js   # JS 側の箱とそろっている


# ---------------------------------------------------------------- 国内の傾向（全国の流れ）の画面で測る
@pytest.mark.parametrize("mode", ["truck", "ferry", "rail"])
def test_national_view_vehicles_and_particles_stay_on_their_own_ground(mask, mode):
    with _browser(1920, 1080) as (page, open_):
        d = measure(page, open_, mode, mask)
        assert d["rows"] == 100
        assert not d["bad_heads"], ("乗り物が手段に合わない場所にいる", dict(list(d["bad_heads"].items())[:5]))
        assert not d["bad_tracks"], ("粒子の線が手段に合わない場所を通る", dict(list(d["bad_tracks"].items())[:5]))
        assert d["n_heads"] > 0, "動く乗り物が 1 台も無い"
        if mode != "truck":
            assert d["unknown"] > 0   # 線の無い弧は、乗り物を出さない
        assert not page.js("window.__errs.length")


def test_ferry_ship_moves_only_on_the_sea_leg_of_its_own_pair(mask):
    """フェリーの船は、データの航路（f）の線の上だけを走る（道の区間 t の上にはいない）。"""
    with _browser(1920, 1080) as (page, open_):
        d = measure(page, open_, "ferry", mask)
        by = {}
        for k, x, y in d["heads"]:
            by.setdefault(k, []).append([x, y])
        assert len(by) >= 3
        for k, pts in by.items():
            legs = [dec(l["c"]) for l in PAIRS[k]["m"]["ferry"]["legs"] if l["m"] == "f"]
            roads = [dec(l["c"]) for l in PAIRS[k]["m"]["ferry"]["legs"] if l["m"] == "t"]
            P = np.array(pts)
            assert seg_dist_km(P, legs).max() < 2.5, (k, "航路から外れた")   # 画面の線は 0.02 度（約 2 km）で間引いている
            # 道の区間の途中（港から 3 km より遠い道の上）にはいない
            far_road = [r[len(r) // 3 : 2 * len(r) // 3] for r in roads]
            assert seg_dist_km(P, far_road).min() > 0.5, (k, "道の上にいる")
        # 港の印が道の区間と航路の境に出る
        assert page.js("window.__app.Market.state.flows.ports.length") > 0


def test_ferry_selected_vehicle_ornament_sits_on_the_sea(mask):
    with _browser(1920, 1080) as (page, open_):
        open_("#v=market&m=ferry&n=100&km=0")
        page.wait(lambda: page.js("!!(window.__app.Market.state.flows && window.__app.Market.state.flows.routes.length)"), 60, "流れの作成")
        res = json.loads(page.js("""(function(){const M=__app.Market.state, out=[];
          for (const r of M.flows.routes) { if (!r.real || !r.actTotal) continue;
            const l = r.legs.filter(x => x.on); const mid = l[0]; const f = 0.5 * r.actTotal;
            out.push({k: M.d.prefs[r.r.fi] + '>' + M.d.prefs[r.r.ti], legs: r.legs.map(x => ({mode: x.mode, on: x.on, n: x.pts.length}))}); }
          return JSON.stringify(out);})()"""))
        assert res and all(sum(1 for x in r["legs"] if x["on"]) == 1 and all(x["mode"] == "ferry" for x in r["legs"] if x["on"]) for r in res)


# ---------------------------------------------------------------- 比べる画面（着地を選んだとき）の乗り物
COMPARE_SAMPLE_JS = """(function(){
  const Rt = __app.Rt, cur = Rt.current();
  if (!cur) return JSON.stringify({ready: false});
  const out = [];
  for (const m of ['truck','rail','ferry','air']) { const s = cur.sets[m]; if (!s || !s.ok || !s.items) continue;
    for (let t = 0; t < s.loop; t += 0.2) for (const v of Rt.vehiclesOf(s, t)) out.push([m, v.mode, +v.p[0].toFixed(5), +v.p[1].toFixed(5)]); }
  return JSON.stringify({ready: true, out});
})()"""


@pytest.mark.parametrize("dest", ["01101", "38201", "40133", "47201", "27127", "02201", "34101"])
def test_compare_view_vehicles_stay_on_their_own_ground(mask, dest):
    with _browser(1920, 1080) as (page, open_):
        open_(f"#v=compare&o=13104&l=km&d={dest}")
        page.wait(lambda: json.loads(page.js(COMPARE_SAMPLE_JS)).get("ready"), 60, "比べる画面の経路")
        import time
        time.sleep(3)   # 道の取得（失敗しても直線で出す）が終わるのを待つ
        d = json.loads(page.js(COMPARE_SAMPLE_JS))
        bad = [(m, vm) for m, vm, x, y in d["out"] if violations(mask, vm, [[x, y]])]
        assert not bad, bad[:5]


# ---------------------------------------------------------------- 弧の線（自社の運行・背景の流れ・着地の線）
def test_arcs_that_cross_the_sea_get_no_vehicle_or_a_dotted_line():
    app, own = (WEB / "app.js").read_text(encoding="utf-8"), (WEB / "own.js").read_text(encoding="utf-8")
    assert "if (!Rt.pathOk('truck', p.pts)) return;" in app            # 背景の流れの粒子
    assert "r.kind === 'arc' && !Rt.pathOk('truck', r.path)" in app    # 着地の線は点線
    assert "if (!Rt.pathOk('truck', pth))" in own                      # 自社の運行の地点間の線は点線


def test_ambient_particles_and_arc_routes_are_all_on_land_in_the_browser(mask):
    with _browser(1920, 1080) as (page, open_):
        open_("#v=compare&o=13104&l=km")
        items = json.loads(page.js("JSON.stringify([...new Set(__app.S.ambient.items.map(i => i.path))].map(p => p.map(q => [q[0], q[1]])))"))
        assert items
        for path in items:
            assert violations(mask, "truck", densify(path, 4.0)) == 0
        # 海をまたぐ弧は粒子から外れている（沖縄・北海道・九州などへの扇）
        total = page.js("__app.S.ambient.paths.length")
        assert len(items) < total
