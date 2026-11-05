"""SPEC §26 起動時に自動で入れる例（荷待ちのページと送り方比べのページ）。

- 文字列: 注意の行、フラグ、出典の文言、JITBOX の名前。
- ヘッドレス Edge: 起動で例が入る、保存されない、操作すると保存が始まる、?selftest=1 では入らない。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import artifact_build  # noqa: E402

PUB = ROOT / "web" / "publish"
FILES = {
    "wait_local": ROOT / "web" / "waiting.html",
    "wait_art": PUB / "waiting_artifact.html",
    "cmp_local": ROOT / "web" / "compare_lite.html",
    "cmp_art": PUB / "compare_lite_artifact.html",
}
WAIT_NOTE = "例の記録が入っています（架空の元請A物流）。『表を空にする』で自分の記録に入れ替えてください"
CMP_NOTE = "例の荷物が入っています（架空）。表を空にして、自分の荷物を入れてください"


def _read(k: str) -> str:
    return FILES[k].read_text(encoding="utf-8")


def test_notes_and_flags_are_in_all_builds():
    for k in ("wait_local", "wait_art"):
        h = _read(k)
        assert WAIT_NOTE in h and "S.exampleAuto" in h and "endExampleAuto" in h
    for k in ("cmp_local", "cmp_art"):
        h = _read(k)
        assert CMP_NOTE in h and "S.exampleAuto" in h and "endExampleAuto" in h


def test_compare_sources_and_jitbox_name():
    tariff = json.loads((ROOT / "data" / "tariffs" / "mlit_standard_fare_kinki.json").read_text(encoding="utf-8"))
    assert tariff["source_url"] == "https://www.mlit.go.jp/jidosha/content/001732621.pdf"
    assert "令和6年国土交通省告示第209号" in tariff["verified_by"]
    for k in ("cmp_local", "cmp_art"):
        h = _read(k)
        assert "佐川急便の公式ページで確認" in h and "未了" not in h
        assert "令和 6 年国土交通省告示第 209 号）と照合済み" in h
        assert "JITBOX チャーター便（西濃）" not in h
        assert "西濃運輸とヤマト運輸に同名のサービスがある" in h
        assert "令和6年国土交通省告示第209号、官報号外第66号94ページ" in h  # 埋め込んだ近畿の表


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _uri(k: str) -> str:
    p = FILES[k]
    if k.endswith("_art"):
        html = artifact_build.wrap_for_test(p.read_text(encoding="utf-8"))
        out = Path(tempfile.mkdtemp(prefix="autoex_")) / p.name
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
        return out.as_uri()
    return p.as_uri()


@pytest.mark.parametrize("k", ["wait_local", "wait_art"])
def test_waiting_example_loaded_not_stored_until_user_acts(k):
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1440, 900) as page:
        page.goto(_uri(k))
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "例")
        assert page.js("document.querySelector('#example-note').hidden") is False
        assert page.js("localStorage.getItem('waiting_page_rows_v1')") is None
        # 日付を 1 文字いじる = 操作。行が保存され、注意の行が消える
        page.js("var i = document.querySelector('.w-in[data-k=place]'); i.value = i.value + '甲'; i.dispatchEvent(new Event('input', {bubbles: true}))")
        page.wait(lambda: page.js("localStorage.getItem('waiting_page_rows_v1') !== null"), 10, "保存")
        assert page.js("document.querySelector('#example-note').hidden") is True
        # 保存があるときは、次の起動で例を入れず保存を読む
        page.goto(_uri(k))
        page.wait(lambda: page.js("document.querySelector('#keep-msg') && document.querySelector('#keep-msg').textContent.includes('読み込みました')"), 10, "復元")
        assert page.js("document.querySelector('#example-note').hidden") is True
        # 表を空にする
        page.js("localStorage.clear()")
        page.goto(_uri(k))
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "例")
        page.js("document.querySelector('#b-clear').click()")
        assert page.js("document.querySelector('#example-note').hidden") is True
        assert page.js("localStorage.getItem('waiting_page_rows_v1')") is None
        # ?selftest=1 では入らない
        page.goto(_uri(k) + "?selftest=1")
        page.wait(lambda: page.js("document.querySelector('#selftest').textContent.length > 0"), 30, "自己確認")
        assert page.js("document.querySelector('#example-note').hidden") is True


@pytest.mark.parametrize("k", ["wait_local", "wait_art"])
def test_waiting_example_button_then_stored(k):
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1440, 900) as page:
        page.goto(_uri(k))
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "例")
        page.js("localStorage.clear(); document.querySelector('#b-example').click()")
        page.wait(lambda: page.js("localStorage.getItem('waiting_page_rows_v1') !== null"), 10, "保存")
        assert page.js("document.querySelector('#example-note').hidden") is True


@pytest.mark.parametrize("k", ["cmp_local", "cmp_art"])
def test_compare_example_autoloaded_and_note_goes_away(k):
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1440, 900) as page:
        page.goto(_uri(k))
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        assert page.js("document.querySelector('#example-note').hidden") is False
        assert page.js("document.querySelector('[data-opt=jitbox] .nm').textContent") == "JITBOX チャーター便"
        assert "西濃運輸とヤマト運輸に同名のサービスがある" in page.js("document.querySelector('[data-opt=jitbox]').textContent")
        page.js("var i = document.querySelector('.pin'); i.value = '3'; i.dispatchEvent(new Event('input', {bubbles: true}))")
        assert page.js("document.querySelector('#example-note').hidden") is True
        page.goto(_uri(k))
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        page.js("document.querySelector('#b-clear').click()")
        assert page.js("document.querySelector('#example-note').hidden") is True
        page.goto(_uri(k) + "?selftest=1")
        page.wait(lambda: page.js("document.querySelector('#selftest').textContent.length > 0"), 30, "自己確認")
        assert page.js("document.querySelector('#example-note').hidden") is True
        assert page.js("document.querySelectorAll('#pbody tr').length") == 2
