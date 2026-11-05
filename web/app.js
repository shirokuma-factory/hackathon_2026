'use strict';
/* スポット出荷マップ。運賃・順位などは /api/*（tools/serve_web.py → src/shipcompare）の結果を表示するだけで、ここでは計算しない。
   web/data の JSON は地図の層（距離・運転日数・貸切の色分け）と経路の線だけに使う。
   例外は地図上の位置の計算（経路の補間、時間の帯の表示用の時刻の割り算）だけ。 */

const STYLE_URL = 'https://tiles.openfreemap.org/styles/positron';
const BASE = {
  bg: '#0a0f19', land2: '#0e1621', water: '#05080e', park: '#0d1a1d',
  roadMinor: '#17212f', roadMajor: '#222f44', motorway: '#33455f', rail: '#131c29',
  boundary2: '#4b5c7a', boundary3: '#27344a',
  label: '#7f8da6', labelStrong: '#a9b6cc', labelWater: '#46617f', halo: '#0a0f19',
};
// 単色の青の濃淡。暗い地図の上では、値が大きいほど明るくする。
const PALETTE = ['#1b4469', '#1f5784', '#2570a3', '#3189c0', '#4aa3d8', '#77c0ea', '#a9d9f4'];
const GRAY = [120, 130, 146, 44];
const FILL_ALPHA = 204;
const JAPAN_BOUNDS = [[129.2, 31.0], [146.2, 45.8]];

const $ = (s) => document.querySelector(s);
const App = {}; // 見方の切り替えなど、market.js から呼ぶ入口
const S = {
  view: 'market', hubSel: null, hubLineKey: '', hubLine: null,
  origin: '13104', layer: 'km', sel: -1, meta: null, idx: null, muni: null, pref: null, routes: null, estimate: null, estTok: 0, fareStamp: {},
  od: {}, route: null, selFeature: null, t: 0, playing: false, version: 0, selectedAt: 0, lastTick: 0,
  ambient: null, ambientAlpha: 1, hover: -1, byIndex: new Map(),
};
let map, overlay, firstLabelId;

/* ------------------------------------------------------------ 補助 */

const hex2rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
function paletteColor(i, n) {
  const t = n <= 1 ? 1 : i / (n - 1);
  const x = t * (PALETTE.length - 1), a = Math.floor(x), b = Math.min(PALETTE.length - 1, a + 1), f = x - a;
  const ca = hex2rgb(PALETTE[a]), cb = hex2rgb(PALETTE[b]);
  return ca.map((v, k) => Math.round(v + (cb[k] - v) * f));
}
const rgbCss = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
const fmt = (v) => Math.round(v).toLocaleString('ja-JP');
function fmtMin(m) {
  const h = Math.floor(m / 60), r = Math.round(m % 60);
  return h > 0 ? `${h}時間${String(r).padStart(2, '0')}分` : `${r}分`;
}
const lerp = (a, b, t) => a + (b - a) * t;
const ease = (t) => 1 - Math.pow(1 - t, 3);
const clamp01 = (x) => Math.max(0, Math.min(1, x));
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('on');
  clearTimeout(toast.t);
  toast.t = setTimeout(() => el.classList.remove('on'), 3200);
}

/* ------------------------------------------------------------ 経路の形 */

