'use strict';
/* 地点の点（SPEC §36.1。元にする今のコード: web/waiting_map.js）。表示だけで、集計はしない。
   大きさ = 量（value）。色 = 4 段の区分。区分に使う数は level（無ければ value）で、点の中の数字も level。
   区分は設定の bands（min の小さい順）で決める。点に band（0〜3）があれば、それを使う。
   色だけに頼らず、凡例・点の中の数字・表でも読めるようにする。
   使い方: const p = Kit.points.mount(map, data, config); p.update(newData); p.destroy(); */
(function (Kit) {
  const { esc, merge, hasLL, finite, rgba, uid } = Kit.util;

  const DEFAULTS = {
    bands: Kit.palette.bands,
    size: { min: 18, max: 58, k: 1.5 }, // 直径 px = min(max, min + √量 × k)
    unit: '', levelUnit: '',
    fit: true,
    fitPadding: 72,
    legend: { title: '地点の点', sizeText: '大きさ = 量', colorText: '色と点の中の数字 = ', note: '', container: null, position: 'bottom-left', show: true },
    table: true,
    theme: 'dark',
    onSelect: null, // 点を押したとき (point | null) を受け取る。同じ点をもう一度押すと選びを外す
  };

  function mount(map, data, config) {
    Kit.assertMap(map, 'points');
    const cfg = merge(DEFAULTS, config);
    const owner = uid('kit-points');
    const dock = Kit.dock(map.getContainer(), { container: cfg.legend.container, position: cfg.legend.position, theme: cfg.theme, table: cfg.table, tableLabel: '表で見る（地点の一覧）' });
    let marks = [], sel = '', gone = false;
    const sizeOf = (v) => Math.round(Math.min(cfg.size.max, cfg.size.min + Math.sqrt(Math.max(0, v)) * cfg.size.k));
    const levelOf = (p) => (finite(p.level) ? p.level : p.value);

    function clear() { marks.forEach((m) => m.mk.remove()); marks = []; }
    function paintSel() { marks.forEach((m) => { m.el.classList.toggle('on', m.p.id === sel); m.el.setAttribute('aria-pressed', String(m.p.id === sel)); }); }

    function draw(d, first) {
      const all = (d && d.points) || [], ok = [], skipped = [];
      all.forEach((p, i) => { if (p && hasLL(p) && finite(p.value) && p.value >= 0) ok.push({ ...p, id: p.id ?? p.name }); else skipped.push(i + 1); });
      clear();
      const unit = cfg.unit || (ok[0] && ok[0].unit) || '';
      for (const p of ok) {
        const band = Number.isInteger(p.band) && cfg.bands[p.band] ? p.band : Kit.bandOf(levelOf(p), cfg.bands);
        const sz = sizeOf(p.value), c = cfg.bands[band].color;
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'kit-pt';
        el.setAttribute('data-kit', 'point');
        el.style.width = el.style.height = `${sz}px`;
        el.style.background = rgba(c, 0.85);
        el.style.borderColor = rgba(c, 1);
        el.setAttribute('aria-label', `${p.name}。量 ${Kit.fmt.by(p.value, p.unit || unit)}、${cfg.bands[band].label}（${p.basis || ''}）`);
        el.innerHTML = `<span>${esc(Kit.fmt.num(levelOf(p), 0))}</span>${sz >= 40 ? `<span class="kit-t">${esc(p.name)}</span>` : ''}`;
        el.title = `${p.name}\n量 ${Kit.fmt.by(p.value, p.unit || unit)}\n${cfg.bands[band].label}`;
        el.addEventListener('click', (e) => {
          e.stopPropagation();
          sel = sel === p.id ? '' : p.id;
          paintSel();
          if (cfg.onSelect) cfg.onSelect(sel ? p : null);
        });
        p._band = band;
        marks.push({ el, p, mk: new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat([p.lon, p.lat]).addTo(map) });
      }
      if (sel && !ok.some((p) => p.id === sel)) sel = '';
      paintSel();

      dock.set({
        legend: cfg.legend.show ? {
          title: cfg.legend.title,
          items: cfg.bands.map((b) => ({ label: `${cfg.legend.colorText}${b.label}`, color: b.color })),
          note: `${cfg.legend.sizeText}${unit ? `（${unit}）` : ''}。${Kit.basisNote}${cfg.legend.note ? ' ' + cfg.legend.note : ''}`,
        } : null,
        warns: skipped.length ? [`座標か量が足りない ${skipped.length} 件（${skipped.slice(0, 5).join('、')} 件目など）は描いていません。`] : [],
        table: {
          cols: [{ label: '地点' }, { label: '量', n: true }, { label: '区分値', n: true }, { label: '区分' }, { label: '区別' }, { label: '期間' }, { label: '出典' }],
          rows: ok.map((p) => [p.name, Kit.fmt.by(p.value, p.unit || unit), Kit.fmt.by(levelOf(p), cfg.levelUnit || unit), cfg.bands[p._band].label, p.basis || '', p.period || '', p.source || '']),
        },
        sources: [...new Set(ok.map((p) => p.source).filter(Boolean))],
      });
      if (first && cfg.fit) Kit.fit(map, ok.map((p) => [p.lon, p.lat]), { padding: cfg.fitPadding, maxZoom: 9 });
    }

    draw(data, true);
    return {
      update(d) { if (!gone) draw(d, false); },
      select(id) { sel = id || ''; paintSel(); },
      destroy() { if (gone) return; gone = true; clear(); dock.destroy(); },
    };
  }

  Kit.points = { mount };
})(window.Kit);
