"""compare_lite の荷役の条件（荷姿・フォークリフト・集荷の時刻）、出荷の記録、距離 0 以下の拒否（r6b #8・r8）。"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

PAGE = ROOT / "web" / "compare_lite.html"


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


# ---------------------------------------------------------------- 距離 0 以下（Python）
@pytest.mark.parametrize("km", ["0", "-1", "-250.5"])
def test_kashikiri_fares_rejects_zero_and_negative_km(km):
    from shipcompare.estimate import kashikiri_fares
    from shipcompare.tariffs import load_kashikiri
    with pytest.raises(ValueError, match="0 km より大きい"):
        kashikiri_fares(Decimal(km), Decimal(100), load_kashikiri("関東"))


def test_kashikiri_fares_still_works_at_base_km():
    from shipcompare.estimate import kashikiri_fares
    from shipcompare.tariffs import load_kashikiri
    t = load_kashikiri("関東")
    assert kashikiri_fares(Decimal(t["base_km"]), Decimal(100), t)


def test_page_has_handling_and_history_fields():
    html = PAGE.read_text(encoding="utf-8")
    for t in ('id="fk-from"', 'id="fk-to"', 'id="pickup-time"', "パレット", 'id="hist-d"', "HKEY"):
        assert t in html, t
    assert '<details class="tabd" id="hist-d">' in html   # 記録は閉じた <details>（最初の画面を散らかさない）


# ---------------------------------------------------------------- 画面
def _open(page, hash_=""):
    page.goto(PAGE.as_uri() + "?example=1")
    page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")


PALLET = ("S.rows = [{n:1,l:'110',w:'110',h:'120',kg:'280',pk:'pallet'}]; $('#from').value='埼玉県'; $('#to').value='愛知県'; $('#km').value='330';"
          "$('#fk-from').value='%s'; $('#fk-to').value='%s'; $('#pickup-time').value='09:30'; renderRows(); render();"
          "JSON.stringify({kei: document.querySelector('tr[data-opt=kei]').className, tk: document.querySelector('tr[data-opt=takuhai]').className,"
          " keiText: document.querySelector('tr[data-opt=kei]').textContent, quote: $('#quote').value})")


def test_pallet_280kg_is_not_ok_for_kei_and_takuhai():
    _edge_or_skip()
    import json
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        unk = json.loads(page.js(PALLET % ("unknown", "unknown")))
        assert unk["kei"] == "o-ng" and unk["tk"] == "o-ng", unk
        assert "フォークリフト" in unk["keiText"]
        no = json.loads(page.js(PALLET % ("yes", "no")))
        assert no["kei"] == "o-ng" and "届け先にフォークリフト・パワーゲートがない" in no["keiText"]
        yes = json.loads(page.js(PALLET % ("yes", "yes")))
        assert yes["kei"] != "o-ng" and yes["tk"] == "o-ng"
        assert "パレット渡し" in yes["quote"] and "09:30" in yes["quote"] and "フォークリフト・パワーゲートあり" in yes["quote"]


def test_km_zero_and_negative_show_a_message():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        for v in ("0", "-5"):
            page.js(f"$('#km').value='{v}'; render();")
            assert "0 km より大きい" in page.js("document.querySelector('#errs').textContent"), v


def test_history_adds_totals_by_month_and_survives_blocked_storage():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        assert page.js("document.querySelector('#hist-d').open") is False
        page.js("localStorage.removeItem('compare_lite_history_v1'); renderHistory();")
        page.js("document.querySelector('#h-date').value='2026-11-03'; document.querySelector('#h-amt').value='10,000'; document.querySelector('#b-hadd').click();")
        page.js("document.querySelector('#h-date').value='2026-11-05'; document.querySelector('#h-amt').value='2500'; document.querySelector('#b-hadd').click();")
        page.js("document.querySelector('#h-date').value='2026-10-30'; document.querySelector('#h-amt').value='700'; document.querySelector('#b-hadd').click();")
        t = page.js("document.querySelector('#h-month').textContent")
        assert "2026-11" in t and "12,500 円" in t and "2026-10" in t and "700 円" in t, t
        page.js("document.querySelector('#h-amt').value='abc'; document.querySelector('#b-hadd').click();")
        assert "0 より大きい" in page.js("document.querySelector('#h-msg').textContent")
        # 保存できない環境でも例外を出さず、そのページの間だけ記録が残る
        page.js("Storage.prototype.setItem = function () { throw new Error('blocked'); }; document.querySelector('#h-date').value='2026-11-06'; document.querySelector('#h-amt').value='300'; document.querySelector('#b-hadd').click();")
        assert "保存できませんでした" in page.js("document.querySelector('#h-msg').textContent")
        assert "2026-11-06" in page.js("document.querySelector('#h-list').textContent")