function hav(a, b) {
  const R = 6371, rad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * rad, dLon = (b[0] - a[0]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
function densify(coords, stepKm) {
  const out = [coords[0]];
  for (let i = 1; i < coords.length; i++) {
    const a = coords[i - 1], b = coords[i], n = Math.max(1, Math.ceil(hav(a, b) / stepKm));
    for (let k = 1; k <= n; k++) out.push([lerp(a[0], b[0], k / n), lerp(a[1], b[1], k / n)]);
  }
  return out;
}
// 曲がった弧（直線に垂直な向きにふくらませた 2 次ベジェ）。道路の形状がない区間の代わり。
function arcPath(a, b, bulge, n = 64) {
  const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const cx = mx - dy * bulge, cy = my + dx * bulge;
  const out = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n, u = 1 - t;
    out.push([u * u * a[0] + 2 * u * t * cx + t * t * b[0], u * u * a[1] + 2 * u * t * cy + t * t * b[1]]);
  }
  return out;
}
function makeRoute(path, kind, minutes, km) {
  const cum = [0];
  for (let i = 1; i < path.length; i++) cum.push(cum[i - 1] + hav(path[i - 1], path[i]));
  return { path, cum, total: cum[cum.length - 1] || 1, kind, minutes, km };
}
function pointAt(r, f) {
  const d = clamp01(f) * r.total;
  let lo = 0, hi = r.cum.length - 1;
  while (lo < hi - 1) { const m = (lo + hi) >> 1; if (r.cum[m] <= d) lo = m; else hi = m; }
  const seg = r.cum[hi] - r.cum[lo] || 1, t = (d - r.cum[lo]) / seg;
  return [lerp(r.path[lo][0], r.path[hi][0], t), lerp(r.path[lo][1], r.path[hi][1], t)];
}
function slicePath(r, f) {
  if (f >= 1) return r.path;
  const d = f * r.total, out = [];
  for (let i = 0; i < r.path.length; i++) {
    if (r.cum[i] <= d) out.push(r.path[i]); else break;
  }
  out.push(pointAt(r, f));
  return out;
}

/* ------------------------------------------------------------ 地図の下地（暗い配色に塗り替える） */

function darkStyle(style) {
  const B = BASE;
  for (const l of style.layers) {
    const p = (l.paint = l.paint || {}), id = l.id;
    if (l.type === 'background') p['background-color'] = B.bg;
    else if (l.type === 'fill') {
      delete p['fill-pattern'];
      if (id === 'water') p['fill-color'] = B.water;
      else if (id === 'building') p['fill-opacity'] = 0;
      else if (id.startsWith('park') || id === 'landcover_wood') p['fill-color'] = B.park;
      else p['fill-color'] = B.land2;
    } else if (l.type === 'line') {
      p['line-opacity'] = ['interpolate', ['linear'], ['zoom'], 3, 0.1, 6, 0.28, 9, 0.6, 12, 1];
      if (id.includes('casing')) { p['line-color'] = B.bg; p['line-opacity'] = 0.9; }
      else if (id.includes('motorway')) p['line-color'] = B.motorway;
      else if (id.includes('major')) p['line-color'] = B.roadMajor;
      else if (id.startsWith('highway') || id.startsWith('tunnel')) p['line-color'] = B.roadMinor;
      else if (id.startsWith('railway')) p['line-color'] = B.rail;
      else if (id === 'boundary_2' || id === 'boundary_disputed') p['line-color'] = B.boundary2;
      else if (id.startsWith('boundary')) p['line-color'] = B.boundary3;
      else if (id.startsWith('waterway')) p['line-color'] = B.water;
      else p['line-color'] = B.roadMinor;
    } else if (l.type === 'symbol') {
      const strong = id.startsWith('label_country') || id === 'label_city_capital';
      p['text-color'] = id.includes('water') ? B.labelWater : strong ? B.labelStrong : B.label;
      p['text-halo-color'] = B.halo;
      p['text-halo-width'] = 1.4;
      p['text-halo-blur'] = 0.5;
      p['icon-opacity'] = id === 'airport' ? 0.4 : 0;
      if (l.layout && l.layout['text-field']) {
        l.layout['text-field'] = ['coalesce', ['get', 'name:ja'], ['get', 'name']];
        if (l.layout['text-transform']) l.layout['text-transform'] = 'none';
        if (l.layout['text-font'] && String(l.layout['text-font']).includes('Italic')) l.layout['text-font'] = ['Noto Sans Regular'];
      }
    }
  }
  style.layers = style.layers.filter((l) => !(l.id.startsWith('highway-shield') || l.id.startsWith('road_shield')));
  return style;
}

/* ------------------------------------------------------------ データ */

async function getJSON(u) {
  const r = await fetch(u);
  if (!r.ok) throw new Error(u + ' ' + r.status);
  return r.json();
}
async function loadOrigin(code) {
  if (!S.od[code]) S.od[code] = await getJSON(`data/origin_${code}.json`);
  return S.od[code];
}

/* ------------------------------------------------------------ レイヤー */

const curOd = () => S.od[S.origin];
function classCount() { return curOd().edges[S.layer].length - 1; }
function fillColor(f) {
  const i = f.properties.i;
  if (i < 0) return GRAY;
  const c = curOd().cls[S.layer][i];
  if (c === null || c === undefined) return GRAY;
  return [...paletteColor(c, classCount()), Math.round(FILL_ALPHA * Rt.fillScale())]; // 手段を強調しているときは塗りを薄くして、下の道路・線路を見せる
}

const FERRY_SHORT = '計算できない（海を渡る経路）';
function valueText(i) {
  const od = curOd();
  if (od.km[i] === null) return '経路なし';
  const ferry = od.ferry && od.ferry[i];
  if (S.layer === 'km') return `${od.km[i].toFixed(1)} km${ferry ? '（フェリーを含む経路の可能性）' : ''}`;
  if (ferry) return FERRY_SHORT;
  if (S.layer === 'days') return `運転 ${od.days[i]}日`;
  return od.fare[i] === null ? '表の範囲外（200km未満）または積載量超過' : `約${fmt(od.fare[i])}円（${S.fareVehicleNote || '2t・税抜'}）`;
}

// munis: 画面に入る分だけに絞った市区町村の地物、prefs: 同じく県境の輪（cull.js）の配列。画面が動いて余白を出るまでは同じ参照
function staticLayers(munis, prefs) {
  const L = deck;
  const list = [];
  list.push(new L.GeoJsonLayer({
    id: 'muni', data: munis, pickable: true, stroked: true, filled: true, beforeId: firstLabelId,
    getFillColor: fillColor, getLineColor: [8, 12, 20, 120], lineWidthMinPixels: 0.4, lineWidthUnits: 'pixels', getLineWidth: 0.5,
    autoHighlight: true, highlightColor: [255, 255, 255, 54],
    updateTriggers: { getFillColor: [S.origin, S.layer, S.version, Rt.focus] },
    transitions: { getFillColor: { duration: 650, easing: ease } },
  }));
  list.push(new L.PathLayer({
    id: 'pref', data: prefs, getPath: (d) => d, beforeId: firstLabelId,
    getColor: [210, 226, 255, 74], widthMinPixels: 0.8, getWidth: 0.8, widthUnits: 'pixels',
  }));
  return list;
}

function tripData(paths, dur, period, n, perItemPhase) {
  // 繰り返しが切れないように、各粒を period ずらした 2 本ずつ作る
  const items = [];
  paths.forEach((p, pi) => {
    const total = p.cum[p.cum.length - 1] || 1;
    for (let k = 0; k < n; k++) {
      const phase = perItemPhase ? perItemPhase[pi] : (k * period) / n;
      for (const sh of [0, -period]) {
        items.push({ path: p.pts, ts: p.cum.map((c) => (c / total) * dur + phase + sh) });
      }
    }
  });
  return items;
}

function buildAmbient() {
  const o = S.meta.origins.find((x) => x.code === S.origin), a = [o.lon, o.lat];
  const codes = S.meta.ambient.filter((c) => c !== S.origin);
  const paths = [], phases = [];
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const PERIOD = 26;
  for (const c of codes) {
    const i = S.idx.codes.indexOf(c);
    if (i < 0) continue;
    const b = [S.idx.lon[i], S.idx.lat[i]];
    const pts = arcPath(a, b, 0.16 + rnd() * 0.06, 56);
    const cum = [0];
    for (let k = 1; k < pts.length; k++) cum.push(cum[k - 1] + hav(pts[k - 1], pts[k]));
    paths.push({ pts, cum, len: cum[cum.length - 1] });
    phases.push(rnd() * (PERIOD - 12));
  }
  // 長い経路ほどゆっくり進むように、時間の長さを距離に比例させる
  const items = [];
  paths.forEach((p, pi) => {
    const dur = 6 + p.len / 120;
    for (const sh of [0, -PERIOD]) items.push({ path: p.pts, ts: p.cum.map((c) => (c / p.len) * dur + phases[pi] + sh) });
  });
  S.ambient = { paths: paths.map((p) => p.pts), items, PERIOD };
}

const SEL_FILL = [92, 200, 255, 255], SEL_LINE = [226, 247, 255, 255];
// 選んだ着地の形（塗り用のポリゴンと、縁用の輪）。選び直したときだけ作る。縁は、光る縁（太い）を先に、線（細い）を後に並べて、線が上に重なるようにする
function selShape(f) {
  if (S.selShapeSrc === f) return S.selShape;
  const g = f.geometry, polys = g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates], rings = polys.flatMap((p) => p);
  S.selShapeSrc = f;
  S.selShape = { polys, edges: [...rings.map((p) => ({ p, w: 11, glow: true })), ...rings.map((p) => ({ p, w: 3, glow: false }))] };
  return S.selShape;
}
// 県境の輪（cull.js で画面に入る分だけを層に渡す）
function prefRings() {
  if (S.prefRingsSrc !== S.pref) {
    S.prefRingsSrc = S.pref;
    S.prefRings = S.pref.features.flatMap((f) => (f.geometry.type === 'MultiPolygon' ? f.geometry.coordinates.flat() : f.geometry.coordinates));
  }
  return S.prefRings;
}

// 値が変わらない間は同じ配列（参照）を返す。毎フレーム新しい配列を層に渡すと、deck.gl が属性を毎回作り直す（SPEC §19.5）。deps: 内容を決める値の並び
const SMEMO = new Map();
function stableData(key, deps, make) {
  const m = SMEMO.get(key);
  if (m && m.d.length === deps.length && m.d.every((x, i) => x === deps[i])) return m.v;
  const v = make();
  SMEMO.set(key, { d: deps, v });
  return v;
}

// 内容が変わらない間は同じ層のインスタンスを返す。ref: 内容を決める参照、sig: 値の署名
const LMEMO = new Map();
function memoLayer(id, ref, sig, make) {
  const m = LMEMO.get(id);
  // deck.gl に片付けられた層（画面の層から一度外れたもの）は使い回せない（初期化の assert で落ちる）
  const live = m && (m.l.lifecycle === 'Initialized' || String(m.l.lifecycle).startsWith('Matched') || m.l.lifecycle === 'Awaiting state');
  if (live && m.ref === ref && m.sig === sig) return m.l;
  const l = make();
  LMEMO.set(id, { ref, sig, l });
  return l;
}
/* ゆるやかな流れ（飾りの動き）。画質が低いときは本数を減らし、動きを減らす設定（prefers-reduced-motion）のときは動く層を出さない。
   frac: 0 = 動く層なし（背景の線だけ）、1 = すべて。経路は pi の順に並んでおり、items は 1 本につき 2 つ（時間をずらしたもの）なので、先頭から frac の分だけを使う。 */
function ambientFor(frac) {
  const a = S.ambient;
  if (!a || frac >= 1) return a;
  if (!a.sub) a.sub = new Map();
  let s = a.sub.get(frac);
  if (!s) {
    s = { paths: a.paths, PERIOD: a.PERIOD, items: frac <= 0 ? [] : a.items.filter((_, i) => { const j = i >> 1; return Math.floor((j + 1) * frac) > Math.floor(j * frac); }) };
    a.sub.set(frac, s);
  }
  return s;
}

