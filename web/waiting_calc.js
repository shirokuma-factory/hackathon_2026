"use strict";
/* 荷待ちの計算と文言のうち、地図の画面（web/own.js）と単独ページ（web/waiting.html・Artifact の版）で同じものを使う部分（SPEC §30.4）。
   tools/waiting_page_template.html にはビルドのとき（tools/build_waiting_page.py）にそのまま埋め込む。画面に出す HTML はここでは作らない。
   hotspots と byPlaceMonth は src/shipcompare/waiting.py と同じ数え方で、tests/data/waiting_vectors.json を ?selftest=1 で通して確かめている。 */
const WaitingShared = (() => {
  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  // 四捨五入（0.5 は切り上げ）。浮動小数点を使わず、waiting.py の _half_up と同じ結果にする
  const halfUp = (num, den) => Math.floor((2 * num + den) / (2 * den));
  const pct = (num, den) => (den ? halfUp(100 * num, den) : 0);
  const NO_PLACE = '（場所の記入なし）';

  // どこで・いつ待っているか（SPEC §27.1）。（契約の相手、場所、実際の到着の時間帯）ごと。合計 0 の組も返す
  function hotspots(rows) {
    const by = new Map();
    for (const r of rows) {
      const key = JSON.stringify([r.partner, r.place || NO_PLACE, `${parseInt(r.arrive.slice(0, 2), 10)}時台`]);
      if (!by.has(key)) by.set(key, []);
      by.get(key).push(r);
    }
    const out = [];
    for (const [key, rs] of by) {
      const [partner, place, band] = JSON.parse(key);
      const total = rs.reduce((a, r) => a + r.wait_min, 0);
      out.push({ partner, place, band, count: rs.length, wait_minutes: total, avg_minutes: halfUp(total, rs.length),
        max_minutes: Math.max(...rs.map((r) => r.wait_min)), over_1h_count: rs.filter((r) => r.over_1h).length, amount: rs.reduce((a, r) => a + r.amount, 0),
        designated_count: rs.filter((r) => r.appointed).length, shipper_handling_count: rs.filter((r) => r.handler === '荷主側').length });
    }
    out.sort((a, b) => (b.wait_minutes - a.wait_minutes) || cmp(a.partner, b.partner) || cmp(a.place, b.place) || cmp(a.band, b.band));
    return out;
  }

  // 場所ごと・月ごとの平均の荷待ち（SPEC §28.2、§29.1: report_wait_min で数える）。（場所、月）ごと。並びは場所の文字の順、月の順
  function byPlaceMonth(rows) {
    const by = new Map();
    for (const r of rows) {
      const key = JSON.stringify([r.place || NO_PLACE, r.month]);
      if (!by.has(key)) by.set(key, []);
      by.get(key).push(r);
    }
    const out = [];
    for (const [key, rs] of by) {
      const [place, month] = JSON.parse(key);
      const total = rs.reduce((a, r) => a + r.report_wait_min, 0);
      out.push({ place, month, count: rs.length, wait_minutes: total, avg_minutes: halfUp(total, rs.length), over_1h_count: rs.filter((r) => r.report_wait_min >= 60).length });
    }
    out.sort((a, b) => cmp(a.place, b.place) || cmp(a.month, b.month));
    return out;
  }

  // 根拠の欄にだけ出す事実（SPEC §27.2）。書面（印刷もコピーも）には入れない
  const BASIS_FACTS = [
    '荷待ちの発生を認識している荷主は約 20% です（国交省 トラック輸送状況の実態調査、2020 年度）。',
    '荷待ちのある運行の平均拘束時間は 12 時間 26 分、ない運行は 10 時間 38 分です（2020 年度の同じ調査）。1 日の拘束時間は原則 13 時間までです（改善基準告示）。',
  ];
  const GMEN_QUOTE = 'トラック・物流Gメンの是正の対象の例に「1時間以上の荷待ち又は2時間以上の荷待ち・荷役等を恒常的にさせている行為」があります。';

  // 偏りの読み方の注意（SPEC §28.1）。当てはまるものだけ、括弧で後ろに。指定の無い到着の注記は平均が 30 分以上の組だけ（§29.3）
  const hotspotNotes = (h, doc) => [
    h.designated_count * 2 < h.count && h.avg_minutes >= 30 ? (doc ? '指定の到着時刻の無い到着を含みます' : '指定の到着時刻が無い到着が多い組です。相手に到着の時刻を決めてもらうか、自社で到着を遅らせて変わるかを確かめてください') : '',
    h.shipper_handling_count > 0 ? '荷主側の荷役の時間を含む' : '',
    h.count < 3 ? '回数が少ない' : ''].filter(Boolean);
  const consultLines = (hotspotList, name) => hotspotList.filter((h) => h.partner === name && h.wait_minutes > 0).slice(0, 3)
    .map((h) => `${h.place}に${h.band}に到着した ${h.count} 回の荷待ちは、平均 ${h.avg_minutes} 分、最長 ${h.max_minutes} 分でした。${hotspotNotes(h, true).map((x) => `（${x}）`).join('')}`);
  const PLACE_MONTH_LEAD = '特定荷主は 2026 年度から、施設ごと・月別の平均の荷待ち時間を分単位で測り、国に報告します。荷主の数字を、運送会社の記録で確かめるときに使えます。';
  const PM_DEF = '到着（指定の時刻があればその時刻）から荷役開始までの分。荷主の定期報告の算定方法に合わせています。待機時間料の計算とは数え方が違います。';
  const TRUNC_LINE = '業務記録の義務は 30 分以上の荷待ちだけです。30 分未満で荷役が始まった到着も入れると、平均と 1 時間以上の割合が実際に近くなります。';
  const TRUNC_WARN = '30 分未満の荷待ちの記録がありません。平均と割合は高めに出ている可能性があります。';
  const MONTH_CLOSE = '発荷主・着荷主様との調整が必要な場合は、この一覧をそのままお使いください。';
  const monthLabel = (m) => `${m.slice(0, 4)} 年 ${parseInt(m.slice(5), 10)} 月`;
  const CONSULT_TITLE = '荷待ちを減らすためのご相談';
  const CONSULT_CLOSE = '荷待ちが減れば、待機時間料の対象も減ります。到着の時刻の指定、受付の予約、荷役を始める順番などの見直しをご相談させてください。';

  // 開いた直後と「例を入れる」の記録（架空。SPEC §27.4）。同じ場所（江東区 地点07・市川市 地点03）で、8時台に着いた回は長く待ち、10時台は短い
  // [日, 相手, 場所, 車種, 到着, 荷役開始, 荷役終了, 出発, 荷役をした人, 荷役の方法, 指定の到着時刻, 附帯業務の内容, 附帯業務の分, 荷主等の都合]
  const EX_A = '架空 元請A物流', EX_B = '架空 B食品', P7 = '江東区 地点07', P12 = '川崎市 地点12', P3 = '市川市 地点03';
  const EXAMPLE = [
    [1, EX_A, P7, '中型', '08:00', '09:25', '', '', '', '', '08:00'], [2, EX_A, P7, '中型', '08:00', '09:20', '', '', '', '', '08:00'], [5, EX_A, P7, '中型', '08:00', '09:30', '', '', '', '', '08:00'],
    [6, EX_A, P7, '中型', '08:00', '09:40', '', '', '', '', '08:00'],
    [7, EX_A, P7, '中型', '08:00', '10:15', '10:55', '11:05', '運転者', '手積み', '08:00', '検品・仕分け', '20'],
    [15, EX_A, P7, '中型', '09:00', '09:30', '09:52', '10:00', '荷主側'],
    [8, EX_A, P7, '中型', '10:05', '10:20'], [9, EX_A, P7, '中型', '10:10', '10:25'], [13, EX_A, P7, '中型', '10:15', '10:35'], [14, EX_A, P7, '中型', '10:00', '10:10'],
    [8, EX_A, P12, '中型', '13:05', '13:45'], [16, EX_A, P12, '中型', '13:20', '14:00'],
    [2, EX_B, P3, '大型', '08:00', '09:30', '', '', '', '', '08:00'], [5, EX_B, P3, '大型', '08:00', '09:40', '', '', '', '', '08:00'], [9, EX_B, P3, '大型', '08:00', '09:20', '', '', '', '', '08:00'],
    [13, EX_B, P3, '大型', '10:10', '10:25'], [14, EX_B, P3, '大型', '10:20', '10:35'],
    [15, EX_B, P12, '大型', '13:05', '13:20', '', '', '', '', '', '', '', 'いいえ'],
  ];

  // 荷主等の都合が はい の行で report_wait_min が 30 分未満の行が 0 件で、はい の行が 5 行以上のとき、強めの表示（SPEC §29.2）
  const truncWarn = (rows) => {
    const faultRows = rows.filter((x) => x.shipper_fault);
    return faultRows.length >= 5 && !faultRows.some((x) => x.report_wait_min < 30);
  };

  // 根拠の欄の「トラック・物流Gメン」の項目（相手ごとの 1 時間以上の割合を並べる）
  const gmenBasis = (ps) => {
    const share = ps.length ? `この期間の 1 時間以上の荷待ちの割合: ${ps.map((x) => `${x.partner} ${x.over_1h_share}%（${x.over_1h_count} / ${x.count} 回）`).join('、')}。` : 'この期間の 1 時間以上の荷待ちの割合は、記録を入れると出ます。';
    return `${GMEN_QUOTE}${share}「恒常的」に当たるかは、このツールは判断しません。`;
  };

  // 例の記録を、入力の表の行（16 の欄の辞書）にする。year は M/D の日付に使う年
  const COLUMNS = ['date', 'vehicle_no', 'vehicle_class', 'partner', 'place', 'arrive', 'start', 'end', 'depart', 'handler', 'method', 'shipper_fault', 'appointed', 'side_work', 'side_min', 'post_wait'];
  const exampleRows = (year) => EXAMPLE.map(([d, partner, place, vehicle_class, arrive, start, end, depart, handler, method, appointed, side_work, side_min, fault]) => ({
    ...Object.fromEntries(COLUMNS.map((k) => [k, ''])), date: `${year}-10-${String(d).padStart(2, '0')}`, partner, place, vehicle_class, arrive, start, end: end || '', depart: depart || '', handler: handler || '',
    method: method || '', appointed: appointed || '', side_work: side_work || '', side_min: side_min || '', shipper_fault: fault || 'はい',
  }));

  return {
    cmp, halfUp, pct, NO_PLACE, hotspots, byPlaceMonth, hotspotNotes, consultLines, monthLabel, truncWarn, gmenBasis, exampleRows, EXAMPLE,
    BASIS_FACTS, GMEN_QUOTE, PLACE_MONTH_LEAD, PM_DEF, TRUNC_LINE, TRUNC_WARN, MONTH_CLOSE, CONSULT_TITLE, CONSULT_CLOSE,
  };
})();
