"""国道・高速道路の線を Overpass API から取る（ODbL。© OpenStreetMap contributors）。
使い方: uv run python tools/fetch_osm_roads.py kanto   （地域名を 1 つ。全部は all。要求の間は 35 秒空ける）
"""
from __future__ import annotations
import sys, time, urllib.request, urllib.parse
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "raw" / "roads"
UA = "backfull-hackathon-roads/1.0 (research prototype; one-time download of trunk roads)"
URL = "https://overpass-api.de/api/interpreter"
# 南, 西, 北, 東
REGIONS = {
    "hokkaido": (41.3, 139.3, 45.6, 145.9),
    "tohoku": (37.7, 139.0, 41.6, 142.1),
    "kanto": (34.8, 138.4, 37.8, 141.0),
    "chubu": (34.5, 135.9, 38.0, 138.4),
    "kinki": (33.4, 134.0, 36.0, 136.8),
    "chugoku_shikoku": (32.7, 130.8, 35.7, 134.6),
    "kyushu": (30.9, 128.5, 34.0, 132.2),
    "okinawa": (24.0, 122.9, 27.9, 131.4),
}
Q = """[out:json][timeout:300];
(
 way["highway"="motorway"]({b});
 way["highway"~"^(trunk|primary)$"]["ref"~"^[0-9]+$"]["name"~"国道"]({b});
);
out tags geom qt;"""


def fetch(name: str) -> int:
    s, w, n, e = REGIONS[name]
    q = Q.format(b=f"{s},{w},{n},{e}")
    req = urllib.request.Request(URL, data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": UA})
    body = b""
    for attempt in range(3):  # 失敗したら 90 秒あけてやり直す（負荷をかけない）
        try:
            with urllib.request.urlopen(req, timeout=400) as r:
                body = r.read()
            break
        except Exception as e:  # noqa: BLE001
            print(name, "失敗", attempt + 1, repr(e)[:80], flush=True)
            time.sleep(90)
    if not body:
        raise SystemExit(f"{name}: 取得できませんでした")
    (OUT / f"{name}.json").write_bytes(body)
    print(name, len(body), "bytes")
    return len(body)


NL = chr(10)


def fetch_gap() -> int:
    """地域ごとの取得で道路が見つからなかった観測点（trucks_roads.json の unsnapped）の周囲 1.2 km だけを、1 回の要求でまとめて取る。
    名前に「国道」が入っていない国道（名前が通りの名前になっている線）を拾うため、ref が数字だけの trunk・primary を対象にする。"""
    import json
    root = Path(__file__).resolve().parents[1]
    miss = {k.split("|")[0] for k in json.loads((root / "web/data/trucks_roads.json").read_text(encoding="utf-8"))["unsnapped"]}
    pts = [p for p in json.loads((root / "web/data/trucks_now.json").read_text(encoding="utf-8"))["points"] if str(p["id"]) in miss]
    total = 0
    for n, i in enumerate(range(0, len(pts), 110)):
        if (OUT / f"gap_unsnapped_{n}.json").exists():
            continue
        if n:
            time.sleep(60)
        body = NL.join(f' way["highway"~"^(trunk|primary)$"]["ref"~"^[0-9]+$"](around:1000,{p["lat"]},{p["lon"]});' for p in pts[i:i + 110])
        q = "[out:json][timeout:300];" + NL + "(" + NL + body + NL + ");" + NL + "out tags geom qt;"
        req = urllib.request.Request(URL, data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=400) as r:
            data = r.read()
        (OUT / f"gap_unsnapped_{n}.json").write_bytes(data)
        print("gap", n, len(data), "bytes", flush=True)
        total += len(data)
    return total


if __name__ == "__main__":
    if sys.argv[1] == "gap":
        fetch_gap()
        raise SystemExit(0)
    names = list(REGIONS) if sys.argv[1] == "all" else sys.argv[1:]
    for i, nm in enumerate(names):
        if i:
            time.sleep(35)
        if (OUT / f"{nm}.json").exists():
            print("skip", nm); continue
        fetch(nm)
