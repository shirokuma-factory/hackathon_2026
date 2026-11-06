"""SPEC §36 部品として使えるようにする（受入条件 G150〜G153）。

- G151: examples/kit/data/*.json が web/kit/schema/*.json に合う（jsonschema があればそれで、無ければ自前の簡単な検査で）。
- G150: examples/kit/index.html を静的サーバー（この試験の中で起動）から開き、5 つの部品が描かれ、コンソールの誤り・警告が 0 件（ヘッドレス Edge、1920 と 400）。
- G152: update と destroy が働き、destroy の後に地図の層・DOM が残らない。
- G153: 今のアプリの試験は別のファイルが受け持つ。ここでは既存の web/*.js を部品が書き換えていないこと（部品は web/kit/ だけ）を文字列で確かめる。
Edge が無い環境だけ画面の試験を飛ばす（起動の時間切れは失敗。CDN に届かないと確かめたときだけ飛ばす）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import pytest
from conftest import edge_page, startup_failed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

KIT = ROOT / "web" / "kit"
SCHEMA = KIT / "schema"
DATA = ROOT / "examples" / "kit" / "data"
PAIRS = {  # データのファイル → 形のファイル
    "flows.json": "flow.json", "legs.json": "leg.json", "points.json": "point.json",
    "trips.json": "trip.json", "vehicle.json": "vehicle.json", "placement.json": "placement.json",
    "items.json": "item.json", "waiting_records.json": "waiting_record.json", "sites.json": "site.json",  # 設計 §4 の荷・荷待ちの記録・拠点（SPEC §40）
}
MODULES = ["kit", "flows", "legs", "points", "timeline", "load3d"]


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 自前の簡単な検査（jsonschema が無いとき用）
def mini_validate(inst, schema, root=None, path="$") -> list[str]:
    """required・type・enum・minimum・exclusiveMinimum・minLength・minItems・pattern・properties・items・$ref だけを見る。"""
    root = root or schema
    errs: list[str] = []
    if "$ref" in schema:
        ref = schema["$ref"]
        assert ref.startswith("#/"), ref
        node = root
        for part in ref[2:].split("/"):
            node = node[part]
        return mini_validate(inst, node, root, path)
    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        ok = False
        for x in types:
            ok = ok or (x == "object" and isinstance(inst, dict)) or (x == "array" and isinstance(inst, list)) or (x == "string" and isinstance(inst, str)) \
                or (x == "boolean" and isinstance(inst, bool)) or (x == "null" and inst is None) \
                or (x == "integer" and isinstance(inst, int) and not isinstance(inst, bool)) \
                or (x == "number" and isinstance(inst, (int, float)) and not isinstance(inst, bool))
        if not ok:
            return [f"{path}: 型が {types} ではありません（{type(inst).__name__}）"]
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {inst!r} は {schema['enum']} のどれでもありません")
    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in schema and inst < schema["minimum"]:
            errs.append(f"{path}: {inst} は最小 {schema['minimum']} より小さい")
        if "maximum" in schema and inst > schema["maximum"]:
            errs.append(f"{path}: {inst} は最大 {schema['maximum']} より大きい")
        if "exclusiveMinimum" in schema and inst <= schema["exclusiveMinimum"]:
            errs.append(f"{path}: {inst} は {schema['exclusiveMinimum']} より大きくなければなりません")
    if isinstance(inst, str):
        if "minLength" in schema and len(inst) < schema["minLength"]:
            errs.append(f"{path}: 文字列が短すぎます")
        if "pattern" in schema and not re.search(schema["pattern"], inst):
            errs.append(f"{path}: {inst!r} が形に合いません")
    if isinstance(inst, dict):
        for k in schema.get("required", []):
            if k not in inst:
                errs.append(f"{path}: 必須の欄 {k} がありません")
        for k, sub in schema.get("properties", {}).items():
            if k in inst:
                errs += mini_validate(inst[k], sub, root, f"{path}.{k}")
    if isinstance(inst, list):
        if "minItems" in schema and len(inst) < schema["minItems"]:
            errs.append(f"{path}: 要素が少なすぎます")
        if "maxItems" in schema and len(inst) > schema["maxItems"]:
            errs.append(f"{path}: 要素が多すぎます")
        if "items" in schema:
            for i, x in enumerate(inst):
                errs += mini_validate(x, schema["items"], root, f"{path}[{i}]")
    return errs


def _errors(inst, schema) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return mini_validate(inst, schema)
    v = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in v.iter_errors(inst)]


# ---------------------------------------------------------------- G151 データの形
def test_every_module_schema_and_example_file_exists():
    for d, s in PAIRS.items():
        assert (DATA / d).is_file(), d
        assert (SCHEMA / s).is_file(), s
    for m in MODULES:
        assert (KIT / f"{m}.js").is_file(), m
    assert (ROOT / "examples" / "kit" / "index.html").is_file()
    assert (ROOT / "docs" / "guide" / "部品の使い方.md").is_file()


@pytest.mark.parametrize("data_file,schema_file", sorted(PAIRS.items()))
def test_g151_example_data_matches_schema(data_file, schema_file):
    schema = _load(SCHEMA / schema_file)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    try:
        import jsonschema
        jsonschema.Draft202012Validator.check_schema(schema)
    except ImportError:
        pass
    inst = _load(DATA / data_file)
    assert _errors(inst, schema) == []
    assert mini_validate(inst, schema) == []  # 自前の検査でも通る（jsonschema が無い環境と同じ結果）


@pytest.mark.parametrize("data_file,schema_file", sorted(PAIRS.items()))
def test_g151_schemas_reject_broken_data(data_file, schema_file):
    """形が緩すぎないことの確認: 必須の欄を 1 つ消すと落ちる。"""
    schema = _load(SCHEMA / schema_file)
    inst = _load(DATA / data_file)
    keys = ("flows", "legs", "points", "vehicles", "records", "sites") + (("items",) if schema_file == "item.json" else ())  # placement.json にも items がある
    if "required" in schema and not any(k in inst for k in keys):
        broken = {k: v for k, v in inst.items() if k != schema["required"][0]}
    else:
        key = next(k for k in keys if k in inst)
        first = dict(inst[key][0])
        first.pop(next(k for k in ("basis", "unit") if k in first), None)
        broken = {**inst, key: [first] + inst[key][1:]}
    assert _errors(broken, schema) != []
    assert mini_validate(broken, schema) != []


def test_every_example_item_says_whether_it_is_measured_estimated_or_assumed():
    for d in ("flows.json", "legs.json", "points.json", "vehicle.json", "items.json", "waiting_records.json", "sites.json"):
        inst = _load(DATA / d)
        items = next((inst[k] for k in ("flows", "legs", "points", "items", "records", "sites") if k in inst), [inst])
        assert items and all(x["basis"] in ("実測", "推定", "仮定") for x in items), d
    assert all(v["basis"] in ("実測", "推定", "仮定") for v in _load(DATA / "trips.json")["vehicles"])
    for d in PAIRS:
        meta = _load(DATA / d).get("meta")
        if meta is not None:
            assert meta.get("fiction") is True, d  # 架空のデータには fiction: true


# ---------------------------------------------------------------- 部品の約束（文字列で確かめる）
@pytest.mark.parametrize("name", MODULES)
def test_modules_are_display_only_and_have_no_fixed_paths(name):
    src = (KIT / f"{name}.js").read_text(encoding="utf-8")
    assert "\r" not in src
    for bad in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "localStorage", "eval(", "C:\\\\", "C:/Users"):
        assert bad not in src, (name, bad)
    if name != "kit":
        assert f"Kit.{name} = {{ mount }}" in src
        assert "destroy()" in src and "update(" in src


def test_kit_files_stay_inside_web_kit_and_the_app_loads_only_the_shared_palette():
    """アプリが読むのは web/kit/kit.js だけ（色と区分の共有。道具の採点 r1b #16）。ほかの部品は読まず、既存の web/*.js が Kit を使うのは Kit.palette だけ。"""
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert re.findall(r'src="(kit/[^"]+)"', html) == ["kit/kit.js"]
    for p in (ROOT / "web").glob("*.js"):
        uses = re.findall(r"Kit\.(\w+)", p.read_text(encoding="utf-8"))
        assert set(uses) <= {"palette"}, (p.name, set(uses))


