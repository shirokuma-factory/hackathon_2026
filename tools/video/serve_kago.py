"""鹿児島版の素材の録画用に、スナップショット（outputs/video/kago_env と web_kago）を配信する。

tools/serve_web.py の写し（outputs/video/kago_env/tools/serve_web.py）を読み込み、
データと web を鹿児島版に向けて起動する。リポジトリの web/ src/ data/ には触れない。

使い方:  python tools/video/serve_kago.py --port 8650
"""
import importlib.util
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENV = REPO / "outputs" / "video" / "kago_env"
WEB = REPO / "outputs" / "video" / "web_kago"

os.environ["SHIPCOMPARE_DATA_DIR"] = str(ENV / "data")
sys.path.insert(0, str(ENV / "src"))
spec = importlib.util.spec_from_file_location("serve_web_kago", ENV / "tools" / "serve_web.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["serve_web_kago"] = mod
spec.loader.exec_module(mod)
mod.WEB_DIR = WEB
if __name__ == "__main__":
    sys.exit(mod.main())
