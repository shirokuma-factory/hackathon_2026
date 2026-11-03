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
  };
  const PERIOD = 14;
  const SPACE = {
    free_bed: ['荷台の空き床', '荷台の空いている床に載せる形'],
    inside_rb_upper: ['RB の中の上段', 'RB（ロールボックスパレット）の中の上の段に載せる形'],
    above_rb_rear: ['扉側の列の上の棚', 'RB の列の上に渡した棚に載せる形'],
    shelf_container: ['容器ごとの棚', '容器ごとの棚に載せる形'],
  };
  const CATEGORIES = ['危険物', '生鮮食品', '貴金属', '液体', '動物'];
  const HOLD_CHOICES = [10, 30, 60, 120];
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
  const stamp = (c) => JSON.stringify([c.sh.items, F.dest, F.category, F.inContainer, F.fragile, F.isBox]);
  const body = () => {
    const c = cargoOf();
    return { shipment: { items: c.sh.items }, dest_code: F.dest, category: F.category || null,
      in_container: F.inContainer, fragile: F.fragile, is_box: F.isBox };
  };

  /* ------------------------------------------------------------ サーバーとのやり取り */

  async function loadTrips() {
    try {
      const r = await Work.api('/api/b/trips');
      F.trips = r.trips; F.carriers = r.carriers; F.holds = r.holds; F.note = r.note; F.fiction = r.fiction_notice; F.booking = r.booking_notice;
      F.err = '';
      F.loaded = true;
      if (!F.geo.T) buildGeo();
    } catch (e) { F.err = e.message; }
    F.ver++;
  }

  async function runMatch() {
    const c = cargoOf();
    if (!c || !F.loaded) return;
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
    const groups = {};
    F.trips.forEach((t) => { const k = `${prefOf(t.origin_code)}>${prefOf(t.dest_code)}`; (groups[k] = groups[k] || []).push(t.id); });
    F.trips.forEach((t, ti) => {
      const a = lonlat(t.origin_code), b = lonlat(t.dest_code);
      if (!a || !b) return;
      const g = groups[`${prefOf(t.origin_code)}>${prefOf(t.dest_code)}`], n = g.indexOf(t.id);
      const bulge = (n % 2 === 0 ? 1 : -1) * (0.11 + 0.05 * Math.floor(n / 2));
      const rt = makeRoute(arcPath(a, b, bulge, 72), 'arc', null, null);
      const dur = 4.5 + rt.total / 170;
      const ph = (ti * 2.3) % Math.max(1, PERIOD - dur);
      const items = [];
      for (const sh of [0, -PERIOD]) items.push({ id: t.id, path: rt.path, ts: rt.cum.map((c) => (c / rt.total) * dur + ph + sh) });
      F.geo[t.id] = { a, b, rt, items };
    });
  }

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
    const lv = (d) => level(byId(d.id));
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
      m.el.classList.toggle('dim', lv < 0.5);
      m.el.classList.toggle('on', t.id === F.sel);
      m.el.style.display = F.active ? '' : 'none';
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
  const cdText = (h) => {
    const ms = new Date(h.expires_at).getTime() - Date.now();
    if (!(ms > 0)) return '0:00';
    const s = Math.ceil(ms / 1000);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  };

  function barHtml(t, cargoKg) {
    const reg = Math.max(1, regSlack(t)), rem = shownRemain(t);
    const fill = Math.min(100, (rem / reg) * 100), need = cargoKg === null ? null : Math.min(100, (cargoKg / reg) * 100);
    let txt = `<span>重さの余り <b class="num">${kg(rem)}</b> / ${kg(regSlack(t))} kg</span>`;
    if (cargoKg !== null) txt += `<span class="num">この荷 ${kg(cargoKg)} kg</span>`;
    const shelf = t.remaining_shelf_kg !== null && t.remaining_shelf_kg !== undefined ? `<div class="f-shelf num">棚の耐荷重の残り ${kg(t.remaining_shelf_kg)} kg</div>` : '';
    return `<div class="f-bar"><div class="f-track"><i style="width:${fill.toFixed(1)}%"></i>${need !== null ? `<u style="left:${need.toFixed(1)}%"></u>` : ''}</div>` +
      `<div class="f-bar-txt">${txt}</div>${shelf}</div>`;
  }

  function detailHtml(t) {
    const [sp, spTip] = SPACE[t.space_type] || [t.space_type, ''];
    const row = (k, v) => `<div><span>${k}</span><b>${v}</b></div>`;
    return '<div class="f-detail">' +
      row('空きの形', `<span title="${esc(spTip)}">${esc(sp)}</span>`) + row('空きの高さ', `${n0(t.clear_height_mm)} mm`) +
      row('1口の底面', `${n0(t.unit_footprint_mm[0])}×${n0(t.unit_footprint_mm[1])} mm`) + row('1個の重さの上限', `${kg(t.max_piece_kg)} kg`) +
      row('容器', t.container_required ? '容器入りだけ' : '指定なし') +
      row('降ろせる地点', esc(t.drop_points.map(muniName).join('、'))) +
      row('受けない品目', esc(t.refused_categories.join('、')) || 'なし') + '</div>';
  }

  function holdAreaHtml(t, r) {
    const h = holdOf(t);
    if (h && h.state === 'held') {
      return `<div class="f-hold held"><div class="f-hold-row"><b>仮予約中</b><span class="num">${esc(h.hold_id)}・${kg(h.weight_kg)} kg</span>` +
        `<span class="f-cd num" data-exp="${esc(h.expires_at)}">残り ${cdText(h)}</span></div>` +
        '<div class="f-hold-msg">運送会社の承諾を待っています。期限が切れると重さの余りは戻ります。</div>' +
        '<button type="button" class="btn-link" data-act="to-carrier">運送会社の役の画面で承諾する</button></div>';
    }
    if (h && h.state === 'accepted') {
      return `<div class="f-hold accepted"><div class="f-hold-row"><b>承諾されました</b><span class="num">${esc(h.hold_id)}・${kg(h.weight_kg)} kg</span></div>` +
        `<div class="f-hold-msg">この便の重さの余りから ${kg(h.weight_kg)} kg を確保しました（架空のデモです。支払いも連絡も発生しません）。</div></div>`;
    }
    const past = h ? `<div class="f-hold-msg faint">${h.state === 'declined' ? '前回の仮予約は運送会社が辞退しました。' : '前回の仮予約は期限が切れました。'}</div>` : '';
    if (r && r.ok) {
      return `<div class="f-hold">${past}<button type="button" class="btn-primary sm" data-act="hold" ${F.busy ? 'disabled' : ''}>仮予約する（${F.holdMin} 分）</button></div>`;
    }
    return past ? `<div class="f-hold">${past}</div>` : '';
  }

  function shipperCard(t, c) {
    const r = F.results[t.id], ok = r && r.ok, scope = inScope(t), selCls = t.id === F.sel ? ' sel' : '';
    const chip = !r ? '<span class="chip na">照合中</span>' : ok ? '<span class="chip ok">○ 載ります</span>' : '<span class="chip no">× 載りません</span>';
    const reasons = r && !ok ? `<ul class="f-why">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
    return `<article class="f-card${ok ? ' ok' : ''}${r && !ok ? ' ng' : ''}${selCls}${scope ? '' : ' off'}" data-t="${t.id}" tabindex="0">` +
      `<div class="f-top"><span class="f-id">${t.id}</span><b class="f-veh">${esc(t.vehicle)}</b>${chip}</div>` +
      `<div class="f-sub">${esc(t.carrier_name)}${scope ? '' : ' ・ <em>別の区間の便</em>'}</div>` +
      `<div class="f-route">${esc(t.route_label)}</div>` +
      `<div class="f-when num">${esc(muniName(t.origin_code))} ${hhmm(t.depart_at)} 発 → ${esc(muniName(t.dest_code))} ${hhmm(t.arrive_at)} 着</div>` +
      barHtml(t, c ? c.w : null) +
      `<div class="f-price"><b class="num">${n0(tripPrice(t))}</b><span>円</span><small>1口・税抜・架空</small></div>` +
      reasons + holdAreaHtml(t, r) + (t.id === F.sel ? detailHtml(t) : '') + '</article>';
  }

  function carrierCard(t) {
    const hs = F.holds.filter((h) => h.trip_id === t.id);
    const pending = hs.filter((h) => h.state === 'held');
    const done = hs.filter((h) => h.state !== 'held');
    const ed = F.edits[t.id];
    const holds = pending.map((h) => `<div class="f-hrow"><div><b>${esc(h.hold_id)}</b> <span class="num">${kg(h.weight_kg)} kg</span>` +
      `<span class="f-cd num" data-exp="${esc(h.expires_at)}">残り ${cdText(h)}</span></div>` +
      `<div class="f-hbtn"><button type="button" class="btn-primary sm" data-act="accept" data-h="${esc(h.hold_id)}">承諾</button>` +
      `<button type="button" class="btn-ghost sm" data-act="decline" data-h="${esc(h.hold_id)}">辞退</button></div></div>`).join('');
    const past = done.map((h) => `<div class="f-hpast"><span class="num">${esc(h.hold_id)}・${kg(h.weight_kg)} kg</span><span class="chip ${h.state === 'accepted' ? 'ok' : 'na'}">${
      { accepted: '承諾済み', declined: '辞退済み', expired: '期限切れ' }[h.state]}</span></div>`).join('');
    return `<article class="f-card carrier${t.id === F.sel ? ' sel' : ''}${t.carrier_id === F.carrier ? '' : ' off'}" data-t="${t.id}" tabindex="0">` +
      `<div class="f-top"><span class="f-id">${t.id}</span><b class="f-veh">${esc(t.vehicle)}</b>` +
      (pending.length ? `<span class="chip check">承諾待ち ${pending.length}</span>` : '') + '</div>' +
      `<div class="f-route">${esc(t.route_label)}</div>` +
      `<div class="f-when num">${esc(muniName(t.origin_code))} ${hhmm(t.depart_at)} 発 → ${esc(muniName(t.dest_code))} ${hhmm(t.arrive_at)} 着</div>` +
      barHtml(t, null) +
      `<div class="f-price"><b class="num">${n0(tripPrice(t))}</b><span>円</span><small>1口・税抜・架空</small>` +
      (ed ? '<em class="f-edited">画面の中だけで変更</em>' : '') + '</div>' +
      (holds ? `<div class="f-holds"><div class="f-hh">荷主からの仮予約</div>${holds}</div>` : '<div class="f-hold-msg faint">承諾待ちの仮予約はありません。</div>') +
      (past ? `<div class="f-hhist">${past}</div>` : '') + (t.id === F.sel ? detailHtml(t) : '') + '</article>';
  }

  function sortKey(t) {
    const r = F.results[t.id];
    return [inScope(t) ? 0 : 1, r && r.ok ? 0 : 1, r && !r.ok ? r.reasons.length : 0, tripPrice(t)];
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
    const cs = q('#f-carrier-sel');
    if (cs.options.length !== F.carriers.length) cs.innerHTML = F.carriers.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');
    cs.value = F.carrier;
    const mine = F.trips.filter((t) => t.carrier_id === F.carrier);
    const ts = q('#f-trip-sel'), want = mine.map((t) => t.id).join(',');
    if (ts.dataset.ids !== want) { ts.innerHTML = mine.map((t) => `<option value="${t.id}">${t.id} ${esc(t.route_label.split('（')[0])}</option>`).join(''); ts.dataset.ids = want; }
    if (!mine.some((t) => t.id === ts.value)) ts.value = mine.length ? mine[0].id : '';
    syncEditForm(false);
    q('#f-dl').disabled = !Object.keys(F.edits).length;
    q('#f-edit-note').textContent = Object.keys(F.edits).length ? `画面の中だけで変えた便: ${Object.keys(F.edits).join('、')}` : '';
  }

  function syncEditForm(force) {
    const t = byId(q('#f-trip-sel').value);
    if (!t) return;
    const slack = q('#f-slack-in'), price = q('#f-price-in');
    if (force || slack.dataset.t !== t.id) {
      slack.value = regSlack(t); price.value = tripPrice(t); slack.dataset.t = t.id;
    }
  }

  function render() {
    if (!F.loaded) { renderList(); return; }
    renderLeft();
    renderPairBar();
    renderList();
    syncTags();
    q('#f-fiction').textContent = F.fiction || '架空の運送会社と便です。';
    q('#f-booking').textContent = F.booking || '積むかどうか、どこにどう積むかは運送会社が決めます。受付で量って条件に合わないときは受け付けません。';
  }

  function tick() {
    if (!F.active) return;
    document.querySelectorAll('.f-cd').forEach((el) => {
      const ms = new Date(el.dataset.exp).getTime() - Date.now();
      const s = Math.max(0, Math.ceil(ms / 1000));
      el.textContent = `残り ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
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
    q('#f-holdmin').innerHTML = HOLD_CHOICES.map((m) => `<option value="${m}">${m} 分</option>`).join('');
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
      toast('仮予約と重さの余りを最初の架空データに戻しました');
    }));
    q('#find-right').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-act]'), card = e.target.closest('.f-card');
      if (btn) {
        const a = btn.dataset.act, id = card && card.dataset.t;
        if (a === 'hold') {
          act(async () => {
            const r = await Work.api('/api/b/hold', { ...body(), trip_id: id, hold_minutes: F.holdMin });
            F.mine.add(r.hold.hold_id);
            F.sel = id;
          });
        } else if (a === 'accept' || a === 'decline') {
          act(async () => { await Work.api('/api/b/decide', { hold_id: btn.dataset.h, decision: a }); });
        } else if (a === 'to-carrier') {
          const t = byId(id);
          if (t) F.carrier = t.carrier_id;
          setRole('carrier');
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
  }

  /* ------------------------------------------------------------ 公開 */

  async function init() {
    bind();
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
    await runMatch();
    render();
    fitTrips((rev.length ? rev : F.trips).map((t) => t.id));
  }
  function leave() { F.active = false; F.markers.forEach((m) => { m.el.style.display = 'none'; }); }
  function onCargo() { if (F.loaded) runMatch(); }
  function hashParams(p) { p.set('r', F.role); if (F.sel) p.set('t', F.sel); }
  function readHash(p) { if (p.get('r') === 'carrier') F.role = 'carrier'; if (p.get('t')) F.sel = p.get('t'); }

  return { init, enter, leave, layers, click, onCargo, hashParams, readHash, state: F, select, setRole };
})();
