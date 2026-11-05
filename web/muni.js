'use strict';
/* 市区町村の推定・物流の集積・施設の層（SPEC §18）。
   数字（推定の出荷・入荷・差、集積の従業者数と面積あたり、階級の境界、OSM の件数）は tools/build_web_data.py が
   data/stats/muni_estimate.json などから作った web/data/muni.json の値で、ここでは色・高さ・位置への変換と表示だけを行う。
   P31（国土数値情報の物流拠点）は非商用・再配布不可なので、手元に取得したときだけ web/data/local/p31.json（gitignore）から読む。 */

const Muni = (() => {
  const U = {
    d: null, scope: 'pref', conc: false, metric: 'dens', col3d: false, fac: false, sel: -1, selFeat: null, active: true,
    types: { 5: true, 1: true, 3: true, 2: true, 7: true, 4: false, air: true, osm: true },
    p31: undefined /* undefined = 未取得、null = 手元にない */, osm: null, c28: null, cols: null, maxB: 1, p31Items: null,
  };
  const DISCLAIMER = '推定です。公開の統計に市区町村単位の流動はありません。県の値を業種の従業者数と人口で分けた目安です';
  // 県の層（market.js の TEAL・ORANGE）と同じ色相で、明るく淡くする。県の層と見分けがつくようにする
  const TEAL_M = [[40, 92, 100], [150, 246, 232]];
  const ORANGE_M = [[104, 78, 56], [255, 204, 140]];
  const CONC = [[46, 40, 98], [226, 198, 255]]; // 物流の集積（紫の濃淡）
  const GRAY = [124, 134, 150, 150];
  const ATTR = {
    estat: '総務省・経済産業省『令和6年経済センサス‐基礎調査』、総務省『令和2年国勢調査』（e-Stat）を加工して作成',
    p31: '国土数値情報（物流拠点データ）（国土交通省）2013年。非商用の利用に限ります（利用約款）',
    c28: '国土数値情報（空港データ）（国土交通省）を加工して作成',
    osm: '© OpenStreetMap contributors（ODbL 1.0）。倉庫・物流施設の建物と点',
  };
  // 施設の記号。色だけに頼らず、形でも区別する（白い図形を色で塗る）
  const GLYPH = {
    5: ['トラックターミナル', '<rect x="10" y="10" width="44" height="44" rx="6"/>', [255, 152, 38]],
    1: ['コンテナターミナル', '<path d="M32 3 L61 32 L32 61 L3 32 Z"/>', [58, 196, 255]],
    3: ['鉄道貨物駅', '<path d="M32 5 L61 57 L3 57 Z"/>', [72, 214, 112]],
    2: ['航空貨物ターミナル', '<circle cx="32" cy="32" r="22" fill="none" stroke="#fff" stroke-width="11"/>', [210, 192, 255]],
    7: ['卸売市場', '<path d="M32 3 L57 17 L57 47 L32 61 L7 47 L7 17 Z"/>', [244, 166, 206]],
    4: ['保税地域', '<path d="M23 5h18v18h18v18H41v18H23V41H5V23h18z"/>', [170, 184, 204]],
    air: ['空港', '<path d="M32 3 L37 24 L61 39 L61 46 L37 40 L37 53 L45 59 L45 62 L32 59 L19 62 L19 59 L27 53 L27 40 L3 46 L3 39 L27 24 Z"/>', [244, 248, 255]],
  };
  const P31_ORDER = [5, 1, 3, 2, 7, 4];
  const svgUrl = (body) => 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><g fill="#fff">${body}</g></svg>`);
  const ICON = {};
  for (const k of Object.keys(GLYPH)) ICON[k] = { id: 'g' + k, url: svgUrl(GLYPH[k][1]), width: 64, height: 64, mask: true };
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const n0 = (v) => Math.round(v).toLocaleString('ja-JP');
  const q = (s) => document.querySelector(s);
  const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
  const rgbCss = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
  const signed = (v) => `${v >= 0 ? '+' : '−'}${n0(Math.abs(v))}`;

  /* ------------------------------------------------------------ 起動 */

  async function load() {
    U.d = await getJSON('data/muni.json');
    const d = U.d;
    const abs = d.bal.filter((v) => v !== null).map((v) => Math.abs(v)).sort((a, b) => a - b);
    U.maxB = Math.max(1, abs[Math.floor(abs.length * 0.985)] || 1); // 外れ値で全体が淡くならないよう、上位 1.5% は最も濃い色にそろえる
    U.cols = [];
    d.cemp.forEach((v, i) => { if (v && d.pt[i]) U.cols.push({ i, p: d.pt[i], v }); });
    U.cols.sort((a, b) => b.v - a.v);
    U.maxEmp = U.cols.length ? U.cols[0].v : 1;
  }

  function init() {
    const d = U.d;
    q('#mu-notice-text').textContent = DISCLAIMER;
    q('#mu-formula-text').textContent = d.formula;
    q('#m-scope').addEventListener('click', (e) => { const b = e.target.closest('button[data-scope]'); if (b) setScope(b.dataset.scope); });
    q('#mlg-conc').addEventListener('click', onConcClick);
    q('#mlg-conc').addEventListener('change', (e) => { if (e.target.id === 'cc-3d') setCol3d(e.target.checked); });
    q('#mlg-fac').addEventListener('click', onFacClick);
    q('#m-muni').addEventListener('click', (e) => { if (e.target.closest('[data-act="close"]')) deselect(); });
    document.querySelectorAll('[data-fold]').forEach((b) => b.addEventListener('click', () => fold(b.dataset.fold, b.getAttribute('aria-expanded') === 'true')));
    q('#m-rfold').addEventListener('click', () => foldRight(!document.body.classList.contains('m-right-collapsed')));
    q('#m-credits-d').addEventListener('toggle', () => document.body.classList.toggle('cr-open', q('#m-credits-d').open));
    // 画面が狭い・低いときは、重なりを避けて最初から畳む
    const narrow = innerWidth <= 1100, short = innerHeight < 800;
    if (narrow) foldRight(true);
    if (narrow || short) fold('m-calc', true);
    q('#m-credits-d').open = innerWidth >= 1700 && innerHeight >= 900;
    document.body.classList.toggle('cr-open', q('#m-credits-d').open);
    renderAll();
  }

  function fold(id, close) {
    const el = q('#' + id), b = q(`[data-fold="${id}"]`);
    el.classList.toggle('folded', close);
    if (b) b.setAttribute('aria-expanded', String(!close));
  }
  function foldRight(close) {
    document.body.classList.toggle('m-right-collapsed', close);
    q('#m-rfold').setAttribute('aria-expanded', String(!close));
  }

  /* ------------------------------------------------------------ 状態 */

  const isMuni = () => U.scope === 'muni';
  const concChoro = () => U.conc && !U.col3d;
  const prefFilled = () => !isMuni() && !concChoro();
  const maxBal = () => U.maxB;

  function setScope(s) {
    U.scope = s === 'muni' ? 'muni' : 'pref';
    if (U.scope === 'pref' && U.sel >= 0 && !U.conc) deselect();
    renderAll();
    Market.renderLegend();
    syncHash();
  }
  function setConc(on) {
    U.conc = on;
    if (!on && U.col3d) { U.col3d = false; tilt(false); }
    if (on && U.col3d) tilt(true);
    document.querySelector('.tgl[data-toggle="conc"]').setAttribute('aria-pressed', String(on));
    if (on) fold('m-calc', true); // 凡例に場所を空ける（想定の計算は見出しから開き直せる）
    renderAll();
    syncHash();
  }
  function setCol3d(on) {
    U.col3d = on;
    tilt(on);
    renderAll();
    syncHash();
  }
  function tilt(on) {
    if (typeof map === 'undefined' || !map) return;
    map.easeTo({ pitch: on && U.active ? 55 : 0, duration: 1300, essential: true });
  }
  async function setFac(on) {
    U.fac = on;
    document.querySelector('.tgl[data-toggle="fac"]').setAttribute('aria-pressed', String(on));
    if (on) fold('m-calc', true);
    renderAll();
    syncHash();
    if (on) await loadFacilities();
  }

  async function loadFacilities() {
    const tasks = [];
    if (!U.c28) tasks.push(getJSON('data/c28.json').then((j) => { U.c28 = j; }));
    if (!U.osm) tasks.push(getJSON('data/osm_points.json').then((j) => { U.osm = j; }));
    if (U.p31 === undefined) {
      tasks.push(fetch('data/local/p31.json').then(async (r) => {
        if (!r.ok) { U.p31 = null; return; }
        U.p31 = await r.json();
        U.p31Items = U.p31.items.map(([lon, lat, kind, name, address, operator]) => ({ p: [lon, lat], kind, name, address, operator }));
      }).catch(() => { U.p31 = null; }));
    }
    try { await Promise.all(tasks); } catch (e) { toast('施設のデータを読み込めませんでした'); }
    renderAll();
  }

  function setActive(on) {
    U.active = on;
    if (!on) { if (typeof map !== 'undefined' && map && map.getPitch() > 0) map.easeTo({ pitch: 0, duration: 600, essential: true }); }
    else if (U.col3d) tilt(true);
  }

  /* ------------------------------------------------------------ 色 */

  function balColor(v) {
    const t = Math.pow(Math.min(1, Math.abs(v) / U.maxB), 0.5);
    const pal = v >= 0 ? TEAL_M : ORANGE_M;
    return [...mix(pal[0], pal[1], t), 222];
  }
  const balFill = (i) => {
    if (i < 0 || U.d.ost[i] === null) return [60, 70, 90, 60];
    const v = U.d.bal[i];
    return v === null ? GRAY : balColor(v);
  };
  const concValue = (i) => (U.metric === 'dens' ? U.d.cdens[i] : U.d.cemp[i]);
  const concEdges = () => U.d.edges[U.metric];
  function classOf(v, edges) {
    for (let k = 1; k < edges.length; k++) if (v <= edges[k]) return k - 1;
    return edges.length - 2;
  }
  function concColor(v, edges, alpha) {
    const n = Math.max(1, edges.length - 2);
    return [...mix(CONC[0], CONC[1], classOf(v, edges) / n), alpha];
  }
  const concFill = (i) => {
    if (i < 0) return [60, 70, 90, 60];
    const v = concValue(i);
    return v === null || v === undefined ? GRAY : concColor(v, concEdges(), 214);
  };

  /* ------------------------------------------------------------ 層 */

  // 県の塗りの下（市区町村の塗り）
  function under() {
    const L = deck, out = [];
    if (concChoro()) {
      out.push(new L.GeoJsonLayer({
        id: 'mu-conc', data: S.muni, pickable: true, stroked: true, filled: true, beforeId: firstLabelId,
        getFillColor: (f) => concFill(f.properties.i), getLineColor: [8, 12, 22, 130], lineWidthMinPixels: 0.35, getLineWidth: 0.4, lineWidthUnits: 'pixels',
        autoHighlight: true, highlightColor: [255, 255, 255, 56],
        updateTriggers: { getFillColor: [U.metric] }, transitions: { getFillColor: { duration: 500, easing: ease } },
      }));
    } else if (isMuni()) {
      out.push(new L.GeoJsonLayer({
        id: 'mu-bal', data: S.muni, pickable: true, stroked: true, filled: true, beforeId: firstLabelId,
        getFillColor: (f) => balFill(f.properties.i), getLineColor: [6, 10, 18, 140], lineWidthMinPixels: 0.35, getLineWidth: 0.4, lineWidthUnits: 'pixels',
        autoHighlight: true, highlightColor: [255, 255, 255, 56],
      }));
    }
    return out;
  }

  const mpp = (z) => 126650 / Math.pow(2, z); // 緯度 36° での 1 ピクセルの長さ（m）

  // 地図の上に重ねる層（3D の柱、施設、選んだ市区町村の輪郭）
  function over(zoom) {
    const L = deck, out = [];
    if (U.conc && U.col3d) {
      const z = Math.round(zoom * 4) / 4, m = mpp(z), edges = U.d.edges.emp;
      out.push(new L.ColumnLayer({
        id: 'mu-col', data: U.cols, getPosition: (d) => d.p, diskResolution: 14, radius: 4.2 * m, extruded: true,
        getElevation: (d) => d.v, elevationScale: (170 * m) / U.maxEmp, getFillColor: (d) => concColor(d.v, edges, 240),
        pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 90],
        material: { ambient: 0.62, diffuse: 0.7, shininess: 28, specularColor: [190, 190, 230] },
        updateTriggers: { getFillColor: [1] },
      }));
    }
    if (U.fac) out.push(...facLayers(zoom));
    if (U.sel >= 0 && U.selFeat) {
      out.push(new L.GeoJsonLayer({
        id: 'mu-sel', data: [U.selFeat], stroked: true, filled: false, getLineColor: [255, 255, 255, 240], lineWidthUnits: 'pixels', getLineWidth: 2.2,
      }));
    }
    return out;
  }

  // 施設層のデータ配列を、元データと種別の選択が同じ間は同じ参照で返す
  const facMemo = {};
  function facP31() {
    const sig = Object.keys(U.types).filter((k) => U.types[k]).join(',');
    const m = facMemo.p31;
    if (m && m.src === U.p31Items && m.sig === sig) return m.v;
    const v = U.p31Items.filter((x) => U.types[x.kind]);
    facMemo.p31 = { src: U.p31Items, sig, v };
    return v;
  }
  function facAir() {
    const m = facMemo.air;
    if (m && m.src === U.c28) return m.v;
    const v = U.c28.items.map(([lon, lat, name, status]) => ({ p: [lon, lat], name, status }));
    facMemo.air = { src: U.c28, v };
    return v;
  }
  function facHalo() {
    const a = U.p31Items ? facP31() : null, b = U.c28 && U.types.air ? facAir() : null;
    const m = facMemo.halo;
    if (m && m.a === a && m.b === b) return m.v;
    const v = [...(a || []), ...(b || [])];
    facMemo.halo = { a, b, v };
    return v;
  }

  function facLayers(zoom) {
    const L = deck, out = [], size = Math.max(9, Math.min(17, 6 + (zoom - 3) * 1.6));
    const halo = [];
    if (U.p31Items) {
      const items = facP31(); // 同じ参照を返す（毎フレーム新しい配列を渡すと属性が毎回作り直される）
      out.push(new L.IconLayer({
        id: 'fac-p31', data: items, getPosition: (d) => d.p, getIcon: (d) => ICON[d.kind], getSize: size, sizeUnits: 'pixels',
        getColor: (d) => [...GLYPH[d.kind][2], 245], pickable: true, updateTriggers: { getSize: [Math.round(size)] },
      }));
      halo.push(...items);
    }
    if (U.c28 && U.types.air) {
      const air = facAir();
      out.push(new L.IconLayer({
        id: 'fac-air', data: air, getPosition: (d) => d.p, getIcon: () => ICON.air, getSize: size + 3, sizeUnits: 'pixels', getColor: [...GLYPH.air[2], 255], pickable: true,
        updateTriggers: { getSize: [Math.round(size)] },
      }));
      halo.push(...air);
    }
    if (halo.length) {
      out.unshift(new L.ScatterplotLayer({
        id: 'fac-halo', data: facHalo(), getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: size * 0.72, filled: true, getFillColor: [8, 12, 22, 175],
        updateTriggers: { getRadius: [Math.round(size)], data: [halo.length] },
      }));
    }
    if (U.types.osm && U.osm) {
      if (zoom >= 11) {
        // 全国 8 万件を毎フレーム描くと、内蔵 GPU では 1 フレームに約 20ms かかる（SPEC §19.5）。画面の周りの分だけ渡す
        const cv = Cull.view('fac-osm', U.osm.items, (d) => [d[0], d[1], d[0], d[1]], typeof map !== 'undefined' ? map : null);
        out.push(new L.ScatterplotLayer({
          id: 'fac-osm', data: cv.items, getPosition: (d) => [d[0], d[1]], radiusUnits: 'pixels', getRadius: 3.2, filled: true, getFillColor: [238, 206, 120, 215],
          stroked: true, getLineColor: [10, 14, 24, 220], lineWidthUnits: 'pixels', getLineWidth: 0.8, pickable: true,
        }));
      } else if (U.d) {
        if (!U.agg) U.agg = U.d.osm.map((c, i) => ({ i, p: U.d.pt[i], n: c ? c.reduce((a, b) => a + b, 0) : 0 })).filter((x) => x.n > 0 && x.p);
        out.push(new L.ScatterplotLayer({
          id: 'fac-osm-agg', data: U.agg, getPosition: (d) => d.p, radiusUnits: 'pixels', getRadius: (d) => Math.min(26, 3 + Math.sqrt(d.n) * 0.62),
          filled: true, getFillColor: [238, 206, 120, 64], stroked: true, getLineColor: [238, 206, 120, 210], lineWidthUnits: 'pixels', getLineWidth: 1, pickable: true,
        }));
      }
    }
    out.push(...fac3d(zoom));
    return out;
  }

  // 施設の 3D モデル（ズーム 12 以上、画面に入るものだけ。models3d.js）。向きは固定。
  // P31 の種類 → モデル: トラックターミナル・卸売市場・保税地域 → 倉庫、コンテナターミナル → コンテナヤード + 港のクレーン、
  // 鉄道貨物駅 → ターミナル、航空貨物ターミナルと空港（C28）→ 航空貨物ターミナル。OSM の倉庫・物流施設 → 倉庫（冷蔵の区別はデータにない）
  const P31_MODEL = { 5: 'warehouse', 7: 'warehouse', 4: 'warehouse', 3: 'terminal', 2: 'air_cargo_terminal' };
  // 毎フレーム配列を作り直すと deck.gl がデータの再計算をするので、元のデータと種類の選択が同じ間は同じ配列を返す
  const memo = new Map();
  function cached(key, deps, fn) {
    const m = memo.get(key);
    if (m && m.deps.length === deps.length && m.deps.every((d, i) => d === deps[i])) return m.v;
    const v = fn();
    memo.set(key, { deps, v });
    return v;
  }
  function fac3d(zoom) {
    if (!M3D.facilityOn(zoom)) return [];
    const out = [], add = (l) => { if (l) out.push(l); };
    const sig = Object.keys(U.types).map((k) => (U.types[k] ? k : '')).join();
    if (U.p31Items) {
      const g = cached('p31', [U.p31Items, sig], () => {
        const items = U.p31Items.filter((x) => U.types[x.kind]), by = {};
        for (const kind of Object.keys(P31_MODEL)) by[kind] = items.filter((x) => String(x.kind) === kind);
        const cont = items.filter((x) => x.kind === 1);
        return { by, cont, crane: cont.map((x) => ({ p: [x.p[0], x.p[1] - 0.0006] })) };
      });
      for (const kind of Object.keys(P31_MODEL)) add(M3D.facility('fac3d-p31-' + kind, P31_MODEL[kind], g.by[kind], zoom, { k: P31_MODEL[kind] === 'warehouse' ? 0.7 : 1 }));
      add(M3D.facility('fac3d-p31-yard', 'container_yard', g.cont, zoom));
      add(M3D.facility('fac3d-p31-crane', 'port_crane', g.crane, zoom));
    }
    if (U.c28 && U.types.air) add(M3D.facility('fac3d-air', 'air_cargo_terminal', cached('air', [U.c28], () => U.c28.items.map(([lon, lat]) => ({ p: [lon, lat] }))), zoom));
    if (U.types.osm && U.osm) {
      const skip = U.osm.types.indexOf('truck_stop');
      if (!U.osmWh) U.osmWh = U.osm.items.filter((d) => d[2] !== skip);
      add(M3D.facility('fac3d-osm', 'warehouse', U.osmWh, zoom, { getP: (d) => [d[0], d[1]], k: 0.4 })); // OSM の建物は小さいものが多いので、モデルを 0.4 倍にして重なりを減らす（実際の形ではない）
    }
    return out;
  }

  // 比べる画面の施設（拡大 12 以上の 3D だけ。点やアイコンは出さない）。データは最初に必要になったときに読み込む（「施設」の切り替えとは無関係）
  function compareFac(zoom) {
    if (!M3D.facilityOn(zoom)) return [];
    if (!U.facLoading && (!U.c28 || !U.osm || U.p31 === undefined)) { U.facLoading = true; loadFacilities().finally(() => { U.facLoading = false; }); }
    return fac3d(zoom);
  }

  /* ------------------------------------------------------------ 説明（地図の上にあわせたとき）と選択 */

  const nameOf = (i) => `${S.idx.prefs[i]} ${S.idx.names[i]}`;
  function stateText(code) { return U.d.state_names[code]; }

  // 施設の説明に添えるサムネイル（SPEC §19.6）。コンテナターミナルは港のクレーン
  const HOVER_MODEL = { 5: 'warehouse', 7: 'warehouse', 4: 'warehouse', 3: 'terminal', 2: 'air_cargo_terminal', 1: 'port_crane' };
  function hoverText(info) {
    const o = info && info.object, id = info && info.layer && info.layer.id;
    if (!o || !id) return null;
    const d = U.d;
    if (id === 'mu-bal' || id === 'mu-conc' || id === 'mu-col') {
      const i = id === 'mu-col' ? o.i : o.properties.i;
      if (i < 0) return null;
      if (id === 'mu-bal') {
        if (d.bal[i] === null) return { t: nameOf(i), s: `灰色: ${d.reason[i] || '数字がありません'}` };
        return { t: nameOf(i), s: `推定 B ${signed(d.bal[i])} t（出荷 ${n0(d.out[i])} − 入荷 ${n0(d.in[i])}）。推定です` };
      }
      return { t: nameOf(i), s: concLine(i) };
    }
    if (id === 'fac-p31') return { t: o.name, s: `${GLYPH[o.kind][0]}・${o.address || '住所なし'}・2013 年（国土数値情報 P31）`, m: HOVER_MODEL[o.kind] };
    if (id === 'fac-air') return { t: o.name, s: `空港（${o.status}）・国土数値情報 C28（2021）`, m: 'air_cargo_terminal' };
    if (id === 'fac-osm') return { t: '倉庫・物流施設（OSM）', s: U.osm.tags[U.osm.types[o[2]]] || '', m: 'warehouse' };
    if (id === 'fac-osm-agg') return { t: nameOf(o.i), s: `OSM の倉庫・物流施設 ${n0(o.n)} 件（拡大 11 以上で 1 件ずつ出します）` };
    return null;
  }
  function concLine(i) {
    const d = U.d;
    if (d.cst[i] !== 0) {
      const part = (c, nm) => (c[2] === 0 ? `${nm} ${n0(c[1])} 人` : `${nm} ${stateText(c[2])}`);
      return `灰色: 従業者数を出せません（${part(d.c44[i], '運送業')}、${part(d.c47[i], '倉庫業')}）`;
    }
    return `従業者 ${n0(d.cemp[i])} 人（運送業 ${n0(d.c44[i][1])}、倉庫業 ${n0(d.c47[i][1])}）／ 面積あたり ${d.cdens[i].toLocaleString('ja-JP', { maximumFractionDigits: 1 })} 人/km²`;
  }

  function click(info) {
    const o = info && info.object, id = info && info.layer && info.layer.id;
    if (!o || !id) return false;
    if (id === 'mu-bal' || id === 'mu-conc' || id === 'mu-col' || id === 'fac-osm-agg') {
      const i = id === 'mu-col' ? o.i : id === 'fac-osm-agg' ? o.i : o.properties.i;
      if (i >= 0) select(i);
      return true;
    }
    return false;
  }

  function select(i) {
    U.sel = i;
    U.selFeat = S.byIndex.get(i) || null;
    renderDetail();
    foldRight(false);
  }
  function deselect() {
    U.sel = -1;
    U.selFeat = null;
    renderDetail();
  }

  function renderDetail() {
    const box = q('#m-muni'), i = U.sel;
    document.body.classList.toggle('m-has-muni', i >= 0);
    if (i < 0) { box.hidden = true; box.innerHTML = ''; return; }
    const d = U.d, pref = S.idx.prefs[i], pf = d.prefs[pref];
    const gray = d.bal[i] === null;
    const w = (v, tot) => (v === null || !tot ? '—' : `${(v / tot * 100).toFixed(2)}%`);
    const num = (v, u = '') => (v === null || v === undefined ? '—' : `${n0(v)}${u}`);
    const c44 = d.c44[i], c47 = d.c47[i];
    const conc = (c, nm) => `<tr><td>${nm}</td><td class="num">${c[0] === null ? '—' : n0(c[0])}</td><td class="num">${c[2] === 0 ? n0(c[1]) : `<span class="mu-gray-t">${stateText(c[2])}</span>`}</td></tr>`;
    box.innerHTML =
      `<div class="mp-top"><div class="mp-title"><span>${esc(S.idx.names[i])}</span><small class="mu-pref">${esc(pref)}</small></div>` +
      `<button class="mp-x" data-act="close" aria-label="市区町村の選択を外す">&#10005;</button></div>` +
      `<p class="mu-est">推定です。県の値を従業者数と人口で分けた目安で、実際の流動ではありません。</p>` +
      (gray ? `<p class="mu-gray-r"><i class="sw sw-gray"></i>灰色（0 ではありません）: ${esc(d.reason[i] || '数字がありません')}</p>` : '') +
      `<div class="mp-stats mu-stats3"><div><span>出荷 Out</span><b class="num">${num(d.out[i])}</b><small>t</small></div>` +
      `<div><span>入荷 In</span><b class="num">${num(d.in[i])}</b><small>t</small></div>` +
      `<div><span>推定の差 B</span><b class="num">${d.bal[i] === null ? '—' : signed(d.bal[i])}</b><small>t</small></div></div>` +
      `<div class="mp-stats mu-stats4"><div><span>出荷の重み</span><b class="num">${w(d.out[i], pf[0])}</b></div>` +
      `<div><span>入荷の重み</span><b class="num">${w(d.in[i], pf[1])}</b></div>` +
      `<div><span>製造・運送・倉庫の従業者</span><b class="num">${num(d.emp[i])}</b><small>人</small></div>` +
      `<div><span>人口（2020 年）</span><b class="num">${num(d.pop[i])}</b><small>人</small></div></div>` +
      `<p class="mu-un">${esc(pref)}の未配分: 出荷 ${n0(pf[2])} t、入荷 ${n0(pf[3])} t（${esc(pref)}の出荷 ${n0(pf[0])} t・入荷 ${n0(pf[1])} t のうち、重みを出せない市区町村の分。配らずに残しています。灰色 ${pf[5]} 件）</p>` +
      `<table class="mu-tbl"><thead><tr><th>物流の集積（令和6年）</th><th>事業所数</th><th>従業者数</th></tr></thead><tbody>${conc(c44, '道路貨物運送業（44）')}${conc(c47, '倉庫業（47）')}</tbody></table>` +
      `<p class="mu-tn">面積 ${d.area[i] === null ? '—' : d.area[i].toLocaleString('ja-JP')} km²（簡略化した行政区域の面積）。量は物流センサス 2021 の 3 日間のトンで、年間ではありません。</p>`;
    box.hidden = false;
  }

  /* ------------------------------------------------------------ 凡例・操作の描画 */

  function renderBalHead() {
    const mu = isMuni();
    q('#mlg-bal-t').textContent = mu ? '市区町村の出荷と入荷の差（推定）' : '県の出荷と入荷の差';
    q('#mlg-bal').classList.toggle('is-muni', mu);
    q('#mlg-gray').hidden = !mu;
    q('#mlg-bal').hidden = concChoro();
  }

  function renderAll() {
    document.querySelectorAll('#m-scope button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.scope === U.scope)));
    q('#mu-notice').hidden = !isMuni();
    document.body.classList.toggle('mu-on', isMuni());
    renderBalHead();
    renderConc();
    renderFac();
    renderAttr();
  }

  function fmtEdge(v) {
    if (U.metric === 'dens') return v >= 100 ? n0(v) : v >= 10 ? v.toFixed(0) : v.toFixed(1);
    return v >= 1e4 ? `${(v / 1e4).toFixed(1)}万` : n0(v);
  }

  function renderConc() {
    const box = q('#mlg-conc');
    box.hidden = !U.conc;
    if (!U.conc) return;
    const edges = U.col3d ? U.d.edges.emp : concEdges(), n = edges.length - 1;
    const metric = U.col3d ? 'emp' : U.metric;
    const cells = Array.from({ length: n }, (_, k) => `<i style="background:${rgbCss(mix(CONC[0], CONC[1], k / Math.max(1, n - 1)))}"></i>`).join('');
    const ticks = edges.map((v, k) => {
      const tf = k === 0 ? 'none' : k === n ? 'translateX(-100%)' : 'translateX(-50%)';
      const lab = metric === 'dens' ? (v >= 100 ? n0(v) : v >= 10 ? v.toFixed(0) : v.toFixed(1)) : (v >= 1e4 ? `${(v / 1e4).toFixed(1)}万` : n0(v));
      return `<span style="left:${(k / n) * 100}%;transform:${tf}">${lab}</span>`;
    }).join('');
    box.innerHTML =
      `<div class="mlg-t">物流の集積<small>運送業（44）＋ 倉庫業（47）の従業者</small></div>` +
      `<div class="m-seg m-seg-s" id="cc-metric" role="group" aria-label="集積の測り方"><button data-metric="dens" aria-pressed="${U.metric === 'dens'}">面積あたり（人/km²）</button>` +
      `<button data-metric="emp" aria-pressed="${U.metric === 'emp'}">実数（従業者数）</button></div>` +
      `<label class="mlg-chk"><input type="checkbox" id="cc-3d"${U.col3d ? ' checked' : ''}><span>3D の柱で見る（高さ = 従業者数）</span></label>` +
      `<div class="mlg-bar mlg-bar-c">${cells}</div><div class="mlg-ticks mlg-ticks-c">${ticks}</div>` +
      `<div class="mlg-cap mlg-cap-c"><span>${U.col3d ? '柱の色と高さ: 従業者数（人）' : metric === 'dens' ? '色: 1 km² あたりの従業者数（人）。7 階級の分位数' : '色: 従業者数（人）。7 階級の分位数'}</span></div>` +
      `<div class="mlg-gray"><i class="sw sw-gray"></i><span>灰色 = 秘匿・数字なし（触れると理由が出ます。0 とは扱いません）</span></div>` +
      (isMuni() && !U.col3d ? `<p class="mlg-faint">市区町村（推定）の塗りは、集積を塗っている間は隠れます。柱で見ると重ねて見られます。</p>` : '');
  }
  function onConcClick(e) {
    const b = e.target.closest('button[data-metric]');
    if (!b) return;
    U.metric = b.dataset.metric;
    renderConc();
    syncHash();
  }

  const glyphSvg = (k, c) => `<svg class="fg" viewBox="0 0 64 64" width="14" height="14" aria-hidden="true"><g fill="${rgbCss(c)}">${GLYPH[k][1].replace('stroke="#fff"', `stroke="${rgbCss(c)}"`)}</g></svg>`;

  function renderFac() {
    const box = q('#mlg-fac');
    box.hidden = !U.fac;
    if (!U.fac) return;
    const chip = (k, label, count, off) =>
      `<button class="fac-chip" data-fac="${k}" aria-pressed="${!off && !!U.types[k]}"${off ? ' disabled' : ''}>${glyphSvg(k, GLYPH[k][2])}<span>${label}</span>${count === null ? '' : `<em class="num">${n0(count)}</em>`}</button>`;
    const n31 = {};
    if (U.p31Items) for (const x of U.p31Items) n31[x.kind] = (n31[x.kind] || 0) + 1;
    const noP31 = U.p31 === null;
    const osmN = U.d.osm_types.length ? U.d.osm.reduce((a, c) => a + (c ? c.reduce((x, y) => x + y, 0) : 0), 0) : 0;
    box.innerHTML =
      `<div class="mlg-t">施設<small>色と形で見分けられます</small></div>` +
      `<div class="fac-chips">${P31_ORDER.map((k) => chip(k, GLYPH[k][0], noP31 || U.p31 === undefined ? null : (n31[k] || 0), noP31)).join('')}` +
      `${chip('air', '空港', U.c28 ? U.c28.items.length : null, false)}` +
      `<button class="fac-chip fac-osm" data-fac="osm" aria-pressed="${!!U.types.osm}"><i class="fac-dot"></i><span>倉庫・物流施設（OSM）</span><em class="num">${n0(osmN)}</em></button></div>` +
      (noP31 ? `<p class="fac-msg">物流拠点（国土数値情報 P31）は再配布できないため、この版では表示しません</p>` : '') +
      `<details class="mlg-more"><summary>表示の条件</summary><p>倉庫・物流施設は、拡大 11 以上で 1 件ずつ、それより広いと市区町村ごとの件数を丸で出します。トラックターミナル・コンテナターミナル・鉄道貨物駅・航空貨物ターミナル・卸売市場・保税地域は 2013 年のデータで、いまとは違う場合があります。</p></details>`;
  }
  function onFacClick(e) {
    const b = e.target.closest('button[data-fac]');
    if (!b || b.disabled) return;
    const k = b.dataset.fac === 'air' || b.dataset.fac === 'osm' ? b.dataset.fac : Number(b.dataset.fac);
    U.types[k] = !U.types[k];
    renderFac();
    renderAttr();
    syncHash();
  }

  function renderAttr() {
    const lines = [];
    if (isMuni() || U.conc) lines.push(ATTR.estat);
    if (U.fac) {
      if (U.p31Items && P31_ORDER.some((k) => U.types[k])) lines.push(ATTR.p31);
      if (U.types.air) lines.push(ATTR.c28);
      if (U.types.osm) lines.push(ATTR.osm);
    }
    const box = q('#mlg-attr');
    box.hidden = !lines.length;
    box.innerHTML = lines.map((t) => `<div>${esc(t)}</div>`).join('');
  }

  /* ------------------------------------------------------------ 状態の URL */

  function syncHash() {
    if (S.view !== 'market') return;
    const p = new URLSearchParams(location.hash.slice(1));
    const set = (k, v) => { if (v) p.set(k, v); else p.delete(k); };
    set('sc', U.scope === 'muni' ? 'muni' : '');
    set('cc', U.conc ? (U.metric === 'emp' ? 'emp' : 'dens') : '');
    set('c3', U.conc && U.col3d ? '1' : '');
    set('fc', U.fac ? '1' : '');
    history.replaceState(null, '', '#' + p.toString());
  }
  function readHash(p) {
    if (p.get('sc') === 'muni') U.scope = 'muni';
    if (p.get('cc')) { U.conc = true; U.metric = p.get('cc') === 'emp' ? 'emp' : 'dens'; }
    if (p.get('c3') === '1' && U.conc) U.col3d = true;
    if (p.get('fc') === '1') U.fac = true;
  }
  function afterStart() {
    document.querySelector('.tgl[data-toggle="conc"]').setAttribute('aria-pressed', String(U.conc));
    document.querySelector('.tgl[data-toggle="fac"]').setAttribute('aria-pressed', String(U.fac));
    if (U.col3d) tilt(true);
    if (U.conc || U.fac) fold('m-calc', true);
    if (U.fac) loadFacilities();
    renderAll();
    Market.renderLegend();
  }

  return {
    load, init, afterStart, readHash, isMuni, prefFilled, maxBal, balColor, renderBalHead, under, over, compareFac, hoverText, click, setScope, setConc, setCol3d, setFac,
    setActive, select, deselect, state: U, DISCLAIMER, ATTR,
  };
})();
