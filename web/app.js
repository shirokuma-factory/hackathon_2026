'use strict';
/* スポット出荷マップ（試作）。運賃などの値は web/data の JSON を読むだけで、ここでは計算しない。
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
const S = {
  origin: '13104', layer: 'km', sel: -1, meta: null, idx: null, muni: null, pref: null, routes: null, est: null,
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
  return [...paletteColor(c, classCount()), FILL_ALPHA];
}

function valueText(i) {
  const od = curOd();
  if (S.layer === 'km') return od.km[i] === null ? '経路なし' : `${od.km[i].toFixed(1)} km`;
  if (S.layer === 'days') return od.days[i] === null ? '経路なし' : `運転 ${od.days[i]}日`;
  if (od.km[i] === null) return '経路なし';
  return od.fare[i] === null ? '表の範囲外（200km未満）' : `約${fmt(od.fare[i])}円（2t・税抜）`;
}

function staticLayers() {
  const L = deck;
  const list = [];
  list.push(new L.GeoJsonLayer({
    id: 'muni', data: S.muni, pickable: true, stroked: true, filled: true, beforeId: firstLabelId,
    getFillColor: fillColor, getLineColor: [8, 12, 20, 120], lineWidthMinPixels: 0.4, lineWidthUnits: 'pixels', getLineWidth: 0.5,
    autoHighlight: true, highlightColor: [255, 255, 255, 54],
    updateTriggers: { getFillColor: [S.origin, S.layer, S.version] },
    transitions: { getFillColor: { duration: 650, easing: ease } },
  }));
  list.push(new L.GeoJsonLayer({
    id: 'pref', data: S.pref, stroked: true, filled: false, beforeId: firstLabelId,
    getLineColor: [210, 226, 255, 74], lineWidthMinPixels: 0.8, getLineWidth: 0.8, lineWidthUnits: 'pixels',
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

function dynamicLayers(now) {
  const L = deck, out = [];
  const o = S.meta.origins.find((x) => x.code === S.origin);
  const sec = now / 1000;
  const amb = S.ambient, aA = S.ambientAlpha;
  if (amb) {
    out.push(new L.PathLayer({
      id: 'amb-base', data: amb.paths, getPath: (d) => d, getColor: [170, 205, 245, 34 * aA], widthMinPixels: 0.8, getWidth: 1,
      widthUnits: 'pixels', updateTriggers: { getColor: [aA] },
    }));
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
  // 選択した着地
  if (S.selFeature) {
    const pulse = 0.55 + 0.45 * Math.sin(sec * 2.4);
    out.push(new L.GeoJsonLayer({
      id: 'sel-fill', data: S.selFeature, filled: true, stroked: false, getFillColor: [92, 200, 255, 38 + 34 * pulse], opacity: 1,
      updateTriggers: { getFillColor: [Math.round(pulse * 20)] },
    }));
    out.push(new L.GeoJsonLayer({
      id: 'sel-glow', data: S.selFeature, filled: false, stroked: true, getLineColor: [92, 200, 255, 70 + 50 * pulse],
      lineWidthMinPixels: 11, getLineWidth: 8, lineWidthUnits: 'pixels', lineJointRounded: true,
      updateTriggers: { getLineColor: [Math.round(pulse * 20)] },
    }));
    out.push(new L.GeoJsonLayer({
      id: 'sel-line', data: S.selFeature, filled: false, stroked: true, getLineColor: [226, 247, 255, 255],
      lineWidthMinPixels: 3, getLineWidth: 3, lineWidthUnits: 'pixels', lineJointRounded: true,
    }));
  }
  const r = S.route;
  if (r) {
    const reveal = ease(clamp01((now - S.selectedAt) / 1100));
    const dashed = r.kind === 'arc';
    const shown = reveal < 1 ? slicePath(r, reveal) : r.path;
    out.push(new L.PathLayer({
      id: 'route-base', data: [{ p: shown }], getPath: (d) => d.p, getColor: [92, 200, 255, dashed ? 120 : 90], widthMinPixels: dashed ? 1.6 : 2.4,
      getWidth: 2, widthUnits: 'pixels', capRounded: true, jointRounded: true,
      getDashArray: dashed ? [5, 4] : [0, 0], dashJustified: false, extensions: dashed ? [new L.PathStyleExtension({ dash: true })] : [],
      updateTriggers: { getPath: [reveal < 1 ? reveal : 1, S.selectedAt], getColor: [dashed] },
    }));
    if (reveal >= 0.98) {
      const RD = 3.6, TR = 1.3, C = RD + TR + 0.9, N = 3;
      if (!r.trips) {
        const ts = r.cum.map((c) => (c / r.total) * RD);
        r.trips = [];
        for (let k = 0; k < N; k++) for (const sh of [0, -C]) r.trips.push({ path: r.path, ts: ts.map((v) => v + (k * C) / N + sh) });
      }
      const ct = sec % C;
      out.push(new L.TripsLayer({
        id: 'route-glow', data: r.trips, getPath: (d) => d.path, getTimestamps: (d) => d.ts, getColor: [92, 200, 255, 120],
        widthMinPixels: 13, capRounded: true, jointRounded: true, trailLength: TR, currentTime: ct, fadeTrail: true,
      }));
      out.push(new L.TripsLayer({
        id: 'route-core', data: r.trips, getPath: (d) => d.path, getTimestamps: (d) => d.ts, getColor: [235, 250, 255, 255],
        widthMinPixels: 3.6, capRounded: true, jointRounded: true, trailLength: TR * 0.8, currentTime: ct, fadeTrail: true,
      }));
    }
    // 1日の運転の終わりの印
    if (r.minutes) {
      const marks = [];
      for (let d = 540; d < r.minutes; d += 540) marks.push({ p: pointAt(r, d / r.minutes) });
      if (marks.length) {
        out.push(new L.ScatterplotLayer({
          id: 'daymarks', data: marks, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 6, stroked: true, filled: true,
          getFillColor: [10, 16, 26, 230], getLineColor: [255, 255, 255, 220], lineWidthUnits: 'pixels', getLineWidth: 1.6,
        }));
      }
    }
    // 着地の印（ゆっくり広がる輪つき）
    const dph = ((sec + 1.3) % 2.6) / 2.6;
    out.push(new L.ScatterplotLayer({
      id: 'dest-pulse', data: [{ p: r.path[r.path.length - 1] }], getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 8 + dph * 24, stroked: true,
      filled: false, getLineColor: [226, 247, 255, Math.round(190 * (1 - dph))], lineWidthUnits: 'pixels', getLineWidth: 1.6,
      updateTriggers: { getLineColor: [Math.round(dph * 30)], getRadius: [Math.round(dph * 30)] },
    }));
    const dest = r.path[r.path.length - 1];
    out.push(new L.ScatterplotLayer({
      id: 'dest', data: [{ p: dest }], getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 5.5, stroked: true, filled: true,
      getFillColor: [255, 255, 255, 255], getLineColor: [92, 200, 255, 255], lineWidthUnits: 'pixels', getLineWidth: 2.5,
    }));
    // トラック
    if (r.minutes) {
      const p = pointAt(r, S.t / r.minutes);
      out.push(new L.ScatterplotLayer({
        id: 'truck-halo', data: [{ p }], getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 17, getFillColor: [92, 200, 255, 70],
      }));
      out.push(new L.ScatterplotLayer({
        id: 'truck', data: [{ p }], getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 6.5, stroked: true, filled: true,
        getFillColor: [255, 255, 255, 255], getLineColor: [92, 200, 255, 255], lineWidthUnits: 'pixels', getLineWidth: 3,
      }));
    }
  }
  // 出発地（ゆっくり広がる輪）
  const ph = (sec % 2.6) / 2.6;
  out.push(new L.ScatterplotLayer({
    id: 'origin-pulse', data: [{ p: [o.lon, o.lat] }], getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 8 + ph * 26, stroked: true, filled: false,
    getLineColor: [92, 200, 255, Math.round(200 * (1 - ph))], lineWidthUnits: 'pixels', getLineWidth: 1.6,
    updateTriggers: { getLineColor: [Math.round(ph * 30)], getRadius: [Math.round(ph * 30)] },
  }));
  out.push(new L.ScatterplotLayer({
    id: 'origin', data: [{ p: [o.lon, o.lat] }], getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 6, stroked: true, filled: true,
    getFillColor: [92, 200, 255, 255], getLineColor: [255, 255, 255, 255], lineWidthUnits: 'pixels', getLineWidth: 2,
  }));
  return out;
}

function frame(now) {
  // 時間の帯の再生
  const dt = Math.min(0.1, (now - (S.lastTick || now)) / 1000);
  S.lastTick = now;
  if (S.playing && S.route && S.route.minutes) {
    const M = S.route.minutes, axis = axisMax();
    S.t += (M / 12) * dt;
    if (S.t >= axis) { S.t = 0; }
    updateTime();
  }
  const targetAlpha = S.sel >= 0 ? 0.28 : 1;
  S.ambientAlpha += (targetAlpha - S.ambientAlpha) * Math.min(1, dt * 3);
  if (!S.staticLayers) S.staticLayers = staticLayers();
  overlay.setProps({ layers: [...S.staticLayers, ...dynamicLayers(now)] });
  placeTruckTag();
  requestAnimationFrame(frame);
}

/* ------------------------------------------------------------ 時間の帯 */

