"""地図の画面（web/）の配信サーバー（SPEC §15.1）。標準ライブラリだけで書く。

web/ の静的ファイルと JSON の API を出す。計算はすべて src/shipcompare の関数を呼び、
JS では運賃や順位を計算しない。127.0.0.1 だけで待ち受け、サーバーにファイルは書かない。
--public（公開版。Cloud Run 用）の違いは SPEC §15.1「公開版」と docs/DEPLOY.md。

使い方:
    uv run python tools/serve_web.py            # http://127.0.0.1:8600/
    uv run python tools/serve_web.py --port 8700

API（POST は Content-Type: application/json の JSON。入力の誤りは 400 と日本語の理由）:
    GET  /api/scenarios                 場面 S1〜S5
    GET  /api/demo_quotes?id=S5&version=1[&today=2026-11-03]   架空の見積回答（id は空）
    POST /api/estimate   {shipment, origin_code?, dest_code?, route?, distance_km?}
    POST /api/layer      {origin_code, shipment}   荷物に合わせた貸切の額の層
    POST /api/loadplan   {shipment, container?, stackable?}   荷室への積付けの配置（SPEC §20。目安）
    POST /api/waiting/calc {year?, rows:[...] | text}   荷待ち・荷役の記録の計算（SPEC §23。記録は保存もログ出力もしない）
    POST /api/request    {shipment, scenario?}
    POST /api/compare    {quotes, version, today?}
    POST /api/save       案件（case_to_dict の形）→ 正規化した案件 JSON（保存はブラウザのダウンロード）
    POST /api/load       保存した案件 JSON → 正規化した案件 JSON
  画面が案件の規則（版の増やし方、場面の情報、見積回答の id）を Python に任せるための補助:
    POST /api/confirm          {case, shipment, scenario_id?}  Case.confirm → {case, changed}
    POST /api/add_quote        {case, quote}                   id と版を付けて追加 → {case, quote_id}
    POST /api/add_demo_quotes  {case, today?}                  場面の架空の回答を追加 → {case, added}
  B 空きを探す・つなぐ（SPEC §16.2、架空のデモ。状態はメモリで、再起動か reset で初期に戻る）:
    GET  /api/route?from=<出発地の市区町村コード>&to=<着地の市区町村コード>[&from_ll=経度,緯度]
                                         トラックの道路の経路（SPEC §17.1）。その場で OSRM に問い合わせ、data/routes/cache/ に保存する
                                         （1 秒 1 件）。取れなければ直線の弧と「経路を取得できませんでした」（fallback: true）
    GET  /api/b/trips                   架空の運送会社と便（いまの重さの余りと仮予約つき）
    POST /api/b/match   {shipment, dest_code, category?, in_container?, fragile?, is_box?, origin_code?, window_from?, window_to?}  便ごとの合否と理由
    POST /api/b/hold    {trip_id, shipment, dest_code, ..., hold_minutes? | hold_until?, shipper?}   期限付きの仮予約（余りを減らす。締切は 72 時間まで）
    POST /api/b/decide  {hold_id, decision: accept|decline}                  運送会社の役の承諾か辞退
    POST /api/b/contract {hold_id, fuel_surcharge?, payment_method?}          承諾済みの仮予約から書面の下書き（SPEC §21.3。承諾前は 400）
    POST /api/b/price_formula {trip_id, shipment, base_load_rate?, lead_discount?, region?, min_fee_yen?}   個建運賃の参考額（SPEC §21.6。運送会社の役の画面で使う）
    POST /api/b/reset   {}                                                    初期の架空データに戻す
"""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import mimetypes
import os
import re
import sys
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import geo, loadplan, spaceb, waiting  # noqa: E402
from shipcompare import routes as route_lib  # noqa: E402
from shipcompare.estimate import ST_NOCALC, estimate_all, summarize  # noqa: E402
from shipcompare.guide import contacts_for_rows, load_transit_days, mode_group, transit_days, transit_days_text  # noqa: E402
from shipcompare.models import COST_KEYS, KIND_AMOUNT, KIND_INCLUDED, KIND_UNKNOWN  # noqa: E402
from shipcompare.quotes import compare_quotes, line_amounts_ex_tax  # noqa: E402
from shipcompare.request import build_request  # noqa: E402
from shipcompare.storage import (  # noqa: E402
    ORDER_NOTICE, case_from_dict, case_to_dict, demo_quotes_for, list_scenarios, load_scenario, quote_from_dict,
    quote_to_dict, scenario_info, shipment_from_dict, shipment_to_dict, validate_shipment,
)
from shipcompare.tariffs import ROUTES, load_tariff  # noqa: E402

WEB_DIR = ROOT / "web"
DEFAULT_PORT = 8600
MAX_BODY = 2_000_000
ALLOWED_HOSTS = ("127.0.0.1", "localhost")