def test_example_page_pins_the_same_cdn_versions_as_the_app():
    app = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    ex = (ROOT / "examples" / "kit" / "index.html").read_text(encoding="utf-8")
    for pin in ("maplibre-gl@4.7.1", "deck.gl@9.0.38"):
        assert pin in app and pin in ex
    assert "three" not in ex.lower().replace("three-dimensional", "")  # 今のアプリは three.js を使わない。部品も使わない


# ---------------------------------------------------------------- 画面（Edge）
def _make_page_class(cs):
    class CapPage(cs.Page):
        """CDP のイベント（コンソール・例外・ログ）を取っておく。"""

        def __init__(self, ws_url):
            self.events = []
            super().__init__(ws_url)

        def send(self, method, **params):
            self._id += 1
            mid = self._id
            self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            while True:
                msg = json.loads(self.ws.recv(timeout=60))
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg.get("result", {})
                self.events.append(msg)

        def problems(self):
            """誤り・警告（コンソール、例外、ブラウザのログ）の一覧。"""
            out = []
            for m in self.events:
                if "Tracking Prevention" in json.dumps(m, ensure_ascii=False):
                    continue  # Edge の追跡防止の通知。CDN の配信元（jsdelivr）の保存領域へのアクセスを止めたという、ブラウザ自身の知らせで、部品のコードとは関係がない
                meth, p = m.get("method"), m.get("params", {})
                if meth == "Runtime.consoleAPICalled" and p.get("type") in ("error", "warning", "assert"):
                    out.append("console." + p["type"] + ": " + " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))[:300])
                elif meth == "Runtime.exceptionThrown":
                    out.append("exception: " + json.dumps(p.get("exceptionDetails", {}), ensure_ascii=False)[:300])
                elif meth == "Log.entryAdded" and p.get("entry", {}).get("level") in ("error", "warning"):
                    out.append("log." + p["entry"]["level"] + ": " + p["entry"].get("text", "") + " " + p["entry"].get("url", ""))
            return out

        def pump(self, seconds):
            """待っている間に届くイベントも拾う。"""
            end = time.time() + seconds
            while time.time() < end:
                self.js("1")
                time.sleep(0.2)

    return CapPage


class Env:
    """静的サーバー（セッションで 1 つ）に Edge を 1 つ直列につなぐ。終わりに必ず止める。"""

    def __init__(self, port):
        import capture_screens as cs
        self.port = port
        self.cs = cs
        self._cm = None
        self.page = None

    def __enter__(self):
        self._cm = edge_page(1920, 1080, page_cls=_make_page_class(self.cs))
        self.page = self._cm.__enter__()
        self.page.send("Log.enable")
        return self

    def open(self, width, height=1000):
        p = self.page
        p.events.clear()
        p.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=width < 600)
        p.goto("about:blank")
        try:
            p.goto(f"http://127.0.0.1:{self.port}/examples/kit/index.html")
            p.wait(lambda: p.js("!!(window.__kit && window.__kit.ready)"), 120, "例の画面の準備")
        except Exception as e:  # noqa: BLE001
            msg = p.js("document.querySelector('#status') && document.querySelector('#status').textContent")
            startup_failed(p, f"例の画面が準備できません（状態表示: {msg}）", e, "kit_fail")
        p.pump(3)  # 地図の読み込みと描画が落ち着くまで
        return p

    def __exit__(self, *a):
        if self._cm is not None:
            self._cm.__exit__(*a)