const axisMax = () => (S.route && S.route.minutes ? S.route.minutes * 1.06 : 1);
function setupTimebar() {
  const r = S.route, tb = document.body.classList;
  if (!r || !r.minutes) { tb.remove('has-route'); return; }
  const M = r.minutes, axis = axisMax();
  $('#tb-end').textContent = `到着 運転${fmtMin(M)}`;
  let t = '';
  const days = Math.ceil(M / 540);
  for (let d = 1; d < days; d++) t += `<div class="tick" style="left:${((d * 540) / axis) * 100}%"></div>`;
  t += `<div class="arrive" style="left:${(M / axis) * 100}%"></div>`;
  $('#tb-ticks').innerHTML = t;
  let lab = '';
  for (let d = 0; d < days; d++) lab += `<span class="st" style="left:${((d * 540) / axis) * 100}%">${d + 1}日目</span>`;
  lab += `<span class="arr" style="left:${(M / axis) * 100}%">到着</span>`;
  $('#tb-days').innerHTML = lab;
  tb.add('has-route');
  updateTime();
}
function updateTime() {
  const r = S.route;
  if (!r || !r.minutes) return;
  const M = r.minutes, axis = axisMax();
  S.t = Math.max(0, Math.min(axis, S.t));
  const pct = (S.t / axis) * 100;
  $('#tb-fill').style.width = pct + '%';
  $('#tb-handle').style.left = pct + '%';
  const arrived = S.t >= M;
  const day = Math.min(Math.ceil(M / 540), Math.floor(Math.max(0, S.t - 1e-6) / 540) + 1);
  $('#tb-now').textContent = S.t <= 0 ? '出発' : arrived ? '到着' : `出発から ${fmtMin(S.t)}`;
  $('#tb-day').textContent = arrived ? '' : `${day}日目`;
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
  const tag = $('#truck-tag'), r = S.route;
  if (!r || !r.minutes || !document.body.classList.contains('has-route')) { tag.classList.remove('on'); return; }
  const p = map.project(pointAt(r, S.t / r.minutes));
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
    const step = (S.route ? S.route.minutes : 100) / 40;
    if (e.key === 'ArrowRight') { S.t += step; updateTime(); e.preventDefault(); }
    if (e.key === 'ArrowLeft') { S.t -= step; updateTime(); e.preventDefault(); }
    if (e.key === ' ') { setPlaying(!S.playing); e.preventDefault(); }
  });
  $('#play').addEventListener('click', () => {
    if (!S.playing && S.route && S.t >= axisMax() - 1) S.t = 0;
    setPlaying(!S.playing);
  });
}

