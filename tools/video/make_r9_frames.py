"""r9 のアプリの場面: 元の録画（切り抜き・寄りなし、4K）から区間を切り、ブラウザーの窓に入れる。

r8 までは focus_clips.py の仮想カメラ（強調枠へ 0.7〜0.9 秒で寄って移る）を窓に入れていたため、
画面の全体が見えず、動きが速くて酔いそうになった（作成者の指摘 11/7）。r9 は画面全体を動かさずに見せ、
縦に流れる録画は 0.75 倍に落とす。

出力: outputs/video/rawcut/<名前>.mp4（4K の切り出し）、outputs/video/bframe/r9/<名前>.mp4（1080）と <名前>_4k.mp4
使い方: uv run --no-project --with opencv-python-headless --with pillow --with numpy python tools/video/make_r9_frames.py [名前 ...]
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OV = ROOT / "outputs" / "video"
FF = "C:/ai/bin/ffmpeg.exe"

# 名前: (録画, 始め, 終わり, 速さ)
CUTS = {
    "W_R2": ("rec6/R2g.mp4", 2.4, 14.4, 1.0),       # H2 帰りが空く理由（市場の地図）
    "W_FLOW0": ("rec6/FLOW0.mp4", 0.0, 14.0, 1.0),  # H4 始めの画面
    "W_F1": ("rec6/R2g.mp4", 19.0, 25.0, 1.0),      # できること 1: 帰りが空く場所を見る
    "W_F2": ("rec6/R5cg.mp4", 6.0, 12.0, 1.0),      # できること 2: 空いた荷台と荷物をつなぐ
    "W_F3": ("rec7/NEGOTIATE.mp4", 0.2, 5.0, 0.75),  # できること 3: 荷待ちの相談の書面
    "W_F4": ("rec6/R5ag.mp4", 1.0, 7.0, 1.0),       # できること 4: 送り方を比べる
    "W_OWNVIZ": ("rec7/OWNVIZ.mp4", 0.3, 15.6, 0.75),
    "W_NEGO": ("rec7/NEGOTIATE.mp4", 0.2, 14.8, 0.75),
    "W_P7": ("rec6/R5ag.mp4", 1.0, 7.0, 1.0),
    "W_P8": ("rec6/R5bg.mp4", 1.5, 9.37, 0.8),
    "W_QUOTE": ("rec7/QUOTE.mp4", 0.0, 12.6, 0.8),
    "W_P9": ("rec6/R5cg.mp4", 0.0, 2.63, 1.0),
    "W_P10": ("rec6/R5cg.mp4", 2.0, 6.5, 1.0),
    "W_P12": ("rec6/R5cg.mp4", 6.0, 14.5, 1.0),
    "W_END": ("rec6/R2g.mp4", 14.0, 26.0, 1.0),
    "W_C1": ("rec6/R2g.mp4", 14.0, 26.0, 1.0),   # C1 用（W_END と同じ絵。W_END には最後のクレジットの行が付くので名前を分ける）
    # r12: 窓を左右に出し分ける（作成者の指摘 11/7）。_L は窓が左、字幕は右の列
    "W_FLOW0_L": ("rec6/FLOW0.mp4", 0.0, 14.0, 1.0, "left"),
    "W_F2_L": ("rec6/R5cg.mp4", 6.0, 12.0, 1.0, "left"),
    "W_F4_L": ("rec6/R5ag.mp4", 1.0, 7.0, 1.0, "left"),
    "W_NEGO_L": ("rec7/NEGOTIATE.mp4", 0.2, 14.8, 0.75, "left"),
    "W_P8_L": ("rec6/R5bg.mp4", 1.5, 9.37, 0.8, "left"),
    "W_P9_L": ("rec6/R5cg.mp4", 0.0, 2.63, 1.0, "left"),
    "W_P10_L": ("rec6/R5cg.mp4", 2.0, 6.5, 1.0, "left"),
    "W_P12_L": ("rec6/R5cg.mp4", 6.0, 14.5, 1.0, "left"),
    "W_LIVE_L": ("rec7/LIVE.mp4", 0.3, 13.5, 1.0, "left"),   # H2「国道を走る大型車の数」
    # r20: アプリが地図の層のずれ（画素比 2 で県境・経路が 1.33 倍に拡大して 2 重に見えた、web/app.js 0c81241）を直したあとの撮り直し。
    # 区間は r16 の W16_* と同じ（撮り方の手順が同じなので、同じ秒に同じ画面が出ることをコンタクトシートで確かめた）
    "W20_R2": ("rec20/R2g.mp4", 2.4, 14.4, 1.0),
    "W20_LIVE_L": ("rec20/LIVE2.mp4", 0.5, 13.2, 1.0, "left"),
    "W20_F1": ("rec20/R2g.mp4", 19.0, 25.0, 1.0),
    "W20_F2_L": ("rec20/R5cg.mp4", 6.0, 12.0, 1.0, "left"),
    "W20_F3": ("rec20/FLOW0c.mp4", 5.0, 11.0, 1.0),
    "W20_F4_L": ("rec20/R5ag.mp4", 1.0, 7.0, 1.0, "left"),
    "W20_C1": ("rec20/R2g.mp4", 14.0, 26.0, 1.0),
    "W20_END": ("rec20/R2g.mp4", 14.0, 26.0, 1.0),
    "W20_IMPORT": ("rec20/FLOW0b.mp4", 2.0, 8.7, 1.0),
    "W20_OWNVIZ": ("rec20/OWNVIZ.mp4", 2.0, 15.3, 0.75),
    "W20_P7": ("rec20/R5ag.mp4", 1.0, 13.2, 1.0),
    "W20_P8_L": ("rec20/R5bg.mp4", 0.5, 15.5, 0.8, "left"),
    "W20_P9_L": ("rec20/R5cg.mp4", 0.0, 2.63, 1.0, "left"),
    "W20_P10_L": ("rec20/R5cg.mp4", 2.0, 6.5, 1.0, "left"),
    "W20_P12_L": ("rec20/R5cg.mp4", 6.0, 17.9, 1.0, "left"),
}


def main() -> None:
    names = sys.argv[1:] or list(CUTS)
    (OV / "rawcut").mkdir(parents=True, exist_ok=True)
    (OV / "bframe/r9").mkdir(parents=True, exist_ok=True)
    for n in names:
        src, t0, t1, sp, *rest = CUTS[n]
        side = rest[0] if rest else "right"
        cut = OV / "rawcut" / f"{n}.mp4"
        subprocess.run([FF, "-v", "error", "-y", "-ss", f"{t0}", "-t", f"{t1 - t0}", "-i", str(OV / src),
                        "-vf", f"setpts=PTS/{sp},fps=30", "-an", "-c:v", "libx264", "-crf", "12", "-preset", "fast", "-pix_fmt", "yuv420p", str(cut)], check=True)
        for sc, tag in ((2, "_4k"), (1, "")):
            subprocess.run([sys.executable, str(ROOT / "tools/video/browser_frame_ff.py"), "--in", str(cut),
                            "--out", str(OV / "bframe/r9" / f"{n}{tag}.mp4"), "--scale", str(sc), "--side", side], check=True)


if __name__ == "__main__":
    main()
