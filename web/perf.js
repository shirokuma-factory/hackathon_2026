'use strict';
/* 画質の自動調整とフレームの計測（SPEC §19.5）。
   目的は最高の fps ではなく、どの PC でも動きが一定であること。内蔵 GPU や高解像度の画面でもカクつかないよう、
   直近のフレーム時間から画質の段（高 / 中 / 低）を自動で上げ下げする。

   ■ 段ごとの設定
     高: 描画の解像度比 min(devicePixelRatio, 1.5)。施設の 3D は上限どおり。乗り物と施設は、画面で 40px より小さく見えるとき低ポリゴン版を使う。
     中: 解像度比 1。施設の上限を半分にし、施設は低ポリゴン版。
     低: 解像度比 1。乗り物も施設も低ポリゴン版。施設の 3D は出さない。ゆるやかな流れの本数を減らす。施設の glTF アニメーションは止める。
   ■ 自動で切り替える規則（段を選んだときは固定）
     ・直近 2 秒の p95 が 25ms を超えたら 1 段下げる。直近 5 秒の p95 が 14ms 未満なら 1 段上げる。
     ・切り替えのあとは 5 秒たつまで次の切り替えをしない（履歴も捨てる）。上げたあと 20 秒以内にまた下げたら、次に上げるまでの待ちを倍にする（15 秒から最大 120 秒）。
     ・最初の段は GPU の名前で決める（SwiftShader・Basic Render・llvmpipe は低、Intel・AMD の内蔵は中、それ以外は高）。
   ■ その他
     ・起動から 6 秒は自動の判定をしない（データの読み込みとモデルの先読みで一時的に重くなるため）。
     ・prefers-reduced-motion が有効なときは、ゆるやかな流れ（飾りの動き）を止める。
     ・frame() の JS 時間（長いフレームの原因の切り分け用）を PERF.js に残す。 */

