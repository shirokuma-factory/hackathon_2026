'use strict';
/* 貨物駅・港・フェリー航路の層（SPEC §15.5）。データは tools/build_web_data.py が作った web/data/hubs.json で、
   最寄りの拠点と直線距離も Python（geo.nearest_hub）が計算済み。ここでは表示するだけで、距離は計算しない。
   出典: © OpenStreetMap contributors（ODbL 1.0）。全駅・全港ではなく、OSM に登録された主なもの。 */

const Hubs = (() => {
  const H = { data: null, on: false, sel: null /* 'rail' | 'ferry' | null */ };
  const svg = (body) => 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">${body}</svg>`);
  const ICON_SQUARE = { url: svg('<rect x="5" y="5" width="22" height="22" rx="3" fill="#fff"/>'), width: 32, height: 32, mask: true };
  const ICON_ARROW = { url: svg('<path d="M16 3 L28 27 L16 21 L4 27 Z" fill="#fff"/>'), width: 32, height: 32, mask: true };
  const C_ST = [236, 200, 110], C_PT = [118, 206, 236], C_RT = [118, 176, 222];
  H.ICON_ARROW = ICON_ARROW;

  async function load() {
    const r = await fetch('data/hubs.json');
    if (!r.ok) throw new Error('hubs.json ' + r.status);
    H.data = await r.json();
    const d = H.data;
    d.st = d.stations.map(([name, lon, lat]) => ({ name, p: [lon, lat], k: 'st' }));
    d.pt = d.terminals.map(([name, lon, lat, lv]) => ({ name, p: [lon, lat], lv, k: 'pt' }));
    d.pt1 = d.pt.filter((x) => x.lv >= 1);
    d.pt2 = d.pt.filter((x) => x.lv >= 2);
  }

  // 出発地・着地ごとの最寄りの拠点（Python の計算結果）。kind は 'rail' か 'ferry'
  function nearest(originCode, destIdx) {
    const d = H.data;
    if (!d || !d.origin[originCode]) return null;
    const o = d.origin[originCode];
    return {
      rail: { o: { hub: d.st[o.st[0]], km: o.st[1] }, d: { hub: d.st[d.dest.st[destIdx]], km: d.dest.st_km[destIdx] } },
      ferry: { o: { hub: d.pt[o.pt[0]], km: o.pt[1] }, d: { hub: d.pt[d.dest.pt[destIdx]], km: d.dest.pt_km[destIdx] } },
    };
  }

  function line(kind, originCode, destIdx, a, b) {
    const n = nearest(originCode, destIdx);
    if (!n) return null;
    const x = n[kind];
    return { path: [a, x.o.hub.p, x.d.hub.p, b], hubs: [x.o.hub, x.d.hub], kind };
  }

  const dashExt = () => (typeof deck.PathStyleExtension === 'function' ? [new deck.PathStyleExtension({ dash: true })] : []);
  const collExt = () => (typeof deck.CollisionFilterExtension === 'function' ? [new deck.CollisionFilterExtension()] : []);

  function textLayer(id, data, minZoom, zoom, color) {
    if (zoom < minZoom) return null;
    return new deck.TextLayer({
      id, data, getPosition: (d) => d.p, getText: (d) => d.name, getSize: 11.5, sizeUnits: 'pixels', getColor: color,
      getPixelOffset: [0, 15], getTextAnchor: 'middle', getAlignmentBaseline: 'top', fontFamily: '"Noto Sans JP", sans-serif',
      characterSet: 'auto', outlineWidth: 3, outlineColor: [8, 12, 20, 235], fontSettings: { sdf: true }, billboard: true,
      extensions: collExt(), collisionEnabled: true, getCollisionPriority: (d) => (d.lv || 3), collisionGroup: id,
    });
  }

  // 層の一覧。opts.line が渡されたとき（鉄道・フェリーのカードを選んだとき）は、点線とその両端の拠点を重ねる
  function layers(zoom, opts = {}) {
    const L = deck, out = [];
    const d = H.data;
    if (!d) return out;
    if (H.on) {
      const ports = zoom < 6.2 ? d.pt2 : zoom < 8 ? d.pt1 : d.pt;
      out.push(new L.PathLayer({
        id: 'hub-routes', data: d.routes, getPath: (r) => r.p, getColor: [...C_RT, 150], getWidth: 1.1, widthUnits: 'pixels', widthMinPixels: 1,
        extensions: dashExt(), getDashArray: [3, 3], dashJustified: true, pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 120],
      }));
      out.push(new L.ScatterplotLayer({
        id: 'hub-ports', data: ports, getPosition: (x) => x.p, radiusUnits: 'pixels', getRadius: (x) => (x.lv >= 2 ? 4.6 : x.lv === 1 ? 3.6 : 2.4),
        filled: true, getFillColor: [...C_PT, 55], stroked: true, getLineColor: [...C_PT, 230], lineWidthUnits: 'pixels', getLineWidth: 1.3,
        pickable: true, updateTriggers: { getRadius: [zoom < 6.2 ? 2 : zoom < 8 ? 1 : 0] },
      }));
      out.push(new L.IconLayer({
        id: 'hub-stations', data: d.st, getPosition: (x) => x.p, getIcon: () => ICON_SQUARE, getSize: 12, sizeUnits: 'pixels',
        getColor: [...C_ST, 245], pickable: true,
      }));
      const t1 = textLayer('hub-st-names', d.st, 5.6, zoom, [...C_ST, 235]);
      const t2 = textLayer('hub-pt-names2', d.pt2, 6.4, zoom, [...C_PT, 225]);
      const t3 = textLayer('hub-pt-names1', d.pt1, 8, zoom, [...C_PT, 215]);
      for (const t of [t1, t2, t3]) if (t) out.push(t);
      // 3D モデル（ズーム 12 以上、画面に入るものだけ。models3d.js）: 貨物駅 → ターミナル、港 → クレーン（大きい港は コンテナヤードも）
      if (M3D.facilityOn(zoom)) {
        if (!d.cargoPt) {
          // 旅客用の船着場・乗り場は除く（名前で判断）。コンテナヤードは、フェリー・ふ頭・コンテナ・貨物を名前に含む大きな港だけ
          const pax = /船着場|乗り場|乗場|桟橋|渡船|海上バス|客船|クルーズ/;
          d.cargoPt = d.pt1.filter((x) => !pax.test(x.name));
          d.yardPt = d.pt2.filter((x) => !pax.test(x.name) && /フェリー|ふ頭|埠頭|コンテナ|貨物/.test(x.name)).map((x) => ({ p: [x.p[0], x.p[1] + 0.0006] }));
        }
        for (const l of [M3D.facility('hub3d-st', 'terminal', d.st, zoom), M3D.facility('hub3d-crane', 'port_crane', d.cargoPt, zoom), M3D.facility('hub3d-yard', 'container_yard', d.yardPt, zoom)]) if (l) out.push(l);
      }
    }
    const ln = opts.line;
    if (ln) {
      const col = ln.kind === 'rail' ? C_ST : C_PT;
      out.push(new L.PathLayer({
        id: 'hub-line', data: [ln], getPath: (x) => x.path, getColor: [...col, 235], getWidth: 2.2, widthUnits: 'pixels',
        extensions: dashExt(), getDashArray: [4, 3], dashJustified: true, capRounded: true,
      }));
      out.push(new L.ScatterplotLayer({
        id: 'hub-line-ends', data: ln.hubs, getPosition: (x) => x.p, radiusUnits: 'pixels', getRadius: 8, filled: true, getFillColor: [...col, 70],
        stroked: true, getLineColor: [...col, 255], lineWidthUnits: 'pixels', getLineWidth: 2,
      }));
      const t = textLayer('hub-line-names', ln.hubs, 0, zoom, [255, 255, 255, 245]);
      if (t) out.push(t);
    }
    return out;
  }

  // 地図の上のマウスの位置にある拠点の説明（pick の結果から）
  function hoverText(info) {
    const o = info && info.object;
    if (!o || !info.layer) return null;
    const id = info.layer.id;
    if (id === 'hub-stations') return { t: o.name, s: '貨物駅（OSM に登録された主なもの）', m: 'terminal' };
    if (id === 'hub-ports') return { t: o.name, s: o.lv ? 'フェリーなどの港（航路の端）' : '港・船着場', m: 'port_crane' };
    if (id === 'hub-routes') return { t: o.n, s: `フェリー航路 ${o.km}km（OSM、30km 以上）` };
    return null;
  }

  Object.assign(H, { load, nearest, line, layers, hoverText });
  return H;
})();
