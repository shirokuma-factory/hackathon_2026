'use strict';
/* 空きを探す・つなぐ（SPEC §16.2、架空のデモ）。
   照合・残りの重さ・仮予約・承諾は /api/b/*（tools/serve_web.py → src/shipcompare/spaceb.py）が返す結果を表示するだけで、
   ここでは判定しない。例外は地図上の位置、並べ替え、残り時間の表示、画面の中だけの変更（保存しない）。 */

const Find = (() => {
  const F = {
    role: 'shipper', trips: [], carriers: [], holds: [], note: '', fiction: '', booking: '', results: {}, loaded: false, err: '',
    dest: '28110', category: '', inContainer: false, fragile: false, isBox: true, holdMin: 30,
    sel: null, pair: null, onlyPair: false, carrier: 'C1', edits: {}, mine: new Set(), busy: false,
    ver: 0, active: false, geo: {}, markers: new Map(), matchTok: 0, lastCargo: '', timer: null, poll: 0,
    fm: { rate: 70, disc: 0, minFee: '', region: null, key: '', data: null, err: '', built: false, tok: 0 }, scBack: {}, scLoaded: false, bhSid: null, ct: {}, ctOpen: null, ctData: null, confirmed: new Set(), // SPEC §21
    scList: [], ex: { key: '', id: null, busy: false }, dir: null, // SPEC §30.3（0 便の案内の例の荷、運送会社の役で登録する向き）
  };
  const PERIOD = 14;
  const SPACE = {
    free_bed: ['荷台の空き床', '荷台の空いている床に載せる形'],
    inside_rb_upper: ['RB の中の上段', 'RB（ロールボックスパレット）の中の上の段に載せる形'],
    above_rb_rear: ['扉側の列の上の棚', 'RB の列の上に渡した棚に載せる形'],
    shelf_container: ['容器ごとの棚', '容器ごとの棚に載せる形'],
  };
  const CATEGORIES = ['危険物', '生鮮食品', '貴金属', '液体', '動物'];
  const HOLD_CHOICES = [10, 30, 60, 120, 1440, 4320];
  const holdLabel = (m) => (m >= 60 && m % 60 === 0 ? `${m / 60} 時間` : `${m} 分`);
  const FUEL_CHOICES = ['なし', '運賃の 5%（燃料サーチャージ）', '高速道路料金は実費で別途'];
  const md = (iso) => { const d = new Date(iso); return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const n0 = (v) => Math.round(v).toLocaleString('ja-JP');
  const kg = (v) => (Math.round(v * 100) / 100).toLocaleString('ja-JP');
  const q = (s) => document.querySelector(s);
  const byId = (id) => F.trips.find((t) => t.id === id);

  /* ------------------------------------------------------------ 場所と向き */

  const codeIdx = () => (F.codeMap || (F.codeMap = new Map(S.idx.codes.map((c, i) => [c, i]))));
  const muniName = (c) => { const i = codeIdx().get(c); return i === undefined ? c : S.idx.names[i]; };
  const muniFull = (c) => { const i = codeIdx().get(c); return i === undefined ? c : `${S.idx.prefs[i]}${S.idx.names[i]}`; };
  const prefOf = (c) => { const i = codeIdx().get(c); return i === undefined ? '' : S.idx.prefs[i]; };
  const lonlat = (c) => { const i = codeIdx().get(c); return i === undefined ? null : [S.idx.lon[i], S.idx.lat[i]]; };
  // 荷が多い向きの区間（from → to）の、荷が少ない向き（to → from）を走る便か
  const isReverse = (t) => !!F.pair && prefOf(t.origin_code) === F.pair.to && prefOf(t.dest_code) === F.pair.from;
  const inScope = (t) => !F.pair || isReverse(t);
  const dropCodes = () => [...new Set(F.trips.flatMap((t) => t.drop_points))];
  const hhmm = (s) => `${Number(s.slice(5, 7))}/${Number(s.slice(8, 10))} ${s.slice(11, 16)}`;
  const tripPrice = (t) => (F.edits[t.id] && F.edits[t.id].price !== undefined ? F.edits[t.id].price : t.price_ex_tax_yen);
  const regSlack = (t) => (F.edits[t.id] && F.edits[t.id].slack !== undefined ? F.edits[t.id].slack : t.weight_slack_kg);
  // 画面の中だけで変えた登録値に合わせて、サーバーの残りを足し引きして見せる（サーバーの照合には使われない）
  const shownRemain = (t) => Math.max(0, t.remaining_slack_kg + (regSlack(t) - t.weight_slack_kg));
  const cargoOf = () => {
    const sh = Work.confirmedShipment();
    if (!sh) return null;
    let n = 0, w = 0;
    for (const it of sh.items) { n += it.count; w += it.count * it.weight_kg; }
    return { sh, n, w: Math.round(w * 100) / 100, label: sh.cargo_name || (Work.scenarioId() ? `場面 ${Work.scenarioId()}` : '手入力') };
  };
  // 場面 S6 などの追加項目（集荷の地点・届けばよい日の幅・荷主の名称）。場面データの backhaul（SPEC §21.1）
  const bh = () => (Work.scenarioId() ? F.scBack[Work.scenarioId()] || null : null);
  const stamp = (c) => JSON.stringify([c.sh.items, F.dest, F.category, F.inContainer, F.fragile, F.isBox, Work.scenarioId(), S.origin]);
  const body = () => {
    const c = cargoOf(), b = bh();
    return { shipment: { items: c.sh.items }, dest_code: F.dest, category: F.category || null,
      in_container: F.inContainer, fragile: F.fragile, is_box: F.isBox,
      origin_code: b ? b.origin_code : S.origin, // 集荷の地点。場面に無ければ、地図で選んだ出発地（SPEC §21.2）
      ...(b ? { window_from: b.window_from, window_to: b.window_to } : {}) };
  };
  // 場面が変わったら、受け取り場所と箱の有無をその場面の値にそろえる
  function syncScenario() {
    const sid = Work.scenarioId() || '';
    if (F.bhSid === sid) return;
    F.bhSid = sid;
    const b = bh();
    if (b) { F.dest = b.dest_code; F.isBox = b.is_box; }
  }

  /* ------------------------------------------------------------ サーバーとのやり取り */

  async function loadTrips() {
    try {
      const r = await Work.api('/api/b/trips');
      F.trips = r.trips; F.carriers = r.carriers; F.holds = r.holds; F.note = r.note; F.fiction = r.fiction_notice; F.booking = r.booking_notice;
      F.err = '';
      if (!F.scLoaded) {
        try { F.scList = (await Work.api('/api/scenarios')).scenarios; F.scList.forEach((x) => { F.scBack[x.id] = x.backhaul || null; }); F.scLoaded = true; } catch (e) { /* 場面の追加項目が無くても動く */ }
      }
      F.loaded = true;
      if (F.carriers.length && !F.carriers.some((c) => c.id === F.carrier)) F.carrier = F.carriers[0].id; // 登録した便を戻すと「架空 自社」は消える
      if (F.sel && !byId(F.sel)) F.sel = null;
      buildGeo();
    } catch (e) { F.err = e.message; }
    F.ver++;
  }

  async function runMatch() {
    const c = cargoOf();
    if (!c || !F.loaded) return;
    syncScenario();
    F.lastCargo = stamp(c);
    const tok = ++F.matchTok;
    try {
      const r = await Work.api('/api/b/match', body());
      if (tok !== F.matchTok) return;
      F.results = Object.fromEntries(r.results.map((x) => [x.trip_id, x]));
      F.err = '';
    } catch (e) { if (tok === F.matchTok) { F.results = {}; F.err = e.message; } }
    F.ver++;
    render();
  }

  async function refresh() {
    await loadTrips();
    await runMatch();
    render();
  }

  async function act(fn) {
    if (F.busy) return;
    F.busy = true;
    try { await fn(); } catch (e) { toast(e.message); } finally { F.busy = false; }
    await refresh();
  }

  /* ------------------------------------------------------------ 地図の線 */

  function buildGeo() {
    F.geo.T = true;
    const ids = new Set(F.trips.map((t) => t.id));
    for (const id of Object.keys(F.geo)) if (id !== 'T' && !ids.has(id)) delete F.geo[id]; // 戻して消えた便（登録した便）の線を捨てる
    for (const [id, m] of [...F.markers]) if (!ids.has(id)) { m.mk.remove(); F.markers.delete(id); }
    const groups = {};
    F.trips.forEach((t) => { const k = `${prefOf(t.origin_code)}>${prefOf(t.dest_code)}`; (groups[k] = groups[k] || []).push(t.id); });
    F.trips.forEach((t, ti) => {
      if (F.geo[t.id]) return;
      const a = lonlat(t.origin_code), b = lonlat(t.dest_code);
      if (!a || !b) return;
      const g = groups[`${prefOf(t.origin_code)}>${prefOf(t.dest_code)}`], n = g.indexOf(t.id);
      const bulge = (n % 2 === 0 ? 1 : -1) * (0.11 + 0.05 * Math.floor(n / 2));
      // 架空の便でも道路は実在する。OSRM の道路の経路（SPEC §17.2）。なければ直線の弧（登録した便と、経路のデータが無い便。SPEC §31.4・§31.5 #9）
      const real = Rt.tripPath(t.id);
      const rt = real && real.length > 2 ? makeRoute(real, 'road', null, null) : makeRoute(arcPath(a, b, bulge, 72), 'arc', null, null);
      const dur = Math.min(PERIOD - 3, 4.5 + rt.total / 110);
      const ph = (ti * 2.3) % Math.max(1, PERIOD - dur);
      const items = [];
      for (const sh of [0, -PERIOD]) items.push({ id: t.id, path: rt.path, ts: rt.cum.map((c) => (c / rt.total) * dur + ph + sh) });
      F.geo[t.id] = { a, b, rt, items, dur, ph, arc: rt.kind === 'arc' };
    });
  }

  // 直線の弧で出す便の注記（SPEC §31.4・§31.5 #9）
  const ARC_NOTE_REG = '登録した便は直線で表示しています';
  const ARC_NOTE_NODATA = '経路のデータが無いので直線で表示しています';
  const arcNote = (t) => (F.geo[t.id] && F.geo[t.id].arc ? `<div class="f-arcnote">${t.registered ? ARC_NOTE_REG : ARC_NOTE_NODATA}</div>` : '');

  // 地図での明るさ。荷に合う便は明るく、合わない便・別の区間の便は薄く
  function level(t) {
    if (!inScope(t)) return 0.13;
    if (F.role === 'carrier') return t.carrier_id === F.carrier ? 1 : 0.3;
    const r = F.results[t.id];
    return !r || r.ok ? 1 : 0.26;
  }

  function layers(now) {
    const L = deck, out = [], sec = now / 1000;
    const ts = F.trips.filter((t) => F.geo[t.id]);
    if (!ts.length) return out;
    const items = ts.flatMap((t) => F.geo[t.id].items);
    const lv = (d) => { const t = byId(d.id); return t ? level(t) : 0; }; // 戻して消えた便の線が 1 コマ残っても落ちない
    const emph = (d) => (d.id === F.sel ? 1.35 : 1);
    const trig = [F.ver, F.sel];
    const paths = ts.map((t) => ({ id: t.id, path: F.geo[t.id].rt.path }));
    out.push(new L.PathLayer({
      id: 'f-base', data: paths, getPath: (d) => d.path, widthUnits: 'pixels',
      getWidth: (d) => (d.id === F.sel ? 3.2 : 2.2), getColor: (d) => [150, 225, 255, Math.round(200 * lv(d))], capRounded: true,
      updateTriggers: { getColor: trig, getWidth: trig },
    }));
    out.push(new L.PathLayer({
      id: 'f-pick', data: paths, getPath: (d) => d.path, widthUnits: 'pixels', getWidth: 16, getColor: [255, 255, 255, 3], pickable: true,
    }));
    const tp = { data: items, getPath: (d) => d.path, getTimestamps: (d) => d.ts, widthUnits: 'pixels', capRounded: true, jointRounded: true, fadeTrail: true, currentTime: sec % PERIOD };
    out.push(new L.TripsLayer({ ...tp, id: 'f-glow', getWidth: (d) => 9 * emph(d), trailLength: 3.6, getColor: (d) => [92, 200, 255, Math.round(110 * lv(d))], updateTriggers: { getColor: trig, getWidth: trig } }));
    out.push(new L.TripsLayer({ ...tp, id: 'f-core', getWidth: (d) => 3 * emph(d), trailLength: 2.8, getColor: (d) => [236, 250, 255, Math.round(235 * lv(d))], updateTriggers: { getColor: trig, getWidth: trig } }));
    const cur = sec % PERIOD, veh = [];
    for (const t of ts) {
      const g = F.geo[t.id], p = ((((cur - g.ph) % PERIOD) + PERIOD) % PERIOD) / g.dur;
      if (p >= 1) continue;
      const pos = pointAt(g.rt, p), nx = pointAt(g.rt, Math.min(1, p + 0.01)), pv = pointAt(g.rt, Math.max(0, p - 0.01));
      const dx = (nx[0] - pv[0]) * Math.cos((pos[1] * Math.PI) / 180), dy = nx[1] - pv[1];
      veh.push({ p: pos, ang: -(Math.atan2(dx, dy) * 180) / Math.PI, a: lv(t) * Math.min(1, p * 10, (1 - p) * 10), model: M3D.byVehicle(t.vehicle) });
    }
    // 2D アイコンと 3D モデルの切り替え（models3d.js の LOD）。3D は車種ごとのモデルに分けて出す
    const zm = map.getZoom(), mx3 = M3D.mixFor(['truck', 'truck_4t', 'box_2t', 'kei_van'], zm);
    out.push(Rt.iconLayer('f-veh', mx3 > 0.001 ? veh.map((d) => ({ ...d, a: d.a * (1 - mx3) })) : veh, 'truck', 28));
    if (mx3 > 0.001) {
      for (const key of ['truck', 'truck_4t', 'box_2t', 'kei_van']) {
        const l3 = M3D.vehicles('f-veh-3d-' + key, key, veh.filter((d) => d.model === key), zm);
        if (l3) out.push(l3);
      }
    }
    const ends = ts.flatMap((t) => [{ id: t.id, p: F.geo[t.id].a, o: true }, { id: t.id, p: F.geo[t.id].b, o: false }]);
    out.push(new L.ScatterplotLayer({
      id: 'f-ends', data: ends, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: (d) => (d.o ? 3.4 : 4.6), filled: true, stroked: true,
      getFillColor: (d) => (d.o ? [92, 200, 255, Math.round(255 * lv(d))] : [255, 255, 255, Math.round(255 * lv(d))]),
      getLineColor: [10, 16, 26, 220], lineWidthUnits: 'pixels', getLineWidth: 1.2, updateTriggers: { getFillColor: trig },
    }));
    const st = F.sel && F.geo[F.sel];
    if (st) {
      const ph = (sec % 2.4) / 2.4;
      out.push(new L.ScatterplotLayer({
        id: 'f-sel-pulse', data: [{ p: st.b }], getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: 8 + ph * 24, stroked: true, filled: false,
        getLineColor: [255, 255, 255, Math.round(190 * (1 - ph))], lineWidthUnits: 'pixels', getLineWidth: 1.6,
        updateTriggers: { getLineColor: [Math.round(ph * 30)], getRadius: [Math.round(ph * 30)] },
      }));
    }
    return out;
  }

  function syncTags() {
    for (const t of F.trips) {
      const g = F.geo[t.id];
      if (!g) continue;
      let m = F.markers.get(t.id);
      if (!m) {
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'f-tag';
        el.addEventListener('click', (e) => { e.stopPropagation(); select(t.id, { fly: false }); });
        m = { el, mk: new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat(pointAt(g.rt, 0.36 + 0.14 * (F.trips.indexOf(t) % 3))).addTo(map) };
        F.markers.set(t.id, m);
      }
      const lv = level(t), r = F.role === 'shipper' ? F.results[t.id] : null;
      m.el.textContent = `${t.id} 架空${r ? (r.ok ? ' ○' : ' ×') : ''}`;
      m.el.title = g.arc ? (t.registered ? ARC_NOTE_REG : ARC_NOTE_NODATA) : '';
      m.el.classList.toggle('dim', lv < 0.5);
      m.el.classList.toggle('on', t.id === F.sel);
      m.el.style.display = F.active ? '' : 'none';
    }
    spreadTags();
  }

  // 名札が重ならないように、画面上の位置で上下にずらす（SPEC §32 #9）。近い名札は、先に置いた名札と重ならない最初の段に置く
  function spreadTags() {
    if (!F.active) return;
    const W = 76, H = 26, placed = [];
    const items = F.trips.map((t) => ({ t, m: F.markers.get(t.id) })).filter((x) => x.m);
    for (const { t, m } of items) {
      const g = F.geo[t.id];
      if (!g) continue;
      const p = map.project(m.mk.getLngLat());
      let k = 0, dy = 0;
      for (; k < 14; k++) {
        dy = k === 0 ? 0 : (k % 2 ? 1 : -1) * Math.ceil(k / 2) * (H + 2);
        if (!placed.some((r) => Math.abs(r.x - p.x) < W && Math.abs(r.y - (p.y + dy)) < H + 1)) break;
      }
      placed.push({ x: p.x, y: p.y + dy });
      m.mk.setOffset([0, dy]);
    }
  }

  function click(info) {
    const o = info && info.object;
    if (o && info.layer && info.layer.id === 'f-pick') select(o.id, { fly: false });
  }

  function fitTrips(ids) {
    const pts = ids.flatMap((id) => (F.geo[id] ? F.geo[id].rt.path : []));
    if (!pts.length) return;
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const rw = (q('#find-right').offsetWidth || 480) + 80;
    map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]],
      { padding: { top: 120, bottom: 150, left: 440, right: rw }, maxZoom: 7.6, duration: 1800, essential: true, curve: 1.4 });
  }

  function select(id, { fly = true } = {}) {
    F.sel = id;
    F.ver++;
    render();
    const el = q(`#f-list .f-card[data-t="${id}"]`);
    if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    if (fly) fitTrips([id]);
    writeHash();
  }

  /* ------------------------------------------------------------ 描画 */

  const holdOf = (t) => {
    const hs = F.holds.filter((h) => h.trip_id === t.id && F.mine.has(h.hold_id));
    return hs.length ? hs[hs.length - 1] : null;
  };
  // 残り時間。1 時間以上は 時:分:秒
  const cdFmt = (ms) => {
    const s = Math.max(0, Math.ceil(ms / 1000)), z = (n) => String(n).padStart(2, '0');
    return s >= 3600 ? `${Math.floor(s / 3600)}:${z(Math.floor((s % 3600) / 60))}:${z(s % 60)}` : `${Math.floor(s / 60)}:${z(s % 60)}`;
  };
  const cdText = (h) => cdFmt(new Date(h.expires_at).getTime() - Date.now());

  function barHtml(t, cargoKg) {
    const reg = Math.max(1, regSlack(t)), rem = shownRemain(t);
    const fill = Math.min(100, (rem / reg) * 100), need = cargoKg === null ? null : Math.min(100, (cargoKg / reg) * 100);
    let txt = `<span>重さの余り <b class="num">${kg(rem)}</b> / ${kg(regSlack(t))} kg</span>`;
    if (cargoKg !== null) txt += `<span class="num">この荷 ${kg(cargoKg)} kg</span>`;
    const shelf = t.remaining_shelf_kg !== null && t.remaining_shelf_kg !== undefined ? `<div class="f-shelf num">棚の耐荷重の残り ${kg(t.remaining_shelf_kg)} kg</div>` : '';
    const floor = t.remaining_floor_m2 !== null && t.remaining_floor_m2 !== undefined ? `<div class="f-shelf num">床の空き ${kg(t.remaining_floor_m2)} / ${kg(t.floor_free_m2)} m²</div>` : '';
    return `<div class="f-bar"><div class="f-track"><i style="width:${fill.toFixed(1)}%"></i>${need !== null ? `<u style="left:${need.toFixed(1)}%"></u>` : ''}</div>` +
      `<div class="f-bar-txt">${txt}</div>${shelf}${floor}</div>`;
  }

  function detailHtml(t) {
    const [sp, spTip] = SPACE[t.space_type] || [t.space_type, ''];
    const row = (k, v) => `<div><span>${k}</span><b>${v}</b></div>`;
    return '<div class="f-detail">' +
      row('空きの形', `<span title="${esc(spTip)}">${esc(sp)}</span>`) + row('空きの高さ', `${n0(t.clear_height_mm)} mm`) +
      row('1口の底面', `${n0(t.unit_footprint_mm[0])}×${n0(t.unit_footprint_mm[1])} mm`) + row('1個の重さの上限', `${kg(t.max_piece_kg)} kg`) +
      row('容器', t.container_required ? '容器入りだけ' : '指定なし') +
      row('降ろせる地点', esc(t.drop_points.map(muniName).join('、'))) +
      row('受けない品目', esc(t.refused_categories.join('、')) || 'なし') +
      (t.pickup_code ? row('集荷できる範囲', `${esc(muniName(t.pickup_code))} から ${kg(t.pickup_radius_km)} km 以内`) : '') +
      (t.accepts_unboxed ? row('箱でない荷', '受ける（木枠など）') : '') + '</div>';
  }

  function holdAreaHtml(t, r) {
    const h = holdOf(t);
    if (h && h.state === 'held') {
      return `<div class="f-hold held"><div class="f-hold-row"><b>仮予約中</b><span class="num">${esc(h.hold_id)}・${kg(h.weight_kg)} kg</span>` +
        `<span class="f-cd num" data-exp="${esc(h.expires_at)}">残り ${cdText(h)}</span></div>` +
        `<div class="f-hold-msg">締切 <b class="num">${md(h.expires_at)}</b> までに運送会社が承諾しないと、重さと床の余りは戻ります。</div>` +
        '<button type="button" class="btn-link" data-act="to-carrier">運送会社の役の画面で承諾する</button></div>';
    }
    if (h && h.state === 'accepted') {
      return `<div class="f-hold accepted"><div class="f-hold-row"><b>承諾されました</b><span class="num">${esc(h.hold_id)}・${kg(h.weight_kg)} kg</span></div>` +
        `<div class="f-hold-msg">この便の重さの余りから ${kg(h.weight_kg)} kg を確保しました（架空のデモです。支払いも連絡も発生しません）。</div>` +
        `<button type="button" class="btn-primary sm" data-act="contract" data-h="${esc(h.hold_id)}">書面の下書きを見る</button></div>`;
    }
    if (h && (h.state === 'expired' || h.state === 'declined')) {
      const why = h.state === 'declined' ? '運送会社が辞退しました。' : '締切を過ぎました。';
      return `<div class="f-hold lost"><div class="f-hold-row"><b>${h.state === 'declined' ? '辞退されました' : '承諾なし'}</b><span class="num">${esc(h.hold_id)}</span></div>` +
        `<div class="f-hold-msg">${why}送り方を比べるの見積依頼文で、ほかの手段に頼めます。</div>` +
        '<button type="button" class="btn-primary sm" data-act="to-compare">見積依頼文を開く</button>' +
        (r && r.ok ? `<button type="button" class="btn-link" data-act="hold" ${F.busy ? 'disabled' : ''}>もう一度仮予約する</button>` : '') + '</div>';
    }
    if (r && r.ok) {
      return `<div class="f-hold"><button type="button" class="btn-primary sm" data-act="hold" ${F.busy ? 'disabled' : ''}>仮予約する（締切 ${holdLabel(F.holdMin)}後）</button></div>`;
    }
    return '';
  }

  function shipperCard(t, c) {
    const r = F.results[t.id], ok = r && r.ok, scope = inScope(t), selCls = t.id === F.sel ? ' sel' : '';
    const mh = holdOf(t), mine = mh && (mh.state === 'held' || mh.state === 'accepted');  // 自分の仮予約がある便は、残りが減った照合の結果を出さない
    const chip = mine ? `<span class="chip ${mh.state === 'accepted' ? 'ok' : 'check'}">${mh.state === 'accepted' ? '承諾済み' : '仮予約中'}</span>`
      : !r ? '<span class="chip na">照合中</span>' : ok ? '<span class="chip ok">○ 載ります</span>' : '<span class="chip no">× 載りません</span>';
    const reasons = r && !ok && !mine ? `<ul class="f-why">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
    return `<article class="f-card${ok ? ' ok' : ''}${r && !ok ? ' ng' : ''}${selCls}${scope ? '' : ' off'}" data-t="${t.id}" tabindex="0">` +
      `<div class="f-top"><span class="f-id">${t.id}</span><b class="f-veh">${esc(t.vehicle)}</b>${chip}</div>` +
      `<div class="f-sub">${esc(t.carrier_name)}${scope ? '' : ' ・ <em>別の区間の便</em>'}</div>` +
      `<div class="f-route">${esc(t.route_label)}</div>` + arcNote(t) +
      `<div class="f-when num">${esc(muniName(t.origin_code))} ${hhmm(t.depart_at)} 発 → ${esc(muniName(t.dest_code))} ${hhmm(t.arrive_at)} 着</div>` +
      barHtml(t, c ? c.w : null) +
      `<div class="f-price"><b class="num">${n0(tripPrice(t))}</b><span>円</span><small>${esc(t.price_unit || '1口')}・税抜・架空</small></div>` +
      reasons + holdAreaHtml(t, r) + (t.id === F.sel ? detailHtml(t) : '') + '</article>';
  }

  function carrierCard(t) {
    const hs = F.holds.filter((h) => h.trip_id === t.id);
    const pending = hs.filter((h) => h.state === 'held');
    const done = hs.filter((h) => h.state !== 'held');
    const ed = F.edits[t.id];
    const holds = pending.map((h) => `<div class="f-hrow"><div><b>${esc(h.hold_id)}</b> <span class="num">${kg(h.weight_kg)} kg</span>` +
      `<span class="f-cd num" data-exp="${esc(h.expires_at)}">残り ${cdText(h)}</span><small class="f-hdl">締切 ${md(h.expires_at)}</small></div>` +
      `<div class="f-hbtn"><button type="button" class="btn-primary sm" data-act="accept" data-h="${esc(h.hold_id)}">承諾</button>` +
      `<button type="button" class="btn-ghost sm" data-act="decline" data-h="${esc(h.hold_id)}">辞退</button></div></div>`).join('');
    const past = done.map((h) => `<div class="f-hpast"><span class="num">${esc(h.hold_id)}・${kg(h.weight_kg)} kg</span>${h.state === 'accepted'
      ? `<button type="button" class="btn-link" data-act="contract" data-h="${esc(h.hold_id)}">書面の下書き</button>` : ''}<span class="chip ${h.state === 'accepted' ? 'ok' : 'na'}">${
      { accepted: '承諾済み', declined: '辞退済み', expired: '締切を過ぎた' }[h.state]}</span></div>`).join('');
    return `<article class="f-card carrier${t.id === F.sel ? ' sel' : ''}${t.carrier_id === F.carrier ? '' : ' off'}" data-t="${t.id}" tabindex="0">` +
      `<div class="f-top"><span class="f-id">${t.id}</span><b class="f-veh">${esc(t.vehicle)}</b>` +
      (F.dir && prefOf(t.origin_code) === F.dir.from && prefOf(t.dest_code) === F.dir.to ? '<span class="chip ok">この向き</span>' : '') +
      (pending.length ? `<span class="chip check">承諾待ち ${pending.length}</span>` : '') + '</div>' +
      `<div class="f-route">${esc(t.route_label)}</div>` + arcNote(t) +
      `<div class="f-when num">${esc(muniName(t.origin_code))} ${hhmm(t.depart_at)} 発 → ${esc(muniName(t.dest_code))} ${hhmm(t.arrive_at)} 着</div>` +
      barHtml(t, null) +
      `<div class="f-price"><b class="num">${n0(tripPrice(t))}</b><span>円</span><small>${esc(t.price_unit || '1口')}・税抜・架空</small>` +
      (ed ? '<em class="f-edited">画面の中だけで変更</em>' : '') + '</div>' +
      (holds ? `<div class="f-holds"><div class="f-hh">荷主からの仮予約</div>${holds}</div>` : '<div class="f-hold-msg faint">承諾待ちの仮予約はありません。</div>') +
      (past ? `<div class="f-hhist">${past}</div>` : '') + (t.id === F.sel ? detailHtml(t) : '') + '</article>';
  }

  function sortKey(t) {
    const r = F.results[t.id];
    return [inScope(t) ? 0 : 1, r && r.ok ? 0 : 1, r && !r.ok ? r.reasons.length : 0, tripPrice(t)];
  }

  /* ------------------------------------------------------------ 0 便の案内（SPEC §30.3） */

  // 合わない理由の文（サーバーが返したもの）を、数えるための短い言い方にまとめる
  const REASON_KINDS = [
    [/1個の重さ/, '1 個の重さが便の上限を超える'], [/箱の高さ/, '箱の高さが便の空きの高さに入らない'], [/箱の底面/, '箱の底面が 1 口の底面に収まらない'],
    [/合計の重さ/, '合計の重さが便の重さの余りを超える'], [/降ろせる地点/, '受け取り場所が便の降ろせる地点にない'], [/届けばよい日/, '届けばよい日の幅に便の到着が入らない'],
    [/集荷の地点/, '集荷の地点が便の空く地点から遠い'], [/床の合計/, '床の合計が便の床の空きを超える'], [/容器/, '容器に入れた荷だけを受ける便'],
    [/RB/, 'RB の持ち主の同意が無い、または 1 口の重さが上段の上限を超える'], [/直方体の箱/, '箱でない荷は受けない便'], [/壊れ物/, '壊れ物は受けない便'], [/品目/, '品目が便の受けない品目に入る'],
  ];
  const kindOf = (r) => { const k = REASON_KINDS.find((x) => x[0].test(r)); return k ? k[1] : r; };
  // 名前に「（架空）」が含まれるときは足さない（SPEC §31.5 #13）
  const fict = (name) => (/[（(]架空[）)]/.test(String(name)) ? String(name) : `${name}（架空）`);
  const zeroTrips = () => (F.pair && F.role === 'shipper' && F.loaded ? F.trips.filter(isReverse) : []);

  function zeroHtml() {
    const rs = zeroTrips();
    if (!rs.length && !(F.pair && F.onlyPair)) return '';
    if (rs.some((t) => (F.results[t.id] || {}).ok || holdOf(t))) return '';
    if (rs.some((t) => !F.results[t.id])) return ''; // 照合の結果が来るまでは出さない
    const cnt = new Map();
    for (const t of rs) for (const k of new Set((F.results[t.id].reasons || []).map(kindOf))) cnt.set(k, (cnt.get(k) || 0) + 1);
    const top = [...cnt.entries()].sort((a, b) => b[1] - a[1]).slice(0, 2);
    const sc = F.ex.id ? F.scList.find((x) => x.id === F.ex.id) : null;
    startExample();
    return '<div class="f-zero" role="status"><b>この荷に合う便はありません</b>' +
      (top.length ? `<p class="f-zero-h">合わない理由の多い順（${rs.length} 便のうち）</p><ol class="f-zero-why">${top.map((x) => `<li>${esc(x[0])}<span class="num">${x[1]} 便</span></li>`).join('')}</ol>`
        : '<p class="f-zero-h">この向きの便がありません。</p>') +
      '<div class="f-zero-btns">' +
      (sc ? `<button type="button" class="btn-primary sm" data-act="try-ex">この向きの便に合う例の荷で試す</button><small>場面 ${esc(sc.id)} ${esc(fict(sc.name))}</small>` : '') +
      '<button type="button" class="btn-ghost sm" data-act="to-compare-now">送り方を比べて見積依頼文を作る</button></div></div>';
  }

  // この向きの便に 1 便以上合う場面を、サーバーの照合で探す（場面に追加の条件があればそれを使い、無ければいまの受け取り場所と出発地で）
  async function startExample() {
    const rs = zeroTrips().map((t) => t.id), key = `${F.pair.from}>${F.pair.to}|${rs.join(',')}|${F.dest}`;
    if (F.ex.key === key || F.ex.busy) return;
    F.ex = { key, id: null, busy: true };
    let found = null;
    try {
      const order = [...F.scList.filter((x) => x.backhaul), ...F.scList.filter((x) => !x.backhaul)];
      for (const sc of order) {
        const b = sc.backhaul;
        const r = await Work.api('/api/b/match', {
          shipment: { items: sc.shipment.items }, dest_code: b ? b.dest_code : F.dest, category: F.category || null,
          in_container: F.inContainer, fragile: F.fragile, is_box: b ? b.is_box : F.isBox, origin_code: b ? b.origin_code : S.origin,
          ...(b ? { window_from: b.window_from, window_to: b.window_to } : {}),
        });
        if (r.results.some((x) => rs.includes(x.trip_id) && x.ok)) { found = sc.id; break; }
      }
    } catch (e) { found = null; }
    F.ex.id = found; F.ex.busy = false;
    if (found) renderList();
  }

  async function tryExample() {
    const id = F.ex.id, sel = document.querySelector('#sc-select'), ok = document.querySelector('#ed-confirm');
    if (!id || !sel || !ok) return;
    sel.value = id;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    document.querySelector('#ed-confirm').click(); // 荷物の欄の「この条件で確定」と同じ。版が 1 つ上がり、照合が計算し直される
    for (let i = 0; i < 80 && Work.scenarioId() !== id; i++) await new Promise((r) => setTimeout(r, 100));
    F.bhSid = null;
    await runMatch();
    const rs = zeroTrips().filter((t) => (F.results[t.id] || {}).ok);
    if (rs.length) { F.sel = rs[0].id; F.ver++; render(); fitTrips([rs[0].id]); }
    toast(`例の荷（場面 ${id}）を読み込みました`);
  }

  async function compareNow() {
    await App.compareTo(null, F.dest);
    const b = document.querySelector('#make-request');
    if (b && !b.disabled) b.click();
  }

  function renderList() {
    const box = q('#f-list');
    if (!box) return;
    const keep = box.scrollTop;
    let html = '';
    if (F.err) html += `<p class="f-err">${esc(F.err)}</p>`;
    if (!F.loaded) { box.innerHTML = html || '<p class="f-empty">便を読み込んでいます</p>'; return; }
    if (F.role === 'shipper') {
      const c = cargoOf();
      let ts = F.trips.slice().sort((a, b) => { const x = sortKey(a), y = sortKey(b); for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] - y[i]; return 0; });
      if (F.pair && F.onlyPair) ts = ts.filter(inScope);
      const okN = ts.filter((t) => F.results[t.id] && F.results[t.id].ok).length;
      q('#f-rsub').textContent = `${ts.length} 便中 ${okN} 便が載ります`;
      q('#f-rtitle').textContent = 'いまの荷に合う便';
      html = zeroHtml() + html;
      html += ts.map((t) => shipperCard(t, c)).join('');
      if (F.pair && F.onlyPair && !ts.length) html += '<p class="f-empty">この区間の逆向きの架空の便はありません。</p>';
    } else {
      const mine = F.trips.filter((t) => t.carrier_id === F.carrier), other = F.trips.filter((t) => t.carrier_id !== F.carrier);
      const pend = F.holds.filter((h) => h.state === 'held' && mine.some((t) => t.id === h.trip_id)).length;
      q('#f-rtitle').textContent = `${(F.carriers.find((c) => c.id === F.carrier) || {}).name || ''} の便`;
      q('#f-rsub').textContent = `${mine.length} 便・承諾待ち ${pend} 件`;
      html += mine.map(carrierCard).join('');
      if (other.length) html += '<div class="f-split">ほかの運送会社（架空）の便</div>' + other.map(carrierCard).join('');
    }
    box.innerHTML = html;
    box.scrollTop = keep;
  }

  function renderPairBar() {
    const el = q('#f-pairbar');
    if (!F.pair || F.role !== 'shipper') { el.hidden = true; return; }
    const n = F.trips.filter(isReverse).length;
    el.hidden = false;
    el.innerHTML = `<div><b>市場を見るで選んだ区間 ${esc(F.pair.from)}→${esc(F.pair.to)}</b>` +
      `<span>荷が少ない逆向き（${esc(F.pair.to)}→${esc(F.pair.from)}）の帰りの便は ${n} 本です。</span></div>` +
      `<label class="f-chk"><input type="checkbox" id="f-onlypair" ${F.onlyPair ? 'checked' : ''}><span>この向きの便だけ出す</span></label>` +
      '<button type="button" class="btn-link" data-act="clear-pair">区間の指定をやめる</button>';
  }

  function renderLeft() {
    const c = cargoOf();
    document.querySelectorAll('#f-role button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.role === F.role)));
    q('#f-shipper').hidden = F.role !== 'shipper';
    q('#f-carrier').hidden = F.role !== 'carrier';
    if (c) q('#f-cargo').innerHTML = `<span class="cg-name">${esc(c.label)}</span><span class="num">${c.n}個・${kg(c.w)} kg</span>`;
    const dq = q('#f-dest-q');
    if (document.activeElement !== dq) dq.value = F.dest ? muniFull(F.dest) : '';
    q('#f-cat').value = F.category;
    q('#f-container').checked = F.inContainer;
    q('#f-fragile').checked = F.fragile;
    q('#f-box').checked = F.isBox;
    q('#f-holdmin').value = String(F.holdMin);
    const bhEl = q('#f-bh'), b = bh();
    bhEl.hidden = !b;
    if (b) bhEl.textContent = `この場面の追加の条件: 集荷は ${muniFull(b.origin_code)}、届けばよい日は ${md(`${b.window_from}T00:00`).split(' ')[0]}〜${md(`${b.window_to}T00:00`).split(' ')[0]}。${b.unboxed_note || ''}`;
    const cs = q('#f-carrier-sel');
    if (cs.options.length !== F.carriers.length) cs.innerHTML = F.carriers.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');
    cs.value = F.carrier;
    const mine = F.trips.filter((t) => t.carrier_id === F.carrier);
    const ts = q('#f-trip-sel'), want = mine.map((t) => t.id).join(',');
    if (ts.dataset.ids !== want) { ts.innerHTML = mine.map((t) => `<option value="${t.id}">${t.id} ${esc(t.route_label.split('（')[0])}</option>`).join(''); ts.dataset.ids = want; }
    if (!mine.some((t) => t.id === ts.value)) ts.value = mine.length ? mine[0].id : '';
    renderDir();
    syncEditForm(false);
    q('#f-dl').disabled = !Object.keys(F.edits).length;
    q('#f-edit-note').textContent = Object.keys(F.edits).length ? `画面の中だけで変えた便: ${Object.keys(F.edits).join('、')}` : '';
  }

  /* ------------------------------------------------------------ 運送会社の役で帰り便を登録する（SPEC §31.4）
     市場を見るの区間の帰りの向き（発地の県・着地の県）で、登録の欄を埋めて開く。登録した便は、この来訪者の状態（サーバーの盤）にだけ足され、
     空き便の一覧・照合・地図に出る。「最初の状態に戻す」で消える。運送会社の名前は「架空 自社」で固定。値段の既定値は値段の式（§21.6）の参考額。 */
  const PREF_CAPITAL = {
    北海道: '札幌市', 青森県: '青森市', 岩手県: '盛岡市', 宮城県: '仙台市', 秋田県: '秋田市', 山形県: '山形市', 福島県: '福島市', 茨城県: '水戸市', 栃木県: '宇都宮市', 群馬県: '前橋市',
    埼玉県: 'さいたま市', 千葉県: '千葉市', 東京都: '新宿区', 神奈川県: '横浜市', 新潟県: '新潟市', 富山県: '富山市', 石川県: '金沢市', 福井県: '福井市', 山梨県: '甲府市', 長野県: '長野市',
    岐阜県: '岐阜市', 静岡県: '静岡市', 愛知県: '名古屋市', 三重県: '津市', 滋賀県: '大津市', 京都府: '京都市', 大阪府: '大阪市', 兵庫県: '神戸市', 奈良県: '奈良市', 和歌山県: '和歌山市',
    鳥取県: '鳥取市', 島根県: '松江市', 岡山県: '岡山市', 広島県: '広島市', 山口県: '山口市', 徳島県: '徳島市', 香川県: '高松市', 愛媛県: '松山市', 高知県: '高知市', 福岡県: '福岡市',
    佐賀県: '佐賀市', 長崎県: '長崎市', 熊本県: '熊本市', 大分県: '大分市', 宮崎県: '宮崎市', 鹿児島県: '鹿児島市', 沖縄県: '那覇市',
  };
  // 県の県庁所在地の市区（東京都は新宿区）。政令市は中央区・中区があればそれ、無ければ最初の区
  function capitalOf(pref) {
    const city = PREF_CAPITAL[pref];
    if (!city) return '';
    const hit = S.idx.codes.filter((c, i) => S.idx.prefs[i] === pref && S.idx.names[i].startsWith(city));
    return hit.find((c) => /(中央区|中区)$/.test(muniName(c))) || hit[0] || '';
  }
  const fullIdx = () => (F.fullMap || (F.fullMap = new Map(S.idx.codes.map((c, i) => [S.idx.prefs[i] + S.idx.names[i], c]))));
  const codeOfName = (t) => fullIdx().get(String(t || '').trim()) || '';
  const pad2 = (n) => String(n).padStart(2, '0');
  const dtLocal = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  function regDefaults(from, to) {
    const dep = new Date(); dep.setDate(dep.getDate() + 1); dep.setHours(17, 0, 0, 0);
    const arr = new Date(dep.getTime() + 17 * 3600 * 1000);
    const o = capitalOf(from), d = capitalOf(to);
    return { o, d, dep: dtLocal(dep), arr: dtLocal(arr), veh: '4t', drops: d ? muniFull(d) : '', price: '', priceDirty: false, basis: '', err: '', done: '', busy: false, tok: 0, ...vehFill('4t') };
  }
  function vehFill(k) {
    const v = (F.regVeh || {})[k];
    return v ? { slack: v.weight_slack_kg, fw: v.footprint_mm[0], fd: v.footprint_mm[1], h: v.clear_height_mm, piece: v.max_piece_kg } : { slack: '', fw: '', fd: '', h: '', piece: '' };
  }
  async function loadRegVeh() {
    if (F.regVeh || F.regVehBusy) return;
    F.regVehBusy = true;
    try { F.regVeh = (await Work.api('/api/b/register_defaults', {})).vehicles; } catch (e) { F.regVeh = null; } finally { F.regVehBusy = false; }
    if (F.regVeh) { if (F.reg) Object.assign(F.reg, vehFill(F.reg.veh)); const el = q('#f-dir'); if (el) el.dataset.k = ''; renderDir(); }
  }
  function ensureMuniList() {
    if (document.getElementById('f-munis')) return;
    const dl = document.createElement('datalist');
    dl.id = 'f-munis';
    dl.innerHTML = S.idx.codes.map((c, i) => `<option value="${esc(S.idx.prefs[i] + S.idx.names[i])}"></option>`).join('');
    document.body.appendChild(dl);
  }
  function regFormHtml() {
    const r = F.reg, vs = F.regVeh;
    if (!vs) return '<p class="f-hint">登録の欄を読み込んでいます</p>';
    const inp = (id, lbl, val, type = 'text', extra = '') => `<label><span class="lbl">${lbl}</span><input id="${id}" class="ctl sm${type === 'number' ? ' num' : ''}" type="${type}" value="${esc(val)}" ${extra}></label>`;
    return '<div class="f-reg" id="f-reg">' +
      '<div class="f-grid">' +
      inp('fr-o', '発地の市区町村', r.o ? muniFull(r.o) : '', 'text', 'list="f-munis" autocomplete="off"') + inp('fr-d', '着地の市区町村', r.d ? muniFull(r.d) : '', 'text', 'list="f-munis" autocomplete="off"') +
      inp('fr-dep', '出発日時', r.dep, 'datetime-local') + inp('fr-arr', '到着日時', r.arr, 'datetime-local') + '</div>' +
      `<div class="f-grid"><label><span class="lbl">車種</span><select id="fr-veh" class="ctl sm">${Object.entries(vs).map(([k, v]) => `<option value="${k}"${k === r.veh ? ' selected' : ''}>${esc(v.label)}</option>`).join('')}</select></label>` +
      inp('fr-slack', '重さの余り（kg）', r.slack, 'number', 'min="1" step="any"') + '</div>' +
      '<div class="f-grid3">' + inp('fr-fw', '1口の底面 縦（mm）', r.fw, 'number', 'min="100" step="any"') + inp('fr-fd', '1口の底面 横（mm）', r.fd, 'number', 'min="100" step="any"') +
      inp('fr-h', '空きの高さ（mm）', r.h, 'number', 'min="100" step="any"') + '</div>' +
      '<div class="f-grid">' + inp('fr-piece', '1個の重さの上限（kg）', r.piece, 'number', 'min="1" step="any"') +
      inp('fr-price', '値段（円・税抜・1件）', r.price, 'number', 'min="0" step="100"') + '</div>' +
      `<p class="f-hint" id="fr-basis">${esc(r.basis || '値段は、荷が確定していて発地・着地の道路距離があるとき、値段の式の既定値で埋めます。空にして登録すると同じ既定値になります')}</p>` +
      inp('fr-drops', '降ろせる地点（「、」で区切る。市区町村の正式な名前）', r.drops, 'text', 'autocomplete="off"') +
      '<p class="f-hint">運送会社: <b>架空 自社</b>（固定）。この便はこの画面を開いている来訪者の状態にだけ足され、サーバーのほかの来訪者には見えません。</p>' +
      `<div class="f-btns"><button type="button" class="btn-sm" id="fr-go"${r.busy ? ' disabled' : ''}>この便を登録する</button></div>` +
      `<p class="f-reg-err" id="fr-err" role="status">${esc(r.err)}</p><p class="f-hint" id="fr-done">${esc(r.done)}</p></div>`;
  }
  const dirTrips = () => (F.dir && F.dir.from && F.dir.to ? F.trips.filter((t) => prefOf(t.origin_code) === F.dir.from && prefOf(t.dest_code) === F.dir.to) : []);
  function renderDir() {
    const el = q('#f-dir');
    if (!el) return;
    el.hidden = F.role !== 'carrier';
    if (el.hidden) return;
    loadRegVeh();
    ensureMuniList();
    const prefs = Market.state.d ? Market.state.d.prefs : [];
    const opt = (v) => `<option value="">指定なし</option>${prefs.map((p) => `<option value="${esc(p)}"${p === v ? ' selected' : ''}>${esc(p)}</option>`).join('')}`;
    const d = F.dir || { from: '', to: '' }, key = `${d.from}>${d.to}`;
    if (el.dataset.k !== key) {
      el.dataset.k = key;
      if (!F.reg || F.reg.dirKey !== key) { F.reg = regDefaults(d.from, d.to); F.reg.dirKey = key; }
      const ts = dirTrips();
      el.innerHTML = '<div class="m-h f-sub-h">帰り便を登録する<span class="m-tag">帰りの向き</span></div>' +
        `<div class="f-grid"><label><span class="lbl">発地の県</span><select id="f-dir-from" class="ctl sm">${opt(d.from)}</select></label>` +
        `<label><span class="lbl">着地の県</span><select id="f-dir-to" class="ctl sm">${opt(d.to)}</select></label></div>` +
        (d.from && d.to ? `<p class="f-hint">${esc(d.from)}→${esc(d.to)}の便: ${ts.length ? ts.map((t) => esc(t.id)).join('・') : 'まだありません'}（架空）。下の欄で、この向きの帰り便を 1 本登録できます。</p>` + regFormHtml()
          : '<p class="f-hint">発地の県と着地の県を選ぶと、帰り便を登録する欄が開きます。</p>');
      scheduleRegPrice();
    }
  }
  // 市場を見るの区間（荷が多い向き）を受け取り、その逆向き（帰りの向き）で登録の欄を開く。(pair) か (荷が多い向きの発地の県, 着地の県)
  function openCarrier(a, b) {
    const pair = a && typeof a === 'object' ? a : (a && b ? { from: a, to: b } : null);
    if (!pair) return;
    F.dir = { from: pair.to, to: pair.from }; // 荷が多い向きの逆が、帰り便の向き
    F.reg = null;
    const el = q('#f-dir');
    if (el) el.dataset.k = '';
    F.role = 'carrier';
    App.setView('find');
    F.ver++;
    render();
  }

  function regPriceBox(r) {
    const p = q('#fr-price'), bs = q('#fr-basis');
    if (p && document.activeElement !== p) p.value = r.price;
    if (bs && r.basis) bs.textContent = r.basis;
  }
  // 値段の既定値。発地・着地・車種・荷が変わったとき、利用者が値段を直していなければ、値段の式（§21.6）の参考額で埋める
  function scheduleRegPrice() {
    const r = F.reg, c = cargoOf();
    if (!r || r.priceDirty || !r.o || !r.d || r.o === r.d || !c || !F.regVeh) return;
    const tok = ++r.tok;
    Work.api('/api/b/register_defaults', { origin_code: r.o, dest_code: r.d, vehicle: r.veh, shipment: { items: c.sh.items } })
      .then((res) => { if (tok !== r.tok || r.priceDirty) return; r.price = res.price.price_ex_tax_yen ?? ''; r.basis = res.price.basis; regPriceBox(r); })
      .catch((e) => { if (tok === r.tok) { r.basis = e.message; regPriceBox(r); } });
  }
  function regInput(e) {
    const r = F.reg, t = e.target;
    if (!r || !t.id || !t.id.startsWith('fr-')) return;
    const v = t.value;
    if (t.id === 'fr-o') { r.o = codeOfName(v); if (e.type === 'change') scheduleRegPrice(); }
    else if (t.id === 'fr-d') {
      const was = r.d ? muniFull(r.d) : '';
      r.d = codeOfName(v);
      if (e.type === 'change') {
        if (r.d && (r.drops === was || !r.drops)) { r.drops = muniFull(r.d); q('#fr-drops').value = r.drops; }
        scheduleRegPrice();
      }
    }
    else if (t.id === 'fr-dep') r.dep = v;
    else if (t.id === 'fr-arr') r.arr = v;
    else if (t.id === 'fr-veh') {
      r.veh = v; Object.assign(r, vehFill(v));
      for (const [id, k] of [['fr-slack', 'slack'], ['fr-fw', 'fw'], ['fr-fd', 'fd'], ['fr-h', 'h'], ['fr-piece', 'piece']]) q('#' + id).value = r[k];
      scheduleRegPrice();
    }
    else if (t.id === 'fr-slack') r.slack = v;
    else if (t.id === 'fr-fw') r.fw = v;
    else if (t.id === 'fr-fd') r.fd = v;
    else if (t.id === 'fr-h') r.h = v;
    else if (t.id === 'fr-piece') r.piece = v;
    else if (t.id === 'fr-drops') r.drops = v;
    else if (t.id === 'fr-price') { r.price = v; r.priceDirty = v !== ''; if (v === '') scheduleRegPrice(); }
  }
  async function registerTrip() {
    const r = F.reg, c = cargoOf(), fail = (m) => { r.err = m; r.done = ''; const el = q('#fr-err'); if (el) el.textContent = m; };
    if (!r || r.busy) return;
    if (!r.o) return fail('発地の市区町村を、一覧から選ぶか正式な名前（例: 東京都新宿区）で入れてください');
    if (!r.d) return fail('着地の市区町村を、一覧から選ぶか正式な名前（例: 兵庫県神戸市中央区）で入れてください');
    const names = String(r.drops).split(/[、,，\n]/).map((x) => x.trim()).filter(Boolean), drops = [];
    for (const n of names) { const code = codeOfName(n); if (!code) return fail(`降ろせる地点「${n}」は、市区町村の正式な名前（例: 兵庫県神戸市中央区）で入れてください`); drops.push(code); }
    r.busy = true; r.err = '';
    const go = q('#fr-go');
    if (go) go.disabled = true;
    try {
      const res = await Work.api('/api/b/register_trip', {
        origin_code: r.o, dest_code: r.d, depart_at: r.dep, arrive_at: r.arr, vehicle: r.veh, weight_slack_kg: Number(r.slack),
        unit_footprint_mm: [Number(r.fw), Number(r.fd)], clear_height_mm: Number(r.h), max_piece_kg: Number(r.piece), drop_points: drops,
        ...(r.price !== '' ? { price_ex_tax_yen: Number(r.price) } : {}), ...(c ? { shipment: { items: c.sh.items } } : {}),
      });
      const t = res.trip;
      F.carrier = t.carrier_id; F.sel = t.id;
      await loadTrips();
      await runMatch();
      r.done = `${t.id} を登録しました（${muniName(t.origin_code)}→${muniName(t.dest_code)}、${n0(t.price_ex_tax_yen)} 円・税抜）。一覧と地図に出ています。`;
      render();
      const dn = q('#fr-done'); if (dn) { dn.textContent = r.done; dn.scrollIntoView({ block: 'nearest' }); }
      fitTrips([t.id]);
      toast(`${t.id} を登録しました。「最初の状態に戻す」で消えます`);
    } catch (e) { fail(e.message); toast(e.message); } finally { r.busy = false; const g = q('#fr-go'); if (g) g.disabled = false; }
  }

  function syncEditForm(force) {
    const t = byId(q('#f-trip-sel').value);
    if (!t) return;
    const slack = q('#f-slack-in'), price = q('#f-price-in');
    if (force || slack.dataset.t !== t.id) {
      slack.value = regSlack(t); price.value = tripPrice(t); slack.dataset.t = t.id;
    }
  }

  /* ------------------------------------------------------------ 値段の式（SPEC §21.6。運送会社の役の画面だけ） */

  // 計算はサーバー（/api/b/price_formula → src/shipcompare/spaceb.py）。ここは入力を送って、返った値を並べるだけ
  const FORMULA_NOTE = '標準的運賃（2024 年 3 月 22 日告示）の個建運賃とリードタイムの割引の考え方に当てた参考額です。値段は運送会社が決めます';
  const FORMULA_LINE = '個建運賃の参考額 = 距離制運賃 × max（重さの割合, 床の割合）÷ 基準積載率 ×（1 − リードタイムの割引）';
  const pc1 = (v) => `${(Math.round(v * 1000) / 10).toLocaleString('ja-JP')}%`;

  function renderFormula() {
    const el = q('#f-formula');
    if (!el) return;
    if (F.role !== 'carrier') { el.innerHTML = ''; F.fm.built = false; F.fm.key = ''; return; }
    if (!F.fm.built) {
      el.innerHTML = '<div class="m-h f-sub-h">値段の式<span class="m-tag">運送会社の役だけ</span></div>' +
        `<p class="fm-line">${FORMULA_LINE}</p>` +
        '<div class="f-grid3"><label><span class="lbl">基準積載率（%）</span><input id="fm-rate" class="ctl sm num" type="number" min="10" max="100" step="5"></label>' +
        `<label><span class="lbl">リードタイムの割引</span><select id="fm-disc" class="ctl sm">${[0, 10, 20, 30, 40, 50].map((v) => `<option value="${v}">${v}%</option>`).join('')}</select></label>` +
        '<label><span class="lbl">地域の表</span><select id="fm-region" class="ctl sm"><option value="近畿">近畿運輸局</option><option value="関東">関東運輸局</option></select></label>' +
        '<label><span class="lbl">最低保証料（円）</span><input id="fm-min" class="ctl sm num" type="number" min="0" step="1000" placeholder="空欄"></label></div>' +
        '<p class="fm-note">最低保証料を下回るときは最低保証料。</p>' +
        '<div id="fm-out" class="fm-out"></div>' +
        `<p class="fm-note">${FORMULA_NOTE}。</p>`;
      el.querySelector('#fm-rate').value = F.fm.rate;
      el.querySelector('#fm-disc').value = String(F.fm.disc);
      el.querySelector('#fm-min').value = F.fm.minFee;
      if (!F.fm.bound) { el.addEventListener('input', fmInput); el.addEventListener('change', fmInput); F.fm.bound = true; }
      F.fm.built = true;
    }
    const c = cargoOf(), tid = q('#f-trip-sel').value, car = F.carriers.find((x) => x.id === F.carrier) || {};
    const region = F.fm.region || car.tariff_region || '関東';
    q('#fm-region').value = region;
    const out = q('#fm-out');
    if (!c || !tid) { out.innerHTML = '<p class="fm-miss">荷物を確定すると、その荷での参考額を出します。</p>'; return; }
    const rate = Number(F.fm.rate), mf = String(F.fm.minFee || '').trim();
    if (mf !== '' && !(Number(mf) >= 0)) { out.innerHTML = '<p class="fm-miss">最低保証料は 0 円以上の数で入れてください。</p>'; return; }
    if (!(rate >= 10 && rate <= 100)) { out.innerHTML = '<p class="fm-miss">基準積載率は 10〜100% で入れてください。</p>'; return; }
    const key = JSON.stringify([tid, body(), rate, F.fm.disc, region, mf]);
    if (key !== F.fm.key) {
      F.fm.key = key;
      const tok = ++F.fm.tok;
      Work.api('/api/b/price_formula', { ...body(), trip_id: tid, base_load_rate: rate / 100, lead_discount: F.fm.disc / 100, region, ...(mf !== '' ? { min_fee_yen: Number(mf) } : {}) })
        .then((r) => { if (tok === F.fm.tok) { F.fm.data = r.reference; F.fm.err = ''; drawFormula(); } })
        .catch((e) => { if (tok === F.fm.tok) { F.fm.data = null; F.fm.err = e.message; drawFormula(); } });
    }
    drawFormula();
  }

  function drawFormula() {
    const out = q('#fm-out'), d = F.fm.data;
    if (!out) return;
    if (F.fm.err) { out.innerHTML = `<p class="fm-miss">${esc(F.fm.err)}</p>`; return; }
    if (!d) { out.innerHTML = '<p class="fm-miss">計算しています…</p>'; return; }
    const by = d.ratio_by;
    out.innerHTML = '<div class="fm-tbl">' +
      `<div><span>距離制運賃（${esc(d.region)}運輸局 ${esc(d.tariff_class)}、荷を積む区間 ${esc(muniName(d.segment_origin_code))} → ${esc(muniName(d.segment_dest_code))} 道路 ${d.km.toLocaleString('ja-JP')} km）</span><b class="num">${n0(d.distance_fare_yen)} 円</b></div>` +
      `<div class="${by === '重さ' ? 'big' : ''}"><span>重さの割合 = ${kg(d.cargo_kg)} kg ÷ ${kg(d.max_load_kg)} kg</span><b class="num">${pc1(d.weight_ratio)}</b></div>` +
      `<div class="${by === '床' ? 'big' : ''}"><span>床の割合 = ${kg(d.cargo_floor_m2)} m² ÷ ${kg(d.floor_total_m2)} m²</span><b class="num">${pc1(d.floor_ratio)}</b></div>` +
      `<div><span>基準積載率</span><b class="num">${pc1(d.base_load_rate)}</b></div>` +
      `<div><span>リードタイムの割引</span><b class="num">${pc1(d.lead_discount)}</b></div>` +
      (d.min_fee_yen != null ? `<div><span>式の額 / 最低保証料</span><b class="num">${n0(d.formula_yen)} 円 / ${n0(d.min_fee_yen)} 円</b></div>` : '') +
      `<div class="sum"><span>個建運賃の参考額（税抜）${d.min_fee_applied ? '（最低保証料）' : ''}</span><b class="num">${n0(d.reference_yen)} 円</b></div>` +
      (d.trip_price_yen != null ? `<div><span>この便に登録した料金（架空・税抜）</span><b class="num">${n0(d.trip_price_yen)} 円</b></div>` : '') +
      '</div>' + (d.km_note ? `<p class="fm-km">${esc(d.km_note)}</p>` : '');
  }

  function fmInput(e) {
    const t = e.target;
    if (t.id === 'fm-rate') F.fm.rate = t.value;
    else if (t.id === 'fm-disc') F.fm.disc = Number(t.value);
    else if (t.id === 'fm-region') F.fm.region = t.value;
    else if (t.id === 'fm-min') F.fm.minFee = t.value;
    else return;
    renderFormula();
  }

  function render() {
    if (!F.loaded) { renderList(); return; }
    renderLeft();
    renderFormula();
    renderPairBar();
    renderList();
    syncTags();
    q('#f-fiction').textContent = F.fiction || '架空の運送会社と便です。';
    arcPara();
    q('#f-booking').textContent = F.booking || '積むかどうか、どこにどう積むかは運送会社が決めます。受付で量って条件に合わないときは受け付けません。';
  }

  // 地図の凡例の欄に、直線で表示している便がある旨を 1 行足す
  function arcPara() {
    const any = F.trips.some((t) => F.geo[t.id] && F.geo[t.id].arc), nt = q('#find-notes');
    let p = q('#f-arc');
    if (!any) { if (p) p.remove(); return; }
    if (!p && nt) { p = document.createElement('p'); p.id = 'f-arc'; p.className = 'f-n3'; nt.insertBefore(p, nt.querySelector('.f-lg')); }
    if (p) p.textContent = '地図で直線の弧で表示している便は、経路のデータが無い便と、登録した便です。';
  }

  function tick() {
    if (!F.active) return;
    if (F.role === 'carrier' && F.loaded) renderFormula();
    document.querySelectorAll('.f-cd').forEach((el) => {
      const ms = new Date(el.dataset.exp).getTime() - Date.now();
      const s = Math.max(0, Math.ceil(ms / 1000));
      el.textContent = `残り ${cdFmt(ms)}`;
      el.classList.toggle('low', ms < 60000);
      if (ms <= 0 && !el.dataset.fired) { el.dataset.fired = '1'; refresh(); }
    });
    // サーバー側の状態（期限切れ・承諾）を数秒おきに取り直す
    if (++F.poll % 5 === 0 && !F.busy) loadTrips().then(() => runMatch());
    const c = cargoOf();
    if (c && F.loaded && stamp(c) !== F.lastCargo) runMatch();
  }

  /* ------------------------------------------------------------ 受け取り場所の検索（便の降ろせる地点を先頭に） */

  function bindDest() {
    const inp = q('#f-dest-q'), ul = q('#f-sug');
    let items = [], on = -1;
    const close = () => { ul.classList.remove('open'); inp.setAttribute('aria-expanded', 'false'); on = -1; };
    const render = () => {
      const v = inp.value.trim(), drops = dropCodes(), set = new Set(drops);
      let res = [];
      if (!inp.dataset.typed || !v) res = drops;
      else {
        res = drops.filter((c) => muniFull(c).includes(v));
        for (let i = 0; i < S.idx.names.length && res.length < 14; i++) {
          const c = S.idx.codes[i];
          if (!set.has(c) && (S.idx.prefs[i] + S.idx.names[i]).includes(v)) res.push(c);
        }
      }
      items = res.slice(0, 14);
      on = items.length ? 0 : -1;
      let h = '';
      items.forEach((c, n) => {
        if (n === 0 || set.has(c) !== set.has(items[n - 1])) h += `<li class="grp">${set.has(c) ? '便の降ろせる地点' : '自由に探した地点（どの便も降ろせない場合があります）'}</li>`;
        h += `<li role="option" data-c="${c}" class="${n === on ? 'on' : ''}"><span>${esc(muniName(c))}</span><small>${esc(prefOf(c))}</small></li>`;
      });
      ul.innerHTML = h || '<li class="empty">該当する市区町村がありません</li>';
      ul.classList.add('open');
      inp.setAttribute('aria-expanded', 'true');
    };
    const pick = (c) => { close(); inp.blur(); inp.dataset.typed = ''; F.dest = c; F.ver++; runMatch(); };
    inp.addEventListener('focus', () => { inp.select(); inp.dataset.typed = ''; render(); });
    inp.addEventListener('input', () => { inp.dataset.typed = '1'; render(); });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!items.length) return;
        on = (on + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        [...ul.querySelectorAll('li[data-c]')].forEach((li, n) => li.classList.toggle('on', n === on));
        e.preventDefault();
      } else if (e.key === 'Enter') { if (on >= 0 && items[on]) pick(items[on]); } else if (e.key === 'Escape') { close(); }
    });
    ul.addEventListener('mousedown', (e) => { const li = e.target.closest('li[data-c]'); if (li) { e.preventDefault(); pick(li.dataset.c); } });
    inp.addEventListener('blur', () => setTimeout(() => { close(); if (F.loaded) renderLeft(); }, 120));
  }

  /* ------------------------------------------------------------ 操作 */

  function setRole(r) {
    F.role = r;
    F.ver++;
    render();
    if (F.ctOpen) renderContract();
    if (r === 'carrier') fitTrips(F.trips.filter((t) => t.carrier_id === F.carrier).map((t) => t.id));
    writeHash();
  }

  function download() {
    const out = {
      fictional: true, note: '画面の中だけで変えた値です。サーバーには書いていません。架空の運送会社と便です。',
      saved_at: new Date().toISOString(),
      trips: Object.entries(F.edits).map(([id, e]) => ({
        trip_id: id, carrier_id: (byId(id) || {}).carrier_id,
        ...(e.slack !== undefined ? { weight_slack_kg: e.slack } : {}), ...(e.price !== undefined ? { price_ex_tax_yen: e.price } : {}),
      })),
    };
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 2)], { type: 'application/json' }));
    a.download = 'carrier_trip_edits.json';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  function bind() {
    q('#f-role').addEventListener('click', (e) => { const b = e.target.closest('button[data-role]'); if (b) setRole(b.dataset.role); });
    q('#f-cat').innerHTML = '<option value="">指定なし</option>' + CATEGORIES.map((c) => `<option value="${c}">${c}</option>`).join('');
    q('#f-holdmin').innerHTML = HOLD_CHOICES.map((m) => `<option value="${m}">${holdLabel(m)}</option>`).join('');
    q('#f-cat').addEventListener('change', (e) => { F.category = e.target.value; runMatch(); });
    q('#f-container').addEventListener('change', (e) => { F.inContainer = e.target.checked; runMatch(); });
    q('#f-fragile').addEventListener('change', (e) => { F.fragile = e.target.checked; runMatch(); });
    q('#f-box').addEventListener('change', (e) => { F.isBox = e.target.checked; runMatch(); });
    q('#f-holdmin').addEventListener('change', (e) => { F.holdMin = Number(e.target.value); render(); });
    q('#f-change-cargo').addEventListener('click', () => {
      App.setView('compare');
      const b = q('#cargo-toggle');
      if (b && b.getAttribute('aria-expanded') !== 'true') b.click();
    });
    q('#f-carrier-sel').addEventListener('change', (e) => {
      F.carrier = e.target.value; F.ver++; render();
      fitTrips(F.trips.filter((t) => t.carrier_id === F.carrier).map((t) => t.id));
    });
    q('#f-trip-sel').addEventListener('change', (e) => { syncEditForm(true); select(e.target.value, { fly: true }); });
    q('#f-dir').addEventListener('change', (e) => {
      if (e.target.id !== 'f-dir-from' && e.target.id !== 'f-dir-to') return;
      F.dir = { from: q('#f-dir-from').value, to: q('#f-dir-to').value };
      q('#f-dir').dataset.k = '';
      F.ver++;
      render();
      const ts = dirTrips();
      if (ts.length) { F.carrier = ts[0].carrier_id; F.sel = ts[0].id; F.ver++; render(); fitTrips(ts.map((t) => t.id)); }
    });
    q('#f-dir').addEventListener('input', regInput);
    q('#f-dir').addEventListener('change', regInput);
    q('#f-dir').addEventListener('click', (e) => { if (e.target.id === 'fr-go') registerTrip(); });
    q('#f-apply').addEventListener('click', () => {
      const t = byId(q('#f-trip-sel').value);
      if (!t) return;
      const sv = q('#f-slack-in').value, pv = q('#f-price-in').value, s = Number(sv), p = Number(pv);
      if (sv === '' || pv === '' || !(s >= 0) || !(p >= 0)) { toast('重さの余りと料金は 0 以上の数で入れてください'); return; }
      const e = {};
      if (s !== t.weight_slack_kg) e.slack = s;
      if (p !== t.price_ex_tax_yen) e.price = p;
      if (Object.keys(e).length) F.edits[t.id] = e; else delete F.edits[t.id];
      F.ver++;
      render();
      toast('画面の中だけに反映しました。サーバーには書いていません');
    });
    q('#f-dl').addEventListener('click', download);
    q('#f-reset').addEventListener('click', () => act(async () => {
      await Work.api('/api/b/reset', {});
      F.mine.clear(); F.edits = {}; F.results = {};
      q('#f-slack-in').dataset.t = '';
      F.reg = null; F.fm.data = null; F.fm.key = '';
      const dEl = q('#f-dir'); if (dEl) dEl.dataset.k = '';
      toast('仮予約・登録した便・重さの余りを最初の架空データに戻しました');
    }));
    q('#find-right').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-act]'), card = e.target.closest('.f-card');
      if (btn) {
        const a = btn.dataset.act, id = card && card.dataset.t;
        if (a === 'hold') {
          act(async () => {
            const r = await Work.api('/api/b/hold', { ...body(), trip_id: id, hold_minutes: F.holdMin, ...(bh() && bh().shipper ? { shipper: bh().shipper } : {}) });
            F.mine.add(r.hold.hold_id);
            F.sel = id;
          });
        } else if (a === 'accept' || a === 'decline') {
          act(async () => { await Work.api('/api/b/decide', { hold_id: btn.dataset.h, decision: a }); });
        } else if (a === 'to-carrier') {
          const t = byId(id);
          if (t) F.carrier = t.carrier_id;
          setRole('carrier');
        } else if (a === 'contract') { openContract(btn.dataset.h);
        } else if (a === 'to-compare') {
          App.setView('compare');
          Work.openWork(1);
        } else if (a === 'try-ex') { tryExample();
        } else if (a === 'to-compare-now') { compareNow();
        } else if (a === 'clear-pair') { F.pair = null; F.onlyPair = false; F.ver++; render(); }
        return;
      }
      if (card) select(card.dataset.t, { fly: true });
    });
    q('#find-right').addEventListener('change', (e) => { if (e.target.id === 'f-onlypair') { F.onlyPair = e.target.checked; F.ver++; render(); } });
    q('#find-right').addEventListener('keydown', (e) => {
      const card = e.target.closest && e.target.closest('.f-card');
      if (card && e.target === card && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); select(card.dataset.t, { fly: true }); }
    });
    bindDest();
    bindContract();
  }

  /* ------------------------------------------------------------ 書面の下書き（SPEC §21.3） */

  async function loadContract() {
    const id = F.ctOpen;
    if (!id) return;
    const inp = F.ct[id] || {};
    try {
      F.ctData = (await Work.api('/api/b/contract', { hold_id: id, fuel_surcharge: inp.fuel || null, payment_method: inp.pay || null })).contract;
      F.err = '';
    } catch (e) { F.ctData = null; toast(e.message); F.ctOpen = null; }
    renderContract();
  }

  function openContract(id) { F.ctOpen = id; loadContract(); }

  function renderContract() {
    const el = q('#f-contract'), d = F.ctData;
    el.hidden = !(F.ctOpen && d);
    if (el.hidden) return;
    const carrierRole = F.role === 'carrier', inp = F.ct[F.ctOpen] || {};
    const sel = (k, list, cur) => `<select class="ctl sm" data-ct="${k}"><option value="">選んでください</option>${list.map((x) => `<option value="${esc(x)}"${x === cur ? ' selected' : ''}>${esc(x)}</option>`).join('')}</select>`;
    const rows = d.items.map((it) => {
      let v;
      if (carrierRole && it.by === 'carrier') v = sel(it.key === 'surcharge' ? 'fuel' : 'pay', it.key === 'surcharge' ? FUEL_CHOICES : it.options, it.value);
      else if (it.value) v = esc(it.value);
      else v = it.by === 'carrier' ? '<em class="fc-miss">未記入（運送会社の役が選びます）</em>' : '<em class="fc-miss">未登録</em>';
      return `<div class="fc-row${it.legal ? ' legal' : ''}"><span>${esc(it.label)}</span><b>${v}</b></div>`;
    }).join('');
    const miss = d.missing.length ? `<p class="fc-missing">${d.missing.map((m) => `未記入: ${esc(m)}`).join('<br>')}</p>` : '<p class="fc-ready">必要な項目はすべて埋まっています。</p>';
    const done = F.confirmed.has(d.hold_id);
    q('#fc-body').innerHTML = `<div class="fc-sub num">仮予約 ${esc(d.hold_id)}・便 ${esc(d.trip_id)}</div>${rows}${miss}` +
      (done ? '<p class="fc-done">確定しました（架空のデモです。実際の契約は結ばれません）。</p>' : '');
    const b = q('#fc-confirm');
    b.disabled = !d.ready || done;
    b.textContent = done ? '確定しました' : '確定する';
  }

  function bindContract() {
    q('#fc-body').addEventListener('change', (e) => {
      const k = e.target.dataset && e.target.dataset.ct;
      if (!k) return;
      (F.ct[F.ctOpen] = F.ct[F.ctOpen] || {})[k] = e.target.value;
      loadContract();
    });
    q('#fc-close').addEventListener('click', () => { F.ctOpen = null; renderContract(); });
    q('#fc-print').addEventListener('click', () => window.print());
    q('#fc-confirm').addEventListener('click', () => {
      if (F.ctData && F.ctData.ready) { F.confirmed.add(F.ctData.hold_id); renderContract(); }
    });
  }

  /* ------------------------------------------------------------ 公開 */

  async function init() {
    if (!document.querySelector('link[href$="find_extra.css"]')) { // 登録の欄の見た目（index.html に <link> があれば何もしない）
      const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = 'find_extra.css'; document.head.appendChild(l);
    }
    bind();
    map.on('moveend', () => spreadTags());
    await loadTrips();
    if (F.loaded) {
      if (F.trips.length && !F.trips.some((t) => t.drop_points.includes(F.dest))) F.dest = F.trips[0].dest_code;
      await runMatch();
    }
    render();
    F.timer = setInterval(tick, 1000);
  }

  // 見方を開いたとき。pair は市場を見るで選んだ区間（荷が多い向き）。
  async function enter(pair) {
    F.active = true;
    if (pair) { F.pair = { from: pair.from, to: pair.to }; F.onlyPair = true; }
    await loadTrips();
    const rev = F.pair ? F.trips.filter(isReverse) : [];
    if (pair && rev.length) {
      if (!rev.some((t) => t.drop_points.includes(F.dest))) F.dest = rev[0].dest_code;
      F.sel = rev[0].id;
    }
    if (pair && F.dir && F.role === 'carrier') {
      const ts = dirTrips();
      if (ts.length) { F.carrier = ts[0].carrier_id; F.sel = ts[0].id; }
    } else if (pair) F.dir = null;
    await runMatch();
    render();
    fitTrips((rev.length ? rev : F.trips).map((t) => t.id));
    if (pair && typeof Sheet !== 'undefined') Sheet.scrollTo(F.role === 'carrier' ? '#find-view' : '#find-right');
  }
  function leave() { F.active = false; F.markers.forEach((m) => { m.el.style.display = 'none'; }); q('#f-contract').hidden = true; }
  function onCargo() { if (F.loaded) runMatch(); }
  function hashParams(p) { p.set('r', F.role); if (F.sel) p.set('t', F.sel); }
  function readHash(p) { if (p.get('r') === 'carrier') F.role = 'carrier'; if (p.get('t')) F.sel = p.get('t'); }

  return { init, enter, leave, layers, click, onCargo, hashParams, readHash, state: F, select, setRole, openCarrier };
})();
