"""B 案の線画の 2 枚（主役と強調）を重ねて <role>_line.png にする。"""
import os
from PIL import Image
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
RAW = os.path.join(ROOT, "outputs", "illustrations_2026-11-07", "B_role_cards", "raw")


def merge():
    for tag in ("shipper", "carrier"):
        a = Image.open(os.path.join(RAW, f"{tag}_line_main.png")).convert("RGBA")
        b = Image.open(os.path.join(RAW, f"{tag}_line_acc.png")).convert("RGBA")
        a.alpha_composite(b)
        a.save(os.path.join(RAW, f"{tag}_line.png"))


if __name__ == "__main__":
    merge()
