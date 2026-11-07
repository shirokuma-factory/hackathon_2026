"""docs/video/edl_r4.json から Resolve に新しいタイムラインを組む。

DaVinci Resolve の MCP（run_script_unsafe）に、このファイルの中身をそのまま渡して実行する。
`resolve` と `project` は MCP が用意する。読み込み済みのファイルは同じパスのものを使い、無ければ 12_r4 に読み込む。
"""
import json
import os

# リポジトリの場所は呼び出す側が ROOT_DIR で渡すか、環境変数 BACKFULL_ROOT で決める（PC 固有のパスを台本に書かない）
ROOT_DIR = globals().get("ROOT_DIR") or os.environ.get("BACKFULL_ROOT")
EDL = globals().get("EDL_PATH") or (os.path.join(ROOT_DIR, "docs/video/edl_r4.json") if ROOT_DIR else None)
if not EDL:
    raise RuntimeError("EDL_PATH か ROOT_DIR（または環境変数 BACKFULL_ROOT）を渡してください")
edl = json.load(open(EDL, encoding="utf-8"))
mp = project.GetMediaPool()
root = mp.GetRootFolder()


def walk(f, acc):
    for c in f.GetClipList() or []:
        p = c.GetClipProperty("File Path")
        if p:
            acc[p.lower()] = c
    for s in f.GetSubFolderList() or []:
        walk(s, acc)
    return acc


pool = walk(root, {})
fol = [f for f in root.GetSubFolderList() if f.GetName() == "12_r4"]
fol = fol[0] if fol else mp.AddSubFolder(root, "12_r4")


def item(path):
    k = path.replace("/", "\\").lower()
    if k in pool:
        return pool[k]
    mp.SetCurrentFolder(fol)
    r = mp.ImportMedia([path.replace("/", "\\")])
    if r:
        pool[k] = r[0]
        return r[0]
    return None


name = edl["timeline"]
for i in range(1, project.GetTimelineCount() + 1):
    t = project.GetTimelineByIndex(i)
    if t.GetName() == name:
        mp.DeleteTimelines([t])
        break
tl = mp.CreateEmptyTimeline(name)
project.SetCurrentTimeline(tl)
st = tl.GetStartFrame()
log = []
for kind, mt in (("video", 1), ("audio", 2)):
    need = max(int(k) for k in edl[kind])
    while tl.GetTrackCount(kind) < need:
        tl.AddTrack(kind, "stereo") if kind == "audio" else tl.AddTrack(kind)
    for t, rows in sorted(edl[kind].items(), key=lambda x: int(x[0])):
        for d in rows:
            m = item(d["path"])
            if m is None:
                log.append(["missing", d["path"]])
                continue
            L = d["e"] - d["s"]
            r = mp.AppendToTimeline([{"mediaPoolItem": m, "startFrame": d["off"], "endFrame": d["off"] + L - 1,
                                      "recordFrame": st + d["s"], "trackIndex": int(t), "mediaType": mt}])
            if not r:
                log.append(["append failed", kind, t, d["s"], d["path"]])
                continue
            it = r[0]
            for k, v in (d.get("p") or {}).items():
                if v not in (None, 1.0, 0.0, 100.0) or k in ("ZoomX", "ZoomY") and v != 1.0:
                    it.SetProperty(k, v)
            if kind == "video" and (d.get("fade_in") or d.get("fade_out")):
                it.SetFades({"FadeIn": d.get("fade_in", 0), "FadeOut": d.get("fade_out", 0)})
            d["_item"] = it
# 章の中の切り替えは重ねる
for d in edl["video"].get("1", []):
    if d.get("xfade_out") and d.get("_item"):
        tr = d["_item"].AddTransition({"type": "Cross Dissolve", "category": "simple", "position": "end", "alignment": "center", "duration": d["xfade_out"]})
        if not tr:
            log.append(["xfade failed", d["s"], d["path"][-30:]])
result = {"timeline": name, "frames": tl.GetEndFrame() - st, "log": log[:40], "nlog": len(log)}
