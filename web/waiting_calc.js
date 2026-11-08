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
    h.designated_count * 2 < h.count && h.avg_minutes >= 30 ? (doc ? '指定の到着時刻の無い到着を含みます' : '指定の到着時刻が無い到着が多い組です。相手に到着の時刻を決めてもらうか、到着を遅らせて待ちが減るかを確かめてください。') : '',
    h.shipper_handling_count > 0 ? '荷主側の荷役の時間を含む' : '',
    h.count < 3 ? '回数が少ない' : ''].filter(Boolean);
  // 表のセルに入れる短い注記（2026-11-08 の最終評価 W-06）。長い文は title（ツールチップ）に出す
  const hotspotNoteCell = (h, esc) => {
    const long = hotspotNotes(h, false);
    const short = [
      h.designated_count * 2 < h.count && h.avg_minutes >= 30 ? '到着の指定なし' : '',
      h.shipper_handling_count > 0 ? '荷主側の荷役を含む' : '',
      h.count < 3 ? '回数が少ない' : ''].filter(Boolean);
    return short.length ? `<span title="${esc(long.join(' '))}">${esc(short.join('・'))}</span>` : '';
  };
  // 相手に渡す書面では、注記を括弧にせず、続く文にする
  const consultNotes = (h) => [
    h.designated_count * 2 < h.count && h.avg_minutes >= 30 ? '指定の到着時刻のない到着を含みます。' : '',
    h.shipper_handling_count > 0 ? '貴社側で荷役をしていただいた時間を含みます。' : '',
    h.count < 3 ? '回数が少ないため、参考としてご覧ください。' : ''].filter(Boolean);
  const consultLines = (hotspotList, name) => hotspotList.filter((h) => h.partner === name && h.wait_minutes > 0).slice(0, 3)
    .map((h) => `${h.place === NO_PLACE ? '場所の記載がない分のうち、' : `${h.place}では、`}${h.band}に到着した ${h.count} 回の荷待ち（到着から荷役開始まで）は、平均 ${h.avg_minutes} 分、最長 ${h.max_minutes} 分でした。${consultNotes(h).join('')}`);
  const PLACE_MONTH_LEAD = '特定荷主は 2026 年度から、施設ごと・月別の平均の荷待ち時間を分単位で測り、国に報告します。荷主の数字を、運送会社の記録で確かめるときに使えます。';
  const PM_DEF = '到着（指定の時刻があればその時刻）から荷役開始までの分です。荷主の定期報告の算定方法に合わせています。待機時間料の計算とは数え方が違います。';
  const TRUNC_LINE = '業務記録の義務は 30 分以上の荷待ちだけです。30 分未満で荷役が始まった到着も入れると、平均と 1 時間以上の割合が実際に近くなります。';
  const TRUNC_WARN = '30 分未満の荷待ちの記録がありません。平均と割合は高めに出ている可能性があります。';
  const MONTH_CLOSE = '発荷主・着荷主の方との調整が必要な場合は、この一覧をそのままお使いいただけます。';
  const monthLabel = (m) => `${m.slice(0, 4)} 年 ${parseInt(m.slice(5), 10)} 月`;
  const CONSULT_TITLE = '荷待ちを減らすためのご相談';
  const PM_PARTNER_TITLE = '場所ごと・月ごとの平均の荷待ち';
  const PM_PARTNER_SCREEN_NOTE = 'この相手の行だけです。ページ全体の表とは数字が違います。';   // 画面だけ。荷主に送る文・印刷には入れない
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

    // ---- 以下は単独ページ（tools/waiting_page_template.html）から移した、行ごとの計算と貼り付けの読み取り（SPEC §31.1）。地図の画面もこれを使い、サーバーに送らない
  const HEADER_ALIASES = {
    '日付': 'date', '車番': 'vehicle_no', '車種': 'vehicle_class', '契約の相手': 'partner', '場所': 'place', '到着': 'arrive', '荷役開始': 'start', '荷役終了': 'end',
    '出発': 'depart', '荷役をした人': 'handler', '荷役の方法': 'method', '荷主等の都合の待機か': 'shipper_fault', '荷主等の都合': 'shipper_fault',
    '指定の到着時刻': 'appointed', '附帯業務の内容': 'side_work', '附帯業務の分': 'side_min',
      '運行日': 'date', '年月日': 'date', '月日': 'date', '車両番号': 'vehicle_no', '車格': 'vehicle_class',
      '相手': 'partner', '相手先': 'partner', '荷主': 'partner', '元請': 'partner', '契約先': 'partner', '到着時刻': 'arrive', '着時刻': 'arrive',
      '積込開始': 'start', '積込開始時刻': 'start', '荷役開始時刻': 'start', '積込終了': 'end', '積込終了時刻': 'end', '荷役終了時刻': 'end',
      '出発時刻': 'depart', '発時刻': 'depart', '指定時刻': 'appointed', '指定到着': 'appointed',
      '作業日': 'date', '配送日': 'date', '納品日': 'date', 'トン数': 'vehicle_class', '車型': 'vehicle_class',
      '取引先': 'partner', '得意先': 'partner', '荷主名': 'partner', '納品先': 'place', '届け先': 'place', '配送先': 'place',
      '到着時間': 'arrive', '着': 'arrive', '積込開始時間': 'start', '荷役開始時間': 'start', '積込終了時間': 'end', '荷役終了時間': 'end',
      '出発時間': 'depart', '発': 'depart', '指定時間': 'appointed',
      // 貨物自動車運送事業輸送安全規則 第 8 条第 1 項の記録項目の言葉と、よくある様式の言葉（SPEC §23.8）
      '集荷地点等': 'place', '集貨地点等': 'place', '集荷地点': 'place', '集貨地点': 'place', '荷待ち地点': 'place', '荷待地点': 'place',
      '到着日時': 'arrive', '到着の日時': 'arrive', '指定到着日時': 'appointed', '指定の到着日時': 'appointed',
      '荷役作業の開始日時': 'start', '荷役開始日時': 'start', '荷役作業開始日時': 'start',
      '荷役作業の終了日時': 'end', '荷役終了日時': 'end', '荷役作業終了日時': 'end',
      '出発日時': 'depart', '出発の日時': 'depart', '荷役作業等の内容': 'work_content', '荷役作業の内容': 'work_content',
      '車両登録番号': 'vehicle_no', '自動車登録番号': 'vehicle_no', '登録番号': 'vehicle_no',
      // 2026-11-08 の最終評価に合わせて: Excel で自然に付ける見出し（着いた時刻・指定の時刻・荷主の都合）も読む
      '着いた時刻': 'arrive', '着いた': 'arrive', '着いた時間': 'arrive', '指定の時刻': 'appointed', '指定の時間': 'appointed',
      '荷主の都合': 'shipper_fault', '荷主の都合か': 'shipper_fault', '荷主の都合で待ったか': 'shipper_fault',
      '荷主都合': 'shipper_fault', '荷主都合か': 'shipper_fault', '荷主都合の有無': 'shipper_fault', '荷主起因': 'shipper_fault',
      // 読まない列（見出しとしては知っている。黙って読み飛ばす）
      '乗務員氏名': '_ignore', '運転者氏名': '_ignore', '乗務員名': '_ignore', '運転者名': '_ignore', '氏名': '_ignore',
      '荷主の確認の有無': '_ignore', '荷主の確認': '_ignore', '確認の有無': '_ignore', '備考': '_ignore',
      '附帯業務開始日時': '_ignore', '附帯業務終了日時': '_ignore', '附帯業務の開始日時': '_ignore', '附帯業務の終了日時': '_ignore',
      '荷役のあとの待ちを荷主等の都合として数える': 'post_wait', '荷役のあとの待ちを数える': 'post_wait', '荷役のあとの待ち': 'post_wait', '荷役後の待ち': 'post_wait',
      // 現場のメモの言葉（SPEC §41）。積み始め・積み終わり・積んだ人、受付の時刻、荷主が自社の受付記録を貼るときの運送会社・施設
      '積み始め': 'start', '積始め': 'start', '積み始め時刻': 'start', '積み終わり': 'end', '積み終り': 'end', '積終わり': 'end', '積み終わり時刻': 'end',
      '積んだ人': 'handler', '荷役者': 'handler', '作業者': 'handler', '荷役した人': 'handler', '積んだ人名': 'handler',
      '受付': 'reception', '受付時刻': 'reception', '受付時間': 'reception', '受付日時': 'reception',
      '運送会社': 'partner', '運送事業者': 'partner', '運送業者': 'partner', '施設': 'place', '施設名': 'place', '拠点': 'place',
      // ドライバーの手書きメモ・荷主の受付記録の言葉（SPEC §42）。driver.html のボタンは「呼ばれた（荷役開始）」
      '呼ばれ': 'start', '呼ばれた': 'start', '呼出': 'start', '呼出し': 'start', '呼び出し': 'start', '呼出時刻': 'start', '呼出時間': 'start', '呼び出し時刻': 'start',
      '作業開始': 'start', '作業開始時刻': 'start', '作業終了': 'end', '作業終了時刻': 'end',
      '作業開始時間': 'start', '作業終了時間': 'end', '終了': 'end', '終了時刻': 'end', '終了時間': 'end', '終了日時': 'end', '荷役終わり': 'end',
      '開始時刻': 'start', '開始時間': 'start', '開始日時': 'start', '開始': 'start',
      '行き先': 'place', '行先': 'place', '積地': 'place', '積み地': 'place', '積込地': 'place',
      // 荷主の受付記録の言葉（SPEC §43）。入場は呼ばれて構内に入った時刻（呼出と同じ扱い）、退場は出発
      '受付日': 'date', '入場日': 'date', '到着日': 'date', '日にち': 'date', '来場日': 'date',
      '入場': 'start', '入場時刻': 'start', '入場時間': 'start', '入構': 'start', '入構時刻': 'start',
      '退場': 'depart', '退場時刻': 'depart', '退場時間': 'depart', '退構': 'depart', '退構時刻': 'depart',
  };
  // 呼び出しの言葉の列。荷役開始の別の列（作業開始など）が同じ表にあるときは、荷役開始ではなく呼出（call）として読む（荷主の受付記録: 受付 → 呼出 → 作業開始）
  const CALL_HEADERS = new Set(['呼ばれ', '呼ばれた', '呼出', '呼出し', '呼び出し', '呼出時刻', '呼出時間', '呼び出し時刻', '入場', '入場時刻', '入場時間', '入構', '入構時刻']);
  // これらの列があるときは、運送会社の請求の書面の側の表（荷主の受付記録ではない）
  const CARRIER_CONTEXT_COLS = ['shipper_fault', 'side_work', 'side_min', 'post_wait', 'handler', 'method', 'work_content', 'appointed'];
  // 日付の無い時刻が前の時刻より小さいとき翌日と読むが、その差がこれを超えるなら誤りにする（9:00 → 8:30 を 23.5 時間と読まない）
  const REVERSE_MAX_MIN = 12 * 60;
  const CLASS_ALIASES = {
    '小型': '小型', '小型車': '小型', '中型': '中型', '中型車': '中型', '大型': '大型', '大型車': '大型',
    'トレーラー': 'トレーラー', 'トレーラ': 'トレーラー', 'トレーラ車': 'トレーラー', 'トレーラー車': 'トレーラー',
    'セミトレーラ': 'トレーラー', 'セミトレーラー': 'トレーラー',
  };
  const TON_CLASS = { '2': '小型', '4': '中型', '10': '大型' };   // 「4t車」「4トン」「4tウイング」の先頭の数字で見る
  const YES = new Set(['はい', 'yes', 'y', 'true', '1', '○', '〇', '◯', '有', 'あり', '荷主都合', '荷主の都合', '荷主等の都合']);
  const NO = new Set(['いいえ', 'no', 'n', 'false', '0', '×', '✕', '✗', 'x', '無', 'なし', '渋滞', '都合外']);
  const ERA_BASE = { '令和': 2018, 'r': 2018, '平成': 1988, 'h': 1988, '昭和': 1925, 's': 1925 };
  // 元号の始まりの日。これより前の日付は読めない日付にする（令和元年 1 月 5 日は平成 31 年で、打ち間違い）
  const ERA_START = { '令和': '2019-05-01', 'r': '2019-05-01', '平成': '1989-01-08', 'h': '1989-01-08', '昭和': '1926-12-25', 's': '1926-12-25' };
  const YEARLESS = /^[0-9]{1,2}(?:\/|月)[0-9]{1,2}日?(?: ?\([月火水木金土日祝]+\))?$/;
  const APPROX_TIME_NOTE = '「頃」「ごろ」と書かれた時刻は、その時刻として数えています（実際の時刻と違うときは直してください）';
  const LONG_WAIT_NOTE = '待ちが 1 日（24 時間）を超えています。日付と時刻を確かめてください';
  const MAX_ROWS = 5000;
  // 日時の欄（「2026/10/05 9:00」「10/5 9:00」「R8.10.5 9時00分」）の、時刻の前の日付の部分
  const DT_PREFIX = '(?:[0-9令和平成昭和元RHSrhs/\\-年月日.]+(?: ?\\([月火水木金土日祝]+\\))? )?';
  const DT_SPLIT = new RegExp('^([0-9令和平成昭和元RHSrhs/\\-年月日.]+(?: ?\\([月火水木金土日祝]+\\))?) ((?:[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)|(?:[0-9]{1,2}時(?:[0-9]{1,2}分?|半)?))$');
  const NO_HEADER_HINT = '1 行目に見出し（日付・到着・荷役開始…）を付けて貼り直してください。見出しが無いと、列の順で読むため、別の列を到着などとして読んでしまいます';
  const POS_NOTE = '見出しの行が無いので、列の順（日付、車番、車種、契約の相手、場所、到着、荷役開始、荷役終了、出発、荷役をした人、荷役の方法、荷主等の都合）で読みました';
  const UNKNOWN_HANDLER = '不明';
  const UNKNOWN_HANDLER_NOTE = '荷役をした人が分からないので、荷役の時間は待機にも積込料にも入れていません';
  // 契約の相手の列として黙って読んではいけない見出し（荷主は着荷主のことがある）。読んだときは asks に入れて、画面で確かめる
  const ASK_PARTNER_HEADERS = new Set(['荷主', '荷主名']);
  const ASK_PARTNER = 'partner_from_shipper';
  // 早着: 指定の到着時刻が無く、到着がこの範囲（5:00〜7:59）のとき「早着の疑い」。指定があって実際の到着が指定より前のときは、指定の時刻から数える
  const EARLY_FROM_MIN = 5 * 60, EARLY_UNTIL_MIN = 8 * 60;
  const DRIVER_LABEL = '荷待ち記録';
  // ドライバーの 1 枚（web/driver.html）が LINE に貼る文の言葉 → 入力の列
  const DRIVER_KEYS = { '着': 'arrive', '到着': 'arrive', '受付': 'reception', '荷役開始': 'start', '積込開始': 'start', '開始': 'start',
    '終了': 'end', '荷役終了': 'end', '積込終了': 'end', '出発': 'depart', '車番': 'vehicle_no', '相手': 'partner', '荷主': 'partner',
    '車種': 'vehicle_class', '指定': 'appointed', '指定時刻': 'appointed', '荷役': 'handler' };
  const DRIVER_DATE = /^(?:[0-9]{4}[/.\-])?[0-9]{1,2}(?:\/|月)[0-9]{1,2}日?(?:\([月火水木金土日祝]+\))?$/;

  /* ---------------------------------------------------------------- 計算（waiting.py と同じ） */
  const norm = (s) => String(s == null ? '' : s).trim();
  // 全角・半角をそろえ（NFKC）、空白（全角空白を含む）を 1 つにして前後を切る
  const nz = (s) => String(s == null ? '' : s).normalize('NFKC').replace(/\s+/g, ' ').trim();

  function normalizeClass(raw) {
    const key = nz(raw).replace(/[\s（）()]/g, '').toLowerCase();
    if (Object.prototype.hasOwnProperty.call(CLASS_ALIASES, key)) return CLASS_ALIASES[key];
    const m = /^(10|2|4)(?:t|ton|トン)/.exec(key);
    if (m) return TON_CLASS[m[1]];
    for (const name of ['トレーラー', 'トレーラ', '小型', '中型', '大型']) if (key.startsWith(name.toLowerCase())) return name.startsWith('トレーラ') ? 'トレーラー' : name;
    return null;
  }

  // 時刻を分にする。9:00 / 9:00:00 / 09時00分 / 9時 / 0900 / 900（全角も可、日付つきも可）。読めなければ null
  function parseTime(text) {
    const t = nz(text).replace(/\s*(?:頃|ごろ|ころ)$/, '');   // 「8:00頃」は 8:00 として読む（行の注記は calcRow）
    let h, mi, sec;
    let m = new RegExp(`^${DT_PREFIX}([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}))?$`).exec(t);
    if (m) { h = Number(m[1]); mi = Number(m[2]); sec = Number(m[3] || 0); }
    else if ((m = new RegExp(`^${DT_PREFIX}([0-9]{1,2})時(?:([0-9]{1,2})分?|半)?$`).exec(t))) { h = Number(m[1]); mi = t.endsWith('半') ? 30 : Number(m[2] || 0); sec = 0; }
    else if ((m = /^([0-9]{1,2})([0-9]{2})$/.exec(t))) { h = Number(m[1]); mi = Number(m[2]); sec = 0; }
    else return null;
    if (h > 23 || mi > 59 || sec > 59) return null;
    return h * 60 + mi;
  }

  const fmtTime = (m) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;

  function mkDate(y, mo, d) {
    if (y < 1 || y > 9999 || mo < 1 || mo > 12 || d < 1) return null;
    const dt = new Date(Date.UTC(2000, mo - 1, 1));
    dt.setUTCFullYear(y);
    dt.setUTCMonth(mo - 1, d);
    if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
    return `${String(y).padStart(4, '0')}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  }

  // 日付を読む。2026-10-05 / 2026/10/5 / 2026年10月5日 / R8.10.5 / 令和8年10月5日（元年も可）と、年を渡したときの 10/5 / 10月5日。曜日の（月）や時刻が後ろに付いていてもよい
  function parseDate(text, year) {
    let t = nz(text);
    t = t.replace(/ [0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?$/, '');
    t = t.replace(/ ?\([月火水木金土日祝]+\)$/, '');
    let m = /^([0-9]{4})(?:[-/.]|年)([0-9]{1,2})(?:[-/.]|月)([0-9]{1,2})日?$/.exec(t);
    if (m) return mkDate(Number(m[1]), Number(m[2]), Number(m[3]));
    m = /^(令和|平成|昭和|r|h|s)(元|[0-9]{1,2})(?:[-/.]|年)([0-9]{1,2})(?:[-/.]|月)([0-9]{1,2})日?$/i.exec(t);
    if (m) {
      const n = m[2] === '元' ? 1 : Number(m[2]);
      const key = m[1].toLowerCase();
      const dt = mkDate(ERA_BASE[key] + n, Number(m[3]), Number(m[4]));
      return dt !== null && dt >= ERA_START[key] ? dt : null;
    }
    m = /^([0-9]{1,2})(?:\/|月)([0-9]{1,2})日?$/.exec(t);
    if (m && year != null) return mkDate(year, Number(m[1]), Number(m[2]));
    return null;
  }

  function toBool(raw) {
    const k = nz(raw).toLowerCase();
    if (YES.has(k)) return true;
    if (NO.has(k)) return false;
    return null;
  }

  const headerKey = (c) => nz(c).replace(/\s+/g, '');

  // 日時の欄（「2026/10/05 9:00」「10/5 9:00」）から日付の部分を取り出す。無ければ空
  function datePart(text) {
    const m = DT_SPLIT.exec(nz(text));
    return !m || parseTime(m[2]) === null ? '' : m[1];
  }

  // Excel から貼り付けたタブ区切りを行にする（waiting.py の parse_paste_ex と同じ）。
  // 見出しがあれば見出しの名前で読み、読まなかった見出しは notes に名前を書く。見出しの行が無いときだけ列の順で読み、notes に書く。
  // 見出しがあって「荷主等の都合」の欄が無い、または見出しが無くてどの行にも欄が無いときは、全部の行を はい にして defaulted に印を付ける
  const nzLines = (text) => String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n').filter((ln) => ln.trim()).map(nz);

  // ドライバーの 1 枚（web/driver.html）が LINE に貼る文を行にする（SPEC §41。waiting.py の parse_driver_text と同じ）。
  // 1 件 =「荷待ち記録 11/10 江東区 ○○倉庫 着 7:50 受付 8:05 荷役開始 9:40 終了 10:30 出発 10:40 車番 1234 相手 ○○」の形（1 行）。
  // 日付の次から最初の言葉までが場所。言葉の順は問わない。1 件は行の終わりか次の「荷待ち記録」で終わる
  function parseDriverText(text) {
    const rows = [];
    for (const line of nzLines(text)) {
      for (const seg of line.split(DRIVER_LABEL).slice(1)) {
        const toks = seg.split(' ').filter(Boolean);
        if (!toks.length) continue;
        const r = {};
        let i = 0;
        if (DRIVER_DATE.test(toks[0])) { r.date = toks[0].replace(/\([月火水木金土日祝]+\)$/, ''); i = 1; }
        const place = [];
        while (i < toks.length && !has(DRIVER_KEYS, toks[i])) place.push(toks[i++]);
        if (place.length) r.place = place.join(' ');
        while (i < toks.length) {
          const key = DRIVER_KEYS[toks[i++]];
          const val = [];
          while (i < toks.length && !has(DRIVER_KEYS, toks[i])) val.push(toks[i++]);
          const v = val.join(' ');
          if (v && !['-', 'ー', 'なし'].includes(v)) r[key] = v;
        }
        if (r.end && !r.handler) r.handler = UNKNOWN_HANDLER;
        rows.push(r);
      }
    }
    return rows;
  }
  const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

  // ---- 「荷待ち記録」で始まらないスマホのメモ（SPEC §43。waiting.py の parse_memo_text と同じ）。1 行 = 1 件: 「11/10 江東 着7:50 呼ばれ9:40 終わり10:30」
  const MEMO_NO_PARTNER = '（相手未記入）';
  // 契約の相手が空の行を、画面で相手を入れてもらうまで仮の名前で計算する（defaults.provisional_partner を渡したときだけ）
  const PROVISIONAL_PARTNER = '（相手を入れてください）';
  const MEMO_UNREADABLE = '読める形ではありませんでした（{shown}）。次のどれかの形で貼ってください: Excel の表（1 行目が見出し、列はタブ区切り）、'
    + 'ドライバー用のページで作った文（荷待ち記録 11/10 …）、1 行 1 件のメモ（11/10 江東 着7:50 呼ばれ9:40 終わり10:30）';
  const MEMO_KEYS = { '到着': 'arrive', '着': 'arrive', '受付': 'reception', '受': 'reception', '呼ばれた': 'call', '呼ばれ': 'call', '呼び出し': 'call', '呼出し': 'call', '呼出': 'call', '呼': 'call',
    '荷役開始': 'start', '積込開始': 'start', '積み始め': 'start', '積始め': 'start', '開始': 'start',
    '荷役終了': 'end', '積込終了': 'end', '積み終わり': 'end', '積み終り': 'end', '積終わり': 'end', '終わり': 'end', '終了': 'end', '積込終': 'end', '積終': 'end', '終': 'end',
    '出発': 'depart', '発': 'depart', '指定': 'appointed' };
  // 「520」「1530」のように 3〜4 桁の数字だけの書き方は、言葉の隣にあるときだけ 時:分 として読む（5:20、15:30）。車番などの数字を時刻にしない
  const MEMO_TIME = '(?:[0-9]{1,2}:[0-9]{2}|[0-9]{1,2}時(?:[0-9]{1,2}分?|半)?|(?<![0-9])[0-2]?[0-9][0-5][0-9](?![0-9:]))';
  const MEMO_KEYS_RE = Object.keys(MEMO_KEYS).sort((a, b) => b.length - a.length).join('|');
  // 「着7:50」（言葉が先）と「6:10着」（時刻が先。間に空白を入れない）の両方を、左から重ならないように拾う
  const memoRe = () => new RegExp('(?:(' + MEMO_KEYS_RE + ')[ :]?(' + MEMO_TIME + ')|(' + MEMO_TIME + ')(' + MEMO_KEYS_RE + '))', 'g');
  const memoTextRe = () => /(車番|相手|荷主|車種) ?([^ ]+)/g;
  const memoPlateRe = () => /車 ?([0-9][0-9-]*)/g;   // 「車1234」「車 1234」
  const MEMO_CLASS_RE = /^(?:(?:大型|中型|小型)車?|トレーラー?車?|(?:10|2|4)(?:t|ton|トン)\S*)$/i;   // 「大型」「4t」だけでも車種
  const MEMO_DATE_RE = /^((?:[0-9]{4}[/.-])?[0-9]{1,2}[/\-月][0-9]{1,2}日?(?:\([月火水木金土日祝]+\))?)(?= |$)/;
  const MEMO_TEXT_COLS = { '車番': 'vehicle_no', '相手': 'partner', '荷主': 'partner', '車種': 'vehicle_class' };
  const memoTimeNorm = (tm) => { const m = !tm.includes(':') && !tm.includes('時') ? /^([0-9]{1,2})([0-9]{2})$/.exec(tm) : null; return m ? `${Number(m[1])}:${m[2]}` : tm; };
  const memoFound = (rest) => {
    const found = {};
    for (const k of rest.matchAll(memoRe())) {
      const key = k[1] !== undefined ? k[1] : k[4], tm = memoTimeNorm(k[1] !== undefined ? k[2] : k[3]);
      const col = MEMO_KEYS[key];
      if (!has(found, col)) found[col] = tm;
    }
    return found;
  };
  const memoMinutes = (t) => {
    let m = /^([0-9]{1,2}):([0-9]{2})$/.exec(t);
    if (m) return Number(m[1]) * 60 + Number(m[2]);
    m = /^([0-9]{1,2})時(?:([0-9]{1,2})分?|(半))?$/.exec(t);
    if (m) return Number(m[1]) * 60 + (m[3] ? 30 : Number(m[2] || 0));
    return null;
  };
  const memoClip = (line) => { const a = Array.from(line); return a.length <= 36 ? line : a.slice(0, 36).join('') + '…'; };

  function parseMemoText(text) {
    const lines = nzLines(text);
    const isMemo = lines.some((ln) => { const m = MEMO_DATE_RE.exec(ln); return m && memoRe().test(ln.slice(m[0].length)); });
    if (!isMemo) return null;
    const rows = [], bad = [], read = [];
    for (const ln of lines) {
      const m = MEMO_DATE_RE.exec(ln);
      if (!m) { bad.push([ln, '日付（11/10 のような形）で始まっていません']); continue; }
      const dateS = m[1].replace(/\([月火水木金土日祝]+\)$/, '');
      if (parseDate(dateS, 2000) === null) { bad.push([ln, `日付「${dateS}」を読めません`]); continue; }
      const rest = ln.slice(m[0].length);
      const found = memoFound(rest);
      const rest2 = rest.replace(memoRe(), ' ');
      const r = { date: dateS };
      for (const k of rest2.matchAll(memoTextRe())) { const col = MEMO_TEXT_COLS[k[1]]; if (!has(r, col)) r[col] = k[2]; }
      let rem = rest2.replace(memoTextRe(), ' ');
      for (const k of rem.matchAll(memoPlateRe())) { if (!has(r, 'vehicle_no')) r.vehicle_no = k[1]; }
      rem = rem.replace(memoPlateRe(), ' ');
      let toks = [];
      for (const t of rem.split(/\s+/).filter(Boolean)) {
        if (MEMO_CLASS_RE.test(t) && !has(r, 'vehicle_class')) r.vehicle_class = t; else toks.push(t);
      }
      let unread = [];
      if (!has(r, 'partner') && toks.length >= 2) {
        r.partner = toks[0];
        unread = toks.slice(2);
        toks = toks.slice(1, 2);
      }
      const place = toks.join(' ');
      if (place) r.place = place;
      if (!has(found, 'arrive')) { bad.push([ln, '着（到着）の時刻がありません']); continue; }
      if (!has(found, 'start') && !has(found, 'call')) { bad.push([ln, '呼ばれ（荷役開始・積み始め）の時刻がありません']); continue; }
      r.arrive = found.arrive;
      if (has(found, 'reception')) r.reception = found.reception;
      if (has(found, 'start')) { r.start = found.start; if (has(found, 'call')) r.call = found.call; } else r.start = found.call;
      for (const col of ['end', 'depart', 'appointed']) if (has(found, col)) r[col] = found[col];
      if (r.end) r.handler = UNKNOWN_HANDLER;
      const check = [];
      const aMin = memoMinutes(found.arrive), rcMin = has(found, 'reception') ? memoMinutes(found.reception) : null;
      if (aMin !== null && rcMin !== null && rcMin < aMin) check.push(`受付 ${found.reception} が到着 ${found.arrive} より前です`);
      if (/[0-9]{1,2}:[0-9]{2}|[0-9]{1,2}時/.test(place)) check.push('場所に時刻のような文字が残っています');
      rows.push(r);
      read.push({ line: memoClip(ln), partner: r.partner || '', vehicle_no: r.vehicle_no || '', vehicle_class: r.vehicle_class || '', place: r.place || '', unread, check });
    }
    return { rows, bad, read };
  }

  function parsePasteFull(text) {
    const lines = String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n').filter((ln) => ln.trim());
    const asks = [];
    if (!lines.length) return { rows: [], defaulted: [], notes: [], asks: [], shipper_log: false };
    const headLine = (ln) => { const f = ln.split(String.fromCharCode(9)).map(headerKey); return f.length > 0 && (f[0] === '日付' || f[0] === 'date' || f.filter((c) => has(HEADER_ALIASES, c)).length >= 3); };
    // タブのある貼り付けは Excel の表（表題に「荷待ち記録」とあっても表として読む）。見出しの行が無いタブ区切り（PC の LINE の「トーク履歴を保存」）は、「荷待ち記録」から後ろをドライバーの文として読む
    if (nz(text).includes(DRIVER_LABEL) && (!lines.some((ln) => ln.includes(String.fromCharCode(9))) || !lines.slice(0, 5).some(headLine))) {
      const drows = parseDriverText(text);
      const defaulted = [], notes = [];
      // 「荷待ち記録」を含まない行のうち、日付で始まるメモの形の行（PC の LINE の本文欄に手で書いた「10/7 船橋DC 着8:05 呼ばれ9:50 終わり10:30」）は、メモとして読む。読めないものは行を挙げる
      const otherLines = lines.filter((ln) => !ln.includes(DRIVER_LABEL)).map((ln) => nz(ln.split(String.fromCharCode(9)).pop()))
        .filter((ln) => { const m = MEMO_DATE_RE.exec(ln); return m && ln.slice(m[0].length).trim(); });
      if (otherLines.length) {
        const memo = parseMemoText(otherLines.join('\n'));
        if (memo) {
          memo.rows.forEach((r) => { if (!r.partner) r.partner = MEMO_NO_PARTNER; drows.push(r); });
          if (memo.rows.length) notes.push(`「荷待ち記録」で始まらないメモの行も読みました（${memo.rows.length} 件）。契約の相手が書かれていない行は『${MEMO_NO_PARTNER}』としています`);
          if (memo.bad.length) notes.push(`読まなかった行（${memo.bad.length} 行）: ` + memo.bad.map(([ln, why]) => `「${memoClip(ln)}」は${why}`).join('、'));
        } else notes.push(`読まなかった行（${otherLines.length} 行）: ` + otherLines.map((ln) => `「${memoClip(ln)}」`).join('、') + '。『着7:50』『呼ばれ9:40』のような言葉つきの時刻が無いので読めません');
      }
      if (drows.length) {
        notes.push(`ドライバーの記録（荷待ち記録）を読みました（${drows.length} 件）。『相手』を契約の相手として読んでいます`);
        if (drows.some((r) => r.partner)) asks.push(ASK_PARTNER);
        drows.forEach((r) => { r.shipper_fault = 'はい'; });
        defaulted.push('shipper_fault');
      }
      return { rows: drows, defaulted, notes, asks, shipper_log: false };
    }
    if (!lines.some((ln) => ln.includes(String.fromCharCode(9)))) {   // タブが無く、日付で始まるメモの行（「11/10 江東 着7:50 呼ばれ9:40 終わり10:30」）
      const memo = parseMemoText(text);
      if (memo !== null) {
        const mrows = memo.rows, mnotes = [], mdefaulted = [];
        if (mrows.length) {
          mnotes.push(`スマホのメモの書き方で読みました（${mrows.length} 件）。『着』『受付（受）』『呼ばれ（呼）』『積み始め』『終わり（終・積込終）』『出発』の言葉の前後の時刻を読んでいます（『6:10着』のように時刻が先でも読みます）。行ごとに読んだ相手・車番・車種・場所を、下の「読み取った内容」に出します。違うときは、そこか表で直してください。`);
          if (mrows.some((r) => !r.partner)) {
            mrows.forEach((r) => { if (!r.partner) r.partner = MEMO_NO_PARTNER; });
            mnotes.push(`契約の相手が書かれていない行は『${MEMO_NO_PARTNER}』としました。表の『契約の相手』を書き換えてください（メモに『相手 ○○』と書き足しても読みます）`);
          }
          if (mrows.some((r) => r.partner !== MEMO_NO_PARTNER)) asks.push(ASK_PARTNER);
          mrows.forEach((r) => { r.shipper_fault = 'はい'; });
          mdefaulted.push('shipper_fault');
        }
        if (memo.bad.length) {
          const items = memo.bad.map(([ln, why]) => `「${memoClip(ln)}」は${why}`).join('、');
          mnotes.push(`読めなかった行は表に入れていません（${memo.bad.length} 行）: ${items}。1 行に『日付 場所 着7:50 呼ばれ9:40 終わり10:30』の形で書くか、ドライバー用のページで作った文を使ってください`);
        }
        memo.read.forEach((rd) => { if (rd.check.length) mnotes.push(`確かめてください:「${rd.line}」は` + rd.check.join('、')); });
        return { rows: mrows, defaulted: mdefaulted, notes: mnotes, asks, shipper_log: false, memo_read: memo.read };
      }
      const shown = nzLines(text).slice(0, 3).map((ln) => `「${memoClip(ln)}」`).join('、');
      return { rows: [], defaulted: [], notes: [MEMO_UNREADABLE.replace('{shown}', shown)], asks, shipper_log: false };
    }
    const notes = [];
    let cols = COLUMNS.slice();
    let body = lines, header = false;
    // 表題の行が見出しの上にあるときのために、先頭の数行から見出しの行を探す（waiting.py の HEADER_SEARCH_LINES と同じ）
    const isHead = (ln) => { const f = ln.split('\t').map(headerKey); return f.length > 0 && (f[0] === '日付' || f[0] === 'date' || f.filter((c) => has(HEADER_ALIASES, c)).length >= 3); };
    let headAt = lines.slice(0, 5).findIndex(isHead);
    if (headAt < 0) headAt = 0;
    const rawFirst = lines[headAt].split('\t');
    const first = rawFirst.map(headerKey);
    const known = first.filter((c) => has(HEADER_ALIASES, c)).length;
    if (first.length && (first[0] === '日付' || first[0] === 'date' || known >= 3)) {
      header = true;
      if (headAt) notes.push(`見出しの上の ${headAt} 行（「${nz(lines[0].split('\t')[0]).slice(0, 20)}」など）は表題として読まずに、${headAt + 1} 行目を見出しにしました`);
      const mapped = first.map((c) => (has(HEADER_ALIASES, c) ? HEADER_ALIASES[c] : null));
      const startIdx = mapped.map((c, i) => (c === 'start' ? i : -1)).filter((i) => i >= 0);
      const callIdx = startIdx.filter((i) => CALL_HEADERS.has(first[i]));
      if (callIdx.length && callIdx.length < startIdx.length) callIdx.forEach((i) => { mapped[i] = 'call'; });   // 作業開始などの別の列があるとき、呼出は荷役開始にしない
      cols = mapped.map((c) => (c && c !== '_ignore' ? c : ''));   // 読まない列・知らない列は '' にして、行に入れない
      if (mapped.some((c, i) => c === 'partner' && ASK_PARTNER_HEADERS.has(first[i]))) asks.push(ASK_PARTNER);
      const seen = new Set();
      rawFirst.forEach((raw, i) => {
        const name = nz(raw);
        if (mapped[i] === null && name && !seen.has(name)) { seen.add(name); notes.push(`見出し『${name}』は読みませんでした`); }
      });
      body = lines.slice(headAt + 1);
    } else {
      notes.push(POS_NOTE);
      // 見出しが無く、列の順で読むと到着の列が時刻にならないとき: 見出しを付けて貼り直してもらう
      const c0 = (body[0] || '').split('\t');
      if (c0.length && !/^\s*\d{1,2}[:：時]\d{0,2}/.test(c0[COLUMNS.indexOf('arrive')] || '')) notes.push(NO_HEADER_HINT);
    }
    const rows = body.map((ln) => {
      const cells = ln.split('\t'), o = {};
      for (let i = 0; i < Math.min(cols.length, cells.length); i++) if (cols[i]) o[cols[i]] = cells[i].trim();
      return o;
    });
    // 日付の列が無い（または空の）行は、到着日時などの日時の欄の日付の部分を日付にする
    for (const r of rows) {
      if (!r.date) {
        for (const k of ['arrive', 'start', 'end', 'depart']) {
          const d = datePart(r[k] || '');
          if (d) { r.date = d; break; }
        }
      }
    }
    // 荷主の自社の受付記録: 受付の列があり、運送会社の書面の側の列（荷主等の都合、荷役をした人、附帯業務など）が無い
    const shipperLog = !!(header && cols.includes('reception') && !cols.includes('arrive') && !CARRIER_CONTEXT_COLS.some((c) => cols.includes(c)));
    // 到着の列が無く受付の列があるとき（荷主が自社の受付記録を貼る使い方）は、受付の時刻を到着として読む
    if (header && !cols.includes('arrive') && cols.includes('reception')) {
      for (const r of rows) if (r.reception) { r.arrive = r.reception; delete r.reception; r.arrive_src = 'reception'; }   // 到着として読んだ印。早着の文を受付の言葉にする
      notes.push('到着の列が無いので、受付の時刻を到着として読みました（荷主の受付記録を読むときの扱いです。受付の前に着いて待った時間は入りません）。');
    }
    // 荷役をした人の列が無いとき: 荷役作業等の内容があれば運転者（法定の記録は運転者自身の荷役）、無ければ不明
    if (header && !cols.includes('handler')) {
      let derived = false;
      for (const r of rows) {
        if (!r.end) continue;
        if (r.work_content) {
          r.handler = '運転者';
          if (!r.method) {
            const wc = nz(r.work_content);
            r.method = wc.includes('フォーク') || wc.includes('機械') ? 'フォークリフト等' : '手積み';
          }
          derived = true;
        } else r.handler = UNKNOWN_HANDLER;
      }
      if (derived) notes.push('荷役をした人の列が無いので、荷役作業等の内容がある行は運転者が荷役をしたとして読みました（荷役の方法が書かれていない行は、荷役作業等の内容にフォークリフトの語が無ければ手積み）。違う行は表で直してください');
    }
    const defaulted = [];
    const noFault = header ? !cols.includes('shipper_fault') : rows.every((r) => !has(r, 'shipper_fault'));
    if (rows.length && noFault) {
      rows.forEach((r) => { r.shipper_fault = 'はい'; });
      defaulted.push('shipper_fault');
    }
    return { rows, defaulted, notes, asks, shipper_log: shipperLog, carrier_table: !!(header && !shipperLog && CARRIER_CONTEXT_COLS.some((c) => cols.includes(c))) };   // carrier_table: 運送会社の書面の側の列がある表（荷主の画面から戻す判断に使う）
  }
  const parsePaste = (text) => parsePasteFull(text).rows;

  // 紙・PDF からコピーした表（列を空白で区切ったもの）やタブ区切りの表を、parsePasteFull に渡せるタブ区切りにする（SPEC §43）。
  // 1 行目が見出し（日付・車番・到着・荷役開始・荷役終了など 3 つ以上が分かる言葉）のときだけ読む。空白 2 つ以上で区切った表を先に試し、だめなら空白 1 つで区切る。
  // 列の数が見出しと違う行は読まず skipped に返す。表ではないときは null
  function looseTableToTsv(text) {
    const lines = String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n').filter((ln) => ln.trim());
    if (lines.length < 2) return null;
    const known = (cells) => cells.filter((c) => has(HEADER_ALIASES, headerKey(c))).length;
    if (lines.some((ln) => ln.includes('\t'))) return known(lines[0].split('\t')) >= 3 ? { tsv: lines.join('\n'), skipped: [] } : null;
    // 空白 1 つ区切りでは、場所名や会社名に空白があると列がずれる。見出しより多く分かれた行は、場所（無ければ契約の相手）の欄に余りをまとめ、
    // 到着・荷役開始などの時刻の欄が時刻の形になっているかで合っているかを確かめる（SPEC §47）
    const TIME_KEYS_L = ['arrive', 'start', 'end', 'depart', 'appointed', 'reception', 'call'];
    const mergeExtra = (toks, head) => {
      if (toks.length <= head.length) return toks;
      const keys = head.map((h) => HEADER_ALIASES[headerKey(h)]);
      const extra = toks.length - head.length;
      const tries = [keys.indexOf('place'), keys.indexOf('partner')].filter((i, k, a) => i >= 0 && a.indexOf(i) === k);
      for (const idx of tries) {
        const t = toks.slice(0, idx).concat([toks.slice(idx, idx + extra + 1).join(' ')], toks.slice(idx + extra + 1));
        if (t.every((c, i) => !TIME_KEYS_L.includes(keys[i]) || c === '' || parseTime(c) !== null)) return t;
      }
      return toks;
    };
    const splitters = [(ln) => ln.normalize('NFKC').trim().split(/ {2,}/), (ln, head) => mergeExtra(nz(ln).split(' '), head || [])];
    for (const sp of splitters) {
      const head = sp(lines[0]);
      if (known(head) < 3) continue;
      const out = [head.join('\t')], skipped = [];
      for (const ln of lines.slice(1)) { const c = sp(ln, head); if (c.length === head.length) out.push(c.join('\t')); else skipped.push(nz(ln)); }
      return { tsv: out.join('\n'), skipped };
    }
    return null;
  }

  const ceilDiv = (a, b) => (a > 0 ? Math.ceil(a / b) : 0);

  function reverseMsg(label, t, prevLabel, pt, gap) {
    return `${label}（${fmtTime(t)}）が${prevLabel}（${fmtTime(pt)}）より前です。日付が無いので翌日と読むと ${Math.floor(gap / 60)} 時間 ${gap % 60} 分あいたことになります。` +
      '日付つきの時刻（10/5 09:00 のように）で入れるか、時刻を確かめてください';
  }

  function calcRow(n, raw, year, tariff, defaults) {
    const errs = [];
    const g = {};
    for (const k of COLUMNS) g[k] = norm(raw[k]);
    defaults = defaults || {};
    const used = [];   // 空欄を既定の値で計算した欄（SPEC §41）

    let d = null;
    if (!g.date) errs.push('日付がありません');
    else {
      d = parseDate(g.date, year);
      if (d === null) {
        if (year == null && parseDate(g.date, 2000) !== null) errs.push(`日付「${g.date}」の年が分かりません（年を選んでください）`);
        else errs.push(`日付「${g.date}」を読めません（2026-10-05、2026/10/5、10/5、10月5日、R8.10.5、令和8年10月5日 のように入れてください）`);
      }
    }

    let vclass = null;
    if (!g.vehicle_class && defaults.vehicle_class) { vclass = defaults.vehicle_class; used.push('vehicle_class'); }
    else if (!g.vehicle_class) errs.push('車種がありません');
    else {
      vclass = normalizeClass(g.vehicle_class);
      if (vclass === null) errs.push(`車種「${g.vehicle_class}」を読めません。小型・中型・大型・トレーラーのどれかを入れてください（2t・4t・10t でも読みます）。`);
    }
    let partner = nz(g.partner);   // 全角・半角の違いで相手が分かれないようにそろえる
    let partnerProvisional = false;
    if (partner === nz(MEMO_NO_PARTNER) && defaults.provisional_partner) partner = '';   // スマホのメモで相手が書かれていない行も、空と同じに扱う
    if (!partner && defaults.provisional_partner) { partner = PROVISIONAL_PARTNER; partnerProvisional = true; }   // 荷待ちの記録のページだけ。相手の列が無い表も計算し、画面で相手を入れてもらう
    else if (!partner) errs.push('契約の相手がありません');

    const needTime = (key, label, required) => {
      if (!g[key]) { if (required) errs.push(`${label}がありません`); return null; }
      const v = parseTime(g[key]);
      if (v === null) errs.push(`${label}「${g[key]}」を読めません（9:00、09時00分、0900 のように入れてください）`);
      return v;
    };
    const arrive = needTime('arrive', '到着', true);
    const start = needTime('start', '荷役開始', true);
    const end = needTime('end', '荷役終了', false);
    const depart = needTime('depart', '出発', false);
    const appointed = needTime('appointed', '指定の到着時刻', false);
    const receptionRaw = norm(raw.reception);   // 受付の時刻（記録の一覧に出すだけ。計算には使わない。読めないときは行を落とさず注記する）
    const reception = receptionRaw ? parseTime(receptionRaw) : null;
    const callRaw = norm(raw.call);   // 呼出の時刻（荷主の受付記録。記録の一覧に出すだけ。計算には使わない）
    const call = callRaw ? parseTime(callRaw) : null;
    let sideMin = 0;
    if (g.side_min) {
      if (/^[0-9]{1,4}$/.test(nz(g.side_min))) sideMin = Number(nz(g.side_min));
      else errs.push(`附帯業務の分「${g.side_min}」を読めません（分を整数で入れてください）`);
    }

    let handler = nz(g.handler);
    if (['荷主', '荷主側', '先方', '客先', '倉庫側'].includes(handler)) handler = '荷主側';
    else if (['運転者', 'ドライバー', '運転手', '自分', '自社'].includes(handler)) handler = '運転者';
    else if (handler === UNKNOWN_HANDLER) { /* 荷役をした人の列が無い貼り付け。行は残し、荷役の時間は数えない（下で注記） */ }
    else if (handler) { errs.push(`荷役をした人「${handler}」を読めません（運転者 / 荷主側）`); handler = ''; }
    const hasHandling = !!g.end;
    if (!handler && (hasHandling || g.method)) {
      if (!errs.some((e) => e.includes('荷役をした人'))) errs.push('荷役をした人がありません（運転者 / 荷主側）');
    }
    if (handler && handler !== UNKNOWN_HANDLER && !hasHandling) errs.push('荷役をした人を入れたときは、荷役終了も入れてください');

    let method = nz(g.method);
    if (['フォークリフト', 'フォークリフト等', 'フォーク', '機械'].includes(method)) method = 'フォークリフト等';
    const hasMethod = (m) => Object.prototype.hasOwnProperty.call(tariff.handling, m);
    if (handler === '運転者') {
      if (!method && defaults.method) { method = defaults.method; used.push('method'); }
      else if (!method) errs.push('運転者が荷役をしたときは、荷役の方法（手積み / フォークリフト等）が必要です');
      else if (!hasMethod(method)) { errs.push(`荷役の方法「${g.method}」を読めません（手積み / フォークリフト等）`); method = ''; }
    } else if (method && !hasMethod(method)) method = '';

    let postOn = false;
    if (g.post_wait) {
      const pw = toBool(g.post_wait);
      if (pw === null) errs.push(`荷役のあとの待ちを数えるか「${g.post_wait}」を読めません（はい / 空欄）`);
      else postOn = pw;
    }
    if (postOn && (!g.end || !g.depart)) errs.push('荷役のあとの待ちを数えるときは、荷役終了と出発を入れてください');

    let fault = null;
    if (!g.shipper_fault) errs.push('荷主等の都合の待機か（はい / いいえ）がありません');
    else {
      fault = toBool(g.shipper_fault);
      if (fault === null) errs.push(`荷主等の都合の待機か「${g.shipper_fault}」を読めません（はい / いいえ）`);
    }
    if (errs.length) return { res: null, errs };

    // 日時の欄に日付が書いてあるときは、日付と時刻から実際の差を出す（SPEC §38）。日付が無い欄だけ、
    // 到着を基準に、前の時刻より小さければ +24 時間（日付をまたぐ）として扱う
    const yr = Number(d.slice(0, 4));
    const ordOf = (iso) => Date.parse(`${iso}T00:00:00Z`) / 86400000;
    const fieldDay = (key, ref) => {
      const part = g[key] ? datePart(g[key]) : '';
      if (!part) return null;
      let dd = parseDate(part, yr);
      if (dd !== null && ref != null && YEARLESS.test(nz(part)) && ordOf(dd) < ref - 180) dd = parseDate(part, yr + 1);   // 年の無い 1/1 が 12/31 の翌日になる場合
      return dd !== null ? ordOf(dd) : null;
    };
    let aDay = fieldDay('arrive', null);
    if (aDay === null) aDay = ordOf(d);
    const aAbs = aDay * 1440 + arrive;

    let sRel;
    const sDay = fieldDay('start', aDay);
    if (sDay !== null) {
      sRel = sDay * 1440 + start - aAbs;
      if (sRel < 0) errs.push('荷役開始が到着より前です（日付と時刻を確かめてください）');
    } else {
      sRel = start - arrive;
      if (sRel < 0) {
        sRel += 1440;
        if (sRel > REVERSE_MAX_MIN) errs.push(reverseMsg('荷役開始', start, '到着', arrive, sRel));
      }
    }
    let eRel = null;
    if (end !== null) {
      const eDay = fieldDay('end', aDay);
      if (eDay !== null) {
        eRel = eDay * 1440 + end - aAbs;
        if (eRel < sRel) errs.push('荷役終了が荷役開始より前です（日付と時刻を確かめてください）');
      } else {
        eRel = end - arrive;
        if (eRel < sRel) {
          while (eRel < sRel) eRel += 1440;
          if (eRel - sRel > REVERSE_MAX_MIN) errs.push(reverseMsg('荷役終了', end, '荷役開始', start, eRel - sRel));
        }
      }
    }
    let postMin = 0;   // 荷役終了 → 出発（荷役のあとの待ち）
    let depRel = null;
    if (depart !== null) {
      const floor = eRel !== null ? eRel : sRel;
      const pDay = fieldDay('depart', aDay);
      if (pDay !== null) {
        depRel = pDay * 1440 + depart - aAbs;
        if (depRel < floor) errs.push(`出発が${eRel !== null ? '荷役終了' : '荷役開始'}より前です（日付と時刻を確かめてください）`);
      } else {
        depRel = depart - arrive;
        if (depRel < floor) {
          while (depRel < floor) depRel += 1440;
          if (depRel - floor > REVERSE_MAX_MIN) errs.push(reverseMsg('出発', depart, eRel !== null ? '荷役終了' : '荷役開始', eRel !== null ? end : start, depRel - floor));
        }
      }
      if (postOn && eRel !== null) postMin = depRel - eRel;
    }
    if (errs.length) return { res: null, errs };
    const crosses = arrive + Math.max(...[sRel, eRel, depRel].filter((x) => x !== null)) >= 1440;
    const handlingMin = eRel !== null ? eRel - sRel : 0;

    const unit = tariff.unit_minutes, free = tariff.free_minutes, thr = tariff.threshold_minutes;

    // 待機の始まり = max(到着, 指定の到着時刻)。指定との差は 12 時間以内の近い側に取る（SPEC §23.7）
    let base = 0;
    if (appointed !== null) {
      const apDay = fieldDay('appointed', aDay);
      let diff;
      if (apDay !== null) diff = apDay * 1440 + appointed - aAbs;   // 日付があるときは実際の差
      else {
        diff = appointed - arrive;
        if (diff > 720) diff -= 1440;
        else if (diff < -720) diff += 1440;
      }
      base = Math.max(0, diff);
    }
    const boundary = base + thr;

    // 待機。時計の上の区間ごとに、待機の始まり + 120 分の境の前と後に分ける（SPEC §25.6 の訂正、告示 別紙 V）。
    // 荷役のあとの待ちも、荷役の時間を含めた時計の境で分ける（運転者の荷役の間に境をまたぐ場合を含む）
    const hi = handler === '荷主側' ? Math.max(base, eRel) : Math.max(base, sRel);   // 荷主側の荷役は待機に含む
    const nMain = Math.max(0, Math.min(hi, boundary) - base);
    const oMain = Math.max(0, hi - boundary);
    const postOver = postMin > 0 ? Math.max(0, depRel - Math.max(eRel, boundary)) : 0;
    const waitRaw = nMain + oMain + postMin;   // 30 分の差し引きは 1 か所ごとに 1 回（SPEC §25）
    const w = fault ? waitRaw : 0;
    const wnMin = Math.max(0, (fault ? nMain + postMin - postOver : 0) - free);
    const woMin = fault ? oMain + postOver : 0;
    const wnUnits = ceilDiv(wnMin, unit), woUnits = ceilDiv(woMin, unit);
    const wnRate = tariff.waiting.normal[vclass], woRate = tariff.waiting.over_2h[vclass];
    const waitFee = wnUnits * wnRate + woUnits * woRate;

    let hnUnits = 0, hoUnits = 0, hnRate = 0, hoRate = 0, driverHandlingMin = 0;
    if (handler === '運転者') {
      driverHandlingMin = handlingMin;
      const before = Math.max(0, Math.min(eRel, boundary) - Math.min(sRel, boundary));
      const after = Math.max(0, eRel - Math.max(sRel, boundary));
      hnUnits = ceilDiv(before, unit); hoUnits = ceilDiv(after, unit);
      hnRate = tariff.handling[method].normal[vclass];
      hoRate = tariff.handling[method].over_2h[vclass];
    }
    const handlingFee = hnUnits * hnRate + hoUnits * hoRate;

    const rowNotes = handler === UNKNOWN_HANDLER && end !== null ? [UNKNOWN_HANDLER_NOTE] : [];
    if (['arrive', 'start', 'end', 'depart', 'appointed', 'reception', 'call'].some((k) => /(?:頃|ごろ|ころ)$/.test(nz(raw[k])))) rowNotes.push(APPROX_TIME_NOTE);
    if (receptionRaw && reception === null) rowNotes.push(`受付「${receptionRaw}」を読めなかったので、受付の時刻は書面に出していません`);
    // 受付が到着より前で、差が 12 時間より短い（日付なし）ときは、夜中をまたいだ受付（23:50 着・0:20 受付）ではないので、時刻の入れ違いを知らせる
    if (reception !== null && arrive !== null && norm(raw.arrive_src) !== 'reception' && !datePart(receptionRaw) && reception < arrive && arrive - reception < 720) rowNotes.push(`受付の時刻 ${fmtTime(reception)} が到着 ${fmtTime(arrive)} より前です。時刻を確かめてください`);
    // 早着（SPEC §41）。指定の到着時刻があって到着が指定より前: 指定の時刻から数えている。指定が無く 5:00〜7:59 の到着: 早着の疑い（別に数える）
    let early = '';
    if (appointed !== null && base > 0) early = 'designated';
    else if (appointed === null && arrive >= EARLY_FROM_MIN && arrive < EARLY_UNTIL_MIN) early = 'assumed';
    if (callRaw && call === null) rowNotes.push(`呼出「${callRaw}」を読めなかったので、呼出の時刻は使っていません`);
    if (waitRaw >= 1440) rowNotes.push(LONG_WAIT_NOTE);
    const res = {
        row: n, date: d, month: d.slice(0, 7), vehicle_no: g.vehicle_no, vehicle_class: vclass, partner, place: nz(g.place),
        arrive: fmtTime(arrive), start: fmtTime(start), end: end !== null ? fmtTime(end) : '', depart: depart !== null ? fmtTime(depart) : '',
        appointed: appointed !== null ? fmtTime(appointed) : '', wait_from: base > 0 ? '指定の到着時刻' : '到着', wait_base_min: base,
        side_work: g.side_work, side_min: sideMin,
        handler, method, shipper_fault: fault, crosses_midnight: crosses,
        report_wait_min: Math.max(0, sRel - base),   // SPEC §29.1: 定期報告の定義（到着か指定の時刻の遅い方 → 荷役開始。荷主等の都合に関わらず）
        wait_raw_min: sRel, wait_min: w, post_wait: postOn, post_wait_min: fault ? postMin : 0, handling_min: driverHandlingMin,
        wait_normal_units: wnUnits, wait_normal_rate: wnRate, wait_over_units: woUnits, wait_over_rate: woRate, wait_fee: waitFee,
        handling_normal_units: hnUnits, handling_normal_rate: hnRate, handling_over_units: hoUnits, handling_over_rate: hoRate, handling_fee: handlingFee,
        amount: waitFee + handlingFee,
        over_1h: w >= 60, over_2h_total: (w + driverHandlingMin) >= thr,
        has_side_work: !!g.side_work || sideMin > 0,
        ...(reception !== null ? { reception: fmtTime(reception) } : {}),
        ...(call !== null ? { call: fmtTime(call) } : {}),
        ...(norm(raw.arrive_src) === 'reception' ? { arrive_from_reception: true } : {}),
        ...(early ? { early_arrival: early } : {}),
        ...(used.length ? { defaults_used: used } : {}),
        ...(partnerProvisional ? { partner_provisional: true } : {}),
        ...(rowNotes.length ? { notes: rowNotes } : {}),
    };
    // 受付の時刻があるとき: 受付から数えた待機も出す（SPEC §43）。到着が受付より前でも、荷主の受付から数える額が書面の主な額
    if (reception !== null && norm(raw.arrive_src) !== 'reception') {
      // 受付が到着より小さい時刻（23:50 着・受付 0:20）は夜中をまたいだ受付として +24 時間で数える。受付に日付があれば日付で差を取る
      const rcPart = datePart(receptionRaw);
      let rcDay = null;
      if (rcPart) {
        let rd = parseDate(rcPart, yr);
        if (rd !== null && YEARLESS.test(nz(rcPart)) && ordOf(rd) < aDay - 180) rd = parseDate(rcPart, yr + 1);
        rcDay = rd !== null ? ordOf(rd) : null;
      }
      let rcRel, rcDate;
      if (rcDay !== null) {
        rcRel = rcDay * 1440 + reception - aAbs;
        rcDate = rcDay;
      } else {
        rcRel = reception - arrive;
        if (rcRel < 0) rcRel += 1440;
        rcDate = aDay + (arrive + rcRel >= 1440 ? 1 : 0);
      }
      if (rcRel >= 0 && rcRel <= REVERSE_MAX_MIN) {
        const part = g.arrive ? datePart(g.arrive) : '';
        const iso = new Date(rcDate * 86400000).toISOString().slice(0, 10);
        const v = { ...raw, arrive: (part || rcDay !== null ? iso + ' ' : '') + fmtTime(reception), reception: '' };
        delete v.arrive_src;
        const r2 = calcRow(n, v, year, tariff, defaults);
        if (r2.res !== null) { res.reception_wait_min = r2.res.wait_min; res.reception_amount = r2.res.amount; }
      }
    }
    return { res, errs: [] };
  }

  function sumRows(rows) {
    const s = (f) => rows.reduce((a, r) => a + f(r), 0);
    return {
      count: rows.length, wait_minutes: s((r) => r.wait_min), handling_minutes: s((r) => r.handling_min),
      wait_fee: s((r) => r.wait_fee), handling_fee: s((r) => r.handling_fee), amount: s((r) => r.amount),
      over_1h_count: s((r) => (r.over_1h ? 1 : 0)), over_2h_count: s((r) => (r.over_2h_total ? 1 : 0)),
      side_work_count: s((r) => (r.has_side_work ? 1 : 0)), side_work_minutes: s((r) => r.side_min),
    };
  }

  function aggregate(rows) {
    const by = new Map();
    for (const r of rows) { if (!by.has(r.partner)) by.set(r.partner, []); by.get(r.partner).push(r); }
    const out = [];
    for (const [name, rs] of by) {
      const months = [...new Set(rs.map((r) => r.month))].sort(cmp).map((m) => ({ month: m, ...sumRows(rs.filter((r) => r.month === m)) }));
      const dates = rs.map((r) => r.date).sort(cmp);
      const early = rs.filter((r) => r.early_arrival === 'assumed');
      const rec = rs.filter((r) => has(r, 'reception_amount'));   // 受付の時刻があって、受付から数えた額を出せた行
      const earlyNorec = early.filter((r) => !has(r, 'reception_amount'));
      const item = { partner: name, ...sumRows(rs), early_assumed_count: early.length, early_assumed_wait_minutes: early.reduce((a, r) => a + r.wait_min, 0),
        early_assumed_amount: early.reduce((a, r) => a + r.amount, 0), early_designated_count: rs.filter((r) => r.early_arrival === 'designated').length, over_1h_share: pct(rs.filter((r) => r.over_1h).length, rs.length), date_from: dates[0], date_to: dates[dates.length - 1], months };
      if (rec.length) {
        item.reception_count = rec.length;
        item.reception_wait_minutes = rec.reduce((a, r) => a + r.reception_wait_min, 0);
        item.reception_amount = rec.reduce((a, r) => a + r.reception_amount, 0);
        item.reception_arrive_wait_minutes = rec.reduce((a, r) => a + r.wait_min, 0);
        item.reception_arrive_amount = rec.reduce((a, r) => a + r.amount, 0);
        item.main_amount = item.amount - item.reception_arrive_amount + item.reception_amount;
        item.early_assumed_norec_count = earlyNorec.length;
        item.early_assumed_norec_wait_minutes = earlyNorec.reduce((a, r) => a + r.wait_min, 0);
        item.early_assumed_norec_amount = earlyNorec.reduce((a, r) => a + r.amount, 0);
      }
      out.push(item);
    }
    out.sort((a, b) => (b.amount - a.amount) || cmp(a.partner, b.partner));
    return out;
  }

  // defaults（任意）: { vehicle_class: '中型', method: '手積み' }。車種が空欄の行、運転者が荷役をして荷役の方法が空欄の行を、行を落とさずこの値で計算し、
  // 行に defaults_used を付ける。渡さないときは、空欄の行は誤りになる（waiting.py の calc と同じ）
  function calc(rows, year, tariff, defaults) {
    const dfl = {};
    if (defaults) {
      if (has(tariff.waiting.normal, defaults.vehicle_class)) dfl.vehicle_class = defaults.vehicle_class;
      if (has(tariff.handling, defaults.method)) dfl.method = defaults.method;
      if (defaults.provisional_partner) dfl.provisional_partner = true;
    }
    if (rows.length > MAX_ROWS) throw new Error(`行が多すぎます（${MAX_ROWS.toLocaleString('ja-JP')} 行まで）`);
    const done = [], errors = [];
    const yi = inferYears(rows, year);
    rows.forEach((raw, i) => {
      const n = i + 1;
      if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) { errors.push({ row: n, message: '行の形が正しくありません' }); return; }
      const { res, errs } = calcRow(n, raw, yi.years[i], tariff, dfl);
      if (res === null) errs.forEach((e) => errors.push({ row: n, message: e })); else done.push(res);
    });
    const out = { rows: done, by_partner: aggregate(done), hotspots: hotspots(done), by_place_month: byPlaceMonth(done), errors };
    if (yi.crossed) out.year_crossed = { from: year, to: year + 1, rows: yi.nextRows };   // 年のない日付が 10〜12 月と 1〜3 月にまたがるとき、1〜3 月を翌年と読んだ印
    return out;
  }

  // 年のない日付（10/5）が 10〜12 月と 1〜3 月の両方にあり、4〜9 月が無いときは、年末年始をまたぐ記録とみなし、1〜3 月を翌年にする（waiting.py の infer_years と同じ）
  function inferYears(rows, year) {
    const years = rows.map(() => year);
    if (year == null) return { years, crossed: false, nextRows: 0 };
    const ms = rows.map((raw) => {
      if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return null;
      const t = norm(raw.date);
      if (!t || parseDate(t, null) !== null) return null;
      const d = parseDate(t, year);
      return d === null ? null : Number(d.slice(5, 7));
    });
    const got = ms.filter((m) => m !== null);
    if (!(got.some((m) => m >= 10) && got.some((m) => m <= 3) && !got.some((m) => m >= 4 && m <= 9))) return { years, crossed: false, nextRows: 0 };
    let nextRows = 0;
    ms.forEach((m, i) => { if (m !== null && m <= 3) { years[i] = year + 1; nextRows += 1; } });
    return { years, crossed: true, nextRows };
  }


  /* ---------------------------------------------------------------- 貼り付けを表の後ろに足す（SPEC §42）。同じ行（日付・場所・到着・車番）は 1 つにする */
  const TIME_KEYS = new Set(['arrive', 'start', 'end', 'depart', 'appointed', 'reception', 'call']);
  const META_KEYS = new Set(['fault_default', 'arrive_src']);
  // 同じ行とみなすのは、日付・場所・契約の相手・到着・荷役開始・車番がすべて同じで、車番が空でないときだけ。
  // 日付・到着・車番のどれかが空の行は、同じ行かどうか言えないので足す（'' を返す）。契約の相手が違う、荷役開始が違う行も別の行（B1）
  function rowKey(r, year) {
    const dRaw = nz(r.date), aRaw = nz(r.arrive), plate = nz(r.vehicle_no);
    if (!dRaw || !aRaw || !plate) return '';
    const dd = parseDate(norm(r.date), year);
    const t = parseTime(norm(r.arrive));
    const sRaw = nz(r.start), s = sRaw ? parseTime(norm(r.start)) : null;
    return [dd || dRaw, nz(r.place), nz(r.partner), t === null ? aRaw : t, s === null ? sRaw : s, plate].join('|');
  }
  const sameValue = (k, a, b) => {
    if (TIME_KEYS.has(k)) { const x = parseTime(norm(a)), y = parseTime(norm(b)); if (x !== null && y !== null) return x === y; }
    return nz(a) === nz(b);
  };
  // 車番が空などで rowKey が空の行は自動では同じ行とみなさない（別のトラックが同じ時刻に着くことがある）。
  // ただし、既にある行と全部の欄が同じ行は、同じ貼り付けや同じ CSV をもう一度入れた可能性が高いので、数えて画面で聞く。
  // fresh は同じでなかった行、dup は既にある行と全欄が同じだった行の数（既にある行 1 つにつき 1 回までしか数えない）
  function splitIdentical(existing, incoming, year) {
    const used = new Set();
    // 同じ表を貼り直した行と、書き出した CSV を読み直した行は書き方が違う（10/5 と 2026-10-05、8:50 と 08:50、車種の空欄と書き出しの既定の中型）ので、日付と時刻は値にそろえて比べる。参考額（doc_amount）は計算で出る欄なので比べない
    const canon = (k, v) => {
      const t = String(v == null ? '' : v).trim();
      if (k === 'date') return parseDate(t, year) || t;
      if (TIME_KEYS.has(k)) { const m = parseTime(t); return m === null ? t : String(m); }
      if (k === 'vehicle_class') return t || '中型';
      return t;
    };
    const sig = (r) => Object.keys(r).filter((k) => !META_KEYS.has(k) && k !== 'doc_amount' && (canon(k, r[k]) !== '' || k === 'vehicle_class')).sort().map((k) => k + '=' + canon(k, r[k])).join('');
    const pool = new Map();
    existing.forEach((r, i) => { if (rowKey(r, year)) return; const g = sig(r); if (!pool.has(g)) pool.set(g, []); pool.get(g).push(i); });
    const fresh = []; let dup = 0;
    for (const r of incoming) {
      if (!rowKey(r, year)) {
        const arr = pool.get(sig(r));
        if (arr && arr.length) { arr.shift(); dup += 1; continue; }
      }
      fresh.push(r);
    }
    return { fresh, dup };
  }
  // existing に incoming を足す。同じ行は足さず、既にある行の空欄だけを埋める。値が違う欄は既にある値を残し、conflicts に数える
  function mergeRows(existing, incoming, year) {
    const out = existing.map((r) => ({ ...r }));
    const idx = new Map();
    out.forEach((r, i) => { const k = rowKey(r, year); if (k && !idx.has(k)) idx.set(k, i); });
    let added = 0, merged = 0, conflicts = 0;
    for (const r of incoming) {
      const k = rowKey(r, year);
      if (k && idx.has(k)) {
        const t = out[idx.get(k)];
        let diff = false;
        for (const [f, v] of Object.entries(r)) {
          if (META_KEYS.has(f) || v == null || String(v).trim() === '') continue;
          if (t[f] == null || String(t[f]).trim() === '') t[f] = v;
          else if (!sameValue(f, t[f], v)) diff = true;
        }
        merged += 1; if (diff) conflicts += 1;
      } else { out.push({ ...r }); if (k) idx.set(k, out.length - 1); added += 1; }
    }
    return { rows: out, added, merged, conflicts };
  }

  /* ---------------------------------------------------------------- 荷主として使う（SPEC §42）。受付記録の施設ごとの時間帯の集中・早い受付・呼出から作業開始まで */
  const mm = (t) => { const m = /^(\d{1,2}):(\d{2})$/.exec(t || ''); return m ? Number(m[1]) * 60 + Number(m[2]) : null; };
  const fwd = (a, b) => (((b - a) % 1440) + 1440) % 1440;   // a から b まで進んだ分（日付をまたぐ）
  const avgOf = (xs) => (xs.length ? halfUp(xs.reduce((a, x) => a + x, 0), xs.length) : null);
  function shipperSummary(rows) {
    const by = new Map();
    for (const r of rows) { const k = r.place || NO_PLACE; if (!by.has(k)) by.set(k, []); by.get(k).push(r); }
    const places = [];
    for (const [place, rs] of by) {
      const byHour = new Map();
      for (const r of rs) {
        const h = Math.floor(mm(r.arrive) / 60);
        if (!byHour.has(h)) byHour.set(h, []);
        byHour.get(h).push(r);
      }
      const maxDay = (xs) => { const c = new Map(); for (const r of xs) c.set(r.date, (c.get(r.date) || 0) + 1); return Math.max(...c.values()); };   // 1 日に同じ時間帯へ集まった件数の最多
      const hours = [...byHour.keys()].sort((a, b) => a - b).map((h) => ({ hour: h, count: byHour.get(h).length, max_day: maxDay(byHour.get(h)), avg_wait: avgOf(byHour.get(h).map((r) => r.report_wait_min)) }));
      const peak = hours.slice().sort((a, b) => (b.count - a.count) || (a.hour - b.hour))[0];
      const arr = rs.map((r) => mm(r.arrive));
      const early = rs.filter((r) => mm(r.arrive) >= EARLY_FROM_MIN && mm(r.arrive) < EARLY_UNTIL_MIN);
      const callGaps = rs.filter((r) => r.call).map((r) => fwd(mm(r.call), mm(r.start))).filter((x) => x <= 720);
      const recCall = rs.filter((r) => r.call).map((r) => fwd(mm(r.arrive), mm(r.call))).filter((x) => x <= 720);
      const ahead = rs.filter((r) => r.reception && !r.arrive_from_reception).map((r) => fwd(mm(r.arrive), mm(r.reception))).filter((x) => x <= 720);
      places.push({
        place, count: rs.length, days: new Set(rs.map((r) => r.date)).size, hours, peak_hour: peak.hour, peak_count: peak.count, peak_share: pct(peak.count, rs.length),
        earliest: fmtTime(Math.min(...arr)), early_count: early.length, early_share: pct(early.length, rs.length),
        avg_wait: avgOf(rs.map((r) => r.report_wait_min)), over_1h: rs.filter((r) => r.report_wait_min >= 60).length,
        call_count: callGaps.length, call_avg: avgOf(callGaps), call_max: callGaps.length ? Math.max(...callGaps) : null, call_over15: callGaps.filter((x) => x > 15).length,
        reccall_avg: avgOf(recCall), ahead_count: ahead.filter((x) => x >= 30).length, ahead_known: ahead.length,
      });
    }
    places.sort((a, b) => (b.count - a.count) || cmp(a.place, b.place));
    const hourSet = [...new Set(places.flatMap((p) => p.hours.map((h) => h.hour)))].sort((a, b) => a - b);
    const earlyAll = places.reduce((a, p) => a + p.early_count, 0);
    return { n: rows.length, places, hours: hourSet, early_count: earlyAll, early_share: pct(earlyAll, rows.length), call_count: places.reduce((a, p) => a + p.call_count, 0) };
  }
  const hourLabel = (h) => `${h} 時台`;
  // 予約の枠の提案。平均の待ちが最も長い時間帯から、平均の待ちが最も短い時間帯へ移す。枠は、その短い時間帯で実際にあった 1 日の最多の件数から決める。
  // 長い時間帯の 1 日の最多がその枠以下なら、枠を決めても移す車が出ないので、枠は提案しない。
  function slotBody(top) {
    const days = Math.max(1, top.days);
    const rc = top.reccall_avg !== null ? `受付から呼出までの平均は ${top.reccall_avg} 分です。` : '';
    const hs = top.hours.filter((h) => h.avg_wait !== null);
    if (hs.length < 2) return `${top.place}は待ちの記録がある時間帯が ${hs.length} つだけなので、時間帯ごとの待ちを比べられません。受付の時刻が違う記録を増やすと、予約の枠を決める材料になります。` + rc;
    const src = hs.slice().sort((a, b) => (b.avg_wait - a.avg_wait) || (b.count - a.count) || (a.hour - b.hour))[0];
    const others = hs.filter((h) => h.hour !== src.hour && h.avg_wait < src.avg_wait);
    if (!others.length) return `${top.place}は、どの時間帯も平均の待ちは ${src.avg_wait} 分で同じです。時間帯をずらしても待ちは減らないので、枠の提案はありません。` + rc;
    const best = Math.min(...others.map((h) => h.avg_wait));
    const dests = others.filter((h) => h.avg_wait === best).sort((a, b) => (a.count - b.count) || (a.hour - b.hour)).slice(0, 2);
    const cap = Math.max(1, ...dests.map((h) => h.max_day));
    const dLabel = dests.map((h) => hourLabel(h.hour)).join('か ');
    const perDay = Math.ceil((src.count / days) * 10) / 10;
    const head = `${top.place}は ${hourLabel(src.hour)}の平均の待ちが最も長く（${src.avg_wait} 分、${src.count} 件）、${dLabel}が最も短い（${best} 分）です。`;
    if (src.max_day <= cap) return head + `${hourLabel(src.hour)}は 1 日あたり平均 ${perDay} 件、最多の日でも ${src.max_day} 件で、短い時間帯の最多（${cap} 件）を超えません。予約の枠を決めても動かす車はないので、枠の提案はありません。` + rc;
    return head + `${hourLabel(src.hour)}の予約を 1 時間 ${cap} 件までにして（短い時間帯で実際にあった 1 日の最多に合わせた数です。この時間帯は 1 日あたり平均 ${perDay} 件、最多の日 ${src.max_day} 件）、超える分を ${dLabel}に移します。` + rc;
  }
  // 荷主が取れる行動（記録の数字から作る提案。実行するかは荷主が決める）
  function shipperActions(sum) {
    const out = [];
    if (!sum.places.length) return out;
    const top = sum.places[0];
    out.push({ id: 'open', title: '受付開始の時刻を決めて知らせる',
      body: `${top.place}: 記録で最も早い受付は ${top.earliest}、8 時前の受付は ${top.count} 件中 ${top.early_count} 件（${top.early_share}%）です。` +
        '受付を始める時刻を決めて運送会社に知らせ、それより前に着いた車が待つ場所と順番も決めておきます。8 時前の受付が多いときは、受付を始める時刻を早めることも選べます。' });
    out.push({ id: 'slot', title: '予約の枠を決める', body: slotBody(top) });
    if (sum.call_count) {
      const c = sum.places.filter((p) => p.call_count);
      const w = c.reduce((a, p) => a + p.call_avg * p.call_count, 0), n = c.reduce((a, p) => a + p.call_count, 0);
      const over = c.reduce((a, p) => a + p.call_over15, 0), mx = Math.max(...c.map((p) => p.call_max));
      out.push({ id: 'call', title: '呼出から作業開始までを短くする',
        body: `呼出から作業開始までは平均 ${halfUp(w, n)} 分、最長 ${mx} 分で、${n} 件中 ${over} 件が 15 分を超えています。この待ちは荷主が減らせます。` +
          '呼出は、荷役の場所（バース）が空いたのを確かめてから行い、呼出と作業開始の時刻を記録に残します。' });
    } else {
      out.push({ id: 'call', title: '呼出の時刻を記録する',
        body: '呼出の列が無いので、呼出から作業開始までの待ちは出せません。受付・呼出・作業開始の 3 つの時刻を記録すると、荷主が減らせる待ち（呼出から作業開始まで）が分かります。' });
    }
    return out;
  }
  // 早着の扱いの通知文（雛形）。（　）は荷主が書き入れる
  function noticeTemplate(places, openTime) {
    const where = places && places.length ? places.join('・') : '（施設名）';
    return ['【早着の扱いについてのお願い】', '運送会社の皆様へ', '',
      `${where}の受付は、${openTime || '（受付開始の時刻）'}からです。`,
      '受付の開始より前に到着された場合は、（待機する場所）でお待ちください。受付の順番は（到着順 / 予約の時刻順）です。',
      '予約の時刻が決まっている車両は、予約の時刻の（　）分前から受付します。',
      '受付・呼出・作業開始の時刻は、当方でも記録しています。荷待ちの記録を照らし合わせたい場合は、お問い合わせください。', '',
      'よろしくお願いいたします。', '',
      '（会社名・施設名）',
      '問い合わせ先: （担当者・電話番号）'].join('\n');
  }

  /* ---------------------------------------------------------------- 運送会社の書面の CSV と自社の受付記録を照らし合わせる（SPEC §42）。日付と車番（数字の下 4 桁）が同じ行を組にして、時刻の差を見る */
  const vehicleKey = (v) => { const t = nz(v).replace(/[\s\-ー]/g, ''); const d = t.replace(/\D/g, ''); return d.length >= 1 ? d.slice(-4) : t.toLowerCase(); };
  const sdiff = (a, b) => { const d = fwd(a, b); return d > 720 ? d - 1440 : d; };   // a から見た b の差（−720〜720 分）
  // 照合の鍵は 3 通り。車番が両方にあれば日付と車番（数字の下 4 桁）。車番が片方でも空なら、日付と場所と到着の時刻（±30 分）。場所も空なら日付と到着の時刻（±30 分）。
  // 候補が複数あって 1 組に決められない行は、組にせず candidates に並べる（推測で組にしない）
  const FALLBACK_MIN = 30;
  const VEHICLE_MIN = 60;   // 車番が同じ行を同じ回とする到着の差の上限（分）
  const BEST_GAP = 10;   // 候補が複数あるとき、いちばん近い行が 2 番目より何分以上近ければ同じ回とするか
  const placeKey = (v) => nz(v).replace(/\s+/g, '').toLowerCase();
  const placeSame = (a, b) => { const x = placeKey(a), y = placeKey(b); return !!x && !!y && (x === y || (Math.min(x.length, y.length) >= 2 && (x.includes(y) || y.includes(x)))); };
  function crossCheck(carrierRows, shipperRows, thr) {
    const limit = thr == null ? 15 : thr;
    const mkPair = (c, s, key) => {
      const ad = sdiff(mm(c.arrive), mm(s.arrive)), sd = sdiff(mm(c.start), mm(s.start));
      // 待機の長さ（到着から荷役開始まで）の差と、荷役終了の差。到着と開始がそれぞれ限度未満でも、反対向きにずれて待機が長くなる書面を見逃さない
      const wd = sdiff(fwd(mm(c.arrive), mm(c.start)), fwd(mm(s.arrive), mm(s.start)));
      const ed = mm(c.end) !== null && mm(s.end) !== null ? sdiff(mm(c.end), mm(s.end)) : 0;
      return { date: c.date, vehicle: c.vehicle_no || s.vehicle_no || '', key, carrier: c, shipper: s, arrive_diff: ad, start_diff: sd, wait_diff: wd, end_diff: ed,
        differs: Math.abs(ad) >= limit || Math.abs(sd) >= limit || Math.abs(wd) >= limit || Math.abs(ed) >= limit };
    };
    const pairs = [], ambiguous = [];
    const usedC = new Set(), usedS = new Set();
    // 1. 日付と車番が両方にある行
    const gc = new Map(), gs = new Map();
    const put = (g, r, i) => { const vk = vehicleKey(r.vehicle_no); if (!vk) return; const k = `${r.date}|${vk}`; if (!g.has(k)) g.set(k, []); g.get(k).push(i); };
    carrierRows.forEach((r, i) => put(gc, r, i)); shipperRows.forEach((r, j) => put(gs, r, j));
    for (const k of gc.keys()) {
      if (!gs.has(k)) continue;
      const cand = [];
      // 車番と日付が同じでも、場所が違う、または到着が VEHICLE_MIN 分より離れている行は、別の回として組にしない
      gc.get(k).forEach((i) => gs.get(k).forEach((j) => {
        const c = carrierRows[i], s = shipperRows[j];
        if (placeKey(c.place) && placeKey(s.place) && !placeSame(c.place, s.place)) return;
        const d = Math.abs(sdiff(mm(c.arrive), mm(s.arrive)));
        if (!(d <= VEHICLE_MIN)) return;
        cand.push([d, i, j]);
      }));
      cand.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
      for (const [, i, j] of cand) {
        if (usedC.has(i) || usedS.has(j)) continue;
        usedC.add(i); usedS.add(j); pairs.push(mkPair(carrierRows[i], shipperRows[j], 'vehicle'));
      }
    }
    // 2. 車番で組にならなかった行どうし。両方に違う車番があるときは組にしない
    const poolC = carrierRows.map((r, i) => i).filter((i) => !usedC.has(i)), poolS = shipperRows.map((r, j) => j).filter((j) => !usedS.has(j));
    const cand = new Map(), back = new Map();
    for (const i of poolC) {
      const c = carrierRows[i], cv = vehicleKey(c.vehicle_no);
      for (const j of poolS) {
        const s = shipperRows[j], sv = vehicleKey(s.vehicle_no);
        if (c.date !== s.date || (cv && sv)) continue;
        const ma = mm(c.arrive), sa = mm(s.arrive);
        if (ma == null || sa == null || Math.abs(sdiff(ma, sa)) > FALLBACK_MIN) continue;
        const hasPlace = !!placeKey(c.place) && !!placeKey(s.place);
        if (hasPlace && !placeSame(c.place, s.place)) continue;
        if (!cand.has(i)) cand.set(i, []);
        cand.get(i).push({ j, key: hasPlace ? 'place' : 'time', d: Math.abs(sdiff(ma, sa)) });
        if (!back.has(j)) back.set(j, []);
        back.get(j).push(i);
      }
    }
    // 候補が複数あっても、お互いにいちばん時刻が近く、2 番目の候補より BEST_GAP 分以上近い組は、同じ回とする（時刻まで一致する行を、近くの別の回に邪魔させない）。決まった行を外して、残りで繰り返す
    const dropC = new Set(), dropS = new Set();
    for (let again = true; again;) {
      again = false;
      const bestOf = (list) => { const a = list.filter((x) => !dropS.has(x.j)).sort((x, y) => x.d - y.d); return a.length && (a.length === 1 || a[1].d - a[0].d >= BEST_GAP) ? a[0] : null; };
      const sideBest = new Map();   // j → [{i, d}]
      for (const [i, list] of cand) { if (dropC.has(i)) continue; list.forEach((x) => { if (dropS.has(x.j)) return; if (!sideBest.has(x.j)) sideBest.set(x.j, []); sideBest.get(x.j).push({ i, d: x.d }); }); }
      for (const [i, list] of cand) {
        if (dropC.has(i)) continue;
        const b = bestOf(list); if (!b) continue;
        const back1 = sideBest.get(b.j).sort((x, y) => x.d - y.d);
        if (back1[0].i !== i || (back1.length > 1 && back1[1].d - back1[0].d < BEST_GAP)) continue;
        dropC.add(i); dropS.add(b.j); usedC.add(i); usedS.add(b.j); pairs.push(mkPair(carrierRows[i], shipperRows[b.j], b.key)); again = true; break;
      }
    }
    for (const i of dropC) cand.delete(i);
    for (const [i, list] of [...cand]) { const rest = list.filter((x) => !dropS.has(x.j)); if (rest.length) cand.set(i, rest); else cand.delete(i); }
    back.clear();
    for (const [i, list] of cand) list.forEach((x) => { if (!back.has(x.j)) back.set(x.j, []); back.get(x.j).push(i); });
    const inAmbiguous = new Set(), ambC = new Set();
    for (const [i, list] of cand) {
      if (list.length === 1 && back.get(list[0].j).length === 1) {
        usedC.add(i); usedS.add(list[0].j); pairs.push(mkPair(carrierRows[i], shipperRows[list[0].j], list[0].key));
      } else {
        ambC.add(i); list.forEach((x) => inAmbiguous.add(x.j));
        ambiguous.push({ carrier: carrierRows[i], candidates: list.map((x) => shipperRows[x.j]) });
      }
    }
    // 組にならなかった行に、別の書面の行から指された自社の行も、決められない行として扱う
    const onlyCarrier = poolC.filter((i) => !usedC.has(i) && !ambC.has(i)).map((i) => carrierRows[i]);
    const onlyShipper = poolS.filter((j) => !usedS.has(j) && !inAmbiguous.has(j)).map((j) => shipperRows[j]);
    pairs.sort((a, b) => cmp(a.date, b.date) || cmp(String(a.vehicle), String(b.vehicle)) || cmp(a.carrier.arrive, b.carrier.arrive));
    const byDate = (a, b) => cmp(a.date, b.date) || cmp(a.arrive, b.arrive);
    onlyCarrier.sort(byDate); onlyShipper.sort(byDate);
    const keyCount = (k) => pairs.filter((p) => p.key === k).length;
    return { limit, pairs, differ: pairs.filter((p) => p.differs), same: pairs.filter((p) => !p.differs).length, only_carrier: onlyCarrier, only_shipper: onlyShipper, ambiguous,
      key_counts: { vehicle: keyCount('vehicle'), place: keyCount('place'), time: keyCount('time') },
      unmatchable_carrier: [], unmatchable_shipper: [] };
  }

  return {
    rowKey, mergeRows, splitIdentical, shipperSummary, shipperActions, noticeTemplate, crossCheck, hourLabel, vehicleKey, REVERSE_MAX_MIN,
    HEADER_ALIASES, headerKey, COLUMNS, parseDriverText, parseMemoText, looseTableToTsv, MEMO_NO_PARTNER, PROVISIONAL_PARTNER, ASK_PARTNER, EARLY_FROM_MIN, EARLY_UNTIL_MIN, DRIVER_LABEL, nz, norm, fmtTime, parsePasteFull, parsePaste, calc, aggregate, UNKNOWN_HANDLER, MAX_ROWS, cmp, halfUp, pct, NO_PLACE, hotspots, byPlaceMonth, hotspotNotes, hotspotNoteCell, consultLines, monthLabel, truncWarn, gmenBasis, exampleRows, EXAMPLE,
    BASIS_FACTS, GMEN_QUOTE, PLACE_MONTH_LEAD, PM_DEF, TRUNC_LINE, TRUNC_WARN, MONTH_CLOSE, CONSULT_TITLE, CONSULT_CLOSE, PM_PARTNER_TITLE, PM_PARTNER_SCREEN_NOTE,
  };
})();
