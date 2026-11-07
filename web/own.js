'use strict';
/* 自社の運行（SPEC §22 のサンプル、§35 の自社のファイル）。
   サンプルの数字・区間・気づきは tools/build_own_sample.py（src/shipcompare/own.py）が作った web/data/own_sample.json を表示する。
   自社の CSV を落としたときは、web/own_calc.js（OwnCalc.analyze。own.py と同じ結果になる JS）でこのページの中だけで計算し、同じ形で表示する。
   ファイルはブラウザの外に送らない（/api/ は呼ばない）。ここで計算するのは、地図の線の形、単価からの費用の式、書き出しの文字だけ。 */

const Own = (() => {
  // 荷待ちを入れる（SPEC §23）の状態。記録はこのページのメモリにだけあり、保存しない
  const W = { year: 2026, rows: [], res: null, err: '', tok: 0, tm: null, partner: '', pasteMsg: '', pasteNote: '', bulkMsg: '', idxMap: [], bad: new Set() };
  const O = {
    data: null, loaded: false, active: false, tab: 'import', market: false, dec: {}, fixTxt: {}, open: new Set(), geo: null, markers: [], dropMsg: '',
    sample: null, mode: 'sample', files: [], userAliases: {}, largeT: null, yearPick: null, cost: { perKm: '', perHour: '' }, lastCopy: '', copyMsg: '', munis: null, pairs: null, busy: false, pendingYear: null, pendingRoles: null, haishaUse: null, aiOwn: { avail: null, confirm: null, busy: false, msg: '', sugg: {} }, maxPayload: loadMaxPayload(),
    monthPick: null, board: null, tmr: null, fileOrder: null, prevHaishaUsed: null,  rate: { kind: 'km', value: '' }, cmpId: '', saveMsg: '', roles: {}, restoreOffer: null, glossOpen: false,
    guided: false, goal: '', advOpen: false, focus: null, focusKey: '', focusGeo: null, fxT0: 0,
  };
  const WD = '月火水木金土日';
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const q = (s) => document.querySelector(s);
  const pc = (v, d = 0) => (v == null ? '—' : (v * 100).toFixed(d) + '%');
  const n0 = (v) => Math.round(v).toLocaleString('ja-JP');
  const n1 = (v) => v.toLocaleString('ja-JP', { minimumFractionDigits: 1, maximumFractionDigits: 1 }); // 小数 1 桁（式の km と同じ丸め）
  const md2 = (iso) => (iso ? `${+iso.slice(5, 7)}/${+iso.slice(8, 10)}` : '');
  const ck = (c) => c.key || c.id; // 確認の決定の鍵（取り込んだ結果は key、サンプルは id）
  const COLOR = Kit.palette.legs.colors; // 色は web/kit/kit.js と共有（試験 test_v39_ui_contrast_kit.py）
  const DOT = { green: 'g', yellow: 'y', red: 'r', gray: 'n' };
  const KIND = Kit.palette.legs.labels;
  const NOT_YET = 'AI の案: まだありません';
  const isImport = () => O.mode === 'import';
  const claudeText = (c) => (c ? 'Claude の提案: ' + c.role : isImport() ? 'AI は使っていません' : NOT_YET);

  /* ------------------------------------------------------------ 読み込み */

  async function load() {
    if (O.loaded) return;
    try {
      O.rate = loadRate();
      O.sample = await getJSON('data/own_sample.json');
      O.data = O.sample;
      O.loaded = true;
      buildGeo();
    } catch (e) {
      console.error(e);
      O.err = e.message;
    }
  }

  /* ------------------------------------------------------------ 地図の線 */

  function offsetPath(path, d) {
    const out = [];
    for (let i = 0; i < path.length; i++) {
      const a = path[Math.max(0, i - 1)], b = path[Math.min(path.length - 1, i + 1)], k = Math.cos((path[i][1] * Math.PI) / 180);
      const dx = (b[0] - a[0]) * k, dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1;
      out.push([path[i][0] + ((-dy / L) * d) / k, path[i][1] + (dx / L) * d]);
    }
    return out;
  }
  // 道のりを一定の長さで区切って点線にする（座標は度のまま。見た目の長さは拡大で変わる）
  function dashes(path, dash, gap) {
    const out = [];
    let cur = [], on = true, left = dash;
    for (let i = 0; i < path.length - 1; i++) {
      let [x0, y0] = path[i];
      const [x1, y1] = path[i + 1], k = Math.cos((y0 * Math.PI) / 180);
      let seg = Math.hypot((x1 - x0) * k, y1 - y0);
      const ux = (x1 - x0) / (seg || 1), uy = (y1 - y0) / (seg || 1);
      if (on && !cur.length) cur.push([x0, y0]);
      while (seg > 1e-9) {
        const step = Math.min(left, seg);
        x0 += ux * step; y0 += uy * step; seg -= step; left -= step;
        if (on) cur.push([x0, y0]);
        if (left <= 1e-9) {
          if (on) { out.push(cur); cur = []; } else cur = [[x0, y0]];
          on = !on; left = on ? dash : gap;
        }
      }
    }
    if (on && cur.length > 1) out.push(cur);
    return out;
  }
  // 進む向きを示す「く」の字（山形）を、線の上の指定の位置に置く
  function chevrons(path, fracs, size, flip) {
    const out = [], n = path.length - 1;
    if (n < 1) return out;
    for (const f of fracs) {
      // 点が 2 つだけ（同じ県内の往復など）のときは、線の上の f の位置に、その線の向きで置く。止まらない
      let a, b, p;
      if (n < 2) { a = path[0]; b = path[1]; p = [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]; }
      else { const i = Math.max(1, Math.min(n - 1, Math.round(n * f))); a = path[i - 1]; b = path[i + 1]; p = path[i]; }
      const k = Math.cos((p[1] * Math.PI) / 180);
      let dx = (b[0] - a[0]) * k, dy = b[1] - a[1];
      const L = Math.hypot(dx, dy) || 1;
      dx /= L; dy /= L;
      if (flip) { dx = -dx; dy = -dy; }
      const tip = [p[0] + (dx * size * 0.6) / k, p[1] + dy * size * 0.6];
      const wing = (s) => {
        const c = Math.cos(s), sn = Math.sin(s), rx = dx * c - dy * sn, ry = dx * sn + dy * c;
        return [tip[0] - (rx * size) / k, tip[1] - ry * size];
      };
      out.push([wing(0.5), tip, wing(-0.5)]);
    }
    return out;
  }

  // 向きと種類ごとに、道路の形を少しずつ横へずらして並べる（重ならないように）。行きは北側、帰りは南側
  // 値は「内陸側への度数」（正が内陸、負が海側）。空車と不明の線は内陸側に置き、道路から離しすぎない（太平洋側の道では、海側に寄せると海の上に出る）
  const LANE = { out: { loaded: 0.04, unknown: 0.14, empty: 0.19 }, back: { loaded: -0.04, empty: 0.09, unknown: -0.1 } };
  // 道の進む向きから、offsetPath の正（進む向きの左）が内陸かどうかを決める。東向き・北向きは左が北・西で内陸、西向き・南向きは右が内陸（日本の太平洋側の道を想定した決め）
  function inlandSign(path) {
    const a = path[0], b = path[path.length - 1], dx = (b[0] - a[0]) * Math.cos((a[1] * Math.PI) / 180), dy = b[1] - a[1];
    return Math.abs(dx) >= Math.abs(dy) ? (dx >= 0 ? 1 : -1) : (dy >= 0 ? 1 : -1);
  }
  const laneOf = (path, v) => offsetPath(path, v * inlandSign(path));
  // 手順 2・3 の線は、画面の画素で間をあける（度で決めると、縮小した地図で実線と点線が重なる）。px は内陸側が正。いまの拡大と緯度から度に直す
  const LANE_PX = { back: { loaded: -8, empty: 8, unknown: -16 }, out: { loaded: 0 } };
  const pxToDeg = (px, lat) => (px * 40075016 * Math.cos((lat * Math.PI) / 180)) / (512 * Math.pow(2, (typeof map !== 'undefined' && map && map.getZoom ? map.getZoom() : 6)) * 111320);
  const lanePx = (path, px) => (px ? offsetPath(path, pxToDeg(px, path[Math.floor(path.length / 2)][1]) * inlandSign(path)) : path);
  // 地図はすべて真上から見る（pitch 0・bearing 0）。案内の手順でも傾けない
  const restView = () => ({ pitch: 0, bearing: 0 });
  const fxMs = (n) => (typeof MapFx !== 'undefined' ? MapFx.ms(n) : (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : n));

  function buildGeo() {
    const d = O.data;
    const groups = {}, other = {};
    // 主な往復（車庫 ⇄ 主な行き先）の組に当たる区間だけを、道路の形の線にまとめる。遠くの発地からの空車の帰り（名古屋 → 加古川など）は、
    // 向きが「帰り」でも別の線として、市区町村の代表点どうしを結ぶ
    const isMain = (l) => !l.frm || !l.to || (l.frm === d.base && l.to === d.dock) || (l.frm === d.dock && l.to === d.base);
    for (const l of d.legs) {
      if (l.dir && isMain(l)) { const k = `${l.dir}|${l.kind}`; groups[k] = (groups[k] || 0) + 1; }
      else if (l.frm && l.to && l.frm !== l.to) { const k = `${l.frm}|${l.to}|${l.kind}`; other[k] = (other[k] || 0) + 1; }
    }
    const items = [], marks = [], base = d.route && d.route.path;
    if (base && base.length > 1) {
      for (const [k, n] of Object.entries(groups)) {
        const [dir, kind] = k.split('|');
        const pth = laneOf(base, LANE[dir][kind]);
        const w = Math.min(9, 1.8 + 0.16 * n);
        const col = COLOR[kind];
        if (kind === 'empty') for (const s of dashes(pth, 0.14, 0.1)) items.push({ path: s, w, col, dir, kind, n });
        else items.push({ path: pth, w: kind === 'unknown' ? Math.max(1.5, w * 0.6) : w, col: kind === 'unknown' ? [col[0], col[1], col[2], 190] : col, dir, kind, n });
        if (kind === 'loaded') marks.push(...chevrons(pth, [0.28, 0.5, 0.72], 0.06, dir === 'back').map((p) => ({ path: p })));
      }
    }
    // 主な往復の組以外の区間は、市区町村の代表点どうしを直線で結ぶ（道路の形は持っていない）
    for (const [k, n] of Object.entries(other)) {
      const [a, b, kind] = k.split('|'), pa = d.places[a], pb = d.places[b];
      if (!pa || !pb || pa.lon == null || pb.lon == null) continue;
      const w = Math.min(6, 2.2 + 0.2 * n), col = COLOR[kind];
      const line = [[pa.lon, pa.lat], [pb.lon, pb.lat]];
      if (kind === 'empty') for (const s of dashes(line, 0.14, 0.1)) items.push({ path: s, w, col, dir: null, kind, n });
      else items.push({ path: line, w, col, dir: null, kind, n });
    }
    O.geo = { items, marks };
  }

  const placed = (d) => Object.values(d.places).filter((p) => p.lon != null && p.lat != null);

  const layers = () => [...baseLayers(), ...placeLayers()];
  function baseLayers() {
    if (O.guided && O.focus && O.focusGeo && O.tab !== 'wait') return focusLayers();
    if (O.guided && O.step === 1) return []; // 手順 1（データを選ぶ）では、まだ選んでいないデータの線と点を地図に出さない
    if (!O.geo || O.tab === 'wait') return []; // 荷待ちのタブでは、サンプルの運行の線と点は出さない（荷待ちの点だけを見せる）
    const L = deck;
    return [
      new L.PathLayer({
        id: 'o-path', data: O.geo.items, getPath: (x) => x.path, getWidth: (x) => x.w, getColor: (x) => x.col, widthUnits: 'pixels', capRounded: true, jointRounded: true,
        pickable: false, widthMinPixels: 1.2,
      }),
      new L.PathLayer({
        id: 'o-chev', data: O.geo.marks, getPath: (x) => x.path, getWidth: 2.4, getColor: [236, 248, 255, 235], widthUnits: 'pixels', capRounded: true, jointRounded: true, pickable: false,
      }),
      new L.ScatterplotLayer({
        id: 'o-pts', data: placed(O.data), getPosition: (p) => [p.lon, p.lat], getRadius: 7, radiusUnits: 'pixels', getFillColor: [255, 255, 255, 255],
        getLineColor: [92, 200, 255, 255], stroked: true, lineWidthUnits: 'pixels', getLineWidth: 3, pickable: false,
      }),
    ];
  }

  /* 手順 2・3 の地図: 選んだ区間の帰りだけを描く。実線＝荷物あり、点線＝空車。選んだ曜日の便の線を濃く、ほかの曜日はうすくする（0.28 秒でゆっくり切り替わる）。
     主な往復の道路の形が無い区間（遠くの発地からの帰りなど）は、2 つの地点を結ぶ線にする。 */
  const DIM = 0.28, FADE_MS = 280;
  function focusGeoOf(f) {
    const d = O.data, base = d.route && d.route.path, items = [], ids = new Set(f.ids), anyHl = f.wd != null && ids.size > 0, cnt = {};
    for (const r of f.rows) { if (r.kind === 'unknown') continue; const k = `${r.kind}|${!anyHl || ids.has(r.leg_id) ? 1 : 0}`; cnt[k] = (cnt[k] || 0) + 1; }
    const main = f.pair.fr === d.dock && f.pair.to === d.base && base && base.length > 1;
    const pa = d.places[f.pair.fr], pb = d.places[f.pair.to];
    const line = main ? base : pa && pb && pa.lon != null && pb.lon != null ? [[pa.lon, pa.lat], [pb.lon, pb.lat]] : null;
    if (!line) return [];
    for (const [k, n] of Object.entries(cnt)) {
      const [kind, hl] = k.split('|'), pth = lanePx(line, LANE_PX.back[kind]), col = COLOR[kind], w = hl === '1' ? Math.min(10, 4 + 0.6 * n) : 2.4;
      if (kind === 'empty') { const la = pth[Math.floor(pth.length / 2)][1]; for (const sg of dashes(pth, pxToDeg(10, la), pxToDeg(8, la))) items.push({ path: sg, w, col, hl: hl === '1' }); } // 点線の長さと間も画素で決める（縮小すると実線に見えるため）
      else items.push({ path: pth, w, col, hl: hl === '1' });
    }
    if (main) { const n = d.legs.filter((l) => l.dir === 'out' && l.kind === 'loaded').length; if (n) items.push({ path: lanePx(base, LANE_PX.out.loaded), w: 3, col: COLOR.loaded, hl: false }); }
    return items;
  }
  function setFocus(f) {
    const key = f ? [f.pair.key, f.wd, f.ids.length, O.mode, (O.data && O.data.legs.length) || 0].join('|') : '';
    if (key === O.focusKey) return;
    const pairChanged = (f && f.pair.key) !== (O.focus && O.focus.pair.key);
    O.focusKey = key; O.focus = f; O.focusGeo = f ? focusGeoOf(f) : null; O.fxT0 = performance.now();
    if (O.active && O.guided) { if (pairChanged) fit(true); try { map.triggerRepaint(); } catch (e) { /* 地図が無いとき */ } }
  }
  function focusLayers() {
    const p = Math.min(1, (performance.now() - O.fxT0) / FADE_MS), e = 1 - Math.pow(1 - p, 3);
    if (p < 1) requestAnimationFrame(() => { try { map.triggerRepaint(); } catch (x) { /* 地図が無いとき */ } });
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches, k = reduce ? 1 : e;
    const L = deck;
    const fp = O.step === 3 && O.focus ? O.data.places[O.focus.pair.fr] : null;
    return [
      ...(fp && fp.lon != null ? [new L.ScatterplotLayer({ id: 'o-start', data: [fp], getPosition: (q) => [q.lon, q.lat], getRadius: 16, radiusUnits: 'pixels', filled: false, stroked: true, getLineColor: [255, 176, 72, 255], lineWidthUnits: 'pixels', getLineWidth: 4, pickable: false })] : []),
      new L.PathLayer({
        id: 'o-focus', data: O.focusGeo, getPath: (x) => x.path, getWidth: (x) => x.w, getColor: (x) => { const a = x.col[3] == null ? 255 : x.col[3]; return [x.col[0], x.col[1], x.col[2], x.hl ? a * (DIM + (1 - DIM) * k) : a * DIM]; },
        widthUnits: 'pixels', capRounded: true, jointRounded: true, pickable: false, widthMinPixels: 1.2, updateTriggers: { getColor: [Math.round(k * 20)] },
      }),
      new L.ScatterplotLayer({
        id: 'o-pts', data: placed(O.data), getPosition: (q) => [q.lon, q.lat], getRadius: 7, radiusUnits: 'pixels', getFillColor: [255, 255, 255, 255],
        getLineColor: [92, 200, 255, 255], stroked: true, lineWidthUnits: 'pixels', getLineWidth: 3, pickable: false,
      }),
    ];
  }

  /* ------------------------------------------------------------ 地点の詳細（地図の地点の札を押すと小さなカードを出す） */

  /* 数字は OwnCalc.analyze の legs（frm・to・kind）と OwnCalc.legWd（出発の曜日）を数えるだけで、新しい規則は作らない。
     便数は frm か to がその地点の区間。空車で出た便は、frm がその地点で kind が empty の区間 */
  function placeInfo(id) {
    const d = O.data, p = d && d.places[id];
    if (!p) return null;
    const dep = d.legs.filter((l) => l.frm === id), arr = d.legs.filter((l) => l.to === id);
    const empty = dep.filter((l) => l.kind === 'empty');
    const wd = Array.from({ length: 7 }, () => ({ trips: 0, empty: 0 }));
    for (const l of dep) { const w = OwnCalc.legWd(l); if (w >= 0 && w < 7) { wd[w].trips++; if (l.kind === 'empty') wd[w].empty++; } }
    const dest = new Map();
    for (const l of dep) {
      if (!l.to || l.to === id) continue;
      const x = dest.get(l.to) || { id: l.to, loaded: 0, empty: 0, n: 0 };
      x.n++; if (l.kind === 'loaded') x.loaded++; else if (l.kind === 'empty') x.empty++;
      dest.set(l.to, x);
    }
    const top = [...dest.values()].sort((a, b) => b.n - a.n).slice(0, 2).map((x) => ({ ...x, name: d.places[x.id] ? placeName(d.places[x.id]) : '?' }));
    return { id, name: placeName(p), dep: dep.length, arr: arr.length, empty: empty.length, share: dep.length ? empty.length / dep.length : 0, wd, top };
  }
  const placeName = (p) => (isImport() || p.id === (O.data && O.data.base) ? p.short : p.pseudo || p.short);
  let placeCard = null;
  function ensurePlaceCard() {
    if (placeCard) return placeCard;
    placeCard = document.createElement('aside');
    placeCard.id = 'own-place-card'; placeCard.className = 'glass node-card'; placeCard.hidden = true;
    placeCard.setAttribute('aria-label', '地点の詳細');
    placeCard.addEventListener('click', (e) => { if (e.target.closest('.nc-close')) closePlace(); });
    document.body.appendChild(placeCard);
    addEventListener('keydown', (e) => { if (e.key === 'Escape' && O.placeSel != null) closePlace(); });
    return placeCard;
  }
  function openPlace(id) {
    const d = placeInfo(id);
    if (!d) return;
    const c = ensurePlaceCard(), mx = Math.max(1, ...d.wd.map((x) => x.empty));
    O.placeSel = id;
    const cells = d.wd.map((x, i) => `<li class="nc-wd${x.empty ? ' has' : ''}" style="--a:${(x.empty / mx).toFixed(2)}" title="${WD[i]}曜発 ${x.trips} 便のうち空車 ${x.empty} 便"><small>${WD[i]}</small><b class="num">${x.empty}</b></li>`).join('');
    const dest = d.top.map((x) => `<li><span class="nc-n">${esc(x.name)}</span><span class="num nc-v">実車 ${x.loaded} ／ 空車 ${x.empty}</span></li>`).join('');
    c.innerHTML = `<div class="nc-head"><b class="nc-title">${esc(d.name)}</b><button type="button" class="nc-close" aria-label="閉じる">×</button></div>`
      + `<dl class="nc-nums"><div><dt>発</dt><dd class="num">${d.dep} 便</dd></div><div><dt>着</dt><dd class="num">${d.arr} 便</dd></div><div><dt>空車で出た</dt><dd class="num">${d.empty} 便（${Math.round(d.share * 100)}%）</dd></div></dl>`
      + `<div class="nc-sub">曜日ごとの空車で出た便</div><ul class="nc-wds">${cells}</ul>`
      + `<div class="nc-sub">主な行き先</div><ul class="nc-list">${dest || '<li>行き先の記録がありません</li>'}</ul>`;
    c.hidden = false;
    const rt = q('#own-right'), shown = rt && rt.getClientRects().length && getComputedStyle(rt).position === 'fixed' && innerWidth > 1100;
    c.style.right = shown ? `${Math.round(innerWidth - rt.getBoundingClientRect().left + 16)}px` : '';
    O.placeGeo = null;
    c.querySelector('.nc-close').focus({ preventScroll: true });
    try { map.triggerRepaint(); } catch (e) { /* 地図が無いとき */ }
  }
  function closePlace() {
    if (O.placeSel == null) return;
    O.placeSel = null; O.placeGeo = null;
    if (placeCard) placeCard.hidden = true;
    try { map.triggerRepaint(); } catch (e) { /* 地図が無いとき */ }
  }
  // 選んだ地点を出る・着く区間を、地点どうしを結ぶ線で強調する（実車は青の実線、空車は橙の点線）
  function placeLayers() {
    const id = O.placeSel, d = O.data;
    if (id == null || !d || !d.places[id] || !O.active) return [];
    if (!O.placeGeo) {
      const cnt = new Map();
      for (const l of d.legs) {
        if (!l.frm || !l.to || l.frm === l.to || (l.frm !== id && l.to !== id)) continue;
        const a = d.places[l.frm], b = d.places[l.to];
        if (!a || !b || a.lon == null || b.lon == null) continue;
        const k = `${l.frm}|${l.to}|${l.kind}`; cnt.set(k, (cnt.get(k) || 0) + 1);
      }
      const items = [];
      for (const [k, n] of cnt) {
        const [f, t, kind] = k.split('|'), a = d.places[f], b = d.places[t], pth = arcPath([a.lon, a.lat], [b.lon, b.lat], 0.1, 40), col = COLOR[kind] || COLOR.unknown || [150, 160, 176, 255], w = Math.min(9, 3 + 0.6 * n);
        if (!Rt.pathOk('truck', pth)) { const la = pth[Math.floor(pth.length / 2)][1]; for (const sg of dashes(pth, pxToDeg(5, la), pxToDeg(7, la))) items.push({ path: sg, w: 2, col: [168, 176, 190, 170] }); }   // 弧が海をまたぐとき（実際の道ではない）は、細い点線で経路不明を示す
        else if (kind === 'empty') { const la = pth[Math.floor(pth.length / 2)][1]; for (const sg of dashes(pth, pxToDeg(10, la), pxToDeg(8, la))) items.push({ path: sg, w, col }); }
        else items.push({ path: pth, w, col });
      }
      O.placeGeo = { items, pt: [d.places[id]] };
    }
    const L = deck;
    return [
      new L.PathLayer({ id: 'o-place-hl', data: O.placeGeo.items, getPath: (x) => x.path, getWidth: (x) => x.w, getColor: (x) => x.col, widthUnits: 'pixels', capRounded: true, jointRounded: true, pickable: false, widthMinPixels: 2 }),
      new L.ScatterplotLayer({ id: 'o-place-ring', data: O.placeGeo.pt, getPosition: (q) => [q.lon, q.lat], getRadius: 13, radiusUnits: 'pixels', filled: false, stroked: true, getLineColor: [255, 255, 255, 255], lineWidthUnits: 'pixels', getLineWidth: 3, pickable: false }),
    ];
  }

  function clearMarkers() {
    for (const m of O.markers) { m.mk.remove(); }
    O.markers = [];
  }

  function syncMarkers() {
    if (!O.loaded || O.noMap) return;
    if (!O.markers.length) {
      for (const p of placed(O.data)) {
        const el = document.createElement('div');
        el.className = 'own-tag';
        el.innerHTML = p.id === O.data.base
          ? `<b>${esc(p.short)}</b><small>自社の車庫</small>`
          : isImport() ? `<b>${esc(p.short)}</b><small>${esc(p.label)}</small>` : `<b>${esc(p.pseudo)}</b><small>${esc(p.label)}</small>`;
        el.classList.add('pick'); el.tabIndex = 0; el.setAttribute('role', 'button'); el.setAttribute('aria-label', `${p.short} の詳細を開く`);
        el.addEventListener('click', (e) => { e.stopPropagation(); openPlace(p.id); });
        el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPlace(p.id); } });
        O.markers.push({ el, mk: new maplibregl.Marker({ element: el, anchor: 'bottom', offset: [0, -12] }).setLngLat([p.lon, p.lat]).addTo(map) });
      }
    }
    const hide = O.guided && O.step === 1; // 手順 1（データを選ぶ）では、まだ選んでいないサンプルの地名を地図に出さない
    for (const m of O.markers) m.el.style.display = O.active && O.tab !== 'wait' && !hide ? '' : 'none';
  }

  /* ------------------------------------------------------------ 左: タブ */

  // 市区町村の名前に当たらなかった地点。線にせず、ここに並べる。市区町村の名前を入れると、別名として覚えて計算し直す
  // 同じ地名の施設（辰巳倉庫・辰巳DC3）を 1 つの地点にまとめたこと。まとめない選択もできる（SPEC §47）
  function mergedHtml(d) {
    const m = d.merged_places || [];
    if (!m.length && O.mergePlaces !== false) return '';
    return `<p class="o-hint" id="own-merged">${m.length ? `同じ地名の施設を 1 つの地点にまとめました: ${m.map((x) => `${esc(x.short)}（${x.names.map(esc).join('・')}）`).join('、')}。` : '地点のまとめを止めています。'}
      <button type="button" class="btn-link" data-act="merge-toggle">${O.mergePlaces === false ? '同じ地名の施設をまとめる' : 'まとめない'}</button></p>`;
  }
  function unplacedHtml(d) {
    if (!isImport()) return '';
    const u = d.unplaced || [];
    if (!u.length) return mergedHtml(d) + '<div class="o-h">地図に置けない地点</div><p class="o-hint">地図に置けない地点はありません</p>';
    const rows = u.map((x) => `<div class="o-up" data-name="${esc(x.name)}"><span class="o-upn"><b>${esc(x.name)}</b><small>${x.count} 回出てきます・${esc(x.reason || '')}</small></span>
      <input class="ctl sm" type="text" placeholder="市区町村名（例: 江東区）" aria-label="${esc(x.name)} の市区町村名"><button type="button" class="btn-sm" data-act="alias">地図に置く</button></div>`).join('');
    return `${mergedHtml(d)}<div class="o-h">地図に置けない地点 ${u.length} か所</div>
      <p class="o-hint">地名の先頭が市区町村の名前に当たらなかった地点です。線にはしていません（実車か空車かの判定には使っています）。市区町村名を入れると、その名前の別名として覚えて計算し直します。</p>${rows}`;
  }

  function counts() {
    const d = O.data;
    const pending = d.confirm.filter((c) => !O.dec[ck(c)]).length;
    return { pending, ins: d.insights.length };
  }

  function dotCls(c) { return `o-dot ${DOT[c] || 'n'}`; }

  // 配車表の年。見出しの曜日に合う年が複数あるときと、曜日が無いときに、選ばせる
  function yearHtml(d) {
    const yc = d.year_choice;
    if (isImport() && d.year_assumed && !(yc && yc.ambiguous)) { // 年を決める手がかりが無く、年を仮に置いた（own_calc.js の year_assumed）
      const lo = (OwnCalc.YEAR_RANGE || [d.year - 2, d.year + 1]), ys = [];
      for (let y = Math.max(lo[0], d.year - 3); y <= Math.min(lo[1], d.year + 2); y++) ys.push(y);
      return `<div class="o-year o-assumed"><label>${d.year} 年としました（変えられます） <select id="own-year" class="ctl sm" aria-label="配車表の年">${ys.map((y) => `<option value="${y}" ${y === d.year ? 'selected' : ''}>${y} 年</option>`).join('')}</select></label>
        <small>見出しに年も曜日も無いので、年を決められませんでした。違うときは選び直してください。</small></div>`;
    }
    if (!isImport() || !yc || !yc.ambiguous) return '';
    const why = yc.source === 'weekday' ? '見出しの曜日に合う年が複数あります' : yc.source === 'mismatch' ? '見出しの曜日がどの年にも合いません' : '見出しに曜日がありません';
    const opts = yc.candidates.map((y) => `<option value="${y}" ${y === d.year ? 'selected' : ''}>${y} 年</option>`).join('');
    return `<div class="o-year"><label>配車表の年 <select id="own-year" class="ctl sm" aria-label="配車表の年">${opts}</select></label>
      <small>${why}。いまは ${d.year} 年として計算しています。違うときは選び直してください（今日の日付では決めていません）。</small></div>`;
  }

  // 合う年が複数あって、まだ計算していない配車表。選ぶまで計算しない（いちばん新しい年を黙って使わない）
  function pendingYearHtml() {
    const p = O.pendingYear;
    if (!p) return '';
    const opts = '<option value="">年を選ぶ</option>' + p.candidates.map((y) => `<option value="${y}">${y} 年</option>`).join('');
    return `<div class="o-year o-pend" role="alert"><b>配車表の年を選んでください。選ぶまで計算しません</b>
      <label>配車表の年 <select id="own-year-pick" class="ctl sm" aria-label="配車表の年を選ぶ">${opts}</select></label>
      <button type="button" class="btn-link" data-act="year-cancel">このファイルを読むのをやめる</button>
      <small>見出しの曜日に合う年が複数あります（${p.candidates.map((y) => y + ' 年').join('・')}）。年を黙って決めないため、選んでもらいます（今日の日付では決めていません）。</small></div>`;
  }

  // 列の役割の日本語の名前。英語の役割名は出さない
  const colLabel = (c) => c.role_label_ja || c.dict_label || (c.shape_best_role && OwnCalc.ROLE_LABEL && OwnCalc.ROLE_LABEL[c.shape_best_role]) || '';

  const ROLE_OPTS = [['date', '日付'], ['plate', '車番'], ['from', '荷を積む場所（発地）'], ['to', '荷を降ろす場所（着地）'], ['weight', '重さ'], ['skip', '使わない']];
  // OwnCalc.analyze が返す、役割を選べる列（column_roles_needed）。無ければ空で、今までどおり
  const roleNeeds = (d) => (Array.isArray(d.column_roles_needed) ? d.column_roles_needed : []);
  // 選んだ役割は、配車表・デジタコ・どちらか分からないファイルの別に分けて OwnCalc.analyze の column_roles（{ haisha, digitaco }）に渡す
  const roleGroup = (source) => (source === '配車表' ? 'haisha' : source === 'デジタコ' ? 'digitaco' : 'both');
  function rolesForCalc() {
    const r = O.roles, out = { haisha: { ...(r.haisha || {}), ...(r.both || {}) }, digitaco: { ...(r.digitaco || {}), ...(r.both || {}) } };
    return Object.keys(out.haisha).length || Object.keys(out.digitaco).length ? out : undefined;
  }
  /* ------------------------------------------------------------ 取り込みの案内と見本の CSV、AI による列の整理（SPEC §53.1） */

  // 見本の CSV（架空）。web/data/own_sample.json の元にした data/own_sample/raw の配車表と同じ地名・車番の書き方で、OwnCalc の辞書が読む見出しを使う
  const SAMPLE_CSV = ['日付,車番,車種,発地,着地,着時刻,重さ,実空',
    '2026/10/01,鹿児島 100 か 9001,大型,鹿屋,江東,05:30,4.6t,',
    '2026/10/02,鹿児島 100 か 9001,大型,江東,鹿屋,04:30,,空',
    '2026/10/03,鹿児島 100 か 9001,大型,鹿屋,江東,05:30,3.2t,',
    '2026/10/04,鹿児島 100 か 9001,大型,江東,鹿屋,04:30,,空',
    '2026/10/01,鹿児島 100 か 9002,大型,鹿屋,辰巳,05:30,3.8t,',
    '2026/10/02,鹿児島 100 か 9002,大型,辰巳,鹿屋,04:00,2.9t,'].join('\r\n') + '\r\n';
  function saveSampleCsv() {
    const blob = new Blob(['﻿' + SAMPLE_CSV], { type: 'text/csv;charset=utf-8' }); // BOM つき UTF-8（Excel でも文字化けしない）
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = '見本_配車表.csv';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    toast('見本の CSV を保存しました（中身は架空です）');
  }
  // 最低限の列は own_calc.js の判定から: 配車表の縦の形は 日付・車番・発地か着地、横の形は 車番と 10/1 のような日付の見出し。デジタコは 開始と終了の日時を含む 3 列以上、または 日付と実車・空車の距離
  function formatGuideHtml() {
    return `<div class="o-fmt" id="own-fmt"><p class="o-hint">読めるのは、配車表・デジタコ・運行日報の CSV です。Excel のファイルは「CSV として保存」してください。文字コードは UTF-8 でも Shift_JIS でも読めます。</p>
      <p class="o-hint">最低限ほしい列は、配車表なら「日付・車番・発地か着地」（日付が横に並ぶ表なら「車番」と 10/1 のような日付の見出し）、デジタコなら「開始と終了の日時を含む 3 列以上」か「日付と実車・空車の距離」です。着時刻・重さがあれば使います。</p>
      <button type="button" class="btn-link" data-act="sample-csv">見本の CSV を保存</button></div>`;
  }

  // 値の形（決まった語彙のどれか）。値そのものは外に出さない。語彙は src/shipcompare/ai_explain.py の SHAPE_LABELS と同じ
  const SHAPE_JA = {
    date_ymd: 'YYYY/MM/DD の日付', date_md: 'M/D の日付', datetime: 'YYYY-MM-DD HH:MM の日時', time_hm: 'HH:MM の時刻', int_1_2: '整数 1〜2 桁', int_3_4: '整数 3〜4 桁',
    int_5p: '整数 5 桁以上', decimal: '小数まじりの数', number_unit: '数 + 単位', plate_like: '車番らしい文字列', place_like: '漢字・かなの名称', code_like: '英数字の符号', other_text: 'そのほかの文字列',
  };
  function valueShape(v) {
    const s = String(v).normalize('NFKC').trim();
    if (/^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}[ T]\d{1,2}:\d{2}(:\d{2})?$/.test(s)) return 'datetime';
    if (/^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$/.test(s)) return 'date_ymd';
    if (/^\d{1,2}[/月]\d{1,2}日?(\(.\))?$/.test(s)) return 'date_md';
    if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(s)) return 'time_hm';
    if (/^\d+$/.test(s)) return s.length <= 2 ? 'int_1_2' : s.length <= 4 ? 'int_3_4' : 'int_5p';
    if (/^\d+\.\d+$/.test(s)) return 'decimal';
    if (/^\d+(\.\d+)?\s*[A-Za-zぁ-んァ-ヶ一-鿿]{1,3}$/.test(s)) return 'number_unit';
    if (OwnCalc.normalizePlate && OwnCalc.normalizePlate(s) != null) return 'plate_like';
    if (/^[぀-ヿ一-鿿々ー・\s]+$/.test(s)) return 'place_like';
    if (/^[A-Za-z0-9_-]{2,}$/.test(s)) return 'code_like';
    return 'other_text';
  }
  // 見出しの欄に、見出しではなく値が入っているとき（見出しのない CSV）は、見出しを送らず「列 N」にする
  function safeHeader(h, i) {
    const s = String(h || '').normalize('NFKC').trim();
    if (!s || s.length > 20 || /\d{4,}/.test(s) || valueShape(s) !== 'other_text' && valueShape(s) !== 'place_like' && valueShape(s) !== 'code_like') return `列 ${i + 1}`;
    return s;
  }
  // 列 x の値（そのファイルの全行）から、値の形の要約だけを作る。この関数が返す物に値は入らない
  function columnShape(x, files) {
    const vals = [];
    for (const name of (x.files && x.files.length ? x.files : [x.file])) {
      const f = files.find((y) => y.name === name);
      if (!f) continue;
      let rows;
      try { rows = OwnCalc.parseCsv(f.text); } catch (e) { continue; }
      let hi = -1, j = -1;
      for (let i = 0; i < Math.min(rows.length, 40) && j < 0; i++) { const k = rows[i].findIndex((c) => String(c || '').trim() === x.column); if (k >= 0) { hi = i; j = k; } }
      if (j < 0) continue;
      for (const r of rows.slice(hi + 1)) vals.push(j < r.length ? String(r[j] || '') : '');
    }
    const filled = vals.map((v) => v.trim()).filter((v) => v);
    const cnt = {};
    for (const v of filled) { const k = valueShape(v); cnt[k] = (cnt[k] || 0) + 1; }
    const classes = {};
    Object.entries(cnt).sort((a, b) => b[1] - a[1]).slice(0, 5).forEach(([k, n]) => { classes[k] = Math.round((n / filled.length) * 100) / 100; });
    const lens = filled.map((v) => v.length);
    return { n: vals.length, fill: vals.length ? Math.round((filled.length / vals.length) * 100) / 100 : 0, distinct: new Set(filled).size, classes, len_min: lens.length ? Math.min(...lens) : 0, len_max: lens.length ? Math.max(...lens) : 0 };
  }
  const aiNeed = () => (O.pendingRoles ? O.pendingRoles.need : roleNeeds(O.data || {}));
  const aiFiles = () => (O.pendingRoles ? O.pendingRoles.files : O.files);
  const aiKey = (x) => `${x.source || ''}|${x.column}`;
  function buildAiPayload() {
    const need = aiNeed().slice(0, 40), files = aiFiles();
    return {
      keys: need.map(aiKey),
      payload: { columns: need.map((x, i) => ({ index: i, header: safeHeader(x.column, i), options: (x.options && x.options.length ? x.options.map((o) => o.role) : ROLE_OPTS.map((o) => o[0])), shape: columnShape(x, files) })) },
    };
  }
  const roleJa = (r) => (ROLE_OPTS.find((o) => o[0] === r) || [])[1] || (OwnCalc.ROLE_LABEL_JA && OwnCalc.ROLE_LABEL_JA[r]) || AI_ROLE_JA[r] || r;
  const AI_ROLE_JA = { type: '車種・トン数', time: '着く時刻', mark: '実空の印', kind: '走行か停車か', start_time: '開始の日時', end_time: '終了の日時', distance_km: '走った距離', loaded_flag: '実車か空車か', place_name: '停車した場所', driver_code: '運転者', loaded_km: '実車の距離', empty_km: '空車の距離', wait_min: '荷待ちの時間', restraint: '拘束時間' };

  function aiStatusInit() {
    const A = O.aiOwn;
    if (A.avail !== null) return;
    A.avail = false;
    if (typeof AiExplain === 'undefined' || !AiExplain.status) return;
    AiExplain.status().then((st) => { if (st && st.available) { A.avail = true; if (aiNeed().length) renderLeft(); } }).catch(() => {});
  }
  function aiConfirmHtml(c) {
    const cols = c.payload.columns, optsOf = (x) => x.options.map(roleJa).join('・');
    const same = cols.every((x) => optsOf(x) === optsOf(cols[0])); // 選べる役割が全列で同じなら、表の下に 1 回だけ出す
    const rows = cols.map((x) => `<tr><td>${esc(x.header)}</td><td>${Object.entries(x.shape.classes).map(([k, v]) => `${esc(SHAPE_JA[k] || k)} ${Math.round(v * 100)}%`).join('、') || '—'}</td>
      <td class="num">${Math.round(x.shape.fill * 100)}%</td><td class="num">${x.shape.distinct}</td>${same ? '' : `<td>${esc(optsOf(x))}</td>`}</tr>`).join('');
    return `<div class="o-aiconf" role="group" aria-label="AI に送る内容の確認"><b>この内容を Anthropic の API に送ります</b>
      <table class="o-tbl"><thead><tr><th>列の見出し</th><th>値の形（割合）</th><th>入力あり</th><th>種類の数</th>${same ? '' : '<th>選べる役割</th>'}</tr></thead><tbody>${rows}</tbody></table>
      ${same ? `<p class="o-hint">選べる役割（全列共通）: ${esc(optsOf(cols[0]))}</p>` : ''}
      <p class="o-hint">値そのものは送りません（車番・運転者・地点名・金額は出ません）。形の割合は、読み込んだ全行からこのブラウザの中で数えています。送るのは上の表だけで、AI の案を決めるのはあなたです。</p>
      <div class="bar"><button type="button" class="primary" data-act="ai-cols-send">送る</button><button type="button" data-act="ai-cols-cancel">やめる</button></div></div>`;
  }
  function aiBoxHtml(need) {
    const A = O.aiOwn;
    if (!A.avail && !A.msg) return '';
    const open = Object.keys(A.sugg).some((k) => need.some((x) => aiKey(x) === k));
    return `<div class="o-aibox" id="own-aibox">${A.avail ? `<button type="button" class="btn-link" data-act="ai-cols" ${A.busy || A.confirm ? 'disabled' : ''}>AI に列を整理してもらう</button><small class="o-hint"> 列の見出しと値の形だけを送ります（確認の画面が出ます）</small>` : ''}
      ${A.busy ? '<p class="o-hint" role="status">AI が列を整理しています…</p>' : ''}${A.msg ? `<p class="o-hint" role="status">${esc(A.msg)}</p>` : ''}${A.confirm ? aiConfirmHtml(A.confirm) : ''}
      ${open ? '<button type="button" class="btn-link" data-act="ai-apply-all">AI の案をすべて決める</button>' : ''}</div>`;
  }
  // 役割を 1 列ぶん決める（選んだ役割の記録と、見出しの形ごとの記憶）。計算し直しは呼び出し側が 1 回だけ行う
  function setRoleChoice(source, col, filesStr, value, pend) {
    const g = roleGroup(source);
    const grp = { ...((pend ? pend.roles : O.roles)[g] || {}) };
    if (value) grp[col] = value; else delete grp[col];
    const nr = { ...(pend ? pend.roles : O.roles), [g]: grp };
    if (pend) pend.roles = nr; else O.roles = nr;
    const fl = pend ? pend.files : O.files;
    for (const fname of (filesStr || '').split('|')) { // 同じ見出しの形のファイルを次に落としたときのために覚える
      const f = fl.find((x) => x.name === fname);
      if (!f) continue;
      const hs = OwnMonth.headSig(f.text), cur = OwnMonth.rolesGet(hs) || {};
      const gg = { ...(cur[g] || {}) };
      if (value) gg[col] = value; else delete gg[col];
      OwnMonth.rolesSet(hs, { ...cur, [g]: gg });
    }
  }
  const afterRoles = (pend, any) => { if (pend) { if (any) commitImport(pend.files, pend.bad, null, pend.roles); } else reanalyze(); };
  function aiApply(keys) {
    const pend = O.pendingRoles, need = aiNeed();
    let any = false;
    for (const k of keys) {
      const x = need.find((y) => aiKey(y) === k), s = O.aiOwn.sugg[k];
      if (!x || !s) continue;
      const files = (x.files && x.files.length ? x.files : [x.file]).filter((f) => f);
      setRoleChoice(x.source || '', x.column, files.join('|'), s.role, pend);
      delete O.aiOwn.sugg[k]; any = true;
    }
    if (any) afterRoles(pend, true);
  }
  function aiSend() {
    const A = O.aiOwn, c = A.confirm;
    if (!c) return;
    A.confirm = null; A.busy = true; A.msg = ''; renderLeft();
    AiExplain.ownColumns(c.payload).then((res) => {
      const need = aiNeed();
      let n = 0;
      for (const s of res.suggestions || []) {
        const key = c.keys[s.index], x = need.find((y) => aiKey(y) === key);
        const role = s.role === 'ignore' ? 'skip' : s.role;
        const ok = x && (x.options && x.options.length ? x.options.some((o) => o.role === role) : ROLE_OPTS.some((o) => o[0] === role));
        if (!ok) continue;
        A.sugg[key] = { role, confidence: s.confidence, reason: s.reason_ja }; n++;
      }
      A.busy = false; A.msg = n ? `AI の案が ${n} 列に付きました。内容を確かめて、決めてください（決めるまで計算は変わりません）` : 'AI は役割を決められませんでした。手で選んでください';
      renderLeft();
    }).catch((e) => { A.busy = false; A.msg = e.message || 'AI に整理してもらえませんでした。手で選んでください'; renderLeft(); });
  }

  (function injectImportStyle() {
    if (typeof document === 'undefined' || document.getElementById('own-fmt-style')) return;
    const st = document.createElement('style');
    st.id = 'own-fmt-style';
    st.textContent = '.o-fmt{margin:6px 0 8px}.o-fmt .o-hint{margin:2px 0}.o-aibox{margin:6px 0 8px}.o-aibox>small{display:block;margin:2px 0}.o-aiconf{margin:8px 0;padding:10px 12px;border:1px solid var(--line,#ccc);border-radius:10px;overflow-x:auto}' +
      '.o-aiconf .o-tbl{margin:6px 0}.o-aitag{color:var(--accent,#1b5fbf)}.o-aitag b{border:1px solid currentColor;border-radius:999px;padding:0 8px}';
    document.head.appendChild(st);
  })();

  // 役割を選ぶ欄の中身。need は column_roles_needed、roles は選んだ役割（{ haisha, digitaco, both }）
  function rolesRows(need, roles) {
    return need.map((x) => {
      const cur0 = ((roles[roleGroup(x.source)] || {})[x.column]) || '';
      const sg = !cur0 ? O.aiOwn.sugg[aiKey(x)] : null, cur = cur0 || (sg ? sg.role : '');
      const opts = [['', 'この列の中身を選ぶ']].concat(x.options && x.options.length ? x.options.map((o) => [o.role, o.label_ja]) : ROLE_OPTS);
      const why = x.reason_ja || (x.blocking ? 'この列の役割を決めないと、ファイルを読めません' : '列の名前から役割を決められませんでした。使うか、使わないかを選べます');
      const files = (x.files && x.files.length ? x.files : [x.file]).filter((f) => f);
      return `<div class="o-rl"><span class="o-rn"><b>${esc(x.column)}</b><small>${esc(x.source || '')}・${esc(files.join('、'))}</small>${x.samples && x.samples.length ? `<small>中身の例: ${esc(x.samples.slice(0, 3).join('、'))}</small>` : ''}<small>${esc(why)}</small>${x.suggested_label_ja ? `<small>候補: ${esc(x.suggested_label_ja)}</small>` : ''}${sg ? `<small class="o-aitag"><b>AI の案</b>: ${esc(roleJa(sg.role))}（${{ high: '確からしい', medium: 'たぶん', low: '自信なし' }[sg.confidence] || ''}）${esc(sg.reason)} <button type="button" class="btn-link" data-act="ai-apply" data-key="${esc(aiKey(x))}">この案で決める</button></small>` : ''}</span>
        <select class="ctl sm o-rsel" data-role-source="${esc(x.source || '')}" data-role-col="${esc(x.column)}" data-role-files="${esc(files.join('|'))}" aria-label="${esc(x.column)} の役割">${opts.map(([v, t]) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></div>`;
    }).join('');
  }
  function rolesHtml(d) {
    if (!isImport()) return '';
    const need = roleNeeds(d);
    if (!need.length) return '';
    return `<div class="o-roles" id="own-roles"><div class="o-h">役割を選んでほしい列 ${need.length} 本</div>
      <p class="o-hint">列の名前だけでは役割を決められなかった列です。選ぶと計算し直します。選んだ役割は、見出しの形が同じファイルを次に落としたときも使います（このブラウザの中だけに覚えます）。</p>${aiBoxHtml(need)}${rolesRows(need, O.roles)}</div>`;
  }
  // 区間を 1 件も読めなかったが、列の役割を選べば読めるかもしれないファイル。選ぶまで前の表示は変えない（O.pendingRoles）
  function pendingRolesHtml() {
    const p = O.pendingRoles;
    if (!p) return '';
    return `<div class="o-roles o-pend" id="own-roles-pend" role="alert"><div class="o-h">この列の中身を選んでください（${p.need.length} 本）。選ぶまで計算しません</div>
      <p class="o-hint">${esc(p.failed)}。列の名前だけでは役割を決められなかったためです。下の列の役割を選ぶと、読み直します。選んだ役割は、見出しの形が同じファイルを次に落としたときも使います（このブラウザの中だけに覚えます）。</p>
      ${aiBoxHtml(p.need)}${rolesRows(p.need, p.roles)}<button type="button" class="btn-link" data-act="roles-cancel">このファイルを読むのをやめる</button></div>`;
  }

  // 月ごとの保存と、前の月との並び（own_month.js）。ファイルの中身は保存しない
  // 2 か月以上を一緒に落としたときは月ごとの要約（months_detail）。最後の月を「いまの月」とし、比べるときは同じ月どうしにする
  const curSummaries = () => OwnMonth.summaries(O.data, OwnCalc.costs(O.data, '', ''));
  const curSummary = () => { const a = curSummaries(); return a[a.length - 1]; };
  function monthOthers() {
    if (!isImport()) return [];
    const mine = new Set(curSummaries().map((x) => x.month));
    return OwnMonth.list().filter((e) => !mine.has(e.month)); // 同じ月は 1 つにまとめる（配車表とデジタコを別々に落としても、同じ月どうしを「前の月」として並べない）
  }
  function cmpPair() {
    if (!O.cmpId) return null;
    if (isImport()) { const e = monthOthers().find((x) => x.month + ':' + x.sig === O.cmpId); return e ? [e.summary, curSummaries().find((x) => x.month === e.month) || curSummary()] : null; }
    const all = OwnMonth.list();
    return O.cmpId === 'saved' && all.length >= 2 ? [all[all.length - 2].summary, all[all.length - 1].summary] : null;
  }
  function cmpBlock(print) {
    const pr = cmpPair();
    if (!pr) return '';
    const rows = OwnMonth.compareRows(pr[0], pr[1]).map((r) => `<tr><th>${esc(r.name)}</th><td class="num">${esc(r.a)}</td><td class="num">${esc(r.b)}</td><td class="num o-df">${esc(r.diff)}</td></tr>`).join('');
    return `${print ? '<h3>前の月と並べる</h3>' : ''}<table class="o-cmp" aria-label="前の月との比べ"><thead><tr><th></th><th>${esc(pr[0].label)}</th><th>${esc(pr[1].label)}</th><th>差</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="o-hint">差は引き算だけです。良くなったか悪くなったかは、月の仕事の量や行き先で変わるので、決めていません。</p>`;
  }
  function monthHtml() {
    const all = OwnMonth.list();
    let h = '';
    if (isImport()) {
      if (O.saveMsg) h += `<p class="o-hint o-save">${esc(O.saveMsg)}</p>`;
      const ro = O.restoreOffer;
      if (ro) h += `<div class="o-year o-restore"><b>前に保存した、同じファイルの確認の決定と地名の対応があります</b><button type="button" class="btn-sm" data-act="month-restore">戻す</button><small>確認の決定 ${Object.keys(ro.dec || {}).length} 件・地名の対応 ${Object.keys(ro.aliases || {}).length} 件（${esc(ro.summary ? ro.summary.label : '')}）</small></div>`;
      const others = monthOthers();
      if (others.length) {
        const cur = curSummary(), prevs = others.filter((e) => e.month < cur.month), def = (prevs.length ? prevs[prevs.length - 1] : others[others.length - 1]);
        const pick = O.cmpId || def.month + ':' + def.sig;
        h += `<div class="o-cmpbar"><button type="button" class="btn-sm" data-act="month-cmp">前の月と並べる</button>
          <select id="own-cmp" class="ctl sm" aria-label="並べる月">${others.map((e) => `<option value="${esc(e.month + ':' + e.sig)}" ${e.month + ':' + e.sig === pick ? 'selected' : ''}>${esc(e.summary.label)}（${esc((e.savedAt || '').slice(0, 10))} に保存）</option>`).join('')}</select></div>`;
      }
    } else if (all.length) {
      h += `<p class="o-hint o-save">このブラウザに保存した月が ${all.length} 件あります（${esc(all.map((e) => e.summary.label).join('、'))}）。同じ形のファイルを落とすと、前の月と並べられます。</p>`;
      if (all.length >= 2) h += '<div class="o-cmpbar"><button type="button" class="btn-sm" data-act="month-cmp-saved">保存した最新の 2 つの月を並べる</button></div>';
    }
    h += cmpBlock(false);
    if (all.length) h += `<div class="o-cmpbar"><button type="button" class="btn-link" data-act="month-clear">保存したデータをすべて消す（${all.length} 件）</button></div>`;
    if (isImport() || all.length) h += '<p class="o-note">保存するのは、数字の要約・地名の対応・確認の決定・列の役割だけです。CSV の中身は保存しません。このブラウザの中だけにあり、上のボタンで消せます。</p>';
    return h ? `<div class="o-month" id="own-month"><div class="o-h">月ごとの保存</div>${h}</div>` : '';
  }

  // 日の数字だけの見出し（1〜31）で月が決まらないとき: 月を選んでもらい、選んだ月で計算し直す
  function monthPickHtml(d) {
    if (!isImport() || !d.needs_month) return '';
    const cs = (d.month_choice && d.month_choice.candidates) || [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
    return `<div class="o-year o-month-pick" role="alert"><label>配車表の月 <select id="own-month-pick" class="ctl sm" aria-label="配車表の月">${'<option value="">月を選ぶ</option>' + cs.map((m) => `<option value="${m}" ${O.monthPick === m ? 'selected' : ''}>${m} 月</option>`).join('')}</select></label>
      <small>見出しが日の数字だけで、月が分かりません。いまは ${d.mon} 月として計算しています。違うときは選んでください。</small></div>`;
  }
  // 同じデジタコの二重読みなどの注意（own_calc.js の notices）
  function noticesHtml(d) {
    const n = (d.notices || []).filter((x) => x && x.text_ja);
    return n.length ? `<div class="o-notices">${n.map((x) => `<p class="o-hint">${esc(x.text_ja)}</p>`).join('')}</div>` : '';
  }

  // 傭車の行（自社の車として数えていない行）。1 行の知らせと、閉じた「詳しく」の一覧
  function hiredHtml(d) {
    const h = Array.isArray(d.hired) ? d.hired : [];
    if (!isImport() || !h.length) return '';
    const days = (d.counts && d.counts.hired_cells) != null ? d.counts.hired_cells : h.reduce((a, x) => a + (x.cells || 0), 0);
    const rows = h.map((x) => `<li>${esc(x.name)}　${x.cells} 日分・区間 ${x.legs} 件・${esc(x.file || '')}${x.line != null ? ` ${x.line} 行目` : ''}</li>`).join('');
    return `<div class="o-hired" id="own-hired"><p class="o-hint">傭車の行 ${days} 日分は自社の車として数えていません</p>
      <details class="o-more2"><summary>詳しく</summary><ul>${rows}</ul><small>傭車は他社の車なので、実車率や空車 km に入れていません。</small></details></div>`;
  }

  // 重なる配車表（同じ車番・日付が複数のファイルにある）。使うファイルを選べる
  function haishaChoiceHtml(d) {
    const hc = d.haisha_choice;
    if (!isImport() || !hc || !(hc.overlaps || []).length) return '';
    const files = hc.files || [], used = new Set(hc.used || []);
    const rows = files.map((f) => `<li><label><input type="checkbox" data-hf="${esc(f.file)}" ${used.has(f.file) || f.used ? 'checked' : ''}> <b>${esc(f.file)}</b></label>
      <small>${f.used || used.has(f.file) ? '使っています' : '使っていません'}${f.reason ? `（${esc(f.reason)}）` : ''}・${f.cells ?? ''} マス${f.from ? `・${esc(f.from)}〜${esc(f.to || '')}` : ''}</small></li>`).join('');
    const ov = hc.overlaps.map((o) => `<li>${(o.files || []).map(esc).join(' と ')}：${o.cells} マスが重なっています${o.identical ? '（中身は同じ）' : '（中身が違います）'}</li>`).join('');
    return `<div class="o-roles" id="own-hchoice"><div class="o-h">配車表が重なっています。使うファイルを選べます</div>
      <ul class="o-hf">${rows}</ul><ul class="o-hf">${ov}</ul><small class="o-hint">チェックを変えると、選んだファイルだけで計算し直します。</small></div>`;
  }

  function importHtml() {
    aiStatusInit(); // 使えるかの確認は、取り込み欄を最初に出したときに 1 回だけ（ファイルを落としたあとには要求を出さない）
    const d = O.data;
    const rows = d.files.map((f) => `<tr><td class="o-fn"><b>${esc(f.file)}</b>${isImport() ? `<button type="button" class="btn-link o-fx" data-act="file-remove" data-f="${esc(f.file)}" aria-label="${esc(f.file)}を外して計算し直す">外す</button>` : ''}<small>${esc(f.kind)}${f.encoding ? '・' + esc(f.encoding) : ''}${f.note ? '・' + esc(f.note) : ''}</small>${f.layout_note_ja ? `<small class="o-lay">${esc(f.layout_note_ja)}</small>` : ''}</td>
      <td class="num">${f.ok}</td><td class="num ${f.pending ? 'o-y' : ''}">${f.pending}</td><td class="num ${f.unreadable ? 'o-r' : ''}">${f.unreadable}</td></tr>`).join('');
    const unread = d.unreadable.map((u) => `<tr><td class="o-where"><small>${esc(u.file)}</small>${esc(u.where)}</td><td class="o-mono">${esc(u.original)}</td><td class="o-r">${esc(u.reason)}${u.howto_ja ? `<small class="o-how">${esc(u.howto_ja)}</small>` : ''}${u.judged_cells && u.judged_cells.length ? `<small class="o-how">見出しとして見た ${u.judged_line} 行目: ${esc(u.judged_cells.join(' | '))}</small>` : ''}</td></tr>`).join('');
    const cols = d.columns.map((c) => {
      const lab = colLabel(c), pr = c.shape_ratio == null ? '' : pc(c.shape_ratio);
      const why = c.reason_ja || (c.dict_hit ? `列名から「${lab}」と判断しました。値の形は ${pr} 合っています` : lab ? `列名からは分からず、値の形から「${lab}」らしいと見ています（${pr} 合っています）` : '列名からも値の形からも、役割を決められませんでした');
      const verdict = { green: '合っているはず', yellow: '確認してください', red: '決められません' }[c.color] || '';
      return `<div class="o-col" title="${esc(c.formula)}"><span class="o-cn"><b>${esc(c.column)}</b><small>${esc(c.source)}</small></span>
        <span class="o-cd">${esc(lab || '—')}</span><span class="o-cs">${esc(why)}</span><span class="o-cc"><i class="${dotCls(c.color)}"></i><span>${verdict}</span></span></div>`;
    }).join('');
    const g = d.columns.filter((c) => c.color === 'green').length, y = d.columns.filter((c) => c.color === 'yellow').length, r = d.columns.filter((c) => c.color === 'red').length;
    const un = unplacedHtml(d);
    return `<div class="o-drop" id="own-drop" tabindex="0" role="button" aria-label="自社のファイルを落とす欄（配車表・デジタコの CSV。ブラウザの外には送りません）">
        <b>配車表・デジタコの CSV をここに置く</b><span>文字コードはどちらでも読めます。ファイルは外に送りません。</span><input id="own-file" type="file" accept=".csv,.txt,text/csv" multiple hidden></div>
      ${formatGuideHtml()}<p class="o-drop-msg" id="own-drop-msg" ${O.dropMsg ? '' : 'hidden'}>${esc(O.dropMsg)}</p>${pendingYearHtml()}${pendingRolesHtml()}${yearHtml(d)}${monthPickHtml(d)}${noticesHtml(d)}${hiredHtml(d)}${haishaChoiceHtml(d)}${rolesHtml(d)}${monthHtml()}
      <table class="o-tbl"><thead><tr><th>ファイル</th><th>読めた行</th><th>確認待ち</th><th>読めない行</th></tr></thead><tbody>${rows}
        <tr class="o-paper"><td colspan="4">${esc(d.paper.text)}</td></tr></tbody></table>
      <details class="o-det"><summary>表の形の判定（ファイルごと）</summary>${d.files.map((f) => `<p class="o-verdict"><b>${esc(f.file)}</b> ${esc(f.verdict)}</p>`).join('')}</details>
      <details class="o-det o-unr" ${d.unreadable.length && d.unreadable.length <= 3 && isImport() ? 'open' : ''}><summary>読めない ${d.unreadable.length} 行${isImport() ? '' : '（サンプルにわざと入れた行です）'}</summary>
      <table class="o-tbl o-un"><tbody>${unread || '<tr><td class="o-hint">読めない行はありません</td></tr>'}</tbody></table></details>${un}
      <details class="o-det"><summary>どの列を何と読んだか（緑 ${g}・黄 ${y}・赤 ${r}）</summary>
        <p class="o-claude ${d.claude.present || isImport() ? '' : 'none'}">${isImport() ? '列の対応づけは、列名の辞書と値の形だけで決めています（外には送っていません）' : esc(d.claude.label)}${d.claude.present || isImport() ? '' : '（開発のときに作る記録の欄は空です。どのくらい確かかは辞書の一致と値の形だけで計算しています）'}</p>
        <div class="o-cols">${cols}</div></details>`;
  }

  // 「修正」「読まない」を押したとき、計算に何が起きるか（カードに書く）
  function effectText(c) {
    if (!isImport()) return 'サンプルの数字は元の記録から作った固定の値です。自社のファイルを落とすと、修正・読まないが計算に反映されます';
    if (c.effect === 'kpi') return '押すと、この項目を変えて実車率などを計算し直します';
    if (c.effect === 'display') return '押すと、区間の時刻の表示だけが変わります。実車率などの数字は変わりません';
    return '計算は変わりません（印を付けるだけです）';
  }
  function fixHint(c) {
    if (c.kind === 'column') return '役割の名前（例: 走行距離）';
    if (c.kind === 'weight') return '重さ（例: 4.6t、4600kg）';
    if (c.kind === 'link') return /:time$/.test(c.key || '') ? '時刻（例: 5:30）' : '距離 km（例: 551.8）';
    return '直した値を入れる';
  }
  // 入れた文字が使えるか。使えなければ理由を返す
  function fixProblem(c, v) {
    if (!isImport()) return '';
    if (c.kind === 'column' && c.source === 'デジタコ' && !OwnCalc.resolveRole(v)) return '役割の名前が分かりません（例: 走行距離、開始の日時、実車・空車の区分）';
    if (c.kind === 'link' && /:distance$/.test(c.key || '') && OwnCalc.parseKm(v) == null) return '距離を数で入れてください（例: 551.8）';
    if (c.kind === 'weight') { const w = OwnCalc.parseWeight(v); if (!w || w.kg == null || !(w.kg > 0)) return '重さを 4.6t や 4600kg のように入れてください（記号は付けません）'; }
    if (c.kind === 'link' && /:time$/.test(c.key || '') && !OwnCalc.parseTime(v)) return '時刻を 5:30 のように入れてください';
    return '';
  }

  // 車ごと・車種ごとの最大積載量（kg）。このブラウザに覚え、読み直しても戻らないようにする（ファイルの中身ではなく、車番・車種と数だけ）
  // 鍵は関数の中で文字のまま使う: O を作るときに loadMaxPayload() が先に呼ばれるので、下の定数はまだ読めず、覚えた値が開き直すと消えていた
  const MP_KEY = 'backfull.own.v42.maxload';
  function loadMaxPayload() {
    try {
      const o = JSON.parse(localStorage.getItem('backfull.own.v42.maxload') || 'null');
      const clean = (m) => Object.fromEntries(Object.entries(m || {}).filter(([k, v]) => k && Number(v) > 0));
      return { by_plate: clean(o && o.by_plate), by_class: clean(o && o.by_class) };
    } catch (e) { return { by_plate: {}, by_class: {} }; }
  }
  function saveMaxPayload() { try { localStorage.setItem(MP_KEY, JSON.stringify(O.maxPayload)); } catch (e) { /* 使えないとき */ } }
  // 最大積載量を超える重さのカードか。その車の車番と車種を返す
  function overMaxInfo(c) {
    if (!isImport() || c.kind !== 'weight' || !/最大積載量.*超え/.test(c.original || '')) return null;
    const l = O.data.legs.find((x) => x.id === c.leg), v = l && O.data.vehicles.find((x) => x.plate === l.plate);
    return l ? { plate: l.plate, cls: v ? v.cls : '', kg: v ? v.max_load_kg : null } : null;
  }
  function maxPayloadHtml(c, key) {
    const m = overMaxInfo(c);
    if (!m) return '';
    const mine = O.maxPayload.by_plate[m.plate], byCls = O.maxPayload.by_class[m.cls];
    const cur = mine ? `この車（${esc(m.plate)}）は ${mine / 1000} t にしてあります` : byCls ? `${esc(m.cls)}の車は ${byCls / 1000} t にしてあります` : '';
    return `<div class="o-mpw"><button type="button" class="btn-link" data-act="mp-open">この車の最大積載量を変える</button>${cur ? `<small class="o-mpnow">${cur}</small>` : ''}
      <div class="o-mp" ${O.open.has('mp' + key) ? '' : 'hidden'}><label>最大積載量 <input class="ctl sm" type="number" min="0.5" max="40" step="0.5" inputmode="decimal" placeholder="例 13" aria-label="最大積載量（トン）"> t</label>
        <button type="button" class="btn-sm" data-act="mp-plate" data-plate="${esc(m.plate)}">この車だけ</button><button type="button" class="btn-sm" data-act="mp-class" data-cls="${esc(m.cls || '')}" ${m.cls ? '' : 'disabled'}>${esc(m.cls || '車種不明')}の車すべて</button>
        ${mine || byCls ? `<button type="button" class="btn-link" data-act="mp-reset" data-plate="${esc(m.plate)}" data-cls="${esc(m.cls || '')}">元に戻す</button>` : ''}
        <small>車検証の最大積載量を入れます。このブラウザに覚えるので、次に読み込んでも戻りません。変えると計算し直します。</small></div></div>`;
  }

  function confirmHtml() {
    const d = O.data, cnt = { ok: 0, fix: 0, skip: 0 };
    for (const c of d.confirm) if (O.dec[ck(c)]) cnt[O.dec[ck(c)]]++;
    const pend = d.confirm.length - cnt.ok - cnt.fix - cnt.skip;
    const cards = d.confirm.map((c) => {
      const key = ck(c), st = O.dec[key] || '';
      const claude = c.kind === 'column' && !isImport() ? `<div class="o-kv"><span>Claude</span><span class="${c.claude ? '' : 'o-none'}">${esc(claudeText(c.claude))}</span></div>` : '';
      const conf = c.confidence != null ? `<div class="o-kv"><span>どのくらい確かか</span><span class="num o-formula">${esc(c.formula)}</span></div>` : `<div class="o-kv"><span>しきい値</span><span class="o-formula">${esc(c.formula)}</span></div>`;
      const label = { ok: '確定しました', fix: '修正しました', skip: '読まないことにしました' }[st] || '';
      const eff = st && c.decision && c.decision.note ? c.decision.note : effectText(c);
      return `<div class="o-card ${st ? 'done ' + st : ''}" data-id="${esc(c.id)}" data-key="${esc(key)}">
        <div class="o-ch"><i class="${dotCls(st ? 'green' : c.color)}"></i><b>${esc(c.kind === 'return' ? `車庫へ戻る区間 ${c.count != null ? c.count : ''} 件に空・実車の印がありません` : c.title)}</b><small>${esc(c.source)}</small></div>
        <div class="o-kv"><span>元の値</span><span>${esc(c.original)}</span></div>
        <div class="o-kv"><span>提案</span><span>${esc(c.proposal)}</span></div>${c.reason_ja ? `<div class="o-kv"><span>理由</span><span>${esc(c.reason_ja)}</span></div>` : ''}${claude}${c.reason_ja ? '' : conf}
        <div class="o-kv"><span>反映</span><span class="o-eff">${esc(eff)}</span></div>
        <div class="o-btns">${st ? `<span class="o-res">${label}${st === 'fix' && O.fixTxt[key] ? '：' + esc(O.fixTxt[key]) : ''}</span><button type="button" class="btn-link" data-act="undo">取り消す</button>`
          : (c.kind === 'return' ? '<button type="button" class="btn-sm" data-act="ru-empty">全部 空車にする</button><button type="button" class="btn-sm" data-act="ru-loaded">全部 実車</button><button type="button" class="btn-sm" data-act="ru-keep">不明のまま</button>'
            : '<button type="button" class="btn-sm" data-act="ok">確定</button><button type="button" class="btn-sm" data-act="fix">修正</button><button type="button" class="btn-sm" data-act="skip">読まない</button>')}</div>
        ${maxPayloadHtml(c, key)}
        <div class="o-fix" ${O.open.has('fix' + key) && !st ? '' : 'hidden'}><input class="ctl sm" type="text" placeholder="${esc(fixHint(c))}" aria-label="直した値"><button type="button" class="btn-sm" data-act="fixok">反映</button></div>
      </div>`;
    }).join('');
    return `<p class="o-sum">確認待ち <b class="num">${pend}</b> 件・確定 ${cnt.ok}・修正 ${cnt.fix}・読まない ${cnt.skip}<small>${isImport() ? '押した結果は計算に渡し、数字を計算し直します。確認の決定は、このブラウザに保存されます（「月ごとの保存」の欄で消せます）。' : '押した結果はこのページの中だけに残ります。'}</small></p>${cards}`;
  }

  // 求荷求車のサイトや LINE に貼れる文（空車の帰りの区間 = 発地 → 着地ごとに 1 つ。曜日・便数・車格・空きの重さ。連絡先は空欄）。全部を 1 つの欄に並べ、区間ごとのコピーも出す
  const BOARD_SEP = '\n\n----------\n\n';
  function boardHtml(i) {
    if (!O.board || O.board.id !== i.id) return '';
    const items = O.board.items || [];
    return `<div class="o-board"><p class="o-hint">${items.length > 1 ? `空車の帰りの区間 ${items.length} 本ぶんの文を、区間ごとに分けて作りました。` : ''}掲示には区間ごとに 1 件ずつ貼ります。</p>
      <textarea id="own-board-ta" class="o-ta" rows="${Math.min(22, 9 + 9 * Math.max(0, items.length - 1))}" readonly aria-label="求荷求車に貼る文">${esc(O.board.text)}</textarea>
      <div class="o-btns"><button type="button" class="btn-primary sm" data-act="board-copy">${items.length > 1 ? '全部をコピー' : 'コピー'}</button>${items.length > 1 ? items.map((it, k) => `<button type="button" class="btn-sm" data-act="board-copy-one" data-i="${k}">${esc(it.label)}だけコピー</button>`).join('') : ''}<span class="o-copy-msg" id="own-board-msg" role="status">${esc(O.board.msg || '')}</span></div>
      <p class="o-note">会社名・電話番号は入っていません。貼る前に書き入れてください。運賃は書いていません。空車の情報を出して荷を探す掲示なので、見出しは【求荷】です。</p></div>`;
  }

  // 明日の空きから 1 便ぶんの文を作る（配車担当の毎日の仕事用）。初期値は、いちばん多い空車の帰りの区間から取る
  function tmrHtml() {
    const d = O.data;
    if (!OwnMonth.returnPairs(d).length) return '';
    if (!O.tmr) O.tmr = { ...OwnMonth.boardDefaults(d), text: '', msg: '' };
    const nw = OwnMonth.nextWorkday(d), WDJ = '月火水木金土日';
    const dayHint = nw.basis ? `日付は、配車表で車が動いている曜日（${nw.open.map((w) => WDJ[w]).join('')}）の次の日にしました${nw.skipped.length ? `（休みの曜日の ${nw.skipped.map((x) => `${+x.slice(5, 7)}/${+x.slice(8, 10)}`).join('・')} は飛ばしました）` : ''}。` : '';
    const t = O.tmr, inp = (id, k, type, ph, extra) => `<input id="own-tmr-${id}" class="ctl sm" type="${type}" value="${esc(t[k])}" ${ph ? `placeholder="${esc(ph)}"` : ''} ${extra || ''}>`;
    return `<div class="o-tmr" id="own-tmr"><div class="o-h">明日の空車の掲示文を作る</div>
      <p class="o-hint">次に車が動く日に空く帰りの便を、日付つきの 1 便ぶんの文にします。いちばん多い空車の帰りの区間から初期値を入れました。${esc(dayHint)}違うところは直してください。</p>
      <div class="o-tmrg">
        <label>日付${inp('date', 'date', 'date', '', 'aria-label="出発の日付"')}</label>
        <label>積める時間（始まり）${inp('time', 'time', 'time', '', 'aria-label="積める時間の始まり"')}</label>
        <label>積める時間（終わり）${inp('timeTo', 'timeTo', 'time', '', 'aria-label="積める時間の終わり（空でもよい）"')}</label>
        <label>発地${inp('frm', 'frm', 'text', '例 愛知県名古屋市', 'aria-label="発地"')}</label>
        <label>着地${inp('to', 'to', 'text', '例 鹿児島県鹿屋市', 'aria-label="着地"')}</label>
        <label>車格<select id="own-tmr-cls" class="ctl sm" aria-label="車格"><option value="">選ぶ</option>${OwnMonth.CLASS_CHOICES.map((c) => `<option value="${c}" ${t.cls === c ? 'selected' : ''}>${c}車</option>`).join('')}</select></label>
        <label>荷台の形<select id="own-tmr-bed" class="ctl sm" aria-label="荷台の形"><option value="">選ばない</option>${OwnMonth.BED_CHOICES.map((c) => `<option value="${c}" ${t.bed === c ? 'selected' : ''}>${c}</option>`).join('')}</select></label>
        <label>荷台の内寸（長さ×幅×高さ）${inp('size', 'size', 'text', '例 9.6m×2.4m×2.4m', 'aria-label="荷台の内寸"')}</label>
        <label>積める重さ（t）${inp('kg', 'kg', 'text', '例 4', 'inputmode="decimal" aria-label="空きの重さ（トン）"')}</label></div>
      <div class="o-btns"><button type="button" class="btn-primary sm" data-act="tmr-make">文を作る</button>${t.text ? '<button type="button" class="btn-sm" data-act="tmr-copy">コピー</button>' : ''}<span class="o-copy-msg" id="own-tmr-msg" role="status">${esc(t.msg)}</span></div>
      ${t.text ? `<textarea id="own-tmr-ta" class="o-ta" rows="9" readonly aria-label="明日の空きの文">${esc(t.text)}</textarea><p class="o-note">会社名・電話番号と運賃は入っていません。貼る前に書き入れてください。</p>` : ''}</div>`;
  }

  function insightsHtml() {
    const d = O.data;
    if (!d.insights.length) return tmrHtml() + '<p class="o-sum">今月は目立つ点はありません。</p>';
    return tmrHtml() + d.insights.map((i) => {
      const open = O.open.has('ev' + i.id);
      const ev = open ? `<div class="o-ev">${i.evidence.map((e) => `<div class="o-evr"><b>${esc(e.label)}</b><small>${esc(e.file || '')} ${esc(e.where || '')}</small><span>${esc(e.text || '')}</span></div>`).join('')}</div>` : '';
      return `<div class="o-ins" data-id="${i.id}">
        <div class="o-ih"><span class="o-rule">${esc(i.rule_label_ja || '気づき ' + i.rule)}</span></div>
        <p class="o-it">${esc(i.text)}</p>
        <div class="o-chips">${i.numbers.map((n) => `<span class="o-chip"><small>${esc(n.k)}</small><b class="num">${esc(n.v)}</b></span>`).join('')}</div>
        <div class="o-btns"><button type="button" class="${i.next.action === 'draft_backhaul' ? 'btn-primary sm' : 'btn-sm'}" data-act="next" data-a="${esc(i.next.action)}">${esc(i.next.label)}</button>
          ${i.next.action === 'draft_backhaul' ? '<button type="button" class="btn-sm" data-act="board">求荷求車に貼る文を作る</button>' : ''}
          <button type="button" class="btn-link" data-act="ev">${open ? '元の行を閉じる' : '元の行を見る'}（${i.evidence.length}）</button></div>${ev}${boardHtml(i)}</div>`;
    }).join('');
  }

  function renderLeft() {
    const body = q('#own-body');
    if (!body || !O.loaded) return;
    const c = counts();
    q('#own-tabs').innerHTML = `<button data-tab="import" aria-pressed="${O.tab === 'import'}">読んだファイル</button>
      <button data-tab="confirm" aria-pressed="${O.tab === 'confirm'}">確認<em class="${c.pending ? 'y' : ''}">${c.pending}</em></button>
      <button data-tab="insight" aria-pressed="${O.tab === 'insight'}">気づき<em>${c.ins}</em></button>
      ${O.guided ? '' : `<button data-tab="wait" aria-pressed="${O.tab === 'wait'}">荷待ちを入れる</button>`}`;
    const keep = body.scrollTop;
    body.innerHTML = O.tab === 'import' ? importHtml() : O.tab === 'confirm' ? confirmHtml() : O.tab === 'wait' ? waitHtml() : insightsHtml();
    body.scrollTop = keep;
    const tag = q('#own-view .m-tag');
    if (tag) tag.textContent = O.tab === 'wait' ? '自社の記録を入れる' : isImport() ? '自社のファイル' : '架空のサンプル';
    syncSource();
    bindDrop();
    // 手順に沿う画面では、読んだファイルが閉じた「詳しく」の中にある。年・月・列の役割を選ぶ欄が出たときは、閉じたままにしない
    const need = O.guided && !!(O.pendingYear || O.pendingRoles || q('#own-body .o-month-pick') || q('#own-body .o-assumed'));
    const rr = q('#own-right');
    if (rr) rr.classList.toggle('o-need', need);
    if (need) { O.advOpen = true; const ad = q('#own-adv'); if (ad) ad.open = true; }
  }

  // どちらのデータを見ているかを、いつも上に出す（SPEC §35.2）。上の説明と、下の注意の文もデータに合わせる
  // 読んだファイルの数。区間も停車も取り出せなかったファイルは「読めなかった」に数える
  const readCount = () => (O.data && O.data.files_read != null ? O.data.files_read : O.files.length);
  const unreadCount = () => (O.data && O.data.files_unread ? O.data.files_unread.length : 0);

  // ファイル名の一覧。3 件までを書き、残りは「ほか N 件」にする（全部は title に入れる）
  // 見出しの月。2 か月以上を一緒に落としたときは own_calc.js の month_label（期間の言い方）
  const monthWord = (d) => d.month_label || `${d.year} 年 ${d.mon} 月`;
  // 見出しに書くファイル名: 数字のもとにしたファイル（files_used）。無ければ落としたファイル全部
  const usedNames = (d) => (Array.isArray(d.files_used) && d.files_used.length ? d.files_used.map((f) => f.file) : O.files.map((f) => f.name));
  const fileNames = (names) => (names.length > 3 ? `${names.slice(0, 3).join('、')} ほか ${names.length - 3} 件` : names.join('、'));

  function syncSource() {
    let bar = q('#own-src');
    if (!bar) { bar = document.createElement('div'); bar.id = 'own-src'; bar.className = 'o-srcbar'; q('#own-tabs').before(bar); }
    bar.className = 'o-srcbar' + (isImport() ? ' on' : '');
    bar.innerHTML = isImport()
      ? `<span class="o-srcn"><small>いま見ているデータ</small><b>自社のファイル: ${readCount()} 件${unreadCount() ? `（読めなかった ${unreadCount()} 件）` : ''}</b></span><button type="button" class="btn-sm" data-act="reset">サンプルに戻す</button>`
      : '<span class="o-srcn"><small>いま見ているデータ</small><b>サンプル（架空の会社）</b></span>';
    const d = O.data, co = q('#own-view .o-co');
    if (co) co.title = isImport() ? O.files.map((f) => f.name).join('、') : '';
    if (co) co.textContent = isImport()
      ? `${fileNames(usedNames(d))}（${monthWord(d)}、車両 ${d.vehicles.length} 台）`
      : `${d.company.name}（${d.company.city}・車両 ${d.company.vehicles} 台）の ${monthWord(d)}`;
    const n1 = q('#own-notes .f-n1'), n2 = q('#own-notes .f-n2'), n3 = q('#own-notes .f-n3');
    if (n1) n1.textContent = isImport() ? '自社のファイルの数字です。' : '架空の会社のサンプルです';
    if (n2) n2.textContent = 'CSV の中身はページを閉じると消えます。数字の要約と直した内容は、このブラウザに残ります。';
    if (n3 && !q('#own-notes .o-ndet')) {
      const det = document.createElement('details');
      det.className = 'o-ndet'; det.innerHTML = '<summary>詳しく</summary>';
      const rest = [...document.querySelectorAll('#own-notes .f-n3')];
      n3.before(det); rest.forEach((x) => det.appendChild(x));
    }
    if (n3) n3.textContent = isImport() ? '列の対応づけは、列名の辞書と値の形だけで決めています。外には送りません' : '列の対応づけの Claude の提案は、開発のときに作った記録です';
  }

  /* ------------------------------------------------------------ 右: 数字と表 */

  // 数字と式の 1 行を先に出し、空車か実車か分かった区間の数などは閉じた「詳しく」に入れる（400px でも数字から次の数字までが短い）
  // cnt: 大きな数字のもとになった区間の数。5 未満のときは「件数が少ないので参考です」を付ける。alt: 別の数え方の値の 1 行（HTML）
  const FEW = 5;
  // faint: 計算できた区間が少ない（30% 未満）、または読めた割合が低いときの理由の文。あれば数字を薄くして「参考」と書く
  function tile(label, val, sub, nat, tip, term, fml, cnt, alt, faint) {
    const cntH = (cnt == null ? '' : `<div class="o-tc">${cnt} 区間から${cnt < FEW ? '<span class="o-few">件数が少ないので参考です</span>' : ''}</div>`) + (faint ? `<div class="o-tc o-faintnote"><span class="o-few">参考</span> ${esc(faint)}</div>` : '');
    const more = [alt ? `<div class="o-talt">${alt}</div>` : '', fml ? `<div class="o-tf">${esc(fml)}</div>` : '', nat ? `<div class="o-tn">${nat}</div>` : '', `<div class="o-ts">${sub}</div>`, `<div class="o-ts">${esc(tip)}</div>`].join('');
    return `<div class="o-tile${faint ? ' o-faint' : ''}" title="${esc(tip)}"><div class="o-tl">${label}${term ? OwnMonth.tipHtml(term) : ''}</div><div class="o-tv num">${val}</div>${cntH}
      <details class="o-tmore"><summary>詳しく</summary>${more}</details></div>`;
  }

  const yenS = (v) => `${n0(v)} 円`;

  // 実車率を、不明の区間と読めない行を空車とした場合の値。own_calc.js が kpis の項目で返せばそれを使い、無いときは不明の区間の距離を空車に足して求める
  // 返す値: { v: 0〜1, full: 読めない行まで含めた値か, unk: 不明の区間数, unread: 読めない行数 }。不明も読めない行も無ければ null
  function altActual(d) {
    const k = d.kpis, unk = (d.counts && d.counts.unknown) || 0, unread = (d.unreadable || []).length;
    if (!unk && !unread && !((d.counts || {}).unreadable_rows)) return null;
    const pick = (o) => (o == null ? null : typeof o === 'object' ? (o.value != null ? o.value : null) : o);
    const ua = k.unknown_as_empty || {};
    let v = pick(k.loaded_rate_if_unknown_empty);
    if (v == null) v = pick(k.actual_rate_if_unknown_empty);
    if (v == null) v = pick(k.actual_rate && k.actual_rate.if_unknown_empty);
    if (v == null) { const e = pick(k.empty_rate_if_unknown_empty); if (e != null) v = 1 - e; }
    let full = false; // 読めない行は距離が分からず率に入れられないので、いつも「不明の区間を空とした場合」と書く
    if (v == null) {
      const km = d.legs.filter((l) => l.kind === 'unknown' && l.km != null).reduce((a, l) => a + l.km, 0), ar = k.actual_rate, tot = (ar.km_loaded || 0) + (ar.km_empty || 0) + km;
      if (!unk || !(tot > 0)) return null;
      v = (ar.km_loaded || 0) / tot; full = false;
    }
    return { v, full, unk: ua.unknown_legs != null ? Math.max(unk, ua.unknown_legs) : unk, unread: (d.counts && d.counts.unreadable_rows != null) ? d.counts.unreadable_rows : unread, noKm: ua.unknown_legs_without_km || 0 };
  }
  function altText(a) {
    if (!a) return '';
    return `${a.full ? '不明・読めない行を空とした場合' : '不明の区間を空とした場合'} <b class="num">${pc(a.v, 1)}</b><small>（不明 ${a.unk} 区間・読めない ${a.unread} 行。読めない行は距離が分からないので、この率には入れられません）</small>`;
  }

  // 空車の印にならなかったが、セルに書かれていた語。その区間は、積地と降ろし地があるので実車として数えている。空車を意味する語なら、実車率が高く出ているので、利用者が気づけるように一覧にする
  function ladenHtml(d) {
    const ws = d.laden_words || [];
    if (!ws.length) return '';
    return `<details class="o-laden" id="own-laden"><summary>実車として数えた区間のセルに付いていた語 ${ws.length} 種類</summary>
      <p class="o-hint">積地と降ろし地があるので実車として数えた区間のうち、重さ・時刻・空車の印のどれでもない語が付いていたものです。空車の意味の語があれば、列の役割の選択か、空車の印（空・回送・戻り空・荷なしなど）に書き換えて落とし直してください。</p>
      <ul class="o-lw">${ws.map((w) => `<li><b>${esc(w.word)}</b> ${w.count} 区間<small>例: ${esc(w.example)}</small></li>`).join('')}</ul></details>`;
  }

  // 月をまたぐ配車表のとき、数字がどの期間の合計かを書く
  function periodNote(d) {
    const p = d.period;
    if (!p || p.from.slice(0, 7) === p.to.slice(0, 7)) return '';
    const md = (iso) => `${+iso.slice(5, 7)}/${+iso.slice(8, 10)}`;
    return `・数字は ${md(p.from)}〜${md(p.to)} の合計`;
  }

  // 旧: 主な往復だけの表（区間の集計 d.pair_table が無いとき）
  function tableHtmlLegacy(d) {
    const cs = OwnCalc.costs(d, O.cost.perKm, O.cost.perHour), dockLong = (wd) => d.stops.filter((s) => s.place === d.dock && s.minutes >= 30 && s.cycle_wd === wd).reduce((a, s) => a + s.minutes, 0);
    const extra = (cs.perKm != null ? 1 : 0) + (cs.perHour != null ? 1 : 0), nm = (id) => (d.places[id] ? d.places[id].short : '?');
    const rowsOf = (dir) => {
      const rs = d.table.filter((t) => t.dir === dir);
      if (!rs.length) return '';
      const tot = rs.reduce((a, t) => ({ trips: a.trips + t.trips, empty: a.empty + t.empty, km: a.km + t.empty_km, ls: a.ls + (t.long_stops || 0), unk: a.unk + t.unknown, lm: a.lm + (dir === 'out' ? dockLong(t.wd) : 0) }), { trips: 0, empty: 0, km: 0, ls: 0, unk: 0, lm: 0 });
      tot.km = OwnCalc.pyRound(tot.km, 1); // 表に出す km（小数 1 桁）で費用を掛ける
      const costCells = (km, lm) => (cs.perKm != null ? `<td class="num o-ccell" data-l="空車の費用">${yenS(Math.round(km * cs.perKm))}</td>` : '') + (cs.perHour != null ? `<td class="num o-ccell" data-l="停車の費用">${dir === 'out' ? yenS(Math.round((lm / 60) * cs.perHour)) : '—'}</td>` : '');
      const body = rs.map((t) => {
        const hot = dir === 'back' && t.trips >= 4 && t.empty * 4 >= (t.trips - t.unknown) * 3 && (t.trips - t.unknown) >= 4;
        return `<tr class="${hot ? 'hot' : ''}"><td>${WD[t.wd]}曜発${hot ? '<small class="o-hotmark">空車が多い</small>' : ''}</td><td class="num" data-l="便数">${t.trips}</td><td class="num" data-l="空車">${t.empty}</td><td class="num" data-l="空車の km">${t.empty_km ? n1(t.empty_km) : '—'}</td><td class="num" data-l="30 分以上の停車">${t.long_stops == null ? '—' : t.long_stops}</td>${costCells(t.empty_km, dir === 'out' ? dockLong(t.wd) : 0)}</tr>`;
      }).join('');
      const a = dir === 'out' ? d.base : d.dock, b = dir === 'out' ? d.dock : d.base;
      return `<tr class="grp"><td colspan="${5 + extra}">${dir === 'out' ? '行き（車庫から）' : '帰り（車庫へ）'}　${esc(nm(a))} → ${esc(nm(b))}</td></tr>${body}
        <tr class="tot"><td>合計</td><td class="num" data-l="便数">${tot.trips}</td><td class="num" data-l="空車">${tot.empty}</td><td class="num" data-l="空車の km">${n1(tot.km)}</td><td class="num" data-l="30 分以上の停車">${dir === 'out' ? tot.ls : '—'}</td>${costCells(tot.km, tot.lm)}</tr>`;
    };
    const body = rowsOf('out') + rowsOf('back');
    if (!body) return '<p class="o-hint">主な往復（車庫から・車庫へ）の区間がありません。区間の表は出せません。</p>';
    return `<table class="o-tab" aria-label="区間と向きと曜日の表"><thead><tr><th>向き・出た曜日</th><th>便数</th><th>空車</th><th>空車の km</th><th>${esc(nm(d.dock))}での 30 分以上の停車</th>${cs.perKm != null ? '<th>空車の費用</th>' : ''}${cs.perHour != null ? '<th>停車の費用</th>' : ''}</tr></thead>
        <tbody>${body}</tbody></table>${scopeNote(d, cs, nm)}`;
  }

  // 車庫をどの地点にしたかと、その理由（1 行）。d.base_basis は web/own_calc.js の analyze が付ける
  function baseWhy(d, nm) {
    const b = d.base_basis;
    if (!b) return '';
    if (b.kind === 'given') return '指定された地点';
    const src = b.kind === 'digitaco_from' ? 'デジタコの走行で出発地に最も多く出た地点' : '配車表で積地に最も多く書かれた地点';
    const sec = b.second ? `、次は ${nm(b.second.place)} の ${b.second.count} 件` : '';
    return `${src}。${b.total} 件のうち ${b.count} 件${sec}`;
  }

  // 表の向きの言葉（行き（車庫から）・帰り（車庫へ））と、曜日の数え方の説明。推定した車庫と理由を表の上に出す
  function baseNote(d, nm) {
    if (!d.base) return '';
    return `<p class="o-hint o-basenote" id="own-basenote">車庫は ${esc(nm(d.base))} と見ています${baseWhy(d, nm) ? `（${esc(baseWhy(d, nm))}）` : ''}。違うときは行きと帰りが逆になります。</p>`;
  }

  // 区間（実際の発地 → 着地の組）× 曜日の表。見出しは組の名前（d.pair_table / d.pairs）。単価が入っているときだけ、費用の列を足す（空なら出さない。0 円にしない）
  const PAIR_SHOW = 6; // 初めに開いて見せる区間の数。残りは閉じた「ほかの区間」に入れる
  function tableHtml(d) {
    if (!Array.isArray(d.pair_table) || !d.pair_table.length || !Array.isArray(d.pairs) || !d.pairs.length) return tableHtmlLegacy(d);
    const cs = OwnCalc.costs(d, O.cost.perKm, O.cost.perHour), nm = (id) => (d.places[id] ? d.places[id].short : '?');
    const extra = (cs.perKm != null ? 1 : 0) + (cs.perHour != null ? 1 : 0);
    const dockLong = (wd) => d.stops.filter((s) => s.place === d.dock && s.minutes >= 30 && s.cycle_wd === wd).reduce((a, s) => a + s.minutes, 0);
    const head = `<thead><tr><th title="行きの行は車庫を出た日の曜日、帰りの行は帰りの便が出た日の曜日で数えています（荷主への案内文と同じ数え方）">便が出た曜日</th><th>便数</th><th>空車</th><th>空車の km</th><th>${esc(nm(d.dock))}での 30 分以上の停車</th>${cs.perKm != null ? '<th>空車の費用</th>' : ''}${cs.perHour != null ? '<th>停車の費用</th>' : ''}</tr></thead>`;
    const legById = new Map((d.legs || []).map((l) => [l.id, l]));
    // 帰りの向きは、行きが車庫を出た曜日ではなく、帰りの便自身が出た日の曜日で数え直す（荷主への案内文と同じ基準。便が出た日が分からないときは元の数え方のまま）
    const byReturnDay = (rs) => {
      const acc = new Map();
      for (const t of rs) {
        for (const id of t.leg_ids || []) {
          const l = legById.get(id);
          if (!l) continue;
          const w = OwnCalc.returnWhen(l)[2];
          const wd = w == null ? t.wd : w;
          if (!acc.has(wd)) acc.set(wd, { wd, trips: 0, empty: 0, unknown: 0, empty_km: 0 });
          const a = acc.get(wd);
          a.trips++;
          if (l.kind === 'empty') { a.empty++; a.empty_km += l.km || 0; } else if (l.kind === 'unknown') a.unknown++;
        }
      }
      return [...acc.values()].sort((x, y) => x.wd - y.wd).map((a) => ({ ...a, empty_km: OwnCalc.pyRound(a.empty_km, 1) }));
    };
    const group = (p) => {
      let rs = d.pair_table.filter((t) => t.pair === p.id);
      if (!rs.length) return '';
      if (p.role === 'back') rs = byReturnDay(rs);
      const atDock = p.role === 'out' && p.to === d.dock;
      const tot = rs.reduce((a, t) => ({ trips: a.trips + t.trips, empty: a.empty + t.empty, km: a.km + t.empty_km, unk: a.unk + t.unknown, lm: a.lm + (atDock ? dockLong(t.wd) : 0), ls: a.ls + (atDock ? ((d.table.find((x) => x.dir === 'out' && x.wd === t.wd) || {}).long_stops || 0) : 0) }), { trips: 0, empty: 0, km: 0, unk: 0, lm: 0, ls: 0 });
      tot.km = OwnCalc.pyRound(tot.km, 1);
      const costCells = (km, lm) => (cs.perKm != null ? `<td class="num o-ccell" data-l="空車の費用">${yenS(Math.round(km * cs.perKm))}</td>` : '') + (cs.perHour != null ? `<td class="num o-ccell" data-l="停車の費用">${atDock ? yenS(Math.round((lm / 60) * cs.perHour)) : '—'}</td>` : '');
      const stopsOf = (wd) => d.table.find((x) => x.dir === 'out' && x.wd === wd);
      const body = rs.map((t) => {
        const hot = t.trips >= 4 && t.empty * 4 >= (t.trips - t.unknown) * 3 && (t.trips - t.unknown) >= 4;
        const ls = atDock ? (((stopsOf(t.wd) || {}).long_stops) == null ? '—' : stopsOf(t.wd).long_stops) : '—';
        return `<tr class="${hot ? 'hot' : ''}"><td>${WD[t.wd]}曜発${hot ? '<small class="o-hotmark">空車が多い</small>' : ''}</td><td class="num" data-l="便数">${t.trips}</td><td class="num" data-l="空車">${t.empty}</td><td class="num" data-l="空車の km">${t.empty_km ? n1(t.empty_km) : '—'}</td><td class="num" data-l="30 分以上の停車">${ls}</td>${costCells(t.empty_km, atDock ? dockLong(t.wd) : 0)}</tr>`;
      }).join('');
      const role = p.role === 'out' ? '行き（車庫から）' : p.role === 'back' ? '帰り（車庫へ）' : '';
      return `<tr class="grp"><td colspan="${5 + extra}">${esc(p.label)}${role ? `<small>主な往復の「${role}」の向き${p.role === 'back' ? '・帰りの便が出た曜日で数えています' : ''}</small>` : ''}</td></tr>${body}
        <tr class="tot"><td>合計</td><td class="num" data-l="便数">${tot.trips}</td><td class="num" data-l="空車">${tot.empty}</td><td class="num" data-l="空車の km">${n1(tot.km)}</td><td class="num" data-l="30 分以上の停車">${atDock ? tot.ls : '—'}</td>${costCells(tot.km, tot.lm)}</tr>`;
    };
    const gs = d.pairs.map(group).filter((x) => x);
    if (!gs.length) return '<p class="o-hint">区間の表を作れる便がありません。</p>';
    const first = gs.slice(0, PAIR_SHOW), rest = gs.slice(PAIR_SHOW);
    return `${baseNote(d, nm)}<table class="o-tab" aria-label="区間と曜日の表">${head}<tbody>${first.join('')}</tbody></table>
      ${rest.length ? `<details class="o-more2 o-pairrest"><summary>ほかの区間 ${rest.length} 本</summary><table class="o-tab" aria-label="ほかの区間と曜日の表">${head}<tbody>${rest.join('')}</tbody></table></details>` : ''}${scopeNote(d, cs, nm)}`;
  }

  // 表の「30 分以上の停車」は主な行き先だけ、費用の合計は全地点。数が違うときは、その違いを画面に書く
  function scopeNote(d, cs, nm) {
    const dockCount = d.table.filter((t) => t.dir === 'out').reduce((a, t) => a + (t.long_stops || 0), 0);
    const hours = OwnCalc.pyRound(cs.longMin / 60, 1);
    if (!cs.longCount) return '';
    if (dockCount === cs.longCount) return `<p class="o-hint o-scope">表の停車は主な行き先（${esc(nm(d.dock))}）の分で、全地点の 30 分以上の停車（${cs.longCount} 回・${hours} 時間）と同じ数です。</p>`;
    return `<p class="o-hint o-scope">表の「30 分以上の停車」は主な行き先（${esc(nm(d.dock))}）の行きの分だけで ${dockCount} 回です。停車の費用の合計は、すべての地点の 30 分以上の停車 ${cs.longCount} 回・${hours} 時間で計算しています。</p>`;
  }

  // 単価の欄と、式つきの費用（SPEC §35.3）。単価は利用者の入力で、削減額の約束ではない
  function costOutHtml(d) {
    const c = OwnCalc.costs(d, O.cost.perKm, O.cost.perHour);
    const errs = [c.errors.perKm && `1 km あたりの費用: ${c.errors.perKm}`, c.errors.perHour && `時間あたりの費用: ${c.errors.perHour}`].filter((x) => x)
      .map((t) => `<p class="o-err" role="alert">${esc(t)}</p>`).join('');
    const warns = [c.warns && c.warns.perKm, c.warns && c.warns.perHour].filter((x) => x).map((t) => `<p class="o-hint o-warn" role="status">${esc(t)}</p>`).join('');
    // 停車の記録はデジタコにだけある。配車表だけのときは、0 円と書かずに理由を書く
    const noWait = c.waitMissing && c.perHour != null ? '<p class="o-hint" id="own-wait-missing">30 分以上の停車の費用は、停車の記録が無いので出していません（停車の記録はデジタコにだけあります。デジタコの CSV を入れると出ます）。</p>' : '';
    if (c.empty == null && c.wait == null) return errs + warns + noWait + (noWait ? '' : '<p class="o-hint">単価を入れると、空車と停車の費用の目安を出します。</p>');
    const row = (label, x) => `<div class="o-crow"><span>${label}</span><b class="num">${yenS(x.yen)}</b><small class="o-formula">${esc(x.formula)} = ${yenS(x.yen)}</small></div>`;
    return errs + warns + (c.empty ? row('空車の費用（月の合計）<small class="o-keep">帰り荷を取っても減らない費用です</small>', c.empty) : '') + (c.wait ? row('30 分以上の停車の費用（月の合計）', c.wait) : '') + noWait;
  }

  // 帰り荷 1 便の売上の目安（標準的運賃の距離制の表）。空車の帰りの便ごとに、その便の車の種類と km で出し、区間（発地 → 着地）ごとの小計を並べる。
  // 空車の費用（帰り荷を取っても減らない費用）とは別に、取るかどうかを決める数字として出す。合計は「全部の帰りに荷が付いた場合の上限」
  const REV_GLOSS = '区分は、200 km を超えた分を 20 km ごと（500 km を超えると 50 km ごと）に 1 と数えた回数です。';
  // 自社の成約単価（円/km か 円/便）。このブラウザに覚える（数と単位だけ。ファイルの中身ではない）
  const RATE_KEY = 'backfull.own.v43.rate';
  function loadRate() {
    try { const o = JSON.parse(localStorage.getItem(RATE_KEY) || 'null'); return o && (o.kind === 'km' || o.kind === 'trip') ? { kind: o.kind, value: String(o.value == null ? '' : o.value) } : { kind: 'km', value: '' }; } catch (e) { return { kind: 'km', value: '' }; }
  }
  function saveRate() { try { localStorage.setItem(RATE_KEY, JSON.stringify(O.rate)); } catch (e) { /* 使えないとき */ } }
  const STD_NOTE = '国の標準的運賃で計算した額です。実際の帰り荷の運賃は、これより低いことが多いです。';
  // 自社の成約単価で出した額（入力があるときだけ）
  function ownRevHtml(d) {
    const r = OwnMonth.ownRevenue(d, O.rate);
    if (!r) return O.rate.value.trim() ? '<p class="o-err" role="alert">成約単価は 0 より大きい数で入れてください（例 120、30000）</p>' : '';
    const sh = (id) => esc(((d.places[id] || {}).short) || ((d.places[id] || {}).label) || '?');
    const rows = r.pairs.map((p) => `<tr><th>${sh(p.frm)} → ${sh(p.to)}</th><td class="num">${p.n}</td><td class="num">${yenS(p.subtotal)}</td></tr>`).join('');
    return `<div class="o-crow o-rev3" id="own-rev-own"><span>自社の成約単価（${n0(r.value)} ${r.unit}）で出した額<small class="o-sub">全部の帰りに荷が付いた場合の上限（車庫に向かう帰りだけ）・${r.counted} 便ぶん</small></span><b class="num">${yenS(r.total)}</b>
        <small class="o-formula">${r.kind === 'km' ? '便ごとの km × ' : '1 便 × '}${n0(r.value)} ${r.unit} の合計${r.noKm ? `（距離が分からない ${r.noKm} 便は数えていません）` : ''}。入れた単価で計算した値で、取れる保証ではありません</small></div>
      <table class="o-tbl o-revt"><thead><tr><th>区間</th><th>便数</th><th>小計</th></tr></thead><tbody>${rows}</tbody></table>${revScopeHtml(d)}`;
  }
  // 売上の目安が数えた範囲。数えたのは車庫に向かう帰りの空車だけで、空車の費用（全部の空車で計算）とは範囲が違う。数えなかった空車の便数と km を書く
  function revScopeHtml(d) {
    const sc = OwnMonth.revenueScope(d);
    if (!sc.all) return '';
    return `<p class="o-hint o-scope" id="own-rev-scope">数えたのは車庫へ帰る空車 ${sc.back} 便だけです。${sc.other ? `ほかの空車 ${sc.other} 便（${n1(sc.otherKm)} km）は数えていません。上の空車の費用は全部の空車（${sc.all} 便）の分です。` : `空車はすべて数えています。`}</p>`;
  }
  function revenueHtml(d) {
    const r = OwnMonth.revenue(d);
    if (!r) return '';
    const sh = (id) => esc(((d.places[id] || {}).short) || ((d.places[id] || {}).label) || '?');
    const head = `<div class="o-ch2">帰り荷 1 便の売上の目安<small>目安。取れる保証ではありません</small></div>
      <p class="o-std" id="own-rev-std"><b>${esc(STD_NOTE)}</b></p>
      <div class="o-cin o-rate"><label>自社の運賃の単価（入れなくてもよい）<span><input id="own-rate" class="ctl sm" type="text" inputmode="decimal" autocomplete="off" value="${esc(O.rate.value)}" placeholder="例 120" aria-label="自社の成約単価">
        <select id="own-rate-kind" class="ctl sm" aria-label="成約単価の単位"><option value="km" ${O.rate.kind === 'km' ? 'selected' : ''}>円/km</option><option value="trip" ${O.rate.kind === 'trip' ? 'selected' : ''}>円/便</option></select></span></label>
        <small class="o-hint">入れると、国の標準的運賃の額と並べて出します。</small></div><div id="own-rev-ownw">${ownRevHtml(d)}</div>`;
    const skipped = [r.noClass && `車の種類が分からない帰り ${r.noClass} 便は数えていません`, r.noFare && `200 km 未満で表が無い帰り ${r.noFare} 便は数えていません`, r.noKm && `距離が分からない帰り ${r.noKm} 便は数えていません`].filter((x) => x);
    const skipP = (skipped.length ? `<p class="o-hint" id="own-rev-skip">${esc(skipped.join('。'))}。</p>` : '') + revScopeHtml(d);
    let body;
    if (!r.counted) {
      body = r.noFare && !r.noClass ? `<p class="o-hint">帰りの空車の区間は 200 km 未満が多く、標準的運賃の距離制の表（200 km から）に当たらないので、目安を出しません。短い距離は時間制の運賃を見てください。</p>`
        : `<p class="o-hint">帰りの空車 ${r.trips} 便のうち、売上の目安を出せた便がありません。${esc(skipped.join('。'))}。</p>`;
      return `<div class="o-rev" id="own-rev">${head}${body}</div>`;
    }
    const ex = r.example, amt = r.min === r.max ? yenS(r.min) : `${yenS(r.min)} 〜 ${yenS(r.max)}`;
    const rows = r.pairs.map((p) => {
      const cl = Object.entries(p.byCls).map(([c, n]) => `${c} ${n} 便`).join('・');
      const one = p.min === p.max ? yenS(p.min) : `${n0(p.min)}〜${yenS(p.max)}`;
      return `<tr><th>${sh(p.frm)} → ${sh(p.to)}<small>${esc(cl)}・${n1(p.kmMin)}${p.kmMin === p.kmMax ? '' : '〜' + n1(p.kmMax)} km</small></th><td class="num">${p.n}</td><td class="num">${one}</td><td class="num">${yenS(p.subtotal)}</td></tr>`;
    }).join('');
    const trips = r.perTrip.slice().sort((x, y) => (x.date < y.date ? -1 : x.date > y.date ? 1 : 0)).map((t) =>
      `<tr><td>${esc(md2(t.date))}</td><td>${esc(t.plate)}</td><td>${sh(t.frm)} → ${sh(t.to)}</td><td>${esc(t.cls)}</td><td class="num">${n1(t.km)}</td><td class="num">${yenS(t.amount)}</td><td class="o-formula">${esc(t.fare.formula)}</td></tr>`).join('');
    body = `<div class="o-crow o-rev1"><span>1 便の売上の目安<small class="o-sub">便ごとに、その便の車の種類と km で計算</small></span><b class="num">${amt}</b>
        <small class="o-formula">例: ${esc(md2(ex.date))} ${esc(ex.plate)}（${esc(ex.cls)}車・${sh(ex.frm)} → ${sh(ex.to)}・${n1(ex.km)} km）　${esc(ex.fare.formula)} = ${yenS(ex.amount)}（目安）</small></div>
      <table class="o-tbl o-revt"><thead><tr><th>区間</th><th>便数</th><th>1 便</th><th>小計</th></tr></thead><tbody>${rows}</tbody></table>
      <div class="o-crow o-rev2"><span>車庫へ帰る便すべてに荷が付いたときの額（${r.counted} 便）</span><b class="num o-upper">${yenS(r.total)}</b>
        <small class="o-formula">全便に荷が付くことはまずありません。</small></div>${skipP}
      <details class="o-more" id="own-rev-more"><summary>便ごとの額を見る</summary>
        <div class="o-tw2"><table class="o-tbl"><thead><tr><th>日</th><th>車番</th><th>区間</th><th>車格</th><th>km</th><th>目安</th><th>式</th></tr></thead><tbody>${trips}</tbody></table></div>
        <p class="o-note">標準的運賃（国土交通省 2024 年 3 月 22 日告示）の距離制の表。運賃の地域は、その便の発地の県で決めています（県が分からないか関東・近畿以外は関東の表）。車の種類は 8 t 以上を 10t、3 t 以下を 2t、その間を 4t としています。${esc(REV_GLOSS)}実際の運賃は荷主との相談で決まります。
          上の「空車の費用」は、帰り荷を取っても減らない費用です。この売上の目安と引き算はしないでください。</p></details>`;
    return `<div class="o-rev" id="own-rev">${head}${body}<p class="o-note">用語: 空車 km${OwnMonth.tipHtml('空車 km')}</p></div>`;
  }

  function renderRate() {
    const w = q('#own-rev-ownw');
    if (w) w.innerHTML = ownRevHtml(O.data);
    const ps = q('#own-printsheet');
    if (ps) ps.innerHTML = printHtml(O.data);
  }

  function renderCost() {
    const d = O.data, o1 = q('#own-cost-out'), t = q('#own-tw');
    if (o1) o1.innerHTML = costOutHtml(d);
    if (t) t.innerHTML = tableHtml(d);
    const c = OwnCalc.costs(d, O.cost.perKm, O.cost.perHour);
    for (const [id, er] of [['own-perkm', c.errors.perKm], ['own-perhour', c.errors.perHour]]) {
      const e = q('#' + id);
      if (e) { if (er) e.setAttribute('aria-invalid', 'true'); else e.removeAttribute('aria-invalid'); }
    }
  }

  // 例のファイル（examples/own/）を落とした直後の数字がサンプルと少し違う理由（docs/guide/自社の運行を分析する.md と同じ説明）
  function exampleNote(d) {
    if (!isImport() || !O.files.some((f) => /^haisha_2026-10\.csv$/i.test(f.name)) || !(d.unplaced || []).some((u) => /辰巳/.test(u.name))) return '';
    return `<span class="o-why o-exnote">例のファイル（examples/own/）の実車率 ${pc(d.kpis.actual_rate.value, 1)} が、組み込みのサンプルの 77.6% と少し違うのは、サンプルが「辰巳 → 江東区」の別名を最初から入れて作った値だからです。左の「地図に置けない地点」に江東区と入れると、77.6% になります。</span>`;
  }

  // 印刷用の 1 ページ（画面では隠し、印刷のときだけ出す。own.css の @media print）
  function printHtml(d) {
    const k = d.kpis, c = OwnCalc.costs(d, O.cost.perKm, O.cost.perHour), sm = OwnMonth.summary(d, c);
    const cmp = cmpBlock(true);
    const tr = (a, b, t) => `<tr><th>${a}</th><td>${b}</td><td>${t || ''}</td></tr>`;
    const rev = OwnMonth.revenue(d), alt = altActual(d), own = OwnMonth.ownRevenue(d, O.rate);
    return `<h2>自社の運行 ${monthWord(d)}${isImport() ? '' : '（架空のサンプル）'}</h2>
      <p>${esc(d.company.name)}・車両 ${d.vehicles.length} 台${esc(periodNote(d))}。この数字は取り込んだ記録から計算した目安です。法令で残す元の記録は別に保管してください。</p>
      <table class="o-pt">${tr('実車率', pc(k.actual_rate.value, 1), `空車か実車か分かった区間 ${k.actual_rate.legs} / ${k.actual_rate.of}${k.actual_rate.legs < FEW ? '（参考。件数が少ない）' : ''}`)}${alt ? tr(alt.full ? '実車率（不明・読めない行を空とした場合）' : '実車率（不明の区間を空とした場合）', pc(alt.v, 1), `不明の区間 ${alt.unk}・読めない行 ${alt.unread} を空車とした値`) : ''}${tr('実車率に入れていないもの', `不明の区間 ${(d.counts && d.counts.unknown) || 0}・読めない行 ${(d.unreadable || []).length}`, '不明の区間は実車率の計算に入れていません。読めない行は取り込めていません')}${tr('大型の積載率', pc(k.load_rate.value, 1), '大型の最大積載量は登録が無いとき 10 t と仮定')}
        ${tr('大型の積載効率', pc(k.load_efficiency.value, 1), '大型の積載率 × 大型の実車率')}${tr('空車 km', `${n1(sm.empty_km)} km`, '')}${tr('荷待ち（30 分以上の停車）', c.waitMissing ? '記録なし' : `${c.longCount} 回・${n1(sm.wait_min / 60)} 時間`, c.waitMissing ? 'デジタコを入れていないので、停車は数えていません' : '')}
        ${rev && rev.total ? tr('帰り荷の売上の目安（全部の帰りに荷が付いた場合の上限）', yenS(rev.total), `空車の帰り ${rev.counted} 便の合計。1 便 ${rev.min === rev.max ? yenS(rev.min) : `${yenS(rev.min)}〜${yenS(rev.max)}`}（標準的運賃、便ごとの車の種類と km、目安）。${STD_NOTE}`) : ''}${own ? tr(`帰り荷の売上（自社の成約単価 ${n0(own.value)} ${own.unit} で出した上限）`, yenS(own.total), `空車の帰り ${own.counted} 便ぶん。入れた単価で計算した値`) : ''}</table>
      ${cmp}
      <h3>気づき</h3><ol>${d.insights.map((i) => `<li>${esc(i.text)}</li>`).join('') || '<li>今月は目立つ点はありません。</li>'}</ol>`;
  }

  function renderRight() {
    const el = q('#own-right');
    if (!el || !O.loaded) return;
    Negotiate.park(); // 帰り便の提案の欄は、右の欄を描き直しても消さない（編集した文面を守る）
    parkLeft(); // 左の欄も、右の中に入れているときは先に元へ戻す（描き直しで消えないように）
    if (O.tab === 'wait') { syncWaitMap(); const keep = (el.querySelector('.w-rbody') || {}).scrollTop || 0; el.innerHTML = waitRightHtml(); const b = el.querySelector('.w-rbody'); if (b) b.scrollTop = keep; mountAiExplain(el); return; }
    const d = O.data, k = d.kpis, n = d.national;
    const ar = k.actual_rate, lr = k.load_rate, le = k.load_efficiency, lar = k.large_actual_rate;
    const big = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}<small>%</small>`);
    const overNote = lr.over_max ? `最大積載量を超える重さの記録 ${lr.over_max} 件は入れていません` : '';
    const rq = OwnMonth.readQuality(d), pcs = (v) => `${(v * 100).toFixed(1)}`;
    const arBig = rq.low && rq.lo != null && ar.value != null ? `${pcs(rq.lo)}〜${pcs(ar.value)}<small>%</small>` : big(ar.value); // 読めた割合が低いときは、不明を空車とした値〜不明を外した値の幅で出す
    const fewTxt = (share, what) => (share != null && share < 0.3 && share > 0 ? `${what}が全体の ${pc(share)}（30% 未満）の区間だけから出した値です` : '');
    const arFaint = rq.low ? `読めた割合が ${pc(rq.share)} です。幅の下は不明を空車とした値、上は不明を外した値です` : fewTxt(ar.share, '空車か実車か分かった区間');
    const lrFaint = fewTxt(lr.share, '計算できた区間');
    const leFaint = lrFaint || (lar.of_large ? fewTxt(lar.legs / lar.of_large, '実車率を計算できた大型の区間') : '');
    const tiles = [
      tile('実車率', arBig, `空車か実車か分かった区間 ${pc(ar.share)}（${ar.legs} / ${ar.of} 区間）`,
        `全国 ${n.actual_rate_pct}%（営業用普通車）`, ar.formula, '実車率', '実車 km ÷ 走った km', ar.legs, altText(altActual(d)), arFaint),
      tile('大型の積載率', big(lr.value), `計算できた区間 ${pc(lr.share)}（${lr.legs} 区間。大型 ${lr.of_large} 区間のうち）`, overNote, lr.formula, '積載率', '積んだ重さ ÷ 最大積載量', lr.legs, '', lrFaint),
      tile('大型の積載効率', big(le.value), `大型の積載率（${lr.legs} 区間）× 大型の実車率 ${pc(lar.value)}（${lar.legs} 区間）`, `全国 ${n.load_efficiency_pct}%（トンキロの比。式が違います）`, le.formula, '積載効率', '積載率 × 大型の実車率', null, '', leFaint),
    ].join('');
    const readq = rq.low ? `<p class="o-readq" id="own-readq" role="status"><b>読めた割合 ${pc(rq.share)}</b>（${rq.total} 件のうち ${rq.known} 件。読めない行・セル ${rq.unread} 件、実車か空車か不明の区間 ${rq.unk} 件）。下の実車率は、読めなかった分を含まない参考の値です${rq.lo != null ? '。幅は、不明を空車とした場合から、不明を外した場合までです' : ''}。</p>` : '';
    const assumed = isImport() && d.vehicles.some((v) => v.max_load_assumed);
    const mr = d.max_load_raised, largeShown = O.largeT || (mr ? mr.to_kg / 1000 : 10);
    const raisedNote = mr ? `<span class="o-why" id="own-max-raised">配車表に ${(mr.to_kg / 1000).toFixed(1)}t の記録があったので、${esc(mr.cls)}の最大積載量の仮定を ${(mr.from_kg / 1000).toFixed(1)}t から ${(mr.to_kg / 1000).toFixed(1)}t に上げました（${mr.legs} 区間が元の仮定を超えていたため。外してはいません）。</span>` : '';
    const estWord = isImport() ? '直線距離 × 1.25 の推定' : 'OSRM（地図データから道路の距離を求める無料のソフト）の推定';
    const outside = d.counts.outside_pair || 0, sh = (id) => esc((d.places[id] || {}).short || '?');
    const headHtml = `<header><div class="m-rh"><b>自社の運行 ${d.month_label ? esc(d.month_label) : d.mon + ' 月'}</b><span class="m-rsub">${esc(d.company.name)}・車両 ${d.vehicles.length} 台${periodNote(d)}</span><button type="button" class="btn-link o-toguide" data-act="to-guided">手順に沿って進める</button></div></header>`;
    const restHtml = `${readq}<div class="o-tiles">${tiles}</div>
      <details class="o-fm" id="own-fm"><summary>3 つの数字の式を見る</summary><ul>
        <li><b>実車率</b>　${esc(ar.formula)}</li><li><b>大型の積載率</b>　${esc(lr.formula)}</li><li><b>大型の積載効率</b>　${esc(le.formula)}</li></ul></details>
      <details class="o-fm o-gloss" id="own-gloss"><summary>用語の説明</summary><ul>${Object.entries(OwnMonth.GLOSS).map(([k, v]) => `<li><b>${esc(k)}</b>　${esc(v)}</li>`).join('')}</ul></details>
      <p class="o-caution">${esc(n.caution)}</p>
      <p class="o-unk">分からない区間 ${d.counts.unknown} 区間（${pc(d.counts.unknown_ratio)}）は、実車率に入れていません。
        ${assumed ? `<label class="o-large">大型の最大積載量は登録が無いので <input id="own-large" class="ctl sm" type="number" min="1" max="40" step="0.5" value="${largeShown}" aria-label="大型の最大積載量（トン）"> t と仮定しています。</label>${raisedNote}` : ''}</p>
      ${ladenHtml(d)}
      <details class="o-more" id="own-more"><summary>距離と最大積載量の決め方</summary>
        <p class="o-unk">距離は元の記録のメーター、無いときは ${estWord}です。${outside ? `主な往復（${sh(d.base)} と ${sh(d.dock)}）に入らない区間 ${outside} 区間は、下の表に入れていません（実車率には入れています）。` : ''}
        ${assumed ? `<span class="o-why">この ${largeShown} t は、車検証の最大積載量が分からないときの仮定です。比べる画面の 10 t ウイングの 13,500 kg は、公表されている 1 車種の荷室の例の最大積載量で、この会社の車の値とは別です。車ごとの値は、確認のカードの「この車の最大積載量を変える」で入れられます。</span>` : ''}</p>${exampleNote(d)}</details>
      <div class="o-cost"><div class="o-ch2">空車と停車の費用<small>単価を入れると出ます</small></div>
        <div class="o-cin"><label>1 km あたりの費用<span><input id="own-perkm" class="ctl sm" type="text" inputmode="decimal" autocomplete="off" value="${esc(O.cost.perKm)}" placeholder="例 30" aria-label="1 km あたりの費用（円/km）"> 円/km</span></label>
          <label>時間あたりの費用<span><input id="own-perhour" class="ctl sm" type="text" inputmode="decimal" autocomplete="off" value="${esc(O.cost.perHour)}" placeholder="例 3000" aria-label="時間あたりの費用（円/時）"> 円/時</span></label></div>
        <div id="own-cost-out">${costOutHtml(d)}</div>
        <p class="o-note">入れた単価で計算した目安です。</p></div>
      ${revenueHtml(d)}
      <div class="o-tw" id="own-tw">${tableHtml(d)}</div>
      <div class="o-foot"><div class="o-copy"><button type="button" class="btn-sm" data-copy="csv">便ごとの空車・実車を CSV でコピー</button><button type="button" class="btn-sm" data-copy="text">数字と分かったことを文でコピー</button>
          <button type="button" class="btn-sm" data-print="1">1 ページで印刷</button>
          <span class="o-copy-msg" id="own-copy-msg" role="status">${esc(O.copyMsg)}</span></div>
        <details class="o-src" id="own-copy-det" ${O.lastCopy ? 'open' : ''}><summary>コピーした内容</summary><textarea id="own-copy-ta" class="o-ta" readonly rows="5" aria-label="コピーした内容">${esc(O.lastCopy)}</textarea></details>
        <label class="f-chk"><input type="checkbox" id="own-mkt" ${O.market ? 'checked' : ''}><span>全国の荷の流れを地図に重ねる</span></label>
        <div class="o-lg"><span><i class="lg-l"></i>実車（濃い線）</span><span><i class="lg-e"></i>空車（点線）</span><span><i class="lg-u"></i>不明</span><span class="mlg-faint">太さ = 便数</span>
        <span><i class="lg-m"></i>全国の荷の流れ（薄い青）</span></div>
        <details class="o-src"><summary>全国の値の出典</summary><p>${esc(n.source)}</p></details></div>
      <div class="o-printsheet" id="own-printsheet">${printHtml(d)}</div>`;
    // 手順に沿う画面（guided）では、相談の欄だけを前に出し、そのほかは閉じた「詳しく」に入れる。左の欄（読んだファイル）もその中へ移す
    el.innerHTML = O.guided
      ? `<div id="neg-slot" class="neg-slot"></div><details class="o-adv" id="own-adv" ${O.advOpen ? 'open' : ''}><summary>読んだファイルと今月の数字を見る</summary><div id="own-adv-slot"></div>${restHtml}</details>`
      : `${headHtml}<div id="neg-slot" class="neg-slot"></div>${restHtml}`;
    mountLeft();
    const cb = q('#own-mkt');
    if (cb) cb.addEventListener('change', () => { O.market = cb.checked; });
    Negotiate.mount(q('#neg-slot'), negCtx());
  }

  // 帰り便の提案（web/negotiate.js）に渡すもの。読み込みに失敗したときの理由は、サンプルに切り替えずにそこへ出す
  function pickFile() {
    O.dropMsg = '';
    const f = q('#own-file');
    if (f) { f.click(); return; }
    const t = document.createElement('input');
    t.type = 'file'; t.accept = '.csv,.txt,text/csv'; t.multiple = true; t.hidden = true;
    t.addEventListener('change', () => { const l = [...t.files]; t.remove(); importFiles(l); });
    document.body.appendChild(t); t.click();
  }
  const negCtx = () => ({ data: O.data, mode: O.mode, error: O.dropMsg, useSample: () => backToSample(), pickFile, guided: O.guided,
    onFocus: (f) => setFocus(f), trip: () => ({ places: O.data.places, vehicles: O.data.vehicles, route: O.data.route, base: O.data.base, dock: O.data.dock }),
    // 「最初の画面に戻る」と「全部を見る」は、画面の上の案内の帯（#guide-bar）にあるので、手順の欄には出さない（同じ操作を 2 か所に置かない）
    onStep: (n) => { const r = q('#own-right'); if (r) r.dataset.step = String(n); if (O.step !== n) { const prev = O.step; O.step = n; try { syncMarkers(); stepCamera(prev, n); map.triggerRepaint(); } catch (e) { /* 地図が無いとき */ } } } });

  /* ------------------------------------------------------------ 手順に沿う画面（guided）と全部を見る画面（full）
     guided: #v=own&g=empty / g=propose と、1 枚版。右の欄に「データを選ぶ → 空いた帰りを見る → 荷主に相談する」を出し、左の欄は右の「詳しく」の中へ移す。
     full: g が無い #v=own。機能は全部出す（自由に触る画面）。 */
  const GOALS = ['empty', 'propose'];
  function mountLeft() {
    const v = q('#own-view'), slot = q('#own-adv-slot');
    if (!O.guided || !slot || !v) return;
    slot.appendChild(v);
    const n = q('#own-notes');
    if (n) slot.appendChild(n);
  }
  function parkLeft() {
    const v = q('#own-view'), sheet = q('#own-sheet');
    if (!v || !sheet || v.parentNode === sheet) return;
    sheet.insertBefore(v, sheet.firstChild);
    const n = q('#own-notes');
    if (n) sheet.appendChild(n);
  }
  function syncGuidedClass() {
    const sh = q('#own-sheet');
    if (sh) sh.classList.toggle('own-guided', !!(O.guided && O.active));
    document.body.classList.toggle('own-guided', !!(O.guided && O.active));
    // 手順に沿う画面では、Tab で地図の部品（地図の本体・出典のリンク）を通らず、手順の欄から始める（地図はマウスと指で動かせる）
    const on = !!(O.guided && O.active);
    for (const e of document.querySelectorAll('.maplibregl-canvas, .maplibregl-ctrl-attrib a')) { if (on) e.setAttribute('tabindex', '-1'); else if (e.classList.contains('maplibregl-canvas')) e.setAttribute('tabindex', '0'); else e.removeAttribute('tabindex'); }
  }
  function setGuided(on, goal) {
    const was = O.guided;
    O.guided = !!on;
    if (goal !== undefined) { O.goal = on ? (GOALS.includes(goal) ? goal : 'empty') : ''; Negotiate.setGoal(O.goal); }
    if (on && O.tab === 'wait') O.tab = 'import';
    if (was === O.guided && goal === undefined) return;
    syncGuidedClass(); syncMode();
    if (!O.loaded) return;
    renderLeft(); renderRight(); writeHash();
    if (O.active && was !== O.guided) { WaitingMap.show(false); fit(true); }
  }
  // アドレスの g（empty・propose）を読む。無い・ほかの値は全部を見る画面
  function goalFromHash(h) { return new URLSearchParams(String(h || '').replace(/^#/, '')).get('g') || ''; }
  function onHashChange() {
    const p = new URLSearchParams(location.hash.replace(/^#/, ''));
    if (p.get('v') !== 'own') return;
    const g = p.get('g') || '';
    if (GOALS.includes(g)) { if (!O.guided || O.goal !== g) setGuided(true, g); } else if (O.guided) setGuided(false, '');
  }
  window.addEventListener('hashchange', onHashChange);


  /* ------------------------------------------------------------ 荷待ちを入れる（SPEC §23）
     計算はブラウザの中だけで行う（web/waiting_calc.js の WaitingShared.calc。単独ページ waiting.html と同じ関数、SPEC §31.1）。サーバーの /api/waiting は呼ばない。
     告示の表は data/waiting_tariff.json（静的なファイル）。入れた記録は外に送らない。 */

  const W_COLS = [
    { k: 'date', t: '日付', ph: '10/5', w: 76 }, { k: 'vehicle_no', t: '車番', w: 58 },
    { k: 'vehicle_class', t: '車種', sel: ['', '小型', '中型', '大型', 'トレーラー'], w: 78 },
    { k: 'partner', t: '契約の相手', w: 118 }, { k: 'place', t: '場所', w: 104 },
    { k: 'arrive', t: '到着', ph: '09:00', w: 52 }, { k: 'start', t: '荷役開始', ph: '09:30', w: 52 },
    { k: 'end', t: '荷役終了', ph: '09:52', w: 52 }, { k: 'depart', t: '出発', ph: '10:00', w: 52 },
    { k: 'handler', t: '荷役をした人', sel: ['', '運転者', '荷主側'], w: 76 },
    { k: 'method', t: '荷役の方法', sel: ['', '手積み', 'フォークリフト等'], w: 96 },
    { k: 'shipper_fault', t: '荷主の都合で待ったか', sel: ['', 'はい', 'いいえ'], w: 66 },
    { k: 'appointed', t: '指定の到着時刻', ph: '08:30', w: 56 },
    { k: 'side_work', t: '荷役以外の作業', w: 110 }, { k: 'side_min', t: '附帯業務の分', ph: '20', w: 52 },
    { k: 'post_wait', t: '荷役のあとの待ちを荷主等の都合として数える', sel: ['', 'はい'], w: 124 },
  ];
  const W_PROTECT = [
    '参考額は告示の表に当てた計算です。請求するか、いくらにするかは運送会社が決めます。法的な判断は弁護士・運輸支局などに確かめてください',
    '入れた記録はこの PC の中だけで計算し、外に送りません',
  ];
  const W_BASIS_QUOTE = '貸切運賃料金適用方（参考例）第 21 項（Q&A 集に収録）は、待機時間に「荷主による積込み・取卸しの時間を含む」とし、30 分を超える部分に待機時間料を収受するとしています。荷主側が荷役をした行は、その時間も待機に入れています。';
  const W_BASIS = [
    W_BASIS_QUOTE,
    '書面交付の法定事項に、荷役作業・荷役以外の作業と対価が入ります（改正法 Q&A 問2-2・2-3）。',
    '標準運送約款 第 34 条（標準貨物自動車運送約款）: 「到着後、荷送人又は荷受人の責により待機した時間」について待機時間料を収受できます。荷役のあとの待ち（書類待ちなど）がこれに当たるかは、契約の相手と確かめてください。既定では数えず、行ごとに「荷役のあとの待ちを荷主等の都合として数える」を はい にした行だけ、出発 − 荷役終了 を待機に足します（30 分の差し引きは 1 か所ごとに 1 回、2 時間の境は待機の始まりから）。',
    '取適法では、長時間の待機の費用を負担しないことが違反になり得ます（Q42・Q132。規模の基準に当たる場合）。',
    'トラック・物流Gメンの情報提供の窓口があります（荷主には元請けを含みます）。',
    '数え方は標準的運賃（2024 年 3 月 22 日告示）の別紙の表と、Q&A 集 Q29（到着時刻から数える。最初の 30 分を差し引く）です。積込料・取卸料の数え方は告示の文言からの読み取りで、Q&A の例で確かめたのは待機時間料だけです。',
  ];
  const W_PROCESS = [
    '次回からの契約の書面に、待機時間料と積込料・取卸料の欄を入れてもらうよう頼みます。過去分を求めるかどうかは、運送会社が決めます。',
    '国交省のリーフレットは、記録を待機時間料などを収受する根拠にできると書いています。',
    '取適法では、相手（元請など）が規模の基準（資本金か従業員数）に当たるとき、公正取引委員会・中小企業庁・国土交通大臣に知らせたことを理由にした取引の停止などの不利益な扱いが禁じられています（第 5 条第 1 項第 7 号）。相手が基準に当たらないときは使えません。トラック・物流Gメンの窓口への情報提供がこれに当たるかの公式の説明は確かめていません。',
    'トラック・物流Gメンの窓口: https://truck-logistics-gmen.mlit.go.jp/s/',
    '指定の到着時刻より早く着いたときは、指定の時刻から待機を数えています（控えめな扱い）。附帯業務は記録して一覧に出しますが、告示で実費として収受とされているため、参考額は付けていません。',
  ];
  const W_DEFAULT_FAULT_NOTE = '荷主等の都合の欄が無いので『はい』として計算しました。違う行は『いいえ』に変えてください';
  const W_POST_WAIT_NOTE = '「荷役のあとの待ちを荷主等の都合として数える」を はい にした行は、出発 − 荷役終了 も待機の分に足しています。標準運送約款 第 34 条の「到着後、荷送人又は荷受人の責により待機した時間」に当たるかは、契約の相手と確かめてください。';
  const blankRow = () => Object.fromEntries(W_COLS.map((c) => [c.k, '']));
  const isBlank = (r) => W_COLS.every((c) => !String(r[c.k] || '').trim());
  const hm = (m) => (m ? `${Math.floor(m / 60)} 時間 ${m % 60} 分` : '0 分');
  const yen = (v) => `${n0(v)} 円`;

  // 全角・半角の違いを同じ相手として数える（サーバーの計算と同じ正規化）
  const nzp = (s) => String(s == null ? '' : s).normalize('NFKC').replace(/\s+/g, ' ').trim();

  function bulkOptions() {
    const names = [...new Set(W.rows.filter((r) => !isBlank(r)).map((r) => nzp(r.partner)).filter(Boolean))];
    return '<option value="">すべての行</option>' + names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
  }

  function bulkRefresh() {
    const sel = q('#w-bulk-partner');
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = bulkOptions();
    sel.value = [...sel.options].some((o) => o.value === cur) ? cur : '';
  }

  // 選んだ相手（「すべての行」なら全部）の行の「荷主の都合で待ったか」を まとめて はい / いいえ にする
  function bulkFault(val) {
    const sel = q('#w-bulk-partner'), who = sel ? sel.value : '';
    let n = 0;
    W.rows.forEach((r) => { if (!isBlank(r) && (!who || nzp(r.partner) === who)) { r.shipper_fault = val; n += 1; } });
    W.bulkMsg = n ? `${n} 行を『${val}』にしました` : '対象の行がありません';
    renderLeft(); waitCalc();
  }

  // 空欄の欄を、すぐ上の入っている値で埋める（Excel の結合セルを貼ると下の行が空になるため）
  function fillDown(key, label) {
    let last = '', n = 0;
    W.rows.forEach((r) => {
      if (isBlank(r)) return;
      if (String(r[key] || '').trim()) last = r[key];
      else if (last) { r[key] = last; n += 1; }
    });
    W.bulkMsg = n ? `${label}の空欄 ${n} か所を、上の行の値で埋めました` : `${label}の空欄はありません`;
    renderLeft(); waitCalc();
  }

  function waitInit() { if (!W.rows.length) W.rows = [blankRow(), blankRow(), blankRow()]; }

  // 例の記録（架空）は waiting.html と同じもの（web/waiting_calc.js）。参考額 46,780 円など、地図の画面の数字が単独ページの例と一致する（SPEC §30.4）
  const exampleRows = () => WaitingShared.exampleRows(W.year).map((r) => ({ ...blankRow(), ...r }));

  function waitRowHtml(r, i) {
    const cells = W_COLS.map((c) => {
      const v = r[c.k] || '';
      if (c.sel) return `<td><select class="w-in${r._draft && !v ? ' w-need' : ''}" data-i="${i}" data-k="${c.k}" aria-label="${esc(c.t)}">${(c.sel.includes(v) || !v ? c.sel : [...c.sel, v]).map((o) => `<option value="${esc(o)}"${o === v ? ' selected' : ''}>${esc(o) || '—'}</option>`).join('')}</select></td>`;
      const need = r._draft && (c.k === 'partner' || c.k === 'start');
      return `<td><input class="w-in${need ? ' w-need' : ''}" type="text" data-i="${i}" data-k="${c.k}" value="${esc(v)}" placeholder="${esc(need ? (c.k === 'partner' ? '相手を入れてください' : '要入力') : c.ph || '')}" aria-label="${esc(c.t)}" autocomplete="off"></td>`;
    }).join('');
    return `<tr data-i="${i}"${W.bad.has(i) ? ' class="bad"' : ''}><td class="w-n">${i + 1}</td>${cells}<td><button type="button" class="w-del" data-w="del" data-i="${i}" aria-label="${i + 1} 行目を消す">×</button></td></tr>`;
  }

  function waitErrsHtml() {
    if (W.err) return `<p class="w-err">${esc(W.err)}</p>`;
    const r = W.res;
    if (!r) return '';
    const rowNotes = r.rows.filter((x) => x.notes && x.notes.length).map((x) => x.notes.map((m) => `<div>表の ${(W.idxMap[x.row - 1] ?? x.row - 1) + 1} 行目: ${esc(m)}</div>`).join('')).join('');
    const notesHtml = rowNotes ? `<div class="w-note">${rowNotes}</div>` : '';
    if (!r.errors.length) return notesHtml;
    return notesHtml + `<div class="w-errs"><b>読めなかった行 ${new Set(r.errors.map((e) => e.row)).size} 行（計算から外しています）</b>` +
      r.errors.map((e) => `<div>表の ${(W.idxMap[e.row - 1] ?? e.row - 1) + 1} 行目: ${esc(e.message === '契約の相手がありません' ? '契約の相手を入れてください' : e.message)}</div>`).join('') + '</div>';
  }

  function waitHtml() {
    waitInit();
    const years = []; for (let y = 2024; y <= 2027; y++) years.push(y);
    const nd = isImport() ? OwnMonth.draftRows(O.data).length : 0;
    const needP = W.rows.some((r) => r._draft && !String(r.partner || '').trim());
    const draftBar = nd ? `<div class="w-bar w-draft"><button type="button" class="btn-primary sm" data-w="from-stops">デジタコの停車から下書きを入れる（30 分以上・${nd} 件）</button>
        ${needP ? '<label>契約の相手が空の行にまとめて入れる<input id="w-draft-partner" class="ctl sm" type="text" autocomplete="off" aria-label="契約の相手（下書きの行にまとめて入れる）"></label><button type="button" class="btn-sm" data-w="draft-partner">入れる</button>' : ''}
        <small>日付・車番・場所・到着・出発は停車の記録から入れます。契約の相手、荷役開始、荷主の都合で待ったかは、停車の記録に無いので空欄です。相手を入れてください。</small></div>` : '';
    return `<p class="w-lead">運送を頼まれた相手ごとに、荷待ち・荷役の記録を入れます。1 行 = 1 か所での 1 回の荷待ち・荷役です。</p>
      <div class="w-bar"><label>年（M/D で入れた日付に使います）<select id="w-year" class="ctl sm">${years.map((y) => `<option value="${y}"${y === W.year ? ' selected' : ''}>${y} 年</option>`).join('')}</select></label>
        <button type="button" class="btn-sm" data-w="example">例を入れる</button><button type="button" class="btn-sm" data-w="clear">表を空にする</button></div>${draftBar}
      <div class="w-tw"><table class="w-tbl" aria-label="荷待ち・荷役の記録の表"><thead><tr><th>#</th>${W_COLS.map((c) => `<th style="min-width:${c.w}px">${esc(c.t)}</th>`).join('')}<th></th></tr></thead>
        <tbody id="w-tbody">${W.rows.map(waitRowHtml).join('')}</tbody></table></div>
      <div class="w-bar"><button type="button" class="btn-sm" data-w="add">行を足す</button><small>時刻は HH:MM（日付をまたぐ時刻は翌日として扱います）。荷役終了は荷役をしたときに入れます。指定の到着時刻と附帯業務は、あるときだけ入れます。</small></div>
      <div class="w-bar" id="w-bulk">
        <label>相手<select id="w-bulk-partner" class="ctl sm" aria-label="まとめて変える相手">${bulkOptions()}</select></label>
        <button type="button" class="btn-sm" data-w="fault-yes">この相手の行をすべて荷主等の都合にする</button>
        <button type="button" class="btn-sm" data-w="fault-no">この相手の行をすべて荷主等の都合でないにする</button>
        <button type="button" class="btn-sm" data-w="fill-partner">契約の相手を下の行に埋める</button>
        <button type="button" class="btn-sm" data-w="fill-class">車種を下の行に埋める</button>
        <small id="w-bulk-msg">${esc(W.bulkMsg)}</small></div>
      <p class="w-note" id="w-paste-note">${esc(W.pasteNote).replace(/\n/g, '<br>')}</p>
      <div id="w-errs-box">${waitErrsHtml()}</div>
      <details class="o-det" open><summary>Excel から貼り付ける</summary>
        <p class="w-small">Excel の表をコピーして貼り付けます（タブ区切り）。1 行目が見出しなら、列の順は問わず見出しの名前で読みます。見出しが無いときだけ、列の順は上の表と同じにしてください。日付は 10月5日・R8.10.5・2026/10/5、時刻は 9:00:00・9時00分・0900、車種は 4t車・4トン・中型車、荷主等の都合は ○ / × でも読みます。</p>
        <textarea id="w-paste" class="w-paste" rows="3" placeholder="ここに貼り付け" aria-label="Excel から貼り付ける欄"></textarea>
        <div class="w-bar"><button type="button" class="btn-sm" data-w="paste">貼り付けた内容を表に入れる</button><span id="w-paste-msg" class="w-small">${esc(W.pasteMsg)}</span></div></details>`;
  }

  function partnerRows(name) { return W.res ? W.res.rows.filter((r) => r.partner === name) : []; }

  function draftHtml(p) {
    const t = W.res.tariff, rows = partnerRows(p.partner);
    const classes = [...new Set(rows.map((r) => r.vehicle_class))];
    const methods = [...new Set(rows.filter((r) => r.handler === '運転者' && r.method).map((r) => r.method))];
    const rec = rows.map((r) => `<tr><td>${esc(r.date)}</td><td>${esc(r.vehicle_no)}</td><td>${esc(r.vehicle_class)}</td><td>${esc(r.place)}</td><td>${esc(r.arrive)}</td><td>${esc(r.start)}</td><td>${esc(r.end)}</td><td>${esc(r.handler)}</td>
      <td class="num">${r.wait_min}${r.handler === '荷主側' && r.wait_min > 0 && r.end ? '<small>荷主側の荷役を待機に含む</small>' : ''}${r.post_wait_min > 0 ? `<small>荷役のあとの待ち ${r.post_wait_min} 分を含む</small>` : ''}${r.wait_base_min > 0 ? `<small>（指定 ${esc(r.appointed)} から）</small>` : ''}</td><td class="num">${r.handling_min}</td><td>${esc(r.side_work)}${r.side_min ? ` ${r.side_min} 分` : ''}</td><td class="num">${n0(r.amount)}</td></tr>`).join('');
    const rate = classes.map((c) => `<tr><td>${esc(t.class_labels[c] || c)}</td><td class="num">${n0(t.waiting.normal[c])}</td><td class="num">${n0(t.waiting.over_2h[c])}</td>${methods.map((m) => `<td class="num">${n0(t.handling[m].normal[c])} / ${n0(t.handling[m].over_2h[c])}</td>`).join('')}</tr>`).join('');
    return `<h3>荷待ち・荷役に関する協議のお願い（下書き）</h3>
      <dl class="w-dl"><dt>相手</dt><dd>${esc(p.partner)} 御中</dd><dt>期間</dt><dd>${esc(p.date_from)} 〜 ${esc(p.date_to)}（${p.count} 回）</dd>
        <dt>差出人</dt><dd>（運送会社の名称・担当者を書き入れてください）</dd></dl>
      <h4>記録の一覧</h4>
      <div class="w-recw"><table class="w-rec"><thead><tr><th>日付</th><th>車番</th><th>車種</th><th>場所</th><th>到着</th><th>荷役開始</th><th>荷役終了</th><th>荷役をした人</th><th>待機（分）</th><th>荷役（分）</th><th>附帯業務（記録のみ）</th><th>参考額（円）</th></tr></thead><tbody>${rec}</tbody></table></div>
      <h4>参考額の計算のしかた</h4>
      <p>参考額の合計 <b class="num">${yen(p.amount)}</b>（税抜。待機時間料 ${yen(p.wait_fee)}、積込料・取卸料 ${yen(p.handling_fee)}）。待機の合計 ${hm(p.wait_minutes)}、運転者の荷役の合計 ${hm(p.handling_minutes)}。</p>
      <p class="w-small">標準的運賃（${esc(t.applies_from)} 告示）の別紙の表に当てた額です。待機は到着時刻（指定の到着時刻があって実際の到着が早いときは指定の時刻）から数え、最初の ${t.free_minutes} 分を差し引き、${t.unit_minutes} 分までごとに通常の額、到着から ${t.threshold_minutes / 60} 時間を超える部分は ${t.unit_minutes} 分までごとに 2 時間超の額です（Q&A 集 Q29）。荷主側が荷役をした時間も待機に入れています。出典: ${esc(t.source_url)}</p>
      <p class="w-small">${esc(W_BASIS_QUOTE)}</p>
      ${rows.some((r) => r.post_wait) ? `<p class="w-small">${esc(W_POST_WAIT_NOTE)}</p>` : ''}
      <div class="w-recw"><table class="w-rec"><thead><tr><th>使った車種の単価（税抜・円）</th><th>待機時間料<small>${t.unit_minutes} 分までごと</small></th><th>2 時間超</th>${methods.map((m) => `<th>${esc(m)}の積込料・取卸料<small>通常 / 2 時間超</small></th>`).join('')}</tr></thead><tbody>${rate}</tbody></table></div>
      ${WaitingMap.consultHtml(W.res, p.partner)}
      <h4>次の契約の書面（運送引受書）に入れる欄の案</h4>
      <ul class="w-list"><li>待機時間料: 荷主等の都合による待機について、到着（指定の到着時刻があるときは、その時刻）から ${t.free_minutes} 分を超えた時間に、${t.unit_minutes} 分までごとに上の単価（到着から 2 時間を超える部分は 2 時間超の単価）。</li>
        <li>積込料・取卸料: 運転者が荷役をしたとき、荷役の時間に ${t.unit_minutes} 分までごとに上の単価（手積み / フォークリフト等で別）。</li>
        <li>荷主等の都合でない待機（予期せぬ渋滞など）は対象にしません。</li></ul>
      <p class="w-sentence">荷待ちが残る場合に備えて、次回からの契約の書面に、待機時間料と積込料・取卸料の扱いを記載いただけるようご相談します。下記の荷待ち・荷役について、標準的運賃の表に当てた参考額は計 ${n0(p.amount)} 円（税抜）です。</p>`;
  }

  const processHtml = () => `<aside class="w-process no-print" aria-label="協議の進め方"><b>協議の進め方</b><ul>${W_PROCESS.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></aside>`;

  // 結果が変わったときだけ、地図の点を作り直す。点を選んだときは右の欄だけを作り直す
  function syncWaitMap() {
    if (W.painted !== W.res) {
      W.painted = W.res;
      WaitingMap.paint(W.res);
      if (O.active && O.tab === 'wait') fitPoints(false);
    }
    WaitingMap.show(O.active && O.tab === 'wait');
  }

  // 説明文の自動作成（SPEC §53）。ai_explain.js が無いとき・サーバーが使えないと言うときは、何も出さない
  function aiFactsOf(p) {
    const t = W.res.tariff, rows = partnerRows(p.partner);
    return { partner: p.partner, period: `${p.date_from} 〜 ${p.date_to}`, count: p.count,
      rows: rows.map((r, i) => ({ row: i + 1, date: r.date, place: r.place, arrive: r.arrive, start: r.start, end: r.end, handler: r.handler, wait_min: r.wait_min, handling_min: r.handling_min, amount: r.amount })),
      totals: { amount_yen_ex_tax: p.amount, wait_total: hm(p.wait_minutes), handling_total: hm(p.handling_minutes), wait_fee_yen: p.wait_fee, handling_fee_yen: p.handling_fee },
      basis: { tariff_applies_from: t.applies_from, unit_minutes: t.unit_minutes, free_minutes: t.free_minutes, quote: W_BASIS_QUOTE } };
  }
  function mountAiExplain(el) {
    const box = el.querySelector('#ai-box');
    const r = W.res;
    if (!box || !r || !r.by_partner.length) return;
    box.innerHTML = '<p class="w-small">説明文の自動作成はサーバーで開いたときだけ使えます（今は型どおりの書面です）</p>';   // 使えるときは ai_explain.js が置き換える
    if (!window.AiExplain) return;
    const p = r.by_partner.find((x) => x.partner === W.partner) || r.by_partner[0];
    window.AiExplain.mount(box, () => aiFactsOf(p));
  }

  function waitRightHtml() {
    const r = W.res;
    const protect = `<div class="w-protect"><b>守る線</b>${W_PROTECT.map((x) => `<p>${esc(x)}</p>`).join('')}</div>`;
    const basis = `<div class="w-basis"><b>根拠</b><ul>${WaitingMap.basisItems(W_BASIS, r).map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>`;
    const pageLink = `<p class="w-small wm-link no-print"><a href="waiting.html" target="_blank" rel="noopener" id="wm-open">荷待ちの書面を別の画面で開く</a>　別の画面で開きます。入れた記録は引き継がれません。</p>`;
    let mid;
    if (!r || !r.by_partner.length) {
      mid = '<p class="w-empty">まだ計算できる記録がありません。左の表に記録を入れるか、「例を入れる」を押してください。</p>';
    } else {
      const sel = r.by_partner.some((p) => p.partner === W.partner) ? W.partner : r.by_partner[0].partner;
      W.partner = sel;
      const p = r.by_partner.find((x) => x.partner === sel);
      const tb = r.by_partner.map((x) => `<tr class="${x.partner === sel ? 'on' : ''}" data-w="pick" data-p="${esc(x.partner)}"><td>${esc(x.partner)}</td><td class="num">${x.count}</td><td class="num">${hm(x.wait_minutes)}</td>
        <td class="num">${hm(x.handling_minutes)}</td><td class="num"><b>${n0(x.amount)}</b></td><td class="num">${x.over_1h_count}</td><td class="num">${x.over_2h_count}</td></tr>`).join('');
      mid = `<div class="w-sect"><div class="m-rh"><b>契約の相手ごと</b><span class="m-rsub">参考額は税抜・円</span></div>
        <table class="w-par"><thead><tr><th>契約の相手</th><th>回数</th><th title="荷主側の荷役を含む待機">待機の合計</th><th>運転者の荷役の合計</th><th>参考額</th><th title="待機（荷主側の荷役を含む）が 1 時間以上の回数">1 時間以上の荷待ち</th><th title="待機と運転者の荷役の合計が 2 時間以上の回数">2 時間以上の荷待ち・荷役</th></tr></thead><tbody>${tb}</tbody></table></div>
        ${WaitingMap.hotspotsHtml(r)}
        ${WaitingMap.mapSectionHtml(r)}
        <div class="w-sect"><div class="m-rh"><b>協議の書面の下書き</b><span class="m-rsub no-print">上の表の行を押すと相手を切り替えます</span></div>
          <div class="w-bar no-print"><button type="button" class="btn-primary sm" data-w="print">印刷して残す</button><button type="button" class="btn-sm" data-w="csv">計算結果を CSV で書き出す</button></div>
          <div id="ai-box" class="ai-box no-print"></div>
          <div class="w-draftwrap"><article id="wait-doc" class="w-doc">${draftHtml(p)}</article>${processHtml()}</div></div>`;
    }
    return `<header><div class="m-rh"><b>荷待ち・荷役の記録から協議の書面を作る</b><span class="m-rsub">${r ? esc(`標準的運賃 ${r.tariff.applies_from} 告示`) : ''}</span></div></header>
      <div class="w-rbody">${pageLink}${protect}${mid}${basis}</div>`;
  }

  function collectRows() {
    W.idxMap = [];
    const rows = [];
    W.rows.forEach((r, i) => { if (!isBlank(r)) { const { _draft, ...rest } = r; rows.push(rest); W.idxMap.push(i); } });
    return rows;
  }

  // 告示の表（静的なファイル）。1 度だけ読む。記録は含まない
  let tariffP = null;
  const loadTariff = () => (tariffP = tariffP || fetch('data/waiting_tariff.json').then((r) => { if (!r.ok) throw new Error('告示の表を読めませんでした'); return r.json(); }).catch((e) => { tariffP = null; throw e; }));

  function localCalc(rows, tariff) {
    const res = WaitingShared.calc(rows, W.year, tariff);
    res.tariff = tariff;
    return res;
  }

  async function waitCalc() {
    const rows = collectRows(), tok = ++W.tok;
    if (!rows.length) { W.res = null; W.err = ''; waitRefresh(); return; }
    try {
      const tariff = await loadTariff();
      if (tok !== W.tok) return;
      const res = localCalc(rows, tariff);
      W.res = res; W.err = '';
    } catch (e) { if (tok !== W.tok) return; W.res = null; W.err = e.message; }
    waitRefresh();
  }

  function waitRefresh() {
    W.bad = new Set(W.res ? W.res.errors.map((e) => W.idxMap[e.row - 1]) : []);
    document.querySelectorAll('#w-tbody tr').forEach((tr) => tr.classList.toggle('bad', W.bad.has(Number(tr.dataset.i))));
    const box = q('#w-errs-box');
    if (box) box.innerHTML = waitErrsHtml();
    bulkRefresh();
    renderRight();
  }

  const waitSoon = () => { clearTimeout(W.tm); W.tm = setTimeout(waitCalc, 350); };

  function waitCsv() {
    const heads = ['日付', '車番', '車種', '契約の相手', '場所', '到着', '荷役開始', '荷役終了', '出発', '荷役をした人', '荷役の方法', '荷主の都合で待ったか', '指定の到着時刻', '荷役以外の作業', '附帯業務の分', '荷役のあとの待ちを荷主等の都合として数える', '待機の数え始め', '待機（分）', '運転者の荷役（分）',
      '待機時間料 通常（回）', '待機時間料 2時間超（回）', '待機時間料（円）', '積込料・取卸料 通常（回）', '積込料・取卸料 2時間超（回）', '積込料・取卸料（円）', '参考額（円・税抜）'];
    const cell = (v) => { const t0 = String(v ?? ''); const t = /^[=+\-@\t\r]/.test(t0) && !/^[-+]?\d+(\.\d+)?$/.test(t0) ? `'${t0}` : t0; return /[",\r\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t; };
    const lines = [heads, ...W.res.rows.map((r) => [r.date, r.vehicle_no, r.vehicle_class, r.partner, r.place, r.arrive, r.start, r.end, r.depart, r.handler, r.method, r.shipper_fault ? 'はい' : 'いいえ', r.appointed, r.side_work, r.side_min || '', r.post_wait ? 'はい' : '', r.wait_from,
      r.wait_min, r.handling_min, r.wait_normal_units, r.wait_over_units, r.wait_fee, r.handling_normal_units, r.handling_over_units, r.handling_fee, r.amount])];
    const blob = new Blob(['﻿' + lines.map((l) => l.map(cell).join(',')).join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'waiting_calc.csv';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  async function waitPaste() {
    const text = q('#w-paste').value;
    if (!text.trim()) { toast('貼り付ける欄が空です'); return; }
    try {
      const pf = WaitingShared.parsePasteFull(text);
      if (pf.rows.length > WaitingShared.MAX_ROWS) throw new Error(`行が多すぎます（${WaitingShared.MAX_ROWS.toLocaleString('ja-JP')} 行まで）`);
      const inp = pf.rows;
      W.rows = inp.length ? inp.map((r) => ({ ...blankRow(), ...r })) : [blankRow(), blankRow(), blankRow()];
      W.pasteMsg = `${inp.length} 行を表に入れました`;
      W.pasteNote = (pf.defaulted.includes('shipper_fault') ? [W_DEFAULT_FAULT_NOTE] : []).concat(pf.notes || []).join('\n');
    } catch (e) { W.pasteMsg = e.message; }
    renderLeft();
    waitCalc();
  }

  // デジタコの 30 分以上の停車を、荷待ちの表の下書きの行にする。前に入れた下書きの行は置き換え、手で入れた行は残す
  function waitFromStops() {
    const rows = OwnMonth.draftRows(O.data).map((r) => ({ ...blankRow(), ...r }));
    if (!rows.length) { W.pasteNote = '30 分以上の停車がありません'; renderLeft(); return; }
    W.rows = W.rows.filter((r) => !isBlank(r) && !r._draft).concat(rows);
    W.pasteNote = `デジタコの停車 ${rows.length} 件から、${rows.length} 行の下書きを入れました。契約の相手、荷役開始、荷主の都合で待ったかは、停車の記録に無いので空欄です。相手を入れてください。入れるまで、その行は計算に入りません（読めなかった行として出ます）。いらない行は右端の × で消せます。`;
    renderLeft(); waitCalc();
  }

  function bindWait() {
    const root = q('#own-view'), right = q('#own-right');
    root.addEventListener('input', (e) => {
      const t = e.target;
      if (t.classList && t.classList.contains('w-in')) { W.rows[Number(t.dataset.i)][t.dataset.k] = t.value; waitSoon(); }
    });
    root.addEventListener('change', (e) => {
      const t = e.target;
      if (t.id === 'w-year') { W.year = Number(t.value); waitCalc(); }
    });
    for (const el of [root, right]) {
      el.addEventListener('click', (e) => {
        const wm = e.target.closest('[data-wm="pick"]');
        if (wm) { WaitingMap.select(wm.dataset.place); return; }
        const b = e.target.closest('[data-w]');
        if (!b) return;
        const a = b.dataset.w;
        if (a === 'example') { W.rows = exampleRows(); renderLeft(); waitCalc(); }
        else if (a === 'clear') { W.pasteNote = ''; W.rows = [blankRow(), blankRow(), blankRow()]; W.res = null; W.err = ''; W.bad = new Set(); renderLeft(); renderRight(); }
        else if (a === 'add') { W.rows.push(blankRow()); renderLeft(); const ins = document.querySelectorAll('#w-tbody tr:last-child .w-in'); if (ins[0]) ins[0].focus(); }
        else if (a === 'del') { W.rows.splice(Number(b.dataset.i), 1); if (!W.rows.length) W.rows.push(blankRow()); renderLeft(); waitCalc(); }
        else if (a === 'paste') waitPaste();
        else if (a === 'from-stops') waitFromStops();
        else if (a === 'draft-partner') {
          const v = ((q('#w-draft-partner') || {}).value || '').trim();
          if (!v) { toast('契約の相手を入れてください'); return; }
          W.rows.forEach((r) => { if (r._draft && !String(r.partner || '').trim()) r.partner = v; });
          renderLeft(); waitCalc();
        }
        else if (a === 'fault-yes') bulkFault('はい');
        else if (a === 'fault-no') bulkFault('いいえ');
        else if (a === 'fill-partner') fillDown('partner', '契約の相手');
        else if (a === 'fill-class') fillDown('vehicle_class', '車種');
        else if (a === 'pick') { W.partner = b.dataset.p; renderRight(); }
        else if (a === 'print') window.print();
        else if (a === 'csv') { if (W.res && W.res.rows.length) waitCsv(); }
      });
    }
  }

  /* ------------------------------------------------------------ 操作 */

  /* ------------------------------------------------------------ 自社のファイルの取り込み（SPEC §35.2）
     CSV はブラウザの中で読み、OwnCalc.analyze（web/own_calc.js）で計算する。fetch も XMLHttpRequest も使わない（/api/ へは送らない）。 */

  const MAX_BYTES = 20 * 1024 * 1024;

  // 市区町村の表（名前・都道府県・代表点）。荷待ちの点と同じ S.idx と Muni.state.d.pt から作る
  function munisList() {
    if (O.munis) return O.munis;
    if (typeof S === 'undefined' || !S.idx || typeof Muni === 'undefined' || !Muni.state || !Muni.state.d) return null;
    const ix = S.idx, pt = Muni.state.d.pt || [];
    O.munis = ix.codes.map((c, i) => {
      const p = pt[i] || [ix.lon[i], ix.lat[i]];
      return { code: c, name: ix.names[i], pref: ix.prefs[i], lon: p ? p[0] : null, lat: p ? p[1] : null };
    });
    return O.munis;
  }

  // 主な往復の道路の形。都道府県どうしのトラックの経路（routes_pairs.json）があれば使い、なければ代表点どうしの直線
  async function attachRoute(d) {
    const a = d.places[d.base], b = d.places[d.dock];
    if (!a || !b || a.lon == null || b.lon == null) { d.route = { path: null, osrm_km: null, note: '' }; return; }
    let path = [[a.lon, a.lat], [b.lon, b.lat]], note = '市区町村の代表点どうしを直線で結んでいます（道路の形ではありません）';
    try {
      const pairs = typeof Rt !== 'undefined' ? await Rt.loadPairs() : {};
      const fwd = pairs[`${a.pref}>${b.pref}`], rec = fwd || pairs[`${b.pref}>${a.pref}`], m = rec && rec.m && rec.m.truck;
      if (m && m.legs && a.pref !== b.pref) {
        let pts = [];
        for (const lg of m.legs) {
          const c = lg.c;
          let x = c[0], y = c[1];
          pts.push([x / 1e4, y / 1e4]);
          for (let i = 2; i < c.length; i += 2) { x += c[i]; y += c[i + 1]; pts.push([x / 1e4, y / 1e4]); }
        }
        if (!fwd) pts.reverse();
        const step = 5, sm = pts.filter((_, i) => i % step === 0);
        if ((pts.length - 1) % step) sm.push(pts[pts.length - 1]);
        path = [[a.lon, a.lat], ...sm.map((p) => [Math.round(p[0] * 1e4) / 1e4, Math.round(p[1] * 1e4) / 1e4]), [b.lon, b.lat]];
        note = '道路の形は 都道府県間のトラックの経路（OSRM は道路の経路を求める無料のソフト、OpenStreetMap は地図データ）。両端は地点に延ばしています';
      }
    } catch (e) { console.warn(e); }
    d.route = { path, osrm_km: null, note };
  }

  function showDropMsg(t) {
    O.dropMsg = t;
    const m = q('#own-drop-msg');
    if (m) { m.textContent = t; m.hidden = !t; }
    Negotiate.update({ error: t });
    if (t) toast(t);
  }

  function setData(d, mode, keep) {
    O.data = d; O.mode = mode;
    Negotiate.picked(mode);
    if (mode === 'import') { O.fileOrder = d.file_order || null; O.prevHaishaUsed = d.haisha_choice && d.haisha_choice.used ? d.haisha_choice.used.slice() : null; }
    if (!keep) { O.dec = {}; O.fixTxt = {}; O.open = new Set(); O.yearPick = null; O.cmpId = ''; }
    O.copyMsg = ''; O.lastCopy = ''; O.board = null; O.tmr = null;
    buildGeo();
    clearMarkers();
    renderLeft(); renderRight(); syncMarkers();
    if (O.active) fit(true);
  }

  // 確認のカードで押した決定（鍵 → { act, text }）。取り込んだ結果の計算に渡す
  function decisionsOf() {
    const m = {};
    for (const [k, act] of Object.entries(O.dec)) m[k] = { act, text: O.fixTxt[k] || '' };
    return m;
  }

  // 車ごと・車種ごとの最大積載量（kg）を OwnCalc.analyze の options に渡す形にする。無ければ何も渡さない
  function maxPayloadOpts() {
    const o = {}, m = O.maxPayload;
    if (Object.keys(m.by_plate).length) o.max_payload_by_plate = { ...m.by_plate };
    if (Object.keys(m.by_class).length) o.max_payload_by_class = { ...m.by_class };
    return o;
  }

  async function analyzeFiles() {
    const d = OwnCalc.analyze(O.files, { munis: munisList(), merge_places: O.mergePlaces !== false, userAliases: O.userAliases, largeMaxLoadKg: O.largeT ? O.largeT * 1000 : undefined, year: O.yearPick || undefined, month: O.monthPick || undefined, file_order: isImport() ? O.fileOrder || undefined : undefined, prev_haisha_used: isImport() ? O.prevHaishaUsed || undefined : undefined, decisions: decisionsOf(), column_roles: rolesForCalc(), ...maxPayloadOpts(), ...(O.haishaUse ? { haisha_use: O.haishaUse } : {}) });
    if (!d.needs_year) await attachRoute(d);
    return d;
  }

  // 落とされたファイルを読む。Shift_JIS も読める（OwnCalc.decode）
  async function importFiles(list) {
    const files = [...list];
    if (!files.length) return;
    const bad = [], got = [];
    for (const f of files) {
      if (f.size > MAX_BYTES) { bad.push(`${f.name}: 20MB を超えています`); continue; }
      if (!/\.(csv|txt|tsv)$/i.test(f.name)) { bad.push(`${f.name}: CSV ではありません（Excel のファイルは「CSV UTF-8」で保存し直してください）`); continue; }
      try {
        const r = OwnCalc.decode(await f.arrayBuffer());
        got.push({ name: f.name, text: r.text, encoding: r.encoding });
      } catch (e) { bad.push(`${f.name}: 読めませんでした`); }
    }
    if (!got.length) { showDropMsg(`読めるファイルがありません。${bad.join('。')}`); return; }
    const prev = isImport() ? O.files : [], names = new Set(got.map((g) => g.name));
    const files2 = prev.filter((p) => !names.has(p.name)).concat(got);
    await commitImport(files2, bad, null);
  }

  // 見出しの形が同じファイルで前に選んだ列の役割を探す（{ haisha, digitaco, both } にまとめる）
  function savedRoles(files) {
    const m = {};
    for (const f of files) {
      const r = OwnMonth.rolesGet(OwnMonth.headSig(f.text));
      if (r) for (const g of Object.keys(r)) m[g] = { ...(m[g] || {}), ...r[g] };
    }
    return m;
  }

  // 同じ月・同じファイルの保存があって、確認の決定か地名の対応が入っていれば、戻すか聞く（黙って戻さない）
  function offerRestore(files) {
    const sg = OwnMonth.sig(files), mo = curSummary().month;
    const old = OwnMonth.list().find((e) => e.sig === sg && e.month === mo);
    O.restoreOffer = old && (Object.keys(old.dec || {}).length || Object.keys(old.aliases || {}).length) ? old : null;
  }

  // 結果の要約・地名の対応・確認の決定・列の役割を、このブラウザに保存する。CSV の中身は保存しない。消すボタンで消した後は、次のファイルを落とすまで保存しない
  function monthSave() {
    if (!isImport() || O.noSave) return;
    const d = O.data, ro = O.restoreOffer, pick = (cur, old) => (Object.keys(cur || {}).length ? cur : (old || cur || {}));
    const sms = curSummaries();
    let ok = true;
    for (const sm of sms) ok = OwnMonth.save({ month: sm.month, sig: OwnMonth.sig(O.files), summary: sm, aliases: pick(O.userAliases, ro && ro.aliases), dec: pick(O.dec, ro && ro.dec), fixTxt: pick(O.fixTxt, ro && ro.fixTxt), yearPick: O.yearPick, roles: O.roles }) && ok;
    O.saveMsg = ok ? `${sms.map((x) => x.label).join('・')}の結果をこのブラウザに保存しました（数字の要約・地名の対応・確認の決定・列の役割だけ。CSV の中身は保存していません）` : 'このブラウザに保存できませんでした（保存が使えない設定のようです）。画面はそのまま使えます';
  }

  // 読んだファイルの一式で計算し、画面に反映する。年が決まらない配車表は、年を選ぶまで計算しない（O.pendingYear）
  async function commitImport(files2, bad, yearPick, rolesIn) {
    O.pendingRoles = null;
    const keep = O.files, keepAl = O.userAliases, keepYear = O.yearPick, keepDec = O.dec, keepFix = O.fixTxt, keepRoles = O.roles, keepOffer = O.restoreOffer, keepNoSave = O.noSave;
    const restore = () => { O.files = keep; O.userAliases = keepAl; O.yearPick = keepYear; O.dec = keepDec; O.fixTxt = keepFix; O.roles = keepRoles; O.restoreOffer = keepOffer; O.noSave = keepNoSave; };
    try {
      O.files = files2;
      if (!isImport()) O.userAliases = {};
      O.yearPick = yearPick; O.monthPick = null; O.dec = {}; O.fixTxt = {}; // 新しいファイルでは、年の選択と確認の決定を引き継がない
      O.roles = rolesIn || savedRoles(files2); O.noSave = false; O.haishaUse = null; // 列の役割は、見出しの形が同じファイルで前に選んだものだけ使う
      const d = await analyzeFiles();
      if (d.needs_year) { // 曜日に合う年が複数: 計算せず、前の表示のまま年を選んでもらう
        restore();
        O.pendingYear = { files: files2, bad, candidates: d.year_choice.candidates };
        O.tab = 'import';
        renderLeft();
        showDropMsg('配車表の年を選んでください。選ぶまで計算しません');
        return;
      }
      if (d.failed && roleNeeds(d).length) { // 区間を 1 件も読めないが、列の役割を選べば読めるかもしれない: 年を選ぶときと同じく、選ぶまで前の表示のまま役割を選んでもらう
        const roles = O.roles;
        restore();
        O.pendingRoles = { files: files2, bad, need: roleNeeds(d), roles, failed: d.failed };
        O.tab = 'import';
        renderLeft();
        showDropMsg(`${d.failed}。列の役割を選ぶと読めるかもしれません。選ぶまで計算せず、前の表示のままです`);
        return;
      }
      if (d.failed) { // 区間を 1 件も読めなかった: 取り込みの失敗として扱い、前の表示を残す
        restore();
        showDropMsg(`${d.failed}。前の表示のままです${bad.length ? '。読めなかった: ' + bad.join('。') : ''}`);
        return;
      }
      O.pendingYear = null; O.pendingRoles = null;
      O.tab = 'import';
      setData(d, 'import');
      O.yearPick = yearPick;
      offerRestore(files2);
      monthSave();
      renderLeft(); renderRight();
      const unread = bad.concat(d.files_unread.map((u) => `${u.file}: ${u.reason}`));
      showDropMsg(`ファイル ${d.files_read} 件（区間 ${(d.legs || []).length} 件）を読みました（このブラウザの中だけで計算しています）${(d.files || []).some((f) => f.no_plate_col) ? '。車番の列がないので、車両 1 台（車番の列なし）として読みました' : ''}${unread.length ? `。読めなかった ${unread.length} 件: ` + unread.join('。') : ''}`);
      writeHash();
    } catch (e) {
      console.warn(e);
      restore();
      showDropMsg(`計算できませんでした: ${e.message}`);
    }
  }

  // 取り込んだファイルを 1 つ外して、残りで計算し直す。最後の 1 つを外したらサンプルに戻る
  function removeFile(name) {
    const rest = O.files.filter((f) => f.name !== name);
    if (rest.length === O.files.length) return;
    if (!rest.length) { backToSample(); showDropMsg(`${name} を外しました。ファイルが無くなったので、サンプルに戻しました`); return; }
    commitImport(rest, [], O.yearPick).then(() => { if (O.files.length === rest.length && !O.pendingYear && !O.pendingRoles) showDropMsg(`${name} を外して、残り ${rest.length} 件で計算し直しました`); });
  }

  function chooseYear(y) {
    const p = O.pendingYear;
    if (p && y) commitImport(p.files, p.bad, y);
  }

  // 同じファイルで計算し直す（別名・大型の積載量・年・確認の決定が変わったとき）。確認の決定は引き継ぐ
  async function reanalyze() {
    try {
      const d = await analyzeFiles();
      if (d.needs_year) { showDropMsg('配車表の年を選んでください。選ぶまで計算しません'); return; }
      if (d.failed) { showDropMsg(`${d.failed}。前の表示のままです`); return; }
      setData(d, 'import', true);
      monthSave();
      renderLeft(); renderRight();
    } catch (e) { console.warn(e); showDropMsg(`計算できませんでした: ${e.message}`); }
  }

  function backToSample() {
    O.fileOrder = null; O.prevHaishaUsed = null;
    O.files = []; O.userAliases = {}; O.pendingYear = null; O.pendingRoles = null; O.haishaUse = null; O.restoreOffer = null; O.roles = {}; O.saveMsg = '';
    setData(O.sample, 'sample');
    O.dropMsg = '';
  }

  function bindDrop() {
    const z = q('#own-drop'), f = q('#own-file');
    if (!z || z.dataset.bound) return;
    z.dataset.bound = '1';
    z.addEventListener('click', () => f.click());
    z.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); f.click(); } });
    f.addEventListener('change', () => { const l = [...f.files]; f.value = ''; importFiles(l); });
    ['dragenter', 'dragover'].forEach((t) => z.addEventListener(t, (e) => { e.preventDefault(); z.classList.add('over'); }));
    z.addEventListener('dragleave', (e) => { e.preventDefault(); z.classList.remove('over'); });
    z.addEventListener('drop', (e) => { e.preventDefault(); z.classList.remove('over'); importFiles((e.dataTransfer && e.dataTransfer.files) || []); });
  }

  /* ------------------------------------------------------------ 書き出し（SPEC §35.4） */

  async function copyText(t, label) {
    O.lastCopy = t;
    let ok = false;
    try { await navigator.clipboard.writeText(t); ok = true; } catch (e) { /* 下の予備へ */ }
    if (!ok) {
      const ta = document.createElement('textarea');
      ta.value = t; ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;left:-9999px;top:0';
      document.body.appendChild(ta); ta.select();
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      ta.remove();
    }
    O.copyMsg = ok ? `${label}をコピーしました` : `${label}をコピーできませんでした。下の欄から選んでコピーしてください`;
    const m = q('#own-copy-msg'), ta2 = q('#own-copy-ta'), det = q('#own-copy-det');
    if (m) m.textContent = O.copyMsg;
    if (ta2) ta2.value = t;
    if (det) det.open = true;
    toast(O.copyMsg);
  }

  // 1 ページの印刷。body に印を付けて own.css の @media print で印刷用の欄だけを出す
  function doPrint() {
    const ps = q('#own-printsheet');
    if (ps && O.data) ps.innerHTML = printHtml(O.data);
    document.body.classList.add('own-print-sum');
    try { window.print(); } finally { setTimeout(() => document.body.classList.remove('own-print-sum'), 0); }
  }

  // 求荷求車の文のコピー。クリップボードが使えないときは、文を選択して Ctrl+C を案内する
  // 明日の空きの欄の入力を O.tmr に写す
  function tmrSync() {
    if (!O.tmr) return;
    for (const [id, k] of [['date', 'date'], ['time', 'time'], ['timeTo', 'timeTo'], ['frm', 'frm'], ['to', 'to'], ['cls', 'cls'], ['bed', 'bed'], ['size', 'size'], ['kg', 'kg']]) { const e = q('#own-tmr-' + id); if (e) O.tmr[k] = e.value; }
  }
  async function tmrCopy() {
    const ta = q('#own-tmr-ta');
    if (!ta || !O.tmr) return;
    let ok = false;
    try { await navigator.clipboard.writeText(ta.value); ok = true; } catch (e) { /* 下の予備へ */ }
    if (!ok) { ta.focus(); ta.select(); try { ok = document.execCommand('copy'); } catch (e) { ok = false; } }
    O.tmr.msg = ok ? '明日の空きの文をコピーしました' : 'コピーできませんでした。選択してあるので、Ctrl+C でコピーしてください';
    const m = q('#own-tmr-msg');
    if (m) m.textContent = O.tmr.msg;
    toast(O.tmr.msg);
  }

  async function boardCopy(idx) {
    const ta = q('#own-board-ta');
    if (!ta || !O.board) return;
    const one = Number.isInteger(idx) && O.board.items[idx] ? O.board.items[idx].text : null;
    const text = one == null ? ta.value : one;
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (e) { /* 下の予備へ */ }
    if (!ok) {
      if (one != null) { // 区間 1 つぶんだけを選択してコピーする
        const at = ta.value.indexOf(one);
        ta.focus(); ta.setSelectionRange(at, at + one.length);
      } else { ta.focus(); ta.select(); }
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    }
    O.board.msg = ok ? (one != null ? `${O.board.items[idx].label}の文をコピーしました` : '求荷求車の文をコピーしました') : 'コピーできませんでした。選択してあるので、Ctrl+C でコピーしてください';
    if (!ok) ta.focus();
    const m = q('#own-board-msg');
    if (m) m.textContent = O.board.msg;
    toast(O.board.msg);
  }

  // 用語の「?」: 押す・Enter・Space で説明を開く（マウスを乗せると title の吹き出しも出る）。別の所を押すか Esc で閉じる
  function bindTips() {
    const closeAll = (except) => document.querySelectorAll('.o-tipw.open').forEach((w) => { if (w !== except) { w.classList.remove('open'); const b = w.querySelector('.o-tip'); if (b) b.setAttribute('aria-expanded', 'false'); } });
    document.addEventListener('click', (e) => {
      const b = e.target.closest && e.target.closest('.o-tip'), w = b && b.parentElement;
      closeAll(w);
      if (b) { const on = !w.classList.contains('open'); w.classList.toggle('open', on); b.setAttribute('aria-expanded', String(on)); e.preventDefault(); }
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAll(null); });
  }

  function bindRight() {
    const root = q('#own-right');
    root.addEventListener('toggle', (e) => { if (e.target && e.target.id === 'own-adv') O.advOpen = e.target.open; }, true);
    root.addEventListener('input', (e) => {
      const t = e.target;
      if (t.id === 'own-perkm') { O.cost.perKm = t.value; renderCost(); }
      else if (t.id === 'own-perhour') { O.cost.perHour = t.value; renderCost(); }
      else if (t.id === 'own-rate') { O.rate.value = t.value; saveRate(); renderRate(); }
    });
    root.addEventListener('change', (e) => {
      if (e.target.id === 'own-rate-kind') { O.rate.kind = e.target.value === 'trip' ? 'trip' : 'km'; saveRate(); renderRate(); return; }
      if (e.target.id === 'own-large') {
        const v = Number(e.target.value);
        if (v > 0 && v <= 40) { O.largeT = v; reanalyze(); }
      }
    });
    root.addEventListener('click', (e) => {
      if (e.target.closest('[data-act="to-guided"]')) { setGuided(true, 'empty'); return; }
      if (e.target.closest('[data-act="merge-toggle"]')) { O.mergePlaces = O.mergePlaces === false; reanalyze(); return; }
      const pb = e.target.closest('button[data-print]');
      if (pb && O.tab !== 'wait') { doPrint(); return; }
      const b = e.target.closest('button[data-copy]');
      if (!b || O.tab === 'wait') return;
      if (b.dataset.copy === 'csv') copyText(OwnCalc.legsCsv(O.data), '区間の判定の CSV ');
      else copyText(OwnCalc.summaryText(O.data, OwnCalc.costs(O.data, O.cost.perKm, O.cost.perHour)), '数字と気づきの文');
    });
  }

  function draftNote() {
    const ins = O.data.insights.find((i) => i.rule === 1);
    const p = ins ? ins.next.payload : null;
    const m = O.data.mon;
    if (!p) return `${m} 月の運行から、帰り便の下書きを作りました。11/13 の帰り便 T7 はこの下書きから登録した、という設定の架空の便です。`;
    const body = p.empty === p.trips ? `帰りが ${p.empty} 回とも空でした` : `帰りが ${p.trips} 回のうち ${p.empty} 回空でした`;
    return `${m} 月は${p.weekday}曜${p.night ? 'の夜' : ''}に出た便の${body}。11/13 の帰り便 T7 はこの下書きから登録した、という設定の架空の便です。`;
  }

  // 取り込んだ自社のファイルのとき: 下書きのもとにする区間（気づきの曜日・向きに当たる空車の区間。無ければ向きだけ合う区間）
  function importDraftLeg() {
    const ins = O.data.insights.find((i) => i.rule === 1), p = ins ? ins.next.payload : null;
    const dir = p && p.dir ? p.dir : 'back';
    const ok = (l) => l.dir === dir && l.frm && l.to && l.frm !== l.to;
    const pick = (f) => O.data.legs.filter((l) => ok(l) && f(l)).sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))[0];
    return pick((l) => (!p || p.wd == null || l.cycle_wd === p.wd) && l.kind === 'empty') || pick((l) => l.kind === 'empty') || pick(() => true) || null;
  }

  const pad2 = (n) => String(n).padStart(2, '0');
  const dtLocal = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;

  // 取り込んだ区間の出発の曜日・時刻を、これから先でいちばん近いその曜日の日付に置く（日付は利用者が直す）。到着は、区間の所要時間で足す
  function draftTimes(leg) {
    const dep0 = leg.dep || (leg.arr_hint ? `${leg.date}T${leg.arr_hint}` : `${leg.date}T17:00`);
    const wd = (new Date(Date.UTC(+dep0.slice(0, 4), +dep0.slice(5, 7) - 1, +dep0.slice(8, 10))).getUTCDay() + 6) % 7;
    const skew = (Find.state && Find.state.skew) || 0;
    const dep = new Date(Date.now() + skew);
    dep.setDate(dep.getDate() + 1);
    while ((dep.getDay() + 6) % 7 !== wd) dep.setDate(dep.getDate() + 1);
    dep.setHours(+dep0.slice(11, 13), +dep0.slice(14, 16), 0, 0);
    let mins = 17 * 60;
    if (leg.dep && leg.arr && leg.arr > leg.dep) mins = Math.round((Date.parse(leg.arr + ':00Z') - Date.parse(leg.dep + ':00Z')) / 60000);
    return { dep: dtLocal(dep), arr: dtLocal(new Date(dep.getTime() + mins * 60000)), wd };
  }

  // 登録の欄に、取り込んだ区間の発地・着地・曜日・時刻を入れる。入れた内容を文で返す
  function fillRegisterFromImport() {
    const leg = importDraftLeg();
    const a = leg && O.data.places[leg.frm], b = leg && O.data.places[leg.to];
    if (!leg || !a || !b || !a.pref || !b.pref) {
      Find.setRole('carrier');
      return '取り込んだ区間のうち、地図の市区町村に置けた帰りの区間が見つかりませんでした。登録の欄は自分で入れてください。';
    }
    Find.openCarrier(b.pref, a.pref); // 荷が多い向き（着地→発地）の逆、つまり帰りの向きで登録の欄を開く
    const tm = draftTimes(leg), r = Find.state.reg;
    if (!r) return '帰りの向きで登録の欄を開きました。登録の欄がまだ開けていないので、発地・着地・出発は自分で入れてください。';
    if (a.code) r.o = a.code;
    if (b.code) { r.d = b.code; r.drops = `${b.pref}${b.muni}`; }
    r.dep = tm.dep; r.arr = tm.arr;
    const el = q('#f-dir');
    if (el) el.dataset.k = ''; // 入れ直した内容で欄を作り直す
    Find.setRole('carrier');
    const place = (p) => `${p.pref || ''}${p.muni || p.label}`;
    return `${leg.date.slice(5, 7).replace(/^0/, '')}/${+leg.date.slice(8, 10)} の${leg.kind === 'empty' ? '空車の' : ''}帰り（${place(a)} → ${place(b)}）をもとに、`
      + `登録の欄に発地・着地と、出発の曜日（${WD[tm.wd]}曜）・時刻を入れました。日付は次の${WD[tm.wd]}曜にしてあります。値段・車種・荷の条件は欄で確かめてから登録してください。`;
  }

  function draft() {
    App.setView('find');
    const st = Find.state;
    let note;
    if (isImport()) note = fillRegisterFromImport(); // 自社のファイル: 取り込んだ区間を入れる。架空の便 T7 の説明は出さない
    else {
      if (st.carriers && st.carriers.some((c) => c.id === 'C4')) st.carrier = 'C4';
      Find.setRole('carrier');
      note = draftNote();
    }
    const el = q('#own-draft');
    if (el) {
      el.querySelector('.od-text').textContent = note;
      el.hidden = false;
    }
  }

  function onNext(a) {
    if (a === 'draft_backhaul') draft();
    else if (a === 'import') { if (O.tab !== 'import') setTab('import'); const f = q('#own-file'); if (f) f.click(); }
  }

  function bindLeft() {
    const root = q('#own-view');
    root.addEventListener('click', (e) => {
      const tb = e.target.closest('#own-tabs button[data-tab]');
      if (tb) { setTab(tb.dataset.tab); return; }
      const b = e.target.closest('button[data-act]');
      if (!b) return;
      if (b.dataset.act === 'reset') { backToSample(); return; }
      if (b.dataset.act === 'file-remove') { removeFile(b.dataset.f); return; }
      if (b.dataset.act === 'tmr-make') { tmrSync(); O.tmr.text = OwnMonth.boardOne(O.tmr); O.tmr.msg = ''; renderLeft(); return; }
      if (b.dataset.act === 'tmr-copy') { tmrCopy(); return; }
      if (b.dataset.act === 'sample-csv') { saveSampleCsv(); return; }
      if (b.dataset.act === 'ai-cols') { const c = buildAiPayload(); if (c.payload.columns.length) { O.aiOwn.confirm = c; O.aiOwn.msg = ''; renderLeft(); } return; }
      if (b.dataset.act === 'ai-cols-cancel') { O.aiOwn.confirm = null; renderLeft(); return; }
      if (b.dataset.act === 'ai-cols-send') { aiSend(); return; }
      if (b.dataset.act === 'ai-apply') { aiApply([b.dataset.key]); return; }
      if (b.dataset.act === 'ai-apply-all') { aiApply(Object.keys(O.aiOwn.sugg)); return; }
      if (b.dataset.act === 'roles-cancel') { O.pendingRoles = null; renderLeft(); showDropMsg('取りやめました。前の表示のままです'); return; }
      if (b.dataset.act === 'year-cancel') { O.pendingYear = null; renderLeft(); showDropMsg('取りやめました。前の表示のままです'); return; }
      if (b.dataset.act === 'alias') {
        const row = b.closest('.o-up'), v = row.querySelector('input').value.trim();
        if (!v) { toast('市区町村名を入れてください'); return; }
        O.userAliases[row.dataset.name] = v;
        reanalyze().then(() => { if ((O.data.unplaced || []).some((u) => u.name === row.dataset.name)) showDropMsg(`「${v}」は地図の市区町村に見つかりません（${row.dataset.name} は地図に置けないままです）`); });
        return;
      }
      if (b.dataset.act === 'month-cmp') { const sel = q('#own-cmp'); O.cmpId = sel ? sel.value : ''; renderLeft(); renderRight(); return; }
      if (b.dataset.act === 'month-cmp-saved') { O.cmpId = 'saved'; renderLeft(); return; }
      if (b.dataset.act === 'month-clear') { OwnMonth.clearAll(); O.noSave = true; O.cmpId = ''; O.restoreOffer = null; O.saveMsg = '保存したデータを消しました'; renderLeft(); renderRight(); return; }
      if (b.dataset.act === 'month-restore') {
        const ro = O.restoreOffer;
        if (ro) { O.userAliases = { ...(ro.aliases || {}) }; O.dec = { ...(ro.dec || {}) }; O.fixTxt = { ...(ro.fixTxt || {}) }; O.restoreOffer = null; reanalyze(); }
        return;
      }
      const card = b.closest('.o-card'), ins = b.closest('.o-ins');
      const act = b.dataset.act;
      if (card) {
        const id = card.dataset.key, c = O.data.confirm.find((x) => ck(x) === id);
        if (act === 'mp-open') { const k = 'mp' + id; if (O.open.has(k)) O.open.delete(k); else O.open.add(k); renderLeft(); return; }
        if (act === 'mp-plate' || act === 'mp-class' || act === 'mp-reset') {
          const mpv = Number((card.querySelector('.o-mp input') || {}).value);
          if (act === 'mp-reset') { delete O.maxPayload.by_plate[b.dataset.plate]; if (b.dataset.cls) delete O.maxPayload.by_class[b.dataset.cls]; }
          else if (!(mpv >= 0.5 && mpv <= 40)) { toast('最大積載量を 0.5〜40 t の数で入れてください（例 13）'); return; }
          else if (act === 'mp-plate') O.maxPayload.by_plate[b.dataset.plate] = Math.round(mpv * 1000);
          else if (b.dataset.cls) O.maxPayload.by_class[b.dataset.cls] = Math.round(mpv * 1000);
          saveMaxPayload();
          O.open.delete('mp' + id);
          reanalyze();
          return;
        }
        if (act === 'ru-empty' || act === 'ru-loaded') { O.dec[id] = 'fix'; O.fixTxt[id] = act === 'ru-empty' ? '空' : '実車'; }
        else if (act === 'ru-keep') O.dec[id] = 'ok';
        else if (act === 'ok' || act === 'skip') O.dec[id] = act;
        else if (act === 'fix') { O.open.add('fix' + id); }
        else if (act === 'fixok') {
          const v = card.querySelector('.o-fix input').value.trim();
          if (!v) { toast('直した値を入れてください'); return; }
          const why = c ? fixProblem(c, v) : '';
          if (why) { toast(why); return; }
          O.fixTxt[id] = v; O.dec[id] = 'fix'; O.open.delete('fix' + id);
        } else if (act === 'undo') { delete O.dec[id]; delete O.fixTxt[id]; }
        renderLeft();
        if (isImport() && act !== 'fix') reanalyze(); // 決定を計算に渡して、数字を計算し直す
      } else if (ins) {
        const id = ins.dataset.id;
        if (act === 'board') {
          const it = O.data.insights.find((x) => x.id === id);
          let items = OwnMonth.boardTexts(O.data);
          if (!items.length) { const t = OwnMonth.boardText(O.data, importDraftLeg(), it && it.next ? it.next.payload : null); items = t ? [{ label: '', text: t }] : []; }
          O.board = { id, items, text: items.map((x) => x.text).join(BOARD_SEP), msg: '' };
          renderLeft();
        } else if (act === 'board-copy') boardCopy();
        else if (act === 'board-copy-one') boardCopy(Number(b.dataset.i));
        else if (act === 'ev') { const k = 'ev' + id; if (O.open.has(k)) O.open.delete(k); else O.open.add(k); renderLeft(); }
        else if (act === 'next') {
          const a = b.dataset.a;
          if (a === 'show_evidence') { O.open.add('ev' + id); renderLeft(); } else onNext(a);
        }
      }
    });
    root.addEventListener('input', (e) => { if (e.target.id && e.target.id.indexOf('own-tmr-') === 0) tmrSync(); });
    root.addEventListener('change', (e) => {
      if (e.target.id && e.target.id.indexOf('own-tmr-') === 0) { tmrSync(); return; }
      if (e.target.id === 'own-year') { O.yearPick = Number(e.target.value); reanalyze(); }
      else if (e.target.id === 'own-month-pick' && e.target.value) { O.monthPick = Number(e.target.value); reanalyze(); }
      else if (e.target.matches && e.target.matches('input[data-hf]')) {
        const l = [...root.querySelectorAll('input[data-hf]')].filter((x) => x.checked).map((x) => x.dataset.hf);
        if (!l.length) { e.target.checked = true; toast('使う配車表を 1 つ以上選んでください'); return; }
        O.haishaUse = l; reanalyze();
      } else if (e.target.id === 'own-year-pick' && e.target.value) chooseYear(Number(e.target.value));
      else if (e.target.matches && e.target.matches('select[data-role-col]')) { // 列の役割を選んだ: 計算し直して、同じ形のファイルのために覚える
        const t = e.target, pend = O.pendingRoles;
        delete O.aiOwn.sugg[`${t.dataset.roleSource || ''}|${t.dataset.roleCol}`]; // 手で選んだ列の AI の案は消す
        setRoleChoice(t.dataset.roleSource, t.dataset.roleCol, t.dataset.roleFiles, t.value, pend);
        afterRoles(pend, !!t.value);
      }
    });
    q('#own-draft .od-close').addEventListener('click', () => { q('#own-draft').hidden = true; });
  }

  function setTab(t) {
    const was = O.tab;
    O.tab = t;
    syncMode();
    renderLeft();
    renderRight();
    writeHash();
    syncMarkers();
    if (!O.active) return;
    if (t === 'wait') fitPoints(true);
    else if (was === 'wait') { WaitingMap.show(false); fit(true); }
  }

  // 荷待ちのタブでは、左を広げて表を入れ、右に相手ごとの表と書面の下書きを出す（own.css の body.own-wait）
  function syncMode() { document.body.classList.toggle('own-wait', O.active && O.tab === 'wait'); }

  /* ------------------------------------------------------------ 見方の出入り */

  // 地図に収める範囲の余白。画面の大きさで決める（狭い画面で余白が地図より大きくならないように）
  function fitPad(wait) {
    const w = window.innerWidth, h = window.innerHeight;
    if (w <= 600) {
      if (!wait) return { top: 60, bottom: Math.round(h * 0.5), left: 24, right: 24 };
      // 荷待ちの点: 点の半径ぶんを足して、地図の端と出典の文字（下のパネルのすぐ上）に重ならないようにする（SPEC §31.5 #15）
      const r = Math.ceil(WaitingMap.maxRadius()), at = document.querySelector('.maplibregl-ctrl-attrib');
      // 出典の文字は狭い画面で折り返して高さが変わるので、測った位置と「下のパネルの上端 − 4 行ぶん」の小さいほうを使う
      const sh = document.querySelector('#own-sheet'), shTop = sh && sh.getBoundingClientRect().height ? sh.getBoundingClientRect().top : h * 0.56;
      const edge = Math.min(shTop - 72, at && at.getBoundingClientRect().height ? at.getBoundingClientRect().top : h);
      return { top: 60 + r, bottom: Math.round(h - edge + r + 8 + 26),  // 26 は点の下の場所の名前の高さ
         left: 24 + r, right: 24 + r };
    }
    const lw = (q('#own-view') && q('#own-view').offsetWidth) || 440, rw = (q('#own-right') && q('#own-right').offsetWidth) || 540;
    let pad = wait ? { top: 80, bottom: 80, left: lw + 40, right: rw + 40 } : { top: 70, bottom: 150, left: O.guided ? 80 : 500, right: rw + 60 };
    const over = (pad.left + pad.right) / (w * 0.8);
    if (over > 1) pad = { ...pad, left: Math.round(pad.left / over), right: Math.round(pad.right / over) };
    return pad;
  }

  // 手順が変わったときの地図の動き（SPEC §47）。手順 2 は選んだ区間の両端が収まる範囲、手順 3 も同じ範囲のまま（積込の側の輪は付ける）
  function stepCamera(prev, n) {
    if (!O.active || !O.guided || !O.loaded || !O.focus) return;
    // 手順 3 も区間の全体を映したままにする。積込の側へ寄ると、横へずらした点線が道路から離れた切れ端に見えるため（画面の出来 r3 F7・F18）
    if (n === 2 || n === 3) fit(true);
  }

  function fit(animate) {
    if (O.noMap) return; // 地図を描けない画面（noMapStart）では動かす地図が無い
    let bb = [[134.2, 34.1], [140.6, 36.2]];
    if (isImport()) { // 自社のファイルでは、地図に置けた地点が収まる範囲へ
      const ps = placed(O.data);
      if (ps.length) {
        const lo = [Math.min(...ps.map((p) => p.lon)), Math.min(...ps.map((p) => p.lat))], hi = [Math.max(...ps.map((p) => p.lon)), Math.max(...ps.map((p) => p.lat))];
        const pad = Math.max(0.35, (hi[0] - lo[0]) * 0.05, (hi[1] - lo[1]) * 0.05);
        bb = [[lo[0] - pad, lo[1] - pad], [hi[0] + pad, hi[1] + pad]];
      }
    }
    const f = O.guided && O.focus ? O.focus.pair : null, pa = f && O.data.places[f.fr], pb = f && O.data.places[f.to];
    if (pa && pb && pa.lon != null && pb.lon != null) { // 手順 2・3: 選んだ区間の両端が収まる範囲へ
      const lo = [Math.min(pa.lon, pb.lon), Math.min(pa.lat, pb.lat)], hi = [Math.max(pa.lon, pb.lon), Math.max(pa.lat, pb.lat)], pd = Math.max(0.35, (hi[0] - lo[0]) * 0.08, (hi[1] - lo[1]) * 0.08);
      bb = [[lo[0] - pd, lo[1] - pd], [hi[0] + pd, hi[1] + pd]];
    }
    const ease = (t) => 1 - Math.pow(1 - t, 3), dur = animate ? fxMs(1600) : 0, rv = restView();
    if (O.guided && window.innerWidth <= 600) { // 狭い画面: 地図は上の細い帯（画面の高さの 34%）。その帯に収める
      const h = window.innerHeight, cam = maplibregl.Map.prototype.cameraForBounds.call(map, bb, { padding: { top: 56, bottom: Math.round(h * 0.66) + 8, left: 24, right: 24 } });
      if (cam) { map.easeTo({ center: cam.center, zoom: cam.zoom, ...rv, duration: dur, easing: ease, essential: true }); return; }
    }
    map.fitBounds(bb, { padding: fitPad(false), ...rv, duration: dur, easing: ease, essential: true });
  }

  // 荷待ちの点が収まる範囲へ。点が無いときは動かさない。force が false のときは、点の顔ぶれが変わったときだけ動かす（入力のたびに地図が動かないように）
  function fitPoints(force) {
    if (O.noMap) return;
    const b = WaitingMap.bounds();
    if (!b) return;
    const changed = WaitingMap.fitKeyChanged(WaitingMap.key());
    if (!changed && !force) return;
    const opts = { padding: fitPad(true), maxZoom: 10 };
    if (window.innerWidth <= 600) {
      // 狭い画面では sheet.js が map.fitBounds / cameraForBounds の余白を固定の値に置き換えるので、元のメソッドで位置を求めて移す（SPEC §31.5 #15）
      const cam = maplibregl.Map.prototype.cameraForBounds.call(map, b, opts);
      if (cam) { map.easeTo({ center: cam.center, zoom: cam.zoom, pitch: 0, bearing: 0, duration: fxMs(900), essential: true }); return; }
    }
    map.fitBounds(b, { ...opts, pitch: 0, bearing: 0, duration: fxMs(900), essential: true });
  }

  async function enter() {
    O.active = true;
    hookZoom();
    const g = goalFromHash(location.hash);
    if (GOALS.includes(g)) { O.guided = true; O.goal = g; Negotiate.setGoal(g); }
    syncGuidedClass();
    syncMode();
    await load();
    renderLeft(); renderRight(); syncMarkers();
    if (O.tab === 'wait') { WaitingMap.show(true); if (WaitingMap.bounds()) fitPoints(true); else fit(true); } else fit(true);
  }
  function leave() {
    closePlace(); O.active = false; syncGuidedClass(); syncMode(); syncMarkers(); WaitingMap.show(false);
    try { if (map && Math.abs(map.getPitch()) > 0.5) map.easeTo({ pitch: 0, bearing: 0, duration: fxMs(700), essential: true }); } catch (e) { /* 地図が無いとき */ }
  }
  // 拡大が変わったら、手順 2・3 の線の間隔（画素）を作り直す
  function hookZoom() {
    if (O.zoomHook || typeof map === 'undefined' || !map || !map.on) return;
    O.zoomHook = true;
    map.on('zoomend', () => { if (O.active && O.guided && O.focus && O.tab !== 'wait') { O.focusGeo = focusGeoOf(O.focus); try { map.triggerRepaint(); } catch (e) { /* 地図が無いとき */ } } });
  }

  async function init() {
    await load();
    if (!O.loaded) return;
    bindTips();
    bindLeft();
    bindRight();
    bindWait();
    WaitingMap.onChange(() => { renderRight(); const d = q('#wm-detail'); if (d) d.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); });
    renderLeft(); renderRight();
  }

  /* ------------------------------------------------------------ 地図を描けないとき（SPEC §49）
     WebGL が無い端末や、地図のデータが読めなかったときは、app.js の起動が止まり（BOOT.state が failed）、自社の運行は案内の箱だけになって手順 1 を始められなかった。
     分析と相談文は地図が無くても作れるので、地図の場所を日本地図の静止画（img/home_map.jpg）に替え、手順の欄をそのまま動かす。手順 2 の運行の図は欄の中に出る。 */
  async function noMapStart() {
    if (O.noMap || O.active) return;
    O.noMap = true;
    const c = document.body.classList, g = goalFromHash(location.hash);
    c.add('view-own', 'own-nomap');
    if (GOALS.includes(g)) c.add('guided');
    const ld = q('#loading'); if (ld) ld.remove();
    if (!q('#own-nomap')) {
      const f = document.createElement('div');
      f.id = 'own-nomap';
      f.innerHTML = '<img src="img/home_map.jpg" alt="日本地図の静止画（地図を動かせない端末の代わりです）" decoding="async"><p class="nomap-note" role="status">地図の描画（WebGL）を使えないため、地図の代わりに静止画を出しています。取り込みと分析、相談文は地図なしで使えます。<a href="#" id="nomap-home">最初の画面に戻る</a></p>';
      document.body.appendChild(f);
      const hm = f.querySelector('#nomap-home');
      if (hm) hm.addEventListener('click', (e) => { e.preventDefault(); location.hash = ''; location.reload(); });
    }
    try { await init(); } catch (e) { console.error(e); }
    try { await enter(); } catch (e) { console.error(e); }
  }
  // 起動が失敗したら（BOOT.state が failed）、自社の運行を開く指定のときだけ地図なしの画面にする
  (function watchBoot() {
    if (new URLSearchParams(String(location.hash || '').replace(/^#/, '')).get('v') !== 'own') return;
    let n = 0;
    const t = setInterval(() => {
      n++;
      const st = typeof BOOT !== 'undefined' ? BOOT.state : '';
      if (st === 'failed') { clearInterval(t); noMapStart(); } else if (st === 'ready' || n > 480) clearInterval(t);
    }, 250);
  })();

  function hashParams(p) { if (O.guided) p.set('g', O.goal || 'empty'); if (O.tab !== 'import') p.set('ot', O.tab); }
  function readHash(p) {
    const g = p.get('g') || '';
    if (GOALS.includes(g)) { O.guided = true; O.goal = g; Negotiate.setGoal(g); }
    const t = p.get('ot'); if (['import', 'confirm', 'insight', 'wait'].includes(t) && !O.guided) O.tab = t;
  }
  function writeHash() {
    if (typeof S === 'undefined' || S.view !== 'own') return;
    const p = new URLSearchParams();
    p.set('v', 'own');
    hashParams(p);
    history.replaceState(null, '', '#' + p.toString());
  }

  return { init, enter, leave, setGuided, sampleCsv: () => SAMPLE_CSV, aiPayload: buildAiPayload, layers, market: () => O.market, placeInfo, openPlace, closePlace, setTab, hashParams, readHash, state: O, fit };
})();