function dynamicLayers(now) {
  const L = deck, out = [];
  const o = S.meta.origins.find((x) => x.code === S.origin);
  const sec = now / 1000;
  const amb = ambientFor(PERF.ambientMul()), aA = S.ambientAlpha;
  if (amb) {
    // 濃さが変わらない間は同じ層を返す（毎フレーム作り直さない）
    out.push(memoLayer('amb-base', amb.paths, aA.toFixed(3), () => new L.PathLayer({
      id: 'amb-base', data: amb.paths, getPath: (d) => d, getColor: [170, 205, 245, 34 * aA], widthMinPixels: 0.8, getWidth: 1,
      widthUnits: 'pixels', updateTriggers: { getColor: [aA] },
    })));
  }
  if (amb && amb.items.length) {
    out.push(new L.TripsLayer({
      id: 'amb-glow', data: amb.items, getPath: (d) => d.path, getTimestamps: (d) => d.ts, getColor: [120, 185, 255, 60 * aA],
      widthMinPixels: 5, capRounded: true, jointRounded: true, trailLength: 4.5, currentTime: sec % amb.PERIOD, fadeTrail: true,
      updateTriggers: { getColor: [aA] },
    }));
    out.push(new L.TripsLayer({
      id: 'amb-core', data: amb.items, getPath: (d) => d.path, getTimestamps: (d) => d.ts, getColor: [225, 242, 255, 200 * aA],
      widthMinPixels: 1.4, capRounded: true, jointRounded: true, trailLength: 3.2, currentTime: sec % amb.PERIOD, fadeTrail: true,
      updateTriggers: { getColor: [aA] },
    }));
  }
  // 選択した着地。GeoJsonLayer は 1 つで 3 層に分かれるので、塗り 1 層と、光る縁・縁の線 1 層の計 2 層にする
  // 塗りの濃さは層の opacity（定数）で動かし、頂点の色の再計算をしない
  if (S.selFeature) {
    const pulse = 0.55 + 0.45 * Math.sin(sec * 2.4), sh = selShape(S.selFeature), step = Math.round(pulse * 20);
    out.push(new L.SolidPolygonLayer({
      id: 'sel-fill', data: sh.polys, getPolygon: (d) => d, getFillColor: SEL_FILL, opacity: (38 + 34 * pulse) / 255,
    }));
    out.push(memoLayer('sel-edge', sh.edges, step, () => new L.PathLayer({
      id: 'sel-edge', data: sh.edges, getPath: (d) => d.p, widthUnits: 'pixels', getWidth: (d) => d.w, capRounded: true, jointRounded: true,
      getColor: (d) => (d.glow ? [92, 200, 255, 70 + 50 * pulse] : SEL_LINE), updateTriggers: { getColor: [step] },
    })));
  }
  const r = S.route;
  if (r && Rt.hasSets()) {
    out.push(...Rt.compareLayers(now, map.getZoom(), S.t));
    // 着地の印（ゆっくり広がる輪つき）
    const dest = [S.idx.lon[S.sel], S.idx.lat[S.sel]], dph = ((sec + 1.3) % 2.6) / 2.6;
    const destD = stableData('dest-c', dest, () => [{ p: dest }]);
    out.push(new L.ScatterplotLayer({
      id: 'dest-pulse', data: destD, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 8 + dph * 24, stroked: true,
      filled: false, getLineColor: [226, 247, 255, Math.round(190 * (1 - dph))], lineWidthUnits: 'pixels', getLineWidth: 1.6,
      updateTriggers: { getLineColor: [Math.round(dph * 30)], getRadius: [Math.round(dph * 30)] },
    }));
    out.push(memoLayer('dest', destD, '', () => new L.ScatterplotLayer({
      id: 'dest', data: destD, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 5.5, stroked: true, filled: true,
      getFillColor: [255, 255, 255, 255], getLineColor: [92, 200, 255, 255], lineWidthUnits: 'pixels', getLineWidth: 2.5,
    })));
  } else if (r) {
    const reveal = ease(clamp01((now - S.selectedAt) / 1100));
    const shown = reveal < 1 ? slicePath(r, reveal) : r.path;
    out.push(new L.PathLayer({
      id: 'route-base', data: [{ p: shown }], getPath: (d) => d.p, getColor: [92, 200, 255, 90], widthMinPixels: 2.4,
      getWidth: 2, widthUnits: 'pixels', capRounded: true, jointRounded: true,
      updateTriggers: { getPath: [reveal < 1 ? reveal : 1, S.selectedAt] },
    }));
    const dest = r.path[r.path.length - 1];
    const destD = stableData('dest-r', dest, () => [{ p: dest }]);
    out.push(memoLayer('dest', destD, '', () => new L.ScatterplotLayer({
      id: 'dest', data: destD, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 5.5, stroked: true, filled: true,
      getFillColor: [255, 255, 255, 255], getLineColor: [92, 200, 255, 255], lineWidthUnits: 'pixels', getLineWidth: 2.5,
    })));
  }
  // 出発地（ゆっくり広がる輪）
  const ph = (sec % 2.6) / 2.6, oD = stableData('origin', [o.lon, o.lat], () => [{ p: [o.lon, o.lat] }]);
  out.push(new L.ScatterplotLayer({
    id: 'origin-pulse', data: oD, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 8 + ph * 26, stroked: true, filled: false,
    getLineColor: [92, 200, 255, Math.round(200 * (1 - ph))], lineWidthUnits: 'pixels', getLineWidth: 1.6,
    updateTriggers: { getLineColor: [Math.round(ph * 30)], getRadius: [Math.round(ph * 30)] },
  }));
  out.push(memoLayer('origin', oD, '', () => new L.ScatterplotLayer({
    id: 'origin', data: oD, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 6, stroked: true, filled: true,
    getFillColor: [92, 200, 255, 255], getLineColor: [255, 255, 255, 255], lineWidthUnits: 'pixels', getLineWidth: 2,
  })));
  return out;
}

function frame(now) {
  PERF.tick(now);
  // 時間の帯の再生
  const dt = Math.min(0.1, (now - (S.lastTick || now)) / 1000);
  S.lastTick = now;
  const tl = Rt.timeline();
  if (S.playing && tl) {
    const axis = axisMax();
    S.t += (tl.M / 12) * dt;
    if (S.t >= axis) { S.t = 0; }
    updateTime();
  }
  const targetAlpha = S.sel >= 0 ? 0.28 : 1;
  S.ambientAlpha += (targetAlpha - S.ambientAlpha) * Math.min(1, dt * 3);
  const zoom = map.getZoom();
  // 市区町村・県境は画面に入る分だけを層に渡す（画面の外の頂点を毎フレーム処理しない。SPEC §19.5）
  const tc = performance.now();
  const cm = S.view === 'market' ? null : Cull.view('muni', S.muni.features, Cull.featureBox, map);
  const cp = S.view === 'market' ? null : Cull.view('pref', prefRings(), Cull.bboxOfCoords, map);
  PERF.mark('cull', tc);
  const sk = `${S.origin}|${S.layer}|${S.version}|${Rt.focus}|${cm ? cm.v : 0}|${cp ? cp.v : 0}`; // 値・出発地・手段の強調・絞り込みが変わったときだけ層を作り直す（色が更新される）
  if (cm && (!S.staticLayers || S.staticKey !== sk)) { S.staticLayers = staticLayers(cm.items, cp.items); S.staticKey = sk; }
  let layers;
  const tl0 = performance.now();
  if (S.view === 'market') layers = Market.layers(now, zoom);
  else if (S.view === 'find') layers = [S.staticLayers[1], ...Market.flowLayers(now, 0.3), ...Find.layers(now)]; // 県境、帰りの空きやすさ（うすく）、架空の便
  else layers = [...S.staticLayers, ...Hubs.layers(zoom, { line: currentHubLine() }), ...Muni.compareFac(zoom), ...dynamicLayers(now)];
  if (WARM.running) layers.push(...WARM.layers(now)); // 起動時のシェーダーの先読み（warmup.js）。読み込み画面の裏で、種類ごとに 1 個ずつ描く
  PERF.mark('layers', tl0);
  const ts0 = performance.now();
  overlay.setProps({ layers });
  PERF.mark('setProps', ts0);
  placeTruckTag();
  PERF.endFrame();
  requestAnimationFrame(frame);
}