def _shot(page, path: Path, width: int):
    h = int(page.js("Math.ceil(document.documentElement.scrollHeight)"))
    page.send("Emulation.setDeviceMetricsOverride", width=width, height=h, deviceScaleFactor=1, mobile=width < 600)
    time.sleep(1.0)
    r = page.send("Page.captureScreenshot", format="jpeg", quality=82)
    import base64
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(r["data"]))


STATE_JS = """(function(){
  const K = window.__kit, out = {};
  out.markers = document.querySelectorAll('.maplibregl-marker').length;
  out.kitDom = document.querySelectorAll('[data-kit]').length;
  out.docks = document.querySelectorAll('[data-kit=dock]').length;
  out.points = document.querySelectorAll('[data-kit=point]').length;
  out.tags = document.querySelectorAll('[data-kit=tag]').length;
  out.vehicles = document.querySelectorAll('[data-kit=vehicle]').length;
  out.load3d = document.querySelectorAll('[data-kit=load3d]').length;
  out.load3dCanvas = document.querySelectorAll('[data-kit=load3d] canvas').length;
  out.deckIds = {}; out.styleLayers = {}; out.canvases = {};
  for (const k of Object.keys(K.maps)) {
    out.deckIds[k] = Kit.deckLayers.ids(K.maps[k]);
    out.styleLayers[k] = K.maps[k].getStyle().layers.map(l => l.id);
    out.canvases[k] = K.maps[k].getContainer().querySelectorAll('canvas').length;
  }
  out.scrollW = document.documentElement.scrollWidth; out.clientW = document.documentElement.clientWidth;
  return JSON.stringify(out);
})()"""


