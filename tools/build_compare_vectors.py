"""web/compare_lite.html の貸切運賃の試験表 tests/data/compare_lite_vectors.json を estimate.kashikiri_fares から作る（SPEC §24、G88）。

地域（関東・近畿）× 距離 × 重さの組ごとに、載る車格と額を Python の式で出す。ページの JS は ?selftest=1 で同じ表を通す。
使い方: uv run python tools/build_compare_vectors.py
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import estimate, tariffs  # noqa: E402

OUT = ROOT / "tests" / "data" / "compare_lite_vectors.json"

KMS = ["200", "200.1", "219.9", "220", "220.1", "250", "299.5", "300", "401", "491.8", "492", "499.9", "500", "500.1", "525", "550", "550.1",
       "556", "600", "650", "700.5", "800", "1000", "1200.4"]
KGS = ["1", "400", "2000", "2001", "3500", "4000", "4001"]


def build() -> dict:
    tables = {region: tariffs.load_kashikiri(region) for region in ("関東", "近畿")}
    cases = []
    for region, t in tables.items():
        for km in KMS:
            for kg in KGS:
                fares = estimate.kashikiri_fares(Decimal(km), Decimal(kg), t)
                cases.append({"region": region, "km": km, "kg": kg,
                              "expect": [{"class": f["class"], "amount": f["amount"], "steps": f["steps"], "steps_over_500": f["steps_over_500"]} for f in fares]})
    return {"note": "tools/build_compare_vectors.py が estimate.kashikiri_fares の出力から作る。手で直さない（SPEC §24 G88）", "cases": cases}


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(build(), f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    print(f"保存: {OUT}  {OUT.stat().st_size} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
