'use strict';
/* 3D モデルの表示（SPEC §19）。Blender で作った glTF（web/models/*.glb）を、拡大したときだけ deck.gl の ScenegraphLayer で出す。
   縮小したときは従来の 2D アイコンのままで、ズームで切り替える（LOD）。

   ■ LOD の規則（ズームは MapLibre の値）
     ・乗り物: ズーム Z_ON(9.0) 未満は 2D アイコンだけ。Z_ON〜Z_FULL(9.8) は 2D を薄くしながら 3D を濃くする（交差）。Z_FULL 以上は 3D だけ。
     ・施設（倉庫・駅・港・空港）: ズーム Z_FAC(12) 以上で 3D。画面内に入るものだけを、モデルごとに最大 CAP(300) 件まで（中心に近い順。ズーム 14 未満はさらに絞る）。
     ・地図の最大ズームは app.js の地図の作成時に 16 にしてある（実寸のモデルを見るため）。

   ■ 負荷を抑える規則（SPEC §19.5）
     ・施設の層のデータ（画面内の絞り込み）は、地図が止まって 150ms たったときにだけ作り直す。動いている間は直前の配列（同じ参照）を使うので、
       deck.gl はデータの再計算をしない。絞り込みの範囲は画面より 35% 広くしてあり、少し動かしただけでは端が欠けない。
     ・画面内に 1 件もないモデルの層は作らない（layer が null を返す）。
     ・層に渡す定数（_animations、parameters）は毎回作り直さず、同じオブジェクトを使う。 
   ■ 大きさ（sizeScale）
     モデルの単位はメートル。実寸のままだと遠くでは 1 px 未満になるので、
       sizeScale = max(1, px × mpp / len)
     とする。px = 画面で最低限見せたい長さ（モデルごと）、mpp = 画面 1 px あたりのメートル（78271.5 × cos(緯度) / 2^ズーム）、len = モデルの全長（m）。
     近づくと px × mpp / len が 1 を下回るので、そこから実寸（sizeScale = 1）になる。遠いときは 2D アイコンとほぼ同じ見た目の大きさで、
     ズームを 1 上げるごとに倍率が半分になり連続的に変わる（段差なし）。

   ■ 向きの導出（getOrientation と glTF の座標）
     glTF は Y が上、前が +Z。deck の座標は東 +X、北 +Y、上 +Z。ScenegraphLayer は glTF を変換せず Y 上のまま置くので、
     [0, 0, 0] のままではモデルの前（+Z）が真上を向き、横倒し（鼻立ち）になる。
     deck.gl 9.0.38 の回転行列（mesh-layers の SI 関数。[pitch, yaw, roll] の順）を yaw=0 で展開すると、roll は X 軸まわりの回転になる。
       roll = 90: Y(上) → +Z(上)、+Z(前) → -Y(南)。つまりモデルが起き上がり、前は南を向く。
       yaw は Z 軸まわりの反時計回り: 南向きの前を yaw 度回すと (sin yaw, -cos yaw)。
     経路の向き ang は IconLayer と同じ「北から反時計回りの度」で、進む向きは (-sin ang, cos ang)。
       (sin yaw, -cos yaw) = (-sin ang, cos ang) を解くと yaw = ang + 180。
     これを YAW_SIGN = 1, YAW_OFFSET = 180 とする。東向き（ang = -90）なら yaw = 90 で前が東（+X）、北向き（ang = 0）なら yaw = 180 で前が北になる。
     画面での確認は docs/assets/2026-11-04_map3d_*.png と SPEC §19 に記録した。 */

