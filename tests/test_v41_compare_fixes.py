"""v4.1: 床の割合 100% 超、丸めた超過の文、巨大な数、比べるページの契約単価、kit の起動、README（実務の効き目 r1 を受けた修正）。"""

from __future__ import annotations

import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

from shipcompare import loadplan, spaceb  # noqa: E402
from shipcompare.models import parse_number_text  # noqa: E402

from test_v21_backhaul import s6_body, t7  # noqa: E402

PAGE = ROOT / "web" / "compare_lite.html"


def _ship(l_cm, w_cm, h_cm, kg, count=1):
    body = s6_body()
    body["shipment"] = {"items": [{"count": count, "length_cm": l_cm, "width_cm": w_cm, "height_cm": h_cm, "weight_kg": kg}]}
    return spaceb.parse_shipment(body)


# ---------------------------------------------------------------- 1. 床の割合 100% 超
def test_floor_ratio_exactly_100_percent_is_priced():
    t = t7()
    assert t["floor_total_m2"] == 13.64
    ref = spaceb.per_unit_reference(t, _ship(1364, 100, 50, 10), "0.7", "0", "近畿")
    assert ref["floor_ratio"] == 1.0 and ref["reference_yen"] > 0


def test_floor_ratio_over_100_percent_is_refused():
    t = t7()
    with pytest.raises(ValueError, match=r"この車に載りません（床）") as e:
        spaceb.per_unit_reference(t, _ship(1440, 100, 50, 10), "0.7", "0", "近畿")  # 14.4m² on 13.64m²
    assert "+0.76m²" in str(e.value)
    with pytest.raises(ValueError, match=r"この車に載りません（床）"):
        spaceb.per_unit_reference(t, _ship(1364.1, 100, 50, 10), "0.7", "0", "近畿")  # 0.0001m² だけ超える


def test_default_register_price_refuses_floor_over():
    t = dict(t7(), tariff_region="近畿")
    r = spaceb.default_register_price(t, _ship(1440, 100, 50, 10))
    assert r["price_ex_tax_yen"] is None and "この車に載りません（床）" in r["basis"]
    ok = spaceb.default_register_price(t, _ship(1364, 100, 50, 10))
    assert ok["price_ex_tax_yen"] and ok["price_ex_tax_yen"] > 0


# ---------------------------------------------------------------- 2. 丸めた超過の文
def test_weight_excess_message_shows_the_hidden_part():
    t = t7()
    with pytest.raises(ValueError) as e:
        spaceb.per_unit_reference(t, _ship(100, 100, 100, "2700.01"), "0.7", "0", "近畿")
    msg = str(e.value)
    assert "2,700.01kg" in msg and "+0.01kg" in msg
    assert "2,700kg が 2,700kg" not in msg and "100%" not in msg


def test_match_excess_messages_never_print_zero_or_same_numbers():
    t = t7()
    ok, reasons = spaceb.match(_ship(100, 100, 100, "2000.01"), t)
    assert not ok
    joined = " ".join(reasons)
    assert "+0.01kg" in joined and "2,000.01kg" in joined
    ok2, reasons2 = spaceb.match(_ship(100.1, 60, 100, 100, count=10), t)  # 床 6.006m² > 空き 6.00m²
    assert not ok2
    j2 = " ".join(reasons2)
    # 2026-11-08 の直しに合わせて: 床の超過は小数 2 桁にそろえて出す（空き 6.00m²、荷 6.01m²、+0.01m²）。差が 0 に見える桁には丸めない
    assert "床の空き 6.00m² に対して荷の床 6.01m²" in j2 and "+0.01m²" in j2 and "+0.00m²" not in j2
    assert "0.0kg 超え" not in j2


def test_loadplan_over_text():
    assert loadplan._over_text(0.01) == "0.01kg"
    assert loadplan._over_text(0.004) == "0.004kg"
    assert loadplan._over_text(12.34) == "12.3kg"
    assert loadplan._over_text(3.0) == "3.0kg"


# ---------------------------------------------------------------- 3. 指数の書き方と巨大な数
@pytest.mark.parametrize("bad", ["1e400", "1E400", "１ｅ４００", "9" * 400, "1e3", 10 ** 400, 1e300])
def test_huge_or_exponent_numbers_are_not_numbers(bad):
    assert parse_number_text(bad) is None
    body = s6_body()
    body["shipment"] = {"items": [{"count": 1, "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_kg": bad}]}
    with pytest.raises(ValueError) as e:
        spaceb.parse_shipment(body)
    assert "数で入れてください" in str(e.value)
    assert not re.search(r"\d{20}", str(e.value)) and len(str(e.value)) < 200


def test_ordinary_numbers_still_parse():
    assert parse_number_text("１２．５") == Decimal("12.5")
    assert parse_number_text(" 2700 ") == Decimal(2700)
    assert parse_number_text(0.5) == Decimal("0.5")
    assert parse_number_text("999999999999999") == Decimal("999999999999999")


