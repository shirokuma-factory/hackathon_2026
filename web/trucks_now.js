/* いまの大型車（web/trucks_now.html）。data/trucks_now.json を読んで、地図・数字・折れ線・表を描く。
   集計は tools/fetch_trucks_now.py が済ませている。ここでは計算し直さず、表示の形に直すだけ。 */
(function () {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const POLL_MS = 5 * 60 * 1000;
  const C_NATION = [31, 41, 51];
  const C_PREF = [180, 84, 10];
  const S = { data: null, prefByName: {}, sel: null, geo: null, map: null, overlay: null, maxAvg: 1, hover: null };

  const fmt = (v, d = 0) => (v == null ? '欠測' : Number(v).toLocaleString('ja-JP', { minimumFractionDigits: d, maximumFractionDigits: d }));
  const dash = (v, d = 0) => (v == null ? '—' : fmt(v, d));
  const ratioText = (r) => (r == null ? '—' : r.toFixed(2) + ' 倍');
  const hourOf = (code) => Math.floor(code / 100) % 100;
  const dateOf = (code) => { const s = String(code); return `${s.slice(0, 4)}年${+s.slice(4, 6)}月${+s.slice(6, 8)}日`; };

  function ramp(v) { // 1 色相の連続スケール。値が無い県は薄い灰色
    if (v == null) return [226, 229, 232, 200];
    const t = Math.max(0, Math.min(1, v / S.maxAvg));
    const a = [227, 238, 246], b = [29, 94, 140];
    return [0, 1, 2].map((i) => Math.round(a[i] + (b[i] - a[i]) * t)).concat(210);
  }

  function radius(p) {
    const t = p.series[p.series.length - 1];
    return t == null ? 1.8 : 1.2 + Math.sqrt(t) * 0.28;
  }

  function showError(msg) { const e = $('#err'); e.textContent = msg; e.style.display = msg ? 'block' : 'none'; }

  /* ---------- 数字・見出し ---------- */
  function renderHead() {
    const d = S.data, n = d.national;
    $('#sub').textContent = `${dateOf(d.latest_hour_code)} ${hourOf(d.latest_hour_code)}時台の観測（およそ20分遅れで更新）　取得 ${d.fetched_at.replace('T', ' ').slice(0, 16)}`;
    $('#n-obs').innerHTML = `${fmt(n.n_observed)} <small>/ ${fmt(n.n_points)}</small>`;
    $('#n-avg').innerHTML = `${dash(n.avg_heavy, 1)} <small>台/時</small>`;
    $('#n-week').textContent = ratioText(n.week_ratio);
    $('#attr1').textContent = d.attribution.processed;
    $('#attr2').textContent = d.attribution.service;
    $('#notes').innerHTML = d.notes.map((t) => `<li>${t.replace(/</g, '&lt;')}</li>`).join('');
  }

  /* ---------- 折れ線（SVG） ---------- */
  function renderChart() {
    const d = S.data, svg = $('#chart');
    const W = Math.max(280, Math.round(svg.clientWidth || 360)), H = 190, L = 34, R = 10, T = 10, B = 24;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const series = [{ name: '全国', v: d.national.series_avg, color: `rgb(${C_NATION})` }];
    const sp = S.sel && S.prefByName[S.sel];
    if (sp) series.push({ name: sp.pref, v: sp.series_avg, color: `rgb(${C_PREF})` });
    const all = series.flatMap((s) => s.v).filter((x) => x != null);
    const step = niceStep(Math.max(1, ...all) / 4);
    const ymax = step * Math.max(1, Math.ceil(Math.max(1, ...all) / step));
    const nt = Math.round(ymax / step);
    const x = (i) => L + (i * (W - L - R)) / 23;
    const y = (v) => T + (1 - v / ymax) * (H - T - B);
    let out = '';
    for (let k = 0; k <= nt; k++) {
      const v = step * k;
      out += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="#e3e7ea"/><text x="${L - 4}" y="${y(v) + 3}" text-anchor="end">${Math.round(v)}</text>`;
    }
    d.hours.forEach((h, i) => {
      if (i % 4 === 3) out += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${hourOf(h)}時</text>`;
    });
    for (const s of series) {
      let path = '', pen = false;
      s.v.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        path += `${pen ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
        pen = true;
      });
      out += `<path d="${path}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round"/>`;
    }
    svg.innerHTML = out;
    $('#chart-legend').innerHTML = series.map((s) => `<span><i style="background:${s.color}"></i>${s.name}</span>`).join('') + '<span>欠測の時間は線を切っています</span>';
  }

  function niceStep(v) { // 目盛りの間隔を 1・2・5 の倍数にする
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 5, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }

  /* ---------- 表 ---------- */
  function renderTable() {
    const tb = $('#rows');
    tb.innerHTML = '';
    for (const p of S.data.prefs) {
      const tr = document.createElement('tr');
      tr.tabIndex = 0;
      tr.dataset.pref = p.pref;
      if (p.pref === S.sel) tr.className = 'sel';
      tr.innerHTML = `<td>${p.pref}</td><td>${dash(p.avg_heavy, 1)}</td><td>${fmt(p.n_observed)} / ${fmt(p.n_points)}</td><td>${p.week_ratio == null ? '—' : p.week_ratio.toFixed(2)}</td>`;
      tr.addEventListener('click', () => select(p.pref));
      tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(p.pref); } });
      tb.appendChild(tr);
    }
  }

  function select(name) {
    S.sel = S.sel === name ? null : name;
    renderChart();
    for (const tr of document.querySelectorAll('#rows tr')) tr.classList.toggle('sel', tr.dataset.pref === S.sel);
    renderLayers();
  }

  /* ---------- 地図 ---------- */
  function renderLegend() {
    $('#legend').innerHTML =
      `<div>県の色: 1地点あたりの大型車（台/時）</div><div class="bar"></div><div class="ends"><span>0</span><span>${Math.round(S.maxAvg)}</span></div>` +
      `<div class="dots"><i style="background:rgb(${C_PREF})"></i>観測点（円の大きさは台数）　<i style="background:#9aa0a6"></i>欠測</div>`;
  }

  function tipHtml(info) {
    const o = info.object;
    if (!o) return '';
    if (info.layer.id === 'points') {
      const t = o.series[o.series.length - 1];
      const share = t != null && o.small != null && t + o.small > 0 ? Math.round((t / (t + o.small)) * 100) + ' %' : '—';
      return `<b>地点 ${o.id}</b>（${o.type}）<br>${o.pref || '県は不明'}<br>大型 上り ${fmt(o.up)} / 下り ${fmt(o.down)} 台/時<br>大型の割合 ${share}<br>先週の同じ時刻 ${dash(o.week_ago)} 台/時（上下の合計）`;
    }
    const name = o.properties.n, p = S.prefByName[name];
    if (!p) return `<b>${name}</b><br>観測点がありません`;
    return `<b>${name}</b><br>1地点あたり 大型 ${dash(p.avg_heavy, 1)} 台/時<br>観測できた地点 ${p.n_observed} / ${p.n_points}<br>先週比 ${ratioText(p.week_ratio)}`;
  }

  function onHover(info) {
    const tip = $('#tip');
    const html = info && info.object ? tipHtml(info) : '';
    if (!html) { tip.style.display = 'none'; return; }
    tip.innerHTML = html;
    tip.style.display = 'block';
    const box = $('.mapwrap').getBoundingClientRect();
    const x = Math.min(info.x + 14, box.width - tip.offsetWidth - 6);
    const y = Math.min(info.y + 14, box.height - tip.offsetHeight - 6);
    tip.style.left = Math.max(4, x) + 'px';
    tip.style.top = Math.max(4, y) + 'px';
  }

  function renderLayers() {
    if (!S.overlay || !S.geo) return;
    const sel = S.sel;
    const layers = [
      new deck.GeoJsonLayer({
        id: 'prefs', data: S.geo, pickable: true, stroked: true, filled: true, lineWidthUnits: 'pixels',
        getFillColor: (f) => { const p = S.prefByName[f.properties.n]; return ramp(p ? p.avg_heavy : null); },
        getLineColor: (f) => (f.properties.n === sel ? [...C_PREF, 255] : [255, 255, 255, 230]),
        getLineWidth: (f) => (f.properties.n === sel ? 2.5 : 0.8),
        updateTriggers: { getFillColor: [S.data.latest_hour_code, S.data.fetched_at], getLineColor: sel, getLineWidth: sel },
        onClick: (i) => { if (i.object) select(i.object.properties.n); },
      }),
      new deck.ScatterplotLayer({
        id: 'points', data: S.data.points, pickable: true, radiusUnits: 'pixels', stroked: true, lineWidthUnits: 'pixels', getLineWidth: 0.6,
        getPosition: (p) => [p.lon, p.lat], getRadius: radius,
        getFillColor: (p) => (p.series[p.series.length - 1] == null ? [154, 160, 166, 200] : [...C_PREF, 150]),
        getLineColor: [255, 255, 255, 220],
        updateTriggers: { getRadius: S.data.fetched_at, getFillColor: S.data.fetched_at },
      }),
    ];
    S.overlay.setProps({ layers });
  }

  function initMap() {
    S.map = new maplibregl.Map({
      container: 'map', style: 'https://tiles.openfreemap.org/styles/positron', bounds: [[128.8, 30.6], [146.2, 45.8]], fitBoundsOptions: { padding: 16 }, minZoom: 3.4, maxZoom: 12,
      dragRotate: false, maxBounds: [[110, 15], [165, 55]],
    });
    S.map.touchZoomRotate.disableRotation();
    S.overlay = new deck.MapboxOverlay({
      interleaved: false, layers: [], onHover,
      getCursor: ({ isHovering, isDragging }) => (isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab'),
    });
    S.map.addControl(S.overlay);
    S.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');
  }

  /* ---------- 読み込み ---------- */
  function apply(data) {
    S.data = data;
    S.prefByName = Object.fromEntries(data.prefs.map((p) => [p.pref, p]));
    const avgs = data.prefs.map((p) => p.avg_heavy).filter((v) => v != null).sort((a, b) => a - b);
    S.maxAvg = avgs.length ? Math.max(1, avgs[Math.floor(avgs.length * 0.95)]) : 1; // 外れ値で全体が薄くならないよう 95 パーセンタイルで頭打ち
    renderHead();
    renderLegend();
    renderChart();
    renderTable();
    renderLayers();
  }

  async function load(first) {
    try {
      const r = await fetch('data/trucks_now.json?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      if (first || !S.data || data.latest_hour_code !== S.data.latest_hour_code || data.fetched_at !== S.data.fetched_at) apply(data);
      showError('');
    } catch (e) {
      showError('データを読み込めませんでした。tools/fetch_trucks_now.py を実行して web/data/trucks_now.json を作ってください。（' + e.message + '）');
    }
  }

  async function main() {
    initMap();
    try {
      S.geo = await (await fetch('data/pref.geojson')).json();
    } catch (e) {
      showError('県境のデータを読み込めませんでした。');
    }
    await load(true);
    setInterval(() => load(false), POLL_MS);
  }

  main();
  window.__trucks = S; // 確認用
})();
