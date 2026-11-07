"""tools/video/record.py を、鹿児島版のスナップショット（serve_kago.py）に向けて動かす。

record.py は ROOT/tools/serve_web.py を起動して録画する。ここでは ROOT を差し替えて、
同じ場所に置いた小さな起動役（serve_web.py）が serve_kago.py を呼ぶようにする。record.py 本体は変更しない。

使い方:  python tools/video/record_kago.py --takes tools/video/takes_r5.json --take R1g --out outputs/video/rec5/R1g.mp4
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import record  # noqa: E402

shim_root = REPO / "outputs" / "video" / "kago_root"
shim = shim_root / "tools" / "serve_web.py"
shim.parent.mkdir(parents=True, exist_ok=True)
with open(shim, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("import runpy\nrunpy.run_path(" + repr(str(HERE / "serve_kago.py")) + ", run_name='__main__')\n")
record.ROOT = shim_root

if __name__ == "__main__":
    sys.exit(record.main())
