/* 荷待ち協議の書面の「Claude で説明文を作る」（SPEC §53）。waiting.html と地図の画面（own.js）が使う。
   サーバーで開いたとき（http）で、/api/ai/status が available のときだけ、ボタンを出す。
   送る前に、送る内容を一覧にして確認を取る。キーはサーバーの環境変数にあり、このファイルには出てこない。
   waiting.html には通信の記述を置かない決まり（SPEC §23.7）なので、通信はこのファイルだけが行い、サーバーで開いたときだけ読み込まれる。 */
(function (root) {
  'use strict';
  const OFF_LINE = '説明文の自動作成はサーバーで開いたときだけ使えます（今は型どおりの書面です）';
  const FIELD_LABEL = { date: '日付', place: '場所', arrive: '到着', reception: '受付', appointed: '指定の到着時刻', start: '荷役開始', end: '荷役終了',
    handler: '荷役をした人', shipper_fault: '荷主等の都合', wait_min: '待機（分）', handling_min: '荷役（分）', amount: '参考額（円）' };
  const STEP_TEXT = {
    check: { model: 'Haiku 5.5', run: 'が記録を点検中', done: 'が記録を点検' },
    draft: { model: 'Sonnet 5.5', run: 'が説明文を下書き中', done: 'が説明文を下書き' },
    verify: { model: 'Opus 5.5', run: 'が数字と根拠を確かめ中', done: 'が数字と根拠を確かめ' },
  };
  const ORDER = ['check', 'draft', 'verify'];
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const usd = (x) => `約 $${(x < 0.01 ? x.toFixed(4) : x.toFixed(3))}`;
  const memo = { key: '', events: [], result: null };
  let statusP = null;

  function injectStyle() {
    if (document.getElementById('ai-style')) return;
    const st = document.createElement('style');
    st.id = 'ai-style';
    st.textContent = '.ai-box{margin:10px 0}.ai-box .ai-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap}' +
      '.ai-box .ai-confirm,.ai-box .ai-result{margin-top:8px;padding:10px 12px;border:1px solid var(--line,#ccc);border-radius:10px;background:var(--card,transparent)}' +
      '.ai-box .ai-confirm ul{margin:4px 0 8px;padding-left:20px}.ai-box ol.ai-steps{margin:8px 0;padding-left:22px}' +
      '.ai-box .ai-text{white-space:pre-wrap;margin:6px 0}.ai-box .ai-badge{display:inline-block;padding:1px 10px;border-radius:999px;font-weight:700;font-size:12px;border:1px solid currentColor}' +
      '.ai-box .ai-badge.ok{color:var(--accent,#1b5fbf)}.ai-box .ai-badge.ng{color:var(--warn,#b3261e)}.ai-box .ai-cost{color:var(--muted,#5b6475);font-size:12px}';
    document.head.appendChild(st);
  }

  function getStatus() {
    if (!/^https?:$/.test(location.protocol)) return Promise.resolve(null);
    if (!statusP) {
      statusP = fetch('/api/ai/status', { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    }
    return statusP;
  }

  function confirmHtml(facts) {
    const n = facts.rows.length;
    const keys = Object.keys(FIELD_LABEL).filter((k) => facts.rows.some((r) => r[k] !== undefined && r[k] !== ''));
    const hist = (facts.history || []).length;
    const cons = facts.consult || [];
    return `<b>次の内容を Anthropic の API に送ります</b>
      <ul><li>記録 ${n} 行（${keys.map((k) => FIELD_LABEL[k]).join('、')}）</li>
        <li>相手の名前: ${esc(facts.partner)}（入力どおり）、期間: ${esc(facts.period)}</li>
        <li>合計の数字と、計算の根拠の文（告示の表の単価と出典）</li>
        ${cons.length ? `<li>協議の項目 ${cons.length} 件（${esc(String(cons[0]).slice(0, 40))}${String(cons[0]).length > 40 ? '…' : ''} など）</li>` : ''}
        ${hist ? `<li>これまでの協議の履歴 ${hist} 件</li>` : ''}</ul>
      <p class="small">送らないもの: 車番、貼り付けた元の文、ファイル、ほかの相手の行。送った内容は保存しません。</p>
      <div class="bar"><button type="button" class="primary" data-ai="send">送る</button><button type="button" data-ai="cancel">やめる</button></div>`;
  }

  function stepsHtml(events) {
    const by = {};
    events.forEach((e) => { by[e.step] = Object.assign(by[e.step] || {}, e); });
    return ORDER.filter((s) => by[s]).map((s) => {
      const e = by[s], t = STEP_TEXT[s];
      if (e.event !== 'done') return `<li>${t.model} ${t.run}…</li>`;
      let tail;
      if (e.status === 'failed') tail = `できませんでした（${esc(e.detail)}）`;
      else if (s === 'check') tail = `完了（指摘 ${e.count || 0} 件）`;
      else if (s === 'verify') tail = e.verified ? '一致' : '要確認';
      else tail = `完了（${esc(e.detail)}）`;
      return `<li>${t.model} ${t.done} → ${tail} <span class="ai-cost">入力 ${e.input_tokens} / 出力 ${e.output_tokens} トークン、${usd(e.cost_usd || 0)}（目安）、${(e.ms / 1000).toFixed(1)} 秒</span></li>`;
    }).join('');
  }

  function resultHtml(res) {
    const tot = res.totals;
    const parts = [];
    const badge = res.ok ? '<span class="ai-badge ok">一致</span>' : '<span class="ai-badge ng">要確認</span>';
    parts.push(`<div>${badge}${res.cached ? ' <span class="small">（同じ内容の結果を再利用しました）</span>' : ''}</div>`);
    if (res.ok && res.draft) {
      parts.push(`<p class="ai-text" id="ai-text">${esc(res.draft)}</p><div class="bar"><button type="button" class="primary" data-ai="copy">説明文をコピー</button><span class="small" id="ai-copymsg" role="status"></span></div>`);
    } else if (res.message) {
      parts.push(`<p class="errs">${esc(res.message)}</p>`);
    }
    if ((res.problems || []).length) {
      parts.push(`<p><b>確かめで見つかった点</b></p><ul>${res.problems.map((p) => `<li>${p.quote ? `「${esc(p.quote)}」: ` : ''}${esc(p.reason)}</li>`).join('')}</ul>`);
    }
    const chk = (res.steps || []).find((s) => s.step === 'check');
    const checkFailed = !!chk && chk.status === 'failed';
    parts.push(`<p><b>記録の点検</b></p>${checkFailed ? '<p class="small">点検できませんでした。</p>' : (res.issues || []).length ? `<ul>${res.issues.map((i) => `<li>${(i.n || 1) > 1 ? `${esc(i.field)}: ${esc(i.message)}（${esc(i.n)} 件）` : `${esc(i.row)} 行目 ${esc(i.field)}: ${esc(i.message)}`}</li>`).join('')}</ul>` : '<p class="small">指摘はありませんでした。</p>'}`);
    if ((res.steps || []).some((s) => s.status === 'failed')) parts.push('<div class="bar"><button type="button" data-ai="retry">もう一度試す</button><span class="small">失敗した回も、もう一度押すと新しく送ります。</span></div>');
    if (tot) parts.push(`<p class="ai-cost">合計 入力 ${tot.input_tokens} / 出力 ${tot.output_tokens} トークン、${usd(tot.cost_usd)}（目安）。金額は概算です。</p>`);
    return parts.join('');
  }

  function build(el, getFacts) {
    el.innerHTML = `<div class="ai-head"><button type="button" class="primary" data-ai="ask">Claude で説明文を作る</button>
      <span class="small">入れたデータとここまでの計算をもとに、Haiku・Sonnet・Opus が荷主に出す説明文を作り、数字を確かめます。</span></div>
      <div class="ai-confirm" hidden></div><ol class="ai-steps" hidden></ol>
      <details class="ai-result" hidden><summary>作った説明文と確かめの結果</summary><div class="ai-body"></div></details>`;
    const q = (s) => el.querySelector(s);
    let timer = 0;
    const paint = (events, result) => {
      q('.ai-steps').hidden = !events.length;
      q('.ai-steps').innerHTML = stepsHtml(events);
      const det = q('.ai-result');
      if (result) { det.hidden = false; det.open = true; q('.ai-body').innerHTML = resultHtml(result); }
    };
    const stop = () => { clearTimeout(timer); timer = 0; };
    let pending = null;   // 「Claude で説明文を作る」を押した時点の事実。確認の一覧に出したものを、そのまま送る
    const fail = (e) => { q('[data-ai=ask]').disabled = false; q('.ai-steps').hidden = false; q('.ai-steps').innerHTML = `<li>${esc(e.message)} <button type="button" data-ai="retry">もう一度試す</button></li>`; };
    const send = (facts) => {
      stop(); q('[data-ai=ask]').disabled = true; paint([], null); q('.ai-result').hidden = true;
      fetch('/api/ai/waiting_explain', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(facts) })
        .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
        .then(({ ok, j }) => {
          if (!ok) throw new Error(j.error || '送れませんでした');
          const key = JSON.stringify(facts);
          if (j.cached && j.result) {   // 同じ内容の結果はその場で返る
            memo.key = key; memo.result = j.result;
            memo.events = (j.result.steps || []).map((s) => Object.assign({ event: 'done' }, s));
            paint(memo.events, memo.result); q('[data-ai=ask]').disabled = false;
            return;
          }
          poll(j.job, key);
        })
        .catch(fail);
    };
    const poll = (job, key) => {
      if (!el.isConnected) return;
      fetch(`/api/ai/job?id=${encodeURIComponent(job)}`, { cache: 'no-store' }).then((r) => r.json()).then((j) => {
        if (!j || j.error) throw new Error((j && j.error) || '進み具合を読めませんでした');
        memo.key = key; memo.events = j.events; memo.result = j.state === 'done' ? j.result : null;
        paint(j.events, memo.result);
        q('[data-ai=ask]').disabled = j.state !== 'done';
        if (j.state !== 'done') timer = setTimeout(() => poll(job, key), 400);
      }).catch(fail);
    };
    el.onclick = (ev) => {
      const b = ev.target.closest && ev.target.closest('[data-ai]');
      if (!b) return;
      const act = b.dataset.ai, box = q('.ai-confirm');
      if (act === 'ask') {
        const facts = getFacts();
        if (!facts || !facts.rows || !facts.rows.length) { box.hidden = false; box.textContent = '先に記録を入れてください。'; return; }
        pending = JSON.parse(JSON.stringify(facts));
        box.hidden = false; box.innerHTML = confirmHtml(pending);
      } else if (act === 'cancel') {
        box.hidden = true; box.innerHTML = ''; pending = null;
      } else if (act === 'send') {
        box.hidden = true; box.innerHTML = '';
        if (pending) send(pending);
      } else if (act === 'retry') {
        if (pending) send(pending); else q('[data-ai=ask]').click();   // 描き直しで押した内容が無いときは、確認からやり直す
      } else if (act === 'copy') {
        const text = (memo.result && memo.result.draft) || '';
        const msg = q('#ai-copymsg');
        const ok = () => { if (msg) msg.textContent = 'コピーしました'; };
        const fallback = () => { const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); ok(); } catch (e) { if (msg) msg.textContent = '自動でコピーできませんでした。文を選んでコピーしてください'; } ta.remove(); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(ok, fallback); else fallback();
      }
    };
    // 同じ内容でページが描き直されたときは、前の結果を戻す
    let key = '';
    try { key = JSON.stringify(getFacts()); } catch (e) { key = ''; }
    if (key && memo.key === key && memo.events.length) paint(memo.events, memo.result);
  }

  function mount(el, getFacts) {
    if (!el) return Promise.resolve(false);
    return getStatus().then((st) => {
      if (!st || !st.available || !el.isConnected) {
        if (st && el.isConnected) el.title = st.reason_text || '';
        return false;
      }
      injectStyle();
      build(el, getFacts);
      return true;
    });
  }

  // 自社の運行記録の列の整理（SPEC §53.1）。own.js が作った「列の見出し + 値の形の要約」だけを送る。値そのものは渡されない
  function ownColumns(payload) {
    return fetch('/api/ai/own_columns', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
      .then(({ ok, j }) => { if (!ok) throw new Error(j.error || '送れませんでした'); return j; });
  }

  // 帰り便の案内文を読みやすく整える（SPEC §53.2）。negotiate.js が確認を取ったあとの本文だけを渡す
  function offerPolish(text) {
    return fetch('/api/ai/offer_polish', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) })
      .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
      .then(({ ok, j }) => { if (!ok) throw new Error(j.error || '送れませんでした'); return j; });
  }

  root.AiExplain = { mount, OFF_LINE, status: getStatus, ownColumns, offerPolish, _stepsHtml: stepsHtml, _resultHtml: resultHtml };
})(window);