/* ------------------------------------------------------------ 凡例 */

function renderLegend() {
  const od = curOd(), m = S.meta.layers[S.layer], edges = od.edges[S.layer], n = edges.length - 1;
  $('#lg-title').textContent = m.label;
  $('#lg-unit').textContent = S.layer === 'km' ? 'km' : S.layer === 'days' ? '日（運転のみ・1日9時間まで）' : '円（2t車・税抜）';
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
  $('#lg-null').textContent = S.layer === 'fare' ? '値なし（経路なし、または200km未満で表の範囲外）' : '値なし（経路がない着地）';
}

/* ------------------------------------------------------------ 選択と詳細パネル */

const MODES = [
  { key: 'takuhai', label: '宅配' }, { key: 'rosen', label: '路線便' }, { key: 'jitbox', label: 'カゴ台車' },
  { key: 'kashikiri', label: '貸切' }, { key: 'rail', label: '鉄道' }, { key: 'ferry', label: 'フェリー' },
];
const CHIP = { '使える候補': 'ok', '要確認': 'check', '使えない': 'no', '計算できない': 'na' };

function buildCards(i) {
  const od = curOd(), code = S.idx.codes[i], key = `${S.origin}-${code}`, est = S.est[key];
  const cards = [];
  const NOTABLE = '運賃表なし・問い合わせ', NOTABLE_NOTE = 'この区間の運賃表は持っていません';
  for (const m of MODES) {
    if (est && est.modes[m.key]) {
      const e = est.modes[m.key];
      let tip = e.note || '';
      if (m.key === 'kashikiri' && e.alt) tip = `${tip}。OSRM の道路距離 ${e.alt.km}km で計算すると ${fmt(e.alt.amount)}円です`;
      if (e.amount === null) {
        cards.push({ label: m.label, status: e.status, amount: null, none: NOTABLE, tip: e.reason || '', days: e.days,
          src: e.source_name ? [e.source_name, e.source_url] : null });
      } else {
        cards.push({ label: m.label, sub: e.vehicle ? `${e.vehicle}車` : '', status: e.status, amount: e.amount, low: e.low, high: e.high,
          days: e.days, tip, src: e.source_name ? [e.source_name, e.source_url] : null });
      }
    } else if (m.key === 'kashikiri') {
      const km = od.km[i], fare = od.fare[i], d = od.days[i];
      const src = S.meta.kashikiri_source;
      if (km === null) {
        cards.push({ label: m.label, status: '計算できない', amount: null, none: '道路の経路がありません（離島など）', src: [src.name, src.url] });
      } else if (fare === null) {
        cards.push({ label: m.label, sub: '2t車', status: '使えない', amount: null, none: 'この表の範囲外（距離200km未満）', days: `運転だけで${d}日`,
          tip: '標準的運賃の表は200km以上です', src: [src.name, src.url] });
      } else {
        cards.push({ label: m.label, sub: '2t車', status: '使える候補', amount: fare, days: `運転だけで${d}日（積込み・荷待ちを除く）`,
          tip: `OSRM の道路距離 ${od.km[i].toFixed(1)}km で計算した国の目安です。会社ごとに違います`, src: [src.name, src.url] });
      }
    } else {
      cards.push({ label: m.label, status: '計算できない', amount: null, none: est ? NOTABLE : NOTABLE_NOTE, dim: true });
    }
  }
  return cards;
}

