"""今のアプリ（tools/serve_web.py、web/ src/ data/ そのまま）でテイクを録る。

r6・r7 の録画は鹿児島版の写し（outputs/video/web_kago2、serve_kago2.py）で録っていた。11/8 の時点で
アプリ本体が鹿児島の見本（架空 さつま運輸、鹿屋 → 江東）を持つようになり、画面も大きく変わったので、
写しをやめて本体で録る（作成者の指摘「アプリの画面がだいぶ変わっているので、それを使わないといけない」11/8）。

record_hd.py は読み込むと record.ROOT を写しの場所に替えるので、読み込んだあとで戻す。
使い方: python tools/video/record_live.py --takes tools/video/takes_r6.json --take R2g --out outputs/video/rec16/R2g.mp4 --gpu --cursor
（引数は record_hd7.py と同じ。--takes に書いた "dsf" が無いテイクは 2 倍で録る）
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import record  # noqa: E402
import record_hd  # noqa: E402,F401  読み込むと record.ROOT が写しに替わる

record.ROOT = HERE.parents[1]
import record_hd7  # noqa: E402

if __name__ == "__main__":
    sys.exit(record_hd7.main())
