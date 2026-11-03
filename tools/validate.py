"""決定的なプロジェクト検証。tools/requirements.json を読み、100点満点で採点する。"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "tools" / "requirements.json"
SELF_EXCLUDE = {"tools/validate.py", "tools/requirements.json"}

SECRET_RES = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-"),
    re.compile(r"-----BEGIN .*PRIVATE KEY-----"),
    re.compile(r"(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"\s]{12,}", re.I),
]
PATH_RES = [
    re.compile(r"[A-Za-z]:\\\\Users\\\\", re.I),
    re.compile(r"[A-Za-z]:\\Users\\", re.I),
    re.compile(r"C:/Users/", re.I),
    re.compile(r"/c/Users/", re.I),
    re.compile("おたつ"),
]


def git_files(paths=None):
    cmd = ["git", "ls-files", "-co", "--exclude-standard", "-z"]
    if paths:
        cmd += ["--"] + paths
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True).stdout
    return sorted({p for p in out.decode("utf-8", "replace").split("\0") if p and (ROOT / p).is_file()})


def is_binary(data):
    return b"\0" in data[:8192]


def fingerprint():
    h = hashlib.sha256()
    for p in git_files(["src", "app", "data", "tests"]):
        data = (ROOT / p).read_bytes().replace(b"\r\n", b"\n")
        h.update(p.encode("utf-8") + b"\0" + hashlib.sha256(data).hexdigest().encode() + b"\n")
    return h.hexdigest()


def parse_junit(xml_text):
    """{nodeid: passed}。パラメータ化は括弧を除いて集約（全件合格のときだけ True）。"""
    res = {}
    root = ET.fromstring(xml_text)
    for tc in root.iter("testcase"):
        cls = tc.get("classname", "")
        name = re.sub(r"\[.*\]$", "", tc.get("name", ""), flags=re.S)
        parts = cls.split(".") if cls else []
        if len(parts) >= 2:
            file = "/".join(parts[:2]) + ".py"
            klass = parts[2:]
        else:
            file, klass = (parts[0] + ".py" if parts else ""), []
        ok = not any(c.tag in ("failure", "error") for c in tc)
        ids = [f"{file}::{name}"]
        if klass:
            ids.append(f"{file}::{'::'.join(klass)}::{name}")
        for i in ids:
            res[i] = res.get(i, True) and ok
    return res


def run_pytest():
    with tempfile.TemporaryDirectory() as td:
        x = os.path.join(td, "junit.xml")
        r = subprocess.run(["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={x}"],
                           cwd=ROOT, capture_output=True)
        if not os.path.exists(x):
            tail = (r.stdout + r.stderr).decode("utf-8", "replace")[-300:]
            return {}, 0, f"pytest が結果を出さなかった: {tail}"
        text = Path(x).read_text(encoding="utf-8")
    root = ET.fromstring(text)
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    bad = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    return parse_junit(text), bad, None


def image_width(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(data[16:20], "big")
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            m = data[i + 1]
            if m == 0xFF:
                i += 1
                continue
            if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            ln = int.from_bytes(data[i + 2:i + 4], "big")
            if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                return int.from_bytes(data[i + 7:i + 9], "big")
            i += 2 + ln
    return None


def read_text(rel):
    p = ROOT / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None


def floor_half(x):
    return math.floor(x * 2) / 2


def scan_files(exclude_self=False):
    for p in git_files():
        if p == "uv.lock" or (exclude_self and p in SELF_EXCLUDE):
            continue
        data = (ROOT / p).read_bytes()
        if is_binary(data):
            continue
        yield p, data


def line_hits(regexes):
    why = []
    for p, data in scan_files(True):
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if any(r.search(line) for r in regexes):
                why.append(f"{p}:{n}")
    return why


def short(items, n=5):
    return ", ".join(items[:n]) + (f" ... 計{len(items)}件" if len(items) > n else "")


def check_item(item, ctx):
    """戻り値 (得点, 成否, 理由)"""
    c, pts = item["check"], item["points"]
    t = c["type"]

    def full(ok, why=""):
        return (pts if ok else 0), ok, ("" if ok else why)

    tests = ctx["tests"]
    if t == "pytest_all_pass":
        if ctx["err"]:
            return full(False, ctx["err"])
        return full(ctx["bad"] == 0 and len(tests) > 0, f"失敗・エラー {ctx['bad']} 件 / 試験 {len(tests)} 件")
    if t == "tests":
        miss = [n for n in c["tests"] if n not in tests]
        fail = [n for n in c["tests"] if n in tests and not tests[n]]
        why = []
        if miss:
            why.append("なし: " + ", ".join(n.split("::")[-1] for n in miss))
        if fail:
            why.append("不合格: " + ", ".join(n.split("::")[-1] for n in fail))
        return full(not why, " / ".join(why))
    if t == "test_prefix":
        sel = {k: v for k, v in tests.items() if k.split("::")[-1].startswith(c["prefix"])}
        if not sel:
            return full(False, f"{c['prefix']}* の試験がない")
        fail = [k.split("::")[-1] for k, v in sel.items() if not v]
        return full(not fail, "不合格: " + ", ".join(fail))
    if t == "grep":
        s = read_text(c["file"])
        if s is None:
            return full(False, f"{c['file']} がない")
        return full(re.search(c["pattern"], s) is not None, "パターンが見つからない")
    if t == "evidence_images":
        devlog = read_text("docs/DEVLOG.md") or ""
        okn, why = 0, []
        for f in c["files"]:
            p = ROOT / f
            if not p.is_file():
                why.append(f"{Path(f).name}: なし")
                continue
            w = image_width(p.read_bytes())
            if w is None or w < 1280:
                why.append(f"{Path(f).name}: 幅 {w}")
            elif Path(f).name not in devlog:
                why.append(f"{Path(f).name}: DEVLOG に未記載")
            else:
                okn += 1
        got = floor_half(pts * okn / len(c["files"]))
        msg = f"{okn}/{len(c['files'])} 件。" + short(why, 2) if why else ""
        return got, okn == len(c["files"]), msg
    if t == "review":
        s = read_text(c["file"])
        if s is None:
            return full(False, f"{c['file']} がない")
        m = re.search(r"^対象の指紋:\s*([0-9a-fA-F]+)\s*$", s, re.M)
        if not m:
            return full(False, "対象の指紋の行がない")
        if m.group(1).lower() != ctx["fp"]:
            return full(False, "指紋が今のコードと違う（レビューが古い）")
        if not re.search(r"^P0: 0\s*$", s, re.M) or not re.search(r"^P1: 0\s*$", s, re.M):
            return full(False, "P0: 0 / P1: 0 の行がない")
        return full(True)
    if t == "headings":
        s = read_text(c["file"])
        if s is None:
            return full(False, f"{c['file']} がない")
        miss = [h for h in c["headings"] if not re.search(r"^#{1,6}\s*" + re.escape(h), s, re.M)]
        return full(not miss, "見出しなし: " + ", ".join(miss))
    if t == "contains_all":
        s = read_text(c["file"])
        if s is None:
            return full(False, f"{c['file']} がない")
        miss = [w for w in c["words"] if w not in s]
        return full(not miss, "なし: " + ", ".join(miss))
    if t == "no_secrets":
        why = [f"{p} (追跡された .env)" for p in git_files()
               if Path(p).name != ".env.example" and (Path(p).name == ".env" or Path(p).name.startswith(".env."))]
        why += line_hits(SECRET_RES)
        return full(not why, short(why))
    if t == "no_local_paths":
        why = line_hits(PATH_RES)
        return full(not why, short(why))
    if t == "lf_only":
        why = [p for p, data in scan_files(False) if b"\r\n" in data]
        return full(not why, "CRLF: " + short(why))
    return 0, False, f"未知の検査 {t}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fingerprint", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.fingerprint:
        print(fingerprint())
        return 0
    req = json.loads(REQ.read_text(encoding="utf-8"))
    items = [(g, i) for g in req["groups"] for i in g["items"]]
    total = sum(i["points"] for _, i in items)
    if total != 100:
        print(f"配点の合計が {total} 点です。100 点にしてください。")
        return 2
    tests, bad, err = run_pytest()
    ctx = {"tests": tests, "bad": bad, "err": err, "fp": fingerprint()}
    rows, score, failed = [], 0.0, []
    print("群 ID    得点/配点  判定  内容 / 理由")
    for g, i in items:
        got, ok, why = check_item(i, ctx)
        score += got
        if not ok:
            failed.append(i["id"])
        mark = "○" if ok else "×"
        ex = "（採点外）" if i.get("excluded") else ""
        print(f"{g['id']:<2} {i['id']:<4} {got:>5g}/{i['points']:<3g}  {mark}   {i['text']}{ex}"
              + (f"  → {why}" if not ok else ""))
        rows.append({"group": g["id"], "id": i["id"], "text": i["text"], "points": i["points"],
                     "got": got, "passed": ok, "reason": why, "excluded": bool(i.get("excluded"))})
    print(f"\n合計 {score:g} / 100 点  不合格 {len(failed)} 件")
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    out = ROOT / "docs" / "validation"
    out.mkdir(parents=True, exist_ok=True)
    latest = {"score": score, "head": head, "timestamp": ts, "fingerprint": ctx["fp"],
              "failed_ids": failed, "items": rows}
    with open(out / "latest.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(out / "history.jsonl", "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"timestamp": ts, "head": head, "score": score, "failed_ids": failed},
                           ensure_ascii=False) + "\n")
    return 0 if score == 100 and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
