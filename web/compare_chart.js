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
    if (m) return `運転のみ ${m[1]} 日`;
    const k = /^(\d+km)圏内は預かりの翌日、\d+km以上は翌々日/.exec(d);
    if (k) return `${k[1]}まで翌日、超は翌々日`; // 2 行に収まる短い言い方（全文はツールチップ）
    return String(d).replace(/（.*$/, '').replace(/\s*https?:\/\/\S+/, '').trim();
  }

  const priced = (c) => c.amount !== null && c.amount !== undefined && c.amount > 0;

  // 使える手段（額がある）の最小と最大。2 つ未満なら倍率は出さない
  function ratioOf(cards) {
    const v = cards.filter(priced).map((c) => c.amount);
    if (v.length < 2) return null;
    const lo = Math.min(...v), hi = Math.max(...v);
    return { lo, hi, ratio: hi / lo };
  }
  function ratioText(r) {
    if (!r) return '';
    if (r.ratio < 1.05) return 'この荷では、使える手段の目安額はほぼ同じです';
    const n = r.ratio >= 10 ? Math.round(r.ratio) : Math.round(r.ratio * 10) / 10;
    return `この荷で一番高い手段は、一番安い手段の約 ${n} 倍`;
  }

  function html(cards) {
    if (!cards.length) return '';
    const r = ratioOf(cards);
    const rows = cards.filter(priced).sort((a, b) => a.amount - b.amount);
    const others = cards.filter((c) => !priced(c));
    const max = r ? r.hi : (rows[0] ? rows[0].amount : 1);
    const name = (c) => esc(c.label) + (c.sub ? `<small>${esc(c.sub)}</small>` : '');
    const days = (c) => (c.days ? `<span class="cc-days" title="${esc(c.days)}">${esc(shortDays(c.days))}</span>` : '<span class="cc-days"></span>');
    const line = (c, i) => {
      const w = Math.max(3, (c.amount / max) * 68).toFixed(1); // 値の文字が右に付くので、最長の棒でも 68% まで
      const cheapest = r && c.amount === r.lo;
      return `<div class="cc-row${cheapest ? ' cheap' : ''}"><span class="cc-name">${name(c)}</span>` +
        `<span class="cc-track"><i class="cc-bar" style="width:${w}%"></i><b class="cc-val num">${fmt(c.amount)}円${c.est ? '<em>試算</em>' : ''}</b>` +
        `${cheapest ? '<span class="cc-mark">最安</span>' : ''}</span>${days(c)}</div>`;
    };
    const none = (c) => {
      const unusable = c.status === '使えない';
      return `<div class="cc-row off"><span class="cc-name">${name(c)}</span>` +
        `<span class="cc-track"><span class="cc-off">${unusable ? '使えない' : '問い合わせ'}</span></span>${days(c)}</div>`;
    };
    const sum = r ? `<p class="cc-ratio">${esc(ratioText(r))}</p>` : '';
    return `<section class="cc" aria-label="手段ごとの目安額の比べ">` +
      `<h4 class="cc-title">手段ごとの目安額<small>税抜・宅配、路線便、カゴ台車は百円の単位の試算</small></h4>` +
      `${sum}<div class="cc-rows">${rows.map(line).join('')}${others.map(none).join('')}</div></section>`;
  }

  return { html, ratioOf, ratioText, shortDays };
})();
