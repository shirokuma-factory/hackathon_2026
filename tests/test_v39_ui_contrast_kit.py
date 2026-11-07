"""道具の採点 r1b #13・#14・#16 の修正の確認。

- #13: 荷待ちのページと比べるページの薄い文字（--faint）が、明るい配色・暗い配色・印刷の配色のどれでも、背景に対して 4.5:1 以上。
- #16: アプリ（web/own.js・waiting_map.js・market.js）の凡例の色と区分は web/kit/kit.js の Kit.palette を読み、部品と同じ値になる。
- #14: 400px で出典は「出典」を押すまで閉じていて、チェックボックスなどの押せる所が 24px 以上。
画面の確認はヘッドレス Edge。Edge が無いときだけ飛ばし、起動の時間切れは失敗にする。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest
from conftest import edge_page, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
WEB = ROOT / "web"
PAGES = [
    ROOT / "tools" / "waiting_page_template.html", ROOT / "tools" / "compare_lite_template.html",
    WEB / "waiting.html", WEB / "compare_lite.html",
]


def _lum(hex_: str) -> float:
    h = hex_.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a: str, b: str) -> float:
    x, y = sorted([_lum(a), _lum(b)], reverse=True)
    return (x + 0.05) / (y + 0.05)


def _read(p: Path) -> str:
    """テンプレートは /*__UI_KIT__*/ の所にデザインキット（tools/ui_kit.css）を埋めて読む（デザインの作り直し 2026-11-07）。"""
    html = p.read_text(encoding="utf-8")
    return html.replace("/*__UI_KIT__*/", (ROOT / "tools" / "ui_kit.css").read_text(encoding="utf-8"))


def _var_blocks(html: str) -> list[dict[str, str]]:
    """--faint を持つ変数の組（明るい・暗い（media）・暗い（data-theme）・印刷）を順に返す。"""
    out = []
    for m in re.finditer(r"\{([^{}]*--faint:[^{}]*)\}", html):
        v = dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-fA-F]{6})", m.group(1)))
        # 暗い配色の --hi は半透明（rgba）。カードの上に重なるので、カードの色と合成した色で測る
        for name, r, g, b, a in re.findall(r"(--[a-z-]+):\s*rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([0-9.]+)\)", m.group(1)):
            if name in v or "--card" not in v:
                continue
            base = [int(v["--card"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
            mix = [round(float(a) * int(c) + (1 - float(a)) * bc) for c, bc in zip((r, g, b), base)]
            v[name] = "#%02x%02x%02x" % tuple(mix)
        out.append(v)
    return out


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_faint_text_has_contrast_4_5_on_every_background_in_every_theme(page):
    blocks = _var_blocks(_read(page))
    assert len(blocks) >= 3, page  # 明るい・暗い（media）・暗い（data-theme）。荷待ちのページは印刷用もある
    for v in blocks:
        for bg in ("--card", "--bg", "--hi", "--head-bg"):
            ratio = contrast(v["--faint"], v[bg])
            assert ratio >= 4.5, (page.name, v["--faint"], bg, v[bg], round(ratio, 2))


def test_generated_pages_use_the_same_faint_colours_as_the_templates():
    for tpl, out in ((PAGES[0], PAGES[2]), (PAGES[1], PAGES[3])):
        a = [b["--faint"] for b in _var_blocks(_read(tpl))]
        b = [b["--faint"] for b in _var_blocks(out.read_text(encoding="utf-8"))]
        assert a == b, out.name


# ---------------------------------------------------------------- #16 色と区分の共有
def test_app_scripts_read_the_kit_palette_instead_of_their_own_colours():
    own, wm, mk = [(WEB / n).read_text(encoding="utf-8") for n in ("own.js", "waiting_map.js", "market.js")]
    assert "Kit.palette.legs.colors" in own and "Kit.palette.legs.labels" in own
    assert "Kit.palette.bands" in wm and "Kit.palette.flows" in mk
    for src, triples in ((own, ["92, 200, 255, 240", "244, 150, 70", "150, 162, 180"]), (wm, ["255, 226, 120", "178, 28, 92"]), (mk, ["88, 150, 205", "236, 249, 255"])):
        for t in triples:
            assert t not in src, t
    for name, ref in (("legs", "Kit.palette.legs"), ("points", "Kit.palette.bands"), ("flows", "Kit.palette.flows")):
        assert ref in (WEB / "kit" / f"{name}.js").read_text(encoding="utf-8"), name
    html = (WEB / "index.html").read_text(encoding="utf-8")
    k = html.index("kit/kit.js")
    assert k < html.index('src="own.js"') and k < html.index('src="market.js"') and k < html.index('src="waiting_map.js"')


# ---------------------------------------------------------------- 画面（Edge）
class Browser:
    """共通の配信サーバー（セッションで 1 つ）に、Edge を 1 つ直列につなぐ。終わりに必ず止める。"""

    def __init__(self):
        self.events: list[dict] = []
        self.page = None
        self._cm = None

    def __enter__(self):
        import capture_screens as cs
        self.port = server_port("local")
        self._cm = edge_page(1920, 1080)
        page = self._cm.__enter__()
        events = self.events

        def send(method, **params):
            page._id += 1
            mid = page._id
            page.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            while True:
                msg = json.loads(page.ws.recv(timeout=90))
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg.get("result", {})
                events.append(msg)

        page.send = send
        page.send("Log.enable")
        self.cs = cs
        self.page = page
        return self

    def open(self, hash_: str, width: int, height: int = 900):
        import capture_web as cw
        p = self.page
        p.send("Page.addScriptToEvaluateOnNewDocument", source=cw.ERR_HOOK)
        self.events.clear()
        p.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=width < 600)
        p.goto("about:blank")
        open_app(p, f"http://127.0.0.1:{self.port}/index.html{hash_}", tag="contrast_kit_fail")
        time.sleep(4)
        return p

    def problems(self) -> list[str]:
        out = []
        for m in self.events:
            if "Tracking Prevention" in json.dumps(m, ensure_ascii=False):
                continue
            meth, p = m.get("method"), m.get("params", {})
            if meth == "Runtime.consoleAPICalled" and p.get("type") in ("error", "warning", "assert"):
                out.append("console." + p["type"] + ": " + " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))[:200])
            elif meth == "Runtime.exceptionThrown":
                out.append("exception: " + json.dumps(p.get("exceptionDetails", {}), ensure_ascii=False)[:200])
            elif meth == "Log.entryAdded" and p.get("entry", {}).get("level") in ("error", "warning"):
                out.append("log: " + p["entry"].get("text", "") + " " + p["entry"].get("url", ""))
        return out

    def __exit__(self, *a):
        if self._cm is not None:
            self._cm.__exit__(*a)


SMALL_JS = """JSON.stringify([...document.querySelectorAll('button,input,select,summary,.f-chk')].filter(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
  return r.width>0&&r.height>0&&s.visibility!='hidden'&&(r.width<24||r.height<24)&&r.bottom>0&&r.top<innerHeight&&e.type!='hidden'}).map(e=>e.tagName+'#'+e.id+' '+Math.round(e.getBoundingClientRect().width)+'x'+Math.round(e.getBoundingClientRect().height)))"""


def test_kit_palette_equals_the_waiting_map_bands_and_400px_attribution_is_collapsed():
    with Browser() as b:
        p = b.open("#v=find", 400)
        # 色と区分: アプリの荷待ちの 4 段は Kit.palette.bands と同じ
        same = p.js("JSON.stringify(WaitingMap.BANDS.map(x=>[x.min,x.label,x.rgb])) === JSON.stringify(Kit.palette.bands.map(x=>[x.min,x.label,x.color]))")
        assert same is True
        # #14: 出典は閉じていて、「出典」を押すと開く
        assert p.js("getComputedStyle(document.querySelector('#attr-toggle')).display") == "block"
        assert p.js("getComputedStyle(document.querySelector('.maplibregl-ctrl-attrib')).display") == "none"
        assert p.js("document.querySelector('#attr-toggle').getAttribute('aria-expanded')") == "false"
        p.js("document.querySelector('#attr-toggle').click()")
        assert p.js("getComputedStyle(document.querySelector('.maplibregl-ctrl-attrib')).display") == "block"
        assert p.js("document.querySelector('#attr-toggle').getAttribute('aria-expanded')") == "true"
        assert "国土数値情報" in p.js("document.querySelector('.maplibregl-ctrl-attrib').textContent")
        p.js("document.querySelector('#attr-toggle').click()")
        small = json.loads(p.js(SMALL_JS))
        assert small == [], small
        assert b.problems() == [], b.problems()
        assert p.js("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_desktop_has_no_attribution_toggle_and_no_console_problems():
    with Browser() as b:
        p = b.open("#v=find", 1920, 1080)
        assert p.js("getComputedStyle(document.querySelector('#attr-toggle')).display") == "none"
        assert p.js("getComputedStyle(document.querySelector('.maplibregl-ctrl-attrib')).display") != "none"
        assert b.problems() == [], b.problems()
