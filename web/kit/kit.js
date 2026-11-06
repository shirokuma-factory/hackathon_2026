'use strict';
/* 部品の共通部分（SPEC §36.1）。表示だけで、計算はしない。
   ここにあるもの: 色の区分、数字の書き方（円・km・%・分・kg）、凡例と「表で見る」と出典の欄（dock）、
   地図の上の描画の共有（deck.gl の重ね合わせを 1 つの地図に 1 つだけ作り、部品ごとに層を出し入れする）、地図の下地の作り方。
   読み込む順: MapLibre GL 4.7.1 と deck.gl 9.0.38（今のアプリと同じ固定版）→ kit.js → 使う部品の js。
   どの部品も Kit.<名前>.mount(地図か要素, データ, 設定) を持ち、返り値の update(データ) と destroy() で更新と片付けをする。 */
(function (root) {
  const Kit = root.Kit || (root.Kit = {});
  Kit.version = '1.0.0';

  /* ------------------------------------------------------------ 小さな道具 */
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const isObj = (v) => v && typeof v === 'object' && !Array.isArray(v);
  // 設定の既定値と利用者の設定を重ねる（配列は丸ごと置き換える）
  function merge(base, over) {
    const out = { ...base };
    for (const k of Object.keys(over || {})) out[k] = isObj(base[k]) && isObj(over[k]) ? merge(base[k], over[k]) : over[k];
    return out;
  }
  const finite = (v) => typeof v === 'number' && Number.isFinite(v);
  const hasLL = (p) => !!p && finite(p.lon) && finite(p.lat) && Math.abs(p.lon) <= 180 && Math.abs(p.lat) <= 90;
  const rgba = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  const lerp = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
  let seq = 0;
  const uid = (p) => `${p}-${++seq}`;
  Kit.util = { esc, merge, finite, hasLL, rgba, lerp, uid };

  /* ------------------------------------------------------------ 数字の書き方（円・km・%・分・kg） */
  const num = (v, d = 0) => (finite(v) ? Number(v).toLocaleString('ja-JP', { maximumFractionDigits: d, minimumFractionDigits: 0 }) : '—');
  const fmt = {
    num,
    yen: (v) => (finite(v) ? `${num(v)} 円` : '—'),
    km: (v) => (finite(v) ? `${num(v, 1)} km` : '—'),
    pct: (v) => (finite(v) ? `${num(v, 1)}%` : '—'),
    kg: (v) => (finite(v) ? `${num(v, 1)} kg` : '—'),
    min: (v) => (finite(v) ? (v >= 60 ? `${Math.floor(v / 60)} 時間 ${Math.round(v % 60)} 分` : `${num(v)} 分`) : '—'),
    // 単位の文字に合わせて書き分ける。知らない単位は「数 単位」
    by(v, unit) {
      switch (unit) {
        case '円': return fmt.yen(v);
        case 'km': return fmt.km(v);
        case '%': return fmt.pct(v);
        case 'kg': return fmt.kg(v);
        case '分': return fmt.min(v);
        default: return finite(v) ? `${num(v, Number.isInteger(v) ? 0 : 1)}${unit ? ' ' + unit : ''}` : '—';
      }
    },
  };
  Kit.fmt = fmt;

  /* ------------------------------------------------------------ 実測・推定・仮定の区別 */
  Kit.BASIS = ['実測', '推定', '仮定'];
  Kit.basisNote = '「実測」は記録から数えた値、「推定」は計算で求めた値、「仮定」は利用者が置いた値です。';

  /* ------------------------------------------------------------ 色と区分の定数（今のアプリ web/own.js・waiting_map.js・market.js も同じ値を読む。SPEC §36 / 道具の採点 r1b #16） */
  Kit.palette = {
    // 運行の区間の色（RGBA）と名前: 実車・空車・不明
    legs: { colors: { loaded: [92, 200, 255, 240], empty: [244, 150, 70, 245], unknown: [150, 162, 180, 215] }, labels: { loaded: '実車', empty: '空車', unknown: '不明' } },
    // 地点の点の 4 段の区分（平均の荷待ち 分）。min の小さい順
    bands: [
      { min: 0, label: '30 分未満', color: [255, 226, 120] },
      { min: 30, label: '30〜59 分', color: [255, 170, 54] },
      { min: 60, label: '60〜119 分', color: [238, 92, 64] },
      { min: 120, label: '120 分以上', color: [178, 28, 92] },
    ],
    // 量の流れの色。帰りの量が少ない（偏りが大きい）ほど base から bright へ明るくする
    flows: { base: [88, 150, 205], bright: [236, 249, 255] },
  };

  /* ------------------------------------------------------------ 色の区分 */
  // bands: [{min, label, color}] を min の小さい順に並べる。値が入る最後の区分の番号を返す
  Kit.bandOf = function bandOf(value, bands) {
    let b = 0;
    bands.forEach((x, i) => { if (value >= x.min) b = i; });
    return b;
  };

  /* ------------------------------------------------------------ 地図の下地（背景色と、あれば境界線だけ。タイルは使わない） */
  // outlineUrl: 境界線の GeoJSON の場所。attribution: 下地のデータの出典（地図の右下に出る）
  Kit.baseStyle = function baseStyle(opt = {}) {
    const dark = opt.theme !== 'light';
    const style = { version: 8, sources: {}, layers: [{ id: 'kit-bg', type: 'background', paint: { 'background-color': dark ? '#0d141f' : '#eef2f6' } }] };
    if (opt.outlineUrl) {
      style.sources['kit-outline'] = { type: 'geojson', data: opt.outlineUrl, attribution: opt.attribution || '' };
      style.layers.push({ id: 'kit-outline-fill', type: 'fill', source: 'kit-outline', paint: { 'fill-color': dark ? '#16202e' : '#fbfcfd' } });
      style.layers.push({ id: 'kit-outline-line', type: 'line', source: 'kit-outline', paint: { 'line-color': dark ? '#3a4a60' : '#9aa7b6', 'line-width': 0.8 } });
    }
    return style;
  };

  // 地図の出典の欄に文を足す（MapLibre の AttributionControl）。destroy で外す
  Kit.attribution = function attribution(map, texts) {
    const ctl = new maplibregl.AttributionControl({ compact: false, customAttribution: texts });
    map.addControl(ctl, 'bottom-right');
    return { destroy() { try { map.removeControl(ctl); } catch (e) { /* 地図がもう無い */ } } };
  };

  Kit.fit = function fit(map, coords, opt = {}) {
    const pts = coords.filter((c) => c && finite(c[0]) && finite(c[1]));
    if (!pts.length) return;
    const xs = pts.map((c) => c[0]), ys = pts.map((c) => c[1]);
    const b = [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]];
    map.fitBounds(b, { padding: opt.padding ?? 48, maxZoom: opt.maxZoom ?? 9, duration: 0, essential: true });
  };

  /* ------------------------------------------------------------ 地図の上の描画の共有（deck.gl） */
  // 1 つの地図に deck.MapboxOverlay を 1 つだけ作り、部品ごとの層を持ち寄って重ねる。最後の部品が外れたらオーバーレイも外す。
  // 地図のスタイルの層は増やさない（interleaved: false。描画は地図の上の別の canvas）
  const OV = new WeakMap();
  const TIP_STYLE = { background: '#0d141f', color: '#e8eef8', border: '1px solid rgba(255,255,255,.16)', borderRadius: '8px', font: '12px "Noto Sans JP","Yu Gothic",Meiryo,sans-serif', padding: '6px 10px', whiteSpace: 'pre-line' };
  Kit.deckLayers = {
    set(map, owner, layers) {
      let e = OV.get(map);
      if (!e) {
        const overlay = new deck.MapboxOverlay({
          interleaved: false, layers: [], pickingRadius: 6,
          getCursor: ({ isHovering, isDragging }) => (isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab'),
          // 層のデータの要素に tip（文字）があれば、指を置くだけで読める説明として出す
          getTooltip: ({ object }) => (object && object.tip ? { text: object.tip, style: TIP_STYLE } : null),
        });
        map.addControl(overlay);
        e = { overlay, owners: new Map() };
        OV.set(map, e);
      }
      e.owners.set(owner, layers);
      e.overlay.setProps({ layers: [...e.owners.values()].flat() });
    },
    remove(map, owner) {
      const e = OV.get(map);
      if (!e) return;
      e.owners.delete(owner);
      if (e.owners.size) { e.overlay.setProps({ layers: [...e.owners.values()].flat() }); return; }
      try { map.removeControl(e.overlay); } catch (err) { /* 地図がもう無い */ }
      OV.delete(map);
    },
    // いま出している層の ID の一覧（試験用）
    ids(map) {
      const e = OV.get(map);
      return e ? [...e.owners.values()].flat().map((l) => l.id) : [];
    },
  };

  /* ------------------------------------------------------------ 凡例・表・出典の欄（dock） */
  const CSS = `
.kit-dock{box-sizing:border-box;font:13px/1.55 "Noto Sans JP","Yu Gothic",Meiryo,sans-serif;color:#e8eef8;background:rgba(13,20,31,.92);border:1px solid rgba(255,255,255,.14);border-radius:10px;padding:10px 12px;max-width:100%}
.kit-dock.kit-float{position:absolute;z-index:3;max-width:min(340px,calc(100% - 20px));max-height:70%;overflow:auto}
.kit-dock.kit-bl{left:10px;bottom:28px}.kit-dock.kit-br{right:10px;bottom:28px}.kit-dock.kit-tl{left:10px;top:10px}.kit-dock.kit-tr{right:10px;top:10px}
.kit-dock.kit-light{color:#18222f;background:rgba(255,255,255,.96);border-color:rgba(0,0,0,.16)}
.kit-dock b{font-weight:700}.kit-dock small,.kit-dock .kit-sub{font-size:12px;opacity:.85}
.kit-lg-t{display:block;margin-bottom:4px}
.kit-lg-i{display:flex;align-items:center;gap:8px;margin:2px 0}
.kit-lg-i i{display:inline-block;flex:none}
.kit-lg-dot{width:14px;height:14px;border-radius:50%}
.kit-lg-line{width:26px;height:0;border-top:4px solid}
.kit-lg-dash{width:26px;height:0;border-top:4px dashed}
.kit-lg-ramp{width:100%;max-width:200px;height:10px;border-radius:5px}
.kit-lg-ends{display:flex;justify-content:space-between;gap:16px;font-size:12px}
.kit-det{margin-top:8px}.kit-det summary{cursor:pointer}
.kit-tw{overflow:auto;max-height:240px;margin-top:4px}
.kit-tab{border-collapse:collapse;width:100%;font-size:12px}
.kit-tab th,.kit-tab td{border-bottom:1px solid rgba(128,140,160,.35);padding:3px 6px;text-align:left;white-space:nowrap}
.kit-tab td.n,.kit-tab th.n{text-align:right;font-variant-numeric:tabular-nums}
.kit-src{margin:6px 0 0;font-size:12px;opacity:.85}
.kit-warn{margin:6px 0 0;font-size:12px;color:#ffcf7a}
.kit-dock.kit-light .kit-warn{color:#8a4b00}
.kit-tag{font:12px/1.3 "Noto Sans JP","Yu Gothic",Meiryo,sans-serif;color:#e8eef8;background:rgba(13,20,31,.82);border:1px solid rgba(255,255,255,.2);border-radius:6px;padding:2px 6px;white-space:nowrap;pointer-events:none}
.kit-pt{box-sizing:border-box;border:2px solid;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#10161f;cursor:pointer;padding:0;font:700 12px/1 "Noto Sans JP","Yu Gothic",Meiryo,sans-serif}
.kit-pt.on{outline:3px solid #fff}
.kit-pt .kit-t{font-weight:400;font-size:10px;line-height:1.1;max-width:90%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kit-tl{box-sizing:border-box;font:13px/1.55 "Noto Sans JP","Yu Gothic",Meiryo,sans-serif;color:#e8eef8;background:rgba(13,20,31,.92);border:1px solid rgba(255,255,255,.14);border-radius:10px;padding:10px 12px}
.kit-tl-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.kit-btn{font:inherit;color:inherit;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.3);border-radius:8px;padding:5px 12px;cursor:pointer;min-height:32px}
.kit-btn:focus-visible,.kit-sel:focus-visible,.kit-rng:focus-visible,.kit-pt:focus-visible{outline:3px solid #8cd7ff;outline-offset:2px}
.kit-sel{font:inherit;color:inherit;background:#16202e;border:1px solid rgba(255,255,255,.3);border-radius:8px;padding:4px 8px;min-height:32px}
.kit-rng{flex:1 1 160px;min-width:120px}
.kit-clock{font-variant-numeric:tabular-nums;min-width:4.5em}
.kit-band{margin-top:8px}
.kit-band-row{display:grid;grid-template-columns:7em 1fr;align-items:center;gap:8px;margin:3px 0}
.kit-band-lab{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}
.kit-band-track{position:relative;height:14px;background:rgba(255,255,255,.07);border-radius:4px}
.kit-seg{position:absolute;top:0;bottom:0;border-radius:3px}
.kit-seg.stop{background:repeating-linear-gradient(45deg,#8795a8 0 3px,#4a586b 3px 6px)}
.kit-cursor{position:absolute;top:-3px;bottom:-3px;width:2px;background:#fff;pointer-events:none}
.kit-axis{display:grid;grid-template-columns:7em 1fr;gap:8px;font-size:11px;opacity:.8}
.kit-axis span:last-child{display:flex;justify-content:space-between}
.kit-3d{box-sizing:border-box;position:relative;font:13px/1.55 "Noto Sans JP","Yu Gothic",Meiryo,sans-serif;color:#e8eef8;background:#0d141f;border:1px solid rgba(255,255,255,.14);border-radius:10px;overflow:hidden}
.kit-3d-stage{position:relative;height:var(--kit-3d-h,420px);min-height:260px}
.kit-3d-tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:8px 10px}
.kit-3d-msg{position:absolute;left:10px;top:10px;right:10px;font-size:12px;color:#ffcf7a;z-index:2}
.kit-3d-side{padding:8px 12px 12px}
.kit-3d-nums{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:6px 14px;margin:6px 0}
.kit-3d-nums div{display:flex;justify-content:space-between;gap:8px;border-bottom:1px solid rgba(255,255,255,.12)}
.kit-3d-ok{color:#7be0a0}.kit-3d-warn{color:#ffcf7a}.kit-3d-ng{color:#ff8f8f}
@media (max-width:600px){.kit-dock.kit-float{max-height:50%}.kit-band-row,.kit-axis{grid-template-columns:5em 1fr}}
`;
  function ensureStyle() {
    if (document.getElementById('kit-style')) return;
    const s = document.createElement('style');
    s.id = 'kit-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  }
  Kit.ensureStyle = ensureStyle;

  function legendHtml(l) {
    if (!l) return '';
    const items = (l.items || []).map((it) => {
      const c = it.color ? rgba(it.color, 0.95) : '#999';
      const mark = it.kind === 'line' ? `<i class="kit-lg-line" style="border-color:${c}"></i>`
        : it.kind === 'dash' ? `<i class="kit-lg-dash" style="border-color:${c}"></i>`
        : `<i class="kit-lg-dot" style="background:${c}"></i>`;
      return `<span class="kit-lg-i">${mark}<span>${esc(it.label)}</span></span>`;
    }).join('');
    const ramp = l.ramp
      ? `<span class="kit-lg-i"><i class="kit-lg-ramp" style="background:linear-gradient(90deg,${rgba(l.ramp.from)},${rgba(l.ramp.to)})"></i></span>
         <span class="kit-lg-ends"><span>${esc(l.ramp.left)}</span><span>${esc(l.ramp.right)}</span></span>` : '';
    return `<div class="kit-lg" role="group" aria-label="${esc(l.title || '凡例')}"><b class="kit-lg-t">${esc(l.title || '凡例')}</b>${items}${ramp}${l.note ? `<small class="kit-sub">${esc(l.note)}</small>` : ''}</div>`;
  }

  function tableHtml(t, label) {
    if (!t || !t.rows || !t.rows.length) return '';
    const head = t.cols.map((c) => `<th${c.n ? ' class="n"' : ''}>${esc(c.label)}</th>`).join('');
    const body = t.rows.map((r) => `<tr>${r.map((v, i) => `<td${t.cols[i].n ? ' class="n"' : ''}>${esc(v)}</td>`).join('')}</tr>`).join('');
    return `<details class="kit-det"><summary>${esc(label || '表で見る')}</summary><div class="kit-tw"><table class="kit-tab"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div></details>`;
  }

  // 凡例・表・注意・出典を 1 つの箱にまとめる。cfg.container があればその要素の中に、なければ地図の隅に重ねる
  // cfg: { container, position: 'bottom-left'|'bottom-right'|'top-left'|'top-right', theme, table: true, tableLabel }
  Kit.dock = function dock(target, cfg = {}) {
    ensureStyle();
    const el = document.createElement('div');
    const pos = { 'bottom-left': 'kit-bl', 'bottom-right': 'kit-br', 'top-left': 'kit-tl', 'top-right': 'kit-tr' }[cfg.position || 'bottom-left'] || 'kit-bl';
    el.className = `kit-dock${cfg.container ? '' : ` kit-float ${pos}`}${cfg.theme === 'light' ? ' kit-light' : ''}`;
    el.setAttribute('data-kit', 'dock');
    (cfg.container || target).appendChild(el);
    const S = { legend: null, table: null, warns: [], sources: [] };
    function render() {
      el.innerHTML = legendHtml(S.legend)
        + S.warns.map((w) => `<p class="kit-warn" role="status">${esc(w)}</p>`).join('')
        + (cfg.table === false ? '' : tableHtml(S.table, cfg.tableLabel))
        + (S.sources.length ? `<p class="kit-src">出典: ${S.sources.map(esc).join(' ／ ')}</p>` : '');
      el.hidden = !el.innerHTML;
    }
    return {
      el,
      set(part) { Object.assign(S, part); render(); },
      destroy() { el.remove(); },
    };
  };

  /* ------------------------------------------------------------ 部品の共通の手順 */
  Kit.assertMap = function assertMap(map, name) {
    if (typeof maplibregl === 'undefined' || typeof deck === 'undefined') throw new Error(`Kit.${name}: MapLibre GL と deck.gl を先に読み込んでください`);
    if (!map || typeof map.getContainer !== 'function') throw new Error(`Kit.${name}.mount の 1 つ目は MapLibre の地図です`);
  };
  // 地図の名前札（HTML）。返り値の remove() で外す
  Kit.tag = function tag(map, p, html, opt = {}) {
    const el = document.createElement('div');
    el.className = 'kit-tag';
    el.innerHTML = html;
    el.setAttribute('data-kit', 'tag');
    return new maplibregl.Marker({ element: el, anchor: opt.anchor || 'bottom', offset: opt.offset || [0, -8] }).setLngLat([p.lon, p.lat]).addTo(map);
  };
})(typeof window !== 'undefined' ? window : globalThis);