# ---- 公開版（Cloud Run。SPEC §15.1「公開版」）。--public か環境変数 SHIPCOMPARE_PUBLIC=1 で有効
PUBLIC_DEFAULT_PORT = 8080  # Cloud Run は環境変数 PORT で渡す。無ければこの値
SESSION_HEADER = "X-Demo-Session"
SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
ANON_SESSION = "anon"
SESSION_MAX = 300  # 来訪者ごとの状態を持つ最大数（古い順に捨てる）
SESSION_TTL_SEC = 2 * 3600  # 使われないまま経つとこの時間で捨てる
GZIP_EXTS = {".js", ".css", ".json", ".geojson", ".html", ".svg", ".glb"}
LONG_CACHE_DIRS = ("data", "models", "img")  # web/ の下で 1 時間キャッシュさせる場所
LONG_CACHE = "public, max-age=3600"
MAX_STATIC_CACHE = 32_000_000  # 圧縮済みデータをメモリに持つ上限（バイト）


def env_public() -> bool:
    return os.environ.get("SHIPCOMPARE_PUBLIC", "").strip().lower() in ("1", "true", "yes", "on")


def hide_local() -> bool:
    """web/data/local/（P31。再配布不可）を配らない。公開版では常に配らない。"""
    return os.environ.get("SHIPCOMPARE_HIDE_LOCAL", "").strip() == "1"


def resolve_port(public: bool, cli_port: int | None, env: dict[str, str] | None = None) -> int:
    """待ち受けるポート。--port > 公開版なら環境変数 PORT（無ければ 8080） > 8600。"""
    if cli_port is not None:
        return cli_port
    if public:
        raw = (env if env is not None else os.environ).get("PORT", "")
        return int(raw) if raw.isdigit() else PUBLIC_DEFAULT_PORT
    return DEFAULT_PORT


class _Ctx(threading.local):
    """リクエストごとの状態（ハンドラが入れる）。API の関数はここから公開版かどうかと来訪者 ID を読む。"""
    public = False
    sid = ANON_SESSION


_CTX = _Ctx()


class BadRequest(Exception):
    """入力の誤り。メッセージは利用者に見せる日本語。"""


# ---------------------------------------------------------------- 入力の読み取り

def _need_dict(body: Any, name: str = "本文") -> dict[str, Any]:
    if not isinstance(body, dict):
        raise BadRequest(f"{name}が JSON のオブジェクトではありません")
    return body


def _shipment_of(body: dict[str, Any]):
    raw = body.get("shipment")
    if not isinstance(raw, dict):
        raise BadRequest("shipment（荷物と条件）がありません")
    try:
        s = shipment_from_dict(raw)
        validate_shipment(s)
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        raise BadRequest(f"荷物と条件の形が違います: {e}") from e
    return s


def _parse_date(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise BadRequest(f"{name}は YYYY-MM-DD の文字列で渡してください")
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise BadRequest(f"{name}の日付が読めません: {value}") from e


def _int_of(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BadRequest(f"{name}は整数で渡してください")
    return value


def _quotes_of(body: dict[str, Any]):
    raw = body.get("quotes")
    if not isinstance(raw, list):
        raise BadRequest("quotes（見積回答の一覧）がありません")
    out = []
    for n, q in enumerate(raw, start=1):
        if not isinstance(q, dict):
            raise BadRequest(f"見積回答{n}件目が辞書ではありません")
        for line_key, line in (q.get("lines") or {}).items():
            if not isinstance(line, dict):
                raise BadRequest(f"見積回答{n}件目の内訳 {line_key} が辞書ではありません")
            kind, amount = line.get("kind"), line.get("amount")
            if kind not in (KIND_AMOUNT, KIND_INCLUDED, KIND_UNKNOWN):
                raise BadRequest(f"見積回答{n}件目の内訳 {line_key} の種別は amount・included・unknown のどれかです")
            if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int)):
                raise BadRequest(f"見積回答{n}件目の内訳 {line_key} の金額は整数で渡してください")
        try:
            quote = quote_from_dict(q)
        except KeyError as e:
            raise BadRequest(f"見積回答{n}件目に項目 {e} がありません") from e
        except (ValueError, TypeError, AttributeError) as e:
            raise BadRequest(f"見積回答{n}件目の形が違います: {e}") from e
        if not all(isinstance(x, str) for x in (quote.id, quote.carrier, quote.product, quote.tax)):
            raise BadRequest(f"見積回答{n}件目の id・会社名・商品名・税区分は文字列で渡してください")
        if isinstance(quote.version, bool) or not isinstance(quote.version, int):
            raise BadRequest(f"見積回答{n}件目の版は整数で渡してください")
        out.append(quote)
    return out


# ---------------------------------------------------------------- 出力の形

def _plain(obj: Any) -> Any:
    """Decimal などを JSON にできる形にする。"""
    if isinstance(obj, Decimal):
        return float(obj) if obj != obj.to_integral_value() else int(obj)
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def _row_dict(row, route: str | None, transit_modes: dict[str, Any]) -> dict[str, Any]:
    td = None
    if row.status != ST_NOCALC:  # 「計算できない」行には日数を出さない（SPEC §13.8）
        td = transit_days(row.mode, route, transit_modes)
    src = row.source
    return {
        "mode": row.mode,
        "group": mode_group(row.mode),
        "label": row.label,
        "status": row.status,
        "amount": row.amount if row.has_amount() else None,
        "low": row.low,
        "high": row.high,
        "reasons": list(row.reasons),
        "missing": list(row.missing),
        "issues": [{"code": i.code, "message": i.message} for i in row.issues],
        "notes": list(row.notes),
        "detail": _plain(row.detail),
        "source": {"name": src.name, "url": src.url or None, "applies_from": src.applies_from,
                   "checked_on": src.checked_on} if src else None,
        "transit": None if td is None else {
            "text": td.text, "note": td.note, "url": td.url, "published": td.published,
            "source_name": td.source_name, "source_url": td.source_url, "checked_on": td.checked_on,
            "display": transit_days_text(td),
        },
    }