/* ------------------------------------------------------------ 時間の帯 */

const axisMax = () => { const tl = Rt.timeline(); return tl ? tl.M * 1.06 : 1; };
const NOTE_TT = '所要時間は時刻表で確認';
// 時間の帯: トラックの区間だけ分で進む。電車・船・飛行機の区間は固定の幅の帯で、時間は表さない（SPEC §17.2）
function setupTimebar() {
  const tl = Rt.timeline(), cb = document.body.classList;
  if (!tl) { cb.remove('has-route'); $('#truck-tag').classList.remove('on'); return; }
  const M = tl.M, axis = axisMax();
  $('#tb-end').textContent = tl.single ? `到着 運転${fmtMin(M)}` : `トラックの区間 計${fmtMin(tl.truckMin)}`;
  let t = '', lab = '';
  const pc = (v) => ((v / axis) * 100).toFixed(2);
  if (tl.single) {
    const days = Math.ceil(M / 540);
    for (let d = 1; d < days; d++) t += `<div class="tick" style="left:${pc(d * 540)}%"></div>`;
    for (let d = 0; d < days; d++) lab += `<span class="st" style="left:${pc(d * 540)}%">${d + 1}日目</span>`;
  } else {
    for (const sg of tl.segs) {
      if (!sg.truck) {
        const c = rgbCss(Rt.col(sg.leg.mode));
        t += `<div class="gapband" style="left:${pc(sg.t0)}%;width:${pc(sg.t1 - sg.t0)}%;--c:${c}"></div>`;
        lab += `<span class="st gl" style="left:${pc(sg.t0)}%;color:${c}">${Rt.LABEL[sg.leg.mode]}の区間: ${NOTE_TT}</span>`;
      } else lab += `<span class="st" style="left:${pc(sg.t0)}%">トラック</span>`;
    }
  }
  t += `<div class="arrive" style="left:${pc(M)}%"></div>`;
  lab += `<span class="arr" style="left:${pc(M)}%">到着</span>`;
  $('#tb-ticks').innerHTML = t;
  $('#tb-days').innerHTML = lab;
  cb.add('has-route');
  updateTime();
}
function truckElapsed(tl, t) {
  let m = 0;
  for (const sg of tl.segs) { if (sg.truck) m += Math.max(0, Math.min(t, sg.t1) - sg.t0); }
  return m;
}
function updateTime() {
  const tl = Rt.timeline();
  if (!tl) return;
  const M = tl.M, axis = axisMax();
  S.t = Math.max(0, Math.min(axis, S.t));
  const pct = (S.t / axis) * 100;
  $('#tb-fill').style.width = pct + '%';
  $('#tb-handle').style.left = pct + '%';
  const arrived = S.t >= M, st = Rt.tlState(S.t), gap = st && !st.seg.truck && !arrived;
  const el = truckElapsed(tl, S.t);
  const day = Math.min(Math.ceil(M / 540), Math.floor(Math.max(0, S.t - 1e-6) / 540) + 1);
  $('#tb-now').textContent = S.t <= 0 ? '出発' : arrived ? '到着' : gap ? `${Rt.LABEL[st.seg.leg.mode]}の区間（${NOTE_TT}）` : `トラックの運転 ${fmtMin(el)}`;
  $('#tb-day').textContent = arrived || gap || !tl.single ? '' : `${day}日目`;
  const tr = $('#track');
  tr.setAttribute('aria-valuenow', String(Math.round((S.t / axis) * 100)));
  tr.setAttribute('aria-valuetext', $('#tb-now').textContent);
}
function setPlaying(on) {
  S.playing = on;
  $('#play').classList.toggle('playing', on);
  $('#play').setAttribute('aria-label', on ? '一時停止' : '再生');
}
function placeTruckTag() {
  const tag = $('#truck-tag'), st = Rt.tlState(S.t);
  if (!st || !document.body.classList.contains('has-route')) { tag.classList.remove('on'); return; }
  const p = map.project([st.p[0], st.p[1]]);
  tag.style.transform = `translate(${Math.round(p.x + 16)}px, ${Math.round(p.y - 34)}px)`;
  tag.textContent = $('#tb-now').textContent + ($('#tb-day').textContent ? ` ・ ${$('#tb-day').textContent}` : '');
  tag.classList.add('on');
}
function bindTimebar() {
  const track = $('#track');
  const fromEvent = (e) => {
    const b = track.getBoundingClientRect();
    S.t = clamp01((e.clientX - b.left) / b.width) * axisMax();
    updateTime();
  };
  let drag = false;
  track.addEventListener('pointerdown', (e) => { drag = true; track.setPointerCapture(e.pointerId); setPlaying(false); fromEvent(e); });
  track.addEventListener('pointermove', (e) => { if (drag) fromEvent(e); });
  track.addEventListener('pointerup', () => { drag = false; });
  track.addEventListener('keydown', (e) => {
    const step = (Rt.timeline() ? Rt.timeline().M : 100) / 40;
    if (e.key === 'ArrowRight') { S.t += step; updateTime(); e.preventDefault(); }
    if (e.key === 'ArrowLeft') { S.t -= step; updateTime(); e.preventDefault(); }
    if (e.key === ' ') { setPlaying(!S.playing); e.preventDefault(); }
  });
  $('#play').addEventListener('click', () => {
    if (!S.playing && Rt.timeline() && S.t >= axisMax() - 1) S.t = 0;
    setPlaying(!S.playing);
  });
}

/* ------------------------------------------------------------ 凡例 */

function renderLegend() {
  const od = curOd(), m = S.meta.layers[S.layer], edges = od.edges[S.layer], n = edges.length - 1;
  $('#lg-title').textContent = m.label;
  $('#lg-unit').textContent = S.layer === 'km' ? 'km' : S.layer === 'days' ? '日（運転のみ・1日9時間まで）' : '円（載る最小の車格・税抜）';
  $('#lg-bar').innerHTML = Array.from({ length: n }, (_, i) => `<i style="background:${rgbCss(paletteColor(i, n))}"></i>`).join('');
  const t = $('#lg-ticks');
  t.className = 'lg-ticks' + (S.layer === 'days' ? ' discrete' : '');
  const fmtB = (v) => (S.layer === 'fare' ? (v >= 1e5 ? Math.round(v / 1e4) : (v / 1e4).toFixed(1)) + '万' : fmt(v));
  if (S.layer === 'days') {
    const labs = ['1日', '2日', '3日', '4日以上'];
    t.style.cssText = '';
    t.innerHTML = labs.slice(0, n).map((x) => `<span>${x}</span>`).join('');
  } else {
    t.style.cssText = 'position:relative;height:14px;display:block';
    t.innerHTML = edges.map((v, i) => {
      const pos = (i / n) * 100, tf = i === 0 ? 'none' : i === n ? 'translateX(-100%)' : 'translateX(-50%)';
      return `<span style="position:absolute;left:${pos}%;transform:${tf};flex:none">${fmtB(v)}</span>`;
    }).join('');
  }
  $('#lg-null').textContent = S.layer === 'fare' ? '値なし（経路なし・フェリーの可能性・表の範囲外）'
    : S.layer === 'days' ? '値なし（経路なし・フェリーの可能性）' : '値なし（経路がない着地）';
}

