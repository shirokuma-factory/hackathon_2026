"""2026-11-08 の最終評価に合わせて: 「宮城 100 あ 21」のように、かなの後ろが 1〜4 桁の車番を Python と JS の両方で読む。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import own  # noqa: E402

CASES = [
    ("宮城 100 あ 21", "0021", "宮城100あ0021"),
    ("宮城 100 あ 1-21", "0121", "宮城100あ0121"),
    ("宮城 100 あ 12-34", "1234", "宮城100あ1234"),
    ("宮城 100 あ 1234", "1234", "宮城100あ1234"),
    ("宮城100あ5", "0005", "宮城100あ0005"),
    ("1234", "1234", None),
]


@pytest.mark.parametrize("raw,num,full", CASES)
def test_python_plate_short(raw, num, full):
    assert own.plate_identity(raw) == (num, full)


def test_js_plate_short(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node が無い")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([c[0] for c in CASES], ensure_ascii=False), encoding="utf-8")
    js = tmp_path / "run.js"
    lines = [
        "const fs = require('fs');",
        f"const src = fs.readFileSync({json.dumps(str(ROOT / 'web' / 'own_calc.js'))}, 'utf8') + ';\\nreturn OwnCalc;';",
        "const calc = new Function(src)();",
        f"const cases = JSON.parse(fs.readFileSync({json.dumps(str(cases))}, 'utf8'));",
        "process.stdout.write(JSON.stringify(cases.map((s) => calc.plateIdentity(s))));",
    ]
    js.write_text("\n".join(lines), encoding="utf-8")
    r = subprocess.run([node, str(js)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [[n, f] for _, n, f in CASES]
