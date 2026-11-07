"""rec7 用の録画。record_hd.py と同じ録画を、テイクごとのデバイス倍率（"dsf"、既定 2）で行う。
スマートフォンの画面（390x844、dsf 3 → 1170x2532）を録るために作った。record_hd.py 本体は変更しない。

使い方:  python tools/video/record_hd7.py --takes tools/video/takes_r7.json --take OWNVIZ --out outputs/video/rec7/OWNVIZ.mp4 --gpu --cursor
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import record  # noqa: E402
import record_hd as rh  # noqa: E402


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--take", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--takes", required=True)
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--cursor", action="store_true")
    a = ap.parse_args()
    takes = record.load_takes(Path(a.takes))
    take = next((t for t in takes if t["id"] == a.take), None)
    if take is None:
        print("テイクがありません:", a.take)
        return 2
    rh.DSF = int(take.get("dsf", 2))
    if take.get("fast"):   # ポインタの移動と輪を短くして、押す操作が多いテイクの尺を縮める
        record.MOVE_SEC, record.RING_SEC = 0.4, 0.3
    ffmpeg = record.find_ffmpeg()
    out = Path(a.out)
    res = rh.record_take_hd(take, out, False, ffmpeg, a.gpu, a.cursor)
    print("保存:", out)
    print(record.probe(ffmpeg, out))
    print(f"受け取ったコマ {res['frames']} 枚 / 所要 {res['wall']:.1f} 秒 / 届いたコマ {res['fps_in']:.1f} 枚/秒（{res['span']:.1f} 秒間）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
