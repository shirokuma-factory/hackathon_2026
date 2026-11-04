'use strict';
/* 動きの滑らかさの計測（SPEC §19.5）。画面は ?perf=1 で読み込む（app.js が起動後にこのファイルを足す）。
   使い方:
     http://localhost:8600/index.html?perf=1&scenario=b&tier=high   → 起動して自動で場面 b を測る
     window.__perf.run('a' | 'b' | 'c', 秒数)                         → 手で呼ぶ（Promise で結果を返す）
   場面:
     a 市場を見る。東京湾 ズーム 13 で、ゆっくりした移動（easeTo）を 10 秒
     b 送り方を比べる。新宿区 → 大阪市中央区の再生。ズーム 12 で 10 秒
     c 比べる画面で、ズーム 5 から 12 への flyTo（乗り物の 3D は 9 から、施設の 3D は 12 で出る）
   結果は window.__perf.result と window.__perf.all に入る（median / p95 / p99 は フレーム時間 ms、over33 / over50 は件数）。
   PERF（perf.js）がなくても動く（変更前の画面と比べるため）。 */

(() => {
  const S = window.__perf = { running: false, done: false, result: null, all: {}, log: [] };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const pct = (a, q) => (a.length ? a[Math.min(a.length - 1, Math.floor(a.length * q))] : 0);
  const waitFor = async (f, ms, what) => { const t0 = performance.now(); while (performance.now() - t0 < ms) { try { if (f()) return true; } catch (e) { /* 待つ */ } await sleep(150); } S.log.push('timeout: ' + what); return false; };

  function gpuName() {
    try {
      const g = document.createElement('canvas').getContext('webgl2'), e = g.getExtension('WEBGL_debug_renderer_info');
      return e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : g.getParameter(g.RENDERER);
    } catch (e) { return ''; }
  }

  // sec 秒の間、フレーム間隔・長いタスク・段・S.t の進みを記録する。during はその間に始める動き（Promise は待たない）
  async function record(name, sec, during, extra) {
    const dts = [], stamps = [], tvals = [], longtasks = [];
    let po = null;
    try {
      po = new PerformanceObserver((l) => { for (const e of l.getEntries()) longtasks.push({ at: Math.round(e.startTime), ms: Math.round(e.duration) }); });
      po.observe({ entryTypes: ['longtask'] });
    } catch (e) { /* 使えないときは空 */ }
    if (typeof PERF !== 'undefined') PERF.resetStats();
    const tier0 = typeof PERF !== 'undefined' ? PERF.name : '(なし)';
    await new Promise((resolve) => {
      let last = 0;
      const t0 = performance.now();
      if (during) during();
      const f = (t) => {
        if (last) { dts.push(t - last); stamps.push(t); tvals.push(window.__app ? window.__app.S.t : 0); }
        last = t;
        if (t - t0 < sec * 1000) requestAnimationFrame(f); else resolve();
      };
      requestAnimationFrame(f);
    });
    if (po) po.disconnect();
    const sorted = [...dts].sort((a, b) => a - b);
    const total = dts.reduce((a, b) => a + b, 0);
    // 再生の進み（S.t の増分 ÷ フレーム時間）のばらつき。時間で進めていれば一定になる
    let speedDev = null;
    if (name === 'b') {
      const sp = [];
      for (let i = 1; i < tvals.length; i++) { const d = tvals[i] - tvals[i - 1], dt = stamps[i] - stamps[i - 1]; if (d > 0 && dt < 100) sp.push(d / dt); }
      sp.sort((a, b) => a - b);
      if (sp.length > 20) { const m = sp[sp.length >> 1]; speedDev = { median: m, p05: sp[Math.floor(sp.length * 0.05)] / m, p95: sp[Math.floor(sp.length * 0.95)] / m, n: sp.length }; }
    }
    const res = {
      scenario: name, sec, frames: dts.length, fps: +(dts.length / (total / 1000)).toFixed(1),
      median: +pct(sorted, 0.5).toFixed(1), p95: +pct(sorted, 0.95).toFixed(1), p99: +pct(sorted, 0.99).toFixed(1), max: +(sorted[sorted.length - 1] || 0).toFixed(1),
      over33: dts.filter((d) => d > 33.4).length, over50: dts.filter((d) => d > 50).length,
      longtasks: { count: longtasks.length, totalMs: longtasks.reduce((a, b) => a + b.ms, 0), maxMs: Math.max(0, ...longtasks.map((l) => l.ms)) },
      tierStart: tier0, tierEnd: typeof PERF !== 'undefined' ? PERF.name : '(なし)', perf: typeof PERF !== 'undefined' ? PERF.snapshot() : null,
      zoom: window.__app ? +window.__app.map().getZoom().toFixed(2) : null, speedDev, gpu: gpuName(), dpr: devicePixelRatio, ...extra,
    };
    S.all[name] = res; S.result = res;
    console.log('PERF ' + JSON.stringify({ ...res, perf: undefined }));
    return res;
  }

  async function setupCompare() {
    const A = window.__app;
    A.setView('compare');
    await sleep(300);
    if (A.S.origin !== '13104') await A.setOrigin('13104');
    const i = A.S.idx.codes.indexOf('27128');
    A.select(i, { fly: false });
    A.map().jumpTo({ center: [139.60, 35.45], zoom: 12 });
    A.S.t = 0; A.S.playing = true;
    await waitFor(() => A.Rt.hasSets(), 20000, 'route');
    await sleep(600);
  }

  async function run(name, sec = 10) {
    const A = window.__app;
    S.running = true; S.done = false;
    try {
      const m = A.map();
      let res;
      if (name === 'a') {
        A.setView('market');
        m.jumpTo({ center: [139.80, 35.62], zoom: 13 });
        try { const b = document.querySelector('.tgl[data-toggle="fac"]'); if (b && b.getAttribute('aria-pressed') !== 'true') b.click(); } catch (e) { /* 施設の切り替えがなくてもよい */ }
        await sleep(6000);
        res = await record('a', sec, () => m.easeTo({ center: [139.90, 35.60], duration: sec * 1000, easing: (t) => t, essential: true }));
      } else if (name === 'b') {
        await setupCompare();
        await sleep(6000);
        res = await record('b', sec);
      } else if (name === 'c') {
        await setupCompare();
        m.jumpTo({ center: [139.60, 35.45], zoom: 5 });
        await sleep(12000); // 起動後のアイドル時の先読みが終わる時間
        res = await record('c', sec, () => m.flyTo({ center: [139.60, 35.45], zoom: 12, duration: (sec - 1) * 1000, essential: true, curve: 1.2 }));
      } else throw new Error('unknown scenario ' + name);
      return res;
    } finally { S.running = false; S.done = true; }
  }
  S.run = run;

  const q = new URLSearchParams(location.search), sc = q.get('scenario');
  if (sc) {
    (async () => {
      await waitFor(() => window.__app, 60000, 'app');
      await sleep(Number(q.get('settle') || 3000));
      await run(sc, Number(q.get('sec') || 10));
    })();
  }
})();
