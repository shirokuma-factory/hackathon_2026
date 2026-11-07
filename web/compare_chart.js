'use strict';
/* 送り方を比べるの右のカードの上に置く、手段ごとの目安額の横棒の図（SPEC §30.5）。
   金額・状態・日数は /api/estimate の結果（app.js の buildCards が作ったカード）をそのまま並べる。ここでは額を計算しない。
   計算するのは、最も安い手段と最も高い手段の比（倍率）だけ。 */
const CompareChart = (() => {
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmt = (v) => Math.round(v).toLocaleString('ja-JP');

  // 日数は棒の右に短く出す（全文はツールチップ）。「（…）」の注釈は外す
  function shortDays(d) {
    if (!d) return '';
    const m = /^運転だけで(\d+)日/.exec(d);
    if (m) return `運転だけで ${m[1]} 日`;
    const k = /^(\d+)km圏内は預かりの翌日、\d+km以上は翌々日/.exec(d);
    if (k) return `${k[1]} km までは翌日、それより遠いと翌々日`; // 2 行に収まる短い言い方（全文はツールチップ）
    return String(d).replace(/（.*$/, '').replace(/\s*https?:\/\/\S+/, '').trim();
  }

  const priced = (c) => c.amount !== null && c.amount !== undefined && c.amount > 0;
  // 条件のそろった手段（公開条件に合う）。要確認の手段は最安・倍率に入れない（レビュー F02）
  const fit = (c) => priced(c) && c.status === '公開条件に合う';

  // 使える手段（額がある）の最小と最大。2 つ未満なら倍率は出さない
  function ratioOf(cards) {
    const v = cards.filter(priced).map((c) => c.amount);
    if (v.length < 2) return null;
    const lo = Math.min(...v), hi = Math.max(...v);
    return { lo, hi, ratio: hi / lo };
  }
  function ratioText(r, ref) {
    if (!r) return '';
    const pre = '';
    if (r.ratio < 1.05) return ref ? '確認が要る便を含めて、金額はほぼ同じです。' : '使える送り方の金額はほぼ同じです。';
    const n = r.ratio >= 10 ? Math.round(r.ratio) : Math.round(r.ratio * 10) / 10;
    return `一番高い送り方は、一番安い送り方の約 ${n} 倍です${ref ? '（確認が要る便を含む）' : ''}。`;
  }

  // 額の出る手段が 1 つだけのとき（倍率は出さない。SPEC §33 #3）。表を持たない手段は、宅配・路線便・カゴ台車のうち問い合わせになっているもの
  function onlyText(one, others) {
    const ask = ['宅配', '路線便', 'カゴ台車'].filter((n) => others.some((c) => c.label === n && c.status !== '使えない'));
    return `条件がそろって額が出るのは${one.label}だけです。` + (ask.length ? `${ask.join('・')}は料金表がないため、問い合わせが要ります。どれが安いかはまだ分かりません。` : '');
  }

  // 行の表示名。内部の名前（label）は app.js と共有する選択の値なので変えず、表示だけ分かりやすくする
  const DISP = { 'カゴ台車': 'カゴ台車の貸切（1 本単位）', '鉄道': '鉄道', '船': '船', '飛行機': '飛行機' };
  const ALT_DETAIL = { '鉄道': '鉄道（コンテナ貨物）', '船': '船（フェリー）', '飛行機': '飛行機（航空便）' };
  const disp = (l) => DISP[l] || l;
  // 公表の運賃表を持たない手段（鉄道・船・飛行機）。額は出さず、地図に経路を出すだけ
  const MODE_OF = { '鉄道': 'rail', '船': 'ferry', '飛行機': 'air' };
  let altOpen = true;   // 鉄道・船・飛行機の欄の開閉（表を作り直しても保つ）
  document.addEventListener('toggle', (e) => { if (e.target && e.target.classList && e.target.classList.contains('cc-alt')) altOpen = e.target.open; }, true);
  const CAGE_EXPL = 'カゴ単位で払う便';
  const NO_FARE = '公表の運賃表を持っていないため、額は出しません';
  const rtNone = (m) => !!(typeof Rt !== 'undefined' && Rt.cur && Rt.cur.sets && Rt.cur.sets[m] && Rt.cur.sets[m].ok === false);
  // 飛行機のカードは app.js の手段の一覧に無いので、ここで足す（鉄道・船は app.js が作る）
  function withAir(cards) {
    if (cards.some((c) => c.label === '飛行機')) return cards;
    return cards.concat([{ label: '飛行機', key: 'air', status: '計算できない', amount: null, dim: true, routeNone: rtNone('air'), none: '公表の運賃表がありません' }]);
  }
  // カゴ台車と貸切の額が近い（15% 以内）ときの 1 行。本数と 1 本の運賃は行の detail（カードの note）の値
  function nearText(cards) {
    const cage = cards.find((c) => c.label === 'カゴ台車' && priced(c)), ch = cards.find((c) => c.label === '貸切' && priced(c));
    if (!cage || !ch) return '';
    if (Math.abs(cage.amount - ch.amount) / Math.max(cage.amount, ch.amount) > 0.15) return '';
    const veh = ch.sub ? `${String(ch.sub).replace(/車$/, '')} 車 1 台` : '車 1 台';
    return cage.note ? `${cage.note}で、${veh}を貸し切るのに近い量です。` : `${veh}を貸し切るのに近い量です。`;
  }

  // 手段ごとの1行。左から 送り方 / この荷物に使えるか / 費用の目安 / 日数（または未確認）。額が無い手段は「要見積」、使えない手段は理由を添える
  // auto: 案内の表示で、選ばれていなければ一番安い（確認待ちだけのときは参考の最安の）行を初めから選んでおく
  function html(cards, pick, auto) {
    if (!cards.length) return '';
    cards = withAir(cards);
    const fits = cards.filter(fit).sort((a, b) => a.amount - b.amount);
    const checks = cards.filter((c) => priced(c) && !fit(c)).sort((a, b) => a.amount - b.amount);
    const ref = !fits.length && checks.length > 0; // 全部が要確認: 確認待ちを含む参考の最安にする
    const base = ref ? checks : fits; // 最安・倍率を決める行
    const r = ratioOf(base);
    const others = cards.filter((c) => !priced(c));
    const alt = others.filter((c) => MODE_OF[c.label]);   // 鉄道・船・飛行機は別の欄（案内の表示は上から 3 行だけ出すので、その外に置く）
    const plain = others.filter((c) => !MODE_OF[c.label]);
    // 他の手段が「要見積」（表を持たない）のとき、額の出る 1 つだけを最安として初めから選ばない（r7b 指摘1）
    const unknownOthers = others.some((c) => c.status !== '使えない' && !c.routeNone);
    if (auto && !pick && base.length && (r || !unknownOthers)) { const f = r ? base.find((c) => c.amount === r.lo) : base[0]; if (f) pick = f.label; }
    const max = Math.max(1, ...cards.filter(priced).map((c) => c.amount));
    const name = (c, usable) => `<span class="cc-name">${usable ? `<input type="radio" name="cc-pick" value="${esc(c.label)}"${pick === c.label ? ' checked' : ''} aria-label="${esc(disp(c.label))}を選ぶ">` : ''}<span>${esc(disp(c.label))}${c.sub ? `<small>${esc(c.sub)}</small>` : ''}</span></span>`;
    const days = (c) => (c.days ? `<span class="cc-days" data-l="日数" title="${esc(c.days)}">${esc(shortDays(c.days))}</span>` : '<span class="cc-days unk" data-l="日数">未確認</span>');
    const ok = (txt, cls) => `<span class="cc-ok ${cls}" data-l="この荷物に使えるか">${txt}</span>`;
    const line = (c) => {
      const w = Math.max(3, (c.amount / max) * 50).toFixed(1); // 値の文字と印が右に付くので、最長の棒でも 50% まで
      const isCheck = !fit(c);
      const cheapest = !!r && base.includes(c) && c.amount === r.lo;
      const best = cheapest ? `<span class="cc-mark">${ref ? '最安（確認が要る）' : '最安'}</span>` : '';
      const why = isCheck && c.why ? `<span class="cc-why" title="${esc(c.why)}">確かめること: ${esc(String(c.why).split('。')[0])}</span>` : '';
      const expl = c.label === 'カゴ台車' ? `<span class="cc-expl">${esc(CAGE_EXPL)}</span>` : '';
      return `<label class="cc-row${cheapest && !isCheck ? ' cheap' : ''}${isCheck ? ' chk' : ''}">${name(c, true)}` +
        `${ok(isCheck ? '要確認' : '使える', isCheck ? 'chk' : 'yes')}` +
        `<span class="cc-track" data-l="費用の目安"><i class="cc-bar" style="width:${w}%"></i><b class="cc-val num">${fmt(c.amount)}円${c.est ? '<em>概算</em>' : ''}</b>${best}</span>${days(c)}${why}${expl}</label>`;
    };
    const none = (c) => {
      const unusable = c.status === '使えない';
      const nonet = !!c.routeNone;
      const alt = !!MODE_OF[c.label];   // 鉄道・船・飛行機: 公表の運賃表が無い。日数も出典のある値が無いので見積で確かめる
      const txt = unusable ? '—' : nonet ? (c.label === '飛行機' ? 'なし（つながる路線がありません）' : 'なし（つながる航路がありません）') : alt ? '日数と額は見積で確認' : '見積が要ります';   // 料金が取れない手段は 0 円にせず「要見積」
      const note = unusable ? (c.none ? `<span class="cc-why">理由: ${esc(c.none)}</span>` : '') : nonet ? '' : alt ? `<span class="cc-nofare">${esc(ALT_DETAIL[c.label])}。金額は見積で確かめます。道は地図に出ます。</span>` : '<span class="cc-why">この行き先の料金表がないため、見積で確かめてください。</span>';
      const dy = alt && !c.days ? '<span class="cc-days unk" data-l="日数">見積で確認</span>' : days(c);
      return `<label class="cc-row off">${name(c, !unusable && !nonet)}${ok(unusable ? '使えない' : '未確認', unusable ? 'no' : 'unk')}` +
        `<span class="cc-track" data-l="費用の目安"><span class="cc-off">${txt}</span></span>${dy}${note}</label>`;
    };
    let sum = '';
    if (r) sum = `<p class="cc-ratio">${esc(ratioText(r, ref))}</p>`;
    else if (fits.length === 1) sum = `<p class="cc-only">${esc(onlyText(fits[0], cards.filter((c) => c !== fits[0])))}</p>`;
    const near = nearText(cards);
    if (near) sum += `<p class="cc-near">${esc(near)}</p>`;
    if (!r && !fits.length && checks.length === 1) sum = '<p class="cc-only">金額が出たのは確認が要る送り方だけです。どれが安いかはまだ決められません。</p>';
    const ttl = ref ? '送り方ごとの費用（確認が要る便を含む）' : '送り方ごとの費用';
    return `<section class="cc" aria-label="送り方の比べ">` +
      `<h4 class="cc-title">${ttl}<small class="cc-long">税抜・この荷物 1 件分です。高速代と待機料は別です。</small><small class="cc-unit">円・税抜・この荷物 1 件分。付帯料金は別</small></h4>` +
      `${sum}<div class="cc-head" aria-hidden="true"><span>送り方</span><span>使えるか</span><span>費用の目安</span><span>日数</span></div>` +
      `<div class="cc-rows" role="radiogroup" aria-label="見積依頼文を作る送り方">${fits.map(line).join('')}${checks.map(line).join('')}${plain.map(none).join('')}</div>` +
      (alt.length ? `<details class="cc-alt"${altOpen ? ' open' : ''}><summary>鉄道・船・飛行機（見積で確認）</summary><div class="cc-alt-rows" role="radiogroup" aria-label="見積依頼文を作る送り方（額を出さない手段）">${alt.map(none).join('')}</div></details>` : '') +
      `</section>`;
  }

  // 鉄道・船・飛行機を選んだら、その経路だけを地図に出す（真上から。routes.js の setFocus）。ほかの手段はトラックの経路
  document.addEventListener('change', (e) => {
    const t = e.target;
    if (!t || t.name !== 'cc-pick' || typeof Rt === 'undefined' || !Rt.cur || !Rt.cur.sets) return;
    const m = MODE_OF[t.value] || 'truck', s = Rt.cur.sets[m];
    if (s && s.ok && Rt.focus !== m) Rt.setFocus(m);
  });

  return { html, ratioOf, ratioText, shortDays, disp, MODE_OF };
})();