/* ------------------------------------------------------------ 選択と詳細パネル */

const MODES = [
  { key: 'takuhai', label: '宅配' }, { key: 'rosen', label: '路線便' }, { key: 'jitbox', label: 'カゴ台車' },
  { key: 'kashikiri', label: '貸切' }, { key: 'rail', label: '鉄道' }, { key: 'ferry', label: 'フェリー' },
];
const CHIP = { '公開条件に合う': 'ok', '要確認': 'check', '使えない': 'no', '計算できない': 'na' };
// カードに添えるサムネイル（SPEC §19.6）。貸切は車種（2t・4t・10t）で選ぶ
const CARD_MODEL = { takuhai: 'box_80', rosen: 'truck', jitbox: 'roll_cage', rail: 'freight_train', ferry: 'ferry' };
const SHORT_LABEL = { takuhai: '宅配', rosen: '路線便', jitbox: 'カゴ台車' };

const cardModel = (key, r) => (key !== 'kashikiri' ? CARD_MODEL[key] : r.detail && r.detail.vehicle ? M3D.byVehicle(r.detail.vehicle) : null);

// /api/estimate の行からカードを作る。金額・状態・理由・出典・日数はすべて API の値。
function buildCards(i, est) {
  const cards = [];
  const srcOf = (r) => (r.source && r.source.name ? [r.source.name, r.source.url || '#'] : null);
  for (const m of MODES) {
    if (m.key === 'rail' || m.key === 'ferry') {
      const hn = Hubs.nearest(S.origin, i);
      cards.push({ label: m.label, model: CARD_MODEL[m.key], status: '計算できない', amount: null, dim: true, hub: hn ? { kind: m.key, ...hn[m.key] } : null,
        none: '運賃表を使えません。各社への問い合わせが必要です',
        tip: m.key === 'rail' ? 'JR貨物の運賃は二次利用できないため、金額は出していません' : 'フェリーの運賃は一次資料で確かめられなかったため、金額は出していません' });
      continue;
    }
    const r = est.rows.find((x) => x.group === m.key);
    if (!r) continue;
    const notes = [].concat(r.notes, r.issues.map((x) => x.message).filter((x) => !r.notes.includes(x)));
    const days = r.transit ? (r.transit.published || m.key === 'jitbox' ? r.transit.display : r.transit.text) : null;
    if (r.amount === null || r.amount === undefined) {
      cards.push({ label: m.label, model: cardModel(m.key, r), status: r.status, amount: null, none: r.reasons[0] || '運賃表なし・問い合わせ', days,
        tip: r.reasons.concat(notes).join(' / '), src: srcOf(r) });
      continue;
    }
    let tip = notes.join('。');
    let dayText = days;
    if (m.key === 'kashikiri') {
      const g = est.geo, o = est.osrm_kashikiri;
      if (est.route !== 'other') tip = `想定距離 ${est.assumed_km}km で計算した国の目安です` + (tip ? `。${tip}` : '');
      else tip = `OSRM の道路距離 ${g.km}km で計算した国の目安です。会社ごとに違います`;
      if (o && est.route !== 'other' && o.amount !== r.amount) tip += `。OSRM の道路距離 ${o.km}km で計算すると ${fmt(o.amount)}円です`;
      if (g && g.days) dayText = `運転だけで${g.days}日（積込み・荷待ちを除く）`;
    }
    cards.push({ label: m.label, model: cardModel(m.key, r), sub: r.detail && r.detail.vehicle ? `${r.detail.vehicle}車` : '', status: r.status, amount: r.amount,
      low: r.low, high: r.high, days: dayText, tip, src: srcOf(r) });
  }
  return cards;
}

// 鉄道とフェリーのカード: 出発地・着地それぞれの最寄りの貨物駅・港と直線距離（Python が計算済み。運賃は出さない）
function hubHtml(h) {
  const nm = h.kind === 'rail' ? '貨物駅' : '港';
  const row = (lbl, x) => `<div class="hrow"><span class="k">${lbl}</span><span class="v">${escapeHtml(x.hub.name)}</span><span class="d num">直線 ${x.km.toFixed(1)}km</span></div>`;
  return `<div class="hubinfo">${row(`出発地に近い${nm}`, h.o)}${row(`着地に近い${nm}`, h.d)}` +
    `<button class="hub-btn" data-hub="${h.kind}" aria-pressed="${S.hubSel === h.kind}">地図に点線を出す</button>` +
    `<div class="hub-note">直線距離で、${h.kind === 'rail' ? '線路' : '航路'}や道路の長さではありません。点線は経路の形ではなく、つなぎ方の目安です。OSM に登録された${h.kind === 'rail' ? '主な貨物駅' : '100km 以上のフェリー航路の端にある港'}の中から選んでいます。</div></div>`;
}

function renderCards(cards) {
  const max = Math.max(0, ...cards.map((c) => c.amount || 0));
  $('#cards').innerHTML = '<p class="cards-note">公開条件に合っても、その会社がこの出荷を受けるかは見積で確かめます。金額は税抜です。</p>' + cards.map((c, n) => {
    const amt = c.amount !== null && c.amount !== undefined
      ? `<div class="amt"><b class="num" data-to="${c.amount}">${fmt(c.amount)}</b><span>円（税抜）</span>` +
        (c.low && c.high ? `<span class="range num">${fmt(c.low)}〜${fmt(c.high)}円の幅</span>` : '') + '</div>' +
        `<div class="bar"><i data-w="${Math.max(3, (c.amount / max) * 100).toFixed(1)}"></i></div>`
      : '';
    const meta = c.days ? `<div class="meta"><span>日数</span><span class="v" title="${escapeHtml(c.days)}">${escapeHtml(c.days)}</span></div>` : '';
    const note = c.note ? `<div class="note">${escapeHtml(c.note)}</div>` : '';
    const src = c.src ? `<a class="src" href="${escapeHtml(c.src[1])}" target="_blank" rel="noopener" title="${escapeHtml(c.src[0])}">出典: ${escapeHtml(c.src[0])}</a>` : '';
    const tip = c.tip ? `<span class="tip" title="${escapeHtml(c.tip)}">補足</span>` : '';
    const foot = src || tip ? `<div class="foot">${src}${tip}</div>` : '';
    const none = c.amount === null || c.amount === undefined ? `<span class="amt-none" title="${escapeHtml(c.none)}">${escapeHtml(c.none)}</span>` : '';
    const hub = c.hub ? hubHtml(c.hub) : '';
    const th = c.model ? M3D.thumb(c.model, 80, 50, 'mth-card') : '';
    return `<article class="card${c.dim ? ' dim' : ''}${none ? ' none' : ''}" style="--n:${n}"><div class="top">${th}<h3>${c.label}${c.sub ? `<small>${c.sub}</small>` : ''}</h3>` +
      `${none}<span class="chip ${CHIP[c.status] || 'na'}">${c.status}</span></div>${amt}${note}${meta}${hub}${foot}</article>`;
  }).join('');
  requestAnimationFrame(() => requestAnimationFrame(() => {
    document.querySelectorAll('#cards .bar i').forEach((el) => { el.style.width = el.dataset.w + '%'; });
  }));
  document.querySelectorAll('#cards .amt b').forEach((el, n) => {
    const to = Number(el.dataset.to), t0 = performance.now() + 260 + n * 55, D = 800;
    const tick = (now) => {
      const f = clamp01((now - t0) / D);
      el.textContent = fmt(to * ease(f));
      if (f < 1) requestAnimationFrame(tick);
    };
    el.textContent = '0';
    requestAnimationFrame(tick);
  });
}

