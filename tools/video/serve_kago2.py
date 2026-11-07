"""鹿児島版（第2版、高解像度録画用）のスナップショットを配信する。
outputs/video/kago_env2（data・src・serve_web.py の写し）と outputs/video/web_kago2（web/ の写し、運送会社名を「架空 さつま運輸」に直したもの）を使う。
リポジトリの web/ src/ data/ には触れない。

使い方:  python tools/video/serve_kago2.py --port 8660
"""
import importlib.util
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENV = REPO / "outputs" / "video" / "kago_env2"
WEB = REPO / "outputs" / "video" / "web_kago2"

os.environ["SHIPCOMPARE_DATA_DIR"] = str(ENV / "data")
sys.path.insert(0, str(ENV / "src"))
spec = importlib.util.spec_from_file_location("serve_web_kago2", ENV / "tools" / "serve_web.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["serve_web_kago2"] = mod
spec.loader.exec_module(mod)
mod.WEB_DIR = WEB
if __name__ == "__main__":
    sys.exit(mod.main())
