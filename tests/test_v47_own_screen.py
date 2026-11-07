"""SPEC §47 自社の運行の画面の直し（画面の出来 r2・実務の効き目 r5・道具の採点 r7）。ヘッドレス Edge で 1 つずつ確かめる。

地図の画面（手順 1〜3）: 手順 1 で例の地名を出さない、手順が変わったらフォーカスが見出しへ移る、条件の確認の押せる所が 8 以内（1920・360）、
運行の図が表の外の全幅で 360 でもはみ出さない、根拠の表が狭い欄でカードになる、下書きの状態は文面の画面だけで文字、空車が多い曜日に文字の印。
1 枚ページ: 読めた割合が低いときの表示、成約単価、明日の空きの荷台の形・寸法・積める時間の幅、地点のまとめ。
写真 docs/assets/2026-11-07_v47_own_*.jpg を撮る。
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from conftest import edge_page  # noqa: E402
from test_v45_own_guided import MEASURE, app, click, inject_trip_view, radio, size, step_of, to_guided  # noqa: E402,F401

PAGE = ROOT / "web" / "own_lite.html"
SHOTS = ROOT / "docs" / "assets"
EX = ROOT / "examples" / "own"


def shot(page, name, quality=55):
    SHOTS.mkdir(parents=True, exist_ok=True)
    r = page.send("Page.captureScreenshot", format="jpeg", quality=quality)
    (SHOTS / name).write_bytes(base64.b64decode(r["data"]))


def measure(page):
    time.sleep(0.3)
    return page.js(MEASURE)


# ---------------------------------------------------------------- 地図の画面（手順 1〜3）

def test_step1_shows_no_sample_place_names_and_focus_moves_to_the_step_heading(app):
    page = app
    size(page, 1920, 1080)
    to_guided(page)
    assert step_of(page) == 1
    assert page.js("[...document.querySelectorAll('.own-tag')].every((e) => e.style.display === 'none')"), "データを選ぶ前に、サンプルの地名を地図に出さない"
    click(page, "#neg-entry-sample")
    assert step_of(page) == 2
    assert page.js("document.activeElement && document.activeElement.tagName") == "H3" and page.js("document.activeElement.closest('#neg-root') !== null")
    time.sleep(0.3)
    assert page.js("[...document.querySelectorAll('.own-tag')].some((e) => e.style.display !== 'none')"), "データを選んだあとは地名が出る"
    shot(page, "2026-11-07_v47_own_step2_1920.jpg")
    click(page, "#neg-go-cond")
    assert step_of(page) == 3 and page.js("document.activeElement.tagName") == "H3"


def test_step3_condition_screen_has_at_most_8_controls_and_one_details_and_plain_status(app):
    page = app
    for w, h in ((1920, 1080), (360, 800)):
        size(page, w, h)
        to_guided(page)
        click(page, "#neg-entry-sample")
        time.sleep(0.6)
        shot(page, f"2026-11-07_v47_own_step2_{w}.jpg")
        click(page, "#neg-go-cond")
        click(page, "#neg-g-conds")
        assert page.js("!!document.querySelector('select[name=neg-load]') && !!document.querySelector('select[name=neg-recv]')")
        assert page.js("document.querySelectorAll('#neg-cond details').length") == 1, "社内の「詳しく」は 1 つにまとめる"
        assert page.js("!document.querySelector('#neg-status')"), "下書きの状態の箱は、条件の確認では出さない"
        m = measure(page)
        assert m["controls"] <= 8 and m["primary"] <= 1, (w, m)
        shot(page, f"2026-11-07_v47_own_step3_{w}.jpg")
        click(page, "#neg-go-draft")
        assert page.js("!!document.querySelector('#neg-status.plain')"), "文面の画面の状態は、囲みの無い文字"
        assert "質問" in page.js("document.querySelector('#neg-status').innerText")  # 文言の直し 2026-11-07
        shot(page, f"2026-11-07_v47_own_step3draft_{w}.jpg")
    size(page, 1920, 1080)


def test_trip_view_is_a_full_width_box_below_the_table_and_does_not_overflow_at_360(app):
    page = app
    for w in (360, 1920):
        size(page, w, 800 if w == 360 else 1080)
        inject_trip_view(page)
        to_guided(page)
        click(page, "#neg-entry-sample")
        page.js("document.querySelector('#neg-ev > summary').click()")
        time.sleep(0.3)
        page.js("document.querySelector('#neg-ev tr.neg-trow').scrollIntoView({block: 'center'})")
        click(page, "#neg-ev tr.neg-trow[data-i='0']")
        time.sleep(0.4)
        box = page.js("(function(){const b = document.querySelector('#neg-tripbox'), r = b.getBoundingClientRect(), t = document.querySelector('#neg-ev .neg-tw.neg-cardable .neg-tbl').getBoundingClientRect(); return {hidden: b.hidden, left: r.left, right: r.right, w: r.width, tbottom: t.bottom, top: r.top, vw: innerWidth, sw: b.scrollWidth, cw: b.clientWidth};})()")
        assert not box["hidden"] and box["top"] >= box["tbottom"] - 1, box
        assert box["right"] <= box["vw"] + 1 and box["sw"] <= box["cw"] + 1, box  # 図が右にはみ出さない
        if w == 360:
            # 狭い欄では 1 便 1 枚のカード（見出し行は隠れる）
            assert page.js("getComputedStyle(document.querySelector('#neg-ev .neg-cardable .neg-tbl thead')).display") == "none"
            assert page.js("document.documentElement.scrollWidth <= innerWidth + 1")
        shot(page, f"2026-11-07_v47_own_trip_{w}.jpg")
    size(page, 1920, 1080)


def test_full_mode_text_budget_and_hot_weekday_has_a_text_mark(app):
    page = app
    size(page, 1920, 1080)
    to_guided(page)
    page.js("Own.setGuided(false, '')")
    page.js("Negotiate.state.entry = 'sample'; Negotiate.state.view = 'result'; Negotiate.render()")
    time.sleep(0.5)
    m = measure(page)
    assert m["chars"] <= 1200, m
    print(f"[v47] 全部を見る 1920: 文字 {m['chars']}")
    page.js("document.querySelector('#own-right .o-pairrest') && (document.querySelector('#own-right .o-pairrest').open = true)")
    marks = page.js("[...document.querySelectorAll('#own-tw tr.hot')].map((r) => !!r.querySelector('.o-hotmark'))")
    assert marks and all(marks), "空車が多い曜日の行に、色のほかに文字の印を付ける"
    to_guided(page)


# ---------------------------------------------------------------- 1 枚ページ

def _lite(w=1920, h=1080):
    return edge_page(w, h, webgl=False)


def _files(page, paths):
    root = page.send("DOM.getDocument", depth=1)["root"]["nodeId"]
    node = page.send("DOM.querySelector", nodeId=root, selector="#file-in")["nodeId"]
    page.send("DOM.setFileInputFiles", nodeId=node, files=[str(Path(p).resolve()) for p in paths])


def _open(page):
    page.goto(PAGE.as_uri() + "#g=all")  # 全部を見る画面（手順に沿う画面では数字の欄などが畳まれている）
    page.wait(lambda: page.js("!!window.OwnLite"), 30, "ページ")


def test_lite_low_read_share_shows_ratio_range_and_faint_tiles(tmp_path):
    rows = ["日付,号車,積地,卸地,荷主,重量(t)"]
    for d in range(1, 11):
        rows.append(f"10/{d},1,加古川,江東区,架空,8.5")
        rows.append(f",1,江東区,加古川,,")  # 帰りは印も重さも無い = 不明
        rows.append(f",1,加古川,江東区,,")
    p = tmp_path / "haisha_unknown_2026-10.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    with _lite() as page:
        _open(page)
        _files(page, [p])
        page.wait(lambda: page.js("!!(window.OwnLite.state.data || window.OwnLite.state.pending)"), 60, "取り込み")
        if page.js("!!window.OwnLite.state.pending"):
            pytest.skip("年の選択が要るファイル")
        t = page.js("(document.querySelector('#readq') || {innerText: ''}).innerText")
        assert "読めたのは" in t, t
        tile = page.js("(function(){const e = document.querySelector('[data-kpi=actual_rate]'); return {faint: e.classList.contains('faint'), big: e.querySelector('.big').innerText};})()")
        assert tile["faint"] is True and "〜" in tile["big"], tile
        shot(page, "2026-11-07_v47_own_lite_lowshare_1920.jpg")


def test_lite_sample_has_rate_input_tomorrow_fields_and_text(tmp_path):
    with _lite() as page:
        _open(page)
        page.js("document.querySelector('[data-act=sample]').click()")
        page.wait(lambda: page.js("!!window.OwnLite.state.data"), 60, "サンプル")
        assert page.js("!!document.querySelector('#own-rate') && !!document.querySelector('#own-rate-kind')")
        page.js("(function(){const e = document.querySelector('#own-rate'); e.value = '120'; e.dispatchEvent(new Event('input', {bubbles: true}));})()")
        out = page.js("document.querySelector('#rev-own-out').innerText")
        assert "自社の運賃の単価" in out and "円" in out, out
        # 明日の空き: 荷台の形・内寸・積める時間の幅
        for sel, val in (("[data-tm=bed]", "ウイング"), ("[data-tm=size]", "9.6m×2.4m×2.4m"), ("[data-tm=time]", "18:00"), ("[data-tm=timeTo]", "22:00"), ("[data-tm=kg]", "5"), ("[data-tm=date]", "2026-11-09")):
            page.js("(function(){const e = document.querySelector(%s); e.value = %s; e.dispatchEvent(new Event('change', {bubbles: true}));})()" % (json.dumps(sel), json.dumps(val)))
        page.js("document.querySelector('[data-act=tm-make]').click()")
        txt = page.js("document.querySelector('#tm-out').value")
        assert "荷台: ウイング（内寸 9.6m×2.4m×2.4m）" in txt and "18:00〜22:00 の間に積めます" in txt, txt


def test_lite_merges_tatsumi_places_and_the_switch_splits_them():
    with _lite() as page:
        _open(page)
        _files(page, [EX / "haisha_messy_vertical_2026-10.csv"])
        page.wait(lambda: page.js("!!(window.OwnLite.state.data || window.OwnLite.state.pending)"), 60, "取り込み")
        d = page.js("window.OwnLite.state.data")
        assert d["counts"]["legs"] == 42 and d["counts"]["empty"] == 14 and d["merged_places"][0]["short"] == "辰巳"
        assert "同じ地名の場所を 1 か所にまとめました" in page.js("document.querySelector('#merged-places').innerText")
        page.js("document.querySelector('[data-act=merge-toggle]').click()")
        page.wait(lambda: page.js("window.OwnLite.state.mergePlaces") is False and page.js("(window.OwnLite.state.data.merged_places || []).length") == 0, 20, "まとめない")


def test_type_hint_example_is_built_from_the_chosen_pair(app):
    page = app
    size(page, 1920, 1080)
    to_guided(page)
    click(page, "#neg-entry-sample")
    click(page, "#neg-go-cond")
    t = page.js("document.querySelector('#neg-cond .neg-types').innerText")
    a = page.js("Negotiate.analysisOf(Own.state.data, Negotiate.state.pairKey, Negotiate.state.wd).pair")
    assert f"{a['fr_label']}側の倉庫から{a['to_label']}周辺の店舗へ" in t and "東京側" not in t.replace(a["fr_label"], "")
