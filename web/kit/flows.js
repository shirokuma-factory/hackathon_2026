'use strict';
/* 地点の間の量の流れ（SPEC §36.1。元にする今のコード: web/market.js）。表示だけで、量や比は計算しない。
   太さ = 量（value）。明るさ = 偏りの比（return_value ÷ value が小さいほど、帰りが少なく明るい。market.js の「帰りの荷が少ないほど明るい」と同じ向き）。
   return_value が無い流れは、明るさを付けず基本の色にする。
   使い方: const f = Kit.flows.mount(map, data, config); f.update(newData); f.destroy(); */
(function (Kit) {
  const { esc, merge, hasLL, finite, lerp, uid } = Kit.util;

  const DEFAULTS = {
    colors: { base: [92, 200, 255], bright: [255, 214, 102] },
    width: { min: 2, max: 16 },
    names: true,
    fit: true,
    fitPadding: 56,
    legend: { title: '量の流れ', note: '', container: null, position: 'bottom-left', show: true },
    table: true,
    theme: 'dark',
    unit: '',
    texts: { thin: '少ない', thick: '多い', left: '帰りも多い（偏りが小さい）', right: '帰りが少ない（偏りが大きい）', noReturn: '帰りの量が無い流れは基本の色です' },
  };

  function clean(data) {
    const all = (data && data.flows) || [];
    const ok = [], skipped = [];
    all.forEach((f, i) => {
      if (f && hasLL(f.from) && hasLL(f.to) && finite(f.value) && f.value >= 0) ok.push(f);
      else skipped.push(i + 1);
    });
    return { ok, skipped };
  }

  function mount(map, data, config) {
    Kit.assertMap(map, 'flows');
    const cfg = merge(DEFAULTS, config);
    const owner = uid('kit-flows');
    const dock = Kit.dock(map.getContainer(), { container: cfg.legend.container, position: cfg.legend.position, theme: cfg.theme, table: cfg.table, tableLabel: '表で見る（流れの一覧）' });
    let tags = [], gone = false, current = null;

    function clearTags() { tags.forEach((m) => m.remove()); tags = []; }

    function draw(d, first) {
      const { ok, skipped } = clean(d);
      current = ok;
      const unit = cfg.unit || (ok[0] && ok[0].unit) || '';
      const max = Math.max(1, ...ok.map((f) => f.value));
      const wOf = (v) => cfg.width.min + (cfg.width.max - cfg.width.min) * Math.sqrt(v / max);
      const bright = (f) => (finite(f.return_value) && f.value > 0 ? 1 - Math.min(1, f.return_value / f.value) : 0);
      const items = ok.map((f) => {
        const b = bright(f);
        const col = lerp(cfg.colors.base, cfg.colors.bright, b);
        return {
          ...f, w: wOf(f.value), b, col,
          src: [f.from.lon, f.from.lat], dst: [f.to.lon, f.to.lat],
          tip: `${f.from.name} → ${f.to.name}\n${Kit.fmt.by(f.value, f.unit || unit)}（${f.basis}）${finite(f.return_value) ? `\n帰り ${Kit.fmt.by(f.return_value, f.unit || unit)}` : ''}\n${f.period || ''}`,
        };
      });
      const L = deck;
      Kit.deckLayers.set(map, owner, [
        new L.ArcLayer({
          id: `${owner}-arc`, data: items, getSourcePosition: (x) => x.src, getTargetPosition: (x) => x.dst, getWidth: (x) => x.w, widthUnits: 'pixels',
          getSourceColor: (x) => [...x.col, 90], getTargetColor: (x) => [...x.col, 235], getHeight: 0.35, greatCircle: false, pickable: true,
          updateTriggers: { getWidth: [max], getSourceColor: [items.length], getTargetColor: [items.length] },
        }),
        new L.ScatterplotLayer({
          id: `${owner}-end`, data: items, getPosition: (x) => x.dst, getRadius: (x) => Math.max(4, x.w * 0.55), radiusUnits: 'pixels', getFillColor: (x) => [...x.col, 255],
          stroked: true, getLineColor: [255, 255, 255, 220], lineWidthUnits: 'pixels', getLineWidth: 1, pickable: true,
        }),
      ]);

      clearTags();
      if (cfg.names) {
        const seen = new Map();
        ok.forEach((f) => { [f.from, f.to].forEach((p) => { const k = `${p.lon},${p.lat}`; if (!seen.has(k)) seen.set(k, p); }); });
        tags = [...seen.values()].map((p) => Kit.tag(map, p, esc(p.name), { offset: [0, -6] }));
      }

      const periods = [...new Set(ok.map((f) => f.period).filter(Boolean))];
      const sources = [...new Set(ok.map((f) => f.source).filter(Boolean))];
      const t = cfg.texts;
      dock.set({
        legend: cfg.legend.show ? {
          title: cfg.legend.title,
          items: [{ label: `線の太さ = 量（${t.thin}から${t.thick}。最大 ${Kit.fmt.by(max, unit)}）`, color: cfg.colors.base, kind: 'line' }],
          ramp: { from: cfg.colors.base, to: cfg.colors.bright, left: t.left, right: t.right },
          note: `${t.noReturn}。${periods.length ? `期間: ${periods.join('、')}。` : ''}${Kit.basisNote}`.replace(/。。/g, '。') + (cfg.legend.note ? ' ' + cfg.legend.note : ''),
        } : null,
        warns: skipped.length ? [`座標か量が足りない ${skipped.length} 件（${skipped.slice(0, 5).join('、')} 件目など）は描いていません。`] : [],
        table: {
          cols: [{ label: '出発' }, { label: '到着' }, { label: `量`, n: true }, { label: '帰りの量', n: true }, { label: '期間' }, { label: '区別' }, { label: '出典' }],
          rows: ok.map((f) => [f.from.name, f.to.name, Kit.fmt.by(f.value, f.unit || unit), finite(f.return_value) ? Kit.fmt.by(f.return_value, f.unit || unit) : '—', f.period || '', f.basis || '', f.source || '']),
        },
        sources,
      });
      if (first && cfg.fit) Kit.fit(map, ok.flatMap((f) => [[f.from.lon, f.from.lat], [f.to.lon, f.to.lat]]), { padding: cfg.fitPadding });
    }

    draw(data, true);
    return {
      update(d) { if (!gone) draw(d, false); },
      destroy() {
        if (gone) return;
        gone = true; clearTags(); Kit.deckLayers.remove(map, owner); dock.destroy();
      },
      get items() { return current; },
    };
  }

  Kit.flows = { mount };
})(window.Kit);
