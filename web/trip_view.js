/* 選んだ 1 回の運行の図（TripView）。「どこを走ったか」「その区間に荷物があったか」を 1 枚で読む。
   使い方: TripView.render(container, leg, ctx) / TripView.destroy(container)
     leg: OwnCalc.analyze の legs の 1 件（id, frm, to, date, dep, arr, km, kind, kind_src, km_src, plate）か、
          OwnCalc.returnTrips の rows の 1 件（pair_id, dt, dt_basis, km, km_src_cat, kind, kind_src_cat, plate）。どちらも受ける
     ctx: { places, vehicles, route: { path: [[lon, lat], ...] }, base, dock, imgBase, vehicleImages, routePair: { fr, to } }
   道路は、この区間の組が経路データ（route.path）の組と同じときだけ描く。無いときは「区間の模式図」にして、道路も現在位置も描かない。
   車は経路の上に置かない（走行中の位置は分からないため）。図の下の帯に、車と荷の状態を並べる。
   ライブラリは使わない（DOM と SVG だけ）。動きは無い。 */
(function (root) {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const VW = 360, VH = 264, GROUND_H = 198;
  const MAP = { x0: 40, x1: 320, y0: 50, y1: 128 };
  const MAX_POINTS = 60;

  // 車格ごとの画像。あとで上から描いた画像に差し替えるときは、ここ（または ctx.vehicleImages）だけを変える
  const VEHICLE_IMAGES = { '2t': 'box_2t.webp', '4t': 'truck_4t.webp', '大型': 'truck.webp', '10t': 'truck.webp', '軽': 'kei_van.webp' };
  const IMG_BASE = 'img/models/';
  const KIND_TEXT = { loaded: '荷物あり', empty: '空車', unknown: '不明' };
  const SRC_TEXT = { digitaco: 'デジタコ', haisha: '配車表', estimate: '推定', user: '画面で選んだ値', unknown: '不明' };
  const WD = ['日', '月', '火', '水', '木', '金', '土'];
  const BASIS_TEXT = { dep: '出発の時刻', date: '日付だけ分かる', none: '日時の記録なし' };

  const el = (tag, attrs, text) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  };
  const h = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  function srcCat(s) {
    if (root.OwnCalc && root.OwnCalc.srcCat) return root.OwnCalc.srcCat(s);
    if (s == null || String(s) === '') return 'unknown';
    const t = String(s);
    if (t.includes('デジタコ')) return 'digitaco';
    if (t.includes('配車表')) return 'haisha';
    if (t.includes('推定') || t.includes('OSRM') || t.includes('直線')) return 'estimate';
    if (t.includes('利用者')) return 'user';
    return 'unknown';
  }

  /* ---------- 入力をそろえる */
  function whenOf(l) {
    // 行（returnTrips の rows）はすでに日時と根拠を持つ
    if (l.dt_basis) return { dt: l.dt || null, basis: l.dt_basis };
    const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(typeof l.dep === 'string' ? l.dep : '');
    if (m) return { dt: `${m[1]}T${m[2]}`, basis: 'dep' };
    if (typeof l.date === 'string' && /^\d{4}-\d{2}-\d{2}/.test(l.date)) return { dt: l.date.slice(0, 10), basis: 'date' };
    return { dt: null, basis: 'none' };
  }
  function normalize(l, ctx) {
    let fr = l.frm != null ? l.frm : null, to = l.to != null ? l.to : null;
    if (l.pair_id && fr == null && to == null) { const p = String(l.pair_id).split('>'); fr = p[0] || null; to = p[1] || null; }
    const kind = l.kind === 'loaded' || l.kind === 'empty' ? l.kind : 'unknown';
    const km = typeof l.km === 'number' && isFinite(l.km) && l.km >= 0 ? l.km : null;
    const veh = ((ctx && ctx.vehicles) || []).find((v) => v.plate === l.plate);
    return {
      id: l.leg_id != null ? l.leg_id : l.id, fr, to, kind, km, plate: l.plate,
      cls: l.cls || (veh && veh.cls) || null,
      when: whenOf(l), arr: l.arr || null,
      kindSrc: l.kind_src_cat || (kind !== 'unknown' ? srcCat(l.kind_src) : 'unknown'),
      kmSrc: l.km_src_cat || (km != null ? srcCat(l.km_src || (l.sources || {}).km) : 'unknown'),
    };
  }
  function fmtWhen(w) {
    if (!w.dt) return '日時不明';
    const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}:\d{2}))?/.exec(w.dt);
    if (!m) return w.dt;
    const wd = WD[new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).getUTCDay()];
    return `${+m[2]}/${+m[3]}（${wd}）${m[4] || ''}`.trim();
  }

  /* ---------- 経路 */
  function routeFor(t, ctx) {
    const path = ctx && ctx.route && ctx.route.path;
    if (!path || path.length < 2 || !t.fr || !t.to) return null;
    const rp = (ctx && ctx.routePair) || { fr: ctx && ctx.base, to: ctx && ctx.dock };
    if (!rp.fr || !rp.to) return null;
    if (t.fr === rp.fr && t.to === rp.to) return path.slice();
    if (t.fr === rp.to && t.to === rp.fr) return path.slice().reverse();
    return null;
  }
  function dpSimplify(pts, tol) {
    const keep = new Uint8Array(pts.length);
    keep[0] = keep[pts.length - 1] = 1;
    const stack = [[0, pts.length - 1]];
    while (stack.length) {
      const [a, b] = stack.pop();
      let dmax = 0, idx = -1;
      const [ax, ay] = pts[a], [bx, by] = pts[b], dx = bx - ax, dy = by - ay, len = Math.hypot(dx, dy) || 1e-9;
      for (let i = a + 1; i < b; i++) {
        const d = Math.abs(dy * pts[i][0] - dx * pts[i][1] + bx * ay - by * ax) / len;
        if (d > dmax) { dmax = d; idx = i; }
      }
      if (dmax > tol && idx > 0) { keep[idx] = 1; stack.push([a, idx], [idx, b]); }
    }
    return pts.filter((_, i) => keep[i]);
  }
  function simplify(pts) { // 画面の座標で 60 点以内にする（両端は残す）
    let tol = 0.2, out = pts;
    for (let i = 0; i < 24 && out.length > MAX_POINTS; i++) { out = dpSimplify(pts, tol); tol *= 1.6; }
    if (out.length > MAX_POINTS) {
      const step = (out.length - 1) / (MAX_POINTS - 1);
      out = Array.from({ length: MAX_POINTS }, (_, i) => out[Math.round(i * step)]);
    }
    return out;
  }
  function project(lonlat) {
    const lat0 = lonlat.reduce((s, p) => s + p[1], 0) / lonlat.length, k = Math.cos((lat0 * Math.PI) / 180);
    const raw = lonlat.map((p) => [p[0] * k, -p[1]]);
    const xs = raw.map((p) => p[0]), ys = raw.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const w = MAP.x1 - MAP.x0, hh = MAP.y1 - MAP.y0;
    const sc = Math.min(w / ((x1 - x0) || 1e-9), hh / ((y1 - y0) || 1e-9));
    const ox = MAP.x0 + (w - (x1 - x0) * sc) / 2, oy = MAP.y0 + (hh - (y1 - y0) * sc) / 2;
    return raw.map((p) => [+(ox + (p[0] - x0) * sc).toFixed(1), +(oy + (p[1] - y0) * sc).toFixed(1)]);
  }

  /* ---------- 部品の絵 */
  // 出発地と到着地の絵。どちらも正面から見た建物で、荷物の箱（オレンジ）と同じ色の差し色を使う。画像は使わず SVG だけで描く
  // 出発地（自社の車庫・営業所）: 切妻の屋根、大きなシャッターの扉、旗。到着地（荷主の倉庫）: 低い平屋根、並んだ荷役の扉、積み荷の箱
  function warehouse(cx, cy, role) {
    const g = el('g', { class: 'tv-wh', 'data-symbol': 'warehouse', 'data-role': role || 'dest', transform: `translate(${cx - 26},${cy - 20})` });
    g.appendChild(el('rect', { class: 'tv-wh-apron', x: -4, y: 31, width: 60, height: 8, rx: 2 }));
    if (role === 'origin') {
      g.appendChild(el('rect', { class: 'tv-wh-roof', x: 2, y: 12, width: 48, height: 20, rx: 1.5 }));
      g.appendChild(el('polygon', { class: 'tv-wh-gable', points: '-2,13 26,0 54,13' }));
      g.appendChild(el('rect', { class: 'tv-wh-door', x: 11, y: 17, width: 30, height: 15, rx: 1.5 }));
      for (let i = 0; i < 3; i++) g.appendChild(el('line', { class: 'tv-wh-slat', x1: 11, y1: 21 + i * 4, x2: 41, y2: 21 + i * 4 }));
      g.appendChild(el('line', { class: 'tv-wh-pole', x1: 26, y1: 0, x2: 26, y2: -9 }));
      g.appendChild(el('polygon', { class: 'tv-wh-flag', points: '26,-9 35,-6 26,-3' }));
    } else {
      g.appendChild(el('rect', { class: 'tv-wh-roof', x: 0, y: 8, width: 52, height: 24, rx: 1.5 }));
      g.appendChild(el('rect', { class: 'tv-wh-cap', x: -2, y: 5, width: 56, height: 5, rx: 1.5 }));
      for (let i = 0; i < 3; i++) {
        g.appendChild(el('rect', { class: 'tv-wh-door', x: 5 + i * 15, y: 15, width: 11, height: 17, rx: 1 }));
        g.appendChild(el('line', { class: 'tv-wh-slat', x1: 5 + i * 15, y1: 21, x2: 16 + i * 15, y2: 21 }));
      }
      g.appendChild(el('rect', { class: 'tv-wh-crate', x: 40, y: 25, width: 7, height: 7, rx: 1 }));
    }
    return g;
  }
  function placeLabel(g, cx, cy, role, name, above) {
    const anchor = cx < 74 ? 'start' : cx > 286 ? 'end' : 'middle';
    const tx = anchor === 'start' ? Math.max(cx - 26, 6) : anchor === 'end' ? Math.min(cx + 26, VW - 6) : cx;
    const y = above ? cy - 36 : cy + 40;
    g.appendChild(el('text', { class: 'tv-role', x: tx, y, 'text-anchor': anchor }, role));
    g.appendChild(el('text', { class: 'tv-place', x: tx, y: y + 13, 'text-anchor': anchor }, name.length > 12 ? name.slice(0, 11) + '…' : name));
  }
  function cargoGlyph(x, y, kind) { // 色を外しても形で分かる: 箱=荷物あり、点線の枠=空、丸に ？=不明
    const g = el('g', { class: 'tv-cargo', 'data-shape': kind, transform: `translate(${x},${y})` });
    if (kind === 'loaded') {
      g.appendChild(el('rect', { class: 'tv-box', x: 0, y: 0, width: 22, height: 22, rx: 2 }));
      g.appendChild(el('line', { class: 'tv-box-band', x1: 11, y1: 0, x2: 11, y2: 22 }));
      g.appendChild(el('line', { class: 'tv-box-band', x1: 0, y1: 11, x2: 22, y2: 11 }));
    } else if (kind === 'empty') {
      g.appendChild(el('rect', { class: 'tv-frame', x: 1, y: 1, width: 20, height: 20, rx: 2 }));
    } else {
      g.appendChild(el('circle', { class: 'tv-unk', cx: 11, cy: 11, r: 10.5 }));
      g.appendChild(el('text', { class: 'tv-unk-q', x: 11, y: 16.5, 'text-anchor': 'middle' }, '？'));
    }
    return g;
  }
  function vehicleSymbol(x, y, cls, ctx) {
    const g = el('g', { class: 'tv-veh', transform: `translate(${x},${y})` });
    const imgs = Object.assign({}, VEHICLE_IMAGES, (ctx && ctx.vehicleImages) || {});
    const base = (ctx && ctx.imgBase) || IMG_BASE;
    const generic = (label, known) => {
      const gg = el('g', { class: 'tv-veh-generic', 'data-symbol': known ? 'class-only' : 'generic' });
      gg.appendChild(el('rect', { class: 'tv-veh-body', x: 0, y: 6, width: 84, height: 40, rx: 10 }));
      gg.appendChild(el('text', { class: 'tv-veh-text', x: 42, y: 32, 'text-anchor': 'middle' }, label));
      return gg;
    };
    if (!cls) { g.setAttribute('data-vehicle', 'unknown'); g.appendChild(generic('車種不明', false)); return g; }
    g.setAttribute('data-vehicle', cls);
    const file = imgs[cls];
    if (!file) { g.appendChild(generic(cls, true)); return g; }
    const img = el('image', { class: 'tv-veh-img', href: /^(https?:|\/|data:)/.test(file) ? file : base + file, x: 0, y: 0, width: 90, height: 56, preserveAspectRatio: 'xMidYMid meet' });
    img.addEventListener('error', () => { if (img.parentNode) { g.replaceChild(generic(cls, true), img); g.setAttribute('data-image', 'failed'); } });
    g.appendChild(img);
    return g;
  }
  function drawChevrons(g, pts) {
    const seg = []; let total = 0;
    for (let i = 1; i < pts.length; i++) { const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); seg.push(d); total += d; }
    if (total < 1) return;
    const n = Math.max(2, Math.min(6, Math.floor(total / 46)));
    for (let k = 1; k <= n; k++) {
      let want = (total * k) / (n + 1), i = 0;
      while (i < seg.length - 1 && want > seg[i]) { want -= seg[i]; i++; }
      const t = seg[i] ? want / seg[i] : 0, a = pts[i], b = pts[i + 1];
      const x = a[0] + (b[0] - a[0]) * t, y = a[1] + (b[1] - a[1]) * t, ang = (Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI;
      g.appendChild(el('polyline', { class: 'tv-chev', points: '-3,-3.6 1.6,0 -3,3.6', transform: `translate(${x.toFixed(1)},${y.toFixed(1)}) rotate(${ang.toFixed(1)})` }));
    }
  }

  /* ---------- 組み立て */
  function placeName(ctx, pid) {
    if (!pid) return '地点不明';
    const p = ((ctx && ctx.places) || {})[pid];
    return p ? (p.short || p.label || p.pseudo || pid) : pid;
  }
  function render(container, leg, ctx) {
    destroy(container);
    ctx = ctx || {};
    const t = normalize(leg || {}, ctx);
    const route = routeFor(t, ctx);
    const wrap = h('div', 'tv'); wrap.setAttribute('data-tv', '1');
    wrap.setAttribute('data-route', route ? 'known' : 'schematic');
    wrap.setAttribute('data-kind', t.kind);
    const svg = el('svg', { class: 'tv-svg', viewBox: `0 0 ${VW} ${VH}`, role: 'img', preserveAspectRatio: 'xMidYMid meet' });
    const frName = placeName(ctx, t.fr), toName = placeName(ctx, t.to);
    svg.setAttribute('aria-label', `${frName}から${toName}への運行。${KIND_TEXT[t.kind]}。${route ? '道路の形は標準経路' : '出発と到着の位置だけを示しています'}`);
    svg.appendChild(el('rect', { class: 'tv-ground', x: 0, y: 0, width: VW, height: GROUND_H }));

    let a, b;
    if (route) {
      const pts = simplify(project(route));
      a = pts[0]; b = pts[pts.length - 1];
      const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0] + ' ' + p[1]).join(' ');
      svg.appendChild(el('path', { class: 'tv-road-edge', d }));
      svg.appendChild(el('path', { class: 'tv-road', d }));
      svg.appendChild(el('path', { class: 'tv-route', d, 'data-points': pts.length }));
      const cg = el('g', { class: 'tv-chevs' }); drawChevrons(cg, pts); svg.appendChild(cg);
    } else {
      a = [64, 92]; b = [296, 92];
      svg.appendChild(el('line', { class: 'tv-schem-line', x1: a[0] + 36, y1: a[1], x2: b[0] - 40, y2: b[1] }));
      svg.appendChild(el('polygon', { class: 'tv-schem-head', points: `${b[0] - 40},${b[1] - 6} ${b[0] - 28},${b[1]} ${b[0] - 40},${b[1] + 6}` }));
      svg.appendChild(el('text', { class: 'tv-schem-note', x: VW / 2, y: 28, 'text-anchor': 'middle' }, '出発と到着の位置だけを示しています'));
    }
    const near = Math.abs(a[0] - b[0]) < 110;
    svg.appendChild(warehouse(a[0], a[1], 'origin')); svg.appendChild(warehouse(b[0], b[1], 'dest'));
    const labels = el('g', { class: 'tv-labels' });
    placeLabel(labels, a[0], a[1], '出発', frName, false);
    placeLabel(labels, b[0], b[1], '到着', toName, near && b[1] >= a[1]);
    svg.appendChild(labels);
    if (route) svg.appendChild(el('text', { class: 'tv-cap', x: VW / 2, y: GROUND_H - 6, 'text-anchor': 'middle' }, '道はよく使われる道です。実際に走った道とは違うことがあります。'));

    // 下の帯: 車と荷の状態（走行中の位置を示すものではない）
    svg.appendChild(el('rect', { class: 'tv-band', x: 0, y: GROUND_H, width: VW, height: VH - GROUND_H }));
    svg.appendChild(vehicleSymbol(12, GROUND_H + 6, t.cls, ctx));
    const tag = el('g', { class: 'tv-state', 'data-kind': t.kind });
    tag.appendChild(el('rect', { class: 'tv-state-bg', x: 112, y: GROUND_H + 15, width: 150, height: 36, rx: 8 }));
    tag.appendChild(cargoGlyph(122, GROUND_H + 22, t.kind));
    tag.appendChild(el('text', { class: 'tv-state-text', x: 152, y: GROUND_H + 40 }, KIND_TEXT[t.kind]));
    svg.appendChild(tag);
    wrap.appendChild(svg);

    const dl = h('dl', 'tv-facts');
    const row = (k, v, cls) => { dl.appendChild(h('dt', null, k)); dl.appendChild(h('dd', cls || null, v)); };
    row('車', t.cls ? `${t.cls}の車` : '車種不明', 'tv-veh-row');
    row('日時', `${fmtWhen(t.when)}（${BASIS_TEXT[t.when.basis]}）`, 'tv-when');
    if (t.arr && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(t.arr)) row('到着', fmtWhen({ dt: t.arr.slice(0, 16) }), 'tv-arr');
    row('距離', t.km != null ? `${t.km.toLocaleString('ja-JP', { maximumFractionDigits: 1 })} km（${SRC_TEXT[t.kmSrc]}）` : '距離不明', 'tv-km');
    row('荷物あり・空車の元の記録', t.kind === 'unknown' ? '不明' : SRC_TEXT[t.kindSrc], 'tv-kindsrc');
    wrap.appendChild(dl);
    container.appendChild(wrap);
    return wrap;
  }
  function destroy(container) {
    if (!container) return;
    for (const n of Array.from(container.querySelectorAll(':scope > [data-tv]'))) n.remove();
  }

  root.TripView = { render, destroy, VEHICLE_IMAGES, simplify, project, MAX_POINTS };
})(typeof window !== 'undefined' ? window : globalThis);
