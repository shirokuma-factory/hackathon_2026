"""C 案の線画の 2 枚（主役と細部・強調）を重ねて <name>_line.png にする。"""
import os
from PIL import Image
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
RAW = os.path.join(ROOT, "outputs", "illustrations_2026-11-07", "C_steps", "raw")


def merge():
    for f in sorted(os.listdir(RAW)):
        if f.endswith("_line_main.png"):
            base = f[: -len("_line_main.png")]
            a = Image.open(os.path.join(RAW, f)).convert("RGBA")
            b = Image.open(os.path.join(RAW, base + "_line_acc.png")).convert("RGBA")
            a.alpha_composite(b)
            a.save(os.path.join(RAW, base + "_line.png"))


if __name__ == "__main__":
    merge()
