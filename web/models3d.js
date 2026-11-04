'use strict';
/* 3D モデルの表示（SPEC §19）。Blender で作った glTF（web/models/*.glb）を、拡大したときだけ deck.gl の ScenegraphLayer で出す。
   縮小したときは従来の 2D アイコンのままで、ズームで切り替える（LOD）。

   ■ LOD の規則（ズームは MapLibre の値）
     ・乗り物: ズーム Z_ON(9.0) 未満は 2D アイコンだけ。Z_ON〜Z_FULL(9.8) は 2D を薄くしながら 3D を濃くする（交差）。Z_FULL 以上は 3D だけ。
     ・施設（倉庫・駅・港・空港）: ズーム Z_FAC(12) 以上で 3D。画面内に入るものだけを、モデルごとに最大 CAP(300) 件まで（中心に近い順。ズーム 14 未満はさらに絞る）。
     ・地図の最大ズームは app.js で 12 だが、それでは実寸のモデルが見えないので、この層が使われるときに 16 まで上げる（M3D.mix 内）。

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

  const clamp01 = (v) => Math.max(0, Math.min(1, v));
  const mpp = (zoom, lat) => (78271.517 * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, zoom);
  const smooth = (t) => t * t * (3 - 2 * t);

  const hasDeck = () => typeof deck !== 'undefined' && typeof deck.ScenegraphLayer === 'function';

  // 3D の濃さ（0 = 2D だけ、1 = 3D だけ）。地図の最大ズームもここで広げる
  function mix(zoom) {
    if (!hasDeck()) return 0;
    if (typeof map !== 'undefined' && map && map.getMaxZoom() < MAXZ) map.setMaxZoom(MAXZ);
    return smooth(clamp01((zoom - Z_ON) / (Z_FULL - Z_ON)));
  }
  const iconAlpha = (zoom) => 1 - mix(zoom); // 2D アイコンに掛ける濃さ
  const facilityOn = (zoom) => hasDeck() && zoom >= Z_FAC;

  const centerLat = () => (typeof map !== 'undefined' && map ? map.getCenter().lat : 36);
  function sizeScale(key, zoom, lat) {
    const m = MODELS[key];
    return Math.max(1, (m.px * mpp(zoom, lat ?? centerLat())) / m.len);
  }
  const yawOf = (ang) => YAW_SIGN * (ang || 0) + YAW_OFFSET;

  /* ScenegraphLayer の作成。data の要素: { p: [経度, 緯度, 高さ m], ang: 北から反時計回りの度, a: 0〜1 の濃さ（省略可）, yaw: 固定の向き（省略可） }
     opts: zoom（必須）, opacity（既定 1）, color（既定 白 = 元の色）, pickable */
  function layer(id, key, data, opts = {}) {
    if (!hasDeck() || !MODELS[key] || !data.length) return null;
    const m = MODELS[key], zoom = opts.zoom, op = opts.opacity ?? 1;
    if (op <= 0.01) return null;
    const c = opts.color || [255, 255, 255];
    return new deck.ScenegraphLayer({
      id, data, scenegraph: DIR + m.f,
      getPosition: (d) => d.p,
      getOrientation: opts.orient || ((d) => [0, d.yaw ?? yawOf(d.ang), ROLL]),
      getColor: (d) => [c[0], c[1], c[2], Math.round(255 * (d.a ?? 1))],
      sizeScale: sizeScale(key, zoom, opts.lat) * (opts.k ?? 1),
      _lighting: 'pbr',
      _animations: m.anim ? { '*': { playing: true, speed: 1 } } : undefined,
      opacity: op,
      pickable: !!opts.pickable,
      parameters: { depthCompare: 'less-equal' },
      updateTriggers: { getColor: [c.join(), opts.trig ?? 0] },
    });
  }

  // 乗り物（経路の上を動くもの）。手段 mode か、モデル名 key で指定する
  function vehicles(id, modeOrKey, data, zoom, opts = {}) {
    return layer(id, MODELS[modeOrKey] ? modeOrKey : MODE_MODEL[modeOrKey], data, { ...opts, zoom, opacity: mix(zoom) * (opts.opacity ?? 1) });
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
  const capFor = (zoom) => Math.round((zoom < 13 ? 120 : zoom < 14 ? 200 : CAP) * cfg.capScale);
  function inView(points, getP, zoom) {
    getP = getP || ((d) => d.p);
    if (typeof map === 'undefined' || !map) return [];
    const b = map.getBounds(), c = map.getCenter();
    const w = b.getWest(), e = b.getEast(), s = b.getSouth(), n = b.getNorth();
    const pad = (e - w) * 0.1;
    const out = [];
    for (const d of points) {
      const p = getP(d);
      if (p[0] >= w - pad && p[0] <= e + pad && p[1] >= s - pad && p[1] <= n + pad) out.push(d);
    }
    const cap = capFor(zoom ?? 99);
    if (out.length <= cap) return out;
    const k = Math.cos((c.lat * Math.PI) / 180);
    const dist = (d) => { const p = getP(d); return ((p[0] - c.lng) * k) ** 2 + (p[1] - c.lat) ** 2; };
    return out.map((d) => [dist(d), d]).sort((a, b2) => a[0] - b2[0]).slice(0, cap).map((x) => x[1]);
  }
  function facility(id, key, points, zoom, opts = {}) {
    if (!facilityOn(zoom) || !points || !points.length) return null;
    mix(zoom); // 地図の最大ズームを広げる
    const vis = inView(points, opts.getP, zoom);
    return layer(id, key, vis.map((d) => ({ p: d.p || opts.getP(d), yaw: d.yaw ?? opts.yaw ?? 0, a: 1 })), { ...opts, zoom, opacity: 1 });
  }

  return { cfg, capFor, MODELS, MODE_MODEL, Z_ON, Z_FULL, Z_FAC, MAXZ, CAP, mix, iconAlpha, facilityOn, sizeScale, mpp, yawOf, layer, vehicles, facility, inView, byVehicle, hasDeck };
})();