function renderCards(cards) {
  const max = Math.max(0, ...cards.map((c) => c.amount || 0));
  $('#cards').innerHTML = cards.map((c, n) => {
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
    return `<article class="card${c.dim ? ' dim' : ''}${none ? ' none' : ''}" style="--n:${n}"><div class="top"><h3>${c.label}${c.sub ? `<small>${c.sub}</small>` : ''}</h3>` +
      `${none}<span class="chip ${CHIP[c.status] || 'na'}">${c.status}</span></div>${amt}${note}${meta}${foot}</article>`;
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
  const minutes = od.min[i], km = od.km[i];
  if (demo) S.route = makeRoute(densify(demo.coordinates, 2), 'road', demo.minutes, demo.km);
  else S.route = makeRoute(arcPath(a, b, 0.1, 80), 'arc', minutes, km);
  S.t = 0;
  setPlaying(false);
  S.selectedAt = performance.now();
  if (!fromSearch) $('#q').value = S.idx.names[i];
  else $('#q').value = S.idx.names[i];
  renderDetail(i, demo);
  setupTimebar();
  document.body.classList.add('has-detail');
  $('#detail').setAttribute('aria-hidden', 'false');
  writeHash();
  if (fly) fitRoute();
}

function renderDetail(i, demo) {
  const od = curOd(), r = S.route;
  const km = demo ? demo.km : od.km[i], min = demo ? demo.minutes : od.min[i];
  const days = demo ? Math.max(1, Math.ceil(demo.minutes / 540)) : od.days[i];
  $('#d-from').textContent = od.name;
  $('#d-to').innerHTML = `<span class="ar">→</span><small>${escapeHtml(S.idx.prefs[i])}</small> ${escapeHtml(S.idx.names[i])}`;
  $('#s-km').innerHTML = km === null ? '—' : `${km.toFixed(1)}<small>km</small>`;
  $('#s-time').innerHTML = min === null ? '—' : fmtMin(min).replace(/(\d+)(時間|分)/g, '$1<small>$2</small>');
  $('#s-days').innerHTML = days === null ? '—' : `${days}<small>日</small>`;
  $('#route-kind').textContent = r.kind === 'road' ? '経路は OSRM の道路の形です' : '経路は直線の目安の弧です（道路の形は未取得）';
  renderCards(buildCards(i));
  $('#cards').scrollTop = 0;
  const kc = [...document.querySelectorAll('#cards .card')].find((el) => el.querySelector('h3').textContent.startsWith('貸切'));
  if (kc) setTimeout(() => kc.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 900);
}

function deselect() {
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
  map.fitBounds(JAPAN_BOUNDS, { padding: { top: 20, bottom: 110, left: 60, right: 110 }, duration: animate ? 1800 : 0 });
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
      if (k >= 0 && S.est[`${S.origin}-${S.idx.codes[i]}`]) k = -1; // デモの区間を先頭に
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
  const p = new URLSearchParams();
  p.set('o', S.origin);
  p.set('l', S.layer);
  if (S.sel >= 0) p.set('d', S.idx.codes[S.sel]);
  history.replaceState(null, '', '#' + p.toString());
}

/* ------------------------------------------------------------ 起動 */

function setLayer(l) {
  S.layer = l;
  document.querySelectorAll('.lyr').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.layer === l)));
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
  if (S.sel >= 0) { const i = S.sel; select(i, { fly: true }); } else writeHash();
}

