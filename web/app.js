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
const GUIDE_JP_BOUNDS = [[129.6, 30.95], [146.0, 45.6]];   // 案内の全国: 鹿児島県の南端から北海道の北東端まで（沖縄県は入れない）

const $ = (s) => document.querySelector(s);
const App = {}; // 見方の切り替えなど、market.js から呼ぶ入口
const S = {
  view: 'market', guide: '', hubSel: null, hubLineKey: '', hubLine: null,
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

/* データの読み込み。通信が一瞬途切れたり、サーバーが忙しいときに 1 回の失敗で画面が止まらないよう、3 回まで間をあけて読み直す（500ms、1,000ms）。
   それでも読めないときは、英語の「Failed to fetch」を見せず、日本語の文と「読み直す」ボタンを出す（SPEC §43）。見つからない（404）ときは読み直さない */
class LoadError extends Error {}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function getJSON(u) {
  let status = '';
  for (let i = 0; i < 3; i++) {
    if (i > 0) await wait(500 * 2 ** (i - 1));
    try {
      const r = await fetch(u);
      if (r.ok) return await r.json();
      status = `（${r.status}）`;
      if (r.status === 404) break;
    } catch (e) { status = ''; }
  }
  throw new LoadError(`読み込めませんでした${status}。通信を確かめて、読み直してください。`);
}
// 読み込みの失敗を、利用者に見せる日本語の文にする。日本語を含まない英語の文（Failed to fetch など）はそのまま見せない
function jpLoadMessage(e) {
  const m = String((e && e.message) || e || '');
  if (e instanceof LoadError || /[぀-ヿ一-鿿]/.test(m)) return m;
  if (/Failed to fetch|NetworkError|Load failed|network/i.test(m)) return '読み込めませんでした。通信を確かめて、読み直してください。';
  return '画面を読み込めませんでした。読み直してください。';
}
// 読み直しの案内。retry があればその処理をもう一度やり、なければページを読み込み直す
function showLoadNotice(text, retry) {
  let el = document.querySelector('#load-notice');
  if (!el) { el = document.createElement('div'); el.id = 'load-notice'; el.className = 'load-fail'; el.setAttribute('role', 'alert'); document.body.appendChild(el); }
  el.innerHTML = '';
  const p = document.createElement('p'); p.textContent = text;
  const b = document.createElement('button'); b.type = 'button'; b.className = 'retry'; b.textContent = '読み直す';
  b.addEventListener('click', () => { if (retry) { el.remove(); retry(); } else location.reload(); });
  el.append(p, b);
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

const FERRY_SHORT = '船が要るため計算できません';
function valueText(i) {
  const od = curOd();
  if (od.km[i] === null) return '道がありません';
  const ferry = od.ferry && od.ferry[i];
  if (S.layer === 'km') return `${od.km[i].toFixed(1)} km${ferry ? '（船を使う道かもしれません）' : ''}`;
  if (ferry) return FERRY_SHORT;
  if (S.layer === 'days') return `運転 ${od.days[i]}日`;
  return od.fare[i] === null ? '200 km 未満か、重すぎて計算できません' : `約 ${fmt(od.fare[i])} 円（${S.fareVehicleNote || '2t・税抜'}）`;
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
    transitions: { getFillColor: { duration: MapFx.ms(300), easing: ease } },
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
    if (!Rt.pathOk('truck', p.pts)) return;   // 海をまたぐ弧は実際の道ではないので、トラックの粒子は走らせない（背景の線だけ）
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
    const unk = r.kind === 'arc' && !Rt.pathOk('truck', r.path);   // 実際の道が無く、弧が海をまたぐときは細い点線（経路は不明。乗り物は出さない）
    const shown = reveal < 1 ? slicePath(r, reveal) : r.path;
    out.push(new L.PathLayer({
      id: 'route-base', data: [{ p: shown }], getPath: (d) => d.p, getColor: [92, 200, 255, unk ? 120 : 90], widthMinPixels: unk ? 1.4 : 2.4,
      getWidth: unk ? 1.2 : 2, widthUnits: 'pixels', capRounded: !unk, jointRounded: true,
      extensions: unk && typeof deck.PathStyleExtension === 'function' ? [new deck.PathStyleExtension({ dash: true })] : [], getDashArray: [3, 3], dashJustified: true,
      updateTriggers: { getPath: [reveal < 1 ? reveal : 1, S.selectedAt], getWidth: [unk] },
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
  if (S.view === 'home') { S.lastTick = now; requestAnimationFrame(frame); return; }   // 最初の画面では地図を描かない
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
  // 案内の「送り方を比べる」は、着地を選ぶまで背景の細い線（新宿から全国への扇）を描かない（r4a 指摘6）
  const targetAlpha = (S.view === 'compare' && document.body.classList.contains('guided')) ? 0 : S.sel >= 0 ? 0.28 : 1;   // 案内の比べるは、選んだ後も背景の細い線を出さない
  S.ambientAlpha += (targetAlpha - S.ambientAlpha) * Math.min(1, dt * 3);
  const zoom = map.getZoom();
  // 市区町村・県境は画面に入る分だけを層に渡す（画面の外の頂点を毎フレーム処理しない。SPEC §19.5）
  const tc = performance.now();
  const cm = S.view === 'market' ? null : Cull.view('muni', S.muni.features, Cull.featureBox, map);
  const cp = S.view === 'market' ? null : Cull.view('pref', prefRings(), Cull.bboxOfCoords, map);
  PERF.mark('cull', tc);
  const sk = `${S.origin}|${S.layer}|${S.version}|${Rt.focus}|${cm ? cm.v : 0}|${cp ? cp.v : 0}`; // 値・出発地・手段の強調・絞り込みが変わったときだけ層を作り直す（色が更新される）
  // 画面の層から一度外れた層（全国の物流傾向では県境などを出さない）は deck.gl に片付けられ、同じ物を戻すと初期化の assert で落ちる。片付け済みなら作り直す
  const usedHere = S.view === 'find' || S.view === 'own' ? [1] : [0, 1];   // この見方が実際に出す層（県境だけ、または両方）
  const spent = !!S.staticLayers && usedHere.some((i) => String(S.staticLayers[i].lifecycle).startsWith('Finalized'));
  if (cm && (!S.staticLayers || S.staticKey !== sk || spent)) { S.staticLayers = staticLayers(cm.items, cp.items); S.staticKey = sk; }
  let layers;
  const tl0 = performance.now();
  if (S.view === 'market') layers = V.Market.layers(now, zoom);
  else if (S.view === 'find') layers = [S.staticLayers[1], ...V.Market.flowLayers(now, 0.3), ...V.Find.layers(now)]; // 県境、帰りの空きやすさ（うすく）、架空の便
  else if (S.view === 'own') layers = [S.staticLayers[1], ...(V.Own.market() ? V.Market.flowLayers(now, 0.55) : []), ...V.Own.layers()]; // 県境、市場の矢印（切り替え）、自社の区間（SPEC §22）
  else layers = [...S.staticLayers, ...Hubs.layers(zoom, { line: currentHubLine() }), ...V.Muni.compareFac(zoom), ...dynamicLayers(now)];
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

// 船・飛行機でしか行けない着地か（道路の運転時間・日数を出さない。上の欄と時間の帯で同じ規則にする）
const seaDest = () => !!(S.sel >= 0 && curOd() && curOd().ferry && curOd().ferry[S.sel]);
const axisMax = () => { const tl = Rt.timeline(); return tl ? tl.M * 1.06 : 1; };
const NOTE_TT = '時間は時刻表で確かめてください';
// 時間の帯: トラックの区間だけ分で進む。電車・船・飛行機の区間は固定の幅の帯で、時間は表さない（SPEC §17.2）
function setupTimebar() {
  const tl = Rt.timeline(), cb = document.body.classList;
  if (!tl) { cb.remove('has-route'); $('#truck-tag').classList.remove('on'); return; }
  const M = tl.M, axis = axisMax();
  const sea = seaDest();
  $('#tb-end').textContent = tl.single && sea ? '運転時間は出しません（船・飛行機の時間は見積で確認）' : tl.single ? `到着 運転${fmtMin(M)}` : `トラックで走る時間 計${fmtMin(tl.truckMin)}${S.sel >= 0 && curOd() && curOd().ferry && curOd().ferry[S.sel] ? '（港・空港まで。船・飛行機の時間は見積で確認）' : ''}`;
  let t = '', lab = '';
  const pc = (v) => ((v / axis) * 100).toFixed(2);
  if (tl.single && sea) {
    // 海を渡る着地: 道路だけの時間と日数は出さない（目盛りも付けない）
  } else if (tl.single) {
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
  const sea = tl.single && seaDest();
  $('#tb-now').textContent = S.t <= 0 ? '出発' : arrived ? '到着' : sea ? '道の目安（船の時間は見積で確認）' : gap ? `${Rt.LABEL[st.seg.leg.mode]}の区間（${NOTE_TT}）` : `トラックの運転 ${fmtMin(el)}`;
  $('#tb-day').textContent = arrived || gap || sea || !tl.single ? '' : `${day}日目`;
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
  tag.textContent = $('#tb-now').textContent + ($('#tb-day').textContent ? ` ・ ${$('#tb-day').textContent}` : '');
  let x = Math.round(p.x + 16);
  const y = Math.round(p.y - 34), ly = $('#layers').getBoundingClientRect();
  // 右の「表示」の欄（#layers）に名札が重なるときは、点の左側に出す（レビュー F9）
  if (ly.width > 0 && getComputedStyle($('#layers')).display !== 'none') {
    const w = tag.offsetWidth || 160, h = tag.offsetHeight || 28;
    if (x + w > ly.left - 6 && x < ly.right && y + h > ly.top && y < ly.bottom) x = Math.max(8, Math.round(p.x - 16 - w));
  }
  tag.style.transform = `translate(${x}px, ${y}px)`;
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
  $('#lg-unit').textContent = S.layer === 'km' ? 'km' : S.layer === 'days' ? '日（運転のみ・1日9時間まで）' : '円（荷物が載るいちばん小さい車・税抜）';
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
  $('#lg-null').textContent = S.layer === 'fare' ? '数字なし（道がない・船が要る・200km 未満）'
    : S.layer === 'days' ? '数字なし（道がない・船が要る）' : '数字なし（道がない届け先）';
}

/* ------------------------------------------------------------ 選択と詳細パネル */

const MODES = [
  { key: 'takuhai', label: '宅配' }, { key: 'rosen', label: '路線便' }, { key: 'jitbox', label: 'カゴ台車' },
  { key: 'kashikiri', label: '貸切' }, { key: 'rail', label: '鉄道' }, { key: 'ferry', label: '船' },
];
const CHIP = { '公開条件に合う': 'ok', '要確認': 'check', '使えない': 'no', '計算できない': 'na' };
const CHIP_WORD = { '公開条件に合う': '使える', '要確認': '確認が要る', '計算できない': '額は出せません' };   // 画面に出す語（API の状態の語は変えない。文言の直し 2026-11-07）
// カードに添えるサムネイル（SPEC §19.6）。貸切は車種（2t・4t・10t）で選ぶ
const CARD_MODEL = { takuhai: 'box_80', rosen: 'truck', jitbox: 'roll_cage', rail: 'freight_train', ferry: 'ferry' };
// 公表運賃から出した額は百円の単位に丸め、「試算」と付けて出す（SPEC §30.1）
const ESTIMATE_GROUPS = ['takuhai', 'rosen', 'jitbox'];
const round100 = (v) => (v === null || v === undefined ? v : Math.round(v / 100) * 100);
const SHORT_LABEL = { takuhai: '宅配', rosen: '路線便', jitbox: 'カゴ台車' };

const cardModel = (key, r) => (key !== 'kashikiri' ? CARD_MODEL[key] : r.detail && r.detail.vehicle ? M3D.byVehicle(r.detail.vehicle) : null);

// /api/estimate の行からカードを作る。金額・状態・理由・出典・日数はすべて API の値。
// 船の経路が無いか。routes.js（Rt）の状態をそのまま読む。経路の読み込み前は false（問い合わせ）
const shipNoneNow = () => !!(typeof Rt !== 'undefined' && Rt.cur && Rt.cur.sets && Rt.cur.sets.ferry && Rt.cur.sets.ferry.ok === false);

// 手段ごとの API の行。貸切は車格ごとに行があるので、載る車格を安い順にすべて返す（3.5m の荷なら 2t が要確認でも 4t が条件に合う、を落とさない）。
// 条件に合う（公開条件に合う）最初の車格までを出し、それより大きい車は出さない。1 つも合わなければ額の出る車格すべて。載る車格がなければ最初の行
function rowsOfMode(group, rows) {
  const all = rows.filter((x) => x.group === group);
  if (group !== 'kashikiri') return all.slice(0, 1);
  const usable = all.filter((x) => x.amount !== null && x.amount !== undefined && x.status !== '使えない');
  if (!usable.length) return all.slice(0, 1);
  const k = usable.findIndex((x) => x.status === '公開条件に合う');
  return k < 0 ? usable : usable.slice(0, k + 1);
}

function buildCards(i, est) {
  const cards = [];
  S.shipNone = shipNoneNow();
  const srcOf = (r) => (r.source && r.source.name ? [r.source.name, r.source.url || ''] : null);
  for (const m of MODES) {
    if (m.key === 'rail' || m.key === 'ferry') {
      const hn = Hubs.nearest(S.origin, i);
      cards.push({ label: m.label, key: m.key, routeNone: m.key === 'ferry' && S.shipNone, model: CARD_MODEL[m.key], status: '計算できない', amount: null, dim: true, hub: hn && !(m.key === 'ferry' && S.shipNone) ? { kind: m.key, ...hn[m.key] } : null, // 航路が無い着地では、最寄りの港と点線のボタンは出さない（レビュー F5）
       
        none: m.key === 'ferry' && S.shipNone ? 'このデモの航路の表に入っていません。船会社に見積を頼んでください。' : '料金表がないため、各社に問い合わせてください。',
        tip: m.key === 'rail' ? 'JR 貨物の運賃は公開の決まりで載せられないため、金額は出していません。' : 'フェリーの運賃は確かめられなかったため、金額は出していません。' });
      continue;
    }
    for (const r of rowsOfMode(m.key, est.rows)) {
    const notes = [].concat(r.notes, r.issues.map((x) => x.message).filter((x) => !r.notes.includes(x)));
    const days = r.transit ? (r.transit.published || m.key === 'jitbox' ? r.transit.display : r.transit.text) : null;
    if (r.amount === null || r.amount === undefined) {
      cards.push({ label: m.label, model: cardModel(m.key, r), status: r.status, amount: null, none: r.reasons[0] || '料金表がないため、各社に問い合わせてください。', days,
        tip: [...new Set(r.reasons.concat(notes))].join(' / '), src: srcOf(r) });
      continue;
    }
    let tip = notes.join('。');
    let dayText = days;
    if (m.key === 'kashikiri') {
      const g = est.geo, o = est.osrm_kashikiri;
      if (est.kashikiri_basis === 'road') tip = `道路で ${est.distance_km !== undefined && est.distance_km !== null ? est.distance_km : (g && g.km)} km として、国の標準的運賃で計算しました` + (tip ? `。${tip}` : '');
      else if (est.route !== 'other') tip = `${est.assumed_km} km として計算した国の目安です` + (tip ? `。${tip}` : '');
      else tip = `道路で ${g.km} km として計算した国の目安です。会社ごとに違います`;
      if (o && est.route !== 'other' && o.amount !== r.amount) tip += `。実際の道のりの ${o.km} km で計算すると ${fmt(o.amount)} 円です`;
      if (g && g.days) dayText = `運転だけで ${g.days} 日（積み降ろしと荷待ちは別）`;
      if (g && g.origin_note) tip += `。${g.origin_note}`;
      if (tip && !/[。]$/.test(tip)) tip += '。';
    }
    const isEst = ESTIMATE_GROUPS.includes(m.key);
    // 路線便の試算が出せる区間の限り（比べるページには額が無い理由）は、吹き出しだけでなくカードの本文に出す（SPEC §32 #5）
    const rosenNote = m.key === 'rosen' ? r.notes.find((x) => x.startsWith('路線便の試算は')) : null;
    cards.push({ label: m.label, model: cardModel(m.key, r), sub: r.detail && r.detail.vehicle ? `${r.detail.vehicle}車` : '', status: r.status, note: rosenNote || (r.detail && r.detail.load_note) || '',
      sample: /架空の表/.test(`${r.label || ''}${r.source && r.source.name || ''}`),   // 架空の表から出した額か（画面にも「架空の表の例」と出す）
      amount: isEst ? round100(r.amount) : r.amount, low: isEst ? round100(r.low) : r.low, high: isEst ? round100(r.high) : r.high, est: isEst, days: dayText, tip, why: r.status === '要確認' ? ((r.reasons && r.reasons[0]) || (r.issues && r.issues[0] && r.issues[0].message) || '条件を確かめてください') : '', src: srcOf(r),
      pick: m.key === 'kashikiri' && r.detail && r.detail.vehicle ? `${m.label} ${r.detail.vehicle}車` : undefined });
    }
  }
  return cards;
}

// 鉄道とフェリーのカード: 出発地・着地それぞれの最寄りの貨物駅・港と直線距離（Python が計算済み。運賃は出さない）
function hubHtml(h) {
  const nm = h.kind === 'rail' ? '貨物駅' : '港';
  const row = (lbl, x) => `<div class="hrow"><span class="k">${lbl}</span><span class="v">${escapeHtml(x.hub.name)}</span><span class="d num">直線で ${x.km.toFixed(1)} km</span></div>`;
  return `<div class="hubinfo">${row(`集荷地に近い${nm}`, h.o)}${row(`届け先に近い${nm}`, h.d)}` +
    `<button class="hub-btn" data-hub="${h.kind}" aria-pressed="${S.hubSel === h.kind}">地図に出す</button>` +
    `<div class="hub-note">直線の距離です（${h.kind === 'rail' ? '線路' : '航路'}の長さとは別）。<br>点線はつなぎ方の目安です。</div></div>`;
}

// 額が出ているのが貸切だけのとき、荷の重さ・床が車のどれだけかを出す。小さな荷は「この荷に貸切は過大」（比べるページと同じ基準: 重さも床も 25% 未満）。
// 車の値は data/vehicle_decks.json の料金の区分（2t・4t は表の上限、10t）と荷台（2t ショート・4t ウイング・10t ウイング）と同じ。試験 test_v42 が合っていることを確かめる
const CHARTER_CLASS = { '2t': { kg: 2000, floor: 5.1 }, '4t': { kg: 4000, floor: 13.64 }, '10t': { kg: 10000, floor: 23.04 } };
function charterOnlyNote(cards) {
  const priced = cards.filter((c) => c.amount !== null && c.amount !== undefined);
  if (!priced.length || !priced.every((c) => c.label === '貸切')) return '';
  const cls = CHARTER_CLASS[String(priced[0].sub || '').replace('車', '')], sh = Work.confirmedShipment();
  if (!cls || !sh || !sh.items || !sh.items.length) return '';
  let kg = 0, floor = 0;
  for (const it of sh.items) { kg += Number(it.count) * Number(it.weight_kg); floor += Number(it.count) * Number(it.length_cm) * Number(it.width_cm) / 1e4; }
  if (!(kg > 0)) return '';
  const pc = (v) => Math.max(1, Math.round(v * 100));
  const kgUse = kg / cls.kg, floorUse = floor / cls.floor, over = kgUse < 0.25 && floorUse < 0.25;
  return `<div class="cards-note charter-only${over ? ' excessive' : ''}" role="note"><b>${over ? 'この荷物に貸切は大きすぎます。' : '金額が出るのは貸切だけです。'}</b> ` +
    `荷物は${priced[0].sub}の積める重さの ${pc(kgUse)}%、荷台の床の ${pc(floorUse)}% を使います。<br>宅配・路線便: 料金表なし</div>`;
}

function renderCards(cards) {
  const max = Math.max(0, ...cards.map((c) => c.amount || 0));
  const exNote = typeof Work !== 'undefined' && Work.isExample && Work.isExample() ? '<p class="cards-note ex-note" role="note"><b>例の荷物（架空）で計算しています。</b>自分の荷物に変えてください（左の「荷物」）。</p>' : '';
  $('#cards').innerHTML = exNote + (typeof CompareChart !== 'undefined' ? CompareChart.html(cards, S.pickLabel, document.body.classList.contains('guided')) : '') + charterOnlyNote(cards) + '<details class="cards-more"><summary>送り方ごとの金額の出どころ</summary>' + cards.map((c, n) => {
    const amt = c.amount !== null && c.amount !== undefined
      ? `<div class="amt"><b class="num" data-to="${c.amount}">${fmt(c.amount)}</b><span>円（税抜${c.est ? '・試算' : ''}${c.sample ? '・架空の表の例' : ''}）</span>` +
        (c.low && c.high ? `<span class="range num">${fmt(c.low)}〜${fmt(c.high)} 円の幅</span>` : '') + '</div>' +
        `<div class="bar"><i data-w="${Math.max(3, (c.amount / max) * 100).toFixed(1)}"></i></div>`
      : '';
    const meta = c.days ? `<div class="meta"><span>日数</span><span class="v" title="${escapeHtml(c.days)}">${escapeHtml(c.days)}</span></div>` : '';
    const note = c.note ? `<div class="note">${escapeHtml(c.note)}</div>` : '';
    const src = c.src && !c.src[1] ? `<span class="src" title="${escapeHtml(c.src[0])}">出典: ${escapeHtml(c.src[0])}</span>` : c.src ? `<a class="src" href="${escapeHtml(c.src[1])}" target="_blank" rel="noopener" title="${escapeHtml(c.src[0])}">出典: ${escapeHtml(c.src[0])}</a>` : '';
    // 補足は押す・Enter で本文として開く（title だけに置かない。レビュー F06）
    const tip = c.tip ? `<button type="button" class="tip" aria-expanded="false" aria-controls="tip-${n}">補足</button>` : '';
    const foot = src || tip ? `<div class="foot">${src}${tip}</div>` + (c.tip ? `<div class="tip-body" id="tip-${n}" hidden>${escapeHtml(c.tip)}</div>` : '') : '';
    const basis = (c.tip || '').split('。').filter((x) => /想定距離|道路距離|国の目安です|国の標準的運賃で計算しました|積付けは公表例の荷台での目安です/.test(x)).map((x) => `<div class="note basis">${escapeHtml(x)}</div>`).join('');
    const none = c.amount === null || c.amount === undefined ? `<span class="amt-none">${escapeHtml(c.none)}</span>` : '';
    const hub = c.hub ? hubHtml(c.hub) : '';
    const th = c.model ? M3D.thumb(c.model, 80, 50, 'mth-card') : '';
    return `<article class="card${c.dim ? ' dim' : ''}${none ? ' none' : ''}" style="--n:${n}"><div class="top">${th}<h3>${c.label}${c.sub ? `<small>${c.sub}</small>` : ''}</h3>` +
      `${none}<span class="chip ${CHIP[c.status] || 'na'}">${CHIP_WORD[c.status] || c.status}</span></div>${amt}${note}${basis}${meta}${hub}${foot}</article>`;
  }).join('') + '</details>';
  // 案内の表示は操作部品を 8 つまでに抑えるため、鉄道・船・飛行機の選べる欄は閉じた「手段ごとの内訳」の中へ移す。表には 1 行の説明（cc-alt-line）を残す
  if (document.body.classList.contains('guided')) { const alt = document.querySelector('#cards .cc-alt'), more = document.querySelector('#cards .cards-more'); if (alt && more) more.insertBefore(alt, more.children[1] || null); }
  syncPick();
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

// 送り方の選択（比べの表の丸ボタン）。選んだあとにだけ「見積依頼文を作る」を出す
function syncPick() {
  const r = document.querySelector('#cards input[name="cc-pick"]:checked'), btn = $('#make-request'), hint = $('#pick-hint');
  S.pickLabel = r ? r.value : null;
  if (btn) btn.hidden = !r;
  if (hint) hint.textContent = r ? `選んだ送り方: ${r.value}（このまま依頼文を作れます）` : '送り方を1つ選ぶと、依頼文を作れます。';
}

function select(i, { fly = true, fromSearch = false } = {}) {
  if (i < 0) return;
  const od = curOd(), code = S.idx.codes[i];
  if (code === S.origin) { toast('集荷地と同じ市区町村です。別の届け先を選んでください。'); return; }
  if (S.sel !== i) S.pickLabel = null;   // 着地が変わったら、選んだ送り方は選び直す
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
  syncPick();
  writeHash();
}

function renderDetail(i, demo) {
  const od = curOd(), r = S.route;
  const ferry = !!(od.ferry && od.ferry[i]);
  const km = demo ? demo.km : od.km[i], min = ferry ? null : demo ? demo.minutes : od.min[i];   // 船・飛行機でしか行けない着地は道路の運転時間を出さない（下の帯のトラックの区間と食い違うため）
  const days = ferry ? null : demo ? Math.max(1, Math.ceil(demo.minutes / 540)) : od.days[i];
  $('#d-from').textContent = od.name;
  $('#d-to').innerHTML = `<span class="ar">→</span><small>${escapeHtml(S.idx.prefs[i])}</small> ${escapeHtml(S.idx.names[i])}`;
  const kk = $('#s-km').previousElementSibling;
  if (kk) kk.textContent = ferry ? '経路の距離（海を含む）' : '道路距離';   // 海を渡る経路は「道路距離」と呼ばない
  $('#s-km').innerHTML = km === null ? '—' : `${km.toFixed(1)}<small>km</small>`;
  $('#s-time').innerHTML = min === null ? (ferry ? '—<small>船の時間は見積で確認</small>' : '—') : fmtMin(min).replace(/(\d+)(時間|分)/g, '$1<small>$2</small>');
  $('#s-days').innerHTML = days === null ? '—' : `${days}<small>日</small>`;
  $('#route-kind').textContent = '距離と日数の数え方を見る';
  const rn = document.querySelector('.route-note p');
  if (rn) rn.textContent = (ferry ? '船を使う道かもしれないため、運転日数と貸切の額は出していません。' : '') +
    '道のりは地図データから出した目安で、大型車が通れるとは限りません。日数は1日9時間の運転で数え、積み降ろし・荷待ち・休憩は入れていません。';
  S.estimate = null;
  $('#cards').scrollTop = 0;
  refreshEstimate();
}

const estimateBody = () => ({ shipment: Work.confirmedShipment(), origin_code: S.origin, dest_code: S.idx.codes[S.sel] });
// 右のカードは、確定した荷物と着地で /api/estimate を呼び直して作る。荷物・出発地・着地が変わるたびに呼ぶ。
async function refreshEstimate() {
  if (S.sel < 0) return;
  const i = S.sel, tok = ++S.estTok;
  if (!document.querySelector('#cards .card')) $('#cards').innerHTML = '<p class="cards-note">費用を計算しています</p>';
  try {
    // 荷物の欄に確定していない変更があるときは、その場で確定して同じ荷物で計算する。確定できないときは、別の荷物の額を出さずに理由を出す
    const pend = await Work.syncPending();
    if (tok !== S.estTok || S.sel !== i) return;
    if (pend) { S.estimate = null; $('#cards').innerHTML = `<p class="cards-note err">荷物の変更を確定してください（${escapeHtml(pend)}）。いまの結果は、確定した前の荷物のものではなく、出していません。</p>`; return; }
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
    S.fareVehicleNote = r.total_kg === null ? '2t・税抜' : `合計 ${Math.round(r.total_kg)} kg が載るいちばん小さい車・税抜`;
    if (o === S.origin) { S.version++; renderLegend(); }
  } catch (e) { toast('貸切の運賃の色を出せませんでした。'); }
}

// 送り方（表の行の名前）が、いまの荷物では使えないとき、その理由。表と同じ /api/estimate の行を読む。使えるか分からないときは空
const MODE_GROUP = { 宅配: 'takuhai', 路線便: 'rosen', カゴ台車: 'jitbox', 貸切: 'kashikiri' };
function modeProblem(mode) {
  const g = MODE_GROUP[mode], rows = S.estimate && S.estimate.rows ? S.estimate.rows.filter((x) => x.group === g) : [];
  if (!rows.length || !rows.every((x) => x.status === '使えない')) return '';
  return (rows[0].reasons && rows[0].reasons[0]) || '表で「使えない」になっています';
}
function onCargoChange() {
  S.fareStamp = {};
  refreshFareLayer();
  if (S.sel >= 0) { S.estimate = null; refreshEstimate(); }
}

// 手段ごとの経路の読み込み・取得が進んだとき（routes.js から）。帯を作り直し、最初の 1 回は地図を経路に合わせる
App.onRt = (first) => {
  setupTimebar();
  // 横棒の図の船の行は、経路の有無で「なし」か「問い合わせ」を変える。経路の読み込みが終わってから変わったときだけ、カードを作り直す
  if (S.estimate && S.sel >= 0 && shipNoneNow() !== S.shipNone) renderCards(buildCards(S.sel, S.estimate));
  if (first && S.pendingFit && S.view === 'compare' && S.sel >= 0) { S.pendingFit = false; Rt.fit(); }
  syncGuideBar();   // 案内の凡例の手段ごとの線の見本を、経路の読み込みに合わせる
};

function deselect() {
  Rt.clear();
  S.sel = -1; S.selFeature = null; S.route = null; S.t = 0; S.pickLabel = null;
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
  // 案内の表示では条件の欄が閉じている（left が 36 になる）ので、届け先の端が左の端で切れないよう余白を取る
  const g = document.body.classList.contains('guided');
  return { top: Math.round(h * 0.1), bottom: 190, left: g ? Math.max(left, w > 600 ? 130 : 24) : Math.min(left, w * 0.34), right: dw + 70 };
}
function fitRoute() {
  const pts = S.route.path;
  let x0 = 999, y0 = 999, x1 = -999, y1 = -999;
  for (const p of pts) { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); }
  const a = pts[0], b = pts[pts.length - 1];
  map.fitBounds([[x0, y0], [x1, y1]], { padding: padding(), maxZoom: 8.6, ...MapFx.cam(2000, { curve: 1.5 }), ...MapFx.selectCam(a, b, 1900) });
}
/* 案内の表示で、地図に重なる欄を除いた「地図の空き」を、fitBounds の余白（画素）で返す。
   左の欄（狭い画面では下の欄）、上の帯、下の凡例、開いている県のカード（右上。狭い画面では上いっぱい）を避ける。market.js の区間の寄せもこれを使う */
function guideFree() {
  const w = innerWidth, h = innerHeight, vis = (e) => (e && e.getClientRects().length ? e.getBoundingClientRect() : null);
  let l = 16, t = 16, r = 16, b = 16;
  const bar = vis($('#guide-bar')); if (bar) t = Math.max(t, Math.round(bar.bottom + 12));
  const lg = vis($('#g-legend')); if (lg && lg.top > h * 0.5 && lg.width > w * 0.5) b = Math.max(b, Math.round(h - lg.top + 12));
  const pn = vis($('#m-guide'));
  if (pn) { if (pn.width > w * 0.8) b = Math.max(b, Math.round(h - pn.top + 12)); else l = Math.max(l, Math.round(pn.right + 24)); }
  const card = vis(document.getElementById('pref-card'));
  if (card) {
    if (card.width <= w * 0.8) r = Math.max(r, Math.round(w - card.left + 16));
    else if (!document.body.classList.contains('m-has-pair')) t = Math.max(t, Math.round(card.bottom + 10));   // 狭い画面ではカードが上いっぱいに出る。区間を選んでいるときは、両端が見える範囲が残らないのでカードを避けない
  }
  return { top: t, bottom: b, left: l, right: r };
}
window.guideFree = guideFree;
addEventListener('m-cardchange', () => {   // 県のカードを開閉したら、カードに隠れないよう地図を寄せ直す（案内の全国の表示だけ）
  if (S.view !== 'market' || !document.body.classList.contains('guided')) return;
  if (innerWidth <= 600 && V.Market.selected()) return;   // 狭い画面で区間を選んでいるときは、カードを開いても地図を動かさない
  setTimeout(() => { if (V.Market.selected()) V.Market.refit(); else fitJapan(true); }, 0);
});
function fitJapan(animate) {
  const guided = MapFx.guided3d();
  const pad = guided && S.view === 'market' && innerWidth > 1100 ? { top: 70, bottom: 130, left: 420, right: 60 }
    : S.view === 'market'
    ? { top: 165, bottom: 100, left: 400, right: ($('#m-right').offsetWidth || 480) + 90 }
    : S.view === 'find'
      ? { top: 60, bottom: 100, left: 420, right: ($('#find-right').offsetWidth || 480) + 70 }
    : S.view === 'own'
      ? { top: 60, bottom: 130, left: 480, right: ($('#own-right').offsetWidth || 540) + 70 }
    : { top: 20, bottom: 110, left: 60, right: 110 };
  if (guided && S.view === 'market') {   // 案内の全国の物流傾向: 九州（鹿児島）から北海道までの全体を、左の欄・上の帯・下の凡例・県のカードを除いた範囲の中央に収める
    map.fitBounds(GUIDE_JP_BOUNDS, { padding: guideFree(), ownPadding: true, maxZoom: 7.4, ...MapFx.rest(), ...MapFx.cam(animate ? 1800 : 0) });
    return;
  }
  if (guided) {   // 傾けると上下が縮んで日本が小さくなるので、傾けない場合の収まりより少し寄る（傾けた分の高さを使う）
    const cam = map.cameraForBounds(JAPAN_BOUNDS, { padding: pad, bearing: MapFx.rest().bearing });
    if (cam) { map.easeTo({ center: cam.center, zoom: Math.max(3.4, cam.zoom + (innerWidth > 600 ? 0.32 : 0.2)), ...MapFx.rest(), ...MapFx.cam(animate ? 1800 : 0) }); return; }
  }
  map.fitBounds(JAPAN_BOUNDS, { padding: pad, ...MapFx.cam(animate ? 1800 : 0) });
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
      const k = n.startsWith(v) ? 0 : n.includes(v) ? 1 : pn.includes(v) ? 2 : -1;
      if (k < 0) continue;
      res.push([S.routes[`${S.origin}-${S.idx.codes[i]}`] ? k - 3 : k, i]); // デモの区間を先頭に（候補から外さない。並べ替えのときだけ小さい値にする）
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
  const pick = (i) => {
    close(); q.blur(); select(i, { fromSearch: true });
    // 案内の表示: 届け先を選んだあとのフォーカスは、結果の欄の次の主ボタン（見積依頼文を作る）へ移す（body に落とさない）
    if (S.guide) {
      let n = 0;
      const t = setInterval(() => {
        const b = $('#make-request');
        if (b && !b.hidden && !b.closest('[inert]')) { b.focus({ preventScroll: true }); clearInterval(t); } else if (++n > 40) clearInterval(t);
      }, 100);
    }
  };
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
  if (S.view === 'home') return;   // 最初の画面ではアドレスを書かない（戻る・進むの履歴を壊さない）
  if (S.view === 'market') { V.Market.syncHash(); return; }
  const p = new URLSearchParams();
  p.set('v', S.view);
  if (S.view === 'find') V.Find.hashParams(p);
  if (S.view === 'own') V.Own.hashParams(p);
  if (S.guide) p.set('g', S.guide);
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
  try { await loadOrigin(code); } catch (e) {   // この出発地のデータだけ読めなかった。ほかの出発地と見方は使える
    console.error(e);
    $('#origin').value = S.origin;
    showLoadNotice(`集荷地を変えられませんでした。前の集荷地のままです。`, () => setOrigin(code));
    return;
  }
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
  for (const v of ['market', 'find', 'compare', 'own', 'home']) c.toggle('view-' + v, S.view === v);
  document.querySelectorAll('#views button[data-view]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.view === S.view)));
  applyGuide();
  if (S.view !== 'home') { const hv = document.querySelector('.home-hero video'); if (hv) hv.remove(); if (window.Hero3D) Hero3D.stop(); }   // 最初の画面を離れたら背景の映像と 3D は止める（3D は資源も解放）
  else if (typeof loadHeroLoop === 'function' && document.readyState === 'complete') loadHeroLoop();   // 最初の画面へ戻ったら、また 3D を始める
}
/* 案内の表示の地図は傾けた 3D（map_fx.js の rest）。入るときに傾け、出る（全部を見る・別の見方）ときに真上へ戻す。乗り物は v2 の 3D に替える（models3d.js の skin） */
let was3d = false;
function syncTilt() {
  if (typeof map === 'undefined' || !map) return;
  const now = MapFx.guided3d();
  if (now === was3d) return;
  was3d = now;
  try { M3D.setSkin(now); } catch (e) { /* 3D の部品が無いとき */ }
  if (now) { if (S.view === 'market' && !V.Market.selected()) fitJapan(true); else map.easeTo({ ...MapFx.rest(), duration: MapFx.ms(1200) }); }   // 全国の物流傾向は、傾けながら上位 5 区間に寄せる
  else if (map.getPitch() > 0.5 && !(S.view === 'market' && V.Muni && V.Muni.state && V.Muni.state.col3d)) map.easeTo({ pitch: 0, bearing: 0, duration: MapFx.ms(900) });
  setBasemapGuide(now);
}
// 案内の表示では、国外の地名と県名の名札（地図の上の名札と二重になる）を出さない。filter は元の値を覚えておいて戻す
const BASEMAP_ORIG = new Map();
const JAPAN_BOXES = [[129.2, 30.8, 132.2, 34.3], [130.8, 33, 135.2, 36.2], [135, 33, 142.6, 41.8], [139, 38, 146.2, 45.8], [122.8, 24, 131.6, 27.4]];
const JAPAN_WITHIN = { type: 'MultiPolygon', coordinates: JAPAN_BOXES.map(([x0, y0, x1, y1]) => [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]) };
function setBasemapGuide(on) {
  try {
    for (const l of map.getStyle().layers) {
      if (l.type !== 'symbol' || !/^label_/.test(l.id) || /water/.test(l.id)) continue;
      if (!BASEMAP_ORIG.has(l.id)) BASEMAP_ORIG.set(l.id, { f: l.filter || null });
      const o = BASEMAP_ORIG.get(l.id);
      if (on) {
        if (l.id === 'label_state') map.setLayoutProperty(l.id, 'visibility', 'none');
        else map.setFilter(l.id, o.f ? ['all', o.f, ['within', JAPAN_WITHIN]] : ['within', JAPAN_WITHIN]);
      } else {
        if (l.id === 'label_state') map.setLayoutProperty(l.id, 'visibility', 'visible');
        else map.setFilter(l.id, o.f);
      }
    }
  } catch (e) { /* 背景の地図の層が違うとき（地名の絞り込みだけ諦める） */ }
}
// 案内の表示（SPEC §44 追記）: 目的から入ったときは、その目的に答えるものだけを出す。#...&g=... があるとき。自社の運行は own.js が自分で扱う
// 案内の表示では、地図の canvas と出典のリンクを最初の Tab の止まり先から外す（案内の欄の操作に先に届くように）。全部を見るに戻したら元へ。自社の運行は own.js が自分で扱う
function syncMapTabStops() {
  const b = document.body.classList;
  if (b.contains('own-guided') || b.contains('view-own')) return;
  const on = b.contains('guided');
  for (const e of document.querySelectorAll('.maplibregl-canvas, .maplibregl-ctrl-attrib a')) {
    if (on) e.setAttribute('tabindex', '-1'); else if (e.classList.contains('maplibregl-canvas')) e.setAttribute('tabindex', '0'); else e.removeAttribute('tabindex');
  }
}
function applyGuide() {
  document.body.classList.toggle('guided', !!S.guide && ['compare', 'market', 'find'].includes(S.view));
  syncTilt();
  syncMapTabStops();
  const ac = document.querySelector('.maplibregl-ctrl-attrib');
  if (ac && !ac.dataset.tabWatch && window.MutationObserver) { ac.dataset.tabWatch = '1'; new MutationObserver(syncMapTabStops).observe(ac, { childList: true, subtree: true }); }   // 出典の欄は地図の読み込みで作り直されるので、そのたびに合わせる
  const t = document.getElementById('g-toggle');
  if (t) t.textContent = '全部を見る';
  syncGuideBar();
}
// 案内の表示の 1 行の凡例と、送り方を比べるの段の表示（1 条件 → 2 結果 → 3 見積依頼文）
const GUIDE_LEGEND = {
  // 先頭の 3 項目だけを常に出す。残りは「凡例・出典」を開いたときに出る
  market: '<span class="g-k"><i class="sw-line"></i>青い線: 荷が多い向き</span><span class="g-k"><i class="sw-dash"></i>橙の点線: 帰りが空く向き</span><span class="g-k g-more"><i class="sw sw-out"></i>出荷が多い県<i class="sw sw-in g-sw2"></i>入荷が多い県</span><span class="g-k g-src">出典: 物流センサス（国土交通省、e-Stat）を加工して作成</span>',
  find: '<span class="g-k">細い線は帰りの空きやすさの統計です。便はすべて例です。</span>',
};
const LAYER_WORD = { km: '道路距離', days: '運転日数', fare: '貸切の目安額' };
function syncGuideBar() {
  const b = document.body.classList, g = b.contains('guided');
  const lg = document.getElementById('g-legend'), st = document.getElementById('g-steps');
  if (lg) {
    const html = !g ? '' : S.view === 'compare'
      ? `${b.contains('has-work') ? '' : `<span class="g-k">色は${LAYER_WORD[S.layer] || '道路距離'}（値が大きいほど明るい水色）</span>`}${(Rt.guideLegend && S.sel >= 0 ? Rt.guideLegend() : '')}<span class="g-k g-more">線と車の絵は道の目安で、実際の便ではありません。</span>`
      : (GUIDE_LEGEND[S.view] || '');
    if (lg.dataset.k !== html) { lg.dataset.k = html; lg.innerHTML = html; }
    lg.hidden = !html;
  }
  const at = document.getElementById('attr-toggle');
  if (at) { const w = g && (S.view === 'market' || S.view === 'compare') ? '色の見方と出典' : '出典'; if (at.textContent !== w) at.textContent = w; }
  if (st) {
    // 段の表示: 送り方を比べる（条件 → 結果 → 見積依頼文）、全国の物流傾向と空きを探す（区間を選ぶ → 空きを探す）
    const on = g && (S.view === 'compare' || S.view === 'market' || S.view === 'find');
    const n = S.view === 'find' ? 2 : S.view === 'market' ? 1 : b.contains('has-work') ? 3 : b.contains('has-detail') ? 2 : 1;
    const labels = S.view === 'compare' ? ['荷物と行き先', '送り方と費用', '見積の依頼文'] : ['区間を選ぶ', '空いた帰り便を探す'];
    const key = on ? `${S.view}${n}` : '';
    if (st.dataset.n !== key) {
      st.dataset.n = key;
      st.innerHTML = on ? labels.map((t, i) => `<li${i + 1 === n ? ' aria-current="step"' : ''}><b>${i + 1}</b> ${t}</li>`).join('') : '';
    }
    st.hidden = !on;
  }
}
function setGuide(g) {
  S.guide = g || '';
  if (S.view === 'own' && !S.guide && V.Own && V.Own.setGuided) V.Own.setGuided(false, '');   // 自社の運行の「全部を見る」
  applyGuide();
  if (S.view === 'market') { V.Market.syncHash(); const p = new URLSearchParams(location.hash.slice(1)); if (S.guide) p.set('g', S.guide); else p.delete('g'); history.replaceState(null, '', '#' + p.toString()); } else writeHash();
  if (map) requestAnimationFrame(() => map.resize());
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
  if (prev === 'market') V.Market.setActive(false);
  if (prev === 'find') V.Find.leave();
  if (prev === 'own') V.Own.leave();
  $('#hover-tag').classList.remove('on');
  S.view = v;
  applyViewClasses();
  if (v === 'home') { setHubLegend(); return; }   // 最初の画面。地図や見方の開始はしない（選ばれたときに始める）
  if (prev === 'home') { map.resize(); requestAnimationFrame(() => focusHeading(v)); }
  if (v === 'compare') {
    c.add(...(S.savedFlags || []));
    Rt.resume();
    if (S.sel >= 0 && S.route) { if (Rt.hasSets()) Rt.fit(); else fitRoute(); } else fitJapan(true);
  } else if (v === 'market') {
    V.Market.setActive(true);
    const r = V.Market.selected();
    if (r) V.Market.select(r.k, { fly: true }); else fitJapan(true);
  } else if (v === 'own') {
    V.Own.enter();
  } else {
    fitJapan(true);
    V.Find.enter((prev === 'market' ? V.Market.pairNames() : null) || V.Find.pairFromHash(new URLSearchParams(location.hash.slice(1))));
  }
  writeHash();
  setHubLegend();
}
App.setView = setView;
App.writeHash = () => writeHash();
App.repickPair = () => {   // 空きを探すから、全国の物流傾向（案内）へ戻って区間を選び直す。いま選んでいる区間は選んだままにする
  let k = '';
  try { const pr = V.Find.state.pair, ix = V.Market.state.prefIdx; if (pr && ix && ix.has(pr.from) && ix.has(pr.to)) k = `${ix.get(pr.from)}-${ix.get(pr.to)}`; } catch (e) { k = ''; }
  goHash('#v=market&g=1' + (k ? `&p=${k}` : ''));
};
const HEADING_OF = { compare: '#panel .p-title', market: '#m-now-h', find: '#find-view .p-title', own: '#own-view .p-title' };
function focusHeading(v) {
  const sel = (S.guide && v === 'market') ? '#mg-h' : (HEADING_OF[v] || '');
  // 自社の運行の案内では見出し（.p-title）を隠して手順ごとの見出しを出すので、見えている最初の見出しに移す（案内では右の手順の欄 #own-sheet の見出し）
  const go = () => {
    const list = [...document.querySelectorAll(v === 'own' ? '#own-view .p-title, #own-view h2, #own-view h3, #own-sheet h2, #own-sheet h3' : sel)];
    const h = list.find((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
    if (!h) return;
    if (!h.hasAttribute('tabindex')) h.setAttribute('tabindex', '-1');
    h.focus({ preventScroll: true });
  };
  go();
  // 表示の組み立て（自社の運行など）の途中でフォーカスが body に落ちることがあるので、落ちていたら短い間隔で数回かけ直す
  for (const ms of [250, 700, 1500]) setTimeout(() => { const a = document.activeElement; if (!a || a === document.body) go(); }, ms);
}
// 送り方を比べるへ移り、発地（無ければ今のまま）と着地を選ぶ（市場を見る・空きを探すから、SPEC §30.3・§30.5）
App.compareTo = async (originCode, destCode) => {
  setView('compare');
  if (originCode && originCode !== S.origin && S.meta.origins.some((o) => o.code === originCode)) await setOrigin(originCode);
  const i = S.idx.codes.indexOf(destCode);
  if (i >= 0) select(i, { fly: true });
};

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
      map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]], { padding: padding(), maxZoom: 8.6, ...MapFx.cam(1600, { curve: 1.4 }) });
    }
  }
}

// 画質の段が変わったとき（と起動時）: 描画の解像度比を合わせる
function applyQuality() {
  const r = PERF.pixelRatio();
  try {
    if (map && map.getPixelRatio && map.setPixelRatio) {
      const c = map.getCanvas();
      if (map.getPixelRatio() !== r) map.setPixelRatio(r);
      // 作成時の pixelRatio が描画バッファに反映されず、端末の拡大率のままになることがある（getPixelRatio() は r を返すのに canvas.width は DPR 倍）。
      // 食い違うと deck の層が地図に対して左上基準で拡大して重なる（DPR 2 の録画で日本が二重に見えた）。バッファの大きさを作り直して合わせる
      if (c && c.clientWidth > 0 && Math.abs(c.width - Math.round(c.clientWidth * r)) > 1) map.resize();
    }
  } catch (e) { console.error(e); }
  if (overlay) {
    // 重ね合わせ（interleaved）では deck は地図の描画バッファをそのまま使うので、実際のバッファの解像度比を渡す
    let eff = r;
    try { const c = map.getCanvas(); if (c.clientWidth > 0) eff = c.width / c.clientWidth; } catch (e) { /* 地図が無いとき */ }
    overlay.setProps({ useDevicePixels: eff });
  }
  try { map.triggerRepaint(); } catch (e) { /* 地図が無いとき */ }
}

/* 部品（スクリプト）の読み込みに失敗したとき。読み直し（index.html）でも読めなかった部品がある見方だけを止め、ほかの見方は起動する（SPEC §42）。
   見方の部品が無いときは、その見方の名前に代わりの空の部品を置き、その見方を開いたときだけ理由を出す */
const CORE_MODULES = { PERF: () => typeof PERF, Hubs: () => typeof Hubs, Rt: () => typeof Rt, Work: () => typeof Work, Sheet: () => typeof Sheet, WARM: () => typeof WARM, M3D: () => typeof M3D };
const VIEW_MODULES = {
  market: { label: '全国の物流傾向', host: '#m-left', mods: { Market: () => typeof Market, Muni: () => typeof Muni } },
  find: { label: '空きを探す・つなぐ', host: '#find-view', mods: { Find: () => typeof Find } },
  own: { label: '自社の運行', host: '#own-view', mods: { Own: () => typeof Own, OwnCalc: () => typeof OwnCalc, OwnMonth: () => typeof OwnMonth, WaitingShared: () => typeof WaitingShared, WaitingMap: () => typeof WaitingMap } },
};
function showViewMissing(view) {
  const info = S.missingViews && S.missingViews[view];
  if (!info) return;
  const host = document.querySelector(VIEW_MODULES[view].host);
  if (!host) { toast(info.text); return; }
  let el = host.querySelector(':scope > .view-missing');
  if (!el) {
    el = document.createElement('p'); el.className = 'view-missing'; el.setAttribute('role', 'alert'); host.prepend(el);
    const b = document.createElement('button'); b.type = 'button'; b.className = 'retry'; b.textContent = '読み直す'; b.addEventListener('click', () => location.reload());
    el.append(document.createElement('span'), b);
  }
  el.firstChild.textContent = info.text + ' ';
}
// 見方に必要なデータが読めなかったとき、その見方だけを「使えない」にして、ほかの見方は起動する
function softFail(view, e) {
  console.error(e);
  S.missingViews = S.missingViews || {};
  S.missingViews[view] = { mods: [], text: `この画面を開けませんでした。ほかの画面は使えます。` };
  return null;
}
const STUBS = {};
const viewStub = (view) => STUBS[view] || (STUBS[view] = new Proxy({}, { get: (_, key) => (key === 'then' ? undefined : (...a) => { if (key === 'enter' || (key === 'setActive' && a[0])) showViewMissing(view); return ['flowLayers', 'layers', 'compareFac'].includes(key) ? [] : undefined; }) }));
// 見方の部品への入口。部品が揃っていればその部品そのもの、足りなければ何もしない代わりの部品を返す（呼び出しはすべて V.Market.xxx() の形）
const V = {};
for (const [name, view, real] of [['Market', 'market', () => Market], ['Muni', 'market', () => Muni], ['Find', 'find', () => Find], ['Own', 'own', () => Own]]) {
  Object.defineProperty(V, name, { get: () => (S.missingViews && S.missingViews[view] ? viewStub(view) : real()) });
}
const modType = (f) => { try { return f(); } catch (e) { return 'undefined'; } };   // 途中で失敗した部品は const が未初期化のままで、typeof でも例外になる
function guardModules() {
  const R = window.__scriptRetry, failedSrc = R && R.failed.length ? R.failed.join('、') : '';
  const coreMissing = Object.keys(CORE_MODULES).filter((k) => modType(CORE_MODULES[k]) === 'undefined');
  if (coreMissing.length) throw new Error(`読み込めませんでした。通信を確かめて、ページを読み直してください。`);
  S.missingViews = {};
  for (const [view, v] of Object.entries(VIEW_MODULES)) {
    const miss = Object.keys(v.mods).filter((k) => modType(v.mods[k]) === 'undefined');
    if (miss.length) S.missingViews[view] = { mods: miss, text: `この画面を開けませんでした。ほかの画面は使えます。ページを読み直すと開けることがあります。` };
  }
}

async function main() {
  if (window.__scriptRetry) await window.__scriptRetry.settled();
  guardModules();
  PERF.init();
  const [meta, idx, muni, pref, routes, style] = await Promise.all([
    getJSON('data/meta.json'), getJSON('data/index.json'), getJSON('data/muni.geojson'), getJSON('data/pref.geojson'),
    getJSON('data/routes.json'), getJSON(STYLE_URL), Hubs.load(), Rt.loadMeta().then(() => Promise.all([Rt.loadPairs(), Rt.loadTrips()])),
    Promise.resolve().then(() => V.Market.load()).catch((e) => softFail('market', e)), Promise.resolve().then(() => V.Muni.load()).catch((e) => softFail('market', e)),
  ]);
  Object.assign(S, { meta, idx, muni, pref, routes });
  for (const f of muni.features) S.byIndex.set(f.properties.i, f);
  // 大きな都市（各都道府県の代表）。ゆるやかな流れの行き先
  meta.ambient = ['01101', '02201', '03201', '04101', '05201', '06201', '07201', '15103', '16201', '17201', '18201', '19201', '20201', '21201',
    '22101', '22130', '23106', '24201', '25201', '26104', '27128', '28110', '29201', '30201', '31201', '32201', '33101', '34101', '35203',
    '36201', '37201', '38201', '39201', '40131', '41201', '42201', '43101', '44201', '45201', '46201', '47201', '27140', '14100', '23101'];
  const p = new URLSearchParams(location.hash.slice(1));
  S.view = urlTarget() || 'home';   // 開く先がアドレスに無いときは最初の画面（戻るで来た場合）
  S.guide = urlGuide();
  V.Market.readHash(p);
  V.Muni.readHash(p);
  if (p.get('o') && meta.origins.some((o) => o.code === p.get('o'))) S.origin = p.get('o');
  if (['km', 'days', 'fare'].includes(p.get('l'))) S.layer = p.get('l');
  await loadOrigin(S.origin);

  $('#origin').innerHTML = meta.origins.map((o) => `<option value="${o.code}">${o.name}</option>`).join('');
  $('#origin').value = S.origin;
  await Work.init({
    onCargoChange: () => { onCargoChange(); V.Find.onCargo(); },
    modeProblem,
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
  try { map.getCanvas().setAttribute('aria-label', '日本地図。物流の統計と経路を描いています。キーボードでは矢印キーで動かし、プラスとマイナスで拡大・縮小できます'); map.getCanvas().setAttribute('role', 'application'); } catch (e) { /* 地図の部品が無いとき */ }
  Sheet.init(map); // 幅 600px 以下のパネルと、fitBounds の余白（SPEC §30.7）
  // 空きを探す・つなぐ: 左のパネルの高さを、下の注意と凡例の欄の上端までに限る（重ならないように、SPEC §31.5 #7）。値が変わるときだけ書く
  const fn = document.querySelector('#find-notes');
  if (fn && window.ResizeObserver) {
    const setFn = () => { const h = String(Math.ceil(fn.offsetHeight)); if (document.documentElement.style.getPropertyValue('--fn-h') !== h + 'px' && fn.offsetHeight > 0) document.documentElement.style.setProperty('--fn-h', h + 'px'); };
    new ResizeObserver(setFn).observe(fn);
    setFn();
  }
  map.addControl(new maplibregl.AttributionControl({
    compact: false,
    customAttribution: ['国土数値情報（行政区域データ）（国土交通省）を加工して作成', '国土数値情報（鉄道データ）（国土交通省）を加工して作成', '道路・フェリー・空港: OSRM・OpenStreetMap（© OpenStreetMap contributors, ODbL）'],
  }), 'bottom-right');
  overlay = new deck.MapboxOverlay({
    interleaved: new URLSearchParams(location.search).get('il') !== '0', layers: [], // ?il=0 は計測の比較用（地図の上に別に重ねる）
    getCursor: ({ isHovering, isDragging }) => (isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab'),
    onHover: (info) => { if (S.view === 'market') V.Market.hover(info); else if (S.view === 'compare') hoverTag(info); },
    pickingRadius: 6,
    effects: MapFx.effects(),   // 3D の乗り物に当てる光（map_fx.js）
    onClick: (info) => { if (S.view === 'market') { V.Market.click(info); return; } if (S.view === 'find') { V.Find.click(info); return; } if (S.view !== 'compare') return; if (Work.isOpen()) Work.closeWork(); if (info.object && info.layer && info.layer.id === 'muni' && info.object.properties.i >= 0) select(info.object.properties.i); },
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
  V.Market.setActive(S.view === 'market');
  V.Market.init();
  V.Market.afterStart();
  V.Muni.init();
  V.Muni.afterStart();
  setupMarketNow(!p.get('n'));
  V.Find.readHash(p);
  await V.Find.init();
  if (S.view === 'find') V.Find.enter(V.Find.pairFromHash(new URLSearchParams(location.hash.slice(1))));
  V.Own.readHash(p);
  await V.Own.init();
  if (S.view === 'own') V.Own.enter();
  setHubLegend();
  document.querySelectorAll('#views button[data-view]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
  // 閉じているパネルの中のボタンに Tab が入らないようにする（aria-hidden の値に合わせて inert を付け外しする）
  for (const id of ['detail', 'work']) {
    const el = document.getElementById(id);
    if (!el) continue;
    const sync = () => { el.inert = el.getAttribute('aria-hidden') !== 'false'; };
    new MutationObserver(sync).observe(el, { attributes: true, attributeFilter: ['aria-hidden'] });
    sync();
  }
  document.querySelector('.tgl[data-toggle="hubs"]').addEventListener('click', () => setHubs(!Hubs.on));
  document.querySelector('.tgl[data-toggle="jartic"]').addEventListener('click', (e) => {
    const on = e.currentTarget.getAttribute('aria-pressed') !== 'true';
    e.currentTarget.setAttribute('aria-pressed', String(on));
    V.Market.setJartic(on);
  });
  document.querySelector('.tgl[data-toggle="conc"]').addEventListener('click', (e) => V.Muni.setConc(e.currentTarget.getAttribute('aria-pressed') !== 'true'));
  document.querySelector('.tgl[data-toggle="fac"]').addEventListener('click', (e) => V.Muni.setFac(e.currentTarget.getAttribute('aria-pressed') !== 'true'));
  if (p.get('hubs') === '1') setHubs(true);
  $('#cards').addEventListener('change', (e) => { if (e.target.closest('input[name="cc-pick"]')) syncPick(); });
  $('#cards').addEventListener('click', (e) => {
    const b = e.target.closest('.hub-btn'); if (b) setHubSel(b.dataset.hub);
    const t = e.target.closest('.tip');
    if (t) { const body = document.getElementById(t.getAttribute('aria-controls')), open = t.getAttribute('aria-expanded') !== 'true'; t.setAttribute('aria-expanded', String(open)); if (body) body.hidden = !open; }
  });

  $('#origin').addEventListener('change', (e) => setOrigin(e.target.value));
  document.querySelectorAll('.lyr[data-layer]').forEach((b) => b.addEventListener('click', () => setLayer(b.dataset.layer)));
  $('#close').addEventListener('click', () => { deselect(); fitJapan(true); const q = $('#q'); if (q) q.focus({ preventScroll: true }); });   // 条件に戻る: 届け先の欄へ
  $('#home-jp').addEventListener('click', () => {
    if (S.view === 'market') V.Market.deselect(false);
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
  WARM.onDone(() => {
    PERF.hold(2000);
    setTimeout(() => { // 読み込み画面は、消えたあと DOM から取り除く（画面の文字に残さない）
      const l = $('#loading');
      if (!l) return;
      l.classList.add('done');
      setTimeout(() => l.remove(), 450);
    }, 150);
  });
  requestAnimationFrame(frame);
  M3D.preload();
  { // ?perf=1 は計測、?check=1 は利用者向けの動作確認（どちらも付いていなければ何も読まない）
    const qs = new URLSearchParams(location.search);
    if (qs.get('perf') || qs.get('check')) { const sc = document.createElement('script'); sc.src = 'perfprobe.js'; document.head.appendChild(sc); }
    if (qs.get('check')) { const sc = document.createElement('script'); sc.src = 'check.js'; document.head.appendChild(sc); }
  }
  window.__app = { WARM, S, Market: V.Market, Muni: V.Muni, Hubs, Find: V.Find, Own: V.Own, Rt, setView, setHubs, setHubSel, select, setLayer, setOrigin, map: () => map, overlay: () => overlay, refreshEstimate };
}

/* 全国の物流傾向: 選んでいる区間と今の表示条件を地図のそばに出し、全国の指標と区間の順位は選んだときだけ開く */
function setupMarketNow(noN) {
  const b = document.body, q = (s) => document.querySelector(s);
  b.classList.add('m-right-collapsed');
  q('#m-rfold').setAttribute('aria-expanded', 'false');
  const cb = q('[data-fold="m-calc"]');
  if (cb && cb.getAttribute('aria-expanded') === 'true') cb.click();
  if (S.guide) { const cd = q('#m-credits-d'); if (cd) { cd.open = false; b.classList.remove('cr-open'); } }   // 案内の表示では出典は閉じておく（開けば読める）
  const sum = q('#open-sum'), rank = q('#open-rank');
  const sheetOn = () => b.classList.contains('sheet-mode');   // 狭い画面は下のパネルの「数字」の切り替えで出す（#m-summary は sh-on のときだけ見える）
  const sheetTab = (id) => { const t = q(`#sheet-tabs button[data-sh="${id}"]`); if (t) t.click(); return !!t; };
  sum.addEventListener('click', () => {
    if (sheetOn()) { if (!sheetTab(q('#m-summary').classList.contains('sh-on') ? 'm-left' : 'm-summary')) b.classList.toggle('m-sum-open'); return; }
    b.classList.toggle('m-sum-open');
  });
  q('#mg-next').addEventListener('click', () => {   // 選んだ区間（荷が多い向きの発地 pf と着地 pt）をアドレスで空きを探すへ渡す
    const r = V.Market.pairNames(), p = new URLSearchParams({ v: 'find', g: '1' });
    if (r) { p.set('pf', r.from); p.set('pt', r.to); }
    goHash('#' + p.toString());
  });
  q('#mg-rows').addEventListener('click', (e) => {
    const bt = e.target.closest('.mg-row');
    if (!bt) return;
    V.Market.select(bt.dataset.k, { fly: true });
    // 一覧の行は選ぶたびに作り直されてフォーカスが消えるので、次の主なボタンへ移す（キーボードで選んだあとも Tab で戻らなくてよい）
    setTimeout(() => { const nx = q('#mg-next'); if (nx && !nx.disabled) nx.focus({ preventScroll: true }); }, 60);
  });
  if (S.guide && noN) {   // 案内の表示は、上位 5 区間だけを地図に出す
    const ms = V.Market.state; ms.n = 5; V.Market.refresh(false);
  }
  rank.addEventListener('click', () => q('#m-rfold').click());
  const txt = (s) => ((q(s) || {}).textContent || '').replace(/\s+/g, ' ').trim();
  const pressed = (s) => { const e = q(s + ' [aria-pressed="true"]'); return e ? e.textContent.trim() : ''; };
  let hadPair = b.classList.contains('m-has-pair'), lastG = null;
  const sync = () => {
    const gNow = b.classList.contains('guided') && b.classList.contains('view-market');
    if (gNow !== lastG) { lastG = gNow; try { V.Market.syncMarkers(); } catch (e) { /* 地図が無いとき */ } }   // 案内の表示に入る・出るとき、上位 5 区間の番号を出し入れする
    const so = sheetOn() ? q('#m-summary').classList.contains('sh-on') : b.classList.contains('m-sum-open'), ro = !b.classList.contains('m-right-collapsed');
    sum.setAttribute('aria-expanded', String(so)); sum.textContent = so ? '全国の積載効率と不足を閉じる' : '全国の積載効率と不足を見る';
    rank.setAttribute('aria-expanded', String(ro)); rank.textContent = ro ? '帰りが空く向きの順位を閉じる' : '帰りが空く向きの順位を見る';
    try {   // 案内の表示: 帰りが空きやすい区間の上位 3 件（物流センサス）
      const st = V.Market.state, P = st.d && st.d.prefs;
      if (P) {
        const sk = st.sel;
        // 帰りが空きやすい向き（荷が多い向きの逆）で書く。数字は、その向きに足りない荷の量（不足量 = 荷が多い向きの量 − 逆向きの量）。番号は地図の番号と同じ
        // 例の便がある向きに印を付ける。一番上の行に例の便が無いことがあるので、「おすすめ」は例の便がある最初の行にする（番号は地図の番号のまま）
        const fl = V.Find.state, hasTrip = (r) => !fl.loaded || V.Find.hasReturn({ from: P[r.fi], to: P[r.ti] });
        if (!fl.loaded && !sync.loading && st.rows[0]) { sync.loading = true; V.Find.checkFit({ from: P[st.rows[0].fi], to: P[st.rows[0].ti] }).then(() => sync()); }
        const rec = fl.loaded ? st.rows.slice(0, 5).findIndex(hasTrip) : -1;
        q('#mg-rows').innerHTML = st.rows.slice(0, 5).map((r, i) => `<li><button type="button" class="mg-row" data-k="${escapeHtml(r.k)}" aria-pressed="${r.k === sk}"><span class="mg-no num" aria-hidden="true">${i + 1}</span><span class="mg-pair">${escapeHtml(P[r.ti])} → ${escapeHtml(P[r.fi])}</span><span class="num mg-short">足りない荷 ${Math.round(r.short).toLocaleString('ja-JP')} t</span>${fl.loaded ? `<small class="mg-tag${i === rec ? ' rec' : ''}">${hasTrip(r) ? (V.Find.fitState({ from: P[r.fi], to: P[r.ti] }) === false ? '例の便は埋まっています' : i === rec ? 'おすすめ・例の便あり' : '例の便あり') : '例の便なし'}</small>` : ''}</button></li>`).join('');
        const sr = st.rows.find((r) => r.k === sk), n0 = (v) => Math.round(v).toLocaleString('ja-JP');
        q('#mg-sel').textContent = sr ? `${P[sr.ti]}から${P[sr.fi]}へ帰る荷は ${n0(sr.back)} t で、行きの ${n0(sr.go)} t より少なくなっています。` : '上の一覧から 1 つ選んでください。';
      }
    } catch (e) { /* 区間が読めていないときは空のまま */ }
    try {   // 案内: 区間を選ぶまでは主ボタンを押せない（選んだ区間の帰り便を探す）
      const nx = q('#mg-next'), pn = V.Market.pairNames(), has = !!pn;
      const fit = has && V.Find.hasReturn(pn) ? V.Find.fitState(pn) : true;   // 帰りの便があっても、載る例の荷が無ければ（null は調べている最中）主ボタンは出さない
      if (has && fit === null) V.Find.checkFit(pn).then(() => sync());
      const noFit = has && fit === false;
      const noAnswer = has && (!V.Find.hasReturn(pn) || noFit);   // 選んだ区間の帰りの向きに合う架空の便が 1 つも無いときは、行き先が空の結果になるので主ボタンを出さない
      if (noAnswer) { const sl = q('#mg-sel'); if (sl) sl.textContent += noFit ? ' この向きの便は埋まっています。別の向きを選んでください。' : ' この向きの例の便は、まだありません。別の向きを選ぶか、ボタンで空きを探してください。'; }
      if (nx) { nx.hidden = !has; nx.disabled = !has; /* 区間を選ぶまでは、押せない主ボタンを出さない。例の便が無い区間でも、次の行き先として空きを探すを出す */ const t = has ? (noAnswer ? 'この向きで空きを探す' : 'この向きで帰り便を探す') : '向きを選ぶ'; if (nx.dataset.t !== t) { nx.dataset.t = t; nx.innerHTML = has ? `${t}<small class="mg-badge">架空のデモ</small>` : t; } }
    } catch (e) { /* 区間が読めていないとき */ }
    q('#m-now-cond').textContent = `${pressed('#m-mode')}／${(() => { const s = pressed('#m-scope'); return /（推定）$/.test(s) ? s.replace(/（推定）$/, 'ごと（推定）') : `${s}ごと`; })()}／上位 ${txt('#m-n-out')}／${txt('#m-km-out')}`;
    let r = null; try { r = V.Market.pairNames(); } catch (e) { r = null; }
    { const ns = q('#m-now-sel'); ns.textContent = r ? `選んだ向き: ${r.from} → ${r.to}` : ''; ns.hidden = !r; }   // 向きを選ぶまでは何も出さない（2026-11-08 の最終評価に合わせて）
    const pair = b.classList.contains('m-has-pair');
    if (pair && !hadPair && !ro) q('#m-rfold').click();   // 区間を選んだら、その説明が見えるように右の欄を開く
    hadPair = pair;
  };
  const ob = new MutationObserver(sync);
  ob.observe(b, { attributes: true, attributeFilter: ['class'] });
  ob.observe(q('#m-summary'), { attributes: true, attributeFilter: ['class'] });
  for (const s of ['#m-count', '#m-scope', '#m-mode', '#m-n-out', '#m-km-out', '#m-tbody']) { const e = q(s); if (e) ob.observe(e, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['aria-pressed'] }); }
  sync();
}

function bootFailed(e) {
  console.error(e);
  BOOT.state = 'failed';
  // 地図そのものに要るデータが読めなかった。英語の誤りは出さず、日本語の文と「読み直す」を出す
  const l = $('#loading');
  if (l) { l.classList.remove('done'); l.textContent = ''; }
  const m = String((e && e.message) || e || '');
  const head = /webgl/i.test(m) ? 'この端末では地図を出せません。下の地図なしの画面を使ってください。' : jpLoadMessage(e);
  showLoadNotice(head, null);
  const n = document.querySelector('#load-notice');
  // 地図が無くても答えに届くように、最初の画面と、地図なしの版へのリンクを出す
  const nav = document.createElement('div'); nav.className = 'load-fail-nav';
  const back = document.createElement('button'); back.type = 'button'; back.className = 'btn-link'; back.id = 'fail-home'; back.textContent = '最初の画面に戻る';
  back.addEventListener('click', () => { n.remove(); if (l) l.remove(); goHome(); });
  const lite = (href, t) => { const a = document.createElement('a'); a.href = href; a.className = 'fail-lite'; a.textContent = t; return a; };
  nav.append(back, lite('own_lite.html', '自社の運行を地図なしで開く'), lite('compare_lite.html', '送り方を地図なしで比べる'));
  n.appendChild(nav);
  if (l) l.appendChild(n);
  if (/webgl/i.test(m) && urlTarget() === 'market') marketWithoutWebgl(n);
}
// 地図を描けない端末でも、全国の物流傾向の答え（帰りが空きやすい向きの上位 5 区間）が読めるように、静止画の日本地図と表を出す
async function marketWithoutWebgl(n) {
  try {
    const d = await getJSON('data/market.json'), P = d.prefs;
    const rows = d.modes.truck.pairs.filter((p) => p[6] >= 200).slice(0, 5);
    const box = document.createElement('div');
    box.className = 'load-fail-market';
    box.innerHTML = '<img src="img/home_map.jpg" alt="日本地図の静止画（地図を動かせない端末の代わりです）" loading="lazy" decoding="async">' +
      '<p class="lfm-h">帰りが空きやすい向きの上位 5 つ（営業用トラック）</p>' +
      `<ol>${rows.map((p, i) => `<li><span class="mg-no num" aria-hidden="true">${i + 1}</span><span class="mg-pair">${escapeHtml(P[p[1]])} → ${escapeHtml(P[p[0]])}</span><span class="num mg-short">足りない荷 ${Math.round(p[5]).toLocaleString('ja-JP')} t</span></li>`).join('')}</ol>` +
      '<p class="lfm-s">数字は、その向きに足りない荷の量です。</p>';
    n.appendChild(box);
  } catch (e) { /* 統計が読めないときは、案内の文だけを残す */ }
}

/* ------------------------------------------------------------ 最初の画面と起動（SPEC §44）
   アドレスに開く先（#v=... か旧い比較の条件）が無いときは、最初の画面で役割を選ばせる。
   地図と統計の読み込み（main）は、地図の要る画面が選ばれたときに始める。最初の画面は、地図のデータを待たない */
const VIEW_NAMES = ['market', 'find', 'compare', 'own'];
function urlTarget() {
  const p = new URLSearchParams(location.hash.slice(1)), v = p.get('v');
  if (VIEW_NAMES.includes(v)) return v;
  return (p.get('o') || p.get('l') || p.get('d')) ? 'compare' : null;
}
const urlGuide = () => new URLSearchParams(location.hash.slice(1)).get('g') || '';
const urlRole = () => { const r = new URLSearchParams(location.hash.slice(1)).get('r'); return r === 'shipper' || r === 'carrier' ? r : ''; };
const BOOT = { state: 'idle' };   // idle → running → ready か failed
function boot() {
  if (BOOT.state !== 'idle') return;
  BOOT.state = 'running';
  main().then(() => {
    BOOT.state = 'ready';
    S.guide = urlGuide();
    const want = urlTarget() || 'home';   // 読み込み中に戻る・進むが押されたときの追いつき
    if (want !== S.view) setView(want);
    else { applyGuide(); if (BOOT.focus) focusHeading(S.view); }
    if (S.view === 'market' && document.body.classList.contains('guided') && !V.Market.selected()) fitJapan(false);   // 上位 5 区間が決まったあとで、その線に寄せ直す
  }).catch(bootFailed);
}
// 最初の画面の 2 段目: 役割を選ぶと、その役割の目的を大きな選択肢で出す
function renderHomeStep(focus) {
  const r = urlRole();
  document.getElementById('home-step1').hidden = !!r;
  document.getElementById('home-step2').hidden = !r;
  document.querySelectorAll('#home-step2 [data-for]').forEach((e) => { e.hidden = e.dataset.for !== r; });
  document.getElementById('home').classList.toggle('home-step-2', !!r);
  const h = document.getElementById('home-h');
  h.textContent = r === 'shipper' ? '荷主の方: 何をしますか' : r === 'carrier' ? '運送会社の方: 何をしますか' : 'どちらの作業をしますか';
  if (focus) h.focus({ preventScroll: true });   // 役割を選んだあと、隠れた段のボタンにフォーカスが残らないよう見出しへ移す
}
// 役割の選択・戻る・進むで、いまのアドレスの開く先に画面を合わせる
// 最初の画面から地図の画面へ移るときの 300ms の切り替え（動きを減らす設定では CSS が止める）
function fadeInView() {
  const b = document.body;
  b.classList.add('view-fade');
  setTimeout(() => b.classList.remove('view-fade'), 420);
}
function syncToUrl() {
  const t = urlTarget();
  if (t && document.body.classList.contains('view-home')) fadeInView();
  if (BOOT.state === 'idle') { if (t) { document.body.className = 'view-' + t; fadeInView(); boot(); } else { document.body.className = 'view-home'; renderHomeStep(true); } return; }
  if (BOOT.state === 'failed') { if (!t) { document.body.className = 'view-home'; renderHomeStep(true); } else location.reload(); return; }
  if (BOOT.state === 'ready') {
    const want = t || 'home';
    S.guide = urlGuide();
    if (want !== S.view) setView(want); else applyGuide();
    if (!t) renderHomeStep();
  }
  if (!t) { const h = document.getElementById('home-h'); if (h) h.focus({ preventScroll: true }); }
}
function goHash(h) {
  history.pushState(null, '', location.pathname + location.search + h);
  BOOT.focus = true;   // 起動後、選んだ画面の見出しにフォーカスを置く
  syncToUrl();
}
const goHome = () => goHash('');
document.querySelectorAll('#home [data-role]').forEach((b) => b.addEventListener('click', () => goHash('#r=' + b.dataset.role)));
document.querySelectorAll('#home [data-go]').forEach((b) => b.addEventListener('click', () => goHash('#' + b.dataset.go)));
document.querySelectorAll('[data-home]').forEach((b) => b.addEventListener('click', goHome));
document.getElementById('g-toggle').addEventListener('click', () => setGuide(''));
window.addEventListener('hashchange', syncToUrl);
new MutationObserver(() => syncGuideBar()).observe(document.body, { attributes: true, attributeFilter: ['class'] });   // 段の表示（条件・結果・見積依頼文）と凡例を、選択や作業の開閉に合わせる
/* 最初の画面の背景の短いループ映像。最初の描画のあとで読み込み、動きを減らす設定・狭い画面・通信量を抑える設定では読み込まない。
   絵の上に重ねるだけ（位置は絶対指定）なので、読み込みで配置は動かない。映像が読めなければ、そのまま静止画 */
function loadHeroLoop() {
  const box = document.querySelector('.home-hero');
  if (!box || box.querySelector('video') || box.querySelector('canvas') || MapFx.reduced() || innerWidth <= 700) return;
  const c = navigator.connection;
  if (c && (c.saveData || /(^|-)2g$/.test(c.effectiveType || ''))) return;
  /* 使える端末では three.js の 3D（hero3d.js）を先に試す。WebGL がない・4 秒以内に読めないときだけ、今までの映像にする */
  // three.js の背景は、経路の色（青の実線・橙の点線）と木の密度が Blender の映像に届くまで、?hero3d=1 のときだけ使う（2026-11-07 本線の判断）
  if (/[?&]hero3d=1(&|$)/.test(location.search) && window.Hero3D && Hero3D.capable()) { Hero3D.start(box).then((ok) => { if (!ok && S.view === 'home') loadHeroVideo(box); }); return; }
  loadHeroVideo(box);
}
const HERO_DIR = 'img/hero/';   // 背景の絵と映像の置き場（版の切り替えは廃止）
/* 背景の映像に合わせた場面の字幕。時刻と位置は HERO_DIR + 'scenes.json'（{segments:[{key,t0,t1,anchor:{x,y}}]}、x・y は映像の幅・高さに対する 0〜1）、
   文言はここの HERO_CAPTIONS。JSON がなければ何も出さない。パネル（.home-wrap）と名前（.home-brand）に重ならない位置へ寄せる */
const HERO_CAPTIONS = {
  kanto: ['関東', '拠点で荷待ちが起きています'],
  kansai: ['関西 → 関東', '帰りは空車で戻っています'],
  kyushu: ['鹿児島 → 東京', '長距離の帰りは空車になりがちです'],
  chihou: ['地方', '帰りの荷が少なくなっています'],
  zenkoku: ['全国', '物流網と荷の流れ'],
};
const HeroCaptions = (function () {
  let segs = null, raf = 0, cur = null, hideTimer = 0;
  const el = () => document.getElementById('home-cap');
  const R = (e) => e.getBoundingClientRect();
  function hit(a, b, m) { return a.left < b.right + m && b.left < a.right + m && a.top < b.bottom + m && b.top < a.bottom + m; }
  function place(seg, c) {
    const vw = innerWidth, vh = innerHeight, w = c.offsetWidth, h = c.offsetHeight, m = 16;
    const vid = document.querySelector('.home-hero video'), iw = (vid && vid.videoWidth) || 1920, ih = (vid && vid.videoHeight) || 1080;
    const k = Math.max(vw / iw, vh / ih), ox = (vw - iw * k) / 2, oy = (vh - ih * k) / 2;
    const ax = ox + seg.anchor.x * iw * k, ay = oy + seg.anchor.y * ih * k;
    const brand = document.getElementById('home-brand');
    const avoid = [document.querySelector('.home-wrap'), brand].filter(Boolean).map(R);
    const clampX = (x) => Math.min(vw - w - m, Math.max(m, x)), clampY = (y) => Math.min(vh - h - m, Math.max(m, y));
    const ok = (x, y) => { const r = { left: x, top: y, right: x + w, bottom: y + h }; return avoid.every((a) => !hit(r, a, 8)); };
    const cands = [[ax + 16, ay - h / 2], [ax - 16 - w, ay - h / 2], [ax - w / 2, ay - 16 - h], [ax - w / 2, ay + 16]];
    for (const [x, y] of cands) { const cx = clampX(x), cy = clampY(y); if (ok(cx, cy)) return [cx, cy]; }
    /* どこも重なるときは、名前の下の帯か、パネルの下の帯の空いている所に置く */
    const pr = avoid[0], top = brand ? R(brand).bottom + 16 : m;
    const ys = [clampY(top), clampY(pr ? pr.bottom + 16 : vh)];
    for (const y of ys) { const cx = clampX(ax - w / 2); if (ok(cx, y)) return [cx, y]; }
    return [clampX(ax - w / 2), ys[0]];
  }
  function show(seg) {
    const c = el(); if (!c) return;
    clearTimeout(hideTimer);
    const t = HERO_CAPTIONS[seg.key] || [seg.label || '', seg.line || ''];
    c.querySelector('.hc-label').textContent = t[0]; c.querySelector('.hc-line').textContent = t[1];
    c.hidden = false;
    const [x, y] = place(seg, c);
    c.style.transform = 'translate(' + Math.round(x) + 'px,' + Math.round(y) + 'px)';
    requestAnimationFrame(() => c.classList.add('on'));
  }
  function hide() {
    const c = el(); if (!c) return;
    c.classList.remove('on');
    clearTimeout(hideTimer); hideTimer = setTimeout(() => { if (!cur) c.hidden = true; }, 320);
  }
  function update(t) {
    const seg = (segs && segs.find((s) => t >= s.t0 && t < s.t1)) || null;
    if (seg === cur) return;
    cur = seg;
    if (seg) show(seg); else hide();
  }
  function tick() {
    raf = 0;
    if (document.hidden || !document.body.classList.contains('view-home') || !segs) return;
    const v = document.querySelector('.home-hero video');
    if (v && v.readyState >= 2 && !v.paused) update(v.currentTime);
    raf = requestAnimationFrame(tick);
  }
  function run() { if (!raf && segs) raf = requestAnimationFrame(tick); }
  function load(json) {
    segs = json && Array.isArray(json.segments) ? json.segments.filter((s) => s && s.anchor && s.t1 > s.t0) : null;
    if (segs && !segs.length) segs = null;
    cur = null; if (!segs) hide(); else run();
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) { if (raf) cancelAnimationFrame(raf); raf = 0; } else run(); });
  function init() {
    if (MapFx.reduced() || innerWidth <= 700) return;
    if (!/[?&]captions=1/.test(location.search + location.hash)) return;   // 字幕の台本（scenes.json）は明示したときだけ読む（無いと 404 になる）
    fetch(HERO_DIR + 'scenes.json').then((r) => (r.ok ? r.json() : null)).then(load).catch(() => {});
  }
  return { init, load, update, run };
})();
window.HeroCaptions = HeroCaptions;
function loadHeroVideo(box) {
  if (!box || box.querySelector('video')) return;
  const v = document.createElement('video');
  v.muted = true; v.loop = true; v.autoplay = true; v.playsInline = true; v.preload = 'auto'; v.setAttribute('aria-hidden', 'true'); v.tabIndex = -1;
  v.poster = HERO_DIR + 'hero_1920.webp';
  for (const [f, t] of [['hero_loop.webm', 'video/webm'], ['hero_loop.mp4', 'video/mp4']]) { const s = document.createElement('source'); s.src = HERO_DIR + f; s.type = t; v.appendChild(s); }
  v.addEventListener('playing', () => v.classList.add('on'), { once: true });
  v.addEventListener('error', () => v.remove(), true);
  box.appendChild(v);
  const p = v.play(); if (p && p.catch) p.catch(() => {});
  HeroCaptions.init();
}
if (document.body.classList.contains('view-home')) {
  const go = () => requestAnimationFrame(() => setTimeout(loadHeroLoop, 400));
  if (document.readyState === 'complete') go(); else window.addEventListener('load', go, { once: true });
}
if (urlTarget()) boot();
else { document.body.className = 'view-home'; renderHomeStep(); }