const PERF = (() => {
  const LV = ['high', 'mid', 'low'];
  const LABEL = { auto: '自動', high: '高', mid: '中', low: '低' };
  const KEY = 'quality';
  const DROP_MS = 25, DROP_WIN = 2000, RAISE_MS = 14, RAISE_WIN = 5000, MIN_GAP = 5000, START_GRACE = 6000;
  const P = {
    pref: 'auto', // auto | high | mid | low
    level: 0, // 0 高, 1 中, 2 低
    gpu: '', gpuLevel: 0, reduced: false,
    frameNo: 0, frameStart: 0, // frame() の最初の時刻（予算の判定に使う）
    last: 0, lastSwitch: -1e9, raiseWait: 15000, lastRaise: -1e9, raiseBlock: 0,
    changes: [], // [{t, from, to, why}]
    js: { n: 0, sum: 0, max: 0, over8: 0 }, // frame() の JS 時間
    jsMarks: {}, // 区間ごとの最大 ms
    listeners: [],
  };
  // 直近のフレーム間隔（リング）
  const N = 720, dts = new Float32Array(N), ts = new Float64Array(N);
  let head = 0, count = 0, lastEval = 0;

  const clampLv = (v) => Math.max(0, Math.min(2, v));
  const dpr = () => (typeof devicePixelRatio === 'number' ? devicePixelRatio : 1);

  function detectGpu() {
    try {
      const c = document.createElement('canvas'), g = c.getContext('webgl2') || c.getContext('webgl');
      if (!g) return;
      const e = g.getExtension('WEBGL_debug_renderer_info');
      const name = e ? String(g.getParameter(e.UNMASKED_RENDERER_WEBGL)) : String(g.getParameter(g.RENDERER));
      P.gpu = name;
      if (/swiftshader|basic render|llvmpipe|software|softpipe/i.test(name)) P.gpuLevel = 2;
      else if (/intel|uhd|iris|radeon\(tm\)|radeon graphics|vega|apple|adreno|mali|powervr/i.test(name) && !/arc\s*[ab]\d|rtx|geforce|radeon rx|radeon pro/i.test(name)) P.gpuLevel = 1;
      else P.gpuLevel = 0;
      const ext = g.getExtension('WEBGL_lose_context');
      if (ext) ext.loseContext();
    } catch (err) { /* 判別できないときは高のまま */ }
  }

  // 解像度比。高だけ 1.5 まで（高解像度の画面で 3 倍の画素数になるのを避ける）
  const pixelRatio = () => (P.level === 0 ? Math.min(dpr(), 1.5) : 1);
  const capMul = () => (P.level === 0 ? 1 : P.level === 1 ? 0.5 : 0);
  const facilities3d = () => P.level < 2;
  const loFacilities = () => P.level >= 1;
  const loVehicles = () => P.level >= 2;
  const ambientMul = () => (P.reduced ? 0 : P.level === 2 ? 0.35 : 1);
  const pauseFacilityAnim = () => P.level >= 2;

  function setLevel(lv, why) {
    lv = clampLv(lv);
    if (lv === P.level) return;
    const now = performance.now();
    P.changes.push({ t: Math.round(now), from: LV[P.level], to: LV[lv], why });
    if (lv < P.level) P.lastRaise = now;
    P.level = lv;
    P.lastSwitch = now;
    count = 0; head = 0; // 切り替え前の履歴は捨てる
    for (const f of P.listeners) { try { f(P.level); } catch (e) { console.error(e); } }
  }
  function setPref(v) {
    if (!(v in LABEL)) v = 'auto';
    P.pref = v;
    try { localStorage.setItem(KEY, v); } catch (e) { /* 保存できなくても動く */ }
    P.lastSwitch = -1e9;
    setLevel(v === 'auto' ? P.gpuLevel : LV.indexOf(v), 'pref:' + v);
    for (const f of P.listeners) { try { f(P.level); } catch (e) { console.error(e); } }
  }

  // 直近 win ms の p95（足りないときは null）
  function p95(win, now, minN) {
    const vals = [];
    let span = 0;
    for (let i = 0; i < count; i++) {
      const k = (head - 1 - i + N) % N;
      if (now - ts[k] > win) break;
      vals.push(dts[k]); span += dts[k];
    }
    if (vals.length < minN || span < win * 0.85) return null;
    vals.sort((a, b) => a - b);
    return vals[Math.min(vals.length - 1, Math.floor(vals.length * 0.95))];
  }

  // 毎フレーム 1 回。now は requestAnimationFrame の時刻
  function tick(now) {
    P.frameNo++;
    P.frameStart = performance.now();
    if (P.last) {
      const dt = now - P.last;
      if (dt < 1000 && !document.hidden) { dts[head] = dt; ts[head] = now; head = (head + 1) % N; if (count < N) count++; }
    }
    P.last = now;
    if (!P.t0) P.t0 = now;
    // 起動から 6 秒は判定しない（データの読み込みとモデルの先読みで一時的に重いため）
    if (P.pref !== 'auto' || now - lastEval < 250 || now - P.t0 < START_GRACE) return;
    lastEval = now;
    if (now - P.lastSwitch < MIN_GAP) return;
    const d = p95(DROP_WIN, now, 3); // 1 秒に 1 フレームのような重さでも判定できるよう、下げるほうは 3 フレームから見る
    if (d !== null && d > DROP_MS && P.level < 2) { // 上げたすぐあとにまた下げたら、次に上げるまでを倍に待つ
      if (now - P.lastRaise < 20000) P.raiseWait = Math.min(120000, P.raiseWait * 2);
      P.raiseBlock = now + P.raiseWait;
      setLevel(P.level + 1, `p95 ${d.toFixed(1)}ms > ${DROP_MS}`);
      return;
    }
    if (P.level > 0 && now >= P.raiseBlock) {
      const r = p95(RAISE_WIN, now, 40);
      if (r !== null && r < RAISE_MS) setLevel(P.level - 1, `p95 ${r.toFixed(1)}ms < ${RAISE_MS}`);
    }
  }

  // frame() の JS 時間。frame() の最後に呼ぶ
  function endFrame() {
    const ms = performance.now() - P.frameStart, j = P.js;
    j.n++; j.sum += ms; if (ms > j.max) j.max = ms; if (ms > 8) j.over8++;
  }
  // 区間の時間（計測用）。名前ごとに最大を残す
  function mark(name, t0) {
    const ms = performance.now() - t0;
    if (!(P.jsMarks[name] >= ms)) P.jsMarks[name] = ms;
    return ms;
  }
  // 重い作業をこのフレームでしてよいか（フレームの先頭から 5ms 以内。1 フレームに重い作業が集中しないようにする）
  const canSpend = (ms = 5) => performance.now() - P.frameStart < ms;

  function snapshot() {
    return { pref: P.pref, level: LV[P.level], gpu: P.gpu, gpuLevel: LV[P.gpuLevel], reduced: P.reduced, dpr: dpr(), pixelRatio: pixelRatio(), changes: P.changes.slice(), js: { ...P.js, mean: P.js.n ? P.js.sum / P.js.n : 0 }, marks: { ...P.jsMarks } };
  }
  function resetStats() { P.js = { n: 0, sum: 0, max: 0, over8: 0 }; P.jsMarks = {}; }

  function onChange(f) { P.listeners.push(f); }

  // 画質の選択欄（#quality）。index.html の中にあるものに結びつける
  function bindUi() {
    const el = document.getElementById('quality');
    if (!el) return;
    el.value = P.pref;
    el.addEventListener('change', () => setPref(el.value));
  }

  function init() {
    detectGpu();
    try { const mq = matchMedia('(prefers-reduced-motion: reduce)'); P.reduced = mq.matches; mq.addEventListener('change', (e) => { P.reduced = e.matches; }); } catch (e) { /* なくてよい */ }
    let pref = 'auto';
    try { const v = localStorage.getItem(KEY); if (v && v in LABEL) pref = v; } catch (e) { /* なくてよい */ }
    // 計測用: ?tier=high|mid|low で固定、?tier=auto で自動
    try { const q = new URLSearchParams(location.search).get('tier'); if (q && q in LABEL) pref = q; } catch (e) { /* なくてよい */ }
    P.pref = pref;
    P.level = pref === 'auto' ? P.gpuLevel : LV.indexOf(pref);
    bindUi();
  }

  return {
    P, LV, LABEL, init, tick, endFrame, mark, canSpend, setPref, setLevel, snapshot, resetStats, onChange,
    get level() { return P.level; }, get name() { return LV[P.level]; }, get reduced() { return P.reduced; },
    pixelRatio, capMul, facilities3d, loFacilities, loVehicles, ambientMul, pauseFacilityAnim,
  };
})();