function select(i, { fly = true, fromSearch = false } = {}) {
  if (i < 0) return;
  const od = curOd(), code = S.idx.codes[i];
  if (code === S.origin) { toast('出発地と同じ市区町村です。別の着地を選んでください'); return; }
  S.sel = i;
  S.selFeature = S.byIndex.get(i) || null;
  const o = S.meta.origins.find((x) => x.code === S.origin), a = [o.lon, o.lat], b = [S.idx.lon[i], S.idx.lat[i]];
  const demo = S.routes[`${S.origin}-${code}`];
  const ferry = !!(od.ferry && od.ferry[i]);
  const minutes = ferry ? null : od.min[i], km = od.km[i]; // フェリーを含む経路は、運転時間と日数の帯を出さない（SPEC §15.3）
  if (demo) S.route = makeRoute(densify(demo.coordinates, 2), 'road', demo.minutes, demo.km);
  else S.route = makeRoute(arcPath(a, b, 0.1, 80), 'arc', minutes, km);
  S.t = 0;
  setPlaying(false);
  S.selectedAt = performance.now();
  if (!fromSearch) $('#q').value = S.idx.names[i];
  else $('#q').value = S.idx.names[i];
  renderDetail(i, demo);
  S.pendingFit = fly;
  Rt.setDestination(S.origin, i);
  setupTimebar();
  document.body.classList.add('has-detail');
  $('#detail').setAttribute('aria-hidden', 'false');
  writeHash();
}

function renderDetail(i, demo) {
  const od = curOd(), r = S.route;
  const ferry = !!(od.ferry && od.ferry[i]);
  const km = demo ? demo.km : od.km[i], min = demo ? demo.minutes : od.min[i];
  const days = ferry ? null : demo ? Math.max(1, Math.ceil(demo.minutes / 540)) : od.days[i];
  $('#d-from').textContent = od.name;
  $('#d-to').innerHTML = `<span class="ar">→</span><small>${escapeHtml(S.idx.prefs[i])}</small> ${escapeHtml(S.idx.names[i])}`;
  $('#s-km').innerHTML = km === null ? '—' : `${km.toFixed(1)}<small>km</small>`;
  $('#s-time').innerHTML = min === null ? '—' : fmtMin(min).replace(/(\d+)(時間|分)/g, '$1<small>$2</small>');
  $('#s-days').innerHTML = days === null ? '—' : `${days}<small>日</small>`;
  $('#route-kind').textContent = ferry ? 'フェリーを含む経路の可能性があります（平均速度 25km/h 未満）。運転日数と貸切の額は出しません'
    : '経路の形: 道路は OSRM、線路は国土数値情報（鉄道データ）と OSM の貨物専用線、航路と空港は OSM。手段のカードで選べます';
  S.estimate = null;
  $('#cards').scrollTop = 0;
  refreshEstimate();
}

const estimateBody = () => ({ shipment: Work.confirmedShipment(), origin_code: S.origin, dest_code: S.idx.codes[S.sel] });
// 右のカードは、確定した荷物と着地で /api/estimate を呼び直して作る。荷物・出発地・着地が変わるたびに呼ぶ。
async function refreshEstimate() {
  if (S.sel < 0) return;
  const i = S.sel, tok = ++S.estTok;
  if (!document.querySelector('#cards .card')) $('#cards').innerHTML = '<p class="cards-note">目安額を計算しています</p>';
  try {
    const est = await Work.api('/api/estimate', estimateBody());
    if (tok !== S.estTok || S.sel !== i) return;
    S.estimate = est;
    renderCards(buildCards(i, est));
  } catch (e) {
    if (tok === S.estTok) $('#cards').innerHTML = `<p class="cards-note err">${escapeHtml(e.message)}</p>`;
  }
}

// 貸切の層（荷物の合計重量に載る最小の車格）を、確定した荷物で計算し直す
async function refreshFareLayer() {
  const o = S.origin, stamp = JSON.stringify(Work.confirmedShipment());
  if (S.fareStamp[o] === stamp) return;
  try {
    const r = await Work.api('/api/layer', { origin_code: o, shipment: Work.confirmedShipment() });
    const od = S.od[o];
    od.fare = r.fare;
    od.cls.fare = r.cls;
    if (r.edges.length) od.edges.fare = r.edges;
    S.fareStamp[o] = stamp;
    S.fareVehicleNote = r.total_kg === null ? '2t・税抜' : `合計${Math.round(r.total_kg)}kg が載る最小の車格・税抜`;
    if (o === S.origin) { S.version++; renderLegend(); }
  } catch (e) { toast('貸切の層を計算し直せませんでした: ' + e.message); }
}

function onCargoChange() {
  S.fareStamp = {};
  refreshFareLayer();
  if (S.sel >= 0) { S.estimate = null; refreshEstimate(); }
}

// 手段ごとの経路の読み込み・取得が進んだとき（routes.js から）。帯を作り直し、最初の 1 回は地図を経路に合わせる
App.onRt = (first) => {
  setupTimebar();
  if (first && S.pendingFit && S.view === 'compare' && S.sel >= 0) { S.pendingFit = false; Rt.fit(); }
};

function deselect() {
  Rt.clear();
  S.sel = -1; S.selFeature = null; S.route = null; S.t = 0;
  setPlaying(false);
  document.body.classList.remove('has-detail', 'has-route');
  $('#detail').setAttribute('aria-hidden', 'true');
  $('#q').value = '';
  writeHash();
}

function padding() {
  const w = innerWidth, h = innerHeight;
  const left = Math.round($('#panel').getBoundingClientRect().right + 36);
  const dw = $('#detail').offsetWidth || 400;
  return { top: Math.round(h * 0.1), bottom: 190, left: Math.min(left, w * 0.34), right: dw + 70 };
}
function fitRoute() {
  const pts = S.route.path;
  let x0 = 999, y0 = 999, x1 = -999, y1 = -999;
  for (const p of pts) { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); }
  map.fitBounds([[x0, y0], [x1, y1]], { padding: padding(), maxZoom: 8.6, duration: 2200, essential: true, curve: 1.5 });
}
function fitJapan(animate) {
  const pad = S.view === 'market'
    ? { top: 165, bottom: 100, left: 400, right: ($('#m-right').offsetWidth || 480) + 90 }
    : S.view === 'find'
      ? { top: 60, bottom: 100, left: 420, right: ($('#find-right').offsetWidth || 480) + 70 }
    : { top: 20, bottom: 110, left: 60, right: 110 };
  map.fitBounds(JAPAN_BOUNDS, { padding: pad, duration: animate ? 1800 : 0 });
}

/* ------------------------------------------------------------ 検索 */

