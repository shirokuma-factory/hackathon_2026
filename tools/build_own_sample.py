"""自社の運行のサンプル（SPEC §22.1）を作る。乱数を使わず、何度走らせても同じ結果になる。

作るもの:
  data/own_sample/raw/haisha_2026-10.csv        元の配車表（横の形。1 行 = 1 車両、列 = 日付）
  data/own_sample/raw/digitaco_<車番>.csv       元のデジタコ（2 台分。列名は架空）
  data/own_sample/mapping.json                  列の対応づけの記録（辞書の結果・値の形の一致率。Claude の提案の欄は本線が埋める）
  web/data/own_sample.json                      直した結果（画面は表示するだけ）

作り方: まず元の CSV を書き、それを読み直して src/shipcompare/own.py の関数で処理する。
直した結果の数字は、すべて元の CSV から計算したもので、ここでは決め打ちしない。
使い方:
    uv run python tools/build_own_sample.py
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import own  # noqa: E402

RAW = ROOT / "data" / "own_sample" / "raw"
MAPPING = ROOT / "data" / "own_sample" / "mapping.json"
OUT = ROOT / "web" / "data" / "own_sample.json"
YEAR, MON = 2026, 10
HAISHA = "haisha_2026-10.csv"
OSRM_KM = 556.3  # data/geo/osrm_13104.json の 28210（加古川市）。江東区からの値は無いので新宿区からの値で代用する

# ---------------------------------------------------------------- 架空の会社と車両
VEHICLES = [  # plate, 配車表の車番の書き方, 配車表の車種の書き方, 区分, 車両総重量, 最大積載量, 運行記録計, 運転者コード
    ("9001", "神戸 100 か 9001", "大型", "大型", 20000, 10000, "analog", None),
    ("9002", "9002", "大型", "大型", 20000, 10000, "analog", None),
    ("1234", "4t-1234", "4t", "4t", 8000, 3500, "digital", "D01"),
    ("5678", "神戸 100 さ 5678", "4t", "4t", 8000, 3500, "digital", "D02"),
    ("3344", "3344", "4トン", "4t", 6900, 3000, "analog", None),
    ("3355", "4t-3355", "4t", "4t", 6900, 3000, "analog", None),
    ("7788", "2t-7788", "２ｔ", "2t", 4500, 2000, "analog", None),
    ("7799", "神戸 400 あ 7799", "2t", "2t", 4500, 2000, "analog", None),
]
TACHO_LABEL = {"digital": "デジタコ（CSV を読む）", "analog": "チャート紙（この版では読みません）"}
DRIVER_LABEL = {"D01": "運転者 A", "D02": "運転者 B"}

# 地点の別名の辞書。KNOWN は最初から入っている別名、PROPOSED は初めて出た別名（確認待ちに入る）
KNOWN = [("加古川営業所", "p01"), ("加古川", "p01"), ("江東区辰巳", "p07"), ("江東区", "p07"), ("江東", "p07")]
PROPOSED = [("辰巳", "p07")]

# ---------------------------------------------------------------- 運行の予定（行きを出る日 D。行きは D の列、帰りは D+1 の列）
# デジタコの 2 台は、停車の分 m・帰りの出発 R（時）・到着の分 arr などを 1 回ずつ決める。
# 拘束時間 = 29.5 + m/60 + (arr - 5.5) - R（行きの到着が 05:30 の基準）。13 時間・14 時間を超える日の数はこの値で決まる。
DIGI = {
    "1234": {"cycles": {
        6: dict(m=50, R=19.0, back_km=668.0),  # 帰りのメーターが推定より 20% 長い（距離の確認待ち）
        8: dict(m=20, R=19.0),
        12: dict(m=70, R=16.25),
        14: dict(m=65, R=16.5),
        16: dict(m=75, R=16.5),
        20: dict(m=45, R=19.5),
        22: dict(m=15, R=19.0),
        26: dict(m=55, R=19.5, arr=6 * 60 + 50),  # 配車表の着 5:30 と 80 分ずれる（時刻の確認待ち）
        28: dict(m=10, R=19.0),
    }},
    "5678": {"cycles": {
        1: dict(m=25, R=19.0),
        5: dict(m=40, R=16.5),
        10: dict(m=35, R=16.75),
        13: dict(m=45, R=19.5, arr=6 * 60 + 55),  # 時刻の確認待ち
        15: dict(m=20, R=19.0),
        19: dict(m=45, R=16.5),
        24: dict(m=45, R=19.0, out_km=655.0),  # 行きのメーターが推定より 18% 長い（距離の確認待ち）
        27: dict(m=25, R=19.0),
        29: dict(m=10, R=19.0),
    }},
}
OTHER = {  # デジタコの無い車の、行きを出る日
    "9001": [2, 6, 10, 13, 18, 21, 24, 27],
    "9002": [3, 7, 11, 14, 19, 23, 26, 30],
    "3344": [2, 5, 9, 12, 19, 24],
    "3355": [4, 7, 11, 17, 20, 27],
    "7788": [6, 13, 21, 28],
    "7799": [9, 16, 23, 30],
}
BACK_EMPTY_EVERY, BACK_EMPTY_OFFSET = 3, 0  # 木曜以外の帰りは 3 回に 1 回ほど空
UNKNOWN_EVERY, UNKNOWN_OFFSET = 4, 0  # デジタコの無い車の区間のうち、積地か降ろし地が空欄の区間（不明になる）
FREE_NOTES = {("3344", 14): "整備", ("7799", 2): "車検", ("7788", 10): "休", ("3355", 24): "休"}
SHIPPERS_OUT = ["架空 みなと食品", "架空 東和電機", "架空 光建材", "架空 大洋機械", "架空 北斗製菓"]
SHIPPERS_BACK = ["架空 播州製紙", "架空 加古川化学", "架空 瀬戸内鋼材"]
OUT_LOAD_T = [3.2, 4.6, 5.1, 3.8, 4.4, 5.9, 2.9, 4.0]
ARR_FORMATS = ["{h}{m:02d}", "{h}:{m:02d}", "{h:02d}:{m:02d}", "{h}時{m:02d}分"]
DOCK_NAMES = ["江東区辰巳", "辰巳倉庫", "江東区辰巳A棟", "辰巳DC3"]  # 偶数番は漢字だけ、奇数番は英数字まじり


def wd_of(d: int) -> int:
    return date(YEAR, MON, d).weekday()


def iso(d: int, minutes: int) -> datetime:
    return datetime(YEAR, MON, d) + timedelta(minutes=minutes)


def fmt_arr(i: int, minutes: int) -> str:
    h, m = (minutes // 60) % 24, minutes % 60
    return ARR_FORMATS[i % len(ARR_FORMATS)].format(h=h, m=m)


# ---------------------------------------------------------------- 元の CSV を書く


def make_cycles() -> list[dict]:
    """全車両の運行（行きと帰りの 1 組）を決める。"""
    cyc: list[dict] = []
    factors = [0.992, 1.004, 1.012, 0.998, 1.021, 0.987, 1.008, 1.015]
    vidx = {v[0]: i for i, v in enumerate(VEHICLES)}
    for plate, spec in DIGI.items():
        for k, (d, p) in enumerate(sorted(spec["cycles"].items())):
            cyc.append({"plate": plate, "D": d, "k": k, "digi": True, **p})
    for plate, days in OTHER.items():
        for k, d in enumerate(days):
            cyc.append({"plate": plate, "D": d, "k": k, "digi": False})
    cyc.sort(key=lambda c: (c["D"], vidx[c["plate"]]))
    for n, c in enumerate(cyc):
        thursday = wd_of(c["D"]) == 3
        c["thursday"] = thursday
        c["back_empty"] = True if thursday else ((c["k"] + vidx[c["plate"]] + BACK_EMPTY_OFFSET) % BACK_EMPTY_EVERY == 0)
        c["f_out"], c["f_back"] = factors[n % 8], factors[(n * 3 + 1) % 8]
        c["dev"] = [-20, -10, 0, 10, 20][n % 5]
        if c["digi"]:
            c.setdefault("arr", 5 * 60 + 30)
            c["dep"] = 20 * 60 + c["dev"]
            c["out_km"] = c.get("out_km", round(OSRM_KM * c["f_out"], 1))
            c["back_km"] = c.get("back_km", round(OSRM_KM * c["f_back"], 1))
            c["back_run"] = 9 * 60 + 20 + (n % 4) * 10
            c["dock_name"] = DOCK_NAMES[(c["k"] // 2) % 2 + (0 if c["k"] % 2 == 0 else 2)]
    # デジタコの無い車の区間のうち、4 区間に 1 つを「積地か降ろし地が空欄」にする
    legs_other = [(c["D"], c["plate"], d) for c in cyc if not c["digi"] for d in ("out", "back")]
    legs_other.sort(key=lambda t: (t[0], t[1], 0 if t[2] == "out" else 1))
    unreadable_leg = ("7788", 13, "back")  # 時刻が読めない 1 区間（7788 の 13 日発の帰り。不明の数には入れない）
    unknown: set[tuple[str, int, str]] = set()
    for i, (D, plate, d) in enumerate(legs_other):
        if i % UNKNOWN_EVERY == (UNKNOWN_OFFSET if (i // 8) % 2 == 0 else UNKNOWN_OFFSET + 1):  # 行きと帰りの両方に散らす
            key = (plate, D, d)
            if key == unreadable_leg:
                continue
            unknown.add(key)
    for c in cyc:
        c["unknown_out"] = (c["plate"], c["D"], "out") in unknown
        c["unknown_back"] = (c["plate"], c["D"], "back") in unknown
    return cyc


def leg_text(c: dict, d: str, i: int) -> tuple[str, str]:
    """配車表のセルの文字と、荷主の行の文字。"""
    large = c["plate"] in ("9001", "9002")
    if d == "out":
        dest = "辰巳" if (c["plate"], c["D"]) in (("3344", 5), ("3355", 11), ("9002", 14)) else "江東"
        if c.get("unknown_out"):
            return "加古川 →", ""
        txt = f"加古川 → {dest} 着{fmt_arr(i, 5 * 60 + 30)}"  # 着の目安はいつも 5:30。デジタコの到着がずれた運行は確認待ちになる
        if large and i % 5 != 0:
            txt += f" {OUT_LOAD_T[i % 8]}t"
        return txt, SHIPPERS_OUT[i % len(SHIPPERS_OUT)]
    if c.get("unknown_back"):
        return "江東 →", ""
    if c["back_empty"]:
        end = fmt_arr(i + 1, int((c["R"] * 60 + c["back_run"]) % 1440)) if c["digi"] else fmt_arr(i + 1, 4 * 60 + 30)
        return (f"江東 → 加古川（空）" if i % 2 == 0 else f"江東 → 加古川 着{end}（空）"), ""
    end = fmt_arr(i + 2, int((c["R"] * 60 + c["back_run"]) % 1440)) if c["digi"] else fmt_arr(i + 2, 4 * 60)
    txt = f"江東 → 加古川 着{end}"
    if large and i % 5 != 2:
        txt += f" {OUT_LOAD_T[(i + 3) % 8]}t"
    return txt, SHIPPERS_BACK[i % len(SHIPPERS_BACK)]


def write_haisha(cycles: list[dict]) -> None:
    cells: dict[str, dict[int, tuple[str, str]]] = {v[0]: {} for v in VEHICLES}
    for i, c in enumerate(cycles):
        cells[c["plate"]][c["D"]] = leg_text(c, "out", i)
        if c["D"] + 1 <= 31:
            cells[c["plate"]][c["D"] + 1] = leg_text(c, "back", i)
    # 時刻が読めない 1 セル（7788 の 10/14 の帰り）
    cells["7788"][14] = ("江東 → 加古川 着8時半ごろ", "")
    for (plate, d), t in FREE_NOTES.items():
        cells[plate].setdefault(d, (t, ""))
    head = ["車番", "車種"] + [f"{MON}/{d}({own.WEEKDAYS[wd_of(d)]})" for d in range(1, 32)] + ["13日"]
    rows = [head]
    for plate, raw, vtype, *_ in VEHICLES:
        a = [raw, vtype] + [cells[plate].get(d, ("", ""))[0] for d in range(1, 32)] + [""]
        b = ["", ""] + [cells[plate].get(d, ("", ""))[1] for d in range(1, 32)] + [""]
        if plate == "9001":
            a[-1] = "加古川 → 江東"  # 見出しが「13日」だけの列（月が分からない）
        rows += [a, b]
    rows.append([""] * len(head))  # 空行（ここから先は上の車番を引き継がない）
    stray = [""] * len(head)
    stray[2 + 19] = "加古川 → 江東 着530"  # 車番が空欄の行（10/20 の列）
    rows.append(stray)
    write_csv(RAW / HAISHA, rows)


def write_digitaco(cycles: list[dict]) -> None:
    head = ["KBN", "ST_TIME", "ED_TIME", "ODO_KM", "JK_FLG", "POS_NAME", "DRV_CD", "X9"]
    f = lambda dt: dt.strftime("%Y-%m-%d %H:%M")  # noqa: E731
    for plate, *_rest in VEHICLES:
        drv = next(v[7] for v in VEHICLES if v[0] == plate)
        if not drv:
            continue
        rows = [head]
        for c in (x for x in cycles if x["plate"] == plate):
            dep = iso(c["D"], c["dep"])
            arr = iso(c["D"] + 1, c["arr"])
            stop_end = arr + timedelta(minutes=c["m"])
            back_dep = iso(c["D"] + 1, int(round(c["R"] * 60)))
            back_arr = back_dep + timedelta(minutes=c["back_run"])
            sp = lambda km, mins: f"{km / (mins / 60):.1f}"  # noqa: E731
            rows.append(["1", f(dep), f(arr), f"{c['out_km']:.1f}", "1", "", drv, sp(c["out_km"], (arr - dep).total_seconds() / 60)])
            rows.append(["2", f(arr), f(stop_end), "", "", c["dock_name"], drv, ""])
            rows.append(["1", f(back_dep), f(back_arr), f"{c['back_km']:.1f}", "0" if c["back_empty"] else "1", "", drv, sp(c["back_km"], c["back_run"])])
            rows.append(["2", f(back_arr), f(back_arr + timedelta(minutes=20)), "", "", "加古川営業所", drv, ""])
        write_csv(RAW / f"digitaco_{plate}.csv", rows)


def write_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerows(rows)


# ---------------------------------------------------------------- 読み直して処理する


def read_rows(path: Path) -> list[list[str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


def resolve_place(name: str) -> tuple[str | None, str | None, bool]:
    """地点名 → (地点 ID, 当たった別名, 初めて出た別名か)。長い別名から調べる。"""
    for alias, pid in sorted(KNOWN, key=lambda x: -len(x[0])):
        if alias in name:
            return pid, alias, False
    for alias, pid in PROPOSED:
        if alias in name:
            return pid, alias, True
    return None, None, False


def build(write: bool = True, use_claude: bool = True) -> dict:
    """write=False のときは何も書かず、元の CSV は data/own_sample/raw/ の今のファイルを読む（試験用）。
    use_claude=False のときは mapping.json の Claude の提案を使わない（ブラウザの JS と比べるときの条件）。"""
    cycles = make_cycles()
    if write:
        write_haisha(cycles)
        write_digitaco(cycles)

    points = json.loads((ROOT / "data" / "geo" / "municipality_points.json").read_text(encoding="utf-8"))["points"]
    places = {
        "p01": {"id": "p01", "pseudo": "地点 01", "short": "加古川", "muni": "加古川市", "label": "加古川営業所（自社）", "code": "28210",
                "lon": points["28210"]["lon"], "lat": points["28210"]["lat"], "aliases": ["加古川営業所", "加古川"]},
        "p07": {"id": "p07", "pseudo": "地点 07", "short": "江東", "muni": "江東区", "label": "江東区辰巳", "code": "13108",
                "lon": points["13108"]["lon"], "lat": points["13108"]["lat"], "aliases": ["江東区辰巳", "江東区", "江東", "辰巳（新）"]},
    }
    base, dock = "p01", "p07"
    est = {frozenset((base, dock)): OSRM_KM}

    # --- 配車表
    hrows = read_rows(RAW / HAISHA)
    wl = own.wide_to_long(hrows, YEAR)
    unreadable = [dict(u, file=HAISHA) for u in wl["unreadable"]]
    new_alias_hits = {"haisha": 0, "digitaco": 0}
    haisha_legs, nonleg_cells, cell_pending_alias = [], 0, 0
    hseq = 0
    for r in wl["rows"]:
        pc = own.parse_cell(r["text"])
        if pc["errors"]:
            unreadable.append({"line": r["line"], "col": r["col"], "original": r["text"], "reason": pc["errors"][0], "file": HAISHA, "plate": r["plate"]})
            continue
        if pc["nonleg"]:
            nonleg_cells += 1
            continue
        for idx, lg in enumerate(pc["legs"]):
            hseq += 1
            ids = []
            for nm in (lg["frm"], lg["to"]):
                pid, _al, new = resolve_place(nm) if nm else (None, None, False)
                ids.append(pid)
                if new:
                    new_alias_hits["haisha"] += 1
            haisha_legs.append({
                "id": f"H{hseq:03d}", "plate": r["plate"], "date": r["date"], "idx": idx, "frm": ids[0], "to": ids[1],
                "empty": lg["empty"], "arr": lg["arr"], "weight_kg": lg["weight_kg"],
                "ref": {"file": HAISHA, "where": f"{r['line']} 行目・{int(r['date'][5:7])}/{int(r['date'][8:10])} の列", "text": r["text"]},
            })
    # --- デジタコ
    runs, stops, d_files, dseq, sseq = [], [], [], 0, 0
    def resolver(name: str) -> str | None:
        pid, _a, new = resolve_place(name)
        if new:
            new_alias_hits["digitaco"] += 1
        return pid
    dig_values: dict[str, list[str]] = {}
    for plate, *_r in VEHICLES:
        p = RAW / f"digitaco_{plate}.csv"
        if not p.exists():
            continue
        with open(p, encoding="utf-8", newline="") as fh:
            dr = list(csv.DictReader(fh))
        for r in dr:
            for k, v in r.items():
                dig_values.setdefault(k, []).append(v)
        pr = own.parse_digitaco(dr, resolver, plate)
        for x in pr["runs"]:
            dseq += 1
            x["id"] = f"D{dseq:03d}"
            x["ref"] = {"file": p.name, "where": f"{x['line']} 行目", "text": f"走行 {x['start'][11:16]}〜{x['end'][11:16]} {x['km']} km"}
            runs.append(x)
        for x in pr["stops"]:
            sseq += 1
            x["id"] = f"S{sseq:03d}"
            x["ref"] = {"file": p.name, "where": f"{x['line']} 行目", "text": f"停車 {x['start'][11:16]}〜{x['end'][11:16]}（{x['minutes']} 分）"}
            stops.append(x)
        d_files.append({"file": p.name, "plate": plate, "rows": len(dr), "errors": len(pr["errors"]), "columns": list(dr[0].keys()) if dr else []})

    # --- 結び付けと実車・空車
    lk = own.link(haisha_legs, runs, est)
    cl = own.classify_legs(lk["legs"])
    legs = cl["legs"]
    for l in legs:
        l["dir"] = ("out" if (l["to"] == dock or l["frm"] == base) else "back" if (l["to"] == base or l["frm"] == dock) else None)
    legs.sort(key=lambda l: (l["date"], l["plate"], 0 if l["dir"] == "out" else 1))
    for i, l in enumerate(legs, 1):
        l["id"] = f"L{i:03d}"
    # 行きを出た日の曜日（帰りは、直前の行きと同じ運行）
    for l in legs:
        if l["dir"] == "out":
            l["cycle_wd"], l["cycle_dep"] = date.fromisoformat(l["date"]).weekday(), l["dep"]
        else:
            prev = [o for o in legs if o["plate"] == l["plate"] and o["dir"] == "out" and 0 < (date.fromisoformat(l["date"]) - date.fromisoformat(o["date"])).days <= 2]
            if prev:
                o = max(prev, key=lambda x: x["date"])
                l["cycle_wd"], l["cycle_dep"] = date.fromisoformat(o["date"]).weekday(), o["dep"]
            else:
                l["cycle_wd"], l["cycle_dep"] = (date.fromisoformat(l["date"]) - timedelta(days=1)).weekday(), None
        l["color"] = "yellow" if l["flags"] else ("gray" if l["kind"] == "unknown" else "green")
    # 停車に、行きを出た日の曜日を付ける
    run_by_id = {r["id"]: r for r in runs}
    leg_by_did = {l["did"]: l for l in legs if l["did"]}
    for s in stops:
        prev = [r for r in runs if r["plate"] == s["plate"] and r["end"] == s["start"]]
        s["cycle_wd"] = leg_by_did[prev[0]["id"]]["cycle_wd"] if prev and prev[0]["id"] in leg_by_did else None
    # --- 拘束時間（デジタコの運転者ごと）
    shifts, kseq = [], 0
    for plate, *_r in VEHICLES:
        mine = [r for r in runs if r["plate"] == plate]
        if not mine:
            continue
        evs = [{"start": r["start"], "end": r["end"], "line": r["line"]} for r in mine] + [{"start": s["start"], "end": s["end"], "line": s["line"]} for s in stops if s["plate"] == plate]
        drv = mine[0]["driver"]
        sh = own.build_shifts(evs)
        for k in own.kousoku_hours(sh):
            kseq += 1
            lines = next(x["lines"] for x in sh if x["start"] == k["start"])
            lines = [n for n in lines if n]
            k.update({"id": f"K{kseq:03d}", "driver": drv, "driver_label": DRIVER_LABEL.get(drv, drv), "plate": plate,
                      "ref": {"file": f"digitaco_{plate}.csv", "where": f"{min(lines)}〜{max(lines)} 行目"}})
            k.pop("lines", None)
            shifts.append(k)

    vehicles = [{"plate": p, "raw_plate": raw, "type_raw": vt, "cls": cls, "gvw_kg": gvw, "max_load_kg": ml, "tachograph": tg,
                 "tachograph_label": TACHO_LABEL[tg], "driver": DRIVER_LABEL.get(dr)} for p, raw, vt, cls, gvw, ml, tg, dr in VEHICLES]
    kp = own.kpis(legs, vehicles)
    table = own.weekday_table(legs, stops, dock)
    ins = own.insights({"legs": legs, "stops": stops, "shifts": shifts, "vehicles": vehicles, "places": places, "base": base, "dock": dock, "month": MON})

    # --- 列の対応づけ（mapping.json）と確認待ち
    mapping, col_items = build_mapping(hrows, dig_values, wl, write=write, use_claude=use_claude)
    confirm = []
    alias_total = new_alias_hits["haisha"] + new_alias_hits["digitaco"]
    confirm.append({
        "id": "C1", "kind": "alias", "source": "配車表・デジタコ", "title": "地点の別名「辰巳」",
        "original": f"「辰巳」（配車表 {new_alias_hits['haisha']} か所、デジタコ {new_alias_hits['digitaco']} か所）", "proposal": f"{places['p07']['pseudo']}（{places['p07']['label']}）の別名にする",
        "confidence": 0.7, "color": "yellow", "formula": "辞書に無い別名です。地点 07 の住所「江東区辰巳」に含まれるため、ローカルの規則で候補にしました（AI は使っていません）",
        "claude": None, "count": alias_total})
    for it in col_items:
        if it["color"] == "yellow":
            confirm.append(it)
    lk_items = []
    for l in legs:
        for fl in l["flags"]:
            if fl["type"] == "time":
                orig = f"配車表の着 {fl['hint']} と、デジタコの到着 {fl['digitaco']} が {abs(fl['diff_min'])} 分ずれています"
                prop = "時刻はデジタコの値を使う"
            else:
                orig = f"デジタコの走行距離 {fl['digitaco_km']} km が、OSRM の推定 {fl['est_km']} km より {round(fl['ratio'] * 100)}% 長くなっています"
                prop = "距離はデジタコのメーターを使う（迂回した可能性）"
            lk_items.append({"id": "", "kind": "link", "source": "配車表とデジタコ", "title": f"{int(l['date'][5:7])}/{int(l['date'][8:10])} 車番 {l['plate']} の{'行き' if l['dir'] == 'out' else '帰り'}（{'時刻' if fl['type'] == 'time' else '距離'}）",
                             "original": orig, "proposal": prop, "confidence": None, "color": "yellow", "leg": l["id"], "formula": "距離が 15% 以上、時刻が 60 分以上ずれたら確認待ちに入れます", "claude": None})
    confirm += lk_items
    for n, it in enumerate(confirm, start=1):
        it["id"] = f"C{n}"
    for u in unreadable:
        u["where"] = f"{u['line']} 行目・「{hrows[0][u['col']]}」の列"

    # --- ファイルごとの結果
    hs = wl["stats"]
    h_cells = len(wl["rows"]) + len(wl["unreadable"])
    pend_h = sum(1 for c in confirm if c["kind"] in ("alias", "link") or c.get("source") == "配車表")
    pend_d = sum(1 for c in confirm if c.get("source") == "デジタコ")
    files = [{
        "file": HAISHA, "kind": "配車表", "shape": "日付が列に並ぶ横の形",
        "verdict": f"日付が列に並ぶ横の形です。1 行 = 1 車両 × 1 日に直しました（車番 {hs['groups'] - 1} 台、日付の列 {hs['date_cols']} 個。"
                   f"車番が空欄の続きの行 {hs['filled_rows']} か所を、上の行の車番で埋めました。色は読みません）。",
        "cells": h_cells, "ok": h_cells - len(unreadable), "pending": pend_h, "unreadable": len(unreadable),
    }]
    for df in d_files:
        mapped = sum(1 for c in df["columns"] if c.upper() in own.DIGITACO_DICT)
        miss = [c for c in df["columns"] if c.upper() not in own.DIGITACO_DICT]
        first = df is d_files[0]
        files.append({
            "file": df["file"], "kind": "デジタコ", "shape": "1 行 = 1 回の走行か停車の縦の形",
            "verdict": f"1 行 = 1 回の走行か停車の縦の形です。列 {len(df['columns'])} 個のうち {mapped} 個を辞書で対応づけました。{len(miss)} 個は分かりません（列名: {'、'.join(miss)}）。読まずに進めます。",
            "cells": df["rows"], "ok": df["rows"] - df["errors"], "pending": pend_d if first else 0, "unreadable": df["errors"],
            "note": None if first else "列の対応は digitaco_1234.csv と同じです",
        })
    counts = {
        "legs": len(legs), "loaded": cl["counts"]["loaded"], "empty": cl["counts"]["empty"], "unknown": cl["counts"]["unknown"],
        "unknown_ratio": round(cl["unknown_ratio"], 4), "stops": len(stops), "shifts": len(shifts),
        "confirm": len(confirm), "unreadable": len(unreadable), "digitaco_only": lk["digitaco_only"], "haisha_only": lk["haisha_only"],
        "nonleg_cells": nonleg_cells,
    }

    # --- 地図の道路の形（兵庫県 → 東京都のトラックの経路。両端は自社と地点 07 に延ばす）
    path = road_path(places)
    claude_present = any(c.get("claude_proposal") for c in mapping["columns"])
    data = {
        "version": 1, "fiction": True, "month": f"{YEAR}-{MON:02d}", "year": YEAR, "mon": MON,
        "company": {"name": "架空 播磨ライン運輸", "city": "兵庫県加古川市", "code": "28210", "vehicles": len(vehicles)},
        "places": places, "base": base, "dock": dock,
        "route": {"path": path, "osrm_km": OSRM_KM, "note": "道路の形は 兵庫県 → 東京都 のトラックの経路（OSRM、OpenStreetMap）。両端は自社の車庫と地点 07 に延ばしています。距離は新宿区 → 加古川市の OSRM の値で代用しています"},
        "vehicles": vehicles, "files": files, "unreadable": unreadable,
        "paper": {"count": 3, "text": "紙の日報 3 枚（この版では読みません）"},
        "columns": [{k: v for k, v in c.items() if k != "claude_input"} for c in mapping["columns"]],
        "claude": {"present": claude_present, "model": mapping.get("claude_model"), "created_at": mapping.get("claude_created_at"),
                   "label": "Claude の提案: 未作成" if not claude_present else "Claude の提案: あり"},
        "confirm": confirm, "legs": [leg_json(l) for l in legs], "stops": [stop_json(s) for s in stops], "shifts": shifts,
        "kpis": kp, "table": table, "insights": ins, "counts": counts,
        "national": {"actual_rate_pct": 62.15, "load_efficiency_pct": 39.08,
                     "source": "自動車輸送統計年報 2025 年度（付表）原単位、e-Stat 統計表 ID 000040511074（営業用普通車）。積載効率は表 3-1 の輸送トンキロ ÷ 能力トンキロ（ID 000040511073）と一致",
                     "caution": "全国の積載効率はトンキロの比で、自社の値と同じ式ではありません。比べられるのは実車率だけです"},
    }
    return data


def leg_json(l: dict) -> dict:
    keep = ("id", "plate", "date", "dir", "cycle_wd", "cycle_dep", "dep", "arr", "arr_hint", "frm", "to", "km", "kind", "kind_src", "weight_kg", "flags", "color", "ref", "dref", "driver")
    d = {k: l.get(k) for k in keep}
    d["km_src"] = (l.get("sources") or {}).get("km")
    d["time_src"] = (l.get("sources") or {}).get("time")
    return d


def stop_json(s: dict) -> dict:
    return {k: s.get(k) for k in ("id", "plate", "place", "name", "start", "end", "minutes", "driver", "cycle_wd", "ref")}


def road_path(places: dict) -> list[list[float]]:
    pairs = json.loads((ROOT / "web" / "data" / "routes_pairs.json").read_text(encoding="utf-8"))
    rec = pairs["兵庫県>東京都"]["m"]["truck"]
    pts: list[tuple[float, float]] = []
    for lg in rec["legs"]:
        c = lg["c"]
        x, y = c[0], c[1]
        pts.append((x / 1e4, y / 1e4))
        for i in range(2, len(c), 2):
            x += c[i]
            y += c[i + 1]
            pts.append((x / 1e4, y / 1e4))
    step = 5
    sm = pts[::step] + ([pts[-1]] if (len(pts) - 1) % step else [])
    a, b = places["p01"], places["p07"]
    path = [[a["lon"], a["lat"]]] + [[round(x, 4), round(y, 4)] for x, y in sm] + [[b["lon"], b["lat"]]]
    return path


# ---------------------------------------------------------------- mapping.json


def build_mapping(hrows: list[list[str]], dig_values: dict[str, list[str]], wl: dict, write: bool = True, use_claude: bool = True) -> tuple[dict, list[dict]]:
    """列ごとに、辞書の結果・値の形の一致率・Claude の提案の欄を作る。既にある Claude の提案は残す。"""
    old = {}
    old_top = {}
    if use_claude and MAPPING.exists():
        o = json.loads(MAPPING.read_text(encoding="utf-8"))
        old = {(c["source"], c["column"]): c for c in o.get("columns", [])}
        old_top = {k: o.get(k) for k in ("claude_model", "claude_created_at")}
    head = hrows[0]
    plate_vals = [r[0] for r in hrows[1:] if len(r) > 0 and r[0].strip()]
    type_vals = [r[1] for r in hrows[1:] if len(r) > 1 and r[1].strip()]
    date_heads = [h for i, h in enumerate(head) if i >= 2]
    specs = [
        ("配車表", "車番", "plate", "車番" in own.HAISHA_DICT, plate_vals),
        ("配車表", "車種", "vehicle_type", "車種" in own.HAISHA_DICT, type_vals),
        ("配車表", f"日付の見出し（{len(date_heads)} 列）", "date", True, date_heads),  # 「月/日」の形を日付の列とみなす規則
    ]
    for name, vals in dig_values.items():
        role = own.DIGITACO_DICT.get(name.upper())
        specs.append(("デジタコ", name, role, role is not None, vals))
    columns, items = [], []
    for source, col, role, hit, vals in specs:
        if hit:
            skey = own.ROLE_SHAPE[role]
            ratio = own.shape_ratio(vals, skey)
            exp = own.VALUE_SHAPES[skey][0]
            role_label = own.ROLE_LABEL[role]
        else:
            role, ratio = own.best_role_for(vals)
            exp = own.VALUE_SHAPES[own.ROLE_SHAPE[role]][0]
            role_label = None
        prev = old.get((source, col), {})
        prop = prev.get("claude_proposal")
        agrees = bool(prop) and prop.get("role") == role
        c = own.confidence(hit, ratio, agrees)
        color = own.confidence_color(c)
        entry = {
            "source": source, "column": col, "dict_hit": hit, "dict_role": role if hit else None, "dict_label": role_label,
            "shape_expected": exp, "shape_ratio": round(ratio, 4), "shape_best_role": None if hit else role,
            "claude_proposal": prop, "claude_note": prev.get("claude_note") if prop else "未作成（本線が作る）",
            "claude_agrees": agrees, "confidence": c, "color": color,
            "formula": f"0.5 × max(辞書 {1 if hit else 0}, Claude {1 if agrees else 0}) + 0.5 × 値の形 {ratio:.3f} = {c:.3f}",
            "claude_input": {
                "source": source, "column": col, "row_count": len([v for v in vals]), "non_blank_ratio": round(len([v for v in vals if (v or '').strip()]) / max(1, len(vals)), 4),
                "shape_summary": own.shape_summary(vals),
            },
        }
        columns.append(entry)
        if color == "yellow":
            items.append({
                "id": "", "kind": "column", "source": source, "file": HAISHA if source == "配車表" else "digitaco_*.csv", "title": f"列「{col}」の対応づけ",
                "original": f"列名「{col}」。値の形は「{exp}」に {round(ratio * 100)}% 一致", "proposal": f"{role_label or '不明'}として読む",
                "confidence": c, "color": color, "formula": entry["formula"], "claude": prop, "column": col,
            })
    mapping = {
        "schema": {
            "note": "列ごとの辞書の結果と値の形の一致率。claude_proposal は開発のセッションで Claude が列名と値の形の説明（claude_input）だけを見て出した提案を入れる欄。"
                    "null のときは『未作成』と表示し、確かさは辞書の一致だけで計算する。",
            "claude_proposal": {"role": "辞書の役割の ID（kind / start_time / end_time / distance_km / loaded_flag / place_name / driver_code / plate / vehicle_type / date）", "reason": "短い理由（任意）"},
            "claude_input": "Claude に送る記録の形。列名、行数、空欄でない割合、値の形の説明の割合だけ。実際の値・氏名・荷主名・地点名は含めない",
        },
        "claude_model": old_top.get("claude_model"), "claude_created_at": old_top.get("claude_created_at"),
        "dictionary": {"digitaco": own.DIGITACO_DICT, "haisha": own.HAISHA_DICT, "roles": own.ROLE_LABEL},
        "columns": columns,
    }
    if write:
        MAPPING.parent.mkdir(parents=True, exist_ok=True)
        with open(MAPPING, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(mapping, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    return mapping, items


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    c = data["counts"]
    print(f"区間 {c['legs']}（実車 {c['loaded']}、空車 {c['empty']}、不明 {c['unknown']} = {c['unknown_ratio'] * 100:.1f}%）、停車 {c['stops']}、確認待ち {c['confirm']}、読めない {c['unreadable']}")
    print("気づき:", [i["rule"] for i in data["insights"]])
    print("保存:", OUT.relative_to(ROOT), f"{OUT.stat().st_size:,}B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