def _state(page):
    return json.loads(page.js(STATE_JS))


BASE_LAYERS = {"kit-bg", "kit-outline-fill", "kit-outline-line"}


@pytest.mark.parametrize("width", [1920, 400])
def test_g150_example_page_draws_five_parts_without_console_problems(width, examples_server):
    with Env(examples_server) as env:
        page = env.open(width)
        s = _state(page)
        # 5 つの部品が描かれている
        assert s["points"] == 8, s
        assert s["docks"] >= 4 and s["load3d"] == 1, s
        assert s["load3dCanvas"] >= 1, s
        assert s["vehicles"] == 3, s
        assert len(s["deckIds"]["flows"]) == 2 and len(s["deckIds"]["legs"]) == 3 and len(s["deckIds"]["timeline"]) == 2, s
        assert s["deckIds"]["points"] == [], s  # 点は HTML の要素で描く
        # 凡例と表と出典
        texts = page.js("[...document.querySelectorAll('[data-kit=dock]')].map(e => e.innerText).join('\\n')")
        for t in ("量の流れ", "運行の区間", "実車", "空車", "不明", "地点の点", "120 分以上", "時間の帯", "表で見る", "出典: 架空の例"):
            assert t in texts, t
        assert "実測」は記録から数えた値" in texts
        assert page.js("document.querySelectorAll('[data-kit=timeline] .kit-band-row').length") == 3
        # 3D の表示の中身
        t3 = page.js("document.querySelector('[data-kit=load3d]').innerText")
        assert "全部載ります" in t3 and "40 / 40 個" in t3
        # 横にあふれない
        assert s["scrollW"] <= s["clientW"] + 1, s
        # 再生ボタン: 押すと時刻が進み、もう一度押すと止まる
        # 「1 秒 = 15 分」で約 2 秒再生すると、約 30 分（1 800 000 ms）進む。実際に経過した時間で期待値を出して比べる
        page.js("(() => { const s = document.querySelector('[data-k=speed]'); s.value = '15'; s.dispatchEvent(new Event('change')); })()")
        t0 = page.js("__kit.parts.timeline.time")
        page.js("window.__playT = performance.now(); document.querySelector('[data-k=play]').click()")
        page.pump(2)
        assert page.js("__kit.parts.timeline.playing") is True
        page.js("document.querySelector('[data-k=play]').click()")
        assert page.js("__kit.parts.timeline.playing") is False
        moved = page.js("__kit.parts.timeline.time") - t0
        elapsed = page.js("(performance.now() - window.__playT) / 1000")
        expected = elapsed * 15 * 60000
        # 1 コマが 100ms を超えると進みを切り詰めるので、下は広めに取る。1/1000 の速さ（約 1800 ms）は確実に落ちる
        assert 0.4 * expected <= moved <= 1.05 * expected, (moved, expected, elapsed)
        assert moved >= 10 * 60000, moved  # 少なくとも 10 分は進む
        # 帯（スライダー）の値は、開始からの分数と一致する
        assert page.js("Number(document.querySelector('[data-k=range]').value)") >= 10
        clock_txt = page.js("document.querySelector('[data-k=clock]').textContent")
        assert clock_txt != "05:30", clock_txt
        # 点を押す
        page.js("document.querySelector('[data-kit=point]').click()")
        assert "を選びました" in page.js("document.querySelector('#status').textContent")
        assert page.js("document.querySelectorAll('[data-kit=point].on').length") == 1
        page.pump(1)
        out = os.environ.get("KIT_SHOT_DIR")
        shot_dir = Path(out) if out else Path(tempfile.gettempdir())
        _shot(page, shot_dir / f"2026-11-06_v37_kit_{width}.jpg", width)
        assert page.problems() == [], page.problems()


