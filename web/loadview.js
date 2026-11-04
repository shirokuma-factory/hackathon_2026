'use strict';
/* 積付けの 3D 表示（SPEC §20）。配置は /api/loadplan（Python の src/shipcompare/loadplan.py）が返す。
   ここでは別の deck.Deck（OrbitView）に荷室と荷を描き、置く順に落とす動きを付けるだけで、位置や判定は計算しない。
   単位は API が mm、画面の座標は m（桁を小さくして描画を安定させる）。 */
const LoadView = (() => {
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v, d = 0) => Number(v).toLocaleString('ja-JP', { maximumFractionDigits: d });

  // 品目の色（荷物の行ごと）。暗い背景で区別でき、○△×の色（緑・黄・赤）とは別にしてある
  const PALETTE = [[92, 200, 255], [240, 182, 78], [167, 139, 250], [255, 143, 171], [120, 220, 200], [217, 199, 160], [143, 163, 184], [232, 238, 248]];
  const colorOf = (i) => PALETTE[i % PALETTE.length];
  const VERDICT = {
    '○': { cls: 'ok', text: '全部載ります' },
    '△': { cls: 'warn', text: '載りますが、数値が目安か余裕が小さい条件です' },
    '×': { cls: 'ng', text: '載りません' },
  };
  const ANIM_MS = 2000, DROP_MS = 520;
  const EDGES = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]];

  const V = {
    shipment: null, container: 'box_2t', stackable: true, res: null, deck: null, raf: 0, t0: 0, error: '',
    token: 0, mesh: null, busy: false, open: false,
  };

  // 0..1 の単位立方体（原点が角）。法線つき
  function cubeMesh() {
    if (V.mesh) return V.mesh;
    const faces = [
      [[0, 0, 0], [0, 1, 0], [0, 1, 1], [0, 0, 1], [-1, 0, 0]], [[1, 0, 0], [1, 0, 1], [1, 1, 1], [1, 1, 0], [1, 0, 0]],
      [[0, 0, 0], [0, 0, 1], [1, 0, 1], [1, 0, 0], [0, -1, 0]], [[0, 1, 0], [1, 1, 0], [1, 1, 1], [0, 1, 1], [0, 1, 0]],
      [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, -1]], [[0, 0, 1], [0, 1, 1], [1, 1, 1], [1, 0, 1], [0, 0, 1]],
    ];
    const pos = [], nor = [], idx = [];
    faces.forEach((f, k) => {
      for (let j = 0; j < 4; j++) { pos.push(...f[j]); nor.push(...f[4]); }
      idx.push(k * 4, k * 4 + 1, k * 4 + 2, k * 4, k * 4 + 2, k * 4 + 3);
    });
    V.mesh = {
      attributes: { positions: { size: 3, value: new Float32Array(pos) }, normals: { size: 3, value: new Float32Array(nor) } },
      indices: { size: 1, value: new Uint16Array(idx) },
    };
    return V.mesh;
  }

  /* ---------------------------------------------------------------- 画面の骨組み */
  function build() {
    const root = $('#loadview');
    root.innerHTML = `
      <div class="lv-back" data-lv="close"></div>
      <div class="lv-panel glass" role="dialog" aria-modal="true" aria-label="積み方を 3D で見る">
        <header class="lv-head"><b>積み方を 3D で見る</b><span class="lv-sub" id="lv-sub"></span>
          <button type="button" class="lv-x" data-lv="close" aria-label="閉じる">&#10005;</button></header>
        <div class="lv-body">
          <div class="lv-stage">
            <div class="lv-canvas" id="lv-canvas"></div>
            <div class="lv-msg" id="lv-msg" role="status"></div>
            <div class="lv-tools">
              <button type="button" class="lv-btn" data-lv="replay">もう一度落とす</button>
              <button type="button" class="lv-btn" data-lv="reset">視点を戻す</button>
              <span class="lv-hint">ドラッグで回転、ホイールで拡大・縮小、右ドラッグで移動</span>
            </div>
          </div>
          <aside class="lv-side" id="lv-side"></aside>
        </div>
      </div>`;
    root.addEventListener('click', (e) => {
      const b = e.target.closest('[data-lv]');
      if (!b) return;
      const a = b.dataset.lv;
      if (a === 'close') close();
      else if (a === 'replay') replay();
      else if (a === 'reset') resetView();
    });
    root.addEventListener('change', (e) => {
      if (e.target.id === 'lv-cont') { V.container = e.target.value; load(); }
      else if (e.target.id === 'lv-stack') { V.stackable = e.target.checked; load(); }
    });
    addEventListener('keydown', (e) => { if (e.key === 'Escape' && V.open) close(); });
  }

  /* ---------------------------------------------------------------- 開く・閉じる・読み込み */
  function open(shipment) {
    if (!$('#loadview .lv-panel')) build();
    V.shipment = JSON.parse(JSON.stringify(shipment || { items: [] }));
    V.open = true;
    $('#loadview').hidden = false;
    document.body.classList.add('lv-open');
    load();
  }

  function close() {
    V.open = false;
    cancelAnimationFrame(V.raf);
    if (V.deck) { V.deck.finalize(); V.deck = null; }
    $('#loadview').hidden = true;
    document.body.classList.remove('lv-open');
  }

  async function load() {
    const my = ++V.token;
    V.error = '';
    setMsg('計算しています');
    try {
      const res = await Work.api('/api/loadplan', { shipment: V.shipment, container: V.container, stackable: V.stackable });
      if (my !== V.token || !V.open) return;
      V.res = res;
      V.container = res.container.id;
      renderSide();
      setMsg('');
      startDeck();
      replay();
    } catch (e) {
      if (my !== V.token || !V.open) return;
      V.error = e.message;
      V.res = V.res || null;
      renderSide();
      setMsg(e.message, true);
      if (V.deck) V.deck.setProps({ layers: [] });
    }
  }

  function setMsg(t, err) {
    const m = $('#lv-msg');
    m.textContent = t;
    m.classList.toggle('err', !!err);
    m.hidden = !t;
  }

  /* ---------------------------------------------------------------- 横のパネル */
  // 荷室の ID → サムネイルのモデル名（SPEC §19.6）。JITBOX のボックスは該当するモデルがないので出さない
  const CONT_MODEL = {
    kei_van: 'kei_van', box_2t_short: 'box_2t', box_2t: 'box_2t', wing_4t: 'truck_4t', wing_10t: 'truck',
    jr_12ft: 'container_12ft', jr_31ft: 'container_31ft', roll_cage: 'roll_cage', rb_upper_1700: 'truck_4t', rb_upper_2000: 'truck_4t',
  };
  function renderSide() {
    const side = $('#lv-side');
    const r = V.res;
    if (!r) { side.innerHTML = `<p class="lv-err">${esc(V.error)}</p>`; return; }
    const c = r.container, s = r.summary, vd = VERDICT[s.verdict] || VERDICT['×'];
    const opts = r.containers.map((x) => `<option value="${esc(x.id)}"${x.id === c.id ? ' selected' : ''}>${esc(x.name)}</option>`).join('');
    const items = r.items.map((it, i) => {
      const placed = r.placements.filter((p) => p.item === i).length;
      return `<li><i style="background:rgb(${colorOf(i).join(',')})"></i><span>荷物${i + 1}行目 ${num(it.length_cm, 1)}×${num(it.width_cm, 1)}×${num(it.height_cm, 1)}cm・${num(it.weight_kg, 1)}kg</span><b class="num">${placed} / ${it.count}</b></li>`;
    }).join('');
    const un = r.unplaced.length
      ? `<ul class="lv-un">${r.unplaced.map((u) => `<li><b>荷物${u.item + 1}行目 ${u.count}個</b><span>${esc(u.reason)}</span></li>`).join('')}</ul>`
      : '<p class="lv-none">置けなかった荷はありません</p>';
    const bar = (pct) => `<div class="lv-bar"><i style="width:${Math.min(100, pct)}%"></i></div>`;
    $('#lv-sub').textContent = `${c.name}・${num(c.length_mm)}×${num(c.width_mm)}×${num(c.height_mm)}mm`;
    side.innerHTML = `
      <label class="lbl" for="lv-cont">荷室</label>
      <select id="lv-cont" class="ctl sm">${opts}</select>
      ${typeof M3D !== 'undefined' && CONT_MODEL[c.id] ? `<div class="lv-thumb">${M3D.thumb(CONT_MODEL[c.id], 144, 90)}</div>` : ''}
      <p class="lv-spec num">内寸 ${num(c.length_mm)}×${num(c.width_mm)}×${num(c.height_mm)}mm・最大積載 ${num(c.payload_kg)}kg
        <span class="lv-basis b-${esc(c.basis)}">${esc(c.basis)}</span></p>
      ${c.sellable_slot ? '' : '<p class="lv-warn">推定・販売枠ではありません。積付けの属性として見るための空間です。</p>'}
      <details class="lv-det"><summary>この荷室の数値の根拠</summary><p>${esc(c.note)}</p></details>
      <label class="lv-chk"><input type="checkbox" id="lv-stack"${V.stackable ? ' checked' : ''}>積み重ねてよい（全部の荷）</label>
      <div class="lv-verdict ${vd.cls}"><span class="lv-mark">${esc(s.verdict)}</span><span>${vd.text}${s.weight_ok ? '' : `（最大積載量を ${num(s.weight_over_kg, 1)}kg 超えます）`}</span></div>
      <dl class="lv-nums num">
        <div><dt>置いた数</dt><dd>${num(s.placed)} / ${num(s.requested)} 個</dd></div>
        <div><dt>置けなかった数</dt><dd class="${s.unplaced_count ? 'bad' : ''}">${num(s.unplaced_count)} 個</dd></div>
        <div><dt>床の使用率</dt><dd>${num(s.floor_area_pct, 1)}%</dd>${bar(s.floor_area_pct)}</div>
        <div><dt>容積の使用率</dt><dd>${num(s.volume_pct, 1)}%</dd>${bar(s.volume_pct)}</div>
        <div><dt>重さ</dt><dd class="${s.weight_ok ? '' : 'bad'}">${num(s.total_weight_kg, 1)} / ${num(s.payload_kg)}kg</dd>${bar(s.total_weight_kg * 100 / s.payload_kg)}</div>
        <div><dt>積み重ね</dt><dd>${s.stacked ? 'あり' : 'なし'}</dd></div>
      </dl>
      <div class="lv-h">置けなかった荷</div>${un}
      <div class="lv-h">荷物の色</div><ul class="lv-items">${items}</ul>
      <p class="lv-note">${esc(s.note)}</p>`;
  }

  /* ---------------------------------------------------------------- 描画 */
  function viewState() {
    const c = V.res.container, L = c.length_mm / 1000, W = c.width_mm / 1000, H = c.height_mm / 1000;
    const cv = $('#lv-canvas');
    const px = Math.max(240, Math.min(cv.clientWidth * 0.62, cv.clientHeight * 1.0));
    const span = Math.sqrt(L * L + W * W + H * H);
    return {
      target: [L / 2, W / 2, H / 2], rotationX: 28, rotationOrbit: -38, zoom: Math.log2(px / span),
      minZoom: Math.log2(60 / span), maxZoom: Math.log2(4000 / span),
    };
  }

  function startDeck() {
    const vs = viewState();
    if (!V.deck) {
      V.deck = new deck.Deck({
        parent: $('#lv-canvas'),
        views: new deck.OrbitView({ orbitAxis: 'Z', fovy: 32 }),
        initialViewState: vs,
        controller: { inertia: 200 },
        layers: [],
        getTooltip: ({ object }) => {
          if (!object || object.item === undefined) return null;
          const it = V.res.items[object.item];
          return { html: `荷物${object.item + 1}行目 ${num(it.length_cm, 1)}×${num(it.width_cm, 1)}×${num(it.height_cm, 1)}cm・${num(it.weight_kg, 1)}kg${object.rotated ? '（床で90度回転）' : ''}`,
            style: { background: '#0d141f', color: '#e8eef8', border: '1px solid rgba(255,255,255,.16)', borderRadius: '8px', font: '12px "Noto Sans JP", sans-serif', padding: '6px 10px' } };
        },
      });
    } else {
      V.deck.setProps({ initialViewState: vs });
    }
  }

  function resetView() {
    if (!V.deck || !V.res) return;
    V.deck.setProps({ initialViewState: { ...viewState() } });
  }

  const easeOut = (p) => 1 - Math.pow(1 - p, 3);
  const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

  function replay() {
    cancelAnimationFrame(V.raf);
    if (!V.deck || !V.res) return;
    V.t0 = performance.now();
    const total = reduced() ? 0 : ANIM_MS;
    const tick = (now) => {
      const t = now - V.t0;
      drawAt(total === 0 ? Infinity : t);
      if (total !== 0 && t < total + 50) V.raf = requestAnimationFrame(tick);
    };
    V.raf = requestAnimationFrame(tick);
  }

  // t ミリ秒の時点の層。i 番目の荷は i / (n-1) の割合の時刻に落とし始め、DROP_MS で着く
  function drawAt(t) {
    const r = V.res, c = r.container, n = r.placements.length;
    const dropH = c.height_mm / 1000 + 0.6;
    const span = Math.max(0, ANIM_MS - DROP_MS);
    const vis = [];
    r.placements.forEach((p, i) => {
      const start = n > 1 ? span * i / (n - 1) : 0;
      const q = Math.max(0, Math.min(1, (t - start) / DROP_MS));
      if (q <= 0 && t !== Infinity) return;
      const e = t === Infinity ? 1 : easeOut(q);
      vis.push({
        ...p, fx: p.x / 1000, fy: p.y / 1000, fz: p.z / 1000 + (1 - e) * dropH,
        fl: p.l / 1000, fw: p.w / 1000, fh: p.h / 1000, a: Math.round(255 * Math.min(1, q * 4 || 1)),
      });
    });
    V.deck.setProps({ layers: buildLayers(vis, c) });
  }

  function boxEdges(b) {
    const x = b.fx, y = b.fy, z = b.fz, l = b.fl, w = b.fw, h = b.fh;
    const pts = [[x, y, z], [x + l, y, z], [x + l, y + w, z], [x, y + w, z], [x, y, z + h], [x + l, y, z + h], [x + l, y + w, z + h], [x, y + w, z + h]];
    return EDGES.map(([i, j]) => ({ s: pts[i], t: pts[j] }));
  }

  function buildLayers(vis, c) {
    const L = c.length_mm / 1000, W = c.width_mm / 1000, H = c.height_mm / 1000;
    const corners = [[0, 0, 0], [L, 0, 0], [L, W, 0], [0, W, 0], [0, 0, H], [L, 0, H], [L, W, H], [0, W, H]];
    const faces = [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4], [3, 2, 6, 7], [0, 3, 7, 4], [1, 2, 6, 5]].map((f) => ({ p: f.map((i) => corners[i]) }));
    const contEdges = EDGES.map(([i, j]) => ({ s: corners[i], t: corners[j] }));
    const step = Math.max(L, W) > 5 ? 1 : 0.5;
    const grid = [];
    for (let x = step; x < L - 1e-6; x += step) grid.push({ s: [x, 0, 0.002], t: [x, W, 0.002] });
    for (let y = step; y < W - 1e-6; y += step) grid.push({ s: [0, y, 0.002], t: [L, y, 0.002] });
    const noDepthWrite = { depthWriteEnabled: false };
    const labels = [
      { p: [L / 2, -0.12 * Math.max(W, 1), 0], t: `L ${num(c.length_mm)}mm` },
      { p: [L + 0.12 * Math.max(W, 1), W / 2, 0], t: `W ${num(c.width_mm)}mm` },
      { p: [0, -0.12 * Math.max(W, 1), H / 2], t: `H ${num(c.height_mm)}mm` },
    ];
    return [
      new deck.LineLayer({ id: 'lv-grid', data: grid, getSourcePosition: (d) => d.s, getTargetPosition: (d) => d.t, getColor: [255, 255, 255, 34], getWidth: 1, widthUnits: 'pixels' }),
      new deck.SimpleMeshLayer({
        id: 'lv-box', data: vis, mesh: cubeMesh(), pickable: true, sizeScale: 1,
        getPosition: (d) => [d.fx, d.fy, d.fz], getScale: (d) => [d.fl, d.fw, d.fh],
        getColor: (d) => [...colorOf(d.item), d.a], material: { ambient: 0.5, diffuse: 0.7, shininess: 20, specularColor: [50, 50, 50] },
        updateTriggers: { getPosition: vis.length ? vis.map((d) => d.fz).join(',') : 0, getColor: vis.map((d) => d.a).join(',') },
      }),
      new deck.LineLayer({
        id: 'lv-box-edge', data: vis.flatMap(boxEdges), getSourcePosition: (d) => d.s, getTargetPosition: (d) => d.t,
        getColor: [8, 12, 20, 170], getWidth: 1, widthUnits: 'pixels',
      }),
      new deck.SolidPolygonLayer({
        id: 'lv-faces', data: faces, getPolygon: (d) => d.p, getFillColor: [92, 200, 255, 20], filled: true, extruded: false,
        parameters: noDepthWrite,
      }),
      new deck.LineLayer({ id: 'lv-cont-edge', data: contEdges, getSourcePosition: (d) => d.s, getTargetPosition: (d) => d.t, getColor: [140, 215, 255, 230], getWidth: 2, widthUnits: 'pixels' }),
      new deck.TextLayer({
        id: 'lv-dim', data: labels, getPosition: (d) => d.p, getText: (d) => d.t, getSize: 12, getColor: [143, 157, 180, 255],
        fontFamily: '"Noto Sans JP", sans-serif', background: false, getTextAnchor: 'middle', getAlignmentBaseline: 'center',
      }),
    ];
  }

  return { open, close, state: V };
})();
window.LoadView = LoadView;
