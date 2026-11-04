"""e-Stat の統計表（データベース形式）を、アプリケーション ID なしで CSV として取得する小さな部品。

e-Stat の画面の内部呼び出しを再現しています。公式 API（アプリケーション ID が必要）ではありません。再取得は控え、作り直しは --no-download で行ってください。

e-Stat のウェブ画面「統計表・グラフ表示 → ダウンロード」が使っている同じ手順を、標準ライブラリだけで再現する。
  1. dbview/api_get_model   表の見出し（項目コードの一覧）を取る
  2. dbview/api_download_create  選んだ項目で CSV を作らせる
  3. dbview/api_download_run     できた CSV を受け取る
公式の API（アプリケーション ID が必要）ではなく、画面の内部の呼び出しなので、e-Stat の画面の仕様が変わると動かなくなる。
出典の表示: 政府統計の総合窓口（e-Stat）の統計表を加工して作成。利用規約は政府標準利用規約（第2.0版）。
"""

from __future__ import annotations

import base64
import gzip
import json
import time
import urllib.parse
import urllib.request

BASE = "https://www.e-stat.go.jp/dbview/"
USER_AGENT = "shipcompare-hackathon/0.3 (non-commercial demo)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


def _post(path: str, sid: str, form: dict | None = None, timeout: int = 300) -> bytes:
    url = f"{BASE}{path}?sid={sid}"
    body = urllib.parse.urlencode(form or {}).encode()
    req = urllib.request.Request(url, data=body, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def get_model(sid: str) -> dict:
    return json.loads(_post("api_get_model", sid))


def _gz(obj) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")


def build_request(model: dict, select: dict[str, set[str] | None], stat_code: str = "") -> dict:
    """select: 項目の見出しの名前（部分一致）→ 選ぶコードの集合。None は全部。書かない見出しは全部選ぶ。

    画面の「ダウンロード」ボタンが作る要求と同じ形にする（rows / cols / tops は gzip + base64）。
    """
    rows, cols, tops, view_tops, tops_all = [], [], [], [], []
    count_sel = count_all = 1
    matters = sorted(model["matters"].values(), key=lambda m: -int(m["positionNum"]))
    for m in matters:
        want = None
        for key, codes in select.items():
            if key in m["matterName"]:
                want = codes
        items = list(m["listData"].values())
        for it in items:
            it["selected"] = want is None or it["code"] in want
        chosen = [it for it in items if it["selected"]]
        if not chosen:
            raise ValueError(f"{m['matterName']}: 選んだコードがありません")
        count_sel *= len(chosen)
        count_all *= len(items)
        h = {"matterId": m["matterId"], "tableName": m["tableName"], "dispTableName": m["dispTableName"],
             "positionNum": m["positionNum"], "matterName": m["matterName"], "initDisp": m["initDisp"],
             "listData": [], "allListData": [], "allSelected": 1, "allDataSelected": 1}
        if m["position"] in ("row", "col"):
            for it in items:
                if it["selected"]:
                    h["listData"].append({"name": it["name"], "code": it["code"], "unit": it["unitName"]})
                else:
                    h["allSelected"] = 0
            (rows if m["position"] == "row" else cols).append(h)
        else:  # top: 画面では 1 つずつ切り替えて見る項目。全データのダウンロードでは allListData が使われる
            first = chosen[0]
            for it in items:
                if not (it["selected"] and it["code"] == first["code"]):
                    h["allSelected"] = 0
            h["listData"].append({"name": first["name"], "code": first["code"]})
            for it in items:
                if it["selected"]:
                    h["allListData"].append({"name": it["name"], "code": it["code"], "unit": it["unitName"]})
                else:
                    h["allDataSelected"] = 0
            tops.append(h)
            if m["initDisp"] != 0:
                view_tops.append(h)
            tops_all.append(h)
    return {
        "rows": _gz(rows), "cols": _gz(cols), "tops": _gz(tops), "viewTops": _gz(view_tops), "topsAll": _gz(tops_all),
        "annotationFlg": "1", "rowNoDataDispFlg": "1", "colNoDataDispFlg": "1", "commaType": "0", "replaceSpChars": "0",
        "currentCols": "", "currentRows": "", "downloadRange": "0", "fileFormat": "2",
        "titleDispFlg": "1", "codeDispFlg": "1", "legendDispFlg": "0", "levelCodeDispFlg": "0",
        "totalCellSelected": "1", "totalCellSelectedCount": str(count_sel), "totalCellCount": str(count_all),
        "totalColSelected": str(max(1, count_sel)), "title": "download", "statCode": stat_code,
        "charset": "Shift-JIS", "bom": "", "auxiliaryCodeDispFlg": "1",
        "startNumber": "1", "totalNum": "", "toNum": "",
    }


def download_csv(sid: str, select: dict[str, set[str] | None], stat_code: str = "") -> tuple[bytes, dict]:
    """列指向形式（1 行 = 1 セル）の CSV（Shift-JIS）のバイト列と、作成結果の情報を返す。"""
    model = get_model(sid)
    form = build_request(model, select, stat_code)
    created = json.loads(_post("api_download_create", sid, form))
    if not created or "fileList" not in created[0]:
        raise RuntimeError(f"CSV の作成に失敗しました: {str(created)[:300]}")
    info = created[0]
    if len(info["fileList"]) != 1:
        raise RuntimeError("ファイルが分割されました。選ぶ項目を減らしてください")
    time.sleep(1)
    f = info["fileList"][0]
    data = _post("api_download_run", sid, {"index": f["fileNo"], "path1": info["filePath"], "name1": f["fileName"]})
    return data, info
