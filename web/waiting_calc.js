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
  const PM_PARTNER_TITLE = '場所ごと・月ごとの平均の荷待ち（この相手の行だけ。ページ全体の表とは数字が違います）';
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
      '荷主': 'partner', '元請': 'partner', '契約先': 'partner', '到着時刻': 'arrive', '着時刻': 'arrive',
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
      '荷主都合': 'shipper_fault', '荷主都合か': 'shipper_fault', '荷主都合の有無': 'shipper_fault', '荷主起因': 'shipper_fault',
      // 読まない列（見出しとしては知っている。黙って読み飛ばす）
      '乗務員氏名': '_ignore', '運転者氏名': '_ignore', '乗務員名': '_ignore', '運転者名': '_ignore', '氏名': '_ignore',
      '荷主の確認の有無': '_ignore', '荷主の確認': '_ignore', '確認の有無': '_ignore', '備考': '_ignore',
      '附帯業務開始日時': '_ignore', '附帯業務終了日時': '_ignore', '附帯業務の開始日時': '_ignore', '附帯業務の終了日時': '_ignore',
      '荷役のあとの待ちを荷主等の都合として数える': 'post_wait', '荷役のあとの待ちを数える': 'post_wait', '荷役のあとの待ち': 'post_wait', '荷役後の待ち': 'post_wait',
  };
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
  const LONG_WAIT_NOTE = '待ちが 1 日（24 時間）を超えています。日付と時刻を確かめてください';
  const MAX_ROWS = 5000;
  // 日時の欄（「2026/10/05 9:00」「10/5 9:00」「R8.10.5 9時00分」）の、時刻の前の日付の部分
  const DT_PREFIX = '(?:[0-9令和平成昭和元RHSrhs/\\-年月日.]+(?: ?\\([月火水木金土日祝]+\\))? )?';
  const DT_SPLIT = new RegExp('^([0-9令和平成昭和元RHSrhs/\\-年月日.]+(?: ?\\([月火水木金土日祝]+\\))?) ((?:[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)|(?:[0-9]{1,2}時(?:[0-9]{1,2}分?)?))$');
  const POS_NOTE = '見出しの行が無いので、列の順（日付、車番、車種、契約の相手、場所、到着、荷役開始、荷役終了、出発、荷役をした人、荷役の方法、荷主等の都合）で読みました';
  const UNKNOWN_HANDLER = '不明';
  const UNKNOWN_HANDLER_NOTE = '荷役をした人が分からないので、荷役の時間は待機にも積込料にも入れていません';

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
    const t = nz(text);
    let h, mi, sec;
    let m = new RegExp(`^${DT_PREFIX}([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}))?$`).exec(t);
    if (m) { h = Number(m[1]); mi = Number(m[2]); sec = Number(m[3] || 0); }
    else if ((m = new RegExp(`^${DT_PREFIX}([0-9]{1,2})時(?:([0-9]{1,2})分?)?$`).exec(t))) { h = Number(m[1]); mi = Number(m[2] || 0); sec = 0; }
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
  function parsePasteFull(text) {
    const lines = String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n').filter((ln) => ln.trim());
    if (!lines.length) return { rows: [], defaulted: [], notes: [] };
    const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
    const notes = [];
    let cols = COLUMNS.slice();
    let body = lines, header = false;
    const rawFirst = lines[0].split('\t');
    const first = rawFirst.map(headerKey);
    const known = first.filter((c) => has(HEADER_ALIASES, c)).length;
    if (first.length && (first[0] === '日付' || first[0] === 'date' || known >= 3)) {
      header = true;
      const mapped = first.map((c) => (has(HEADER_ALIASES, c) ? HEADER_ALIASES[c] : null));
      cols = mapped.map((c) => (c && c !== '_ignore' ? c : ''));   // 読まない列・知らない列は '' にして、行に入れない
      const seen = new Set();
      rawFirst.forEach((raw, i) => {
        const name = nz(raw);
        if (mapped[i] === null && name && !seen.has(name)) { seen.add(name); notes.push(`見出し『${name}』は読みませんでした`); }
      });
      body = lines.slice(1);
    } else notes.push(POS_NOTE);
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
    return { rows, defaulted, notes };
  }
  const parsePaste = (text) => parsePasteFull(text).rows;

  const ceilDiv = (a, b) => (a > 0 ? Math.ceil(a / b) : 0);

  function calcRow(n, raw, year, tariff) {
    const errs = [];
    const g = {};
    for (const k of COLUMNS) g[k] = norm(raw[k]);

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
    if (!g.vehicle_class) errs.push('車種がありません');
    else {
      vclass = normalizeClass(g.vehicle_class);
      if (vclass === null) errs.push(`車種「${g.vehicle_class}」を読めません（小型（2t）/ 中型（4t）/ 大型（10t）/ トレーラー）`);
    }
    const partner = nz(g.partner);   // 全角・半角の違いで相手が分かれないようにそろえる
    if (!partner) errs.push('契約の相手がありません');

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
    let sideMin = 0;
    if (g.side_min) {
      if (/^[0-9]{1,4}$/.test(nz(g.side_min))) sideMin = Number(nz(g.side_min));
      else errs.push(`附帯業務の分「${g.side_min}」を読めません（分を整数で入れてください）`);
    }

    let handler = nz(g.handler);
    if (handler === '荷主' || handler === '荷主側') handler = '荷主側';
    else if (handler === '運転者' || handler === 'ドライバー' || handler === '運転手') handler = '運転者';
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
      if (!method) errs.push('運転者が荷役をしたときは、荷役の方法（手積み / フォークリフト等）が必要です');
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
      if (sRel < 0) sRel += 1440;
    }
    let eRel = null;
    if (end !== null) {
      const eDay = fieldDay('end', aDay);
      if (eDay !== null) {
        eRel = eDay * 1440 + end - aAbs;
        if (eRel < sRel) errs.push('荷役終了が荷役開始より前です（日付と時刻を確かめてください）');
      } else {
        eRel = end - arrive;
        while (eRel < sRel) eRel += 1440;
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
        while (depRel < floor) depRel += 1440;
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
    if (waitRaw >= 1440) rowNotes.push(LONG_WAIT_NOTE);
    return {
      res: {
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
        ...(rowNotes.length ? { notes: rowNotes } : {}),
      },
      errs: [],
    };
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
      out.push({ partner: name, ...sumRows(rs), over_1h_share: pct(rs.filter((r) => r.over_1h).length, rs.length), date_from: dates[0], date_to: dates[dates.length - 1], months });
    }
    out.sort((a, b) => (b.amount - a.amount) || cmp(a.partner, b.partner));
    return out;
  }

  function calc(rows, year, tariff) {
    if (rows.length > MAX_ROWS) throw new Error(`行が多すぎます（${MAX_ROWS.toLocaleString('ja-JP')} 行まで）`);
    const done = [], errors = [];
    rows.forEach((raw, i) => {
      const n = i + 1;
      if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) { errors.push({ row: n, message: '行の形が正しくありません' }); return; }
      const { res, errs } = calcRow(n, raw, year, tariff);
      if (res === null) errs.forEach((e) => errors.push({ row: n, message: e })); else done.push(res);
    });
    return { rows: done, by_partner: aggregate(done), hotspots: hotspots(done), by_place_month: byPlaceMonth(done), errors };
  }


  return {
    HEADER_ALIASES, headerKey, COLUMNS, nz, norm, fmtTime, parsePasteFull, parsePaste, calc, aggregate, UNKNOWN_HANDLER, MAX_ROWS, cmp, halfUp, pct, NO_PLACE, hotspots, byPlaceMonth, hotspotNotes, consultLines, monthLabel, truncWarn, gmenBasis, exampleRows, EXAMPLE,
    BASIS_FACTS, GMEN_QUOTE, PLACE_MONTH_LEAD, PM_DEF, TRUNC_LINE, TRUNC_WARN, MONTH_CLOSE, CONSULT_TITLE, CONSULT_CLOSE, PM_PARTNER_TITLE,
  };
})();
