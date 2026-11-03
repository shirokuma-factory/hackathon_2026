"""実アプリ（Streamlit）の 1920x1080 スクリーンショットを再現可能に撮る。

標準ライブラリ + websockets + Pillow と、入っている Microsoft Edge だけを使う。
Edge の場所は環境変数 EDGE_PATH、なければ一般的なインストール先から探す。

使い方:
    uv run python tools/capture_screens.py --all
    uv run python tools/capture_screens.py --scene S5_quotes_ranked_breakdown
    uv run python tools/capture_screens.py --list
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "assets"
DEFAULT_PORT = 8502
PORT = DEFAULT_PORT
BASE = f"http://localhost:{PORT}"
W, H = 1920, 1080


class SceneError(Exception):
    pass


# ------------------------------------------------------------------ 起動と終了

def find_edge() -> str:
    env = os.environ.get("EDGE_PATH")
    if env and Path(env).exists():
        return env
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            c = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if c.exists():
                return str(c)
    found = shutil.which("msedge")
    if found:
        return found
    raise SystemExit("Edge が見つかりません。環境変数 EDGE_PATH に msedge.exe のパスを指定してください。")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_health(timeout: float = 90) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"{BASE}/_stcore/health", timeout=2) as r:
                if r.read().decode().strip() == "ok":
                    return
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit("Streamlit が起動しませんでした。")


def stop_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(10)
    except Exception:
        proc.kill()


# ------------------------------------------------------------------ CDP

FIND_JS = r"""
(function(spec){
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  let list = [];
  if (spec.kind === 'aria') {
    list = [...document.querySelectorAll('[aria-label]')].filter(e => spec.exact
      ? e.getAttribute('aria-label') === spec.text : e.getAttribute('aria-label').includes(spec.text));
    if (spec.tag) list = list.filter(e => e.matches(spec.tag));
  } else if (spec.kind === 'tab') {
    list = [...document.querySelectorAll('[role=tab]')].filter(e => norm(e.innerText).includes(spec.text));
  } else if (spec.kind === 'button') {
    list = [...document.querySelectorAll('button')].filter(e => norm(e.innerText).includes(spec.text));
  } else if (spec.kind === 'text') {
    list = [...document.querySelectorAll('body *')].filter(e => e.children.length === 0 && norm(e.textContent).includes(spec.text));
  } else if (spec.kind === 'css') {
    list = [...document.querySelectorAll(spec.text)];
  }
  list = list.filter(vis);
  return list[spec.index || 0] || null;
})
"""


class Page:
    def __init__(self, ws_url: str):
        self._cm = connect(ws_url, max_size=None, open_timeout=20)
        self.ws = self._cm.__enter__() if hasattr(self._cm, "__enter__") else self._cm
        self._id = 0
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send("DOM.enable")
        self.send("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)

    def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=60))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError("JS error: " + json.dumps(r["exceptionDetails"], ensure_ascii=False)[:400])
        return r["result"].get("value")

    def goto(self, url: str) -> None:
        self.send("Page.navigate", url=url)
        time.sleep(0.5)
        self.wait(lambda: self.js("document.readyState") == "complete", 30, "ページ読み込み")

    def wait(self, cond, timeout: float, what: str):
        end = time.time() + timeout
        while time.time() < end:
            try:
                v = cond()
            except Exception:
                v = False
            if v:
                return v
            time.sleep(0.2)
        raise SceneError(f"待ちが時間切れ: {what}（{timeout:.0f}秒）")

    # ---- マウス・キーボード（本物の入力イベント）
    def mouse(self, x: float, y: float) -> None:
        self.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        self.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
        self.send("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)

    def key(self, name: str, ctrl: bool = False) -> None:
        codes = {"Enter": (13, "\r"), "Tab": (9, ""), "a": (65, "a")}
        vk, text = codes[name]
        base = dict(key=name, code=("Key" + name.upper() if len(name) == 1 else name),
                    windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk, modifiers=2 if ctrl else 0)
        down = dict(base, type="keyDown")
        if text and not ctrl:
            down["text"] = text
        self.send("Input.dispatchKeyEvent", **down)
        self.send("Input.dispatchKeyEvent", **dict(base, type="keyUp"))

    def insert(self, text: str) -> None:
        self.send("Input.insertText", text=text)

    # ---- 要素の探索
    def _find(self, spec: dict) -> str:
        return f"({FIND_JS})({json.dumps(spec, ensure_ascii=False)})"

    def exists(self, spec: dict) -> bool:
        return bool(self.js(f"!!{self._find(spec)}"))

    def center(self, spec: dict, timeout: float = 15):
        self.wait(lambda: self.exists(spec), timeout, "要素 " + json.dumps(spec, ensure_ascii=False))
        self.js(f"{self._find(spec)}.scrollIntoView({{block:'center',inline:'center'}})")
        time.sleep(0.3)
        return self.js(f"(function(){{const r={self._find(spec)}.getBoundingClientRect();"
                       "return [r.x + r.width/2, r.y + r.height/2];})()")

    def click(self, kind: str, text: str) -> None:
        x, y = self.center(dict(kind=kind, text=text))
        self.mouse(x, y)
        time.sleep(0.4)

    def body_text(self) -> str:
        return self.js("document.body.innerText") or ""

    def idle(self, timeout: float = 30) -> None:
        """Streamlit の再実行が終わるまで待つ。"""
        time.sleep(0.6)

        def ok():
            return self.js("""(function(){
              const w = document.querySelector('[data-testid="stStatusWidget"]');
              return !w || w.getBoundingClientRect().width === 0;})()""")
        self.wait(ok, timeout, "Streamlit の再実行の完了")
        time.sleep(0.4)

    def wait_chart(self, timeout: float = 60) -> None:
        """Altair のグラフ（canvas か svg）が描かれるまで待つ。"""
        # canvas/svg が見つからない描き方でも、グラフの枠に高さがあれば描けたとみなす
        self.wait(lambda: self.js("""(function(){
            if (document.querySelector('.vega-embed canvas, .vega-embed svg')) return true;
            const c = document.querySelector('[data-testid="stVegaLiteChart"]');
            return !!c && c.getBoundingClientRect().height > 50;})()"""),
                  timeout, "グラフの描画")

    def need_field(self, label: str, expected: str) -> None:
        """入力欄（textarea）の値は innerText に出ないので、値を直接見る。"""
        v = self.js(f"""(function(){{const e=[...document.querySelectorAll('textarea')].find(
            t=>t.getAttribute('aria-label')===({json.dumps(label, ensure_ascii=False)}));
            return e ? e.value : null;}})()""")
        if v is None or expected not in v:
            raise SceneError(f"入力欄「{label}」の値が期待と違います: {v!r}")

    def check_no_exception(self) -> None:
        msg = self.js("""(function(){const e=document.querySelector('[data-testid="stException"]');
          return e ? e.innerText.slice(0,200) : null;})()""")
        if msg:
            raise SceneError("アプリが例外を表示しています: " + msg.replace(chr(10), " "))

    def hide_chrome(self) -> None:
        """Streamlit 自身のお知らせの吹き出しを隠す（アプリの表示には関係しない）。"""
        self.js("""(function(){const s=document.createElement('style');
          s.textContent='[data-testid="stToast"],[data-testid="stToastContainer"]{display:none !important}';
          document.head.appendChild(s);return true;})()""")

    def wait_text(self, text: str, timeout: float = 20) -> None:
        self.wait(lambda: text in self.body_text(), timeout, f"文字「{text}」の表示")

    def need(self, *texts: str, timeout: float = 15) -> None:
        """画面の文字に全部含まれるまで待つ。時間内に揃わなければ、足りない文字を挙げて失敗する。"""
        end = time.time() + timeout
        while True:
            body = self.body_text()
            miss = [t for t in texts if t not in body]
            if not miss:
                return
            if time.time() > end:
                raise SceneError("画面に必要な文字がありません: " + " / ".join(miss))
            time.sleep(0.4)

    # ---- Streamlit 部品
    def select(self, label: str, option: str) -> None:
        x, y = self.center(dict(kind="aria", text=label, exact=True, tag="input"))
        self.mouse(x, y)
        time.sleep(0.3)
        self.insert(option)
        time.sleep(0.5)
        self.key("Enter")
        self.idle()

    def fill(self, label: str, value: str, tab: bool = True) -> None:
        x, y = self.center(dict(kind="aria", text=label, exact=True, tag="input"))
        self.mouse(x, y)
        self.key("a", ctrl=True)
        self.insert(value)
        if tab:
            self.key("Tab")
        self.idle()

    def fill_area(self, label: str, value: str) -> None:
        x, y = self.center(dict(kind="aria", text=label, exact=True, tag="textarea"))
        self.mouse(x, y)
        self.insert(value)
        self.key("Tab")
        self.idle()

    def tab(self, name: str) -> None:
        self.click("tab", name)
        self.idle()

    def button(self, text: str) -> None:
        self.click("button", text)
        self.idle()

    def scroll_to_text(self, text: str, block: str = "start") -> None:
        spec = dict(kind="text", text=text)
        self.wait(lambda: self.exists(spec), 10, f"文字「{text}」")
        self.js(f"{self._find(spec)}.scrollIntoView({{block:'{block}'}})")
        # 表（canvas）はスクロールで画面に入ってから列を描くので、描き終わるまで待つ
        time.sleep(3.0)

    def shot(self, name: str) -> Path:
        from PIL import Image
        self.check_no_exception()
        time.sleep(1.0)  # グラフの描画を待つ
        r = self.send("Page.captureScreenshot", format="png", fromSurface=True,
                      clip={"x": 0, "y": 0, "width": W, "height": H, "scale": 1})
        img = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / name
        img.save(path, "JPEG", quality=85)
        print(f"保存: {path.relative_to(ROOT)}  {img.size[0]}x{img.size[1]}")
        return path


# ------------------------------------------------------------------ 場面の共通手順

def fresh(p: Page, scenario: str) -> None:
    p.goto(BASE)
    p.wait_text("Backfull", 60)
    p.hide_chrome()
    p.idle()
    p.select("場面を読み込む", scenario)
    p.button("この条件で確定")
    p.wait_text("確定済み")


def add_quote(p: Page, carrier: str, amounts: dict[str, int], demo: bool = False) -> None:
    """見積回答のフォームに入れて追加する（税抜）。amounts にない項目は「含む」。demo なら架空の印を付ける。"""
    p.fill("会社名", carrier, tab=False)
    p.fill("商品名", "路線便", tab=False)
    names = {"pickup": "集荷", "trunk": "幹線", "delivery": "配達", "handling": "荷役",
             "fuel": "燃料", "highway": "高速", "other": "その他"}
    for key, nm in names.items():
        if key in amounts:
            p.select(nm, "金額")
            p.fill(f"{nm}の金額（円）", str(amounts[key]))
        else:
            p.select(nm, "含む")
    if demo:
        p.click("text", "架空の回答（デモ用）")
    p.button("この回答を追加")
    p.wait_text(f"{carrier} の回答を追加しました")
    p.idle()


A_FULL = {"pickup": 6000, "trunk": 18000, "delivery": 5000}


def s5_with_demo_and_a(p: Page) -> None:
    fresh(p, "S5")
    p.tab("4 見積回答")
    p.button("架空の回答例を入れる")
    p.wait_text("架空の回答例を 2 件入れました")
    add_quote(p, "架空A社", A_FULL, demo=True)


# ------------------------------------------------------------------ 場面（返り値は出力ファイル名）

def scene_s1_estimates(p: Page) -> str:
    fresh(p, "S1")
    p.tab("2 目安額")
    p.need("合計重量", "計算重量", "252 kg")
    p.wait_chart()
    p.scroll_to_text("公表運賃からの目安（税抜）")
    return "2026-11-03_S1_estimates.jpg"


def scene_s2_estimates_chart(p: Page) -> str:
    fresh(p, "S2")
    p.tab("2 目安額")
    p.need("合計重量", "406.56 kg")
    p.wait_chart()
    p.scroll_to_text("公表運賃からの目安（税抜）")
    return "2026-11-03_S2_estimates_chart.jpg"


def scene_s5_quotes_demo_partial(p: Page) -> str:
    fresh(p, "S5")
    p.tab("4 見積回答")
    p.button("架空の回答例を入れる")
    p.wait_text("架空の回答例を 2 件入れました")
    p.need("比べる相手がいません", "不完全な回答（順位に入れません）", "費用の内訳")
    p.scroll_to_text("見積回答は、いまの条件の版")
    return "2026-11-03_S5_quotes_demo_partial.jpg"


def scene_s5_quotes_ranked_breakdown(p: Page) -> str:
    s5_with_demo_and_a(p)
    p.need("費用の内訳", "不完全な回答（順位に入れません）")
    p.wait_chart()
    p.scroll_to_text("完全な回答")
    p.need("26,000", "29,000", "3,000")
    return "2026-11-03_S5_quotes_ranked_breakdown.jpg"


def scene_s5_quotes_history(p: Page) -> str:
    s5_with_demo_and_a(p)
    p.tab("1 出荷条件")
    p.select("集荷か持込か", "持込")
    p.button("この条件で確定")
    p.wait_text("条件の版 2")
    p.tab("4 見積回答")
    p.need("条件変更前の回答")
    p.scroll_to_text("条件変更前の回答")
    return "2026-11-03_S5_quotes_history.jpg"


REASON = "集荷から配達まで含む総額が最安で、追加の費用が出にくいため。"


def judgment_state(p: Page) -> None:
    s5_with_demo_and_a(p)
    p.tab("5 判断と保存")
    p.select("選ぶ回答（完全な回答だけ選べます）", "架空B社")
    p.fill_area("選んだ理由", REASON)
    p.fill_area("残る確認事項（1行に1つ）", "集荷日の指定\n荷姿の確認")


def scene_s5_judgment_notice(p: Page) -> str:
    judgment_state(p)
    p.need("発注は確定していません")
    p.need_field("選んだ理由", REASON)
    p.scroll_to_text("発注は確定していません")
    return "2026-11-03_S5_judgment_notice.jpg"


INTERCEPT = r"""
(function(){
  window.__dl = null;
  if (!window.__origClick) window.__origClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function(){
    if (this.href && (this.hasAttribute('download') || this.href.includes('/media/'))) { window.__dl = this.href; return; }
    return window.__origClick.apply(this, arguments);
  };
  return true;
})()
"""


def scene_s5_restored(p: Page) -> str:
    judgment_state(p)
    tmpdir = Path(tempfile.mkdtemp(prefix="shipcase_"))
    tmp = tmpdir / "case.json"
    try:
        p.js(INTERCEPT)
        p.button("この出荷を JSON で保存")
        href = p.wait(lambda: p.js("window.__dl"), 15, "保存 JSON の URL")
        text = p.js(f"fetch({json.dumps(href)}).then(r => r.text())")
        if '"quotes"' not in text:
            raise SceneError("保存した JSON に quotes がありません")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        p.goto(BASE)
        p.wait_text("Backfull", 60)
        p.hide_chrome()
        p.idle()
        p.tab("5 判断と保存")
        doc = p.send("DOM.getDocument", depth=-1)
        node = p.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="input[type=file]")
        if not node.get("nodeId"):
            raise SceneError("ファイル入力欄が見つかりません")
        p.send("DOM.setFileInputFiles", files=[str(tmp)], nodeId=node["nodeId"])
        p.wait_text("case.json", 15)
        p.button("この内容を読み込む")
        p.wait_text("読み込みました", 15)
        p.idle()
        p.need("読み込みました", "見積回答 3 件")
        p.need_field("選んだ理由", REASON)
        p.scroll_to_text("読み込みました")
        time.sleep(1.0)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return "2026-11-03_S5_restored.jpg"


def scene_request_contacts(p: Page) -> str:
    fresh(p, "S1")
    p.tab("3 見積依頼文")
    p.need("不足項目", "運送会社に確かめること", "窓口は各社が公表している頁です", timeout=60)
    p.scroll_to_text("窓口は各社が公表している頁です", block="end")
    return "2026-11-03_request_contacts.jpg"


def map_open(p: Page) -> None:
    """地図タブ（最初のタブ）を開き、OSM のタイルと塗り分けが描かれるまで待つ。"""
    p.goto(BASE)
    p.wait_text("Backfull", 60)
    p.hide_chrome()
    p.idle()
    p.need("© OpenStreetMap contributors", "国土数値情報（行政区域データ）（国土交通省）を加工して作成", timeout=60)
    p.wait(lambda: p.js("!!document.querySelector('[data-testid=\"stDeckGlJsonChart\"] canvas')"), 60, "地図の描画")
    time.sleep(6.0)  # 背景タイルの読み込みを待つ


def map_scroll_top(p: Page, offset: int = 24) -> None:
    """地図の上端が画面の上から offset の位置に来るようにスクロールする。"""
    p.js(f"""(function(){{const e=document.querySelector('[data-testid="stDeckGlJsonChart"]');
      e.scrollIntoView({{block:'start'}});
      const m=document.querySelector('[data-testid="stMain"]'); if (m) m.scrollTop -= {offset};
      return true;}})()""")
    time.sleep(1.5)


def scene_map_distance(p: Page) -> str:
    map_open(p)
    p.need("道路距離（km）", "7階級")
    map_scroll_top(p, 60)
    return "2026-11-03_map_distance.jpg"


def scene_map_selected_osaka(p: Page) -> str:
    map_open(p)
    p.select("都道府県", "大阪府")
    p.select("市区町村", "大阪市中央区")
    p.click("text", "貸切の目安額（円")
    p.idle()
    p.need("東京都新宿区 → 大阪府 大阪市中央区", "491.8km", timeout=30)
    time.sleep(14.0)  # 塗り直しとタイルの読み込みを待つ
    map_scroll_top(p, 20)
    return "2026-11-03_map_selected_osaka.jpg"


SCENES = {
    "map_distance": scene_map_distance,
    "map_selected_osaka": scene_map_selected_osaka,
    "S1_estimates": scene_s1_estimates,
    "S2_estimates_chart": scene_s2_estimates_chart,
    "S5_quotes_demo_partial": scene_s5_quotes_demo_partial,
    "S5_quotes_ranked_breakdown": scene_s5_quotes_ranked_breakdown,
    "S5_quotes_history": scene_s5_quotes_history,
    "S5_judgment_notice": scene_s5_judgment_notice,
    "S5_restored": scene_s5_restored,
    "request_contacts": scene_request_contacts,
}


# ------------------------------------------------------------------ 実行

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", action="append", choices=list(SCENES), help="撮る場面（複数指定可）")
    ap.add_argument("--all", action="store_true", help="すべての場面を撮る")
    ap.add_argument("--list", action="store_true", help="場面の一覧を出す")
    args = ap.parse_args()
    if args.list:
        print("\n".join(SCENES))
        return 0
    names = list(SCENES) if args.all else (args.scene or [])
    if not names:
        ap.error("--scene NAME か --all を指定してください")

    global PORT, BASE
    with socket.socket() as probe:  # 8502 を別のサーバーが使っていたら、空いているポートに替える
        if probe.connect_ex(("127.0.0.1", DEFAULT_PORT)) == 0:
            PORT = free_port()
            print(f"ポート {DEFAULT_PORT} は使用中のため {PORT} で起動します")
    BASE = f"http://localhost:{PORT}"
    edge = find_edge()
    profile = tempfile.mkdtemp(prefix="edge_profile_")
    st_proc = subprocess.Popen(
        ["uv", "run", "streamlit", "run", "app/main.py", "--server.headless", "true",
         "--server.port", str(PORT), "--client.toolbarMode", "minimal", "--server.fileWatcherType", "none", "--browser.gatherUsageStats", "false"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    edge_proc = None
    results: dict[str, str] = {}
    try:
        wait_health()
        dbg = free_port()
        edge_proc = subprocess.Popen(
            [edge, "--headless=new", f"--remote-debugging-port={dbg}", f"--window-size={W},{H}",
             f"--user-data-dir={profile}", "--hide-scrollbars", "--force-device-scale-factor=1",
             "--no-first-run", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        targets = []
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{dbg}/json/list", timeout=2) as r:
                    targets = [t for t in json.load(r) if t.get("type") == "page"]
                if targets:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not targets:
            raise SystemExit("Edge のページに接続できませんでした。")
        page = Page(targets[0]["webSocketDebuggerUrl"])
        for n in names:
            print(f"--- {n}")
            try:
                out = SCENES[n](page)
                page.shot(out)
                results[n] = "成功"
            except SceneError as e:
                results[n] = f"失敗: {e}"
            except Exception as e:
                results[n] = f"失敗(想定外): {type(e).__name__}: {e}"
            if results[n] != "成功":
                print(results[n])
                try:  # 失敗時の画面を一時フォルダに残す（docs/assets には書かない）
                    from PIL import Image
                    r = page.send("Page.captureScreenshot", format="png")
                    dbgpath = Path(tempfile.gettempdir()) / f"capture_fail_{n}.png"
                    Image.open(io.BytesIO(base64.b64decode(r["data"]))).save(dbgpath)
                    print(f"失敗時の画面: {dbgpath}")
                except Exception:
                    pass
    finally:
        if edge_proc is not None:
            stop_tree(edge_proc)
        stop_tree(st_proc)
        shutil.rmtree(profile, ignore_errors=True)
    print("=== 結果")
    for n, r in results.items():
        print(f"{n}: {r}")
    return 0 if all(r == "成功" for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
