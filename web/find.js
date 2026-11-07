'use strict';
/* 空きを探す・つなぐ（SPEC §16.2、架空のデモ）。
   照合・残りの重さ・仮予約・承諾は /api/b/*（tools/serve_web.py → src/shipcompare/spaceb.py）が返す結果を表示するだけで、
   ここでは判定しない。例外は地図上の位置、並べ替え、残り時間の表示、画面の中だけの変更（保存しない）。 */

const Find = (() => {
  const F = {
    role: 'shipper', trips: [], carriers: [], holds: [], note: '', fiction: '', booking: '', results: {}, loaded: false, err: '',
    dest: '46201', category: '', inContainer: false, fragile: false, isBox: true, holdMin: 30,
    sel: null, pair: null, onlyPair: false, carrier: 'C1', edits: {}, mine: new Set(), busy: false,
    ver: 0, active: false, geo: {}, markers: new Map(), matchTok: 0, lastCargo: '', timer: null, poll: 0,
    fm: { rate: 70, disc: 0, minFee: '', region: null, key: '', data: null, err: '', built: false, tok: 0 }, scBack: {}, scLoaded: false, bhSid: null, ct: {}, ctOpen: null, ctData: null, confirmed: new Set(), // SPEC §21
    autoDone: false, autoNote: '', tipN: 0, postOpen: null, scList: [], ex: { key: '', id: null, busy: false }, dir: null, // SPEC §30.3（0 便の案内の例の荷、運送会社の役で登録する向き）
  };
  const PERIOD = 14;
  const SPACE = {
    free_bed: ['荷台の空き床', '荷台の空いている床に載せる形'],
    inside_rb_upper: ['ロールボックスの上段', 'ロールボックスパレットの上の段に載せる形'],
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

  /* ------------------------------------------------------------ 用語の説明（タップ・キーボードで開く） */
  const TIPS = {
    '1口': '荷台の箱 1 つ分の単位',
    '基準積載率': '荷が入る割合の前提',
    'リードタイムの割引': '早く頼むと安くなる率',
  };
  // 用語のあとに置く ? ボタン。押す（Enter・Space）と説明が開き、もう一度押すか Esc で閉じる
  const tip = (term) => `<button type="button" class="f-tip" data-tip="${esc(term)}" aria-expanded="false" aria-label="${esc(term)}の説明">?</button>` +
    `<span class="f-tipbox" role="note" hidden>${esc(TIPS[term])}</span>`;
  // 一覧の中では最初の 1 件にだけ付ける（毎回の描画で数え直す）
  const tipOnce = (term) => (F.tipN++ === 0 ? tip(term) : '');
  function closeTips(except) {
    document.querySelectorAll('.f-tip[aria-expanded="true"]').forEach((b) => {
      if (b === except) return;
      b.setAttribute('aria-expanded', 'false');
      const box = b.nextElementSibling;
      if (box) box.hidden = true;
    });
  }
  function toggleTip(btn) {
    const open = btn.getAttribute('aria-expanded') !== 'true';
    closeTips(btn);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    const box = btn.nextElementSibling;
    if (box) box.hidden = !open;
  }

  // 運送会社が登録した空きの便を、求荷求車の掲示板や LINE に貼る文にする
  function postText(t) {
    const rem = shownRemain(t);
    const lines = ['【空車（帰り便）のご案内】',
      `区間: ${muniFull(t.origin_code)} → ${muniFull(t.dest_code)}`,
      `出発: ${hhmm(t.depart_at)}　到着: ${hhmm(t.arrive_at)}`,
      `車: ${t.vehicle}`,
      `空き: 重さ ${kg(rem)}kg まで${t.remaining_floor_m2 != null ? `、床 ${kg(t.remaining_floor_m2)}m²` : ''}`];
    if (t.max_piece_kg != null) lines.push(`1 個の重さ: ${kg(t.max_piece_kg)}kg まで`);
    if (t.unit_footprint_mm) lines.push(`1 口の底面: ${n0(t.unit_footprint_mm[0])}×${n0(t.unit_footprint_mm[1])}mm`);
    lines.push(`運賃: ${n0(tripPrice(t))} 円（${t.price_unit || '1口'}・税抜。高速代などは別）`);
    lines.push('積めるかどうかは、受付で量って決めます。', '連絡先: （ここに書き入れてください）', '※ 下書きです。日時と連絡先を確かめてから貼ってください。');
    if (F.edits[t.id]) lines.push('※ 保存していない値で作った文です。');
    return lines.join('\n');
  }

  /* ------------------------------------------------------------ 場所と向き */

  const codeIdx = () => (F.codeMap || (F.codeMap = new Map(S.idx.codes.map((c, i) => [c, i]))));
  const muniName = (c) => { const i = codeIdx().get(c); return i === undefined ? c : S.idx.names[i]; };
  const muniFull = (c) => { const i = codeIdx().get(c); return i === undefined ? c : `${S.idx.prefs[i]}${S.idx.names[i]}`; };
  const prefOf = (c) => { const i = codeIdx().get(c); return i === undefined ? '' : S.idx.prefs[i]; };
  const lonlat = (c) => { const i = codeIdx().get(c); return i === undefined ? null : [S.idx.lon[i], S.idx.lat[i]]; };
  // 荷が多い向きの区間（from → to）の、荷が少ない向き（to → from）を走る便か
  const isReverse = (t) => !!F.pair && prefOf(t.origin_code) === F.pair.to && prefOf(t.dest_code) === F.pair.from;
  const inScope = (t) => !F.pair || isReverse(t);
  // 全国の物流傾向で選んだ区間の帰り便（荷が少ない向き）があるか。便の一覧を読む前は、あるものとして扱う
  const hasReturn = (pair) => !F.loaded || F.trips.some((t) => prefOf(t.origin_code) === pair.to && prefOf(t.dest_code) === pair.from);
  // 区間を引き継いだ荷主の集荷は、帰り便の出発地から（東京都の帰り便なら東京都内）。無ければ地図で選んだ出発地
  const pickupOrigin = () => { const r = F.pair ? F.trips.find(isReverse) : null; return r ? r.origin_code : S.origin; };
  const dropCodes = () => [...new Set(F.trips.flatMap((t) => t.drop_points))];
  const hhmm = (s) => `${Number(s.slice(5, 7))}/${Number(s.slice(8, 10))} ${s.slice(11, 16)}`;
  // 運送会社の役が画面の中だけで変えた値（F.edits）は、盤（サーバー）には登録されていない。照合・仮予約・書面は盤の値を使うので、
  // 荷主のカードは盤の値だけを出し、変えた値は運送会社のカードに「未登録」と分けて出す（SPEC §42）
  const tripPrice = (t) => (F.edits[t.id] && F.edits[t.id].price !== undefined ? F.edits[t.id].price : t.price_ex_tax_yen);
  const regSlack = (t) => (F.edits[t.id] && F.edits[t.id].slack !== undefined ? F.edits[t.id].slack : t.weight_slack_kg);
  const boardPrice = (t) => t.price_ex_tax_yen;
  // 画面の中だけで変えた登録値に合わせて、サーバーの残りを足し引きして見せる（運送会社のカードだけ。サーバーの照合には使われない）
  const shownRemain = (t) => Math.max(0, t.remaining_slack_kg + (regSlack(t) - t.weight_slack_kg));
  const cargoOf = () => {
    const sh = Work.confirmedShipment();
    if (!sh) return null;
    let n = 0, w = 0;
    for (const it of sh.items) { n += it.count; w += it.count * it.weight_kg; }
    return { sh, n, w: Math.round(w * 100) / 100, label: sh.cargo_name || (Work.scenarioId() ? `例 ${Work.scenarioId()}` : '手入力') };
  };
  // 場面 S6 などの追加項目（集荷の地点・届けばよい日の幅・荷主の名称）。場面データの backhaul（SPEC §21.1）
  const bh = () => (Work.scenarioId() ? F.scBack[Work.scenarioId()] || null : null);
  const stamp = (c) => JSON.stringify([c.sh.items, F.dest, F.category, F.inContainer, F.fragile, F.isBox, Work.scenarioId(), S.origin]);
  const body = () => {
    const c = cargoOf(), b = bh();
    return { shipment: { items: c.sh.items }, dest_code: F.dest, category: F.category || null,
      in_container: F.inContainer, fragile: F.fragile, is_box: F.isBox,
      origin_code: b ? b.origin_code : pickupOrigin(), // 集荷の地点。場面に無ければ、全国の物流傾向で選んだ区間の帰り便の出発地、無ければ地図で選んだ出発地（SPEC §21.2）
      ...(b ? { window_from: b.window_from, window_to: b.window_to } : {}) };
  };
  // 場面が変わったら、受け取り場所と箱の有無をその場面の値にそろえる
  function syncScenario() {
    const sid = Work.scenarioId() || '';
    if (F.bhSid === sid) return;
    F.bhSid = sid;
    const b = bh();
    if (b) { F.dest = (F.pair && F.pairDest) || b.dest_code; F.isBox = b.is_box; }   // 全国の物流傾向で選んだ区間があるときは、受け取り場所を場面の値で上書きしない
    else F.isBox = true;   // 追加の条件が無い場面は、直方体の箱として始める（前の場面の「箱でない」を引きずらない）
  }

  /* ------------------------------------------------------------ サーバーとのやり取り */

  async function loadTrips() {
    try {
      const r = await Work.api('/api/b/trips');
      F.trips = r.trips; F.carriers = r.carriers; F.holds = r.holds; F.note = r.note; F.fiction = r.fiction_notice; F.booking = r.booking_notice;
      if (r.board_now) { F.boardNow = r.board_now; F.skew = new Date(r.board_now).getTime() - Date.now(); } // 盤の時計（デモの時刻、SPEC §34.6）
      F.demoLabel = r.demo_now_label || F.demoLabel;
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
    if (!F.autoDone && !F.pair && !F.err && F.loaded && Work.version() === 1 && Work.scenarioId() === 'S1' && F.trips.length && !F.trips.some((t) => F.results[t.id] && F.results[t.id].ok)) autoExample();
  }

  // 最初の荷（場面 S1）はどの便にも載らない。そのままだと「0 便が載ります」から始まるので、載る例の荷を一度だけ入れる（荷主が触る前だけ）
  async function autoExample() {
    F.autoDone = true;
    let found = null;
    try {
      for (const sc of [...F.scList.filter((x) => x.backhaul), ...F.scList.filter((x) => !x.backhaul)]) {
        const b = sc.backhaul;
        const r = await Work.api('/api/b/match', {
          shipment: { items: sc.shipment.items }, dest_code: b ? b.dest_code : F.dest, category: null, in_container: false, fragile: false,
          is_box: b ? b.is_box : true, origin_code: b ? b.origin_code : S.origin, ...(b ? { window_from: b.window_from, window_to: b.window_to } : {}),
        });
        if (r.results.some((x) => x.ok)) { found = sc; break; }
      }
    } catch (e) { found = null; }
    if (!found || Work.version() !== 1 || F.pair) return;   // 区間を引き継いだときは、その区間に載る荷を enter() が入れる
    const sel = document.querySelector('#sc-select'), ok = document.querySelector('#ed-confirm');
    if (!sel || !ok) return;
    sel.value = found.id;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    document.querySelector('#ed-confirm').click(); // 場面を選ぶと欄が描き直されるので、押す直前に探し直す
    for (let i = 0; i < 80 && Work.scenarioId() !== found.id; i++) await new Promise((r) => setTimeout(r, 100));
    if (Work.scenarioId() !== found.id) return;
    F.bhSid = null;
    F.autoNote = `最初の荷物は載らないため、載る例の荷物（例 ${found.id} ${fict(found.name)}）に替えました。「荷物を変える」で自分の荷物にできます。`;
    await runMatch();
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
      // 直線の弧が海をまたぐときは、トラックを走らせない（海の上を走るのを防ぐ）。線だけ残す
      const noVeh = rt.kind === 'arc' && !Rt.pathOk('truck', rt.path);
      if (!noVeh) for (const sh of [0, -PERIOD]) items.push({ id: t.id, path: rt.path, ts: rt.cum.map((c) => (c / rt.total) * dur + ph + sh) });
      F.geo[t.id] = { a, b, rt, items, dur, ph, arc: rt.kind === 'arc', noVeh };
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
      if (p >= 1 || g.noVeh) continue;
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

  // 案内の表示で区間を引き継いだときは、合う便（無ければ、その向きの便）の札だけを出す。残りの便は「全部を見る」で出る
  function tagShown(t) {
    if (!(document.body.classList.contains('guided') && F.pair && F.role === 'shipper')) return true;
    const rev = F.trips.filter(isReverse), ok = rev.filter((x) => (F.results[x.id] || {}).ok);
    return (ok.length ? ok : rev).includes(t);
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
        m.el.tabIndex = -1;   // 一覧の札がキーボードの道。地図の札はタブで止めない（maplibre は作るときに tabindex=0 を付けるので、作ったあとに外す）
        F.markers.set(t.id, m);
      }
      const lv = level(t), r = F.role === 'shipper' ? F.results[t.id] : null;
      m.el.textContent = `${t.id} 架空${r ? (r.ok ? ' ○' : ' ×') : ''}`;
      m.el.setAttribute('aria-label', `${t.id} ${t.carrier_name}（架空の便）${r ? (r.ok ? '、載ります' : '、載りません') : ''}`);
      m.el.title = g.arc ? (t.registered ? ARC_NOTE_REG : ARC_NOTE_NODATA) : '';
      m.el.classList.toggle('dim', lv < 0.5);
      m.el.classList.toggle('on', t.id === F.sel);
      m.el.style.display = F.active && tagShown(t) ? '' : 'none';
    }
    spreadTags();
  }

  // 名札が重ならないように、画面上の位置で上下にずらす（SPEC §32 #9）。近い名札は、先に置いた名札と重ならない最初の段に置く
  function spreadTags() {
    if (!F.active) return;
    const W = 76, H = 26, placed = [];
    // 左下の注意の欄と右の一覧に隠れる名札は、見える側（欄のすぐ外）に寄せる（SPEC §33 #14）。広い画面だけ。狭い画面は下のパネルが地図の下半分を覆うので扱わない
    const cr = map.getContainer().getBoundingClientRect(), blocks = [];
    if (window.innerWidth >= 900) {
      for (const id of ['#find-notes', '#find-right']) {
        const e = q(id);
        if (!e || !e.getClientRects().length) continue;
        const r = e.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) blocks.push({ l: r.left - cr.left - 4, r: r.right - cr.left + 4, t: r.top - cr.top - 4, b: r.bottom - cr.top + 4 });
      }
    }
    const items = F.trips.map((t) => ({ t, m: F.markers.get(t.id) })).filter((x) => x.m && x.m.el.style.display !== 'none');
    for (const { t, m } of items) {
      const g = F.geo[t.id];
      if (!g) continue;
      const p = map.project(m.mk.getLngLat());
      let k = 0, dy = 0, dx = 0;
      for (; k < 14; k++) {
        dy = k === 0 ? 0 : (k % 2 ? 1 : -1) * Math.ceil(k / 2) * (H + 2);
        dx = 0;
        // 欄に重なるときは、欄の左端か右端の外まで横にずらす（近い方）
        for (const b of blocks) {
          const x = p.x + dx, y = p.y + dy;
          if (x + W / 2 > b.l && x - W / 2 < b.r && y + H / 2 > b.t && y - H / 2 < b.b) {
            const toLeft = b.l - W / 2 - p.x, toRight = b.r + W / 2 - p.x;
            dx = Math.abs(toLeft) <= Math.abs(toRight) ? toLeft : toRight;
          }
        }
        if (!placed.some((r) => Math.abs(r.x - (p.x + dx)) < W && Math.abs(r.y - (p.y + dy)) < H + 1)) break;
      }
      placed.push({ x: p.x + dx, y: p.y + dy });
      m.mk.setOffset([dx, dy]);
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
      { padding: { top: 120, bottom: 150, left: 440, right: rw }, maxZoom: 7.6, ...MapFx.cam(1800, { curve: 1.4 }), ...MapFx.rest() });
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
  // 画面の「いま」は盤の時計（デモの時刻）。実際の日付には依存しない
  const nowMs = () => Date.now() + (F.skew || 0);
  const cdText = (h) => cdFmt(new Date(h.expires_at).getTime() - nowMs());

  function barHtml(t, cargoKg, edited) {
    const slack = edited ? regSlack(t) : t.weight_slack_kg, reg = Math.max(1, slack), rem = edited ? shownRemain(t) : t.remaining_slack_kg;
    const fill = Math.min(100, (rem / reg) * 100), need = cargoKg === null ? null : Math.min(100, (cargoKg / reg) * 100);
    let txt = `<span>積める重さの残り <b class="num">${kg(rem)}</b> / ${kg(slack)} kg</span>`;
    if (cargoKg !== null) txt += `<span class="num">この荷 ${kg(cargoKg)} kg</span>`;
    const shelf = t.remaining_shelf_kg !== null && t.remaining_shelf_kg !== undefined ? `<div class="f-shelf num">棚の耐荷重の残り ${kg(t.remaining_shelf_kg)} kg</div>` : '';
    // 床の空きは盤の残りと登録値（API が floor_free_m2 を必ず返す）。数が読めないときは行を出さない。棚の形は車の床ではなく 1 口の床
    const fNum = (v) => typeof v === 'number' && Number.isFinite(v);
    const floor = fNum(t.remaining_floor_m2) && fNum(t.floor_free_m2)
      ? `<div class="f-shelf num">${t.floor_is_unit ? '1口の床の空き' : '床の空き'} ${kg(t.remaining_floor_m2)} / ${kg(t.floor_free_m2)} m²</div>` : '';
    return `<div class="f-bar"><div class="f-track"><i style="width:${fill.toFixed(1)}%"></i>${need !== null ? `<u style="left:${need.toFixed(1)}%"></u>` : ''}</div>` +
      `<div class="f-bar-txt">${txt}</div>${shelf}${floor}</div>`;
  }

  function detailHtml(t) {
    const [sp, spTip] = SPACE[t.space_type] || [t.space_type, ''];
    const row = (k, v) => `<div><span>${k}</span><b>${v}</b></div>`;
    // 閉じた欄に入れ、5 行にまとめる（案内の画面は文字を増やさない）
    return '<details class="f-detail-d"><summary>便の条件</summary><div class="f-detail">' +
      row('空きの形・高さ', `<span title="${esc(spTip)}">${esc(sp)}</span>・${n0(t.clear_height_mm)} mm`) +
      row('1口・1個の上限', `${n0(t.unit_footprint_mm[0])}×${n0(t.unit_footprint_mm[1])} mm・${kg(t.max_piece_kg)} kg`) +
      row('容器', (t.container_required ? '容器入りだけ' : '指定なし') + (t.accepts_unboxed ? '。箱でない荷も受ける（木枠など）' : '')) +
      row('降ろせる地点', esc(t.drop_points.map(muniName).join('、')) + (t.pickup_code ? `。集荷は${esc(muniName(t.pickup_code))}から ${kg(t.pickup_radius_km)} km 以内` : '')) +
      row('受けない品目', esc(t.refused_categories.join('、')) || 'なし') + '</div></details>';
  }

  function holdAreaHtml(t, r) {
    const h = holdOf(t);
    if (h && h.state === 'held') {
      return `<div class="f-hold held"><div class="f-hold-row"><b title="承諾待ちの予約">仮予約中</b><span class="num">${esc(h.hold_id)}・${kg(h.weight_kg)} kg</span>` +
        `<span class="f-cd num" data-exp="${esc(h.expires_at)}">残り ${cdText(h)}</span></div>` +
        `<div class="f-hold-msg"><b class="num">${md(h.expires_at)}</b> までに運送会社が承諾しないと、予約は取り消されます。</div>` +
        '<button type="button" class="btn-link" data-act="to-carrier">運送会社の画面に切り替えて承諾する</button></div>';
    }
    if (h && h.state === 'accepted') {
      return `<div class="f-hold accepted"><div class="f-hold-row"><b>承諾されました</b><span class="num">${esc(h.hold_id)}・${kg(h.weight_kg)} kg</span></div>` +
        `<div class="f-hold-msg">${kg(h.weight_kg)} kg 分を押さえました。架空のデモなので、支払いや連絡はありません。</div>` +
        `<button type="button" class="btn-primary sm" data-act="contract" data-h="${esc(h.hold_id)}">運送の約束の書面を見る</button></div>`;
    }
    if (h && (h.state === 'expired' || h.state === 'declined')) {
      const why = h.state === 'declined' ? '運送会社が辞退しました。' : '締切を過ぎました。';
      return `<div class="f-hold lost"><div class="f-hold-row"><b>${h.state === 'declined' ? '辞退されました' : '期限切れ'}</b><span class="num">${esc(h.hold_id)}</span></div>` +
        `<div class="f-hold-msg">${why}ほかの送り方でも見積を頼めます。</div>` +
        '<button type="button" class="btn-primary sm" data-act="to-compare">見積依頼文を開く</button>' +
        (r && r.ok ? `<button type="button" class="btn-link" data-act="hold" ${F.busy ? 'disabled' : ''}>もう一度仮予約する</button>` : '') + '</div>';
    }
    if (r && r.ok) {
      return `<div class="f-hold"><button type="button" class="btn-primary sm" data-act="hold" ${F.busy ? 'disabled' : ''}>仮予約する（${holdLabel(F.holdMin)}以内に返事）</button></div>`;
    }
    return '';
  }

  function shipperCard(t, c) {
    const r = F.results[t.id], ok = r && r.ok, scope = inScope(t), selCls = t.id === F.sel ? ' sel' : '';
    const mh = holdOf(t), mine = mh && (mh.state === 'held' || mh.state === 'accepted');  // 自分の仮予約がある便は、残りが減った照合の結果を出さない
    const chip = mine ? `<span class="chip ${mh.state === 'accepted' ? 'ok' : 'check'}">${mh.state === 'accepted' ? '承諾済み' : '仮予約中'}</span>`
      : !r ? '<span class="chip na">確認中</span>' : ok ? '<span class="chip ok">○ 載ります</span>' : `<span class="chip no">${esc(noChip(r.reasons))}</span>`;
    const reasons = r && !ok && !mine ? `<ul class="f-why">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
    return `<article class="f-card${ok ? ' ok' : ''}${r && !ok ? ' ng' : ''}${mh ? ' hold-mine' : ''}${selCls}${scope ? '' : ' off'}" data-t="${t.id}" tabindex="0">` +
      `<div class="f-top"><span class="f-id">${t.id}</span><b class="f-veh">${esc(t.vehicle)}</b>${chip}</div>` +
      `<div class="f-sub">${esc(t.carrier_name)}${scope ? '' : ' ・ <em>別の区間の便</em>'}</div>` +
      `<div class="f-route">${esc(t.route_label)}</div>` + arcNote(t) +
      `<div class="f-when num">${esc(muniName(t.origin_code))} ${hhmm(t.depart_at)} 発 → ${esc(muniName(t.dest_code))} ${hhmm(t.arrive_at)} 着</div>` +
      barHtml(t, c ? c.w : null, false) +
      `<div class="f-price"><b class="num">${n0(boardPrice(t))}</b><span>円</span><small>${esc(t.price_unit || '1口')}・税抜・架空</small>${(t.price_unit || '1口') === '1口' ? tipOnce('1口') : ''}</div>` +
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
      { accepted: '承諾済み', declined: '辞退済み', expired: '期限切れ' }[h.state]}</span></div>`).join('');
    return `<article class="f-card carrier${t.id === F.sel ? ' sel' : ''}${t.carrier_id === F.carrier ? '' : ' off'}" data-t="${t.id}" tabindex="0">` +
      `<div class="f-top"><span class="f-id">${t.id}</span><b class="f-veh">${esc(t.vehicle)}</b>` +
      (F.dir && prefOf(t.origin_code) === F.dir.from && prefOf(t.dest_code) === F.dir.to ? '<span class="chip ok">この向き</span>' : '') +
      (pending.length ? `<span class="chip check">承諾待ち ${pending.length}</span>` : '') + '</div>' +
      `<div class="f-route">${esc(t.route_label)}</div>` + arcNote(t) +
      `<div class="f-when num">${esc(muniName(t.origin_code))} ${hhmm(t.depart_at)} 発 → ${esc(muniName(t.dest_code))} ${hhmm(t.arrive_at)} 着</div>` +
      barHtml(t, null, true) +
      `<div class="f-price"><b class="num">${n0(tripPrice(t))}</b><span>円</span><small>${esc(t.price_unit || '1口')}・税抜・架空</small>` +
      (ed ? '<em class="f-edited">保存していない変更</em>' : '') + '</div>' +
      (ed ? `<div class="f-hold-msg">保存した値は 積める重さ ${kg(t.weight_slack_kg)} kg・${n0(boardPrice(t))} 円です。荷主にはこの値が見えます。</div>` : '') +
      (t.registered ? `<div class="f-post"><button type="button" class="btn-ghost sm" data-act="post-text">掲示板・LINE に貼る文</button>${F.postOpen === t.id ? `<textarea class="ctl f-posttext" readonly rows="9" aria-label="掲示板・LINE に貼る文">${esc(postText(t))}</textarea><div class="f-hbtn"><button type="button" class="btn-primary sm" data-act="post-copy">文をコピーする</button><small class="f-postmsg" role="status"></small></div>` : ''}</div>` : '') +
      (holds ? `<div class="f-holds"><div class="f-hh">荷主からの仮予約</div>${holds}</div>` : '<div class="f-hold-msg faint">承諾待ちの仮予約はありません。</div>') +
      (past ? `<div class="f-hhist">${past}</div>` : '') + (t.id === F.sel ? detailHtml(t) : '') + '</article>';
  }

  function sortKey(t) {
    const r = F.results[t.id];
    return [inScope(t) ? 0 : 1, r && r.ok ? 0 : 1, r && !r.ok ? r.reasons.length : 0, boardPrice(t)];
  }

  /* ------------------------------------------------------------ 0 便の案内（SPEC §30.3） */

  // 合わない理由の文（サーバーが返したもの）を、数えるための短い言い方にまとめる
  const REASON_KINDS = [
    [/1個の重さ/, '1 個の重さが便の上限を超える'], [/箱の高さ/, '箱の高さが便の空きの高さに入らない'], [/箱の底面/, '箱の底面が 1 口の底面に収まらない'],
    [/合計の重さ/, '合計の重さが便の重さの余りを超える'], [/降ろせる地点/, '届け先がこの便の降ろし先にない'], [/届けばよい日/, '便の到着が希望の日に合わない'],
    [/集荷の地点/, '集荷場所がこの便の通り道から遠い'], [/荷の床/, '荷物の置き場所が足りない'], [/出発済み/, '便がすでに出発している'], [/容器/, '容器に入れた荷だけを受ける便'],
    [/RB/, 'ロールボックスの上段に載せる許可がないか、重すぎる'], [/直方体の箱/, '箱でない荷は受けない便'], [/壊れ物/, '壊れ物は受けない便'], [/品目/, 'この便が運ばない品目です'],
  ];
  // カードの見出しのチップ: 最初の理由の種類を出す。理由が複数あるときは「ほか ○件」を足す（SPEC §38 F04）
  const CHIP_KINDS = [
    [/箱の高さ|箱の底面/, '寸法が合いません'], [/1個の重さ/, '1 個の重さが上限を超えます'], [/合計の重さ/, '合計の重さが合いません'], [/荷の床/, '床が足りません'],
    [/届けばよい日/, '日が合いません'], [/集荷の地点/, '集荷の範囲外'], [/出発済み/, '出発済み'], [/降ろせる地点/, '届け先に寄れません'],
    [/^品目/, '受けない品目'], [/容器|RB|直方体の箱|壊れ物/, 'この便では受けない荷物です'],
  ];
  function noChip(reasons) {
    const list = reasons || [];
    const k = list.length ? CHIP_KINDS.find((x) => x[0].test(list[0])) : null;
    return `× ${k ? k[1] : '載りません'}${list.length > 1 ? `（ほか ${list.length - 1} 件）` : ''}`;
  }
  const kindOf = (r) => { const k = REASON_KINDS.find((x) => x[0].test(r)); return k ? k[1] : r; };
  // 名前に「（架空）」が含まれるときは足さない（SPEC §31.5 #13）
  const fict = (name) => (/[（(]架空[）)]/.test(String(name)) ? String(name) : `${name}（架空）`);
  const zeroTrips = () => (F.pair && F.role === 'shipper' && F.loaded ? F.trips.filter(isReverse) : []);

  function zeroHtml() {
    const rs = zeroTrips();
    if (!rs.length) return '';   // 区間に合う便がそもそも無いときは noPairHtml()（合う架空の便はありません）
    if (rs.some((t) => (F.results[t.id] || {}).ok || holdOf(t))) return '';
    if (rs.some((t) => !F.results[t.id])) return ''; // 照合の結果が来るまでは出さない
    const cnt = new Map();
    for (const t of rs) for (const k of new Set((F.results[t.id].reasons || []).map(kindOf))) cnt.set(k, (cnt.get(k) || 0) + 1);
    const top = [...cnt.entries()].sort((a, b) => b[1] - a[1]).slice(0, 2);
    const sc = F.ex.id ? F.scList.find((x) => x.id === F.ex.id) : null;
    startExample();
    const guided = document.body.classList.contains('guided');   // 案内の表示では、選んだ区間に「合う架空の便はありません」と言い、区間を選び直せる
    return `<div class="f-zero" role="status"><b>${guided ? 'この区間に載せられる便はありません（架空のデモ）' : 'この荷物に載せられる便はありません'}</b>` +
      (top.length ? `<p class="f-zero-h">載らない理由（${rs.length} 便中、多い順）</p><ol class="f-zero-why">${top.map((x) => `<li>${esc(x[0])}<span class="num">${x[1]} 便</span></li>`).join('')}</ol>`
        : '<p class="f-zero-h">この向きの便がありません。</p>') +
      '<div class="f-zero-btns">' +
      (sc ? `<button type="button" class="btn-primary sm" data-act="try-ex">載る荷物の例で試す</button><small>${esc(exLabel(F.ex))}</small>` : '') +
      '<button type="button" class="btn-ghost sm" data-act="to-compare-now">ほかの送り方で見積を頼む</button>' +
      (guided ? '<button type="button" class="btn-link" data-act="repick">別の区間を選ぶ</button>' : '') + '</div></div>';
  }

  // 例の荷を探す共通の処理。まず場面の荷そのまま、載らなければ 1 種類の箱の場面を個数だけ減らして（1 口分など）サーバーの照合で探す
  // o: dest(b)・origin(b) は場面の追加の条件 b を受けて返す関数、category・fragile・inContainer は左の欄の値
  async function exampleSearch(rs, o) {
    const plain = F.scList.filter((x) => !x.backhaul);
    const order = [...F.scList.filter((x) => x.backhaul), ...plain.filter((x) => x.id !== 'S1'), ...plain.filter((x) => x.id === 'S1')];   // 最初の荷（S1）は、同じ内容だと版が増えず切り替わらないので最後に試す
    const conts = o.inContainer ? [true] : [false, true];   // 容器に入れた荷だけを受ける便もあるので、容器ありでも試す
    const hit = async (sc, items, c) => {
      const b = sc.backhaul;
      const r = await Work.api('/api/b/match', {
        shipment: { items }, dest_code: o.dest(b), category: o.category || null,
        in_container: c, fragile: !!o.fragile, is_box: b ? b.is_box : true, origin_code: o.origin(b),
        ...(b ? { window_from: b.window_from, window_to: b.window_to } : {}),
      });
      return r.results.some((x) => rs.includes(x.trip_id) && x.ok);
    };
    for (const sc of order) for (const c of conts) if (await hit(sc, sc.shipment.items, c)) return { id: sc.id, cont: c, count: null };
    // どの場面もそのままでは載らない（棚の便は 1 口分の床しかない）。1 個の限度（重さ・高さ・底面）を通る箱の場面を、載る最大の個数に減らす
    for (const sc of order) {
      if (sc.shipment.items.length !== 1 || !(sc.shipment.items[0].count >= 2)) continue;
      const it = sc.shipment.items[0], withN = (n) => [{ ...it, count: n }];
      for (const c of conts) {
        if (!(await hit(sc, withN(1), c))) continue;
        let lo = 1, hi = it.count - 1;
        while (lo < hi) { const mid = Math.ceil((lo + hi) / 2); if (await hit(sc, withN(mid), c)) lo = mid; else hi = mid - 1; }
        return { id: sc.id, cont: c, count: lo };
      }
    }
    return null;
  }
  const exLabel = (ex) => { const sc = F.scList.find((x) => x.id === ex.id); return sc ? (ex.count ? `例 ${sc.id} の荷物を ${ex.count} 個にしたもの` : `例 ${sc.id} ${fict(sc.name)}`) : ''; };

  // この向きの便に 1 便以上合う場面を、サーバーの照合で探す（場面に追加の条件があればそれを使い、無ければいまの受け取り場所と出発地で）
  async function startExample() {
    const rs = zeroTrips().map((t) => t.id), key = `${F.pair.from}>${F.pair.to}|${rs.join(',')}|${F.dest}`;
    if (F.ex.busy) return F.ex.p;   // 探している最中は、その終わりを待てるようにする
    if (F.ex.key === key) return;
    F.ex = { key, id: null, cont: false, count: null, busy: true, p: null };
    const run = async () => {
    let found = null;
    try {
      found = await exampleSearch(rs, {
        dest: (b) => (b ? ((F.pair && F.pairDest) || b.dest_code) : F.dest), origin: (b) => (b ? b.origin_code : pickupOrigin()),
        category: F.category, fragile: F.fragile, inContainer: F.inContainer,
      });
    } catch (e) { found = null; }
    F.ex.id = found ? found.id : null; F.ex.cont = found ? found.cont : false; F.ex.count = found ? found.count : null; F.ex.busy = false;
    if (found && F.active) renderList();
    };
    F.ex.p = run();
    return F.ex.p;
  }

  // 全国の物流傾向で選んだ区間に、載る例の荷があるか（案内の主ボタンを出すかどうか）。true・false・まだ調べていない（null）
  const fitCache = new Map(), fitBusy = new Map();
  const fitState = (pair) => { const v = fitCache.get(`${pair.from}>${pair.to}`); return v === undefined ? null : v; };
  function checkFit(pair) {
    const key = `${pair.from}>${pair.to}`;
    if (fitCache.has(key)) return Promise.resolve(fitCache.get(key));
    if (!fitBusy.has(key)) fitBusy.set(key, fitRun(pair, key));
    return fitBusy.get(key);
  }
  async function fitRun(pair, key) {
    let ok = true;   // 調べられなかったときは、ボタンを隠さない
    try {
      if (!F.loaded) await loadTrips();
      if (!F.scList.length) throw new Error('例の荷物を読み込めませんでした');
      const rs = F.trips.filter((t) => prefOf(t.origin_code) === pair.to && prefOf(t.dest_code) === pair.from);
      if (rs.length) {
        let d = '';
        for (const t of rs) { d = t.drop_points.find((c) => prefOf(c) === pair.from) || ''; if (d) break; }
        if (!d) d = capitalOf(pair.from);
        ok = !!(await exampleSearch(rs.map((t) => t.id), { dest: (b) => (b ? b.dest_code : d), origin: (b) => (b ? b.origin_code : rs[0].origin_code), category: null, fragile: false, inContainer: false }));
      }
    } catch (e) { ok = true; }
    fitCache.set(key, ok);
    fitBusy.delete(key);
    return ok;
  }

  async function tryExample(quiet) {
    const id = F.ex.id, sel = document.querySelector('#sc-select'), ok = document.querySelector('#ed-confirm');
    if (!id || !sel || !ok) return;
    if (F.ex.cont && !F.inContainer) { F.inContainer = true; const cb = q('#f-container'); if (cb) cb.checked = true; }
    sel.value = id;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    const cnt = F.ex.count;
    if (cnt) {   // 個数を減らした例: 場面を読み込んだあとの個数の欄を書き換えてから確定する
      const ci = document.querySelector('#cargo-editor input[data-item="0"][data-k="count"]');
      if (!ci) return;
      ci.value = String(cnt);
      ci.dispatchEvent(new Event('input', { bubbles: true }));
    }
    document.querySelector('#ed-confirm').click(); // 荷物の欄の「この条件で確定」と同じ。版が 1 つ上がり、照合が計算し直される
    for (let i = 0; i < 80 && (Work.scenarioId() !== id || (cnt && (cargoOf() || {}).n !== cnt)); i++) await new Promise((r) => setTimeout(r, 100));
    F.bhSid = null;
    await runMatch();
    const rs = zeroTrips().filter((t) => (F.results[t.id] || {}).ok);
    if (rs.length) { F.sel = rs[0].id; F.ver++; render(); fitTrips([rs[0].id]); }
    if (!quiet) toast(`例の荷物（${exLabel(F.ex)}）を入れました`);
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
    F.tipN = 0;
    if (F.err) html += `<p class="f-err">${esc(F.err)}</p>`;
    if (!F.loaded) { box.innerHTML = html || '<p class="f-empty">便を読み込んでいます</p>'; return; }
    if (F.role === 'shipper') {
      const c = cargoOf();
      let ts = F.trips.slice().sort((a, b) => { const x = sortKey(a), y = sortKey(b); for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] - y[i]; return 0; });
      if (F.pair && F.onlyPair) ts = ts.filter(inScope);
      const okN = ts.filter((t) => F.results[t.id] && F.results[t.id].ok).length;
      q('#f-rsub').textContent = `${ts.length} 便中 ${okN} 便が載ります`;
      q('#f-rtitle').textContent = '今の荷物を載せられる便';
      html = (F.autoNote ? `<p class="f-autonote" role="status">${esc(F.autoNote)}</p>` : '') + zeroHtml() + html;
      html += ts.map((t) => shipperCard(t, c)).join('');
      if (F.pair && F.onlyPair && !ts.length) html += noPairHtml();
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

  // 全国の物流傾向で選んだ区間に、合う架空の便が 1 つも無いとき（行き止まりにしない。区間を選び直せる）
  function noPairHtml() {
    return '<div class="f-zero f-nopair" role="status"><b>この区間に載せられる便はありません（架空のデモ）</b>' +
      `<p class="f-zero-h">${esc(F.pair.to)} → ${esc(F.pair.from)} の向きの架空の便は、このデモに入っていません。</p>` +
      '<div class="f-zero-btns"><button type="button" class="btn-primary sm" data-act="repick">別の区間を選ぶ</button>' +
      '<button type="button" class="btn-ghost sm" data-act="clear-pair">すべての便を見る</button></div></div>';
  }

  function renderPairBar() {
    const el = q('#f-pairbar');
    if (!F.pair || F.role !== 'shipper') { el.hidden = true; const pl0 = q('#f-pairline'); if (pl0) pl0.hidden = true; return; }
    const n = F.trips.filter(isReverse).length;
    el.hidden = false;
    const pl = q('#f-pairline');
    if (pl) { pl.hidden = false; pl.textContent = `選んだ区間: ${F.pair.to}で集荷し、${F.pair.from}で荷を降ろします`; }
    el.innerHTML = `<div><b>選んだ区間 ${esc(F.pair.from)}→${esc(F.pair.to)}</b>` +
      `<span>${esc(F.pair.to)}で集荷し、${esc(F.pair.from)}で荷を降ろします。この向きの帰りの便は ${n} 本です。</span></div>` +
      `<label class="f-chk"><input type="checkbox" id="f-onlypair" ${F.onlyPair ? 'checked' : ''}><span>この向きの便だけ出す</span></label>` +
      '<button type="button" class="btn-link" data-act="clear-pair">すべての区間の便を見る</button>';
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
    if (b) bhEl.textContent = `この例の条件: 集荷は ${muniFull(b.origin_code)}、届け日は ${md(`${b.window_from}T00:00`).split(' ')[0]}〜${md(`${b.window_to}T00:00`).split(' ')[0]}。${b.unboxed_note || ''}`;
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
    const dep = new Date(nowMs()); dep.setDate(dep.getDate() + 1); dep.setHours(17, 0, 0, 0); // 盤の時計の翌日 17:00
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
      `<p class="f-hint" id="fr-basis">${esc(r.basis || '空欄で登録すると、参考の値段が入ります。')}</p>` +
      inp('fr-drops', '降ろせる市区町村（「、」で区切る）', r.drops, 'text', 'autocomplete="off"') +
      '<p class="f-hint">この便はあなたの画面だけに出ます。ほかの人には見えません。</p>' +
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
    if (!r.d) return fail('着地の市区町村を、一覧から選ぶか正式な名前（例: 鹿児島県鹿児島市）で入れてください');
    const names = String(r.drops).split(/[、,，\n]/).map((x) => x.trim()).filter(Boolean), drops = [];
    for (const n of names) { const code = codeOfName(n); if (!code) return fail(`降ろせる地点「${n}」は、市区町村の正式な名前（例: 鹿児島県鹿児島市）で入れてください`); drops.push(code); }
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
      toast(`${t.id} を登録しました。「仮予約と空きを最初の状態に戻す」で消えます`);
    } catch (e) { fail(e.message); toast(e.message); } finally { r.busy = false; const g = q('#fr-go'); if (g) g.disabled = false; }
  }

  function syncEditForm(force) {
    const t = byId(q('#f-trip-sel').value);
    if (!t) return;
    const slack = q('#f-slack-in'), price = q('#f-price-in');
    if (force || slack.dataset.t !== t.id) {
      slack.value = regSlack(t); price.value = tripPrice(t); slack.dataset.t = t.id;
    }
    const lbl = price.closest('label') && price.closest('label').querySelector('.lbl'); // 左の欄の見出しは、選んだ便の値段の単位に合わせる（SPEC §38 F4）
    if (lbl) lbl.textContent = `${t.price_unit || '1口'}の料金（円・税抜）`;
  }

  /* ------------------------------------------------------------ 値段の式（SPEC §21.6。運送会社の役の画面だけ） */

  // 計算はサーバー（/api/b/price_formula → src/shipcompare/spaceb.py）。ここは入力を送って、返った値を並べるだけ
  const FORMULA_NOTE = '国の標準的運賃から出した参考額です。値段は運送会社が決めます。';
  const FORMULA_LINE = '個建運賃の参考額 = 距離制運賃 × max（重さの割合, 床の割合）÷ 基準積載率 ×（1 − リードタイムの割引）';
  const pc1 = (v) => `${(Math.round(v * 1000) / 10).toLocaleString('ja-JP')}%`;

  function renderFormula() {
    const el = q('#f-formula');
    if (!el) return;
    if (F.role !== 'carrier') { el.innerHTML = ''; F.fm.built = false; F.fm.key = ''; return; }
    if (!F.fm.built) {
      el.innerHTML = '<div class="m-h f-sub-h">値段の計算<span class="m-tag">運送会社向け</span></div>' +
        `<p class="fm-line">${FORMULA_LINE}</p>` +
        `<div class="f-grid3"><label><span class="lbl">基準積載率（%）${tip('基準積載率')}</span><input id="fm-rate" class="ctl sm num" type="number" min="10" max="100" step="5"></label>` +
        `<label><span class="lbl">リードタイムの割引${tip('リードタイムの割引')}</span><select id="fm-disc" class="ctl sm">${[0, 10, 20, 30, 40, 50].map((v) => `<option value="${v}">${v}%</option>`).join('')}</select></label>` +
        '<label><span class="lbl">地域の表</span><select id="fm-region" class="ctl sm"><option value="近畿">近畿運輸局</option><option value="関東">関東運輸局</option></select></label>' +
        '<label><span class="lbl">最低保証料（円）</span><input id="fm-min" class="ctl sm num" type="number" min="0" step="1000" placeholder="空欄"></label></div>' +
        '<p class="fm-note">計算した額が最低保証料より低いときは、最低保証料にします。</p>' +
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
    if (!c || !tid) { out.innerHTML = '<p class="fm-miss">荷物を決めると、参考の値段を出します。</p>'; return; }
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
      `<div><span title="標準的運賃の距離制">車1台の運賃（${esc(muniName(d.segment_origin_code))} → ${esc(muniName(d.segment_dest_code))}・${d.km.toLocaleString('ja-JP')} km）</span><b class="num">${n0(d.distance_fare_yen)} 円</b></div>` +
      `<div class="${by === '重さ' ? 'big' : ''}"><span>重さの割合 = ${kg(d.cargo_kg)} kg ÷ ${kg(d.max_load_kg)} kg</span><b class="num">${pc1(d.weight_ratio)}</b></div>` +
      `<div class="${by === '床' ? 'big' : ''}"><span>床の割合 = ${kg(d.cargo_floor_m2)} m² ÷ ${kg(d.floor_total_m2)} m²</span><b class="num">${pc1(d.floor_ratio)}</b></div>` +
      `<div><span>基準積載率</span><b class="num">${pc1(d.base_load_rate)}</b></div>` +
      `<div><span>リードタイムの割引</span><b class="num">${pc1(d.lead_discount)}</b></div>` +
      (d.min_fee_yen != null ? `<div><span>式の額 / 最低保証料</span><b class="num">${n0(d.formula_yen)} 円 / ${n0(d.min_fee_yen)} 円</b></div>` : '') +
      `<div class="sum"><span title="荷の量で分けた運賃">荷物分の参考額（税抜）${d.min_fee_applied ? '（最低保証料）' : ''}</span><b class="num">${n0(d.reference_yen)} 円</b></div>` +
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
    demoClockLine();
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

  // 画面の上に、デモの時刻を常に出す（SPEC §34.6）。サーバーが返した文を使い、無ければ決まった文
  function demoClockLine() {
    const host = q('#find-view') || q('#f-shipper')?.parentElement;
    if (!host) return;
    let p = q('#f-democlock');
    if (!p) { p = document.createElement('p'); p.id = 'f-democlock'; p.className = 'f-democlock'; const h = host.querySelector('.m-h'); if (h) h.insertAdjacentElement('afterend', p); else host.insertBefore(p, host.firstChild); }
    p.textContent = F.demoLabel || 'デモの時刻: 11/10 09:00（架空の便に合わせています）';
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
      const ms = new Date(el.dataset.exp).getTime() - nowMs();
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
        if (n === 0 || set.has(c) !== set.has(items[n - 1])) h += `<li class="grp">${set.has(c) ? '便の降ろせる市区町村' : 'ほかの市区町村（降ろせる便がないことがあります）'}</li>`;
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
      fictional: true, note: '保存していない変更です。架空の会社と便です。',
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
      toast('この画面だけ変えました（保存はしていません）');
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
    q('#f-repick').addEventListener('click', () => { App.repickPair && App.repickPair(); });
    q('#find-right').addEventListener('click', (e) => {
      if (e.target.closest('.f-tip, .f-tipbox')) return; // 用語の説明を開くだけ。便の選択や描き直しはしない
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
        } else if (a === 'post-text') { F.postOpen = F.postOpen === id ? null : id; renderList();
        } else if (a === 'post-copy') {
          const t = byId(id), ta = card.querySelector('.f-posttext'), msg = card.querySelector('.f-postmsg');
          const manual = () => { ta.focus(); ta.select(); if (msg) msg.textContent = '自動でコピーできませんでした。枠の文を選んであります。Ctrl+C でコピーしてください'; };
          try {
            if (!navigator.clipboard || !navigator.clipboard.writeText || !t) throw new Error('no clipboard');
            navigator.clipboard.writeText(ta.value).then(() => { if (msg) msg.textContent = 'コピーしました'; }, manual);
          } catch (err) { manual(); }
        } else if (a === 'try-ex') { tryExample();
        } else if (a === 'to-compare-now') { compareNow();
        } else if (a === 'clear-pair') { F.pair = null; F.pairDest = ''; F.onlyPair = false; F.ver++; render(); App.writeHash && App.writeHash();
        } else if (a === 'repick') { App.repickPair && App.repickPair(); }
        return;
      }
      if (card) select(card.dataset.t, { fly: true });
    });
    document.addEventListener('click', (e) => {
      const b = e.target.closest && e.target.closest('.f-tip');
      if (b && b.closest('#find-view, #find-right')) { toggleTip(b); return; }
      if (!(e.target.closest && e.target.closest('.f-tipbox'))) closeTips(null);
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeTips(null); });
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

  function openContract(id) { F.ctOpener = document.activeElement && document.activeElement !== document.body ? document.activeElement : null; F.ctOpen = id; loadContract(); }

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
      else v = it.by === 'carrier' ? '<em class="fc-miss">未記入（運送会社が選びます）</em>' : '<em class="fc-miss">未登録</em>';
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
    q('#fc-close').addEventListener('click', () => {
      F.ctOpen = null; renderContract();
      const o = F.ctOpener; F.ctOpener = null;   // 閉じたら、開いたボタンにフォーカスを戻す（SPEC §44）
      if (o && document.contains(o) && typeof o.focus === 'function') o.focus();
    });
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
    let wasG = document.body.classList.contains('guided');
    new MutationObserver(() => { const g = document.body.classList.contains('guided'); if (g !== wasG) { wasG = g; if (F.active && F.loaded) syncTags(); } }).observe(document.body, { attributes: true, attributeFilter: ['class'] });   // 案内の表示と全部を見るの切り替えで、便の札の出し分けをやり直す
    map.on('moveend', () => spreadTags());
    await loadTrips();
    if (F.loaded) {
      if (F.trips.length && !F.trips.some((t) => t.drop_points.includes(F.dest))) F.dest = F.trips[0].dest_code;
      await runMatch();
    }
    render();
    F.timer = setInterval(tick, 1000);
  }

  // 見方を開いたとき。pair は全国の物流傾向で選んだ区間（荷が多い向き）。
  async function enter(pair) {
    F.active = true;
    if (pair) { F.pair = { from: pair.from, to: pair.to }; F.onlyPair = true; }
    await loadTrips();
    const rev = F.pair ? F.trips.filter(isReverse) : [];
    if (pair) {
      syncScenario();   // 場面の追加の条件が先に受け取り場所を上書きしないよう、場面との同期を先に済ませる
      // 受け取り場所は、選んだ区間の受け取り側（荷が多い向きの発地の県）の中から入れる。逆向きの便があればその便が降ろせる地点、無ければ県庁所在地
      let d = '';
      for (const t of rev) { d = t.drop_points.find((c) => prefOf(c) === pair.from) || ''; if (d) break; }
      if (!d) d = capitalOf(pair.from);
      if (d) { F.dest = d; F.pairDest = d; }
      if (rev.length) F.sel = rev[0].id;
    }
    if (pair && F.dir && F.role === 'carrier') {
      const ts = dirTrips();
      if (ts.length) { F.carrier = ts[0].carrier_id; F.sel = ts[0].id; }
    } else if (pair) F.dir = null;
    await runMatch();
    if (pair && rev.length && F.role === 'shipper' && !rev.some((t) => (F.results[t.id] || {}).ok)) {   // 選んだ区間の帰り便に載る荷で始める（「合う便はありません」から始めない）
      await startExample();
      if (F.ex.id) {
        await tryExample(true);
        const lb = exLabel(F.ex);
        if (lb) F.autoNote = F.ex.count
          ? `この便は 1 口分の空きなので、例の荷物を載る数に減らしました（${lb}）。自分の荷物は「荷物を変える」で入れます。`
          : `載る例の荷物（${lb}）を入れました。自分の荷物は「荷物を変える」で入れます。`;
      }
    }
    render();
    fitTrips((rev.length ? rev : F.trips).map((t) => t.id));
    if (pair && typeof Sheet !== 'undefined') Sheet.scrollTo(F.role === 'carrier' ? '#find-view' : '#find-right');
  }
  function leave() { F.active = false; F.markers.forEach((m) => { m.el.style.display = 'none'; }); q('#f-contract').hidden = true; }
  function onCargo() { if (F.loaded) runMatch(); }
  function hashParams(p) { p.set('r', F.role); if (F.sel) p.set('t', F.sel); if (F.pair) { p.set('pf', F.pair.from); p.set('pt', F.pair.to); } }
  function readHash(p) { if (p.get('r') === 'carrier') F.role = 'carrier'; if (p.get('t')) F.sel = p.get('t'); }
  // アドレスの pf（荷が多い向きの発地の県）と pt（着地の県）から、全国の物流傾向で選んだ区間を取り出す。県の名前でないものは無視する
  function pairFromHash(p) {
    const f = p.get('pf'), t = p.get('pt'), P = Market.state.d ? Market.state.d.prefs : null;
    if (!f || !t || f === t || (P && (!P.includes(f) || !P.includes(t)))) return null;
    return { from: f, to: t };
  }

  return { init, enter, leave, layers, click, onCargo, hashParams, readHash, pairFromHash, hasReturn, fitState, checkFit, state: F, select, setRole, openCarrier };
})();
