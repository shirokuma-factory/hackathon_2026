"""版3の4段目: 市区町村の推定と物流の集積（SPEC §18、受入条件 G44・G46）。通信はしない。

データは data/ にコミット済みのものを読む（data/raw/ は読まない）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from shipcompare import muni

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def est():
    return muni.load_estimate()


@pytest.fixture(scope="module")
def econ():
    return json.loads((ROOT / "data/stats/muni_econ_2024.json").read_text(encoding="utf-8"))["areas"]


# ---------------------------------------------------------------- G44: 按分の合計が県の値と一致する
def test_spec18_flows_match_prefecture_balance(est):
    """県の Out − In が、backhaul_2021.json の県ごとの差（営業用トラック）と合う。"""
    bal = json.loads((ROOT / "data/stats/backhaul_2021.json").read_text(encoding="utf-8"))["modes"]["truck"]["balance_out_minus_in_t"]
    flows = muni.pref_flows()
    for pref in muni.PREFS:
        assert flows["out"][pref] - flows["in"][pref] == pytest.approx(bal[pref], abs=2.5), pref  # 組ごとに 0.1 t へ丸めた誤差（最大 46 組 × 0.05）
        assert est["prefectures"][pref]["out_t"] == pytest.approx(flows["out"][pref], abs=0.1)
        assert est["prefectures"][pref]["in_t"] == pytest.approx(flows["in"][pref], abs=0.1)


def test_spec18_allocation_sums_equal_prefecture_values(est):
    """県ごとに Σ_m Out_i × w_out(m) + 未配分 = Out_i、入荷も同じ（丸めの誤差内）。"""
    by_pref: dict[str, list[dict]] = {}
    for r in est["municipalities"].values():
        by_pref.setdefault(r["pref"], []).append(r)
    assert set(by_pref) == set(muni.PREFS)
    for pref, rows in by_pref.items():
        p = est["prefectures"][pref]
        tol = 0.05 * len(rows) + 0.2  # 市区町村ごとに小数 1 桁へ丸めた誤差
        s_out = sum(r["out_t"] for r in rows if r["out_t"] is not None)
        s_in = sum(r["in_t"] for r in rows if r["in_t"] is not None)
        assert s_out + p["unallocated_out_t"] == pytest.approx(p["out_t"], abs=tol), pref
        assert s_in + p["unallocated_in_t"] == pytest.approx(p["in_t"], abs=tol), pref
        assert p["unallocated_out_t"] >= -tol and p["unallocated_in_t"] >= -tol, pref


def test_spec18_unallocated_is_small_where_nothing_is_gray(est):
    """灰色の市区町村がなく、表にない市区町村もない県では、未配分はほぼ 0（東京都の境界未定地域などは別に数える）。"""
    for pref, p in est["prefectures"].items():
        if p["n_gray"] == 0 and p["out_t"] > 1000:
            assert abs(p["unallocated_out_t"]) / p["out_t"] < 0.01, pref
            assert abs(p["unallocated_in_t"]) / max(p["in_t"], 1) < 0.001, pref


def test_spec18_weights_are_recomputed_from_raw_tables(est, econ):
    """分け方の確認: 東京都の区の出荷の推定が、元の表の従業者数から手で計算した値と合う。"""
    pop = json.loads((ROOT / "data/stats/muni_pop_2020.json").read_text(encoding="utf-8"))["areas"]
    flows = muni.pref_flows()

    def w(code):
        a = econ[code]["v"]
        return sum((a[k][1] or 0) for k in ("E", "44", "47"))

    denom = w("13000")
    code = "13101"  # 千代田区
    assert est["municipalities"][code]["out_t"] == pytest.approx(flows["out"]["東京都"] * w(code) / denom, abs=0.06)
    assert est["municipalities"][code]["in_t"] == pytest.approx(flows["in"]["東京都"] * pop[code]["pop"] / pop["13000"]["pop"], abs=0.06)
    r = est["municipalities"][code]
    assert r["balance_t"] == pytest.approx(r["out_t"] - r["in_t"], abs=0.11)


def test_spec18_wholesale_is_not_in_the_weight(est):
    assert "卸売" in est["formula"] and "入れない" in est["formula"]
    r = est["municipalities"]["13101"]
    assert "mfg_trucking_warehouse_employees" in r and "mfg_wholesale_warehouse_employees" not in r


def test_spec18_allocate_never_turns_none_into_zero():
    out = muni.allocate({"a": 10.0, "b": None, "c": 30.0}, 400.0, denom=100.0)
    assert out["b"] is None
    assert out["a"] == pytest.approx(40.0) and out["c"] == pytest.approx(120.0)
    # 分母を渡さないときは、重みのある分だけで配りきる
    out2 = muni.allocate({"a": 10.0, "b": None, "c": 30.0}, 400.0)
    assert out2["a"] + out2["c"] == pytest.approx(400.0)
    assert muni.allocate({"a": None}, 5.0) == {"a": None}


# ---------------------------------------------------------------- 秘匿・数字なしは 0 にしない
def test_spec18_suppressed_values_are_none_not_zero(est, econ):
    # 元の表: 倉庫業の事業所が 1 つあり、従業者数が数字でない（秘匿）
    sample = econ["19346"]  # 市川三郷町
    assert sample["v"]["47"][0] == 1 and sample["v"]["47"][1] is None
    r = est["municipalities"]["19346"]
    assert r["warehousing_47"]["status"] == "secret"
    assert r["warehousing_47"]["employees"] is None
    assert r["warehousing_47"]["employees_per_km2"] is None
    assert r["warehousing_47"]["establishments"] == 1
    assert r["out_t"] is None and r["balance_t"] is None and r["gray_reason"]


def test_spec18_every_gray_municipality_has_no_numbers_and_a_reason(est):
    gray = [(c, r) for c, r in est["municipalities"].items() if r["balance_t"] is None]
    assert gray, "秘匿・欠測の市区町村が 1 つもないのは不自然（表の読み方が変わった可能性）"
    for code, r in gray:
        assert r["gray_reason"], code
        assert r["out_t"] is None or r["in_t"] is None, code
        assert r["balance_t"] is None, code
    for code, r in est["municipalities"].items():
        if r["balance_t"] is not None:
            assert r["gray_reason"] is None, code


def test_spec18_concentration_states():
    area = {"v": {"44": [5, 120, 110], "47": [1, None, None], "50": [None, None, None]}}
    ok = muni.concentration(area, "44", 10.0)
    assert ok == {"status": "ok", "establishments": 5, "employees": 120,
                  "establishments_per_km2": 0.5, "employees_per_km2": 12.0}
    secret = muni.concentration(area, "47", 10.0)
    assert secret["status"] == "secret" and secret["employees"] is None and secret["employees_per_km2"] is None
    assert secret["establishments"] == 1
    none = muni.concentration(area, "50", 10.0)  # 事業所数も *** → 事業所がない（0 件）
    assert none["status"] == "none" and none["establishments"] == 0 and none["employees"] == 0
    missing = muni.concentration(None, "44", 10.0)
    assert missing["status"] == "missing" and missing["employees"] is None and missing["establishments"] is None
    assert muni.concentration(area, "44", None)["employees_per_km2"] is None


def test_spec18_hamamatsu_wards_are_gray_not_zero(est):
    """2024 年に区が再編された浜松市の旧区は、表に対応がないので None（0 にしない）。"""
    for code in ("22131", "22132", "22133", "22134", "22135", "22136", "22137"):
        r = est["municipalities"][code]
        assert r["out_t"] is None and r["trucking_44"]["status"] == "missing"
        assert r["trucking_44"]["employees"] is None


def test_spec18_national_totals_match_the_table(econ):
    """事業所数も従業者数も *** を 0 件と読む根拠: 県の行の合計 = 全国（倉庫業）。"""
    prefs = [c for c in econ if c.endswith("000") and c != "00000"]
    assert len(prefs) == 47
    for k in (0, 1):
        assert sum(econ[c]["v"]["47"][k] or 0 for c in prefs) == econ["00000"]["v"]["47"][k]
        assert sum(econ[c]["v"]["44"][k] or 0 for c in prefs) == econ["00000"]["v"]["44"][k]


def test_spec18_concentration_known_values(est, econ):
    r = est["municipalities"]["13101"]  # 千代田区
    assert r["trucking_44"]["establishments"] == econ["13101"]["v"]["44"][0]
    assert r["area_km2"] == pytest.approx(11.66, rel=0.15)  # 千代田区は約 11.7 km²（簡略化した境界なので幅を持たせる）
    assert r["trucking_44"]["employees_per_km2"] == pytest.approx(r["trucking_44"]["employees"] / r["area_km2"], rel=0.01)
    # 東京都全体の面積は約 2,194 km²。島しょ部を含む N03 の合計がおおよそ合う
    total = sum(v["area_km2"] for v in est["municipalities"].values() if v["pref"] == "東京都")
    assert 1900 < total < 2500


# ---------------------------------------------------------------- 幾何
def test_spec18_sphere_area_of_one_degree_box():
    ring = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
    assert abs(muni.ring_area_km2(ring)) == pytest.approx(12364, rel=0.01)
    hole = [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75], [0.25, 0.25]]
    assert muni.polygons_area_km2([[ring, hole]]) == pytest.approx(12364 * 0.75, rel=0.01)


def test_spec18_locator_points_in_polygons():
    loc = muni.Locator(muni.load_municipalities())
    assert loc.locate(139.6917, 35.6895) == "13104"  # 東京都庁（新宿区）
    assert loc.locate(135.5023, 34.6937) is not None
    assert loc.locate(150.0, 10.0) is None  # 太平洋


def test_spec18_locator_respects_holes():
    square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    loc = muni.Locator([{"code": "X", "polys": [[square, hole]]}])
    assert loc.locate(1, 1) == "X"
    assert loc.locate(5, 5) is None


# ---------------------------------------------------------------- 取得したデータの形
def test_spec18_osm_counts_are_consistent():
    pts = ROOT / "data/geo/osm_logistics_points.json"
    cnt = ROOT / "data/geo/osm_logistics_counts.json"
    if not pts.exists() or not cnt.exists():
        pytest.skip("OSM の取得結果がない（tools/fetch_osm_logistics.py を実行する）")
    p = json.loads(pts.read_text(encoding="utf-8"))
    c = json.loads(cnt.read_text(encoding="utf-8"))
    assert p["license"].startswith("ODbL") and "OpenStreetMap" in p["attribution"]
    for t_idx, t in enumerate(p["types"]):
        n_points = sum(1 for row in p["items"] if row[2] == t_idx)
        assert c["totals"][t] == n_points
        in_munis = sum(v.get(t, 0) for v in c["municipalities"].values())
        assert in_munis + c["unmatched"][t] == n_points, t
    assert c["totals"]["warehouse"] > 30000


def test_spec18_c28_airports_have_attribution():
    d = json.loads((ROOT / "data/geo/c28_airports_2021.json").read_text(encoding="utf-8"))
    assert "国土数値情報" in d["source"] and "商用可" in d["terms"]
    assert len(d["items"]) >= 90
    assert any(a["name"] == "成田国際空港" for a in d["items"]) or any("成田" in a["name"] for a in d["items"])


def test_spec18_stats_files_carry_attribution():
    for fn in ("muni_econ_2024.json", "muni_pop_2020.json", "muni_estimate.json"):
        d = json.loads((ROOT / "data/stats" / fn).read_text(encoding="utf-8"))
        assert "e-Stat" in d["source"] and "加工して作成" in d["source"], fn
        assert "政府標準利用規約" in d["terms"] or "e-Stat 利用規約" in d["terms"], fn
    e = muni.load_estimate()
    assert e["formula"] and e["disclaimer"].startswith("推定です")


# ---------------------------------------------------------------- G46: P31 がリポジトリに入っていない
def _git_files() -> list[str]:
    if shutil.which("git") is None:
        pytest.skip("git がない")
    res = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        pytest.skip("git のリポジトリではない")
    return res.stdout.splitlines()


def test_spec18_g46_no_raw_or_p31_files_tracked():
    files = _git_files()
    bad = [f for f in files if f.startswith("data/raw/") or "P31-" in Path(f).name or "p31_points" in Path(f).name]
    assert not bad, f"P31 または data/raw/ がコミット対象に入っています: {bad[:5]}"


def test_spec18_g46_gitignore_blocks_p31():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/raw/p31/" in ignore and "p31_points" in ignore


def test_spec18_g46_no_p31_in_committed_data():
    """data/ の中（コミットされる場所）に、P31 の点が混ざっていない。"""
    for fn in (ROOT / "data").rglob("*.json"):
        if "raw" in fn.relative_to(ROOT / "data").parts[:1]:
            continue
        head = fn.read_bytes()[:400].decode("utf-8", errors="ignore")
        assert "P31-13" not in head, fn.name