def api_scenarios() -> dict[str, Any]:
    return {"scenarios": [{
        "id": sc["id"], "name": sc["name"], "label": sc.get("label"),
        "assumed_shipper": bool(sc.get("assumed_shipper")), "assumption_note": sc.get("assumption_note"),
        "has_demo_quotes": bool(sc.get("demo_quotes")), "shipment": shipment_to_dict(sc["shipment"]),
        "backhaul": sc.get("backhaul"),  # 空きを探す・つなぐ用の追加項目（SPEC §21。S6 だけ）
    } for sc in list_scenarios()]}


def api_demo_quotes(query: dict[str, list[str]]) -> dict[str, Any]:
    sid = (query.get("id") or [""])[0]
    if not re.fullmatch(r"S\d{1,3}", sid):
        raise BadRequest("場面の id は S1〜S5 の形で渡してください")
    try:
        sc = load_scenario(sid)
    except FileNotFoundError as e:
        raise BadRequest(f"場面 {sid} が見つかりません") from e
    raw_version = (query.get("version") or [""])[0]
    if not raw_version.isdigit() or int(raw_version) < 1:
        raise BadRequest("version（条件の版）は 1 以上の整数で渡してください")
    raw_today = (query.get("today") or [""])[0]
    today = _parse_date(raw_today, "today") if raw_today else date.today()
    return {"scenario_id": sid, "quotes": [quote_to_dict(q) for q in demo_quotes_for(sc, int(raw_version), today)]}


ORIGIN_SUBSTITUTE_MAX_KM = 50  # 代用してよい最寄りの出発地までの直線距離


def _origin_or_nearest(oc: Any) -> tuple[str, str | None]:
    """出発地のコード。関東の7か所に無い市区町村（例: S6 の江東区）は、最寄りの出発地の事前計算の表で代用する。
    戻り値は (使うコード, 代用の注記)。新しく通信はしない（SPEC §21.2）。"""
    if oc in geo.ORIGINS:
        return oc, None
    pts = geo.load_points()
    if not isinstance(oc, str) or oc not in pts:
        raise BadRequest(f"出発地 {oc!r} は対応していません（関東の7か所だけです）")
    a = pts[oc]
    used = min(geo.ORIGINS, key=lambda c: geo.haversine_km(a["lon"], a["lat"], pts[c]["lon"], pts[c]["lat"]))
    if geo.haversine_km(a["lon"], a["lat"], pts[used]["lon"], pts[used]["lat"]) > ORIGIN_SUBSTITUTE_MAX_KM:
        raise BadRequest(f"出発地 {oc!r} は対応していません（関東の7か所と、その近く {ORIGIN_SUBSTITUTE_MAX_KM}km 以内だけです）")
    return used, f"距離は最寄りの出発地（{pts[used]['name']}）の値で代用"


def _apply_destination(body: dict[str, Any], s) -> dict[str, Any] | None:
    """出発地と着地のコードがあれば、区間と距離を荷物に当てはめて、道路の情報を返す。"""
    oc, dc = body.get("origin_code"), body.get("dest_code")
    if oc is None and dc is None:
        if body.get("route") is not None:
            if body["route"] not in ROUTES:
                raise BadRequest(f"区間 {body['route']!r} は対応していません")
            s.route = body["route"]
        if body.get("distance_km") is not None:
            d = body["distance_km"]
            if isinstance(d, bool) or not isinstance(d, (int, float)) or d < 0:
                raise BadRequest("distance_km は 0 以上の数で渡してください")
            s.distance_km = float(d)
        return None
    oc, origin_note = _origin_or_nearest(oc)
    if not isinstance(dc, str) or dc not in geo.load_points():
        raise BadRequest(f"着地 {dc!r} の市区町村コードが見つかりません")
    info = geo.drive_info(oc, dc)
    if info is not None:
        info = dict(info, origin_note=origin_note, origin_used=oc)
    route_id = geo.route_for(oc, dc)
    s.route = route_id
    if route_id == "other":
        s.origin_in_kanto = True  # 出発地は関東の7か所
        s.distance_km = None if (info["no_route"] or info["ferry"]) else info["km"]
    else:
        s.distance_km = None  # デモの区間は表の想定距離を使う
    return info


