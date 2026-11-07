"""web/compare_lite.html を tools/compare_lite_template.html から作る（SPEC §24）。

貸切の運賃の表（関東・近畿）、試験表（tests/data/compare_lite_vectors.json）、各社の問い合わせ先（data/contacts.json）をページの中に埋め込む。
ページは通信をしないので、この 3 つはビルドのときにだけ読む。使い方: uv run python tools/build_compare_page.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_build  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from shipcompare.loadplan import TARIFF_4T_NOTE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "tools" / "compare_lite_template.html"
KANTO = ROOT / "data" / "tariffs" / "mlit_standard_fare_kanto.json"
KINKI = ROOT / "data" / "tariffs" / "mlit_standard_fare_kinki.json"
CONTACTS = ROOT / "data" / "contacts.json"
DECKS = ROOT / "data" / "vehicle_decks.json"
CARRIERS = ROOT / "data" / "demo" / "carriers.json"
SAGAWA = ROOT / "data" / "tariffs" / "sagawa_kanto.json"
VECTORS = ROOT / "tests" / "data" / "compare_lite_vectors.json"
OUT_ARTIFACT = ROOT / "web" / "publish" / "compare_lite_artifact.html"
TITLE = "荷物の送り方比べ"
POINTS = ROOT / "data" / "geo" / "municipality_points.json"
# 都道府県庁の所在地の代表点（市区町村コード）。道路距離の既定値（直線×1.25）の計算に使う
PREF_CAPITAL_CODES = ["01101", "02201", "03201", "04101", "05201", "06201", "07201", "08201", "09201", "10201", "11107", "12101", "13104", "14104", "15103", "16201", "17201", "18201", "19201", "20201", "21201", "22101", "23106", "24201", "25201", "26104", "27128", "28110", "29201", "30201", "31201", "32201", "33101", "34101", "35203", "36201", "37201", "38201", "39201", "40133", "41201", "42201", "43101", "44201", "45201", "46201", "47201"]
OUT = ROOT / "web" / "compare_lite.html"


def _js(obj: object) -> str:
    # </script> が JSON の中に出ても閉じないようにする
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _filled() -> str:
    html = artifact_build.inline_kit(TEMPLATE.read_text(encoding="utf-8"))
    tariffs = {"関東": json.loads(KANTO.read_text(encoding="utf-8")), "近畿": json.loads(KINKI.read_text(encoding="utf-8"))}
    for t in tariffs.values():  # 照合の記録に、リポジトリの中の文書の節（「調査 §L」）が入っている。公開するページには載せない（SPEC §31.5 #17）
        if t.get("verified_by"):
            t["verified_by"] = re.sub(r"、調査 §[A-Z]+）", "）", t["verified_by"])
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    # 各社の公式の問い合わせ・申し込みページ（data/contacts.json）。ページは通信をしないので、ビルドのときに埋め込む
    raw = json.loads(CONTACTS.read_text(encoding="utf-8"))
    contacts = {"checked_on": raw["checked_on"], "contacts": {k: [{"name": c["name"], "url": c["url"], "condition": c["condition"], "reading": c.get("reading", "")} for c in v] for k, v in raw["contacts"].items()}}
    # 荷台の寸法は積付け・空きを探すと同じ data/vehicle_decks.json（洗い出し N7）。4t の注記は積付けの文（SPEC §34.5）と同じ
    decks = json.loads(DECKS.read_text(encoding="utf-8"))
    # 公開するページに、リポジトリの中のファイル名を出さない（SPEC §31.5 #17）
    decks.pop("basis", None)
    for d in decks.get("decks", []):
        d.pop("basis", None)
    decks["tariff_4t_note"] = TARIFF_4T_NOTE
    # 帰り便の目安は、地図の画面のデモの便 T7 の「空き」で判定する（/api/b/match と同じ。SPEC §39）
    t7 = next(t for t in json.loads(CARRIERS.read_text(encoding="utf-8"))["trips"] if t["id"] == "T7")
    home = {k: t7[k] for k in ("weight_slack_kg", "floor_free_m2", "max_piece_kg", "clear_height_mm", "unit_footprint_mm")}
    # 宅配の公表運賃（関東発・関西あて）。会社名・商品名・表への URL は載せない。サイズ・寸法と重さの上限・運賃（税込）・持込の減額だけ（SPEC §47）
    sg = json.loads(SAGAWA.read_text(encoding="utf-8"))
    pub_takuhai = {"origin": sg["origin_region"], "dest": sg["dest_region"], "tax": sg["tax"], "checked_on": sg["checked_on"],
                   "sizes": [{k: z[k] for k in ("size", "max_sum_cm", "max_kg", "fare")} for z in sg["sizes"]],
                   "dropoff_discount": sg["rules"]["dropoff_discount_per_piece"]}
    pts = json.loads(POINTS.read_text(encoding="utf-8"))["points"]
    prefxy = {pts[c]["pref"]: [round(pts[c]["lon"], 3), round(pts[c]["lat"], 3)] for c in PREF_CAPITAL_CODES}
    if len(prefxy) != 47:
        raise SystemExit("県庁所在地の代表点が 47 件になりません")
    for key, val in (("/*__PUBTAKUHAI__*/null", _js(pub_takuhai)), ("/*__HOME__*/null", _js(home)), ("/*__PREFXY__*/null", _js(prefxy)), ("/*__DECKS__*/null", _js(decks)), ("/*__TARIFFS__*/null", _js(tariffs)), ("/*__VECTORS__*/null", _js(vectors)), ("/*__CONTACTS__*/null", _js(contacts))):
        if html.count(key) != 1:
            raise SystemExit(f"テンプレートに {key} が 1 つありません")
        html = html.replace(key, val)
    return html


def build() -> str:
    return artifact_build.local_flag(_filled())


def build_artifact() -> str:
    return artifact_build.to_artifact(_filled(), TITLE)


def main() -> int:
    OUT_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    for path, html in ((OUT, build()), (OUT_ARTIFACT, build_artifact())):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
        print(f"保存: {path}  {path.stat().st_size} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
