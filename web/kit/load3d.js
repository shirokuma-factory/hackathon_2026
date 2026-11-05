'use strict';
/* 荷室と荷の 3D の積付けの表示（SPEC §36.1。元にする今のコード: web/loadview.js、web/models3d.js）。
   配置の結果（placement。src/shipcompare/loadplan.py の結果の形）を受け取って描くだけで、置く位置や載るかどうかは計算しません。
   単位: データは mm・kg・cm（loadplan.py と同じ）。画面の座標は m に直して描きます（桁を小さくして描画を安定させる）。
   3D は deck.gl の OrbitView（今のアプリと同じ描き方）。three.js は使いません。
   操作: ドラッグで回転、ホイールで拡大・縮小、右ドラッグで移動、「視点を戻す」「もう一度落とす」。
   3D を使えない環境や、使いたくない人のために、置いた荷の位置を表でも出します。動きを減らす設定のときは、落とす動きを付けず最初から並べます。
   使い方: const v = Kit.load3d.mount(container, { vehicle, placement }, config); v.update(newData); v.destroy(); */
(function (Kit) {
  const { esc, merge, finite, uid } = Kit.util;

  const DEFAULTS = {
    height: 420, // 3D の領域の高さ（px）
    palette: [[92, 200, 255], [240, 182, 78], [167, 139, 250], [255, 143, 171], [120, 220, 200], [217, 199, 160], [143, 163, 184], [232, 238, 248]],
    animate: true,
    animMs: 2000, dropMs: 520,
    theme: 'dark',
    texts: {
      title: '積み方の 3D',
      replay: 'もう一度落とす', reset: '視点を戻す', hint: 'ドラッグで回転、ホイールで拡大・縮小、右ドラッグで移動',
      verdict: { '○': ['ok', '全部載ります'], '△': ['warn', '載りますが、数値が目安か余裕が小さい条件です'], '×': ['ng', '載りません'] },
      noWebgl: '3D を表示できません。下の表で配置を確認してください。',
    },
    table: true,
  };

  const EDGES = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
  const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const easeOut = (p) => 1 - Math.pow(1 - p, 3);
  const n1 = (v) => Kit.fmt.num(v, 1);

  let MESH = null;
  // 0..1 の単位立方体（原点が角）。法線つき
  function cubeMesh() {
    if (MESH) return MESH;
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
    MESH = { attributes: { positions: { size: 3, value: new Float32Array(pos) }, normals: { size: 3, value: new Float32Array(nor) } }, indices: { size: 1, value: new Uint16Array(idx) } };
    return MESH;
  }

  // 荷室の内寸（mm）。vehicle があればその内寸、なければ placement.container
  function boxOf(d) {
    const v = d && d.vehicle, c = d && d.placement && d.placement.container;
    const s = v && v.inner ? { l: v.inner.length_mm, w: v.inner.width_mm, h: v.inner.height_mm } : c ? { l: c.length_mm, w: c.width_mm, h: c.height_mm } : null;
    return s && [s.l, s.w, s.h].every((x) => finite(x) && x > 0) ? s : null;
  }

  function mount(container, data, config) {
    if (typeof deck === 'undefined') throw new Error('Kit.load3d: deck.gl を先に読み込んでください');
    if (!container || !container.appendChild) throw new Error('Kit.load3d.mount の 1 つ目は要素です');
    const cfg = merge(DEFAULTS, config);
    Kit.ensureStyle();
    const root = document.createElement('section');
    root.className = 'kit-3d';
    root.setAttribute('data-kit', 'load3d');
    root.setAttribute('aria-label', cfg.texts.title);
    root.innerHTML = `<div class="kit-3d-stage" data-k="stage" style="--kit-3d-h:${cfg.height}px"><div class="kit-3d-msg" data-k="msg" role="status" hidden></div></div>
      <div class="kit-3d-tools"><button type="button" class="kit-btn" data-k="replay">${esc(cfg.texts.replay)}</button><button type="button" class="kit-btn" data-k="reset">${esc(cfg.texts.reset)}</button><small>${esc(cfg.texts.hint)}</small></div>
      <div class="kit-3d-side" data-k="side"></div>`;
    container.appendChild(root);
    const $k = (k) => root.querySelector(`[data-k="${k}"]`);
    const stage = $k('stage'), msg = $k('msg'), side = $k('side');
    const st = { data: null, deck: null, raf: 0, t0: 0, gone: false };

    const setMsg = (t) => { msg.textContent = t || ''; msg.hidden = !t; };
    const colorOf = (i) => cfg.palette[i % cfg.palette.length];

    function viewState() {
      const b = boxOf(st.data), L = b.l / 1000, W = b.w / 1000, H = b.h / 1000;
      const px = Math.max(240, Math.min(stage.clientWidth * 0.62, stage.clientHeight * 1.0));
      const span = Math.sqrt(L * L + W * W + H * H);
      return { target: [L / 2, W / 2, H / 2], rotationX: 28, rotationOrbit: -38, zoom: Math.log2(px / span), minZoom: Math.log2(60 / span), maxZoom: Math.log2(4000 / span) };
    }

    function ensureDeck() {
      if (st.deck) { st.deck.setProps({ initialViewState: viewState() }); return true; }
      try {
        st.deck = new deck.Deck({
          parent: stage, width: '100%', height: '100%',
          views: new deck.OrbitView({ orbitAxis: 'Z', fovy: 32 }), initialViewState: viewState(), controller: { inertia: 200 }, layers: [],
          getTooltip: ({ object }) => (object && object.tip ? { text: object.tip, style: { background: '#0d141f', color: '#e8eef8', border: '1px solid rgba(255,255,255,.16)', borderRadius: '8px', font: '12px "Noto Sans JP","Yu Gothic",Meiryo,sans-serif', padding: '6px 10px' } } : null),
        });
        return true;
      } catch (e) {
        st.deck = null;
        setMsg(cfg.texts.noWebgl);
        return false;
      }
    }

    function layersAt(t) {
      const P = st.data.placement, b = boxOf(st.data), L = b.l / 1000, W = b.w / 1000, H = b.h / 1000;
      const n = P.placements.length, dropH = H + 0.6, span = Math.max(0, cfg.animMs - cfg.dropMs);
      const vis = [];
      P.placements.forEach((p, i) => {
        const start = n > 1 ? (span * i) / (n - 1) : 0;
        const q = Math.max(0, Math.min(1, (t - start) / cfg.dropMs));
        if (q <= 0 && t !== Infinity) return;
        const e = t === Infinity ? 1 : easeOut(q);
        const it = (P.items || [])[p.item];
        vis.push({ ...p, fx: p.x / 1000, fy: p.y / 1000, fz: p.z / 1000 + (1 - e) * dropH, fl: p.l / 1000, fw: p.w / 1000, fh: p.h / 1000, a: Math.round(255 * Math.min(1, q * 4 || 1)),
          tip: `${it && it.name ? it.name : `荷物 ${p.item + 1} 行目`}${it ? `\n${n1(it.length_cm)}×${n1(it.width_cm)}×${n1(it.height_cm)} cm・${n1(it.weight_kg)} kg` : ''}${p.rotated ? '\n床で 90 度回転' : ''}` });
      });
      const corners = [[0, 0, 0], [L, 0, 0], [L, W, 0], [0, W, 0], [0, 0, H], [L, 0, H], [L, W, H], [0, W, H]];
      const faces = [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4], [3, 2, 6, 7], [0, 3, 7, 4], [1, 2, 6, 5]].map((f) => ({ p: f.map((i) => corners[i]) }));
      const contEdges = EDGES.map(([i, j]) => ({ s: corners[i], t: corners[j] }));
      const boxEdges = vis.flatMap((v) => {
        const pts = [[v.fx, v.fy, v.fz], [v.fx + v.fl, v.fy, v.fz], [v.fx + v.fl, v.fy + v.fw, v.fz], [v.fx, v.fy + v.fw, v.fz], [v.fx, v.fy, v.fz + v.fh], [v.fx + v.fl, v.fy, v.fz + v.fh], [v.fx + v.fl, v.fy + v.fw, v.fz + v.fh], [v.fx, v.fy + v.fw, v.fz + v.fh]];
        return EDGES.map(([i, j]) => ({ s: pts[i], t: pts[j] }));
      });
      const step = Math.max(L, W) > 5 ? 1 : 0.5, grid = [];
      for (let x = step; x < L - 1e-6; x += step) grid.push({ s: [x, 0, 0.002], t: [x, W, 0.002] });
      for (let y = step; y < W - 1e-6; y += step) grid.push({ s: [0, y, 0.002], t: [L, y, 0.002] });
      const lab = 0.12 * Math.max(W, 1);
      const labels = [{ p: [L / 2, -lab, 0], t: `L ${Kit.fmt.num(b.l)}mm` }, { p: [L + lab, W / 2, 0], t: `W ${Kit.fmt.num(b.w)}mm` }, { p: [0, -lab, H / 2], t: `H ${Kit.fmt.num(b.h)}mm` }];
      const D = deck;
      return [
        new D.LineLayer({ id: 'kit-3d-grid', data: grid, getSourcePosition: (d) => d.s, getTargetPosition: (d) => d.t, getColor: [255, 255, 255, 34], getWidth: 1, widthUnits: 'pixels' }),
        new D.SimpleMeshLayer({
          id: 'kit-3d-box', data: vis, mesh: cubeMesh(), pickable: true, sizeScale: 1, getPosition: (d) => [d.fx, d.fy, d.fz], getScale: (d) => [d.fl, d.fw, d.fh],
          getColor: (d) => [...colorOf(d.item), d.a], material: { ambient: 0.5, diffuse: 0.7, shininess: 20, specularColor: [50, 50, 50] },
          updateTriggers: { getPosition: vis.map((d) => d.fz).join(',') || 0, getColor: vis.map((d) => d.a).join(',') },
        }),
        new D.LineLayer({ id: 'kit-3d-box-edge', data: boxEdges, getSourcePosition: (d) => d.s, getTargetPosition: (d) => d.t, getColor: [8, 12, 20, 170], getWidth: 1, widthUnits: 'pixels' }),
        new D.SolidPolygonLayer({ id: 'kit-3d-faces', data: faces, getPolygon: (d) => d.p, getFillColor: [92, 200, 255, 20], filled: true, extruded: false, parameters: { depthWriteEnabled: false } }),
        new D.LineLayer({ id: 'kit-3d-cont-edge', data: contEdges, getSourcePosition: (d) => d.s, getTargetPosition: (d) => d.t, getColor: [140, 215, 255, 230], getWidth: 2, widthUnits: 'pixels' }),
        new D.TextLayer({ id: 'kit-3d-dim', data: labels, getPosition: (d) => d.p, getText: (d) => d.t, getSize: 12, getColor: [143, 157, 180, 255], background: false, getTextAnchor: 'middle', getAlignmentBaseline: 'center' }),
      ];
    }

    function draw(t) { if (st.deck && !st.gone) st.deck.setProps({ layers: layersAt(t) }); }
    function replay() {
      cancelAnimationFrame(st.raf);
      if (!st.deck || !st.data) return;
      if (!cfg.animate || reduced()) { draw(Infinity); return; }
      st.t0 = performance.now();
      const tick = (now) => { const t = now - st.t0; draw(t); if (t < cfg.animMs + 50) st.raf = requestAnimationFrame(tick); else draw(Infinity); };
      st.raf = requestAnimationFrame(tick);
    }

    function sideHtml() {
      const P = st.data.placement, V = st.data.vehicle, s = P.summary || {}, c = P.container || {};
      const b = boxOf(st.data);
      const vd = cfg.texts.verdict[s.verdict] || cfg.texts.verdict['×'];
      const items = (P.items || []).map((it, i) => {
        const placed = P.placements.filter((p) => p.item === i).length;
        return `<li><i style="display:inline-block;width:12px;height:12px;border-radius:3px;background:rgb(${colorOf(i).join(',')});margin-right:6px"></i>${esc(it.name || `荷物 ${i + 1} 行目`)} ${n1(it.length_cm)}×${n1(it.width_cm)}×${n1(it.height_cm)} cm・${n1(it.weight_kg)} kg　置いた数 ${placed} / ${it.count}</li>`;
      }).join('');
      const un = (P.unplaced || []).length ? `<ul>${P.unplaced.map((u) => `<li>荷物 ${u.item + 1} 行目 ${u.count} 個: ${esc(u.reason)}</li>`).join('')}</ul>` : '<p>置けなかった荷はありません。</p>';
      const spec = V
        ? `${esc(V.name || V.id)}　内寸 ${Kit.fmt.num(b.l)}×${Kit.fmt.num(b.w)}×${Kit.fmt.num(b.h)} mm・開口 ${V.opening ? `${Kit.fmt.num(V.opening.width_mm)}×${Kit.fmt.num(V.opening.height_mm)} mm` : '—'}・最大積載量 ${Kit.fmt.kg(V.payload_kg)}・床面積 ${finite(V.floor_area_m2) ? `${n1(V.floor_area_m2)} ㎡` : '—'}　（${esc(V.basis || '')}）`
        : `${esc(c.name || '')}　内寸 ${Kit.fmt.num(b.l)}×${Kit.fmt.num(b.w)}×${Kit.fmt.num(b.h)} mm（${esc(c.basis || '')}）`;
      const table = cfg.table ? `<details class="kit-det"><summary>表で見る（置いた荷の位置）</summary><div class="kit-tw"><table class="kit-tab"><thead><tr><th>荷物</th><th class="n">x</th><th class="n">y</th><th class="n">z</th><th class="n">長さ</th><th class="n">幅</th><th class="n">高さ</th><th>向き</th></tr></thead><tbody>${
        P.placements.map((p) => `<tr><td>${p.item + 1} 行目</td><td class="n">${p.x}</td><td class="n">${p.y}</td><td class="n">${p.z}</td><td class="n">${p.l}</td><td class="n">${p.w}</td><td class="n">${p.h}</td><td>${p.rotated ? '床で 90 度回転' : '—'}</td></tr>`).join('')}</tbody></table></div><small>位置と寸法は mm。x は奥行き、y は幅、z は床からの高さです。</small></details>` : '';
      return `<p><b class="kit-3d-${vd[0]}">${esc(s.verdict || '')} ${esc(vd[1])}</b>${s.weight_ok === false ? `（最大積載量を ${n1(s.weight_over_kg)} kg 超えます）` : ''}</p>
        <p class="kit-sub">${spec}</p>
        <div class="kit-3d-nums"><div><span>置いた数</span><b>${Kit.fmt.num(s.placed)} / ${Kit.fmt.num(s.requested)} 個</b></div><div><span>床の使用率</span><b>${Kit.fmt.pct(s.floor_area_pct)}</b></div><div><span>容積の使用率</span><b>${Kit.fmt.pct(s.volume_pct)}</b></div><div><span>重さ</span><b>${Kit.fmt.kg(s.total_weight_kg)} / ${Kit.fmt.kg(s.payload_kg)}</b></div></div>
        <ul style="list-style:none;padding:0;margin:6px 0">${items}</ul>${un}
        <p class="kit-sub">${esc(s.note || '')}${Kit.basisNote ? ' ' + esc(Kit.basisNote) : ''}</p>${table}`;
    }

    function setData(d) {
      pauseAll();
      const ok = d && d.placement && Array.isArray(d.placement.placements) && boxOf(d);
      if (!ok) { st.data = null; setMsg('荷室の寸法か配置の結果が足りないため、描いていません。'); side.innerHTML = ''; if (st.deck) st.deck.setProps({ layers: [] }); return; }
      st.data = d;
      setMsg('');
      side.innerHTML = sideHtml();
      if (ensureDeck()) replay();
    }
    function pauseAll() { cancelAnimationFrame(st.raf); }

    $k('replay').addEventListener('click', replay);
    $k('reset').addEventListener('click', () => { if (st.deck && st.data) st.deck.setProps({ initialViewState: { ...viewState() } }); });
    setData(data);

    return {
      update(d) { if (!st.gone) setData(d); },
      replay,
      destroy() {
        if (st.gone) return;
        st.gone = true; pauseAll();
        if (st.deck) { try { st.deck.finalize(); } catch (e) { /* もう無い */ } st.deck = null; }
        root.remove();
      },
    };
  }

  Kit.load3d = { mount };
})(window.Kit);
