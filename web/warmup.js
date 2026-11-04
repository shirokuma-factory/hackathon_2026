'use strict';
/* 起動時のシェーダーの先読み（SPEC §19.5）。
   初めて描くものがあると、GPU のシェーダーのコンパイルが画面の動きを 100〜300ms 止める（同期の待ちで、避けられない）。
   利用者は初回に 1 度だけ開くので、その待ちを読み込み画面（#loading）の裏で先に済ませる。

   ■ 地図（MapLibre）: 道路・鉄道・水・公園・境界・ラベルなどの描画の種類は、その地物がある場所に来たときに初めてコンパイルされる。
     羽田の付近（東京）でズーム 6.5・9・13・15.6 に順に移して描かせ、そのたびに地図が静まる（idle）のを待ってから、元の位置に戻す。
     取り寄せるタイルは各ズームで十数枚ほど。1 回だけで、ブラウザが覚えるので、2 回目以降の訪問ではほぼ通信しない。
     日本語のラベルの字形（MapLibre が 1 文字ずつ作る SDF）も、このとき一緒に作られる。
   ■ 地図の上の層（deck.gl）: アプリが使う層の種類と、シェーダーが変わる組み合わせを 1 種類ずつ、画面の中心に 1 個だけ描く。
     色の描画と、マウスの当たり判定（picking。描画用とは別のシェーダーになる）の両方を通す。
     1 段階（1 組）ごとに数フレームかけて、重い作業が 1 フレームに集まらないようにする。描いたあとの層は外す。
     組み合わせ: モデル（元・低ポリゴン版で材質が違う）、線（点線あり・なし）、軌跡、地物（面と枠）、点、柱（陰影つき）、アイコン、文字（衝突回避あり・なし）。
   ■ 画面: 先読みが終わるまで #loading を出したままにする（最大 MAX_MS）。?warm=0 で止められる（計測の比較用）。 */