def api_estimate(body: Any) -> dict[str, Any]:
    body = _need_dict(body)
    s = _shipment_of(body)
    info = _apply_destination(body, s)
    rows = estimate_all(s)
    totals = summarize(s)
    transit_modes = load_transit_days()
    out_rows = [_row_dict(r, s.route, transit_modes) for r in rows]
    blocked = None
    if info is not None and (info["no_route"] or info["ferry"]):
        blocked = ("道路の経路がありません（離島など）" if info["no_route"]
                   else "計算できない（海を渡る経路）")
        kept, seen = [], False
        for r in out_rows:
            if r["group"] == "kashikiri":
                if seen:
                    continue
                seen = True
                r.update(status=ST_NOCALC, amount=None, low=None, high=None, reasons=[blocked], missing=[],
                         issues=[], notes=[], detail={}, transit=None)
            kept.append(r)
        out_rows = kept
    osrm_kashikiri = None
    if info is not None and not (info["no_route"] or info["ferry"]):
        kf = geo.kashikiri_for_distance(info["km"], float(totals.weight_kg) if totals.weight_kg is not None else None,
                                        load_tariff("kashikiri"))
        osrm_kashikiri = {"km": info["km"], "class": kf[0], "amount": kf[1]} if kf else None
    route = ROUTES.get(s.route or "")
    return {
        "route": s.route,
        "shipment": shipment_to_dict(s),  # 着地の区間・距離を当てはめた荷物（確定に使う）
        "route_label": route.label if route else None,
        "assumed_km": route.km if route else None,
        "distance_km": s.distance_km,
        "geo": info,
        "blocked_reason": blocked,
        "osrm_kashikiri": osrm_kashikiri,
        "totals": _plain({
            "weight_kg": totals.weight_kg, "volume_m3": totals.volume_m3, "chargeable_kg": totals.chargeable_kg,
            "problems": totals.problems,
        }),
        "rows": out_rows,
    }


def api_layer(body: Any) -> dict[str, Any]:
    body = _need_dict(body)
    oc, origin_note = _origin_or_nearest(body.get("origin_code"))
    s = _shipment_of(body)
    totals = summarize(s)
    kg = float(totals.weight_kg) if totals.ok and totals.weight_kg is not None else None
    codes = sorted(geo.load_points())
    fare = geo.fare_layer(oc, kg, load_tariff("kashikiri"), codes)
    present = [v for v in fare if v is not None]
    edges = geo.quantile_edges(present) if present else []
    cls = [geo.class_of(v, edges) if v is not None else None for v in fare]
    return {"origin_code": oc, "total_kg": kg, "fare": fare, "edges": edges, "cls": cls, "origin_note": origin_note}


def api_loadplan(body: Any) -> dict[str, Any]:
    body = _need_dict(body)
    s = _shipment_of(body)
    cid = body.get("container", "box_2t")
    if not isinstance(cid, str):
        raise BadRequest("container は荷室の ID（文字列）で渡してください")
    stackable = body.get("stackable", True)
    if not isinstance(stackable, bool):
        raise BadRequest("stackable は true か false で渡してください")
    try:
        container = loadplan.container_by_id(cid)
        items = loadplan.items_from_shipment(s, stackable)
        res = loadplan.plan_load(items, container)
    except ValueError as e:
        raise BadRequest(str(e)) from e
    res["containers"] = [c.to_dict() for c in loadplan.containers()]
    res["items"] = [{"count": it.count, "length_cm": it.length_cm, "width_cm": it.width_cm,
                     "height_cm": it.height_cm, "weight_kg": it.weight_kg, "stackable": it.stackable} for it in items]
    return res


def api_request(body: Any) -> dict[str, Any]:
    body = _need_dict(body)
    s = _shipment_of(body)
    scenario = body.get("scenario")
    if scenario is not None and not isinstance(scenario, dict):
        raise BadRequest("scenario は辞書か null で渡してください")
    draft = build_request(s, scenario)
    contacts = contacts_for_rows(estimate_all(s))
    return {
        "status": draft.status,
        "missing_required": draft.missing_required,
        "missing_optional": draft.missing_optional,
        "text": draft.text,
        "contacts": [{"mode": c.mode, "mode_label": c.mode_label, "name": c.name, "url": c.url,
                      "condition": c.condition, "checked_on": c.checked_on, "has_window": c.has_window}
                     for c in contacts],
    }


def evaluation_dict(e) -> dict[str, Any]:
    q = e.quote
    return {
        "quote": quote_to_dict(q),
        "state": e.state,
        "total_ex_tax": e.total_ex_tax,
        "partial_ex_tax": e.partial_ex_tax,
        "unknown_keys": list(e.unknown_keys),
        "reasons": list(e.reasons),
        "rank": e.rank,
        "diff_from_lowest": e.diff_from_lowest,
        "summary": e.summary,
        "line_amounts_ex_tax": line_amounts_ex_tax(q),
    }


def api_compare(body: Any) -> dict[str, Any]:
    body = _need_dict(body)
    quotes = _quotes_of(body)
    version = _int_of(body.get("version"), "version（条件の版）")
    today = _parse_date(body["today"], "today") if body.get("today") is not None else date.today()
    cmp = compare_quotes(quotes, version, today)
    return {
        "message": cmp.message,
        "notice": ORDER_NOTICE,
        "ranking_shown": cmp.ranking_shown,
        "today": today.isoformat(),
        "cost_keys": [{"key": k, "label": v} for k, v in COST_KEYS.items()],
        "evaluations": [evaluation_dict(e) for e in cmp.evaluations],
    }


def api_save(body: Any) -> dict[str, Any]:
    body = dict(_need_dict(body, "案件"))
    body.setdefault("schema_version", 2)
    case = _case_of(body)
    case.saved_at = datetime.now().isoformat(timespec="seconds")
    return case_to_dict(case)