def test_g152_update_and_destroy_leave_nothing_behind(examples_server):
    with Env(examples_server) as env:
        page = env.open(1920)
        before = _state(page)
        assert before["markers"] > 0 and before["kitDom"] > 0

        # --- update: データを差し替えると中身が変わる（部品は増えない）
        page.js("""(function(){
          const K = __kit, D = JSON.parse(JSON.stringify(K.data));
          D.flows.flows = D.flows.flows.slice(0, 3);
          D.points.points = D.points.points.slice(0, 5);
          D.legs.legs = D.legs.legs.slice(0, 6);
          D.trips.vehicles = D.trips.vehicles.slice(0, 2);
          D.placement.placements = D.placement.placements.slice(0, 10);
          K.parts.flows.update(D.flows); K.parts.points.update(D.points); K.parts.legs.update(D.legs);
          K.parts.timeline.update(D.trips); K.parts.load3d.update({ vehicle: D.vehicle, placement: D.placement });
        })()""")
        page.pump(1.5)
        mid = _state(page)
        assert mid["points"] == 5 and mid["vehicles"] == 2, mid
        assert mid["load3d"] == 1 and mid["docks"] == before["docks"], mid
        assert len(mid["deckIds"]["flows"]) == 2, mid
        rows = page.js("document.querySelectorAll('#d-flows .kit-tab tbody tr').length")
        assert rows == 3
        # 座標の欠けた要素は描かず、注意の文を出す
        page.js("""(function(){
          const D = JSON.parse(JSON.stringify(__kit.data)); D.points.points[0].lon = null;
          __kit.parts.points.update(D.points);
        })()""")
        assert "描いていません" in page.js("document.querySelector('#d-points').innerText")
        assert page.js("document.querySelectorAll('[data-kit=point]').length") == 7

        # --- destroy: 層・名札・点・凡例・3D が残らない。2 回呼んでも害はない
        page.js("Object.values(__kit.parts).forEach(p => { p.destroy(); p.destroy(); }); __kit.mounted = false")
        page.pump(0.8)
        after = _state(page)
        assert after["kitDom"] == 0, after
        assert after["markers"] == 0 and after["load3d"] == 0, after
        assert all(v == [] for v in after["deckIds"].values()), after
        for k, ids in after["styleLayers"].items():
            assert set(ids) <= BASE_LAYERS, (k, ids)  # 部品は地図のスタイルに層を足していない
        assert all(n == 1 for n in after["canvases"].values()), after  # 重ねた deck の canvas も外れた（地図の canvas だけ）
        assert page.js("document.querySelectorAll('.kit-tl, .kit-dock, .kit-3d, .kit-tag, .kit-pt').length") == 0

        # --- 付け直し: ボタンで元に戻り、また外せる
        page.js("document.querySelector('#toggle').click()")  # mounted が false なので付け直す
        page.pump(1.5)
        again = _state(page)
        assert again["points"] == 8 and again["load3d"] == 1, again
        page.js("document.querySelector('#toggle').click()")
        page.pump(0.8)
        assert _state(page)["kitDom"] == 0
        assert page.problems() == [], page.problems()


def test_destroy_after_the_map_is_removed_does_not_throw(examples_server):
    with Env(examples_server) as env:
        page = env.open(1920)
        r = page.js("""(function(){
          try { __kit.maps.flows.remove(); __kit.parts.flows.destroy(); __kit.maps.legs.remove(); __kit.parts.legs.destroy(); return 'ok'; }
          catch (e) { return String(e); }
        })()""")
        assert r == "ok", r