# ---------------------------------------------------------------- 6・7. kit
def test_kit_example_data_js_matches_json():
    text = (ROOT / "examples" / "kit" / "data" / "data.js").read_text(encoding="utf-8")
    marker = chr(10) + "window.KIT_DATA="
    payload = text[text.index(marker) + len(marker):].rstrip().rstrip(";")
    data = json.loads(payload)
    for name in ("flows", "legs", "points", "trips", "vehicle", "placement"):
        assert data[name] == json.loads((ROOT / "examples" / "kit" / "data" / f"{name}.json").read_text(encoding="utf-8")), name
    html = (ROOT / "examples" / "kit" / "index.html").read_text(encoding="utf-8")
    assert 'src="data/data.js"' in html and "KIT_DATA" in html


def test_kit_controls_are_24px():
    css = (ROOT / "web" / "kit" / "kit.js").read_text(encoding="utf-8")
    assert re.search(r"\.kit-det summary\{[^}]*min-height:24px", css)
    assert re.search(r"\.kit-rng\{[^}]*min-height:24px", css)


def test_find_checkbox_css_24px():
    css = (ROOT / "web" / "find_extra.css").read_text(encoding="utf-8")
    assert re.search(r"\.f-chk input \{ width: 24px; height: 24px; \}", css)


def test_readme_has_try_now_section_and_limits():
    s = (ROOT / "README.md").read_text(encoding="utf-8")
    # 11/7 の書き直し: 誰でも開ける公開版（Cloud Run）の URL を「すぐ試す」に置く。共有された人しか開けない Artifact の URL は置かない
    head = s.split("## 手元で動かす")[0]
    assert s.index("## すぐ試す") < s.index("## 手元で動かす")
    assert "run.app/waiting.html" in head and "run.app/compare_lite.html" in head
    assert "claude.ai/artifact/" not in s
    assert "uv run python tools/serve_web.py" in s
    assert "実在の会社のファイルでは、まだ確かめていません" in s


# ---------------------------------------------------------------- 5. 比べるページ
def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def test_template_no_longer_shows_rosen_note_on_kei_row():
    html = PAGE.read_text(encoding="utf-8")
    assert "NO_ESTIMATE_IDS" not in html and "const NO_ESTIMATES" in html
    assert "自社の契約単価" in html and "compare_lite_contract_v1" in html


def test_compare_lite_contract_rates_in_headless_edge():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(PAGE.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        got = page.js("""(function(){
          const t = (id) => { const tr = document.querySelector('tr[data-opt=' + id + ']'); return tr ? tr.textContent : ''; };
          return {kei: t('kei'), rosen: t('rosen'), cmp: document.querySelector('#cmp').textContent};
        })()""")
        assert "目安の額は出せません。契約単価を入れると額が出ます。" not in got["kei"]   # 文言の直し 2026-11-07
        assert "目安の額は出せません。契約単価を入れると額が出ます。" in got["rosen"]
        assert "まだ比べられません" in got["cmp"] and "契約単価" in got["cmp"]
        # 小さな荷 1 個。宅配 100 サイズ 900 円、路線便 15 円/kg・最低 3,000 円
        page.js("""(function(){
          const set = (sel, v) => { const el = document.querySelector(sel); el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); };
          const rows = document.querySelectorAll('#pbody tr');
          document.querySelector('#b-clear').click();
          const cell = (i, k, v) => set('input.pin[data-i="' + i + '"][data-k="' + k + '"]', v);
          cell(0, 'n', '1'); cell(0, 'l', '40'); cell(0, 'w', '30'); cell(0, 'h', '20'); cell(0, 'kg', '5');
          set('#c-t-100', '900'); set('#c-r-rate', '15'); set('#c-r-min', '3000');
        })()""")
        page.wait(lambda: "宅配で送れます" in page.js("document.querySelector('#cmp').textContent"), 10, "比べる")   # 2026-11-08 の直しに合わせて（「宅配で足ります」→「宅配で送れます」）
        cmp_text = page.js("document.querySelector('#cmp').textContent")
        assert "いちばん安いのは" in cmp_text and "宅配便" in cmp_text
        kei = page.js("document.querySelector('tr[data-opt=kei]').textContent")
        assert "目安の額は出せません。契約単価を入れると額が出ます。" not in kei and "会員登録して見積もります" in kei   # 文言の直し 2026-11-07
        order = page.js("Array.from(document.querySelectorAll('#cmp table.cmpt tbody tr td.num')).map((td) => Number(td.textContent.replace(/[^0-9]/g, '')))")
        assert order == sorted(order) and len(order) >= 2
        assert "大きすぎ" in page.js("document.querySelector('tr[data-opt=kashikiri_4t]').textContent")   # 2026-11-08 の直しに合わせて（「過大」→「大きすぎ」）
        assert page.js("JSON.parse(localStorage.getItem('compare_lite_contract_v1')).tk['100']") == "900"
        # 読み直しても残る
        page.goto(PAGE.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        assert page.js("document.querySelector('#c-t-100').value") == "900"
        # 消す
        page.js("document.querySelector('#b-cclear').click()")
        assert page.js("document.querySelector('#c-t-100').value") == ""
        # 保存が使えなくても動く
        page.js("Object.defineProperty(window, 'localStorage', {get() { throw new Error('blocked'); }}); document.querySelector('#b-example').click()")
        assert page.js("!!document.querySelector('#cmp')") is True
