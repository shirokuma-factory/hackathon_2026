'use strict';
/* 画面に入る分だけに絞った配列を作る（SPEC §19.5）。
   deck.gl は、画面の外にあるものも頂点をすべて処理する。市区町村の境界（約 22 万点）、県境（約 6 万点）、鉄道網（約 2 万点）を
   毎フレーム全部処理すると、拡大したときに無駄が大きい。ここでは次の規則で、絞った配列を返す。
     ・絞り込みの範囲（cov）は、画面の幅と高さの 3 倍（画面の外側に 1 画面ぶんの余白）。画面が cov の中にある間は、同じ配列（同じ参照）を返す。
       deck.gl はデータの再計算をしない。
     ・画面が cov からはみ出したとき、または cov が画面の 6 倍より広くなったとき（拡大したとき。ただし地図が動いている間は待ち、止まってから）だけ作り直し、v を進める。
     ・動いている間も、はみ出した次のフレームで作り直すので、端が欠けて見えることはない。
   items はこの関数が作った配列を同じ参照のまま保持する。呼び出し側は items と v だけを見ればよい。 */

const Cull = (() => {
  const MEM = new Map(); // key → { src, bb, cov, items, v }

  function bboxOfCoords(coords) {
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    const walk = (a) => {
      if (typeof a[0] === 'number') { if (a[0] < x0) x0 = a[0]; if (a[0] > x1) x1 = a[0]; if (a[1] < y0) y0 = a[1]; if (a[1] > y1) y1 = a[1]; return; }
      for (const c of a) walk(c);
    };
    walk(coords);
    return [x0, y0, x1, y1];
  }
  // GeoJSON の Feature の外接矩形 [西, 南, 東, 北]
  const featureBox = (f) => bboxOfCoords(f.geometry.coordinates);

  /* key: 呼び出し側ごとの名前。src: 元の配列（参照が変わったら作り直す）。boxOf: 要素の外接矩形 [西, 南, 東, 北] を返す関数
     map がまだないとき、または画面の範囲を取れないときは、絞らずに src をそのまま返す。 */
  function view(key, src, boxOf, map) {
    if (!src || !map) return { items: src || [], v: 0 };
    let c = MEM.get(key);
    if (!c || c.src !== src) {
      c = { src, bb: src.map(boxOf), cov: null, items: src, v: (c ? c.v : 0) + 1 };
      MEM.set(key, c);
    }
    const b = map.getBounds(), w = b.getWest(), e = b.getEast(), s = b.getSouth(), n = b.getNorth();
    const vw = e - w, vh = n - s;
    const k = c.cov;
    // 拡大して範囲が広すぎるだけのとき（画面は範囲の中に収まっている）は、動いている間は作り直さない。作り直すと市区町村の面の三角形分割が走り、
    // 1 フレームが 40〜90ms かかるため。動きが止まってから 1 回だけ作り直す。画面が範囲からはみ出したときは、動いていても作り直す
    const out = !k || w < k[0] || s < k[1] || e > k[2] || n > k[3];
    const need = out || (k[2] - k[0] > vw * 6 && !(map.isMoving && map.isMoving()));
    // 作り直しが重なったときは、このフレームで 5ms 使っていたら次のフレームにまわす（余白が 1 画面分あるので 1 フレーム遅れても端は欠けない）
    if (need && (!k || typeof PERF === 'undefined' || PERF.canSpend(5))) {
      const t0 = performance.now();
      const cw = w - vw, cs = s - vh, ce = e + vw, cn = n + vh;
      const items = [];
      for (let i = 0; i < src.length; i++) {
        const q = c.bb[i];
        if (q[2] >= cw && q[0] <= ce && q[3] >= cs && q[1] <= cn) items.push(src[i]);
      }
      c.cov = [cw, cs, ce, cn];
      c.items = items.length === src.length ? src : items;
      c.v++;
      if (typeof PERF !== 'undefined') PERF.mark('cullRebuild', t0);
    }
    return c;
  }
  return { view, featureBox, bboxOfCoords };
})();
