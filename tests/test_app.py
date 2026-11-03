"""画面の動作確認（streamlit.testing.v1.AppTest）。data_editor は AppTest で編集できないため、場面の読み込みで入力する。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app" / "main.py")
COST_KEYS = ["pickup", "trunk", "delivery", "handling", "fuel", "highway", "other"]


def new_app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=60).run()


def load_scenario(at: AppTest, index: int) -> AppTest:
    """index: 1=S1 ... 4=S4。"""
    at.selectbox(key="scenario_select").select(f"S{index}").run()
    return at


def confirm(at: AppTest) -> AppTest:
    at.button(key="confirm").click().run()
    return at


def df_text(at: AppTest) -> str:
    return "\n".join(d.value.to_string() for d in at.dataframe)


def add_quote(at: AppTest, carrier: str, amount_each: int, unknown: tuple[str, ...] = ()) -> AppTest:
    at.text_input(key="q_carrier").set_value(carrier)
    at.text_input(key="q_product").set_value("路線便")
    for k in COST_KEYS:
        if k in unknown:
            at.selectbox(key=f"q_kind_{k}").select("unknown")
        else:
            at.selectbox(key=f"q_kind_{k}").select("amount")
            at.number_input(key=f"q_amt_{k}").set_value(amount_each)
    at.button(key="q_submit").click().run()
    return at


def test_app_loads_without_exception():
    at = new_app()
    assert not at.exception
    assert "公表運賃からの目安です" in " ".join(m.value for m in at.info)
    assert "Backfull" in at.title[0].value


def test_s1_confirm_shows_expected_amounts():
    at = confirm(load_scenario(new_app(), 1))
    assert not at.exception
    text = df_text(at)
    assert "10,200" in text and "14,291" in text
    assert any("条件の版 1" in m.value for m in at.markdown)


def test_confirm_same_content_keeps_version():
    at = confirm(load_scenario(new_app(), 1))
    confirm(at)
    assert at.session_state["case"].current_version == 1


def test_two_complete_quotes_show_ranking():
    at = confirm(load_scenario(new_app(), 1))
    add_quote(at, "A運輸", 1000)
    add_quote(at, "B急送", 2000)
    assert not at.exception
    text = df_text(at)
    assert "順位" in text and "最安との差額" in text
    assert "7,000" in text and "14,000" in text
    assert any("完全な回答2件を比べました" in m.value for m in at.markdown)


def test_incomplete_quote_not_ranked():
    at = confirm(load_scenario(new_app(), 1))
    add_quote(at, "A運輸", 1000)
    add_quote(at, "C便", 1500, unknown=("pickup", "delivery"))
    text = df_text(at)
    assert "順位" not in text
    assert "集荷・配達" in text


def test_condition_change_moves_quotes_to_history():
    at = confirm(load_scenario(new_app(), 1))
    add_quote(at, "A運輸", 1000)
    add_quote(at, "B急送", 2000)
    at.selectbox(key="f_pickup").select("持込").run()
    confirm(at)
    assert at.session_state["case"].current_version == 2
    md = " ".join(m.value for m in at.markdown)
    assert "条件変更前の回答" in md
    text = df_text(at)
    assert "A運輸" in text and "B急送" in text
    assert "順位" not in text
    assert "完全な回答がまだありません" in md


def test_delete_quote():
    at = confirm(load_scenario(new_app(), 1))
    add_quote(at, "A運輸", 1000)
    at.button(key="del_button").click().run()
    assert at.session_state["case"].quotes == []


def test_judgment_and_restore_roundtrip():
    from shipcompare.storage import case_from_dict, case_to_dict

    at = confirm(load_scenario(new_app(), 1))
    add_quote(at, "A運輸", 1000)
    add_quote(at, "B急送", 2000)
    at.selectbox(key="j_choice").select("q1").run()
    at.text_area(key="j_reason").set_value("安いため").run()
    at.text_area(key="j_issues").set_value("積付けの確認\n有効期限の確認").run()
    case = at.session_state["case"]
    assert case.chosen_quote_id == "q1" and case.open_issues == ["積付けの確認", "有効期限の確認"]

    saved = json.dumps(case_to_dict(case), ensure_ascii=False)

    # 新しいセッションに読み込ませて同じ状態になることを確かめる（file_uploader は AppTest で扱えないため、
    # コールバックと同じ変換を直接通す）
    restored = case_from_dict(json.loads(saved))
    assert restored.versions[-1].shipment == case.versions[-1].shipment
    assert [q.id for q in restored.quotes] == ["q1", "q2"]
    assert (restored.chosen_quote_id, restored.reason) == ("q1", "安いため")


def _restore_script():
    import importlib.util
    import sys

    import streamlit as st

    spec = importlib.util.spec_from_file_location("shipcompare_app_main", st.session_state.get("app_path") or sys.argv[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if "restored_once" not in st.session_state:
        st.session_state["restored_once"] = True
        mod.init_state()
        with open(st.session_state["payload_path"], "rb") as f:
            mod.apply_restore(f.read())
    mod.main()


def test_restore_gives_same_screen_state(tmp_path):
    from shipcompare.storage import case_to_dict

    at = confirm(load_scenario(new_app(), 1))
    add_quote(at, "A運輸", 1000)
    add_quote(at, "B急送", 2000)
    at.selectbox(key="j_choice").select("q2").run()
    at.text_area(key="j_reason").set_value("理由").run()
    original = at.session_state["case"]
    before_tables = df_text(at)
    payload = tmp_path / "case.json"
    payload.write_text(json.dumps(case_to_dict(original), ensure_ascii=False), encoding="utf-8")

    # file_uploader は AppTest で扱えないため、同じ関数 apply_restore に保存内容を通した新しい画面で確かめる
    at2 = AppTest.from_function(_restore_script, default_timeout=60)
    at2.session_state["app_path"] = APP
    at2.session_state["payload_path"] = str(payload)
    at2.run()
    assert not at2.exception
    restored = at2.session_state["case"]
    assert restored.versions[-1].shipment == original.versions[-1].shipment
    assert [q.id for q in restored.quotes] == ["q1", "q2"]
    assert at2.session_state["j_choice"] == "q2" and at2.session_state["j_reason"] == "理由"
    assert at2.selectbox(key="f_route").value == "kanto_kansai"
    assert df_text(at2) == before_tables


def test_restore_rejects_bad_file():
    from shipcompare.storage import case_from_dict

    with pytest.raises(ValueError):
        case_from_dict({"schema_version": 99})


def manual_keys(at: AppTest) -> list[str]:
    return [n.key for n in at.number_input if str(n.key).startswith("manual_")]


def manual_key(at: AppTest, suffix: str) -> str:
    return next(k for k in manual_keys(at) if k.endswith(suffix))


def tbl(at: AppTest):
    return next(d.value for d in at.dataframe if "手段" in d.value.columns)


def takuhai_amount(at: AppTest) -> str:
    df = tbl(at)
    return df[df["手段"].str.contains("宅配")]["目安額（税抜・円）"].iloc[0]


def test_manual_input_appears_only_for_route_without_table():
    at = confirm(load_scenario(new_app(), 3))  # S3 は kanto_kansai
    assert manual_keys(at) == []

    at.selectbox(key="f_route").select("kanto_tohoku").run()
    confirm(at)
    assert any(k.endswith("takuhai_ex_tax") for k in manual_keys(at))  # 宅配は関東→関西の表だけ持つ
    at.number_input(key=manual_key(at, "takuhai_ex_tax")).set_value(30000).run()
    assert "30,000" in df_text(at)


def test_manual_input_is_not_reused_after_route_or_scenario_change():
    at = confirm(load_scenario(new_app(), 1))
    at.selectbox(key="f_route").select("kanto_tohoku").run()
    confirm(at)
    at.number_input(key=manual_key(at, "takuhai_ex_tax")).set_value(20000).run()
    at.number_input(key=manual_key(at, "rosen_base_fare")).set_value(9000).run()
    assert takuhai_amount(at) == "20,000"

    # 区間を変えて確定し直す: 前の額は使わない
    at.selectbox(key="f_route").select("sapporo_tokyo").run()
    confirm(at)
    assert takuhai_amount(at) == ""
    assert "9,000" not in df_text(at)
    assert all(at.number_input(key=k).value == 0 for k in manual_keys(at))

    # 同じ区間に戻して確定し直しても、版が変わっているので前の額は残らない
    at.selectbox(key="f_route").select("kanto_tohoku").run()
    confirm(at)
    assert takuhai_amount(at) == ""

    # 場面を替えても残らない
    at.number_input(key=manual_key(at, "takuhai_ex_tax")).set_value(15000).run()
    load_scenario(at, 3)
    at.selectbox(key="f_route").select("kanto_tohoku").run()
    confirm(at)
    assert takuhai_amount(at) == ""
    assert "15,000" not in df_text(at)


def test_no_route_says_choose_route():
    at = load_scenario(new_app(), 1)
    at.selectbox(key="f_route").select(None).run()
    confirm(at)
    assert not at.exception
    assert any("区間を選んでください" in m.value for m in at.info)
    assert manual_keys(at) == []
    assert "この区間の表は持っていません" not in df_text(at)


def test_manual_row_shows_user_input_as_source():
    at = confirm(load_scenario(new_app(), 1))
    at.selectbox(key="f_route").select("kanto_tohoku").run()
    confirm(at)
    at.number_input(key=manual_key(at, "takuhai_ex_tax")).set_value(20000).run()
    df = tbl(at)
    row = df[df["手段"].str.contains("宅配")].iloc[0]
    assert row["出典"] == "利用者の入力" and row["確認日"] == ""


def test_estimate_tab_has_chart_and_notes():
    at = confirm(load_scenario(new_app(), 2))
    charts = at.get("vega_lite_chart")
    assert len(charts) == 1
    spec = json.loads(charts[0].proto.spec)
    assert spec["title"]["text"] == "公表運賃からの目安（税抜）"
    import pyarrow as pa

    table = pa.ipc.open_stream(charts[0].proto.datasets[0].data.data).read_all()
    data = table.to_pylist()
    assert all("宅配" not in r["手段"] for r in data)  # 使えないものは入れない
    amounts = [r["目安額"] for r in data]
    assert amounts == sorted(amounts)
    assert {r["状態"] for r in data} == {"公開条件に合う", "要確認"}
    assert next(r for r in data if "JITBOX" in r["手段"])["状態"] == "要確認"
    md = " ".join(m.value for m in at.markdown)
    assert "1個がボックスの内寸を超えます" in md


def judged_app():
    at = confirm(load_scenario(new_app(), 1))
    add_quote(at, "A運輸", 1000)
    add_quote(at, "B急送", 2000)
    at.selectbox(key="j_choice").select("q1").run()
    at.text_area(key="j_reason").set_value("安いため").run()
    return at


def test_expired_chosen_quote_keeps_decision_and_warns():
    from datetime import date

    at = judged_app()
    at.session_state["case"].quotes[0].valid_until = date(2020, 1, 1)
    at.run()
    assert not at.exception
    assert any("選んだ見積は有効期限が切れています" in w.value for w in at.warning)
    case = at.session_state["case"]
    assert case.chosen_quote_id == "q1" and case.reason == "安いため"
    assert at.session_state["j_choice"] == "q1"
    at.text_area(key="j_issues").set_value("確認").run()  # 保存前の編集でも判断は消えない
    assert at.session_state["case"].chosen_quote_id == "q1"


def test_included_line_with_amount_warns():
    at = confirm(load_scenario(new_app(), 1))
    at.text_input(key="q_carrier").set_value("A運輸")
    at.selectbox(key="q_kind_fuel").select("included")
    at.number_input(key="q_amt_fuel").set_value(500)
    at.button(key="q_submit").click().run()
    assert any("燃料" in w.value and "使っていません" in w.value for w in at.warning)


def test_all_included_quote_is_not_ranked_first():
    at = confirm(load_scenario(new_app(), 1))
    at.text_input(key="q_carrier").set_value("ゼロ運輸")
    for k in COST_KEYS:
        at.selectbox(key=f"q_kind_{k}").select("included")
    at.button(key="q_submit").click().run()
    assert "金額の項目がありません" in df_text(at)
    assert "完全な回答がまだありません" in " ".join(m.value for m in at.markdown)


@pytest.mark.parametrize("index", [1, 2, 3, 4])
def test_all_scenarios_render(index):
    at = confirm(load_scenario(new_app(), index))
    assert not at.exception
    assert "想定の荷主" in at.selectbox(key="scenario_select").options[index]


def run_restore(tmp_path, payload):
    path = tmp_path / "case.json"
    path.write_bytes(payload.encode("utf-8") if isinstance(payload, str) else payload)
    at = AppTest.from_function(_restore_script, default_timeout=60)
    at.session_state["app_path"] = APP
    at.session_state["payload_path"] = str(path)
    at.run()
    return at


def _saved_case_dict():
    from shipcompare.storage import case_to_dict

    at = judged_app()
    return case_to_dict(at.session_state["case"])


def test_restore_with_expired_chosen_quote_keeps_choice(tmp_path):
    d = _saved_case_dict()
    d["quotes"][0]["valid_until"] = "2020-01-01"
    at = run_restore(tmp_path, json.dumps(d, ensure_ascii=False))
    assert not at.exception
    assert at.session_state["case"].chosen_quote_id == "q1"
    assert at.session_state["j_choice"] == "q1" and at.session_state["j_reason"] == "安いため"
    assert any("選んだ見積は有効期限が切れています" in w.value for w in at.warning)


def _mut(f):
    def run(d):
        f(d)
        return json.dumps(d, ensure_ascii=False)
    return run


@pytest.mark.parametrize("make", [
    _mut(lambda d: d["versions"][0]["shipment"].update(items=["x", 1])),
    _mut(lambda d: d["versions"][0]["shipment"].update(destinations=0)),
    _mut(lambda d: d["versions"][0]["shipment"].update(route="mars")),
    _mut(lambda d: d["versions"][0].pop("shipment")),
    _mut(lambda d: d.update(quotes=[1, 2])),
    _mut(lambda d: d["quotes"][0].pop("tax")),
    lambda d: "[1, 2, 3]",
    lambda d: "null",
    lambda d: "{",
])
def test_restore_malformed_json_shows_error_not_crash(tmp_path, make):
    at = run_restore(tmp_path, make(_saved_case_dict()))
    assert not at.exception
    assert any("読み込めませんでした" in e.value for e in at.error)


def test_restore_integer_distance_does_not_crash(tmp_path):
    d = _saved_case_dict()
    d["versions"][0]["shipment"].update(route="other", distance_km=700, origin_in_kanto=True)
    at = run_restore(tmp_path, json.dumps(d, ensure_ascii=False))
    assert not at.exception
    assert at.session_state["f_dist"] == 700.0


def test_restore_invalid_utf8_shows_error(tmp_path):
    at = run_restore(tmp_path, b"\xff\xfe\x00")
    assert not at.exception
    assert any("読み込めませんでした" in e.value for e in at.error)
