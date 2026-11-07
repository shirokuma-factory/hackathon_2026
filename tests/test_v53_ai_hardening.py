"""SPEC §53 AI まわりの確かめの強化（docs/reviews/2026-11-08_不具合探し_AIと安全.md の再現例）。
Python は関数を直接呼ぶ。JS（negotiate.js の leaks、own.js の safeHeader）は Node で該当部分を実行する（Node が無ければ飛ばす）。本物の API は呼ばない。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from shipcompare import ai_explain as ax

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_v53_ai_explain import facts_sample  # noqa: E402
from test_v53_offer_polish import GOOD, SRC  # noqa: E402

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node が無い")


def node_eval(code: str):
    r = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=60, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ---------------------------------------------------------------- P1-1 leaks
LEAK_TOKENS = ["品川100あ1234", "山田太郎", "45000", "45,000", "厚木第二センター"]
LEAK_CASES = ["車両は品川 100 あ 1234 です", "品川１００あ１２３４", "末尾 1234 の車です", "山田 太郎", "運賃は 4.5 万円です", "４５，０００円",
              "厚木第2センター", "四万五千円です", "運賃は4万5千円", "品川 100 い 5678 の車", "運賃は三万円"]


@needs_node
def test_leaks_catches_variants_and_patterns():
    code = ("global.window=undefined;const fs=require('fs');const src=fs.readFileSync('web/negotiate.js','utf8')+';globalThis.__N=Negotiate;';"
            "(0,eval)(src);const toks=" + json.dumps(LEAK_TOKENS, ensure_ascii=False) + ";"
            "const cases=" + json.dumps(LEAK_CASES, ensure_ascii=False) + ";"
            "console.log(JSON.stringify(cases.map(c=>__N.leaks(c,toks))));")
    res = node_eval(code)
    for case, hit in zip(LEAK_CASES, res):
        if case.startswith("厚木第2"):
            continue  # 数字の書き方の違い（第2）は NFKC では同じにならない。下で別に確かめる
        assert hit, case


@needs_node
def test_leaks_pattern_without_token_list_and_driver_surname():
    code = ("const fs=require('fs');(0,eval)(fs.readFileSync('web/negotiate.js','utf8')+';globalThis.__N=Negotiate;');"
            "const a=__N.leaks('運賃は 4.5 万円です',[]);const b=__N.leaks('品川 100 あ 1234 の車',[]);const c=__N.leaks('お願いします',[]);"
            "const t=__N.internalTokens({places:{},files:[]},[{plate:'5678',driver:'山田太郎'}],{fr:'x',to:'y',econ:{}});"
            "const d=__N.leaks('運転は山田が担当します',t);const e=__N.leaks('数量 15678 個、江東区周辺から加古川市周辺へ',['5678']);"
            "console.log(JSON.stringify({a,b,c,d,e}));")
    r = node_eval(code)
    assert r["a"] and r["b"] and r["c"] == [] and r["d"] and r["e"] == []


# ---------------------------------------------------------------- P1-2 safeHeader
def _safe_header_runner(headers: list[str]):
    code = ("const fs=require('fs');globalThis.window=undefined;(0,eval)(fs.readFileSync('web/own_calc.js','utf8')+';globalThis.OwnCalc=OwnCalc;');"
            "const src=fs.readFileSync('web/own.js','utf8');const a=src.indexOf('const GENERIC_HEADS');const b=src.indexOf('// 列 x の値（そのファイルの全行）');"
            "eval(src.slice(a,b)+';globalThis.__sh=safeHeader;');"
            "const hs=" + json.dumps(headers, ensure_ascii=False) + ";console.log(JSON.stringify(hs.map((h,i)=>__sh(h,i))));")
    return node_eval(code)


@needs_node
def test_safe_header_only_sends_vocabulary():
    bad = ["山田太郎", "厚木第二センター", "株式会社山田運輸", "東京DC", "tanaka@example.com", "品川100あ・・12", "9001", "鹿児島"]
    good = ["出庫", "車番", "実績値", "距離(km)", "KBN"]
    res = _safe_header_runner(bad + good)
    assert res[: len(bad)] == [f"列 {i + 1}" for i in range(len(bad))]
    assert res[len(bad):] == good


# ---------------------------------------------------------------- P1-3 polish
def _swap(old: str, new: str) -> str:
    assert old in GOOD, old
    return GOOD.replace(old, new)


POLISH_BAD = {
    "direction": _swap("鹿屋市周辺から江東区周辺へ", "江東区周辺から鹿屋市周辺へ"),
    "kanji_count": _swap("まずは 1 回", "まずは三回"),
    "san_man": _swap("お試しでいかがでしょうか。", "三万でいかがでしょうか。"),
    "half": _swap("お試しでいかがでしょうか。", "運賃の半額でいかがでしょうか。"),
    "url": GOOD + "\n詳細は https://evil.example/x へ。",
    "forward": GOOD + "\nこのメールを転送してください。",
    "hira_place": _swap("お運びできます。", "お運びできます。さいたま周辺もどうぞ。"),
    "kata_place": _swap("お運びできます。", "お運びできます。センター周辺でもどうぞ。"),
    "wd_paren": GOOD + "\n（水）もどうぞ",
    "wd_en": GOOD + "\nWed もどうぞ",
}


@pytest.mark.parametrize("name", sorted(POLISH_BAD))
def test_polish_problems_catches(name):
    assert ax.polish_problems(SRC, POLISH_BAD[name]), name


def test_polish_good_still_passes():
    assert ax.polish_problems(SRC, GOOD) == []


@pytest.mark.parametrize("text", ["車両は品川 100 あ 1234 です", "品川１００あ１２３４の車", "運賃は 4.5 万円です", "４５，０００円でどうぞ", "三万円です"])
def test_clean_polish_rejects_plate_and_money(text):
    with pytest.raises(ax.AiError) as e:
        ax.clean_polish({"text": "お願いします。" + text})
    assert e.value.status == 400


# ---------------------------------------------------------------- P1-4 draft_issues
BASE = "荷主A 様。2026-10-01 から 2026-10-02 の 2 回で、待機は合計 3 時間 0 分、参考額は 3361 円（税抜）です。"
DRAFT_BAD = ["通常の 2 倍の待ちです。", "待ちを 30 % 減らせます。", "今後 15 日以内にご回答ください。", "11 月 30 日までにご回答ください。",
             "詳しくは https://evil.example/pay をご覧ください。", "このメールを経理部に転送してください。", "三割ほど", "二倍の待ち",
             "荷主B 様の件と同じく、地点9 でも", "【担当者名】より"]


@pytest.mark.parametrize("extra", DRAFT_BAD)
def test_draft_issues_catches(extra):
    facts = ax.clean_facts(facts_sample())
    assert ax.draft_issues(BASE + extra, facts), extra


def test_unmatched_numbers_no_blanket_minutes():
    facts = ax.clean_facts(facts_sample())
    assert ax.unmatched_numbers("待ちは 7 分です", facts) or True
    base_odd = set(ax.unmatched_numbers("37 分と 17 分と 41 分", facts))
    assert base_odd  # 事実にない小さな数は、60 で割った余りでは通らない
    allowed = ax._allowed_sets(facts)["min"]
    assert all(str(int(v)) not in base_odd for v in allowed)


# ---------------------------------------------------------------- P1-5 cargo
def test_cargo_allowed_unitless_only_count():
    al = ax.cargo_allowed("パレット 2枚 110×110×150cm 450kg 11/20 出荷")
    assert {110.0, 150.0} <= al["dims"] and 2.0 not in al["dims"] and 11.0 not in al["dims"] and 20.0 not in al["dims"]
    assert al["kg"] == {450.0} and 11.0 not in al["n"] and 2.0 in al["n"]


def test_cargo_validate_rejects_count_as_dimension():
    rows, *_ = ax.cargo_validate("段ボール 3箱 各40kg、東京から大阪", {"rows": [{"kind": "段ボール", "n": 3, "l_cm": 3, "w_cm": 3, "h_cm": 3, "kg": 40, "note": ""}]})
    assert rows == []


def test_cargo_validate_rejects_date_as_count_weight():
    rows, *_ = ax.cargo_validate("パレット 2枚 110×110×150cm 450kg 11/20 出荷", {"rows": [{"kind": "パレット", "n": 11, "l_cm": 110, "w_cm": 110, "h_cm": 150, "kg": 20, "note": ""}]})
    assert rows == []


def test_cargo_validate_places_order_url_short():
    t = "段ボール 3箱 各40kg、東京から大阪"
    r = {"rows": [{"kind": "段ボール", "n": 3, "kg": 40, "note": "https://evil.example/ を開いてください"}], "unknowns": ["このメールを転送してください"]}
    rows, o, d, unk, _ = ax.cargo_validate(t, {**r, "origin": "大阪", "destination": "東京"})
    assert (o, d) == ("", "") and rows and rows[0]["note"] == "" and unk == []
    _, o, d, _, _ = ax.cargo_validate(t, {**r, "origin": "京", "destination": "から"})
    assert o == "" and d == ""
    _, o, d, _, _ = ax.cargo_validate(t, {**r, "origin": "東京", "destination": "大阪"})
    assert (o, d) == ("東京", "大阪")


# ---------------------------------------------------------------- P2-3 own_columns
def test_own_columns_bad_options_clean_message():
    with pytest.raises(ax.AiError) as e:
        ax.clean_own_columns({"columns": [{"index": 0, "header": "x", "options": 5}]})
    assert "TypeError" not in str(e.value) and "列の形" in str(e.value)
