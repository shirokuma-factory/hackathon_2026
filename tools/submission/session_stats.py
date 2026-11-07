"""手元の Claude Code のセッション記録（~/.claude/projects/<このリポジトリ>/*.jsonl とサブエージェントの記録）から、
道具・サブエージェント・スキル・MCP・モデルの使用回数を数える。docs/CLAUDE_USAGE.md の回数の出典。

使い方: uv run python tools/submission/session_stats.py [出力の json]
記録の本文は外に出さず、回数だけを出す。
"""
import json, sys, collections, pathlib
root = pathlib.Path.home()/".claude/projects"/str(pathlib.Path(__file__).resolve().parents[2]).replace(":", "-").replace("\\", "-").replace("/", "-").replace("_", "-")
out = {}
for f in sorted(root.glob("*.jsonl")):
    tools = collections.Counter(); agents = collections.Counter(); models = collections.Counter()
    skills = collections.Counter(); users = 0; first=last=None; compacts=0; title=None
    with open(f, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try: d = json.loads(line)
            except Exception: continue
            ts = d.get("timestamp")
            if ts: first = first or ts; last = ts
            if d.get("type")=="summary": title = d.get("summary")
            if d.get("isCompactSummary"): compacts+=1
            m = d.get("message") or {}
            if d.get("type")=="assistant":
                if m.get("model"): models[m["model"]]+=1
                for c in m.get("content") or []:
                    if isinstance(c, dict) and c.get("type")=="tool_use":
                        n=c["name"]; tools[n]+=1
                        inp=c.get("input") or {}
                        if n in("Agent","Task"): agents[f'{inp.get("subagent_type","-")}/{inp.get("model","inherit")}']+=1
                        if n=="Skill": skills[inp.get("skill")]+=1
            if d.get("type")=="user" and isinstance(m.get("content"), str): users+=1
    sub = root/f.stem/"subagents"
    nsub = len(list(sub.glob("*.jsonl"))) if sub.exists() else 0
    out[f.stem]=dict(first=first,last=last,title=title,user_msgs=users,compacts=compacts,subagent_files=nsub,
        models=dict(models),tools=dict(tools.most_common()),agents=dict(agents),skills=dict(skills))
json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else "outputs/session_stats.json","w",encoding="utf-8",newline="\n"), ensure_ascii=False, indent=1)
for k,v in out.items():
    mcp = {t:n for t,n in v["tools"].items() if t.startswith("mcp__")}
    mcp_srv = collections.Counter()
    for t,n in mcp.items(): mcp_srv[t.split("__")[1]]+=n
    print(k[:8], v["first"][:16], v["last"][:16], "user", v["user_msgs"], "compact", v["compacts"], "subfiles", v["subagent_files"])
    print("  models", v["models"]); print("  agents", v["agents"]); print("  skills", v["skills"]); print("  mcp", dict(mcp_srv))
    print("  top", {t:n for t,n in list(v["tools"].items())[:14] if not t.startswith("mcp__")})

# 合計と、サブエージェントの応答のモデルごとの割合（CLAUDE_USAGE の「応答の 81% が Sonnet」の出典）
tot = collections.Counter(); agents_tot = collections.Counter(); skills_tot = collections.Counter(); users_tot = 0; compacts_tot = 0
for v in out.values():
    tot.update(v["tools"]); agents_tot.update(v["agents"]); skills_tot.update({k: n for k, n in v["skills"].items() if k})
    users_tot += v["user_msgs"]; compacts_tot += v["compacts"]
sub_models = collections.Counter()
for sf in root.glob("*/subagents/*.jsonl"):
    with open(sf, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try: d = json.loads(line)
            except Exception: continue
            m = d.get("message") if isinstance(d.get("message"), dict) else {}
            if d.get("type") == "assistant" and m.get("model"): sub_models[m["model"]] += 1
n_sub = sum(sub_models.values()) or 1
print("合計: 作成者の発言", users_tot, "圧縮", compacts_tot, "サブエージェントの起動", sum(agents_tot.values()))
print("  agents", dict(agents_tot.most_common())); print("  skills", dict(skills_tot))
print("  サブエージェントの応答", {k: f"{v} ({v / n_sub:.0%})" for k, v in sub_models.most_common()})