const M3D = (() => {
  const Z_ON = 9.0, Z_FULL = 9.8, Z_FAC = 12, MAXZ = 16, CAP = 300;
  const cfg = { capScale: 1 }; // 施設の件数の上限に掛ける倍率（計測用）
  const DIR = 'models/';
  const THUMB_DIR = 'img/models/';
  const ANIM = { '*': { playing: true, speed: 1 } }; // 同じ参照を渡す（毎回作ると層の差分の判定が重くなる）
  const PARAMS = { depthCompare: 'less-equal' };
  // len: 全長（m、glb の寸法から）。px: 画面で最低限見せたい長さ。anim: 焼き込みのアニメーションがあるモデル
  const MODELS = {
    truck: { f: 'truck.glb', len: 12.1, px: 26, anim: true },
    truck_4t: { f: 'truck_4t.glb', len: 8.7, px: 24, anim: true },
    box_2t: { f: 'box_2t.glb', len: 4.8, px: 20 },
    kei_van: { f: 'kei_van.glb', len: 3.6, px: 18 },
    trailer: { f: 'trailer.glb', len: 16.6, px: 28 },
    freight_train: { f: 'freight_train.glb', len: 82.1, px: 46 },
    container_12ft: { f: 'container_12ft.glb', len: 3.7, px: 14 },
    ferry: { f: 'ferry.glb', len: 190.2, px: 38 },
    roro_ship: { f: 'roro_ship.glb', len: 170, px: 38 },
    container_ship: { f: 'container_ship.glb', len: 115, px: 36 },
    cargo_plane: { f: 'cargo_plane.glb', len: 64.7, px: 50 },
    passenger_jet: { f: 'passenger_jet.glb', len: 57, px: 46 },
    warehouse: { f: 'warehouse.glb', len: 228, px: 30 },
    cold_storage: { f: 'cold_storage.glb', len: 124, px: 28 },
    terminal: { f: 'terminal.glb', len: 161.3, px: 34 },
    air_cargo_terminal: { f: 'air_cargo_terminal.glb', len: 200.6, px: 36 },
    port_crane: { f: 'port_crane.glb', len: 94.2, px: 30, anim: true },
    container_yard: { f: 'container_yard.glb', len: 150, px: 36, anim: true },
  };
  // 手段ごとの既定のモデル
  const MODE_MODEL = { truck: 'truck', rail: 'freight_train', ferry: 'ferry', air: 'cargo_plane' };

  // 向きの換算。YAW_SIGN, YAW_OFFSET は描いて決めた値（検証の記録は下の「向きの検証」）
  const YAW_SIGN = 1, YAW_OFFSET = 180;
  const ROLL = 90; // glTF の Y 上を deck の Z 上へ起こす

  /* ■ モデルの読み込みと低ポリゴン版（LOD）
     glb は自分で 1 回だけ読み込んで解析し（loaders.load）、できあがった glTF のオブジェクトを層に渡す。URL を層に渡すと、層を作るたびに
     読み込みと解析が走り、最初に 3D が出る瞬間にカクつくため。読み込めていないモデルは層を作らず、2D アイコンのままにする（mixFor）。
     低ポリゴン版 web/models/lo/<名前>.glb は、web/models/lod.json に載っているモデルだけ使う（なければ元のモデルにもどる）。
     lo を使う条件は PERF の段と、画面での大きさ（40px より小さく見えるとき。36px 未満で lo、44px 超で元に戻す）。 */
  const GL = new Map(); // url → { st: 0 未, 1 読込中, 2 完了, 3 失敗, gltf }
  const LOD = { names: null, loaded: false };
  const LO_ON = 36, LO_OFF = 44;
  const loState = new Map(); // 層の id → lo を使っているか（ちらつき防止の履歴）
  const loadersList = () => {
    if (loadersList.v) return loadersList.v;
    try { loadersList.v = new deck.ScenegraphLayer({ id: '_probe', data: [] }).props.loaders; } catch (e) { loadersList.v = null; }
    return loadersList.v;
  };
  function ensure(url) {
    let e = GL.get(url);
    if (e) return e;
    e = { st: 1, gltf: null };
    GL.set(url, e);
    const ld = typeof loaders !== 'undefined' && loadersList();
    if (!ld) { e.st = 3; return e; }
    loaders.load(url, ld).then((g) => { e.gltf = g; e.st = 2; }, () => { e.st = 3; });
    return e;
  }
  const ready = (url) => { const e = GL.get(url); return !!e && e.st === 2; };
  const hasLo = (key) => !!(LOD.names && MODELS[key] && (LOD.names.has(key) || LOD.names.has(MODELS[key].f)));
  const urlOf = (key, lo) => DIR + (lo && hasLo(key) ? 'lo/' : '') + MODELS[key].f;
  function lodJson(j) {
    const out = new Set();
    const add = (v) => { if (typeof v === 'string') { out.add(v.replace(/^.*\//, '').replace(/\.glb$/i, '')); out.add(v.replace(/^.*\//, '')); } };
    const walk = (v) => {
      if (Array.isArray(v)) v.forEach((x) => (typeof x === 'string' ? add(x) : x && typeof x === 'object' ? (x.name || x.model || x.file ? add(x.name || x.model || x.file) : walk(x)) : 0));
      else if (v && typeof v === 'object') for (const k of Object.keys(v)) { if (v[k] === true || (v[k] && typeof v[k] === 'object' && !Array.isArray(v[k]) && k in MODELS)) add(k); else if (typeof v[k] === 'string' && k in MODELS) add(k); else walk(v[k]); }
    };
    walk(j);
    return out;
  }
  function loadLod() {
    if (LOD.loaded) return Promise.resolve();
    LOD.loaded = true;
    return fetch(DIR + 'lod.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).then((j) => { if (j) LOD.names = lodJson(j); }, () => {});
  }
  /* 起動後のアイドル時に、全モデル（lo と元）を 1 つずつ読み込む。重い解析を 1 度に集めないよう、1 件ごとに間をあける。 */
  const PRELOAD = { started: false, done: false, shader: 0 };
  function preload() {
    if (PRELOAD.started || !hasDeck()) return;
    PRELOAD.started = true;
    const idle = (f) => (typeof requestIdleCallback === 'function' ? requestIdleCallback(f, { timeout: 2500 }) : setTimeout(f, 300));
    const order = ['truck', 'truck_4t', 'box_2t', 'kei_van', 'trailer', 'freight_train', 'ferry', 'cargo_plane', 'warehouse', 'terminal', 'port_crane', 'container_yard',
      'cold_storage', 'air_cargo_terminal', 'roro_ship', 'container_ship', 'passenger_jet', 'container_12ft'];
    loadLod().then(() => {
      const urls = [];
      for (const k of order) if (hasLo(k)) urls.push(urlOf(k, true));
      for (const k of order) urls.push(urlOf(k, false));
      let i = 0;
      const next = () => {
        if (i >= urls.length) { PRELOAD.done = true; return; }
        const u = urls[i++];
        ensure(u);
        setTimeout(() => idle(next), 120);
      };
      setTimeout(() => idle(next), 1200);
    });
  }
  // 起動直後の最初の描画で使う: lod.json を先に読む
  if (typeof document !== 'undefined') loadLod();

  // 3D が使えるか（モデルのどれかが読み込めているか）。使えるまでは 2D アイコンのまま
  function modelReady(key) {
    key = MODELS[key] ? key : MODE_MODEL[key];
    if (!key || !MODELS[key]) return false;
    return ready(urlOf(key, false)) || (hasLo(key) && ready(urlOf(key, true)));
  }

  const clamp01 = (v) => Math.max(0, Math.min(1, v));
  const mpp = (zoom, lat) => (78271.517 * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, zoom);
  const smooth = (t) => t * t * (3 - 2 * t);

  /* サムネイル（SPEC §19.6）。web/img/models/*.webp（320x200、透過）。UI の説明用で、地図の層とは無関係。
     alt は日本語。画面外のものは読み込まない（loading=lazy）。知らない名前のときは空文字を返す。 */
  const THUMB_ALT = {
    truck: '10t ウイングボディ', truck_4t: '4t ウイング車', box_2t: '2t 箱車', kei_van: '軽バン', trailer: 'トラクターと 40ft 海上コンテナ',
    freight_train: '電気機関車とコンテナ貨車', container_12ft: '12ft 鉄道コンテナ', container_31ft: '31ft 鉄道コンテナ', ferry: '長距離 RORO フェリー', roro_ship: '貨物専用 RORO 船',
    container_ship: 'コンテナ船', cargo_plane: '貨物機', passenger_jet: '旅客機（腹の貨物室）', warehouse: '倉庫', cold_storage: '冷蔵・冷凍倉庫',
    terminal: 'トラックターミナル・貨物駅', port_crane: '港のガントリークレーン', air_cargo_terminal: '空港の貨物ターミナル', container_yard: 'コンテナヤード',
    roll_cage: 'カゴ台車', box_80: '段ボール箱（80 サイズ）',
  };
  const thumbAlt = (name) => THUMB_ALT[name] || '';
  function thumb(name, w = 96, h = 60, cls = '') {
    if (!THUMB_ALT[name]) return '';
    return `<img class="mth${cls ? ' ' + cls : ''}" src="${THUMB_DIR}${name}.webp" width="${w}" height="${h}" alt="${THUMB_ALT[name]}" loading="lazy" decoding="async">`;
  }

  const hasDeck = () => typeof deck !== 'undefined' && typeof deck.ScenegraphLayer === 'function';

  // 3D の濃さ（0 = 2D だけ、1 = 3D だけ）
  function mix(zoom) {
    if (!hasDeck()) return 0;
    return smooth(clamp01((zoom - Z_ON) / (Z_FULL - Z_ON)));
  }
  const iconAlpha = (zoom) => 1 - mix(zoom); // 2D アイコンに掛ける濃さ
  // モデルが読み込めていないときは 3D の濃さを 0 にして、2D アイコンのままにする（keys: 手段名かモデル名、または配列）
  function mixFor(keys, zoom) {
    const m = mix(zoom);
    if (m <= 0) return 0;
    for (const k of Array.isArray(keys) ? keys : [keys]) if (!modelReady(k)) return 0;
    return m;
  }
  const facilityOn = (zoom) => hasDeck() && zoom >= Z_FAC && PERF.facilities3d();

  const centerLat = () => (typeof map !== 'undefined' && map ? map.getCenter().lat : 36);
  function sizeScale(key, zoom, lat) {
    const m = MODELS[key];
    return Math.max(1, (m.px * mpp(zoom, lat ?? centerLat())) / m.len);
  }
  const yawOf = (ang) => YAW_SIGN * (ang || 0) + YAW_OFFSET;

  /* ScenegraphLayer の作成。data の要素: { p: [経度, 緯度, 高さ m], ang: 北から反時計回りの度, a: 0〜1 の濃さ（省略可）, yaw: 固定の向き（省略可） }
     opts: zoom（必須）, opacity（既定 1）, color（既定 白 = 元の色）, pickable, fac（施設）
     data の参照・大きさ・濃さ・モデルが前回と同じなら、前回の層をそのまま返す（毎フレーム層を作り直さない）。 */
  const GETPOS = (d) => d.p;
  const ORIENT = (d) => [0, d.yaw ?? yawOf(d.ang), ROLL];
  const GETCOL_W = (d) => [255, 255, 255, Math.round(255 * (d.a ?? 1))];
  const ANIM_OFF = { '*': { playing: false, speed: 1 } };
  const LM = new Map(); // 層の id → { sig, layer }
  const LIVE = (l) => !!l && (l.lifecycle === 'Initialized' || String(l.lifecycle).startsWith('Matched') || l.lifecycle === 'Awaiting state');
  function pickUrl(key, id, drawnPx, fac) {
    let lo;
    if (!hasLo(key)) lo = false;
    else if (PERF.loVehicles() || (fac && PERF.loFacilities())) lo = true;
    else {
      const was = loState.get(id) ?? false;
      lo = was ? drawnPx < LO_OFF : drawnPx < LO_ON;
    }
    let url = urlOf(key, lo);
    if (!ready(url)) { // 読み込めていない版の代わりに、読み込めている版を使う
      const alt = urlOf(key, !lo);
      if (alt !== url && ready(alt)) { url = alt; lo = !lo; } else ensure(url);
    }
    loState.set(id, url.includes('/lo/'));
    return url;
  }
  function layer(id, key, data, opts = {}) {
    if (!hasDeck() || !MODELS[key] || !data.length) return null;
    const m = MODELS[key], zoom = opts.zoom, op = opts.opacity ?? 1;
    if (op <= 0.01) return null;
    const ss = sizeScale(key, zoom, opts.lat) * (opts.k ?? 1);
    const drawn = (ss * m.len) / mpp(zoom, opts.lat ?? centerLat());
    const url = pickUrl(key, id, drawn, opts.fac);
    const e = GL.get(url);
    if (!e || e.st !== 2) return null;
    const c = opts.color || null, anim = m.anim ? (opts.fac && PERF.pauseFacilityAnim() ? ANIM_OFF : ANIM) : undefined;
    const trig = opts.trig ?? 0, cs = c ? c.join() : '';
    const prev = LM.get(id);
    // 一度画面の層から外れて deck.gl に片付けられた層（Discarded / Finalized）を渡すと、初期化の assert で落ちる。生きている層だけ使い回す
    if (prev && LIVE(prev.layer)) { const q = prev.q; if (q.data === data && q.url === url && q.ss === ss && q.op === op && q.anim === anim && q.pick === !!opts.pickable && q.c === cs && q.trig === trig && q.orient === opts.orient) return prev.layer; }
    const col = c ? (d) => [c[0], c[1], c[2], Math.round(255 * (d.a ?? 1))] : GETCOL_W;
    const l = new deck.ScenegraphLayer({
      id, data, scenegraph: M3D_SHARED ? e.gltf : url,
      getPosition: GETPOS,
      getOrientation: opts.orient || ORIENT,
      getColor: col,
      sizeScale: ss,
      _lighting: 'pbr',
      _animations: anim,
      opacity: op,
      pickable: !!opts.pickable,
      parameters: PARAMS,
      updateTriggers: { getColor: [c ? c.join() : '', trig] },
    });
    LM.set(id, { q: { data, url, ss, op, anim, pick: !!opts.pickable, c: cs, trig, orient: opts.orient }, layer: l });
    return l;
  }
  const M3D_SHARED = true;

  // 乗り物（経路の上を動くもの）。手段 mode か、モデル名 key で指定する
  function vehicles(id, modeOrKey, data, zoom, opts = {}) {
    return layer(id, MODELS[modeOrKey] ? modeOrKey : MODE_MODEL[modeOrKey], data, { ...opts, zoom, opacity: mixFor(MODELS[modeOrKey] ? modeOrKey : MODE_MODEL[modeOrKey], zoom) * (opts.opacity ?? 1) });
  }

  // 便の車種名（「架空 4t ウイング」など）から、使うモデル名を選ぶ
  function byVehicle(name) {
    const v = String(name || '');
    if (/軽/.test(v)) return 'kei_van';
    if (/大型|増トン|10\s*t/.test(v)) return 'truck';
    if (/(^|[^0-9])2\s*t/i.test(v)) return 'box_2t';
    return 'truck_4t';
  }

  /* 施設。points: [{p:[経度,緯度], yaw?}] を、画面に入るものだけ（中心に近い順に CAP 件まで）にして 1 つの層にする */
  // 施設の件数の上限。縮小気味のときは小さく見えるだけなので、件数を絞って描画の負荷を抑える（ズーム 12〜13 は 120、13〜14 は 200、14 以上は CAP）
  const capFor = (zoom) => Math.round((zoom < 13 ? 120 : zoom < 14 ? 200 : CAP) * cfg.capScale * PERF.capMul());
  function inView(points, getP, zoom, pad = 0.1) {
    getP = getP || ((d) => d.p);
    if (typeof map === 'undefined' || !map) return [];
    const b = map.getBounds(), c = map.getCenter();
    const w = b.getWest(), e = b.getEast(), s = b.getSouth(), n = b.getNorth();
    const px = (e - w) * pad, py = (n - s) * pad;
    const out = [];
    for (const d of points) {
      const p = getP(d);
      if (p[0] >= w - px && p[0] <= e + px && p[1] >= s - py && p[1] <= n + py) out.push(d);
    }
    const cap = capFor(zoom ?? 99);
    if (out.length <= cap) return out;
    const k = Math.cos((c.lat * Math.PI) / 180);
    const dist = (d) => { const p = getP(d); return ((p[0] - c.lng) * k) ** 2 + (p[1] - c.lat) ** 2; };
    return out.map((d) => [dist(d), d]).sort((a, b2) => a[0] - b2[0]).slice(0, cap).map((x) => x[1]);
  }

  /* 地図の動きの検知。動いている間は施設の絞り込みをやり直さず、止まって IDLE_MS たったら epoch を進めて作り直させる */
  const IDLE_MS = 150;
  const mv = { watched: false, moving: false, epoch: 0, timer: 0 };
  function watchMap() {
    if (mv.watched || typeof map === 'undefined' || !map) return;
    mv.watched = true;
    const stop = () => { clearTimeout(mv.timer); mv.timer = setTimeout(() => { mv.moving = false; mv.epoch++; }, IDLE_MS); };
    map.on('movestart', () => { clearTimeout(mv.timer); mv.moving = true; });
    map.on('moveend', stop);
  }
  const FC = new Map(); // 層の id → { pts, cap, epoch, data }。data は止まっている間は同じ参照
  function facility(id, key, points, zoom, opts = {}) {
    if (!facilityOn(zoom) || !points || !points.length) return null;
    watchMap();
    const cap = capFor(zoom);
    let c = FC.get(id);
    const stale = !c || c.pts !== points || (!mv.moving && (c.epoch !== mv.epoch || c.cap !== cap));
    // 重い絞り込みは 1 フレームに集中させない（このフレームで 5ms 使っていたら次のフレームにまわす。古いデータは 1 フレームだけそのまま使う）
    if (stale && !PERF.canSpend(5)) { if (!c) return null; } else if (stale) {
      const t0 = performance.now();
      const vis = inView(points, opts.getP, zoom, 0.35);
      c = { pts: points, cap, epoch: mv.epoch, data: vis.map((d) => ({ p: d.p || opts.getP(d), yaw: d.yaw ?? opts.yaw ?? 0, a: 1 })) };
      FC.set(id, c);
      PERF.mark('facility', t0);
    }
    return layer(id, key, c.data, { ...opts, zoom, opacity: 1, fac: true });
  }

  // 3D の層を最初に出す瞬間のカクつきを避けるための見えない層（シェーダーの作成と GPU への転送を先に済ませる。画面の外に 1 個だけ置く）
  let warmUntil = 0;
  function warm(zoom) {
    if (!hasDeck()) return null;
    const key = ['kei_van', 'truck_4t', 'truck'].find((k) => ready(urlOf(k, hasLo(k)))) || null;
    if (!key) return null;
    const now = performance.now();
    if (!warmUntil) warmUntil = now + 1500;
    if (now > warmUntil) return null;
    return new deck.ScenegraphLayer({ id: 'warm-3d', data: WARM_DATA, scenegraph: GL.get(urlOf(key, hasLo(key))).gltf, getPosition: GETPOS, getOrientation: ORIENT, sizeScale: 1, _lighting: 'pbr', opacity: 0.02, parameters: PARAMS });
  }
  const WARM_DATA = [{ p: [0, 0, 0], ang: 0 }];

  return { cfg, mixFor, modelReady, preload, warm, loadLod, ready, ensure, urlOf, hasLo, GL, LOD, capFor, MODELS, MODE_MODEL, Z_ON, Z_FULL, Z_FAC, MAXZ, CAP, mix, iconAlpha, facilityOn, sizeScale, mpp, yawOf, layer, vehicles, facility, inView, byVehicle, hasDeck, thumb, thumbAlt, THUMB_ALT };
})();
