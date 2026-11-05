'use strict';
/* 実際の線の形で見せる（SPEC §17）。
   データは tools/build_web_data.py が data/routes/ から作った web/data/routes_*.json（座標は 1e-4 度の整数の差分）。
   ここでは、線の復元、乗り物の位置と向きの補間（見た目のためだけ）、4 手段の層、手段のカード、背景の地図の強調を行う。
   距離 km と運転時間はデータの値を表示するだけで、計算し直さない。 */

const Rt = (() => {
  const R = { meta: null, od: {}, pairsData: null, tripsData: null, rail: null, railPaths: null, cur: null, focus: null, tok: 0, orig: new Map(), emph: '', memo: new Map() };
  const ORDER = ['truck', 'rail', 'ferry', 'air'];
  const CODE = { t: 'truck', r: 'rail', f: 'ferry', a: 'air' };
  const DEF_COLORS = { truck: [255, 152, 38], rail: [72, 214, 112], ferry: [58, 196, 255], air: [210, 192, 255] }; // routes_meta.json と同じ値（読み込み前の予備）
  const LABEL = { truck: 'トラック', rail: '鉄道', ferry: '船', air: '飛行機' };
  const SPK = { truck: 0.016, rail: 0.016, ferry: 0.026, air: 0.0045 }; // 見た目の速さ（1km あたりの秒）。実際の所要時間ではない
  const SIZE = { truck: 30, rail: 46, ferry: 38, air: 54 };
  const ALT_RATIO = 0.2, ALT_MAX = 240000; // 飛行機の弧の高さ（見やすさのための強調で、実際の高度ではない）
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const q = (s) => document.querySelector(s);
  const col = (m) => (R.meta && R.meta.colors && R.meta.colors[m]) || DEF_COLORS[m];

  /* ------------------------------------------------------------ 自作の乗り物（SVG。上向きが進む向き） */

  const dark = '#0a0f19';
  const svgUrl = (body) => 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">${body}</svg>`);
  function vehicleSvg(mode, c, hi) {
    const st = `stroke="${dark}" stroke-width="2" stroke-linejoin="round"`;
    if (mode === 'truck') {
      return `<rect x="21" y="23" width="22" height="38" rx="3" fill="${c}" ${st}/><rect x="21" y="23" width="22" height="6" rx="2" fill="${hi}" opacity=".55"/>` +
        `<path d="M22 21V10.5Q22 5 27.5 5H36.5Q42 5 42 10.5V21Z" fill="${c}" ${st}/><path d="M25.5 9.5Q25.5 8 27 8H37Q38.5 8 38.5 9.5V14H25.5Z" fill="#dff3ff" stroke="${dark}" stroke-width="1"/>` +
        '<path d="M25 33H39M25 40H39M25 47H39M25 54H39" stroke="#0a0f19" stroke-width="1.2" opacity=".35"/>';
    }
    if (mode === 'rail') {
      return `<g ${st}><rect x="23" y="2" width="18" height="17" rx="5" fill="${c}"/><rect x="24" y="22" width="16" height="18" rx="2.5" fill="${c}"/><rect x="24" y="43" width="16" height="19" rx="2.5" fill="${c}"/></g>` +
        `<path d="M27 6.5Q32 4.5 37 6.5V10.5H27Z" fill="#dff3ff" stroke="${dark}" stroke-width="1"/>` +
        `<path d="M27 27H37M27 31H37M27 35H37M27 48H37M27 52H37M27 56H37" stroke="${dark}" stroke-width="1.2" opacity=".35"/><rect x="24" y="22" width="16" height="3" fill="${hi}" opacity=".5"/>`;
    }
    if (mode === 'ferry') {
      return `<path d="M32 2C41 12 44 26 43 52Q43 61 36 61H28Q21 61 21 52C20 26 23 12 32 2Z" fill="${c}" ${st}/>` +
        `<rect x="24" y="22" width="7" height="8" rx="1" fill="${hi}" stroke="${dark}" stroke-width="1"/><rect x="33" y="22" width="7" height="8" rx="1" fill="#dff3ff" stroke="${dark}" stroke-width="1"/>` +
        `<rect x="24" y="31.5" width="7" height="8" rx="1" fill="#dff3ff" stroke="${dark}" stroke-width="1"/><rect x="33" y="31.5" width="7" height="8" rx="1" fill="${hi}" stroke="${dark}" stroke-width="1"/>` +
        `<rect x="24" y="41" width="7" height="6" rx="1" fill="${hi}" stroke="${dark}" stroke-width="1"/><rect x="33" y="41" width="7" height="6" rx="1" fill="${hi}" stroke="${dark}" stroke-width="1"/>` +
        `<rect x="26" y="50" width="12" height="7" rx="2" fill="#e9f7ff" stroke="${dark}" stroke-width="1.2"/>`;
    }
    return `<path d="M32 2C34.4 2 35.5 7 35.5 13V24L59 38.5V44L35.5 37.5V51L43.5 56.5V61L32 58.5L20.5 61V56.5L28.5 51V37.5L5 44V38.5L28.5 24V13C28.5 7 29.6 2 32 2Z" fill="${c}" ${st}/>` +
      '<path d="M30.2 5.5Q32 4 33.8 5.5L33.6 9H30.4Z" fill="#dff3ff"/>' +
      `<path d="M32 11V50" stroke="${dark}" stroke-width="1.1" opacity=".3"/><circle cx="18" cy="38.5" r="2.3" fill="${hi}" stroke="${dark}" stroke-width="1"/><circle cx="46" cy="38.5" r="2.3" fill="${hi}" stroke="${dark}" stroke-width="1"/>`;
  }
  const lighten = (c, t) => `rgb(${c.map((v) => Math.round(v + (255 - v) * t)).join(',')})`;
  const rgb = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
  let ICONS = null;
  function icons() {
    if (ICONS) return ICONS;
    ICONS = {};
    for (const m of ORDER) {
      const c = col(m);
      ICONS[m] = { url: svgUrl(vehicleSvg(m, rgb(c), lighten(c, 0.55))), width: 64, height: 64, id: 'veh-' + m };
    }
    return ICONS;
  }
  const iconInline = (m, px) => { const c = col(m); return `<svg class="rt-svg" width="${px}" height="${px}" viewBox="0 0 64 64" aria-hidden="true">${vehicleSvg(m, rgb(c), lighten(c, 0.55))}</svg>`; };

  /* ------------------------------------------------------------ 読み込みと復元 */

  async function getj(u) {
    const r = await fetch(u);
    if (!r.ok) throw new Error(u + ' ' + r.status);
    return r.json();
  }
  function dec(flat) {
    const n = flat.length >> 1, out = new Array(n);
    let x = 0, y = 0;
    for (let i = 0; i < n; i++) { x += flat[2 * i]; y += flat[2 * i + 1]; out[i] = [x / 1e4, y / 1e4]; }
    return out;
  }
  async function loadMeta() {
    if (!R.meta) { try { R.meta = await getj('data/routes_meta.json'); } catch (e) { R.meta = { colors: DEF_COLORS }; } }
    icons();
    return R.meta;
  }
  async function loadOrigin(oc) {
    if (!R.od[oc]) R.od[oc] = getj(`data/routes_${oc}.json`);
    try { return await R.od[oc]; } catch (e) { delete R.od[oc]; throw e; }
  }
  async function loadPairs() { if (!R.pairsData) { try { R.pairsData = await getj('data/routes_pairs.json'); } catch (e) { R.pairsData = {}; } } return R.pairsData; }
  async function loadTrips() { if (!R.tripsData) { try { R.tripsData = await getj('data/routes_trips.json'); } catch (e) { R.tripsData = {}; } } return R.tripsData; }
  async function loadRail() {
    if (!R.rail) {
      R.rail = true;
      try {
        const d = await getj('data/routes_rail.json');
        // 画面の外を描かないように、線を 48 点ずつに分ける（隣の部分と 1 点重ねる）。cull.js で画面に入る部分だけを層に渡す
        R.railPaths = [];
        for (const l of d.lines) {
          const pts = dec(l).map((p) => [p[0], p[1], 0]);
          for (let a = 0; a < pts.length - 1; a += 47) R.railPaths.push(pts.slice(a, Math.min(pts.length, a + 48)));
        }
      } catch (e) { R.railPaths = []; }
    }
  }

  // ダグラス・ポイカー法の簡略化（度の単位。地図上の表示用に点を減らすだけ）
  function simplifyLL(pts, tol) {
    const n = pts.length;
    if (n <= 2) return pts.slice();
    const k0 = Math.cos((pts[0][1] * Math.PI) / 180);
    const keep = new Uint8Array(n);
    keep[0] = keep[n - 1] = 1;
    const stack = [[0, n - 1]];
    while (stack.length) {
      const [a, b] = stack.pop();
      const ax = pts[a][0] * k0, ay = pts[a][1], bx = pts[b][0] * k0, by = pts[b][1], dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy || 1e-12;
      let md = 0, mi = -1;
      for (let i = a + 1; i < b; i++) {
        const px = pts[i][0] * k0 - ax, py = pts[i][1] - ay, t = Math.max(0, Math.min(1, (px * dx + py * dy) / L2));
        const d = Math.hypot(px - t * dx, py - t * dy);
        if (d > md) { md = d; mi = i; }
      }
      if (md > tol && mi > 0) { keep[mi] = 1; stack.push([a, mi], [mi, b]); }
    }
    return pts.filter((_, i) => keep[i]);
  }
  const joinLegs = (m) => {
    const out = [];
    for (const l of m.legs) for (const p of dec(l.c)) if (!out.length || out[out.length - 1][0] !== p[0] || out[out.length - 1][1] !== p[1]) out.push(p);
    return out;
  };
  // 市場を見るの区間（県庁所在地どうし）の実際の線。手段は truck / ferry / rail。なければ null
  function pairPath(fromPref, toPref, mode, tol = 0.012) {
    const d = R.pairsData && R.pairsData[`${fromPref}>${toPref}`];
    const m = d && d.m && d.m[mode];
    if (!m) return null;
    const key = `p|${fromPref}|${toPref}|${mode}|${tol}`;
    if (!R.memo.has(key)) R.memo.set(key, simplifyLL(joinLegs(m), tol));
    return R.memo.get(key);
  }
  // 空きを探す・つなぐの架空の便の道路の線
  function tripPath(id, tol = 0.004) {
    const d = R.tripsData && R.tripsData[id];
    if (!d || !d.truck) return null;
    const key = `t|${id}|${tol}`;
    if (!R.memo.has(key)) R.memo.set(key, simplifyLL(joinLegs(d.truck), tol));
    return R.memo.get(key);
  }

  /* ------------------------------------------------------------ 区間と経路 */

  // 区間。path は [経度, 緯度, 高さ m]。飛行機だけ高さをつける
  function mkLeg(mode, km, minutes, label, ll, extra) {
    const cum = [0];
    for (let i = 1; i < ll.length; i++) cum.push(cum[i - 1] + hav(ll[i - 1], ll[i]));
    const total = cum[cum.length - 1] || 1;
    const H = mode === 'air' ? Math.min(ALT_MAX, ALT_RATIO * total * 1000) : 0;
    const path = ll.map((p, i) => [p[0], p[1], H * Math.pow(Math.sin(Math.PI * (cum[i] / total)), 0.85)]);
    return { mode, km, minutes: minutes ?? null, label: label || '', path, cum, total, ...extra };
  }
  const legFromData = (l) => mkLeg(CODE[l.m], l.km, l.min ?? null, l.l || '', dec(l.c));
  function posOn(leg, f) {
    const d = clamp01(f) * leg.total;
    let lo = 0, hi = leg.cum.length - 1;
    while (lo < hi - 1) { const m = (lo + hi) >> 1; if (leg.cum[m] <= d) lo = m; else hi = m; }
    const seg = leg.cum[hi] - leg.cum[lo] || 1, t = (d - leg.cum[lo]) / seg, a = leg.path[lo], b = leg.path[hi];
    return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
  }
  // 位置と向き（deck の角度は反時計回り）。曲がり角で揺れないように、前後の点で向きを決める
  function stateOn(leg, f) {
    const span = Math.max(0.9, leg.total * 0.004) / leg.total;
    const a = posOn(leg, f - span), b = posOn(leg, f + span), p = posOn(leg, f);
    const dx = (b[0] - a[0]) * Math.cos((p[1] * Math.PI) / 180), dy = b[1] - a[1];
    return { p, ang: -(Math.atan2(dx, dy) * 180) / Math.PI };
  }

  function buildSet(mode, m, why, names) {
    if (!m) return { mode, ok: false, why: why || '', legs: [], hubs: [], stops: [], km: 0, truckMin: 0 };
    const legs = m.legs.map(legFromData);
    const hubNames = m.hubs || [];
    const set = { mode, ok: true, why: '', legs, km: m.km, truckMin: m.tm, vfp: !!m.vfp, hubs: [], stops: [], fallback: false };
    const mids = legs.length === hubNames.length + 1 ? hubNames : legs.slice(0, -1).map((l) => (l.label.split('→')[1] || ''));
    set.stops = [names.o, ...mids, names.d];
    if (mode !== 'truck') set.hubs = legs.slice(0, -1).map((l, k) => ({ name: set.stops[k + 1], p: l.path[l.path.length - 1], mode }));
    return set;
  }
  function recount(set) {
    set.km = Math.round(set.legs.reduce((s, l) => s + l.km, 0) * 10) / 10;
    set.truckMin = Math.round(set.legs.reduce((s, l) => s + (l.mode === 'truck' ? l.minutes || 0 : 0), 0) * 10) / 10;
    set.fallback = set.legs.some((l) => l.fallback);
    if (set.mode !== 'truck') set.hubs = set.legs.slice(0, -1).map((l, k) => ({ name: set.stops[k + 1], p: l.path[l.path.length - 1], mode: set.mode }));
  }

  // 見た目の時間割（秒）。手段ごとの速さの比は SPK。K は経路全体の長さに合わせた倍率で、全手段に同じ値を使う
  function schedule(cur) {
    const ok = ORDER.map((m) => cur.sets[m]).filter((s) => s && s.ok);
    const ref = cur.sets.truck && cur.sets.truck.ok ? cur.sets.truck : ok[0];
    if (!ref) return;
    const base = Math.max(ref.km * SPK.truck, 1e-6);
    cur.K = Math.max(5, Math.min(16, base)) / base;
    cur.flat = [];
    cur.shadow = [];
    for (const s of ok) {
      let t = 0;
      for (const l of s.legs) {
        l.t0 = t;
        l.t1 = t + Math.max(0.45, l.km * SPK[l.mode] * cur.K);
        t = l.t1 + 0.3;
        cur.flat.push({ path: l.path, mode: l.mode, set: s.mode, fallback: !!l.fallback });
        if (l.mode === 'air') cur.shadow.push({ path: l.path.map((p) => [p[0], p[1], 0]), set: s.mode });
      }
      s.dur = t;
      s.loop = t + 2.4;
      s.nv = 2;
      s.items = [];
      for (let k = 0; k < s.nv; k++) {
        const off = (k * s.loop) / s.nv;
        for (const sh of [off, off - s.loop]) {
          for (const l of s.legs) s.items.push({ mode: l.mode, path: l.path, ts: l.cum.map((c) => l.t0 + (c / l.total) * (l.t1 - l.t0) + sh) });
        }
      }
    }
    cur.ver = (cur.ver || 0) + 1;
  }
  function vehiclesOf(s, sec) {
    const out = [];
    for (let k = 0; k < s.nv; k++) {
      const tv = ((((sec - (k * s.loop) / s.nv) % s.loop) + s.loop) % s.loop);
      if (tv > s.dur) continue;
      let leg = s.legs[s.legs.length - 1];
      for (const l of s.legs) { if (tv <= l.t1 + 0.3) { leg = l; break; } }
      const st = stateOn(leg, clamp01((tv - leg.t0) / (leg.t1 - leg.t0)));
      out.push({ ...st, mode: leg.mode, set: s.mode, fade: Math.min(1, tv * 3, (s.dur - tv) * 3 + 0.2) });
    }
    return out;
  }

  /* ------------------------------------------------------------ 時間の帯（トラックの区間だけ分で進める） */

  // トラックの区間は分で進める。鉄道・船・飛行機の区間は所要時間を出せないので、帯の上では固定の幅（gap）にして、
  // 「所要時間は時刻表で確認」と出す。その幅は時間を表さない。
  function timeline() {
    const cur = R.cur;
    if (!cur) return null;
    let s = cur.sets[R.focus || 'truck'];
    if (!s || !s.ok) s = ORDER.map((m) => cur.sets[m]).find((x) => x && x.ok);
    if (!s || s.vfp || s.fallback || s.legs.some((l) => l.mode === 'truck' && l.minutes === null)) return null;
    if (cur.tl && cur.tl.mode === s.mode && cur.tl.ver === cur.ver) return cur.tl;
    const truckMin = s.legs.reduce((a, l) => a + (l.mode === 'truck' ? l.minutes : 0), 0);
    const gap = Math.max(40, truckMin * 0.35);
    let t = 0;
    const segs = s.legs.map((l) => {
      const dur = l.mode === 'truck' ? l.minutes : gap;
      const seg = { leg: l, t0: t, t1: t + dur, truck: l.mode === 'truck' };
      t += dur;
      return seg;
    });
    cur.tl = { mode: s.mode, ver: cur.ver, M: t, truckMin, segs, single: s.legs.length === 1, set: s };
    return cur.tl;
  }
  function tlState(t) {
    const tl = timeline();
    if (!tl) return null;
    let seg = tl.segs[tl.segs.length - 1];
    for (const sg of tl.segs) { if (t <= sg.t1) { seg = sg; break; } }
    return { ...stateOn(seg.leg, clamp01((t - seg.t0) / ((seg.t1 - seg.t0) || 1))), mode: seg.leg.mode, seg };
  }

  /* ------------------------------------------------------------ 着地を選んだとき */

  async function fetchRoute(fromCode, toCode, fromLL) {
    const p = new URLSearchParams({ to: toCode });
    if (fromLL) p.set('from_ll', `${fromLL[0].toFixed(5)},${fromLL[1].toFixed(5)}`); else p.set('from', fromCode);
    const r = await fetch('/api/route?' + p.toString());
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || '経路を取得できませんでした');
    return j;
  }
  const originShort = () => curOd().name.replace(/^.+?[都道府県]/, '');

  async function setDestination(oc, i) {
    const tok = ++R.tok;
    R.cur = null;
    R.focus = null;
    applyEmphasis();
    render();
    await loadMeta();
    let od = null;
    try { od = await loadOrigin(oc); } catch (e) { od = null; }
    if (tok !== R.tok) return;
    const d = od && od.dests[S.idx.prefs[i]];
    const ll = [S.idx.lon[i], S.idx.lat[i]];
    const names = { o: originShort(), d: S.idx.names[i] };
    const isCap = !!d && hav(d.ll, ll) < 6; // 県庁所在地（の市区）なら、作ってある経路をそのまま使う
    const sets = {};
    for (const m of ORDER) sets[m] = buildSet(m, d ? d.m[m] : null, d ? (d.why && d.why[m]) || '' : '', names);
    // 県庁所在地の着地: 上の欄（詳細パネル）に道路距離があるときは、トラックのカードも同じ値にする（SPEC §31.5 #10）
    if (isCap && sets.truck.ok) {
      const roadKm = curOd() && curOd().km ? curOd().km[i] : null;
      if (roadKm !== null && roadKm !== undefined) {
        sets.truck.km = roadKm;
        if (sets.truck.legs.length === 1) sets.truck.legs[0].km = roadKm;
        // 運転時間も上の欄と同じ値にそろえる（距離だけそろえると、時間が食い違う）
        const roadMin = curOd().min ? curOd().min[i] : null;
        if (roadMin !== null && roadMin !== undefined) {
          sets.truck.truckMin = roadMin;
          if (sets.truck.legs.length === 1) sets.truck.legs[0].minutes = roadMin;
        }
      }
    }
    R.cur = { tok, oc, i, sets, names, ll, isCap, capLL: d ? d.ll : null, ver: 0, note: '', noteKind: '', tl: null };
    schedule(R.cur);
    if (!isCap) refine(tok);
    render();
    App.onRt(true);
  }

  // 県庁所在地でない市区町村: 着地までのトラックの区間を、その場で取った道路の経路に置き換える（1 秒 1 件）
  async function refine(tok) {
    const cur = R.cur, toCode = S.idx.codes[cur.i];
    const jobs = ORDER.filter((m) => m === 'truck' || (cur.sets[m] && cur.sets[m].ok && cur.sets[m].legs.at(-1).mode === 'truck'));
    let n = 0, failedTail = false, failedTruck = false;
    for (const m of jobs) {
      n++;
      cur.note = `着地までの道路の経路を取得しています（${n}/${jobs.length}）`;
      cur.noteKind = 'busy';
      render();
      let j;
      try {
        if (m === 'truck') j = await fetchRoute(S.origin, toCode, null);
        else { const p = cur.sets[m].legs.at(-1).path[0]; j = await fetchRoute(null, toCode, [p[0], p[1]]); }
      } catch (e) { j = { ok: false, fallback: true, message: '経路を取得できませんでした' }; }
      if (tok !== R.tok) return;
      if (m === 'truck') {
        const fb = !j.ok;
        const ll = j.coordinates || [[curOdPoint()[0], curOdPoint()[1]], cur.ll];
        // 上の欄（詳細パネルの距離）に道路距離があるときは同じ値を出す。無いときは「直線」と書いた直線距離を出す（SPEC §30.6）
        const roadKm = curOd().km ? curOd().km[cur.i] : null;
        const hasRoad = roadKm !== null && roadKm !== undefined;
        const km = hasRoad ? roadKm : (fb ? Math.round(hav(ll[0], ll[ll.length - 1]) * 10) / 10 : j.km);
        const straight = fb && !hasRoad;
        // 距離を上の欄の道路距離にそろえたときは、運転時間も上の欄の値にそろえる
        const roadMin = hasRoad && curOd().min ? curOd().min[cur.i] : null;
        const mins = roadMin !== null && roadMin !== undefined ? roadMin : (fb ? null : j.minutes);
        const leg = mkLeg('truck', km, mins, `${cur.names.o}→${cur.names.d}`, ll, { fallback: fb, straight });
        const ferry = !!(curOd().ferry && curOd().ferry[cur.i]);
        cur.sets.truck = { mode: 'truck', ok: true, why: '', legs: [leg], hubs: [], stops: [cur.names.o, cur.names.d], km: leg.km, truckMin: leg.minutes || 0, vfp: ferry, fallback: fb, straight };
        if (fb) failedTruck = true;
      } else if (j.ok) {
        const s = cur.sets[m], last = s.legs.at(-1);
        s.legs[s.legs.length - 1] = mkLeg('truck', j.km, j.minutes, `${s.stops[s.stops.length - 2]}→${cur.names.d}`, j.coordinates);
        recount(s);
      } else {
        failedTail = true;
        // 着地までの経路が取れなかった鉄道・船・飛行機は、線が県庁所在地の市区までのまま。最後の行き先もそう書く（SPEC §31.5 #11）
        const s = cur.sets[m];
        s.stops[s.stops.length - 1] = `${capitalName(cur)}（県庁所在地）まで`;
        s.capTail = true;
      }
      schedule(cur);
      render();
      App.onRt(false);
    }
    if (tok !== R.tok) return;
    // すべての取得が終わったあと、1 つでも取れなかったものがあれば、その旨を最後に出す。「取得しています」は残さない
    if (failedTruck || failedTail) {
      cur.note = (failedTruck ? '経路を取得できませんでした（直線の弧で表示しています）' : '') +
        (failedTail ? (failedTruck ? '。' : '') + '経路を取得できなかった区間は、県庁所在地までの経路のままです' : '');
      cur.noteKind = 'err';
    } else {
      cur.note = '鉄道・船・飛行機の最後の区間は、貨物駅・港・空港から着地までの道路の経路です';
      cur.noteKind = 'info';
    }
    render();
  }
  // 着地と同じ県の県庁所在地の市区の名前（作ってある経路の着地の位置に最も近い市区）。見つからなければ県の名前
  function capitalName(cur) {
    const pref = S.idx.prefs[cur.i];
    const d = cur.capLL || null;
    if (!d) return `${pref}の`;
    let best = -1, bd = 1e9;
    for (let j = 0; j < S.idx.codes.length; j++) {
      if (S.idx.prefs[j] !== pref) continue;
      const k = hav(d, [S.idx.lon[j], S.idx.lat[j]]);
      if (k < bd) { bd = k; best = j; }
    }
    return best >= 0 ? S.idx.names[best] : `${pref}の`;
  }
  const curOdPoint = () => { const o = S.meta.origins.find((x) => x.code === S.origin); return [o.lon, o.lat]; };

  function clear() {
    R.tok++;
    R.cur = null;
    R.focus = null;
    applyEmphasis();
    resetView();
    render();
  }
  function setFocus(mode) {
    if (!R.cur || !R.cur.sets[mode] || !R.cur.sets[mode].ok) return;
    R.focus = R.focus === mode ? null : mode;
    applyEmphasis();
    render();
    fit();
    App.onRt(false);
  }
  // 市区町村の塗りの濃さ。トラック・鉄道・船を強調しているときは薄くして、背景の道路・線路が見えるようにする
  const fillScale = () => (R.cur && !R.off && (R.focus === 'truck' || R.focus === 'rail' || R.focus === 'ferry') ? 0.36 : 1);
  // 見方を切り替えたとき: 傾きと背景の強調を戻す／もとに戻す
  function suspend() { R.off = true; applyEmphasis(); resetView(); }
  function resume() { R.off = false; applyEmphasis(); }
  function resetView() {
    if (typeof map !== 'undefined' && map && map.getPitch() > 0.5) map.easeTo({ pitch: 0, duration: 900 });
  }
  function fit() {
    const cur = R.cur;
    if (!cur) return;
    const sets = (R.focus ? [cur.sets[R.focus]] : ORDER.map((m) => cur.sets[m])).filter((s) => s && s.ok);
    let x0 = 999, y0 = 999, x1 = -999, y1 = -999;
    for (const s of sets) for (const l of s.legs) for (const p of l.path) { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); }
    if (x0 > x1) { fitRoute(); return; }
    const air = R.focus === 'air';
    const pad = padding();
    pad.right += 110; // 右端の「重ねる」の切り替えの分
    if (air) { pad.top += 150; pad.bottom += 20; pad.right += 120; }
    // fitBounds は傾きを変えないので、カメラの位置を求めてから傾きつきで移動する（飛行機のときは地図を立体にする）
    const cam = map.cameraForBounds([[x0, y0], [x1, y1]], { padding: pad, maxZoom: air ? 6.6 : 8.6 });
    if (!cam) return;
    map.flyTo({ center: cam.center, zoom: air ? cam.zoom + 0.35 : cam.zoom, pitch: air ? 58 : 0, bearing: 0, duration: 2400, curve: 1.5, essential: true });
  }

  /* ------------------------------------------------------------ 背景の地図の強調（OpenFreeMap の層） */

  const EMPH = {
    truck: [
      { ids: ['highway_motorway_inner', 'highway_motorway_subtle', 'highway_motorway_bridge_inner', 'tunnel_motorway_inner'], color: '#ffb35c', op: 1, w: 1.8 },
      { ids: ['highway_motorway_casing', 'highway_motorway_bridge_casing', 'tunnel_motorway_casing'], color: '#2a1a08', op: 0.9 },
      { ids: ['highway_major_inner', 'highway_major_subtle'], color: '#b08a58', op: 0.8 },
    ],
    rail: [{ ids: ['railway', 'railway_dashline', 'railway_transit', 'railway_transit_dashline', 'railway_service', 'railway_service_dashline'], color: '#7be7a2', op: 1, w: 1.6 }],
    ferry: [{ ids: ['water'], prop: 'fill-color', color: '#0d2438' }],
  };
  function applyEmphasis() {
    if (typeof map === 'undefined' || !map || !map.getStyle()) return;
    const want = R.cur && !R.off ? (R.focus || '') : '';
    if (want === R.emph) return;
    R.emph = want;
    // 前の強調を戻す
    for (const [key, v] of R.orig) { const [id, prop] = key.split('|'); if (map.getLayer(id)) map.setPaintProperty(id, prop, v); }
    R.orig.clear();
    for (const g of EMPH[want] || []) {
      for (const id of g.ids) {
        if (!map.getLayer(id)) continue;
        const prop = g.prop || 'line-color';
        R.orig.set(`${id}|${prop}`, map.getPaintProperty(id, prop));
        map.setPaintProperty(id, prop, g.color);
        if (!g.prop) {
          R.orig.set(`${id}|line-opacity`, map.getPaintProperty(id, 'line-opacity'));
          map.setPaintProperty(id, 'line-opacity', ['interpolate', ['linear'], ['zoom'], 3, g.op * 0.55, 6, g.op * 0.9, 9, g.op]);
          if (g.w && !id.includes('casing')) {
            R.orig.set(`${id}|line-width`, map.getPaintProperty(id, 'line-width'));
            map.setPaintProperty(id, 'line-width', ['interpolate', ['linear'], ['zoom'], 4, g.w * 0.5, 8, g.w, 12, g.w * 2.4]);
          }
        }
      }
    }
  }

  /* ------------------------------------------------------------ 層 */

  const dashExt = () => (typeof deck.PathStyleExtension === 'function' ? [new deck.PathStyleExtension({ dash: true })] : []);
  const withA = (c, a) => [c[0], c[1], c[2], Math.round(a)];

  function iconLayer(id, data, mode, size, extra = {}) {
    return new deck.IconLayer({
      id, data, getPosition: (d) => d.p, getIcon: () => icons()[mode], getAngle: (d) => d.ang, getSize: size, sizeUnits: 'pixels', billboard: false,
      getColor: (d) => [255, 255, 255, Math.round(255 * (d.a ?? 1))], ...extra,
    });
  }

  // 乗り物の層。ズームが小さいときは 2D アイコン、大きいときは 3D モデル、間は交差（models3d.js の LOD）
  function vehLayers(id, data, mode, size, zoom, extra = {}, k = 1) {
    const out = [], mx = M3D.mixFor(mode, zoom);
    if (mx < 0.999) out.push(iconLayer(id, mx > 0.001 ? data.map((d) => ({ ...d, a: (d.a ?? 1) * (1 - mx) })) : data, mode, size, extra));
    const l3 = M3D.vehicles(id + '-3d', mode, data, zoom, { k });
    if (l3) out.push(l3);
    return out;
  }

  function railLayer(alpha) {
    if (!R.railPaths) { loadRail(); return null; }
    if (!R.railPaths.length || alpha <= 0) return null;
    const cv = Cull.view('rail', R.railPaths, Cull.bboxOfCoords, typeof map !== 'undefined' ? map : null);
    return new deck.PathLayer({
      id: 'rt-railnet', data: cv.items, getPath: (d) => d, getColor: withA(col('rail'), alpha), widthUnits: 'pixels', getWidth: 1, widthMinPixels: 0.7,
      updateTriggers: { getColor: [alpha] },
    });
  }

  // 比べる画面（着地を選んだとき）の層。S.t は時間の帯の分
  function compareLayers(now, zoom, tmin) {
    const cur = R.cur, L = deck, out = [], sec = now / 1000, F = R.focus;
    if (!cur) return out;
    const sets = ORDER.map((m) => cur.sets[m]).filter((s) => s && s.ok && s.items);
    const A = (m) => (F ? (F === m ? 1 : 0.2) : 1);
    const trig = [F, cur.ver];
    const rl = railLayer(F === 'rail' ? 120 : F ? 0 : 22);
    if (rl) out.push(rl);
    if (F === 'ferry' && Hubs.data) {
      out.push(new L.PathLayer({
        id: 'rt-ferry-routes', data: Hubs.data.routes, getPath: (r) => r.p, getColor: withA(col('ferry'), 120), getWidth: 1.4, widthUnits: 'pixels', widthMinPixels: 1,
        extensions: dashExt(), getDashArray: [3, 3], dashJustified: true,
      }));
    }
    // 飛行機の地面への影
    if (cur.shadow && cur.shadow.length) {
      out.push(new L.PathLayer({
        id: 'rt-air-shadow', data: cur.shadow, getPath: (d) => d.path, getColor: (d) => withA(col('air'), 46 * A(d.set)), widthUnits: 'pixels', getWidth: 1.6,
        extensions: dashExt(), getDashArray: [2, 3], updateTriggers: { getColor: trig },
      }));
    }
    // 線の縁（暗い）と、手段ごとの色の線
    out.push(new L.PathLayer({
      id: 'rt-casing', data: cur.flat, getPath: (d) => d.path, getColor: (d) => [8, 12, 20, 190 * A(d.set)], widthUnits: 'pixels', capRounded: true, jointRounded: true,
      getWidth: (d) => (F === d.set ? 7.5 : 5.5), updateTriggers: { getColor: trig, getWidth: trig },
    }));
    out.push(new L.PathLayer({
      id: 'rt-base', data: cur.flat, getPath: (d) => d.path, getColor: (d) => withA(col(d.mode), (F === d.set ? 235 : F ? 130 : 205) * (d.fallback ? 0.8 : 1)),
      widthUnits: 'pixels', capRounded: true, jointRounded: true, getWidth: (d) => (F === d.set ? 4.2 : 2.6),
      extensions: dashExt(), getDashArray: (d) => (d.fallback ? [4, 3] : [0, 0]), dashJustified: false,
      updateTriggers: { getColor: trig, getWidth: trig, getDashArray: trig },
    }));
    // 乗り物の後ろの軌跡
    for (const s of sets) {
      const a = A(s.mode), ct = sec % s.loop;
      out.push(new L.TripsLayer({
        id: 'rt-glow-' + s.mode, data: s.items, getPath: (d) => d.path, getTimestamps: (d) => d.ts, getColor: (d) => withA(col(d.mode), 70 * a), widthMinPixels: 11,
        capRounded: true, jointRounded: true, trailLength: 2.4, currentTime: ct, fadeTrail: true, updateTriggers: { getColor: trig },
      }));
      out.push(new L.TripsLayer({
        id: 'rt-trail-' + s.mode, data: s.items, getPath: (d) => d.path, getTimestamps: (d) => d.ts, getColor: (d) => withA(lightCol(d.mode), 235 * a), widthMinPixels: 3,
        capRounded: true, jointRounded: true, trailLength: 1.8, currentTime: ct, fadeTrail: true, updateTriggers: { getColor: trig },
      }));
    }
    // 乗り換えの印（貨物駅・港・空港）
    // 配列は元データ・強調・版が同じ間は同じ参照で返す（毎フレーム作り直すと属性が毎回作り直される）
    const hubs = stableData('rt-hubs', [...sets, F, cur.ver], () => sets.flatMap((s) => s.hubs.map((h) => ({ ...h, a: A(s.mode) }))));
    if (hubs.length) {
      const hubNames = stableData('rt-hub-names', [hubs, !!F, zoom >= 7], () => hubs.filter((h) => h.a >= 0.9 && (F || zoom >= 7)));
      out.push(memoLayer('rt-hubs', hubs, F + '|' + cur.ver, () => new L.ScatterplotLayer({
        id: 'rt-hubs', data: hubs, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 5.5, filled: true, stroked: true, getFillColor: (d) => [10, 15, 25, 240 * d.a],
        getLineColor: (d) => withA(col(d.mode), 255 * d.a), lineWidthUnits: 'pixels', getLineWidth: 2.4, updateTriggers: { getFillColor: trig, getLineColor: trig },
      })));
      out.push(memoLayer('rt-hub-names', hubNames, F + '|' + cur.ver, () => new L.TextLayer({
        id: 'rt-hub-names', data: hubNames, getPosition: (d) => d.p, getText: (d) => d.name, getSize: 12, sizeUnits: 'pixels',
        getColor: (d) => withA(lightCol(d.mode), 250), getPixelOffset: [0, 14], getTextAnchor: 'middle', getAlignmentBaseline: 'top', fontFamily: '"Noto Sans JP", sans-serif',
        characterSet: 'auto', outlineWidth: 3, outlineColor: [8, 12, 20, 240], fontSettings: { sdf: true }, billboard: true,
        updateTriggers: { getText: trig },
      })));
    }
    // 乗り物
    const veh = [];
    for (const s of sets) for (const v of vehiclesOf(s, sec)) veh.push({ ...v, a: A(s.mode) * v.fade });
    const lead = tlState(tmin);
    for (const m of ORDER) {
      const data = veh.filter((v) => v.mode === m);
      if (m === 'air') {
        out.push(iconLayer('rt-shadow-air', data.map((v) => ({ ...v, p: [v.p[0], v.p[1], 0], a: v.a * 0.35 })), 'air', SIZE.air * 0.9, { getColor: (d) => [0, 0, 0, Math.round(150 * d.a)] }));
      }
      // 乗り物が 1 台も見えない間も、経路のある手段の層は消さない。消すたびに層が作り直され、アイコン画像の読み込み中に捨てられると
      // WebGL の INVALID_OPERATION の警告が出る（SPEC §33 #5）。データが空の層は描かないだけで、費用はほとんど無い
      if (data.length || (cur.sets[m] && cur.sets[m].ok)) out.push(...vehLayers('rt-veh-' + m, data, m, (F === m ? 1.12 : 1) * SIZE[m], zoom, { updateTriggers: { getSize: [F] } }, F === m ? 1.12 : 1));
    }
    if (lead) {
      R.leadMode = lead.mode;
      const leadD = stableData('rt-lead-halo', [lead.p[0], lead.p[1]], () => [{ p: lead.p }]);
      out.push(memoLayer('rt-lead-halo', leadD, lead.mode, () => new L.ScatterplotLayer({
        id: 'rt-lead-halo', data: leadD, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 24, filled: true, getFillColor: withA(col(lead.mode), 70),
        stroked: true, getLineColor: withA(col(lead.mode), 220), lineWidthUnits: 'pixels', getLineWidth: 1.6, updateTriggers: { getFillColor: [lead.mode], getLineColor: [lead.mode] },
      })));
      out.push(...vehLayers('rt-lead', stableData('rt-lead', [lead.p[0], lead.p[1], lead.ang], () => [{ p: lead.p, ang: lead.ang }]), lead.mode, SIZE[lead.mode] * 1.3, zoom, {}, 1.3));
    } else if (R.leadMode) {
      // 先頭の乗り物が無い間（直線の弧など）も、先頭の乗り物の層は消さずに空のデータで残す（SPEC §33 #5）
      out.push(...vehLayers('rt-lead', stableData('rt-lead-empty', [], () => []), R.leadMode, SIZE[R.leadMode] * 1.3, zoom, {}, 1.3));
    }
    // 1 日の運転（9 時間）の終わりの印（トラックだけの経路のとき）
    const tl = timeline();
    if (tl && tl.single && tl.set.mode === 'truck') {
      const leg = tl.set.legs[0], marks = [];
      for (let d = 540; d < leg.minutes; d += 540) marks.push({ p: posOn(leg, d / leg.minutes) });
      if (marks.length) {
        out.push(new L.ScatterplotLayer({
          id: 'daymarks', data: marks, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 6, stroked: true, filled: true,
          getFillColor: [10, 16, 26, 230], getLineColor: [255, 255, 255, 220], lineWidthUnits: 'pixels', getLineWidth: 1.6,
        }));
      }
    }
    return out;
  }
  const lightCol = (m) => { const c = col(m); return [c[0] + (255 - c[0]) * 0.55, c[1] + (255 - c[1]) * 0.55, c[2] + (255 - c[2]) * 0.55].map(Math.round); };

  /* ------------------------------------------------------------ 手段のカード（右のパネル） */

  const km1 = (v) => (Math.round(v * 10) / 10).toLocaleString('ja-JP', { maximumFractionDigits: 1 });
  function legsHtml(s) {
    let h = esc(s.stops[0]);
    s.legs.forEach((l, k) => {
      h += `<span class="ar">→</span><span class="lg lg-${l.mode}">${LABEL[l.mode]} ${l.straight ? '直線 ' : ''}${km1(l.km)}km</span><span class="ar">→</span>${esc(s.stops[k + 1])}`;
    });
    return h;
  }
  function timeHtml(s) {
    if (s.mode === 'truck') {
      if (s.vfp) return 'フェリーを含む経路の可能性があり、運転時間は出しません';
      if (s.fallback) return s.truckMin ? `運転 <b class="num">${fmtMin(s.truckMin)}</b>（線は直線の弧）` : '運転時間は出せません（直線の弧）';
      return `運転 <b class="num">${fmtMin(s.truckMin)}</b>`;
    }
    return `トラックの区間 計 <b class="num">${fmtMin(s.truckMin)}</b> ／ ${LABEL[s.mode]}の区間は所要時間は時刻表で確認`;
  }
  // 手段の状態: 'route'（経路あり）、'none'（つながる経路なし）、null（着地が未選択）。横棒の図が同じ言い方にそろえるために使う
  function modeState(m) {
    const cur = R.cur;
    if (!cur) return null;
    const s = cur.sets[m];
    return s && s.ok ? 'route' : 'none';
  }
  function render() {
    const el = q('#rt-modes');
    if (!el) return;
    const cur = R.cur;
    if (!cur) { el.innerHTML = ''; return; }
    const F = R.focus;
    const cards = ORDER.map((m) => {
      const s = cur.sets[m];
      if (!s || !s.ok) {
        // 船は「つながる航路がありません」と書き、横棒の図（Rt.modeState('ferry') === 'none'）と同じ言い方にする（SPEC §33 #7）
        if (m === 'ferry') {
          return `<div class="rt-card off" style="--c:${rgb(col(m))}" data-state="none" title="${esc((s && s.why) || '')}"><span class="rt-top">${iconInline(m, 26)}<b>${LABEL[m]}</b></span>` +
            '<span class="rt-why">なし（つながる航路がありません）</span></div>';
        }
        return `<div class="rt-card off" style="--c:${rgb(col(m))}"><span class="rt-top">${iconInline(m, 26)}<b>${LABEL[m]}</b><span class="rt-km">なし</span></span>` +
          `<span class="rt-why">${esc((s && s.why) || '作っていません')}</span></div>`;
      }
      // 鉄道が道路の 2 倍を超えるときは、貨物駅を回る遠回りだと書く（SPEC §33 #6）
      const tk = cur.sets.truck;
      const detour = m === 'rail' && tk && tk.ok && tk.km > 0 && s.km > 2 * tk.km
        ? '<span class="rt-why rt-detour">貨物駅を回るため、道路より長くなります</span>' : '';
      // 車の大きさはデータにないので、トラックは 10t ウイングのサムネイル（SPEC §19.6）
      return `<button type="button" class="rt-card${F === m ? ' on' : ''}${F && F !== m ? ' dim' : ''}" data-mode="${m}" aria-pressed="${F === m}" style="--c:${rgb(col(m))}">` +
        `<span class="rt-body"><span class="rt-top">${iconInline(m, 26)}<b>${LABEL[m]}</b><span class="rt-km num">${s.straight ? '直線 ' : ''}${km1(s.km)} km</span></span>` +
        `<span class="rt-time">${timeHtml(s)}</span><span class="rt-legs">${legsHtml(s)}</span>${detour}</span>${M3D.thumb(M3D.MODE_MODEL[m], 80, 50, 'mth-card')}</button>`;
    }).join('');
    const note = cur.note ? `<p class="rt-note ${cur.noteKind}" role="status">${esc(cur.note)}</p>` : '';
    const head = '<div class="rt-head"><b>送り方を比べる</b><small>手段のカードを押すと、その手段の経路を強く出します</small></div>';
    const key = cards + note;
    if (el.dataset.key === key) return;
    el.dataset.key = key;
    el.innerHTML = head + `<div class="rt-grid">${cards}</div>` + note;
  }
  function bind() {
    q('#rt-modes').addEventListener('click', (e) => { const b = e.target.closest('.rt-card[data-mode]'); if (b) setFocus(b.dataset.mode); });
  }

  /* ------------------------------------------------------------ 凡例 */

  function legendHtml() {
    return '<div class="rt-lg-cap">手段</div>' + ORDER.map((m) => `<div class="rt-lg-row"><span class="rt-lg-ic">${iconInline(m, 24)}${M3D.thumb(M3D.MODE_MODEL[m], 40, 25, 'mth-lg')}</span><span>${LABEL[m]}</span></div>`).join('');
  }
  function mountLegend() { document.querySelectorAll('.rt-legend').forEach((el) => { el.innerHTML = legendHtml(); }); }

  return {
    R, ORDER, LABEL, col, icons, iconInline, loadMeta, loadPairs, loadTrips, loadRail, setDestination, setFocus, clear, fit, suspend, resume, fillScale, resetView, applyEmphasis,
    compareLayers, timeline, tlState, render, bind, pairPath, tripPath, iconLayer, railLayer, legendHtml, mountLegend, simplifyLL, dec,
    get focus() { return R.focus; }, get cur() { return R.cur; }, hasSets: () => !!R.cur, modeState,
  };
})();