def api_load(body: Any) -> dict[str, Any]:
    return case_to_dict(_case_of(_need_dict(body, "保存ファイル")))


def _scenario_of(sid: Any) -> dict[str, Any]:
    if not isinstance(sid, str) or not re.fullmatch(r"S\d{1,3}", sid):
        raise BadRequest("場面の id は S1〜S5 の形で渡してください")
    try:
        return load_scenario(sid)
    except FileNotFoundError as e:
        raise BadRequest(f"場面 {sid} が見つかりません") from e


def _next_quote_id(case) -> str:
    nums = [int(q.id[1:]) for q in case.quotes if q.id.startswith("q") and q.id[1:].isdigit()]
    return f"q{max(nums, default=0) + 1}"


def _case_field(body: dict[str, Any]):
    return _case_of(dict(_need_dict(body.get("case"), "case（案件）"), schema_version=2))


def api_confirm(body: Any) -> dict[str, Any]:
    body = _need_dict(body)
    case = _case_field(body)
    s = _shipment_of(body)
    sid = body.get("scenario_id")
    sc = _scenario_of(sid) if sid is not None else None
    info = scenario_info(sc, modified=s != sc["shipment"]) if sc is not None else None
    before = case.current_version
    case.confirm(s, datetime.now().isoformat(timespec="seconds"), info)
    return {"case": case_to_dict(case), "changed": case.current_version != before}


def api_add_quote(body: Any) -> dict[str, Any]:
    body = _need_dict(body)
    case = _case_field(body)
    if not case.versions:
        raise BadRequest("先に条件を確定してください")
    raw = _need_dict(body.get("quote"), "quote（見積回答）")
    if not isinstance(raw.get("carrier"), str) or not raw["carrier"].strip():
        raise BadRequest("会社名を入れてください")
    for line in (raw.get("lines") or {}).values():
        if isinstance(line, dict) and line.get("kind") == KIND_AMOUNT:
            a = line.get("amount")
            if isinstance(a, bool) or not isinstance(a, int) or a < 0:
                raise BadRequest("金額は 0 以上の整数で入れてください")
    qid = _next_quote_id(case)
    data = dict(raw, id=qid, version=case.current_version, carrier=raw["carrier"].strip(),
                product=(raw.get("product") or "").strip() if isinstance(raw.get("product", ""), str) else "")
    data.setdefault("tax", "税抜")
    data["is_demo"] = bool(raw.get("is_demo", False))
    case.quotes.extend(_quotes_of({"quotes": [data]}))
    return {"case": case_to_dict(case), "quote_id": qid}


def api_add_demo_quotes(body: Any) -> dict[str, Any]:
    body = _need_dict(body)
    case = _case_field(body)
    info = case.scenario
    if not case.versions or not info or not info.get("id") or info.get("modified"):
        raise BadRequest("架空の回答例を入れられるのは、場面を変えずに確定した条件の版だけです")
    sc = _scenario_of(info["id"])
    if not sc.get("demo_quotes"):
        raise BadRequest(f"場面 {info['id']} に架空の回答例はありません")
    today = _parse_date(body["today"], "today") if body.get("today") is not None else date.today()
    added = 0
    for q in demo_quotes_for(sc, case.current_version, today):
        if any(x.is_demo and x.carrier == q.carrier and x.product == q.product and x.version == q.version
               for x in case.quotes):
            continue  # 同じ版に同じ例があれば二重に足さない
        q.id = _next_quote_id(case)
        case.quotes.append(q)
        added += 1
    return {"case": case_to_dict(case), "added": added}


def _case_of(body: dict[str, Any]):
    try:
        return case_from_dict(body)
    except ValueError as e:
        raise BadRequest(str(e)) from e
    except (KeyError, TypeError, AttributeError) as e:
        raise BadRequest(f"案件の形が違います（{type(e).__name__}: {e}）") from e


# ---------------------------------------------------------------- B 空きを探す・つなぐ

BOARD_B = spaceb.Board()  # サーバーの memory。再起動で初期の架空データに戻る（手元の版は全員で 1 つ）


class BoardStore:
    """公開版の来訪者ごとの盤。LRU で SESSION_MAX 件まで、使われない盤は SESSION_TTL_SEC で捨てる。"""

    def __init__(self, max_items: int = SESSION_MAX, ttl: float = SESSION_TTL_SEC, clock=time.monotonic):
        self._d: collections.OrderedDict[str, tuple[spaceb.Board, float]] = collections.OrderedDict()
        self._max, self._ttl, self._clock = max_items, ttl, clock
        self._lock = threading.Lock()

    def get(self, sid: str) -> spaceb.Board:
        now = self._clock()
        with self._lock:
            for k in [k for k, (_, t) in self._d.items() if now - t > self._ttl]:
                del self._d[k]
            if sid in self._d:
                board = self._d[sid][0]
            else:
                board = spaceb.Board()
            self._d[sid] = (board, now)
            self._d.move_to_end(sid)
            while len(self._d) > self._max:
                self._d.popitem(last=False)
            return board

    def __len__(self) -> int:
        return len(self._d)


BOARDS = BoardStore()


def _board() -> spaceb.Board:
    return BOARDS.get(_CTX.sid) if _CTX.public else BOARD_B


