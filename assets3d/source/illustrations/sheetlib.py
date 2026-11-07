"""コンタクトシート用の共通関数（Pillow）。"""
import os
from PIL import Image, ImageDraw, ImageFont

BG = (255, 255, 255)
CHECK = (236, 238, 235)


def font(size, bold=False):
    for f in (r"C:\Windows\Fonts\YuGothB.ttc" if bold else r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\meiryo.ttc"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def trim(im, pad=0.06, square=True, size=None):
    """透過 PNG の余白を切り、正方形のキャンバスの中央に置く（長辺に合わせる）。"""
    im = im.convert("RGBA")
    bb = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if bb:
        im = im.crop(bb)
    w, h = im.size
    side = int(max(w, h) * (1 + pad * 2))
    cv = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    cv.paste(im, ((side - w) // 2, (side - h) // 2), im)
    if size:
        cv = cv.resize((size, size), Image.LANCZOS)
    return cv


def paste_fit(sheet, im, box, bgcol=None):
    x0, y0, x1, y1 = box
    d = ImageDraw.Draw(sheet)
    if bgcol:
        d.rectangle(box, fill=bgcol)
    im = im.convert("RGBA")
    s = min((x1 - x0) / im.width, (y1 - y0) / im.height)
    im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
    sheet.paste(im, (x0 + (x1 - x0 - im.width) // 2, y0 + (y1 - y0 - im.height) // 2), im)
