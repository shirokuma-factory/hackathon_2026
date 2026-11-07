"""連鎖の図（docs/video/motion/CHAIN_m.html）を、P1 の声に合わせた時刻で書き出す。

r8 までは図を 3 つに切って言葉に当てていたため、切れ目で図が消え、言葉の途中で次の項目へ移っていた（作成者の指摘 11/7）。
r9 からは 1 本の図を流し、各項目が出始める秒（TM）を声の文字の時刻から決める。図の 0 秒は P1 の声の始まり。

使い方: uv run python tools/video/make_chain_timed.py --voice-dir outputs/video/voice/tight_r9 --atempo 1.10
出力: docs/video/motion/CHAIN_p1_m.html（このあと render_motion.py で .mov にする）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_subtitles as ms  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "outputs" / "video" / "voice"
# 項目: (読みの言葉, その言葉より何秒前に出始めるか)
# r10: P1 は「運転手が足りない」と「2030 年度に約 3 分の 1」だけを言うので、図も 2 項目（two）にする
ANCHORS = {"n2": ("運転手が", 0.4), "n3": ("にせんさんじゅうねんど", 0.2), "n3c": ("さんぶんのいち", 0.5), "n3d": ("足りなく", 0.3)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice-dir", required=True)
    ap.add_argument("--atempo", type=float, default=1.10)
    ap.add_argument("--tag", default="r7")
    a = ap.parse_args()
    ms.KEEP_DIR = ROOT / a.voice_dir
    al = json.loads((VOICE / f"P1_{a.tag}.json").read_text(encoding="utf-8"))["alignment"]
    rd = "".join(al["characters"])
    sil = ms.silences(VOICE / f"P1_{a.tag}.mp3")
    tm = {}
    for k, (w, lead) in ANCHORS.items():
        i = rd.find(w)
        if i < 0:
            raise SystemExit(f"P1 の読みに「{w}」がありません")
        tm[k] = round(max(0.0, ms.squeeze(al["character_start_times_seconds"][i], sil) / a.atempo - lead), 2)
    tm["n1"], tm["lim"] = 0.0, 99.0  # 2 項目の形では出さない
    end = ms.squeeze(al["character_end_times_seconds"][-1], sil) / a.atempo
    tm["fade"] = round(end + 0.1, 2)   # P1 の声が終わってから。H1 の重ねは、この秒から再生する
    # H1「運ぶ人が足りないのに……運べる量の、ろくわりは」: 「6割」の一文を「運べる量」と言う所で出す。
    # H1 の重ねは図の fade+0.7 秒を「運ぶ人が」の言い始めに合わせて置く（r10_plan.json の land 0.7）
    a1 = json.loads((VOICE / f"H1_{a.tag}.json").read_text(encoding="utf-8"))["alignment"]
    r1 = "".join(a1["characters"])
    s1 = ms.silences(VOICE / f"H1_{a.tag}.mp3")
    t_hako = ms.squeeze(a1["character_start_times_seconds"][r1.find("運ぶ人が")], s1) / a.atempo
    t_beru = ms.squeeze(a1["character_start_times_seconds"][r1.find("運べる量")], s1) / a.atempo
    tm["f2"] = round(tm["fade"] + 0.7 + (t_beru - t_hako) - 0.2, 2)
    tm["two"], tm["fadeTo"] = "true", 1.0
    tm["D"] = round(tm["f2"] + 6.0, 1)
    src = (ROOT / "docs/video/motion/CHAIN_m.html").read_text(encoding="utf-8")
    js = "{" + ", ".join(f"{k}: {v}" for k, v in tm.items()) + "}"
    out, n = re.subn(r"var TM = \{[^}]*\};", f"var TM = {js};", src)
    out, m = re.subn(r'data-duration="[0-9.]+"', f'data-duration="{tm["D"]}"', out)
    if n != 1 or m != 1:
        raise SystemExit("CHAIN_m.html の TM か data-duration が見つかりません")
    with open(ROOT / "docs/video/motion/CHAIN_p1_m.html", "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
    print(json.dumps(tm, ensure_ascii=False))


if __name__ == "__main__":
    main()