def session_id_of(value: str | None) -> str:
    """X-Demo-Session の値。形が違う・無いときは全員共通の "anon"。"""
    return value if value and SESSION_ID_RE.fullmatch(value) else ANON_SESSION


def _b_shipment(body: dict[str, Any]) -> spaceb.BShipment:
    try:
        return spaceb.parse_shipment(body)
    except ValueError as e:
        raise BadRequest(str(e)) from e


def _b_notices() -> dict[str, Any]:
    return {"fiction_notice": spaceb.FICTION_NOTICE, "booking_notice": spaceb.BOOKING_NOTICE}


ROUTE_BASE = None  # 試験で data_dir の代わりを指す（cache の置き場所）
ROUTE_FAIL_MESSAGE = "経路を取得できませんでした"


def _lonlat_param(text: str, name: str) -> tuple[float, float]:
    try:
        lon, lat = (float(v) for v in text.split(","))
    except ValueError as e:
        raise BadRequest(f"{name}は 経度,緯度 の形で渡してください") from e
    if not (120.0 <= lon <= 155.0 and 20.0 <= lat <= 50.0):
        raise BadRequest(f"{name}が日本の範囲の外です")
    return lon, lat


def api_route(query: dict[str, list[str]]) -> dict[str, Any]:
    """トラックの道路の経路。from は出発地の市区町村コード（from_ll があれば、その地点＝貨物駅などから）、to は着地のコード。"""
    def one(key: str) -> str:
        v = (query.get(key) or [""])[0]
        if not v:
            raise BadRequest(f"{key} がありません")
        return v

    points = geo.load_points()
    to_code = one("to")
    if to_code not in points:
        raise BadRequest(f"着地のコード {to_code!r} が見つかりません")
    dest = (points[to_code]["lon"], points[to_code]["lat"])
    if query.get("from_ll"):
        origin = _lonlat_param(query["from_ll"][0], "from_ll")
    else:
        from_code = one("from")
        if from_code not in points:
            raise BadRequest(f"出発地のコード {from_code!r} が見つかりません")
        origin = (points[from_code]["lon"], points[from_code]["lat"])
    if abs(origin[0] - dest[0]) < 1e-6 and abs(origin[1] - dest[1]) < 1e-6:
        raise BadRequest("出発地と着地が同じ場所です")
    r = route_lib.route_on_demand(origin, dest, base=ROUTE_BASE, cache_only=_CTX.public)  # 公開版は OSRM に問い合わせず、書き込みもしない
    if r is None:
        fb = route_lib.fallback_arc(origin, dest)
        return {"ok": False, "fallback": True, "message": ROUTE_FAIL_MESSAGE, "km": fb["km"], "minutes": None,
                "coordinates": fb["coordinates"], "cached": False}
    return {"ok": True, "fallback": False, "message": None, "km": r["km"], "minutes": r["minutes"],
            "coordinates": r["coordinates"], "cached": bool(r.get("cached"))}


def api_b_trips(query: dict[str, list[str]]) -> dict[str, Any]:
    demo = spaceb.load_demo()
    return {"note": demo["note"], "carriers": demo["carriers"], "trips": _board().trips(),
            "holds": _board().holds(), **_b_notices()}


def api_b_match(body: Any) -> dict[str, Any]:
    body = _need_dict(body)
    return {"results": _board().match_all(_b_shipment(body)), **_b_notices()}


def api_b_hold(body: Any) -> dict[str, Any]:
    body = _need_dict(body)
    s = _b_shipment(body)
    try:
        shipper = body.get("shipper")
        if shipper is not None and not (isinstance(shipper, dict) and all(isinstance(v, str) for v in shipper.values())):
            raise spaceb.BoardError("shipper（荷主の名称・住所・法人番号）は文字列の辞書で入れてください")
        hold = _board().hold(body.get("trip_id"), s, body.get("hold_minutes", spaceb.DEFAULT_HOLD_MINUTES),
                             hold_until=body.get("hold_until"), shipper=shipper)
    except spaceb.BoardError as e:
        raise BadRequest(str(e)) from e
    return {"hold": hold, **_b_notices()}


def api_b_contract(body: Any) -> dict[str, Any]:
    body = _need_dict(body)
    try:
        return {"contract": _board().contract(body.get("hold_id"), body.get("fuel_surcharge"), body.get("payment_method")),
                **_b_notices()}
    except spaceb.BoardError as e:
        raise BadRequest(str(e)) from e


def api_b_decide(body: Any) -> dict[str, Any]:
    body = _need_dict(body)
    try:
        hold = _board().decide(body.get("hold_id"), body.get("decision"))
    except spaceb.BoardError as e:
        raise BadRequest(str(e)) from e
    return {"hold": hold, **_b_notices()}