const WARM = (() => {
  const MAX_MS = 15000; // 全体の上限。超えたら先読みを打ち切って画面を出す
  const NEED_MS = 5000; // モデルの読み込みを待つ上限
  const W = {
    state: 'idle', // idle | running | done
    t0: 0, doneAt: 0, mapMs: 0, deckMs: 0, skipped: false,
    steps: [], // { name, ms: 段階の最大フレーム時間, frames }
    cur: null, idx: -1, last: 0, waitSince: 0,
    ctx: null,
  };
  const enabled = () => { try { return new URLSearchParams(location.search).get('warm') !== '0'; } catch (e) { return true; } };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const raf = () => new Promise((r) => requestAnimationFrame(r));

  /* ------------------------------------------------------------ 地図 */

  // 地図が静まる（タイルが揃って描き終わる）のを待つ。最大 ms まで
  function idle(map, ms) {
    return new Promise((resolve) => {
      let done = false;
      const fin = () => { if (done) return; done = true; clearTimeout(tm); map.off('idle', fin); resolve(); };
      const tm = setTimeout(fin, ms);
      map.once('idle', fin);
      map.triggerRepaint();
    });
  }
  // 下地の描画の種類は、ズームの範囲ごとに出る層が違う（OpenFreeMap の positron）。6.5: 州・町のラベルと高速道路、9: 村・その他のラベルと細い道、
  // 13: 鉄道（点線）・主要道・境界の点線、15.6: 道路名（細い道・小道）。これで全ての描画の種類が 1 回ずつ出る
  const MAP_STOPS = [{ z: 6.5 }, { z: 9 }, { z: 13 }, { z: 15.6 }];
  const MAP_CENTER = [139.77, 35.58]; // 東京湾の羽田。道路・鉄道・公園・水・県境・空港・ラベルがそろう
  async function warmMap(map) {
    if (!enabled()) { W.skipped = true; return; }
    const t0 = performance.now();
    const cam = { center: map.getCenter(), zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() };
    try {
      for (const s of MAP_STOPS) {
        map.jumpTo({ center: MAP_CENTER, zoom: s.z, bearing: 0, pitch: 0 });
        await idle(map, 6000);
        await raf(); await raf();
      }
    } catch (e) { console.error(e); }
    map.jumpTo(cam);
    await idle(map, 3000);
    W.mapMs = performance.now() - t0;
  }

  /* ------------------------------------------------------------ 層（deck.gl） */

  const svg = (body) => 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">${body}</svg>`);
  const ICON = { url: svg('<rect x="5" y="5" width="22" height="22" rx="3" fill="#fff"/>'), width: 32, height: 32, mask: true };
  const ICON_PHOTO = { url: svg('<path d="M16 3 L28 27 L16 21 L4 27 Z" fill="#fff"/>'), width: 32, height: 32 };
  const WHITE = [255, 255, 255, 255];

  // 段階の一覧。make(ctx) で層の配列を作る。needs() が false の間は待つ。frames: 層を出しておくフレーム数、pickAt: 当たり判定を通すフレーム
  function buildSteps(ctx) {
    const [lng, lat] = ctx.p, d = 0.0004;
    const pt = [lng, lat], D1 = [{ p: pt }];
    const ring = [[lng - d, lat - d], [lng + d, lat - d], [lng + d, lat + d], [lng - d, lat + d], [lng - d, lat - d]];
    const path = [[lng - d, lat], [lng, lat + d], [lng + d, lat], [lng, lat - d]];
    const feat = { type: 'Feature', properties: { i: -1 }, geometry: { type: 'Polygon', coordinates: [ring] } };
    const base = { pickable: true };
    const text = (id, ext) => new deck.TextLayer({
      ...base, id, data: ctx.labels.map((name) => ({ p: pt, name })), getPosition: (x) => x.p, getText: (x) => x.name, getSize: 11.5, sizeUnits: 'pixels', getColor: WHITE,
      getPixelOffset: [0, 15], getTextAnchor: 'middle', getAlignmentBaseline: 'top', fontFamily: '"Noto Sans JP", sans-serif',
      characterSet: 'auto', outlineWidth: 3, outlineColor: [8, 12, 20, 235], fontSettings: { sdf: true }, billboard: true,
      ...(ext ? { extensions: [new deck.CollisionFilterExtension()], collisionEnabled: true, getCollisionPriority: () => 3, collisionGroup: id } : {}),
    });
    const trips = (id) => new deck.TripsLayer({
      ...base, id, data: [{ path, ts: [0, 1, 2, 3] }], getPath: (x) => x.path, getTimestamps: (x) => x.ts, getColor: WHITE, getWidth: 2, widthUnits: 'pixels',
      capRounded: true, jointRounded: true, trailLength: 2.4, currentTime: 1.5, fadeTrail: true,
    });
    const M = typeof M3D !== 'undefined' ? M3D : null;
    const steps = [];
    if (M && M.hasDeck()) {
      steps.push({ name: 'scenegraph-hi', needs: () => M.warmNeed(false), make: () => M.warmLayers(false, pt), frames: 3, pickAt: 2 });
      steps.push({ name: 'scenegraph-lo', needs: () => M.warmNeed(true), make: () => M.warmLayers(true, pt), frames: 3, pickAt: 2 });
    }
    steps.push({ name: 'path', make: () => [new deck.PathLayer({ ...base, id: 'warm-path', data: [path], getPath: (x) => x, getColor: WHITE, widthUnits: 'pixels', getWidth: 2, capRounded: true, jointRounded: true })], frames: 3, pickAt: 2 });
    steps.push({ name: 'path-dash', make: () => [new deck.PathLayer({
      ...base, id: 'warm-dash', data: [path], getPath: (x) => x, getColor: WHITE, widthUnits: 'pixels', getWidth: 2, capRounded: true,
      extensions: [new deck.PathStyleExtension({ dash: true })], getDashArray: [3, 3], dashJustified: true,
    })], frames: 3, pickAt: 2 });
    steps.push({ name: 'trips', make: () => [trips('warm-trips')], frames: 3, pickAt: 2 });
    steps.push({ name: 'geojson', make: () => [
      new deck.GeoJsonLayer({ ...base, id: 'warm-geo', data: [feat], stroked: true, filled: true, getFillColor: WHITE, getLineColor: WHITE, lineWidthUnits: 'pixels', getLineWidth: 1, autoHighlight: true }),
      new deck.SolidPolygonLayer({ id: 'warm-poly', data: [ring], getPolygon: (x) => x, getFillColor: WHITE }),
    ], frames: 3, pickAt: 2 });
    steps.push({ name: 'scatterplot', make: () => [new deck.ScatterplotLayer({ ...base, id: 'warm-dot', data: D1, getPosition: (x) => x.p, radiusUnits: 'pixels', getRadius: 2, stroked: true, filled: true, getFillColor: WHITE, getLineColor: WHITE, lineWidthUnits: 'pixels', getLineWidth: 1 })], frames: 3, pickAt: 2 });
    steps.push({ name: 'column', make: () => [new deck.ColumnLayer({
      ...base, id: 'warm-col', data: D1, getPosition: (x) => x.p, diskResolution: 14, radius: 10, extruded: true, getElevation: () => 10, getFillColor: WHITE, autoHighlight: true,
      material: { ambient: 0.62, diffuse: 0.7, shininess: 28, specularColor: [190, 190, 230] },
    })], frames: 3, pickAt: 2 });
    steps.push({ name: 'icon', make: () => [
      new deck.IconLayer({ ...base, id: 'warm-icon', data: D1, getPosition: (x) => x.p, getIcon: () => ICON, getSize: 12, sizeUnits: 'pixels', getColor: WHITE }),
      new deck.IconLayer({ ...base, id: 'warm-icon2', data: D1, getPosition: (x) => x.p, getIcon: () => ICON_PHOTO, getAngle: 0, getSize: 12, sizeUnits: 'pixels', billboard: false, getColor: WHITE }),
    ], frames: 8, pickAt: 6 }); // 画像の読み込みを待つぶん長め
    steps.push({ name: 'text', make: () => [text('warm-text', false)], frames: 4, pickAt: 3 });
    if (typeof deck.CollisionFilterExtension === 'function') steps.push({ name: 'text-collision', make: () => [text('warm-text-col', true)], frames: 5, pickAt: 4 });
    return steps;
  }

  /* 文字の形の表（フォントのアトラス）は、使う文字が増えるたびに作り直して GPU に送り直す（大きいと 100ms を超える）。
     拠点の名前に出てくる文字を、先にすべて入れておく（deck.gl は同じフォント設定の表を層の間で共有する）。数文字ずつの文字列にして 1 個の層で描く */
  function labelChunks() {
    const set = new Set('東京大阪名古屋0123456789');
    try {
      const d = typeof Hubs !== 'undefined' ? Hubs.data : null;
      for (const x of [...((d && d.st) || []), ...((d && d.pt) || [])]) for (const ch of String(x.name || '')) set.add(ch);
    } catch (e) { /* なくてよい */ }
    const chars = [...set].slice(0, 1500), out = [];
    for (let i = 0; i < chars.length; i += 8) out.push(chars.slice(i, i + 8).join(''));
    return out;
  }

  // 毎フレーム 1 回（app.js の frame() から）。このフレームで足す層の配列を返す
  function layers(now) {
    if (W.state !== 'running') return NONE;
    const ctx = W.ctx;
    const dt = W.last ? now - W.last : 0;
    W.last = now;
    if (W.cur && dt > W.cur.rec.ms) W.cur.rec.ms = dt; // 1 つ前のフレームの時間は、そのとき出していた段階のもの
    if (now - W.t0 > MAX_MS) { finish(); return NONE; }
    ctx.map.triggerRepaint();
    if (!W.cur) {
      const s = ctx.steps[W.idx + 1];
      if (!s) { finish(); return NONE; }
      if (s.needs && !s.needs()) { // モデルがまだ読み込めていない。一定時間は待つ（超えたらこの段階は飛ばす）
        if (!W.waitSince) W.waitSince = now;
        if (now - W.waitSince < NEED_MS) return NONE;
        W.idx++; W.waitSince = 0;
        W.steps.push({ name: s.name, ms: 0, frames: 0, skipped: 'timeout' });
        return NONE;
      }
      W.idx++; W.waitSince = 0;
      let ls = [];
      try { ls = s.make(ctx).filter(Boolean); } catch (e) { console.error(e); }
      s.rec = { name: s.name, ms: 0, frames: 0 };
      W.steps.push(s.rec);
      W.cur = { s, ls, rec: s.rec, n: 0 };
    }
    const c = W.cur;
    c.n++; c.rec.frames = c.n;
    if (c.n === c.s.pickAt) { // 当たり判定の描画（描画用とは別のシェーダーになる）。層はもう前のフレームで作ってある
      try { ctx.overlay.pickObject({ x: ctx.xy.x, y: ctx.xy.y, radius: 2 }); } catch (e) { console.error(e); }
    }
    if (c.n >= c.s.frames) W.cur = null; // 次のフレームで次の段階へ（この層は外れる）
    return c.ls;
  }
  const NONE = [];

  function finish() {
    if (W.state === 'done') return;
    W.state = 'done';
    W.doneAt = performance.now();
    W.deckMs = W.doneAt - (W.deckStart || W.t0);
    W.cur = null;
    try { W.ctx.map.triggerRepaint(); } catch (e) { /* なくてよい */ }
    for (const f of W.onDone || []) { try { f(); } catch (e) { console.error(e); } }
  }

  // 層の先読みを始める（アプリの描画ループが回り始めてから）。map と overlay は app.js のもの
  function start(map, overlay) {
    if (!enabled() || typeof deck === 'undefined') { W.state = 'done'; W.skipped = true; return; }
    const c = map.getCenter();
    const ctx = { map, overlay, p: [c.lng, c.lat], xy: map.project(c), labels: labelChunks() };
    ctx.steps = buildSteps(ctx);
    W.ctx = ctx;
    W.t0 = W.deckStart = performance.now();
    W.state = 'running';
  }
  function onDone(f) { (W.onDone = W.onDone || []).push(f); if (W.state === 'done') f(); }

  return { start, layers, warmMap, onDone, W, get done() { return W.state === 'done'; }, get running() { return W.state === 'running'; } };
})();
