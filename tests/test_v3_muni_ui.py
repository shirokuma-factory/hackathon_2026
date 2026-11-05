"""版3の4段目: 市区町村の推定・物流の集積・施設の画面（SPEC §18、G44〜G46）。通信はしない。

画面そのものの見た目は tools/capture_web.py の画面写真（docs/assets/2026-11-04_*.jpg）で確かめる。
ここでは、文言・出力・gitignore・出典の表示が崩れていないかを調べる。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from shipcompare import geo, muni

ROOT = Path(__file__).resolve().parents[1]
NOTICE = "推定です。公開の統計に市区町村単位の流動はありません。県の値を業種の従業者数と人口で分けた目安です"
P31_MESSAGE = "物流拠点（国土数値情報 P31）は再配布できないため、この版では表示しません"
ATTRIBUTIONS = [
    "国土数値情報（物流拠点データ）（国土交通省）2013年",
    "国土数値情報（空港データ）（国土交通省）を加工して作成",
    "© OpenStreetMap contributors（ODbL 1.0）",
    "総務省・経済産業省『令和6年経済センサス‐基礎調査』、総務省『令和2年国勢調査』（e-Stat）を加工して作成",
]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def build_mod():
    spec = importlib.util.spec_from_file_location("build_web_data", ROOT / "tools" / "build_web_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def built(build_mod, tmp_path_factory):
    """build_muni と build_facilities を一時のフォルダに作り、コミット済みの web/data を汚さない。"""
    out = tmp_path_factory.mktemp("webdata")
    mp = pytest.MonkeyPatch()
    mp.setattr(build_mod, "OUT", out)
    mp.setattr(build_mod, "LOCAL_OUT", out / "local")
    mp.setattr(build_mod, "P31_RAW", out / "no_such_p31.json")  # 手元に P31 がない状態
    codes = sorted(geo.load_points())
    build_mod.build_muni(codes)
    build_mod.build_facilities()
    yield out, codes
    mp.undo()


# ---------------------------------------------------------------- G45: 推定の表示文
def test_spec18_ui_notice_text_is_in_the_page():
    html = read("web/index.html")
    assert NOTICE in html
    assert f"<b id=\"mu-notice-text\">{NOTICE}</b>" in html
    # 凡例の最初の要素（凡例の上）にあり、既定では隠し、市区町村（推定）のときだけ出す
    legend = html[html.index('id="m-legend"'):]
    assert legend.index('id="mu-notice"') < legend.index('id="mlg-scroll"')
    assert 'id="mu-notice" hidden' in html
    js = read("web/muni.js")
    assert NOTICE in js
    assert "q('#mu-notice').hidden = !isMuni();" in js  # 市区町村（推定）の間は常に出す
    assert muni.DISCLAIMER == NOTICE
    assert json.loads(read("web/data/muni.json"))["disclaimer"] == NOTICE


def test_spec18_ui_formula_text_comes_from_the_json():
    html = read("web/index.html")
    assert 'id="mu-formula-text"' in html and "<details" in html[html.index('id="mu-notice"'):html.index('id="mlg-scroll"')]
    assert "U.d.formula" in read("web/muni.js") or "d.formula" in read("web/muni.js")
    assert json.loads(read("web/data/muni.json"))["formula"] == muni.FORMULA


def test_spec18_ui_scope_toggle_and_layer_buttons_exist():
    html = read("web/index.html")
    assert 'data-scope="pref"' in html and 'data-scope="muni"' in html and "市区町村（推定）" in html
    assert 'data-toggle="conc"' in html and "物流の<br>集積" in html
    assert 'data-toggle="fac"' in html
    js = read("web/muni.js")
    assert "ColumnLayer" in js and "pitch: on && U.active ? 55 : 0" in js


# ---------------------------------------------------------------- 出典と P31 の文言
def test_spec18_ui_attributions_and_p31_message_are_in_the_script():
    js = read("web/muni.js")
    for t in ATTRIBUTIONS:
        assert t in js, t
    assert P31_MESSAGE in js
    assert "非商用" in js
    assert "zoom >= 11" in js  # OSM の建物は拡大 11 以上だけ


# ---------------------------------------------------------------- 作った JSON
def test_spec18_ui_built_muni_json_keeps_none_for_secret(built):
    out, codes = built
    d = json.loads((out / "muni.json").read_text(encoding="utf-8"))
    n = len(codes)
    for key in ("out", "in", "bal", "ost", "ist", "emp", "pop", "area", "c44", "c47", "cemp", "cdens", "cst", "pt", "osm"):
        assert len(d[key]) == n, key
    est = muni.load_estimate()["municipalities"]
    secret = 0
    for i, c in enumerate(codes):
        r = est[c]
        if r["out_state"] != "ok":
            secret += 1
            assert d["ost"][i] == {"secret": 2, "missing": 3}[r["out_state"]]
            assert d["out"][i] is None and d["bal"][i] is None and d["emp"][i] is None, c  # 0 にしない
            assert d["reason"][i], c
        else:
            assert d["bal"][i] == r["balance_t"] or r["balance_t"] is None
        if r["trucking_44"]["status"] in ("secret", "missing") or r["warehousing_47"]["status"] in ("secret", "missing"):
            assert d["cemp"][i] is None and d["cdens"][i] is None and d["cst"][i] in (2, 3), c
        else:
            assert d["cemp"][i] == r["trucking_44"]["employees"] + r["warehousing_47"]["employees"], c
    assert secret >= 20
    # 県の表は muni_estimate.json のとおり
    for name, p in muni.load_estimate()["prefectures"].items():
        assert d["prefs"][name] == [p["out_t"], p["in_t"], p["unallocated_out_t"], p["unallocated_in_t"], p["n_municipalities"], p["n_gray"]]


def test_spec18_ui_built_muni_json_edges_and_osm_counts(built):
    out, codes = built
    d = json.loads((out / "muni.json").read_text(encoding="utf-8"))
    for key in ("dens", "emp"):
        e = d["edges"][key]
        assert len(e) >= 3 and all(a < b for a, b in zip(e, e[1:])), key
    counts = json.loads((ROOT / "data/geo/osm_logistics_counts.json").read_text(encoding="utf-8"))
    total = [sum(c[k] for c in d["osm"]) for k in range(5)]
    assert total == [counts["totals"][t] - counts["unmatched"][t] for t in d["osm_types"]]
    assert all(p is not None and -180 < p[0] < 180 for p in d["pt"])


def test_spec18_ui_built_facility_files(built):
    out, _ = built
    c28 = json.loads((out / "c28.json").read_text(encoding="utf-8"))
    assert len(c28["items"]) == 108 and "商用可" in c28["terms"]
    osm = json.loads((out / "osm_points.json").read_text(encoding="utf-8"))
    assert len(osm["items"]) == 80461 and osm["license"] == "ODbL 1.0"
    assert all(len(x) == 3 for x in osm["items"][:100])
    assert not (out / "local").exists(), "P31 が手元にないときは web/data/local/ に何も作らない"


def test_spec18_ui_p31_is_written_only_to_the_local_folder(build_mod, tmp_path, monkeypatch):
    raw = tmp_path / "p31_points.json"
    raw.write_text(json.dumps({"source": "s", "terms": "t", "fetched_on": "2026-11-04", "kinds": {"5": "トラックターミナル"},
                               "items": [{"name": "試験", "kind": 5, "address": "a", "operator": "民営", "lon": 139.7, "lat": 35.6}]},
                              ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(build_mod, "OUT", tmp_path / "out")
    monkeypatch.setattr(build_mod, "LOCAL_OUT", tmp_path / "out" / "local")
    monkeypatch.setattr(build_mod, "P31_RAW", raw)
    build_mod.build_facilities()
    assert (tmp_path / "out" / "local" / "p31.json").exists()
    assert not list((tmp_path / "out").glob("*p31*"))  # 公開側のフォルダには P31 を置かない


# ---------------------------------------------------------------- G46: P31 はリポジトリに入らない
def test_spec18_ui_p31_output_path_is_gitignored():
    assert build_path_is_ignored("web/data/local/p31.json")
    assert build_path_is_ignored("web/data/local/anything.json")
    assert build_path_is_ignored("data/raw/p31/p31_points.json")


def build_path_is_ignored(rel: str) -> bool:
    git = shutil.which("git")
    if not git:
        pytest.skip("git がありません")
    r = subprocess.run([git, "check-ignore", "-q", "--no-index", rel], cwd=ROOT, capture_output=True)
    return r.returncode == 0


def test_spec18_ui_no_p31_file_is_tracked():
    git = shutil.which("git")
    if not git:
        pytest.skip("git がありません")
    r = subprocess.run([git, "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0
    bad = [f for f in r.stdout.splitlines() if "p31" in f.lower() and f != "tools/fetch_p31.py"]
    assert bad == [], bad
    assert not [f for f in r.stdout.splitlines() if f.startswith("web/data/local/")]


def test_spec18_ui_page_shows_p31_only_after_checking_the_local_file():
    js = read("web/muni.js")
    assert "data/local/p31.json" in js
    # ファイルがないとき（404）は null にして、凡例に案内を出す。コミットしたデータには P31 を含めない
    assert "U.p31 = null" in js and "noP31" in js
    assert "p31" not in "".join(p.name for p in (ROOT / "web" / "data").glob("*.json"))


# ---------------------------------------------------------------- 4 狭い画面での畳み方
def test_spec18_ui_narrow_layout_rules_exist():
    css = read("web/style.css")
    assert "@media (max-width: 1100px)" in css and "m-right-collapsed" in css
    assert ".m-fold" in css and "#m-calc.folded" in css
    html = read("web/index.html")
    assert 'id="m-rfold"' in html and 'data-fold="m-calc"' in html
    assert html.count('class="lb-short"') == 3  # 要約の見出しの短い版
    # 凡例は左の列の中にあり、固定配置で他の枠に重ならない
    left = html[html.index('<div id="m-left"'):html.index('<aside id="m-right"')]
    assert 'id="m-legend"' in left
