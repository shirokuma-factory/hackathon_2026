"""docs/video/edl_r9.json から DaVinci Resolve にタイムラインを組み、Resolve で書き出す（r9 から）。

作成者の指示（11/7）: 動画は Resolve で組んで書き出す。r5〜r8 は ffmpeg（render_edl.py）で書き出していた。
DaVinci Resolve の MCP（run_script_unsafe）に、このファイルの中身をそのまま渡して実行する。`resolve` と `project` は MCP が用意する。

r4 版（resolve_build_r4.py）との違い:
  - タイムラインは 3840x2160 / 30fps（独自設定）。各素材は隣に 4K 版（@2x / _4k）があればそちらを読む
  - V1 の dissolve_in（黒を挟まない切り替え）は、同じトラックに重ねられないので V2 に置き、フェードインで下の V1 から現れる
  - fade_in / fade_out は SetFades、効果音の gain_db は AudioVolume
  - 最後に 1920x1080 の H.264 で書き出しを登録して始める（RENDER=False なら組むだけ）
MCP の 1 回の実行は 10 秒で打ち切られるので、STAGE と START を渡して少しずつ進める:
  STAGE="import"（読み込みだけ。終わるまで繰り返す）→ STAGE="build", START=0（返る next を次の START に。done まで）→ STAGE="render"
"""
import time
import json
import os

EDL = globals().get("EDL_PATH", "C:/dev/hackathon_2026/docs/video/edl_r9.json")
OUT_DIR = globals().get("OUT_DIR", "C:/dev/hackathon_2026/outputs/video/assembly")
OUT_NAME = globals().get("OUT_NAME", "Backfull_r9_resolve")
RENDER = globals().get("RENDER", True)
edl = json.load(open(EDL, encoding="utf-8"))
ROOT_DIR = globals().get("ROOT_DIR", "C:/dev/hackathon_2026")
# EDL の path はリポジトリからの相対パス（r11 から）。絶対パスの古い EDL もそのまま読める
for _k in ("video", "audio"):
    for _rows in edl[_k].values():
        for _d in _rows:
            if not os.path.isabs(_d["path"]):
                _d["path"] = os.path.normpath(os.path.join(ROOT_DIR, _d["path"]))
mp = project.GetMediaPool()
root = mp.GetRootFolder()


def hd_sibling(path):
    d, f = os.path.split(path)
    stem, ext = os.path.splitext(f)
    stems = [stem] + ([stem[:-2]] if stem.endswith("_h") else [])
    for st in stems:
        for tag in ("@2x", "_4k"):
            q = os.path.join(d, st + tag + ext)
            if os.path.exists(q):
                return q
    return path


def walk(f, acc):
    for c in f.GetClipList() or []:
        p = c.GetClipProperty("File Path")
        if p:
            acc[p.lower()] = c
    for s in f.GetSubFolderList() or []:
        walk(s, acc)
    return acc


pool = walk(root, {})
fol = [f for f in root.GetSubFolderList() if f.GetName() == "13_r9"]
fol = fol[0] if fol else mp.AddSubFolder(root, "13_r9")
log = []


def item(path):
    k = path.replace("/", "\\").lower()
    if k in pool:
        return pool[k]
    mp.SetCurrentFolder(fol)
    r = mp.ImportMedia([path.replace("/", "\\")])
    if r:
        pool[k] = r[0]
        if path.lower().endswith(".mov"):
            # 図・字幕は透明つきの qtrle（argb）。透明をそのまま使う
            if not r[0].SetClipProperty("Alpha mode", "Straight"):
                log.append(["alpha mode not set", os.path.basename(path)])
        return r[0]
    return None


STAGE = globals().get("STAGE", "import")
START = globals().get("START", 0)
T0 = time.time()
BUDGET = 4.5  # MCP の 1 回は 10 秒で切れる。読み込みの遅い素材があっても間に合うよう余裕を持つ
name = edl["timeline"]

# V1 の dissolve_in を V2 へ移す（r9 の EDL は V2 を使っていない）
vid = {k: list(v) for k, v in edl["video"].items()}
if "2" not in vid or not vid["2"]:
    vid["2"] = [d for d in vid.get("1", []) if d.get("dissolve_in")]
    vid["1"] = [d for d in vid.get("1", []) if not d.get("dissolve_in")]
rows_all = []
for kind, mt, rows_by in (("video", 1, vid), ("audio", 2, edl["audio"])):
    for t_, rows in sorted(rows_by.items(), key=lambda x: int(x[0])):
        for d in rows:
            path = hd_sibling(d["path"]) if kind == "video" else d["path"]
            rows_all.append((kind, mt, t_, d, path))

if STAGE == "import":
    todo = sorted({p for *_, p in rows_all if p.replace("/", "\\").lower() not in pool})
    done = 0
    for p_ in todo:
        if time.time() - T0 > BUDGET:
            break
        if item(p_) is None:
            log.append(["import failed", p_])
        done += 1
    result = {"stage": "import", "imported": done, "left": len(todo) - done, "log": log}

