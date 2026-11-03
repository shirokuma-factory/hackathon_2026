"""地図の画面（web/）の配信サーバー（SPEC §15.1）。標準ライブラリだけで書く。

web/ の静的ファイルと JSON の API を出す。計算はすべて src/shipcompare の関数を呼び、
JS では運賃や順位を計算しない。127.0.0.1 だけで待ち受け、サーバーにファイルは書かない。

使い方:
    uv run python tools/serve_web.py            # http://127.0.0.1:8600/
    uv run python tools/serve_web.py --port 8700

API（POST は Content-Type: application/json の JSON。入力の誤りは 400 と日本語の理由）:
    GET  /api/scenarios                 場面 S1〜S5
    GET  /api/demo_quotes?id=S5&version=1[&today=2026-11-03]   架空の見積回答（id は空）
    POST /api/estimate   {shipment, origin_code?, dest_code?, route?, distance_km?}
    POST /api/layer      {origin_code, shipment}   荷物に合わせた貸切の額の層
    POST /api/request    {shipment, scenario?}
    POST /api/compare    {quotes, version, today?}
    POST /api/save       案件（case_to_dict の形）→ 正規化した案件 JSON（保存はブラウザのダウンロード）
    POST /api/load       保存した案件 JSON → 正規化した案件 JSON
  画面が案件の規則（版の増やし方、場面の情報、見積回答の id）を Python に任せるための補助:
    POST /api/confirm          {case, shipment, scenario_id?}  Case.confirm → {case, changed}
    POST /api/add_quote        {case, quote}                   id と版を付けて追加 → {case, quote_id}
    POST /api/add_demo_quotes  {case, today?}                  場面の架空の回答を追加 → {case, added}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import geo  # noqa: E402
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
    if oc not in geo.ORIGINS:
        raise BadRequest(f"出発地 {oc!r} は対応していません（関東の7か所だけです）")
    if not isinstance(dc, str) or dc not in geo.load_points():
        raise BadRequest(f"着地 {dc!r} の市区町村コードが見つかりません")
    info = geo.drive_info(oc, dc)
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
    oc = body.get("origin_code")
    if oc not in geo.ORIGINS:
        raise BadRequest(f"出発地 {oc!r} は対応していません（関東の7か所だけです）")
    s = _shipment_of(body)
    totals = summarize(s)
    kg = float(totals.weight_kg) if totals.ok and totals.weight_kg is not None else None
    codes = sorted(geo.load_points())
    fare = geo.fare_layer(oc, kg, load_tariff("kashikiri"), codes)
    present = [v for v in fare if v is not None]
    edges = geo.quantile_edges(present) if present else []
    cls = [geo.class_of(v, edges) if v is not None else None for v in fare]
    return {"origin_code": oc, "total_kg": kg, "fare": fare, "edges": edges, "cls": cls}


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


GET_ROUTES = {"/api/scenarios": lambda q: api_scenarios(), "/api/demo_quotes": api_demo_quotes}
POST_ROUTES = {
    "/api/estimate": api_estimate, "/api/layer": api_layer, "/api/request": api_request,
    "/api/compare": api_compare, "/api/save": api_save, "/api/load": api_load,
    "/api/confirm": api_confirm, "/api/add_quote": api_add_quote, "/api/add_demo_quotes": api_add_demo_quotes,
}


# ---------------------------------------------------------------- HTTP

class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript", ".json": "application/json",
                      ".geojson": "application/geo+json", ".css": "text/css", ".html": "text/html"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:  # 通常のアクセスログは出さない
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # --- 共通
    def _send_json(self, status: int, obj: Any) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ALLOWED_HOSTS

    def _guard(self) -> bool:
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
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802
        if self.path.startswith("/api/"):
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
            return
        super().do_HEAD()

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


def make_server(port: int = DEFAULT_PORT, verbose: bool = False) -> ThreadingHTTPServer:
    """127.0.0.1 だけで待ち受けるサーバーを作る（port=0 なら空いているポート）。"""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    httpd.verbose = verbose  # type: ignore[attr-defined]
    return httpd


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="地図の画面の配信サーバー")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--verbose", action="store_true", help="アクセスログを出す")
    args = ap.parse_args()
    httpd = make_server(args.port, args.verbose)
    print(f"http://127.0.0.1:{httpd.server_address[1]}/  （終了は Ctrl+C）", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
