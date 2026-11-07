'use strict';
/* 自社の運行の計算（SPEC §35.1）。
   src/shipcompare/own.py の取り込みから気づきまでと、tools/build_own_sample.py の組み立てを JS に移したもの。
   通信もファイルの読み書きもしません。ブラウザの中だけで、配車表・デジタコの CSV の文字から web/data/own_sample.json と同じ形の結果を作ります。
   列の対応づけは「列名の辞書」と「値の形」だけで決めます（Claude には送りません）。
   Python の関数と同じ結果になることは tests/test_v35_own_import.py が確かめます（四捨五入も Python の round に合わせています）。 */

const OwnCalc = (() => {
  const WEEKDAYS = '月火水木金土日';

  // 気づきの規則のしきい値（own.py と同じ。筋書き §11.2、AI の提案）
  const R1_MIN_TRIPS = 4, R1_RATIO = [3, 4];
  const R3_MIN_STOP_MIN = 30, R3_MIN_COUNT = 5;
  const R4_BASE_H = 13.0, R4_LONG_H = 14.0, R4_MAX_H = 15.0, R4_WEEK_GUIDE = 2;
  const R5_GVW_KG = 8000, R5_LOAD_KG = 5000;
  const R6_UNKNOWN_RATIO = 0.15;
  const LINK_TIME_MIN = 60, LINK_DIST_RATIO = 0.15, SHIFT_GAP_MIN = 8 * 60;
  const CONF_GREEN = 0.9, CONF_YELLOW = 0.6;
  // 道路の距離の既定の表（市区町村コードの組 → km）。2 つの画面が同じ地点の組には同じ距離を使う（ファイル名や順序では変えない）。
  // 鹿屋市と江東区の間は OSRM の値（tools/build_own_sample.py の OSRM_KM と同じ）。それ以外は直線距離 × ROAD_FACTOR
  const DEFAULT_KM = { '13108|46203': 1381.7 };
  const ROAD_FACTOR = 1.25; // 直線距離から道のりを見積もる係数（地図の道路のデータが無いときの仮定）

  /* ------------------------------------------------------------ Python と同じ数の扱い */

  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  const pad = (n, w = 2) => String(n).padStart(w, '0');
  const nfkc = (s) => String(s == null ? '' : s).normalize('NFKC');

  // Python の round(x, nd)。0.5 ちょうどのときだけ偶数へ寄せる（JS の toFixed は大きい側へ寄せる）
  function pyRound(x, nd = 0) {
    if (x == null || !isFinite(x)) return x;
    const y = x * Math.pow(10, nd), fr = Math.abs(y - Math.trunc(y));
    const exactTie = fr === 0.5 && Number.isInteger(x * Math.pow(2, nd + 1));
    if (exactTie) { const t = Math.trunc(y), even = t % 2 === 0 ? t : t + Math.sign(y); return even / Math.pow(10, nd); }
    return Number(x.toFixed(nd));
  }
  const fix = (x, nd) => pyRound(x, nd).toFixed(nd); // Python の f"{x:.{nd}f}"
  const pyFloat = (v) => (Number.isInteger(v) ? v.toFixed(1) : String(v)); // Python の str(float)（668.0 のように小数点を残す）
  const commas = (n) => Math.round(n).toLocaleString('en-US'); // Python の f"{n:,}"（整数）

  /* ------------------------------------------------------------ 日時（分の整数で持つ。時差の影響を受けない） */

  // 「2026-10-01 05:00」「2026/10/6 20:10」「２０２６/１０/１ ５:００」（全角は NFKC で直す）。秒は捨てる。時差つきと 20261001T0500 のような区切りの無い形は読まない（own.py の parse_dt と同じ）
  const DT = /^([0-9]{4})[-/]([0-9]{1,2})[-/]([0-9]{1,2})(?:[ T]+([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}))?)?$/;
  function parseDT(s) {
    const m = DT.exec(nfkc(s).trim());
    if (!m) return null;
    const [y, mo, d, h, mi] = [+m[1], +m[2], +m[3], +(m[4] || 0), +(m[5] || 0)];
    if (y < 1900 || mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59) return null;
    const t = Date.UTC(y, mo - 1, d, h, mi);
    const chk = new Date(t);
    if (chk.getUTCMonth() !== mo - 1) return null;
    return t / 60000;
  }
  const isoOf = (min) => new Date(min * 60000).toISOString().slice(0, 16);
  const dateMs = (iso) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
  const weekdayOf = (iso) => (new Date(dateMs(iso)).getUTCDay() + 6) % 7; // 月曜 = 0
  const daysBetween = (a, b) => Math.round((dateMs(b) - dateMs(a)) / 86400000);
  const addDays = (iso, n) => new Date(dateMs(iso) + n * 86400000).toISOString().slice(0, 10);
  function isoWeek(iso) { // Python の date.isocalendar() の (年, 週)
    const d = new Date(dateMs(iso)), day = (d.getUTCDay() + 6) % 7;
    d.setUTCDate(d.getUTCDate() - day + 3);
    const y = d.getUTCFullYear(), jan4 = Date.UTC(y, 0, 4);
    return [y, 1 + Math.round(((d.getTime() - jan4) / 86400000 - 3 + ((new Date(jan4).getUTCDay() + 6) % 7)) / 7)];
  }
  const md = (iso) => `${+iso.slice(5, 7)}/${+iso.slice(8, 10)}`;

  /* ------------------------------------------------------------ CSV */

  // 文字コード: UTF-8 として読めなければ Shift_JIS（Windows の Excel が出す CSV）
  function decode(buf) {
    const u8 = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
    try { return { text: new TextDecoder('utf-8', { fatal: true }).decode(u8), encoding: 'UTF-8' }; } catch (e) { /* 次へ */ }
    return { text: new TextDecoder('shift_jis').decode(u8), encoding: 'Shift_JIS' };
  }

  function parseCsv(text) {
    let t = String(text || '');
    if (t.charCodeAt(0) === 0xFEFF) t = t.slice(1);
    const first = t.split(/\r\n|\n|\r/, 1)[0] || '';
    const cnt = (c) => first.split(c).length - 1;
    const delim = cnt('\t') > cnt(',') && cnt('\t') >= cnt(';') ? '\t' : cnt(';') > cnt(',') ? ';' : ',';
    const rows = [];
    rows.warnings = [];
    const firstCnt = first.split(delim).length - 1;
    const needCnt = Math.max(2, Math.ceil(firstCnt * 0.6));
    // 引用符の中に改行があるとき、その次の行が新しい行（区切りが十分ある）に見えるなら、引用符は閉じ忘れとみなす。閉じる引用符がファイルの終わりまで無いときも同じ
    const unterminated = (i) => {
      let k = i + 1;
      for (; k < t.length; k++) { if (t[k] === '"') { if (t[k + 1] === '"') k++; else break; } }
      if (k >= t.length) return true;
      const seg = t.slice(i + 1, k), nl = seg.search(/\r\n|\n|\r/);
      if (nl < 0) return false;
      const rest = seg.slice(nl).replace(/^(\r\n|\n|\r)/, ''), line2 = rest.split(/\r\n|\n|\r/, 1)[0];
      return line2.split(delim).length - 1 >= needCnt;
    };
    let row = [], cell = '', q = false;
    for (let i = 0; i < t.length; i++) {
      const c = t[i];
      if (q) {
        if (c === '"') { if (t[i + 1] === '"') { cell += '"'; i++; } else q = false; } else cell += c;
      } else if (c === '"' && cell === '') {
        if (unterminated(i)) { cell += '"'; rows.warnings.push({ line: (t.slice(0, i).match(/\n/g) || []).length + 1, reason: '閉じていない引用符（"）があったので、記号として読みました' }); } else q = true;
      }
      else if (c === delim) { row.push(cell); cell = ''; }
      else if (c === '\n' || c === '\r') {
        if (c === '\r' && t[i + 1] === '\n') i++;
        row.push(cell); cell = ''; rows.push(row); row = [];
      } else cell += c;
    }
    if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
    return rows;
  }

  /* ------------------------------------------------------------ 車番 */

  const PLATE4 = /(?<!\d)(\d{4})(?!\d)/g;
  const PLATE22 = /(?<!\d)(\d{2})\s*[-‐‑–−ー・]\s*(\d{2})(?!\d)/;
  function normalizePlate(s) {
    if (s == null) return null;
    const t = nfkc(s).trim();
    if (!t) return null;
    const found = [...t.matchAll(PLATE4)];
    if (found.length) return found[found.length - 1][1];
    const m = PLATE22.exec(t);
    return m ? m[1] + m[2] : null;
  }
  // 車番の文字から [末尾 4 桁, 完全な書き方]。「品川 100 あ 1234」は ['1234', '品川100あ1234']、「4t-1234」「1234」は ['1234', null]（own.py の plate_identity と同じ）。
  // 完全な書き方が違う 2 台（品川100あ1234 と 練馬100い1234）を、末尾 4 桁だけで同じ車にしないために使う
  const PLATE_FULL = /([ぁ-んァ-ヶ一-鿿々]{1,4})\s*([0-9]{2,3})\s*([ぁ-んァ-ヶ])\s*[・\-‐]?\s*([0-9]{4}|[0-9]{2}\s*[-‐‑–−ー・]\s*[0-9]{2})/;
  // 「1号車」「１１号車」「3号」「No.2」「7」→「1号車」「11号車」…。4 桁の登録番号が無い配車表で、車の名前として使う（own.py の gou_name と同じ）
  function gouName(s) {
    const m = /^(?:NO\.?\s*)?(\d{1,3})\s*(?:号車|号|番車|番|号機)?$/i.exec(nfkc(s == null ? '' : String(s)).trim());
    return m && +m[1] > 0 ? `${+m[1]}号車` : null;
  }
  function plateIdentity(s) {
    const num = normalizePlate(s);
    if (num == null) return [null, null];
    const m = PLATE_FULL.exec(nfkc(s));
    if (m && m[4].replace(/\D/g, '') === num) return [num, `${m[1]}${m[2]}${m[3]}${num}`];
    return [num, null];
  }

  /* ------------------------------------------------------------ 配車表（横の形）→ 縦の形 */

  const blankRow = (row) => !row.some((c) => (c || '').trim());
  // 日付の見出し。「10/1(木)」を基本に、「10月1日」「2026/10/1」も読む。戻り値は { y（年か null）, m, d }
  function dateHeadParts(h) {
    const t = nfkc(h).trim();
    const me = /^(令和|平成|R|H)\s*(\d{1,2})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?/i.exec(t); // 令和8年10月1日・R8.10.1（令和 = 2018 + n、平成 = 1988 + n。own.py の _DATE_ERA と同じ）
    if (me) return { y: (/^(令和|r)$/i.test(me[1]) ? 2018 : 1988) + +me[2], m: +me[3], d: +me[4] };
    let m = /^(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})/.exec(t);
    if (m) return { y: +m[1], m: +m[2], d: +m[3] };
    m = /^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日/.exec(t);
    if (m) return { y: +m[1], m: +m[2], d: +m[3] };
    m = /^(\d{1,2})\s*\/\s*(\d{1,2})/.exec(t) || /^(\d{1,2})\s*月\s*(\d{1,2})\s*日/.exec(t);
    return m ? { y: null, m: +m[1], d: +m[2] } : null;
  }
  // 見出しの列ごとに { y, m, d } を決める。年の無い見出しは startYear から始め、月が前の列より小さくなったら翌年にする（12/11〜1/10）。
  // 年つきの見出し（2026/10/1）が来たら、その年に合わせる（own.py の assign_head_years と同じ）
  function assignHeadYears(heads, startYear) {
    const out = {};
    let cur = startYear, prev = null;
    heads.forEach((h, i) => {
      const p = dateHeadParts(h || '');
      if (!p) return;
      if (p.y != null) cur = p.y;
      else if (prev != null && p.m >= 1 && p.m <= 12 && p.m < prev) cur += 1;
      if (p.m >= 1 && p.m <= 12) prev = p.m;
      out[i] = { y: cur, m: p.m, d: p.d };
    });
    return out;
  }
  // 曜日の見出し（「10/1(木)」の木）と合う年を探す。時計（今日の日付）は使わない（own.py の infer_year と同じ）。
  // 合う年が複数あるときは、hints（[{ year, source }]。題名・ファイル名・デジタコ・保存した前の月の順）のうち候補に入る最初の年を使い（year_assumed は偽）、
  // 手掛かりが無ければ DEMO_YEAR にいちばん近い年を使って year_assumed を真にする（同じ近さなら前の年。「2026 年以下で最新」にはしない）
  const YEAR_MIN = 2020, YEAR_MAX = 2036, NO_HINT_YEARS = [2024, 2025, 2026, 2027, 2028, 2029, 2030], DEFAULT_YEAR = 2026, DEMO_YEAR = 2026;
  const weekdayOfHead = (h) => { const m = /\(\s*([月火水木金土日])\s*\)/.exec(nfkc(h || '')); return m ? WEEKDAYS.indexOf(m[1]) : null; };
  // ファイル名や題名に書かれた年（2026、202610、令和8年）を出てくる順に返す（own.py の years_in_text と同じ）
  function yearsInText(text) {
    const t = nfkc(text), found = [];
    for (const m of t.matchAll(/令和\s*(\d{1,2})\s*年/g)) found.push([m.index, 2018 + +m[1]]);
    for (const rx of [/(?<!\d)(20[2-3]\d)(?!\d)/g, /(?<!\d)(20[2-3]\d)(?:0[1-9]|1[0-2])(?!\d)/g]) for (const m of t.matchAll(rx)) found.push([m.index, +m[1]]);
    const out = [];
    for (const [, y] of found.sort((a, b) => a[0] - b[0])) if (!out.includes(y)) out.push(y);
    return out;
  }
  const nearestYear = (cands, target = DEMO_YEAR) => cands.slice().sort((a, b) => Math.abs(a - target) - Math.abs(b - target) || a - b)[0];
  function inferYear(head, hints) {
    const heads = (head || []).map((h) => (h || '').trim());
    const parsed = heads.map((h) => dateHeadParts(h));
    const noHint = (source) => ({ year: DEFAULT_YEAR, candidates: NO_HINT_YEARS.slice(), ambiguous: true, year_assumed: true, source, hint: null });
    if (!parsed.some((p) => p)) return noHint('none');
    const implicit = heads.map((_, i) => i).filter((i) => parsed[i] && parsed[i].y == null);
    if (!implicit.length) { const f = parsed.find((p) => p); return { year: f.y, candidates: [f.y], ambiguous: false, year_assumed: false, source: 'explicit', hint: null }; }
    const wdCols = implicit.map((i) => [i, weekdayOfHead(heads[i])]).filter(([, w]) => w != null);
    if (!wdCols.length) return noHint('none');
    const cands = [];
    for (let y = YEAR_MIN; y <= YEAR_MAX; y++) {
      const got = assignHeadYears(heads, y);
      if (wdCols.every(([i, wd]) => { const g = got[i], iso = isoDate(g.y, g.m, g.d); return iso != null && weekdayOf(iso) === wd; })) cands.push(y);
    }
    if (!cands.length) return noHint('mismatch');
    if (cands.length === 1) return { year: cands[0], candidates: cands, ambiguous: false, year_assumed: false, source: 'weekday', hint: null };
    for (const h of hints || []) if (cands.includes(h.year)) return { year: h.year, candidates: cands, ambiguous: true, year_assumed: false, source: 'weekday', hint: h.source || null };
    return { year: nearestYear(cands), candidates: cands, ambiguous: true, year_assumed: true, source: 'weekday', hint: null };
  }
  const isoDate = (y, m, d) => {
    const t = new Date(Date.UTC(y, m - 1, d));
    return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d ? `${pad(y, 4)}-${pad(m)}-${pad(d)}` : null;
  };
  /* ------------------------------------------------------------ 配車表の見出し（別名）と、見出しの行・形の判定（SPEC §41。own.py と同じ表・同じ手順） */

  const HEADER_SCAN_ROWS = 10; // 見出しの行を探す先頭からの行数
  const headKey = (h) => nfkc(h).toUpperCase().replace(/[\s.・:()[\]/_-]/g, '');
  const H_PLATE_STRONG = new Set(['車番', '車両番号', '車両NO', '車番号', 'ナンバー', 'ナンバープレート', '登録番号', '車両ナンバー', '自動車番号', '車NO']);
  const H_PLATE_MID = new Set(['車両', '車輌', '車両名']);
  const H_PLATE_WEAK = new Set(['号車', 'NO']); // 他に車番の列が無いときだけ車番として読む（行の通し番号のことがある）
  const H_TYPE = new Set(['車種', '車格', 'トン数', '車型', '積載量', '積載トン数', '車両区分', '車種区分', 'トン']);
  const H_DATE = new Set(['日付', '日', '運行日', '配車日', '出発日', '出荷日', '年月日', '積日', '配送日', '納品日', '運行年月日', '月日']);
  const H_FROM = new Set(['発地', '積地', '出発地', '積み地', '積込地', '積込場所', '出発', '発', '出発場所', '積地名', '積込', '積み込み地', '積み込み場所', '発地名', '集荷先', '積先', '積み先']);
  const H_TO = new Set(['着地', '降ろし地', '卸地', '荷降地', '届け先', '届先', '納品先', '到着地', '着', '降ろし', '卸し地', '卸先', '配達先', '荷卸地', '卸', '降し地', '降地', '着地名', '配送先', '納入先', '卸し先', '降ろし先', '卸場所', '降ろし場所']);
  const H_TIME = new Set(['時刻', '着時刻', '到着', '到着時刻', '着時間', '時間', '着予定', '到着予定', '納品時間', '指定時刻', '着予定時刻', '到着時間', '納品時刻']);
  const H_MARK = new Set(['実空', '実空区分', '区分', '実車空車', '状態', '種別', '空車', '空実']);
  const H_KM = new Set(['走行KM', '走行キロ', '距離KM', '距離', 'キロ数', '実走行KM', '走行距離KM', '運行距離KM', '運行KM']); // 1 行 = 1 便の表で、その便の走った距離。「走行距離」だけの見出しは月の合計のことがあるので、これには入れない
  const H_DEP = new Set(['発時刻', '出発時刻', '出発時間', '発時間', '発車時刻', '出庫時刻', '出発予定', '積込時刻']);
  const H_IGNORE_IN = ['乗務員', '運転者', '運転手', 'ドライバー', '氏名', '合計', '備考', '稼働', '日数', '摘要', 'メモ', '実車率', '走行距離', '荷主', '得意先', '取引先', '顧客', '依頼主'];
  const H_MARK_NOTE_IN = ['備考', '摘要', 'メモ', '荷主', '得意先', '取引先', '顧客', '依頼主']; // この見出しの列は、読まないが、セルに空車の印（空車回送・帰荷なし）があれば空車にする
  const H_IGNORE_EQ = new Set(['計', '小計', '総計', '担当', '台数', 'TOTAL']);
  const TOTAL_TOKENS = new Set(['合計', '計', '小計', '総計', 'TOTAL', '合計台数', '台数']); // 車番の欄がこれなら合計の行（読まない）

  // 配車表の見出し → 役割。plate / plate2 / plate3（車番。3 段の強さ）, type, date, from, to, weight, time, mark, skip（読まない）。分からなければ null。
  // 利用者が決めた役割（roles。キーは colKey）を別名より先に使う
  function haishaRole(h, roles) {
    const s = h == null ? '' : String(h);
    if (!s.trim()) return null;
    const k = colKey(s);
    if (roles && Object.prototype.hasOwnProperty.call(roles, k)) return roles[k] || 'skip';
    const hk = headKey(s);
    if (H_PLATE_STRONG.has(hk) || ['車番', '車両番号', 'ナンバー'].some((x) => hk.includes(x))) return 'plate';
    if (H_PLATE_MID.has(hk)) return 'plate2';
    if (H_PLATE_WEAK.has(hk)) return 'plate3';
    if (H_TYPE.has(hk) || hk.includes('車種')) return 'type';
    if (H_DATE.has(hk)) return 'date';
    if (H_FROM.has(hk)) return 'from';
    if (H_TO.has(hk)) return 'to';
    if (H_TIME.has(hk)) return 'time';
    if (H_KM.has(hk)) return 'distance_km';
    if (H_DEP.has(hk)) return 'dep_time';
    if (H_MARK.has(hk)) return 'mark';
    if (hk.includes('重量') || ['重さ', '荷重', '荷量'].includes(hk)) return 'weight';
    if (H_IGNORE_IN.some((x) => hk.includes(x)) || H_IGNORE_EQ.has(hk)) return 'skip';
    return null;
  }

  const WD_CELL = /^[月火水木金土日](?:曜日?)?$/;
  const DAYNUM_RE = /^(\d{1,2})日?(?:\s*\(\s*([月火水木金土日])\s*\))?$/; // 「1」「1日」「1日(木)」「1日（木）」（全角の括弧は NFKC で直る。own.py の _DAYNUM と同じ）
  const dayNum = (c) => { const m = DAYNUM_RE.exec(nfkc(c).trim()); return m && +m[1] >= 1 && +m[1] <= 31 ? +m[1] : null; };
  const dayWd = (c) => { const m = DAYNUM_RE.exec(nfkc(c).trim()); return m && m[2] ? WEEKDAYS.indexOf(m[2]) : null; }; // 見出し自体に付いた曜日
  // 縦の一覧の日付のセル → { y, m, d } か null。「10/1」「R8.10.1」「2026/10/01」はそのまま、「1日」「1」は題名の年月（title = [年か null, 月]）と合わせて読む（own.py の vdate と同じ）
  function vdate(cell, title) {
    const t = nfkc(cell || '').trim();
    const p = dateHeadParts(t);
    if (p != null) return p;
    const d = /^\d{1,2}日?$/.test(t) ? dayNum(t) : null;
    return d != null && title ? { y: title[0], m: title[1], d } : null;
  }
  // 見出しの行より上の題名の行から [年か null, 月]。「2026年10月 配車表」「10月分」
  function titleMonth(rows, upto) {
    for (const r of rows.slice(0, upto)) {
      const t = nfkc(r.map((c) => c || '').join(' '));
      const me = /(令和|平成|R|H)\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月/i.exec(t);
      if (me && +me[3] >= 1 && +me[3] <= 12) return [(/^(令和|r)$/i.test(me[1]) ? 2018 : 1988) + +me[2], +me[3]];
      let m = /(\d{4})\s*年\s*(\d{1,2})\s*月/.exec(t);
      if (m && +m[2] >= 1 && +m[2] <= 12) return [+m[1], +m[2]];
      m = /(\d{1,2})\s*月/.exec(t);
      if (m && +m[1] >= 1 && +m[1] <= 12) return [null, +m[1]];
    }
    return null;
  }

  function layoutFromRow(rows, i, roles, month) {
    const cells = rows[i].map((c) => (c || '').trim());
    const rl = cells.map((c) => haishaRole(c, roles));
    let plateCol = null;
    for (const cls of ['plate', 'plate2', 'plate3']) { const j = rl.indexOf(cls); if (j >= 0) { plateCol = j; break; } }
    rl.forEach((r, j) => { if ((r === 'plate' || r === 'plate2' || r === 'plate3') && j !== plateCol) rl[j] = 'skip'; });
    if (plateCol == null) return null;
    const tj = rl.indexOf('type');
    const typeCol = tj >= 0 ? tj : null;
    const md = [], dayn = [];
    cells.forEach((c, j) => {
      if (!c || (rl[j] != null && rl[j] !== 'date')) return; // 列の役割で「日付」を選んだ列も、横の形の日付として扱う
      if (dateHeadParts(c) != null) md.push(j);
      else if (dayNum(c) != null) dayn.push(j);
    });
    const wide = md.length >= 3 || (i === 0 && md.length >= 1) || (!md.length && dayn.length >= 3);
    if (wide) {
      const cols = md.length ? md : dayn;
      const nxt = rows[i + 1] || [];
      let wd = {};
      for (const j of cols) if (j < nxt.length && WD_CELL.test(nfkc(nxt[j] || '').trim())) wd[j] = WEEKDAYS.indexOf(nfkc(nxt[j]).trim()[0]);
      const weekdayRow = Object.keys(wd).length >= Math.min(3, cols.length);
      if (!weekdayRow) wd = {};
      const heads = cells.map(() => '');
      let needsMonth = false;
      const title = titleMonth(rows, i);
      if (md.length) {
        for (const j of md) { let h = cells[j]; if (j in wd && weekdayOfHead(h) == null) h += `(${WEEKDAYS[wd[j]]})`; heads[j] = h; }
      } else {
        let ty = null, mm = null;
        if (month != null) mm = month; else if (title) { ty = title[0]; mm = title[1]; }
        if (mm == null) needsMonth = true;
        else for (const j of dayn) { const d = dayNum(cells[j]), ow = dayWd(cells[j]); heads[j] = (ty ? `${ty}/${mm}/${d}` : `${mm}/${d}`) + (j in wd ? `(${WEEKDAYS[wd[j]]})` : ow != null ? `(${WEEKDAYS[ow]})` : ''); }
      }
      return { kind: 'wide', header_row: i, head: cells, data_start: i + (weekdayRow ? 2 : 1), weekday_row: weekdayRow, plate_col: plateCol, type_col: typeCol, heads,
        day_cols: md.length ? [] : dayn, roles: rl, needs_month: needsMonth, title_month: title ? title.slice() : null };
    }
    const c = {};
    for (const r of ['date', 'from', 'to', 'weight', 'time', 'mark']) { const j = rl.indexOf(r); c[r] = j >= 0 ? j : null; }
    { const jk = rl.indexOf('distance_km'), jd = rl.indexOf('dep_time'); if (jk >= 0) c.km = jk; if (jd >= 0) c.dep = jd; } // 走行km・出発時刻の列がある表だけ、このキーが付く（own.py の cols と同じ形を保つ）
    if (c.date != null && (c.from != null || c.to != null)) {
      const title = titleMonth(rows, i) || (month != null ? [null, month] : null);
      const seen = new Map();
      let dayOnly = 0;
      for (const r of rows.slice(i + 1)) {
        const cell = nfkc(c.date < r.length ? r[c.date] || '' : '').trim();
        const p = vdate(cell, title);
        if (p == null && /^\d{1,2}日?$/.test(cell) && dayNum(cell) != null) dayOnly++;
        if (p != null) {
          const key = dateHeadParts(cell) != null ? cell : (p.y ? `${p.y}/${p.m}/${p.d}` : `${p.m}/${p.d}`);
          if (!seen.has(key)) seen.set(key, [p.m, p.d]);
        }
      }
      const heads = [...seen.entries()].sort((a, b) => cmp(a[1][0], b[1][0]) || cmp(a[1][1], b[1][1]) || cmp(a[0], b[0])).map((e) => e[0]);
      // 備考・摘要・荷主などの列は、読まない列のままにして、セルの中の空車の印（空車回送・帰荷なし）だけ見る。利用者が「読まない」と決めた列は見ない
      const markCols = [];
      cells.forEach((x, j) => {
        if (x && (rl[j] === 'mark' || (rl[j] === 'skip' && H_MARK_NOTE_IN.some((w) => headKey(x).includes(w)) && !(roles && Object.prototype.hasOwnProperty.call(roles, colKey(x)))))) markCols.push(j);
      });
      return { kind: 'vertical', header_row: i, head: cells, data_start: i + 1, weekday_row: false, plate_col: plateCol, type_col: typeCol, cols: c, heads,
        day_cols: [], roles: rl, needs_month: dayOnly >= 3 && !title, title_month: title ? title.slice() : null, mark_cols: markCols };
    }
    return null;
  }

  // 日付が縦に並び、車が横に並ぶ配車表（左端の列が日付、見出しの行に 1234・5678 のような車番が並ぶ）を、日付が横の形（1 行 = 1 車両）に置き換える（own.py の transpose_date_down と同じ）。
  // 戻り値は { rows, header_row, cols, date_col } か null。cols は新しい行 n（見出しの次から）が、元の何列目の車か
  function transposeDateDown(rows) {
    for (let i = 0; i < Math.min(HEADER_SCAN_ROWS, rows.length); i++) {
      const cells = rows[i].map((c) => (c || '').trim());
      const cols = [];
      cells.forEach((c, j) => { if (c && haishaRole(c) == null && dateHeadParts(c) == null && !WD_CELL.test(nfkc(c)) && normalizePlate(c) != null) cols.push(j); });
      if (!cols.length || cells.some((c) => c && dateHeadParts(c) != null)) continue; // 見出しに日付が並ぶ表は、日付が横の形
      const body = rows.slice(i + 1);
      for (let dc = 0; dc < Math.min(3, cells.length); dc++) {
        if (cols.includes(dc)) continue;
        const cellAt = (r, j) => (j < r.length ? r[j] : '') || '';
        if (body.filter((r) => dateHeadParts(cellAt(r, dc)) != null).length < 3) continue;
        const wdc = body.filter((r) => WD_CELL.test(nfkc(cellAt(r, dc + 1)).trim())).length >= 3 ? dc + 1 : null; // 日付の隣の曜日の列
        const dhead = (r) => {
          const d = cellAt(r, dc).trim(), w = wdc == null ? '' : nfkc(cellAt(r, wdc)).trim();
          return d && w && WD_CELL.test(w) && dateHeadParts(d) != null && !d.includes('(') ? `${d}(${w[0]})` : d;
        };
        const head = ['車番', ...body.map(dhead)];
        const out = rows.slice(0, i).map((r) => r.slice()).concat([head]);
        for (const j of cols) out.push([cells[j], ...body.map((r) => cellAt(r, j))]);
        return { rows: out, header_row: i, cols, date_col: dc };
      }
    }
    return null;
  }

  // 配車表の見出しの行と形（wide: 日付が列に並ぶ横の形 / vertical: 1 行 = 1 便）を、先頭の 10 行から探す。見つからなければ null
  function detectHaisha(rows, roles, month) {
    for (let i = 0; i < Math.min(HEADER_SCAN_ROWS, rows.length); i++) {
      if (blankRow(rows[i])) continue;
      const lay = layoutFromRow(rows, i, roles, month == null ? null : month);
      if (lay) return lay;
    }
    return null;
  }

  // 見出しに日付と発地か着地の列があり、車番の列が無い表に、車番の列（全行「1号車」）を足した行列を返す。足せない（車番の列がある・日付の列が無い）ときは null
  const NO_PLATE_NAME = '1号車', NO_PLATE_LABEL_JA = '車両 1 台（車番の列なし）';
  function addVehicleCol(rows, roles, month) {
    for (let i = 0; i < Math.min(HEADER_SCAN_ROWS, rows.length); i++) {
      if (blankRow(rows[i])) continue;
      const rl = rows[i].map((c) => haishaRole((c || '').trim(), roles));
      if (rl.some((r) => r === 'plate' || r === 'plate2' || r === 'plate3')) return null;
      if (!rl.includes('date') || !(rl.includes('from') || rl.includes('to'))) continue;
      const out = rows.map((r, ri) => (ri < i || blankRow(r) ? r : r.concat([ri === i ? '車番' : NO_PLATE_NAME])));
      if (out[i].length !== rows[i].length + 1) return null;
      return detectHaisha(out, roles, month == null ? null : month) ? out : null;
    }
    return null;
  }

  function legacyLayout(rows) {
    const cells = rows[0].map((c) => (c || '').trim());
    const rl = cells.map((c) => haishaRole(c));
    let plateCol = rl.findIndex((r) => r === 'plate' || r === 'plate2' || r === 'plate3');
    if (plateCol < 0) plateCol = 0;
    const tj = rl.findIndex((r, j) => r === 'type' && j !== plateCol);
    return { kind: 'wide', header_row: 0, head: cells, data_start: 1, weekday_row: false, plate_col: plateCol, type_col: tj >= 0 ? tj : null,
      heads: cells.map((c) => (dateHeadParts(c) ? c : '')), day_cols: [], roles: rl, needs_month: false, title_month: null };
  }

  /* ------------------------------------------------------------ 配車表 → 縦の形 */

  function formsByNum(cells) {
    const out = {};
    for (const raw of cells) {
      const [num, full] = plateIdentity(raw);
      if (num == null) continue;
      if (!out[num]) out[num] = { full: new Set(), bare: new Set() };
      (full ? out[num].full : out[num].bare).add(full || nfkc(raw).trim());
    }
    return out;
  }
  const sortedArr = (set) => [...set].sort((a, b) => cmp(a, b));
  // 末尾 4 桁が同じで車格か運転者が違う書き方を探す（own.py の plate_attr_conflicts と同じ）。entries: [[車番の文字, 車種, 運転者]]。戻り値は { 末尾 4 桁: ['書き方（車格・運転者）'] }
  function plateAttrConflicts(entries) {
    const by = {};
    for (const [raw, ty, drv] of entries) {
      const [num] = plateIdentity(raw);
      if (num == null) continue;
      const cls = (ty || '').trim() ? classOf(ty) : '';
      const key = [nfkc(raw).trim(), cls, (drv || '').trim()];
      if (!by[num]) by[num] = [];
      if (!by[num].some((k) => k.join('\u0000') === key.join('\u0000'))) by[num].push(key);
    }
    const out = {};
    for (const [num, ks] of Object.entries(by)) {
      const classes = new Set(ks.map((k) => k[1]).filter((c) => c && c !== '不明')), drivers = new Set(ks.map((k) => k[2]).filter((x) => x));
      if (ks.length >= 2 && (classes.size >= 2 || drivers.size >= 2)) out[num] = ks.map((k) => (k[1] || k[2] ? `${k[0]}（${[k[1], k[2]].filter((x) => x).join('・')}）` : k[0]));
    }
    return out;
  }
  const H_DRIVER_IN = ['乗務員', '運転者', '運転手', 'ドライバー', '氏名'];
  const HIRED_RE = /傭車|庸車|外注|協力会社/;
  const isHired = (raw, type) => HIRED_RE.test(nfkc(`${raw} ${type || ''}`));

  function plateNotesOf(forms, attr) {
    const out = [];
    attr = attr || {};
    for (const num of Object.keys(forms).sort()) {
      const f = forms[num];
      if (attr[num]) out.push({ kind: 'attr', num, forms: attr[num].slice() });
      else if (f.full.size >= 2) out.push({ kind: 'split', num, forms: sortedArr(f.full).concat(sortedArr(f.bare)) });
      else if (f.full.size === 1 && f.bare.size) out.push({ kind: 'merge', num, forms: sortedArr(f.full).concat(sortedArr(f.bare)) });
    }
    return out;
  }
  function totalRow(row, lay) {
    for (const j of new Set([lay.plate_col, 0, lay.type_col])) if (j != null && j < row.length && TOTAL_TOKENS.has(headKey(row[j]))) return true;
    return false;
  }
  function extraColumns(rows, lay, used) {
    const out = [];
    lay.head.forEach((h, j) => {
      if (!h || used.has(j)) return;
      const vals = [];
      for (const r of rows.slice(lay.data_start)) { const v = ((j < r.length ? r[j] : '') || '').trim(); if (v && !vals.includes(v)) vals.push(v); }
      if (vals.length) out.push({ col: j, header: h, key: colKey(h), samples: vals.slice(0, 3) });
    });
    return out;
  }
  // 縦の形の重さの列。単位があればそのまま、数だけなら見出しの単位（kg / t）、見出しに無ければ 100 以上を kg、未満を t とみなす
  function verticalWeight(cell, header) {
    let t = nfkc(cell).trim();
    if (!t) return null;
    if (/^([0-9]+(?:\.[0-9]+)?|[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?)$/.test(t)) {
      const v = parseFloat(t.replace(/,/g, '')), hk = headKey(header);
      const unit = hk.includes('KG') ? 'kg' : (hk.includes('T') || hk.includes('トン')) ? 't' : (v >= 100 ? 'kg' : 't');
      t = t + unit;
    }
    return { weights: parseWeights(t), text: t };
  }
  const uniqNum = (xs) => { const out = []; for (const x of xs) if (!out.includes(x)) out.push(x); return out; };

  // 配車表（横の形か縦の形）を、1 行 = 1 車両 × 1 日の rows に直す（own.py の read_haisha と同じ）。
  // 戻り値: rows[{plate, plate_raw, vehicle_type, date, text, notes, line, col, parsed?}], unreadable, stats, plates, plate_notes, layout, extra_cols, needs_month, skipped_total_rows
  function readHaisha(rows, year, layout, roles, month) {
    const out = { rows: [], unreadable: [], stats: {}, plates: [], plate_notes: [], extra_cols: [], layout: null, needs_month: false, skipped_total_rows: 0 };
    if (!rows.length) { out.stats = { filled_rows: 0, groups: 0, date_cols: 0, bad_cols: 0, data_rows: 0 }; return out; }
    let lay = layout;
    if (!lay) {
      lay = detectHaisha(rows, roles, month);
      if (!lay) { const tr = transposeDateDown(rows); if (tr) { rows = tr.rows; lay = detectHaisha(rows, roles, month); } } // 日付が縦・車が横の配車表
      lay = lay || legacyLayout(rows);
    }
    out.layout = { kind: lay.kind, header_line: lay.header_row + 1, data_start_line: lay.data_start + 1, weekday_row: lay.weekday_row, title_month: lay.title_month || null, plate_col: lay.plate_col };
    out.needs_month = !!lay.needs_month;
    const head = lay.head, pc = lay.plate_col, tc = lay.type_col;
    const body = [];
    rows.forEach((r, ri) => { if (ri >= lay.data_start) body.push([ri + 1, r.map((c) => (c || '').trim())]); });
    const width = head.length;
    const dc = head.findIndex((h) => h && H_DRIVER_IN.some((x) => headKey(h).includes(x)));
    const dcol = dc >= 0 ? dc : null;
    const cellat = (r, j) => ((j != null && j < r.length ? r[j] : '') || '').trim();
    const plateRows = body.map(([, r]) => r).filter((r) => pc < r.length && r[pc] && !TOTAL_TOKENS.has(headKey(r[pc])));
    const isOwn = (r) => !isHired(r[pc], cellat(r, tc));
    const forms = formsByNum(plateRows.filter(isOwn).map((r) => r[pc]));
    const attr = plateAttrConflicts(plateRows.filter(isOwn).map((r) => [r[pc], cellat(r, tc), cellat(r, dcol)]));
    out.plate_notes = plateNotesOf(forms, attr);
    const conflict = new Set(Object.keys(forms).filter((n) => forms[n].full.size >= 2));
    out.hired = [];
    // 4 桁の登録番号がどの行にも無く、1〜3 桁の号車の番号が並ぶ配車表は、「N号車」をそのファイルの中の車の名前にする（own.py の gou_mode と同じ）
    const ownCells = plateRows.filter(isOwn).map((r) => r[pc]);
    const gouMode = ownCells.length > 0 && !ownCells.some((x) => normalizePlate(x) != null) && ownCells.some((x) => gouName(x) != null);
    out.gou_mode = gouMode;
    // 車番の identity。車格か運転者が違うときは、完全な書き方があればそれ、4 桁だけの書き方は 4 桁。同じ書き方で車格が違うときは車格を添える
    const ident = (raw, ty, drv) => {
      const [num, full] = plateIdentity(raw);
      if (num == null) return gouMode ? gouName(raw) : null;
      if (conflict.has(num) && full) return full;
      if (attr[num]) {
        if (full || forms[num].full.size) return full || num;
        const part = (ty || '').trim() ? classOf(ty) : (drv || '').trim();
        return part && part !== '不明' ? `${num}（${part}）` : num;
      }
      return num;
    };
    const hiredNote = (raw, line, cells, legs) => {
      const name = nfkc(raw).trim();
      let h = out.hired.find((x) => x.name === name);
      if (!h) { h = { name, line, cells: 0, legs: 0 }; out.hired.push(h); }
      h.cells += cells; h.legs += legs;
    };
    if (lay.kind === 'vertical') return readVertical(rows, body, lay, year, out, ident, hiredNote, dcol);

    const dateCols = {}, badCols = {};
    const ymd = assignHeadYears(lay.heads, year);
    const dayCols = new Set(lay.day_cols || []);
    const used = new Set([pc]);
    if (tc != null) used.add(tc);
    head.forEach((h, i) => {
      if (used.has(i) || !h) return;
      if (lay.roles[i] === 'skip') { used.add(i); return; }
      if (i in ymd) {
        used.add(i);
        const dh = ymd[i], iso = isoDate(dh.y, dh.m, dh.d);
        if (iso) dateCols[i] = iso; else badCols[i] = '日付が読めません';
      } else if (dayCols.has(i) || /^\s*\d{1,2}\s*日\s*$/.test(nfkc(h))) { used.add(i); badCols[i] = '月が分かりません'; }
    });
    const groups = [];
    let cur = null, filled = 0, dataRows = 0;
    for (const [ri, r0] of body) {
      const row = r0.slice();
      while (row.length < width) row.push('');
      if (blankRow(row)) { cur = null; continue; }
      if (totalRow(row, lay)) { out.skipped_total_rows++; cur = null; continue; }
      dataRows++;
      const raw = row[pc];
      if (raw) {
        const ty = tc != null ? row[tc] : '';
        if (isHired(raw, ty)) { cur = { plate: null, raw, type: ty, rows: [[ri, row]], bad: true, hired: true }; groups.push(cur); continue; }
        const pn = ident(raw, ty, cellat(row, dcol));
        cur = { plate: pn, raw, type: ty, driver: cellat(row, dcol), rows: [[ri, row]], bad: pn == null };
        groups.push(cur);
      } else if (cur && (!cur.bad || cur.hired)) { cur.rows.push([ri, row]); filled++; }
      else { cur = { plate: null, raw: '', type: '', rows: [[ri, row]], bad: true }; groups.push(cur); }
    }
    for (const g of groups) {
      const [firstLine, first] = g.rows[0];
      if (g.hired) { // 傭車: 自社の車ではないので、区間は読まず、傭車の行として数える
        let cells = 0, legsN = 0;
        for (const [, row] of g.rows) row.forEach((cell, ci) => { if (ci in dateCols && cell) { const pcell = parseCell(cell); if (!pcell.nonleg) { cells++; legsN += pcell.legs.length; } } });
        hiredNote(g.raw, firstLine, cells, legsN);
        continue;
      }
      if (g.plate == null) {
        const reason = !g.raw ? '車番がありません' : '車番が読めません';
        for (const [line, row] of g.rows) {
          row.forEach((cell, ci) => {
            if (ci === pc || ci === tc || !cell || !(ci in dateCols || ci in badCols)) return;
            out.unreadable.push({ line, col: ci, original: cell, reason });
          });
        }
        continue;
      }
      if (!out.plates.some((p) => p.plate === g.plate)) out.plates.push({ plate: g.plate, raw: g.raw, type: g.type, driver: g.driver || '' });
      first.forEach((cell, ci) => {
        if (ci in badCols && cell) { out.unreadable.push({ line: firstLine, col: ci, original: cell, reason: badCols[ci], plate: g.plate }); return; }
        if (!(ci in dateCols)) return;
        const notes = g.rows.slice(1).map(([, row]) => row[ci]).filter((x) => x);
        if (!cell && !notes.length) return;
        out.rows.push({ plate: g.plate, plate_raw: g.raw, vehicle_type: g.type, date: dateCols[ci], text: cell, notes, line: firstLine, col: ci });
      });
    }
    out.extra_cols = extraColumns(rows, lay, used);
    out.stats = { filled_rows: filled, groups: groups.length, plate_groups: groups.filter((g) => g.plate != null).length, date_cols: Object.keys(dateCols).length, bad_cols: Object.keys(badCols).length, data_rows: dataRows };
    return out;
  }

  function readVertical(rows, body, lay, year, out, ident, hiredNote, dcol) {
    const c = lay.cols, pc = lay.plate_col, tc = lay.type_col, head = lay.head;
    const used = new Set([pc]);
    for (const v of Object.values(c)) if (v != null) used.add(v);
    if (tc != null) used.add(tc);
    lay.roles.forEach((r, j) => { if (r === 'skip') used.add(j); });
    let dataRows = 0;
    const plates = [];
    const title = lay.title_month ? lay.title_month.slice() : null;
    const markCols = lay.mark_cols || [];
    let lastDate = '', lastRaw = '', filledDates = 0, filledPlates = 0, marked = 0;
    const cell = (r, j) => ((j != null && j < r.length ? r[j] : '') || '').trim();
    for (const [ri, row] of body) {
      if (blankRow(row)) continue;
      if (totalRow(row, lay)) { out.skipped_total_rows++; continue; }
      dataRows++;
      const col = c.date;
      const shown = [cell(row, c.from), cell(row, c.to)].filter((x) => x).join(' → ') || '（記入なし）';
      let raw = cell(row, pc);
      let dtext = nfkc(cell(row, c.date));
      if (!dtext && lastDate) { dtext = lastDate; filledDates++; } // 日付が空欄（Excel の「上と同じ」）は、上の行の日付で読む
      else if (dtext) lastDate = dtext;
      if (!raw && lastRaw) { raw = lastRaw; filledPlates++; } else if (raw) lastRaw = raw;
      if (raw && isHired(raw, cell(row, tc))) { if (hiredNote) hiredNote(raw, ri, 1, 1); continue; }
      const pn = raw ? ident(raw, cell(row, tc), cell(row, dcol)) : null;
      if (pn == null) { out.unreadable.push({ line: ri, col, original: shown, reason: !raw ? '車番がありません' : '車番が読めません' }); continue; }
      const p = vdate(dtext, title);
      let iso = null;
      if (p != null) iso = isoDate(p.y != null ? p.y : year, p.m, p.d);
      if (iso == null) { out.unreadable.push({ line: ri, col, original: shown, reason: '日付が読めません', plate: pn }); continue; }
      if (!plates.some((x) => x.plate === pn)) plates.push({ plate: pn, raw, type: cell(row, tc), driver: cell(row, dcol) });
      let frm = cell(row, c.from) || null, to = cell(row, c.to) || null;
      const mark = nfkc(cell(row, c.mark));
      let empty = isEmptyWord(mark) || isEmptyWord(to || '') || isEmptyWord(frm || '');
      if (!empty && markCols.some((j) => hasEmptyMark(cell(row, j)))) { empty = true; marked++; } // 備考・荷主・摘要の列の「空車回送」「帰荷なし」
      const loadedMark = !empty && LOADED_WORDS.includes(mark.replace(/^[()]+|[()]+$/g, ''));
      if (to && isEmptyWord(to)) to = null;
      if (frm && isEmptyWord(frm)) frm = null;
      if (to && !prefName(to)) to = prefSplit(to)[1] || to; // 「東京都江東区」「東京 江東区」の都道府県は読み飛ばす
      if (frm && !prefName(frm)) frm = prefSplit(frm)[1] || frm;
      const errors = [], warnings = [];
      let arr = null;
      const tcell = nfkc(cell(row, c.time)).replace(/着/g, '').trim();
      if (tcell) {
        arr = parseTime(tcell);
        if (arr == null) { const mt = /(\d{1,2}):(\d{2})/.exec(tcell); arr = mt ? parseTime(mt[0]) : null; }
        if (arr == null) warnings.push(TIME_DROPPED); // 時刻だけ捨てて、区間は残す
      }
      const leg = { frm, to, empty, arr, weight_kg: null };
      const kmFile = c.km != null ? parseKm(cell(row, c.km)) : null;
      if (kmFile != null && kmFile > 0) leg.km_file = kmFile; // ファイルに書かれた走行距離。あれば推定より先に使う
      const depCell = c.dep != null ? parseTime(nfkc(cell(row, c.dep)).trim()) : null;
      if (depCell) leg.dep_hint = depCell;
      if (loadedMark) leg.loaded_mark = true;
      else if (!empty && mark.replace(/^[()]+|[()]+$/g, '')) leg.note = mark.replace(/^[()]+|[()]+$/g, '').slice(0, 24); // 区分の列に書かれた、空車の印でも実車の印でもない語
      const w = verticalWeight(cell(row, c.weight), c.weight != null ? head[c.weight] : '');
      if (w) {
        const sign = w.weights.filter((x) => x.error);
        const kgs = uniqNum(w.weights.filter((x) => x.kg != null).map((x) => x.kg));
        if (sign.length) errors.push(sign[0].error);
        if (kgs.length === 1) leg.weight_kg = kgs[0];
        else if (kgs.length > 1) leg.weights = kgs;
      }
      // 積地・卸地が空で、行のどこかのセルが「公休」「有給」などなら、走らない日の行（区間として数えない）
      const nonleg = (NONLEG.includes(frm) && !to) || (NONLEG.includes(to) && !frm) || (!frm && !to && row.some((x) => NONLEG.includes(nfkc(x || '').trim())));
      const parsed = { legs: nonleg ? [] : [leg], nonleg: !!nonleg, errors, warnings };
      const shownFull = [shown, tcell, w ? w.text : ''].filter((x) => x).join(' ');
      out.rows.push({ plate: pn, plate_raw: raw, vehicle_type: cell(row, tc), date: iso, text: shownFull, notes: [], line: ri, col, parsed });
    }
    out.plates = plates;
    out.extra_cols = extraColumns(rows, lay, used);
    out.stats = { filled_rows: 0, groups: plates.length, plate_groups: plates.length, date_cols: 0, bad_cols: 0, data_rows: dataRows, filled_dates: filledDates, filled_plates: filledPlates, marked_empty: marked };
    return out;
  }

  const wideToLong = (rows, year) => readHaisha(rows, year);

  /* ------------------------------------------------------------ 配車表のセル */

  const NONLEG = ['休', '整備', '休み', '車検', '待機', '点検', '定期点検', '修理', '休日', '公休', '有休', '有給', '有給休暇', '欠勤', '代休', '振休', '特休', '年休'];
  // 重さ: 数の直後に t・kg・トン。直前が英数字・小数点なら重さではない。「4t車」「5トンウイング」のように、数の直後が車の種類を表す言葉なら、車の大きさで重さではない（own.py の parse_weights と同じ）
  const WEIGHT_RE = /(?<![0-9A-Za-z.])([-−‐‑–+]?)\s*((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)\s*([kK][gG]|[tT]|トン)(?![a-zA-Z])/g;
  const CLASS_AFTER = /^\s*(?:車|ウイング|ウィング|ワイド|ロング|箱|平|ユニック|クレーン|冷凍|冷蔵|保冷|ダンプ|トレーラー?|バン|アルミ|幌|ゲート|セミ|低床|高床|パッカー|タンク|ミキサー|コンテナ|キャリア|ローリー)/;
  const WEIGHT_SIGN_ERR = '重さが読めません（マイナスやプラスの記号つきは入れられません）';
  // 「1e3kg」「1e3t」のような指数の書き方。重さとしては読まず、理由を出す（黙って重さ無しの実車にしない。own.py の _SCI_WEIGHT と同じ）
  const SCI_WEIGHT_RE = /(?<![0-9A-Za-z.])[-−‐‑–+]?\s*[0-9]+(?:\.[0-9]+)?[eE][-+]?[0-9]+\s*(?:[kK][gG]|[tT]|トン)(?![a-zA-Z])/;
  const WEIGHT_SCI_ERR = '重さが読めません（1e3 のような指数の書き方は入れられません。4600kg や 4.6t のように書いてください）';
  // 戻り値: [{ kg } か { error }]（記号つき）。重さが無ければ []
  function parseWeights(text) {
    const t = nfkc(text);
    const out = [];
    for (const m of t.matchAll(WEIGHT_RE)) {
      if (CLASS_AFTER.test(t.slice(m.index + m[0].length))) continue;
      if (m[1]) { out.push({ error: WEIGHT_SIGN_ERR }); continue; }
      const v = parseFloat(m[2].replace(/,/g, ''));
      out.push({ kg: m[3].toLowerCase() === 'kg' ? v : v * 1000 });
    }
    if (SCI_WEIGHT_RE.test(t)) out.push({ error: WEIGHT_SCI_ERR });
    return out;
  }
  // 最初の 1 つ。戻り値: { kg } か { error }。重さが無ければ null
  function parseWeight(text) { const ws = parseWeights(text); return ws.length ? ws[0] : null; }

  function parseTime(tok) {
    const t = nfkc(tok).trim();
    let m = /^(\d{1,2}):(\d{2})$/.exec(t) || /^(\d{1,2})時(\d{1,2})分$/.exec(t) || /^(\d{1,2})(\d{2})$/.exec(t);
    if (m) { const h = +m[1], mi = +m[2]; return h < 24 && mi < 60 ? `${pad(h)}:${pad(mi)}` : null; }
    m = /^(\d{1,2})時半$/.exec(t);
    if (m && +m[1] < 24) return `${pad(+m[1])}:30`;
    m = /^(\d{1,2})時$/.exec(t);
    if (m && +m[1] < 24) return `${pad(+m[1])}:00`;
    return null;
  }

  // 空車の印（横の形のセルも縦の形の「区分」の列も、同じ表で読む。own.py の EMPTY_WORDS と同じ）。前後が仮名・漢字の中の「空」（空港、空調）は印にしない
  // 「回送」「空コン」（空のコンテナ。半角カナの「空ｺﾝ回送」は NFKC で直る）は、前後に何があっても空車の印にする
  const EMPTY_WORDS = ['戻り空車', '戻り空', '帰り空車', '帰り空', '空車', '空荷', '荷なし', '荷無し', '荷無', '帰荷なし', '帰荷無し', '帰り荷なし', '帰り荷無し', '空回送', '回送', 'カラ', '空'];
  // 「戻り空」「荷なし」は前が仮名でも印にする。「帰り」「戻り」だけの語は印として弱いので、重さが同じ区間に無いときだけ空車にする（WEAK_EMPTY。own.py の _WEAK_EMPTY と同じ）
  // 「帰り荷さがし中」「帰り荷探し」「帰り荷待ち」「求荷中」は、帰りの荷を探している（今は積んでいない）ので空車の印にする。「帰り荷あり」は印にしない
  const EMPTY_MARK = /回送|空コン|戻り空車|戻り空|荷なし|荷無し|(?:帰り|戻り|帰)荷\s*(?:さがし|探し|待ち|探索|募集|未定)|荷さがし|荷探し|求荷(?:中)?|(?<![ぁ-んァ-ヶ一-鿿々])(?:帰り空車|帰り空|空車|空荷|カラ|空)(?![ぁ-んァ-ヶ一-鿿々])/;
  const WEAK_EMPTY = /(?<![ぁ-んァ-ヶ一-鿿々])(?:帰り|戻り)(?![ぁ-んァ-ヶ一-鿿々])/;
  // 区間の前に付く空車の印（「回送 江東 → 加古川」「（回送）江東→加古川」「回送：江東→加古川」「空 江東→加古川」）。外して印として覚え、残りを区間として読む（own.py の _EMPTY_PREFIX と同じ）
  const EMPTY_PREFIX = /^(?:[(\[【]\s*(?:戻り空車|戻り空|帰り空車|帰り空|空車|空荷|荷なし|荷無し|空回送|回送|空コン|カラ|空)\s*[)\]】]\s*[:：、,]?|(?:空回送|回送|空コン)\s*[:：、,]?|(?:戻り空車|戻り空|帰り空車|帰り空|空車|空荷|荷なし|荷無し|カラ|空)(?:\s*[:：、,]\s*|\s+))\s*/;
  const RETURN_PREFIX = /^(?:帰り|戻り|復路|復)\s*[:：、,]?\s*(?=[^\s→⇒>~〜=\-])/;
  const HAS_ARROW = /->|=>|[→⇒>~〜]/;
  // 区間の前に付いた空車の印と、積地の前の都道府県（「東京 江東区 → 加古川」の「東京」）を外す。戻り値は [空車の印があったか, 残りの文字]（own.py の split_cell_prefix と同じ）
  function splitCellPrefix(part) {
    let t = part.trim(), pre = false;
    const m = EMPTY_PREFIX.exec(t);
    if (m && HAS_ARROW.test(t.slice(m[0].length))) { pre = true; t = t.slice(m[0].length).trim(); }
    else {
      const mb = RETURN_PREFIX.exec(t); // 「帰り 江東→加古川 荷なし」の「帰り」は、区間の名前ではない。外して、空車かどうかは後ろの印で決める
      if (mb && HAS_ARROW.test(t.slice(mb[0].length))) t = t.slice(mb[0].length).trim();
    }
    const m2 = /^(\S+)\s+(?![→⇒>~〜=\-])(\S+)/.exec(t);
    if (m2 && prefName(m2[1]) && /[市区町村]/.test(m2[2].split('→')[0])) t = t.slice(m2[0].length - m2[2].length);
    return [pre, t];
  }
  // 区間の後ろに書かれた、重さ・時刻・空車の印のどれでもない語（own.py の cell_note と同じ）
  const NOTE_STRIP = [
    /[-−‐‑–+]?\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*(?:[kK][gG]|[tT]|トン)\S*/g,
    /[0-9]{1,2}\s*[:：]\s*[0-9]{2}|[0-9]{1,2}時(?:半|[0-9]{1,2}分)?|着|[0-9]+\s*(?:回転|往復|回)/g,
    /\(空\)|実車|積載|積み|(?<![ぁ-んァ-ヶ一-鿿々])実(?![ぁ-んァ-ヶ一-鿿々])/g,
  ];
  const SCI_NOTE = /(?<![0-9A-Za-z.])[-−‐‑–+]?\s*[0-9]+(?:\.[0-9]+)?[eE][-+]?[0-9]+\s*(?:[kK][gG]|[tT]|トン)\S*/g; // 指数の重さ（1e3kg）は、重さとして読めない記号ごと外す
  function cellNote(rest) {
    let t = nfkc(rest || '').replace(SCI_NOTE, ' ');
    for (const rx of NOTE_STRIP) t = t.replace(rx, ' ');
    t = t.replace(/[()\[\]【】:：、,。・\s]+/g, ' ').trim();
    t = t.split(' ').filter((w) => w && !/^[0-9.\-]+$/.test(w)).join(' ');
    return t.slice(0, 24);
  }
  // 実車の印。車庫へ戻る区間で、重さの代わりに「実車」と書いておけば、実車として数える（印も重さも無いと不明になる）。own.py の LOADED_WORDS と同じ
  const LOADED_WORDS = ['実車', '積載', '積み', '実'];
  const LOADED_MARK = /(?<![ぁ-んァ-ヶ一-鿿々])(?:実車|積載|積み|実)(?![ぁ-んァ-ヶ一-鿿々])/;
  const ROUNDS_RE = /(?<![0-9:：])([2-9])\s*(?:回転|往復|回(?![転数]))/;
  const LOCAL_WORD = /市内|区内|町内|構内|場内|ルート|巡回|地場|集配/;
  const TIME_DROPPED = '時刻が読めません';
  // 備考・荷主・摘要の列のセルに空車の印があるか（「空車回送」「空」「回送」「帰荷なし」）。「空港運輸」のように前後が仮名・漢字の「空」は印にしない（own.py の has_empty_mark と同じ）
  const hasEmptyMark = (s) => { const t = nfkc(s || '').trim(); return !!t && (isEmptyWord(t) || EMPTY_MARK.test(t)); };
  const isEmptyWord = (s) => { const t = nfkc(s).trim().replace(/^[()]+|[()]+$/g, '').trim(); return EMPTY_WORDS.includes(t) || t.includes('回送') || t.startsWith('空コン'); };

  // 「着」の前後の時刻。「着530」「着 5:30」「5:30着」。「着」の後ろが重さ（9.2t）のときは前の時刻を読む。戻り値は [時刻か null, 時刻を捨てたか]
  // 「8時半ごろ着」「頃」のような概算や読めない時刻は、時刻だけ捨てる（区間は残す）
  function arrival(rest) {
    if (!rest.includes('着')) return [null, false];
    const after = /着\s*([^\s（(]+)/.exec(rest), before = /([^\s（(着]+)\s*着/.exec(rest);
    let bad = false;
    for (const m of [after, before]) {
      if (!m) continue;
      const t = parseTime(m[1]);
      if (t) return [t, false];
      if (!parseWeights(m[1]).length) bad = true;
    }
    return [null, bad];
  }

  // 「加古川市内 ルート配送」の地名（加古川市）。無ければ null
  function localPlace(t) {
    let m = /^\s*([^\s→>/]+?[市区町村])内/.exec(t);
    if (m) return m[1];
    m = /^\s*([^\s→>/]+?)(?:ルート|巡回|地場|集配|構内|場内)/.exec(t);
    return m ? m[1] : null;
  }

  // 矢印の無いセル。空車の印だけ（「空」「帰り空」「回送」）は積地・降ろし地の無い空車、「加古川市内 ルート配送」は地場の実車。どちらでもなければ null。戻り値は [区間, 時刻を捨てたか]
  function loneCell(part) {
    if (isEmptyWord(part)) return [{ frm: null, to: null, empty: true, arr: null, weight_kg: null }, false];
    if (['帰り', '戻り'].includes(part.trim())) return [{ frm: null, to: null, empty: true, arr: null, weight_kg: null, weak_empty: true }, false];
    if (!LOCAL_WORD.test(part)) return null;
    const place = localPlace(part), [arr, bad] = arrival(part);
    const kgs = uniqNum(parseWeights(part).filter((w) => w.kg != null).map((w) => w.kg));
    const leg = { frm: place, to: place, empty: EMPTY_MARK.test(part), arr, weight_kg: kgs.length === 1 ? kgs[0] : null, local: true };
    if (kgs.length > 1) leg.weights = kgs;
    const rm = ROUNDS_RE.exec(part);
    if (rm) leg.rounds = +rm[1];
    return [leg, bad];
  }

  // 都道府県の名前。「東京 江東区」「東京都江東区」の都道府県は読み飛ばし、市区町村を地点にする（own.py の PREFECTURES と同じ）
  const PREFECTURES = [
    '北海道', '青森県', '岩手県', '宮城県', '秋田県', '山形県', '福島県', '茨城県', '栃木県', '群馬県', '埼玉県', '千葉県', '東京都', '神奈川県', '新潟県', '富山県', '石川県', '福井県',
    '山梨県', '長野県', '岐阜県', '静岡県', '愛知県', '三重県', '滋賀県', '京都府', '大阪府', '兵庫県', '奈良県', '和歌山県', '鳥取県', '島根県', '岡山県', '広島県', '山口県',
    '徳島県', '香川県', '愛媛県', '高知県', '福岡県', '佐賀県', '長崎県', '熊本県', '大分県', '宮崎県', '鹿児島県', '沖縄県',
  ];
  const PREF_SHORT = {};
  for (const p of PREFECTURES) PREF_SHORT[p === '北海道' ? p : p.slice(0, -1)] = p; // 東京 → 東京都
  const PREFS_LONGEST_FIRST = PREFECTURES.slice().sort((a, b) => b.length - a.length);
  // 文字が都道府県の名前（東京・東京都）だけなら、正式な名前（東京都）。そうでなければ null
  function prefName(tok) {
    const t = nfkc(tok).trim();
    if (PREFECTURES.includes(t)) return t;
    return Object.prototype.hasOwnProperty.call(PREF_SHORT, t) ? PREF_SHORT[t] : null;
  }
  // 文字の先頭の都道府県を外す。戻り値は [都道府県の正式な名前か null, 残りの文字]（own.py の pref_split と同じ）。
  // 「東京都江東区」「兵庫県加古川市」は正式な名前の直後から外す。「東京 江東区」のように短い名前は、後ろが空白で区切られているときだけ外す（「大阪狭山市」「京都市」の先頭を都道府県と読まないため）。残りが空になるとき（「東京」だけ）は外さない
  function prefSplit(text) {
    const t = nfkc(text).trim();
    for (const full of PREFS_LONGEST_FIRST) if (t.startsWith(full) && t.slice(full.length).trim()) return [full, t.slice(full.length).trim()];
    const m = /^(\S+)\s+(\S[\s\S]*)$/.exec(t);
    if (m && Object.prototype.hasOwnProperty.call(PREF_SHORT, m[1])) return [PREF_SHORT[m[1]], m[2].trim()];
    return [null, t];
  }
  // 降ろし地が書かれていないセル（「加古川 → 4.6t」「江東 → 着5:30」）の矢印の後ろ。重さ・時刻の形で始まる語は、地名ではない（地名の別名に登録しない。own.py の _NON_PLACE と同じ）
  const NON_PLACE_RE = /^(?:[-−‐‑–+]?\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*(?:[kK][gG]|[tT]|トン)(?![a-zA-Z])|[0-9]+(?:\.[0-9]+)?[eE][-+]?[0-9]+\s*(?:[kK][gG]|[tT]|トン)|[0-9]{1,2}:[0-9]{2}|[0-9]{1,2}時|[0-9]+(?:\.[0-9]+)?$)/;
  // 矢印（→ ⇒ => -> ~ 〜 >。全角の「～」は NFKC で「~」になる）と、1 つのセルの中の区間の区切り（／ / 改行、「AM」「PM」「午前」「午後」の前）
  const ARROW_LEG_RE = /^((?:(?!->|=>)[^→⇒>~〜=\s])*)\s*(?:→|⇒|=>|->|~|〜|>)\s*([^\s（(着]*)\s*([\s\S]*)$/;
  const LEG_SPLIT_RE = /[／/\r\n]+|\s+(?=(?:AM|PM|午前|午後)(?!\s*[:：]?\s*[0-9:：着]))/i;
  const PERIOD_HEAD_RE = /^(?:AM|PM|午前|午後)(?!\s*[:：]?\s*[0-9:：着])\s*[:：]?\s*/i;

  // 矢印の前に荷主名や「積」「卸」が挟まるセル（「姫路 ﾄｳﾜ電機 → 春日部」「岩槻 積 → 姫路」「ﾄｳﾜ電機 姫路→春日部」）。own.py の _origin_only と同じ
  const SHIPPER_WORD = /^(?:積|卸|積み|卸し|積込|積込み|取卸|取卸し|荷積|荷卸|荷主|積地|卸地|着|発)$/;
  const SHIPPER_NAME = /(?:電機|電気|電工|商事|商店|商会|運輸|運送|物流|通運|工業|工場|製作所|製造|産業|食品|フーズ|倉庫|ロジ|ロジスティクス|化学|鉄鋼|金属|建材|機械|石油|興業|紙業|セメント|株式会社|有限会社|\(株\)|\(有\)|㈱|㈲)/;
  const ARROW_ANY = /->|=>|[→⇒>~〜]/;
  // 矢印の前に空白で区切った語が 2 つ以上あるとき、積地として最後の地名らしい語だけを残す（荷主名・「積」「卸」・重さ・時刻の語は積地にしない）。全部が地名らしい語（「東京 江東区」）のときは、そのまま
  function originOnly(part) {
    const am = ARROW_ANY.exec(part);
    if (!am) return part;
    const toks = part.slice(0, am.index).trim().split(/\s+/).filter(Boolean);
    if (toks.length < 2) return part;
    const shipper = (t) => SHIPPER_WORD.test(t) || SHIPPER_NAME.test(t) || NON_PLACE_RE.test(t) || /^[ァ-ヶー]{2,}$/.test(t);
    const cands = toks.filter((t) => !shipper(t));
    if (!cands.length || cands.length === toks.length) return part;
    return cands[cands.length - 1] + ' ' + part.slice(am.index);
  }

  // 配車表の 1 セルから区間を取り出す（own.py の parse_cell と同じ）。errors は区間を読めない・重さの記号・指数の重さ、warnings は時刻だけ読めない（区間は残し、arr を空にする）。
  // 矢印は → ⇒ => -> ～ 〜 >。区間は「／」「/」、セルの中の改行、「AM … PM …」の切れ目で分ける。降ろし地が無いとき（「加古川 → 4.6t」）は to を null にして、後ろの語を重さ・時刻として読む
  function parseCell(text) {
    const t = nfkc(text).trim();
    const res = { legs: [], nonleg: false, errors: [], warnings: [] };
    if (!t) return res;
    if (NONLEG.includes(t)) { res.nonleg = true; return res; }
    for (let part of t.split(LEG_SPLIT_RE)) {
      part = part.trim().replace(PERIOD_HEAD_RE, '').trim();
      if (!part) continue;
      const [preEmpty, part2] = splitCellPrefix(part);
      part = originOnly(part2);
      const m = ARROW_LEG_RE.exec(part);
      if (!m) {
        const lone = loneCell(part);
        if (!lone) { res.errors.push('区間が読めません'); continue; }
        res.legs.push(lone[0]);
        if (lone[1]) res.warnings.push(TIME_DROPPED);
        continue;
      }
      let frm = (m[1] || '').trim() || null, to = (m[2] || '').trim() || null, rest = m[3];
      let chain = []; // 「江東→加古川→大阪」: 降ろし地に矢印が続くときは、経由地ごとに区間を分ける（R7A-10）
      if (to && /->|=>|[→⇒>~〜]/.test(to)) {
        const hops = to.split(/->|=>|[→⇒>~〜]/).map((h) => h.trim());
        if (hops.length >= 2 && hops.every((h) => h)) { chain = hops; to = hops[0]; }
      }
      const tm = /^(.*?)(\d{1,2}:\d{2})$/.exec(to || ''); // 「江東5:30着」のように降ろし地に時刻が続いて書かれたとき
      if (tm && tm[1]) { to = tm[1]; rest = tm[2] + rest; }
      if (to != null && NON_PLACE_RE.test(to)) { rest = (to + ' ' + rest).trim(); to = null; } // 降ろし地が無く、重さか時刻が続いている
      else if (to != null && prefName(to)) { // 「東京 江東区 9t」: 都道府県のあとに市区町村が続くなら、市区町村を降ろし地にする
        const nx = /^\s*([^\s（(着]+)/.exec(rest);
        if (nx && !NON_PLACE_RE.test(nx[1]) && /[市区町村]/.test(nx[1])) { to = nx[1]; rest = rest.slice(nx[0].length); }
      }
      let empty = preEmpty || EMPTY_MARK.test(rest) || part.includes('(空)');
      let weak = false;
      if (!empty && WEAK_EMPTY.test(rest) && !parseWeights(rest).length) { empty = true; weak = true; } // 「江東 → 加古川 帰り」: 重さが無いときだけ空車の印として読む。弱い印なので weak_empty を付け、確認に出す
      if (to != null && isEmptyWord(to)) { to = null; empty = true; }
      if (to != null && !prefName(to)) to = prefSplit(to)[1] || to; // 「東京都江東区」の都道府県は読み飛ばす
      if (frm != null && !prefName(frm)) frm = prefSplit(frm)[1] || frm;
      const [arr, bad] = arrival(rest);
      if (bad) res.warnings.push(TIME_DROPPED);
      const ws = parseWeights(rest);
      const sign = ws.filter((w) => w.error);
      if (sign.length) res.errors.push(sign[0].error);
      const kgs = uniqNum(ws.filter((w) => w.kg != null).map((w) => w.kg));
      const leg = { frm, to, empty, arr, weight_kg: kgs.length === 1 ? kgs[0] : null };
      if (weak) leg.weak_empty = true;
      if (kgs.length > 1) leg.weights = kgs;
      const rm = ROUNDS_RE.exec(rest);
      if (rm && !empty) leg.rounds = +rm[1];
      if (!empty && LOADED_MARK.test(rest)) leg.loaded_mark = true;
      if (!empty) { const note = cellNote(rest); if (note) leg.note = note; }
      if (chain.length) {
        const seq = [leg.to].concat(chain.slice(1).map((x) => (prefName(x) ? x : prefSplit(x)[1] || x)));
        res.legs.push(Object.assign({}, leg, { to: seq[0], via: true, arr: null }));
        for (let k = 0; k < seq.length - 1; k++) res.legs.push(Object.assign({}, leg, { frm: seq[k], to: seq[k + 1], via: true, arr: k === seq.length - 2 ? leg.arr : null }));
        continue;
      }
      res.legs.push(leg);
    }
    return res;
  }

  /* ------------------------------------------------------------ デジタコ */

  // 列名の辞書。own.py の DIGITACO_DICT と DIGITACO_EXTRA と同じもの
  const DIGITACO_DICT = { KBN: 'kind', ST_TIME: 'start_time', ED_TIME: 'end_time', ODO_KM: 'distance_km', JK_FLG: 'loaded_flag', POS_NAME: 'place_name', DRV_CD: 'driver_code' };
  const DIGITACO_EXTRA = {
    '区分': 'kind', '開始日時': 'start_time', '開始時刻': 'start_time', '終了日時': 'end_time', '終了時刻': 'end_time',
    '走行距離': 'distance_km', '走行距離(KM)': 'distance_km', '実空区分': 'loaded_flag', '実車空車': 'loaded_flag', '実空': 'loaded_flag',
    '地点名': 'place_name', '停車地': 'place_name', '停車場所': 'place_name', '乗務員コード': 'driver_code', '運転者コード': 'driver_code',
    // SPEC §41: 別の会社のデジタコの明細（運行日・イベント・開始・終了・区間距離・状態・場所）と、日ごとの集計（日付・車番・実車距離・空車距離）
    '運行日': 'date', '日付': 'date', '年月日': 'date', '車番': 'plate', '車両番号': 'plate', 'ナンバー': 'plate',
    'イベント': 'kind', '種別': 'kind', '開始': 'start_time', '開始時間': 'start_time', '終了': 'end_time', '終了時間': 'end_time',
    '出庫': 'start_time', '出庫時刻': 'start_time', '帰庫': 'end_time', '帰庫時刻': 'end_time',
    '区間距離': 'distance_km', '距離': 'distance_km', '状態': 'loaded_flag', '場所': 'place_name', '地点': 'place_name',
    '運転者': 'driver_code', '乗務員': 'driver_code', '運転手': 'driver_code',
    '実車距離': 'loaded_km', '実車走行距離': 'loaded_km', '実車KM': 'loaded_km', '空車距離': 'empty_km', '空車走行距離': 'empty_km', '空車KM': 'empty_km',
    // SPEC §42: メーカーのソフトの日ごとの集計（単位つきの見出しは colKey の後で単位を外して引く）
    '車輌番号': 'plate', '車輌NO': 'plate', '車両NO': 'plate', '車番号': 'plate',
    '出庫日時': 'start_time', '帰庫日時': 'end_time', '実車キロ': 'loaded_km', '空車キロ': 'empty_km',
    '乗務員名': 'driver_code', '運転者名': 'driver_code', '運転手名': 'driver_code', '乗務員氏名': 'driver_code',
    // 日ごとの集計の「荷待ち時間」（「1:30」は 1 時間 30 分、数だけなら分）
    '拘束時間': 'restraint', '拘束': 'restraint', '総拘束時間': 'restraint',
    '荷待ち時間': 'wait_min', '荷待時間': 'wait_min', '荷待ち': 'wait_min', '待機時間': 'wait_min', '手待ち時間': 'wait_min', '手待時間': 'wait_min',
  };
  const HAISHA_DICT = { '車番': 'plate', '車種': 'vehicle_type' };
  // 区分の値（全角は NFKC で直してから比べる。own.py と同じ）。作業の明細（運転・荷積・荷卸・待機・休憩）は、運転を走行、それ以外を停車として読む
  const KIND_RUN = ['1', '走行', '運転', '運行', '移動'];
  const KIND_STOP = ['2', '停車', '荷積', '荷卸', '積込', '積込み', '取卸', '取卸し', '積み', '卸し', '荷役', '待機', '荷待ち', '手待ち', '休憩', '休息', '仮眠', '給油', '点検'];
  const KIND_WAIT = ['待機', '荷待ち', '手待ち']; // 停車のうち、荷待ちの候補
  const JK_LOADED = ['1', '実', '実車'], JK_EMPTY = ['0', '空', '空車'];
  // 利用者が「確認」や列の役割の選択で決めた列の役割（{大文字にした列名: 役割か null（読まない）}）。analyze の間だけ入る。辞書より先に使う
  let ROLE_OV = null;
  const colKey = (name) => nfkc(name).trim().toUpperCase();
  // 辞書を引く列名の形。そのまま、末尾の単位「(km)」「[時間]」を外したもの、さらに空白を外したもの（own.py の dict_key_variants と同じ）
  const UNIT_SUFFIX = /\s*[([【]\s*(?:KM|キロ|キロメートル|時間|分|H|HOUR|MIN|M)\s*[)\]】]\s*$/;
  const dictKeys = (k) => {
    const out = [k], a = k.replace(UNIT_SUFFIX, '');
    for (const v of [a, a.replace(/\s+/g, '')]) if (v && !out.includes(v)) out.push(v);
    return out;
  };
  const roleOf = (name, ov = ROLE_OV) => {
    const k = colKey(name);
    if (ov && Object.prototype.hasOwnProperty.call(ov, k)) return ov[k] === 'skip' ? null : ov[k];
    for (const v of dictKeys(k)) {
      const r = Object.prototype.hasOwnProperty.call(DIGITACO_DICT, v) ? DIGITACO_DICT[v] : Object.prototype.hasOwnProperty.call(DIGITACO_EXTRA, v) ? DIGITACO_EXTRA[v] : null;
      if (r) return r;
    }
    return null;
  };
  const roleOfDict = (name) => roleOf(name, null);
  // 「修正」で入れた文字（役割の名前や、辞書にある列名）を役割にする。分からなければ null
  function resolveRole(text) {
    const t = nfkc(text).trim();
    if (!t) return null;
    if (Object.values(DIGITACO_DICT).includes(t)) return t;
    const k = t.toUpperCase();
    if (DIGITACO_DICT[k] || DIGITACO_EXTRA[k]) return DIGITACO_DICT[k] || DIGITACO_EXTRA[k];
    for (const [role, label] of Object.entries(ROLE_LABEL)) {
      if (role === 'plate' || role === 'vehicle_type' || role === 'date') continue; // デジタコの列に当てられるのは、デジタコの役割だけ
      if (t === label || t === label.replace(/（.*?）/g, '')) return role;
    }
    return null;
  }

  const END_BEFORE_START = '終了が開始より前（または同じ）です';
  // 走行距離の文字を km の数にする。全角・カンマ・末尾の km を直し、数字と小数点だけなら数、そうでなければ null（own.py の parse_km と同じ）
  function parseKm(s) {
    const t = nfkc(s).replace(/,/g, '').replace(/km$/i, '').trim();
    return /^[0-9]+(\.[0-9]+)?$/.test(t) ? Number(t) : null;
  }
  // 日付だけの文字（2026/10/6、2026-10-06、2026年10月6日。時刻が続いてもよい）。年が無いものは null。戻り値は 'YYYY-MM-DD'
  function parseDateOnly(s) {
    const p = dateHeadParts(s == null ? '' : String(s));
    return p == null || p.y == null ? null : isoDate(p.y, p.m, p.d);
  }
  // 「20:10」「5:30:15」を 0 時からの分にする（秒は捨てる）。読めなければ null
  function parseHm(s) {
    const m = /^(\d{1,2}):(\d{2})(?::\d{2})?$/.exec(nfkc(s == null ? '' : s).trim());
    if (!m) return null;
    const h = +m[1], mi = +m[2];
    return h < 24 && mi < 60 ? h * 60 + mi : null;
  }
  const midnight = (iso) => dateMs(iso) / 60000; // 'YYYY-MM-DD' → 分（parseDT と同じ尺度）

  // rows[0] が見出しの行。役割 → 列の番号
  function digitacoColumns(rows, roles) {
    const col = {};
    (rows[0] || []).forEach((h, i) => { const r = roleOf(h || '', roles === undefined ? ROLE_OV : roles); if (r) col[r] = i; });
    return col;
  }

  function parseDigitaco(rows, resolve, plate, roles, lineOffset = 0) {
    const col = digitacoColumns(rows, roles);
    const runs = [], stops = [], errors = [];
    let prevStop = null, openRun = null;
    let stD0 = null, stEnd = null; // 時刻だけの行のための、直前の行の運行日と終了
    let lastPlate;
    const get = (r, role) => (col[role] != null ? (r[col[role]] || '').trim() : '');
    rows.slice(1).forEach((r, k) => {
      const i = k + 2 + lineOffset;
      if (blankRow(r)) return;
      let rowPlate = plate;
      if (col.plate != null) { // 車番の列があれば、ファイル名の車番は要らない
        rowPlate = normalizePlate(get(r, 'plate')) || plate;
        if (rowPlate == null) { errors.push({ line: i, reason: '車番が読めません' }); return; }
      }
      if (rowPlate !== lastPlate) { prevStop = null; openRun = null; stD0 = null; stEnd = null; lastPlate = rowPlate; }
      const sTxt = get(r, 'start_time'), eTxt = get(r, 'end_time');
      let st = parseDT(sTxt), ed = parseDT(eTxt);
      const d0 = col.date != null ? parseDateOnly(get(r, 'date')) : null;
      if ((st == null || ed == null) && d0 != null) {
        if (st == null && parseHm(sTxt) != null) {
          let base = midnight(d0) + parseHm(sTxt);
          if (stD0 === d0 && stEnd != null) for (let n = 0; n < 3; n++) if (base < stEnd) base += 1440;
          st = base;
        }
        if (ed == null && st != null && parseHm(eTxt) != null) {
          const e = Math.floor(st / 1440) * 1440 + parseHm(eTxt);
          ed = e < st ? e + 1440 : e;
        }
      }
      if (st == null || ed == null) { errors.push({ line: i, reason: '時刻が読めません' }); return; }
      if (ed <= st) { errors.push({ line: i, reason: END_BEFORE_START }); return; }
      stD0 = d0; stEnd = ed;
      const kindRaw = nfkc(get(r, 'kind'));
      const kind = KIND_RUN.includes(kindRaw) ? '1' : KIND_STOP.includes(kindRaw) ? '2' : kindRaw;
      const driver = get(r, 'driver_code') || null;
      if (kind === '1') {
        const kmS = get(r, 'distance_km');
        const jkS = nfkc(get(r, 'loaded_flag'));
        let km = null;
        if (kmS) { km = parseKm(kmS); if (km == null) errors.push({ line: i, reason: '距離が読めません' }); }
        const run = {
          plate: rowPlate, start: isoOf(st), end: isoOf(ed), km,
          jk: JK_LOADED.includes(jkS) ? 1 : JK_EMPTY.includes(jkS) ? 0 : null,
          frm: prevStop ? prevStop.place : null, to: null, driver, line: i,
        };
        if (openRun) openRun.cont = true; // 停車の行をはさまず次の走行が始まった（長距離の途中の休息。link で 1 区間にまとめる）
        runs.push(run); openRun = run;
      } else if (kind === '2') {
        const name = get(r, 'place_name');
        const place = name ? resolve(name) : null;
        const stop = { plate: rowPlate, place, name, start: isoOf(st), end: isoOf(ed), minutes: pyRound(ed - st), driver, line: i };
        if (KIND_WAIT.includes(kindRaw)) stop.wait = true;
        stops.push(stop);
        if (openRun) { openRun.to = place; openRun = null; }
        prevStop = stop;
      } else errors.push({ line: i, reason: '区分が読めません' });
    });
    return { runs, stops, errors };
  }

  // 日ごとの集計の「荷待ち時間」を分にする。「1:30」は 90 分、「45」「45分」は 45 分、「1.5時間」は 90 分。読めなければ null（own.py の parse_wait_min と同じ）
  function parseWaitMin(s) {
    const t = nfkc(s == null ? '' : s).trim().replace(/,/g, '');
    const hm = parseHm(t);
    if (hm != null) return hm;
    const m = /^([0-9]+(?:\.[0-9]+)?)\s*(時間|h|H|分|min|m)?$/.exec(t);
    if (!m) return null;
    const v = parseFloat(m[1]);
    return pyRound(['時間', 'h', 'H'].includes(m[2]) ? v * 60 : v);
  }

  // 日ごとの集計の「拘束時間」を時間（小数）にする。「13:10」は 13.1667、「13時間10分」「13.5時間」、数だけなら 24 以下は時間・それより大きければ分。読めなければ null（own.py の parse_restraint_hours と同じ）
  function parseRestraintHours(s) {
    const t = nfkc(s == null ? '' : s).trim().replace(/,/g, '');
    let m = /^([0-9]{1,3}):([0-9]{2})(?::[0-9]{2})?$/.exec(t); // 拘束時間は 24 時間を超えうる（「34:10」）ので、時を 3 桁まで読む
    if (m && +m[2] < 60) return pyRound((+m[1] * 60 + +m[2]) / 60, 4);
    m = /^([0-9]+)\s*時間\s*(?:([0-9]+)\s*分?)?$/.exec(t);
    if (m) return pyRound(parseInt(m[1], 10) + parseInt(m[2] || '0', 10) / 60, 4);
    m = /^([0-9]+(?:\.[0-9]+)?)\s*(時間|h|H|分|min|m)?$/.exec(t);
    if (!m) return null;
    const v = parseFloat(m[1]);
    if (['分', 'min', 'm'].includes(m[2]) || (m[2] == null && v > 24)) return pyRound(v / 60, 4);
    return pyRound(v, 4);
  }

  // 日ごとの集計の形（日付・車番・実車距離・空車距離。出庫・帰庫があれば読む）を、実車の走行と空車の走行に直す（own.py の parse_digitaco_summary と同じ）
  function parseDigitacoSummary(rows, plate, roles, lineOffset = 0) {
    const col = digitacoColumns(rows, roles);
    const runs = [], errors = [], waits = [], restraints = [];
    const get = (r, role) => (col[role] != null ? (r[col[role]] || '').trim() : '');
    rows.slice(1).forEach((r, k) => {
      const i = k + 2 + lineOffset;
      if (blankRow(r)) return;
      const d0 = parseDateOnly(get(r, 'date'));
      if (d0 == null) { errors.push({ line: i, reason: '日付が読めません' }); return; }
      const pl = col.plate != null ? normalizePlate(get(r, 'plate')) : (plate == null ? null : plate);
      if (pl == null) { errors.push({ line: i, reason: '車番が読めません' }); return; }
      const wtxt = get(r, 'wait_min');
      if (wtxt) {
        const wm = parseWaitMin(wtxt);
        if (wm == null) errors.push({ line: i, reason: '荷待ち時間が読めません' });
        else if (wm > 0) waits.push({ plate: pl, date: d0, minutes: wm, line: i });
      }
      const rtxt = get(r, 'restraint');
      if (rtxt) {
        const rh = parseRestraintHours(rtxt);
        if (rh == null) errors.push({ line: i, reason: '拘束時間が読めません' });
        else if (rh > 0) restraints.push({ plate: pl, date: d0, hours: rh, line: i, driver: get(r, 'driver_code') || null });
      }
      const kms = [];
      for (const [role, jk] of [['loaded_km', 1], ['empty_km', 0]]) {
        const txt = get(r, role);
        if (!txt) continue;
        const v = parseKm(txt);
        if (v == null) errors.push({ line: i, reason: '距離が読めません' });
        else if (v > 0) kms.push([v, jk]);
      }
      const sm = parseHm(get(r, 'start_time')), em = parseHm(get(r, 'end_time'));
      const base = midnight(d0);
      const sdt = parseDT(get(r, 'start_time')), edt = parseDT(get(r, 'end_time'));
      let st, ed, known;
      if (sdt != null && edt != null && edt > sdt) { st = sdt; ed = edt; known = true; } // 「出庫日時」「帰庫日時」のように日付つきの日時
      else if (sm != null && em != null && sm !== em) { st = base + sm; ed = base + em + (em < sm ? 1440 : 0); known = true; } else { st = ed = base; known = false; }
      for (const [v, jk] of kms) runs.push({ plate: pl, start: isoOf(st), end: isoOf(ed), km: v, jk, frm: null, to: null, driver: get(r, 'driver_code') || null, line: i, summary: true, time_unknown: !known });
    });
    return { runs, stops: [], errors, waits, restraints };
  }

  // デジタコの形と見出しの行を、先頭の 10 行から探す。detail: 開始・終了の列があり、役割の付いた列が 3 つ以上。summary: 日付の列と、実車距離か空車距離の列がある
  function detectDigitaco(rows, roles) {
    for (let i = 0; i < Math.min(HEADER_SCAN_ROWS, rows.length); i++) {
      const rm = {};
      rows[i].forEach((c, j) => { if ((c || '').trim()) { const r = roleOf(c, roles === undefined ? ROLE_OV : roles); if (r) rm[j] = r; } });
      const rs = new Set(Object.values(rm));
      if (rs.has('date') && (rs.has('loaded_km') || rs.has('empty_km'))) return { kind: 'summary', header_row: i, roles: rm };
      if (rs.has('start_time') && rs.has('end_time') && rs.size >= 3) return { kind: 'detail', header_row: i, roles: rm };
    }
    return null;
  }

  // 同じ走行・停車の行を 1 つにする（own.py の dedupe_digitaco と同じ）。parts: [{file, plate, runs, stops}]。
  // 走行は (車番, 開始, 終了, 距離)、停車は (車番, 開始, 終了, 地点名) が同じなら、あとから出たものを捨てる
  function dedupeDigitaco(parts) {
    const seenR = new Set(), seenS = new Set();
    const dropped = { runs: 0, stops: 0, by_file: {} };
    const out = parts.map((p) => {
      const runs = [], stops = [];
      let dr = 0, ds = 0;
      for (const r of p.runs) { const k = JSON.stringify([r.plate, r.start, r.end, r.km]); if (seenR.has(k)) dr++; else { seenR.add(k); runs.push(r); } }
      for (const s of p.stops) { const k = JSON.stringify([s.plate, s.start, s.end, s.name]); if (seenS.has(k)) ds++; else { seenS.add(k); stops.push(s); } }
      dropped.runs += dr; dropped.stops += ds;
      if (dr || ds) dropped.by_file[p.file] = { runs: dr, stops: ds };
      return { ...p, runs, stops };
    });
    return { parts: out, dropped };
  }

  /* ------------------------------------------------------------ 列の役割（利用者が選ぶ）とその説明 */

  const ROLE_LABEL_JA = {
    date: '日付', plate: '車番', type: '車種・トン数', from: '荷を積む場所（発地）', to: '荷を降ろす場所（着地）', weight: '重さ', time: '着く時刻', dep_time: '出発の時刻',
    mark: '実空の印がある列', skip: '使わない', kind: '走行か停車か', start_time: '開始の日時', end_time: '終了の日時', distance_km: '走った距離（km）',
    loaded_flag: '実車か空車か', place_name: '停車した場所', driver_code: '運転者', loaded_km: '実車で走った距離（km）', empty_km: '空車で走った距離（km）', wait_min: '荷待ちの時間（分または 時:分）', restraint: '拘束時間（時:分または 時間）',
  };
  const HAISHA_ROLE_OPTIONS = ['date', 'plate', 'from', 'to', 'weight', 'time', 'dep_time', 'distance_km', 'mark', 'type', 'skip'];
  const DIGITACO_ROLE_OPTIONS = ['date', 'plate', 'kind', 'start_time', 'end_time', 'distance_km', 'loaded_flag', 'place_name', 'driver_code', 'loaded_km', 'empty_km', 'wait_min', 'restraint', 'skip'];
  // source: '配車表'、'デジタコ'、それ以外（どちらか分からないファイル。両方の選択肢を重ならないように並べる）
  function roleOptions(source) {
    const roles = source === '配車表' ? HAISHA_ROLE_OPTIONS : source === 'デジタコ' ? DIGITACO_ROLE_OPTIONS
      : uniqNum(HAISHA_ROLE_OPTIONS.filter((r) => r !== 'skip').concat(DIGITACO_ROLE_OPTIONS));
    return roles.map((role) => ({ role, label_ja: ROLE_LABEL_JA[role] }));
  }
  // 役割が決まらなかった列（見出しがあり、中身のある列）。見本の値（3 つまで）と選択肢を付けて返す。blocking が真のときは、その列を決めないとファイルが読めない
  function needingRoles(source, file, head, body, roleFn, used, blocking, headerLine, dictFn) {
    const out = [], taken = new Set();
    head.forEach((h0, j) => {
      const h = (h0 || '').trim();
      if (!h || used.has(j) || roleFn(h)) return;
      const vals = [];
      for (const r of body) { const v = ((j < r.length ? r[j] : '') || '').trim(); if (v && !vals.includes(v)) vals.push(v); }
      if (!vals.length) return;
      let [guess, ratio] = bestRoleFor(vals);
      const dr = dictFn ? dictFn(h) : null;
      if (dr) { guess = dr; ratio = 1; } // 見出しが辞書に当たる列は、その役割を候補にする
      else if (guess === 'plate' && shapeRatio(vals, 'date_header') >= ratio) guess = 'date'; // 日付の形の値は、4 桁の数字をふくんでも車番より日付を先に候補にする
      if (guess === 'start_time' && taken.has('start_time')) guess = 'end_time'; // 日時の列が 2 つ並ぶときは、2 つ目を終了にする
      const ok = ratio >= 0.6 && guess in ROLE_LABEL_JA && blocking;
      if (ok) taken.add(guess);
      out.push({
        source, file, column: h, key: colKey(h), samples: vals.slice(0, 3), blocking, line: headerLine,
        suggested_role: ok ? guess : null, suggested_label_ja: ok ? ROLE_LABEL_JA[guess] : null, options: roleOptions(source),
      });
    });
    return out;
  }
  // ファイルの形の目印。種類と見出しの名前から作り、利用者が列の役割を選ぶ前と後で変わらない。日付の見出しは「日付」とみなして月が変わっても同じにする
  function shapeSignature(kind, head) {
    const keys = uniqNum(head.filter((h) => (h || '').trim()).map((h) => (dateHeadParts(h || '') ? '<日付>' : colKey(h)))).sort();
    return kind + '|' + keys.join('|');
  }

  /* ------------------------------------------------------------ 結び付け・実車と空車 */

  const endsOfLeg = (l) => [...new Set([l.frm, l.to].filter((x) => x))];
  const hm = (s) => +s.slice(0, 2) * 60 + +s.slice(3, 5);
  const timeDiff = (hint, iso) => {
    const a = +iso.slice(11, 13) * 60 + +iso.slice(14, 16);
    return ((((hm(hint) - a + 720) % 1440) + 1440) % 1440) - 720;
  };

  // 停車の行をはさまず 8〜16 時間あいて続く走行（1 人の運転者が途中で休息した長距離）を 1 つの区間にまとめる。own.py の _join_rest_runs と同じ
  const REST_JOIN_MIN = SHIFT_GAP_MIN, REST_JOIN_MAX = 16 * 60;
  function joinRestRuns(run, nxt, used) {
    let cur = run; const parts = [run];
    while (cur.cont && nxt.has(cur.id) && !used.has(nxt.get(cur.id).id)) {
      const n = nxt.get(cur.id);
      const gap = (Date.parse(n.start + ':00Z') - Date.parse(cur.end + ':00Z')) / 60000;
      if (!(gap >= REST_JOIN_MIN && gap <= REST_JOIN_MAX)) break;
      used.add(n.id); parts.push(n); cur = n;
    }
    if (parts.length === 1) return run;
    const anyNull = parts.some((q) => q.km == null);
    return { ...run, end: parts[parts.length - 1].end, to: parts[parts.length - 1].to, km: anyNull ? null : pyRound(parts.reduce((a, q) => a + q.km, 0), 1), joined: parts.map((q) => q.id) };
  }

  const KM_FILE_SRC = 'ファイルの走行km';
  // estFn(ends の配列) は { km, src } か null を返す
  function link(haisha, digitaco, estFn) {
    const byKey = new Map();
    for (const d of digitaco) { const k = d.plate + '|' + d.start.slice(0, 10); if (!byKey.has(k)) byKey.set(k, []); byKey.get(k).push(d); }
    const used = new Set(), legs = [];
    const nxt = new Map();
    for (let i = 0; i + 1 < digitaco.length; i++) if (digitaco[i + 1].plate === digitaco[i].plate) nxt.set(digitaco[i].id, digitaco[i + 1]);
    const hs = haisha.map((h, i) => [h, i]).sort((a, b) => cmp(a[0].date, b[0].date) || cmp(a[0].plate, b[0].plate) || cmp(a[0].idx || 0, b[0].idx || 0) || a[1] - b[1]).map((x) => x[0]);
    const endsOf = (a, b) => [...new Set([a, b].filter((x) => x))];
    for (const h of hs) {
      const cands = (byKey.get(h.plate + '|' + h.date) || []).filter((d) => !used.has(d.id));
      let pick = null;
      if (cands.length) {
        pick = cands[0];
        if (h.arr && cands.length > 1) {
          let best = Infinity;
          for (const d of cands) { const v = Math.abs(timeDiff(h.arr, d.end)); if (v < best) { best = v; pick = d; } }
        }
        used.add(pick.id);
        pick = joinRestRuns(pick, nxt, used);
      }
      const p = pick || {};
      const ends = endsOf(h.frm || p.frm, h.to || p.to);
      const est = ends.length === 2 ? estFn(ends) : null;
      const leg = {
        plate: h.plate, date: h.date, hid: h.id, did: pick ? pick.id : null,
        frm: h.frm || p.frm || null, to: h.to || p.to || null,
        empty_mark: !!h.empty, ...(h.weak_empty ? { weak_empty: true } : {}), has_ends: !!(h.frm && h.to), local: !!h.local, loaded_mark: !!h.loaded_mark, round: h.round || 1, rounds: h.rounds || 1, weight_kg: h.weight_kg == null ? null : h.weight_kg, weights_multi: h.weights && h.weights.length ? h.weights : null, jk: pick ? pick.jk : null, driver: pick ? pick.driver : null,
        dep: pick ? pick.start : null, arr: pick ? pick.end : null, arr_hint: h.arr || null,
        flags: [], sources: {}, ref: h.ref || null, dref: pick ? pick.ref || null : null,
      };
      if (h.note) leg.note = h.note; // 配車表のセルに付いていた、重さ・時刻・印のどれでもない語
      if (pick && pick.joined) leg.dids = pick.joined; // 途中の休息でまとめた走行の ID すべて（停車に曜日を付けるときに使う）
      if (pick && pick.km != null) {
        leg.km = pick.km; leg.sources.km = 'デジタコ';
        if (est) {
          const ratio = pyRound(Math.abs(pick.km - est.km) / est.km, 9);
          if (ratio >= LINK_DIST_RATIO) leg.flags.push({ type: 'distance', ratio: pyRound(ratio, 4), digitaco_km: pick.km, est_km: est.km });
        }
      } else if (h.km_file != null) { leg.km = h.km_file; leg.sources.km = KM_FILE_SRC; // ファイルの走行距離があれば、推定は使わない
      } else if (est) { leg.km = est.km; leg.sources.km = est.src; } else { leg.km = null; leg.sources.km = null; }
      if (pick) {
        leg.sources.time = 'デジタコ';
        if (h.arr) {
          const diff = timeDiff(h.arr, pick.end);
          if (Math.abs(diff) >= LINK_TIME_MIN) leg.flags.push({ type: 'time', diff_min: diff, hint: h.arr, digitaco: pick.end.slice(11, 16) });
        }
      } else leg.sources.time = h.arr ? '配車表' : null;
      leg.sources.kind = null;
      legs.push(leg);
    }
    const onlyD = [];
    for (const d of digitaco) {
      if (used.has(d.id)) continue;
      const ends = endsOf(d.frm, d.to), est = ends.length === 2 ? estFn(ends) : null;
      onlyD.push({
        plate: d.plate, date: d.start.slice(0, 10), hid: null, did: d.id, frm: d.frm || null, to: d.to || null,
        empty_mark: false, has_ends: !!(d.frm && d.to), weight_kg: null, jk: d.jk, driver: d.driver, ...(d.summary ? { summary: true } : {}),
        dep: d.start, arr: d.end, arr_hint: null, flags: [], ref: null, dref: d.ref || null,
        km: d.km != null ? d.km : est ? est.km : null,
        sources: { km: d.km != null ? 'デジタコ' : est ? est.src : null, time: 'デジタコ', kind: null, note: '配車表なし' },
      });
    }
    return { legs: legs.concat(onlyD), digitaco_only: onlyD.length, haisha_only: legs.filter((x) => x.did == null).length };
  }

  const RETURN_UNMARKED_SRC = '車庫へ戻る区間で、空の印も重さも無い';
  const WEAK_EMPTY_SRC = '「帰り」「戻り」だけで、空車とも実車とも書かれていない';
  // base（自社の車庫）に戻る配車表の区間で、空車の印も重さも無いものは実車にせず不明にする（return_unmarked を真にして確認に出す）。地場の配送と、デジタコと結び付いた区間は対象外
  function classifyLegs(legs, base) {
    const out = [], counts = { loaded: 0, empty: 0, unknown: 0 };
    for (const l0 of legs) {
      const l = { ...l0 };
      let kind = 'unknown', src = null;
      if (l.jk === 0 || l.jk === 1) { kind = l.jk === 1 ? 'loaded' : 'empty'; src = 'デジタコの実空区分'; }
      else if (l.empty_mark && l.weak_empty) { kind = 'unknown'; src = WEAK_EMPTY_SRC; l.return_unmarked = true; } // 「帰り」「戻り」だけの印は、空車と決めずに確認に出す
      else if (l.empty_mark) { kind = 'empty'; src = '配車表の（空）の印'; }
      else if (l.has_ends) {
        if (base && l.to === base && l.frm !== base && l.hid != null && l.weight_kg == null && !(l.weights_multi && l.weights_multi.length) && !l.local && !l.loaded_mark) {
          kind = 'unknown'; src = RETURN_UNMARKED_SRC; l.return_unmarked = true;
        } else { kind = 'loaded'; src = '配車表の積地・降ろし地'; }
      }
      else if (l.invoiced) { kind = 'loaded'; src = '請求書の区間'; }
      else if (l.log_loaded === true || l.log_loaded === false) { kind = l.log_loaded ? 'loaded' : 'empty'; src = '業務記録の荷役'; }
      l.kind = kind; l.kind_src = src;
      l.sources = { ...(l.sources || {}), kind: src };
      counts[kind]++;
      out.push(l);
    }
    return { legs: out, counts, unknown_ratio: out.length ? counts.unknown / out.length : 0.0 };
  }

  /* ------------------------------------------------------------ 列の対応づけの確かさ */

  function confidence(dictHit, shapeRatio, claudeAgrees) {
    const m = dictHit || claudeAgrees ? 1.0 : 0.0;
    return pyRound(0.5 * m + 0.5 * Number(shapeRatio), 9);
  }
  function confidenceColor(c) {
    c = pyRound(c, 9);
    return c >= CONF_GREEN ? 'green' : c >= CONF_YELLOW ? 'yellow' : 'red';
  }
  const KANA_KANJI = /^[぀-ヿ一-鿿々]+$/;
  const VALUE_SHAPES = {
    datetime: ['YYYY-MM-DD HH:MM の日時', (v) => /^[0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2}[ T][0-9]{1,2}:[0-9]{2}(:[0-9]{2})?$/.test(v)],
    date_header: ['月/日 の日付の見出し', (v) => /^[0-9]{1,2}\/[0-9]{1,2}(\(.\))?$/.test(v) || /^[0-9]{1,2}月[0-9]{1,2}日(\(.\))?$/.test(v) || /^[0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2}(\(.\))?$/.test(v)],
    flag01: ['0 か 1', (v) => ['0', '1', ...JK_LOADED, ...JK_EMPTY].includes(v)],
    code12: ['1 か 2', (v) => ['1', '2', ...KIND_RUN, ...KIND_STOP].includes(v)],
    driver_code: ['英大文字 1 字と数字 2 桁', (v) => /^[A-Z][0-9]{2}$/.test(v)],
    plate: ['4 桁の番号をふくむ車番', (v) => normalizePlate(v) != null],
    vehicle_type: ['「大型」か「数字 + t」', (v) => /^(大型|[0-9]+t)$/.test(v)],
    decimal: ['小数まじりの数字', (v) => /^[0-9]+(\.[0-9]+)?$/.test(v)],
    place_kanji: ['漢字・かなだけの名称', (v) => KANA_KANJI.test(v)],
    wait_min: ['分か 時:分の数', (v) => parseWaitMin(v) != null],
    restraint: ['時:分か時間の数', (v) => parseRestraintHours(v) != null],
  };
  const ROLE_SHAPE = {
    kind: 'code12', start_time: 'datetime', end_time: 'datetime', distance_km: 'decimal', loaded_flag: 'flag01',
    place_name: 'place_kanji', driver_code: 'driver_code', plate: 'plate', vehicle_type: 'vehicle_type', date: 'date_header',
    loaded_km: 'decimal', empty_km: 'decimal', wait_min: 'wait_min', restraint: 'restraint',
  };
  const ROLE_LABEL = {
    kind: '区分（走行・停車）', start_time: '開始の日時', end_time: '終了の日時', distance_km: '走行距離（km）',
    loaded_flag: '実車・空車の区分', place_name: '停車の地点名', driver_code: '運転者コード', plate: '車番',
    vehicle_type: '車種', date: '日付', loaded_km: '実車で走った距離（km）', empty_km: '空車で走った距離（km）', wait_min: '荷待ちの時間', restraint: '拘束時間',
  };
  const nonBlank = (values) => values.map((v) => (v || '').trim()).filter((v) => v);
  const NFKC_SHAPES = ['datetime', 'date_header', 'flag01', 'code12', 'decimal', 'driver_code']; // 全角を直してから当てる形（車種と地点名は、サンプルの黄色の例のため元の文字のまま。own.py と同じ）
  function shapeRatio(values, key) {
    const vs = values.map((v) => (NFKC_SHAPES.includes(key) ? nfkc(v) : String(v == null ? '' : v)).trim()).filter((v) => v);
    if (!vs.length) return 0.0;
    const ok = VALUE_SHAPES[key][1];
    return vs.filter(ok).length / vs.length;
  }
  function bestRoleFor(values) {
    let best = ['', 0.0];
    for (const [role, key] of Object.entries(ROLE_SHAPE)) {
      if (role === 'wait_min' || role === 'restraint') continue; // 値の形だけでは、ほかの時刻の列と区別できないので、列の名前が辞書に当たったときだけ使う
      const r = shapeRatio(values, key);
      if (r > best[1]) best = [role, r];
    }
    return best;
  }

  /* ------------------------------------------------------------ 拘束時間 */

  function kousokuHours(days) {
    const ds = days.map((d) => ({ start: parseDT(d.start), end: parseDT(d.end), src: d })).sort((a, b) => a.start - b.start);
    return ds.map((d, i) => {
      const w0 = d.start, w1 = d.start + 24 * 60;
      let total = 0.0;
      for (const e of ds.slice(i)) {
        const a = Math.max(e.start, w0), b = Math.min(e.end, w1);
        if (b > a) total += ((b - a) * 60) / 3600;
      }
      const rest = {};
      for (const [k, v] of Object.entries(d.src)) if (k !== 'start' && k !== 'end') rest[k] = v;
      return { ...rest, start: isoOf(d.start), end: isoOf(d.end), hours: pyRound(total, 4) };
    });
  }

  function buildShifts(events, gapMin = SHIFT_GAP_MIN) {
    const evs = events.slice().sort((a, b) => cmp(a.start, b.start));
    const shifts = [];
    for (const e of evs) {
      const s = parseDT(e.start), t = parseDT(e.end);
      if (shifts.length && s - parseDT(shifts[shifts.length - 1].end) < gapMin) {
        const last = shifts[shifts.length - 1];
        if (t > parseDT(last.end)) last.end = isoOf(t);
        last.lines.push(e.line == null ? null : e.line);
      } else shifts.push({ start: isoOf(s), end: isoOf(t), lines: [e.line == null ? null : e.line] });
    }
    return shifts;
  }

  /* ------------------------------------------------------------ 数字と表 */

  // 積んだ重さが、その車の最大積載量を超える区間。積載率・積載効率の平均には入れず、確認のカードと気づきに出す（own.py の over_max_legs と同じ）
  function overMaxLegs(legs, vehicles) {
    const cap = {};
    for (const v of vehicles) cap[v.plate] = v.max_load_kg;
    return legs.filter((l) => l.weight_kg && cap[l.plate] && l.weight_kg > cap[l.plate]);
  }
  const tons = (kg) => fix(kg / 1000, 1);
  // 2 つの重さを、丸めても違いが見える桁数で文字にする（「2,700kg が 2,700kg を超えます」にならないように。own.py の tons_pair と同じ）。単位をふくむ文字を返す
  function tonsPair(aKg, bKg) {
    for (const nd of [1, 2, 3]) {
      const x = fix(aKg / 1000, nd), y = fix(bKg / 1000, nd);
      if (x !== y) return [x + 't', y + 't'];
    }
    const f2 = (v) => { const t = fix(v, 2), i = t.indexOf('.'); return Number(t.slice(0, i)).toLocaleString('en-US') + t.slice(i) + 'kg'; };
    return [f2(aKg), f2(bKg)];
  }
  // 配車表に「0t」と書いてある実車の区間（重さが空欄の区間は入れない）と、1 つのセルに重さが 2 つ以上ある区間
  const zeroWeightLegs = (legs) => legs.filter((l) => l.kind === 'loaded' && l.weight_kg != null && l.weight_kg === 0);
  const multiWeightLegs = (legs) => legs.filter((l) => l.weights_multi && l.weights_multi.length);
  // 重さの確認のカード（id は呼び出し側が付ける。own.py の weight_confirm_items と同じ）。区間ごとに 1 枚。重さが複数、実車で 0t、最大積載量を超える重さ、の順に当てはめる
  function weightConfirmItems(legs, vehicles) {
    const cap = {};
    for (const v of vehicles) cap[v.plate] = v.max_load_kg;
    const over = new Set(overMaxLegs(legs, vehicles)), zero = new Set(zeroWeightLegs(legs)), multi = new Set(multiWeightLegs(legs));
    const out = [];
    for (const l of legs) {
      if (!over.has(l) && !zero.has(l) && !multi.has(l)) continue;
      const item = { id: '', key: `weight:${l.id}`, effect: 'kpi', kind: 'weight', source: '配車表', title: `${md(l.date)} 車番 ${l.plate} の積んだ重さ`, confidence: null, color: 'yellow', leg: l.id, claude: null };
      if (multi.has(l)) {
        Object.assign(item, {
          original: `1 つのセルに重さが ${l.weights_multi.length} つ書いてあります（${l.weights_multi.map((k) => tons(k) + 't').join('、')}）`,
          proposal: 'どれも使わず、重さなしで数える（正しい重さを「修正」で入れると、積載率に入れます）',
          formula: '重さが 2 つ以上あるセルは、どれが積荷の重さか分からないので、率に入れません',
        });
      } else if (zero.has(l)) {
        Object.assign(item, {
          original: '配車表の重さが 0t の実車の区間です',
          proposal: '積載率・積載効率に入れない（積んでいないか、重さの書き忘れかもしれません）',
          formula: '0t の実車の区間は、率に入れません',
        });
      } else {
        const [a, b] = tonsPair(l.weight_kg, cap[l.plate]);
        const vv = vehicles.find((x) => x.plate === l.plate) || {};
        Object.assign(item, {
          over_max: true, plate: l.plate, cls: vv.cls || null, cap_kg: cap[l.plate], cap_assumed: !!vv.max_load_assumed, cap_src: vv.max_load_src || (vv.max_load_assumed ? 'assumed' : null),
          original: `配車表の重さ ${a} が、最大積載量 ${b} を超えています`,
          proposal: '積載率・積載効率に入れない（t と kg の取り違えや、桁の間違いかもしれません）',
          formula: '積んだ重さが最大積載量を超える区間は、率に入れません',
        });
      }
      out.push(item);
    }
    return out;
  }

  function kpis(legs, vehicles) {
    const cls = {};
    for (const v of vehicles) cls[v.plate] = v;
    const overSet = new Set(overMaxLegs(legs, vehicles));
    const n = legs.length;
    const isJudged = (l) => l.kind === 'loaded' || l.kind === 'empty'; // 実車か空車かが分かった区間。距離の有無とは関係ない（率の計算には距離のある区間だけが入る）
    const judged = legs.filter(isJudged);
    const sum = (ls) => ls.reduce((a, l) => a + (l.km || 0), 0);
    const rate = (ls) => { const tot = sum(ls); return tot ? sum(ls.filter((l) => l.kind === 'loaded')) / tot : null; };
    const large = legs.filter((l) => (cls[l.plate] || {}).cls === '大型');
    const largeJudged = large.filter(isJudged);
    const withW = large.filter((l) => l.kind === 'loaded' && l.weight_kg && cls[l.plate].max_load_kg && !overSet.has(l));
    const loadRate = withW.length ? withW.reduce((a, l) => a + l.weight_kg / cls[l.plate].max_load_kg, 0) / withW.length : null;
    const largeRate = rate(largeJudged);
    // 実車か空車か分からない区間（不明）を空車とした場合の率。不明の区間は km があるものだけが入る（km が無い区間は、どちらにしても率に入らない）
    const withKm = legs.filter((l) => (l.kind === 'loaded' || l.kind === 'empty' || l.kind === 'unknown') && l.km);
    const totKm = sum(withKm);
    return {
      loaded_rate_if_unknown_empty: totKm ? sum(withKm.filter((l) => l.kind === 'loaded')) / totKm : null,
      empty_rate_if_unknown_empty: totKm ? sum(withKm.filter((l) => l.kind !== 'loaded')) / totKm : null,
      unknown_as_empty: { legs: withKm.length, unknown_legs: withKm.filter((l) => l.kind === 'unknown').length, unknown_legs_without_km: legs.filter((l) => l.kind === 'unknown' && !l.km).length,
        formula: '不明の区間を空車として数えた場合の率（km が分かる区間だけ）' },
      actual_rate: {
        value: rate(judged), legs: judged.length, legs_with_km: judged.filter((l) => l.km).length, of: n, share: n ? judged.length / n : 0.0,
        km_loaded: pyRound(sum(judged.filter((l) => l.kind === 'loaded')), 1), km_empty: pyRound(sum(judged.filter((l) => l.kind === 'empty')), 1),
        formula: '実車の km ÷ 走った km（実車か空車かが判定できた区間だけ）',
        ...(judged.length && judged.every((l) => l.summary) ? { basis: '距離の合計', formula: '距離の合計（日ごとの集計の実車距離の合計 ÷ 走行距離の合計）' } : {}),
      },
      load_rate: { value: loadRate, legs: withW.length, of_large: large.length, of: n, share: large.length ? withW.length / large.length : 0.0, // 割合の分母は、画面の文が書く「大型の区間」
       
        over_max: overSet.size, zero_weight: zeroWeightLegs(legs).length, multi_weight: multiWeightLegs(legs).length, formula: '積んだ重さ ÷ 最大積載量（大型の実車の区間のうち、重さが分かる区間の平均。最大積載量を超える重さの区間は入れません）' },
      large_actual_rate: { value: largeRate, legs: largeJudged.length, of_large: large.length },
      load_efficiency: { value: loadRate != null && largeRate != null ? loadRate * largeRate : null, formula: '大型の積載率 × 大型の実車率' },
    };
  }

  function weekdayTable(legs, stops, dock) {
    const rows = [];
    for (const d of ['out', 'back']) {
      for (let wd = 0; wd < 7; wd++) {
        const ls = legs.filter((l) => l.dir === d && l.cycle_wd === wd);
        if (!ls.length) continue;
        rows.push({
          dir: d, wd, trips: ls.length, empty: ls.filter((l) => l.kind === 'empty').length, unknown: ls.filter((l) => l.kind === 'unknown').length,
          empty_km: pyRound(ls.filter((l) => l.kind === 'empty').reduce((a, l) => a + (l.km || 0), 0), 1),
          long_stops: d === 'out' ? stops.filter((s) => s.minutes >= R3_MIN_STOP_MIN && s.place === dock && s.cycle_wd === wd).length : null,
        });
      }
    }
    return rows;
  }

  // 区間の [発地, 着地]。両方の地点が分かっていればそのまま。どちらかが無く dir だけあるときは、dir（out: 自社 → 主な行き先、back: 主な行き先 → 自社）から（own.py の leg_pair と同じ）。
  // 「着地が車庫か」で向きを決めると、大井・名古屋・横浜・福岡からの帰りがすべて同じ区間に入るので、実際の発地と着地の組で数える
  function legPair(l, base, dock) {
    if (l.frm && l.to) return [l.frm, l.to];
    if (base && dock) {
      if (l.dir === 'out') return [base, dock];
      if (l.dir === 'back') return [dock, base];
    }
    return [null, null];
  }
  const pairRole = (frm, to, base) => (base && to === base && frm !== base ? 'back' : base && frm === base && to !== base ? 'out' : null);

  // 区間 × 曜日の表と気づきが数える曜日（cycle_wd。その便自身が出た日の曜日）。無い区間は、その便自身の日付の曜日（own.py の leg_wd と同じ）
  const legWd = (l) => (Number.isInteger(l.cycle_wd) ? l.cycle_wd : weekdayOf(l.date));

  // 区間を、実際の発地と着地の組ごとに数える（own.py の pair_tables と同じ）。pairs と pair_table を返す
  function pairTables(legs, places, vehicles, base, dock) {
    const clsOf = {};
    for (const v of vehicles) clsOf[v.plate] = v.cls || classOf(v.type_raw);
    const nm = (pid) => { const p = places[pid] || {}; return p.short || p.pseudo || '?'; };
    const acc = new Map();
    for (const l of legs) {
      const [fr, to] = legPair(l, base, dock);
      if (!fr || !to || fr === to) continue;
      const k = fr + '\u0000' + to;
      if (!acc.has(k)) acc.set(k, { fr, to, legs: [] });
      acc.get(k).legs.push(l);
    }
    const sortObj = (o) => { const r = {}; for (const k of Object.keys(o).sort()) r[k] = o[k]; return r; };
    const tally = (ls) => {
      const cl = {}, ecl = {};
      for (const l of ls) {
        const c = clsOf[l.plate] || '不明';
        cl[c] = (cl[c] || 0) + 1;
        if (l.kind === 'empty') ecl[c] = (ecl[c] || 0) + 1;
      }
      return { trips: ls.length, empty: ls.filter((l) => l.kind === 'empty').length, unknown: ls.filter((l) => l.kind === 'unknown').length,
        empty_km: pyRound(ls.filter((l) => l.kind === 'empty').reduce((a, l) => a + (l.km || 0), 0), 1), classes: sortObj(cl), empty_classes: sortObj(ecl) };
    };
    const items = [];
    for (const { fr, to, legs: raw } of acc.values()) {
      const ls = raw.slice().sort((a, b) => cmp(a.date, b.date) || cmp(a.plate, b.plate));
      const t = tally(ls), byWd = [];
      for (let wd = 0; wd < 7; wd++) {
        const wl = ls.filter((l) => legWd(l) === wd);
        if (wl.length) byWd.push({ wd, ...tally(wl), leg_ids: wl.map((l) => l.id) });
      }
      items.push({
        frm: fr, to, frm_label: nm(fr), to_label: nm(to), label: `${nm(fr)} → ${nm(to)}`, role: pairRole(fr, to, base),
        trips: t.trips, loaded: ls.filter((l) => l.kind === 'loaded').length, empty: t.empty, unknown: t.unknown, empty_km: t.empty_km,
        km: pyRound(ls.reduce((a, l) => a + (l.km || 0), 0), 1), classes: t.classes, empty_classes: t.empty_classes,
        plates: uniq(ls.map((l) => l.plate)).sort(), by_weekday: byWd, leg_ids: ls.map((l) => l.id),
      });
    }
    items.sort((a, b) => cmp(-a.empty, -b.empty) || cmp(-a.trips, -b.trips) || cmp(a.label, b.label) || cmp(a.frm, b.frm) || cmp(a.to, b.to));
    const table = [];
    items.forEach((it, n) => {
      it.id = `P${n + 1}`;
      for (const w of it.by_weekday) {
        table.push({ pair: it.id, frm: it.frm, to: it.to, label: it.label, role: it.role, wd: w.wd, weekday: WEEKDAYS[w.wd], trips: w.trips, empty: w.empty, unknown: w.unknown,
          empty_km: w.empty_km, classes: w.classes, empty_classes: w.empty_classes, leg_ids: w.leg_ids });
      }
    });
    return { pairs: items, pair_table: table };
  }

  /* ------------------------------------------------------------ 復路の根拠（SPEC §44。own.py の return_trips / return_target / return_pairs と同じ）
     復路の候補の母数と、区間（着地）を確認できた便を分ける。曜日は復路の便自身の実出発（dep）。無ければ記録日（根拠 date）。cycle_wd は使わない。 */

  const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;
  const ISO_DT = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/;
  function isoDateOk(s) {
    if (typeof s !== 'string') return null;
    const m = ISO_DATE.exec(s);
    if (!m) return null;
    const y = +s.slice(0, 4), mo = +s.slice(5, 7), d = +s.slice(8, 10);
    if (mo < 1 || mo > 12 || d < 1) return null;
    const t = new Date(Date.UTC(y, mo - 1, d));
    return t.getUTCFullYear() === y && t.getUTCMonth() === mo - 1 && t.getUTCDate() === d ? m[0] : null;
  }
  function srcCat(s) {
    if (s == null || String(s) === '') return 'unknown';
    const t = String(s);
    if (t.includes('デジタコ')) return 'digitaco';
    if (t.includes('配車表') || t.includes('ファイル')) return 'haisha';
    if (t.includes('推定') || t.includes('OSRM') || t.includes('直線')) return 'estimate';
    if (t.includes('利用者')) return 'user';
    return 'unknown';
  }
  function returnWhen(l) {
    const dep = l.dep;
    if (typeof dep === 'string') {
      const m = ISO_DT.exec(dep), d = isoDateOk(dep);
      if (m && d) return [`${m[1]}T${m[2]}`, 'dep', weekdayOf(d)];
    }
    const d = isoDateOk(l.date);
    if (d) return [d, 'date', weekdayOf(d)];
    return [null, 'none', null];
  }
  const retKm = (l) => (typeof l.km === 'number' && isFinite(l.km) && l.km >= 0 ? l.km : null);
  const retSrc = (l, key) => { const s = l[key + '_src']; return s != null ? s : (l.sources || {})[key]; };
  function retCounts(rows) {
    const e = rows.filter((r) => r.kind === 'empty'), kms = e.filter((r) => r.km != null).map((r) => r.km);
    const nl = rows.filter((r) => r.kind === 'loaded').length;
    return { trips: rows.length, empty: e.length, loaded: nl, unknown: rows.length - e.length - nl, judged: e.length + nl,
      empty_km: pyRound(kms.reduce((a, b) => a + b, 0), 1), km_missing: e.length - kms.length };
  }
  const retBasis = (rows) => ({ dep: rows.filter((r) => r.dt_basis === 'dep').length, date: rows.filter((r) => r.dt_basis === 'date').length, none: rows.filter((r) => r.dt_basis === 'none').length });
  function retDtSrc(l, basis) {
    if (basis === 'dep') return srcCat(retSrc(l, 'time'));
    if (basis === 'date') return l.ref ? 'haisha' : (l.dref ? 'digitaco' : 'unknown');
    return 'unknown';
  }
  const orNull = (v) => (v === undefined ? null : v);

  function returnTrips(legs, fr, to, base, dock, pairId) {
    const pid = pairId || `${fr}>${to}`;
    const rows = [], seen = new Set();
    let dup = 0;
    for (const l of legs) {
      if (l.dir !== 'back') continue;
      const [a, b] = legPair(l, base, dock);
      if (a !== fr || b !== to) continue;
      const key = JSON.stringify([orNull(l.plate), orNull(l.date), orNull(l.dep), orNull(l.arr), orNull(l.frm), orNull(l.to), orNull(l.km), orNull(l.kind), orNull(l.round)]);
      if (seen.has(key)) { dup++; continue; }
      seen.add(key);
      const [dt, basis, wd] = returnWhen(l);
      const ref = l.ref || null, dref = l.dref || null;
      const kind = l.kind === 'empty' || l.kind === 'loaded' ? l.kind : 'unknown';
      const km = retKm(l);
      rows.push({
        leg_id: orNull(l.id), row_id: ref ? `${ref.file}:${ref.where}` : (l.id || ''), pair_id: pid, ref, dref, plate: orNull(l.plate), driver: orNull(l.driver),
        dt, dt_basis: basis, dt_src: retDtSrc(l, basis), arr: orNull(l.arr), wd,
        kind, kind_src: kind !== 'unknown' ? orNull(l.kind_src) : null, kind_src_cat: kind !== 'unknown' ? srcCat(l.kind_src) : 'unknown',
        km, km_src_cat: km != null ? srcCat(retSrc(l, 'km')) : 'unknown',
        dest_confirmed: l.frm === fr && l.to === to,
      });
    }
    rows.sort((x, y) => cmp(x.dt || '~', y.dt || '~') || cmp(String(x.leg_id), String(y.leg_id)));
    const conf = rows.filter((r) => r.dest_confirmed), unc = rows.filter((r) => !r.dest_confirmed);
    const days = rows.filter((r) => r.dt).map((r) => r.dt.slice(0, 10)).sort();
    const byWd = [];
    for (let wd = 0; wd < 7; wd++) {
      const w = conf.filter((r) => r.wd === wd);
      if (w.length) byWd.push({ wd, weekday: WEEKDAYS[wd], ...retCounts(w), basis: retBasis(w) });
    }
    return {
      fr, to, pair_id: pid, candidates: retCounts(rows), confirmed: retCounts(conf), unconfirmed: retCounts(unc),
      duplicates_dropped: dup, period: days.length ? { first: days[0], last: days[days.length - 1] } : null,
      basis: retBasis(conf), no_weekday: conf.filter((r) => r.wd == null).length, by_weekday: byWd, rows,
    };
  }

  function returnTarget(model, wd) {
    const rows = model.rows;
    const isT = (r) => wd != null && r.dest_confirmed && r.wd === wd;
    const tgt = rows.filter(isT), oth = rows.filter((r) => !isT(r));
    const c = retCounts(tgt);
    return {
      wd: wd == null ? null : wd, weekday: wd != null ? WEEKDAYS[wd] : null, confirmed: c, basis: retBasis(tgt),
      unconfirmed_dest_on_wd: rows.filter((r) => wd != null && !r.dest_confirmed && r.wd === wd).length,
      all_empty: c.trips > 0 && c.empty === c.trips, has_empty: c.empty > 0, others: retCounts(oth),
      dates: tgt.map((r) => r.dt), rows: tgt,
    };
  }

  function returnPairs(legs, places, base, dock) {
    places = places || {};
    const nm = (pid) => { const p = places[pid] || {}; return p.short || p.pseudo || '?'; };
    const keys = [];
    for (const l of legs) {
      if (l.dir !== 'back') continue;
      const [a, b] = legPair(l, base, dock);
      if (a && b && a !== b && !keys.some((k) => k[0] === a && k[1] === b)) keys.push([a, b]);
    }
    const out = [];
    for (const [fr, to] of keys) {
      const m = returnTrips(legs, fr, to, base, dock);
      const wds = m.by_weekday.filter((w) => w.empty > 0).map((w) => ({ wd: w.wd, weekday: w.weekday, trips: w.trips, empty: w.empty }));
      const best = wds.slice().sort((x, y) => cmp(-x.empty, -y.empty) || cmp(-(x.empty / x.trips), -(y.empty / y.trips)) || cmp(x.wd, y.wd));
      out.push({ fr, to, key: `${fr}>${to}`, fr_label: nm(fr), to_label: nm(to), label: `${nm(fr)} → ${nm(to)}`,
        candidates: m.candidates.trips, confirmed: m.confirmed.trips, confirmed_empty: m.confirmed.empty, confirmed_empty_km: m.confirmed.empty_km, empty_km: m.candidates.empty_km, weekdays: wds, default_wd: best.length ? best[0].wd : null });
    }
    // 空車で走る距離（km）の多い区間を先に並べる（便の数が多い区間ではなく、手を打つ価値の大きい区間を最初の画面に出す）
    out.sort((x, y) => cmp(-x.confirmed_empty_km, -y.confirmed_empty_km) || cmp(-x.empty_km, -y.empty_km) || cmp(-x.confirmed_empty, -y.confirmed_empty) || cmp(-x.candidates, -y.candidates) || cmp(x.label, y.label) || cmp(x.fr, y.fr) || cmp(x.to, y.to));
    return out;
  }

  // 車ごと・車格ごとの最大積載量（kg）を車両に当てた写しを返す。byPlate が byClass より先。値が数でない・0 以下なら使わない（own.py の apply_max_payload と同じ）
  function applyMaxPayload(vehicles, byPlate, byClass) {
    const bp = byPlate || {}, bc = byClass || {};
    const ok = (x) => typeof x === 'number' && isFinite(x) && x > 0;
    return vehicles.map((v0) => {
      const v = { ...v0 };
      for (const [src, key, tbl] of [['plate', v.plate, bp], ['class', v.cls, bc]]) {
        const val = key != null && Object.prototype.hasOwnProperty.call(tbl, key) ? tbl[key] : null;
        if (ok(val)) { v.max_load_kg = +val; v.max_load_assumed = false; v.max_load_src = src; break; }
      }
      return v;
    });
  }

  // 登録が無く仮定した最大積載量（max_load_assumed が真）の車に、仮定を超える重さの記録があるとき、その車格の仮定を、記録の最大（ceilingKg まで）に上げる（own.py の raise_assumed_max と同じ）。
  // 黙って外さないための処理で、利用者が車・車格ごとに決めた値は変えない。戻り値は { vehicles, note }（note: null か { cls, from_kg, to_kg, legs, vehicles }）
  const LARGE_ASSUMED_KG = 10000, LARGE_RAISE_CEIL_KG = 20000; // 仮定を超える重さが LARGE_RAISE_CEIL_KG より大きいときは、t と kg の取り違えかもしれないので上げず、「超える重さ」のカードに出す
  function raiseAssumedMax(vehicles, legs, ceilingKg = LARGE_RAISE_CEIL_KG) {
    const out = vehicles.map((v) => ({ ...v }));
    const by = new Map(out.filter((v) => v.max_load_assumed && v.max_load_kg).map((v) => [v.plate, v]));
    const top = new Map(), nOver = new Map();
    for (const l of legs) {
      const v = by.get(l.plate), w = l.weight_kg;
      if (!v || !w || w <= v.max_load_kg) continue;
      const c = v.cls || '';
      nOver.set(c, (nOver.get(c) || 0) + 1);
      if (w <= ceilingKg && w > (top.get(c) || 0)) top.set(c, w);
    }
    const notes = [];
    for (const [c, mx] of top) {
      let frm = null, nv = 0;
      for (const v of by.values()) {
        if ((v.cls || '') === c && v.max_load_kg < mx) { frm = frm == null ? v.max_load_kg : Math.min(frm, v.max_load_kg); v.max_load_kg = +mx; nv++; }
      }
      if (nv) notes.push({ cls: c, from_kg: frm, to_kg: +mx, legs: nOver.get(c) || 0, vehicles: nv });
    }
    return { vehicles: out, note: notes.length ? notes[0] : null };
  }

  /* ------------------------------------------------------------ 気づき（6 規則） */

  const RULE_LABEL_JA = { // 気づきの名前（「規則 1」ではなく、中身が分かる言い方。own.py の RULE_LABEL_JA と同じ）
    1: '同じ曜日の帰りがいつも空', 2: '空で走った区間', 3: '同じ場所での長い待ち', 4: '拘束時間が長い日', 5: '積載の記録の対象なのに重さが空',
    6: '実車か空車か分からない区間が多い', 7: '最大積載量を超える重さ',
  };
  // 気づきの文に出す地点の言い方。サンプルは「江東区の地点 07」。自社のファイル（ownNames）は、ファイルに書いてあった地名のまま（own.py の place_phrase と同じ）
  function placePhrase(p, pid, ownNames) {
    if (ownNames) {
      const label = String(p.label || p.muni || pid).slice(0, 16), muni = p.muni || '';
      return muni && !label.includes(muni) ? `${label}（${muni}）` : label;
    }
    return p.muni ? `${p.muni}の${p.pseudo || pid}` : (p.pseudo || pid);
  }
  const evLeg = (l) => { const r = l.ref || {}; return { id: l.id, label: `${md(l.date)} 車番 ${l.plate}`, file: r.file, where: r.where, text: r.text }; };
  const uniq = (a) => [...new Set(a)];

  function insights(data) {
    const { legs } = data, stops = data.stops || [], shifts = data.shifts || [], vehicles = data.vehicles || [], places = data.places || {};
    const base = data.base, dock = data.dock;
    const short = (p) => (places[p] || {}).short || '?';
    const out = [];
    const add = (rule, text, numbers, next, evidence, label) => out.push({ id: `I${out.length + 1}`, rule, rule_label_ja: label || RULE_LABEL_JA[rule], text, numbers, next, evidence });
    const known = legs.filter((l) => l.kind === 'loaded' || l.kind === 'empty');

    // 規則 1: ある区間（実際の発地と着地の組）・曜日で、空車の便が 4 便以上のうち 3/4 以上
    const pr = (l) => legPair(l, base, dock);
    const kindOf = (fr, to) => pairRole(fr, to, base) || 'pair';
    const groups = new Map();
    for (const l of known) {
      const [fr, to] = pr(l);
      if (l.cycle_wd != null && fr && to && fr !== to) { const k = fr + '\u0000' + to + '\u0000' + l.cycle_wd; if (!groups.has(k)) groups.set(k, { fr, to, wd: l.cycle_wd, ls: [] }); groups.get(k).ls.push(l); }
    }
    const hits = [];
    for (const g of groups.values()) {
      const n = g.ls.length, e = g.ls.filter((l) => l.kind === 'empty').length;
      if (n >= R1_MIN_TRIPS && e * R1_RATIO[1] >= n * R1_RATIO[0]) hits.push({ e, n, fr: g.fr, to: g.to, wd: g.wd, ls: g.ls });
    }
    const rank = { back: 0, out: 1, pair: 2 };
    hits.sort((a, b) => cmp(-a.e, -b.e) || cmp(a.n, b.n) || cmp(rank[kindOf(a.fr, a.to)], rank[kindOf(b.fr, b.to)]) || cmp(`${short(a.fr)} → ${short(a.to)}`, `${short(b.fr)} → ${short(b.to)}`) || cmp(a.wd, b.wd));
    const pairWds = new Map(); // F10: ある区間の、便のある曜日がどれも全部空なら、曜日ごとのカードを出さず 1 枚にまとめる
    for (const g of groups.values()) { const k = g.fr + '\u0000' + g.to; if (!pairWds.has(k)) pairWds.set(k, []); pairWds.get(k).push(g.wd); }
    const mergedDone = new Set();
    for (const { e, n, fr, to, wd, ls } of hits) {
      const d = kindOf(fr, to);
      const pk = fr + '\u0000' + to;
      const allWds = (pairWds.get(pk) || []).slice().sort((x, y) => x - y);
      const allLegs = [];
      for (const g of groups.values()) if (g.fr === fr && g.to === to) allLegs.push(...g.ls);
      if (allWds.length >= 2 && allLegs.every((l) => l.kind === 'empty') && hits.filter((h) => h.fr === fr && h.to === to).length >= 2) {
        if (mergedDone.has(pk)) continue;
        mergedDone.add(pk);
        const lb2 = `${short(fr)} → ${short(to)}`, word2 = { back: '帰り', out: '行き', pair: '区間' }[d];
        const wds = allWds.map((w) => WEEKDAYS[w]).join('・');
        add(1, `${lb2}の${word2}は、${allLegs.length} 便がすべて空でした（${wds}曜のどの曜日も空で、曜日による違いはありません。実車か空車かが分かった便だけ）。`,
          [{ k: lb2, v: `${allLegs.length} 便中 ${allLegs.length} 便が空` }, { k: '空だった曜日', v: `${wds}（どの曜日も空）` }],
          { label: '帰り便の下書きにする', action: 'draft_backhaul', payload: { dir: d, wd, weekday: WEEKDAYS[wd], trips: allLegs.length, empty: allLegs.length, night: false, month: data.month, frm: fr, to, frm_label: short(fr), to_label: short(to), label: lb2, all_weekdays: true, weekdays: allWds.map((w) => WEEKDAYS[w]) } },
          allLegs.slice().sort((a2, b2) => cmp(a2.date, b2.date)).map(evLeg), { back: '毎回の帰りが空（曜日の違いなし）', out: '毎回の行きが空（曜日の違いなし）', pair: '毎回空の区間（曜日の違いなし）' }[d]);
        continue;
      }
      const others = [];
      for (const g of groups.values()) if (g.fr === fr && g.to === to && g.wd !== wd) others.push(...g.ls);
      const oe = others.filter((l) => l.kind === 'empty').length;
      const night = ls.every((l) => /^\d+$/.test((l.cycle_dep || '').slice(11, 13)) && +l.cycle_dep.slice(11, 13) >= 18);
      const when = `${WEEKDAYS[wd]}曜` + (night ? 'の夜' : '');
      const who = `${when}に出た `; // 帰りも、帰りの便自身が出た日（行きを出た日ではない）
      const body = e === n && d === 'back' ? `${n} 便の帰りがすべて空でした` : e === n ? `${n} 便がすべて空でした` : `${n} 便のうち ${e} 便が空でした`;
      const lb = `${short(fr)} → ${short(to)}`, word = { back: '帰り', out: '行き', pair: '区間' }[d];
      const tail1 = others.length ? `ほかの曜日の同じ向きは ${others.length} 便中 ${oe} 便が空です（実車か空車かが分かった便だけ）。` : 'ほかの曜日の同じ向きの便は、実車か空車かが分かった中にはありません。';
      const text = `${lb}の${word}は、${who}${body}。${tail1}`;
      add(1, text, [{ k: `${WEEKDAYS[wd]}曜発の${lb}`, v: `${n} 便中 ${e} 便が空` }, { k: 'ほかの曜日', v: `${others.length} 便中 ${oe} 便が空` }],
        { label: '帰り便の下書きにする', action: 'draft_backhaul', payload: { dir: d, wd, weekday: WEEKDAYS[wd], trips: n, empty: e, night, month: data.month, frm: fr, to, frm_label: short(fr), to_label: short(to), label: lb } },
        ls.slice().sort((a, b) => cmp(a.date, b.date)).map(evLeg), { back: '同じ曜日の帰りがいつも空', out: '同じ曜日の行きがいつも空', pair: '同じ曜日の同じ区間がいつも空' }[d]);
    }

    // 規則 2
    const empties = legs.filter((l) => l.kind === 'empty');
    if (empties.length) {
      const km = empties.reduce((a, l) => a + (l.km || 0), 0);
      const eb = empties.filter((l) => l.dir === 'back').length, eo = empties.filter((l) => l.dir === 'out').length;
      const byPair = new Map();
      for (const l of empties) { const [fr, to] = pr(l); if (fr && to && fr !== to) { const k = fr + '\u0000' + to; byPair.set(k, { fr, to, c: (byPair.has(k) ? byPair.get(k).c : 0) + 1 }); } }
      const simple = !!(base && dock) && [...byPair.values()].every((x) => (x.fr === dock && x.to === base) || (x.fr === base && x.to === dock)); // 自社と主な行き先の往復だけなら、これまでの言い方
      let tail;
      if (simple) tail = base && dock ? `向きは ${short(dock)} → ${short(base)} が ${eb} 便、${short(base)} → ${short(dock)} が ${eo} 便です。` : '';
      else {
        const top = [...byPair.values()].sort((a, b) => cmp(-a.c, -b.c) || cmp(`${short(a.fr)} → ${short(a.to)}`, `${short(b.fr)} → ${short(b.to)}`));
        tail = top.length ? `向きは ${top.slice(0, 3).map((x) => `${short(x.fr)} → ${short(x.to)} が ${x.c} 便`).join('、')} です${top.length > 3 ? `（ほか ${top.length - 3} 区間）` : ''}。` : '';
      }
      const meter = legs.some((l) => (l.sources || {}).km === 'デジタコ'); // デジタコを入れていないときは、メーターと書かない
      const kmN = empties.filter((l) => l.km).length, kmMiss = empties.length - kmN; // 距離が分からない便は、km の合計に入れない（0 km として足さない）
      const kmSrc = `距離は${meter ? `デジタコのメーターと${data.est_label && data.est_label !== '推定' ? ' ' + data.est_label : '推定'}` : (data.est_label || '推定')}`;
      const kmHead = !kmN ? `空で走った区間は ${empties.length} 便でした（距離が分からないので、km は出していません）。`
        : kmMiss ? `空で走った区間は ${empties.length} 便、距離の分かる ${kmN} 便で約 ${commas(km)} km でした（${kmSrc}）。距離が分からない ${kmMiss} 便は km に入れていません。`
        : `空で走った区間は ${empties.length} 便・約 ${commas(km)} km でした（${kmSrc}）。`;
      add(2, `${kmHead}${tail}`,
        [{ k: '空で走った便', v: `${empties.length} 便` }, { k: '空で走った距離', v: !kmN ? '距離が分からない' : kmMiss ? `約 ${commas(km)} km（距離が分からない ${kmMiss} 便を除く）` : `約 ${commas(km)} km` }],
        { label: '帰り便の下書きにする', action: 'draft_backhaul', payload: { dir: 'back' } },
        empties.slice().sort((a, b) => cmp(a.date, b.date) || cmp(a.plate, b.plate)).map(evLeg).slice(0, 40));
    }

    // 規則 3
    const byPlace = new Map();
    for (const s of stops) if (s.minutes >= R3_MIN_STOP_MIN && s.place) { if (!byPlace.has(s.place)) byPlace.set(s.place, []); byPlace.get(s.place).push(s); }
    const tot = (ss) => ss.reduce((a, s) => a + s.minutes, 0);
    for (const [pid, ss] of [...byPlace.entries()].sort((a, b) => tot(b[1]) - tot(a[1]))) {
      if (ss.length < R3_MIN_COUNT) continue;
      const t = tot(ss), plates = uniq(ss.map((s) => s.plate)).sort();
      const p = places[pid] || {};
      add(3, `${placePhrase(p, pid, data.own_names)} で、30 分以上待った記録が ${ss.length} 回、合わせて ${fix(t / 60, 1)} 時間でした`
        + `（デジタコのある ${plates.length} 台の記録だけ。紙の日報の分と、30 分未満の回は数えていません）。`,
      [{ k: '30 分以上の停車', v: `${ss.length} 回` }, { k: '合計', v: `${fix(t / 60, 1)} 時間` }],
      { label: '荷主に渡す記録の一覧を書き出す', action: 'show_evidence', payload: {} },
      ss.slice().sort((a, b) => cmp(a.start, b.start)).map((s) => ({
        id: s.id, label: `${md(s.start)} 車番 ${s.plate}`, file: (s.ref || {}).file, where: (s.ref || {}).where, text: `${s.start.slice(11, 16)}〜${s.end.slice(11, 16)}（${s.minutes} 分）`,
      })));
    }

    // 規則 4
    const over13 = shifts.filter((s) => s.hours > R4_BASE_H);
    if (over13.length) {
      const over14 = shifts.filter((s) => s.hours > R4_LONG_H), over15 = shifts.filter((s) => s.hours > R4_MAX_H);
      const weeks = new Map();
      for (const s of over14) { const [y, w] = isoWeek(s.start.slice(0, 10)); const k = s.driver + '|' + y + '-W' + pad(w); if (!weeks.has(k)) weeks.set(k, []); weeks.get(k).push(s); }
      const bad = [...weeks.values()].filter((v) => v.length > R4_WEEK_GUIDE);
      const tail = bad.length
        ? `うち 14 時間を超えた日は ${over14.length} 日で、同じ運転者で週 ${Math.max(...bad.map((v) => v.length))} 回の週が ${bad.length} つあります（目安は週 ${R4_WEEK_GUIDE} 回まで）。`
        : over14.length ? `うち 14 時間を超えた日は ${over14.length} 日です。` : '';
      const tail15 = !over15.length ? '15 時間を超えた日はありません。' : `15 時間を超えた日が ${over15.length} 日あります。`;
      add(4, `原則の 13 時間を超えた日が ${over13.length} 日ありました。${tail}${tail15}デジタコの記録から数えた目安で、始業・終業とは同じではありません。`,
        [{ k: '13 時間超', v: `${over13.length} 日` }, { k: '14 時間超', v: `${over14.length} 日` }, { k: '15 時間超', v: `${over15.length} 日` }],
        { label: 'その日の運行の一覧を開く', action: 'show_evidence', payload: {} },
        over13.slice().sort((a, b) => cmp(a.start, b.start)).map((s) => ({
          id: s.id, label: `${md(s.start)} ${s.driver_label != null ? s.driver_label : s.driver} 車番 ${s.plate}`, file: (s.ref || {}).file, where: (s.ref || {}).where,
          text: `始業 ${s.start.slice(11, 16)} から 24 時間の拘束 ${fix(s.hours, 1)} 時間`,
        })));
    }

    // 規則 5（車両総重量か最大積載量が登録されている車だけ。仮の値は使わない）
    const flagged = [];
    for (const v of vehicles) {
      if (!v.registered) continue;
      if ((v.gvw_kg || 0) >= R5_GVW_KG || (v.max_load_kg || 0) >= R5_LOAD_KG) {
        const mine = legs.filter((l) => l.plate === v.plate && l.kind === 'loaded');
        if (!mine.some((l) => l.weight_kg)) flagged.push(v);
      }
    }
    if (flagged.length) {
      const g5 = new Map();
      for (const v of flagged) { if (!g5.has(v.cls)) g5.set(v.cls, []); g5.get(v.cls).push(v); }
      const parts = [...g5.entries()].map(([c, vs]) => `${c} の ${vs.length} 台`).join('、');
      add(5, `${parts}は、車両総重量 8t 以上か最大積載量 5t 以上として登録されています。積載の状況の記録の対象で、積んだ重さの記録がありません。車検証を確かめてください。`,
        [{ k: '対象で重さが空の車', v: `${flagged.length} 台` }],
        { label: '車両の設定を開く', action: 'show_evidence', payload: {} },
        flagged.map((v) => ({ id: `V${v.plate}`, label: `車番 ${v.plate}（${v.cls}）`, file: '車両の設定', where: '車両', text: `車両総重量 ${commas(v.gvw_kg)} kg、最大積載量 ${commas(v.max_load_kg)} kg、運行記録計 ${v.tachograph_label}` })));
    }

    // 規則 6
    const unk = legs.filter((l) => l.kind === 'unknown');
    if (legs.length && unk.length / legs.length >= R6_UNKNOWN_RATIO) {
      const pct = pyRound(100 * unk.length / legs.length);
      add(6, `区間の ${pct}% は実車か空車かが分かりません。配車表の積地・降ろし地を入れると判定できます。`,
        [{ k: '不明の区間', v: `${unk.length} / ${legs.length} 区間` }],
        { label: '配車表を取り込む', action: 'import', payload: {} },
        unk.slice().sort((a, b) => cmp(a.date, b.date) || cmp(a.plate, b.plate)).map(evLeg));
    }

    // 規則 7: 積んだ重さが最大積載量を超える記録（積載率に入れていない。無いときは出さない）
    const over = overMaxLegs(legs, vehicles);
    if (over.length) {
      const cap = {};
      for (const v of vehicles) cap[v.plate] = v.max_load_kg;
      add(7, `積んだ重さが最大積載量を超えている記録が ${over.length} 件ありました。積載率と積載効率の平均には入れていません。重さの単位（t と kg）や桁を、元の配車表で確かめてください。`,
        [{ k: '最大積載量を超える記録', v: `${over.length} 件` }],
        { label: '元の行を見る', action: 'show_evidence', payload: {} },
        over.slice().sort((a, b) => cmp(a.date, b.date) || cmp(a.plate, b.plate)).map((l) => ({ ...evLeg(l), text: (([a, b]) => `重さ ${a} > 最大積載量 ${b}`)(tonsPair(l.weight_kg, cap[l.plate])) })).slice(0, 40));
    }
    return out;
  }

  /* ------------------------------------------------------------ 地点（市区町村の名前の先頭で、地図の代表点に当てる。荷待ちの点と同じ方法） */

  const SUFFIX = /[市区町村]$/;
  function muniIndex(munis) {
    if (munis._ix) return munis._ix;
    const byName = new Map(), byStem = new Map();
    munis.forEach((m, i) => {
      if (!byName.has(m.name)) byName.set(m.name, []);
      byName.get(m.name).push(i);
      if (m.name.length >= 3 && SUFFIX.test(m.name)) { const st = m.name.slice(0, -1); if (!byStem.has(st)) byStem.set(st, []); byStem.get(st).push(i); }
    });
    const stems = [...byStem.keys()].sort((a, b) => b.length - a.length);
    Object.defineProperty(munis, '_ix', { value: { byName, byStem, stems }, enumerable: false });
    return munis._ix;
  }

  // 政令指定都市の区（名古屋市港区）は、区の字まで残す。区を落とすと「名古屋市港」になり、別の場所に見える。市・町・村と東京の特別区（江東区）は、末尾の字を略す
  const WARD_OF_CITY = /市.+区$/;
  const shortOfMuni = (name) => (WARD_OF_CITY.test(name) ? name : name.length >= 3 && SUFFIX.test(name) ? name.slice(0, -1) : name);
  // 戻り値: { m: {code, name, pref, lon, lat, short} } か { reason }
  // 先頭の都道府県（「東京 江東区」「東京都江東区」）は外して調べる。都道府県が分かれば、同じ名前の市区町村（府中市など）をその都道府県の中から選ぶ。外して当たらなければ、元の文字でも調べる
  function matchMuni(text, munis) {
    const [pref, rest] = prefSplit(text);
    if (!pref) return matchMuniCore(text, munis, null);
    const r = matchMuniCore(rest, munis, pref);
    if (r.m) return r;
    const r2 = matchMuniCore(text, munis, null);
    return r2.m ? r2 : r;
  }
  function matchMuniCore(text, munis, pref) {
    const ix = muniIndex(munis);
    const pt = (i) => { const m = munis[i]; return m && m.lon != null && m.lat != null ? m : null; };
    const inPref = (ids) => (pref ? ids.filter((i) => munis[i].pref === pref) : ids);
    for (let len = Math.min(text.length, 8); len >= 2; len--) {
      let ids = ix.byName.get(text.slice(0, len));
      if (!ids) continue;
      if (ids.length > 1) ids = inPref(ids);
      if (!ids.length) continue;
      if (ids.length > 1) return { reason: `「${text.slice(0, len)}」は同じ名前の市区町村が複数あります` };
      const m = pt(ids[0]);
      if (m) return { m: { ...m, short: shortOfMuni(m.name) } };
    }
    for (const st of ix.stems) { // 「加古川」「江東」のように、市・区の字を略した書き方
      if (!text.startsWith(st)) continue;
      let ids = ix.byStem.get(st);
      if (ids.length > 1) ids = inPref(ids);
      if (!ids.length) continue;
      if (ids.length > 1) return { reason: `「${st}」は同じ名前の市区町村が複数あります` };
      const m = pt(ids[0]);
      if (m) return { m: { ...m, short: WARD_OF_CITY.test(m.name) ? m.name : st } };
    }
    const hall = hallWard(text, munis);
    if (hall) return hall;
    const head = text.split(/\s+/)[0];
    if (head.length >= 3 && head.endsWith('市')) { // 政令市は、その市の区の代表点の平均に置く
      const ids = [];
      munis.forEach((m, i) => { if (m.name.startsWith(head) && pt(i)) ids.push(i); });
      if (ids.length) {
        if (new Set(ids.map((i) => munis[i].pref)).size > 1) return { reason: `「${head}」は複数の県にまたがります` };
        const lon = ids.reduce((a, i) => a + munis[i].lon, 0) / ids.length, lat = ids.reduce((a, i) => a + munis[i].lat, 0) / ids.length;
        return { m: { code: null, name: head, pref: munis[ids[0]].pref, lon, lat, short: head.slice(0, -1) } };
      }
    }
    return { reason: `先頭に当たる市区町村が地図にありません` };
  }

  const HALL_WARD_NOTE = '市役所のある区に置きました（区が分かれば直せます）';
  // 区の名前を書かずに市の名前だけが書かれたとき、市役所のある区に置く（政令指定都市。own.py の CITY_HALL_WARD と同じ）
  const CITY_HALL_WARD = {
    '札幌': '中央区', '仙台': '青葉区', 'さいたま': '浦和区', '千葉': '中央区', '横浜': '中区', '川崎': '川崎区', '相模原': '中央区', '新潟': '中央区',
    '静岡': '葵区', '浜松': '中区', '名古屋': '中区', '京都': '中京区', '大阪': '北区', '堺': '堺区', '神戸': '中央区', '岡山': '北区',
    '広島': '中区', '北九州': '小倉北区', '福岡': '中央区', '熊本': '中央区',
  };
  function hallWard(text, munis) {
    const t = nfkc(text).trim();
    for (const city of Object.keys(CITY_HALL_WARD).sort((a, b) => b.length - a.length)) {
      if (!t.startsWith(city)) continue;
      const want = `${city}市${CITY_HALL_WARD[city]}`;
      const m = munis.find((x) => x.name === want && x.lon != null && x.lat != null);
      return m ? { m: { ...m, short: city, hall_ward: true, note: HALL_WARD_NOTE } } : null;
    }
    return null;
  }

  const haversine = (a, b) => {
    const R = 6371.0088, rad = Math.PI / 180, dLat = (b.lat - a.lat) * rad, dLon = (b.lon - a.lon) * rad;
    const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(x));
  };

  // 地図の市区町村に当たらない地名で、倉庫・センターなどの施設名を外した本名（「辰巳倉庫」「辰巳DC3」→「辰巳」。「本社車庫」のように、外すと名前が途中で切れる車庫は外さない）。同じ本名の地名は 1 つの地点にまとめる（options.merge_places が偽のときはまとめない）
  const FACILITY_TAIL = /(?:第?[0-9一二三四五六七八九十]*(?:物流センター|配送センター|センター|ｾﾝﾀｰ|営業所|工場|倉庫|ターミナル|集配所|出張所|支店|デポ|DC|ＤＣ|倉|(?<!車)庫|物流|配送))+[0-9A-Za-z]*$/i;
  function coreName(t) {
    const c = nfkc(t).trim().replace(/\s+/g, '');
    const r = c.replace(FACILITY_TAIL, '').replace(/[0-9\-ー]+$/, '');
    return r.length >= 2 ? r : c;
  }

  /* ------------------------------------------------------------ 車両 */

  function classOf(type) {
    const t = nfkc(type).trim();
    if (!t) return '不明';
    if (t.includes('大型')) return '大型';
    const m = /(\d+(?:\.\d+)?)\s*(?:t|ｔ|トン)/i.exec(t);
    if (m) return +m[1] >= 8 ? '大型' : `${+m[1]}t`;
    return t;
  }

  const TACHO_LABEL = { digital: 'デジタコ（CSV を読む）', analog: 'デジタコのファイルがありません（読みません）' };

  /* ------------------------------------------------------------ 組み立て */

  const NATIONAL = {
    actual_rate_pct: 62.15, load_efficiency_pct: 39.08,
    source: '自動車輸送統計年報 2025 年度（付表）原単位、e-Stat 統計表 ID 000040511074（営業用普通車）。積載効率は表 3-1 の輸送トンキロ ÷ 能力トンキロ（ID 000040511073）と一致',
    caution: '全国の積載効率はトンキロの比で、自社の値と同じ式ではありません。比べられるのは実車率だけです',
  };

  function mostCommon(arr) {
    const c = new Map();
    for (const x of arr) c.set(x, (c.get(x) || 0) + 1);
    let best = null, bn = 0;
    for (const [k, n] of c) if (n > bn) { best = k; bn = n; }
    return best;
  }

  // 重さ・時刻の形の語（「4.6t」「5:30」）か。地名ではないので、地点にも別名にも登録しない
  const isNonPlace = (name) => NON_PLACE_RE.test(nfkc(name).trim());

  // 見出しとして見た行（先頭の 10 行のうち、空でないセルがいちばん多い行。同数なら上の行）。読めなかったファイルの説明と、列の役割の選択に使う
  function judgedRow(rows) {
    let best = -1, bi = 0;
    rows.slice(0, HEADER_SCAN_ROWS).forEach((r, i) => { const n = r.filter((c) => (c || '').trim()).length; if (n > best) { best = n; bi = i; } });
    return bi;
  }
  // 文字の大半が制御文字か置き換え文字（□）のファイル、または NUL を含むファイル（画像・PDF・Excel をそのまま落としたとき）か
  function looksBinary(text) {
    const t = String(text || '').slice(0, 4000);
    if (!t) return false;
    if (t.includes('\u0000')) return true;
    let bad = 0;
    for (let i = 0; i < t.length; i++) { const c = t.charCodeAt(i); if (c === 0xfffd || (c < 32 && c !== 9 && c !== 10 && c !== 13)) bad++; }
    return bad / t.length >= 0.1;
  }
  const HOWTO_UNREADABLE_JA = '配車表なら、見出しの行に「車番」と、「10/1(木)」のような日付を 3 つ以上並べてください（例: 車番,車種,10/1(木),10/2(金),10/3(土)）。'
    + '1 行 = 1 便の形なら、日付・車番・発地・着地の列を入れてください（例: 日付,車番,発地,着地,重さ,時刻）。'
    + 'デジタコなら、開始と終了の日時の列を入れてください（例: 区分,開始日時,終了日時,走行距離）。'
    + '見出しの行が上から 10 行より下にあるときは、その上の行を消してください。画面で各列が何かを選ぶこともできます。';

  const normRoles = (m) => { const out = {}; for (const [k, v] of Object.entries(m || {})) out[colKey(k)] = v == null || v === '' ? 'skip' : v; return out; };

  /* files: [{ name, text, encoding? }]
     options（すべて省略できる。試験では Python と同じ前提を渡す）:
       year, mon, month（日の数字だけの見出しの月）, munis[{code,name,pref,lon,lat}], userAliases{地点の文字: 市区町村名}, estKm{'a|b': km}, estSrc, estKmFn(placeA, placeB)→{km,src}|null,
       places, base, dock, aliases{known:[[別名,id]], proposed:[[別名,id]]}, vehicles[], driverLabels{}, company{}, largeMaxLoadKg, routePath,
       haisha_use（使う配車表のファイル名の配列。省略すると、車番×日付が重ならない配車表を先から合わせて使う）, year_hint（保存した前の月の年。曜日で年が決まらないときの手掛かり）,
       max_payload_by_plate{車番: kg}, max_payload_by_class{車格: kg}（車ごとが先。率と最大積載量を超えるカードに使う）,
       column_roles（利用者が決めた列の役割。{ haisha: {列名: 役割}, digitaco: {列名: 役割} } か、平らな {列名: 役割}。役割は date / plate / from / to / weight / time / type /
         kind / start_time / end_time / distance_km / loaded_flag / place_name / driver_code / loaded_km / empty_km / skip（使わない）） */
  function analyze(files, options) {
    const o = options || {};
    const cr = o.column_roles || {};
    const nested = !!(cr.haisha || cr.digitaco);
    const hRoles = normRoles(nested ? cr.haisha : cr), dRoles = normRoles(nested ? cr.digitaco : cr);
    // 利用者が決めた列の役割（列の役割の選択と「確認」）を、辞書より先に使う
    const ov = {};
    for (const [k, r] of Object.entries(dRoles)) ov[k] = r === 'skip' ? null : r;
    for (const [key, dd] of Object.entries(o.decisions || {})) {
      const m = /^column:デジタコ:([\s\S]*)$/.exec(key);
      if (!m || !dd) continue;
      if (dd.act === 'skip') ov[colKey(m[1])] = null;
      else if (dd.act === 'fix') { const r = resolveRole(dd.text); if (r) ov[colKey(m[1])] = r; }
    }
    ROLE_OV = Object.keys(ov).length ? ov : null;
    try { return analyzeInner(files, o, Object.keys(hRoles).length ? hRoles : null, dRoles); } finally { ROLE_OV = null; }
  }

  // 前に落としたときのファイルの順位（options.file_order。先に読んだほうが重なるマスで優先される）を保つ。同じ名前のファイルを落とし直しても、順位は変わらない。前に無かったファイルは、落とした順に後ろへ足す
  function orderFiles(files, o) {
    const prev = Array.isArray(o.file_order) ? o.file_order : null;
    if (!prev || !prev.length) return { files, changed: false };
    const at = (f) => prev.indexOf(f.name);
    const known = files.map((f, i) => [f, i]).filter(([f]) => at(f) >= 0).sort((a, b) => at(a[0]) - at(b[0]) || a[1] - b[1]).map((x) => x[0]);
    const out = known.concat(files.filter((f) => at(f) < 0));
    return { files: out, changed: out.some((f, i) => f !== files[i]) };
  }

  // 見出しのない CSV か。1 行目に日付の形の値（2026/10/01 など）があり、見出しの辞書に当たる語が 1 つも無い 3 列以上の表なら、1 行目に仮の見出し「列 1」「列 2」…の行を足して返す。違うときは null
  function headerlessRows(rows, roles) {
    if (!rows.length || blankRow(rows[0])) return null;
    const cells = rows[0].map((c) => (c || '').trim()), filled = cells.filter((c) => c);
    if (filled.length < 3 || filled.some((c) => haishaRole(c, roles) || roleOf(c))) return null;
    if (!filled.some((c) => shapeRatio([c], 'date_header') === 1)) return null;
    const w = Math.max(...rows.map((r) => r.length));
    return [Array.from({ length: w }, (_, j) => `列 ${j + 1}`)].concat(rows);
  }
  // 見出しの辞書に当たらない列（役割を聞く列）の候補を、縦の配車表の役割から選び直す。日付の列があるとき、デジタコの形（距離・停車した場所）ではなく、
  // 4 桁の番号の列は車番、漢字の名前の列は先の 2 つを発地と着地、「3t」は重さ、実・空の語は実空の印にする。当てはまらない列は候補を出さない
  function reguessHaisha(needs, dictFn, cells) {
    if (!needs.some((e) => e.suggested_role === 'date') && !cells.some((c) => dictFn(c) === 'date')) return needs;
    const taken = new Set(['date']), places = ['from', 'to'];
    return needs.map((e) => {
      if (dictFn(e.column) || e.suggested_role === 'date') return e;
      const v = e.samples || [];
      let r = null;
      if (!taken.has('plate') && shapeRatio(v, 'plate') >= 0.6 && shapeRatio(v, 'date_header') < 0.6) r = 'plate';
      else if (v.length && v.every((x) => /^[0-9]+(\.[0-9]+)?\s*(t|ｔ|トン|kg|ｋｇ)$/i.test(nfkc(x).trim()))) r = 'weight';
      else if (v.length && v.every((x) => /^(実車?|積車?|空車?|カラ|回送|空回送)$/.test(nfkc(x).trim()) || isEmptyWord(x))) r = 'mark';
      else if (shapeRatio(v, 'place_kanji') >= 0.6 && places.length) r = places.shift();
      else if (shapeRatio(v, 'flag01') < 0.6 && e.suggested_role && HAISHA_ROLE_OPTIONS.includes(e.suggested_role) && !taken.has(e.suggested_role)) r = e.suggested_role;
      if (r) taken.add(r);
      return { ...e, suggested_role: r, suggested_label_ja: r ? ROLE_LABEL_JA[r] : null, options: roleOptions('配車表').concat(roleOptions('不明').filter((o) => !HAISHA_ROLE_OPTIONS.includes(o.role) && o.role !== 'skip')) };
    });
  }

  function analyzeInner(files0, o, hRoles, dRoles) {
    const monthOpt = o.month != null ? +o.month : o.mon != null ? +o.mon : null;
    const ord = orderFiles(files0, o), files = ord.files;
    const parsed = files.map((f) => {
      let rows = parseCsv(f.text), transposed = null, headerless = false;
      const csvWarn = rows.warnings || [];
      if (!detectHaisha(rows, hRoles, monthOpt) && !detectDigitaco(rows)) { const tr = transposeDateDown(rows); if (tr) { rows = tr.rows; transposed = tr; } } // 日付が縦・車が横の配車表は、日付が横の形に置き換えて読む
      if (!transposed && !detectHaisha(rows, hRoles, monthOpt) && !detectDigitaco(rows)) { const hl = headerlessRows(rows, hRoles); if (hl) { rows = hl; headerless = true; } } // 見出しのない CSV は、仮の見出し「列 N」を 1 行目に足して、1 行目も値として読む
      return { name: f.name, encoding: f.encoding || null, rows, transposed, csvWarn, headerless };
    });
    // 車番の列が無い 1 行 = 1 便の表（1 台の会社の日報の写しなど）は、車 1 台の表として読む。車番の列を足して、全部の行を「1号車」にする
    for (const f of parsed) {
      if (f.transposed || detectHaisha(f.rows, hRoles, monthOpt) || detectDigitaco(f.rows)) continue;
      const nv = addVehicleCol(f.rows, hRoles, monthOpt);
      if (nv) { f.rows = nv; f.noPlateCol = true; }
    }
    for (const f of parsed) {
      f.hlay = detectHaisha(f.rows, hRoles, monthOpt);
      f.dlay = f.hlay ? null : detectDigitaco(f.rows);
      f.kind = f.hlay ? 'haisha' : f.dlay ? 'digitaco' : null;
    }
    const hfsAll = parsed.filter((f) => f.kind === 'haisha');
    const dfs = parsed.filter((f) => f.kind === 'digitaco');
    const otherFiles = parsed.filter((f) => !f.kind);
    const notices = [];

    // デジタコの日付（年と月を決める手掛かり）
    const dDates = [];
    for (const f of dfs) {
      const rm = f.dlay.roles, body = f.rows.slice(f.dlay.header_row + 1);
      const cs = Object.keys(rm).find((j) => rm[j] === 'start_time'), cd = Object.keys(rm).find((j) => rm[j] === 'date');
      for (const r of body) {
        let iso = null;
        if (cs != null) { const t = parseDT(r[cs] || ''); if (t != null) iso = isoOf(t).slice(0, 10); }
        if (iso == null && cd != null) iso = parseDateOnly(r[cd] || '');
        if (iso) dDates.push(iso);
      }
    }
    const dYears = dDates.map((d) => +d.slice(0, 4)), dMonths = dDates.map((d) => +d.slice(5, 7));
    // 見出しが「1」〜「31」だけで題名にも月が無いときは、デジタコの日付の月を使う
    for (const f of hfsAll) if (f.hlay.needs_month && dMonths.length) f.hlay = detectHaisha(f.rows, hRoles, +mostCommon(dMonths)) || f.hlay;
    const needsMonth = hfsAll.some((f) => f.hlay.needs_month);

    // 年。配車表の見出しの曜日（10/1(木)）と合う年を、ファイルごとに決める。時計（今日の日付）は使わない。
    // 合う年が複数あるときは、題名・ファイル名・デジタコの日付・保存した前の月（options.year_hint）にある年を先に使い、無ければ 2026 年にいちばん近い年にして、year_assumed を真にする
    const byUser = !!o.year;
    const savedYears = [].concat(o.year_hint == null ? [] : o.year_hint).map(Number).filter((y) => y >= 2000 && y <= 2100);
    const dYearList = [...new Set(dYears)].sort((a, b) => b - a);
    const yearOf = (f) => {
      const hints = [];
      const tm = f.hlay.title_month;
      if (tm && tm[0]) hints.push({ year: tm[0], source: 'title' });
      for (const y of yearsInText(f.rows.slice(0, f.hlay.header_row).map((r) => r.join(' ')).join(' '))) hints.push({ year: y, source: 'title' });
      for (const y of yearsInText(f.name)) hints.push({ year: y, source: 'file_name' });
      for (const y of dYearList) hints.push({ year: y, source: 'digitaco' });
      for (const y of savedYears) hints.push({ year: y, source: 'saved' });
      let yc = inferYear(f.hlay.heads, hints);
      if (dYears.length && !(yc.hint === 'title' || yc.hint === 'file_name')) { // デジタコの日付があるときは、その年（の前の年まで）に収まる年だけを残す
        const lo = Math.min(...dYears) - 1, hi = Math.max(...dYears);
        const keep = yc.source === 'weekday' ? yc.candidates.filter((c) => c >= lo && c <= hi) : [];
        const y0 = keep.length ? Math.max(...keep) : Math.min(...dYears);
        if (yc.source !== 'explicit') yc = { year: y0, candidates: keep.length ? keep : [y0], ambiguous: keep.length > 1, year_assumed: keep.length > 1, source: keep.length ? 'weekday' : 'digitaco', hint: keep.length ? 'digitaco' : null };
      }
      return yc;
    };
    for (const f of hfsAll) {
      const yc = yearOf(f);
      f.year = byUser ? +o.year : yc.year;
      f.yearChoice = { year: f.year, candidates: yc.candidates, ambiguous: !!yc.ambiguous, year_assumed: !byUser && !!yc.year_assumed, source: yc.source, by_user: byUser, hint: yc.hint || null };
    }
    let yc0 = null;
    if (!hfsAll.length) {
      if (dYears.length) { const y0 = +mostCommon(dYears); yc0 = { year: y0, candidates: [y0], ambiguous: false, year_assumed: false, source: 'digitaco', hint: null }; } else yc0 = inferYear([]);
      if (!byUser && yc0.year == null) { // 年が決まらない（通常は起きない）
        return { needs_year: true, failed: null, year_choice: { year: null, candidates: yc0.candidates, ambiguous: true, year_assumed: false, source: yc0.source, by_user: false }, files: parsed.map((f) => ({ name: f.name, kind: f.kind })) };
      }
    }

    // 配車表を全部読む。車番×日付が重ならない配車表（月違い・営業所違い）は合わせて使う。重なるときは、先に読んだほうだけを使い、重なりを報告する（options.haisha_use に使うファイル名を渡すと、その配車表を使う）
    for (const f of hfsAll) {
      f.wl = readHaisha(f.rows, f.year, f.hlay, hRoles, null);
      if (f.transposed) { // 置き換えた表の行・列を、元のファイルの行・列に戻す（画面の「元の行」の案内が合うように）
        const tr = f.transposed, back = (x) => { const n = x.line - 1; if (n > tr.header_row && n - tr.header_row - 1 < tr.cols.length) { x.line = tr.header_row + x.col + 1; x.col = tr.cols[n - tr.header_row - 1]; x.tr = true; } };
        f.wl.rows.forEach(back); f.wl.unreadable.forEach(back);
      }
      f.keys = new Map();
      for (const r of f.wl.rows) f.keys.set(`${r.plate}|${r.date}`, r.text);
    }
    const overlaps = [];
    for (let i = 0; i < hfsAll.length; i++) {
      for (let j = i + 1; j < hfsAll.length; j++) {
        let n = 0, same = 0;
        const pl = new Set(), ds = [];
        for (const [k, t] of hfsAll[i].keys) if (hfsAll[j].keys.has(k)) { n++; if (hfsAll[j].keys.get(k) === t) same++; pl.add(k.split('|')[0]); ds.push(k.split('|')[1]); }
        if (n) overlaps.push({ files: [hfsAll[i].name, hfsAll[j].name], cells: n, identical: n === same, plates: [...pl].sort(), from: ds.sort()[0], to: ds.sort()[ds.length - 1] });
      }
    }
    const pickList = Array.isArray(o.haisha_use) ? o.haisha_use : null;
    const used = [];
    for (const f of hfsAll) {
      if (pickList ? !pickList.includes(f.name) : false) { f.excluded = { reason: 'not_chosen', with: null }; continue; }
      const clash = pickList ? null : used.find((u) => overlaps.some((ov) => ov.files.includes(u.name) && ov.files.includes(f.name)));
      if (clash) { f.excluded = { reason: 'overlap', with: clash.name }; continue; }
      used.push(f);
    }
    const hf = used[0] || null; // 列の対応づけなどは、使う配車表の先頭で見る
    const excludedH = hfsAll.filter((f) => f.excluded);
    const overlapOf = (f) => overlaps.filter((ov) => ov.files.includes(f.name));
    const excludedReason = (f) => (f.excluded.reason === 'overlap'
      ? (overlapOf(f).filter((ov) => ov.files.includes(f.excluded.with))[0].identical
        ? `${f.excluded.with} と同じ内容なので読んでいません`
        : `${f.excluded.with} と車番・日付が重なっています（内容が違います）。どちらを使うか選べます。重ならない配車表は合わせて読みます`)
      : '使う配車表として選ばれていません');
    const haishaChoice = {
      needs_choice: overlaps.some((ov) => !ov.identical) && !pickList,
      files: hfsAll.map((f) => ({
        file: f.name, used: !f.excluded, cells: f.keys.size, plates: uniqNum(f.wl.rows.map((r) => r.plate)).length,
        from: f.keys.size ? [...f.keys.keys()].map((k) => k.split('|')[1]).sort()[0] : null, to: f.keys.size ? [...f.keys.keys()].map((k) => k.split('|')[1]).sort().slice(-1)[0] : null,
        year: f.year, reason: f.excluded ? excludedReason(f) : null,
      })),
      overlaps, used: used.map((f) => f.name),
    };
    // 使う配車表を合わせる
    let wl = null;
    if (used.length) {
      wl = { rows: [], unreadable: [], stats: { filled_rows: 0, groups: 0, plate_groups: 0, date_cols: 0, bad_cols: 0, data_rows: 0 }, plates: [], plate_notes: [], extra_cols: [], layout: used[0].wl.layout, needs_month: false, skipped_total_rows: 0, hired: [] };
      for (const f of used) {
        const w = f.wl;
        wl.rows.push(...w.rows); wl.skipped_total_rows += w.skipped_total_rows; wl.needs_month = wl.needs_month || w.needs_month;
        for (const k of Object.keys(wl.stats)) wl.stats[k] += w.stats[k] || 0;
        for (const pp of w.plates) if (!wl.plates.some((x) => x.plate === pp.plate)) wl.plates.push(pp);
        for (const n of w.plate_notes) if (!wl.plate_notes.some((x) => x.num === n.num && x.kind === n.kind)) wl.plate_notes.push(n);
        for (const h of w.hired || []) wl.hired.push({ ...h, file: f.name });
      }
    }
    const hrows = hf ? hf.rows : [];

    // 地点
    const places = {}, nameCount = new Map(), mergedNames = new Map();
    const mergePlaces = o.merge_places !== false;
    const newAlias = { haisha: 0, digitaco: 0 };
    let resolveName;
    if (o.places) {
      Object.assign(places, JSON.parse(JSON.stringify(o.places)));
      const known = ((o.aliases || {}).known || []).slice().sort((a, b) => b[0].length - a[0].length);
      const proposed = (o.aliases || {}).proposed || [];
      resolveName = (name) => {
        for (const [al, pid] of known) if (name.includes(al)) return [pid, false];
        // 確認待ちの別名は、確定するまで地点に当てない（[別名, 当てる先, 確定までの地点]。確定までの地点が無ければ、地点なし）
        for (const [al, , hold] of proposed) if (name.includes(al)) return [hold || null, true];
        return [null, false];
      };
    } else {
      const byKey = new Map();
      const ua = Object.entries(o.userAliases || {}).map(([k, v]) => [nfkc(k).trim(), nfkc(v).trim()]).filter(([k]) => k).sort((a, b) => b[0].length - a[0].length);
      resolveName = (name) => {
        const t = nfkc(name).trim().replace(/\s+/g, ' ');
        if (!t) return [null, false];
        let target = t;
        for (const [al, to] of ua) if (t.includes(al)) { target = to; break; }
        const res = o.munis && o.munis.length ? matchMuni(target, o.munis) : { reason: '地図の市区町村の表がありません' };
        const key = res.m ? 'm' + (res.m.code || res.m.name) : 'n' + (mergePlaces ? coreName(t) : t);
        if (!byKey.has(key)) {
          const nn = pad(Object.keys(places).length + 1), id = `p${nn}`;
          places[id] = res.m
            ? { id, pseudo: `地点 ${nn}`, short: res.m.short, muni: res.m.name, label: t, code: res.m.code, pref: res.m.pref, lon: res.m.lon, lat: res.m.lat, aliases: [t], placed: true, note: res.m.note || null }
            : { id, pseudo: `地点 ${nn}`, short: (mergePlaces ? coreName(t) : t).slice(0, 6), muni: '', label: t, code: null, pref: null, lon: null, lat: null, aliases: [t], placed: false, reason: res.reason };
          byKey.set(key, id);
        }
        const id = byKey.get(key);
        if (!places[id].aliases.includes(t)) places[id].aliases.push(t);
        if (!res.m) { if (!mergedNames.has(id)) mergedNames.set(id, new Set()); mergedNames.get(id).add(t); }
        return [id, false];
      };
    }
    const resolve = (name, src) => {
      if (!name || isNonPlace(name)) return null; // 重さ・時刻の形の語は、地点にも別名にもしない
      const [pid, isNew] = resolveName(name);
      if (isNew && src) newAlias[src]++;
      if (pid) nameCount.set(pid, (nameCount.get(pid) || 0) + 1);
      return pid;
    };

    // 配車表の区間
    const unreadable = [];
    let unreadableRows = 0; // 行（セル）ごとに読めなかった件数。ファイルごと読めなかったものは入れない
    const haishaLegs = [];
    let nonlegCells = 0, droppedOverlap = 0;
    const timeDropped = [];
    const priorKeys = new Set();
    let hseq = 0;
    for (const f of used) {
      const vert = f.hlay.kind === 'vertical';
      for (const u of f.wl.unreadable) { unreadable.push({ ...u, file: f.name }); unreadableRows++; }
      const thisKeys = new Set();
      for (const r of f.wl.rows) {
        const key = `${r.plate}|${r.date}`;
        if (priorKeys.has(key)) { droppedOverlap++; continue; } // 選んだ配車表どうしで重なるセルは、先に読んだほうだけを使う
        thisKeys.add(key);
        const pc = r.parsed || parseCell(r.text);
        if (pc.errors.length) { unreadable.push({ line: r.line, col: r.col, original: r.text, reason: pc.errors[0], file: f.name, plate: r.plate }); unreadableRows++; continue; }
        if (pc.nonleg) { nonlegCells++; continue; }
        const where = vert ? `${r.line} 行目` : r.tr ? `${r.line} 行目・${r.col + 1} 列目（日付が縦の表）` : `${r.line} 行目・${+r.date.slice(5, 7)}/${+r.date.slice(8, 10)} の列`;
        if (pc.warnings && pc.warnings.length) timeDropped.push({ file: f.name, plate: r.plate, date: r.date, where, text: r.text });
        pc.legs.forEach((lg, idx) => {
          const rounds = lg.rounds || 1;
          for (let rd = 1; rd <= rounds; rd++) {
            hseq++;
            const ids = [lg.frm, lg.to].map((nm) => (nm ? resolve(nm, 'haisha') : null));
            haishaLegs.push({
              id: `H${pad(hseq, 3)}`, plate: r.plate, date: r.date, idx, frm: ids[0], to: ids[1], empty: lg.empty, arr: lg.arr, weight_kg: lg.weight_kg, weights: lg.weights || null,
              local: !!lg.local, loaded_mark: !!lg.loaded_mark, ...(lg.weak_empty ? { weak_empty: true } : {}), ...(lg.km_file != null ? { km_file: lg.km_file } : {}), ...(lg.dep_hint ? { dep_hint: lg.dep_hint } : {}), note: lg.note || null, round: rd, rounds, ref: { file: f.name, where, text: r.text },
            });
          }
        });
      }
      for (const k of thisKeys) priorKeys.add(k);
    }

    // 車番: 配車表で完全な書き方（品川100あ1234）が違う車は別の車。デジタコの車番（ファイル名か列の 4 桁）は、同じ 4 桁の車のうち、4 桁だけの車、無ければ先に出る車に当てる
    const idsByNum = {};
    if (wl) for (const p of wl.plates) { const n = normalizePlate(p.plate); (idsByNum[n] = idsByNum[n] || []).push(p.plate); }
    const plateAmbig = [];
    const resolvePlate = (num, file) => {
      const ids = idsByNum[num];
      if (!ids) return num;
      if (ids.length === 1) return ids[0];
      const pick = ids.includes(num) ? num : ids[0];
      if (file && !plateAmbig.some((a) => a.file === file && a.num === num)) plateAmbig.push({ file, num, ids: ids.slice(), pick });
      return pick;
    };

    // デジタコ（ファイル名の 4 桁が車番。日ごとの集計は車番の列があればそれを使う。車両の並びは配車表の順）
    const order = wl ? wl.plates.map((p) => p.plate) : [];
    const dInfo = dfs.map((f) => {
      const usePlateCol = Object.values(f.dlay.roles).includes('plate'); // 車番の列があれば（辞書で分かった列も、利用者が選んだ列も）、ファイル名の車番は要らない
      const nameNum = normalizePlate(f.name.replace(/\.[^.]*$/, ''));
      return { f, usePlateCol, plate: usePlateCol || !nameNum ? null : resolvePlate(nameNum, f.name) };
    });
    const noPlate = dInfo.filter((x) => !x.plate && !x.usePlateCol);
    const dOk = dInfo.filter((x) => x.plate || x.usePlateCol).sort((a, b) => {
      const ia = a.plate ? order.indexOf(a.plate) : -2, ib = b.plate ? order.indexOf(b.plate) : -2;
      return (ia < 0 ? 1e9 : ia) - (ib < 0 ? 1e9 : ib) || cmp(a.f.name, b.f.name);
    });
    const digValues = {};
    const parts = [];
    for (const { f, plate, usePlateCol } of dOk) {
      const hdr = f.dlay.header_row, hrow = f.rows.slice(hdr);
      const head = hrow[0] || [];
      const recs = hrow.slice(1).filter((r) => !blankRow(r));
      head.forEach((h, ci) => { if (!digValues[h]) digValues[h] = []; for (const r of recs) digValues[h].push(r[ci] == null ? '' : r[ci]); });
      const pr = f.dlay.kind === 'summary' ? parseDigitacoSummary(hrow, plate, undefined, hdr) : parseDigitaco(hrow, (nm) => resolve(nm, 'digitaco'), plate, undefined, hdr);
      if (usePlateCol) for (const x of pr.runs.concat(pr.stops)) x.plate = resolvePlate(x.plate, f.name);
      const plates = uniqNum((plate ? [plate] : []).concat(pr.runs.map((x) => x.plate)).concat(pr.stops.map((x) => x.plate)));
      parts.push({ file: f.name, plate, plates, kind: f.dlay.kind, restraints: pr.restraints || [], runs: pr.runs, stops: pr.stops, errors: pr.errors, head, recs, hdr, encoding: f.encoding, nruns: pr.runs.length, nstops: pr.stops.length });
    }
    // 同じ走行・停車の行（同じデジタコを 2 回落とした場合など）は 1 回だけ数える
    const dd = dedupeDigitaco(parts.map((p) => ({ file: p.file, plate: p.plate, runs: p.runs, stops: p.stops })));
    parts.forEach((p, i) => { p.runs = dd.parts[i].runs; p.stops = dd.parts[i].stops; });
    const dupN = dd.dropped.runs + dd.dropped.stops;
    if (dupN) notices.push({ kind: 'duplicate', text_ja: `同じ走行・停車の行 ${dupN} 件（走行 ${dd.dropped.runs}、停車 ${dd.dropped.stops}）は、1 回だけ数えました（同じデジタコを 2 回落としたときなど）。` });
    const runs = [], stops = [], dFiles = [], summaryRuns = [], restraintRows = [];
    let dseq = 0, sseq = 0;
    for (const p of parts) {
      for (const rr of p.restraints) restraintRows.push({ ...rr, file: p.file });
      for (const x of p.runs) {
        dseq++; x.id = `D${pad(dseq, 3)}`;
        x.ref = { file: p.file, where: `${x.line} 行目`, text: x.summary ? `日ごとの集計 ${x.jk === 1 ? '実車' : '空車'} ${pyFloat(x.km)} km` : `走行 ${x.start.slice(11, 16)}〜${x.end.slice(11, 16)} ${x.km == null ? 'None' : pyFloat(x.km)} km` };
        (x.summary ? summaryRuns : runs).push(x);
      }
      for (const x of p.stops) { sseq++; x.id = `S${pad(sseq, 3)}`; x.ref = { file: p.file, where: `${x.line} 行目`, text: `停車 ${x.start.slice(11, 16)}〜${x.end.slice(11, 16)}（${x.minutes} 分）` }; stops.push(x); }
      for (const e of p.errors) { unreadable.push({ line: e.line, col: null, original: '', reason: e.reason, file: p.file, plate: p.plate, where: `${e.line} 行目` }); unreadableRows++; }
      dFiles.push({ file: p.file, plate: p.plate, plates: p.plates, rows: p.recs.length, errors: p.errors.length, columns: p.head.slice(), encoding: p.encoding, kind: p.kind, nruns: p.nruns, nstops: p.nstops, hdr: p.hdr });
    }
    if (summaryRuns.length) {
      if (hf) notices.push({ kind: 'summary', text_ja: '日ごとの集計のデジタコは、配車表の区間とは別に数えると二重になるので、実車率の計算には入れていません。デジタコの明細（1 回ごとの走行・停車）の形なら、配車表と結び付けて使います。' });
      else runs.push(...summaryRuns);
    }
    // 読めなかったファイルの理由。上の知らせ（unreadable）・ファイルごとの判定（files）・読めなかった一覧（files_unread）で、同じ理由の文を使う
    const NO_PLATE_REASON = 'ファイル名から車番が分かりません';
    const NO_PLATE_HOWTO = '（digitaco_1234.csv のように車番 4 桁をファイル名に入れるか、車番の列の役割を「車番」にしてください）';
    const fileReason = {};
    for (const x of noPlate) {
      fileReason[x.f.name] = NO_PLATE_REASON;
      unreadable.push({ line: 1, col: null, original: x.f.name, reason: NO_PLATE_REASON + NO_PLATE_HOWTO, file: x.f.name, where: 'ファイル名' });
    }
    // 読めなかったファイル: 見出しとして見た行と、どう直せば読めるかの例を添える
    const decided = (h) => { const k = colKey(h); return (hRoles && Object.prototype.hasOwnProperty.call(hRoles, k)) || Object.prototype.hasOwnProperty.call(dRoles, k); };
    const judged = {};
    const dictRole = (h) => { const r = haishaRole(h); if (r) return r === 'plate2' || r === 'plate3' ? 'plate' : r; return roleOf(h) || null; }; // 見出しの辞書で決まる役割（配車表の辞書が先、次にデジタコの辞書）
    const CHOOSE_REASON = '列の役割が決まっていません（画面で各列が何かを選ぶと読めます）';
    const UNKNOWN_REASON = '配車表にもデジタコにも見えません';
    const BINARY_REASON = 'CSV ではありません（文字ではないデータです。画像・PDF・Excel のファイルそのものは読みません）';
    const binaryNames = new Set(files.filter((f) => looksBinary(f.text)).map((f) => f.name)); // 文字の大半が制御文字や置き換え文字のファイルは、列の役割を聞かない
    for (const f of otherFiles) {
      const hi = judgedRow(f.rows), cells = (f.rows[hi] || []).map((c) => (c || '').trim());
      if (binaryNames.has(f.name)) {
        judged[f.name] = { hi, cells, needs: [], binary: true };
        fileReason[f.name] = BINARY_REASON;
        unreadable.push({ line: 1, col: null, original: '', reason: BINARY_REASON, file: f.name, where: 'ファイル全体', howto_ja: HOWTO_UNREADABLE_JA });
        continue;
      }
      const needs = reguessHaisha(needingRoles('不明', f.name, cells, f.rows.slice(hi + 1), (h) => decided(h), new Set(), true, hi + 1, dictRole), dictRole, cells);
      judged[f.name] = { hi, cells, needs };
      const sug = new Set(needs.map((e) => e.suggested_role).filter((r) => r)); // 列の役割を選べば読める形になりそうなときだけ「役割を選ぶ」と案内する
      const chooseable = (sug.has('start_time') && sug.has('end_time')) || (sug.has('date') && (sug.has('loaded_km') || sug.has('empty_km'))) || (sug.has('date') && sug.has('plate') && (sug.has('from') || sug.has('to')));
      fileReason[f.name] = chooseable ? CHOOSE_REASON : UNKNOWN_REASON;
      unreadable.push({
        line: hi + 1, col: null, original: cells.slice(0, 6).join('、'),
        reason: chooseable ? CHOOSE_REASON : UNKNOWN_REASON + '（車番と日付の列、または開始・終了の日時の列が必要です）', file: f.name,
        where: `${hi + 1} 行目（見出しとして見た行）`, judged_line: hi + 1, judged_cells: cells.slice(0, 8), howto_ja: HOWTO_UNREADABLE_JA,
      });
    }
    for (const f of excludedH) {
      fileReason[f.name] = excludedReason(f);
      unreadable.push({ line: 1, col: null, original: (f.rows[0] || []).slice(0, 6).join('、'), reason: `この配車表は読んでいません。${excludedReason(f)}`, file: f.name, where: '見出しの行' });
    }

    // 車両
    const vlist = [];
    const addPlate = (plate, raw, type) => { if (!vlist.some((v) => v.plate === plate)) vlist.push({ plate, raw, type }); };
    if (wl) for (const p of wl.plates) addPlate(p.plate, p.raw, p.type);
    for (const d of dFiles) for (const pl of d.plates) addPlate(pl, pl, '');
    const dvals = (plate) => dFiles.find((d) => d.plates.includes(plate));
    const firstDriver = (plate) => { const r = runs.find((x) => x.plate === plate); return r ? r.driver : null; };
    const dl = o.driverLabels || {};
    const givenV = {};
    for (const v of o.vehicles || []) givenV[v.plate] = v;
    const largeKg = o.largeMaxLoadKg || LARGE_ASSUMED_KG;
    const vehicles0 = vlist.map(({ plate, raw, type }) => {
      if (givenV[plate]) return { registered: true, ...givenV[plate] };
      const cls = classOf(type), digital = !!dvals(plate), drv = firstDriver(plate);
      return {
        plate, raw_plate: raw, type_raw: type, cls, gvw_kg: null, max_load_kg: cls === '大型' ? largeKg : null, registered: false, max_load_assumed: cls === '大型',
        tachograph: digital ? 'digital' : 'analog', tachograph_label: TACHO_LABEL[digital ? 'digital' : 'analog'], driver: drv ? (dl[drv] || drv) : null,
      };
    });
    // 車ごと・車格ごとに利用者が決めた最大積載量（kg）。車ごとが車格ごとより先。率と、最大積載量を超える重さのカードに使う
    let vehicles = applyMaxPayload(vehicles0, o.max_payload_by_plate, o.max_payload_by_class);

    // 基点と主な行き先
    let base = o.base, dock = o.dock;
    let baseBasis = null; // 車庫をどの地点にしたかと、その理由（画面に 1 行で出す）
    if (!base) {
      const fr = haishaLegs.map((l) => l.frm).filter(Boolean);
      base = mostCommon(fr) || null;
      if (base) {
        const cnt = new Map();
        for (const x of fr) cnt.set(x, (cnt.get(x) || 0) + 1);
        const second = [...cnt.entries()].filter(([k]) => k !== base).sort((a, b) => b[1] - a[1])[0] || null;
        baseBasis = { kind: 'haisha_from', count: cnt.get(base), total: fr.length, second: second ? { place: second[0], count: second[1] } : null };
      } else {
        const rf = runs.map((r) => r.frm).filter(Boolean);
        base = mostCommon(rf) || null;
        if (base) baseBasis = { kind: 'digitaco_from', count: rf.filter((x) => x === base).length, total: rf.length, second: null };
      }
    } else baseBasis = { kind: 'given', count: null, total: null, second: null };
    if (!dock) {
      const to = haishaLegs.filter((l) => l.to && l.to !== base).map((l) => l.to);
      dock = mostCommon(to) || mostCommon(runs.map((r) => r.to).filter((x) => x && x !== base)) || null;
    }

    // 距離の推定（デジタコが無い区間）
    const estSrc = o.estSrc || 'OSRM 推定';
    const estFn = (ends) => {
      const key = ends.slice().sort().join('|');
      if (o.estKm && o.estKm[key] != null) return { km: o.estKm[key], src: estSrc };
      const a = places[ends[0]], b = places[ends[1]];
      if (!a || !b) return null;
      if (o.estKmFn) { const r = o.estKmFn(a, b); if (r) return r; }
      const dk = a.code && b.code ? DEFAULT_KM[[a.code, b.code].sort().join('|')] : null;
      if (dk != null && !o.estKm) return { km: dk, src: 'OSRM 推定' };
      if (o.estKm) return null; // 試験用に推定の表を渡したときは、その表にある組だけ
      if (a.lon == null || b.lon == null) return null;
      return { km: pyRound(haversine(a, b) * ROAD_FACTOR, 1), src: `直線距離 × ${ROAD_FACTOR} の推定` };
    };

    // 結び付けと実車・空車
    const lk = link(haishaLegs, runs, estFn);
    const cl = classifyLegs(lk.legs, base);
    const legs = cl.legs;
    // 仮定した最大積載量を超える重さの記録があれば、仮定を上げて黙って外さない（利用者が最大積載量を決めたときは、決めた値のまま）
    let maxRaised = null;
    if (!o.largeMaxLoadKg) { const rr = raiseAssumedMax(vehicles, legs); if (rr.note) { vehicles = rr.vehicles; maxRaised = rr.note; } }
    // 向きは車庫を先に見る（車庫へ戻る区間は、主な行き先が着地でも「帰り」）。車庫が無いときだけ、主な行き先から決める
    for (const l of legs) {
      if (base && l.to === base && l.frm !== base) l.dir = 'back';
      else if (base && l.frm === base) l.dir = 'out';
      else l.dir = dock && l.to === dock ? 'out' : dock && l.frm === dock ? 'back' : (base && l.to === base ? 'back' : null);
    }
    legs.sort((a, b) => cmp(a.date, b.date) || cmp(a.plate, b.plate) || cmp(a.dir === 'out' ? 0 : 1, b.dir === 'out' ? 0 : 1));
    legs.forEach((l, i) => { l.id = `L${pad(i + 1, 3)}`; });
    const decOf = (key) => (o.decisions || {})[key] || null;
    const legNote = {}; // 確認の決定のうち、区間に当てたもの { key: { applied, note } }
    for (const l of legs) {
      for (const fl of l.flags) {
        const key = `link:${l.id}:${fl.type}`, dd2 = decOf(key);
        if (!dd2) continue;
        let applied = false, note = '';
        if (dd2.act === 'ok') note = '確定しました（値は変えていません）';
        else if (fl.type === 'distance') {
          if (dd2.act === 'skip') {
            const ends = endsOfLeg(l), est = ends.length === 2 ? estFn(ends) : null;
            if (est) { l.km = est.km; l.sources.km = est.src; applied = true; note = `デジタコの距離を読まず、${est.src}の ${pyFloat(est.km)} km を使いました`; } else note = '代わりの推定距離がないので、デジタコの距離のままです';
          } else {
            const v = parseKm(dd2.text);
            if (v != null) { l.km = v; l.sources.km = '利用者の修正'; applied = true; note = `距離を ${pyFloat(v)} km にしました`; } else note = '距離を数で入れてください（例: 551.8）。値は変えていません';
          }
        } else if (dd2.act === 'skip') { l.arr = null; l.sources.time = '配車表'; applied = true; note = 'デジタコの到着時刻を読まず、配車表の着の時刻を使います（実車率などは変わりません）'; }
        else {
          const t = parseTime(dd2.text);
          if (t) { l.arr = (l.arr ? l.arr.slice(0, 11) : `${l.date}T`) + t; l.sources.time = '利用者の修正'; applied = true; note = `到着時刻を ${t} にしました（実車率などは変わりません）`; } else note = '時刻を 5:30 のように入れてください。値は変えていません';
        }
        legNote[key] = { applied, note };
      }
    }
    for (const l of legs) {
      // 曜日は、その区間の便自身が出た日（行きも帰りも）。帰りを前の日の行きに結び付けない（同じ日に行って帰る便が、前の日の曜日に入っていた）。
      // 帰りの便の実出発（dep）が無いときは記録日。cycle_dep は、その便自身の出発（夜に出たかの判定に使う）
      l.cycle_wd = l.dir === 'out' ? weekdayOf(l.date) : (returnWhen(l)[2] ?? weekdayOf(l.date));
      l.cycle_dep = l.dep || null;
      l.color = l.flags.some((fl) => !decOf(`link:${l.id}:${fl.type}`)) ? 'yellow' : l.kind === 'unknown' ? 'gray' : 'green';
    }
    const legByDid = {};
    for (const l of legs) { if (l.did) legByDid[l.did] = l; for (const x of l.dids || []) legByDid[x] = l; }
    for (const s of stops) {
      const prev = runs.filter((r) => r.plate === s.plate && r.end === s.start);
      s.cycle_wd = prev.length && legByDid[prev[0].id] ? legByDid[prev[0].id].cycle_wd : null;
    }

    // 拘束時間
    const shifts = [];
    let kseq = 0;
    for (const v of vehicles) {
      const mine = runs.filter((r) => r.plate === v.plate);
      // 日ごとの集計に「拘束時間」の列があり、走行・停車の明細が無い車は、その列の値をそのまま拘束時間にする（出庫〜帰庫の幅で数えない）
      const rs = restraintRows.filter((r) => r.plate === v.plate);
      if (rs.length && !runs.some((r) => r.plate === v.plate && !r.summary) && !stops.some((s) => s.plate === v.plate)) {
        for (const r of rs.slice().sort((a, b) => cmp(a.date, b.date) || a.line - b.line)) {
          kseq++;
          const sr = summaryRuns.find((x) => x.plate === v.plate && x.line === r.line && !x.time_unknown);
          const st = sr ? sr.start : `${r.date}T00:00`;
          const en = isoOf(parseDT(st) + pyRound(r.hours * 60));
          const drv = r.driver;
          shifts.push({ start: st, end: en, hours: r.hours, id: `K${pad(kseq, 3)}`, driver: drv, driver_label: dl[drv] || drv, plate: v.plate, ref: { file: r.file, where: `${r.line} 行目`, text: '拘束時間の列' } });
        }
        continue;
      }
      if (!mine.length) continue;
      const evs = mine.map((r) => ({ start: r.start, end: r.end, line: r.line })).concat(stops.filter((s) => s.plate === v.plate).map((s) => ({ start: s.start, end: s.end, line: s.line })));
      const drv = mine[0].driver, sh = buildShifts(evs);
      for (const k of kousokuHours(sh)) {
        kseq++;
        const lines = (sh.find((x) => x.start === k.start).lines).filter((n) => n);
        const { lines: _drop, ...kk } = k;
        shifts.push({ ...kk, id: `K${pad(kseq, 3)}`, driver: drv, driver_label: dl[drv] || drv, plate: v.plate, ref: { file: dFiles.find((d) => d.plates.includes(v.plate)).file, where: `${Math.min(...lines)}〜${Math.max(...lines)} 行目` } });
      }
    }

    // 重さの確認（最大積載量を超える・0t・1 セルに複数）: 確認のカードを作り、利用者の決定を当ててから率を計算する
    const wItems = weightConfirmItems(legs, vehicles), wNote = {};
    const capOf = {};
    for (const v of vehicles) capOf[v.plate] = v.max_load_kg;
    for (const it of wItems) {
      const dd2 = decOf(it.key), l = legs.find((x) => x.id === it.leg);
      if (!dd2) continue;
      let applied = false, note = '';
      if (dd2.act === 'ok') note = '確定しました（率に入れないままです）';
      else if (dd2.act === 'skip') { l.weight_kg = null; l.weights_multi = null; l.sources.weight = '読まない'; applied = true; note = 'この重さを読まないことにして、計算し直しました（積載率には入れません）'; }
      else {
        const w = parseWeight(dd2.text);
        if (w && w.kg > 0) { l.weight_kg = w.kg; l.weights_multi = null; l.sources.weight = '利用者の修正'; applied = true; note = `重さを ${tons(w.kg)}t にしました。` + (capOf[l.plate] && w.kg > capOf[l.plate] ? 'まだ最大積載量を超えているので、率には入れません' : '積載率に入れました'); }
        else note = '重さを 4.6t や 4600kg のように入れてください（記号は付けません）。値は変えていません';
      }
      wNote[it.key] = { applied, note };
    }
    // 車庫へ戻る区間で、空の印も重さも無いもの: 実車にしないで不明にした。利用者が「空」か「実車」と決めたら、その区間すべてに当てる
    const ruLegs = legs.filter((l) => l.return_unmarked);
    const RU_KEY = 'return-unmarked', ruDec = decOf(RU_KEY), DEST_KEY = 'dest-missing', UNPLACED_KEY = 'place-unplaced';
    let ruNote = { applied: false, note: '' };
    if (ruLegs.length && ruDec) {
      if (ruDec.act === 'ok' || ruDec.act === 'skip') ruNote.note = '確定しました（実車か空車か分からないまま、実車率には入れません）';
      else {
        const t = nfkc(ruDec.text || '');
        const k = /空/.test(t) ? 'empty' : /実|積/.test(t) ? 'loaded' : null;
        if (k) { for (const l of ruLegs) { l.kind = k; l.kind_src = '利用者の修正'; l.sources.kind = '利用者の修正'; } ruNote = { applied: true, note: `${ruLegs.length} 件を${KIND_JA[k]}にしました` }; } else ruNote.note = '「空」か「実車」と入れてください。値は変えていません';
      }
    }
    const kc = { loaded: 0, empty: 0, unknown: 0 };
    for (const l of legs) kc[l.kind]++;
    const estLabel = o.estKm ? 'OSRM の推定' : '推定';
    const kp = kpis(legs, vehicles);
    const table = weekdayTable(legs, stops, dock);
    const pt = pairTables(legs, places, vehicles, base, dock);
    const ymMost = mostCommon(haishaLegs.map((l) => l.date.slice(0, 7)));
    const mon = legs.length ? o.mon || (ymMost ? +ymMost.slice(5, 7) : +(mostCommon(runs.map((r) => +r.start.slice(5, 7))) || 0)) : null;
    const primaryF = used[0] || hfsAll[0] || null;
    const year = o.mon != null || !ymMost ? (primaryF ? primaryF.year : byUser ? +o.year : yc0.year) : +ymMost.slice(0, 4);
    const yearChoice = primaryF ? primaryF.yearChoice : { year, candidates: yc0.candidates, ambiguous: !!yc0.ambiguous, year_assumed: !byUser && !!yc0.year_assumed, source: yc0.source, by_user: byUser, hint: null };
    const ins = insights({ legs, stops, shifts, vehicles, places, base, dock, month: mon, est_label: estLabel, own_names: !o.places });

    // 列の対応づけ
    const { columns, items: colItems } = buildColumns(wl, hrows, digValues, hf, dRoles);

    // 確認待ち
    const confirm = [];
    const aliasTotal = newAlias.haisha + newAlias.digitaco;
    if (aliasTotal && o.places && dock && places[dock]) {
      confirm.push({
        id: 'C1', key: 'alias:辰巳', effect: 'none', kind: 'alias', source: '配車表・デジタコ', title: '地点の別名「辰巳」', original: `「辰巳」（配車表 ${newAlias.haisha} か所、デジタコ ${newAlias.digitaco} か所）`,
        proposal: `${places[dock].pseudo}（${places[dock].label}）の別名にする`, confidence: 0.7, color: 'yellow',
        formula: `登録済みの別名にありません。${places[dock].pseudo} の住所「江東区辰巳」に含まれるため、ローカルの規則で候補にしました（AI は使っていません）`, claude: null, count: aliasTotal,
      });
    }
    for (const it of colItems) if (it.color === 'yellow') confirm.push(it);
    // 車番の末尾 4 桁が重なる書き方
    for (const n of wl ? wl.plate_notes : []) {
      const split = n.kind === 'split', attr = n.kind === 'attr';
      confirm.push({
        id: '', key: `plate:${n.num}`, effect: 'none', kind: 'plate', source: '配車表', title: `車番の末尾 4 桁 ${n.num} が重なっています`,
        original: `配車表に ${n.forms.map((x) => `「${x}」`).join('、')} があります`,
        proposal: attr ? '末尾 4 桁は同じですが、車格か運転者が違うので、別の車として数えました（同じ車なら、配車表の車番の書き方と車格をそろえてください）'
          : split ? '地名・分類番号・かなが違うので、別の車として数えました' : '末尾の 4 桁が同じなので、同じ車として数えました（別の車なら、配車表の車番を完全な書き方にそろえてください）',
        confidence: null, color: 'yellow',
        formula: attr ? '末尾 4 桁が同じでも、車格か運転者が違うときは、別の車として数えます' : '末尾 4 桁だけで同じ車と決めるのは、完全な書き方が同じ（または片方が 4 桁だけ）のときに限ります', claude: null,
      });
    }
    for (const a of plateAmbig) {
      confirm.push({
        id: '', key: `plate-digitaco:${a.file}`, effect: 'none', kind: 'plate', source: 'デジタコ', title: `${a.file} の車番 ${a.num} に当たる車が複数あります`,
        original: `配車表に ${a.ids.map((x) => `「${x}」`).join('、')} の ${a.ids.length} 台があります`, proposal: `「${a.pick}」のデジタコとして読みました`,
        confidence: null, color: 'yellow', formula: 'ファイル名の 4 桁は、4 桁だけの車、無ければ配車表で先に出る車に当てます', claude: null,
      });
    }
    for (const l of legs) {
      for (const fl of l.flags) {
        let orig, prop;
        if (fl.type === 'time') { orig = `配車表の着 ${fl.hint} と、デジタコの到着 ${fl.digitaco} が ${Math.abs(fl.diff_min)} 分ずれています`; prop = '時刻はデジタコの値を使う'; }
        else { orig = `デジタコの走行距離 ${pyFloat(fl.digitaco_km)} km が、${estLabel} ${pyFloat(fl.est_km)} km より ${pyRound(fl.ratio * 100)}% ${fl.digitaco_km >= fl.est_km ? '長く' : '短く'}なっています`; prop = '距離はデジタコのメーターを使う（迂回した可能性）'; }
        confirm.push({
          id: '', key: `link:${l.id}:${fl.type}`, effect: fl.type === 'distance' ? 'kpi' : 'display', kind: 'link', source: '配車表とデジタコ', title: `${md(l.date)} 車番 ${l.plate} の${l.dir === 'out' ? '行き' : '帰り'}（${fl.type === 'time' ? '時刻' : '距離'}）`,
          original: orig, proposal: prop, confidence: null, color: 'yellow', leg: l.id, formula: '距離が 15% 以上、時刻が 60 分以上ずれたら確認待ちに入れます', claude: null,
        });
      }
    }
    if (ruLegs.length) {
      const ex = ruLegs.slice(0, 3).map((l) => `${md(l.date)} 車番 ${l.plate}`).join('、');
      confirm.push({
        id: '', key: RU_KEY, effect: 'kpi', kind: 'return', source: '配車表', title: `戻りの区間 ${ruLegs.length} 件が、実車か空車か分かりません（要確認）`,
        original: `${(places[base] || {}).label || '車庫'}へ戻る区間や、「帰り」「戻り」とだけ書かれた区間で、空の印（「空」「帰り空」「回送」など）も重さも書かれていないものが ${ruLegs.length} 件あります（${ex}${ruLegs.length > 3 ? ' ほか' : ''}）`,
        proposal: '実車か空車か分からないので、実車率には入れない（空車なら「空」、積んでいたなら「実車」と修正してください）', confidence: null, color: 'yellow',
        formula: '車庫へ戻る区間は空車のことが多いので、印も重さも無いものを実車とは数えません', claude: null, legs: ruLegs.map((l) => l.id), count: ruLegs.length,
      });
    }
    // 積地か降ろし地が書かれていない配車表の区間（「加古川 → 4.6t」）は、実車か空車か分からないので不明にした。実車率には入れない
    const endsMissing = o.places ? [] : legs.filter((l) => l.hid != null && l.kind === 'unknown' && !l.empty_mark && !l.has_ends && !l.return_unmarked && l.jk == null); // 地点を決めて渡す試験（サンプルの再現）では出さない
    if (endsMissing.length) {
      const ex = endsMissing.slice(0, 3).map((l) => `${md(l.date)} 車番 ${l.plate} 「${l.ref ? l.ref.text : ''}」`).join('、');
      confirm.push({
        id: '', key: DEST_KEY, effect: 'kpi', kind: 'dest', source: '配車表', title: `降ろし地か積地が書かれていない区間 ${endsMissing.length} 件が、実車か空車か分かりません`,
        original: `配車表のセルで、矢印の前か後ろの地名が無い区間が ${endsMissing.length} 件あります（${ex}${endsMissing.length > 3 ? ' ほか' : ''}）。矢印の後ろの「4.6t」は重さとして読み、地名にはしていません`,
        proposal: '荷を積んで行ったか分からないので、実車率には入れない（元の配車表に降ろし地を書き足すと、実車として数えます）', confidence: null, color: 'yellow',
        formula: '積地と降ろし地の両方がある区間だけを実車として数えます。片方が無い区間は、空の印が無ければ不明にします', claude: null, legs: endsMissing.map((l) => l.id), count: endsMissing.length,
      });
    }
    // 地図に置けない地点（遠い県の名前だけ、など）を通る区間は、距離が分からず実車率に入らない。黙って落とさずに知らせる
    const unplacedLegs = o.places ? [] : legs.filter((l) => !l.km && [l.frm, l.to].some((id) => id && places[id] && places[id].placed === false));
    if (unplacedLegs.length) {
      const names = uniq(unplacedLegs.flatMap((l) => [l.frm, l.to]).filter((id) => id && places[id] && places[id].placed === false).map((id) => places[id].label));
      const ex = unplacedLegs.slice(0, 3).map((l) => `${md(l.date)} 車番 ${l.plate}`).join('、');
      confirm.push({
        id: '', key: UNPLACED_KEY, effect: 'kpi', kind: 'place', source: '配車表', title: `地図に置けない地点を通る区間 ${unplacedLegs.length} 件は、距離が分からず実車率に入っていません`,
        original: `地点 ${names.slice(0, 5).map((x) => `「${x}」`).join('')}${names.length > 5 ? ' ほか' : ''} を市区町村に置けませんでした（${ex}${unplacedLegs.length > 3 ? ' ほか' : ''}）`,
        proposal: '「東京都江東区」のように市区町村まで書くか、「地図に置く」で市区町村を選ぶと、距離を見積もって実車率に入れます。置くまでは、これらの区間を率から外したままにします', confidence: null, color: 'yellow',
        formula: '距離は、両端の市区町村の代表点の直線距離 × 1.25 で見積もります。置けない地点がある区間は見積もれません', claude: null, legs: unplacedLegs.map((l) => l.id), count: unplacedLegs.length,
      });
    }
    confirm.push(...wItems);
    confirm.forEach((it, n) => { it.id = `C${n + 1}`; });
    for (const c of confirm) { // 利用者の決定を添える（カードは決定のあとも残す。取り消せるように）
      const dd2 = decOf(c.key);
      if (!dd2) { c.decision = null; continue; }
      let applied = false, note = '';
      if (c.kind === 'link') ({ applied, note } = legNote[c.key] || { applied: false, note: '' });
      else if (c.kind === 'weight') ({ applied, note } = wNote[c.key] || { applied: false, note: '' });
      else if (c.kind === 'return') ({ applied, note } = ruNote);
      else if (c.kind === 'column') {
        const known = !!roleOfDict(c.column);
        if (dd2.act === 'ok') note = '確定しました（読み方は変えていません）';
        else if (c.source !== 'デジタコ') note = '配車表の列は、読み方を変えられません。計算は変わりません';
        else if (dd2.act === 'skip') { applied = true; note = known ? 'この列を読まずに計算し直しました' : '列名から役割が分からない列なので、もともと読んでいません。計算は変わりません'; }
        else { const r = resolveRole(dd2.text); applied = !!r; note = r ? `この列を「${ROLE_LABEL[r]}」として計算し直しました` : '役割の名前が分かりません（例: 走行距離、開始の日時）。読み方は変えていません'; }
      } else note = dd2.act === 'ok' ? '確定しました' : '計算は変わりません';
      c.decision = { act: dd2.act, text: dd2.text || '', applied, note };
    }
    for (const f of used) for (const u of unreadable) if (u.file === f.name && u.col != null && u.where == null) u.where = f.hlay.kind === 'vertical' ? `${u.line} 行目` : `${u.line} 行目・「${f.hlay.head[u.col]}」の列`;
    for (const u of unreadable) if (u.where == null) u.where = `${u.line} 行目`;

    // ファイルごとの結果
    const files2 = [];
    const pendH = confirm.filter((c) => c.kind === 'alias' || c.kind === 'link' || c.source === '配車表').length;
    const fileEntryH = (f, fi) => {
      const vertical = f.hlay.kind === 'vertical';
      const hs = f.wl.stats, hCells = f.wl.rows.length + f.wl.unreadable.length;
      const hUn = unreadable.filter((u) => u.file === f.name).length;
      const lay = f.hlay, wn = [];
      if (lay.header_row > 0) wn.push(`見出しは ${lay.header_row + 1} 行目です（上の ${lay.header_row} 行は題名として読みません）`);
      if (lay.weekday_row) wn.push('見出しの下の曜日の行を、年を決める手掛かりにしました');
      if (lay.day_cols && lay.day_cols.length) {
        const mUsed = lay.title_month ? lay.title_month[1] : monthOpt != null ? monthOpt : dMonths.length ? +mostCommon(dMonths) : null;
        wn.push(mUsed ? `見出しが日の数字だけなので、${lay.title_month ? "題名の" : monthOpt != null ? "指定した" : "デジタコの"} ${mUsed} 月の日として読みました` : '見出しが日の数字だけで、月が分かりません');
      }
      if (f.wl.skipped_total_rows) wn.push(`合計の行 ${f.wl.skipped_total_rows} 行は読みませんでした`);
      const ign = lay.head.filter((h, i) => h && lay.roles[i] === 'skip');
      if (ign.length) wn.push(`${ign.map((h) => `「${h}」`).join('')}の列は、日々の区間ではないので読みませんでした`);
      const hi = (f.wl.hired || []);
      if (hi.length) wn.push(`傭車の行 ${hi.length} 行（${hi.map((h) => `「${h.name}」`).join('')}）は、自社の車ではないので読まず、傭車として別に数えました`);
      const base0 = {
        file: f.name, kind: '配車表', shape: vertical ? '1 行 = 1 便の縦の形' : '日付が列に並ぶ横の形', encoding: f.encoding, year: f.year,
        layout_note_ja: wn.join('。') + (wn.length ? '。' : ''), shape_signature: shapeSignature(lay.kind, f.noPlateCol ? lay.head.slice(0, -1) : lay.head),
        ...(f.noPlateCol ? { no_plate_col: true, vehicle_label_ja: NO_PLATE_LABEL_JA } : {}),
      };
      if (f.excluded) {
        return { ...base0, verdict: `この配車表は読んでいません。${excludedReason(f)}。`, cells: hCells, ok: 0, pending: 0, unreadable: 1, excluded: true, reason: excludedReason(f) };
      }
      return {
        ...base0,
        verdict: f.noPlateCol
          ? `1 行 = 1 便の縦の形です（車番の列がないので、${NO_PLATE_LABEL_JA}として読みました。日付・発地・着地の列から、1 行を 1 つの区間にしました）。`
          : vertical
          ? `1 行 = 1 便の縦の形です（日付・車番・発地・着地の列から、1 行を 1 つの区間として読みました。車番 ${hs.plate_groups} 台）。`
          : `日付が列に並ぶ横の形です。1 行 = 1 車両 × 1 日に直しました（車番 ${hs.plate_groups} 台、日付の列 ${hs.date_cols} 個。`
            + `車番が空欄の続きの行 ${hs.filled_rows} か所を、上の行の車番で埋めました。色は読みません）。`,
        cells: hCells, ok: hCells - hUn, pending: fi === 0 ? pendH : 0, unreadable: hUn,
      };
    };
    used.forEach((f, fi) => files2.push(fileEntryH(f, fi)));
    for (const f of excludedH) files2.push(fileEntryH(f, 1));
    const pendD = confirm.filter((c) => c.source === 'デジタコ').length;
    dFiles.forEach((df, i) => {
      const mapped = df.columns.filter((c) => roleOf(c)).length, miss = df.columns.filter((c) => !roleOf(c));
      const dup = dd.dropped.by_file[df.file];
      files2.push({
        file: df.file, kind: 'デジタコ', shape: df.kind === 'summary' ? '1 行 = 1 日の集計の形' : '1 行 = 1 回の走行か停車の縦の形', encoding: df.encoding,
        verdict: `${df.kind === 'summary' ? ('1 行 = 1 日の集計の形です（日付・実車距離・空車距離から、実車と空車の走行を作りました' + (hf ? '。配車表と一緒に読んだので、実車率の計算には入れていません（二重に数えないため）' : '') + '）') : '1 行 = 1 回の走行か停車の縦の形です'}。列 ${df.columns.length} 個のうち ${mapped} 個は列名から役割が分かりました。${miss.length ? `${miss.length} 個は分かりません（列名: ${miss.join('、')}）。読まずに進めます。` : '分からない列はありません。'}`,
        cells: df.rows, ok: df.rows - df.errors, pending: i === 0 ? pendD : 0, unreadable: df.errors,
        note: (() => { const same = dFiles.slice(0, i).find((e) => columnSignature(e.columns) === columnSignature(df.columns)); return same ? `列の対応は ${same.file} と同じです` : null; })(),
        duplicates: dup ? { runs: dup.runs, stops: dup.stops } : null,
        shape_signature: shapeSignature('digitaco-' + df.kind, df.columns),
      });
    });
    for (const x of noPlate) files2.push({ file: x.f.name, kind: 'デジタコ', shape: '読めませんでした', encoding: x.f.encoding, verdict: 'ファイル名から車番が分からないため、読みませんでした。', cells: 0, ok: 0, pending: 0, unreadable: 1, reason: fileReason[x.f.name] });
    for (const f of otherFiles) {
      files2.push({
        file: f.name, kind: '不明', shape: '読めませんでした', encoding: f.encoding,
        verdict: fileReason[f.name] === UNKNOWN_REASON ? '配車表にもデジタコにも見えないため、読みませんでした。' : `${fileReason[f.name]}。決めるまでは読みません。`,
        cells: Math.max(0, f.rows.length - 1), ok: 0, pending: 0, unreadable: 1, shape_signature: shapeSignature('unknown', judged[f.name].cells), reason: fileReason[f.name],
      });
    }

    // ファイルごとに、区間か停車を 1 件でも取り出せたかを数える（取り出せなかったファイルは「読めなかった」）
    const filesUnread = [];
    for (const f of used) if (!haishaLegs.some((l) => l.ref.file === f.name)) filesUnread.push({ file: f.name, reason: f.hlay.needs_month ? '見出しが日の数字だけで、月が分かりません' : '区間を 1 件も取り出せませんでした' });
    for (const df of dFiles) if (!df.nruns && !df.nstops) filesUnread.push({ file: df.file, reason: '走行も停車も取り出せませんでした' });
    for (const x of noPlate) filesUnread.push({ file: x.f.name, reason: fileReason[x.f.name] });
    for (const f of otherFiles) filesUnread.push({ file: f.name, reason: fileReason[f.name] });
    for (const f of excludedH) filesUnread.push({ file: f.name, reason: fileReason[f.name] });

    // 利用者が列の役割を選べる列。blocking が真の列は、決めないとファイルが読めない
    const needed = [], seenNeed = new Map();
    const pushNeed = (e) => {
      const k = e.source + '|' + e.key;
      if (seenNeed.has(k)) { seenNeed.get(k).files.push(e.file); return; }
      e.files = [e.file]; seenNeed.set(k, e); needed.push(e);
    };
    for (const f of used) for (const c of f.wl.extra_cols) pushNeed({ source: '配車表', file: f.name, column: c.header, key: c.key, samples: c.samples, blocking: false, line: f.wl.layout.header_line, suggested_role: null, suggested_label_ja: null, options: roleOptions('配車表') });
    for (const p of parts) for (const e of needingRoles('デジタコ', p.file, p.head, p.recs, (h) => roleOf(h) || decided(h), new Set(), false, p.hdr + 1)) pushNeed(e);
    for (const f of otherFiles) {
      const { hi, cells } = judged[f.name];
      if (judged[f.name].binary) continue;
      // 見出しが辞書に当たる列は、役割がもう決まっているので聞かない（聞くのは、見出しからは分からない列だけ）
      for (const e of reguessHaisha(needingRoles('不明', f.name, cells, f.rows.slice(hi + 1), (h) => decided(h) || !!dictRole(h), new Set(), true, hi + 1, dictRole), dictRole, cells)) pushNeed(e);
    }
    const shapeSignatures = {};
    for (const f of files2) if (f.shape_signature) shapeSignatures[f.file] = f.shape_signature;
    const layouts = {};
    for (const f of used) layouts[f.name] = { ...f.wl.layout, plate_notes: f.wl.plate_notes, skipped_total_rows: f.wl.skipped_total_rows };
    const placeOf = (id) => (id && places[id]) || null;
    const bp = placeOf(base) ? placeOf(base).pref || null : null, dp = placeOf(dock) ? placeOf(dock).pref || null : null;

    const hiredAll = wl ? wl.hired : [];
    const hiredCells = hiredAll.reduce((a, h) => a + h.cells, 0), hiredLegs = hiredAll.reduce((a, h) => a + h.legs, 0);
    for (const f of parsed) if (f.headerless) notices.push({ kind: 'headerless', text_ja: `${f.name} には見出しの行がないので、仮の見出し（列 1、列 2…）を足して、1 行目も値として読みました。行番号は、仮の見出しの行を 1 行目として数えています。` });
    for (const f of used) if (f.noPlateCol) notices.push({ kind: 'no_plate_col', text_ja: `${f.name} には車番の列がないので、${NO_PLATE_LABEL_JA}として読みました。画面では ${NO_PLATE_NAME} と表示します。複数の車が混ざったファイルのときは、車番の列を足してください。` });
    if (hiredAll.length) notices.push({ kind: 'hired', text_ja: `傭車の行 ${hiredAll.length} 行（${hiredAll.map((h) => `「${h.name}」`).join('')}）の ${hiredCells} 日ぶん（区間 ${hiredLegs} 件）は、自社の車ではないので読まず、傭車として別に数えました。` });
    for (const f of parsed) if (f.csvWarn.length) notices.push({ kind: 'csv_quote', text_ja: `${f.name} の ${f.csvWarn.map((w) => w.line).slice(0, 5).join('、')} 行目: ${f.csvWarn[0].reason}（${f.csvWarn.length} 件）。` });
    if (timeDropped.length) notices.push({ kind: 'time_dropped', text_ja: `「8時半ごろ着」のように時刻が読めないセル ${timeDropped.length} 件は、時刻だけ使わず、区間は読みました。` });
    // 使った配車表のうち、区間を 1 件でも取り出せたものだけを「使った」と数える（見出しだけで区間が 0 件の配車表は、合わせて読んだことにしない）
    const usedH = used.filter((f) => haishaLegs.some((l) => l.ref.file === f.name));
    if (usedH.length > 1) notices.push({ kind: 'haisha_merged', text_ja: `配車表 ${usedH.length} 個（${usedH.map((f) => f.name).join('、')}）は、車番と日付が重ならないので合わせて読みました。` });
    if (ord.changed) notices.push({ kind: 'file_order', text_ja: `前に落としたファイルの順位を保って読みました（${files.map((f) => f.name).join('、')}）。重なるマスは、この順の先のファイルを使います。` });
    if (Array.isArray(o.prev_haisha_used) && o.prev_haisha_used.length && JSON.stringify(o.prev_haisha_used.slice().sort()) !== JSON.stringify(usedH.map((f) => f.name).sort())) {
      notices.push({ kind: 'haisha_used_changed', text_ja: `使う配車表が前と変わりました（前: ${o.prev_haisha_used.join('、')}、今: ${usedH.map((f) => f.name).join('、') || 'なし'}）。重なるマスの数字が変わっていることがあります。` });
    }
    if (excludedH.length) notices.push({ kind: 'haisha_overlap', text_ja: `${excludedH.map((f) => f.name).join('、')} は、先に読んだ配車表と車番・日付が重なるため読んでいません。${haishaChoice.needs_choice ? '使う配車表を選べます。' : '内容が同じなので、選ぶ必要はありません。'}` });
    if (droppedOverlap) notices.push({ kind: 'haisha_dropped', text_ja: `選んだ配車表どうしで車番と日付が重なるセル ${droppedOverlap} 件は、先に読んだ配車表のほうだけを使いました。` });
    const counts = {
      legs: legs.length, loaded: kc.loaded, empty: kc.empty, unknown: kc.unknown, unknown_ratio: pyRound(legs.length ? kc.unknown / legs.length : 0.0, 4),
      stops: stops.length, shifts: shifts.length, confirm: confirm.length, unreadable: unreadable.length, digitaco_only: lk.digitaco_only, haisha_only: lk.haisha_only, nonleg_cells: nonlegCells,
      outside_pair: legs.filter((l) => !l.dir).length,
      hired_cells: hiredCells, hired_legs: hiredLegs, time_dropped: timeDropped.length, local_legs: legs.filter((l) => l.local).length, return_unmarked: ruLegs.length, dropped_overlap: droppedOverlap,
      unreadable_rows: unreadableRows, dest_missing: endsMissing.length, unplaced_legs: unplacedLegs.length,
    };
    // 使ったファイル（区間・走行・停車を 1 件でも取り出せたもの）。見出しだけの配車表や読めなかったファイルは入れない
    const filesUsed = usedH.map((f) => ({ file: f.name, kind: '配車表', legs: haishaLegs.filter((l) => l.ref.file === f.name).length }))
      .concat(dFiles.filter((df) => df.nruns || df.nstops).map((df) => ({ file: df.file, kind: 'デジタコ', legs: df.nruns + df.nstops })));
    // 月ごとの集計。2 か月以上を一緒に落としたときも、月ごとに数える（月の比べは月どうしでする）。1 か月だけのときは件数と率だけ
    const monthKeys = uniq(legs.map((l) => l.date.slice(0, 7))).sort();
    const monthsDetail = monthKeys.map((ym) => {
      const lm = legs.filter((l) => l.date.slice(0, 7) === ym), yy = +ym.slice(0, 4), mm = +ym.slice(5, 7);
      const c2 = { loaded: 0, empty: 0, unknown: 0 };
      for (const l of lm) c2[l.kind]++;
      const sm = stops.filter((x) => x.start.slice(0, 7) === ym), km2 = shifts.filter((x) => x.start.slice(0, 7) === ym);
      const item = {
        month: ym, year: yy, mon: mm, label: `${yy} 年 ${mm} 月`, full: monthKeys.length > 1,
        period: { from: lm.reduce((a, l) => (l.date < a ? l.date : a), lm[0].date), to: lm.reduce((a, l) => (l.date > a ? l.date : a), lm[0].date) },
        counts: { legs: lm.length, loaded: c2.loaded, empty: c2.empty, unknown: c2.unknown, unknown_ratio: pyRound(lm.length ? c2.unknown / lm.length : 0.0, 4), stops: sm.length, shifts: km2.length },
        kpis: kpis(lm, vehicles), leg_ids: lm.map((l) => l.id),
      };
      if (monthKeys.length > 1) {
        const ptm = pairTables(lm, places, vehicles, base, dock);
        Object.assign(item, {
          table: weekdayTable(lm, sm, dock), pairs: ptm.pairs, pair_table: ptm.pair_table,
          insights: insights({ legs: lm, stops: sm, shifts: km2, vehicles, places, base, dock, month: mm, est_label: estLabel, own_names: !o.places }),
        });
      }
      return item;
    });
    const monthLabel = !monthsDetail.length ? '' : monthsDetail.length === 1 ? monthsDetail[0].label
      : `${monthsDetail[0].label}〜${monthsDetail[monthsDetail.length - 1].year === monthsDetail[0].year ? '' : monthsDetail[monthsDetail.length - 1].year + ' 年 '}${monthsDetail[monthsDetail.length - 1].mon} 月（${monthsDetail.length} か月分）`;
    const unplaced = Object.values(places).filter((p) => !p.placed && p.code == null && o.places == null).map((p) => ({ id: p.id, name: p.label, count: nameCount.get(p.id) || 0, reason: p.reason }))
      .concat(Object.values(places).filter((p) => p.placed && p.note && o.places == null).map((p) => ({ id: p.id, name: p.label, count: nameCount.get(p.id) || 0, reason: p.note, hall_ward: true, placed_at: p.muni })));
    const co = o.company || {};
    return {
      version: 1, fiction: false, month: legs.length ? `${pad(year, 4)}-${pad(mon)}` : '', year, mon, year_choice: yearChoice, year_choices: hfsAll.map((f) => ({ file: f.name, ...f.yearChoice })),
      months: uniq(haishaLegs.map((l) => l.date.slice(0, 7))).sort(),
      // SPEC §43: 2 か月以上を一緒に落としたとき。month・mon は多いほうの月のまま、month_label が期間の言い方、months_detail が月ごとの集計
      multi_month: monthsDetail.length > 1, month_label: monthLabel, months_detail: monthsDetail,
      files_used: filesUsed, file_order: files.map((f) => f.name),
      period: legs.length ? { from: legs.reduce((a, l) => (l.date < a ? l.date : a), legs[0].date), to: legs.reduce((a, l) => (l.date > a ? l.date : a), legs[0].date) } : null,
      failed: legs.length ? null : '区間を 1 件も読めませんでした' + (unreadable.length ? '（' + unreadable.slice(0, 3).map((u) => `${u.file}: ${u.reason}`).join('、') + '）' : ''),
      company: { name: co.name || '自社のファイル', city: co.city || '', code: co.code || '', vehicles: vehicles.length },
      places, base, dock, base_basis: baseBasis, max_load_raised: maxRaised,
      route: { path: o.routePath || null, osrm_km: null, note: o.routeNote || '' },
      vehicles, files: files2, files_read: parsed.length - filesUnread.length, files_unread: filesUnread, unreadable, paper: { count: 0, text: '紙の日報・チャート紙は読みません' },
      columns: columns.map((c) => { const { claude_input: _x, ...rest } = c; return rest; }),
      claude: { present: false, model: null, created_at: null, label: 'Claude の提案: 使いません' },
      confirm, legs: legs.map(legJson), stops: stops.map(stopJson), shifts,
      merged_places: [...mergedNames.entries()].filter(([, ns]) => ns.size >= 2).map(([id, ns]) => ({ id, label: places[id].label, short: coreName(places[id].label), names: [...ns] })),
      kpis: kp, table, pairs: pt.pairs, pair_table: pt.pair_table, insights: ins, counts, national: NATIONAL, unplaced, ...(ladenWords(legs).length ? { laden_words: ladenWords(legs) } : {}), // 実車として数えた区間のセルに付いていた語（無いときはキーも出さない）
     
      hired: hiredAll, time_dropped: timeDropped, haisha_choice: haishaChoice,
      encodings: parsed.map((f) => ({ file: f.name, encoding: f.encoding })),
      // SPEC §41: 利用者が列の役割を選ぶ・ファイルの形・重複・県内の往復のための項目
      column_roles_needed: needed, shape_signatures: shapeSignatures, layouts,
      needs_month: needsMonth, month_choice: needsMonth ? { candidates: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] } : null,
      digitaco_duplicates: { runs: dd.dropped.runs, stops: dd.dropped.stops, by_file: dd.dropped.by_file },
      notices, geo: { base_pref: bp, dock_pref: dp, same_pref: !!(bp && dp && bp === dp) },
    };
  }

  // 列の対応（列名 → 役割）。同じ名前の列が同じ役割に当たるときだけ、2 つのファイルの列の対応が同じ（own.py の column_signature と同じ）
  function columnSignature(columns) {
    return columns.map((c) => `${colKey(c)}=${roleOf(c) || ''}`).sort().join('|');
  }

  function legJson(l) {
    const keep = ['id', 'plate', 'date', 'dir', 'cycle_wd', 'cycle_dep', 'dep', 'arr', 'arr_hint', 'frm', 'to', 'km', 'kind', 'kind_src', 'weight_kg', 'flags', 'color', 'ref', 'dref', 'driver', 'local', 'round', 'rounds', 'return_unmarked'];
    const d = {};
    for (const k of keep) d[k] = l[k] === undefined ? null : l[k];
    d.km_src = (l.sources || {}).km == null ? null : l.sources.km;
    d.time_src = (l.sources || {}).time == null ? null : l.sources.time;
    if (l.note) d.note = l.note;
    return d;
  }
  // 配車表の積地・降ろし地があるので実車として数えた区間のうち、セルに空車の印でも重さでも時刻でもない語が付いていたものを、語ごとに数える（own.py の laden_words と同じ）。
  // 印にならなかった語が空車を意味していたときに、利用者が気づくための一覧
  const LADEN_NOTE_MAX = 8;
  function ladenWords(legs) {
    const cnt = new Map();
    for (const l of legs) {
      if (l.kind !== 'loaded' || l.kind_src !== '配車表の積地・降ろし地' || !l.note) continue;
      for (const w of String(l.note).split(' ')) {
        if (!w) continue;
        if (!cnt.has(w)) cnt.set(w, { word: w, count: 0, example: (((l.ref || {}).text) || '').trim().slice(0, 40) });
        cnt.get(w).count++;
      }
    }
    return [...cnt.values()].sort((a, b) => b.count - a.count || (a.word < b.word ? -1 : a.word > b.word ? 1 : 0)).slice(0, LADEN_NOTE_MAX);
  }
  function stopJson(s) {
    const d = {};
    for (const k of ['id', 'plate', 'place', 'name', 'start', 'end', 'minutes', 'driver', 'cycle_wd', 'ref']) d[k] = s[k] === undefined ? null : s[k];
    return d;
  }

  // 列ごとに、辞書の結果・値の形の一致率・やさしい日本語の理由を出す。Claude の一致は使わない（常に偽）
  function columnReasonJa(source, column, role, hit, ratio, exp) {
    const pct = pyRound(ratio * 100), label = ROLE_LABEL[role || ''] || '';
    if (hit && source === '配車表' && column.startsWith('日付の見出し')) return '見出しが「10/1(木)」のように月と日で書かれている列を、日付の列として読みます。';
    if (hit) {
      let t = `列の名前が「${column}」なので、${label}として読みます。`;
      if (ratio < 0.9) t += ` ただし、中身のうち「${exp}」に見えるのは ${pct}% だけなので、念のため確かめてください。`;
      return t;
    }
    if (label && ratio > 0) return `列の名前からは分かりません。中身の ${pct}% が「${exp}」に見えるので、${label}かもしれません。合っていれば確定してください。`;
    return '列の名前からも中身からも分からないので、読まずに進めます。';
  }

  function buildColumns(wl, hrows, digValues, hf, dRoles) {
    const columns = [], items = [];
    const specs = [];
    if (wl && hf) {
      const lay = hf.hlay, head = lay.head, pc = lay.plate_col, tc = lay.type_col;
      const dataRows = hrows.slice(lay.data_start);
      const plateVals = dataRows.map((r) => r[pc] || '').filter((v) => v.trim() && !TOTAL_TOKENS.has(headKey(v)));
      if (!hf.noPlateCol) specs.push(['配車表', head[pc] || '車番', 'plate', true, plateVals]); // 車番の列を足して読んだファイルは、足した列の中身を確かめない
      if (tc != null) specs.push(['配車表', head[tc], 'vehicle_type', true, dataRows.map((r) => r[tc] || '').filter((v) => v.trim())]);
      if (lay.kind === 'vertical') {
        const dc = lay.cols.date;
        specs.push(['配車表', head[dc], 'date', true, dataRows.map((r) => r[dc] || '').filter((v) => v.trim())]);
      } else {
        const dateHeads = head.filter((h, i) => i !== pc && i !== tc && h && lay.roles[i] !== 'skip');
        specs.push(['配車表', `日付の見出し（${dateHeads.length} 列）`, 'date', true, dateHeads]);
      }
    }
    for (const [name, vals] of Object.entries(digValues)) {
      const role = roleOfDict(name);
      specs.push(['デジタコ', name, role, role != null, vals]);
    }
    for (const [source, col, role0, hit, vals] of specs) {
      let role = role0, ratio, exp, roleLabel;
      if (hit) { const sk = ROLE_SHAPE[role]; ratio = shapeRatio(vals, sk); exp = VALUE_SHAPES[sk][0]; roleLabel = ROLE_LABEL[role]; }
      else { [role, ratio] = bestRoleFor(vals); exp = role ? VALUE_SHAPES[ROLE_SHAPE[role]][0] : '（当てはまる形がありません）'; roleLabel = null; }
      const c = confidence(hit, ratio, false), color = confidenceColor(c);
      const userRole = source === 'デジタコ' && dRoles && Object.prototype.hasOwnProperty.call(dRoles, colKey(col)) ? dRoles[colKey(col)] : null;
      const entry = {
        source, column: col, dict_hit: hit, dict_role: hit ? role : null, dict_label: roleLabel, shape_expected: exp, shape_ratio: pyRound(ratio, 4), shape_best_role: hit ? null : (role || null),
        role_label_ja: ROLE_LABEL[role || ''] || null, reason_ja: columnReasonJa(source, col, role, hit, ratio, exp),
        claude_proposal: null, claude_note: '使いません（ブラウザの中だけで計算します）', claude_agrees: false, confidence: c, color,
        formula: `列名で分かれば 1（今回 ${hit ? 1 : 0}）、値の形が合う割合 ${pyRound(ratio * 100)}% → 確かさ ${fix(c, 2)}`,
        claude_input: { source, column: col, row_count: vals.length, non_blank_ratio: pyRound(nonBlank(vals).length / Math.max(1, vals.length), 4) },
      };
      if (userRole) {
        entry.user_role = userRole; entry.user_role_label_ja = ROLE_LABEL_JA[userRole] || null;
        entry.role_label_ja = entry.user_role_label_ja; entry.reason_ja = `あなたが選んだ役割（${entry.user_role_label_ja}）で読んでいます。`;
      }
      columns.push(entry);
      if (color === 'yellow' && !userRole) {
        items.push({
          id: '', key: `column:${source}:${col}`, effect: source === 'デジタコ' ? 'kpi' : 'none', kind: 'column', source, file: source === '配車表' ? hf.name : 'デジタコの CSV', title: `列「${col}」の対応づけ`, original: `列名「${col}」。値の形は「${exp}」に ${pyRound(ratio * 100)}% 一致`,
          proposal: `${roleLabel || '不明'}として読む`, confidence: c, color, formula: entry.formula, claude: null, column: col,
          role_label_ja: entry.role_label_ja, reason_ja: entry.reason_ja,
        });
      }
    }
    return { columns, items };
  }

  /* ------------------------------------------------------------ 書き出し */

  const csvCell = (v) => { const s0 = v == null ? '' : String(v); const s = /^[=+\-@\t\r]/.test(s0) && !/^[-+]?\d+(\.\d+)?$/.test(s0) ? `'${s0}` : s0; return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const KIND_JA = { loaded: '実車', empty: '空車', unknown: '不明' };
  const DIR_JA = { out: '行き', back: '帰り' };

  // 区間ごとの判定（実車・空車・不明と、その根拠の列）を CSV の文字にする
  function legsCsv(d) {
    const nm = (id) => (d.places[id] ? d.places[id].label : '');
    const head = ['区間ID', '日付', '車番', '出発地', '到着地', '向き', '距離km', '距離の出どころ', '判定', '判定の根拠', '確認待ち', '元の行（配車表）', '元の行（デジタコ）'];
    const rows = d.legs.map((l) => [
      l.id, l.date, l.plate, nm(l.frm), nm(l.to), DIR_JA[l.dir] || '', l.km == null ? '' : l.km, l.km_src || '', KIND_JA[l.kind], l.kind_src || '',
      (l.flags || []).map((f) => (f.type === 'time' ? '時刻のずれ' : '距離のずれ')).join('・'),
      l.ref ? `${l.ref.file} ${l.ref.where}` : '', l.dref ? `${l.dref.file} ${l.dref.where}` : '',
    ]);
    return [head, ...rows].map((r) => r.map(csvCell).join(',')).join('\n') + '\n';
  }

  // 単価の文字を数にする。全角の数字は NFKC で直す。空は { value: null }、負や数でないものは理由つき
  const COST_WARN_KM = 1000, COST_WARN_HOUR = 100000;
  function parseUnit(v) {
    const t = nfkc(v).replace(/,/g, '').trim();
    if (!t) return { value: null, error: null };
    if (/^[0-9]+(\.[0-9]+)?$/.test(t)) return { value: Number(t), error: null };
    if (/^-/.test(t)) return { value: null, error: '0 以上の数で入れてください（マイナスは入れられません）' };
    return { value: null, error: '数で入れてください（例: 30）' };
  }
  const num6 = (n) => n.toLocaleString('en-US', { maximumFractionDigits: 6 });
  const commas1 = (n) => { const t = fix(n, 1), i = t.indexOf('.'); return Number(t.slice(0, i)).toLocaleString('en-US') + t.slice(i); };

  // 費用への換算（単価は利用者が入れる。空のときは null を返し、0 円にしない）。
  // 式に出す km と時間は、画面に出す丸め（小数 1 桁）の値で掛けるので、表示どおりに掛け算が合う。区間が 0 件のときは何も出さない
  function costs(d, perKm, perHour) {
    const pk = parseUnit(perKm), ph = parseUnit(perHour);
    const k = pk.value, h = ph.value;
    // 入れた値が現実の範囲を超えるときは、計算は止めずに確かめる文を出す（単位の取り違え: 円/km に 1 時間ぶんの額を入れた、など）
    const warns = { perKm: k != null && k > COST_WARN_KM ? `1 km あたり ${commas(COST_WARN_KM)} 円を超えています。円/km の単位で入れているか確かめてください` : null,
      perHour: h != null && h > COST_WARN_HOUR ? `1 時間あたり ${commas(COST_WARN_HOUR)} 円を超えています。円/時の単位で入れているか確かめてください` : null };
    const out = { perKm: k, perHour: h, errors: { perKm: pk.error, perHour: ph.error }, warns, emptyKm: null, longCount: 0, longMin: 0, empty: null, wait: null };
    if (!d.legs.length) return out;
    const emptyKm = pyRound(d.legs.filter((l) => l.kind === 'empty').reduce((a, l) => a + (l.km || 0), 0), 1);
    const long = d.stops.filter((s) => s.minutes >= R3_MIN_STOP_MIN);
    const longMin = long.reduce((a, s) => a + s.minutes, 0);
    out.emptyKm = emptyKm; out.longCount = long.length; out.longMin = longMin;
    // 停車の記録はデジタコにだけある。デジタコを入れていない（配車表だけの）ときは、停車が 0 回なのではなく記録が無いので、費用の行を出さない（0 円と書かない）
    out.waitMissing = !(d.stops.length > 0 || !Array.isArray(d.files_used) || d.files_used.some((f) => f.kind === 'デジタコ'));
    if (k != null) out.empty = { yen: Math.round(emptyKm * k), formula: `空車 ${commas1(emptyKm)} km × ${num6(k)} 円/km` };
    if (h != null && !out.waitMissing) { const hours = pyRound(longMin / 60, 1); out.wait = { yen: Math.round(hours * h), formula: `30 分以上の停車 ${fix(hours, 1)} 時間 × ${num6(h)} 円/時` }; }
    return out;
  }

  // 上の数字と気づきを文にする
  function summaryText(d, c) {
    const k = d.kpis, p = (v, n = 1) => (v == null ? '計算できません' : (v * 100).toFixed(n) + '%');
    const L = [];
    L.push(`自社の運行の分析（${d.fiction ? '架空の会社のサンプル' : d.company.name}、${d.year} 年 ${d.mon} 月）`);
    L.push(`区間 ${d.counts.legs} 件（実車 ${d.counts.loaded}、空車 ${d.counts.empty}、不明 ${d.counts.unknown}）`);
    L.push(`実車率 ${p(k.actual_rate.value)}（判定できた区間 ${k.actual_rate.legs} / ${k.actual_rate.of}）。全国は ${d.national.actual_rate_pct}%（営業用普通車）`);
    L.push(`大型の積載率 ${p(k.load_rate.value)}（計算できた区間 ${k.load_rate.legs}）`);
    L.push(`大型の積載効率 ${p(k.load_efficiency.value)}（大型の積載率 × 大型の実車率）。全国は ${d.national.load_efficiency_pct}% ですが、トンキロの比で式が違います`);
    L.push(c.waitMissing ? `空車で走った距離 ${commas1(c.emptyKm)} km（停車の記録はデジタコにだけあります。デジタコを入れていないので、30 分以上の停車は数えていません）`
      : `空車で走った距離 ${commas1(c.emptyKm)} km、30 分以上の停車 ${c.longCount} 回・${fix(c.longMin / 60, 1)} 時間`);
    if (c.empty) L.push(`空車の費用の目安 ${commas(c.empty.yen)} 円（${c.empty.formula}）`);
    if (c.wait) L.push(`停車の費用の目安 ${commas(c.wait.yen)} 円（${c.wait.formula}）`);
    if (c.empty || c.wait) L.push('単価は入力した値で、削減額の約束ではありません。');
    L.push('');
    L.push(d.insights.length ? '気づき' : '気づきはありません。');
    d.insights.forEach((i, n) => L.push(`${n + 1}. ${i.text}`));
    L.push('');
    L.push('この数字は、取り込んだ記録から計算した目安です。法令で残す元の記録は別に保管してください。');
    return L.join('\n');
  }

  // 結果の区間を、部品（web/kit/schema/leg.json）の形にする。地図に置けない地点を通る区間は含めない（件数は meta.note に書く）
  function toKitLegs(d) {
    const okP = (p) => p && p.lon != null && p.lat != null;
    const legs = [];
    let skipped = 0;
    for (const l of d.legs) {
      const a = d.places[l.frm], b = d.places[l.to];
      if (!okP(a) || !okP(b)) { skipped++; continue; }
      const start = l.dep || `${l.date}T00:00`;
      const end = l.arr && l.arr >= start ? l.arr : start;
      const ks = l.km_src;
      legs.push({
        id: l.id, vehicle: l.plate, start, end,
        from: { name: a.label || a.short, lon: a.lon, lat: a.lat }, to: { name: b.label || b.short, lon: b.lon, lat: b.lat },
        status: l.kind, distance_km: l.km == null ? null : l.km, weight_kg: l.weight_kg == null ? null : l.weight_kg,
        basis: ks === 'デジタコ' || ks === KM_FILE_SRC ? '実測' : ks === '利用者の修正' ? '仮定' : '推定',
        source: [l.ref, l.dref].filter((x) => x).map((x) => `${x.file} ${x.where}`).join(' / ') || '配車表・デジタコ',
      });
    }
    return {
      meta: { title: `${d.company.name} ${d.year} 年 ${d.mon} 月の運行`, fiction: !!d.fiction, note: skipped ? `地図に置けない地点を通る区間 ${skipped} 件は含めていません` : '' },
      legs,
    };
  }

  return {
    analyze, decode, parseCsv, wideToLong, parseCell, raiseAssumedMax, parseWaitMin, legWd, parseTime, parseDigitaco, normalizePlate, link, classifyLegs, confidence, confidenceColor, shapeRatio, bestRoleFor,
    parseRestraintHours, kousokuHours, buildShifts, kpis, weekdayTable, insights, matchMuni, legsCsv, costs, summaryText, pyRound,
    isNonPlace, prefSplit, prefName, toKitLegs, resolveRole, hallWard, CITY_HALL_WARD, HALL_WARD_NOTE, inferYear, assignHeadYears, parseKm, parseWeight, parseWeights, parseDT, overMaxLegs, parseUnit,
    detectHaisha, addVehicleCol, NO_PLATE_LABEL_JA, readHaisha, transposeDateDown, haishaRole, headKey, plateIdentity, detectDigitaco, parseDigitacoSummary, dedupeDigitaco, parseDateOnly, parseHm, tonsPair,
    roleOptions, needingRoles, shapeSignature, hasEmptyMark, gouName, vdate, columnReasonJa, judgedRow, ROLE_LABEL_JA, weightConfirmItems, pairTables, legPair, returnTrips, returnTarget, returnPairs, returnWhen, srcCat, applyMaxPayload, roleOf, yearsInText, nearestYear, classOf, isEmptyWord, plateAttrConflicts, EMPTY_WORDS, ladenWords, cellNote, YEAR_RANGE: [YEAR_MIN, YEAR_MAX], DIGITACO_DICT, HAISHA_DICT, ROLE_LABEL, NATIONAL,
  };
})();
if (typeof window !== 'undefined') window.OwnCalc = OwnCalc;
