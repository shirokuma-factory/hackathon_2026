"""スポット出荷 比較・依頼の画面（Streamlit）。計算は src/shipcompare の関数を呼ぶだけにする。"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from shipcompare.estimate import ST_CHECK, ST_OK, EstimateRow, estimate_all, summarize
from shipcompare.models import (
    COST_KEYS,
    KIND_AMOUNT,
    KIND_INCLUDED,
    KIND_UNKNOWN,
    ROUTE_OTHER,
    TAX_EX,
    TAX_IN,
    Case,
    CostLine,
    Item,
    QuoteResponse,
    Shipment,
)
from shipcompare.quotes import STATE_COMPLETE, STATE_HISTORY, STATE_INCOMPLETE, compare_quotes, line_amounts_ex_tax
from shipcompare.request import CONFIRM_ITEMS, build_request
from shipcompare.storage import ORDER_NOTICE, case_from_dict, case_to_dict, demo_quotes_for, list_scenarios, scenario_info
from shipcompare.tariffs import ROUTES

DISCLAIMER = "公表運賃からの目安です。加算や割増の一部は入っていません。最後は各社の見積で確かめてください。"
ITEM_COLUMNS = ["個数", "縦(cm)", "横(cm)", "高さ(cm)", "1個の重さ(kg)"]
PARTY_TYPES = [None, "法人", "個人事業主", "個人"]
KIND_LABELS = {KIND_AMOUNT: "金額", KIND_INCLUDED: "含む", KIND_UNKNOWN: "不明"}
MANUAL_LABELS = {
    "takuhai_ex_tax": "宅配（佐川急便）の税抜額（円）。佐川急便の頁で調べた額を入れてください",
    "rosen_base_fare": "路線便（日本通運）の基準運賃（円）。運賃表を見て入れてください",
}
NEW_INPUT = ""
STATUS_NOTE = "公開条件に合っても、その会社がこの出荷を受けるかは見積で確かめます。"
UNKNOWN_CELL = "不明"
INCLUDED_CELL = "含む"


# ------------------------------------------------------------------ 表示用の小さな関数

def _opt(options_map: dict, none_label: str = "選択してください"):
    return lambda v: none_label if v is None else options_map.get(v, str(v))


def tri_label(v: bool | None, yes: str, no: str) -> str:
    return "未入力" if v is None else (yes if v else no)


def fmt_yen(value: int | None) -> str:
    """金額は桁区切り。不明は 0 ではなく空欄にする。"""
    return "" if value is None else f"{value:,}"


def fmt_dec(d) -> str:
    return f"{d.normalize():f}" if d is not None else ""


def blank_items_df() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series([None], dtype="float64") for c in ITEM_COLUMNS})


def items_to_df(items: list[Item]) -> pd.DataFrame:
    if not items:
        return blank_items_df()
    rows = [[i.count, i.length_cm, i.width_cm, i.height_cm, i.weight_kg] for i in items]
    return pd.DataFrame(rows, columns=ITEM_COLUMNS).astype("float64")


def _num(v):
    if v is None or pd.isna(v):
        return None
    return v


def df_to_items(df: pd.DataFrame) -> list[Item]:
    """空の行は捨てる。一部だけ空の行は None を含む Item にする（不明として扱われる）。"""
    items: list[Item] = []
    for row in df.itertuples(index=False):
        vals = [_num(v) for v in row]
        if all(v is None for v in vals):
            continue
        count = vals[0]
        if count is not None and float(count).is_integer():
            count = int(count)
        items.append(Item(count, vals[1], vals[2], vals[3], vals[4]))
    return items


def basis_text(r: EstimateRow) -> str:
    """根拠欄の文章（計算重量・サイズ・本数・車格など）。"""
    d = r.detail
    parts: list[str] = []
    if r.mode == "takuhai" and "sizes" in d:
        parts.append("、".join(f"荷物{b['line']}行目 {b['size']}サイズ×{b['count']}個" for b in d["sizes"]))
        if d.get("dropoff_discount"):
            parts.append(f"持込で1個 {d['dropoff_discount']}円を引いています")
    elif r.mode == "rosen" and "weight_band_kg" in d:
        parts.append(
            f"計算重量 {fmt_dec(d['chargeable_kg'])}kg（合計重量 {fmt_dec(d['weight_kg'])}kg、"
            f"容積 {d['volume_m3']:.3f}m³）→ {d['weight_band_kg']}kg の段"
        )
        if "base_fare" in d:
            parts.append(f"基準運賃 {d['base_fare']:,}円")
    elif r.mode == "jitbox" and "boxes" in d:
        parts.append(f"最低 {d['boxes']} 本 × {d['fare_per_box']:,}円（{d['route_label']}）")
    elif r.mode.startswith("kashikiri") and "vehicle" in d:
        parts.append(f"{d['vehicle']}車（最大積載 {d['max_kg']:,}kg）、200km 超の加算 {d['steps']} 区分")
    parts += r.reasons
    return "。".join(p for p in parts if p)


def caution_text(r: EstimateRow) -> str:
    seen: list[str] = []
    for t in list(r.notes) + [i.message for i in r.issues]:
        if t and t not in seen and t not in r.reasons:
            seen.append(t)
    return " / ".join(seen)


def estimate_records(rows: list[EstimateRow]) -> pd.DataFrame:
    """表に出す列。長い根拠・注意は表に入れず、行ごとの折りたたみに出す。"""
    recs = []
    for r in rows:
        shown = r.has_amount()
        src = r.source
        recs.append({
            "手段": r.label,
            "状態": r.status,
            "目安額（税抜・円）": fmt_yen(r.amount) if shown else "",
            "最低（円）": fmt_yen(r.low) if shown else "",
            "最高（円）": fmt_yen(r.high) if shown else "",
            "出典": src.name if src else "",
            "出典の頁": (src.url or None) if src else None,
            "適用日": ((src.applies_from or "記載なし") if src.url else "") if src else "",
            "確認日": src.checked_on if src else "",
        })
    return pd.DataFrame(recs)


def chart_frame(rows: list[EstimateRow]) -> pd.DataFrame:
    """棒グラフに使う行。金額がある「公開条件に合う」「要確認」だけを、安い順に並べる。"""
    recs = [{"手段": r.label, "状態": r.status, "目安額": r.amount, "表示": f"{r.amount:,}円"}
            for r in rows if r.has_amount() and r.status in (ST_OK, ST_CHECK)]
    recs.sort(key=lambda x: x["目安額"])
    return pd.DataFrame(recs, columns=["手段", "状態", "目安額", "表示"])


def estimate_chart(df: pd.DataFrame) -> alt.LayerChart:
    order = list(df["手段"])
    top = int(df["目安額"].max() * 1.2)
    base = alt.Chart(df).encode(
        y=alt.Y("手段:N", sort=order, title=None, scale=alt.Scale(paddingInner=0.3),
                axis=alt.Axis(labelFontSize=15, labelLimit=360)),
    )
    bars = base.mark_bar().encode(
        x=alt.X("目安額:Q", title="目安額（税抜・円）", scale=alt.Scale(domain=[0, top]),
                axis=alt.Axis(format=",d", labelFontSize=13, titleFontSize=14)),
        color=alt.Color("状態:N", scale=alt.Scale(domain=[ST_OK, ST_CHECK], range=["#1f77b4", "#e08a00"]),
                        legend=alt.Legend(title="状態", orient="bottom", labelFontSize=14, titleFontSize=14)),
        tooltip=["手段", "状態", alt.Tooltip("目安額:Q", format=",d")],
    )
    labels = base.mark_text(align="left", dx=6, fontSize=16, fontWeight="bold").encode(
        x=alt.X("目安額:Q"), text="表示:N")
    return (bars + labels).properties(
        title=alt.TitleParams("公表運賃からの目安（税抜）", fontSize=20, anchor="start"),
        height=chart_height(len(df)),
    )


def chart_height(n_rows: int) -> int:
    """棒1本あたり 52px。画面側で高さを縮められないよう、st.altair_chart にも同じ値を渡す。"""
    return max(120, 52 * n_rows)


def carrier_label(q: QuoteResponse) -> str:
    """表に出す会社名。架空の回答には（架空）を付ける。"""
    return q.carrier + ("（架空）" if q.is_demo else "")


def breakdown_frame(evals) -> pd.DataFrame:
    """内訳の表。金額は税抜に直した円、含む・不明は文字。"""
    recs = []
    for ev in evals:
        q = ev.quote
        amounts = line_amounts_ex_tax(q)
        rec = {"会社": carrier_label(q), "商品": q.product}
        for k, name in COST_KEYS.items():
            line = q.lines.get(k)
            if k in amounts:
                rec[name] = f"{amounts[k]:,}"
            elif line is not None and line.kind == KIND_INCLUDED:
                rec[name] = INCLUDED_CELL
            else:
                rec[name] = UNKNOWN_CELL
        rec["合計（税抜・円）"] = f"{ev.total_ex_tax:,}" if ev.state == STATE_COMPLETE else f"{ev.partial_ex_tax:,}（部分）"
        recs.append(rec)
    return pd.DataFrame(recs)


def breakdown_chart_frame(complete_evals) -> pd.DataFrame:
    """積み上げ棒に使う行。完全な回答の「金額」行だけで、不明を 0 円にしない。"""
    recs = []
    for ev in complete_evals:
        label = f"{carrier_label(ev.quote)} {ev.quote.product}".strip()
        for k, v in line_amounts_ex_tax(ev.quote).items():
            recs.append({"回答": label, "項目": COST_KEYS[k], "金額": v, "合計": ev.total_ex_tax})
    return pd.DataFrame(recs, columns=["回答", "項目", "金額", "合計"])


def breakdown_chart(df: pd.DataFrame) -> alt.Chart:
    order = list(dict.fromkeys(df.sort_values("合計")["回答"]))
    return alt.Chart(df).mark_bar().encode(
        y=alt.Y("回答:N", sort=order, title=None, scale=alt.Scale(paddingInner=0.3),
                axis=alt.Axis(labelFontSize=15, labelLimit=360)),
        x=alt.X("金額:Q", stack="zero", title="費用の内訳（税抜・円）",
                axis=alt.Axis(format=",d", labelFontSize=13, titleFontSize=14)),
        color=alt.Color("項目:N", sort=list(COST_KEYS.values()),
                        legend=alt.Legend(title="項目", orient="bottom", labelFontSize=14, titleFontSize=14)),
        tooltip=["回答", "項目", alt.Tooltip("金額:Q", format=",d")],
    ).properties(
        title=alt.TitleParams("見積回答の内訳（完全な回答だけ）", fontSize=20, anchor="start"),
        height=chart_height(df["回答"].nunique()),
    )


# ------------------------------------------------------------------ 状態

def read_shipment() -> Shipment:
    """入力欄の今の値から Shipment を作る。"""
    ss = st.session_state
    dest = ss.get("f_dest")
    monthly = ss.get("f_monthly")
    return Shipment(
        items=df_to_items(ss["items_current"]),
        route=ss.get("f_route"),
        distance_km=ss.get("f_dist"),
        origin_in_kanto=ss.get("f_kanto"),
        destinations=int(dest) if dest is not None else None,
        sender_type=ss.get("f_sender"),
        receiver_type=ss.get("f_receiver"),
        pickup=ss.get("f_pickup"),
        delivery=ss.get("f_delivery"),
        ship_date=ss.get("f_ship"),
        desired_date=ss.get("f_desired"),
        forklift=ss.get("f_forklift"),
        tail_lift=ss.get("f_tail"),
        manual_load=ss.get("f_manual"),
        cargo_name=(ss.get("f_cargo") or None),
        fragile_note=(ss.get("f_fragile") or None),
        monthly_shipments=int(monthly) if monthly is not None else None,
    )


def set_form(s: Shipment) -> None:
    """入力欄の状態を Shipment の内容にする。コールバックの中か、最初の実行で呼ぶ。"""
    ss = st.session_state
    for k in [k for k in ss if str(k).startswith("manual_")]:
        del ss[k]  # 前の条件で入れた手入力の額を残さない
    ss["items_df"] = items_to_df(s.items)
    ss["items_current"] = ss["items_df"]
    ss["items_gen"] = ss.get("items_gen", 0) + 1
    ss["f_route"] = s.route
    ss["f_dist"] = s.distance_km
    ss["f_kanto"] = s.origin_in_kanto
    ss["f_dest"] = s.destinations
    ss["f_monthly"] = s.monthly_shipments
    ss["f_sender"] = s.sender_type
    ss["f_receiver"] = s.receiver_type
    ss["f_pickup"] = s.pickup
    ss["f_delivery"] = s.delivery
    ss["f_ship"] = s.ship_date
    ss["f_desired"] = s.desired_date
    ss["f_forklift"] = s.forklift
    ss["f_tail"] = s.tail_lift
    ss["f_manual"] = s.manual_load
    ss["f_cargo"] = s.cargo_name or ""
    ss["f_fragile"] = s.fragile_note or ""


def current_manual_inputs(case: Case) -> dict[int, dict[str, int]]:
    """手入力欄の値を版ごとにまとめる。キーは section_estimate の manual_v{版}_... の形で、手入力は現在の版にだけ残る。"""
    ver = case.current_version
    if ver is None:
        return {}
    out: dict[str, int] = {}
    for k, v in st.session_state.items():
        k = str(k)
        if not k.startswith(f"manual_v{ver}_"):
            continue
        for key in MANUAL_LABELS:
            if k.endswith("_" + key) and isinstance(v, int) and v > 0:
                out[key] = int(v)
    return {ver: out} if out else {}


def set_judgment(case: Case) -> None:
    ss = st.session_state
    ss["j_choice"] = case.chosen_quote_id
    ss["j_reason"] = case.reason
    ss["j_issues"] = "\n".join(case.open_issues)


def on_scenario_change() -> None:
    sid = st.session_state["scenario_select"]
    if sid == NEW_INPUT:
        set_form(Shipment())
        st.session_state["scenario_note"] = ""
        st.session_state["scenario_info"] = None
        return
    sc = next(s for s in list_scenarios() if s["id"] == sid)
    set_form(sc["shipment"])
    st.session_state["scenario_note"] = sc.get("assumption_note", "")
    st.session_state["scenario_info"] = scenario_info(sc)


def apply_restore(data: bytes) -> None:
    """保存した JSON の内容を画面の状態にする。失敗したら flash にメッセージを入れる。"""
    ss = st.session_state
    try:
        case = case_from_dict(json.loads(data.decode("utf-8")))
        shipment = case.versions[-1].shipment if case.versions else Shipment()
        items_to_df(shipment.items)  # 入力欄に入れられるかを、状態を書き換える前に確かめる
    except Exception as e:  # 壊れたファイルで画面を止めない
        ss["flash"] = ("error", f"読み込めませんでした。保存ファイルの内容を確かめてください（{e}）")
        return
    ss["case"] = case
    set_form(shipment)
    set_judgment(case)
    ss["scenario_select"] = NEW_INPUT
    ss["scenario_note"] = ""
    ss["scenario_info"] = case.scenario
    ss["flash"] = ("success", f"読み込みました（条件の版 {case.current_version or 0}、見積回答 {len(case.quotes)} 件）。")


def on_restore() -> None:
    up = st.session_state.get("restore_file")
    if up is None:
        st.session_state["flash"] = ("error", "読み込むファイルを選んでください。")
        return
    apply_restore(up.getvalue())


def delete_quote() -> None:
    qid = st.session_state.get("del_quote")
    case: Case = st.session_state["case"]
    case.quotes = [q for q in case.quotes if q.id != qid]
    if case.chosen_quote_id == qid:
        case.chosen_quote_id = None
        st.session_state["j_choice"] = None


def init_state() -> None:
    ss = st.session_state
    if "case" not in ss:
        ss["case"] = Case()
        set_form(Shipment())
        set_judgment(ss["case"])
        ss["scenario_select"] = NEW_INPUT
        ss["scenario_note"] = ""
        ss["scenario_info"] = None


# ------------------------------------------------------------------ 画面の各部分

def section_input(case: Case) -> None:
    ss = st.session_state
    scenarios = list_scenarios()
    labels = {NEW_INPUT: "新しく入力"}
    labels.update({s["id"]: f"{s['id']} {s['name']}（{s.get('label', '想定の荷主')}）" for s in scenarios})
    st.selectbox("場面を読み込む", list(labels), format_func=labels.get, key="scenario_select",
                 on_change=on_scenario_change)
    if ss.get("scenario_note"):
        st.caption("想定の置き方: " + ss["scenario_note"])

    st.markdown("**荷物**（空欄は不明として扱います。行は下の空行から追加できます）")
    edited = st.data_editor(
        ss["items_df"], num_rows="dynamic", hide_index=True, width="stretch",
        key=f"items_editor_{ss['items_gen']}",
        column_config={
            "個数": st.column_config.NumberColumn("個数", format="%g", step=1),
            "縦(cm)": st.column_config.NumberColumn("縦(cm)", format="%g"),
            "横(cm)": st.column_config.NumberColumn("横(cm)", format="%g"),
            "高さ(cm)": st.column_config.NumberColumn("高さ(cm)", format="%g"),
            "1個の重さ(kg)": st.column_config.NumberColumn("1個の重さ(kg)", format="%g"),
        },
    )
    ss["items_current"] = edited

    c1, c2, c3 = st.columns(3)
    route_labels = {rid: r.label for rid, r in ROUTES.items()}
    with c1:
        st.selectbox("発地域・着地域", [None, *ROUTES], format_func=_opt(route_labels), placeholder="選択してください", key="f_route")
        route = ROUTES.get(ss.get("f_route") or "")
        if route is not None and route.km is not None:
            st.caption(f"道路距離は {route.km:,}km（想定値）として計算します。")
        if ss.get("f_route") == ROUTE_OTHER:
            st.number_input("距離（km）", min_value=0.0, step=10.0, value=None, key="f_dist")
            st.selectbox("発地は関東ですか", [None, True, False],
                         format_func=lambda v: tri_label(v, "関東", "関東以外"), placeholder="選択してください", key="f_kanto")
        st.number_input("届け先の数", min_value=1, step=1, value=None, key="f_dest")
        st.number_input("月の出荷回数（任意）", min_value=1, step=1, value=None, key="f_monthly")
    with c2:
        st.selectbox("発送者の種別", PARTY_TYPES, format_func=_opt({}), placeholder="選択してください", key="f_sender")
        st.selectbox("受取人の種別", PARTY_TYPES, format_func=_opt({}), placeholder="選択してください", key="f_receiver")
        st.selectbox("集荷か持込か", [None, "集荷", "持込"], format_func=_opt({}), placeholder="選択してください", key="f_pickup")
        st.selectbox("配達か引取か", [None, "配達", "引取"], format_func=_opt({}), placeholder="選択してください", key="f_delivery")
    with c3:
        st.date_input("出荷日", value=None, key="f_ship")
        st.date_input("希望着日", value=None, key="f_desired")
        st.selectbox("フォークリフト", [None, True, False],
                     format_func=lambda v: tri_label(v, "あり", "なし"), placeholder="選択してください", key="f_forklift")
        st.selectbox("ゲート車", [None, True, False],
                     format_func=lambda v: tri_label(v, "必要", "不要"), placeholder="選択してください", key="f_tail")
        st.selectbox("手積み", [None, True, False],
                     format_func=lambda v: tri_label(v, "可", "不可"), placeholder="選択してください", key="f_manual")
    d1, d2 = st.columns(2)
    d1.text_input("品目", key="f_cargo")
    d2.text_input("壊れ物の扱い", key="f_fragile")

    if st.button("この条件で確定", type="primary", key="confirm"):
        before = case.current_version
        case.confirm(read_shipment(), datetime.now().isoformat(timespec="seconds"))
        if case.current_version == before:
            st.info("内容が前の版と同じなので、版は増やしませんでした。")

    if case.versions:
        st.markdown(f"**条件の版 {case.current_version}**（確定済み）")
        if read_shipment() != case.versions[-1].shipment:
            st.warning("入力が確定した版と違います。変えた内容を使うには「この条件で確定」を押してください。")
    else:
        st.markdown("**条件はまだ確定していません。**")


def section_estimate(case: Case) -> None:
    if not case.versions:
        st.info("先に「出荷条件」で条件を確定してください。")
        return
    shipment = case.versions[-1].shipment
    st.caption(f"条件の版 {case.current_version} の目安額です。")
    st.caption(STATUS_NOTE)

    totals = summarize(shipment)
    if totals.ok:
        m1, m2, m3 = st.columns(3)
        m1.metric("合計重量", f"{fmt_dec(totals.weight_kg)} kg")
        m2.metric("合計容積", f"{totals.volume_m3:.3f} m³")
        m3.metric("計算重量", f"{fmt_dec(totals.chargeable_kg)} kg")
    else:
        st.warning("計算できない項目があります: " + "、".join(totals.problems))

    if shipment.route is None:
        st.info("区間を選んでください。「1 出荷条件」で区間を選び、もう一度確定すると目安額が出ます。")

    # 表を持たない区間のときだけ手入力欄を出す。キーに条件の版・区間・距離・重量の段を入れ、
    # 条件が変わったら前の値を使わない
    need: dict[str, int | None] = {}
    for r in estimate_all(shipment):
        if r.needs_input:
            need[r.needs_input] = r.detail.get("weight_band_kg")
    manual: dict[str, int] = {}
    if need and shipment.route is not None:
        st.markdown("**手入力**（表を持たない区間です。入れた額で目安額を出します。0 のままなら未入力です）")
        for key, band in need.items():
            wkey = f"manual_v{case.current_version}_{shipment.route}_{shipment.distance_km}_{band}_{key}"
            v = st.number_input(MANUAL_LABELS[key], min_value=0, step=100, value=0, key=wkey)
            if v > 0:
                manual[key] = int(v)

    rows = estimate_all(shipment, manual)
    cf = chart_frame(rows)
    if not cf.empty:
        st.altair_chart(estimate_chart(cf), width="stretch", height=chart_height(len(cf)) + 110)
    st.dataframe(
        estimate_records(rows), hide_index=True, width="stretch",
        column_config={
            "出典の頁": st.column_config.LinkColumn("出典の頁", display_text="開く"),
            "手段": st.column_config.TextColumn("手段", width="large"),
            "目安額（税抜・円）": st.column_config.TextColumn("目安額（税抜・円）", width="medium"),
            "出典": st.column_config.TextColumn("出典", width="large"),
        },
    )
    st.caption("金額はすべて税抜です。空欄は不明で、0円ではありません。")
    st.markdown("**根拠と注意**（「公開条件に合う」以外は開いて表示しています）")
    for r in rows:
        with st.expander(f"{r.label}　{r.status}", expanded=r.status != ST_OK):
            st.markdown("**根拠**: " + (basis_text(r) or "なし"))
            st.markdown("**注意**: " + (caution_text(r) or "なし"))


def section_request(case: Case) -> None:
    if not case.versions:
        st.info("先に「出荷条件」で条件を確定してください。")
        return
    draft = build_request(case.versions[-1].shipment, case.scenario)
    st.markdown(f"**状態: {draft.status}**（条件の版 {case.current_version}）")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("不足項目")
        if draft.missing_required or draft.missing_optional:
            st.markdown("\n".join([f"- {m}（必須）" for m in draft.missing_required]
                                  + [f"- {m}" for m in draft.missing_optional]))
        else:
            st.markdown("- ありません")
    with c2:
        st.markdown("運送会社に確かめること")
        st.markdown("\n".join(f"- {c}" for c in CONFIRM_ITEMS))
    st.code(draft.text, language=None)
    st.download_button("依頼文を .txt で保存", data=draft.text.encode("utf-8"),
                       file_name="mitsumori_irai.txt", mime="text/plain", key="dl_request")
    st.caption("このツールは依頼文を送信しません。コピーして各社へ送ってください。")


def _next_quote_id(case: Case) -> str:
    nums = [int(q.id[1:]) for q in case.quotes if q.id.startswith("q") and q.id[1:].isdigit()]
    return f"q{max(nums, default=0) + 1}"


def section_quotes(case: Case) -> None:
    if not case.versions:
        st.info("先に「出荷条件」で条件を確定してください。")
        return
    today = date.today()
    st.markdown(f"見積回答は、いまの条件の版 {case.current_version} に結び付けて入れます。")

    info = st.session_state.get("scenario_info")
    sc = next((s for s in list_scenarios() if info and s["id"] == info.get("id")), None)
    if sc is not None and sc.get("demo_quotes"):
        if st.button("架空の回答例を入れる", key="add_demo_quotes"):
            added = 0
            for q in demo_quotes_for(sc, case.current_version, today):
                if any(x.is_demo and x.carrier == q.carrier and x.product == q.product and x.version == q.version
                       for x in case.quotes):
                    continue  # 同じ版に同じ例があれば二重に足さない
                q.id = _next_quote_id(case)
                case.quotes.append(q)
                added += 1
            if added:
                st.success(f"架空の回答例を {added} 件入れました。")
            else:
                st.info("この版には架空の回答例がすでに入っています。")

    with st.form("quote_form"):
        a, b, c, d, e = st.columns(5)
        carrier = a.text_input("会社名", key="q_carrier")
        product = b.text_input("商品名", key="q_product")
        received = c.date_input("受領日", value=today, key="q_received")
        valid = d.date_input("有効期限", value=today + timedelta(days=14), key="q_valid")
        tax = e.radio("税区分", [TAX_EX, TAX_IN], horizontal=True, key="q_tax")
        st.markdown("費用の内訳（金額、含む、不明から選びます）")
        cols = st.columns(len(COST_KEYS))
        for col, (k, name) in zip(cols, COST_KEYS.items()):
            with col:
                st.selectbox(name, list(KIND_LABELS), format_func=KIND_LABELS.get, index=2, key=f"q_kind_{k}")
                st.number_input(f"{name}の金額（円）", min_value=0, step=100, value=0, key=f"q_amt_{k}")
        submitted = st.form_submit_button("この回答を追加", key="q_submit")
    if submitted:
        if not carrier.strip():
            st.error("会社名を入れてください。")
        else:
            lines = {}
            for k in COST_KEYS:
                kind = st.session_state[f"q_kind_{k}"]
                entered = int(st.session_state[f"q_amt_{k}"])
                amount = entered if kind == KIND_AMOUNT else None
                if kind != KIND_AMOUNT and entered > 0:
                    st.warning(f"{COST_KEYS[k]}は「{KIND_LABELS[kind]}」が選ばれているため、入力した {entered:,}円 は使っていません。"
                               "金額を使うなら「金額」を選んで追加し直してください。")
                lines[k] = CostLine(kind, amount)
            case.quotes.append(QuoteResponse(
                id=_next_quote_id(case), carrier=carrier.strip(), product=product.strip(),
                version=case.current_version, received_on=received, valid_until=valid, tax=tax, lines=lines,
            ))
            st.success(f"{carrier.strip()} の回答を追加しました。")

    if not case.quotes:
        st.info("見積回答はまだありません。")
        return
    cmp = compare_quotes(case.quotes, case.current_version, today)
    st.markdown(f"**{cmp.message}**（基準日 {today.isoformat()}）")

    complete = cmp.by_state(STATE_COMPLETE)
    if complete:
        st.markdown("完全な回答")
        recs = []
        for ev in complete:
            rec = {}
            if cmp.ranking_shown:
                rec["順位"] = ev.rank
            rec.update({
                "会社": carrier_label(ev.quote), "商品": ev.quote.product,
                "合計（税抜・円）": fmt_yen(ev.total_ex_tax),
            })
            if cmp.ranking_shown:
                rec["最安との差額（円）"] = fmt_yen(ev.diff_from_lowest)
            rec.update({
                "入力時の税区分": ev.quote.tax,
                "受領日": ev.quote.received_on.isoformat() if ev.quote.received_on else "",
                "有効期限": ev.quote.valid_until.isoformat() if ev.quote.valid_until else "",
            })
            recs.append(rec)
        st.dataframe(pd.DataFrame(recs), hide_index=True, width="stretch")

    incomplete = cmp.by_state(STATE_INCOMPLETE)
    if incomplete:
        st.markdown("不完全な回答（順位に入れません）")
        st.dataframe(pd.DataFrame([{
            "会社": carrier_label(ev.quote), "商品": ev.quote.product,
            "部分合計（税抜・円）": fmt_yen(ev.partial_ex_tax),
            "不明な項目": "・".join(COST_KEYS[k] for k in ev.unknown_keys),
            "理由": "、".join(ev.reasons), "表示": ev.summary,
        } for ev in incomplete]), hide_index=True, width="stretch")

    history = cmp.by_state(STATE_HISTORY)
    if history:
        st.markdown("条件変更前の回答（履歴。順位に入れません）")
        st.dataframe(pd.DataFrame([{
            "会社": carrier_label(ev.quote), "商品": ev.quote.product, "回答の版": ev.quote.version,
            "部分合計（税抜・円）": fmt_yen(ev.partial_ex_tax), "理由": "、".join(ev.reasons),
        } for ev in history]), hide_index=True, width="stretch")

    current = complete + incomplete
    if current:
        st.markdown("費用の内訳（税抜に直した円。不明は赤で示します）")
        bdf = breakdown_frame(current)
        st.dataframe(
            bdf.style.map(lambda v: "background-color: #f8c9c9; color: #7a1010; font-weight: bold" if v == UNKNOWN_CELL else ""),
            hide_index=True, width="stretch",
        )
    if complete:
        cdf = breakdown_chart_frame(complete)
        if not cdf.empty:
            st.altair_chart(breakdown_chart(cdf), width="stretch",
                            height=chart_height(cdf["回答"].nunique()) + 110)
    if incomplete:
        st.caption("不完全な回答（不明の項目がある）はグラフに入れません。")

    qlabels = {q.id: f"{carrier_label(q)} {q.product}（版{q.version}）" for q in case.quotes}
    x, y = st.columns([3, 1], vertical_alignment="bottom")
    x.selectbox("削除する回答", list(qlabels), format_func=qlabels.get, key="del_quote")
    y.button("この回答を削除", key="del_button", on_click=delete_quote)


def section_judgment(case: Case) -> None:
    ss = st.session_state
    flash = ss.pop("flash", None)
    if flash:
        (st.success if flash[0] == "success" else st.error)(flash[1])

    if case.versions:
        cmp = compare_quotes(case.quotes, case.current_version, date.today())
        complete = {e.quote.id: e for e in cmp.by_state(STATE_COMPLETE)}
    else:
        complete = {}
    labels = {None: "選ばない"}
    labels.update({i: f"{carrier_label(e.quote)} {e.quote.product}　{e.total_ex_tax:,}円（税抜）" for i, e in complete.items()})
    chosen = ss.get("j_choice")
    if chosen is not None and chosen not in complete:
        # 選んだ回答が完全でなくなっても、判断は消さずに残して警告する
        q = next((x for x in case.quotes if x.id == chosen), None)
        if q is None:
            st.warning("選んだ回答が見積回答の一覧にありません。")
            ss["j_choice"] = None
        else:
            if q.valid_until is not None and q.valid_until < date.today():
                st.warning("選んだ見積は有効期限が切れています")
            elif case.versions and q.version != case.current_version:
                st.warning("選んだ回答は条件変更前のものです。いまの条件で見積を取り直してください。")
            else:
                st.warning("選んだ回答は、いまは完全な回答ではありません。「4 見積回答」で内容を確かめてください。")
            labels[chosen] = f"{carrier_label(q)} {q.product}（選択済み。いまは完全な回答ではありません）"
    st.info(ORDER_NOTICE)
    st.selectbox("選ぶ回答（完全な回答だけ選べます）", list(labels), format_func=labels.get,
                 placeholder="選ばない", key="j_choice")
    st.text_area("選んだ理由", key="j_reason", height=100)
    st.text_area("残る確認事項（1行に1つ）", key="j_issues", height=100)
    case.chosen_quote_id = ss["j_choice"]
    case.reason = ss["j_reason"]
    case.open_issues = [ln.strip() for ln in ss["j_issues"].splitlines() if ln.strip()]

    st.divider()
    s1, s2 = st.columns(2)
    with s1:
        st.markdown("**保存**")
        snap = dataclasses.replace(case, saved_at=datetime.now().isoformat(timespec="seconds"))
        text = json.dumps(case_to_dict(snap, current_manual_inputs(case)), ensure_ascii=False, indent=2) + "\n"
        st.download_button("この出荷を JSON で保存", data=text.encode("utf-8"),
                           file_name=f"shipment_case_{date.today():%Y%m%d}.json", mime="application/json",
                           key="dl_case")
    with s2:
        st.markdown("**読み込み**")
        st.file_uploader("保存した JSON", type=["json"], key="restore_file")
        st.button("この内容を読み込む", key="restore", on_click=on_restore)


# ------------------------------------------------------------------ 組み立て

def main() -> None:
    st.set_page_config(page_title="スポット出荷 比較・依頼", layout="wide")
    init_state()
    case: Case = st.session_state["case"]
    case.scenario = st.session_state.get("scenario_info")

    st.title("スポット出荷 比較・依頼")
    st.markdown("宅配便には大きく、貸切には小さい荷について、使える輸送手段と目安額を同じ条件で並べ、"
                "見積依頼文を作り、届いた見積回答を比べて保存します。")
    st.info(DISCLAIMER)

    t1, t2, t3, t4, t5 = st.tabs(["1 出荷条件", "2 目安額", "3 見積依頼文", "4 見積回答", "5 判断と保存"])
    with t1:
        section_input(case)
    with t2:
        section_estimate(case)
    with t3:
        section_request(case)
    with t4:
        section_quotes(case)
    with t5:
        section_judgment(case)


if __name__ == "__main__":
    main()
