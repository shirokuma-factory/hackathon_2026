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
  // 点検の欄の名前（英字のキー）を画面では日本語で出す
  const FIELD_JA = { date: '日付', place: '場所', arrive: '到着', reception: '受付', appointed: '指定の到着時刻', start: '荷役開始', end: '荷役終了', handler: '荷役をした人', shipper_fault: '荷主等の都合', wait_min: '待ち時間', handling_min: '荷役の時間', amount: '参考額', row: '行' };
  const fieldJa = (f) => FIELD_JA[f] || f;
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
      '.ai-bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:8px 0}' +
      '.ai-box .ai-json{margin:6px 0}.ai-box .ai-json>summary{white-space:normal;overflow-wrap:anywhere;word-break:normal;display:list-item}.ai-box .ai-json pre{max-height:220px;overflow:auto;margin:4px 0;padding:8px;font-size:12px;line-height:1.5;border:1px solid var(--line,#ccc);border-radius:8px;white-space:pre-wrap;word-break:break-all}' +
      '.ai-box .ai-text{white-space:pre-wrap;margin:6px 0}.ai-box .ai-badge{display:inline-block;padding:1px 10px;border-radius:999px;font-weight:700;font-size:12px;border:1px solid currentColor}' +
      '.ai-box .ai-badge.ok{color:var(--accent,#1b5fbf)}.ai-box .ai-badge.ng{color:var(--warn,#b3261e)}.ai-box .ai-cost{color:var(--muted,#5b6475);font-size:12px}' +
      '.ai-cg{margin:8px 0;padding:8px 10px;border:1px solid var(--line,#ccc);border-radius:10px}.ai-cg>summary{cursor:pointer;font-weight:700}' +
      '.ai-cg .ai-cg-text{width:100%;box-sizing:border-box;margin:6px 0;resize:vertical;min-height:64px}.ai-cg .ai-cg-bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}' +
      '.ai-cg .ai-confirm,.ai-cg .ai-result-cg{margin-top:8px;padding:10px 12px;border:1px solid var(--line,#ccc);border-radius:10px}.ai-cg .ai-cg-sent{white-space:pre-wrap;word-break:break-all;margin:6px 0;padding:8px;font-size:13px;border:1px solid var(--line,#ccc);border-radius:8px}' +
      '.ai-cg .ai-cg-scroll{overflow-x:auto;margin:6px 0}.ai-cg table.ai-cg-table{border-collapse:collapse;font-size:13px;width:100%}.ai-cg-table th,.ai-cg-table td{padding:3px 6px;text-align:left;white-space:nowrap}.ai-cg-table .ai-cg-r1 td{padding-top:8px}.ai-cg-table .ai-cg-r2 td{white-space:normal;border-bottom:1px solid var(--line,#ccc)}' +
      '.ai-cg-table td.num{text-align:right}.ai-cg .ai-cg-na{color:var(--muted,#5b6475)}.ai-cg .ai-cg-h{margin:8px 0 2px;font-weight:700}.ai-cg .ai-cg-list{margin:2px 0 6px;padding-left:20px}.ai-cg .ai-badge{display:inline-block;padding:1px 10px;border-radius:999px;font-weight:700;font-size:12px;border:1px solid var(--accent,#1b5fbf);color:var(--accent,#1b5fbf)}';
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
  // 列の整理（own_columns）の費用の目安。送る内容は表だけなので、入力は見出しと形の大きさ
  function ownEstimate(payload, st) {
    const c = st && st.own_cost, tpc = st && st.cost && st.cost.tokens_per_char;
    if (!c || !tpc) return null;
    const cost = ((c.in_base + JSON.stringify(payload).length * tpc) * c.price_in + c.out_typical * c.price_out) / 1e6;
    return { usd: cost, usdText: usd(cost), yen: st.cost.usd_jpy, yenText: yenText(cost * st.cost.usd_jpy) };
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
    if (r === 'disabled') return 'この画面では説明文の自動作成を止めています';
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
      <details class="ai-json" open><summary class="ai-json-sum">送る中身（JSON）</summary><pre class="ai-json-text">${esc(JSON.stringify(sendFacts, null, 2))}</pre></details>
      <p class="small">送らないもの: 車番、運転者名、貼り付けた元の文、ファイル、ほかの相手の行。送った内容は保存しません。</p>
      ${out ? '<p class="errs">今日の上限に達しました。明日また使えます（今は型どおりの書面です）。</p>' : ''}
      <div class="ai-bar">${out ? '' : '<button type="button" class="primary" data-ai="send">送る</button>'}<button type="button" data-ai="cancel">やめる</button></div>`;
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
      parts.push(`<p class="ai-text" id="ai-text">${esc(res.draft)}</p><div class="ai-bar"><button type="button" class="primary" data-ai="copy">説明文をコピー</button><span class="small" id="ai-copymsg" role="status"></span></div>`);
    } else if (res.message) {
      parts.push(`<p class="errs">${esc(res.message)}</p>`);
    }
    if ((res.problems || []).length) {
      parts.push(`<p><b>確かめで見つかった点</b></p><ul>${res.problems.map((p) => `<li>${p.quote ? `「${esc(p.quote)}」: ` : ''}${esc(p.reason)}</li>`).join('')}</ul>`);
    }
    const chk = (res.steps || []).find((s) => s.step === 'check');
    const checkFailed = !!chk && chk.status === 'failed';
    parts.push(`<p><b>記録の点検</b></p>${checkFailed ? '<p class="small">点検できませんでした。</p>' : (res.issues || []).length ? `<ul>${res.issues.map((i) => `<li>${(i.n || 1) > 1 ? `${esc(fieldJa(i.field))}: ${esc(i.message)}（${esc(i.n)} 件）` : `${esc(i.row)} 行目 ${esc(fieldJa(i.field))}: ${esc(i.message)}`}</li>`).join('')}</ul>` : '<p class="small">指摘はありませんでした。</p>'}`);
    if ((res.steps || []).some((s) => s.status === 'failed')) parts.push('<div class="ai-bar"><button type="button" data-ai="retry">もう一度試す</button><span class="small">失敗した回も、もう一度押すと新しく送ります。</span></div>');
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

  // 荷物の条件を文で書いて表にしてもらう（SPEC §53.4）。地図の画面の荷物の欄（work.js）が使う。
  // 送るのは、押した時点の文だけ（1,000 字まで）。確認の枠に同じ文を出し、「送る」を押すまで要求を出さない。
  // 案は「AI の案」として表に出し、「この行を荷物の表に入れる」を押すまで荷物の表は変わらない。
  const CARGO_MAX = 1000;
  const cg = { text: '', phase: 'idle', pending: '', res: null, err: '', done: {} };   // 荷物の欄が描き直されても、入れた文と案は残す
  const CG_COLS = [['kind', '種類'], ['n', '個数'], ['l_cm', '縦cm'], ['w_cm', '横cm'], ['h_cm', '高さcm'], ['kg', '1個kg'], ['note', '備考']];
  const CG_EXAMPLE = '例: 段ボール 120サイズ 3箱 各40kg、パレット1枚 110×110×150cm 450kg、東京から大阪';

  function cargoEstimate(text, st) {
    const c = st && st.cargo_cost, tpc = st && st.cost && st.cost.tokens_per_char;
    if (!c || !tpc) return null;
    const tin = c.in_base + text.length * tpc;
    const one = (out) => (tin * c.price_in + out * c.price_out) / 1e6;
    return { typ: one(c.out_typical), max: one(c.out_max), yen: st.cost.usd_jpy };
  }
  const cgCell = (v) => (v === null || v === undefined || v === '' ? '<span class="ai-cg-na">未入力</span>' : esc(v));

  function cargoHtml(st) {
    const dis = cg.phase === 'busy' ? ' disabled' : '';
    const parts = [`<details class="ai-cg" id="ai-cg-box"${cg.open ? ' open' : ''}><summary>文で書いて AI に表にしてもらう</summary>
      <p class="small">荷物の内容を文で書くと、荷物の表の案を作ります。送るのはここに書いた文だけです。作った案は、押すまで表に入りません。お客さまの名前や住所など、個人の情報は書かないでください。</p>
      <textarea class="ctl ai-cg-text" id="ai-cg-text" rows="3" maxlength="${CARGO_MAX}" placeholder="${esc(CG_EXAMPLE)}" aria-label="荷物の条件を書いた文"${dis}>${esc(cg.text)}</textarea>
      <div class="ai-cg-bar"><button type="button" class="btn-primary sm" data-cg="ask"${dis}>表にしてもらう</button><span class="small" id="ai-cg-count">${cg.text.length} / ${CARGO_MAX} 字</span></div>`];
    if (cg.err) parts.push(`<p class="errs" role="alert" id="ai-cg-err">${esc(cg.err)}</p>`);
    if (cg.phase === 'confirm') {
      const est = cargoEstimate(cg.pending, st), left = st && typeof st.remaining === 'number' ? st.remaining : null, out = left === 0;
      const cost = est ? `<li>1 回の費用の目安: ${usd(est.typ)}（${yenText(est.typ * est.yen)}）。最大でも ${usd(est.max)}（${yenText(est.max * est.yen)}）。1 ドル ${est.yen} 円と仮定。Haiku 5.5 を 1 回呼びます。同じ文は結果を再利用し、費用も回数もかかりません</li>` : '';
      const lft = left !== null ? `<li class="ai-left">今日の残り ${left} 回${st.limits && st.limits.daily ? `（1 日 ${st.limits.daily} 回まで）` : ''}</li>` : '';
      parts.push(`<div class="ai-confirm" id="ai-cg-confirm"><b>この文を Anthropic の API に送ります</b>
        <pre class="ai-cg-sent" id="ai-cg-sent">${esc(cg.pending)}</pre>
        <ul><li>送るのはこの文だけです（ほかの欄、会社名、見積の内容、地図の選択は送りません）。送った内容は保存しません</li>${cost}${lft}</ul>
        ${out ? '<p class="errs">今日の上限に達しました。明日また使えます。荷物の表は手で入れてください。</p>' : ''}
        <div class="ai-bar">${out ? '' : '<button type="button" class="btn-primary sm" id="ai-cg-send" data-cg="send">送る</button>'}<button type="button" class="btn-link" id="ai-cg-cancel" data-cg="cancel">やめる</button></div></div>`);
    } else if (cg.phase === 'busy') {
      parts.push('<p class="small" role="status" id="ai-cg-busy">Haiku 5.5 が表にしています…</p>');
    } else if (cg.phase === 'result' && cg.res) {
      const r = cg.res, rows = r.rows || [];
      const body = rows.map((x, i) => `<tr class="ai-cg-r1"><td>${esc(x.kind)}</td>${CG_COLS.slice(1, 6).map(([k]) => `<td class="num">${cgCell(x[k])}</td>`).join('')}</tr>
        <tr class="ai-cg-r2"><td colspan="6">${x.note ? `<span class="small">備考: ${esc(x.note)}</span> ` : ''}<button type="button" class="btn-link" data-cg="insert" data-i="${i}"${cg.done[i] ? ' disabled' : ''}>${cg.done[i] ? '入れました' : 'この行を荷物の表に入れる'}</button></td></tr>`).join('');
      parts.push(`<div class="ai-result-cg" id="ai-cg-result"><p><span class="ai-badge ok">AI の案</span> <span class="small">${r.cached ? '同じ文の結果を再利用しました。' : ''}文にない数字は入れていません。内容を見てから入れてください。</span></p>
        ${rows.length ? `<p class="small">寸法は cm、重さは 1 個あたり kg です。</p><div class="ai-cg-scroll"><table class="ai-cg-table"><thead><tr>${['種類', '個数', '縦', '横', '高さ', '重さ'].map((l) => `<th>${l}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>` : '<p class="small">表にできる行がありませんでした。</p>'}
        ${r.origin || r.destination ? `<p class="small" id="ai-cg-places">発地: ${esc(r.origin || '（書かれていません）')} / 着地: ${esc(r.destination || '（書かれていません）')}（参考です。地図の選択は変わりません）</p>` : ''}
        ${(r.unknowns || []).length ? `<p class="ai-cg-h">分からなかったこと</p><ul class="ai-cg-list" id="ai-cg-unknowns">${r.unknowns.map((u) => `<li>${esc(u)}</li>`).join('')}</ul>` : ''}
        ${(r.dropped || []).length ? `<details class="ai-cg-drop"><summary>入れなかった行・消した所（${r.dropped.length} 件）</summary><ul class="ai-cg-list" id="ai-cg-dropped">${r.dropped.map((u) => `<li>${esc(u)}</li>`).join('')}</ul></details>` : ''}
        <div class="ai-bar"><button type="button" class="btn-link" data-cg="close">閉じる</button></div></div>`);
    }
    parts.push('</details>');
    return parts.join('');
  }

  function cargoRows(text) {
    return fetch('/api/ai/cargo_rows', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) })
      .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
      .then(({ ok, j }) => { if (!ok) throw new Error(j.error || '送れませんでした'); return j; });
  }

  // el: 荷物の欄に置いた空の枠。opts.onInsert(row): 「この行を荷物の表に入れる」を押したときだけ呼ぶ
  function mountCargo(el, opts) {
    if (!el) return Promise.resolve(false);
    return getStatus().then((st0) => {
      if (!st0 || !st0.available || !el.isConnected) { if (el.isConnected) el.innerHTML = ''; return false; }
      injectStyle();
      let st = st0;
      const paint = () => {
        el.innerHTML = cargoHtml(st);
        const det = el.querySelector('#ai-cg-box');
        if (det) det.addEventListener('toggle', () => { cg.open = det.open; });
      };
      el.oninput = (ev) => {
        if (ev.target.id !== 'ai-cg-text') return;
        cg.text = ev.target.value;
        const c = el.querySelector('#ai-cg-count'); if (c) c.textContent = `${cg.text.length} / ${CARGO_MAX} 字`;
      };
      el.onclick = (ev) => {
        const b = ev.target.closest && ev.target.closest('[data-cg]');
        if (!b || b.disabled) return;
        const act = b.dataset.cg;
        if (act === 'ask') {
          const text = (el.querySelector('#ai-cg-text').value || '').trim();
          cg.text = el.querySelector('#ai-cg-text').value; cg.err = '';
          if (!text) { cg.err = '荷物の条件を文で書いてください。'; cg.phase = 'idle'; paint(); return; }
          if (text.length > CARGO_MAX) { cg.err = `文は ${CARGO_MAX} 字までです。`; cg.phase = 'idle'; paint(); return; }
          cg.pending = text; cg.phase = 'confirm'; cg.res = null; cg.done = {}; cg.open = true;
          paint();
          fetchStatus().then((s) => { if (cg.phase === 'confirm' && cg.pending === text && el.isConnected) { st = s && s.available ? s : st; paint(); } });   // 残りの回数は押した時点のものを出す
        } else if (act === 'cancel') {
          cg.phase = 'idle'; cg.pending = ''; cg.err = ''; paint();
        } else if (act === 'send') {
          const text = cg.pending;
          if (!text) return;
          cg.phase = 'busy'; cg.err = ''; paint();
          cargoRows(text).then((j) => { cg.res = j; cg.done = {}; cg.phase = 'result'; })
            .catch((e) => { cg.err = e.message || '送れませんでした'; cg.phase = 'idle'; })
            .then(() => { if (el.isConnected) paint(); });
        } else if (act === 'insert') {
          const i = Number(b.dataset.i), row = cg.res && cg.res.rows && cg.res.rows[i];
          if (!row || cg.done[i]) return;
          cg.done[i] = true;
          if (opts && opts.onInsert) opts.onInsert(Object.assign({}, row));
          if (el.isConnected) paint();
        } else if (act === 'close') {
          cg.phase = 'idle'; cg.res = null; cg.pending = ''; cg.err = ''; paint();
        }
      };
      paint();
      return true;
    });
  }

  root.AiExplain = { mount, ownEstimate, freshStatus: fetchStatus, OFF_LINE, status: getStatus, ownColumns, offerPolish, cargoRows, mountCargo, pseudonymize, restore, estimate, reasonText,
    _stepsHtml: stepsHtml, _resultHtml: resultHtml };
})(window);
