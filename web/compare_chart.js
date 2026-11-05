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
    const pre = ref ? '確認待ちを含む参考として、' : '';
    if (r.ratio < 1.05) return ref ? 'この荷では、確認待ちを含めて目安額はほぼ同じです' : 'この荷では、使える手段の目安額はほぼ同じです';
    const n = r.ratio >= 10 ? Math.round(r.ratio) : Math.round(r.ratio * 10) / 10;
    return `${pre}この荷で一番高い手段は、一番安い手段の約 ${n} 倍`;
  }

  // 額の出る手段が 1 つだけのとき（倍率は出さない。SPEC §33 #3）。表を持たない手段は、宅配・路線便・カゴ台車のうち問い合わせになっているもの
  function onlyText(one, others) {
    const ask = ['宅配', '路線便', 'カゴ台車'].filter((n) => others.some((c) => c.label === n && c.status !== '使えない'));
    return `条件がそろって額が出るのは${one.label}だけです。` + (ask.length ? `${ask.join('・')}はこの区間の表を持たないため問い合わせです` : '');
  }

  function html(cards) {
    if (!cards.length) return '';
    const fits = cards.filter(fit).sort((a, b) => a.amount - b.amount);
    const checks = cards.filter((c) => priced(c) && !fit(c)).sort((a, b) => a.amount - b.amount);
    const ref = !fits.length && checks.length > 0; // 全部が要確認: 確認待ちを含む参考の最安にする
    const base = ref ? checks : fits; // 最安・倍率を決める行
    const r = ratioOf(base);
    const others = cards.filter((c) => !priced(c));
    const max = Math.max(1, ...cards.filter(priced).map((c) => c.amount));
    const name = (c) => esc(c.label) + (c.sub ? `<small>${esc(c.sub)}</small>` : '');
    const days = (c) => (c.days ? `<span class="cc-days" title="${esc(c.days)}">${esc(shortDays(c.days))}</span>` : '<span class="cc-days"></span>');
    const line = (c) => {
      const w = Math.max(3, (c.amount / max) * 50).toFixed(1); // 値の文字と印が右に付くので、最長の棒でも 50% まで
      const isCheck = !fit(c);
      const cheapest = !!r && base.includes(c) && c.amount === r.lo;
      const mark = isCheck ? `<span class="cc-mark chk">要確認</span>` : '';
      const best = cheapest ? `<span class="cc-mark">${ref ? '参考の最安' : '最安'}</span>` : '';
      const why = isCheck && c.why ? `<span class="cc-why">要確認: ${esc(c.why)}</span>` : '';
      return `<div class="cc-row${cheapest && !isCheck ? ' cheap' : ''}${isCheck ? ' chk' : ''}"><span class="cc-name">${name(c)}</span>` +
        `<span class="cc-track"><i class="cc-bar" style="width:${w}%"></i><b class="cc-val num">${fmt(c.amount)}円${c.est ? '<em>試算</em>' : ''}</b>` +
        `${best}${mark}</span>${days(c)}${why}</div>`;
    };
    const none = (c) => {
      const unusable = c.status === '使えない';
      const txt = unusable ? '使えない' : c.routeNone ? 'なし（つながる航路がありません）' : '問い合わせ'; // 船は経路のカードと同じ言い方（SPEC §33 #7）
      return `<div class="cc-row off"><span class="cc-name">${name(c)}</span>` +
        `<span class="cc-track"><span class="cc-off">${txt}</span></span>${days(c)}</div>`;
    };
    let sum = '';
    if (r) sum = `<p class="cc-ratio">${esc(ratioText(r, ref))}</p>`;
    else if (fits.length === 1) sum = `<p class="cc-only">${esc(onlyText(fits[0], cards.filter((c) => c !== fits[0])))}</p>`;
    if (!r && !fits.length && checks.length === 1) sum = '<p class="cc-only">額が出るのは確認待ちの手段だけです。参考の額で、最安は決めていません</p>';
    const ttl = ref ? '手段ごとの目安額（確認待ちを含む参考の最安）' : '手段ごとの目安額';
    return `<section class="cc" aria-label="手段ごとの目安額の比べ">` +
      `<h4 class="cc-title">${ttl}<small>税抜・宅配、路線便、カゴ台車は百円の単位の試算</small></h4>` +
      `${sum}<div class="cc-rows">${fits.map(line).join('')}${checks.map(line).join('')}${others.map(none).join('')}</div></section>`;
  }

  return { html, ratioOf, ratioText, shortDays };
})();
