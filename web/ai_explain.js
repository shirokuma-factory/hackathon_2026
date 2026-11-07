/* 荷待ち協議の書面の「Claude で説明文を作る」（SPEC §53）。waiting.html と地図の画面（own.js）が使う。
   サーバーで開いたとき（http）で、/api/ai/status が available のときだけ、ボタンを出す。
   送る前に、相手の名前と地点名を「荷主A」「地点1」に置き換え（車番は消す）、送る JSON そのものと費用の目安を見せて確認を取る。
   結果の表示のときだけ、元の名前に戻す（SPEC §53.3）。キーはサーバーの環境変数にあり、このファイルには出てこない。
   waiting.html には通信の記述を置かない決まり（SPEC §23.7）なので、通信はこのファイルだけが行い、サーバーで開いたときだけ読み込まれる。 */
(function (root) {
  'use strict';
  const OFF_LINE = '説明文の自動作成はファイルで開いたときは使えません（今は型どおりの書面です）';
  const FIELD_LABEL = { date: '日付', place: '場所', arrive: '到着', reception: '受付', appointed: '指定の到着時刻', start: '荷役開始', end: '荷役終了',
    handler: '荷役をした人', shipper_fault: '荷主等の都合', wait_min: '待機（分）', handling_min: '荷役（分）', amount: '参考額（円）' };
  // サーバー（ai_explain.py の FACT_KEYS / ROW_KEYS / HANDLER_OK / PLATE_RE）と同じ決まり。これ以外の欄は送らない
  const FACT_KEYS = ['partner', 'period', 'count', 'rows', 'totals', 'basis', 'consult', 'history'];
  const ROW_KEYS = ['row', 'date', 'place', 'arrive', 'reception', 'appointed', 'start', 'end', 'handler', 'shipper_fault', 'wait_min', 'handling_min', 'amount'];
  const HANDLER_OK = ['', '運転者', '荷主側', '荷主', '施設側', 'センター側', '倉庫側', '他'];
  const PLATE = /[一-鿿]{2,4}\s*\d{2,3}\s*[ぁ-ん]\s*\d{1,4}/g;
  const STEP_TEXT = {
    check: { run: 'が記録を点検中', done: 'が記録を点検' },
    draft: { run: 'が説明文を下書き中', done: 'が説明文を下書き' },
    verify: { run: 'が数字と根拠を確かめ中', done: 'が数字と根拠を確かめ' },
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
      '.ai-box .ai-json{margin:6px 0}.ai-box .ai-json pre{max-height:220px;overflow:auto;margin:4px 0;padding:8px;font-size:12px;line-height:1.5;border:1px solid var(--line,#ccc);border-radius:8px;white-space:pre-wrap;word-break:break-all}' +
      '.ai-box .ai-text{white-space:pre-wrap;margin:6px 0}.ai-box .ai-badge{display:inline-block;padding:1px 10px;border-radius:999px;font-weight:700;font-size:12px;border:1px solid currentColor}' +
      '.ai-box .ai-badge.ok{color:var(--accent,#1b5fbf)}.ai-box .ai-badge.ng{color:var(--warn,#b3261e)}.ai-box .ai-cost{color:var(--muted,#5b6475);font-size:12px}';
    document.head.appendChild(st);
  }

  // ---------------------------------------------------------------- 送る前の置き換え（SPEC §53.3）
  // 相手の名前を「荷主A」、地点名を「地点1」「地点2」…（出てきた順）にする。車番は消し、荷役をした人は役割の語だけ残す。
  // 戻り値 { facts, map }。map は { 置き換え後: 元 }。ai_explain.py の pseudonymize と同じ規則
  function pseudonymize(facts) {
    const map = {}, names = [], places = new Map();
    const partner = String(facts.partner || '');
    if (partner) { map['荷主A'] = partner; names.push([partner, '荷主A']); }
    (facts.rows || []).forEach((r) => {
      const pl = String(r.place || '');
      if (pl && !places.has(pl)) { const p = `地点${places.size + 1}`; places.set(pl, p); map[p] = pl; names.push([pl, p]); }
    });
    names.sort((a, b) => b[0].length - a[0].length);
    const scrub = (v) => {
      if (typeof v === 'string') { let t = v; names.forEach(([o, n]) => { t = t.split(o).join(n); }); return t.replace(PLATE, '（車番）'); }
      if (Array.isArray(v)) return v.map(scrub);
      if (v && typeof v === 'object') { const o = {}; Object.keys(v).forEach((k) => { o[k] = scrub(v[k]); }); return o; }
      return v;
    };
    const out = {};
    FACT_KEYS.forEach((k) => {
      if (!(k in facts)) return;
      if (k === 'partner') out[k] = partner ? '荷主A' : '';
      else if (k === 'rows') {
        out[k] = facts[k].map((r) => {
          const n = {};
          ROW_KEYS.forEach((rk) => { if (rk in r) n[rk] = scrub(r[rk]); });
          if ('place' in n) n.place = places.get(String(r.place || '')) || '';
          if ('handler' in n && HANDLER_OK.indexOf(n.handler) < 0) n.handler = '他';
          return n;
        });
      } else out[k] = scrub(facts[k]);
    });
    return { facts: out, map };
  }

  // 結果の表示のためだけに、置き換えた名前を元に戻す（送った内容・保存した内容は置き換えたまま）
  function restore(v, map) {
    if (typeof v === 'string') return v.replace(/荷主A|地点\d+/g, (m) => (map[m] !== undefined ? map[m] : m));
    if (Array.isArray(v)) return v.map((x) => restore(x, map));
    if (v && typeof v === 'object') { const o = {}; Object.keys(v).forEach((k) => { o[k] = restore(v[k], map); }); return o; }
    return v;
  }

  // 1 回の費用の目安。/api/ai/status の cost（段ごとの単価と仮定の表）から、送る内容の大きさで計算する
  function estimate(sendFacts, st) {
    const c = st && st.cost;
    if (!c || !c.stages) return null;
    const all = JSON.stringify(sendFacts).length * c.tokens_per_char, rows = JSON.stringify(sendFacts.rows || []).length * c.tokens_per_char;
    let sum = 0;
    c.stages.forEach((g) => { sum += ((g.in_base + (g.scale === 'rows' ? rows : all)) * g.price_in + g.out * g.price_out) / 1e6; });
    return { usd: sum, yen: sum * c.usd_jpy };
  }
  const yenText = (y) => `約 ${y < 1 ? y.toFixed(2) : y.toFixed(1)} 円`;

  // ---------------------------------------------------------------- 状態と、使えない理由
  function fetchStatus() {
    return fetch('/api/ai/status', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : { available: false, reason: 'http', http: r.status }))
      .catch(() => ({ available: false, reason: 'network' }));
  }

  function getStatus() {
    if (!/^https?:$/.test(location.protocol)) return Promise.resolve(null);   // ファイルで開いたとき
    if (!statusP) {
      statusP = fetchStatus().then((st) => { if (st && st.reason === 'network') statusP = null; return st; });   // 通信の失敗は覚えず、次の描き直しでもう一度調べる
    }
    return statusP;
  }

  function reasonText(st) {
    const r = st.reason;
    if (r === 'public_off' || (r === 'disabled' && st.public)) return 'この公開版では止めています（費用の上限のため）';
    if (r === 'disabled') return 'このサーバーでは使えない設定です（AI_EXPLAIN_ENABLED を 1 にすると使えます）';
    if (r === 'no_key') return 'API キーが設定されていません（サーバーの環境変数）';
    if (r === 'no_sdk') return '必要な部品（anthropic）が入っていません';
    if (r === 'network') return 'サーバーに接続できませんでした';
    if (r === 'http') return `サーバーの応答が読めませんでした（HTTP ${st.http}）`;
    return st.reason_text || '今は使えません';
  }

  // ---------------------------------------------------------------- 確認の枠
  function confirmHtml(sendFacts, st) {
    const n = sendFacts.rows.length;
    const keys = Object.keys(FIELD_LABEL).filter((k) => sendFacts.rows.some((r) => r[k] !== undefined && r[k] !== ''));
    const cons = sendFacts.consult || [];
    const hist = (sendFacts.history || []).length;
    const est = estimate(sendFacts, st);
    const left = st && typeof st.remaining === 'number' ? st.remaining : null;
    const out = left === 0;
    const costLine = est ? `<li>1 回の費用の目安: ${usd(est.usd)}（${yenText(est.yen)}。1 ドル ${st.cost.usd_jpy} 円と仮定）。同じ内容は結果を再利用し、費用も回数もかかりません</li>` : '';
    const leftLine = left !== null ? `<li class="ai-left">今日の残り ${left} 回${st.limits && st.limits.daily ? `（1 日 ${st.limits.daily} 回まで）` : ''}</li>` : '';
    return `<b>次の内容を Anthropic の API に送ります</b>
      <ul><li>記録 ${n} 行（${keys.map((k) => FIELD_LABEL[k]).join('、')}）</li>
        <li>相手の名前は「荷主A」、地点名は「地点1」「地点2」…に置き換えて送ります。結果を見せるときだけ元の名前に戻します</li>
        <li>期間、合計の数字、計算の根拠の文（告示の表の単価と出典）</li>
        ${cons.length ? `<li>協議の項目 ${cons.length} 件</li>` : ''}${hist ? `<li>これまでの協議の履歴 ${hist} 件</li>` : ''}
        ${costLine}${leftLine}</ul>
      <details class="ai-json" open><summary>送る JSON そのもの</summary><pre class="ai-json-text">${esc(JSON.stringify(sendFacts, null, 2))}</pre></details>
      <p class="small">送らないもの: 車番、運転者名、貼り付けた元の文、ファイル、ほかの相手の行。送った内容は保存しません。</p>
      ${out ? '<p class="errs">今日の上限に達しました。明日また使えます（今は型どおりの書面です）。</p>' : ''}
      <div class="bar">${out ? '' : '<button type="button" class="primary" data-ai="send">送る</button>'}<button type="button" data-ai="cancel">やめる</button></div>`;
  }

  function stepsHtml(events) {
    const by = {};
    events.forEach((e) => { by[e.step] = Object.assign(by[e.step] || {}, e); });
    return ORDER.filter((s) => by[s]).map((s) => {
      const e = by[s], t = STEP_TEXT[s], label = e.label || '';
      if (e.model === 'machine') {   // 機械による数字の照合（費用なし）
        return e.event !== 'done' ? '<li>数字を機械で照合中…</li>' : `<li>数字を機械で照合 → ${e.verified ? '一致' : '要確認'} <span class="ai-cost">費用なし</span></li>`;
      }
      if (e.event !== 'done') return `<li>${esc(label)} ${t.run}…</li>`;
      let tail;
      if (e.status === 'failed') tail = `できませんでした（${esc(e.detail)}）`;
      else if (s === 'check') tail = `完了（指摘 ${e.count || 0} 件）`;
      else if (s === 'verify') tail = e.verified ? '一致' : '要確認';
      else tail = `完了（${esc(e.detail)}）`;
      return `<li>${esc(label)} ${t.done} → ${tail} <span class="ai-cost">入力 ${e.input_tokens} / 出力 ${e.output_tokens} トークン、${usd(e.cost_usd || 0)}（目安）、${(e.ms / 1000).toFixed(1)} 秒</span></li>`;
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
      <span class="small">入れたデータとここまでの計算をもとに、Haiku が荷主に出す説明文を作り、数字を機械で照合します。</span></div>
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
    let pending = null, pendingMap = {};   // 確認の一覧に出した（置き換えたあとの）事実。これをそのまま送る
    const fail = (e) => { q('[data-ai=ask]').disabled = false; q('.ai-steps').hidden = false; q('.ai-steps').innerHTML = `<li>${esc(e.message)} <button type="button" data-ai="retry">もう一度試す</button></li>`; };
    const send = (facts, map) => {
      stop(); q('[data-ai=ask]').disabled = true; paint([], null); q('.ai-result').hidden = true;
      fetch('/api/ai/waiting_explain', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(facts) })
        .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
        .then(({ ok, j }) => {
          if (!ok) throw new Error(j.error || '送れませんでした');
          const key = JSON.stringify(facts);
          if (j.cached && j.result) {   // 同じ内容の結果はその場で返る
            memo.key = key; memo.result = restore(j.result, map);
            memo.events = (j.result.steps || []).map((s) => Object.assign({ event: 'done' }, s));
            paint(memo.events, memo.result); q('[data-ai=ask]').disabled = false;
            return;
          }
          poll(j.job, key, map);
        })
        .catch(fail);
    };
    const poll = (job, key, map) => {
      if (!el.isConnected) return;
      fetch(`/api/ai/job?id=${encodeURIComponent(job)}`, { cache: 'no-store' }).then((r) => r.json()).then((j) => {
        if (!j || j.error) throw new Error((j && j.error) || '進み具合を読めませんでした');
        memo.key = key; memo.events = j.events; memo.result = j.state === 'done' ? restore(j.result, map) : null;
        paint(j.events, memo.result);
        q('[data-ai=ask]').disabled = j.state !== 'done';
        if (j.state !== 'done') timer = setTimeout(() => poll(job, key, map), 400);
      }).catch(fail);
    };
    el.onclick = (ev) => {
      const b = ev.target.closest && ev.target.closest('[data-ai]');
      if (!b) return;
      const act = b.dataset.ai, box = q('.ai-confirm');
      if (act === 'ask') {
        const facts = getFacts();
        if (!facts || !facts.rows || !facts.rows.length) { box.hidden = false; box.textContent = '先に記録を入れてください。'; return; }
        const ps = pseudonymize(JSON.parse(JSON.stringify(facts)));
        pending = ps.facts; pendingMap = ps.map;
        box.hidden = false; box.textContent = '送る内容を準備しています…';
        fetchStatus().then((st) => { if (pending === ps.facts) box.innerHTML = confirmHtml(ps.facts, st); });   // 残りの回数は押した時点のものを出す
      } else if (act === 'cancel') {
        box.hidden = true; box.innerHTML = ''; pending = null;
      } else if (act === 'send') {
        box.hidden = true; box.innerHTML = '';
        if (pending) send(pending, pendingMap);
      } else if (act === 'retry') {
        if (pending) send(pending, pendingMap); else q('[data-ai=ask]').click();   // 描き直しで押した内容が無いときは、確認からやり直す
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
    try { key = JSON.stringify(pseudonymize(JSON.parse(JSON.stringify(getFacts()))).facts); } catch (e) { key = ''; }
    if (key && memo.key === key && memo.events.length) paint(memo.events, memo.result);
  }

  function mount(el, getFacts) {
    if (!el) return Promise.resolve(false);
    return getStatus().then((st) => {
      if (!st || !el.isConnected) return false;   // ファイルで開いたとき（st が無い）は、ページ自身の 1 行のまま
      if (!st.available) {
        // 使えない理由を、吹き出しではなく本文に出す
        let off = el.querySelector('.ai-off');
        if (!off) { off = document.createElement('p'); off.className = 'small ai-off'; el.appendChild(off); }
        off.textContent = `説明文の自動作成: ${reasonText(st)}（今は型どおりの書面です）`;
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

  root.AiExplain = { mount, OFF_LINE, status: getStatus, ownColumns, offerPolish, pseudonymize, restore, estimate, reasonText,
    _stepsHtml: stepsHtml, _resultHtml: resultHtml };
})(window);
