"""SPEC §30.2・§30.3・§30.5（CLO の入口）・§30.7・§30.8 の見せてからつなぐまで（受入条件 G130・G131・G133・G135・G136）。

画面の動き（JS）は撮影で確かめ、ここでは、画面が頼るデータとの対応と、画面の部品が入っていることを確かめる。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from shipcompare import spaceb
from shipcompare.storage import list_scenarios

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def pref_of(code: str) -> str:
    idx = json.loads((WEB / "data" / "index.json").read_text(encoding="utf-8"))
    return idx["prefs"][idx["codes"].index(code)]


def reverse_trips(frm: str, to: str):
    """画面の isReverse と同じ規則（荷が多い向き frm → to の、to → frm を走る便）。"""
    return [t for t in spaceb.load_trips() if pref_of(t["origin_code"]) == to and pref_of(t["dest_code"]) == frm]


# ---------------------------------------------------------------- G130 どれだけ悪いかの物差し

def test_g130_summary_has_numbers_and_sources():
    html = read("index.html")
    summary = html[html.index('id="m-summary"'):html.index("</section>", html.index('id="m-summary"'))]
    assert "38.97%" in summary and "44%" in summary and "34.1%" in summary
    assert "2025 年度" in summary and "2028 年度" in summary and "2030 年度" in summary
    assert "約 5 ポイント足りない" in summary
    for src in ("自動車輸送統計年報 2025 年度 表 3-1", "基本的な方針", "令和 7 年 2 月 18 日告示", "目標の土台の範囲は国の資料で未確認",
                "持続可能な物流の実現に向けた検討会 最終取りまとめ", "2023 年 8 月", "4 頁"):
        assert src in summary, src
    # 今ある 39.08%・62.15% の行は残し、範囲を書く
    assert 'id="sum-load"' in summary and 'id="sum-rate"' in summary and "営業用普通車" in summary


def test_g130_credits_have_new_sources_and_x_sentence():
    js = read("market.js")
    assert "38.97%" in js and "44%（2028 年度）" in js and "34.1%" in js and "最終取りまとめ" in js
    html = read("index.html")
    assert "X が動くのは、帰りが空く向きに荷を持つ荷主が帰り便を使い、運送会社が帰り便を登録したときです。このデモの便はすべて架空です。" in html
    # 上の 2 行が、3 つの数字の前にある
    assert html.index("38.97%") < html.index('id="sum-load"')


def test_g130_summary_numbers_match_the_source_arithmetic():
    # 44 − 38.97 = 5.03 ポイント（画面は「約 5 ポイント」）
    assert round(44 - 38.97) == 5


# ---------------------------------------------------------------- G131 見せてからつなぐ

def test_g131_default_cargo_gets_zero_trips_with_two_or_more_reasons_on_hyogo_tokyo():
    rev = reverse_trips("兵庫県", "東京都")
    assert {t["id"] for t in rev} >= {"T1", "T2", "T7"}
    sc = next(s for s in list_scenarios() if s["id"] == "S1")
    body = {"shipment": {"items": [{"count": it.count, "length_cm": it.length_cm, "width_cm": it.width_cm, "height_cm": it.height_cm,
                                    "weight_kg": it.weight_kg} for it in sc["shipment"].items]}, "dest_code": "28110", "origin_code": "13104"}
    sh = spaceb.parse_shipment(body)
    res = {t["id"]: spaceb.match(sh, t) for t in rev}
    assert not any(ok for ok, _ in res.values()), res
    # 理由は 2 種類以上ある（画面は多い順の上位 2 つを出す）
    kinds = set()
    for _, reasons in res.values():
        kinds.update(r[:4] for r in reasons)
    assert len(kinds) >= 2


def test_g131_some_scenario_fits_a_trip_in_the_return_direction():
    """東京都→兵庫県の便に、1 便以上合う架空の場面がある（画面の「この向きの便に合う例の荷で試す」の相手）。"""
    rev = reverse_trips("兵庫県", "東京都")
    fits = []
    for sc in list_scenarios():
        raw = sc.get("backhaul") or {}
        body = {"shipment": {"items": [{"count": it.count, "length_cm": it.length_cm, "width_cm": it.width_cm, "height_cm": it.height_cm,
                                        "weight_kg": it.weight_kg} for it in sc["shipment"].items]},
                "dest_code": raw.get("dest_code", "28110"), "origin_code": raw.get("origin_code", "13104"),
                **({"is_box": raw["is_box"], "window_from": raw["window_from"], "window_to": raw["window_to"]} if raw else {})}
        sh = spaceb.parse_shipment(body)
        if any(spaceb.match(sh, t)[0] for t in rev):
            fits.append(sc["id"])
    assert "S6" in fits, fits


def test_g131_ui_parts_exist():
    mk, fd, html = read("market.js"), read("find.js"), read("index.html")
    assert "この向きの空き便を探す" in mk and "この向きの帰り便を登録する" in mk
    assert 'data-act="carrier"' in mk and "Find.openCarrier(pairNames())" in mk
    for text in ("この荷に合う便はありません", "この向きの便に合う例の荷で試す", "送り方を比べて見積依頼文を作る", "合わない理由の多い順"):
        assert text in fd, text
    assert "slice(0, 2)" in fd  # 理由は多い順の上位 2 つ
    assert "try-ex" in fd and "to-compare-now" in fd
    # 運送会社の役の向きの欄が帰りの向きで埋まる（荷が多い向きの逆）
    assert 'id="f-dir"' in html and "F.dir = { from: pair.to, to: pair.from }" in fd
    assert "openCarrier" in fd.split("return {")[-1]


def test_g131_return_direction_is_the_reverse_of_the_market_pair():
    """市場の兵庫県→東京都を選ぶと、運送会社の役の向きは東京都→兵庫県。この向きの便が登録されている（T1・T2・T7）。"""
    ts = reverse_trips("兵庫県", "東京都")
    assert [t["id"] for t in ts] == ["T1", "T2", "T7"]


# ---------------------------------------------------------------- G133 CLO の入口

def test_g133_clo_entry_for_500km_pairs():
    mk = read("market.js")
    assert "const CLO_KM = 500;" in mk and "r.km >= CLO_KM" in mk
    assert "鉄道・船を含めて送り方を比べる" in mk and 'data-act="clo"' in mk and "App.compareTo(o, d)" in mk
    app = read("app.js")
    assert "App.compareTo = async (originCode, destCode)" in app


def test_g133_capital_table_has_47_prefectures_in_the_same_order_as_market_json():
    mk = read("market.js")
    body = mk[mk.index("const CAPITAL = {"):mk.index("};", mk.index("const CAPITAL = {"))]
    pairs = re.findall(r"'([^']+)': '(\d{5})'", body)
    prefs = json.loads((WEB / "data" / "market.json").read_text(encoding="utf-8"))["prefs"]
    assert [p for p, _ in pairs] == prefs and len(pairs) == 47
    idx = json.loads((WEB / "data" / "index.json").read_text(encoding="utf-8"))
    names = dict(zip(idx["codes"], zip(idx["prefs"], idx["names"])))
    for p, c in pairs:
        assert c in names, (p, c)
        assert names[c][0] == p, (p, c, names[c])
    assert dict(pairs)["東京都"] == "13104" and names["13104"][1] == "新宿区"  # 東京は新宿区
    assert dict(pairs)["兵庫県"] == "28110"


def test_g133_some_market_pairs_reach_500km():
    d = json.loads((WEB / "data" / "market.json").read_text(encoding="utf-8"))
    assert any(p[6] >= 500 for p in d["modes"]["truck"]["pairs"][:60])


# ---------------------------------------------------------------- G136 正式名

def test_g136_official_prefecture_names_in_corridor_labels():
    mk = read("market.js")
    assert "replace(/[県府]$/" not in mk
    assert "esc(P[r.fi])" in mk and "esc(P[r.ti])" in mk
    d = json.loads((ROOT / "data" / "demo" / "carriers.json").read_text(encoding="utf-8"))
    for t in d["trips"]:
        label = t["route_label"]
        m = re.fullmatch(r"(.+?)→(.+?)（(.+?)→(.+?)の荷の帰り）", label)
        assert m, label
        for name in m.groups():
            assert re.search(r"(都|道|府|県)$", name), (label, name)
    labels = [t["route_label"] for t in d["trips"]]
    assert "東京都→兵庫県（兵庫県→東京都の荷の帰り）" in labels


# ---------------------------------------------------------------- G135 狭い画面（静的な確かめ。撮影は別）

def test_g135_narrow_layout_parts():
    css, js, html, app = read("style.css"), read("sheet.js"), read("index.html"), read("app.js")
    assert "@media (max-width: 600px)" in css and "--sheet-h: 44vh" in css
    assert 'id="sheet-tabs"' in html and '<script src="sheet.js"></script>' in html
    assert html.index('src="sheet.js"') < html.index('src="app.js"')
    for wrap in ('id="find-sheet"', 'id="own-sheet"'):
        assert wrap in html
    # 見方の切り替えは 1 行で、収まらなければ枠の中で横にスクロールする
    assert "#views" in css and "overflow-x: auto" in css
    # fitBounds の余白は画面の大きさと下のパネルの高さで決める
    assert "patchMap" in js and "clamp" in js and "Sheet.init(map)" in app
    # 読み込み画面は終わったら DOM から取り除く
    assert "l.remove()" in app


def test_g135_loading_element_is_removed_and_tools_tolerate_it():
    app = read("app.js")
    assert re.search(r"classList\.add\('done'\);\s*setTimeout\(\(\) => l\.remove\(\)", app)
    for p in (ROOT / "tools" / "capture_web.py", WEB / "check.js"):
        text = p.read_text(encoding="utf-8")
        assert "#loading').classList.contains('done')" not in text.replace("l.classList.contains('done')", ""), p


def test_g135_clamp_logic_keeps_padding_under_canvas():
    """sheet.js の clamp と同じ式（JS を実行しないので、式を Python で写して境界を確かめる）。"""
    def clamp(p, w, h):
        fx = (p["left"] + p["right"]) / (w * 0.85)
        fy = (p["top"] + p["bottom"]) / (h * 0.85)
        kx = 1 / fx if fx > 1 else 1
        ky = 1 / fy if fy > 1 else 1
        return {k: int(v * (kx if k in ("left", "right") else ky)) for k, v in p.items()}

    p = clamp({"top": 165, "bottom": 100, "left": 400, "right": 570}, 400, 800)
    assert p["left"] + p["right"] < 400 and p["top"] + p["bottom"] < 800
    p = clamp({"top": 56, "bottom": 364, "left": 16, "right": 16}, 400, 800)
    assert p == {"top": 56, "bottom": 364, "left": 16, "right": 16}
    # sheet.js が同じ式を持つ
    js = read("sheet.js")
    assert "w * 0.85" in js and "h * 0.85" in js


@pytest.mark.parametrize("name", ["style.css", "sheet.js", "market.js", "find.js", "index.html"])
def test_lf_line_endings(name):
    assert b"\r\n" not in (WEB / name).read_bytes()
