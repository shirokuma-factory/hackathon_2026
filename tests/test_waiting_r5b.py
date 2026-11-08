"""リポジトリの採点 r5b の指摘（2026-11-08）。
1. 画面の偏りの表の平均・最長を、メールと同じ数え方（到着か指定の時刻から荷役開始まで）にする。荷主側が荷役をした回があるときは、その旨を言葉にも書く。
2. LINE のメモで契約の相手が無い行も、空欄と同じに入力欄を出し、メールの宛先は仮の文にする。
3. waiting.py も、契約の相手が空の行を defaults の provisional_partner で仮の相手にする（画面の JS と同じ）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
WAITING = ROOT / "web" / "waiting.html"
PROV = "（相手を入れてください）"

# 江東DC・8時台・3回。3 回とも荷主側が荷役をした回。
# 到着から荷役開始までは 60・60・70 分（平均 63、最長 70）。荷役の終了まで数えると 90・90・160 分（平均 113、最長 160）。
PASTE_SHIPPER = ("日付\t契約の相手\t場所\t車種\t到着\t荷役開始\t荷役終了\t荷役をした人\n"
                 "2026/10/5\tA商事\t江東DC\t中型\t8:00\t9:00\t9:30\t荷主側\n"
                 "2026/10/6\tA商事\t江東DC\t中型\t8:30\t9:30\t10:00\t荷主側\n"
                 "2026/10/7\tA商事\t江東DC\t中型\t8:10\t9:20\t10:50\t荷主側\n")
PASTE_DRIVER = PASTE_SHIPPER.replace("\t荷主側", "\t運転者\t手積み").replace("荷役をした人\n", "荷役をした人\t荷役の方法\n")
MEMO_NO_PARTNER_TEXT = "11/10 江東 着7:50 呼ばれ9:40 終わり10:30\n11/11 江東 着8:00 呼ばれ9:50 終わり10:40\n"


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _paste(page, text):
    page.goto(WAITING.as_uri() + "?selftest=0")
    page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "ページ")
    page.js("document.querySelector('#b-clear').click()")
    page.js("document.querySelector('#paste').value = " + json.dumps(text, ensure_ascii=False))
    page.js("document.querySelector('#b-paste').click()")
    page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "下書き")


def test_screen_hotspot_table_matches_email_numbers():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _paste(page, PASTE_SHIPPER)
        cells = page.js("[...document.querySelectorAll('#hotspots tbody tr:first-child td')].map(td => td.textContent.trim())")
        assert cells[1:4] == ["江東DC", "8時台", "3"]
        assert cells[4] == "63 分" and cells[5] == "70 分"
        # 荷主側の荷役の時間は入らないので、その注記は出さない
        assert "荷主側の荷役の時間を含む" not in cells[-1]
        assert "到着から荷役開始までの分" in page.js("document.querySelector('#hot-def').textContent")
        mail = page.js("draftText(S.res.by_partner.find((x) => x.partner === S.partner))")
        assert "平均63分、最長70分でした" in mail
        # 画面の表とメールの数字が同じ
        assert "平均113分" not in mail and cells[4] != "113 分"


def test_wording_names_shipper_side_handling_in_tile_and_email():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _paste(page, PASTE_SHIPPER)
        tile = page.js("document.querySelector('#stats .hero .stat-d').textContent")
        assert "荷役開始まで（荷主側で荷役をした回は荷役の終了まで）" in tile
        mail = page.js("draftText(S.res.by_partner.find((x) => x.partner === S.partner))")
        first = mail.split("\n\n")[2]
        assert "荷役が始まるまで（貴社側で荷役をしていただいた回は、荷役が終わるまで）にお待ちした時間は" in first
        # 荷主側の荷役が無い記録では、括弧を付けない
    with cw.edge_page(1920, 1080) as page:
        _paste(page, PASTE_DRIVER)
        tile2 = page.js("document.querySelector('#stats .hero .stat-d').textContent")
        mail2 = page.js("draftText(S.res.by_partner.find((x) => x.partner === S.partner))")
        assert "荷役の終了まで" not in tile2 and "荷役が終わるまで" not in mail2


def test_memo_without_partner_gets_input_box_and_placeholder_address():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _paste(page, MEMO_NO_PARTNER_TEXT)
        assert page.js("S.res.rows.length") == 2 and page.js("S.res.errors.length") == 0
        assert page.js("[...new Set(S.res.rows.map(r => r.partner))]") == [PROV]
        assert page.js("document.querySelector('#b-prov-fill').textContent") == "全部の行に入れる"
        assert "この記録の荷主（契約の相手）" in page.js("document.querySelector('#prov-box').textContent")
        assert page.js("document.querySelector('#mail-to').textContent") == "（荷主の会社名） 物流ご担当者様"
        assert "（相手未記入）" not in page.js("document.querySelector('#mail-to').textContent")
        page.js("document.querySelector('#prov-name').value = 'C商事'")
        page.js("document.querySelector('#b-prov-fill').click()")
        page.wait(lambda: page.js("!document.querySelector('#prov-box')"), 10, "入力欄が消える")
        assert page.js("[...new Set(S.res.rows.map(r => r.partner))]") == ["C商事"]
        assert page.js("document.querySelector('#mail-to').textContent") == "C商事 物流ご担当者様"


def _row(partner):
    return {"date": "2026-10-05", "vehicle_class": "小型", "partner": partner, "arrive": "09:00", "start": "10:00", "shipper_fault": "はい"}


def test_python_provisional_partner_matches_js_option():
    from shipcompare import waiting
    rows = [_row(""), _row("（相手未記入）"), _row("B商事")]
    off = waiting.calc(rows, 2026)
    assert [r["partner"] for r in off["rows"]] == ["(相手未記入)", "B商事"] and len(off["errors"]) == 1   # 空の相手は誤り。メモの印の相手はそのまま
    on = waiting.calc(rows, 2026, defaults={"provisional_partner": True})
    assert on["errors"] == [] and [r["partner"] for r in on["rows"]] == [PROV, PROV, "B商事"]
    assert [bool(r.get("partner_provisional")) for r in on["rows"]] == [True, True, False]
    # 相手の列が無い表（5 列）も、オプションを付ければ全行を計算する
    five = [{k: v for k, v in _row("").items() if k != "partner"} for _ in range(3)]
    assert len(waiting.calc(five, 2026)["rows"]) == 0
    assert len(waiting.calc(five, 2026, defaults={"provisional_partner": True})["rows"]) == 3


def test_vectors_include_provisional_cases():
    data = json.loads((ROOT / "tests" / "data" / "waiting_vectors.json").read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) and "cases" in data else data
    names = {c["name"] for c in cases}
    assert {"provisional_partner_for_rows_without_partner", "provisional_partner_also_for_memo_no_partner_rows",
            "without_provisional_partner_rows_without_partner_are_errors"} <= names
