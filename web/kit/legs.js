'use strict';
/* 運行の区間の線（SPEC §36.1。元にする今のコード: web/own.js）。表示だけで、判定や距離は計算しない。
   色 = 判定（実車・空車・不明）。太さ = 同じ区間（同じ発地・着地・判定）の便数。空車は破線、不明は細く薄い線。
   同じ発地・着地の線は、進む向きの右側に判定ごとに少しずらして並べる（行きと帰りが重ならない）。
   使い方: const l = Kit.legs.mount(map, data, config); l.update(newData); l.destroy(); */
(function (Kit) {
  const { esc, merge, hasLL, finite, uid } = Kit.util;

  const DEFAULTS = {
    colors: Kit.palette.legs.colors,
    labels: Kit.palette.legs.labels,
    width: { base: 1.8, perTrip: 0.16, max: 9 },
    names: true,
    fit: true,
    fitPadding: 56,
    laneGap: 0.022, // 度。向きの右側へずらす量
    legend: { title: '運行の区間', note: '', container: null, position: 'bottom-left', show: true },
    table: true,
    theme: 'dark',
    texts: { width: '線の太さ = 同じ区間の便数' },
  };
  const LANE = { loaded: 0, empty: 1, unknown: 2 };
  const STATUS = ['loaded', 'empty', 'unknown'];

  // 終了が開始より前の区間は、形（JSON Schema）では見分けられないので、ここで描かずに数える（SPEC §42）
  const reversedLeg = (l) => { const a = Date.parse(l.start), b = Date.parse(l.end); return Number.isFinite(a) && Number.isFinite(b) && b < a; };
  function clean(data) {
    const ok = [], skipped = [], reversed = [];
    ((data && data.legs) || []).forEach((l, i) => {
      if (!(l && hasLL(l.from) && hasLL(l.to) && STATUS.includes(l.status))) skipped.push(i + 1);
      else if (reversedLeg(l)) reversed.push(i + 1);
      else ok.push(l);
    });
    return { ok, skipped, reversed };
  }

  // 進む向きの右側へ d 度ずらした線。経度の 1 度は緯度の 1 度より短いので、緯度で補正する
  function shifted(path, d) {
    const out = [];
    for (let i = 0; i < path.length; i++) {
      const a = path[Math.max(0, i - 1)], b = path[Math.min(path.length - 1, i + 1)];
      const k = Math.cos((path[i][1] * Math.PI) / 180);
      let dx = (b[0] - a[0]) * k, dy = b[1] - a[1];
      const n = Math.hypot(dx, dy) || 1;
      dx /= n; dy /= n;
      out.push([path[i][0] + (dy * d) / k, path[i][1] - dx * d]);
    }
    return out;
  }
  function pointAt(path, t) {
    const seg = []; let tot = 0;
    for (let i = 1; i < path.length; i++) { const l = Math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1]); seg.push(l); tot += l; }
    let want = tot * t;
    for (let i = 0; i < seg.length; i++) {
      if (want <= seg[i] || i === seg.length - 1) {
        const u = seg[i] ? Math.min(1, want / seg[i]) : 0;
        return { p: [path[i][0] + (path[i + 1][0] - path[i][0]) * u, path[i][1] + (path[i + 1][1] - path[i][1]) * u], a: path[i], b: path[i + 1] };
      }
      want -= seg[i];
    }
    return null;
  }
  function chevron(path, t, size) {
    const q = pointAt(path, t);
    if (!q) return null;
    const k = Math.cos((q.p[1] * Math.PI) / 180);
    let dx = (q.b[0] - q.a[0]) * k, dy = q.b[1] - q.a[1];
    const n = Math.hypot(dx, dy) || 1;
    dx /= n; dy /= n;
    const back = (s) => [q.p[0] - (dx * size) / k + (s * dy * size * 0.7) / k, q.p[1] - dy * size - s * dx * size * 0.7];
    return [back(1), q.p, back(-1)];
  }

  function mount(map, data, config) {
    Kit.assertMap(map, 'legs');
    const cfg = merge(DEFAULTS, config);
    const owner = uid('kit-legs');
    const dock = Kit.dock(map.getContainer(), { container: cfg.legend.container, position: cfg.legend.position, theme: cfg.theme, table: cfg.table, tableLabel: '表で見る（区間の一覧）' });
    let tags = [], gone = false;
    const clearTags = () => { tags.forEach((m) => m.remove()); tags = []; };

    function draw(d, first) {
      const { ok, skipped, reversed } = clean(d);
      const groups = new Map();
      ok.forEach((l) => {
        const k = `${l.from.name}|${l.to.name}|${l.status}`;
        if (!groups.has(k)) groups.set(k, { l, n: 0, ids: [] });
        const g = groups.get(k); g.n += 1; g.ids.push(l.id);
      });
      const dashOk = typeof deck.PathStyleExtension === 'function';
      const items = [], marks = [];
      for (const g of groups.values()) {
        const l = g.l;
        const base = Array.isArray(l.path) && l.path.length >= 2 ? l.path : [[l.from.lon, l.from.lat], [l.to.lon, l.to.lat]];
        const path = shifted(base, cfg.laneGap * (0.5 + LANE[l.status] * 0.7));
        const w = Math.min(cfg.width.max, cfg.width.base + cfg.width.perTrip * g.n) * (l.status === 'unknown' ? 0.6 : 1);
        const col = cfg.colors[l.status];
        items.push({ path, w: Math.max(1.5, w), col, n: g.n, dash: l.status === 'empty', tip: `${l.from.name} → ${l.to.name}\n${cfg.labels[l.status]}・${g.n} 便` });
        if (l.status === 'loaded') { const c = chevron(path, 0.5, 0.05); if (c) marks.push({ path: c }); }
      }
      const places = new Map();
      ok.forEach((l) => [l.from, l.to].forEach((p) => { places.set(`${p.lon},${p.lat}`, p); }));
      const L = deck;
      // 空車の破線は、破線の拡張が読み込まれているときだけ。なければ色（橙）で区別する
      const ext = dashOk ? [new L.PathStyleExtension({ dash: true })] : [];
      Kit.deckLayers.set(map, owner, [
        new L.PathLayer({
          id: `${owner}-path`, data: items, getPath: (x) => x.path, getWidth: (x) => x.w, getColor: (x) => x.col, widthUnits: 'pixels', capRounded: true, jointRounded: true, pickable: true,
          getDashArray: (x) => (x.dash ? [5, 3] : [0, 0]), dashJustified: true, extensions: ext,
          widthMinPixels: 1.2,
        }),
        new L.PathLayer({ id: `${owner}-chev`, data: marks, getPath: (x) => x.path, getWidth: 2.4, getColor: [236, 248, 255, 235], widthUnits: 'pixels', capRounded: true, jointRounded: true }),
        new L.ScatterplotLayer({
          id: `${owner}-pts`, data: [...places.values()], getPosition: (p) => [p.lon, p.lat], getRadius: 6, radiusUnits: 'pixels', getFillColor: [255, 255, 255, 255],
          getLineColor: [92, 200, 255, 255], stroked: true, lineWidthUnits: 'pixels', getLineWidth: 3,
        }),
      ]);

      clearTags();
      if (cfg.names) { tags = [...places.values()].map((p) => Kit.tag(map, p, esc(p.name), { offset: [0, -10] })); Kit.declutter(map, tags); }

      const maxN = Math.max(1, ...[...groups.values()].map((g) => g.n));
      dock.set({
        legend: cfg.legend.show ? {
          title: cfg.legend.title,
          items: STATUS.map((s) => ({ label: cfg.labels[s], color: cfg.colors[s], kind: s === 'empty' ? 'dash' : 'line' })),
          note: `${cfg.texts.width}（最大 ${maxN} 便）。向きは進行方向の右側に並べ、実車の線の矢じりが進む向きです。${Kit.basisNote}${cfg.legend.note ? ' ' + cfg.legend.note : ''}`,
        } : null,
        warns: [...(skipped.length ? [`座標か判定が足りない ${skipped.length} 件（${skipped.slice(0, 5).join('、')} 件目など）は描いていません。`] : []),
          ...(reversed.length ? [`終了が開始より前の ${reversed.length} 件（${reversed.slice(0, 5).join('、')} 件目など）は描いていません。`] : [])],
        table: {
          cols: [{ label: '車両' }, { label: '開始' }, { label: '終了' }, { label: '発地' }, { label: '着地' }, { label: '判定' }, { label: '距離', n: true }, { label: '積んだ重さ', n: true }, { label: '区別' }],
          rows: ok.map((l) => [l.vehicle || '', l.start || '', l.end || '', l.from.name, l.to.name, cfg.labels[l.status], finite(l.distance_km) ? Kit.fmt.km(l.distance_km) : '—', finite(l.weight_kg) ? Kit.fmt.kg(l.weight_kg) : '—', l.basis || '']),
        },
        sources: [...new Set(ok.map((l) => l.source).filter(Boolean))],
      });
      if (first && cfg.fit) Kit.fit(map, ok.flatMap((l) => [[l.from.lon, l.from.lat], [l.to.lon, l.to.lat]]), { padding: cfg.fitPadding });
    }

    draw(data, true);
    return {
      update(d) { if (!gone) draw(d, false); },
      destroy() { if (gone) return; gone = true; clearTags(); Kit.deckLayers.remove(map, owner); dock.destroy(); },
    };
  }

  Kit.legs = { mount };
})(window.Kit);
