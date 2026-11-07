"""git の全履歴（全ブランチ）の追加行から、秘密情報・個人の情報・PC 固有のパスの形を探す。

使い方: uv run python tools/submission/scan_history.py [出力の json]
機械で判定できる確認なので、モデルに渡さずにこれで行う。値そのものは先頭 60 文字までしか出さない。
"""
import re, subprocess, sys, collections, json
repo = str(__import__("pathlib").Path(__file__).resolve().parents[2])
out_path = sys.argv[1] if len(sys.argv) > 1 else "outputs/scan_history.json"
pats = {
 "anthropic_key": r"sk-ant-[A-Za-z0-9_-]{20,}",
 "openai_key": r"sk-(proj-)?[A-Za-z0-9]{32,}",
 "elevenlabs_key": r"\bsk_[a-f0-9]{40,}\b",
 "google_api_key": r"AIza[0-9A-Za-z_-]{35}",
 "gcp_sa_private_key": r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE" + r" KEY-----",  # 自分に当たらないよう分けて書く
 "github_token": r"\b(ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}",
 "aws_key": r"\bAKIA[0-9A-Z]{16}\b",
 "slack": r"xox[baprs]-[A-Za-z0-9-]{10,}",
 "generic_assign": r"(?i)(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]",
 "bearer": r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._-]{20,}",
 "win_userpath": r"(?i)C:[\/]+Users[\/]+tatsu",
 "email": r"[A-Za-z0-9._%+-]+@(gmail|yahoo|outlook|hotmail|icloud)\.[a-z.]+",
 "phone_jp": r"\b0[789]0-?\d{4}-?\d{4}\b",
}
cre = {k: re.compile(v) for k, v in pats.items()}
p = subprocess.run(["git","-C",repo,"log","--all","-p","--no-color","--no-ext-diff","--text","--format=@@C %H %ad","--date=short"],capture_output=True)
txt = p.stdout.decode("utf-8","replace")
hits = collections.defaultdict(list); commit=None; fname=None
for line in txt.splitlines():
    if line.startswith("@@C "): commit=line[4:16]; continue
    if line.startswith("+++ b/"): fname=line[6:]; continue
    if not line.startswith("+") or line.startswith("+++"): continue
    for k, r in cre.items():
        if k == "phone_jp" and fname and fname.endswith((".geojson", ".json")):
            continue  # 座標の数字の並びを電話番号と誤検出するため
        for m in r.finditer(line):
            hits[k].append((commit, fname, m.group(0)[:60]))
summary = {}
for k, v in hits.items():
    files = collections.Counter(f for _, f, _ in v)
    vals = collections.Counter(s for _, _, s in v)
    summary[k] = {"count": len(v), "files": dict(files.most_common(12)), "values": dict(vals.most_common(8))}
# files ever added
names = subprocess.run(["git","-C",repo,"log","--all","--diff-filter=A","--name-only","--format="],capture_output=True).stdout.decode("utf-8","replace").split("\n")
sens = [n for n in set(names) if re.search(r"(^|/)\.env($|\.)|\.pem$|\.key$|\.p12$|id_rsa|credentials|service[-_]?account|\.sqlite$|\.db$", n, re.I)]
summary["sensitive_filenames_ever_added"] = sorted(sens)
# big blobs
objs = subprocess.run(["git","-C",repo,"rev-list","--objects","--all"],capture_output=True).stdout.decode("utf-8","replace")
b = subprocess.run(["git","-C",repo,"cat-file","--batch-check=%(objecttype) %(objectname) %(objectsize) %(rest)"],input=objs.encode(),capture_output=True).stdout.decode("utf-8","replace")
big = sorted([l.split(" ",3) for l in b.splitlines() if l.startswith("blob")], key=lambda x:-int(x[2]))[:15]
summary["largest_blobs_MB"] = [(round(int(x[2])/1e6,1), x[3] if len(x)>3 else "") for x in big]
json.dump(summary, open(out_path,"w",encoding="utf-8",newline="\n"), ensure_ascii=False, indent=1)
print(json.dumps(summary, ensure_ascii=False, indent=1)[:9000])
