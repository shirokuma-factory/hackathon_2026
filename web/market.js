'use strict';
/* 全国の物流傾向（旧名「市場を見る」。SPEC §16.1、§16.4 の表示）。
   不足量・偏りの比・県の出と入の差・S と実車率の曲線・JARTIC の集計は、tools/build_web_data.py が Python で計算して
   web/data/market.json と jartic.json に書いたもの。ここでは、距離の下限と件数での絞り込み、色や位置への変換、
   曲線の読み取りだけを行い、統計は計算し直さない。 */

const Market = (() => {
  const M = {
    d: null, j: null, mode: 'truck', n: 30, minKm: 200, x: 5, sel: null, hover: null, rows: [], flows: null,
    endpointSet: new Set(), jartic: true, jpId: null, active: true, markers: [], bal: [], maxBal: 1, readIdx: -1,
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
    TF.lines = await fetch('data/flow_lines.json').then((r) => (r.ok ? r.json() : null)).catch(() => null);   // 交通の流れの線。読めなければ流れだけ出さない
    M.prefIdx = new Map(d.prefs.map((n, i) => [n, i]));
    for (const p of j.points) { p.fi = M.prefIdx.get(p.from); p.ti = M.prefIdx.get(p.to); }
  }

  function init() {
    const d = M.d;
    q('#sum-load').textContent = d.summary.load_factor_pct.toFixed(2);
    q('#sum-load-ref').textContent = d.summary.load_factor_pct.toFixed(2); // 営業用普通車（国の目標と比べる 38.97% とは別の範囲）
    q('#sum-rate').textContent = d.summary.actual_rate_pct.toFixed(2);
    q('#sum-share').textContent = d.summary.shortfall_share_pct;
    q('#calc-formula').textContent = `実車率(後) = 実車率(今) × (1 + X × S)。S = Σ(不足量 × 直線距離) ÷ Σ(両向きの量 × 直線距離) = ${d.scale_s.toFixed(4)}（営業用トラックの全区間、物流センサスから計算）。`;
    q('#m-credits-body').innerHTML = [
      `${esc(d.source)}。${esc(d.unit)}`,
      '営業用普通車の積載効率・実車率: 国土交通省「自動車輸送統計年報」2025 年度（e-Stat 統計表 ID 000040511074 ほか）。出典は e-Stat、政府標準利用規約（第 2.0 版）に従って加工しています',
      '営業用トラックの積載効率 38.97%: 自動車輸送統計年報 2025 年度 表 3-1（輸送トンキロ ÷ 能力トンキロ）。国の目標 44%（2028 年度）: 「基本的な方針」6 頁（令和 7 年 2 月 18 日告示）。目標の土台の範囲は国の資料で未確認です',
      '2030 年度に輸送能力が 34.1% 足りなくなるおそれ: 国土交通省「持続可能な物流の実現に向けた検討会 最終取りまとめ」2023 年 8 月、4 頁（対策をしない場合の見通し）',
      `${esc(M.j.attribution)}。台数は参考値です。積荷の有無は分かりません。このサービスは、交通量 API 機能を使用していますが、サービスの内容は国土交通省によって保証されたものではありません。`,
      '動く線の形: 国土数値情報（鉄道データ）（国土交通省）を加工して作成。道路は OSRM、フェリー航路・貨物専用線・空港は OpenStreetMap（© OpenStreetMap contributors, ODbL 1.0）',
      '拠点（貨物駅・港・フェリー航路）: © OpenStreetMap contributors（ODbL 1.0）／地図: OpenFreeMap © OpenMapTiles Data from OpenStreetMap／行政区域: 国土数値情報（国土交通省）を加工',
    ].map((t) => `<div>${t}</div>`).join('');
    const tt = q('#traf-toggle');
    tt.addEventListener('click', () => { TF.on = !TF.on; tt.setAttribute('aria-checked', String(TF.on)); });
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
    map.on('moveend', () => { if (M.active && isGuide()) spreadNums(); });   // 画面の拡大・傾きが変わったら、番号の札の重なりを解き直す
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
    q('#m-count').textContent = `${MODE_LABEL[M.mode]}: 帰りの荷が足りない順に ${M.rows.length} 件を出しています（全 ${all.length} 件）。`;
    const t = M.d.totals[M.mode];
    q('#m-rsub').textContent = `${MODE_LABEL[M.mode]}・上位 ${M.rows.length}`;
    q('#m-rtot').innerHTML = `帰りの荷が足りない分: ${n0(t.shortfall_t)} t（県をまたぐ荷の ${Math.round(t.share * 100)}%）` +
      (M.mode === 'rail' ? ' 鉄道コンテナは標本が小さく、参考です。' : '');
    renderTable();
    buildFlows();
    renderLegend();
    renderPair();
    syncMarkers();
    syncHash();
  }

  function renderTable() {
    q('#m-tbody').innerHTML = M.rows.map((r, i) => {
      const P = M.d.prefs;
      return `<tr data-k="${r.k}" tabindex="0" class="${r.k === M.sel ? 'on' : ''}"><td class="c-rk num">${i + 1}</td>` +
        `<td class="c-pair">${esc(P[r.fi])}<span class="ar">&rarr;</span>${esc(P[r.ti])}</td>` +
        `<td class="num">${n0(r.go)}</td><td class="num">${n0(r.back)}</td><td class="num">${r.ratio.toFixed(3)}</td>` +
        `<td class="num"><b>${n0(r.short)}</b></td><td class="num">${n0(r.km)}</td></tr>`;
    }).join('') || '<tr><td colspan="7" class="empty">出せる向きがありません。距離の下限を小さくしてください。</td></tr>';
  }

  function renderCalc() {
    const d = M.d, i = Math.round(M.x / 0.5), cur = d.summary.actual_rate_pct;
    const v = d.curve[Math.min(i, d.curve.length - 1)][1];
    q('#m-x-out').textContent = `${M.x.toFixed(1)}%`;
    q('#calc-from').textContent = cur.toFixed(2);
    q('#calc-to').textContent = v.toFixed(2);
    q('#calc-d').textContent = `${(v - cur).toFixed(2)} ポイント上がります`;
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
    q('#mc-jt').hidden = !M.jartic;   // 交通量 API の注記は、その値を出している間いつも見える所に出す
    if (M.j && M.j.period) { const t = q('#mlg-jartic-t'); const w = `${weekText()}の大型車の点`; if (t && t.textContent !== w) t.textContent = w; }
    q('#mlg-flow').hidden = false;
    q('#mlg-traf').hidden = M.mode !== 'truck' || !TF.lines;
  }

  /* ------------------------------------------------------------ 流れ（動く矢印） */

  const clampI = (v, a, b) => Math.max(a, Math.min(b, v));

  function buildFlows() {
    const cap = M.d.cap, P = M.d.prefs, maxShort = Math.max(1, ...M.rows.map((r) => r.short));
    const items = [], base = [], routes = [], actBase = [], roadBase = [], unkBase = [], ports = [];
    M.rows.forEach((r, ri) => {
      const a = cap[r.fi], b = cap[r.ti];
      // 実際の線（営業用トラックは道路、フェリーは港までの道と航路、鉄道コンテナは駅までの道と線路）。作っていない区間は弧
      // 乗り物と粒子は、自分の手段の区間（トラックは道、フェリーは航路、鉄道は線路）だけを走る。港や駅までの道は道として細く描く。
      // 弧は実際の道ではないので、手段に合わない場所（海をまたぐ陸の乗り物、陸の上の船）を通るなら、乗り物を出さずに細い点線だけにする
      const legsRaw = Rt.pairLegs(P[r.fi], P[r.ti], M.mode, 0.02);
      const real = legsRaw && legsRaw.reduce((n, l) => n + l.pts.length, 0) > 2 ? legsRaw : null;
      const arc = real ? null : arcPath(a, b, 0.14, 48);
      const legs = (real || [{ mode: M.mode, pts: arc }]).map((l) => {
        const lc = [0];
        for (let k = 1; k < l.pts.length; k++) lc.push(lc[k - 1] + hav(l.pts[k - 1], l.pts[k]));
        return { mode: l.mode, path: l.pts, pts: l.pts, cum: lc, total: lc[lc.length - 1] || 1, on: l.mode === M.mode };
      });
      const unknown = !real && !Rt.pathOk(M.mode, arc);
      if (unknown) legs.forEach((l) => { l.on = false; });
      let actTotal = 0;
      for (const l of legs) if (l.on) { l.o = actTotal; actTotal += l.total; }
      const pts = real ? legs.flatMap((l, li) => (li ? l.pts.slice(1) : l.pts)) : arc;
      const cum = [0];
      for (let k = 1; k < pts.length; k++) cum.push(cum[k - 1] + hav(pts[k - 1], pts[k]));
      const total = cum[cum.length - 1] || 1;
      const rt = { path: pts, cum, total };
      const w = 1.6 + 6 * Math.sqrt(r.short / maxShort);
      const bright = 1 - Math.min(1, r.ratio); // 帰りの荷が少ないほど明るい
      const col = mix(Kit.palette.flows.base, Kit.palette.flows.bright, Math.pow(bright, 0.6));
      const reps = clampI(Math.round(PERIOD / (6 + r.km / 100)), 1, 4), dur = PERIOD / reps;
      const ph = ((ri * 0.6180339) % 1) * dur;
      const route = { r, rt, w, col, dur, starts: [], real: !!real, unknown, legs, actTotal };
      if (actTotal > 0) {
        for (let k = 0; k < 2; k++) {
          for (let rep = 0; rep < reps; rep++) {
            const s = (ph + (k * dur) / 2 + rep * dur) % PERIOD;
            route.starts.push(s);
            for (const sh of [0, -PERIOD]) for (const l of legs) if (l.on) items.push({ pts: l.pts, ts: l.cum.map((c) => ((l.o + c) / actTotal) * dur + s + sh), route });
          }
        }
      }
      for (const l of legs) (l.on ? actBase : unknown ? unkBase : roadBase).push({ pts: l.pts, route, mode: l.mode });
      legs.forEach((l, li) => { if (li && l.mode !== legs[li - 1].mode) ports.push({ p: l.pts[0], mode: M.mode, route }); });
      base.push({ pts, route });
      routes.push(route);
    });
    M.flows = { items, base, routes, actBase, roadBase, unkBase, ports };
    M.endpointSet = new Set(M.rows.flatMap((r) => [r.fi, r.ti]));
    M.endpoints = [...M.endpointSet].map((i) => ({ p: M.d.cap[i], name: M.d.prefs[i] }));
  }

  // 案内の表示（全国の物流傾向 g=1）。上位 5 区間以外をうすくし、選んだ区間を強める
  const isGuide = () => document.body.classList.contains('guided') && document.body.classList.contains('view-market');
  const dimOf = (route) => {
    const key = route.r.k;
    if (M.hover && M.hover !== key) return M.sel === key ? 1 : 0.35;
    if (M.sel && M.sel !== key) return isGuide() ? 0.14 : 0.28;
    return 1;
  };

  // 県の面を、本土側（立ち上げる）と小さな島（平らなまま）に分ける。bbox の面積が 0.03 平方度以上の面を本土側とし、県に 1 つも無ければ最大の面を本土側にする
  let splitCache = null;
  function prefSplit() {
    if (splitCache && splitCache.src === S.pref) return splitCache;
    const main = [], minor = [], area = (ring) => { let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9; for (const p of ring) { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); } return (x1 - x0) * (y1 - y0); };
    for (const f of S.pref.features) {
      const g = f.geometry, polys = g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates];
      let big = polys.filter((pg) => area(pg[0]) >= 0.03);
      if (!big.length) big = [polys.slice().sort((a, b) => area(b[0]) - area(a[0]))[0]];
      const small = polys.filter((pg) => !big.includes(pg));
      main.push({ ...f, geometry: { type: 'MultiPolygon', coordinates: big } });
      if (small.length) minor.push({ ...f, geometry: { type: 'MultiPolygon', coordinates: small } });
    }
    splitCache = { src: S.pref, main: { type: 'FeatureCollection', features: main }, minor: { type: 'FeatureCollection', features: minor } };
    return splitCache;
  }

  // 選んだ区間の両端の県の外周（立ち上げた高さの位置）。県ごとに作り直すのは選び直したときだけ
  let outlineCache = { k: '', data: [] };
  function selOutline(sp, r) {
    if (outlineCache.k === r.k && outlineCache.s === sp) return outlineCache.data;
    const want = new Set([M.d.prefs[r.fi], M.d.prefs[r.ti]]), data = [];
    for (const f of sp.main.features) {
      if (!want.has(f.properties.n)) continue;
      for (const pg of f.geometry.coordinates) data.push(pg[0].map((p) => [p[0], p[1], 410]));
    }
    outlineCache = { k: r.k, s: sp, data };
    return data;
  }

  /* 乗り物（先頭の 1 台）の動き。経路の弧長（km）に沿って一定の速さで進み、両端だけ加減速する（台形の速さ）。
     位置は毎フレーム、頂点の間を補間して求める。向きは前後の点から求めた接線を、最短の向きで少しずつ回して追う（曲がり角で急に向きが変わらない）。
     時間だけで決まるので、データを更新しても位置は飛ばない。動きを減らす設定では、経路の途中に止めて置く */
  const HEAD_RAMP = 0.06;   // 加速・減速にかける時間の割合（両端）
  const easeTrap = (u) => {
    const a = HEAD_RAMP, vm = 1 / (1 - a);
    if (u <= 0) return 0;
    if (u >= 1) return 1;
    if (u < a) return (vm * u * u) / (2 * a);
    if (u > 1 - a) { const v = 1 - u; return 1 - (vm * v * v) / (2 * a); }
    return vm * (a / 2 + u - a);
  };
  const headState = new Map();   // 乗り物ごとの向きの記憶（経路の鍵 + 出発の位相）
  const turn = (from, to, k) => from + ((((to - from + 540) % 360) + 360) % 360 - 180) * k;   // 度。最短の向きで回す
  const bearingAt = (rt, f) => {
    const d = 0.012, a = pointAt(rt, Math.max(0, f - d)), c = pointAt(rt, Math.min(1, f + d));
    const dx = (c[0] - a[0]) * Math.cos((a[1] * Math.PI) / 180), dy = c[1] - a[1];
    return -(Math.atan2(dx, dy) * 180) / Math.PI;
  };
  // 乗り物を走らせる区間（その手段の区間だけ）の先頭からの割合 f の位置と向き。区間をまたぐときは、次の区間の始めに移る
  function actState(route, f) {
    const d = Math.max(0, Math.min(1, f)) * route.actTotal;
    let leg = null;
    for (const l of route.legs) { if (!l.on) continue; leg = l; if (d <= l.o + l.total) break; }
    const lf = Math.max(0, Math.min(1, (d - leg.o) / leg.total));
    return { p: pointAt(leg, lf), ang: bearingAt(leg, lf) };
  }
  function headData(sec) {
    const cur = sec % PERIOD, out = [], reduced = MapFx.reduced();
    if (headState.size > 600) headState.clear();
    for (const route of M.flows.routes) {
      if (!route.actTotal) continue;   // 乗り物を走らせる区間が無い（実際の線が無く、手段に合わない弧）
      const dim = dimOf(route);
      route.starts.forEach((s, si) => {
        const u = reduced ? 0.3 + 0.4 * (s / PERIOD) : ((((cur - s) % PERIOD) + PERIOD) % PERIOD) / route.dur;
        if (u >= 1) return;
        const f = reduced ? u : easeTrap(u), { p: pos, ang: want } = actState(route, f);
        const key = route.r.k + '|' + si;
        let st = headState.get(key);
        const dt = st ? sec - st.sec : 0;
        if (!st || dt < 0 || dt > 0.5 || u < st.u || reduced) st = { ang: want, sec, u };
        else if (dt > 0) { st.ang = turn(st.ang, want, 1 - Math.exp(-dt * 7)); st.sec = sec; st.u = u; }
        headState.set(key, st);
        out.push({ p: pos, ang: st.ang, u, route, dim, fade: reduced ? 1 : Math.min(1, u * 8, (1 - u) * 8) });
      });
    }
    return out;
  }

  // 空きを探す・つなぐの地図の下に、帰りの空きやすさの流れだけをうすく重ねる（k = 全体の濃さ）
  function flowLayers(now, k) {
    const L = deck, F = M.flows, out = [];
    if (!F || !F.routes.length) return out;
    const cur = (now / 1000) % PERIOD;
    out.push(new L.PathLayer({
      id: 'f-flow-base', data: F.actBase, getPath: (d) => d.pts, widthUnits: 'pixels', getWidth: (d) => Math.max(1, d.route.w * 0.4),
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

  /* ------------------------------------------------------------ 交通の流れ（粒子） */

  /* 流体の流れのように、トラックの量を見せる層。粒子が道路の線に沿って尾を引いて進む。
     線は tools/build_flow_lines.py が作った flow_lines.json（県庁所在地どうしの道路の概算）。量は market.json の営業用トラックの区間の行き・帰り（t）。
     粒子の数と明るさは量の 0.7 乗に比例（合計は TF_N 以下）。速さは道路の弧長に対して一定で、東京・名古屋・大阪の近くだけなめらかに遅くなり、そこで粒子が密になる。
     時間は rAF の差分で進め（隠れたタブや別の表示のあいだは止まる）、動きを減らす設定では細い静止した線だけにする。実際の便の位置ではない */
  const TF = { on: true, lines: null, built: null, key: '', last: 0, clock: 0 };
  const TF_V0 = 90, TF_N = 2800, TF_TRAIL = 0.55, TF_HUBS = [12, 22, 26], TF_SIGMA = 55;   // km/秒、粒子の数、尾の長さ（秒）、東京都・愛知県・大阪府、遅くなる範囲（km）
  const TF_ADD = { depthCompare: 'always', depthWriteEnabled: false, blend: true, blendColorOperation: 'add', blendColorSrcFactor: 'src-alpha', blendColorDstFactor: 'one', blendAlphaOperation: 'add', blendAlphaSrcFactor: 'one', blendAlphaDstFactor: 'one' };
  function buildTraffic() {
    if (!TF.lines || !M.d) return null;
    if (TF.built && TF.key === M.d.source) return TF.built;
    const sc = TF.lines.scale, byPair = new Map(M.d.modes.truck.pairs.map((p) => [`${p[0]}-${p[1]}`, p]));
    const hubs = TF_HUBS.map((i) => M.d.cap[i]);
    const slow = (p) => { let m = 0; for (const h of hubs) { const d = hav(p, h) / TF_SIGMA; m = Math.max(m, Math.exp(-d * d / 2)); } return 1 - 0.65 * m; };
    const near = (p) => hubs.some((h) => hav(p, h) < 170);
    const times = (pts) => { const ts = [0]; for (let k = 1; k < pts.length; k++) ts.push(ts[k - 1] + hav(pts[k - 1], pts[k]) / (TF_V0 * slow([(pts[k - 1][0] + pts[k][0]) / 2, (pts[k - 1][1] + pts[k][1]) / 2]))); return ts; };
    const recs = [], stat = [];
    let wsum = 0, wmax = 0, T = 1;
    for (const ln of TF.lines.lines) {
      const p = byPair.get(`${ln[0]}-${ln[1]}`);
      if (!p) continue;
      const flat = ln[2], raw = [];
      let x = 0, y = 0;
      for (let i = 0; i < flat.length; i += 2) { x += flat[i]; y += flat[i + 1]; raw.push([x / sc, y / sc]); }
      const pts = [raw[0]];   // 東京・名古屋・大阪の近くだけ細かく割って、遅くなる区間をなめらかにする
      for (let k = 1; k < raw.length; k++) {
        const a = raw[k - 1], b = raw[k], d = hav(a, b);
        if (d > 15 && near([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2])) { const n = Math.ceil(d / 12); for (let j = 1; j < n; j++) pts.push([a[0] + ((b[0] - a[0]) * j) / n, a[1] + ((b[1] - a[1]) * j) / n]); }
        pts.push(b);
      }
      const dirs = [[pts, p[2]], [pts.slice().reverse(), p[3]]];
      for (const [pp, vol] of dirs) {
        if (!(vol > 0)) continue;
        const ts = times(pp), w = Math.pow(vol, 0.7);
        recs.push({ pts: pp, ts, dur: ts[ts.length - 1], w });
        wsum += w; wmax = Math.max(wmax, w); T = Math.max(T, ts[ts.length - 1]);
      }
      stat.push({ pts, w: Math.pow(p[2] + p[3], 0.7) });
    }
    T = Math.ceil(T) + 1;
    const items = [];
    recs.forEach((r, ri) => {
      const x = (TF_N * r.w) / wsum, fr = (ri * 0.7548776662) % 1, n = Math.floor(x) + (x - Math.floor(x) > fr ? 1 : 0);
      const a = Math.round(55 + 150 * Math.sqrt(r.w / wmax)), wd = 1.0 + 1.5 * Math.sqrt(r.w / wmax);
      for (let j = 0; j < n; j++) {
        const s = ((j + ((ri * 0.6180339887 + 0.37) % 1)) / n) * T, ts = r.ts.map((t) => t + s);
        items.push({ pts: r.pts, ts, a, wd });
        if (s + r.dur > T) items.push({ pts: r.pts, ts: ts.map((t) => t - T), a, wd });
      }
    });
    TF.key = M.d.source;
    TF.built = { items, T, stat: stat.map((s) => ({ pts: s.pts, a: Math.round(40 + 110 * Math.sqrt(s.w / wmax)), wd: 0.8 + 1.2 * Math.sqrt(s.w / wmax) })) };
    return TF.built;
  }
  function trafficLayers(now, G, dim) {
    if (!(G || TF.on) || M.mode !== 'truck' || Muni.isMuni()) return [];
    const b = buildTraffic();
    if (!b || !b.items.length) return [];
    const dt = TF.last ? Math.min(0.1, Math.max(0, (now - TF.last) / 1000)) : 0;
    TF.last = now;
    TF.clock += dt;
    const k = (G ? 0.5 : 1) * dim, COL = [200, 236, 255];
    if (MapFx.reduced()) {
      return [new deck.PathLayer({ id: 'm-traf-static', parameters: ALWAYS, data: b.stat, getPath: (d) => d.pts, widthUnits: 'pixels', getWidth: (d) => d.wd, getColor: (d) => [...COL, d.a], capRounded: true, opacity: 0.6 * k })];
    }
    const cur = TF.clock % b.T;
    const props = { data: b.items, getPath: (d) => d.pts, getTimestamps: (d) => d.ts, widthUnits: 'pixels', capRounded: true, jointRounded: true, currentTime: cur, fadeTrail: true, trailLength: TF_TRAIL, parameters: TF_ADD, getColor: (d) => [...COL, d.a] };
    return [
      new deck.TripsLayer({ ...props, id: 'm-traf-glow', getWidth: (d) => d.wd * 3.2, opacity: 0.1 * k }),
      new deck.TripsLayer({ ...props, id: 'm-traf-core', getWidth: (d) => d.wd, opacity: 0.6 * k }),
    ];
  }

  /* ------------------------------------------------------------ 層 */

  /* 案内の表示: 帰りが空きやすい向き（荷が多い向きの逆）を、ゆっくり流れる点線で示す。動きを減らす設定では止めた点線。
     統計の区間の向きを示す記号で、実際の便ではない。点線は 1 本ずつの短い線分を、時間とともに進めて作る（画面の 22 画素ごとに 1 本、毎秒 9 画素） */
  const RET_PX = 22, RET_SPEED = 9, RET_DUTY = 0.5;
  let retCache = { key: '', data: [] };
  function subPath(rt, f0, f1) {   // 経路の先頭からの割合 f0〜f1 の部分（曲がり角を含む）
    const out = [pointAt(rt, f0)], c0 = f0 * rt.total, c1 = f1 * rt.total;
    for (let i = 0; i < rt.cum.length; i++) if (rt.cum[i] > c0 && rt.cum[i] < c1) out.push(rt.path[i]);
    out.push(pointAt(rt, f1));
    return out;
  }
  function retDashData(sec, zoom) {
    const reduced = MapFx.reduced(), key = `${reduced ? 0 : Math.round(sec * 14)}|${Math.round(zoom * 2)}|${M.mode}|${M.flows.routes.length}`;
    if (retCache.key === key && retCache.f === M.flows) return retCache.data;
    const mpp = (156543.03 * Math.cos((36 * Math.PI) / 180)) / Math.pow(2, Math.round(zoom * 2) / 2) / 1000;   // 1 画素あたりの km
    const period = RET_PX * mpp, data = [];
    for (const route of M.flows.routes) {
      const tot = route.rt.total || 1, n = Math.max(2, Math.round(tot / period)), per = tot / n, adv = reduced ? 0 : sec * RET_SPEED * mpp;
      for (let i = 0; i < n; i++) {
        const c = (((i * per - adv) % tot) + tot) % tot;   // 帰りの向き（先頭へ向かう）に進めるので、割合を減らす
        const a = c / tot, b = Math.min(1, (c + per * RET_DUTY) / tot);
        if (b - a > 1e-6) data.push({ pts: subPath(route.rt, a, b), route });
      }
    }
    retCache = { key, data, f: M.flows };
    return data;
  }
  const dashExtM = () => (typeof deck.PathStyleExtension === 'function' ? [new deck.PathStyleExtension({ dash: true })] : []);
  const ALWAYS = { depthCompare: 'always' };   // 案内の表示で県を立ち上げても、経路の線と端の印が県の壁に隠れないようにする（平らな表示でも同じ）
  function returnTrail(sec, selRow, zoom) {
    return [new deck.PathLayer({
      id: 'm-ret-dash', parameters: ALWAYS, data: retDashData(sec, zoom), getPath: (d) => d.pts, widthUnits: 'pixels', capRounded: true,
      getWidth: (d) => (selRow && d.route.r.k === selRow.k ? 4.6 : 3.4), getColor: (d) => [...Kit.palette.legs.colors.empty.slice(0, 3), 245 * dimOf(d.route)],   // 空車で戻る向きは橙の点線（実車は青の実線。kit.js の色）
      updateTriggers: { getColor: [M.sel, M.hover, M.mode], getWidth: [M.sel] },
    })];
  }
  // 選んだ区間の中ほどに置く小さな 3D の乗り物（帰りの向き）。置き物で、実際の便の位置ではない
  function selVehicle(r, zoom) {
    const fr = M.flows.routes.find((x) => x.r.k === r.k);
    if (!fr || !fr.actTotal) return null;   // 手段に合う区間が無いときは置かない
    const { p, ang: a0 } = actState(fr, 0.5), ang = a0 + 180;   // 手段に合う区間（船なら航路）の中ほど
    // 案内の表示: 選んだ区間に 1 台だけ大きく置く（帰りの向き、空車）。影をつけて地面から浮いて見えないようにする
    const big = isGuide() ? (innerWidth < 600 ? 2.6 : 3.2) : 1.25, one = [{ p, ang, a: 1 }];
    const l = M3D.layer('m-sel-veh', M3D.MODE_MODEL[M.mode], one, { zoom, opacity: 1, k: big });
    const sh = isGuide() ? M3D.shadow('m-sel-veh-sh', M.mode, one, zoom, { k: big }) : null;
    return sh && l ? [sh, l] : l;
  }

  function layers(now, zoom) {
    const L = deck, out = [], sec = now / 1000, P = M.d.prefs;
    const bal = M.bal;
    const mu = Muni.isMuni();
    const G = isGuide(), selRow = M.sel ? M.rows.find((x) => x.k === M.sel) : null;
    // 案内の表示: 県の塗りは、選んだ区間の両端を強く、上位 5 区間の端をやや強く、ほかはうすく
    const gAlpha = (i) => {
      if (selRow) return i === selRow.fi || i === selRow.ti ? 210 : M.endpointSet.has(i) ? 86 : 38;
      return M.endpointSet.has(i) ? 150 : 52;
    };
    out.push(...Muni.under());
    // 案内の表示: 県を低く立ち上げる（端の県は高く、選んだ区間の両端はさらに高く）。斜めの光が当たり、傾けた地図で起伏のように読める。数字の大きさは表していない。
    // 小さな島（伊豆・小笠原など）は柱になって見苦しいので、立ち上げるのは本土側の面だけにし、島は平らな層で描く
    const prefH = (i) => (i === undefined ? 0 : (selRow ? i === selRow.fi || i === selRow.ti : M.endpointSet.has(i)) ? 400 : 100);   // 立ち上げる県はどれも同じ低さ。高さは強調だけで、量ではない
    const sp = G ? prefSplit() : null;
    const prefLayer = (id, data, ext) => new L.GeoJsonLayer({
      id, data, pickable: Muni.prefFilled(), stroked: true, filled: Muni.prefFilled(), beforeId: firstLabelId,
      extruded: ext, ...(ext ? { getElevation: (f) => prefH(M.prefIdx.get(f.properties.n)), material: { ambient: 0.22, diffuse: 0.3, shininess: 8, specularColor: [30, 36, 48] } } : {}),
      getFillColor: (f) => {
        const i = M.prefIdx.get(f.properties.n);
        if (i === undefined) return [60, 70, 90, G ? 40 : 80];
        const c = balColor(bal[i]);
        if (G) c[3] = gAlpha(i);
        return c;
      },
      getLineColor: mu ? [236, 244, 255, 190] : [210, 226, 255, 80], lineWidthMinPixels: mu ? 1.6 : 0.8, getLineWidth: mu ? 1.6 : 0.8, lineWidthUnits: 'pixels',
      autoHighlight: Muni.prefFilled(), highlightColor: [255, 255, 255, 38],
      updateTriggers: { getFillColor: [M.mode, G, M.sel], getLineColor: [mu], getLineWidth: [mu], getElevation: [G, M.sel] },
      transitions: { getFillColor: { duration: MapFx.ms(300), easing: ease }, ...(ext ? { getElevation: { duration: MapFx.ms(900), easing: ease } } : {}) },
    });
    if (G && sp) { out.push(prefLayer('m-pref', sp.minor, false)); out.push(prefLayer('m-pref-3d', sp.main, true)); } else out.push(prefLayer('m-pref', S.pref, false));
    if (G && sp && selRow) {   // 選んだ区間の両端の県は、立ち上げた面の上の縁を白い太線で示す（塗りは入荷・出荷の別で、端の印ではない）
      out.push(new L.PathLayer({
        id: 'm-sel-outline', parameters: ALWAYS, data: selOutline(sp, selRow), getPath: (d) => d, widthUnits: 'pixels', getWidth: 3.4, getColor: [255, 255, 255, 255],
        capRounded: true, jointRounded: true, updateTriggers: { getPath: [M.sel] },
      }));
    }
    if (Hubs.on) out.push(...Hubs.layers(zoom));
    out.push(...trafficLayers(now, G, M.sel ? 0.55 : 1));   // 選んだ区間の青い実線・橙の点線より下に敷く
    const F = M.flows;
    if (F && F.routes.length) {
      out.push(new L.PathLayer({
        id: 'm-flow-base', parameters: ALWAYS, data: F.actBase, getPath: (d) => d.pts, widthUnits: 'pixels',
        getWidth: (d) => (G ? 3 + d.route.w * 0.7 : Math.max(1, d.route.w * 0.5)),   // 案内の表示は、上位 5 区間の実線を太くして読めるようにする
        getColor: (d) => (G ? [...Kit.palette.legs.colors.loaded.slice(0, 3), 205 * dimOf(d.route)] : d.route.real ? [...Rt.col(M.mode), 92 * dimOf(d.route)] : [...d.route.col, 52 * dimOf(d.route)]), capRounded: true,
        updateTriggers: { getColor: [M.sel, M.hover, M.mode, G], getWidth: [M.mode, G] },
      }));
      if (F.roadBase.length) {   // 港や駅までの道: 細いくすんだ線（乗り物は走らない）。港・駅の印を置く
        out.push(new L.PathLayer({
          id: 'm-flow-road', parameters: ALWAYS, data: F.roadBase, getPath: (d) => d.pts, widthUnits: 'pixels', getWidth: G ? 1.8 : 1.2,
          getColor: (d) => [168, 176, 190, 120 * dimOf(d.route)], capRounded: true, updateTriggers: { getColor: [M.sel, M.hover, M.mode] },
        }));
        out.push(new L.ScatterplotLayer({
          id: 'm-flow-ports', parameters: ALWAYS, data: F.ports, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 3.6, filled: true, stroked: true,
          getFillColor: (d) => [10, 15, 25, 235 * dimOf(d.route)], getLineColor: (d) => [...Rt.col(M.mode), 255 * dimOf(d.route)], lineWidthUnits: 'pixels', getLineWidth: 1.4,
          updateTriggers: { getFillColor: [M.sel, M.hover, M.mode], getLineColor: [M.sel, M.hover, M.mode] },
        }));
      }
      if (F.unkBase.length) {   // 実際の線が無く、弧が手段に合わない区間: 細い点線（経路は不明。乗り物は出さない）
        out.push(new L.PathLayer({
          id: 'm-flow-unknown', parameters: ALWAYS, data: F.unkBase, getPath: (d) => d.pts, widthUnits: 'pixels', getWidth: 1.2,
          getColor: (d) => [168, 176, 190, 110 * dimOf(d.route)], capRounded: true, extensions: dashExtM(), getDashArray: [2, 3], dashJustified: true,
          updateTriggers: { getColor: [M.sel, M.hover, M.mode] },
        }));
      }
      out.push(new L.PathLayer({
        id: 'm-flow-pick', data: F.base, getPath: (d) => d.pts, widthUnits: 'pixels', getWidth: 14, getColor: [255, 255, 255, 3], pickable: true,
      }));
      const cur = sec % PERIOD;
      const tripProps = {
        data: F.items, getPath: (d) => d.pts, getTimestamps: (d) => d.ts, widthUnits: 'pixels', capRounded: true, jointRounded: true,
        currentTime: cur, fadeTrail: true,
      };
      out.push(new L.TripsLayer({
        ...tripProps, id: 'm-flow-glow', parameters: ALWAYS, getWidth: (d) => d.route.w * 2.6, trailLength: 3.4,
        getColor: (d) => [...d.route.col, 46 * dimOf(d.route)], updateTriggers: { getColor: [M.sel, M.hover, M.mode], getWidth: [M.mode] },
      }));
      out.push(new L.TripsLayer({
        ...tripProps, id: 'm-flow-core', parameters: ALWAYS, getWidth: (d) => d.route.w, trailLength: 2.6,
        getColor: (d) => [...d.route.col, 215 * dimOf(d.route)], updateTriggers: { getColor: [M.sel, M.hover, M.mode], getWidth: [M.mode] },
      }));
      // 帰り便の先頭の乗り物。ズームが小さいときは 2D アイコン、大きいときは 3D モデル（models3d.js の LOD）
      const heads = headData(sec), mx3 = M3D.mixFor(M.mode, zoom);
      if (mx3 < 0.999) {
        out.push(new L.IconLayer({
          id: 'm-flow-head', data: heads, getPosition: (d) => d.p, getIcon: () => Rt.icons()[M.mode === 'truck' ? 'truck' : M.mode], getAngle: (d) => d.ang,
          getSize: (d) => (M.mode === 'truck' ? 15 : 20) + d.route.w * 1.9, sizeUnits: 'pixels', getColor: (d) => [255, 255, 255, 255 * d.dim * d.fade * (1 - mx3)], billboard: false,
          updateTriggers: { getIcon: [M.mode], getSize: [M.mode] },
        }));
      }
      const hd = heads.map((d) => ({ p: d.p, ang: d.ang, a: d.dim * d.fade }));
      const sh = G ? M3D.shadow('m-flow-head-sh', M.mode, hd, zoom, { k: 0.9 }) : null;
      if (sh) out.push(sh);
      const h3 = M3D.vehicles('m-flow-head-3d', M.mode, hd, zoom, G ? { k: 0.9 } : {});
      if (h3) out.push(h3);
      if (G) out.push(...returnTrail(sec, selRow, zoom));
      out.push(new L.ScatterplotLayer({
        id: 'm-ends', parameters: ALWAYS, data: M.endpoints, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 2.6, filled: true, getFillColor: [225, 240, 255, 150],
      }));
    }
    out.push(...prefOverlay(G, sp));
    if (M.sel) {
      const r = M.rows.find((x) => x.k === M.sel);
      if (r) {
        const ph = (sec % 2.4) / 2.4, ends = [M.d.cap[r.fi], M.d.cap[r.ti]];
        out.push(new L.ScatterplotLayer({
          id: 'm-sel-pulse', parameters: ALWAYS, data: ends, getPosition: (d) => d, radiusUnits: 'pixels', getRadius: 8 + ph * 24, stroked: true, filled: false,
          getLineColor: [255, 255, 255, Math.round(190 * (1 - ph))], lineWidthUnits: 'pixels', getLineWidth: 1.6,
          updateTriggers: { getLineColor: [Math.round(ph * 30)], getRadius: [Math.round(ph * 30)] },
        }));
        out.push(new L.ScatterplotLayer({
          id: 'm-sel-ends', parameters: ALWAYS, data: ends, getPosition: (d) => d, radiusUnits: 'pixels', getRadius: 5.5, filled: true, getFillColor: [255, 255, 255, 255],
          stroked: true, getLineColor: [10, 15, 25, 255], lineWidthUnits: 'pixels', getLineWidth: 1.5,
        }));
      }
    }
    if (G && selRow && F) {
      const v = selVehicle(selRow, zoom);
      if (Array.isArray(v)) out.push(...v); else if (v) out.push(v);
    }
    if (M.jartic) {
      const pts = M.j.points, selPair = M.sel ? M.rows.find((x) => x.k === M.sel) : null;
      const inSel = (p) => selPair && p.fi === selPair.fi && p.ti === selPair.ti;
      out.push(new L.ScatterplotLayer({
        id: 'm-jartic', data: pts, getPosition: (p) => [p.lon, p.lat], pickable: true, radiusUnits: 'pixels',
        getRadius: (p) => (p.id === M.jpId ? 10 : inSel(p) ? 8.5 : 7), filled: true, getFillColor: (p) => [...jColor(p), G ? 110 : 235],
        stroked: true, getLineColor: (p) => (p.id === M.jpId ? [255, 255, 255, 255] : [12, 18, 30, 235]), lineWidthUnits: 'pixels',
        getLineWidth: (p) => (p.id === M.jpId ? 2.4 : 1.4), updateTriggers: { getRadius: [M.sel, M.jpId], getFillColor: [G], getLineColor: [M.jpId], getLineWidth: [M.jpId] },
      }));
      out.push(new L.IconLayer({
        id: 'm-jartic-arrow', data: pts, getPosition: (p) => [p.lon, p.lat], getIcon: () => ARROW, getAngle: (p) => (p.ratio >= 1 ? 0 : 180),
        getSize: 13, sizeUnits: 'pixels', getPixelOffset: [17, 0], getColor: (p) => [...jColor(p), G ? 120 : 255], updateTriggers: { getColor: [G] },
      }));
    }
    out.push(...Muni.over(zoom));
    return out;
  }

  /* ------------------------------------------------------------ 選択 */

  // 区間の両端が上の要約と右の欄（区間の一覧と重ねる切り替えの列）に隠れないよう、実際の大きさから余白を決める（SPEC §31.5 #8）
  const padding = () => {
    if (isGuide() && typeof guideFree === 'function') {   // 案内の表示は右の欄が無い。左の欄（狭い画面では下の欄）・上の帯・下の凡例・県のカードの外に区間を収める
      const f = guideFree();
      return { top: Math.max(f.top, 24) + 36, bottom: f.bottom + 18, left: f.left + 24, right: f.right + 24 };
    }
    if (innerWidth <= 1100) return { top: 120, bottom: 60, left: 40, right: 40 };
    const shown = (e) => (e && e.getClientRects().length ? e.getBoundingClientRect() : null);
    const sum = shown(q('#m-summary')), rt = shown(q('#m-right')), ly = shown(q('#layers'));
    const top = Math.max(150, sum ? Math.ceil(sum.bottom) + 28 : 0);
    const edge = Math.min(rt ? rt.left : innerWidth, ly ? ly.left : innerWidth);
    const right = edge < innerWidth ? innerWidth - edge + 28 : 560;
    return { top, bottom: 190, left: 430, right };
  };

  function fitPair(r) {
    const a = M.d.cap[r.fi], b = M.d.cap[r.ti];
    const fr = M.flows && M.flows.routes.find((x) => x.r.k === r.k);
    const mid = fr ? fr.rt.path : arcPath(a, b, 0.14, 24);
    const xs = mid.map((p) => p[0]), ys = mid.map((p) => p[1]);
    const cam = MapFx.selectCam(a, b) ;   // 案内の表示だけ、傾けて少し回す（2 秒以内）。全部を見るは今までどおり
    map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]], { padding: padding(), ownPadding: isGuide(), maxZoom: 7.4, ...MapFx.cam(1600, { curve: 1.5 }), ...cam });   // 真上から見る（pitch 0・bearing 0）
  }

  const refit = () => { const r = selected(); if (r && M.active) fitPair(r); };

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
    if (typeof Sheet !== 'undefined' && Sheet.narrow()) {
      // 狭い画面: 下のパネルは先頭（区間の名前と数字）から見せる。表の行に合わせてスクロールしない（SPEC §31.5 #14）
      const top = () => { const p = q('#m-right'); if (p && p.scrollTop !== 0) p.scrollTop = 0; };
      top();
      setTimeout(top, 120);
    } else if (row) row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
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
    if (!M.active) return;
    if (isGuide() && M.flows) {   // 案内の表示: 上位 5 区間の中ほどに、表の番号と同じ数字を置く
      M.rows.slice(0, 5).forEach((r, i) => {
        if (M.sel && r.k !== M.sel) return;   // 区間を選んだら、ほかの番号は隠す（東海道で重なって読めなくなるため）
        const fr = M.flows.routes.find((x) => x.r.k === r.k);
        if (!fr) return;
        const el = document.createElement('div');
        el.className = `m-num${r.k === M.sel ? ' on' : ''}`;
        el.textContent = String(i + 1);
        el.setAttribute('aria-hidden', 'true');
        const mk = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat(pointAt(fr.rt, 0.5)).addTo(map);
        mk.numRt = fr.rt;
        M.markers.push(mk);
      });
      spreadNums();
    }
    if (!M.sel) return;
    const r = M.rows.find((x) => x.k === M.sel);
    if (!r) return;
    for (const [i, cls] of [[r.fi, 'from'], [r.ti, 'to']]) {
      const el = document.createElement('div');
      el.className = `m-end ${cls}`;
      el.textContent = M.d.prefs[i];
      M.markers.push(new maplibregl.Marker({ element: el, anchor: 'bottom', offset: [0, -12] }).setLngLat(M.d.cap[i]).addTo(map));
    }
  }

  // 番号の札が画面上で重なるとき、その線に垂直な向きへ交互にずらす（東海道の上で 1〜3 が団子になるのを防ぐ）
  function spreadNums() {
    const ms = M.markers.filter((m) => m.numRt);
    if (!ms.length) return;
    const R = 26, placed = [];
    for (const m of ms) {
      const rt = m.numRt, a = map.project(pointAt(rt, 0.47)), b = map.project(pointAt(rt, 0.53)), p = map.project(m.getLngLat());
      let tx = b.x - a.x, ty = b.y - a.y;
      const len = Math.hypot(tx, ty) || 1;
      tx /= len; ty /= len;
      const nx = -ty, ny = tx;   // 線に垂直な向き
      let k = 0, dx = 0, dy = 0;
      for (; k < 8; k++) {
        const s = k === 0 ? 0 : (k % 2 ? 1 : -1) * Math.ceil(k / 2) * (R - 2);
        dx = nx * s; dy = ny * s;
        if (!placed.some((o) => Math.hypot(o.x - (p.x + dx), o.y - (p.y + dy)) < R)) break;
      }
      placed.push({ x: p.x + dx, y: p.y + dy });
      m.setOffset([dx, dy]);
    }
  }

  // 案内の表示で上位 5 区間（経路の線）がすべて入る範囲。日本全体ではなく、線のある所だけを収めるのに使う
  function topBounds() {
    if (!M.flows || !M.flows.routes.length) return null;
    let x0 = 999, y0 = 999, x1 = -999, y1 = -999;
    for (const fr of M.flows.routes.slice(0, 5)) for (const p of fr.rt.path) { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); }
    return [[x0, y0], [x1, y1]];
  }

  /* ------------------------------------------------------------ 右の区間の詳細と折れ線 */

  function renderPair() {
    const box = q('#m-pair');
    const r = M.sel ? M.rows.find((x) => x.k === M.sel) : null;
    document.body.classList.toggle('m-has-pair', !!r);
    if (!r) { box.hidden = true; box.innerHTML = ''; return; }
    const P = M.d.prefs, a = P[r.fi], b = P[r.ti];
    const mode = M.mode === 'truck' ? '営業用トラック' : M.mode === 'ferry' ? 'フェリー' : '鉄道コンテナ';
    let h = `<div class="mp-top"><div class="mp-title"><span>${esc(a)}</span><span class="ar">&rarr;</span><span>${esc(b)}</span></div>` +
      `<button class="mp-x" data-act="close" aria-label="区間の選択を外す">&#10005;</button></div>` +
      `<div class="mp-stats"><div><span>行き</span><b class="num">${n0(r.go)}</b><small>t</small></div><div><span>帰り</span><b class="num">${n0(r.back)}</b><small>t</small></div>` +
      `<div><span>帰りに足りない荷</span><b class="num">${n0(r.short)}</b><small>t</small></div>` +
      `<div><span>直線</span><b class="num">${n0(r.km)}</b><small>km</small></div></div>` +
      `<p class="mp-msg">${esc(b)}から${esc(a)}へ帰る荷が少ないため、<b>帰りが空きやすい向き</b>です。</p>` +
      `<ul class="mp-notes"><li>県ごとの数字です。各社の実際の空きとは違います。</li></ul>`;
    h += '<div class="mp-roles">' +
      '<div class="mp-role"><span>荷主の方</span><button class="btn-primary mp-go" data-act="find">この向きの帰り便を探す</button></div>' +
      `<div class="mp-role"><span>運送会社の方</span><button class="btn-ghost mp-go" data-act="carrier">この向きの帰り便を載せる</button></div></div>`;
    const clo = cloTarget(r);
    if (clo && r.km >= CLO_KM) {
      h += `<div class="mp-clo"><p>日数と費用は問い合わせてください。</p>` +
        `<button class="btn-ghost mp-go" data-act="clo"><span class="mp-go-main">鉄道と船も入れて送り方を比べる</span><span class="mp-go-sub">${esc(clo.oName)} から ${esc(clo.dName)} へ</span></button></div>`;
    } else if (!clo) {
      h += '<div class="mp-clo"><p class="mp-clo-why">集荷地は関東の 7 か所だけのため、この向きは比べられません。</p></div>';
    } else {
      h += `<div class="mp-clo"><p class="mp-clo-why">近いため、鉄道と船は比べに出していません。</p></div>`;
    }
    box.innerHTML = h;
    box.hidden = false;
  }

  // データの期間を見出しに出す（取得した週が過ぎても誤りにならないよう、日付で書く）。例: 10/27〜11/2 の 1 週間
  function weekText() {
    const p = M.j.period, f = (s) => `${Number(s.slice(5, 7))}/${Number(s.slice(8, 10))}`;
    return `${f(p.start)}〜${f(p.end)} の ${p.hours === 168 ? '1 週間' : `${p.hours} 時間`}`;
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
    else if (b.dataset.act === 'carrier') { Find.openCarrier(pairNames()); }
    else if (b.dataset.act === 'clo') { cloCompare(); }
  }

  /* 直線 400km 以上の区間から、鉄道・船を含めて送り方を比べる（SPEC §30.5・§31.3）。県庁所在地の市（東京は新宿区）を発地・着地にする */
  const CLO_KM = 400;
  const CAPITAL = {
    '北海道': '01101', '青森県': '02201', '岩手県': '03201', '宮城県': '04101', '秋田県': '05201', '山形県': '06201', '福島県': '07201',
    '茨城県': '08201', '栃木県': '09201', '群馬県': '10201', '埼玉県': '11107', '千葉県': '12101', '東京都': '13104', '神奈川県': '14104',
    '新潟県': '15103', '富山県': '16201', '石川県': '17201', '福井県': '18201', '山梨県': '19201', '長野県': '20201', '岐阜県': '21201',
    '静岡県': '22101', '愛知県': '23106', '三重県': '24201', '滋賀県': '25201', '京都府': '26104', '大阪府': '27128', '兵庫県': '28110',
    '奈良県': '29201', '和歌山県': '30201', '鳥取県': '31201', '島根県': '32201', '岡山県': '33101', '広島県': '34101', '山口県': '35203',
    '徳島県': '36201', '香川県': '37201', '愛媛県': '38201', '高知県': '39201', '福岡県': '40132', '佐賀県': '41201', '長崎県': '42201',
    '熊本県': '43101', '大分県': '44201', '宮崎県': '45201', '鹿児島県': '46201', '沖縄県': '47201',
  };
  // 区間のどちらかの端が発地に選べる関東の都県で、直線 CLO_KM 以上のときだけ入口を出す（SPEC §31.3）。
  // 発地 = 関東の端、着地 = もう一方の県庁所在地の市区（東京は新宿区）。どちらも関東なら区間の発側を発地にする
  function cloTarget(r) {
    if (!r) return null;
    const origins = S.meta.origins.map((o) => o.code);
    const fromP = M.d.prefs[r.fi], toP = M.d.prefs[r.ti];
    let o = CAPITAL[fromP], d = CAPITAL[toP];
    if (!origins.includes(o)) {
      if (!origins.includes(d)) return null;
      [o, d] = [d, o];
    }
    const nm = (c) => { const i = S.idx.codes.indexOf(c); return i >= 0 ? S.idx.names[i] : c; };
    return { o, d, oName: nm(o), dName: nm(d) };
  }
  function cloCompare() {
    const r = selected(), t = r && r.km >= CLO_KM ? cloTarget(r) : null;
    if (t) App.compareTo(t.o, t.d);
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
    if (r) r.innerHTML = '<span class="mp-hint">線の上に指を置くと、1 時間ごとの台数が出ます。</span>';
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
    const v = (a) => (a === null ? '記録なし' : `${n0(a)} 台`);
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
    let t = null, s = null, m = null;
    if (o && (id === 'm-pref' || id === 'm-pref-3d')) {
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
      s = `上り ${n0(o.up_total)} 台／下り ${n0(o.down_total)} 台`;
    } else {
      const mh = Muni.hoverText(info);
      const hh = mh || (Hubs.on ? Hubs.hoverText(info) : null);
      if (hh) { t = hh.t; s = hh.s; m = hh.m; }
    }
    if (!t) { tag.classList.remove('on'); return; }
    tag.innerHTML = `${m ? M3D.thumb(m, 64, 40, 'mth-tip') : ''}<b>${esc(t)}</b><span class="num">${esc(s)}</span>`;
    tag.style.transform = `translate(${Math.min(innerWidth - 420, info.x + 18)}px, ${Math.max(8, info.y - 8)}px)`;
    tag.classList.add('on');
  }

  function click(info) {
    const o = info && info.object, id = info && info.layer && info.layer.id;
    if (Muni.click(info)) return;
    if (o && id === 'm-flow-pick') return select(o.route.r.k, { fly: true });
    if (o && (id === 'm-pref' || id === 'm-pref-3d')) { const pi = M.prefIdx.get(o.properties.n); if (pi !== undefined) { openPref(pi); return; } }
    if (o && id === 'm-jartic') {
      const r = M.rows.find((x) => x.fi === o.fi && x.ti === o.ti);
      if (r) { M.jpId = o.id; select(r.k, { fly: M.sel !== r.k }); renderPair(); }
      else { toast('この向きは今の表示に入っていません。件数か距離を変えてください。'); }
      return;
    }
    if (!o) closePref();
    if (!o && M.sel) deselect(false);
  }

  /* ------------------------------------------------------------ 県の詳細（県を押すと小さなカードを出す） */

  /* 出荷・入荷は modes[手段].pairs（fi が荷の多い向きの出発県）の合計。帰りが空きやすい相手先は、その県を含む区間を差（不足量）の大きい順に 3 つ。
     数字は market.json から足し合わせるだけで、新しい規則は作らない（県の出−入は balance と一致する） */
  function prefInfo(i) {
    const pairs = M.d.modes[M.mode].pairs, P = M.d.prefs;
    let out = 0, inn = 0;
    const partners = [];
    for (const p of pairs) {
      // 荷が多い向きの逆が、帰りの空きやすい向き（ret: [帰りの発, 帰りの着]）
      if (p[0] === i) { out += p[2]; inn += p[3]; partners.push({ j: p[1], short: p[5], ret: p[2] >= p[3] ? [p[1], i] : [i, p[1]] }); }
      else if (p[1] === i) { out += p[3]; inn += p[2]; partners.push({ j: p[0], short: p[5], ret: p[2] >= p[3] ? [i, p[0]] : [p[0], i] }); }
    }
    partners.sort((a, b) => b.short - a.short);
    return { i, name: P[i], out, inn, top: partners.slice(0, 3).map((x) => ({ ...x, name: P[x.j] })) };
  }
  let prefCard = null;
  function ensureCard() {
    if (prefCard) return prefCard;
    prefCard = document.createElement('aside');
    prefCard.id = 'pref-card'; prefCard.className = 'glass node-card'; prefCard.hidden = true;
    prefCard.setAttribute('aria-label', '県の詳細');
    prefCard.addEventListener('click', (e) => { if (e.target.closest('.nc-close')) closePref(); });
    document.body.appendChild(prefCard);
    addEventListener('keydown', (e) => { if (e.key === 'Escape' && M.prefSel != null) closePref(); });
    return prefCard;
  }
  function openPref(i) {
    if (i === undefined || !M.active) return;
    const d = prefInfo(i), c = ensureCard(), P_ = M.d.prefs, max = Math.max(1, d.out, d.inn), mx = Math.max(1, ...d.top.map((x) => x.short));
    M.prefSel = i;
    const bars = d.top.map((x) => `<li><span class="nc-n">${esc(P_[x.ret[0]])} → ${esc(P_[x.ret[1]])}</span><span class="nc-bar"><i class="nc-or" style="width:${Math.max(3, Math.round((100 * x.short) / mx))}%"></i></span><span class="num nc-v">${n0(x.short)} t</span></li>`).join('');
    c.innerHTML = `<div class="nc-head"><b class="nc-title">${esc(d.name)}</b><button type="button" class="nc-close" aria-label="閉じる">×</button></div>`
      + `<dl class="nc-nums"><div><dt>出荷</dt><dd class="num">${n0(d.out)} t</dd></div><div><dt>入荷</dt><dd class="num">${n0(d.inn)} t</dd></div></dl>`
      + `<div class="nc-cmp" aria-hidden="true"><span class="nc-bar"><i class="nc-out" style="width:${Math.round((100 * d.out) / max)}%"></i></span><span class="nc-bar"><i class="nc-in" style="width:${Math.round((100 * d.inn) / max)}%"></i></span></div>`
      + `<div class="nc-sub"><i class="sw-dash"></i>空車で戻りやすい向き（荷の差 t）</div><ul class="nc-list">${bars || '<li>該当する区間がありません</li>'}</ul>`;
    c.hidden = false;
    const rt = q('#m-right'), shown = rt && rt.getClientRects().length && getComputedStyle(rt).position === 'fixed' && innerWidth > 1100;
    c.style.right = shown ? `${Math.round(innerWidth - rt.getBoundingClientRect().left + 16)}px` : '';
    prefDots = null;
    c.querySelector('.nc-close').focus({ preventScroll: true });
    dispatchEvent(new Event('m-cardchange'));
  }
  function closePref() {
    if (M.prefSel == null) return;
    M.prefSel = null; prefDots = null;
    if (prefCard) prefCard.hidden = true;
    dispatchEvent(new Event('m-cardchange'));
  }
  // 地図に重ねる印: 選んだ県の外周（白）と、帰りが空きやすい上位 3 区間の橙の点線（県庁所在地どうしを結ぶ）。静止した点線で、実際の便ではない
  let prefDots = null;
  function prefOverlay(G, sp) {
    const i = M.prefSel;
    if (i == null) return [];
    if (!prefDots || prefDots.i !== i || prefDots.mode !== M.mode) {
      const d = prefInfo(i), dots = [], P = M.d.prefs;
      for (const t of d.top) {
        const real = Rt.pairPath(P[i], P[t.j], M.mode, 0.02);
        const pts = real && real.length > 2 ? real : arcPath(M.d.cap[i], M.d.cap[t.j], 0.14, 48);
        const cum = [0];
        for (let k = 1; k < pts.length; k++) cum.push(cum[k - 1] + hav(pts[k - 1], pts[k]));
        const rt = { path: pts, cum, total: cum[cum.length - 1] || 1 }, n = Math.max(6, Math.round(rt.total / 18));
        for (let k = 0; k < n; k++) dots.push(subPath(rt, (k + 0.1) / n, (k + 0.6) / n));
      }
      prefDots = { i, mode: M.mode, dots };
    }
    const ring = [];
    for (const f of S.pref.features) {
      if (f.properties.n !== M.d.prefs[i]) continue;
      const g = f.geometry, polys = g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates];
      for (const pg of polys) ring.push(pg[0].map((p) => [p[0], p[1], G && sp && M.endpointSet.has(i) ? 410 : G && sp ? 110 : 0]));
    }
    return [
      new deck.PathLayer({ id: 'm-pref-outline', parameters: ALWAYS, data: ring, getPath: (d) => d, widthUnits: 'pixels', getWidth: 3, getColor: [255, 255, 255, 255], capRounded: true, jointRounded: true }),
      new deck.PathLayer({ id: 'm-pref-ret', parameters: ALWAYS, data: prefDots.dots, getPath: (d) => d, widthUnits: 'pixels', getWidth: 4, getColor: [...Kit.palette.legs.colors.empty.slice(0, 3), 245], capRounded: true }),
    ];
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
    if (!on) { TF.last = 0; closePref(); M.markers.forEach((m) => m.remove()); M.markers = []; q('#hover-tag').classList.remove('on'); }
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

  return { load, init, renderLegend, layers, flowLayers, hover, click, setActive, syncHash, readHash, afterStart, setJartic, select, deselect, selected, pairText, pairNames, prefInfo, openPref, closePref, state: M, refresh, syncMarkers, topBounds, headData, refit };
})();
