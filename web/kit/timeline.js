'use strict';
/* 時間の帯と、経路の上を進む乗り物の再生（SPEC §36.1。元にする今のコード: web/routes.js）。表示だけです。
   データ: vehicles[].stops[]（地点・到着・出発）。止まっている間と走っている間は、停車地の到着・出発の時刻から分けて描くだけで、時刻は作りません。
   乗り物の位置は、出発から次の到着までを直線で補間して描く表示用の位置で、実際の走行位置ではありません。
   操作: 再生・止める、速さ（1 秒に何分進めるか）、時刻のつまみ。矢印キーで 1 分ずつ。動きを減らす設定のときは、連続して動かさず 1 秒ごとに進めます。
   アニメーションを使わずに読む代わりの表示として、時間の帯（棒）、いまの位置の文、停車地の表を出します。
   使い方: const t = Kit.timeline.mount(map, data, config); t.update(newData); t.play(); t.pause(); t.setTime(ms); t.destroy(); */
(function (Kit) {
  const { esc, merge, hasLL, finite, uid, rgba } = Kit.util;

  const DEFAULTS = {
    container: null, // 操作と時間の帯を入れる要素。なければ地図の直後に作る
    speeds: [1, 5, 15, 60], // 1 秒に進める分
    speed: 15,
    autoplay: false,
    palette: [[92, 200, 255], [244, 150, 70], [167, 139, 250], [120, 220, 200], [255, 143, 171], [240, 182, 78]],
    fit: true,
    fitPadding: 56,
    names: true,
    legend: { title: '時間の帯', note: '', container: null, show: true },
    table: true,
    theme: 'dark',
    texts: { play: '再生', pause: '止める', speed: '速さ', move: '走行', stop: '停車' },
  };

  const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const pad = (n) => String(n).padStart(2, '0');
  const hm = (ms) => { const d = new Date(ms); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  const ms = (s) => (typeof s === 'string' ? Date.parse(s) : NaN);

  // 乗り物ごとに、停車地の時刻を数にそろえる。座標のない停車地、時刻の読めない停車地があれば、その乗り物は描かない
  function prepare(data) {
    const vs = [], skipped = [];
    ((data && data.vehicles) || []).forEach((v, i) => {
      const stops = ((v && v.stops) || []).map((s) => {
        const a = ms(s.arrive), d = ms(s.depart);
        return { ...s, a: Number.isNaN(a) ? d : a, d: Number.isNaN(d) ? a : d };
      });
      if (v && stops.length >= 2 && stops.every((s) => hasLL(s) && finite(s.a) && finite(s.d)) && stops.every((s, k) => k === 0 || s.a >= stops[k - 1].d)) vs.push({ ...v, stops });
      else skipped.push(v && v.label ? v.label : `${i + 1} 件目`);
    });
    return { vs, skipped };
  }

  // 時刻 t の位置。{ pos, state: 'before'|'move'|'stop'|'after', from, to }
  function at(v, t) {
    const S = v.stops, first = S[0], last = S[S.length - 1];
    if (t < first.a) return { pos: [first.lon, first.lat], state: 'before', at: first };
    if (t > last.d) return { pos: [last.lon, last.lat], state: 'after', at: last };
    for (let i = 0; i < S.length; i++) {
      if (t >= S[i].a && t <= S[i].d) return { pos: [S[i].lon, S[i].lat], state: 'stop', at: S[i] };
      const n = S[i + 1];
      if (n && t > S[i].d && t < n.a) {
        const u = (t - S[i].d) / (n.a - S[i].d);
        return { pos: [S[i].lon + (n.lon - S[i].lon) * u, S[i].lat + (n.lat - S[i].lat) * u], state: 'move', from: S[i], to: n };
      }
    }
    return { pos: [last.lon, last.lat], state: 'after', at: last };
  }

  function mount(map, data, config) {
    Kit.assertMap(map, 'timeline');
    const cfg = merge(DEFAULTS, config);
    Kit.ensureStyle();
    const owner = uid('kit-timeline');
    const box = document.createElement('section');
    box.className = 'kit-tl';
    box.setAttribute('data-kit', 'timeline');
    box.setAttribute('aria-label', '時間の帯と再生');
    if (cfg.container) cfg.container.appendChild(box); else map.getContainer().insertAdjacentElement('afterend', box);

    const st = { vs: [], t0: 0, t1: 1, t: 0, playing: false, speed: cfg.speed, raf: 0, timer: 0, last: 0, markers: [], tags: [] };
    let gone = false;

    box.innerHTML = `<div class="kit-tl-bar">
        <button type="button" class="kit-btn" data-k="play" aria-pressed="false">${esc(cfg.texts.play)}</button>
        <label>${esc(cfg.texts.speed)} <select class="kit-sel" data-k="speed" aria-label="1 秒に進める分">${cfg.speeds.map((s) => `<option value="${s}"${s === cfg.speed ? ' selected' : ''}>1 秒 = ${s} 分</option>`).join('')}</select></label>
        <output class="kit-clock" data-k="clock" aria-label="いまの時刻">--:--</output>
        <input class="kit-rng" data-k="range" type="range" min="0" max="1" step="1" value="0" aria-label="時刻（矢印キーで 1 分ずつ）">
      </div>
      <div class="kit-band" data-k="band"></div>
      <p class="kit-sub" data-k="now"></p>
      <div data-k="dock"></div>`;
    const dock = Kit.dock(map.getContainer(), { container: box.querySelector('[data-k="dock"]'), theme: cfg.theme, table: cfg.table, tableLabel: '表で見る（停車地と時刻）' });
    const $k = (k) => box.querySelector(`[data-k="${k}"]`);
    const btn = $k('play'), sel = $k('speed'), clock = $k('clock'), rng = $k('range'), band = $k('band'), now = $k('now');

    function clearMarkers() { st.markers.forEach((m) => m.mk.remove()); st.markers = []; st.tags.forEach((m) => m.remove()); st.tags = []; }

    function colorOf(i) { return cfg.palette[i % cfg.palette.length]; }

    function drawBand() {
      const span = Math.max(1, st.t1 - st.t0);
      const pct = (x) => ((x - st.t0) / span) * 100;
      const rows = st.vs.map((v, i) => {
        const c = colorOf(i), segs = [];
        v.stops.forEach((s, k) => {
          if (s.d > s.a) segs.push(`<span class="kit-seg stop" style="left:${pct(s.a)}%;width:${pct(s.d) - pct(s.a)}%" title="${esc(s.name)} で停車 ${hm(s.a)}〜${hm(s.d)}"></span>`);
          const n = v.stops[k + 1];
          if (n) segs.push(`<span class="kit-seg" style="left:${pct(s.d)}%;width:${pct(n.a) - pct(s.d)}%;background:${rgba(c, 0.9)}" title="${esc(s.name)} → ${esc(n.name)} ${hm(s.d)}〜${hm(n.a)}"></span>`);
        });
        return `<div class="kit-band-row"><span class="kit-band-lab" title="${esc(v.label || v.id)}">${esc(v.label || v.id)}</span><span class="kit-band-track">${segs.join('')}<span class="kit-cursor" data-k="cur"></span></span></div>`;
      }).join('');
      const ticks = [0, 0.25, 0.5, 0.75, 1].map((u) => `<span>${hm(st.t0 + span * u)}</span>`).join('');
      band.innerHTML = rows + `<div class="kit-axis"><span></span><span>${ticks}</span></div>`;
    }

    function nowText() {
      return st.vs.map((v) => {
        const a = at(v, st.t);
        const w = a.state === 'move' ? `${a.from.name} から ${a.to.name} へ移動中` : a.state === 'before' ? `${a.at.name} を出発する前` : a.state === 'after' ? `${a.at.name} に到着済み` : `${a.at.name} で停車中`;
        return `${v.label || v.id}: ${w}`;
      }).join(' ／ ');
    }

    function paint() {
      const span = Math.max(1, st.t1 - st.t0);
      clock.textContent = hm(st.t);
      rng.value = String(Math.round((st.t - st.t0) / 60000));
      rng.setAttribute('aria-valuetext', hm(st.t));
      box.querySelectorAll('[data-k="cur"]').forEach((c) => { c.style.left = `${((st.t - st.t0) / span) * 100}%`; });
      st.markers.forEach((m, i) => {
        const a = at(st.vs[i], st.t);
        m.mk.setLngLat(a.pos);
        m.el.style.opacity = a.state === 'before' || a.state === 'after' ? '0.55' : '1';
      });
      now.textContent = `${hm(st.t)} の位置: ${nowText()}。位置は出発と到着の時刻から直線で補った表示用です。`;
    }

    function layers() {
      const L = deck;
      const routes = st.vs.map((v, i) => ({ path: v.stops.map((s) => [s.lon, s.lat]), col: [...colorOf(i), 120], tip: `${v.label || v.id}\n${v.stops.map((s) => s.name).join(' → ')}` }));
      const stops = st.vs.flatMap((v, i) => v.stops.map((s) => ({ p: [s.lon, s.lat], col: colorOf(i), tip: `${s.name}\n到着 ${hm(s.a)}・出発 ${hm(s.d)}` })));
      return [
        new L.PathLayer({ id: `${owner}-route`, data: routes, getPath: (x) => x.path, getColor: (x) => x.col, getWidth: 4, widthUnits: 'pixels', capRounded: true, jointRounded: true, pickable: true }),
        new L.ScatterplotLayer({ id: `${owner}-stops`, data: stops, getPosition: (x) => x.p, getRadius: 5, radiusUnits: 'pixels', getFillColor: [255, 255, 255, 255], getLineColor: (x) => [...x.col, 255], stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2.5, pickable: true }),
      ];
    }

    function setup(d, first) {
      pauseNow();
      const { vs, skipped } = prepare(d);
      st.vs = vs;
      const all = vs.flatMap((v) => v.stops.flatMap((s) => [s.a, s.d]));
      const r = d && d.range;
      st.t0 = r && finite(ms(r.start)) ? ms(r.start) : all.length ? Math.min(...all) : 0;
      st.t1 = r && finite(ms(r.end)) ? ms(r.end) : all.length ? Math.max(...all) : st.t0 + 60000;
      if (st.t1 <= st.t0) st.t1 = st.t0 + 60000;
      st.t = first ? st.t0 : Math.min(Math.max(st.t, st.t0), st.t1);
      rng.max = String(Math.max(1, Math.round((st.t1 - st.t0) / 60000)));
      clearMarkers();
      st.markers = vs.map((v, i) => {
        const el = document.createElement('div');
        el.setAttribute('data-kit', 'vehicle');
        el.className = 'kit-tag';
        el.style.cssText = `border-color:${rgba(colorOf(i), 1)};border-width:2px;border-radius:12px;pointer-events:none`;
        el.textContent = v.label || v.id;
        return { el, mk: new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat([v.stops[0].lon, v.stops[0].lat]).addTo(map) };
      });
      if (cfg.names) {
        const seen = new Map();
        vs.forEach((v) => v.stops.forEach((s) => seen.set(`${s.lon},${s.lat}`, s)));
        st.tags = [...seen.values()].map((s) => Kit.tag(map, s, esc(s.name), { offset: [0, -10] }));
      }
      Kit.deckLayers.set(map, owner, layers());
      drawBand();
      dock.set({
        legend: cfg.legend.show ? {
          title: cfg.legend.title,
          items: [...vs.map((v, i) => ({ label: `${v.label || v.id}の${cfg.texts.move}`, color: colorOf(i), kind: 'line' })), { label: `斜線の部分 = ${cfg.texts.stop}（到着から出発まで）`, color: [135, 149, 168], kind: 'line' }],
          note: `棒の白い縦線が、いまの時刻です。${Kit.basisNote}${cfg.legend.note ? ' ' + cfg.legend.note : ''}`,
        } : null,
        warns: skipped.length ? [`停車地の座標か時刻が足りない乗り物（${skipped.join('、')}）は描いていません。`] : [],
        table: {
          cols: [{ label: '乗り物' }, { label: '順' , n: true }, { label: '停車地' }, { label: '到着', n: true }, { label: '出発', n: true }, { label: '区別' }],
          rows: vs.flatMap((v) => v.stops.map((s, k) => [v.label || v.id, k + 1, s.name, hm(s.a), hm(s.d), v.basis || ''])),
        },
        sources: [...new Set(vs.map((v) => v.source).filter(Boolean))],
      });
      paint();
      if (first && cfg.fit) Kit.fit(map, vs.flatMap((v) => v.stops.map((s) => [s.lon, s.lat])), { padding: cfg.fitPadding });
    }

    /* ---- 再生 */
    function pauseNow() {
      st.playing = false;
      cancelAnimationFrame(st.raf); clearTimeout(st.timer);
      btn.textContent = cfg.texts.play; btn.setAttribute('aria-pressed', 'false');
    }
    function step(dtMs) {
      st.t += dtMs * st.speed / 1000 * 60; // 1 秒に speed 分
      if (st.t >= st.t1) { st.t = st.t1; paint(); pauseNow(); return false; }
      paint();
      return true;
    }
    function play() {
      if (gone || !st.vs.length) return;
      if (st.t >= st.t1) st.t = st.t0;
      st.playing = true;
      btn.textContent = cfg.texts.pause; btn.setAttribute('aria-pressed', 'true');
      if (reduced()) { // 連続して動かさず、1 秒ごとに進める
        const tick = () => { if (st.playing && step(1000)) st.timer = setTimeout(tick, 1000); };
        st.timer = setTimeout(tick, 1000);
        return;
      }
      st.last = performance.now();
      const loop = (n) => { if (!st.playing) return; const dt = Math.min(100, n - st.last); st.last = n; if (step(dt)) st.raf = requestAnimationFrame(loop); };
      st.raf = requestAnimationFrame(loop);
    }

    btn.addEventListener('click', () => (st.playing ? pauseNow() : play()));
    sel.addEventListener('change', () => { st.speed = Number(sel.value) || cfg.speed; });
    rng.addEventListener('input', () => { st.t = st.t0 + Number(rng.value) * 60000; paint(); });

    setup(data, true);
    if (cfg.autoplay && !reduced()) play();
    return {
      update(d) { if (!gone) setup(d, false); },
      play, pause: pauseNow,
      setTime(t) { st.t = Math.min(Math.max(t, st.t0), st.t1); paint(); },
      get time() { return st.t; },
      get playing() { return st.playing; },
      destroy() {
        if (gone) return;
        gone = true; pauseNow(); clearMarkers(); Kit.deckLayers.remove(map, owner); dock.destroy(); box.remove();
      },
    };
  }

  Kit.timeline = { mount };
})(window.Kit);
