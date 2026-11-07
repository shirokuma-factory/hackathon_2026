"""機能紹介の追加場面（rec7）の台本 takes_r7.json を作る。鹿児島版のスナップショット（web_kago2、serve_kago2.py）で録る。
takes_r6.json と同じ形式。テイクごとに "dsf"（デバイス倍率、既定 2）を持てる（record_hd7.py が読む）。

 DRIVER    運転者の画面（スマートフォン 390x844、dsf 3）。5 回押して、LINE に貼る文を出す
 POST      自社の運行 → 求荷求車に貼る文（空車の帰りの曜日が元になる）と、明日の掲示文
 OWNVIZ    自社の運行の可視化（曜日ごとの空車、空車 km の棒、実車率、区間と曜日の表、拘束時間の気づき）
 NEGOTIATE 荷待ち協議の書面（相手ごとの額 → 書面の題 → 合計 → 国の標準的運賃の表に当てた根拠）
 NEGO2     帰り便の相談の下書き（自社の運行の気づきから）。NEGOTIATE の追加
 QUOTE     送り方を比べる → 見積を頼む文
 LIVE      いまの大型車（全国 → 東名・名神の帯へ寄る）
 MARKET    全国の物流傾向 → 帰りが空きやすい区間
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
takes_r5 = json.loads((HERE / "takes_r5.json").read_text(encoding="utf-8"))
r4g = next(t for t in takes_r5 if t["id"] == "R4g")
paste_text = next(s["paste"][1] for s in r4g["steps"] if "paste" in s)


def smooth(sel, off, ms):
    """sel の要素の上端が画面の上から off px になるよう、ms ミリ秒でなめらかにスクロールする。"""
    return ("(function(sel,off,ms){const e=document.querySelector(sel); if(!e) return false; const y0=scrollY, y1=Math.max(0,e.getBoundingClientRect().top+y0-off), t0=performance.now();"
            "function s(n){const t=Math.min((n-t0)/ms,1), k=t<.5?2*t*t:1-Math.pow(-2*t+2,2)/2; scrollTo(0,y0+(y1-y0)*k); if(t<1) requestAnimationFrame(s);} requestAnimationFrame(s); return true;})("
            + json.dumps(sel) + "," + str(off) + "," + str(ms) + ")")


def box(rect_js, sec, border=3):
    """rect_js（関数の式）が返す {l,t,w,h}（画面の座標）にオレンジ枠（highlight と同じ色）を sec 秒出す。"""
    return ("(function(){const r=(" + rect_js + ")(); if(!r) return false; const b=document.createElement('div');"
            "b.style.cssText='position:fixed;left:'+Math.round(r.l-6)+'px;top:'+Math.round(r.t-4)+'px;width:'+Math.round(r.w+12)+'px;height:'+Math.round(r.h+8)+'px;border:" + str(border)
            + "px solid #e8590c;border-radius:8px;box-sizing:border-box;pointer-events:none;z-index:2147483645;';"
            "document.documentElement.appendChild(b); setTimeout(()=>b.remove()," + str(int(sec * 1000)) + "); return true;})()")


def cur(sel, fx=0.9, fy=0.65, ms=700):
    """ポインタを sel の要素の中（右寄り）へ ms ミリ秒で動かす。待たずに次の手順へ進むので、枠の表示と同時に動く。"""
    return ("(function(sel,fx,fy,ms){const e=document.querySelector(sel), c=document.getElementById('__rec_cursor'); if(!e||!c||!window.__cur) return 0; const r=e.getBoundingClientRect();"
            "const x=r.left+r.width*fx, y=r.top+r.height*fy, cu=window.__cur, x0=cu.x, y0=cu.y, t0=performance.now(); const ease=t=>t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2;"
            "function st(n){const t=Math.min((n-t0)/ms,1), k=ease(t); cu.x=x0+(x-x0)*k; cu.y=y0+(y-y0)*k; c.style.transform='translate('+cu.x+'px,'+cu.y+'px)'; if(t<1) requestAnimationFrame(st);} requestAnimationFrame(st); return 1;})("
            + json.dumps(sel) + "," + str(fx) + "," + str(fy) + "," + str(ms) + ")")


# textarea の中の、start で始まる 1 行（折り返しを含む）の画面上の位置
TA_LINES = ("function(){const ta=document.querySelector(%s); if(!ta) return null; const cs=getComputedStyle(ta); const v=ta.value; const a=v.indexOf(%s); if(a<0) return null; let b=v.indexOf('\\n',a); if(b<0) b=v.length;"
            "const m=document.createElement('div'); m.style.cssText='position:absolute;left:-9999px;top:0;visibility:hidden;white-space:pre-wrap;overflow-wrap:break-word;box-sizing:content-box;padding:0;border:0;width:'+(ta.clientWidth-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight))+'px;font:'+cs.font+';letter-spacing:'+cs.letterSpacing+';line-height:'+cs.lineHeight+';';"
            "document.body.appendChild(m); const hh=(t)=>{m.textContent=t; return m.getBoundingClientRect().height;};"
            "const lh=parseFloat(cs.lineHeight)||hh('あ'); const top=hh(v.slice(0,a)+'あ')-lh; const bot=hh(v.slice(0,b)); m.remove();"
            "const r=ta.getBoundingClientRect(); return {l:r.left+parseFloat(cs.paddingLeft)-4, t:r.top+parseFloat(cs.borderTopWidth)+parseFloat(cs.paddingTop)+top-ta.scrollTop, w:ta.clientWidth-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight)+8, h:bot-top};}")


def ta_lines(sel, start):
    return TA_LINES % (json.dumps(sel), json.dumps(start))


MAP_READY = "!!window.__app && (function(){const l=document.querySelector('#loading'); return !l || l.classList.contains('done');})()"

OWN_READY = ("(function(){const w=window.__pre=window.__pre||{n:0}; if(w.n===0){w.n=1;(function(){const dt=new DataTransfer(); for(const f of SAMPLE_FILES) dt.items.add(new File([f.text],f.name,{type:'text/csv'}));"
             "const d=document.querySelector('#drop');d.dispatchEvent(new DragEvent('dragenter',{dataTransfer:dt,bubbles:true,cancelable:true}));d.dispatchEvent(new DragEvent('dragover',{dataTransfer:dt,bubbles:true,cancelable:true}));"
             "d.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));return 1})();return false;} const b=document.querySelector('[data-act=to-full]'); "
             "if(w.n===1){ if(!b) return false; w.n=2; b.click(); return false;} if(w.n===2){w.n=3; w.t=Date.now(); return false;} return document.body.innerText.includes('帰りの便 53 本') && Date.now()-w.t>1200;})()")

# 運転者の画面の準備。時計を止めて（押した時刻を決めた値にする）、場所・相手・車番を入れる
DRV_READY = ("(function(){const w=window.__pre=window.__pre||{n:0}; if(w.n===0){w.n=1; const R=Date; window.__R=R; window.__t=new R(2026,10,10,7,50).getTime();"
             "Date=class extends R{constructor(...a){if(a.length)super(...a);else super(window.__t);} static now(){return window.__t;}};"
             "const set=(id,v)=>{const e=document.querySelector('#'+id); e.value=v; e.dispatchEvent(new Event('input',{bubbles:true}));};"
             "set('place','鹿児島市 谷山倉庫'); set('partner','○○食品'); set('vno','1234'); set('vclass','大型'); set('date','11/10'); return false;} "
             "if(w.n===1){w.n=2; w.t=performance.now(); return false;} return !!document.querySelector('[data-stamp=arrive]') && performance.now()-w.t>1200;})()")


def set_time(h, m):
    return {"eval": f"window.__t=new window.__R(2026,10,10,{h},{m}).getTime()"}


def tap(key, h, m):
    return [set_time(h, m), {"click": f"[data-stamp={key}]"}]


tag_ins = {"eval": "(function(){const e=[...document.querySelectorAll('#sec-ins .ins')].find(x=>x.textContent.includes('拘束時間が長い日')); if(e) e.id='rec-ins-restraint';})()"}
TA_BOARD = 'textarea[aria-label$="求荷求車に貼る文"]'

takes = [
    dict(id="DRIVER", url_path="/driver.html", size="412x892", dsf=3, fast=True, cursor=True, ready_js=DRV_READY, steps=[
        {"wait": 0.5},
        *tap("arrive", 7, 50), *tap("reception", 8, 5), *tap("start", 9, 40), *tap("end", 10, 30), *tap("depart", 10, 40),
        {"eval": smooth("#b-copy", 420, 800)}, {"wait": 0.9},
        {"click": "#b-copy"}, {"wait": 0.4},
        {"highlight": ["#recs", 2.4]},
        {"highlight": ["#msg", 1.5]},
        {"wait": 0.4}]),
    dict(id="POST", url_path="/own_lite.html", size="1920x1080", cursor=True, ready_js=OWN_READY, steps=[
        {"eval": smooth("#sec-pairs", 120, 1)}, {"wait": 0.9},
        {"eval": cur("#pair-table-box tr.hot")}, {"highlight": ["#pair-table-box tr.hot", 2.4]},
        {"eval": smooth("#sec-actions h3", 120, 1)}, {"wait": 0.6},
        {"eval": cur(TA_BOARD)}, {"highlight": [TA_BOARD, 1.8]},
        {"eval": box(ta_lines(TA_BOARD, "曜日・時刻"), 2.4)}, {"wait": 2.6},
        {"eval": smooth("[data-act=tm-make]", 420, 1)}, {"wait": 0.6},
        {"click": "[data-act=tm-make]"}, {"wait": 0.5},
        {"eval": cur("#tm-out")}, {"highlight": ["#tm-out", 2.4]}, {"wait": 0.5}]),
    dict(id="OWNVIZ", url_path="/own_lite.html", size="1920x1080", cursor=True, ready_js=OWN_READY, steps=[
        tag_ins,
        {"eval": smooth("#neg-result", 150, 1)}, {"wait": 0.9},
        {"eval": cur("#neg-result .neg-sel")}, {"highlight": ["#neg-result .neg-sel", 2.2]},
        {"eval": smooth("#chart", 330, 1)}, {"wait": 0.6},
        {"eval": cur("#chart svg")}, {"highlight": ["#chart svg", 2.2]},
        {"eval": smooth("#sec-kpi .tile", 420, 1)}, {"wait": 0.6},
        {"eval": cur("#sec-kpi .tile[data-kpi=actual_rate]")}, {"highlight": ["#sec-kpi .tile[data-kpi=actual_rate]", 2.2]},
        {"eval": smooth("#pair-table-box tr.hot", 520, 1)}, {"wait": 0.6},
        {"eval": cur("#pair-table-box tr.hot")}, {"highlight": ["#pair-table-box tr.hot", 2.2]},
        {"eval": smooth("#rec-ins-restraint", 420, 1)}, {"wait": 0.6},
        {"eval": cur("#rec-ins-restraint")}, {"highlight": ["#rec-ins-restraint", 2.4]}, {"wait": 0.4}]),
    dict(id="NEGOTIATE", url_path="/waiting.html", size="1920x1080", cursor=True, ready_js=(
        "(function(){const w=window.__pre=window.__pre||{n:0}; if(w.n===0){w.n=1; const e=document.querySelector('#paste'); e.value=" + json.dumps(paste_text)
        + "; e.dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('#b-paste').click(); return false;}"
        "const h=document.querySelector('#wait-doc h3'); if(!h) return false; if(w.n===1){w.n=2; w.t=Date.now(); const p=document.querySelector('#partners'); scrollTo(0,p.getBoundingClientRect().top+scrollY-40); return false;}"
        "return Date.now()-w.t>1500;})()"), steps=[
        {"wait": 0.5},
        {"eval": cur("#partners table")}, {"highlight": ["#partners table", 3.0]},
        {"eval": smooth("#wait-doc h3", 110, 1)}, {"wait": 0.6},
        {"eval": cur("#wait-doc h3")}, {"highlight": ["#wait-doc h3", 2.2]},
        {"eval": smooth("#doc-main", 330, 1)}, {"wait": 0.6},
        {"eval": cur("#doc-main")}, {"highlight": ["#doc-main", 3.0]},
        {"eval": cur("#doc-main + p")}, {"highlight": ["#doc-main + p", 3.6]}, {"wait": 0.4}]),
    dict(id="NEGO2", url_path="/own_lite.html", size="1920x1080", cursor=True, ready_js=OWN_READY, steps=[
        {"eval": smooth("#neg-result", 150, 1)}, {"wait": 0.7},
        {"eval": cur("#neg-go-cond", 0.5, 0.5)}, {"wait": 0.8},
        {"click": "#neg-go-cond"}, {"wait": 0.8},
        {"eval": cur("#neg-root fieldset")}, {"highlight": ["#neg-root fieldset", 2.4]},
        {"click": "#neg-go-draft"}, {"wait": 0.8},
        {"eval": cur("#neg-text")}, {"highlight": ["#neg-text", 3.6]}, {"wait": 0.4}]),
    dict(id="QUOTE", url_path="/compare_lite.html", size="1920x1080", cursor=True, ready_js="!!document.querySelector('#b-example')", steps=[
        {"wait": 1.0},
        {"click": "#b-example"}, {"wait_for": "table.res"}, {"wait": 1.0},
        {"eval": smooth("tr[data-opt=kashikiri_2t]", 340, 1)}, {"wait": 0.6},
        {"eval": cur("tr[data-opt=kashikiri_2t]")}, {"highlight": ["tr[data-opt=kashikiri_2t]", 2.4]},
        {"eval": "document.querySelector('#quote').style.height='540px'"},
        {"eval": smooth("#quote", 250, 1)}, {"wait": 0.6},
        {"eval": cur("#quote")}, {"highlight": ["#quote", 3.8]}, {"wait": 0.4}]),
    dict(id="LIVE", url_path="/trucks_now.html", size="1920x1080", cursor=True, ready_js=(
        "(function(){const w=window.__pre=window.__pre||{n:0}; const T=window.__trucks; if(!T||!T.data||!T.map||!document.querySelector('#rows tr')) return false; "
        "if(w.n===0){w.n=1; w.t=Date.now(); return false;} return T.map.loaded() && Date.now()-w.t>6000;})()"), steps=[
        {"eval": "(function(){const e=document.querySelector('#n-avg').parentElement; e.id='rec-avg';})()"},
        {"wait": 1.0},
        {"move": "#rec-avg"}, {"highlight": ["#rec-avg", 1.8]},
        {"move": "#legend"}, {"highlight": ["#legend", 1.8]},
        {"eval": "window.__trucks.map.flyTo({center:[138.0,35.1],zoom:6.7,duration:2800,essential:true}); 0"}, {"wait": 3.3},
        {"eval": box("function(){const m=window.__trucks.map; const a=m.project([139.9,35.9]), b=m.project([136.2,34.6]); const c=m.getContainer().getBoundingClientRect(); return {l:c.left+Math.min(a.x,b.x), t:c.top+Math.min(a.y,b.y)-20, w:Math.abs(a.x-b.x), h:Math.abs(a.y-b.y)+40};}", 2.6)}, {"wait": 2.8}]),
    dict(id="MARKET", url_path="/index.html#v=market&km=800", size="1920x1080", cursor=True, ready_js=MAP_READY, steps=[
        {"wait": 1.8},
        {"move": "#m-right header"}, {"highlight": ["#m-right header", 1.3]},
        {"click": "#m-rfold"}, {"wait": 1.0},
        {"highlight": ["#m-tbody tr:nth-child(2)", 1.3]},
        {"highlight": ["#m-tbody tr:nth-child(3)", 1.3]},
        {"click": "#m-tbody tr[data-k=\"45-12\"]"}, {"wait": 1.8},
        {"highlight": ["#m-pair .mp-stats", 2.4]}, {"wait": 0.6}]),
]
out = HERE / "takes_r7.json"
with open(out, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(takes, fh, ensure_ascii=False, indent=1)
    fh.write("\n")
print([t["id"] for t in takes])
