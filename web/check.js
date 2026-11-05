'use strict';
/* この PC での動作確認（SPEC §19.5b）。?check=1 のときだけ app.js が読み込む。
   perfprobe.js の場面 a・b・c を 10 秒ずつ測り、判定と環境を表示する。結果はどこにも送らず、コピーして使う。 */

(() => {
  const VERSION = 'check v1';
  const SCEN = [
    ['a', '市場を見る・施設を表示して移動'],
    ['b', '送り方を比べる・止まって見る'],
    ['c', '送り方を比べる・日本全体から拡大'],
  ];
  const SEC = 10;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const NA = '—（このブラウザでは測れません）';

  const css = document.createElement('style');
  css.textContent = `
#chk { position: fixed; left: 50%; bottom: 56px; transform: translateX(-50%); z-index: 60; width: min(560px, calc(100vw - 32px)); max-height: calc(100vh - 80px); overflow: auto;
  padding: 16px 18px; background: var(--glass-solid, #0d141f); border: 1px solid var(--line-strong, rgba(255,255,255,.16)); border-radius: var(--radius, 14px);
  box-shadow: var(--shadow, 0 18px 50px rgba(0,0,0,.45)); color: var(--text, #e8eef8); font: 13px/1.6 "Noto Sans JP", system-ui, sans-serif; }
#chk[hidden] { display: none; }
#chk h2 { margin: 0 0 6px; font-size: 15px; font-weight: 700; }
#chk p { margin: 0 0 10px; color: var(--muted, #8f9db4); }
#chk button { font: inherit; font-weight: 700; padding: 7px 14px; margin: 0 8px 0 0; border-radius: 10px; cursor: pointer; color: var(--text, #e8eef8); background: var(--accent-soft, rgba(92,200,255,.16)); border: 1px solid var(--accent, #5cc8ff); }
#chk table { border-collapse: collapse; width: 100%; margin: 4px 0 10px; font-size: 12px; }
#chk th, #chk td { padding: 4px 6px; border-bottom: 1px solid var(--line, rgba(255,255,255,.09)); text-align: right; vertical-align: top; }
#chk th:first-child, #chk td:first-child { text-align: left; }
#chk th { color: var(--muted, #8f9db4); font-weight: 500; }
#chk th:last-child, #chk td:last-child { white-space: nowrap; text-align: center; }
#chk td.ok, #chk td.warn, #chk td.bad { font-size: 16px; }
#chk .ok { color: var(--ok, #55d6a6); font-weight: 700; } #chk .warn { color: var(--warn, #f0b64e); font-weight: 700; } #chk .bad { color: var(--error, #ff6b6b); font-weight: 700; }
#chk dl { margin: 0 0 10px; display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; font-size: 12px; }
#chk dt { color: var(--muted, #8f9db4); } #chk dd { margin: 0; word-break: break-all; }
#chk textarea { width: 100%; height: 150px; box-sizing: border-box; margin: 0 0 8px; font: 12px/1.5 monospace; color: var(--text, #e8eef8); background: rgba(0,0,0,.35); border: 1px solid var(--line-strong, rgba(255,255,255,.16)); border-radius: 8px; }
#chk .msg { color: var(--muted, #8f9db4); font-size: 12px; margin: 0 0 6px; min-height: 1em; }
`;
  document.head.appendChild(css);
  const box = document.createElement('div');
  box.id = 'chk';
  box.hidden = true;
  document.body.appendChild(box);

  let readyMs = null;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function judge(r) {
    if (r.p95 <= 20 && r.over50 <= 2) return ['○', 'ok'];
    if (r.p95 <= 34 && r.over50 <= 10) return ['△', 'warn'];
    return ['×', 'bad'];
  }
  function browserName() {
    const u = navigator.userAgent;
    let m;
    if ((m = u.match(/Edg\/(\d+)/))) return 'Edge ' + m[1];
    if ((m = u.match(/(?:Chrome|CriOS)\/(\d+)/))) return 'Chrome ' + m[1];
    if ((m = u.match(/Firefox\/(\d+)/))) return 'Firefox ' + m[1];
    if ((m = u.match(/Version\/(\d+[\d.]*).*Safari/))) return 'Safari ' + m[1];
    return u.slice(0, 40);
  }
  function osName() {
    const u = navigator.userAgent;
    return /Windows/.test(u) ? 'Windows' : /Mac OS X|Macintosh/.test(u) ? 'Mac' : /Android/.test(u) ? 'Android' : /iPhone|iPad/.test(u) ? 'iOS' : /Linux/.test(u) ? 'Linux' : '';
  }
  async function refreshHz() {
    const d = [];
    await new Promise((resolve) => {
      let last = 0; const t0 = performance.now();
      const f = (t) => { if (last) d.push(t - last); last = t; if (performance.now() - t0 < 1000) requestAnimationFrame(f); else resolve(); };
      requestAnimationFrame(f);
    });
    d.sort((a, b) => a - b);
    return d.length ? Math.round(1000 / d[d.length >> 1]) : null;
  }
  const lt = (r) => (r.longtasksOk ? String(r.longtasks.count) : NA);

  function envList(hz, results) {
    const r0 = results[0] || {}, last = results[results.length - 1] || {};
    const env = [
      ['GPU', r0.gpu || '不明'],
      ['ブラウザ', browserName() + (osName() ? '（' + osName() + '）' : '')],
      ['画面の大きさ', innerWidth + ' x ' + innerHeight + ' px（CSS）、拡大率 ' + (window.devicePixelRatio || 1)],
      ['画面の書き換え', hz ? '約 ' + hz + ' Hz' : '不明'],
      ['CPU の論理コア数', navigator.hardwareConcurrency || '不明'],
    ];
    if (navigator.deviceMemory) env.push(['メモリ', navigator.deviceMemory + ' GB 以上']);
    env.push(['画質の段', (r0.tierStart || '不明') + ' → ' + (last.tierEnd || '不明')]);
    env.push(['起動から画面が使えるまで', readyMs != null ? (readyMs / 1000).toFixed(1) + ' 秒' : '不明']);
    return env;
  }
  function textBlock(hz, results) {
    const L = [VERSION + '（' + new Date().toISOString().slice(0, 16).replace('T', ' ') + ' UTC）'];
    for (const r of results) {
      L.push(r.label + ': ' + judge(r)[0] + ' 毎秒' + r.fps + 'コマ、遅い上位5% ' + r.p95 + ' ms、50 ms超 ' + r.over50 + ' コマ、固まった処理 ' + lt(r));
    }
    for (const [k, v] of envList(hz, results)) L.push(k + ': ' + v);
    return L.join('\n');
  }

  function showStart(note) {
    box.hidden = false;
    box.innerHTML = '<h2>この PC での動作確認</h2><p>約 40 秒かかります。測定中は画面に触れないでください。' + (note ? '<br>' + esc(note) : '') + '</p><button type="button" id="chk-go">測定を始める</button>';
    box.querySelector('#chk-go').onclick = start;
  }
  function showProgress(i) {
    box.innerHTML = '<h2>この PC での動作確認</h2><p>測定中（' + i + ' / 3）：' + esc(SCEN[i - 1][1]) + '<br>画面に触れないでください。</p>';
  }
  function showResult(hz, results) {
    const rows = results.map((r) => {
      const [j, cls] = judge(r);
      return '<tr><td>' + esc(r.label) + '</td><td>' + r.fps + '</td><td>' + r.p95 + '</td><td>' + r.over50 + '</td><td>' + esc(lt(r)) + '</td><td class="' + cls + '">' + j + '</td></tr>';
    }).join('');
    const env = envList(hz, results).map(([k, v]) => '<dt>' + esc(k) + '</dt><dd>' + esc(v) + '</dd>').join('');
    const text = textBlock(hz, results);
    box.innerHTML = '<h2>この PC での動作確認：結果</h2>'
      + '<table><tr><th>場面</th><th>毎秒のコマ数</th><th>遅い上位 5%（ms）</th><th>50 ms を超えたコマ</th><th>画面が固まった処理</th><th>判定</th></tr>' + rows + '</table>'
      + '<p>○：なめらか　△：ところどころ引っかかる　×：重い</p>'
      + '<dl>' + env + '</dl>'
      + '<div class="msg" id="chk-msg"></div>'
      + '<button type="button" id="chk-copy">結果をコピー</button><button type="button" id="chk-again">もう一度測る</button>';
    const msg = box.querySelector('#chk-msg');
    box.querySelector('#chk-again').onclick = start;
    box.querySelector('#chk-copy').onclick = async () => {
      let ok = false;
      try { await navigator.clipboard.writeText(text); ok = true; } catch (e) { /* 下の手動コピーへ */ }
      if (ok) { msg.textContent = 'コピーしました。そのまま貼り付けてください。'; return; }
      let ta = box.querySelector('#chk-ta');
      if (!ta) { ta = document.createElement('textarea'); ta.id = 'chk-ta'; ta.readOnly = true; ta.value = text; msg.after(ta); }
      ta.focus(); ta.select();
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      msg.textContent = ok ? 'コピーしました。そのまま貼り付けてください。' : '自動でコピーできませんでした。下の文字を選んだまま、コピー（Ctrl+C / ⌘C）してください。';
    };
  }

  let busy = false;
  async function start() {
    if (busy) return;
    busy = true;
    try {
      const A = window.__app, P = window.__perf;
      const hz = await refreshHz();
      const results = [];
      for (let i = 0; i < SCEN.length; i++) {
        showProgress(i + 1);
        const r = await P.run(SCEN[i][0], SEC);
        // Safari には longtask がないので、観測できるかどうかを別に持つ
        r.longtasksOk = typeof PerformanceObserver !== 'undefined' && (PerformanceObserver.supportedEntryTypes || []).indexOf('longtask') >= 0;
        r.label = SCEN[i][1];
        results.push(r);
      }
      try { A.setView('market'); } catch (e) { /* 戻せなくても結果は出す */ }
      showResult(hz, results);
    } catch (e) {
      console.error(e);
      showStart('測定に失敗しました：' + e.message);
    } finally { busy = false; }
  }

  (async () => {
    for (let i = 0; i < 600 && !(window.__app && window.__perf && document.querySelector('#loading.done')); i++) await sleep(200);
    readyMs = performance.now();
    await sleep(500);
    showStart();
  })();
})();