elif STAGE == "build":
    tl = None
    for i in range(1, project.GetTimelineCount() + 1):
        x = project.GetTimelineByIndex(i)
        if x and x.GetName() == name:
            tl = x
    if START == 0 and not globals().get("NOCREATE"):  # NOCREATE=True なら作り直さずに 0 本目から置く
        if tl:
            mp.DeleteTimelines([tl])
        mp.SetCurrentFolder(fol)
        tl = mp.CreateEmptyTimeline(name)
        for k, v in (("useCustomSettings", "1"), ("timelineResolutionWidth", "3840"), ("timelineResolutionHeight", "2160"),
                     ("timelineFrameRate", "30"), ("timelineOutputResolutionWidth", "3840"), ("timelineOutputResolutionHeight", "2160")):
            if not tl.SetSetting(k, v):
                log.append(["timeline setting failed", k, v])
        for kind, rows_by in (("video", vid), ("audio", edl["audio"])):
            need = max(int(k) for k in rows_by)
            while tl.GetTrackCount(kind) < need:
                tl.AddTrack(kind, "stereo") if kind == "audio" else tl.AddTrack(kind)
    project.SetCurrentTimeline(tl)
    st = tl.GetStartFrame()
    i = START
    while i < len(rows_all) and time.time() - T0 < BUDGET:
        kind, mt, t_, d, path = rows_all[i]
        i += 1
        m = item(path)
        if m is None:
            log.append(["missing", path])
            continue
        L = d["e"] - d["s"]
        r = mp.AppendToTimeline([{"mediaPoolItem": m, "startFrame": d["off"], "endFrame": d["off"] + L - 1,
                                  "recordFrame": st + d["s"], "trackIndex": int(t_), "mediaType": mt}])
        if not r:
            log.append(["append failed", kind, t_, d["s"], os.path.basename(path)])
            continue
        it = r[0]
        for k2, v in (d.get("p") or {}).items():
            if v not in (None, 1.0, 0.0, 100.0) or k2 in ("ZoomX", "ZoomY") and v != 1.0:
                it.SetProperty(k2, v)
        fi = d.get("dissolve_in") or d.get("fade_in", 0)
        fo = d.get("fade_out", 0)
        if kind == "video" and (fi or fo):
            if not it.SetFades({"FadeIn": fi, "FadeOut": fo}):
                log.append(["fade failed", t_, d["s"]])
        if kind == "audio" and d.get("gain_db"):
            if not it.SetProperty("AudioVolume", float(d["gain_db"])):
                log.append(["volume failed", t_, d["s"]])
    result = {"stage": "build", "next": i, "total": len(rows_all), "done": i >= len(rows_all),
              "frames": tl.GetEndFrame() - st, "log": log[:30], "nlog": len(log)}

elif STAGE == "render":
    tl = [project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)]
    tl = [x for x in tl if x.GetName() == name][0]
    project.SetCurrentTimeline(tl)
    # 配置が途中で切れたタイムラインを書き出さない（11/7、build が 10 秒で切れたまま書き出しを始めたため）
    placed = sum(len(tl.GetItemListInTrack(k, i) or []) for k in ("video", "audio") for i in range(1, tl.GetTrackCount(k) + 1))
    if placed != len(rows_all):
        raise RuntimeError(f"タイムラインの素材 {placed} 本と EDL の {len(rows_all)} 本が合いません。build を done まで続けてください")
    # 画質は API の VideoQuality が受け付けられない（Resolve 21.1、どのコーデックでも False）ので、
    # 高画質の組み込みプリセット「H.264 Master」を読み込んでから、範囲・大きさ・名前だけを上書きする
    PRESET = globals().get("PRESET", "H.264 Master")  # "ProRes 422 HQ" で高画質の元を書き出し、配る mp4 は別に作る
    preset_ok = project.LoadRenderPreset(PRESET)
    if PRESET.startswith("H.264"):
        project.SetCurrentRenderFormatAndCodec("mp4", "H264")
    st = tl.GetStartFrame()
    # 書き出す範囲は EDL の長さまで（終わりの効果音の余韻がはみ出して 3 分を超えないように）
    ok = project.SetRenderSettings({"SelectAllFrames": False, "MarkIn": st, "MarkOut": st + round(edl["total"] * 30) - 1, "TargetDir": OUT_DIR.replace("/", "\\"), "CustomName": OUT_NAME,
                                    "FormatWidth": 1920, "FormatHeight": 1080, "FrameRate": 30, "ExportVideo": True, "ExportAudio": True,
                                    **({"AudioCodec": "aac"} if PRESET.startswith("H.264") else {}), "AudioSampleRate": 48000})
    if not ok:
        raise RuntimeError("書き出しの設定が受け付けられませんでした（SetRenderSettings が False）")
    job = project.AddRenderJob()
    started = project.StartRendering([job]) if job else False
    result = {"stage": "render", "preset": preset_ok, "render_settings": ok, "job": job, "started": started}