def api_b_price_formula(body: Any) -> dict[str, Any]:
    """帰り便の値段の式（SPEC §21.6）。基準積載率は 0〜1（既定 0.7）、リードタイムの割引は 0〜0.5 の 0.1 刻み、地域は 近畿 / 関東。"""
    body = _need_dict(body)
    trip = next((t for t in spaceb.load_trips() if t["id"] == body.get("trip_id")), None)
    if trip is None:
        raise BadRequest(f"便 {body.get('trip_id')!r} が見つかりません")
    s = _b_shipment({**body, "dest_code": body.get("dest_code") or trip["dest_code"]})
    carrier = next((c for c in spaceb.load_demo()["carriers"] if c["id"] == trip["carrier_id"]), {})
    try:
        ref = spaceb.per_unit_reference(trip, s, body.get("base_load_rate", spaceb.DEFAULT_BASE_LOAD_RATE),
                                        body.get("lead_discount", 0), body.get("region") or carrier.get("tariff_region") or "関東",
                                        min_fee_yen=body.get("min_fee_yen"))
    except ValueError as e:
        raise BadRequest(str(e)) from e
    return {"reference": ref, **_b_notices()}


def api_b_reset(body: Any) -> dict[str, Any]:
    _need_dict(body)
    _board().reset()
    return {"ok": True}


def api_waiting_calc(body: Any) -> dict[str, Any]:
    """荷待ち・荷役の記録の計算（SPEC §23.4）。記録はファイルにもログにも書かず、応答を返したら捨てる。
    rows は行の辞書の配列。rows の代わりに text（Excel から貼り付けたタブ区切り）も受ける。"""
    body = _need_dict(body)
    year = body.get("year")
    if year is not None and (isinstance(year, bool) or not isinstance(year, int)):
        raise BadRequest("year は整数で渡してください")
    pasted = None
    defaulted: list[str] = []
    notes: list[str] = []
    if body.get("rows") is not None:
        rows = body["rows"]
    elif isinstance(body.get("text"), str):
        rows, defaulted, notes = waiting.parse_paste_ex(body["text"])
        pasted = rows
    else:
        raise BadRequest("rows（行の配列）か text（貼り付けたタブ区切り）を渡してください")
    try:
        res = waiting.calc(rows, year)
    except ValueError as e:
        raise BadRequest(str(e)) from e
    if pasted is not None:
        res["input_rows"] = pasted  # 画面の表に入れ直すための、貼り付けを読んだ行（見出しの順に従った結果）
        res["defaulted"] = defaulted  # 欄が無くて既定値を入れた列（shipper_fault）。画面が注記を出す
        res["notes"] = notes  # 読まなかった見出し、列の順で読んだこと、などの注記（SPEC §23.8）
    return res


GET_ROUTES = {"/api/scenarios": lambda q: api_scenarios(), "/api/demo_quotes": api_demo_quotes,
              "/api/b/trips": api_b_trips, "/api/route": api_route}
POST_ROUTES = {
    "/api/estimate": api_estimate, "/api/layer": api_layer, "/api/loadplan": api_loadplan, "/api/request": api_request,
    "/api/waiting/calc": api_waiting_calc,
    "/api/compare": api_compare, "/api/save": api_save, "/api/load": api_load,
    "/api/confirm": api_confirm, "/api/add_quote": api_add_quote, "/api/add_demo_quotes": api_add_demo_quotes,
    "/api/b/match": api_b_match, "/api/b/hold": api_b_hold, "/api/b/decide": api_b_decide, "/api/b/reset": api_b_reset,
    "/api/b/contract": api_b_contract, "/api/b/price_formula": api_b_price_formula,
}


# ---------------------------------------------------------------- HTTP

_STATIC_CACHE: dict[tuple[str, int, int], bytes] = {}
_STATIC_CACHE_BYTES = 0
_STATIC_LOCK = threading.Lock()


