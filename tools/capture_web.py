"""web/ の地図 UI を、入っている Microsoft Edge（ヘッドレス）で撮る。

標準ライブラリ + websockets + Pillow だけを使う（tools/capture_screens.py の Page を再利用する）。
配信は tools/serve_web.py（API つき）を自分で起動する。
使い方:
    uv run python tools/capture_web.py --scene overview
    uv run python tools/capture_web.py --scene selected --size 1280x720 --out tmp/web_1280.jpg
    uv run python tools/capture_web.py --all
    uv run python tools/capture_web.py --flow     # S5 の筋書きを本物のクリックと入力でたどり、5枚を docs/assets に保存する
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_screens as cs  # noqa: E402

ROOT = cs.ROOT
OUT_DIR = ROOT / "docs" / "assets"

ERR_HOOK = """
window.__errs = [];
window.addEventListener('error', e => window.__errs.push(String(e.message)));
window.addEventListener('unhandledrejection', e => window.__errs.push('rejection: ' + String(e.reason)));
const _ce = console.error; console.error = function(){ window.__errs.push([...arguments].map(String).join(' ')); _ce.apply(console, arguments); };
"""

# 場面: (URL のハッシュ, 待ち秒, 撮る前の JS)
SCENES = {
    "overview": ("#o=13104&l=km", 7.0, None, "2026-11-03_web_map_overview.jpg"),
    "fare": ("#o=13104&l=fare", 6.0, None, "2026-11-03_web_map_fare.jpg"),
    "selected": ("#o=13104&l=fare&d=27128", 9.0, "__app.S.t = __app.S.route.minutes * 0.42; document.querySelector('#track').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight'}))", "2026-11-03_web_map_selected.jpg"),
    "selected_far": ("#o=09201&l=days&d=01101", 9.0, None, "2026-11-03_web_map_selected_far.jpg"),
    "search": ("#o=13104&l=km", 6.0, "(function(){const q=document.querySelector('#q'); q.focus(); q.value='大阪'; q.dispatchEvent(new Event('input'));})()", "2026-11-03_web_map_search.jpg"),
    "ferry": ("#o=13104&l=days&d=13421", 9.0, None, "2026-11-03_web_map_ferry.jpg"),
    "selected_tohoku": ("#o=13104&l=km&d=04101", 9.0, None, "2026-11-03_web_map_selected_tohoku.jpg"),
    # 版3の3段目（SPEC §16）。市場を見る（全国）、兵庫→東京を選んだ画面（JARTIC の折れ線つき）、拠点の層と鉄道カードの点線
    "market_overview": ("#v=market", 9.0, None, "2026-11-03_market_overview.jpg"),
    "market_pair": ("#v=market&p=27-12", 11.0,
                    "(function(){const s=document.querySelector('#ch'); if(!s) return; const b=s.getBoundingClientRect();"
                    "s.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:b.left+b.width*0.43,clientY:b.top+b.height*0.5}));})()",
                    "2026-11-03_market_pair.jpg"),
    # 市場を見るで兵庫→東京を選んでから「この区間の空き便を探す」を押した画面（逆向きの便を強調）
    "b_from_market": ("#v=market&p=27-12", 10.0,
                      "(function(){const b=document.querySelector('#m-pair [data-act=\"find\"]'); if(b) b.click();})()",
                      "2026-11-03_b_from_market.jpg"),
    "hubs": ("#v=compare&o=13104&l=km&d=27128&hubs=1", 9.0,
             "(function(){const b=document.querySelector('#cards .hub-btn[data-hub=\"rail\"]'); if(b){b.scrollIntoView({block:'center'}); b.click();}})()",
             "2026-11-03_hubs.jpg"),
    # 版3の実際の線の形（SPEC §17.3 G43）。新宿区 → 福岡市中央区で 4 手段を同時に、飛行機を立体で、電車を線路つきで
    "modes_all": ("#v=compare&o=13104&l=km&d=40133", 16.0, None, "2026-11-04_modes_all.jpg"),
    "modes_plane_3d": ("#v=compare&o=13104&l=km&d=40133", 12.0,
                       "__app.Rt.setFocus('air')", "2026-11-04_modes_plane_3d.jpg"),
    "modes_rail": ("#v=compare&o=13104&l=km&d=40133", 12.0,
                   "__app.Rt.setFocus('rail')", "2026-11-04_modes_rail.jpg"),
    "market_real": ("#v=market&p=27-12", 12.0, None, "2026-11-04_market_real.jpg"),
    "b_real": ("#v=find", 13.6, None, "2026-11-04_b_real.jpg"),
    # 版3の4段目（SPEC §18、G45・G47）。市区町村（推定）、物流の集積の 3D の柱、施設（P31 は手元にあるときだけ）、拡大 11 の倉庫の点、狭い画面
    "muni_estimate": ("#v=market&sc=muni", 8.0,
                      "(function(){const a=__app; a.map().jumpTo({center:[139.85,35.85],zoom:7.7}); a.Muni.select(a.S.idx.codes.indexOf('12219'));})()",
                      "2026-11-04_muni_estimate.jpg"),
    "concentration_3d": ("#v=market&cc=dens&c3=1", 8.0,
                         "(function(){__app.map().jumpTo({center:[139.6,35.3],zoom:7.4,pitch:55});})()", "2026-11-04_concentration_3d.jpg"),
    "facilities": ("#v=market&fc=1", 9.0, "(function(){__app.map().jumpTo({center:[139.75,35.6],zoom:8.6});})()", "2026-11-04_facilities.jpg"),
    "warehouses_z11": ("#v=market&fc=1", 9.0, "(function(){__app.map().jumpTo({center:[139.82,35.72],zoom:11.3});})()", "2026-11-04_warehouses_z11.jpg"),
    "narrow_800": ("#v=market&sc=muni", 8.0, None, "2026-11-04_narrow_800.jpg"),
}


def serve(port: int) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(ROOT / "tools" / "serve_web.py"), "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- S5 の筋書き（SPEC §15.4 G29）
def _center(page, sel: str):
    return page.js("(function(){const e=document.querySelector(%s); if(!e) return null; e.scrollIntoView({block:'center'});"
                   "const r=e.getBoundingClientRect(); return [r.x+r.width/2, r.y+r.height/2];})()" % json.dumps(sel))


def click(page, sel: str, wait: float = 0.6) -> None:
    page.wait(lambda: _center(page, sel) is not None, 20, "要素 " + sel)
    time.sleep(0.25)
    x, y = _center(page, sel)
    page.mouse(x, y)
    time.sleep(wait)


def type_into(page, sel: str, text: str) -> None:
    click(page, sel, 0.2)
    page.key("a", ctrl=True)
    page.insert(text)
    time.sleep(0.2)


def set_select(page, sel: str, value: str) -> None:
    """ネイティブの選択欄はヘッドレスで開けないので、値を入れて change を送る。"""
    page.js("(function(){const e=document.querySelector(%s); e.value=%s; e.dispatchEvent(new Event('change',{bubbles:true}));})()"
            % (json.dumps(sel), json.dumps(value)))
    time.sleep(0.5)


def flow_steps(page, base: str, out_dir: Path, dl_dir: Path):
    """各段の後で (ファイル名) を yield する。呼び出し側が撮る。"""
    page.send("Page.setDownloadBehavior", behavior="allow", downloadPath=str(dl_dir))
    page.goto("about:blank")
    page.goto(base + "#o=13104&l=fare&d=27128")
    page.wait(lambda: page.js("!!window.__app"), 60, "アプリの起動")
    time.sleep(8.0)
    page.wait(lambda: page.js("document.querySelectorAll('#cards .card').length >= 5"), 30, "右のカード")
    # 1 荷物の欄を開いて場面 S5 を読み込み、確定する
    click(page, "#cargo-toggle")
    set_select(page, "#sc-select", "S5")
    click(page, "#ed-confirm", 2.5)
    page.wait(lambda: "8個" in (page.js("document.querySelector('#cargo').textContent") or ""), 20, "荷物の更新")
    time.sleep(2.0)
    yield "2026-11-03_web_cargo.jpg"
    # 2 着地のパネルのボタンで作業パネルを開く（依頼文）
    click(page, "#cargo-toggle")
    click(page, "#make-request", 2.0)
    page.wait(lambda: page.js("!!document.querySelector('#req-text')"), 20, "依頼文")
    time.sleep(1.2)
    yield "2026-11-03_web_request.jpg"
    # 3 見積回答: 架空の回答例を入れる
    click(page, '#w-steps button[data-step="2"]')
    click(page, "#q-demo", 1.5)
    page.wait(lambda: page.js("document.querySelectorAll('#q-list .q-card').length == 2"), 20, "架空の回答2件")
    time.sleep(0.8)
    yield "2026-11-03_web_quotes.jpg"
    # 4 A社の追加回答（集荷 6,000・幹線 18,000・配達 5,000・ほかは含む）を手で入れる
    type_into(page, 'input[name="carrier"]', "架空A社")
    type_into(page, 'input[name="product"]', "路線便")
    for k, amt in (("pickup", "6000"), ("trunk", "18000"), ("delivery", "5000")):
        click(page, f'.cl[data-k="{k}"] button[data-kind="amount"]', 0.2)
        type_into(page, f'.cl[data-k="{k}"] input', amt)
    for k in ("handling", "fuel", "highway", "other"):
        click(page, f'.cl[data-k="{k}"] button[data-kind="included"]', 0.2)
    click(page, 'input[name="is_demo"]', 0.2)
    click(page, "#q-add", 1.5)
    page.wait(lambda: page.js("document.querySelectorAll('#q-list .q-card').length == 3"), 20, "追加後3件")
    click(page, "#w-next", 1.5)  # 比較へ
    page.wait(lambda: page.js("!!document.querySelector('.cmp-row')"), 20, "比較")
    time.sleep(4.0)  # ソフトウェア描画では地図の描き直しが重く、棒の伸びる動きが終わるまで待つ
    yield "2026-11-03_web_compare.jpg"
    # 5 判断と保存: B社を選び、理由と確認事項を入れて保存する
    click(page, "#w-next", 1.2)
    page.wait(lambda: page.js("!!document.querySelector('#j-save')"), 20, "判断と保存")
    page.js("(function(){const r=[...document.querySelectorAll('input[name=choice]')].find(x=>x.parentElement.textContent.includes('架空B社')); r.scrollIntoView({block:'center'});})()")
    click(page, 'label.opt:has(input[value="q2"])', 0.3)
    type_into(page, "#j-reason", "集荷から配達まで含み、総額が最も低いため。")
    type_into(page, "#j-issues", "受託の可否を電話で確かめる")
    page.key("Enter")
    page.insert("見積の有効期限（14日）内に発注先を決める")
    click(page, "#j-save", 1.5)
    page.wait(lambda: page.js("!!document.querySelector('.saved')"), 20, "保存の表示")
    time.sleep(0.8)
    yield "2026-11-03_web_save.jpg"
    # 6 確認（撮らない）: 閉じても入力が残ること、保存したファイルの読み込み
    click(page, "#w-close", 1.0)
    resume = page.js("document.querySelector('#work-resume').textContent")
    print("作業パネルを閉じた後:", resume, "/ 開いているか:", page.js("document.body.classList.contains('has-work')"))
    click(page, "#work-resume", 1.0)
    print("開き直した後の表示:", page.js("document.querySelector('#w-state').textContent"))
    saved = sorted(dl_dir.glob("shipment_case_*.json"))
    if saved:
        click(page, '#w-steps button[data-step="4"]', 1.0)
        doc = page.send("DOM.getDocument", depth=0)
        nid = page.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#j-load")["nodeId"]
        page.send("DOM.setFileInputFiles", files=[str(saved[-1])], nodeId=nid)
        time.sleep(2.0)
        print("読み込み後のトースト:", page.js("document.querySelector('#toast').textContent"))
        print("読み込み後の荷物欄:", page.js("document.querySelector('#cargo-ver').textContent + ' ' + document.querySelector('#cargo').textContent"))


def bflow_steps(page, base: str, out_dir: Path, dl_dir: Path):
    """空きを探す・つなぐ（SPEC §16.2、G39）の筋書き。S1 の荷物で照合 → 仮予約 → 運送会社の役 → 承諾。"""
    page.goto("about:blank")
    page.goto(base + "#v=find")
    page.wait(lambda: page.js("!!window.__app"), 60, "アプリの起動")
    page.wait(lambda: page.js("document.querySelectorAll('#f-list .f-card').length >= 6"), 30, "便の一覧")
    time.sleep(7.0)
    yield "2026-11-03_b_match.jpg"
    # 載る便（T6）を仮予約する
    click(page, '#f-list .f-card[data-t="T6"] [data-act="hold"]', 1.5)
    page.wait(lambda: page.js("!!document.querySelector('#f-list .f-hold.held')"), 20, "仮予約中の表示")
    time.sleep(3.0)
    yield "2026-11-03_b_hold.jpg"
    # 運送会社の役に切り替える（T6 は C2 の便）
    click(page, '#f-role button[data-role="carrier"]', 0.8)
    set_select(page, "#f-carrier-sel", "C2")
    page.wait(lambda: page.js("!!document.querySelector('#f-list [data-act=accept]')"), 20, "承諾のボタン")
    time.sleep(3.0)
    yield "2026-11-03_b_carrier.jpg"
    click(page, '#f-list [data-act="accept"]', 1.5)
    click(page, '#f-role button[data-role="shipper"]', 0.8)
    page.wait(lambda: page.js("!!document.querySelector('#f-list .f-hold.accepted')"), 20, "承諾の表示")
    time.sleep(3.0)
    yield "2026-11-03_b_accepted.jpg"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", action="append", choices=list(SCENES))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--flow", action="store_true", help="S5 の筋書きをたどって web_cargo / request / quotes / compare / save を撮る")
    ap.add_argument("--bflow", action="store_true", help="空きを探す・つなぐの筋書きをたどって b_match / b_hold / b_carrier / b_accepted を撮る")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--out", help="単一の場面のときの出力先（省略すると docs/assets）")
    ap.add_argument("--click", help="経度,緯度。その地点を本物のマウスでクリックしてから撮る")
    ap.add_argument("--port", type=int, default=0, help="既に起動している配信サーバーのポート（省略すると自分で起動）")
    args = ap.parse_args()
    names = list(SCENES) if args.all else ([] if (args.flow or args.bflow) else (args.scene or ["overview"]))
    w, h = (int(x) for x in args.size.split("x"))
    cs.W, cs.H = w, h

    own = None
    port = args.port
    if not port:
        port = cs.free_port()
        own = serve(port)
        time.sleep(1.2)
    base = f"http://localhost:{port}/index.html"
    edge = cs.find_edge()
    profile = tempfile.mkdtemp(prefix="edge_profile_")
    dbg = cs.free_port()
    edge_proc = subprocess.Popen(
        [edge, "--headless=new", f"--remote-debugging-port={dbg}", f"--window-size={w},{h}", f"--user-data-dir={profile}",
         "--hide-scrollbars", "--force-device-scale-factor=1", "--no-first-run", "--enable-webgl", "--ignore-gpu-blocklist",
         "--enable-unsafe-swiftshader", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rc = 0
    try:
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
        page = cs.Page(targets[0]["webSocketDebuggerUrl"])
        page.send("Page.addScriptToEvaluateOnNewDocument", source=ERR_HOOK)
        for n in names:
            hash_, wait, pre, fname = SCENES[n]
            page.goto("about:blank")  # ハッシュだけの移動だと再読み込みされないので、いったん空白にする
            page.goto(base + hash_)
            page.wait(lambda: page.js("!!window.__app"), 60, "アプリの起動")
            time.sleep(wait)
            if pre:
                page.js(pre)
                time.sleep(5.0 if n.startswith('modes') else 1.0)  # 地図の移動（約 2.4 秒）が終わるまで
            if n == "b_real":  # 架空の便の乗り物が線の上にいる時刻（find.js の周期 14 秒のうち 7〜9 秒）まで待つ
                for _ in range(60):
                    if 7.0 <= (page.js("(performance.now()/1000)%14") or 0) <= 9.0:
                        break
                    time.sleep(0.25)
            if args.click:
                lon, lat = (float(v) for v in args.click.split(","))
                x, y = page.js(f"(function(){{const p=__app.map().project([{lon},{lat}]);return [p.x,p.y];}})()")
                page.mouse(x, y)
                time.sleep(wait)
            errs = page.js("window.__errs") or []
            if errs:
                print("ブラウザのエラー:", *errs[:5], sep="\n  ")
                rc = 1
            r = page.send("Page.captureScreenshot", format="png", fromSurface=True,
                          clip={"x": 0, "y": 0, "width": w, "height": h, "scale": 1})
            from PIL import Image
            img = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")
            path = Path(args.out) if args.out and len(names) == 1 else OUT_DIR / fname
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(path, "JPEG", quality=88)
            print(f"保存: {path}  {img.size[0]}x{img.size[1]}")
        if args.flow or args.bflow:
            from PIL import Image
            dl = Path(tempfile.mkdtemp(prefix="edge_dl_"))
            try:
                for fname in (flow_steps if args.flow else bflow_steps)(page, base, OUT_DIR, dl):
                    errs = page.js("window.__errs") or []
                    if errs:
                        print("ブラウザのエラー:", *errs[:5], sep="\n  ")
                        rc = 1
                    r = page.send("Page.captureScreenshot", format="png", fromSurface=True,
                                  clip={"x": 0, "y": 0, "width": w, "height": h, "scale": 1})
                    img = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")
                    path = OUT_DIR / fname
                    img.save(path, "JPEG", quality=88)
                    print(f"保存: {path}  {img.size[0]}x{img.size[1]}")
                saved = sorted(dl.glob("shipment_case_*.json"))
                print("ダウンロードされた保存ファイル:", [f"{f.name} {f.stat().st_size}B" for f in saved])
            finally:
                shutil.rmtree(dl, ignore_errors=True)
    finally:
        cs.stop_tree(edge_proc)
        cs.stop_edge_profile(profile)
        if own:
            cs.stop_tree(own)
        shutil.rmtree(profile, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
