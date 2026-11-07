"""takes_r5.json（鹿児島版 r5 の台本）から、高解像度録画（rec6）の台本 takes_r6.json を作る。

 - R1g 〜 R5cg: r5 と同じ内容（そのまま）。運送会社名は web_kago2 側で「架空 さつま運輸」に直してある
 - FLOW0a〜d: 使い始め方の流れ（最初の画面 → 運送会社を選ぶ / 自社の運行 → ファイルを入れる / 荷待ち → 貼る → 書面 / 送り方を比べる → 条件 / 空きを探す）。
   読み込み画面を映さないため、地図の重い画面ごとに別テイクにして、あとで concat_flow0.py で 1 本につなぐ
 - DOC: 協議の書面の下書きを、題から参考額の合計までゆっくりスクロールして見せる
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
takes = json.loads((HERE / "takes_r5.json").read_text(encoding="utf-8"))
ids = {t["id"] for t in takes}
r4g = next(t for t in takes if t["id"] == "R4g")
paste_text = next(s["paste"][1] for s in r4g["steps"] if "paste" in s)

HEAVY_READY = ("(function(){const s=window.__rd=window.__rd||{t:Date.now()}; const e=%s; "
               "return !!(e&&e.offsetParent) && !document.body.innerText.includes('描画の準備') && Date.now()-s.t>%d;})()")


def ready(sel, ms):
    return HEAVY_READY % (f"document.querySelector({json.dumps(sel)})", ms)


def tag(sel_js, ident):
    return {"eval": f"(function(){{const e={sel_js}; if(e) e.id={json.dumps(ident)};}})()"}


smooth = ("(function(dy,ms){const y0=scrollY,t0=performance.now();function s(n){const t=Math.min((n-t0)/ms,1),"
          "e=t<.5?2*t*t:1-Math.pow(-2*t+2,2)/2;scrollTo(0,y0+dy*e);if(t<1)requestAnimationFrame(s)}requestAnimationFrame(s)})(%s,%s)")

new = [
    dict(id="FLOW0a", url_path="/index.html", size="1920x1080", cursor=True, ready_js=ready("#role-carrier", 5000), steps=[
        {"wait": 1.8}, {"move": "#role-shipper"}, {"wait": 0.4}, {"move": "#role-carrier"}, {"wait": 0.2},
        {"click": "#role-carrier"}, {"wait": 1.2}, {"move": "#p-car-empty"}, {"highlight": ["#p-car-empty", 1.0]}, {"wait": 0.2}]),
    dict(id="FLOW0b", url_path="/index.html#v=own&g=empty", size="1920x1080", cursor=True, ready_js=ready("#neg-entry-sample", 3000), steps=[
        {"wait": 0.5}, {"click": "#neg-entry-file"}, {"wait": 0.3}, {"highlight": ["#neg-entry-file", 1.0]},
        {"click": "#neg-entry-sample"}, {"wait": 2.4}]),
    dict(id="FLOW0c", url_path="/waiting.html#g", size="1920x1080", cursor=True, ready_js="!!document.querySelector('#g-home')", steps=[
        {"wait": 0.8}, tag("[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Excel の表を貼る'))", "rec-xl"),
        {"click": "#rec-xl"}, {"wait": 0.4}, {"paste": ["#paste", paste_text]}, {"wait": 0.3}, {"click": "#b-paste"}, {"wait": 1.0},
        {"highlight": ["#partners table", 1.7]},
        {"scroll": [None, 1380]}, {"wait": 0.3}, {"highlight": ["#doc-main", 1.8]}]),
    dict(id="FLOW0d", url_path="/index.html#v=compare&g=1", size="1920x1080", cursor=True, ready_js=ready("#q", 3000), steps=[
        {"wait": 0.8}, {"type": ["#q", "鹿児島"]}, {"wait": 0.4}, tag("document.querySelector('li.on')", "rec-sug1"),
        {"click": "#rec-sug1"}, {"wait": 2.2}, {"click": "#g-toggle"}, {"wait": 0.3}, {"click": "[data-view=find]"},
        {"wait_for": "#f-list .f-card.ok"}, {"wait": 0.6}, {"highlight": ["#f-list .f-card.ok", 2.2]}, {"wait": 0.3}]),
    dict(id="DOC", url_path="/waiting.html", size="1920x1080", cursor=True, ready_js=(
        "(function(){const w=window.__pre=window.__pre||{n:0}; if(w.n===0){w.n=1; const e=document.querySelector('#paste'); e.value=" + json.dumps(paste_text)
        + "; e.dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('#b-paste').click(); return false;}"
        "const h=document.querySelector('#wait-doc h3'); if(!h) return false; if(w.n===1){w.n=2; w.t=Date.now(); scrollTo(0,h.getBoundingClientRect().top+scrollY-100); return false;}"
        "return Date.now()-w.t>1500;})()"), steps=[
        {"wait": 0.8}, {"highlight": ["#wait-doc h3", 2.6]}, {"eval": smooth % (330, 3200)}, {"wait": 3.4},
        {"eval": smooth % (330, 3200)}, {"wait": 3.4}, {"highlight": ["#doc-main", 3.2]}, {"wait": 0.6}]),
]
assert not ({t["id"] for t in new} & ids)
out = takes + new
with open(HERE / "takes_r6.json", "w", encoding="utf-8", newline="\n") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
    fh.write("\n")
print([t["id"] for t in out])
