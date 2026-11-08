"""SPEC §30.4 地図の画面の荷待ちのタブを waiting.html にそろえる（受入条件 G132）。

- 静的な確認（文字列）: 共有の JS（web/waiting_calc.js）をページに埋め込んでいること、リンク、読み込みの順。
- 画面の確認: 地図の画面（tools/serve_web.py + ヘッドレス Edge）で例を入れ、waiting.html の例と同じ数字が出て、点と凡例があること。
  Edge が無い環境だけ飛ばす。Edge があるのに起動・接続・読み込みが時間切れになったら失敗にする（SPEC §38 TQ-4・TQ-5）。
  Edge は --disable-extensions/--disable-sync（EDGE_CLEAN_FLAGS）つきで起動し、about:blank のページに接続する。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from conftest import edge_page, fail_with_evidence, open_app

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_waiting_page  # noqa: E402

WEB = ROOT / "web"
SHARED = (WEB / "waiting_calc.js").read_text(encoding="utf-8")
OWN_JS = (WEB / "own.js").read_text(encoding="utf-8")
MAP_JS = (WEB / "waiting_map.js").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "tools" / "waiting_page_template.html").read_text(encoding="utf-8")
PAGE = (WEB / "waiting.html").read_text(encoding="utf-8")
ART = (WEB / "publish" / "waiting_artifact.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 共有の JS
def test_shared_js_is_embedded_in_both_pages_and_the_template_has_no_second_copy():
    body = SHARED.strip("\n").replace("</", "<\\/")
    assert body in PAGE and body in ART
    for src in (PAGE, ART):
        assert src.count("function hotspots(") == 1 and src.count("function byPlaceMonth(") == 1
    assert "function hotspots(" not in TEMPLATE and "function byPlaceMonth(" not in TEMPLATE
    assert "/*__WAITING_CALC__*/" in TEMPLATE
    assert PAGE == build_waiting_page.build()
    assert "WaitingShared" in OWN_JS and "WaitingShared" in MAP_JS


def test_shared_js_makes_no_network_calls_and_has_no_print_or_download():
    for bad in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "window.print", "download"):
        assert bad not in SHARED, bad


def test_map_example_is_the_same_as_the_page_example():
    """地図の画面の「例を入れる」は web/waiting_calc.js の例（waiting.html と同じ）を使う。古い 11 行の例は残さない。"""
    assert "WaitingShared.exampleRows" in OWN_JS
    assert "W_EXAMPLE_DAYS" not in OWN_JS
    assert "const EXAMPLE = [" in SHARED and "江東区 地点07" in SHARED and "架空 元請A物流" in SHARED


# ---------------------------------------------------------------- リンクと読み込みの順
def test_index_loads_the_shared_scripts_before_own_js():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    a, b, c = (html.index(f'<script src="{n}">') for n in ("waiting_calc.js", "waiting_map.js", "own.js"))
    assert a < b < c


def test_map_tab_links_to_the_waiting_page():
    assert 'href="waiting.html"' in OWN_JS and "荷待ちの書面を別の画面で開く" in OWN_JS  # 文言の直し 2026-11-07


def test_waiting_page_has_a_back_link_and_the_artifact_has_text_only():
    assert "地図の画面に戻る" in TEMPLATE and '<a href="index.html">地図の画面に戻る</a>' in PAGE
    assert "index.html" not in ART
    assert "荷待ちの場所は、地図の画面の「自社の運行」で見られます。" in ART   # 文言の直し 2026-11-07
    assert not re.search(r'<a [^>]*href="(?!#)', ART.split("<main>")[0])  # 上部に外へ出るリンクが無い（ページ内の # リンクは案内の操作なので許す）


def test_unmatched_places_and_legend_texts_are_in_the_map_script():
    for text in ("地図に置けない場所", "平均 ", "30 分未満", "30〜59 分", "60〜119 分", "120 分以上", "大きさ = 荷待ちの計"):
        assert text in MAP_JS, text
    assert "荷待ちを減らすためのご相談" in SHARED and "consultHtml" in OWN_JS


# ---------------------------------------------------------------- 画面（Edge）
def test_g132_map_tab_numbers_match_the_waiting_page_example_and_points_have_a_legend(local_server):
    import capture_web as cw

    port = local_server
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK,)) as page:
        open_app(page, f"http://localhost:{port}/index.html#v=own&ot=wait", start_timeout=120, load_timeout=120, tag="waiting_map_fail")
        try:
            page.wait(lambda: page.js("!!document.querySelector('[data-w=example]')"), 60, "例を入れるボタン")
            page.js("document.querySelector('[data-w=example]').click()")
            page.wait(lambda: page.js("!!document.querySelector('#hotspots') && document.querySelectorAll('.wm-pt').length > 0"), 60, "表と点")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "地図の画面の表と点が出ません", e, "waiting_map_fail")

        # 参考額（相手ごとの合計の和）は 46,780 円
        amounts = page.js("[...document.querySelectorAll('table.w-par:not(.wm-card) tbody tr')].map(tr => tr.children[4].textContent.replace(/,/g, '')).join(',')")
        assert sum(int(x) for x in amounts.split(",")) == 46_780
        # 2026-11-08 の直しに合わせて: 地図の画面では匿名の呼び名（地点07）が地名に置き換わる（own.js の plainPlaces。地図の点にするため市区町村名の江東区は残る）。江東区の 8時台は平均 98 分（どこで・いつ待っているか）
        row = page.js("[...document.querySelectorAll('#hotspots tbody tr')].map(tr => tr.textContent.replace(/\\s+/g, ' ')).find(t => t.includes('架空 元請A物流江東区8時台'))")
        assert row and "98 分" in row, page.js("[...document.querySelectorAll('#hotspots tbody tr')].map(tr => tr.textContent.replace(/\s+/g, ' ')).join(' | ')")
        # 場所ごと・月ごと: 58 / 60 / 32 分
        pm = page.js("[...document.querySelectorAll('#placemonth tbody tr')].map(tr => [tr.children[0].textContent, tr.children[3].textContent.replace(/[^0-9]/g, '')])")
        assert {k: v for k, v in pm} == {"江東区": "58", "市川市 地点03": "60", "川崎市 地点12": "32"}
        # 注記・打ち切りの文・ご相談の節
        assert page.js("!!document.querySelector('#trunc-line')") and page.js("!!document.querySelector('#pm-def')")
        assert "荷待ちを減らすためのご相談" in page.js("document.querySelector('#wait-doc').textContent")
        assert page.js("!!document.querySelector('#doc-placemonth')")
        # 点と凡例（3 か所の場所が点になり、地図に置けない場所は無い）
        assert page.js("document.querySelectorAll('.wm-pt').length") == 3
        assert page.js("document.querySelectorAll('.wm-lg-i').length") == 4
        assert not page.js("!!document.querySelector('#wm-unplaced')")
        # 点を押すと、その場所の時間帯ごとの行が出る
        page.js("document.querySelector('.wm-pt[data-place=\"江東区\"]').click()")
        page.wait(lambda: page.js("!!document.querySelector('#wm-detail-tab')"), 10, "場所の詳細")
        rows = page.js("[...document.querySelectorAll('#wm-detail-tab tbody tr')].map(tr => tr.children[1].textContent)")
        assert set(rows) == {"8時台", "9時台", "10時台"}
        # 地図に置けない場所: 市区町村名で始まらない場所の行を足す
        page.js("document.querySelector('[data-w=add]').click()")
        page.wait(lambda: page.js("document.querySelectorAll('#w-tbody tr').length") > 18, 10, "行を足す")
        page.js("""(function(){const tr=[...document.querySelectorAll('#w-tbody tr')].pop(); const set=(k,v)=>{const e=tr.querySelector('[data-k="'+k+'"]'); e.value=v; e.dispatchEvent(new Event('input',{bubbles:true}));};
          set('date','2026-10-20'); set('partner','架空 元請A物流'); set('place','架空 第3倉庫'); set('vehicle_class','中型'); set('arrive','09:00'); set('start','09:40'); set('shipper_fault','はい');})()""")
        page.wait(lambda: page.js("!!document.querySelector('#wm-unplaced')"), 20, "地図に置けない場所")
        assert "架空 第3倉庫" in page.js("document.querySelector('#wm-unplaced').textContent")
        assert page.js("document.querySelectorAll('.wm-pt').length") == 3
        assert not page.js("window.__errs.length")
