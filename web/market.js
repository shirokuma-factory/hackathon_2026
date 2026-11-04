'use strict';
/* 市場を見る（SPEC §16.1、§16.4 の表示）。
   不足量・偏りの比・県の出と入の差・S と実車率の曲線・JARTIC の集計は、tools/build_web_data.py が Python で計算して
   web/data/market.json と jartic.json に書いたもの。ここでは、距離の下限と件数での絞り込み、色や位置への変換、
   曲線の読み取りだけを行い、統計は計算し直さない。 */

const Market = (() => {
  const M = {
    d: null, j: null, mode: 'truck', n: 30, minKm: 200, x: 5, sel: null, hover: null, rows: [], flows: null,
    jartic: true, jpId: null, active: true, markers: [], bal: [], maxBal: 1, readIdx: -1,
  };
  const PERIOD = 24; // 流れの 1 周期（秒）
  const MODE_LABEL = { truck: '営業用トラック', ferry: 'フェリー', rail: '鉄道コンテナ' };
  const TEAL = [[20, 62, 66], [52, 222, 198]];      // 出荷が多い県（暗い → 明るい）
  const ORANGE = [[78, 50, 30], [250, 154, 72]];    // 入荷が多い県
  const J_UP = [125, 162, 255], J_DOWN = [243, 211, 107], J_NEUTRAL = [150, 160, 176];
  const ARROW = Hubs.ICON_ARROW;
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const n0 = (v) => Math.round(v).toLocaleString('ja-JP');
  const q = (s) => document.querySelector(s);
  const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
  const keyOf = (r) => `${r.fi}-${r.ti}`;

  /* ------------------------------------------------------------ 起動 */

  async function load() {
    const [d, j] = await Promise.all([
      fetch('data/market.json').then((r) => r.json()), fetch('data/jartic.json').then((r) => r.json()),
    ]);
    M.d = d;
    M.j = j;
    M.prefIdx = new Map(d.prefs.map((n, i) => [n, i]));
    for (const p of j.points) { p.fi = M.prefIdx.get(p.from); p.ti = M.prefIdx.get(p.to); }
  }

  function init() {
    const d = M.d;
    q('#sum-load').textContent = d.summary.load_factor_pct.toFixed(2);
    q('#sum-rate').textContent = d.summary.actual_rate_pct.toFixed(2);
    q('#sum-share').textContent = d.summary.shortfall_share_pct;
    q('#calc-formula').textContent = `実車率(後) = 実車率(今) × (1 + X × S)。S = Σ(不足量 × 直線距離) ÷ Σ(両向きの量 × 直線距離) = ${d.scale_s.toFixed(4)}（営業用トラックの全区間、物流センサス 2021 から計算）。`;
    q('#m-credits-body').innerHTML = [
      `${esc(d.source)}。${esc(d.unit)}`,
      '営業用普通車の積載効率・実車率: 国土交通省「自動車輸送統計年報」2025 年度（e-Stat 統計表 ID 000040511074 ほか）。出典は e-Stat、政府標準利用規約（第 2.0 版）に従って加工しています',
      `${esc(M.j.attribution)}。台数は参考値です。積荷の有無は分かりません`,
      '動く線の形: 国土数値情報（鉄道データ）（国土交通省）を加工して作成。道路は OSRM、フェリー航路・貨物専用線・空港は OpenStreetMap（© OpenStreetMap contributors, ODbL 1.0）',
      '拠点（貨物駅・港・フェリー航路）: © OpenStreetMap contributors（ODbL 1.0）／地図: OpenFreeMap © OpenMapTiles Data from OpenStreetMap／行政区域: 国土数値情報（国土交通省）を加工',
    ].map((t) => `<div>${t}</div>`).join('');
    q('#m-mode').addEventListener('click', (e) => { const b = e.target.closest('button[data-mode]'); if (b) setMode(b.dataset.mode); });
    q('#m-n').addEventListener('input', (e) => { M.n = Number(e.target.value); refresh(false); });
    q('#m-km').addEventListener('input', (e) => { M.minKm = Number(e.target.value); refresh(false); });
    q('#m-x').addEventListener('input', (e) => { M.x = Number(e.target.value); renderCalc(); });
    q('#m-tbody').addEventListener('click', (e) => { const tr = e.target.closest('tr[data-k]'); if (tr) select(tr.dataset.k, { fly: true }); });
    q('#m-tbody').addEventListener('keydown', (e) => {
      const tr = e.target.closest('tr[data-k]');
      if (tr && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); select(tr.dataset.k, { fly: true }); }
    });
    q('#m-tbody').addEventListener('mouseover', (e) => { const tr = e.target.closest('tr[data-k]'); M.hover = tr ? tr.dataset.k : null; });
    q('#m-tbody').addEventListener('mouseleave', () => { M.hover = null; });
    q('#m-pair').addEventListener('click', onPairClick);
    q('#m-pair').addEventListener('mousemove', onChartMove);
    q('#m-pair').addEventListener('mouseleave', () => hideRead());
    renderCalc();
    renderLegend();
    refresh(false);
  }

  function setMode(m) {
    if (!MODE_LABEL[m]) return;
    M.mode = m;
    M.sel = null;
    refresh(true);
  }

  /* ------------------------------------------------------------ 絞り込みと表 */

  // 件数と距離の下限で絞る（market.json の区間は不足量の大きい順に並べてある）。計算ではなく表示の絞り込み。
  function refresh(animate) {
    const md = M.d.modes[M.mode];
    M.bal = md.balance;
    M.maxBal = Math.max(1, ...M.bal.map((v) => Math.abs(v)));
    const all = md.pairs.filter((p) => p[6] >= M.minKm);
    M.rows = all.slice(0, M.n).map((p) => ({ fi: p[0], ti: p[1], go: p[2], back: p[3], ratio: p[4], short: p[5], km: p[6] }));
    for (const r of M.rows) r.k = keyOf(r);
    if (M.sel && !M.rows.some((r) => r.k === M.sel)) M.sel = null;
    document.querySelectorAll('#m-mode button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === M.mode)));
    q('#m-caveat').hidden = M.mode !== 'rail';
    q('#m-n-out').textContent = `${M.n} 区間`;
    q('#m-km-out').textContent = `${M.minKm} km 以上`;
    q('#m-n').value = M.n;
    q('#m-km').value = M.minKm;
    q('#m-count').textContent = `${MODE_LABEL[M.mode]}: 条件に合う ${all.length} 区間のうち、不足量の大きい ${M.rows.length} 区間を出しています`;
    const t = M.d.totals[M.mode];
    q('#m-rsub').textContent = `${MODE_LABEL[M.mode]}・上位 ${M.rows.length}`;
    q('#m-rtot').innerHTML = `県をまたぐ量 ${n0(t.interpref_t)} t のうち、逆向きに同じ量の荷が無い分は ${n0(t.shortfall_t)} t（${Math.round(t.share * 100)}%）。` +
      (M.mode === 'rail' ? ' 鉄道コンテナは 3 日間の標本が小さく、参考です。' : '');
    renderTable();
    buildFlows();
    renderLegend();
    renderPair();
    syncHash();
  }

  function renderTable() {
    q('#m-tbody').innerHTML = M.rows.map((r, i) => {
      const P = M.d.prefs;
      return `<tr data-k="${r.k}" tabindex="0" class="${r.k === M.sel ? 'on' : ''}"><td class="c-rk num">${i + 1}</td>` +
        `<td class="c-pair">${esc(P[r.fi].replace(/[県府]$/, ''))}<span class="ar">&rarr;</span>${esc(P[r.ti].replace(/[県府]$/, ''))}</td>` +
        `<td class="num">${n0(r.go)}</td><td class="num">${n0(r.back)}</td><td class="num">${r.ratio.toFixed(3)}</td>` +
        `<td class="num"><b>${n0(r.short)}</b></td><td class="num">${n0(r.km)}</td></tr>`;
    }).join('') || '<tr><td colspan="7" class="empty">条件に合う区間がありません。距離の下限を下げてください</td></tr>';
  }

  function renderCalc() {
    const d = M.d, i = Math.round(M.x / 0.5), cur = d.summary.actual_rate_pct;
    const v = d.curve[Math.min(i, d.curve.length - 1)][1];
    q('#m-x-out').textContent = `${M.x.toFixed(1)}%`;
    q('#calc-from').textContent = cur.toFixed(2);
    q('#calc-to').textContent = v.toFixed(2);
    q('#calc-d').textContent = `+${(v - cur).toFixed(2)} ポイント`;
  }

  /* ------------------------------------------------------------ 凡例 */

  const balColor = (v) => {
    const t = Math.pow(Math.min(1, Math.abs(v) / M.maxBal), 0.5);
    const pal = v >= 0 ? TEAL : ORANGE;
    return [...mix(pal[0], pal[1], t), 214];
  };
  const css = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;

  function renderLegend() {
    const mu = Muni.isMuni(), maxBal = mu ? Muni.maxBal() : M.maxBal, colorOf = mu ? Muni.balColor : balColor;
    Muni.renderBalHead();
    const steps = 24, cells = [];
    for (let i = 0; i < steps; i++) {
      const s = (i / (steps - 1)) * 2 - 1; // -1（入荷が多い）〜 1（出荷が多い）
      const v = Math.sign(s) * maxBal * s * s;
      cells.push(`<i style="background:${css(colorOf(v))}"></i>`);
    }
    q('#mlg-bar').innerHTML = cells.join('');
    // 目盛りは、表示用の平方根の尺度の逆変換で置く。1・2・5 × 10^k のうち、尺度の 0.4 と 0.8 に近い値を選ぶ
    const nice = [];
    for (let e = 1; e <= 6; e++) for (const m of [1, 2, 5]) nice.push(m * Math.pow(10, e));
    const pick = (f) => nice.reduce((b2, v) => (Math.abs(Math.sqrt(v / maxBal) - f) < Math.abs(Math.sqrt(b2 / maxBal) - f) ? v : b2), nice[0]);
    const cand = [...new Set([0.4, 0.8].map(pick))].filter((v) => v < maxBal);
    const marks = [...cand.slice().reverse().map((v) => -v), 0, ...cand];
    const lab = (v) => (v === 0 ? '0' : (v < 0 ? '−' : '+') + (Math.abs(v) >= 1e4 ? `${Math.abs(v) / 1e4}万` : n0(Math.abs(v))));
    q('#mlg-ticks').innerHTML = marks.map((v) => {
      const pos = 50 + Math.sign(v) * Math.sqrt(Math.abs(v) / maxBal) * 50;
      return `<span style="left:${pos}%">${lab(v)}</span>`;
    }).join('');
    q('#mlg-jartic').hidden = !M.jartic;
    q('#mlg-flow').hidden = false;
  }

  /* ------------------------------------------------------------ 流れ（動く矢印） */

  const clampI = (v, a, b) => Math.max(a, Math.min(b, v));

  function buildFlows() {
    const cap = M.d.cap, P = M.d.prefs, maxShort = Math.max(1, ...M.rows.map((r) => r.short));
    const items = [], base = [], routes = [];
    M.rows.forEach((r, ri) => {
      const a = cap[r.fi], b = cap[r.ti];
      // 実際の線（営業用トラックは道路、フェリーは航路を含む経路、鉄道コンテナは線路を含む経路）。作っていない区間は弧
      const real = Rt.pairPath(P[r.fi], P[r.ti], M.mode, 0.02);
      const pts = real && real.length > 2 ? real : arcPath(a, b, 0.14, 48);
      const cum = [0];
      for (let k = 1; k < pts.length; k++) cum.push(cum[k - 1] + hav(pts[k - 1], pts[k]));
      const total = cum[cum.length - 1] || 1;
      const rt = { path: pts, cum, total };
      const w = 1.6 + 6 * Math.sqrt(r.short / maxShort);
      const bright = 1 - Math.min(1, r.ratio); // 帰りの荷が少ないほど明るい
      const col = mix([88, 150, 205], [236, 249, 255], Math.pow(bright, 0.6));
      const reps = clampI(Math.round(PERIOD / (6 + r.km / 100)), 1, 4), dur = PERIOD / reps;
      const ph = ((ri * 0.6180339) % 1) * dur;
      const route = { r, rt, w, col, dur, starts: [], real: pts === real };
      for (let k = 0; k < 2; k++) {
        for (let rep = 0; rep < reps; rep++) {
          const s = (ph + (k * dur) / 2 + rep * dur) % PERIOD;
          route.starts.push(s);
          for (const sh of [0, -PERIOD]) items.push({ pts, ts: cum.map((c) => (c / total) * dur + s + sh), route });
        }
      }
      base.push({ pts, route });
      routes.push(route);
    });
    M.flows = { items, base, routes };
    M.endpoints = [...new Set(M.rows.flatMap((r) => [r.fi, r.ti]))].map((i) => ({ p: M.d.cap[i], name: M.d.prefs[i] }));
  }

  const dimOf = (route) => {
    const key = route.r.k;
    if (M.hover && M.hover !== key) return M.sel === key ? 1 : 0.35;
    if (M.sel && M.sel !== key) return 0.28;
    return 1;
  };

  function headData(sec) {
    const cur = sec % PERIOD, out = [];
    for (const route of M.flows.routes) {
      const dim = dimOf(route);
      for (const s of route.starts) {
        const p = ((((cur - s) % PERIOD) + PERIOD) % PERIOD) / route.dur;
        if (p >= 1) continue;
        const pos = pointAt(route.rt, p), nx = pointAt(route.rt, Math.min(1, p + 0.012));
        const dx = (nx[0] - pos[0]) * Math.cos((pos[1] * Math.PI) / 180), dy = nx[1] - pos[1];
        out.push({ p: pos, ang: -(Math.atan2(dx, dy) * 180) / Math.PI, route, dim, fade: Math.min(1, p * 8, (1 - p) * 8) });
      }
    }
    return out;
  }

  // 空きを探す・つなぐの地図の下に、帰りの空きやすさの流れだけをうすく重ねる（k = 全体の濃さ）
  function flowLayers(now, k) {
    const L = deck, F = M.flows, out = [];
    if (!F || !F.routes.length) return out;
    const cur = (now / 1000) % PERIOD;
    out.push(new L.PathLayer({
      id: 'f-flow-base', data: F.base, getPath: (d) => d.pts, widthUnits: 'pixels', getWidth: (d) => Math.max(1, d.route.w * 0.4),
      getColor: (d) => [...d.route.col, 70 * k * dimOf(d.route)], capRounded: true, updateTriggers: { getColor: [M.sel, M.mode], getWidth: [M.mode] },
    }));
    out.push(new L.TripsLayer({
      id: 'f-flow-core', data: F.items, getPath: (d) => d.pts, getTimestamps: (d) => d.ts, widthUnits: 'pixels', capRounded: true, jointRounded: true,
      currentTime: cur, fadeTrail: true, getWidth: (d) => d.route.w * 0.7, trailLength: 2.6,
      getColor: (d) => [...d.route.col, 150 * k * dimOf(d.route)], updateTriggers: { getColor: [M.sel, M.mode], getWidth: [M.mode] },
    }));
    return out;
  }

  /* ------------------------------------------------------------ JARTIC の点 */

  function jColor(p) {
    const lr = Math.log(p.ratio); // 比 = 上り ÷ 下り
    const t = Math.min(1, Math.abs(lr) / Math.log(1.45));
    return mix(J_NEUTRAL, lr >= 0 ? J_UP : J_DOWN, 0.25 + 0.75 * t);
  }

  /* ------------------------------------------------------------ 層 */

  function layers(now, zoom) {
    const L = deck, out = [], sec = now / 1000, P = M.d.prefs;
    const bal = M.bal;
    const mu = Muni.isMuni();
    out.push(...Muni.under());
    out.push(new L.GeoJsonLayer({
      id: 'm-pref', data: S.pref, pickable: Muni.prefFilled(), stroked: true, filled: Muni.prefFilled(), beforeId: firstLabelId,
      getFillColor: (f) => { const i = M.prefIdx.get(f.properties.n); return i === undefined ? [60, 70, 90, 80] : balColor(bal[i]); },
      getLineColor: mu ? [236, 244, 255, 190] : [210, 226, 255, 80], lineWidthMinPixels: mu ? 1.6 : 0.8, getLineWidth: mu ? 1.6 : 0.8, lineWidthUnits: 'pixels',
      autoHighlight: Muni.prefFilled(), highlightColor: [255, 255, 255, 38],
      updateTriggers: { getFillColor: [M.mode], getLineColor: [mu], getLineWidth: [mu] }, transitions: { getFillColor: { duration: 650, easing: ease } },
    }));
    if (Hubs.on) out.push(...Hubs.layers(zoom));
    const F = M.flows;
    if (F && F.routes.length) {
      out.push(new L.PathLayer({
        id: 'm-flow-base', data: F.base, getPath: (d) => d.pts, widthUnits: 'pixels', getWidth: (d) => Math.max(1, d.route.w * 0.5),
        getColor: (d) => (d.route.real ? [...Rt.col(M.mode), 92 * dimOf(d.route)] : [...d.route.col, 52 * dimOf(d.route)]), capRounded: true,
        updateTriggers: { getColor: [M.sel, M.hover, M.mode], getWidth: [M.mode] },
      }));
      out.push(new L.PathLayer({
        id: 'm-flow-pick', data: F.base, getPath: (d) => d.pts, widthUnits: 'pixels', getWidth: 14, getColor: [255, 255, 255, 3], pickable: true,
      }));
      const cur = sec % PERIOD;
      const tripProps = {
        data: F.items, getPath: (d) => d.pts, getTimestamps: (d) => d.ts, widthUnits: 'pixels', capRounded: true, jointRounded: true,
        currentTime: cur, fadeTrail: true,
      };
      out.push(new L.TripsLayer({
        ...tripProps, id: 'm-flow-glow', getWidth: (d) => d.route.w * 2.6, trailLength: 3.4,
        getColor: (d) => [...d.route.col, 46 * dimOf(d.route)], updateTriggers: { getColor: [M.sel, M.hover, M.mode], getWidth: [M.mode] },
      }));
      out.push(new L.TripsLayer({
        ...tripProps, id: 'm-flow-core', getWidth: (d) => d.route.w, trailLength: 2.6,
        getColor: (d) => [...d.route.col, 215 * dimOf(d.route)], updateTriggers: { getColor: [M.sel, M.hover, M.mode], getWidth: [M.mode] },
      }));
      out.push(new L.IconLayer({
        id: 'm-flow-head', data: headData(sec), getPosition: (d) => d.p, getIcon: () => Rt.icons()[M.mode === 'truck' ? 'truck' : M.mode], getAngle: (d) => d.ang,
        getSize: (d) => (M.mode === 'truck' ? 15 : 20) + d.route.w * 1.9, sizeUnits: 'pixels', getColor: (d) => [255, 255, 255, 255 * d.dim * d.fade], billboard: false,
        updateTriggers: { getIcon: [M.mode], getSize: [M.mode] },
      }));
      out.push(new L.ScatterplotLayer({
        id: 'm-ends', data: M.endpoints, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 2.6, filled: true, getFillColor: [225, 240, 255, 150],
      }));
    }
    if (M.sel) {
      const r = M.rows.find((x) => x.k === M.sel);
      if (r) {
        const ph = (sec % 2.4) / 2.4, ends = [M.d.cap[r.fi], M.d.cap[r.ti]];
        out.push(new L.ScatterplotLayer({
          id: 'm-sel-pulse', data: ends, getPosition: (d) => d, radiusUnits: 'pixels', getRadius: 8 + ph * 24, stroked: true, filled: false,
          getLineColor: [255, 255, 255, Math.round(190 * (1 - ph))], lineWidthUnits: 'pixels', getLineWidth: 1.6,
          updateTriggers: { getLineColor: [Math.round(ph * 30)], getRadius: [Math.round(ph * 30)] },
        }));
        out.push(new L.ScatterplotLayer({
          id: 'm-sel-ends', data: ends, getPosition: (d) => d, radiusUnits: 'pixels', getRadius: 5.5, filled: true, getFillColor: [255, 255, 255, 255],
          stroked: true, getLineColor: [10, 15, 25, 255], lineWidthUnits: 'pixels', getLineWidth: 1.5,
        }));
      }
    }
    if (M.jartic) {
      const pts = M.j.points, selPair = M.sel ? M.rows.find((x) => x.k === M.sel) : null;
      const inSel = (p) => selPair && p.fi === selPair.fi && p.ti === selPair.ti;
      out.push(new L.ScatterplotLayer({
        id: 'm-jartic', data: pts, getPosition: (p) => [p.lon, p.lat], pickable: true, radiusUnits: 'pixels',
        getRadius: (p) => (p.id === M.jpId ? 10 : inSel(p) ? 8.5 : 7), filled: true, getFillColor: (p) => [...jColor(p), 235],
        stroked: true, getLineColor: (p) => (p.id === M.jpId ? [255, 255, 255, 255] : [12, 18, 30, 235]), lineWidthUnits: 'pixels',
        getLineWidth: (p) => (p.id === M.jpId ? 2.4 : 1.4), updateTriggers: { getRadius: [M.sel, M.jpId], getLineColor: [M.jpId], getLineWidth: [M.jpId] },
      }));
      out.push(new L.IconLayer({
        id: 'm-jartic-arrow', data: pts, getPosition: (p) => [p.lon, p.lat], getIcon: () => ARROW, getAngle: (p) => (p.ratio >= 1 ? 0 : 180),
        getSize: 13, sizeUnits: 'pixels', getPixelOffset: [17, 0], getColor: (p) => [...jColor(p), 255],
      }));
    }
    out.push(...Muni.over(zoom));
    return out;
  }

  /* ------------------------------------------------------------ 選択 */

  const padding = () => (innerWidth <= 1100 ? { top: 120, bottom: 60, left: 40, right: 40 } : { top: 150, bottom: 190, left: 430, right: (document.body.classList.contains('m-right-collapsed') ? 300 : (q('#m-right').offsetWidth || 480)) + 80 });

  function fitPair(r) {
    const a = M.d.cap[r.fi], b = M.d.cap[r.ti];
    const fr = M.flows && M.flows.routes.find((x) => x.r.k === r.k);
    const mid = fr ? fr.rt.path : arcPath(a, b, 0.14, 24);
    const xs = mid.map((p) => p[0]), ys = mid.map((p) => p[1]);
    map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]], { padding: padding(), maxZoom: 7.4, duration: 2000, essential: true, curve: 1.5 });
  }

  function select(k, { fly = true } = {}) {
    const r = M.rows.find((x) => x.k === k);
    if (!r) return;
    M.sel = k;
    const pt = M.j.points.filter((p) => p.fi === r.fi && p.ti === r.ti);
    if (!pt.some((p) => p.id === M.jpId)) M.jpId = pt.length ? pt[0].id : null;
    renderTable();
    renderPair();
    syncMarkers();
    syncHash();
    const row = q(`#m-tbody tr[data-k="${k}"]`);
    if (row) row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    if (fly) fitPair(r);
  }

  function deselect(fly) {
    M.sel = null;
    renderTable();
    renderPair();
    syncMarkers();
    syncHash();
    if (fly) fitJapan(true);
  }

  function syncMarkers() {
    M.markers.forEach((m) => m.remove());
    M.markers = [];
    if (!M.sel || !M.active) return;
    const r = M.rows.find((x) => x.k === M.sel);
    if (!r) return;
    for (const [i, cls] of [[r.fi, 'from'], [r.ti, 'to']]) {
      const el = document.createElement('div');
      el.className = `m-end ${cls}`;
      el.textContent = M.d.prefs[i];
      M.markers.push(new maplibregl.Marker({ element: el, anchor: 'bottom', offset: [0, -12] }).setLngLat(M.d.cap[i]).addTo(map));
    }
  }

  /* ------------------------------------------------------------ 右の区間の詳細と折れ線 */

  function renderPair() {
    const box = q('#m-pair');
    const r = M.sel ? M.rows.find((x) => x.k === M.sel) : null;
    document.body.classList.toggle('m-has-pair', !!r);
    if (!r) { box.hidden = true; box.innerHTML = ''; return; }
    const P = M.d.prefs, a = P[r.fi], b = P[r.ti];
    const pts = M.j.points.filter((p) => p.fi === r.fi && p.ti === r.ti);
    const mode = M.mode === 'truck' ? '営業用トラック' : M.mode === 'ferry' ? 'フェリー' : '鉄道コンテナ';
    let h = `<div class="mp-top"><div class="mp-title"><span>${esc(a)}</span><span class="ar">&rarr;</span><span>${esc(b)}</span></div>` +
      `<button class="mp-x" data-act="close" aria-label="区間の選択を外す">&#10005;</button></div>` +
      `<div class="mp-stats"><div><span>行き</span><b class="num">${n0(r.go)}</b><small>t</small></div><div><span>帰り</span><b class="num">${n0(r.back)}</b><small>t</small></div>` +
      `<div><span>偏りの比</span><b class="num">${r.ratio.toFixed(3)}</b></div><div><span>不足量</span><b class="num">${n0(r.short)}</b><small>t</small></div>` +
      `<div><span>直線</span><b class="num">${n0(r.km)}</b><small>km</small></div></div>` +
      `<p class="mp-msg">${esc(a)}から${esc(b)}へ運ぶ荷に対して、${esc(b)}から${esc(a)}へ戻る荷は少ないため、<b>この向きの帰り（${esc(b)}→${esc(a)}）が空きやすい</b>区間です。</p>` +
      `<ul class="mp-notes"><li>2021 年 10 月 19〜21 日の 3 日間の出荷で、年間の量ではありません。</li><li>量は重さ（トン）で、便の数ではありません。</li>` +
      `<li>県の単位で、距離は県庁所在地どうしの直線です。${mode}の荷だけを数えています。</li><li>傾向を示すもので、個々の運送会社の空きではありません。</li></ul>`;
    if (pts.length) {
      if (!pts.some((p) => p.id === M.jpId)) M.jpId = pts[0].id;
      const cur = pts.find((p) => p.id === M.jpId);
      h += `<div class="mp-j"><div class="mp-jh"><b>今週の道路の大型車</b><span>国の常設観測点（JARTIC）・1 時間ごと</span></div>` +
        `<div class="mp-chips" role="group" aria-label="観測点">${pts.map((p, i) => `<button data-act="pt" data-id="${p.id}" aria-pressed="${p.id === M.jpId}">地点 ${i + 1}</button>`).join('')}</div>` +
        `<div class="mp-read" id="ch-read"></div><div id="ch-box">${chartSvg(cur)}</div>` +
        `<div class="mp-jstat num"><span><i class="dot dot-up"></i>上り計 <b>${n0(cur.up_total)}</b> 台</span><span><i class="dot dot-down"></i>下り計 <b>${n0(cur.down_total)}</b> 台</span>` +
        `<span>上り÷下り <b>${cur.ratio.toFixed(3)}</b></span><span>欠測 <b>${cur.missing_hours}</b> 時間（観測 ${cur.observed_hours} 時間）</span></div>` +
        `<p class="mp-warn">上り・下りは道路の定義（路線の終点から起点へ向かう方向が上り）で、この区間の行き帰りとは一致しません。台数は参考値で、積荷の有無は分かりません。</p>` +
        `<details class="mp-more"><summary>取得の条件</summary><p>${esc(periodText())}。欠測の時間は 0 台として数えず、線を途切れさせています。地点は区間の直線から ${cur.dist_km}km 以内の常設観測点で、一般道・高速の全てではありません。2021 年の調査は 3 日間です。</p></details></div>`;
    } else {
      h += `<p class="mp-nojt">この区間の大型車の観測点は取得していません（取得したのは兵庫–東京、東京–三重、神奈川–愛知の 3 区間だけです）。</p>`;
    }
    h += `<button class="btn-primary mp-go" data-act="find">この区間の空き便を探す</button>`;
    box.innerHTML = h;
    box.hidden = false;
    const hd = box.querySelector('.mp-jh');
    if (hd) hideRead();
  }

  function periodText() {
    const p = M.j.period, f = (s) => `${s.slice(5, 7)}/${s.slice(8, 10)}`;
    return `取得 ${M.j.fetched_at.slice(0, 16).replace('T', ' ')}、期間 ${f(p.start)}〜${f(p.end)} の ${p.hours} 時間`;
  }

  function onPairClick(e) {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    if (b.dataset.act === 'close') deselect(true);
    else if (b.dataset.act === 'pt') { M.jpId = Number(b.dataset.id); renderPair(); }
    else if (b.dataset.act === 'find') { App.setView('find'); }
  }

  // 折れ線（SVG）。上りと下りを 1 時間ごとに。欠測（null）では線を切る。値の変換は座標への写像だけ。
  const CH = { w: 448, h: 150, l: 40, r: 8, t: 8, b: 22 };
  function chartSvg(p) {
    const n = p.up.length, iw = CH.w - CH.l - CH.r, ih = CH.h - CH.t - CH.b;
    const vals = [...p.up, ...p.down].filter((v) => v !== null);
    const ymax = chartYMax(Math.max(1, ...vals));
    const X = (i) => CH.l + (i / (n - 1)) * iw, Y = (v) => CH.t + ih - (v / ymax) * ih;
    const path = (arr) => {
      let d = '', pen = false;
      arr.forEach((v, i) => { if (v === null) { pen = false; return; } d += `${pen ? 'L' : 'M'}${X(i).toFixed(1)} ${Y(v).toFixed(1)}`; pen = true; });
      return d;
    };
    const start = new Date(Number(String(M.j.first_hour).slice(0, 4)), Number(String(M.j.first_hour).slice(4, 6)) - 1, Number(String(M.j.first_hour).slice(6, 8)));
    const wd = ['日', '月', '火', '水', '木', '金', '土'];
    let g = '';
    for (const f of [0, 0.5, 1]) {
      const y = CH.t + ih - f * ih;
      g += `<line x1="${CH.l}" x2="${CH.w - CH.r}" y1="${y}" y2="${y}" class="gl"/><text x="${CH.l - 6}" y="${y + 3.5}" class="yl" text-anchor="end">${n0(ymax * f)}</text>`;
    }
    for (let d = 0; d < n / 24; d++) {
      const x = X(d * 24), dt = new Date(start.getTime() + d * 86400000);
      g += `<line x1="${x}" x2="${x}" y1="${CH.t}" y2="${CH.t + ih}" class="dl"/><text x="${x + 3}" y="${CH.h - 6}" class="xl">${dt.getMonth() + 1}/${dt.getDate()}(${wd[dt.getDay()]})</text>`;
    }
    const gaps = p.up.map((v, i) => (v === null || p.down[i] === null ? i : -1)).filter((i) => i >= 0)
      .map((i) => `<rect x="${X(i) - 1.5}" y="${CH.t}" width="3" height="${ih}" class="gap"/>`).join('');
    return `<svg id="ch" viewBox="0 0 ${CH.w} ${CH.h}" width="100%" role="img" aria-label="上りと下りの大型車の台数、1 時間ごと">${g}${gaps}` +
      `<path d="${path(p.down)}" class="ln down"/><path d="${path(p.up)}" class="ln up"/>` +
      `<line id="ch-cur" y1="${CH.t}" y2="${CH.t + ih}" class="cur" visibility="hidden"/><circle id="ch-cu" r="3.2" class="pt up" visibility="hidden"/><circle id="ch-cd" r="3.2" class="pt down" visibility="hidden"/></svg>`;
  }

  function hideRead() {
    const r = q('#ch-read');
    if (r) r.innerHTML = '<span class="mp-hint">折れ線にあわせると 1 時間ごとの台数が出ます</span>';
    for (const id of ['#ch-cur', '#ch-cu', '#ch-cd']) { const e = q(id); if (e) e.setAttribute('visibility', 'hidden'); }
  }

  function onChartMove(e) {
    const svg = e.target.closest('#ch');
    if (!svg) return;
    const sel = M.j.points.find((p) => p.id === M.jpId);
    if (!sel) return;
    const b = svg.getBoundingClientRect(), sc = CH.w / b.width;
    const n = sel.up.length, i = clampI(Math.round((((e.clientX - b.left) * sc - CH.l) / (CH.w - CH.l - CH.r)) * (n - 1)), 0, n - 1);
    const x = CH.l + (i / (n - 1)) * (CH.w - CH.l - CH.r);
    const raw = Math.max(1, ...[...sel.up, ...sel.down].filter((v) => v !== null));
    const cur = q('#ch-cur');
    cur.setAttribute('x1', x); cur.setAttribute('x2', x); cur.setAttribute('visibility', 'visible');
    const ih = CH.h - CH.t - CH.b, ymax = chartYMax(raw);
    for (const [id, v] of [['#ch-cu', sel.up[i]], ['#ch-cd', sel.down[i]]]) {
      const c = q(id);
      if (v === null) { c.setAttribute('visibility', 'hidden'); continue; }
      c.setAttribute('cx', x); c.setAttribute('cy', CH.t + ih - (v / ymax) * ih); c.setAttribute('visibility', 'visible');
    }
    const s = String(M.j.first_hour), d0 = new Date(Number(s.slice(0, 4)), Number(s.slice(4, 6)) - 1, Number(s.slice(6, 8)), 0);
    const dt = new Date(d0.getTime() + i * 3600000), wd = ['日', '月', '火', '水', '木', '金', '土'];
    const v = (a) => (a === null ? '欠測' : `${n0(a)} 台`);
    q('#ch-read').innerHTML = `<span class="num">${dt.getMonth() + 1}/${dt.getDate()}(${wd[dt.getDay()]}) ${String(dt.getHours()).padStart(2, '0')}:00</span>` +
      `<span class="num"><i class="dot dot-up"></i>上り ${v(sel.up[i])}</span><span class="num"><i class="dot dot-down"></i>下り ${v(sel.down[i])}</span>`;
  }
  function chartYMax(raw) { // 目盛りが整数になる上端（最大値以上の、桁の 2 倍の倍数）
    const step = Math.pow(10, Math.floor(Math.log10(raw)));
    return Math.ceil(raw / (2 * step)) * 2 * step;
  }

  /* ------------------------------------------------------------ 地図の上の操作 */

  function hover(info) {
    const tag = q('#hover-tag'), o = info && info.object, id = info && info.layer && info.layer.id;
    let t = null, s = null;
    if (o && id === 'm-pref') {
      const i = M.prefIdx.get(o.properties.n);
      if (i !== undefined) {
        const v = M.bal[i];
        t = o.properties.n;
        s = `出荷 − 入荷 ${v >= 0 ? '+' : '−'}${n0(Math.abs(v))} t（${v >= 0 ? '出荷が多い' : '入荷が多い'}）`;
      }
    } else if (o && id === 'm-flow-pick') {
      const r = o.route.r;
      t = `${M.d.prefs[r.fi]} → ${M.d.prefs[r.ti]}`;
      s = `行き ${n0(r.go)} t ／ 帰り ${n0(r.back)} t ／ 不足量 ${n0(r.short)} t`;
    } else if (o && id === 'm-jartic') {
      t = `大型車 ${o.pair}（国の観測点）`;
      s = `上り計 ${n0(o.up_total)} 台 ／ 下り計 ${n0(o.down_total)} 台 ／ 比 ${o.ratio.toFixed(3)}`;
    } else {
      const mh = Muni.hoverText(info);
      const hh = mh || (Hubs.on ? Hubs.hoverText(info) : null);
      if (hh) { t = hh.t; s = hh.s; }
    }
    if (!t) { tag.classList.remove('on'); return; }
    tag.innerHTML = `<b>${esc(t)}</b><span class="num">${esc(s)}</span>`;
    tag.style.transform = `translate(${Math.min(innerWidth - 420, info.x + 18)}px, ${Math.max(8, info.y - 8)}px)`;
    tag.classList.add('on');
  }

  function click(info) {
    const o = info && info.object, id = info && info.layer && info.layer.id;
    if (Muni.click(info)) return;
    if (o && id === 'm-flow-pick') return select(o.route.r.k, { fly: true });
    if (o && id === 'm-jartic') {
      const r = M.rows.find((x) => x.fi === o.fi && x.ti === o.ti);
      if (r) { M.jpId = o.id; select(r.k, { fly: M.sel !== r.k }); renderPair(); }
      else { toast('この点の区間は、いまの表示（件数・距離の下限・手段）に含まれていません'); }
      return;
    }
    if (!o && M.sel) deselect(false);
  }

  /* ------------------------------------------------------------ 状態の URL */

  function syncHash() {
    if (!M.active) return;
    const p = new URLSearchParams(location.hash.slice(1));
    p.set('v', 'market');
    for (const k of ['o', 'l', 'd']) p.delete(k);
    p.set('m', M.mode);
    p.set('n', String(M.n));
    p.set('km', String(M.minKm));
    if (M.sel) p.set('p', M.sel); else p.delete('p');
    history.replaceState(null, '', '#' + p.toString());
  }

  function readHash(p) {
    if (['truck', 'ferry', 'rail'].includes(p.get('m'))) M.mode = p.get('m');
    if (p.get('n') && Number(p.get('n')) >= 5) M.n = Math.min(100, Number(p.get('n')));
    if (p.get('km') && Number(p.get('km')) >= 0) M.minKm = Math.min(800, Number(p.get('km')));
    if (p.get('p')) M.pendingSel = p.get('p');
    if (p.get('x')) M.x = Math.min(20, Math.max(0, Number(p.get('x')) || 0));
  }

  function setActive(on) {
    M.active = on;
    Muni.setActive(on);
    if (!on) { M.markers.forEach((m) => m.remove()); M.markers = []; q('#hover-tag').classList.remove('on'); }
    else { syncMarkers(); syncHash(); }
  }

  function afterStart() {
    if (M.pendingSel) { const k = M.pendingSel; M.pendingSel = null; if (M.rows.some((r) => r.k === k)) select(k, { fly: true }); }
    q('#m-x').value = M.x;
    renderCalc();
  }

  function setJartic(on) { M.jartic = on; renderLegend(); }
  const selected = () => (M.sel ? M.rows.find((x) => x.k === M.sel) : null);
  const pairText = () => { const r = selected(); return r ? `${M.d.prefs[r.fi]}→${M.d.prefs[r.ti]}` : null; };
  const pairNames = () => { const r = selected(); return r ? { from: M.d.prefs[r.fi], to: M.d.prefs[r.ti] } : null; };

  return { load, init, renderLegend, layers, flowLayers, hover, click, setActive, syncHash, readHash, afterStart, setJartic, select, deselect, selected, pairText, pairNames, state: M, refresh };
})();
