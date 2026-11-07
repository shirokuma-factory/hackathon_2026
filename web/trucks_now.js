/* いまの大型車（web/trucks_now.html）。data/trucks_now.json を読んで、地図・時間の操作・データの流れの帯・県の順位・地点のカードを描く。
   集計は tools/fetch_trucks_now.py が済ませている。ここでは計算し直さず、表示の形に直すだけ（計算の部分は trucks_now_core.js）。
   API には直接つながない。読むのは取得済みの JSON だけで、60 秒ごとに読み直して新しければ差し替える（http で開いたときだけ）。 */
(function () {
  'use strict';
  const C = window.TrucksCore;
  const $ = (s) => document.querySelector(s);
  const NH = 24;
  const COMET_K = 10, COMET_H = 4; // 流れる帯 1 本の頂点数（尾、頭）
  const REDUCE = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const BASE = { bg: '#0a0f19', land: '#0e1621', water: '#05080e', park: '#0d1a1d', roadMinor: '#17212f', roadMajor: '#222f44', motorway: '#33455f', rail: '#131c29',
    b2: '#4b5c7a', b3: '#27344a', label: '#7f8da6', labelStrong: '#a9b6cc', labelWater: '#46617f', halo: '#0a0f19' };
  const STAGE_NOTE = {
    api: (m) => `<b>国土交通省 交通量 API</b>: 直轄国道の観測点 ${f(m.api.points)} 地点（常設 ${f(m.api.kinds['常設'] || 0)}、CCTV ${f(m.api.kinds['CCTV'] || 0)}）を、道路の線に結びつけて使います。`,
    fetch: (m) => `<b>取得</b>: ${f(m.fetch.requests)} 回の要求で、直近24時間と先週の同じ時刻の 1 時間値を集めました。値がある地点だけが色の元になります。`,
    missing: (m) => `<b>欠測 ${f(m.missing.count)} 地点</b>: 値が空の地点は 0 台にせず、輪（◯）で残しています。道路の色と平均には入れていません。`,
    agg: (m) => `<b>県ごとに集計</b>: ${f(m.agg.prefs)} 都道府県それぞれで、観測できた地点の平均を出しています。右の順位と県境が光ります。`,
    map: (m) => `<b>地図</b>: 観測点 ${f(m.draw.drawn)} 点の値を、近くの道路の ${f(S.nCol || 0)} 区間に色と流れる点で映しています。${f(S.nGrey || 0)} 区間は近くに観測点がなく灰色です。`,
  };
  const S = {
    data: null, T: NH - 1, playing: false, mode: 'week', focus: null, selKey: null, hoverKey: null, prefHover: null, prefSel: null,
    geo: null, map: null, overlay: null, zs: 1, dirty: true, pulse: null, frame: 0, lastTs: 0, hold: 0, lastIdx: -1, lastRankSig: '',
    pollSec: 60, nextPollAt: 0, polling: false, checkedAt: null, checkState: '', checks: 0, reloads: 0, lastSecond: 0, changed: new Set(),
    pts: [], rankOrder: null, keyIndex: new Map(), R: null, F: null, L: null, LW: null, rankEls: new Map(), sparkW: 0, ready: false,
  };
  function f(v, d) { return v == null ? '欠測' : Number(v).toLocaleString('ja-JP', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); }
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const keyOf = (p) => p.id + '|' + p.type;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

  function showError(msg) { const e = $('#err'); e.textContent = msg; e.style.display = msg ? 'block' : 'none'; }
  function toast(msg) {
    const t = $('#toast');
    t.textContent = msg; t.classList.add('on');
    clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove('on'), 4200);
  }

  /* ---------------- 地図の下地（アプリと同じ暗い配色） ---------------- */
  function darken(st) {
    for (const l of st.layers) {
      const p = (l.paint = l.paint || {}), id = l.id;
      if (l.type === 'background') p['background-color'] = BASE.bg;
      else if (l.type === 'fill') {
        delete p['fill-pattern'];
        if (id === 'water') p['fill-color'] = BASE.water;
        else if (id === 'building') p['fill-opacity'] = 0;
        else if (id.startsWith('park') || id === 'landcover_wood') p['fill-color'] = BASE.park;
        else p['fill-color'] = BASE.land;
      } else if (l.type === 'line') {
        p['line-opacity'] = ['interpolate', ['linear'], ['zoom'], 3, 0.1, 6, 0.28, 9, 0.6, 12, 1];
        if (id.includes('casing')) { p['line-color'] = BASE.bg; p['line-opacity'] = 0.9; }
        else if (id.includes('motorway')) p['line-color'] = BASE.motorway;
        else if (id.includes('major')) p['line-color'] = BASE.roadMajor;
        else if (id.startsWith('railway')) p['line-color'] = BASE.rail;
        else if (id === 'boundary_2' || id === 'boundary_disputed') p['line-color'] = BASE.b2;
        else if (id.startsWith('boundary')) p['line-color'] = BASE.b3;
        else if (id.startsWith('waterway')) p['line-color'] = BASE.water;
        else p['line-color'] = BASE.roadMinor;
      } else if (l.type === 'symbol') {
        const strong = id.startsWith('label_country') || id === 'label_city_capital';
        p['text-color'] = id.includes('water') ? BASE.labelWater : strong ? BASE.labelStrong : BASE.label;
        p['text-halo-color'] = BASE.halo; p['text-halo-width'] = 1.4; p['icon-opacity'] = 0;
        if (l.layout && l.layout['text-field']) {
          l.layout['text-field'] = ['coalesce', ['get', 'name:ja'], ['get', 'name']];
          if (l.layout['text-transform']) l.layout['text-transform'] = 'none';
        }
      }
    }
    st.layers = st.layers.filter((l) => !(l.id.startsWith('highway-shield') || l.id.startsWith('road_shield')));
    return st;
  }
  async function baseStyle() {
    try {
      const r = await fetch('https://tiles.openfreemap.org/styles/positron');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return darken(await r.json());
    } catch (e) {
      return { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': BASE.bg } }] };
    }
  }

  /* ---------------- 道路の区間: 色・流れる点・光らせる対象（1 フレーム分をまとめて計算） ---------------- */
  
  function focusOn(pref, hasData) {
    const fp = S.prefHover || S.prefSel;
    if (fp && pref !== fp) return false;
    const fc = S.focus;
    if (fc === 'observed' || fc === 'comparable') return hasData;
    if (fc === 'missing') return false; // 欠測の段では道路を薄くして、欠測の地点の輪を見せる
    return true;
  }

  /* 道路のファイルを現在の観測点に結びつける（観測点の並びが変わっても使えるよう、キーで引き直す） */
  function resolveRoads() {
    const R = S.roads;
    if (!R) return;
    R.pieces.forEach((pc) => {
      pc.wi = pc.w.map(([ki, wt]) => [S.keyIndex.has(R.keys[ki]) ? S.keyIndex.get(R.keys[ki]) : -1, wt]).filter((x) => x[0] >= 0);
      let best = null;
      for (const x of pc.wi) if (!best || x[1] > best[1]) best = x;
      pc.near = best ? best[0] : -1;
      pc.pref = best ? S.pts[best[0]].pref : null;
    });
  }

  /* 道路の線を滑らかにして、一定の間隔（DS km）で標本を取り直す。折れ線の角を丸め、流れる帯が道なりに進めるようにする。
     接線を前後の点の向きから決め、接線の長さを区間の長さに合わせる（間隔が不ぞろいな点でも膨らみすぎない）。 */
  const FLOWX = 2.2; // 流れる点の見せ方の倍率（密度は台数に比例し、DENS_MAX で頭打ち）
  const DS = 0.15;
  function smoothPiece(pc, sc) {
    const c = pc.c, n = c.length / 2, P = [];
    let x = 0, y = 0;
    for (let i = 0; i < n; i++) { if (i === 0) { x = c[0]; y = c[1]; } else { x += c[2 * i]; y += c[2 * i + 1]; } P.push([x / sc, y / sc]); }
    const cosL = Math.cos((P[n >> 1][1] * Math.PI) / 180), kx = 111.32 * cosL, ky = 111.32;
    const K = P.map((p) => [p[0] * kx, p[1] * ky]);
    const dir = K.map((_, i) => {
      const a = K[Math.max(0, i - 1)], b = K[Math.min(n - 1, i + 1)], dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1;
      return [dx / l, dy / l];
    });
    const dense = [K[0]];
    for (let i = 0; i < n - 1; i++) {
      const p1 = K[i], p2 = K[i + 1], d = Math.hypot(p2[0] - p1[0], p2[1] - p1[1]), m = Math.max(1, Math.ceil(d / 0.06));
      const m1 = [dir[i][0] * d, dir[i][1] * d], m2 = [dir[i + 1][0] * d, dir[i + 1][1] * d];
      for (let k = 1; k <= m; k++) {
        const t = k / m, t2 = t * t, t3 = t2 * t, h00 = 2 * t3 - 3 * t2 + 1, h10 = t3 - 2 * t2 + t, h01 = -2 * t3 + 3 * t2, h11 = t3 - t2;
        dense.push([h00 * p1[0] + h10 * m1[0] + h01 * p2[0] + h11 * m2[0], h00 * p1[1] + h10 * m1[1] + h01 * p2[1] + h11 * m2[1]]);
      }
    }
    const cum = [0];
    for (let i = 1; i < dense.length; i++) cum.push(cum[i - 1] + Math.hypot(dense[i][0] - dense[i - 1][0], dense[i][1] - dense[i - 1][1]));
    const L = cum[cum.length - 1] || 1e-6, ns = Math.max(1, Math.round(L / DS)), N = ns + 1, step = L / ns;
    const flat = new Float64Array(2 * N), nx = new Float32Array(N), ny = new Float32Array(N), kxy = new Float64Array(2 * N);
    let j = 0;
    for (let i = 0; i < N; i++) {
      const d = i * step;
      while (j < dense.length - 2 && cum[j + 1] < d) j++;
      const seg = cum[j + 1] - cum[j] || 1e-9, u = Math.max(0, Math.min(1, (d - cum[j]) / seg));
      const X = dense[j][0] + (dense[j + 1][0] - dense[j][0]) * u, Y = dense[j][1] + (dense[j + 1][1] - dense[j][1]) * u;
      kxy[2 * i] = X; kxy[2 * i + 1] = Y; flat[2 * i] = X / kx; flat[2 * i + 1] = Y / ky;
    }
    for (let i = 0; i < N; i++) {
      const a = Math.max(0, i - 1), b = Math.min(N - 1, i + 1), tx = kxy[2 * b] - kxy[2 * a], ty = kxy[2 * b + 1] - kxy[2 * a + 1], l = Math.hypot(tx, ty) || 1;
      nx[i] = -ty / l; ny[i] = tx / l; // 進行方向の左
    }
    pc.flat = flat; pc.nx = nx; pc.ny = ny; pc.N = N; pc.step = step; pc.L = L;
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    for (let i = 0; i < N; i++) { x0 = Math.min(x0, flat[2 * i]); x1 = Math.max(x1, flat[2 * i]); y0 = Math.min(y0, flat[2 * i + 1]); y1 = Math.max(y1, flat[2 * i + 1]); }
    pc.bb = [x0, y0, x1, y1];
  }

  function buildRoads(R) {
    S.roads = R;
    const N = R.pieces.length, sc = R.scale || 1e5;
    R.pieces.forEach((pc) => {
      smoothPiece(pc, sc);
      pc.slots = Math.max(1, Math.ceil(C.DENS_MAX * FLOWX * pc.L)); // 片方向の最大数
    });
    S.pLaneDens = new Float32Array(N * 2); // 上り(0)・下りの帯(1)それぞれの密度（個/区間）
    S.pLaneIdx = new Float32Array(N * 2); S.pLaneC = new Uint8ClampedArray(N * 6);
    S.pShare = new Float32Array(N); S.pClk = new Float32Array(N * 2);
    S.pIdx = new Float32Array(N); S.pCnt = new Float32Array(N);
    S.pC = new Uint8ClampedArray(N * 4);
    S.pW = new Float32Array(N);
    S.comCap = 36000;
    S.tailPos = new Float32Array(S.comCap * COMET_K * 2); S.tailCol = new Uint8ClampedArray(S.comCap * COMET_K * 4);
    S.headPos = new Float32Array(S.comCap * COMET_H * 2); S.headCol = new Uint8ClampedArray(S.comCap * COMET_H * 4);
    S.tailSI = Uint32Array.from({ length: S.comCap }, (_, i) => i * COMET_K);
    S.headSI = Uint32Array.from({ length: S.comCap }, (_, i) => i * COMET_H);
    S.nCom = 0;
    resolveRoads();
  }

  function blendF(w, arr) {
    let s = 0, t = 0;
    for (let k = 0; k < w.length; k++) { const v = arr[w[k][0]]; if (!Number.isNaN(v)) { s += w[k][1] * v; t += w[k][1]; } }
    return t > 0 ? s / t : null;
  }

  function compute(dt) {
    const pts = S.pts, n = pts.length, T = S.T, R = S.roads;
    if (!S.vIdx || S.vIdx.length !== n) { S.vIdx = new Float32Array(n); S.vCnt = new Float32Array(n); S.PT = new Uint8ClampedArray(n * 4); S.PR = new Float32Array(n); }
    for (let i = 0; i < n; i++) {
      const v = C.valueAt(pts[i].series, T), m = C.refMax(pts[i].series);
      S.vCnt[i] = v == null ? NaN : v;
      S.vIdx[i] = v == null || m == null ? NaN : v / m;
      const miss = v == null;
      const on = focusOn(pts[i].pref, !miss) || (S.focus === 'missing' && (!(S.prefHover || S.prefSel) || pts[i].pref === (S.prefHover || S.prefSel)));
      const col = C.stateColor(miss ? null : S.vIdx[i]);
      S.PT[i * 4] = col[0]; S.PT[i * 4 + 1] = col[1]; S.PT[i * 4 + 2] = col[2];
      S.PT[i * 4 + 3] = miss ? 0 : on ? 255 : 40;
      S.PR[i] = miss ? 1 : 0; // 1 = 欠測（輪で描く）
    }
    S.nCol = 0; S.nGrey = 0;
    if (R) {
      const N = R.pieces.length, moving = !REDUCE, zoom = S.map ? S.map.getZoom() : 5;
      for (let i = 0; i < N; i++) {
        const pc = R.pieces[i], idx = blendF(pc.wi, S.vIdx), cnt = blendF(pc.wi, S.vCnt);
        const has = idx != null;
        S.pIdx[i] = has ? idx : NaN; S.pCnt[i] = has ? cnt : NaN;
        const col = C.stateColor(idx);
        const on = focusOn(pc.pref, has);
        // 観測点から 15 km を超えて値を伸ばした区間は、細く・薄く・彩度を下げる（確かさが下がるため）
        const conf = has && pc.d != null && pc.d > 15 ? clamp(1 - (pc.d - 15) / 10, 0, 1) : 1;
        pc.conf = conf;
        const mix = 0.4 * (1 - conf);
        if (has) {
          S.pC[i * 4] = col[0] + (130 - col[0]) * mix; S.pC[i * 4 + 1] = col[1] + (140 - col[1]) * mix; S.pC[i * 4 + 2] = col[2] + (155 - col[2]) * mix;
          S.pC[i * 4 + 3] = on ? 235 * (0.7 + 0.3 * conf) : 30;
          S.pW[i] = (pc.k === 2 ? 4 : 3) * (0.7 + 0.3 * conf);
        } else { // データなしの道路は、静かな下地（1 px、35% の灰青）。全国の縮尺では高速道路だけ、拡大するほど国道も出す
          S.pC[i * 4] = 140; S.pC[i * 4 + 1] = 162; S.pC[i * 4 + 2] = 195;
          S.pC[i * 4 + 3] = !on ? 12 : (pc.r || (pc.k === 2 ? 3 : 1)) >= 2 || zoom >= 6.8 ? 90 : 0; // 全国は高速道路・trunk だけ、拡大すると primary も
          S.pW[i] = 1;
        }
        if (has) S.nCol++; else S.nGrey++;
        if (!moving) continue;
        // 流れの帯: 上り・下りの 2 本。台数は 交通量 API の上り・下りの比（最新の時間）で分け、それぞれの台数から密度・色・速さを決める
        const sh = blendF(pc.wi, S.upShare), share = sh == null ? 0.5 : sh;
        S.pShare[i] = share;
        for (let dir = 0; dir < 2; dir++) {
          const q = i * 2 + dir, frac = dir === 0 ? share : 1 - share;
          const laneCnt = has ? cnt * frac : null, laneIdx = has ? clamp(idx * 2 * frac, 0, 1) : null;
          S.pLaneDens[q] = has ? C.particleDensity(5 * laneCnt) * pc.L : 0;
          S.pLaneIdx[q] = has ? laneIdx : NaN;
          const lc = C.stateColor(laneIdx);
          S.pLaneC[q * 3] = lc[0]; S.pLaneC[q * 3 + 1] = lc[1]; S.pLaneC[q * 3 + 2] = lc[2];
          if (has) S.pClk[q] = (S.pClk[q] + (dt * 0.3 * C.particleSpeed(laneIdx)) / pc.L) % 1000;
        }
      }
    }
    S.frame++;
  }

  /* 流れの帯（彗星のように、明るい頭と細く消える尾）の頂点を埋める。画面に見える区間だけ、枠の上限まで。
     速さは区間の両端でゆるやかになる（区間のつなぎ目で急に動かない）。 */
  function fillComets() {
    const R = S.roads;
    if (!R || REDUCE || !S.map) { S.nCom = 0; return; }
    const z = S.map.getZoom(), degPx = 360 / (512 * Math.pow(2, z));
    const bd = S.map.getBounds(), w = bd.getWest(), e = bd.getEast(), so = bd.getSouth(), no = bd.getNorth();
    const dfac = clamp(0.3 + (z - 5) * 0.2, 0.1, 1) * (1 + (FLOWX - 1) * clamp((z - 6) / 3, 0, 1)), // 全国の縮尺では少なく、拡大するほど増やす（白飛びを避ける）
       offPx = clamp(0.8 + (z - 5) * 0.5, 0.8, 2.6);
    const tp = S.tailPos, tc = S.tailCol, hp = S.headPos, hc = S.headCol, K = COMET_K, H = COMET_H, cap = S.comCap;
    let q = 0;
    for (let i = 0; i < R.pieces.length && q < cap; i++) {
      const pc = R.pieces[i], bb = pc.bb;
      if (bb[2] < w || bb[0] > e || bb[3] < so || bb[1] > no) continue;
      const dim = focusOn(pc.pref, true) ? 1 : 0.12, cl = Math.cos((bb[1] * Math.PI) / 180);
      const kmPx = degPx * 111.32 * cl, trail0 = clamp(18 * kmPx, 1.0, 2.0);
      for (let dir = 0; dir < 2; dir++) {
        const dens = S.pLaneDens[i * 2 + dir] * dfac;
        if (!(dens > 0)) continue;
        const m = Math.min(pc.slots, Math.ceil(dens)), clk = S.pClk[i * 2 + dir], L = pc.L, step = pc.step, N = pc.N, flat = pc.flat;
        const r0 = S.pLaneC[(i * 2 + dir) * 3], g0 = S.pLaneC[(i * 2 + dir) * 3 + 1], b0 = S.pLaneC[(i * 2 + dir) * 3 + 2];
        const sgn = dir === 0 ? 1 : -1, side = dir === 0 ? offPx : -offPx;
        for (let j = 0; j < m && q < cap; j++) {
          const amp = Math.min(1, dens - j) * dim * (0.55 + 0.45 * (pc.conf == null ? 1 : pc.conf));
          if (amp <= 0.03) continue;
          const hsh = ((i * 131 + dir * 17 + j * 7) * 0.61803398875) % 1;
          let u = (hsh + clk) % 1; if (u < 0) u += 1;
          const ez = u - (0.35 * Math.sin(2 * Math.PI * u)) / (2 * Math.PI); // 両端でゆるやか
          const head = dir === 0 ? ez * L : (1 - ez) * L;
          const trail = Math.min(trail0 * (0.8 + 0.45 * ((hsh * 7.3) % 1)), 0.85 * L);
          const edge = Math.min(head, L - head) / 0.5, env = edge >= 1 ? 1 : edge <= 0 ? 0 : edge * edge * (3 - 2 * edge);
          if (env * amp <= 0.02) continue;
          for (let v = 0; v < K; v++) {
            const s = clamp(head - (sgn * trail * v) / (K - 1), 0, L), t = s / step;
            let i0 = Math.floor(t); if (i0 > N - 2) i0 = N - 2;
            const fr = t - i0, k0 = i0 * 2;
            const nxv = pc.nx[i0] * (1 - fr) + pc.nx[i0 + 1] * fr, nyv = pc.ny[i0] * (1 - fr) + pc.ny[i0 + 1] * fr;
            const o = (q * K + v) * 2;
            tp[o] = flat[k0] + (flat[k0 + 2] - flat[k0]) * fr + nxv * side * degPx;
            tp[o + 1] = flat[k0 + 1] + (flat[k0 + 3] - flat[k0 + 1]) * fr + nyv * side * degPx * cl;
            const f = 1 - v / (K - 1), a = f * f, wh = 0.75 * f * f * f, oc = (q * K + v) * 4;
            tc[oc] = r0 + (255 - r0) * wh; tc[oc + 1] = g0 + (255 - g0) * wh; tc[oc + 2] = b0 + (255 - b0) * wh;
            tc[oc + 3] = 210 * a * env * amp;
            if (v < H) {
              const o2 = (q * H + v) * 2, c2 = (q * H + v) * 4, fh = 1 - v / (H - 1) * 0.9;
              hp[o2] = tp[o]; hp[o2 + 1] = tp[o + 1];
              hc[c2] = r0 + (255 - r0) * (0.4 + 0.5 * fh); hc[c2 + 1] = g0 + (255 - g0) * (0.4 + 0.5 * fh); hc[c2 + 2] = b0 + (255 - b0) * (0.4 + 0.5 * fh);
              hc[c2 + 3] = 235 * fh * env * amp;
            }
          }
          q++;
        }
      }
    }
    S.nCom = q;
  }

  function pulseState(now) {
    const pl = S.pulse;
    if (!pl) return null;
    let e = (now - pl.start) / pl.dur;
    if (e >= 1) { if (pl.loop) { pl.start = now; e = 0; } else { S.pulse = null; return null; } }
    return { e, pl };
  }

  const showPoints = () => S.showPts || S.focus === 'missing' || !S.roads;

  function buildLayers(now) {
    const fp = S.prefHover || S.prefSel;
    const glow = S.focus === 'agg';
    const layers = [];
    if (S.geo) {
      layers.push(new deck.GeoJsonLayer({
        id: 'prefs', data: S.geo, pickable: false, stroked: true, filled: true, lineWidthUnits: 'pixels',
        getFillColor: (ft) => (ft.properties.n === fp ? [92, 200, 255, 30] : [0, 0, 0, 0]),
        getLineColor: (ft) => (ft.properties.n === fp ? [92, 200, 255, 240] : glow ? [92, 200, 255, 200] : [130, 150, 185, 55]),
        getLineWidth: (ft) => (ft.properties.n === fp ? 2.2 : glow ? 1.6 : 0.6),
        updateTriggers: { getFillColor: fp, getLineColor: [fp, glow], getLineWidth: [fp, glow] },
      }));
    }
    if (S.roads) {
      layers.push(new deck.PathLayer({
        id: 'roads', data: S.roads.pieces, pickable: true, widthUnits: 'pixels', capRounded: true, jointRounded: true,
        getPath: (pc) => pc.flat, positionFormat: 'XY',
        getColor: (pc, { index }) => S.pC.subarray(index * 4, index * 4 + 4),
        getWidth: (pc, { index }) => S.pW[index],
        updateTriggers: { getColor: S.frame, getWidth: S.frame },
      }));
      if (!REDUCE && S.nCom > 0) {
        const n = S.nCom, add = { blend: true, blendColorOperation: 'add', blendColorSrcFactor: 'src-alpha', blendColorDstFactor: 'one', blendAlphaOperation: 'add', blendAlphaSrcFactor: 'one', blendAlphaDstFactor: 'one' };
        layers.push(new deck.PathLayer({
          id: 'flow-tail', pickable: false, widthUnits: 'pixels', getWidth: 3, capRounded: true, jointRounded: true, positionFormat: 'XY', parameters: add,
          data: { length: n, startIndices: S.tailSI.subarray(0, n), attributes: { getPath: { value: S.tailPos.subarray(0, n * COMET_K * 2), size: 2 }, getColor: { value: S.tailCol.subarray(0, n * COMET_K * 4), size: 4 } } },
        }));
        layers.push(new deck.PathLayer({
          id: 'flow-head', pickable: false, widthUnits: 'pixels', getWidth: 4.4, capRounded: true, jointRounded: true, positionFormat: 'XY', parameters: add,
          data: { length: n, startIndices: S.headSI.subarray(0, n), attributes: { getPath: { value: S.headPos.subarray(0, n * COMET_H * 2), size: 2 }, getColor: { value: S.headCol.subarray(0, n * COMET_H * 4), size: 4 } } },
        }));
      }
    }
    if (showPoints()) {
      const only = S.focus === 'missing';
      layers.push(new deck.ScatterplotLayer({
        id: 'pts', data: S.pts, pickable: true, radiusUnits: 'pixels', stroked: true, filled: true, lineWidthUnits: 'pixels',
        getPosition: (p) => [p.lon, p.lat],
        getRadius: (p, { index }) => (S.PR[index] ? 5 : 3),
        getFillColor: (p, { index }) => (only ? [0, 0, 0, 0] : S.PT.subarray(index * 4, index * 4 + 4)),
        getLineColor: (p, { index }) => (S.PR[index] ? [195, 203, 217, 240] : only ? [0, 0, 0, 0] : [8, 12, 20, 170]),
        getLineWidth: (p, { index }) => (S.PR[index] ? 1.6 : 0.7),
        updateTriggers: { getRadius: S.frame, getFillColor: [S.frame, only], getLineColor: [S.frame, only], getLineWidth: S.frame },
      }));
    }
    const ps = pulseState(now);
    if (ps && !REDUCE) {
      const { e, pl } = ps, a = Math.round(150 * (1 - e));
      layers.push(new deck.ScatterplotLayer({
        id: 'pulse', data: pl.pts, pickable: false, radiusUnits: 'pixels', stroked: true, filled: false, lineWidthUnits: 'pixels', getLineWidth: 1.8,
        getPosition: (p) => [p.lon, p.lat],
        getRadius: () => 6 + pl.grow * e,
        getLineColor: pl.color.concat(a),
        updateTriggers: { getRadius: e, getLineColor: a },
      }));
    }
    const mark = [];
    for (const k of [S.selKey, S.hoverKey]) { const i = k && S.keyIndex.get(k); if (i != null) mark.push(S.pts[i]); }
    if (mark.length) {
      layers.push(new deck.ScatterplotLayer({
        id: 'mark', data: mark, pickable: false, radiusUnits: 'pixels', stroked: true, filled: false, lineWidthUnits: 'pixels', getLineWidth: 2,
        getPosition: (p) => [p.lon, p.lat], getRadius: () => 9, getLineColor: [255, 255, 255, 240],
      }));
    }
    return layers;
  }

  function startPulse(pts, color, grow, dur, loop) {
    if (REDUCE) return;
    S.pulse = { pts, color, grow: grow || 22, dur: dur || 1400, loop: !!loop, start: performance.now() };
    S.dirty = true;
  }

  /* ---------------- 地点のカード ---------------- */
  function cardSpark(p, T, max) {
    const W = 220, H = 56, pad = 4;
    const path = C.sparkPath(p.series, W, H, max, pad);
    const x = (t) => pad + (t * (W - 2 * pad)) / (NH - 1), y = (v) => pad + (1 - clamp(v / max, 0, 1)) * (H - 2 * pad);
    let miss = '';
    p.series.forEach((v, i) => { if (v == null) miss += `<line class="ms" x1="${x(i)}" x2="${x(i)}" y1="${H - 3}" y2="${H - 9}"/>`; });
    const cv = C.valueAt(p.series, T);
    const cur = `<line class="cu" x1="${x(T)}" x2="${x(T)}" y1="2" y2="${H - 2}"/>` + (cv == null ? '' : `<circle class="dt" cx="${x(T)}" cy="${y(cv)}" r="3"/>`);
    const wk = p.week_ago == null ? '' : `<path class="wk" d="M${x(NH - 1)} ${y(p.week_ago) - 4}l4 4l-4 4l-4 -4z"><title>先週の同じ時刻</title></path>`;
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path class="ln" d="${path}"/>${miss}${wk}${cur}</svg>`;
  }

  function cardHtml(p, full) {
    const T = S.T, v = C.valueAt(p.series, T), idx = Math.round(T);
    const max = Math.max(10, ...p.series.filter((x) => x != null), p.week_ago || 0);
    const eff = C.effectiveMode(S.mode, T, NH);
    const ref = v == null ? null : C.refFor(p, T, eff);
    const ratio = C.changeRatio(v, ref);
    const mh = C.missingHours(p);
    const lastHour = C.fmtHour(S.data.hours[NH - 1]);
    const big = v == null ? `<div class="big miss">欠測<small>この時間は値がありません（0 台ではありません）</small></div>`
      : `<div class="big">${f(v)}<small>台/時 大型車${Math.abs(T - idx) > 0.001 ? '（間を補った値）' : ''}</small></div>`;
    const refLabel = eff === 'week' ? `先週の同じ時刻（${lastHour}の 7 日前）` : '1 時間前';
    const refTxt = ref == null ? (v == null ? '—' : '値なし') : `${f(ref)} 台/時（${ref === 0 ? '0 台のため比は出しません' : '×' + (v / ref).toFixed(2)}）`;
    let h = `<div class="ch"><b>地点 ${esc(p.id)}</b><span class="tag">${esc(p.type)}</span><span style="color:var(--muted)">${esc(p.pref || '県は不明')}</span>${full ? '<button type="button" class="x" id="pin-x" aria-label="カードを閉じる">閉じる</button>' : ''}</div>`;
    h += big + cardSpark(p, T, max);
    const rm = C.refMax(p.series), si = v == null || rm == null ? null : v / rm;
    h += `<table><tr><td>この地点の 24 時間の最大との比</td><td>${si == null ? '—' : Math.round(si * 100) + '%（24時間の最大 ' + f(rm) + '）'}</td></tr><tr><td>${refLabel}</td><td>${refTxt}</td></tr>`;
    {
      const u = p.up, d = p.down, tot = u != null && d != null ? u + d : null;
      h += `<tr><td>${esc(lastHour)} の上り / 下り</td><td>${f(u)} / ${f(d)}</td></tr>`;
      if (tot) h += `<tr><td>地図の流れの帯</td><td>上りの帯 ${Math.round((u / tot) * 100)}% / 下りの帯 ${Math.round((d / tot) * 100)}%（この比で台数を分けています）</td></tr>`;
      if (tot) h += `<tr><td colspan="2"><div class="split" aria-hidden="true"><i style="width:${(u / tot) * 100}%;background:#5cc8ff"></i><i style="width:${(d / tot) * 100}%;background:#7d8aff"></i></div></td></tr>`;
      h += `<tr><td>${esc(lastHour)} の小型車</td><td>${f(p.small)} 台/時</td></tr>`;
      h += `<tr><td>欠測の時間</td><td>${mh} / ${NH} 時間${mh ? '（赤い短線）' : ''}</td></tr>`;
    }
    h += '</table>';
    if (full) {
      h += `<div class="src"><b style="color:var(--muted);font-weight:500">この数字の元の項目</b>
        <div>折れ線の各時間 = <code>上り・大型交通量</code> + <code>下り・大型交通量</code>（どちらかが空なら欠測）</div>
        <div>小型車 = <code>上り・小型交通量</code> + <code>下り・小型交通量</code></div>
        <div>先週 = 同じ 2 項目の 7 日前の同じ時間帯。地点 = <code>常時観測点コード</code> ${esc(p.id)}（${p.type === 'CCTV' ? 'CCTV トラカン' : '常設'}）</div>
        <div>JSON では <code>series[${idx}]</code> / <code>week_ago</code> / <code>up</code> / <code>down</code> / <code>small</code></div></div>`;
    }
    return h;
  }

  function showPin() {
    const el = $('#pin');
    const p = S.selKey != null ? S.pts[S.keyIndex.get(S.selKey)] : null;
    if (!p) { el.hidden = true; el.innerHTML = ''; return; }
    el.hidden = false; el.innerHTML = cardHtml(p, true);
    const x = $('#pin-x');
    if (x) x.addEventListener('click', () => { S.selKey = null; showPin(); S.dirty = true; });
  }

  /* 道路の区間にカーソルがあるときは、その区間でいちばん近い観測点（重みが最大）を対象にする */
  function pickTarget(info) {
    if (!info || !info.object || !info.layer) return null;
    if (info.layer.id === 'pts') return info.object;
    if (info.layer.id === 'roads') { const pc = info.object; return pc.near != null && pc.near >= 0 ? S.pts[pc.near] : null; }
    return null;
  }

  function placeTip(info, target) {
    const tip = $('#tip');
    if (!target || window.innerWidth <= 800) { tip.style.display = 'none'; return; }
    tip.innerHTML = cardHtml(target, false);
    tip.style.display = 'block';
    const box = $('.mapwrap').getBoundingClientRect();
    const x = Math.min(info.x + 16, box.width - tip.offsetWidth - 8), y = Math.min(info.y + 16, box.height - tip.offsetHeight - 8);
    tip.style.left = Math.max(6, x) + 'px'; tip.style.top = Math.max(6, y) + 'px';
  }

  /* ---------------- 県の順位 ---------------- */
  const ROW_H = 28;
  function buildRank() {
    const box = $('#rank');
    box.innerHTML = ''; S.rankEls.clear();
    box.style.height = S.data.prefs.length * ROW_H + 'px';
    S.data.prefs.forEach((q) => {
      const r = document.createElement('div');
      r.className = 'rk'; r.tabIndex = 0; r.setAttribute('role', 'button'); r.dataset.pref = q.pref;
      r.innerHTML = '<span class="n"></span><span class="nm"></span><span class="bar"><i></i></span><span class="v"></span><span class="c"></span>';
      r.querySelector('.nm').textContent = q.pref;
      r.addEventListener('mouseenter', () => setPrefHover(q.pref));
      r.addEventListener('mouseleave', () => setPrefHover(null));
      r.addEventListener('focus', () => setPrefHover(q.pref));
      r.addEventListener('blur', () => setPrefHover(null));
      r.addEventListener('click', () => selectPref(q.pref));
      r.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectPref(q.pref); } });
      box.appendChild(r); S.rankEls.set(q.pref, r);
    });
    S.lastRankSig = ''; S.rankOrder = null;
  }
  /* 並べ替えは、データが届いたときと「並べ替え」を押したときだけ（再生中・つまみを動かしても行は動かさない） */
  function sortRank(T) {
    if (!S.data) return;
    S.rankOrder = C.rankPrefs(S.data, T == null ? NH - 1 : T).rows.map((r) => r.pref);
    S.rankSortedAt = T == null ? NH - 1 : Math.round(T);
    S.lastRankSig = '';
    if (S.rankEls.size) { S.rankOrder.forEach((n, i) => { const el = S.rankEls.get(n); if (el) { el.style.transform = `translateY(${i * ROW_H}px)`; el.querySelector('.n').textContent = i + 1; } }); }
  }
  function updateRank() {
    const { mode, rows } = C.rankPrefs(S.data, S.T);
    const max = Math.max(1, ...rows.map((r) => r.v || 0));
    if (!S.rankOrder || S.rankOrder.length !== rows.length) sortRank();
    rows.forEach((r) => {
      const el = S.rankEls.get(r.pref);
      if (!el) return;
      el.querySelector('.bar i').style.width = r.v == null ? '0%' : (r.v / max) * 100 + '%';
      el.querySelector('.v').textContent = r.v == null ? '欠測' : f(r.v, 1);
      const c = el.querySelector('.c');
      if (r.ratio == null) { c.textContent = '—'; c.className = 'c'; }
      else { c.textContent = (r.ratio >= 1.005 ? '+' : r.ratio <= 0.995 ? '−' : '±') + Math.abs((r.ratio - 1) * 100).toFixed(0) + '%'; c.className = 'c ' + (r.ratio >= 1.05 ? 'up' : r.ratio <= 0.95 ? 'dn' : ''); }
      el.classList.toggle('sel', r.pref === S.prefSel);
    });
    $('#rank-hint').textContent = `${C.fmtHour(S.data.hours[Math.round(S.T)])}の 1 地点あたり大型車（台/時）。並びは ${C.fmtHour(S.data.hours[S.rankSortedAt])} の値の順で固定です（「並べ替え」で今の時間の順に直せます）。右の数字は${mode === 'week' ? '先週の同じ時刻との差' : '1 時間前との差'}。行を指すと地図の県が光ります。`;
  }
  function setPrefHover(name) { S.prefHover = name; for (const [k, el] of S.rankEls) el.classList.toggle('hl', k === name); S.dirty = true; }
  function selectPref(name) {
    S.prefSel = S.prefSel === name ? null : name;
    for (const [k, el] of S.rankEls) el.classList.toggle('sel', k === S.prefSel);
    if (S.prefSel && S.map) {
      const ps = S.pts.filter((p) => p.pref === S.prefSel);
      if (ps.length) {
        const b = ps.reduce((a, p) => [Math.min(a[0], p.lon), Math.min(a[1], p.lat), Math.max(a[2], p.lon), Math.max(a[3], p.lat)], [999, 99, -999, -99]);
        S.map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: padding(), maxZoom: 8.5, duration: REDUCE ? 0 : 700 });
      }
    }
    S.dirty = true;
  }

  /* ---------------- データの流れの帯 ---------------- */
  function updatePipeline() {
    const m = C.pipelineModel(S.data, S.T);
    $('#s-api').innerHTML = `${f(m.api.points)}<small>地点</small>`;
    $('#s-api2').textContent = `常設 ${f(m.api.kinds['常設'] || 0)} / CCTV ${f(m.api.kinds['CCTV'] || 0)}`;
    $('#s-fetch-lb').textContent = '取得';
    $('#s-fetch').innerHTML = `${f(m.fetch.requests)}<small>回</small>`;
    $('#s-fetch2').textContent = `最終 ${m.fetch.at}`;
    $('#s-missing').innerHTML = `${f(m.missing.count)}<small>地点</small>`;
    $('#s-missing2').textContent = `${C.fmtHour(m.hourCode).replace(/^\d+月\d+日\(.\) /, '')}に値なし`;
    $('#s-agg').innerHTML = `${f(m.agg.prefs)}<small>都道府県</small>`;
    $('#s-agg2').textContent = `平均を計算`;
    if (S.roads) { $('#s-map').innerHTML = `${f(S.nCol || 0)}<small>区間</small>`; $('#s-map2').textContent = `灰色 ${f(S.nGrey || 0)} 区間（観測点なし）`; }
    else { $('#s-map').innerHTML = `${f(m.draw.drawn)}<small>点</small>`; $('#s-map2').textContent = '道路の線を読めません'; }
    const labels = { api: '国土交通省 交通量 API', fetch: `取得 ${m.fetch.requests} 回（最終 ${m.fetch.at}）`, missing: `欠測 ${m.missing.count} 地点を除外`, agg: '県ごとに集計', map: '地図' };
    document.querySelectorAll('.stage').forEach((b) => b.setAttribute('aria-label', labels[b.dataset.stage]));
    S.pipe = m;
    if (S.stage) $('#stage-note').innerHTML = STAGE_NOTE[S.stage](m);
  }
  function pickStage(name) {
    S.stage = S.stage === name ? null : name;
    document.querySelectorAll('.stage').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.stage === S.stage)));
    const fcMap = { api: 'all', fetch: 'observed', missing: 'missing', agg: 'agg', map: 'comparable' };
    S.focus = S.stage ? fcMap[S.stage] : null;
    $('#stage-note').innerHTML = S.stage ? STAGE_NOTE[S.stage](S.pipe) : '各段をクリックすると、その段に関わる地点が地図で光ります。';
    $('#stage-note').classList.toggle('idle', !S.stage);
    if (S.stage === 'api') startPulse(S.pts, [92, 200, 255], 26, 1800, false);
    else if (S.stage === 'missing') startPulse(S.pts.filter((p) => C.valueAt(p.series, S.T) == null), [255, 255, 255], 22, 1600, false);
    else if (S.stage === 'agg') { const r = $('#rank'); r.classList.remove('flash'); void r.offsetWidth; r.classList.add('flash'); S.pulse = null; openSheet(true); }
    else S.pulse = null;
    S.dirty = true;
  }
  function flashStages(done) {
    const els = [...document.querySelectorAll('.stage')];
    if (REDUCE) { if (done) done(); return; }
    els.forEach((el, i) => setTimeout(() => { el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); if (i === els.length - 1 && done) done(); }, i * 260));
  }

  /* ---------------- 時間の操作 ---------------- */
  function sparkGeom() { const svg = $('#spark'); const W = Math.max(200, svg.clientWidth || 600), H = svg.clientHeight || 46; return { W, H, pad: 9 }; }
  function drawSpark() {
    const svg = $('#spark'), { W, H, pad } = sparkGeom();
    const s = S.data.national.series_avg, max = Math.max(1, ...s.filter((v) => v != null)) * 1.08;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const x = (i) => pad + (i * (W - 2 * pad)) / (NH - 1), y = (v) => 3 + (1 - clamp(v / max, 0, 1)) * (H - 8);
    const line = s.map((v, i) => (v == null ? '' : `${i && s[i - 1] != null ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`)).join('');
    const area = s.every((v) => v != null) ? `<path class="area" d="${line}L${x(NH - 1)} ${H - 3}L${x(0)} ${H - 3}Z"/>` : '';
    let gaps = '';
    s.forEach((v, i) => { if (v == null) gaps += `<line class="gap" x1="${x(i)}" x2="${x(i)}" y1="${H - 3}" y2="${H - 10}"/>`; });
    svg.innerHTML = `${area}<path class="line" d="${line}"/>${gaps}<line class="cur" id="sp-cur" y1="0" y2="${H}"/><circle class="dot" id="sp-dot" r="3.5"/>`;
    S.sparkW = W; S.sparkMax = max;
    $('#sp-range').textContent = `${C.fmtHour(S.data.hours[0])} から ${C.fmtHour(S.data.hours[NH - 1])}`;
    const t = $('#ticks'); t.innerHTML = '';
    [0, 6, 12, 18, 23].forEach((i) => { const sp = document.createElement('span'); sp.style.left = (i / (NH - 1)) * 100 + '%'; sp.textContent = (C.parseCode(S.data.hours[i]).h) + '時'; t.appendChild(sp); });
    moveCursor();
  }
  function moveCursor() {
    const { W, H, pad } = sparkGeom(), x = pad + (S.T * (W - 2 * pad)) / (NH - 1);
    const cur = $('#sp-cur'), dot = $('#sp-dot');
    if (!cur) return;
    cur.setAttribute('x1', x); cur.setAttribute('x2', x);
    const v = C.valueAt(S.data.national.series_avg, S.T);
    dot.setAttribute('cx', x); dot.setAttribute('cy', v == null ? H - 3 : 3 + (1 - clamp(v / S.sparkMax, 0, 1)) * (H - 8));
    $('#sp-now').textContent = v == null ? '欠測' : f(v, 1);
  }
  function updateTimeUI() {
    const T = S.T, idx = Math.round(T), frac = Math.abs(T - idx) > 0.01;
    $('#slider').value = T;
    $('#hour').innerHTML = `${C.fmtHour(S.data.hours[idx])}${frac ? `<small>${C.fmtHour(S.data.hours[Math.floor(T)])} と ${C.fmtHour(S.data.hours[Math.ceil(T)])} の間を補って表示</small>` : '<small>1 時間ごとの値</small>'}`;
    moveCursor();
  }
  function setT(t, fromUser) {
    S.T = clamp(t, 0, NH - 1);
    if (fromUser && REDUCE) S.T = Math.round(S.T);
    S.dirty = true;
  }
  function setPlaying(on) {
    S.playing = on; S.hold = 0;
    const b = $('#play'); b.textContent = on ? '一時停止' : '再生'; b.setAttribute('aria-pressed', String(on));
    if (on && S.T >= NH - 1 - 1e-6) S.T = 0;
  }

  /* ---------------- 描画ループ ---------------- */
  function frame(ts) {
    requestAnimationFrame(frame);
    if (!S.data || !S.ready) return;
    const dt = Math.min(0.1, (ts - (S.lastTs || ts)) / 1000); S.lastTs = ts;
    if (S.playing) {
      if (S.hold > 0) { S.hold -= dt; if (S.hold <= 0) S.T = 0; }
      else if (REDUCE) { S.stepAcc = (S.stepAcc || 0) + dt; if (S.stepAcc > 0.9) { S.stepAcc = 0; S.T = Math.min(NH - 1, Math.round(S.T) + 1); } }
      else S.T = Math.min(NH - 1, S.T + dt * 1.6);
      if (S.T >= NH - 1 && S.hold <= 0) S.hold = 1.2;
      S.dirty = true;
    }
    const now = performance.now();
    if (!S.dirty && !S.pulse && !(S.roads && !REDUCE)) return;
    S.dirty = false;
    compute(dt); fillComets();
    S.overlay.setProps({ layers: buildLayers(now) });
    updateTimeUI();
    const idx = Math.round(S.T);
    if (idx !== S.lastIdx) { S.lastIdx = idx; updatePipeline(); if (S.focus === 'missing' && S.pulse) S.pulse.pts = S.pts.filter((p) => C.valueAt(p.series, S.T) == null); }
    updateRank();
    if (S.selKey != null && (S.playing || S.pinT !== S.T)) { S.pinT = S.T; showPin(); }
  }

  /* ---------------- 自動で読み直す ---------------- */
  function pollEnabled() { return /^https?:$/.test(location.protocol); }
  function updateFresh() {
    const d = S.data; if (!d) return;
    const age = C.ageText(d.fetched_at, Date.now()), old = Date.now() - Date.parse(d.fetched_at) > 90 * 60000 || C.delayMin(d.latest_hour_code, new Date().toISOString()) > 4 * 60;
    $('#fresh').innerHTML = `<b>${C.fmtHour(d.latest_hour_code)}</b>の観測（取得は ${C.hhmm(d.fetched_at)}、${C.delayText(d.latest_hour_code, d.fetched_at)}）<br><span class="nw">取得 ${C.mdhm(d.fetched_at)}</span>　<span class="age${old ? ' old' : ''}">${age}${old ? '　古いデータです' : ''}</span>`;
    $('#credit').textContent = d.attribution.processed;
    const st = $('#poll-st');
    if (!pollEnabled()) { st.textContent = 'ファイルで開いているため自動更新しません'; st.className = 'st'; $('#poll-bar').style.width = '0'; return; }
    const left = Math.max(0, Math.ceil((S.nextPollAt - Date.now()) / 1000));
    const at = S.checkedAt ? S.checkedAt.toTimeString().slice(0, 8) : '';
    const res = S.checkState === 'ng' ? '確認できませんでした' : S.checkState === 'new' ? '新しいデータに差し替えました' : S.checkState === 'same' ? '変更なし' : '';
    st.className = 'st ' + (S.checkState === 'ng' ? 'ng' : 'ok');
    st.textContent = `${at ? `${at} に確認（${res}）　` : ''}次の確認まで ${left} 秒`;
    $('#poll-bar').style.width = (100 * (1 - left / S.pollSec)) + '%';
  }
  async function check() {
    S.checkedAt = new Date(); S.checks++;
    S.nextPollAt = Date.now() + S.pollSec * 1000;
    try {
      const r = await fetch('data/trucks_now.json?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      if (C.isNewer(S.data, d)) { S.checkState = 'new'; applyData(d, true); } else S.checkState = 'same';
      showError('');
    } catch (e) {
      S.checkState = 'ng';
      if (!S.data) showError('データを読み込めませんでした。tools/fetch_trucks_now.py を実行して web/data/trucks_now.json を作ってください。（' + e.message + '）');
    }
    updateFresh();
  }
  function secondTick() {
    const nowS = Math.floor(Date.now() / 1000);
    if (nowS === S.lastSecond) return;
    S.lastSecond = nowS;
    if (!S.data) return;
    if (S.polling && !document.hidden && Date.now() >= S.nextPollAt) check();
    updateFresh();
  }

  /* ---------------- データを入れる ---------------- */
  function applyData(d, isUpdate) {
    const prev = S.data;
    const changed = isUpdate ? C.changedIds(prev, d) : new Set();
    const wasLatest = !prev || S.T >= NH - 1.5;
    d.points.forEach((p) => { p._k = keyOf(p); });
    S.data = d; S.pts = d.points;
    S.keyIndex = new Map(S.pts.map((p, i) => [p._k, i]));
    if (!prev || prev.prefs.length !== d.prefs.length) buildRank();
    if (wasLatest) S.T = NH - 1;
    if (S.selKey != null && !S.keyIndex.has(S.selKey)) S.selKey = null;
    $('#attr1').textContent = d.attribution.processed;
    $('#attr2').textContent = d.attribution.service;
    $('#notes').innerHTML = d.notes.map((t) => `<li>${esc(t)}</li>`).join('');
    S.upShare = Float32Array.from(S.pts, (p) => (p.up != null && p.down != null && p.up + p.down > 0 ? p.up / (p.up + p.down) : NaN));
    sortRank();
    resolveRoads();
    S.lastIdx = -1; S.ready = true; S.dirty = true;
    drawSpark(); updateFresh(); showPin();
    if (isUpdate) {
      S.reloads++; S.changed = changed;
      flashStages(() => {
        const ch = S.pts.filter((p) => changed.has(p._k));
        if (ch.length) startPulse(ch, [255, 214, 90], 26, 1600, false);
      });
      toast(`新しいデータに差し替えました（取得 ${C.hhmm(d.fetched_at)}、変わった地点 ${f(changed.size)}）`);
    }
  }

  /* ---------------- 地図 ---------------- */
  function padding() {
    if (window.innerWidth <= 800) return { top: 10, bottom: 10, left: 10, right: 10 };
    const side = window.innerWidth <= 1100 ? 300 : 360;
    return { top: 125, bottom: 175, left: 280, right: side + 30 };
  }
  async function initMap() {
    const style = await baseStyle();
    S.map = new maplibregl.Map({
      container: 'map', style, bounds: [[129.2, 31.0], [146.2, 45.8]], fitBoundsOptions: { padding: padding() }, minZoom: 3.4, maxZoom: 13,
      dragRotate: false, pitchWithRotate: false, touchPitch: false, attributionControl: false, fadeDuration: 0, maxBounds: [[110, 15], [165, 55]],
    });
    S.map.touchZoomRotate.disableRotation();
    S.map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-left');
    S.overlay = new deck.MapboxOverlay({
      interleaved: false, layers: [],
      pickingRadius: 6,
      onHover: (info) => {
        const t = pickTarget(info), k = t ? t._k : null;
        if (k !== S.hoverKey) { S.hoverKey = k; S.dirty = true; const q = t && t.pref; for (const [n, el] of S.rankEls) el.classList.toggle('hl', !!q && n === q); }
        placeTip(info, t);
      },
      onClick: (info) => {
        const t = pickTarget(info);
        if (t) { S.selKey = t._k; S.pinT = null; showPin(); openSheet(true); }
        else { S.selKey = null; showPin(); if (S.stage) pickStage(S.stage); }
        S.dirty = true;
      },
      getCursor: ({ isHovering, isDragging }) => (isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab'),
    });
    S.map.addControl(S.overlay);
    S.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
    const zoomed = () => { S.dirty = true; };
    S.map.on('zoom', zoomed); S.map.on('load', zoomed);
    S.map.on('resize', () => { S.dirty = true; });
  }

  /* ---------------- 画面の部品の結びつけ ---------------- */
  function openSheet(on) {
    if (window.innerWidth > 800) return;
    const s = $('#side'); s.classList.toggle('open', on);
    $('#handle').setAttribute('aria-expanded', String(on));
    $('#handle span').textContent = on ? 'タップで閉じる' : 'タップで開く';
  }
  function wireUi() {
    document.querySelectorAll('.stage').forEach((b) => b.addEventListener('click', () => pickStage(b.dataset.stage)));
    $('#play').addEventListener('click', () => setPlaying(!S.playing));
    $('#rank-sort').addEventListener('click', () => { sortRank(S.T); updateRank(); });
    $('#latest').addEventListener('click', () => { setPlaying(false); setT(NH - 1); });
    const sl = $('#slider');
    sl.addEventListener('input', () => { setPlaying(false); setT(parseFloat(sl.value), true); });
    sl.addEventListener('keydown', (e) => {
      const st = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1 }[e.key];
      if (st) { e.preventDefault(); setPlaying(false); setT(Math.round(S.T) + st); }
    });
    const sp = $('#spark');
    const seek = (ev) => { const r = sp.getBoundingClientRect(), { pad } = sparkGeom(); setPlaying(false); setT(((ev.clientX - r.left - pad) / (r.width - 2 * pad)) * (NH - 1), true); };
    sp.addEventListener('pointerdown', (ev) => { sp.setPointerCapture(ev.pointerId); sp.dataset.drag = '1'; seek(ev); });
    sp.addEventListener('pointermove', (ev) => { if (sp.dataset.drag) seek(ev); });
    sp.addEventListener('pointerup', () => { delete sp.dataset.drag; });
    const lo = [0, ...C.STATE_BINS.map((b) => b.max)];
    $('#lg-bins').innerHTML = C.STATE_BINS.map((b, i) => `<span style="border-color:rgb(${b.rgb})">${b.name}<small>${i === 0 ? '〜' + Math.round(b.max * 100) + '%' : i === C.STATE_BINS.length - 1 ? Math.round(lo[i] * 100) + '%〜' : Math.round(lo[i] * 100) + '〜' + Math.round(b.max * 100) + '%'}</small></span>`).join('');
    $('#show-pts').addEventListener('change', (e) => { S.showPts = e.target.checked; S.dirty = true; });
    $('#lg-toggle').addEventListener('click', () => { const l = $('#legend'); const o = l.classList.toggle('open'); $('#lg-toggle').setAttribute('aria-expanded', String(o)); $('#lg-toggle').textContent = o ? '閉じる' : '詳しく'; });
    $('#handle').addEventListener('click', () => openSheet(!$('#side').classList.contains('open')));
    $('#poll-now').addEventListener('click', () => { if (pollEnabled()) check(); else toast('ファイルで開いているため、自動更新はできません'); });
    window.addEventListener('keydown', (e) => {
      if (e.target && /INPUT|TEXTAREA/.test(e.target.tagName) && e.target.type !== 'range') return;
      if (e.key === ' ' && e.target === document.body) { e.preventDefault(); setPlaying(!S.playing); }
      if (e.key === 'Escape') { S.selKey = null; S.prefSel = null; if (S.stage) pickStage(S.stage); showPin(); for (const el of S.rankEls.values()) el.classList.remove('sel'); S.dirty = true; }
    });
    if (window.ResizeObserver) new ResizeObserver(() => { if (S.data) { drawSpark(); } }).observe($('#spark'));
    window.addEventListener('resize', () => { if (S.map) S.map.resize(); });
  }

  async function main() {
    wireUi();
    initMap().catch((e) => showError('地図を表示できませんでした。（' + e.message + '）'));
    try {
      const [d, g, rd] = await Promise.all([
        fetch('data/trucks_now.json?t=' + Date.now(), { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }),
        fetch('data/pref.geojson').then((r) => r.json()).catch(() => null),
        fetch('data/trucks_roads.json').then((r) => (r.ok ? r.json() : null)).catch(() => null),
      ]);
      S.geo = g;
      for (let i = 0; i < 100 && !S.overlay; i++) await new Promise((r) => setTimeout(r, 50));
      applyData(d, false);
      if (rd) { buildRoads(rd); S.dirty = true; }
    } catch (e) {
      showError('データを読み込めませんでした。tools/fetch_trucks_now.py を実行して web/data/trucks_now.json を作ってください。（' + e.message + '）');
      return;
    }
    S.polling = pollEnabled();
    S.nextPollAt = Date.now() + S.pollSec * 1000;
    setInterval(secondTick, 500);
    requestAnimationFrame(frame);
    if (new URLSearchParams(location.search).get('play') === '1') setPlaying(true);
  }

  window.__trucks = S; // 確認と試験用
  S.api = { check, applyData, setT, setPlaying, pickStage, selectPref, cardHtml, compute, updatePipeline };
  main();
})();