function bindSearch() {
  const q = $('#q'), ul = $('#suggest');
  let items = [], on = -1;
  const close = () => { ul.classList.remove('open'); q.setAttribute('aria-expanded', 'false'); on = -1; };
  const render = () => {
    const v = q.value.trim();
    if (!v) { close(); return; }
    const res = [];
    for (let i = 0; i < S.idx.names.length; i++) {
      const n = S.idx.names[i], pn = S.idx.prefs[i] + n;
      let k = n.startsWith(v) ? 0 : n.includes(v) ? 1 : pn.includes(v) ? 2 : -1;
      if (k >= 0 && S.routes[`${S.origin}-${S.idx.codes[i]}`]) k = -1; // デモの区間を先頭に
      if (k >= 0) res.push([k, i]);
    }
    res.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    items = res.slice(0, 8).map((x) => x[1]);
    on = items.length ? 0 : -1;
    ul.innerHTML = items.length
      ? items.map((i, n) => `<li role="option" data-i="${i}" class="${n === on ? 'on' : ''}"><span>${escapeHtml(S.idx.names[i]).replace(escapeHtml(v), (m) => `<mark>${m}</mark>`)}</span><small>${escapeHtml(S.idx.prefs[i])}</small></li>`).join('')
      : '<li class="empty">該当する市区町村がありません</li>';
    ul.classList.add('open');
    q.setAttribute('aria-expanded', 'true');
  };
  const pick = (i) => { close(); q.blur(); select(i, { fromSearch: true }); };
  q.addEventListener('input', render);
  q.addEventListener('focus', () => { if (q.value.trim() && S.sel < 0) render(); });
  q.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!items.length) return;
      on = (on + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      [...ul.children].forEach((li, n) => li.classList.toggle('on', n === on));
      e.preventDefault();
    } else if (e.key === 'Enter') {
      if (on >= 0 && items[on] !== undefined) pick(items[on]);
    } else if (e.key === 'Escape') { close(); }
  });
  ul.addEventListener('mousedown', (e) => {
    const li = e.target.closest('li[data-i]');
    if (li) { e.preventDefault(); pick(Number(li.dataset.i)); }
  });
  q.addEventListener('blur', () => setTimeout(close, 120));
}

/* ------------------------------------------------------------ 状態の URL */

function writeHash() {
  if (S.view === 'market') { Market.syncHash(); return; }
  const p = new URLSearchParams();
  p.set('v', S.view);
  if (S.view === 'find') Find.hashParams(p);
  if (S.view !== 'compare') { history.replaceState(null, '', '#' + p.toString()); return; }
  p.set('o', S.origin);
  p.set('l', S.layer);
  if (S.sel >= 0) p.set('d', S.idx.codes[S.sel]);
  history.replaceState(null, '', '#' + p.toString());
}

/* ------------------------------------------------------------ 起動 */

function setLayer(l) {
  S.layer = l;
  document.querySelectorAll('.lyr[data-layer]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.layer === l)));
  S.version++;
  renderLegend();
  writeHash();
}
async function setOrigin(code) {
  await loadOrigin(code);
  S.origin = code;
  $('#origin').value = code;
  S.version++;
  buildAmbient();
  renderLegend();
  refreshFareLayer();
  if (S.sel >= 0) { const i = S.sel; select(i, { fly: true }); } else writeHash();
}

function hoverTag(info) {
  const tag = $('#hover-tag');
  const o = info && info.object;
  const hh = Hubs.on && o ? Hubs.hoverText(info) : null;
  if (hh) {
    tag.innerHTML = `${hh.m ? M3D.thumb(hh.m, 64, 40, 'mth-tip') : ''}<b>${escapeHtml(hh.t)}</b><span>${escapeHtml(hh.s)}</span>`;
    tag.style.transform = `translate(${Math.min(innerWidth - 300, info.x + 18)}px, ${Math.max(8, info.y - 8)}px)`;
    tag.classList.add('on');
    return;
  }
  if (!o || info.layer.id !== 'muni' || o.properties.i < 0) { tag.classList.remove('on'); return; }
  const i = o.properties.i;
  tag.innerHTML = `<b>${escapeHtml(S.idx.prefs[i])} ${escapeHtml(S.idx.names[i])}</b><span class="num">${escapeHtml(valueText(i))}</span>`;
  const x = Math.min(innerWidth - 260, info.x + 18), y = Math.max(8, info.y - 8);
  tag.style.transform = `translate(${x}px, ${y}px)`;
  tag.classList.add('on');
}

/* ------------------------------------------------------------ 見方の切り替え（SPEC §16）と拠点の層（SPEC §15.5） */

const DETAIL_FLAGS = ['has-detail', 'has-route', 'has-work'];
function applyViewClasses() {
  const c = document.body.classList;
  for (const v of ['market', 'find', 'compare']) c.toggle('view-' + v, S.view === v);
  document.querySelectorAll('#views button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.view === S.view)));
}
function setView(v) {
  if (v === S.view) return;
  const prev = S.view, c = document.body.classList;
  if (prev === 'compare') {
    S.savedFlags = DETAIL_FLAGS.filter((f) => c.contains(f));
    if (Work.isOpen()) Work.closeWork();
    c.remove(...DETAIL_FLAGS);
    Rt.suspend();
    setPlaying(false);
    $('#truck-tag').classList.remove('on');
  }
  if (prev === 'market') Market.setActive(false);
  if (prev === 'find') Find.leave();
  $('#hover-tag').classList.remove('on');
  S.view = v;
  applyViewClasses();
  if (v === 'compare') {
    c.add(...(S.savedFlags || []));
    Rt.resume();
    if (S.sel >= 0 && S.route) { if (Rt.hasSets()) Rt.fit(); else fitRoute(); } else fitJapan(true);
  } else if (v === 'market') {
    Market.setActive(true);
    const r = Market.selected();
    if (r) Market.select(r.k, { fly: true }); else fitJapan(true);
  } else {
    fitJapan(true);
    Find.enter(prev === 'market' ? Market.pairNames() : null);
  }
  writeHash();
  setHubLegend();
}
App.setView = setView;

function currentHubLine() {
  if (!S.hubSel || S.sel < 0 || !S.route) { S.hubLine = null; S.hubLineKey = ''; return null; }
  const key = `${S.hubSel}|${S.origin}|${S.sel}`;
  if (key !== S.hubLineKey) {
    const o = S.meta.origins.find((x) => x.code === S.origin);
    S.hubLine = Hubs.line(S.hubSel, S.origin, S.sel, [o.lon, o.lat], [S.idx.lon[S.sel], S.idx.lat[S.sel]]);
    S.hubLineKey = key;
  }
  return S.hubLine;
}

function setHubLegend() {
  const html = '<span><i class="hb-sq"></i>貨物駅</span><span><i class="hb-ci"></i>港</span><span><i class="hb-ds"></i>フェリー航路（30km 以上）</span>' +
    '<span class="mlg-faint">OpenStreetMap に登録された主なもので、全てではありません</span>';
  document.querySelectorAll('.lg-hubs').forEach((el) => { el.hidden = !Hubs.on; el.innerHTML = html; });
}
function setHubs(on) {
  Hubs.on = on;
  document.querySelector('.tgl[data-toggle="hubs"]').setAttribute('aria-pressed', String(on));
  if (map.getLayer('hub-attr')) map.setLayoutProperty('hub-attr', 'visibility', on ? 'visible' : 'none');
  setHubLegend();
}
function setHubSel(kind) {
  S.hubSel = S.hubSel === kind ? null : kind;
  document.querySelectorAll('#cards .hub-btn').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.hub === S.hubSel)));
  if (S.hubSel && S.route) {
    const ln = currentHubLine();
    if (ln) {
      const pts = ln.path, xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
      map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]], { padding: padding(), maxZoom: 8.6, duration: 1600, essential: true, curve: 1.4 });
    }
  }
}

// 画質の段が変わったとき（と起動時）: 描画の解像度比を合わせる
function applyQuality() {
  const r = PERF.pixelRatio();
  try { if (map && map.getPixelRatio && map.getPixelRatio() !== r && map.setPixelRatio) map.setPixelRatio(r); } catch (e) { console.error(e); }
  if (overlay) overlay.setProps({ useDevicePixels: r });
}

