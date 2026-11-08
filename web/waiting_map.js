'use strict';
/* 地図の画面の荷待ちのタブ（web/own.js）に足す部分（SPEC §30.4）。
   計算と文言は web/waiting_calc.js（単独ページ waiting.html と同じ関数）を使い、数字は WaitingShared.calc（ブラウザの中で計算。サーバーには送らない、SPEC §31.1）の返り値を並べるだけ。
   ここで足すのは、どこで・いつ待っているかの表、場所ごと・月ごとの表、書面の「荷待ちを減らすためのご相談」、地図の上の点（場所の文字の先頭の市区町村名で位置を決める）。 */

const WaitingMap = (() => {
  const SH = WaitingShared;
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const n0 = (v) => Math.round(v).toLocaleString('ja-JP');
  const hm = (m) => (m ? `${Math.floor(m / 60)} 時間 ${m % 60} 分` : '0 分');

  // 点の色は平均の荷待ちの 4 段（30 分未満・30〜59・60〜119・120 以上）。色だけに頼らず、凡例と点の中の数字でも読めるようにする
  // 色は web/kit/kit.js の Kit.palette.bands と共有する。区分の名前と下限は同じ順で持ち、試験（test_v39_ui_contrast_kit.py）が Kit と同じことを確かめる
  const BANDS = [
    { label: '30 分未満', min: 0 },
    { label: '30〜59 分', min: 30 },
    { label: '60〜119 分', min: 60 },
    { label: '120 分以上', min: 120 },
  ].map((b, i) => ({ ...b, rgb: Kit.palette.bands[i].color }));
  const bandOf = (avg) => { let b = 0; BANDS.forEach((x, i) => { if (avg >= x.min) b = i; }); return b; };
  const rgbCss = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

  const M = { pts: [], unplaced: [], markers: [], sel: '', fitKey: '', geo: null, shown: false };

  /* ------------------------------------------------------------ 場所の文字 → 市区町村の代表点 */

  // 市区町村の名前ごとの番号の表。S.idx.names / S.idx.prefs（app.js）と Muni.state.d.pt（muni.js の代表点）を読むだけ
  function geoIndex() {
    if (M.geo) return M.geo;
    if (typeof S === 'undefined' || !S.idx || typeof Muni === 'undefined' || !Muni.state || !Muni.state.d) return null;
    const byName = new Map(), names = S.idx.names, pt = Muni.state.d.pt;
    names.forEach((n, i) => { if (!byName.has(n)) byName.set(n, []); byName.get(n).push(i); });
    M.geo = { byName, names, prefs: S.idx.prefs, pt };
    return M.geo;
  }

  // 返り値: { lon, lat, name } か { reason }。同じ名前が複数の県にあるとき、政令市の区が複数の県にまたがるときは置かない
  function locate(place) {
    const g = geoIndex();
    if (!g) return { reason: '地図の市区町村の表がまだ読み込まれていません' };
    const text = String(place || '').normalize('NFKC').trim();
    if (!text || text === SH.NO_PLACE) return { reason: '場所の文字が空です' };
    for (let len = Math.min(text.length, 8); len >= 2; len--) {
      const ids = g.byName.get(text.slice(0, len));
      if (!ids) continue;
      if (ids.length > 1) return { reason: `「${text.slice(0, len)}」は同じ名前の市区町村が複数あります` };
      const p = g.pt[ids[0]];
      if (p) return { lon: p[0], lat: p[1], name: `${g.prefs[ids[0]]} ${g.names[ids[0]]}` };
    }
    // 政令市（「川崎市 地点12」の「川崎市」）は、その市の区の代表点の平均に置く
    const head = text.split(/\s+/)[0];
    if (head.length >= 3 && head.endsWith('市')) {
      const ids = [];
      g.names.forEach((n, i) => { if (n.startsWith(head) && g.pt[i]) ids.push(i); });
      if (ids.length) {
        if (new Set(ids.map((i) => g.prefs[i])).size > 1) return { reason: `「${head}」は複数の県にまたがります` };
        const lon = ids.reduce((a, i) => a + g.pt[i][0], 0) / ids.length, lat = ids.reduce((a, i) => a + g.pt[i][1], 0) / ids.length;
        return { lon, lat, name: `${g.prefs[ids[0]]} ${head}` };
      }
    }
    return { reason: `先頭の「${head}」に当たる市区町村が地図にありません` };
  }

  /* ------------------------------------------------------------ 場所ごとの点 */

  // 場所ごとに、荷待ちの計（分）と平均を作る。数え方は場所ごと・月ごとの表と同じ（定期報告の定義: report_wait_min。SPEC §31.5 #6）。
  // 荷主等の都合でない到着も回数に入れる。待機時間料の計算に使う wait_min とは数え方が違う
  function placesOf(res) {
    const by = new Map();
    for (const r of (res ? res.rows : [])) {
      const k = r.place || SH.NO_PLACE;
      if (!by.has(k)) by.set(k, { place: k, count: 0, total: 0 });
      const e = by.get(k);
      e.count += 1; e.total += r.report_wait_min;
    }
    return [...by.values()].map((e) => ({ ...e, avg: SH.halfUp(e.total, e.count) })).sort((a, b) => b.total - a.total || SH.cmp(a.place, b.place));
  }

  // 同じ市区町村に置く点が複数あるときは、少しずつずらして重ならないようにする
  function jitter(list) {
    const groups = new Map();
    list.forEach((p) => { const k = `${p.lon.toFixed(4)},${p.lat.toFixed(4)}`; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(p); });
    for (const g of groups.values()) {
      if (g.length < 2) continue;
      g.sort((a, b) => SH.cmp(a.place, b.place));
      g.forEach((p, i) => {
        const a = (2 * Math.PI * i) / g.length - Math.PI / 2, r = 0.028;
        p.lon += (r * Math.cos(a)) / Math.cos((p.lat * Math.PI) / 180); p.lat += r * Math.sin(a);
      });
    }
  }

  function compute(res) {
    M.pts = []; M.unplaced = [];
    for (const e of placesOf(res)) {
      const loc = locate(e.place);
      if (loc.reason) M.unplaced.push({ ...e, reason: loc.reason });
      else M.pts.push({ ...e, lon: loc.lon, lat: loc.lat, where: loc.name, band: bandOf(e.avg) });
    }
    jitter(M.pts);
    if (M.sel && !M.pts.some((p) => p.place === M.sel)) M.sel = '';
  }

  /* ------------------------------------------------------------ 地図の上の点（HTML の要素。maplibregl.Marker） */

  const sizeOf = (total) => Math.round(Math.min(58, 18 + Math.sqrt(total) * 1.5));

  function pointEl(p) {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'wm-pt';
    el.dataset.place = p.place;
    const sz = sizeOf(p.total), c = BANDS[p.band].rgb;
    el.style.width = el.style.height = `${sz}px`;
    el.style.background = rgbCss(c, 0.82);
    el.style.borderColor = rgbCss(c, 1);
    el.setAttribute('aria-label', `${p.place}。荷待ちの計 ${p.total} 分、平均 ${p.avg} 分（国への定期報告と同じ数え方）。押すと時間帯ごとの行を出します`);
    el.innerHTML = `<span class="wm-n">${n0(p.avg)}</span><span class="wm-t">${esc(p.place)}</span>`;
    el.addEventListener('click', (e) => { e.stopPropagation(); select(p.place); });
    return el;
  }

  function clearMarkers() { M.markers.forEach((m) => m.mk.remove()); M.markers = []; }

  // 結果が変わったら点を作り直す。出すか隠すかは show() で決める（別のタブや別の見方では隠す）
  function paint(res) {
    compute(res);
    clearMarkers();
    if (typeof maplibregl === 'undefined' || typeof map === 'undefined') return;
    for (const p of M.pts) {
      const el = pointEl(p);
      M.markers.push({ el, p, mk: new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat([p.lon, p.lat]).addTo(map) });
    }
    mark();
  }

  function mark() { M.markers.forEach((m) => m.el.classList.toggle('on', m.p.place === M.sel)); }
  function show(on) { M.markers.forEach((m) => { m.el.style.display = on ? '' : 'none'; }); }

  let onSelect = null;
  function select(place) { M.sel = M.sel === place ? '' : place; mark(); if (onSelect) onSelect(); }
  function onChange(fn) { onSelect = fn; }

  // 点が収まる範囲。点が無いときは null。同じ範囲のあいだは動かさない（入力のたびに地図が動かないように）
  function bounds() {
    if (!M.pts.length) return null;
    const lons = M.pts.map((p) => p.lon), lats = M.pts.map((p) => p.lat);
    return [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]];
  }
  function fitKeyChanged(key) { if (key === M.fitKey) return false; M.fitKey = key; return true; }
  // 点の外側の半径の最大（画面の端や出典の文字に重ならない余白を、own.js が決めるのに使う）
  const maxRadius = () => (M.pts.length ? Math.max(...M.pts.map((p) => sizeOf(p.total))) / 2 + 6 : 0);
  const key = () => M.pts.map((p) => p.place).join('|');

  /* ------------------------------------------------------------ 右の欄に足す表 */

  function legendHtml() {
    return `<div class="wm-lg" aria-label="地図の点の凡例"><b>地図の点</b>
      <span class="wm-lg-s">大きさ = 荷待ちの計（分）。点の中の数字 = 平均の荷待ち（分）。平均は国への定期報告と同じ数え方です（場所ごと・月ごとの表と同じ。待機時間料の計算とは違います）</span>
      ${BANDS.map((b) => `<span class="wm-lg-i"><i style="background:${rgbCss(b.rgb, 0.85)}"></i>平均 ${b.label}</span>`).join('')}</div>`;
  }

  function placeDetailHtml(res) {
    if (!M.sel) return '<p class="w-small wm-hint">地図の点か、下の場所の名前を押すと、その場所の時間帯ごとの行を出します。</p>';
    const hs = res.hotspots.filter((h) => (h.place) === M.sel);
    const e = M.pts.find((p) => p.place === M.sel) || M.unplaced.find((p) => p.place === M.sel);
    const rows = hs.map((h) => `<tr><td data-l="相手">${esc(h.partner)}</td><td data-l="到着の時間帯">${esc(h.band)}</td><td class="num" data-l="回数">${h.count}</td><td class="num" data-l="平均の荷待ち">${h.avg_minutes} 分</td>
      <td class="num" data-l="最長">${h.max_minutes} 分</td><td class="num" data-l="1 時間以上">${h.over_1h_count}</td><td class="num" data-l="荷待ちの計">${h.wait_minutes} 分</td><td class="w-small wm-c3">${SH.hotspotNoteCell(h, esc)}</td></tr>`).join('');
    return `<div class="wm-detail" id="wm-detail"><b>${esc(M.sel)}</b><span class="w-small">　荷待ちの計 ${e ? hm(e.total) : '—'}（定期報告の数え方）、${e ? e.count : 0} 回</span>
      <div class="w-recw"><table class="w-par wm-card" id="wm-detail-tab"><thead><tr><th>契約の相手</th><th>到着の時間帯</th><th>回数</th><th>平均の荷待ち</th><th>最長</th><th>1 時間以上の回数</th><th>荷待ちの計</th><th>注記</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
  }

  function mapSectionHtml(res) {
    if (!res || !res.rows.length) return '';
    const dot = (p) => `<button type="button" class="wm-chip" data-wm="pick" data-place="${esc(p.place)}"><i style="background:${rgbCss(BANDS[p.band].rgb, 0.9)}"></i>${esc(p.place)}<small>計 ${n0(p.total)} 分・平均 ${p.avg} 分</small></button>`;
    const un = M.unplaced.length
      ? `<div class="wm-un" id="wm-unplaced"><b>地図に置けない場所</b><span class="w-small">　場所の文字の先頭が市区町村名に当たらない場所です。点にはしていません。表では見られます</span>
        <ul>${M.unplaced.map((p) => `<li><button type="button" class="wm-chip" data-wm="pick" data-place="${esc(p.place)}">${esc(p.place)}<small>計 ${n0(p.total)} 分・平均 ${p.avg} 分</small></button><small class="wm-why">${esc(p.reason)}</small></li>`).join('')}</ul></div>` : '';
    return `<div class="w-sect" id="wm-map-sect"><div class="m-rh"><b>荷待ちの多い場所（地図）</b><span class="m-rsub">場所の文字の先頭の市区町村名で位置を決めています</span></div>
      ${legendHtml()}
      <div class="wm-chips">${M.pts.map(dot).join('') || '<span class="w-small">地図に置ける場所はありません。</span>'}</div>
      ${placeDetailHtml(res)}${un}</div>`;
  }

  function hotspotsHtml(res) {
    const total = res.rows.reduce((a, x) => a + x.wait_min, 0);
    const top = res.hotspots.filter((h) => h.wait_minutes > 0).slice(0, 5);
    const tb = top.map((h) => `<tr><td class="wm-c1">${esc(h.partner)}</td><td class="wm-c1">${esc(h.place)}</td><td class="wm-c1">${esc(h.band)}</td><td class="num" data-l="回数">${h.count}</td><td class="num" data-l="平均の荷待ち">${h.avg_minutes} 分</td>
      <td class="num" data-l="最長">${h.max_minutes} 分</td><td class="num" data-l="1 時間以上">${h.over_1h_count}</td><td class="num" data-l="参考額（円）">${n0(h.amount)}</td><td class="w-small wm-c3">${SH.hotspotNoteCell(h, esc)}</td></tr>`).join('');
    const pm = res.by_place_month.map((m) => `<tr><td class="wm-c1">${esc(m.place)}</td><td class="wm-c1">${esc(SH.monthLabel(m.month))}</td><td class="num" data-l="回数">${m.count}</td><td class="num" data-l="平均（分）">${m.avg_minutes}</td><td class="num" data-l="1 時間以上">${m.over_1h_count}</td></tr>`).join('');
    return `<div class="w-sect" id="wm-hot"><div class="m-rh"><b>どこで・いつ待っているか</b><span class="m-rsub">荷待ちの合計が大きい上位 5 組。到着の時間帯は実際の到着で分けています</span></div>
      <p class="w-small wm-total">この期間の荷主等の都合の荷待ちは計 ${hm(total)}です。</p>
      ${SH.truncWarn(res.rows) ? `<p class="w-errs" id="trunc-warn"><b>${esc(SH.TRUNC_WARN)}</b></p>` : ''}
      <p class="w-small" id="trunc-line">${esc(SH.TRUNC_LINE)}</p>
      ${top.length ? `<div class="w-recw"><table class="w-par wm-card" id="hotspots"><thead><tr><th>契約の相手</th><th>場所</th><th>到着の時間帯</th><th>回数</th><th>平均の荷待ち</th><th>最長</th><th>1 時間以上の回数</th><th>参考額（円）</th><th>注記</th></tr></thead><tbody>${tb}</tbody></table></div>` : '<p class="w-empty">荷待ちのある組はありません。</p>'}
      <div class="m-rh wm-h3"><b>場所ごと・月ごとの平均の荷待ち（分）</b></div>
      <p class="w-small">${esc(SH.PLACE_MONTH_LEAD)}</p>
      <p class="w-small" id="pm-def">${esc(SH.PM_DEF)}</p>
      ${pm ? `<div class="w-recw"><table class="w-par wm-card" id="placemonth"><thead><tr><th>場所</th><th>月</th><th>回数</th><th>平均の荷待ち（分）</th><th>1 時間以上の回数</th></tr></thead><tbody>${pm}</tbody></table></div>` : '<p class="w-empty">記録を入れると出ます。</p>'}</div>`;
  }

  // 相手の書面の「荷待ちを減らすためのご相談」（SPEC §27.3、§28.2）。その相手の上位 3 組と、その相手の場所ごと・月ごとの表
  function consultHtml(res, partner) {
    const lines = SH.consultLines(res.hotspots, partner);
    if (!lines.length) return '';
    const months = SH.byPlaceMonth(res.rows.filter((r) => r.partner === partner));
    return `<h4>${esc(SH.CONSULT_TITLE)}</h4>
      <ul class="w-list">${lines.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
      <p>${esc(SH.CONSULT_CLOSE)}</p>
      <h5 id="doc-pm-title" style="margin:8px 0 2px;font-size:12.5px">${esc(SH.PM_PARTNER_TITLE)}</h5>
      <p class="w-small">${esc(SH.PM_DEF)}</p>
      <div class="w-recw"><table class="w-rec" id="doc-placemonth"><thead><tr><th>場所</th><th>月</th><th>回数</th><th>平均の荷待ち（分）</th><th>1 時間以上の回数</th></tr></thead><tbody>${months.map((m) => `<tr><td>${esc(m.place)}</td><td>${esc(SH.monthLabel(m.month))}</td><td class="num">${m.count}</td><td class="num">${m.avg_minutes}</td><td class="num">${m.over_1h_count}</td></tr>`).join('')}</tbody></table></div>
      <p>${esc(SH.MONTH_CLOSE)}</p>`;
  }

  // 根拠の欄に足す事実（SPEC §27.2）。基本の項目の後ろに、認識している荷主の割合、Gメンの是正の例と相手ごとの割合、拘束時間の順に並べる
  function basisItems(base, res) {
    return [...base, SH.BASIS_FACTS[0], SH.gmenBasis(res ? res.by_partner : []), SH.BASIS_FACTS[1]];
  }

  return { BANDS, bandOf, locate, compute, paint, show, select, onChange, bounds, fitKeyChanged, key, maxRadius, mapSectionHtml, hotspotsHtml, consultHtml, basisItems, state: M, sizeOf };
})();
