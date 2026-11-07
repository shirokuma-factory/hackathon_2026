"""takes_r4.json から、鹿児島版（末尾 g）のテイクを作って takes_r5.json に書く。"""
import copy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
src = {t["id"]: t for t in json.loads((HERE / "takes_r4.json").read_text(encoding="utf-8"))}
out = []


def add(base, new_id, **kw):
    t = copy.deepcopy(src[base])
    t["id"] = new_id
    t.update(kw)
    out.append(t)
    return t


# 全国の流れ（実データ）。鹿児島県→東京都は距離が 967 km。区間の一覧は距離の下限 800 km なら 15 番目に出る（下限 200 km だと 184 番目で出ない）
MKT = "/index.html#v=market&km=800"
KEY = '#m-tbody tr[data-k="45-12"]'
for base, nid in (("R1", "R1g"), ("R2", "R2g")):
    t = add(base, nid, url_path=MKT)
    t["steps"] = [{"click": KEY} if s.get("click") == '#m-tbody tr[data-k="27-12"]' else s for s in t["steps"]]
    assert any(s.get("click") == KEY for s in t["steps"])
def own_wording(t):
    """今の画面の文言（本線の変更で「復路の候補 53 便」から「帰りの便 53 本」になった）に合わせる"""
    t["steps"] = json.loads(json.dumps(t["steps"], ensure_ascii=False).replace("復路の候補 53 便", "帰りの便 53 本"))
    if "ready_js" in t:
        t["ready_js"] = t["ready_js"].replace("復路の候補 53 便", "帰りの便 53 本")
    return t


own_wording(add("R3", "R3g"))
t4 = own_wording(add("R4", "R4g"))
# 荷待ちの画面の表の並びが変わったため、「待ち時間の長い場所と時間帯」の表（#hotspots）を印の対象にする
old_ev = [s_ for s_ in t4["steps"] if "rec-hot" in json.dumps(s_, ensure_ascii=False) and "eval" in s_]
assert len(old_ev) == 1
old_ev[0]["eval"] = "(function(){const t=document.querySelector('#hotspots'); if(t) t.parentElement.id='rec-hot';})()"
add("R4_3x", "R4_3xg")

ready = src["R5a"]["ready_js"]
add("R5a", "R5ag", url_path="/index.html#v=compare&o=13104&l=fare&d=46201", ready_js=ready)

# 比べるページ: 届け先を鹿児島県、距離は地図の画面の値（新宿区→鹿児島市 1,345.5 km）
lite_ready = ("(function(){const r=document.querySelector('table.res'); if(!r) return false; const s=window.__lk=window.__lk||{n:0,t:0}; const now=Date.now();"
              "if(s.n===0){const t=document.querySelector('#to'); t.value='鹿児島県'; t.dispatchEvent(new Event('change',{bubbles:true}));"
              "const k=document.querySelector('#km'); k.value='1345.5'; k.dispatchEvent(new Event('input',{bubbles:true})); k.dispatchEvent(new Event('change',{bubbles:true})); s.n=1; s.t=now; return false;}"
              "return now-s.t>1500 && /1,?34[56]/.test(document.querySelector('tr[data-opt=kashikiri_2t]').innerText); })()")
add("R5b", "R5bg", ready_js=lite_ready)

# 探す画面の荷は既定だと場面 S1（120 kg）なので、比べる画面で S6（陳列棚 400 kg）に確定してから探す画面へ移る
find_ready = ready.replace("return now-s.t>=4000 && !!document.querySelector('.cc-row'); })()",
    "if(s.n===5){ if(now-s.t<4000 || !document.querySelector('.cc-row')) return false; App.setView('find'); s.n=6; s.t=now; return false; } "
    "return now-s.t>=3000 && !!document.querySelector('#f-list .f-card.ok'); })()")
assert "App.setView('find')" in find_ready
add("R5c", "R5cg", url_path="/index.html#v=compare&o=13104&l=fare&d=46201", ready_js=find_ready)

with open(HERE / "takes_r5.json", "w", encoding="utf-8", newline="\n") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
    fh.write("\n")
print([t["id"] for t in out])