async function main() {
  PERF.init();
  const [meta, idx, muni, pref, routes, style] = await Promise.all([
    getJSON('data/meta.json'), getJSON('data/index.json'), getJSON('data/muni.geojson'), getJSON('data/pref.geojson'),
    getJSON('data/routes.json'), getJSON(STYLE_URL), Hubs.load(), Rt.loadMeta().then(() => Promise.all([Rt.loadPairs(), Rt.loadTrips()])), Market.load(), Muni.load(),
  ]);
  Object.assign(S, { meta, idx, muni, pref, routes });
  for (const f of muni.features) S.byIndex.set(f.properties.i, f);
  // 大きな都市（各都道府県の代表）。ゆるやかな流れの行き先
  meta.ambient = ['01101', '02201', '03201', '04101', '05201', '06201', '07201', '15103', '16201', '17201', '18201', '19201', '20201', '21201',
    '22101', '22130', '23106', '24201', '25201', '26104', '27128', '28110', '29201', '30201', '31201', '32201', '33101', '34101', '35203',
    '36201', '37201', '38201', '39201', '40131', '41201', '42201', '43101', '44201', '45201', '46201', '47201', '27140', '14100', '23101'];
  const p = new URLSearchParams(location.hash.slice(1));
  S.view = ['market', 'find', 'compare'].includes(p.get('v')) ? p.get('v') : (p.get('o') || p.get('l') || p.get('d')) ? 'compare' : 'market';
  Market.readHash(p);
  Muni.readHash(p);
  if (p.get('o') && meta.origins.some((o) => o.code === p.get('o'))) S.origin = p.get('o');
  if (['km', 'days', 'fare'].includes(p.get('l'))) S.layer = p.get('l');
  await loadOrigin(S.origin);

  $('#origin').innerHTML = meta.origins.map((o) => `<option value="${o.code}">${o.name}</option>`).join('');
  $('#origin').value = S.origin;
  await Work.init({
    onCargoChange: () => { onCargoChange(); Find.onCargo(); },
    onWorkToggle: () => {},
    routeLabel: () => S.workLabel || '',
  });
  document.querySelectorAll('.lyr[data-layer]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.layer === S.layer)));

  const st = darkStyle(style);
  firstLabelId = st.layers.find((l) => l.type === 'symbol').id;
  map = new maplibregl.Map({
    container: 'map', style: st, center: [137.5, 37.2], zoom: 4.6, minZoom: 3.4, maxZoom: 16, attributionControl: false,
    dragRotate: false, pitchWithRotate: false, touchPitch: false, fadeDuration: 0, maxBounds: [[110, 15], [165, 55]],
    pixelRatio: PERF.pixelRatio(), // 高解像度の画面で画素数が増えすぎないようにする（画質の段で変わる）
  });
  map.touchZoomRotate.disableRotation();
  map.addControl(new maplibregl.AttributionControl({
    compact: false,
    customAttribution: ['国土数値情報（行政区域データ）（国土交通省）を加工して作成', '国土数値情報（鉄道データ）（国土交通省）を加工して作成', '道路・フェリー・空港: OSRM・OpenStreetMap（© OpenStreetMap contributors, ODbL）'],
  }), 'bottom-right');
  overlay = new deck.MapboxOverlay({
    interleaved: new URLSearchParams(location.search).get('il') !== '0', layers: [], // ?il=0 は計測の比較用（地図の上に別に重ねる）
    getCursor: ({ isHovering, isDragging }) => (isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab'),
    onHover: (info) => { if (S.view === 'market') Market.hover(info); else if (S.view === 'compare') hoverTag(info); },
    pickingRadius: 6,
    onClick: (info) => { if (S.view === 'market') { Market.click(info); return; } if (S.view === 'find') { Find.click(info); return; } if (S.view !== 'compare') return; if (Work.isOpen()) Work.closeWork(); if (info.object && info.layer && info.layer.id === 'muni' && info.object.properties.i >= 0) select(info.object.properties.i); },
  });
  map.addControl(overlay);
  PERF.onChange(applyQuality);
  applyQuality();
  await new Promise((r) => (map.loaded() ? r() : map.once('load', r)));
  PERF.hold(Infinity); // 先読みが終わるまで、画質の自動判定にフレーム時間を使わない
  $('#loading').textContent = '描画の準備をしています';
  await WARM.warmMap(map); // 地図の描画の種類を先にコンパイルさせる（読み込み画面の裏）
  map.addSource('hub-attr', { type: 'geojson', data: { type: 'FeatureCollection', features: [] }, attribution: '拠点: © OpenStreetMap contributors（ODbL 1.0）' });
  map.addLayer({ id: 'hub-attr', type: 'line', source: 'hub-attr', layout: { visibility: 'none' } });
  applyViewClasses();
  Rt.mountLegend();
  Rt.bind();
  fitJapan(false);
  buildAmbient();
  renderLegend();
  bindSearch();
  bindTimebar();
  Market.setActive(S.view === 'market');
  Market.init();
  Market.afterStart();
  Muni.init();
  Muni.afterStart();
  Find.readHash(p);
  await Find.init();
  if (S.view === 'find') Find.enter(null);
  setHubLegend();
  document.querySelectorAll('#views button').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
  document.querySelector('.tgl[data-toggle="hubs"]').addEventListener('click', () => setHubs(!Hubs.on));
  document.querySelector('.tgl[data-toggle="jartic"]').addEventListener('click', (e) => {
    const on = e.currentTarget.getAttribute('aria-pressed') !== 'true';
    e.currentTarget.setAttribute('aria-pressed', String(on));
    Market.setJartic(on);
  });
  document.querySelector('.tgl[data-toggle="conc"]').addEventListener('click', (e) => Muni.setConc(e.currentTarget.getAttribute('aria-pressed') !== 'true'));
  document.querySelector('.tgl[data-toggle="fac"]').addEventListener('click', (e) => Muni.setFac(e.currentTarget.getAttribute('aria-pressed') !== 'true'));
  if (p.get('hubs') === '1') setHubs(true);
  $('#cards').addEventListener('click', (e) => { const b = e.target.closest('.hub-btn'); if (b) setHubSel(b.dataset.hub); });

  $('#origin').addEventListener('change', (e) => setOrigin(e.target.value));
  document.querySelectorAll('.lyr[data-layer]').forEach((b) => b.addEventListener('click', () => setLayer(b.dataset.layer)));
  $('#close').addEventListener('click', () => { deselect(); fitJapan(true); });
  $('#home').addEventListener('click', () => {
    if (S.view === 'market') Market.deselect(false);
    else if (S.sel >= 0) deselect();
    fitJapan(true);
  });
  $('#make-request').addEventListener('click', async () => {
    if (S.sel < 0) return;
    const btn = $('#make-request');
    btn.disabled = true;
    try {
      const est = await Work.api('/api/estimate', estimateBody());
      S.workLabel = `${curOd().name.replace(/^.+?[都道府県]/, '')} → ${S.idx.names[S.sel]}`;
      await Work.startFromDestination(est);
    } catch (e) { toast(e.message); } finally { btn.disabled = false; }
  });
  addEventListener('keydown', (e) => { if (e.key === 'Escape' && S.view === 'compare' && S.sel >= 0 && !Work.isOpen() && document.activeElement.tagName !== 'INPUT') { deselect(); fitJapan(true); } });

  if (p.get('d') && S.view === 'compare') {
    const i = idx.codes.indexOf(p.get('d'));
    if (i >= 0) select(i, { fly: p.get('fly') !== '0' });
  }
  refreshFareLayer();
  WARM.start(map, overlay);
  WARM.onDone(() => { PERF.hold(2000); setTimeout(() => $('#loading').classList.add('done'), 150); });
  requestAnimationFrame(frame);
  M3D.preload();
  if (new URLSearchParams(location.search).get('perf')) { const sc = document.createElement('script'); sc.src = 'perfprobe.js'; document.head.appendChild(sc); }
  window.__app = { WARM, S, Market, Muni, Hubs, Find, Rt, setView, setHubs, setHubSel, select, setLayer, setOrigin, map: () => map, refreshEstimate };
}

main().catch((e) => {
  console.error(e);
  $('#loading').textContent = '読み込みに失敗しました: ' + e.message;
});