function hoverTag(info) {
  const tag = $('#hover-tag');
  const o = info && info.object;
  if (!o || info.layer.id !== 'muni' || o.properties.i < 0) { tag.classList.remove('on'); return; }
  const i = o.properties.i;
  tag.innerHTML = `<b>${escapeHtml(S.idx.prefs[i])} ${escapeHtml(S.idx.names[i])}</b><span class="num">${escapeHtml(valueText(i))}</span>`;
  const x = Math.min(innerWidth - 260, info.x + 18), y = Math.max(8, info.y - 8);
  tag.style.transform = `translate(${x}px, ${y}px)`;
  tag.classList.add('on');
}

async function main() {
  const [meta, idx, muni, pref, routes, est, style] = await Promise.all([
    getJSON('data/meta.json'), getJSON('data/index.json'), getJSON('data/muni.geojson'), getJSON('data/pref.geojson'),
    getJSON('data/routes.json'), getJSON('data/estimates.json'), getJSON(STYLE_URL),
  ]);
  Object.assign(S, { meta, idx, muni, pref, routes, est });
  for (const f of muni.features) S.byIndex.set(f.properties.i, f);
  // 大きな都市（各都道府県の代表）。ゆるやかな流れの行き先
  meta.ambient = ['01101', '02201', '03201', '04101', '05201', '06201', '07201', '15103', '16201', '17201', '18201', '19201', '20201', '21201',
    '22101', '22130', '23106', '24201', '25201', '26104', '27128', '28110', '29201', '30201', '31201', '32201', '33101', '34101', '35203',
    '36201', '37201', '38201', '39201', '40131', '41201', '42201', '43101', '44201', '45201', '46201', '47201', '27140', '14100', '23101'];
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get('o') && meta.origins.some((o) => o.code === p.get('o'))) S.origin = p.get('o');
  if (['km', 'days', 'fare'].includes(p.get('l'))) S.layer = p.get('l');
  await loadOrigin(S.origin);

  $('#origin').innerHTML = meta.origins.map((o) => `<option value="${o.code}">${o.name}</option>`).join('');
  $('#origin').value = S.origin;
  $('#cargo').innerHTML = `<span>${meta.cargo.label} ${meta.cargo.count}個</span><span class="num">${meta.cargo.total_kg}kg</span>`;
  $('#cargo').title = meta.cargo.detail;
  document.querySelectorAll('.lyr').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.layer === S.layer)));

  const st = darkStyle(style);
  firstLabelId = st.layers.find((l) => l.type === 'symbol').id;
  map = new maplibregl.Map({
    container: 'map', style: st, center: [137.5, 37.2], zoom: 4.6, minZoom: 3.4, maxZoom: 12, attributionControl: false,
    dragRotate: false, pitchWithRotate: false, touchPitch: false, fadeDuration: 0, maxBounds: [[110, 15], [165, 55]],
  });
  map.touchZoomRotate.disableRotation();
  map.addControl(new maplibregl.AttributionControl({
    compact: false,
    customAttribution: ['国土数値情報（行政区域データ）（国土交通省）を加工して作成', '道路距離: OSRM（© OpenStreetMap contributors, ODbL）'],
  }), 'bottom-right');
  overlay = new deck.MapboxOverlay({
    interleaved: true, layers: [],
    getCursor: ({ isHovering, isDragging }) => (isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab'),
    onHover: hoverTag,
    onClick: (info) => { if (info.object && info.layer && info.layer.id === 'muni' && info.object.properties.i >= 0) select(info.object.properties.i); },
  });
  map.addControl(overlay);
  await new Promise((r) => (map.loaded() ? r() : map.once('load', r)));
  fitJapan(false);
  buildAmbient();
  renderLegend();
  bindSearch();
  bindTimebar();

  $('#origin').addEventListener('change', (e) => setOrigin(e.target.value));
  document.querySelectorAll('.lyr').forEach((b) => b.addEventListener('click', () => setLayer(b.dataset.layer)));
  $('#close').addEventListener('click', () => { deselect(); fitJapan(true); });
  $('#home').addEventListener('click', () => { if (S.sel >= 0) deselect(); fitJapan(true); });
  $('#make-request').addEventListener('click', () => toast('次の段階でつなぎます'));
  addEventListener('keydown', (e) => { if (e.key === 'Escape' && S.sel >= 0 && document.activeElement.tagName !== 'INPUT') { deselect(); fitJapan(true); } });

  if (p.get('d')) {
    const i = idx.codes.indexOf(p.get('d'));
    if (i >= 0) select(i, { fly: p.get('fly') !== '0' });
  }
  requestAnimationFrame(frame);
  setTimeout(() => $('#loading').classList.add('done'), 400);
  window.__app = { S, select, setLayer, setOrigin, map: () => map };
}

main().catch((e) => {
  console.error(e);
  $('#loading').textContent = '読み込みに失敗しました: ' + e.message;
});
