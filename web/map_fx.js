'use strict';
/* 地図の動きと光（第 8 部 r1）。計算や判定はしない。見た目の部品だけ。
   - ms(n): 動きを減らす設定（prefers-reduced-motion）のときは 0、そうでなければ n ミリ秒。カメラの移動時間と deck.gl の transitions に使う。
   - ease: ゆるやかに止まるカメラの動き（減速）。
   - effects(): 3D の乗り物に当てる光（環境光と、斜め上からの光 1 つ）。deck.gl の LightingEffect が無い版では空。 */
const MapFx = (() => {
  const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
  const reduced = () => mq.matches;
  const ms = (n) => (mq.matches ? 0 : n);
  const ease = (t) => 1 - Math.pow(1 - t, 3);
  let eff = null;
  function effects() {
    if (eff) return eff;
    eff = [];
    try {
      const d = window.deck;
      const A = d.AmbientLight || d._AmbientLight, D = d.DirectionalLight || d._DirectionalLight;
      if (d.LightingEffect && A && D) {
        eff = [new d.LightingEffect({
          ambient: new A({ color: [235, 240, 255], intensity: 3.5 }),
          sun: new D({ color: [255, 245, 230], intensity: 3.5, direction: [-1.2, -2.2, -3] }),
        })];
      }
    } catch (e) { eff = []; }
    return eff;
  }
  /* 案内の表示（全国の物流傾向・空きを探す・送り方を比べる）の地図は、傾けた 3D で見せる（pitch 46°、わずかに回す）。全部を見るは今までどおり真上から。
     rest() は、いまの表示で「止まっているときの傾きと向き」。カメラの移動と、傾きを 0 に戻す処理（routes.js・muni.js）がこれを使う。動きを減らす設定でも傾きは保つ（動かさないだけ）。 */
  const GUIDE_PITCH = 0, GUIDE_BEARING = 0;   // 地図はすべて真上から見る（作者の指示）。案内の表示でも傾けない・回さない
  const guided3d = () => { const c = document.body.classList; return c.contains('guided') && (c.contains('view-market') || c.contains('view-find') || c.contains('view-compare')); };
  const rest = () => (guided3d() ? { pitch: GUIDE_PITCH, bearing: GUIDE_BEARING } : { pitch: 0, bearing: 0 });
  /* 区間を選んだときの傾きと回し方（2 秒以内。ゆっくり減速）。区間が東西に長いほど少し回し、斜めから見下ろす。全部を見るでは何も足さない（空） */
  function selectCam(a, b, ms2 = 1700) {
    if (!guided3d()) return {};
    const dx = ((b[0] - a[0]) * Math.cos((((a[1] + b[1]) / 2) * Math.PI) / 180)), dy = b[1] - a[1];
    const ang = (Math.atan2(dx, dy) * 180) / Math.PI;   // 北から時計回り
    void ang;
    return { pitch: 0, bearing: 0, ...cam(Math.min(ms2, 1950), { curve: 1.4 }) };
  }
  // カメラの移動（ゆっくり、止まる前に減速）。reduced のときは 0
  const cam = (n, extra = {}) => ({ duration: ms(n), easing: ease, ...extra });
  return { reduced, ms, ease, effects, cam, rest, selectCam, guided3d, GUIDE_PITCH };
})();
