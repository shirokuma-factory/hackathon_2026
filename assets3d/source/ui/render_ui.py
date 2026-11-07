"""blender -b -P render_ui.py -- name [name ...]  （引数なしで全部）
1024px の透過 PNG を outputs/ui_icons_2026-11-07/ に出す。WebP 化は make_sheet.py。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_lib as L  # noqa: E402
import ui_models as M  # noqa: E402

names = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else list(M.MODELS)
os.makedirs(L.OUT, exist_ok=True)
for n in names:
    t = time.time()
    sc, cam = L.setup_scene()
    M.MODELS[n]()
    L.autofit(sc, cam, os.path.join(L.OUT, "_scratch.png"))
    L.render_to(sc, os.path.join(L.OUT, n + ".png"), 1024, 192)
    print("DONE", n, round(time.time() - t, 1), "s", flush=True)
