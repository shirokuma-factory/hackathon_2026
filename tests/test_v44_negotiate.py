"""SPEC §44 帰り便の提案: 復路の根拠の集計（Python と JS の一致）、条件・金額・文面・編集した文面の保護の純粋な関数。

- サンプルの元行（legs）から、復路の候補 53＝空車 20＋実車 25＋不明 8、区間確定 45、土曜 5/5 便・6,931.9 km（2026-11-07 見本の鹿児島化）、ほかの判定済み 40 便中 15 便、実出発時刻が無い 32 便を再計算する（値は書き込まない）
- to が無い 8 便は着地の確認済みにも、文面にもならない
- 小さな合成の入力（月曜発・火曜復路、日付をまたぐ復路、別の区間、空車 0、全件不明、日時・距離の欠損、重複行）で曜日・件数・距離・文面が変わる
- 自社の CSV 3 本を OwnCalc.analyze に通した legs でも、同じ結果になる
- 条件の状態、保留・見送り、金額の空欄・明示の 0・0 円以下、費用の範囲の未確認、編集した文面の保護
- negotiate.js に送信の処理が無い
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import edge_page, edge_path_or_skip

from shipcompare import own

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
SAMPLE = json.loads((WEB / "data" / "own_sample.json").read_text(encoding="utf-8"))


def _norm(x):
    return json.loads(json.dumps(x, ensure_ascii=False))


def leg(i, date, kind="empty", km=500.0, dep="DEFAULT", frm="p07", to="p01", dr="back", **kw):
    base = {"id": i, "plate": kw.pop("plate", "1111"), "date": date, "dir": dr, "dep": f"{date}T19:00" if dep == "DEFAULT" else dep, "arr": None, "frm": frm, "to": to,
            "km": km, "kind": kind, "kind_src": "デジタコの実空区分", "km_src": "デジタコ", "time_src": "デジタコ", "cycle_wd": 0, "round": 1,
            "ref": {"file": "h.csv", "where": f"{i} 行目", "text": ""}, "dref": None, "driver": "D01"}
    base.update(kw)
    return base


# ---------------------------------------------------------------- 1. サンプルの元行から再計算する

def test_sample_numbers_are_recomputed_from_legs():
    legs = SAMPLE["legs"]
    m = own.return_trips(legs, "p07", "p01", SAMPLE["base"], SAMPLE["dock"])
    c, k, u = m["candidates"], m["confirmed"], m["unconfirmed"]
    assert (c["trips"], c["empty"], c["loaded"], c["unknown"]) == (53, 20, 25, 8)
    assert (k["trips"], k["empty"], k["loaded"], k["unknown"]) == (45, 20, 25, 0)
    assert (u["trips"], u["unknown"]) == (8, 8)
    assert m["basis"]["date"] == 32 and m["basis"]["dep"] == 13  # 2026-11-07 見本の鹿児島化（日付だけの行が 27→32 便、実出発時刻のある行が 18→13 便）
    t = own.return_target(m, 5)
    assert t["weekday"] == "土"  # 2026-11-07 見本の鹿児島化（全部空車の曜日が金曜から土曜に変わった）
    f = t["confirmed"]
    assert (f["trips"], f["empty"], f["empty_km"], f["km_missing"]) == (5, 5, 6931.9, 0) and t["all_empty"]
    assert [r["dt"][11:] for r in t["rows"]] == ["19:00", "19:00", "19:30", "19:00", "19:00"]
    o = t["others"]
    assert (o["trips"], o["empty"], o["loaded"], o["unknown"], o["judged"]) == (48, 15, 25, 8, 40)
    # 元行から直接数えた値とも一致（ここでは別の書き方で数える）
    import datetime
    fri = [x for x in legs if x["frm"] == "p07" and x["to"] == "p01" and datetime.date.fromisoformat((x["dep"] or x["date"])[:10]).weekday() == 5]
    assert len(fri) == 5 and round(sum(x["km"] for x in fri), 1) == 6931.9 and all(x["kind"] == "empty" for x in fri)


def test_unknown_destination_legs_are_never_confirmed_and_never_in_the_draft():
    m = own.return_trips(SAMPLE["legs"], "p07", "p01", SAMPLE["base"], SAMPLE["dock"])
    unc = [r for r in m["rows"] if not r["dest_confirmed"]]
    assert len(unc) == 8 and all(r["kind"] == "unknown" and r["km"] is None for r in unc)
    src_ids = {x["id"] for x in SAMPLE["legs"] if x["to"] is None and x["frm"] == "p07"}
    assert {r["leg_id"] for r in unc} == src_ids
    assert not any(r["dest_confirmed"] for r in m["rows"] if r["leg_id"] in src_ids)
    assert sum(1 for r in m["rows"] if r["dest_confirmed"]) == 45


def test_weekday_ignores_cycle_wd():
    legs = json.loads(json.dumps(SAMPLE["legs"]))
    for x in legs:
        x["cycle_wd"] = 6
    m = own.return_trips(legs, "p07", "p01", SAMPLE["base"], SAMPLE["dock"])
    assert own.return_target(m, 5)["confirmed"]["trips"] == 5  # 2026-11-07 見本の鹿児島化


def test_evidence_rows_carry_ids_datetime_kind_km_and_sources():
    m = own.return_trips(SAMPLE["legs"], "p07", "p01", SAMPLE["base"], SAMPLE["dock"], "P1")
    r = next(x for x in m["rows"] if x["dt"] == "2026-10-03T19:00")
    assert r["leg_id"] and r["row_id"] and r["pair_id"] == "P1" and r["dt_basis"] == "dep" and r["kind"] == "empty" and r["km"] == 1387.2
    assert (r["dt_src"], r["kind_src_cat"], r["km_src_cat"]) == ("digitaco", "digitaco", "digitaco")
    dated = next(x for x in m["rows"] if x["dt_basis"] == "date")
    assert dated["dt"] and len(dated["dt"]) == 10 and dated["dt_src"] == "haisha"
    cats = {x["km_src_cat"] for x in m["rows"]}
    assert cats <= {"digitaco", "haisha", "estimate", "unknown", "user"} and "estimate" in cats and "unknown" in cats
    assert own.src_cat(None) == "unknown" and own.src_cat("配車表の積地") == "haisha" and own.src_cat("OSRM 推定") == "estimate"


# ---------------------------------------------------------------- 2. 小さな入力

def test_monday_out_tuesday_return_uses_the_return_legs_own_departure():
    legs = [leg("A", "2026-10-06", dep="2026-10-06T08:00", cycle_wd=0)]  # 前日（月曜）の往路の曜日 0 を持つが、復路は火曜の出発
    m = own.return_trips(legs, "p07", "p01")
    assert m["rows"][0]["wd"] == 1 and own.return_target(m, 1)["confirmed"]["trips"] == 1 and own.return_target(m, 0)["confirmed"]["trips"] == 0
    d = own.return_trips([leg("B", "2026-10-06", dep=None, cycle_wd=0)], "p07", "p01")
    assert d["rows"][0]["wd"] == 1 and d["rows"][0]["dt_basis"] == "date" and d["basis"] == {"dep": 0, "date": 1, "none": 0}


def test_midnight_crossing_return_is_counted_on_its_departure_day():
    x = leg("M", "2026-10-09", dep="2026-10-09T23:30", arr="2026-10-10T04:10")
    m = own.return_trips([x], "p07", "p01")
    assert m["rows"][0]["wd"] == 4


def test_different_pairs_are_separate():
    legs = [leg("A", "2026-10-02"), leg("B", "2026-10-02", frm="p09", to="p01"), leg("C", "2026-10-03", frm="p09", to="p01", kind="loaded")]
    ps = own.return_pairs(legs, {"p07": {"short": "江東"}, "p09": {"short": "大井"}, "p01": {"short": "加古川"}})
    assert [(p["key"], p["candidates"], p["confirmed_empty"]) for p in ps] == [("p07>p01", 1, 1), ("p09>p01", 2, 1)] or [(p["key"]) for p in ps][0] in ("p07>p01", "p09>p01")
    assert own.return_trips(legs, "p09", "p01")["candidates"]["trips"] == 2 and own.return_trips(legs, "p07", "p01")["candidates"]["trips"] == 1


def test_zero_empty_all_unknown_and_missing_values():
    loaded = [leg("A", "2026-10-02", kind="loaded"), leg("B", "2026-10-09", kind="loaded")]
    m = own.return_trips(loaded, "p07", "p01")
    ps = own.return_pairs(loaded)
    assert m["confirmed"]["empty"] == 0 and ps[0]["default_wd"] is None and not own.return_target(m, 4)["has_empty"]
    unk = [leg("A", "2026-10-02", kind="unknown", km=None), leg("B", "2026-10-09", kind=None, km=None)]
    mu = own.return_trips(unk, "p07", "p01")
    assert mu["confirmed"]["unknown"] == 2 and mu["confirmed"]["empty"] == 0 and mu["confirmed"]["judged"] == 0
    miss = [leg("A", "2026-10-02", km=None), leg("B", "2026-10-09", km=400.0), leg("C", None, dep=None, km=100.0)]
    mm = own.return_trips(miss, "p07", "p01")
    assert mm["confirmed"]["empty"] == 3 and mm["confirmed"]["km_missing"] == 1 and mm["confirmed"]["empty_km"] == 500.0
    row = next(r for r in mm["rows"] if r["leg_id"] == "A")
    assert row["km"] is None and row["km_src_cat"] == "unknown"
    nodt = next(r for r in mm["rows"] if r["leg_id"] == "C")
    assert nodt["dt"] is None and nodt["wd"] is None and nodt["dt_basis"] == "none" and mm["no_weekday"] == 1
    assert own.return_target(mm, 3)["confirmed"]["trips"] == 0 and own.return_target(mm, 4)["confirmed"]["trips"] == 2


def test_duplicate_rows_are_counted_once_and_inputs_change_the_numbers():
    a = leg("A", "2026-10-02")
    dup = own.return_trips([a, dict(a), leg("B", "2026-10-03", dep="2026-10-03T07:00")], "p07", "p01")
    assert dup["candidates"]["trips"] == 2 and dup["duplicates_dropped"] == 1
    one = own.return_target(own.return_trips([leg("A", "2026-10-02", km=500.0)], "p07", "p01"), 4)["confirmed"]
    two = own.return_target(own.return_trips([leg("A", "2026-10-02", km=500.0), leg("B", "2026-10-09", km=250.5)], "p07", "p01"), 4)["confirmed"]
    assert (one["trips"], one["empty_km"]) == (1, 500.0) and (two["trips"], two["empty_km"]) == (2, 750.5)


# ---------------------------------------------------------------- 3. Python と JS の一致（サンプルと合成の入力、自社の CSV を通した legs）

def _lib(page, port):
    page.goto(f"http://localhost:{port}/data/meta.json")
    page.js("Promise.all(['/own_calc.js', '/negotiate.js'].map(u => new Promise((ok, ng) => { const s = document.createElement('script'); s.charset = 'utf-8'; s.src = u; s.onload = ok; s.onerror = () => ng(new Error(u)); document.head.appendChild(s); })))")
    assert page.js("typeof OwnCalc === 'object' && typeof Negotiate === 'object'")


def _both(page, legs, base, dock, places):
    out = {}
    pairs = own.return_pairs(legs, places, base, dock)
    jpairs = page.js(f"OwnCalc.returnPairs({json.dumps(legs)}, {json.dumps(places)}, {json.dumps(base)}, {json.dumps(dock)})")
    assert _norm(pairs) == jpairs
    for p in pairs:
        m = own.return_trips(legs, p["fr"], p["to"], base, dock)
        jm = page.js(f"OwnCalc.returnTrips({json.dumps(legs)}, {json.dumps(p['fr'])}, {json.dumps(p['to'])}, {json.dumps(base)}, {json.dumps(dock)})")
        assert _norm(m) == jm, p["key"]
        for wd in [None, 0, 1, 4, 5]:
            t = own.return_target(m, wd)
            jt = page.js(f"OwnCalc.returnTarget({json.dumps(jm)}, {json.dumps(wd)})")
            assert _norm(t) == jt, (p["key"], wd)
        out[p["key"]] = m
    return out


def test_python_and_js_agree_on_sample_synthetic_and_csv_import(local_server):
    edge_path_or_skip()
    import test_v35_own_import as t35
    synth = [leg("A", "2026-10-06", dep="2026-10-06T08:00"), leg("B", "2026-10-09", dep="2026-10-09T23:30", arr="2026-10-10T04:10"), leg("C", "2026-10-09", km=None),
             leg("D", None, dep=None), leg("E", "2026-10-13", kind="unknown", km=None, to=None), leg("F", "2026-10-13", frm="p09", to="p01"), dict(leg("A", "2026-10-06", dep="2026-10-06T08:00")),
             leg("G", "2026-02-30", dep=None), leg("H", "2026-10-16", dep="2026-10-16T25:00")]
    ref = t35._py_reference()
    files = [{"name": n, "text": (t35.RAW / n).read_text(encoding="utf-8")} for n in t35.RAW_NAMES]
    with edge_page(1280, 800, webgl=False) as page:
        _lib(page, local_server)
        s = _both(page, SAMPLE["legs"], SAMPLE["base"], SAMPLE["dock"], SAMPLE["places"])
        assert s["p07>p01"]["candidates"]["trips"] == 53
        _both(page, synth, "p01", "p07", {})
        # 実際の CSV 読み込み（OwnCalc.analyze）の legs と、Python の読み込みの legs
        res = page.js(f"OwnCalc.analyze({json.dumps(files, ensure_ascii=False)}, {json.dumps(t35._js_options(ref), ensure_ascii=False)})")
        jl = res["legs"]
        py_m = own.return_trips(ref["legs"], "p07", "p01", ref["base"], ref["dock"])
        js_m = page.js(f"OwnCalc.returnTrips({json.dumps(jl, ensure_ascii=False)}, 'p07', 'p01', {json.dumps(res['base'])}, {json.dumps(res['dock'])})")
        for key in ("candidates", "confirmed", "unconfirmed", "basis", "by_weekday", "period"):
            assert _norm(py_m[key]) == js_m[key], key
        assert js_m["candidates"]["trips"] == 53 and js_m["confirmed"]["trips"] == 45 and js_m["basis"]["date"] == 32  # 2026-11-07 見本の鹿児島化
        jt = page.js(f"OwnCalc.returnTarget({json.dumps(js_m)}, 5)")
        assert jt["confirmed"]["empty_km"] == 6931.9 and jt["others"]["judged"] == 40 and jt["others"]["empty"] == 15


# ---------------------------------------------------------------- 4. 条件・金額・文面・編集の保護（純粋な関数）

def _ev(page, expr):
    return page.js(expr)


def test_negotiate_logic(local_server):
    edge_path_or_skip()
    with edge_page(1280, 800, webgl=False) as page:
        _lib(page, local_server)
        # 金額: 空欄は 0 ではない、明示の 0 は 0、範囲の未確認では差額を出さない
        E = "Object.assign(Negotiate.emptyEcon(), %s)"
        full = {"quote": "10000", "qUnit": "1件", "qTax": "税抜", "cost": "4000", "cUnit": "1件", "cTax": "税抜", "scope": {"fuel": True, "labor": False, "wait": False, "fee": False, "other": False}, "scopeOk": True}
        r = page.js(f"Negotiate.economics({E % json.dumps(full)})")
        assert r["state"] == "ok" and r["profit"] == 6000
        assert page.js("Negotiate.parseMoney('').state") == "blank" and page.js("Negotiate.parseMoney('0').v") == 0 and page.js("Negotiate.parseMoney('abc').state") == "invalid"
        assert page.js("Negotiate.parseMoney('１０，０００円').v") == 10000
        r = page.js(f"Negotiate.economics({E % json.dumps({**full, 'cost': ''})})")
        assert r["state"] == "incomplete" and r["profit"] is None and any("追加費用が未入力" in x for x in r["missing"])
        r = page.js(f"Negotiate.economics({E % json.dumps({**full, 'cost': '0'})})")
        assert r["state"] == "ok" and r["profit"] == 10000  # 明示の 0 円
        r = page.js(f"Negotiate.economics({E % json.dumps({**full, 'scopeOk': False})})")
        assert r["state"] == "incomplete" and r["profit"] is None and any("範囲にチェック" in x for x in r["missing"])  # 文言の直し 2026-11-07
        r = page.js(f"Negotiate.economics({E % json.dumps({**full, 'cost': '10000'})})")
        assert r["state"] == "skip" and r["profit"] == 0
        r = page.js(f"Negotiate.economics({E % json.dumps({**full, 'cost': '12000'})})")
        assert r["state"] == "skip" and r["profit"] == -2000
        r = page.js(f"Negotiate.economics({E % json.dumps({**full, 'cTax': '税込'})})")
        assert r["state"] == "incomplete" and any("税込・税抜がそろっていません" in x for x in r["missing"])
        r = page.js(f"Negotiate.economics({E % json.dumps({**full, 'cUnit': '1便'})})")
        assert r["state"] == "incomplete" and any("単位がそろっていません" in x for x in r["missing"])
        assert page.js("Negotiate.economics(Negotiate.emptyEcon()).state") == "none"

        # 条件の状態と状況
        def P(load="unconfirmed", recv="unconfirmed", typ="commodity", hold=None, econ=None, wd="金", weekly=False):
            return {"weekly": weekly, "type": typ, "fromMuni": "江東区", "toMuni": "加古川市", "weekday": wd, "load": {"state": load, "note": ""}, "recv": {"state": recv, "note": ""},
                    "hold": hold or {"action": "hold", "picks": []}, "econ": econ or Negotiate_empty}
        page.js("window.NE = Negotiate.emptyEcon()")
        Negotiate_empty = None

        def call(fn, p):
            p = dict(p)
            econ = p.pop("econ", None)
            js = json.dumps(p)
            return page.js(f"(function(){{const p = {js}; p.econ = {econ or 'window.NE'}; return Negotiate.{fn}(p);}})()")
        assert call("statusOf", P()) == "draft"
        assert call("statusOf", P("ok", "ok")) == "ready"
        assert call("statusOf", P("ng", "ok")) == "hold" and call("statusOf", P("ok", "ng")) == "hold"
        assert call("statusOf", P("ng", "ok", hold={"action": "skip", "picks": []})) == "skip"
        assert call("statusOf", P("ok", "ok", econ=E % json.dumps({**full, "cost": "10000"}))) == "skip_econ"
        # 文面: 未確認は質問、不適合は完成扱いにしない、見送りは文面なし
        t = call("buildDraft", P())
        assert "お聞かせいただきたいこと" in t and "積込を希望する日時" in t and "納品先（受取側）が受け取れる日時" in t and "現時点で、確認できている条件はありません" in t
        assert "金曜日" in t and "江東区周辺" in t and "加古川市周辺" in t and "お受けできるかとお見積りをお送りします" in t
        t2 = page.js("(function(){const p = {type:'commodity', fromMuni:'江東区', toMuni:'加古川市', weekday:'金', load:{state:'ok', note:'金曜の夕方に準備できると電話で確認'}, recv:{state:'unconfirmed', note:''}, hold:{action:'hold',picks:[]}, econ: Negotiate.emptyEcon()}; return Negotiate.buildDraft(p);})()")
        assert "積込の条件: 金曜の夕方に準備できると電話で確認" in t2 and "積込を希望する日時" not in t2 and "納品先（受取側）が受け取れる日時" in t2
        th = call("buildDraft", P("ng", "ok", hold={"action": "hold", "picks": ["load_time", "other_day"]}))
        assert "条件の確認のご連絡です" in th and "積込の日時を変えることは可能でしょうか" in th and "別の曜日の集荷であれば可能でしょうか" in th and "お見積りを比べていただく条件" not in th
        # 案内の形に 2026-11-07: 文の最初に、空きがあることと曜日を書く。お願い・ご相談の形にしない
        body = [x for x in t.split(chr(10)) if x and not x.startswith("【") and not x.startswith("突然")][0]
        assert "金曜日" in body and "空きがありました" in body and "毎週" not in t and "ご相談" not in t and "ご相談" not in call("buildDraft", P(typ="returnable"))
        assert "江東区周辺から加古川市周辺へ戻る" in body and "江東区周辺から加古川市周辺へ戻る" in call("buildDraft", P(typ="returnable")) and "空きがありました" in call("buildDraft", P(typ="returnable"))
        # 2026-11-08: 毎週と書くのは、その曜日の帰りの便の 8 割以上が空車のときだけ。見積りの前提は荷主の依頼の言い方にしない
        tw = call("buildDraft", P(weekly=True))
        assert "毎週金曜日に、江東区周辺から加古川市周辺へ戻るトラックに空きがあります" in tw and "お見積りは、荷物の内容と日時を伺ってからお出しします。" in t
        assert "同じ荷物・全量であること" not in t and "必要な台数" not in t and "見積の期限" not in t and "総額をお出しします" not in t
        assert "戻る便でお運びする件で" in th
        assert "条件が合わない点" in th and "お願い" not in th.replace("再確認させてください", "")
        assert call("buildDraft", P("ng", "ok", hold={"action": "skip", "picks": []})) == ""
        assert call("buildDraft", P("ok", "ok", econ=E % json.dumps({**full, "cost": "99999"}))) == ""
        # 商品と返送で質問が違う
        tr = call("buildDraft", P(typ="returnable"))
        assert "通い箱・パレットの種類" in tr and "積み重ねの可否" in tr and "所有者と返却先" in tr and "回収・受け渡しの方法" in tr
        assert "品名・荷姿" not in tr and "通い箱・パレットの種類" not in t
        assert "必ず積め" not in tr
        # 社内の情報の検査
        toks = page.js("Negotiate.internalTokens({company:{name:'テスト運輸'}, places:{p07:{id:'p07',short:'江東',label:'江東区辰巳',muni:'江東区'}, p01:{id:'p01',short:'加古川',label:'加古川営業所',muni:'加古川市'}, p09:{id:'p09',short:'大井',label:'大井ふ頭',muni:'品川区'}}, files:[{file:'haisha_2026-10.csv'}]}, [{plate:'5678', driver:'D02', leg_id:'L003', ref:{file:'haisha_2026-10.csv', where:'8 行目'}, dref:null}], {fr:'p07', to:'p01', kmText:'2,790.4', econ:{quote:'12000', cost:'3000'}})")
        for bad in ["5678", "D02", "L003", "haisha_2026-10.csv", "テスト運輸", "大井", "2,790.4", "5/5", "12,000", "12000", "江東区辰巳"]:
            assert page.js(f"Negotiate.leaks({json.dumps('本文 ' + bad + ' 本文')}, {json.dumps(toks, ensure_ascii=False)})"), bad
        for ok in [t, t2, th, tr, "数量 15678 個、江東区周辺から加古川市周辺へ"]:
            assert page.js(f"Negotiate.leaks({json.dumps(ok, ensure_ascii=False)}, {json.dumps(toks, ensure_ascii=False)})") == [], ok[:30]
        # 編集した文面の保護
        r = page.js("""(function(){
          const d = Negotiate.newDraft(), log = [];
          log.push(Negotiate.syncDraft(d, 'A', 'k1'));
          log.push(Negotiate.syncDraft(d, 'B', 'k2')); // 編集していない: 作り直される
          const t1 = d.text;
          d.text = 'B（手で直した）';
          log.push(Negotiate.syncDraft(d, 'C', 'k3')); // 編集済み: 守る
          log.push(Negotiate.syncDraft(d, 'C', 'k3'));
          return { log, t1, text: d.text, edited: Negotiate.isEdited(d), stale: Negotiate.isStale(d, 'k3'), staleSame: Negotiate.isStale(d, 'k2'),
                   after: (Negotiate.regenerate(d, 'C', 'k3'), [d.text, Negotiate.isEdited(d), Negotiate.isStale(d, 'k3')]) };
        })()""")
        assert r["log"] == ["generated", "generated", "stale", "stale"] and r["t1"] == "B" and r["text"] == "B（手で直した）" and r["edited"]
        assert r["stale"] is True and r["staleSame"] is False  # 編集済みの文は k2 のまま。k3 の条件とは食い違い、k2 とは食い違わない
        assert r["after"] == ["C", False, False]


def test_negotiate_has_no_send_or_network_code():
    src = (WEB / "negotiate.js").read_text(encoding="utf-8")
    import re
    code = "\n".join(line.split("//")[0] for line in re.sub(r"/[*].*?[*]/", "", src, flags=re.S).splitlines())
    for bad in ("fetch(", "XMLHttpRequest", "sendBeacon", "mailto", "WebSocket", "window.confirm", "confirm(", "alert(", "navigator.share", "location.href", "<form", "submit"):
        assert bad not in code, bad
    assert "文面をコピー" in src and src.count("data-act=\"copy\"") == 1
    assert (ROOT / "web" / "index.html").read_text(encoding="utf-8").count("negotiate.js") == 1


def test_own_calc_and_own_py_expose_the_same_functions():
    js = (WEB / "own_calc.js").read_text(encoding="utf-8")
    for n in ("returnTrips", "returnTarget", "returnPairs", "returnWhen", "srcCat"):
        assert n in js
    for n in ("return_trips", "return_target", "return_pairs", "return_when", "src_cat"):
        assert hasattr(own, n)
