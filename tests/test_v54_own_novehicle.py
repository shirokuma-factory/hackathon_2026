"""自社の運行: 車番の列が無い CSV、ファイルの走行 km、曜日の数え方、誤字（2026-11-08 のページ評価の指摘）。

- 車番の列が無い CSV（運行日,発地,着地,発時刻,着時刻,走行km。Shift_JIS）は、車両 1 台として自動で読める（JS の OwnCalc.analyze）
- 走行km の列があれば、その値を距離に使い、出どころを「ファイルの走行km」にする。推定は使わない
- 見出しが辞書にある列は役割を聞かない。日付の形の値には「車番」を候補にしない
- 帰りの向きの曜日は、案内文と同じく帰りの便が出た日の曜日（1 枚のページの表）
- 「。。」「（）」が、ソースにも画面の文字にも無い
Python の own.py には車番の列を足す処理が無い（この読み方は JS だけ。配車表の 3 つの CSV の一致は test_v35 が見る）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from conftest import edge_page, edge_path_or_skip

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_own_page  # noqa: E402

PAGE = ROOT / "web" / "own_lite.html"
SJIS_ROWS = ["運行日,発地,着地,発時刻,着時刻,走行km"] + [f"2026/10/{d:02d},江東,鹿屋,8:00,22:00,1415" for d in (1, 2, 6, 8, 10)]


def _no_vehicle_csv(tmp_path: Path) -> Path:
    p = tmp_path / "unten_nippo.csv"
    p.write_bytes(("\r\n".join(SJIS_ROWS) + "\r\n").encode("cp932"))
    return p


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_sources_have_no_double_period_or_empty_parentheses():
    for name in ("web/own.js", "web/own_calc.js", "tools/own_page_template.html", "tools/build_own_page.py"):
        t = _text(ROOT / name)
        assert "。。" not in t, name
        assert "（）" not in t, name
    for name in ("web/own_lite.html", "web/publish/own_lite_artifact.html"):
        t = _text(ROOT / name)
        assert "。。" not in t and "（）" not in t, name
    assert "今月の数字を見る）" not in _text(ROOT / "web" / "own.js")


def test_generated_pages_are_up_to_date():
    assert _text(PAGE) == build_own_page.build()


def _load_calc(page, port):
    page.goto(f"http://localhost:{port}/data/meta.json")
    page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_calc.js'; s.onload = ok; s.onerror = () => ng(new Error('own_calc.js')); document.head.appendChild(s); })")


def test_csv_without_vehicle_column_is_read_as_one_vehicle_with_the_files_km(local_server, tmp_path):
    edge_path_or_skip()
    raw = _no_vehicle_csv(tmp_path).read_bytes()
    text = raw.decode("cp932")
    with edge_page(1280, 800, webgl=False) as page:
        _load_calc(page, local_server)
        d = page.js(f"""(function () {{
          const d = OwnCalc.analyze([{{ name: 'unten_nippo.csv', text: {json.dumps(text, ensure_ascii=False)} }}], {{}});
          return {{ failed: d.failed, legs: d.legs.map((l) => [l.plate, l.km, l.km_src]), notice: (d.notices || []).map((n) => n.kind + ':' + n.text_ja),
                   need: (d.column_roles_needed || []).map((c) => c.column), file: d.files[0], read: d.files_read, unread: d.files_unread.length,
                   cols: d.columns.map((c) => c.column) }};
        }})()""")
        assert d["failed"] is None and d["read"] == 1 and d["unread"] == 0
        assert len(d["legs"]) == 5
        assert all(p == "1号車" and km == 1415 and src == "ファイルの走行km" for p, km, src in d["legs"])   # 推定 1,381.7 km にしない
        assert any(n.startswith("no_plate_col:") and "車両 1 台（車番の列なし）" in n for n in d["notice"])
        assert d["file"]["no_plate_col"] is True and d["file"]["vehicle_label_ja"] == "車両 1 台（車番の列なし）"
        assert d["need"] == []                                   # 発時刻・走行km は辞書で決まり、聞かない
        assert "車番" not in d["cols"]                            # 足した列は「車番の列」として確かめに出さない
        # 車番の列があるファイルは、足さない
        with_plate = "\n".join(["車番," + SJIS_ROWS[0]] + ["1234," + r for r in SJIS_ROWS[1:]])
        d2 = page.js(f"""(function () {{
          const d = OwnCalc.analyze([{{ name: 'with_plate.csv', text: {json.dumps(with_plate, ensure_ascii=False)} }}], {{}});
          return {{ no: !!(d.files[0] || {{}}).no_plate_col, plates: d.vehicles.map((v) => v.plate) }};
        }})()""")
        assert d2["no"] is False and d2["plates"] == ["1234"]


def test_dictionary_known_headers_are_not_asked_and_date_values_are_not_a_plate(local_server):
    edge_path_or_skip()
    csv = "ひづけ,運行日,謎の列\n2026/10/01,2026/10/01,abc\n2026/10/02,2026/10/02,def\n"
    with edge_page(1280, 800, webgl=False) as page:
        _load_calc(page, local_server)
        d = page.js(f"""(function () {{
          const d = OwnCalc.analyze([{{ name: 'x.csv', text: {json.dumps(csv, ensure_ascii=False)} }}], {{}});
          return {{ failed: !!d.failed, need: (d.column_roles_needed || []).map((c) => [c.column, c.suggested_role]) }};
        }})()""")
        assert d["failed"] is True
        names = [n for n, _ in d["need"]]
        assert "運行日" not in names                              # 見出しが辞書にある列は聞かない
        assert ["ひづけ", "date"] in d["need"]                    # 日付の形の値の候補は車番ではなく日付


def _set_files(page, paths):
    root = page.send("DOM.getDocument", depth=1)["root"]["nodeId"]
    node = page.send("DOM.querySelector", nodeId=root, selector="#file-in")["nodeId"]
    page.send("DOM.setFileInputFiles", nodeId=node, files=[str(Path(p).resolve()) for p in paths])


def test_one_page_no_vehicle_file_reads_and_text_has_no_double_period(tmp_path):
    edge_path_or_skip()
    p = _no_vehicle_csv(tmp_path)
    with edge_page(1920, 1080, webgl=False) as page:
        page.goto(PAGE.as_uri())
        page.wait(lambda: page.js("!!window.OwnLite"), 30, "ページ")
        _set_files(page, [p])
        page.wait(lambda: page.js("!!window.OwnLite.state.data"), 60, "読み込み")
        body = page.js("document.body.innerText")
        assert "車両 1 台（車番の列なし）" in body
        assert "ファイル 1 件（区間 5 件）を読みました" in body
        assert "1 件を読みました" not in body
        assert "。。" not in body and "（）" not in body
        assert "下で列の中身を選ぶと" not in body


def test_one_page_back_rows_use_the_return_trips_departure_weekday():
    edge_path_or_skip()
    with edge_page(1920, 1080, webgl=False) as page:
        page.goto(PAGE.as_uri())
        page.wait(lambda: page.js("!!window.OwnLite"), 30, "ページ")
        page.js("document.querySelector('[data-act=sample]') && document.querySelector('[data-act=sample]').click()")
        page.wait(lambda: page.js("!!window.OwnLite.state.data"), 60, "サンプル")
        r = page.js("""(function () {
          const d = window.OwnLite.state.data, rows = weekdayRows(d).filter((t) => t.role === 'back');
          const own = {};
          for (const t of rows) own[t.wd] = (own[t.wd] || 0) + t.trips;
          const pr = d.pairs.find((p) => p.role === 'back');
          const m = OwnCalc.returnTrips(d.legs, pr.frm, pr.to, d.base, d.dock, pr.id);
          const ret = {};
          for (const r of m.rows) if (r.wd != null) ret[r.wd] = (ret[r.wd] || 0) + 1;
          const text = document.body.innerText;
          return { own, ret, text_ok: !text.includes('。。') && !text.includes('（）') };
        })()""")
        assert r["own"], "帰りの向きの行が無い"
        assert {k: v for k, v in r["own"].items()} == {k: v for k, v in r["ret"].items()}   # 案内文の曜日の数え方と同じ
        assert r["text_ok"]
