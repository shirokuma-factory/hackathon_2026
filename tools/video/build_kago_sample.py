"""鹿児島版のデモ用サンプルを作る（録画 r5 用）。リポジトリの web/ src/ data/ tools/*.py は書き換えない。

やること（書き込み先はすべて outputs/video/ の下）:
  1. tools/build_own_sample.py のソースを文字置換した版（加古川→鹿屋 など）を実行して、
     配車表・デジタコの CSV（outputs/video/kago_env/own_raw/）と own_sample.json（web_kago/data/）を作る。
  2. web_kago/own_lite.html の埋め込みサンプル（SAMPLE_FILES）と既定距離を差し替える。
  3. kago_env/data/demo/carriers.json の C4 と便 T1・T2・T6・T7 を鹿児島版にする。
  4. web_kago/data/routes_trips.json の地図の経路を、新しい便に合わせる。
  5. web_kago の文言（own.js・index.html・find.js の既定の着地）を直す。

使い方:  python tools/video/build_kago_sample.py
前提:    outputs/video/web_kago と outputs/video/kago_env は、web/・src/・data/ のコピー（作成済み）。
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VID = REPO / "outputs" / "video"
WEB = VID / "web_kago"
ENV = VID / "kago_env"
RAW = ENV / "own_raw"
sys.path.insert(0, str(ENV / "src"))
from shipcompare import own  # noqa: E402,F401  スナップショットの src を先に読ませる

OSRM_KM = 1381.7   # 新宿区 → 鹿屋市（OSRM の事前計算。江東区からの値は無いので新宿区の値で代用。サンプルの元の作りと同じ）
T7_PRICE = 95000   # 2t 貸切の目安（新宿区 → 鹿児島市 272,610 円）の約 35%（95,413 円）を 1,000 円に丸めた値

SUBS = [  # 長いものから順に
    ("兵庫県加古川市", "鹿児島県鹿屋市"),
    ("兵庫県 → 東京都", "鹿児島県 → 東京都"),
    ("架空 播磨ライン運輸", "架空 さつま青果便"),
    ("加古川営業所", "鹿屋営業所"),
    ("加古川市", "鹿屋市"),
    ("加古川", "鹿屋"),
    ("28210", "46203"),
    ("神戸", "鹿児島"),
    ("架空 みなと食品", "架空 霧島ファーム"),
    ("架空 東和電機", "架空 志布志農協"),
    ("架空 光建材", "架空 大隅園芸"),
    ("架空 大洋機械", "架空 南薩青果"),
    ("架空 北斗製菓", "架空 桜島野菜組合"),
    ("架空 播州製紙", "架空 関東包材"),
    ("架空 鹿屋化学", "架空 東京飲料"),   # 加古川化学 が置換された後の名前
    ("架空 瀬戸内鋼材", "架空 埼玉紙器"),
    ("556.3", f"{OSRM_KM}"),
]


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def sub_all(s: str) -> str:
    for a, b in SUBS:
        s = s.replace(a, b)
    return s


def road_path(places):
    d = json.loads((WEB / "data" / "routes_13104.json").read_text(encoding="utf-8"))
    rec = d["dests"]["鹿児島県"]["m"]["truck"]
    pts = []
    for lg in rec["legs"]:
        c = lg["c"]
        x, y = c[0], c[1]
        pts.append((x / 1e4, y / 1e4))
        for i in range(2, len(c), 2):
            x += c[i]
            y += c[i + 1]
            pts.append((x / 1e4, y / 1e4))
    pts.reverse()   # 鹿児島 → 東京の向き（元は 兵庫 → 東京）
    step = 5
    sm = pts[::step] + ([pts[-1]] if (len(pts) - 1) % step else [])
    a, b = places["p01"], places["p07"]
    return [[a["lon"], a["lat"]]] + [[round(x, 4), round(y, 4)] for x, y in sm] + [[b["lon"], b["lat"]]]


def build_own():
    src = (REPO / "tools" / "build_own_sample.py").read_text(encoding="utf-8")
    src = sub_all(src)
    ns = {"__file__": str(REPO / "tools" / "build_own_sample.py"), "__name__": "build_own_sample_kago"}
    exec(compile(src, "build_own_sample_kago", "exec"), ns)
    ns["RAW"] = RAW
    ns["OUT"] = WEB / "data" / "own_sample.json"
    ns["road_path"] = road_path
    cycles = ns["make_cycles"]()
    ns["write_haisha"](cycles)
    ns["write_digitaco"](cycles)
    data = ns["build"](write=False)   # mapping.json は読むだけで書かない
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
    w(ns["OUT"], text)
    c = data["counts"]
    print("own_sample:", {k: c[k] for k in ("legs", "loaded", "empty", "unknown")})
    return data


def embed_lite(data):
    files = []
    for n in ("haisha_2026-10.csv", "digitaco_1234.csv", "digitaco_5678.csv"):
        files.append({"name": n, "text": (RAW / n).read_text(encoding="utf-8")})
    js = json.dumps(files, ensure_ascii=False, separators=(",", ":")).replace("</", "<" + chr(92) + "/")
    p = WEB / "own_lite.html"
    html = p.read_text(encoding="utf-8")
    html, n = re.subn(r"^const SAMPLE_FILES = \[.*\];$", lambda m: "const SAMPLE_FILES = " + js + ";", html, flags=re.M)
    assert n == 1, n
    km_old = "'13108|28210': 556.3"
    html = html.replace(km_old, f"'13108|46203': {OSRM_KM}")
    w(p, html)
    p2 = WEB / "own_calc.js"
    js2 = p2.read_text(encoding="utf-8")
    w(p2, js2.replace(km_old, f"'13108|46203': {OSRM_KM}"))
    # 文言
    p3 = WEB / "own.js"
    t = p3.read_text(encoding="utf-8")
    w(p3, t.replace("例 兵庫県加古川市", "例 鹿児島県鹿屋市"))
    p4 = WEB / "index.html"
    t = p4.read_text(encoding="utf-8")
    w(p4, t.replace("架空 播磨ライン運輸（兵庫県加古川市・車両 8 台）", "架空 さつま青果便（鹿児島県鹿屋市・車両 8 台）"))
    p5 = WEB / "find.js"
    t = p5.read_text(encoding="utf-8")
    t = t.replace("dest: '28110'", "dest: '46201'").replace("例: 兵庫県神戸市中央区", "例: 鹿児島県鹿児島市").replace("兵庫県神戸市中央区", "鹿児島県鹿児島市")
    w(p5, t)


def edit_trips():
    p = ENV / "data" / "demo" / "carriers.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    for c in d["carriers"]:
        if c["id"] == "C2":
            c["note"] = "架空。関西・九州発着の帰り便"
        if c["id"] == "C4":
            c.update(name="架空 さつま青果便", note="架空。鹿児島県鹿屋市の運送会社（青果を東京へ運ぶ）。東京からの帰りの便を登録している",
                     address="鹿児島県鹿屋市（架空の番地）", tariff_region="関東", license_no="架空 貨物自動車運送事業 九運自貨第0000号")
    for t in d["trips"]:
        if t["id"] == "T1":   # 東京 → 大阪
            t.update(route_label="東京都→大阪府（大阪府→東京都の荷の帰り）", dest_code="27128", drop_points=["27128", "27127", "27120"])
        elif t["id"] == "T2":  # 東京 → 静岡
            t.update(route_label="東京都→静岡県（静岡県→東京都の荷の帰り）", dest_code="22101", drop_points=["22101", "22203"])
        elif t["id"] == "T6":  # 福岡 → 鹿児島
            t.update(route_label="福岡県→鹿児島県（鹿児島県→福岡県の荷の帰り）", origin_code="40132", dest_code="46201", drop_points=["46201", "46203", "46204"])
        elif t["id"] == "T7":
            t.update(route_label="東京都→鹿児島県（鹿児島県→東京都の荷の帰り）", origin_code="13108", dest_code="46203",
                     arrive_at="2026-11-15T06:00", drop_points=["46201", "46203"], price_ex_tax_yen=T7_PRICE,
                     pickup_radius_km=15)   # 新宿区の荷主は江東区から 10.5 km。元の 10 km では範囲外になる
    w(p, json.dumps(d, ensure_ascii=False, indent=2) + "\n")
    # 場面 S6（東雲木工の陳列棚）の帰り荷の着地を、大阪市中央区から鹿児島市にする
    ps = ENV / "data" / "scenarios" / "S6.json"
    sc = json.loads(ps.read_text(encoding="utf-8"))
    assert sc["backhaul"]["dest_code"] in ("27128", "46201")
    sc["backhaul"]["dest_code"] = "46201"
    w(ps, json.dumps(sc, ensure_ascii=False, indent=2) + chr(10))
    # 地図の経路
    r13 = json.loads((WEB / "data" / "routes_13104.json").read_text(encoding="utf-8"))["dests"]
    pairs = json.loads((WEB / "data" / "routes_pairs.json").read_text(encoding="utf-8"))
    rt = json.loads((WEB / "data" / "routes_trips.json").read_text(encoding="utf-8"))
    rt["T1"] = {"o": "13111", "d": "27128", "truck": r13["大阪府"]["m"]["truck"]}
    rt["T2"] = {"o": "13104", "d": "22101", "truck": r13["静岡県"]["m"]["truck"]}
    rt["T6"] = {"o": "40132", "d": "46201", "truck": pairs["福岡県>鹿児島県"]["m"]["truck"]}
    rt["T7"] = {"o": "13108", "d": "46203", "truck": r13["鹿児島県"]["m"]["truck"]}
    w(WEB / "data" / "routes_trips.json", json.dumps(rt, ensure_ascii=False, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data = build_own()
    embed_lite(data)
    edit_trips()
    print("done")