def _static_bytes(path: Path, st, ext: str, accept_gzip: bool) -> tuple[bytes, bool]:
    """(本文, gzip したか)。gzip した本文は (パス, 更新時刻, 大きさ) をキーにメモリへ置いて使い回す。"""
    global _STATIC_CACHE_BYTES
    if not accept_gzip or ext not in GZIP_EXTS:
        return path.read_bytes(), False
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _STATIC_LOCK:
        hit = _STATIC_CACHE.get(key)
    if hit is not None:
        return hit, True
    data = gzip.compress(path.read_bytes(), compresslevel=6, mtime=0)
    with _STATIC_LOCK:
        if _STATIC_CACHE_BYTES + len(data) <= MAX_STATIC_CACHE:
            _STATIC_CACHE[key] = data
            _STATIC_CACHE_BYTES += len(data)
    return data, True


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript", ".json": "application/json",
                      ".geojson": "application/geo+json", ".css": "text/css", ".html": "text/html",
                      ".glb": "model/gltf-binary", ".svg": "image/svg+xml"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:  # 通常のアクセスログは出さない
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    _cc: str | None = None  # 静的ファイルの Cache-Control（公開版）。無ければ no-store

    @property
    def public(self) -> bool:
        return bool(getattr(self.server, "public", False))

    def end_headers(self) -> None:
        self.send_header("Cache-Control", self._cc or "no-store")
        self._cc = None
        super().end_headers()

    def _enter(self) -> None:
        """リクエストの頭で、公開版かどうかと来訪者 ID を API の関数に渡す。"""
        _CTX.public = self.public
        _CTX.sid = session_id_of(self.headers.get(SESSION_HEADER))

    # --- 共通
    def _send_json(self, status: int, obj: Any) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self) -> bool:
        if self.public:  # 公開版は任意のホスト名で来る（Cloud Run の URL）
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ALLOWED_HOSTS

    def _guard(self) -> bool:
        self._enter()
        if not self._host_ok():
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "このサーバーは 127.0.0.1 または localhost からだけ使えます"})
            return False
        return True

    # --- GET
    def do_GET(self) -> None:  # noqa: N802
        if not self._guard():
            return
        parts = urlsplit(self.path)
        if parts.path.startswith("/api/"):
            fn = GET_ROUTES.get(parts.path)
            if fn is None:
                if parts.path in POST_ROUTES:
                    self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "この API は POST で呼んでください"})
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": f"API {parts.path} はありません"})
                return
            self._run(lambda: fn(parse_qs(parts.query)))
            return
        if self._static(parts.path, head=False):
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802
        if self.path.startswith("/api/"):
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
            return
        self._enter()
        if self._static(urlsplit(self.path).path, head=True):
            return
        super().do_HEAD()

    # --- 静的ファイル（公開版は gzip とキャッシュの指定つきで返す。手元の版は標準の動き）
    def _static(self, url_path: str, head: bool) -> bool:
        """処理したら True。標準の処理に任せるときは False。公開版と SHIPCOMPARE_HIDE_LOCAL=1 は web/data/local/ を 404 にする。"""
        if not (self.public or hide_local()):
            return False
        rel = unquote(url_path).lstrip("/")
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        full = (WEB_DIR / rel).resolve()
        try:
            sub = full.relative_to(WEB_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return True
        if sub.parts[:2] in (("data", "local"), ("data", "tariffs")) or not full.is_file():  # 各社の運賃の表は静的ファイルで返さない（SPEC §30.1）
            self.send_error(HTTPStatus.NOT_FOUND)
            return True
        if not self.public:  # local を隠すだけ。中身は標準の処理で返す
            return False
        st = full.stat()
        ext = full.suffix.lower()
        ctype = self.extensions_map.get(ext) or mimetypes.guess_type(str(full))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "application/geo+json"):
            ctype += "; charset=utf-8"
        body, gz = _static_bytes(full, st, ext, "gzip" in (self.headers.get("Accept-Encoding") or "").lower())
        etag = '"' + hashlib.md5(f"{st.st_mtime_ns}-{st.st_size}-{int(gz)}".encode()).hexdigest()[:16] + '"'
        self._cc = LONG_CACHE if sub.parts[0] in LONG_CACHE_DIRS else "no-cache"
        if self.headers.get("If-None-Match") == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.end_headers()
            return True
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Vary", "Accept-Encoding")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        if not head:
            self.wfile.write(body)
        return True

    # --- POST
    def do_POST(self) -> None:  # noqa: N802
        if not self._guard():
            return
        path = urlsplit(self.path).path
        fn = POST_ROUTES.get(path)
        if fn is None:
            if path in GET_ROUTES:
                self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "この API は GET で呼んでください"})
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"API {path} はありません"})
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Content-Type は application/json で送ってください"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Content-Length がありません"})
            return
        if length < 0 or length > MAX_BODY:
            self.close_connection = True
            remaining = min(max(length, 0), 8 * MAX_BODY)
            while remaining > 0:  # 相手が送り終わる前に閉じると、相手側で通信エラーになるので読み捨てる
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": f"本文が大きすぎます（{MAX_BODY:,} バイトまで）"})
            return
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"本文が JSON として読めません（{type(e).__name__}）"})
            return
        self._run(lambda: fn(body))

    def _run(self, call) -> None:
        try:
            result = call()
        except BadRequest as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return
        except (ValueError, TypeError, KeyError, ArithmeticError, AttributeError) as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"入力を処理できませんでした（{type(e).__name__}: {e}）"})
            return
        except Exception as e:  # noqa: BLE001
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"サーバーの内部エラーです（{type(e).__name__}）"})
            return
        self._send_json(HTTPStatus.OK, result)


def make_server(port: int = DEFAULT_PORT, verbose: bool = False, public: bool = False,
                host: str | None = None) -> ThreadingHTTPServer:
    """サーバーを作る（port=0 なら空いているポート）。手元の版は 127.0.0.1 だけ、公開版は 0.0.0.0 で待ち受ける。"""
    httpd = ThreadingHTTPServer((host or ("0.0.0.0" if public else "127.0.0.1"), port), Handler)
    httpd.daemon_threads = True
    httpd.verbose = verbose  # type: ignore[attr-defined]
    httpd.public = public  # type: ignore[attr-defined]
    return httpd


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="地図の画面の配信サーバー")
    ap.add_argument("--port", type=int, default=None,
                    help=f"既定 {DEFAULT_PORT}（公開版は環境変数 PORT、無ければ {PUBLIC_DEFAULT_PORT}）")
    ap.add_argument("--verbose", action="store_true", help="アクセスログを出す")
    ap.add_argument("--public", action="store_true",
                    help="公開版（0.0.0.0、OSRM に問い合わせない、来訪者ごとの状態、gzip）。SHIPCOMPARE_PUBLIC=1 でも有効")
    args = ap.parse_args()
    public = args.public or env_public()
    httpd = make_server(resolve_port(public, args.port), args.verbose, public)
    where = "0.0.0.0（公開版）" if public else "127.0.0.1"
    print(f"http://{where}:{httpd.server_address[1]}/  （終了は Ctrl+C）", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
