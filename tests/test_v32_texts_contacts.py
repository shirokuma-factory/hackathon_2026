"""SPEC §32 #4・#5・#6・#7・#10（受入条件 G142 の一部）。画面の文字の試験。"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

from shipcompare import spaceb
from shipcompare.guide import CONTACT_POINTER, load_contacts, request_contacts
from shipcompare.storage import load_scenario, shipment_to_dict

from test_v3_web import post, srv  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


@pytest.fixture(scope="module")
def pub(srv):  # noqa: F811
    mod, _ = srv
    httpd = mod.make_server(0, public=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_4_public_request_has_no_contact_windows(pub):
    status, r = post(pub, "/api/request", {"shipment": shipment_to_dict(load_scenario("S1")["shipment"])})
    assert status == 200 and r["contacts"] == [] and r["contacts_note"] == CONTACT_POINTER
    rows, note = request_contacts(public=True)
    assert rows == [] and note == CONTACT_POINTER


def test_4_contact_conditions_have_no_product_name():
    text = json.dumps(load_contacts(), ensure_ascii=False)
    assert "こぐまアロー便" not in text
    for p in ("compare_lite.html", "publish/compare_lite_artifact.html"):
        assert "こぐまアロー便" not in (WEB / p).read_text(encoding="utf-8")


def test_5_rosen_amount_note_on_both_screens():
    for p in ("compare_lite.html", "publish/compare_lite_artifact.html"):
        h = (WEB / p).read_text(encoding="utf-8")
        assert "目安の額は出せません。契約単価を入れると額が出ます。" in h  # 文言の直し 2026-11-07
    src = (ROOT / "src" / "shipcompare" / "estimate.py").read_text(encoding="utf-8")
    assert "路線便の試算は、運賃表を持っている区間だけです" in src   # 日本語の直し 2026-11-08


def test_5_rosen_row_in_map_estimate_carries_the_note(pub):
    s = shipment_to_dict(load_scenario("S1")["shipment"])
    status, r = post(pub, "/api/estimate", {"shipment": s, "origin_code": "13104", "dest_code": "27128"})
    assert status == 200
    rosen = [x for x in r["rows"] if x["mode"] == "rosen"]
    assert rosen and any("運賃表を持っている区間だけです" in n for n in rosen[0]["notes"])   # 日本語の直し 2026-11-08


def test_6_partner_table_heading_says_only_this_partners_rows():
    for p in ("waiting.html", "publish/waiting_artifact.html"):
        h = (WEB / p).read_text(encoding="utf-8")
        assert "この相手の行だけ" in h and 'id="doc-pm-title"' in h
    assert "この相手の行だけ" in (WEB / "waiting_calc.js").read_text(encoding="utf-8")
    assert "doc-pm-title" in (WEB / "waiting_map.js").read_text(encoding="utf-8")


def test_7_no_spec_section_number_on_screen():
    assert "SPEC" not in spaceb.REGISTER_PRICE_BASIS and "国の個建運賃の式" in spaceb.REGISTER_PRICE_BASIS
    for path in (ROOT / "data").rglob("*.json"):
        if "tariffs" in path.parts or path.name in ("carriers.json", "mapping.json", "muni_estimate.json"):
            assert "SPEC §" not in path.read_text(encoding="utf-8"), path
    # 文字列リテラルの中の節番号（コメントとドキュメント文字列は対象外）
    pat = re.compile(r"""(['"`])[^'"`\n]*SPEC\s*§[^'"`\n]*\1""")
    for path in list(WEB.glob("*.js")) + [ROOT / "tools" / "serve_web.py"] + list((ROOT / "src" / "shipcompare").glob("*.py")):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//")[0] if path.suffix == ".js" else line.split("#")[0]
            if code.strip().startswith(('"""', "*", "/*")) or '"""' in code:
                continue
            assert not pat.search(code), f"{path.name}:{no}"


def test_10_readme_describes_public_mode():
    r = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "--public" in r and "PORT" in r and "docs/DEPLOY.md" in r
