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

  const DT = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/;
  function parseDT(s) {
    const m = DT.exec(nfkc(s).trim());
    if (!m) return null;
    const [y, mo, d, h, mi] = [+m[1], +m[2], +m[3], +(m[4] || 0), +(m[5] || 0)];
    if (mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59) return null;
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
    let row = [], cell = '', q = false;
    for (let i = 0; i < t.length; i++) {
      const c = t[i];
      if (q) {
        if (c === '"') { if (t[i + 1] === '"') { cell += '"'; i++; } else q = false; } else cell += c;
      } else if (c === '"' && cell === '') q = true;
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

  /* ------------------------------------------------------------ 配車表（横の形）→ 縦の形 */

  const blankRow = (row) => !row.some((c) => (c || '').trim());
  // 日付の見出し。「10/1(木)」を基本に、「10月1日」「2026/10/1」も読む。戻り値は { y（年か null）, m, d }
  function dateHeadParts(h) {
    const t = nfkc(h).trim();
    let m = /^(\d{4})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})/.exec(t);
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
  // 曜日の見出し（「10/1(木)」の木）と合う年を探す。時計（今日の日付）は使わない（own.py の infer_year と同じ）
  const YEAR_MIN = 2020, YEAR_MAX = 2036, NO_HINT_YEARS = [2024, 2025, 2026, 2027, 2028, 2029, 2030], DEFAULT_YEAR = 2026;
  const weekdayOfHead = (h) => { const m = /\(\s*([月火水木金土日])\s*\)/.exec(nfkc(h || '')); return m ? WEEKDAYS.indexOf(m[1]) : null; };
  function inferYear(head) {
    const heads = (head || []).map((h) => (h || '').trim());
    const parsed = heads.map((h) => dateHeadParts(h));
    const noHint = (source) => ({ year: DEFAULT_YEAR, candidates: NO_HINT_YEARS.slice(), ambiguous: true, source });
    if (!parsed.some((p) => p)) return noHint('none');
    const implicit = heads.map((_, i) => i).filter((i) => parsed[i] && parsed[i].y == null);
    if (!implicit.length) { const f = parsed.find((p) => p); return { year: f.y, candidates: [f.y], ambiguous: false, source: 'explicit' }; }
    const wdCols = implicit.map((i) => [i, weekdayOfHead(heads[i])]).filter(([, w]) => w != null);
    if (!wdCols.length) return noHint('none');
    const cands = [];
    for (let y = YEAR_MIN; y <= YEAR_MAX; y++) {
      const got = assignHeadYears(heads, y);
      if (wdCols.every(([i, wd]) => { const g = got[i], iso = isoDate(g.y, g.m, g.d); return iso != null && weekdayOf(iso) === wd; })) cands.push(y);
    }
    if (!cands.length) return noHint('mismatch');
    return { year: Math.max(...cands), candidates: cands, ambiguous: cands.length > 1, source: 'weekday' };
  }
  const isoDate = (y, m, d) => {
    const t = new Date(Date.UTC(y, m - 1, d));
    return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d ? `${pad(y, 4)}-${pad(m)}-${pad(d)}` : null;
  };
  const PLATE_HEADS = ['車番', '車両番号', 'ナンバー'];
  const TYPE_HEADS = ['車種', '車格', 'トン数'];

  function wideToLong(rows, year) {
    const out = { rows: [], unreadable: [], stats: {}, plates: [] };
    if (!rows.length) { out.stats = { filled_rows: 0, groups: 0, date_cols: 0, bad_cols: 0, data_rows: 0 }; return out; }
    const head = rows[0].map((c) => (c || '').trim());
    let plateCol = head.findIndex((h) => PLATE_HEADS.some((p) => h.includes(p)));
    if (plateCol < 0) plateCol = 0;
    const typeCol = head.findIndex((h, i) => i !== plateCol && TYPE_HEADS.some((p) => h.includes(p)));
    const dateCols = {}, badCols = {};
    const ymd = assignHeadYears(head.map((h, i) => (i === plateCol || i === typeCol ? '' : h)), year);
    head.forEach((h, i) => {
      if (i === plateCol || i === typeCol || !h) return;
      const dh = ymd[i];
      if (dh) {
        const iso = isoDate(dh.y, dh.m, dh.d);
        if (iso) dateCols[i] = iso; else badCols[i] = '日付が読めません';
      } else if (/^\s*\d{1,2}\s*日\s*$/.test(nfkc(h))) badCols[i] = '月が分かりません';
      else badCols[i] = '日付が読めません';
    });
    const groups = [];
    let cur = null, filled = 0, dataRows = 0;
    rows.slice(1).forEach((r0, k) => {
      const ri = k + 2;
      const row = r0.map((c) => (c || '').trim());
      while (row.length < head.length) row.push('');
      if (blankRow(row)) { cur = null; return; }
      dataRows++;
      const raw = row[plateCol];
      if (raw) {
        const pn = normalizePlate(raw);
        cur = { plate: pn, raw, type: typeCol >= 0 ? row[typeCol] : '', rows: [[ri, row]], bad: pn == null };
        groups.push(cur);
      } else if (cur && !cur.bad) { cur.rows.push([ri, row]); filled++; }
      else { cur = { plate: null, raw: '', type: '', rows: [[ri, row]], bad: true }; groups.push(cur); }
    });
    for (const g of groups) {
      const [firstLine, first] = g.rows[0];
      if (g.plate == null) {
        const reason = !g.raw ? '車番がありません' : '車番が読めません';
        for (const [line, row] of g.rows) {
          row.forEach((cell, ci) => {
            if (ci === plateCol || ci === typeCol || !cell || !(ci in dateCols || ci in badCols)) return;
            out.unreadable.push({ line, col: ci, original: cell, reason });
          });
        }
        continue;
      }
      if (!out.plates.some((p) => p.plate === g.plate)) out.plates.push({ plate: g.plate, raw: g.raw, type: g.type });
      first.forEach((cell, ci) => {
        if (ci in badCols && cell) { out.unreadable.push({ line: firstLine, col: ci, original: cell, reason: badCols[ci], plate: g.plate }); return; }
        if (!(ci in dateCols)) return;
        const notes = g.rows.slice(1).map(([, row]) => row[ci]).filter((x) => x);
        if (!cell && !notes.length) return;
        out.rows.push({ plate: g.plate, plate_raw: g.raw, vehicle_type: g.type, date: dateCols[ci], text: cell, notes, line: firstLine, col: ci });
      });
    }
    out.stats = { filled_rows: filled, groups: groups.length, plate_groups: groups.filter((g) => g.plate != null).length, date_cols: Object.keys(dateCols).length, bad_cols: Object.keys(badCols).length, data_rows: dataRows };
    out.head = head;
    out.plateCol = plateCol;
    out.typeCol = typeCol;
    return out;
  }

  /* ------------------------------------------------------------ 配車表のセル */

  const NONLEG = ['休', '整備', '休み', '車検', '待機'];
  const ARR = /着\s*([^\s（(]+)/;
  const WEIGHT = /(\d+(?:\.\d+)?)\s*[tｔ](?![a-zA-Z])/;

  function parseTime(tok) {
    const t = nfkc(tok).trim();
    let m = /^(\d{1,2}):(\d{2})$/.exec(t) || /^(\d{1,2})時(\d{1,2})分$/.exec(t) || /^(\d{1,2})(\d{2})$/.exec(t);
    if (m) { const h = +m[1], mi = +m[2]; return h < 24 && mi < 60 ? `${pad(h)}:${pad(mi)}` : null; }
    m = /^(\d{1,2})時$/.exec(t);
    if (m && +m[1] < 24) return `${pad(+m[1])}:00`;
    return null;
  }

  function parseCell(text) {
    const t = nfkc(text).trim();
    const res = { legs: [], nonleg: false, errors: [] };
    if (!t) return res;
    if (NONLEG.includes(t)) { res.nonleg = true; return res; }
    for (let part of t.split(/[／/]/)) {
      part = part.trim();
      if (!part) continue;
      const m = /^([^→>\s]*)\s*(?:→|->|>)\s*([^\s（(着]*)\s*(.*)$/.exec(part);
      if (!m) { res.errors.push('区間が読めません'); continue; }
      const frm = (m[1] || '').trim() || null, to = (m[2] || '').trim() || null, rest = m[3];
      let arr = null;
      const am = ARR.exec(rest);
      if (am) { arr = parseTime(am[1]); if (arr == null) res.errors.push('時刻が読めません'); }
      const wm = WEIGHT.exec(rest);
      res.legs.push({ frm, to, empty: part.includes('（空）') || part.includes('(空)') || part.includes('空車'), arr, weight_kg: wm ? parseFloat(wm[1]) * 1000 : null });
    }
    return res;
  }

  /* ------------------------------------------------------------ デジタコ */

  // 列名の辞書。own.py の DIGITACO_DICT と同じものに、よくある別の書き方を足してあります（足した分は JS だけ）
  const DIGITACO_DICT = { KBN: 'kind', ST_TIME: 'start_time', ED_TIME: 'end_time', ODO_KM: 'distance_km', JK_FLG: 'loaded_flag', POS_NAME: 'place_name', DRV_CD: 'driver_code' };
  const DIGITACO_EXTRA = {
    '区分': 'kind', '開始日時': 'start_time', '開始時刻': 'start_time', '終了日時': 'end_time', '終了時刻': 'end_time',
    '走行距離': 'distance_km', '走行距離(KM)': 'distance_km', '実空区分': 'loaded_flag', '実車空車': 'loaded_flag', '実空': 'loaded_flag',
    '地点名': 'place_name', '停車地': 'place_name', '停車場所': 'place_name', '乗務員コード': 'driver_code', '運転者コード': 'driver_code',
  };
  const HAISHA_DICT = { '車番': 'plate', '車種': 'vehicle_type' };
  // 利用者が「確認」で決めた列の役割（{大文字にした列名: 役割か null（読まない）}）。analyze の間だけ入る。辞書より先に使う
  let ROLE_OV = null;
  const colKey = (name) => nfkc(name).trim().toUpperCase();
  const roleOf = (name, ov = ROLE_OV) => {
    const k = colKey(name);
    if (ov && Object.prototype.hasOwnProperty.call(ov, k)) return ov[k];
    return DIGITACO_DICT[k] || DIGITACO_EXTRA[k] || null;
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

  function parseDigitaco(rows, resolve, plate, roles) {
    const head = (rows[0] || []).map((h) => h || '');
    const col = {};
    head.forEach((h, i) => { const r = roleOf(h, roles === undefined ? ROLE_OV : roles); if (r) col[r] = i; });
    const runs = [], stops = [], errors = [];
    let prevStop = null, openRun = null;
    const get = (r, role) => (col[role] != null ? (r[col[role]] || '').trim() : '');
    rows.slice(1).forEach((r, k) => {
      const i = k + 2;
      if (blankRow(r)) return;
      const st = parseDT(get(r, 'start_time')), ed = parseDT(get(r, 'end_time'));
      if (st == null || ed == null) { errors.push({ line: i, reason: '時刻が読めません' }); return; }
      if (ed <= st) { errors.push({ line: i, reason: END_BEFORE_START }); return; }
      const kindRaw = nfkc(get(r, 'kind'));
      const kind = ['1', '走行'].includes(kindRaw) ? '1' : ['2', '停車'].includes(kindRaw) ? '2' : kindRaw;
      const driver = get(r, 'driver_code') || null;
      if (kind === '1') {
        const kmS = get(r, 'distance_km');
        const jkS = nfkc(get(r, 'loaded_flag'));
        let km = null;
        if (kmS) { km = parseKm(kmS); if (km == null) errors.push({ line: i, reason: '距離が読めません' }); }
        const run = {
          plate, start: isoOf(st), end: isoOf(ed), km,
          jk: ['1', '実', '実車'].includes(jkS) ? 1 : ['0', '空', '空車'].includes(jkS) ? 0 : null,
          frm: prevStop ? prevStop.place : null, to: null, driver, line: i,
        };
        runs.push(run); openRun = run;
      } else if (kind === '2') {
        const name = get(r, 'place_name');
        const place = name ? resolve(name) : null;
        const stop = { plate, place, name, start: isoOf(st), end: isoOf(ed), minutes: pyRound(ed - st), driver, line: i };
        stops.push(stop);
        if (openRun) { openRun.to = place; openRun = null; }
        prevStop = stop;
      } else errors.push({ line: i, reason: '区分が読めません' });
    });
    return { runs, stops, errors };
  }

  /* ------------------------------------------------------------ 結び付け・実車と空車 */

  const endsOfLeg = (l) => [...new Set([l.frm, l.to].filter((x) => x))];
  const hm = (s) => +s.slice(0, 2) * 60 + +s.slice(3, 5);
  const timeDiff = (hint, iso) => {
    const a = +iso.slice(11, 13) * 60 + +iso.slice(14, 16);
    return ((((hm(hint) - a + 720) % 1440) + 1440) % 1440) - 720;
  };

  // estFn(ends の配列) は { km, src } か null を返す
  function link(haisha, digitaco, estFn) {
    const byKey = new Map();
    for (const d of digitaco) { const k = d.plate + '|' + d.start.slice(0, 10); if (!byKey.has(k)) byKey.set(k, []); byKey.get(k).push(d); }
    const used = new Set(), legs = [];
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
      }
      const p = pick || {};
      const ends = endsOf(h.frm || p.frm, h.to || p.to);
      const est = ends.length === 2 ? estFn(ends) : null;
      const leg = {
        plate: h.plate, date: h.date, hid: h.id, did: pick ? pick.id : null,
        frm: h.frm || p.frm || null, to: h.to || p.to || null,
        empty_mark: !!h.empty, has_ends: !!(h.frm && h.to), weight_kg: h.weight_kg == null ? null : h.weight_kg, jk: pick ? pick.jk : null, driver: pick ? pick.driver : null,
        dep: pick ? pick.start : null, arr: pick ? pick.end : null, arr_hint: h.arr || null,
        flags: [], sources: {}, ref: h.ref || null, dref: pick ? pick.ref || null : null,
      };
      if (pick && pick.km != null) {
        leg.km = pick.km; leg.sources.km = 'デジタコ';
        if (est) {
          const ratio = pyRound(Math.abs(pick.km - est.km) / est.km, 9);
          if (ratio >= LINK_DIST_RATIO) leg.flags.push({ type: 'distance', ratio: pyRound(ratio, 4), digitaco_km: pick.km, est_km: est.km });
        }
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
        empty_mark: false, has_ends: !!(d.frm && d.to), weight_kg: null, jk: d.jk, driver: d.driver,
        dep: d.start, arr: d.end, arr_hint: null, flags: [], ref: null, dref: d.ref || null,
        km: d.km != null ? d.km : est ? est.km : null,
        sources: { km: d.km != null ? 'デジタコ' : est ? est.src : null, time: 'デジタコ', kind: null, note: '配車表なし' },
      });
    }
    return { legs: legs.concat(onlyD), digitaco_only: onlyD.length, haisha_only: legs.filter((x) => x.did == null).length };
  }

  function classifyLegs(legs) {
    const out = [], counts = { loaded: 0, empty: 0, unknown: 0 };
    for (const l0 of legs) {
      const l = { ...l0 };
      let kind = 'unknown', src = null;
      if (l.jk === 0 || l.jk === 1) { kind = l.jk === 1 ? 'loaded' : 'empty'; src = 'デジタコの実空区分'; }
      else if (l.empty_mark) { kind = 'empty'; src = '配車表の（空）の印'; }
      else if (l.has_ends) { kind = 'loaded'; src = '配車表の積地・降ろし地'; }
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
    datetime: ['YYYY-MM-DD HH:MM の日時', (v) => /^\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(:\d{2})?$/.test(v)],
    date_header: ['月/日 の日付の見出し', (v) => /^\d{1,2}\/\d{1,2}(\(.\))?$/.test(v) || /^\d{1,2}月\d{1,2}日(\(.\))?$/.test(v) || /^\d{4}[-/]\d{1,2}[-/]\d{1,2}(\(.\))?$/.test(v)],
    flag01: ['0 か 1', (v) => v === '0' || v === '1'],
    code12: ['1 か 2', (v) => v === '1' || v === '2'],
    driver_code: ['英大文字 1 字と数字 2 桁', (v) => /^[A-Z]\d{2}$/.test(v)],
    plate: ['4 桁の番号をふくむ車番', (v) => normalizePlate(v) != null],
    vehicle_type: ['「大型」か「数字 + t」', (v) => /^(大型|\d+t)$/.test(v)],
    decimal: ['小数まじりの数字', (v) => /^\d+(\.\d+)?$/.test(v)],
    place_kanji: ['漢字・かなだけの名称', (v) => KANA_KANJI.test(v)],
  };
  const ROLE_SHAPE = {
    kind: 'code12', start_time: 'datetime', end_time: 'datetime', distance_km: 'decimal', loaded_flag: 'flag01',
    place_name: 'place_kanji', driver_code: 'driver_code', plate: 'plate', vehicle_type: 'vehicle_type', date: 'date_header',
  };
  const ROLE_LABEL = {
    kind: '区分（走行・停車）', start_time: '開始の日時', end_time: '終了の日時', distance_km: '走行距離（km）',
    loaded_flag: '実車・空車の区分', place_name: '停車の地点名', driver_code: '運転者コード', plate: '車番',
    vehicle_type: '車種', date: '日付',
  };
  const nonBlank = (values) => values.map((v) => (v || '').trim()).filter((v) => v);
  function shapeRatio(values, key) {
    const vs = nonBlank(values);
    if (!vs.length) return 0.0;
    const ok = VALUE_SHAPES[key][1];
    return vs.filter(ok).length / vs.length;
  }
  function bestRoleFor(values) {
    let best = ['', 0.0];
    for (const [role, key] of Object.entries(ROLE_SHAPE)) {
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

  function kpis(legs, vehicles) {
    const cls = {};
    for (const v of vehicles) cls[v.plate] = v;
    const n = legs.length;
    const isJudged = (l) => (l.kind === 'loaded' || l.kind === 'empty') && l.km;
    const judged = legs.filter(isJudged);
    const sum = (ls) => ls.reduce((a, l) => a + l.km, 0);
    const rate = (ls) => { const tot = sum(ls); return tot ? sum(ls.filter((l) => l.kind === 'loaded')) / tot : null; };
    const large = legs.filter((l) => (cls[l.plate] || {}).cls === '大型');
    const largeJudged = large.filter(isJudged);
    const withW = large.filter((l) => l.kind === 'loaded' && l.weight_kg && cls[l.plate].max_load_kg);
    const loadRate = withW.length ? withW.reduce((a, l) => a + l.weight_kg / cls[l.plate].max_load_kg, 0) / withW.length : null;
    const largeRate = rate(largeJudged);
    return {
      actual_rate: {
        value: rate(judged), legs: judged.length, of: n, share: n ? judged.length / n : 0.0,
        km_loaded: pyRound(sum(judged.filter((l) => l.kind === 'loaded')), 1), km_empty: pyRound(sum(judged.filter((l) => l.kind === 'empty')), 1),
        formula: '実車の km ÷ 走った km（実車か空車かが判定できた区間だけ）',
      },
      load_rate: { value: loadRate, legs: withW.length, of_large: large.length, of: n, share: n ? withW.length / n : 0.0, formula: '積んだ重さ ÷ 最大積載量（大型の実車の区間のうち、重さが分かる区間の平均）' },
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

  /* ------------------------------------------------------------ 気づき（6 規則） */

  const evLeg = (l) => { const r = l.ref || {}; return { id: l.id, label: `${md(l.date)} 車番 ${l.plate}`, file: r.file, where: r.where, text: r.text }; };
  const uniq = (a) => [...new Set(a)];

  function insights(data) {
    const { legs } = data, stops = data.stops || [], shifts = data.shifts || [], vehicles = data.vehicles || [], places = data.places || {};
    const base = data.base, dock = data.dock;
    const short = (p) => (places[p] || {}).short || '?';
    const lbl = { out: `${short(base)} → ${short(dock)}`, back: `${short(dock)} → ${short(base)}` };
    const out = [];
    const add = (rule, text, numbers, next, evidence) => out.push({ id: `I${out.length + 1}`, rule, text, numbers, next, evidence });
    const known = legs.filter((l) => l.kind === 'loaded' || l.kind === 'empty');

    // 規則 1
    const groups = new Map();
    for (const l of known) if (l.cycle_wd != null && l.dir) { const k = l.dir + '|' + l.cycle_wd; if (!groups.has(k)) groups.set(k, { d: l.dir, wd: l.cycle_wd, ls: [] }); groups.get(k).ls.push(l); }
    const hits = [];
    for (const g of groups.values()) {
      const n = g.ls.length, e = g.ls.filter((l) => l.kind === 'empty').length;
      if (n >= R1_MIN_TRIPS && e * R1_RATIO[1] >= n * R1_RATIO[0]) hits.push({ e, n, d: g.d, wd: g.wd, ls: g.ls });
    }
    hits.sort((a, b) => cmp(-a.e, -b.e) || cmp(a.n, b.n) || cmp(a.d, b.d) || cmp(a.wd, b.wd));
    for (const { e, n, d, wd, ls } of hits) {
      const others = [];
      for (const g of groups.values()) if (g.d === d && g.wd !== wd) others.push(...g.ls);
      const oe = others.filter((l) => l.kind === 'empty').length;
      const night = ls.every((l) => /^\d+$/.test((l.cycle_dep || '').slice(11, 13)) && +l.cycle_dep.slice(11, 13) >= 18);
      const when = `${WEEKDAYS[wd]}曜` + (night ? 'の夜' : '');
      const who = d === 'back' ? `${short(base)}を${when}に出た ` : `${when}に出た `;
      const body = e === n && d === 'back' ? `${n} 便の帰りがすべて空でした` : e === n ? `${n} 便がすべて空でした` : `${n} 便のうち ${e} 便が空でした`;
      const text = `${lbl[d]}の${d === 'back' ? '帰り' : '行き'}は、${who}${body}。ほかの曜日の同じ向きは ${others.length} 便中 ${oe} 便が空です（実車か空車かが分かった便だけ）。`;
      add(1, text, [{ k: `${WEEKDAYS[wd]}曜発の${lbl[d]}`, v: `${n} 便中 ${e} 便が空` }, { k: 'ほかの曜日', v: `${others.length} 便中 ${oe} 便が空` }],
        { label: '帰り便の下書きにする', action: 'draft_backhaul', payload: { dir: d, wd, weekday: WEEKDAYS[wd], trips: n, empty: e, night, month: data.month } },
        ls.slice().sort((a, b) => cmp(a.date, b.date)).map(evLeg));
    }

    // 規則 2
    const empties = legs.filter((l) => l.kind === 'empty');
    if (empties.length) {
      const km = empties.reduce((a, l) => a + (l.km || 0), 0);
      const eb = empties.filter((l) => l.dir === 'back').length, eo = empties.filter((l) => l.dir === 'out').length;
      add(2, `空で走った区間は ${empties.length} 便・約 ${commas(km)} km でした（距離はデジタコのメーターと ${data.est_label || '推定'}）。向きは ${lbl.back} が ${eb} 便、${lbl.out} が ${eo} 便です。`,
        [{ k: '空で走った便', v: `${empties.length} 便` }, { k: '空で走った距離', v: `約 ${commas(km)} km` }],
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
      add(3, `${p.muni ? p.muni + 'の' + (p.pseudo || pid) : (p.label || pid) + '（' + (p.pseudo || pid) + '）'} で、30 分以上待った記録が ${ss.length} 回、合わせて ${fix(t / 60, 1)} 時間でした`
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

  // 戻り値: { m: {code, name, pref, lon, lat, short} } か { reason }
  function matchMuni(text, munis) {
    const ix = muniIndex(munis);
    const pt = (i) => { const m = munis[i]; return m && m.lon != null && m.lat != null ? m : null; };
    for (let len = Math.min(text.length, 8); len >= 2; len--) {
      const ids = ix.byName.get(text.slice(0, len));
      if (!ids) continue;
      if (ids.length > 1) return { reason: `「${text.slice(0, len)}」は同じ名前の市区町村が複数あります` };
      const m = pt(ids[0]);
      if (m) return { m: { ...m, short: m.name.length >= 3 && SUFFIX.test(m.name) ? m.name.slice(0, -1) : m.name } };
    }
    for (const st of ix.stems) { // 「加古川」「江東」のように、市・区の字を略した書き方
      if (!text.startsWith(st)) continue;
      const ids = ix.byStem.get(st);
      if (ids.length > 1) return { reason: `「${st}」は同じ名前の市区町村が複数あります` };
      const m = pt(ids[0]);
      if (m) return { m: { ...m, short: st } };
    }
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

  const haversine = (a, b) => {
    const R = 6371.0088, rad = Math.PI / 180, dLat = (b.lat - a.lat) * rad, dLon = (b.lon - a.lon) * rad;
    const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(x));
  };

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

  function fileKind(rows) {
    const head = (rows[0] || []).map((h) => (h || '').trim());
    const hasPlate = head.some((h) => PLATE_HEADS.some((p) => h.includes(p)));
    const dates = head.filter((h) => dateHeadParts(h)).length;
    if (hasPlate && dates >= 1) return 'haisha';
    const roles = new Set(head.map(roleOf).filter(Boolean));
    if (roles.has('start_time') && roles.has('end_time') && roles.size >= 3) return 'digitaco';
    return null;
  }

  /* files: [{ name, text, encoding? }]
     options（すべて省略できる。試験では Python と同じ前提を渡す）:
       year, mon, munis[{code,name,pref,lon,lat}], userAliases{地点の文字: 市区町村名}, estKm{'a|b': km}, estSrc, estKmFn(placeA, placeB)→{km,src}|null,
       places, base, dock, aliases{known:[[別名,id]], proposed:[[別名,id]]}, vehicles[], driverLabels{}, company{}, largeMaxLoadKg, routePath */
  function analyze(files, options) {
    const o = options || {};
    // 「確認」で決めた列の役割を、辞書より先に使う
    const ov = {};
    for (const [key, dd] of Object.entries(o.decisions || {})) {
      const m = /^column:デジタコ:([\s\S]*)$/.exec(key);
      if (!m || !dd) continue;
      if (dd.act === 'skip') ov[colKey(m[1])] = null;
      else if (dd.act === 'fix') { const r = resolveRole(dd.text); if (r) ov[colKey(m[1])] = r; }
    }
    ROLE_OV = Object.keys(ov).length ? ov : null;
    try { return analyzeInner(files, o); } finally { ROLE_OV = null; }
  }

  function analyzeInner(files, o) {
    const parsed = files.map((f) => ({ name: f.name, encoding: f.encoding || null, rows: parseCsv(f.text) }));
    for (const f of parsed) f.kind = fileKind(f.rows);
    const hf = parsed.find((f) => f.kind === 'haisha') || null;
    const dfs = parsed.filter((f) => f.kind === 'digitaco');
    const otherFiles = parsed.filter((f) => !f.kind);

    // 年。配車表の見出しの曜日（10/1(木)）と合う年を使う。時計（今日の日付）は使わない。
    // 合う年が複数あるとき、曜日が無いときは year_choice.ambiguous を真にして、画面が年を選ばせる
    const dYears = [];
    for (const f of dfs) {
      const ci = (f.rows[0] || []).findIndex((h) => roleOf(h) === 'start_time');
      if (ci >= 0) for (const r of f.rows.slice(1)) { const t = parseDT(r[ci] || ''); if (t != null) dYears.push(+isoOf(t).slice(0, 4)); }
    }
    let yc;
    if (hf) {
      yc = inferYear(hf.rows[0] || []);
      if (dYears.length) { // デジタコの日付があるときは、その年（の前の年まで）に収まる年だけを残す
        const lo = Math.min(...dYears) - 1, hi = Math.max(...dYears);
        const keep = yc.source === 'weekday' ? yc.candidates.filter((c) => c >= lo && c <= hi) : [];
        const y0 = keep.length ? Math.max(...keep) : Math.min(...dYears);
        if (yc.source !== 'explicit') yc = { year: y0, candidates: keep.length ? keep : [y0], ambiguous: keep.length > 1, source: keep.length ? 'weekday' : 'digitaco' };
      }
    } else if (dYears.length) {
      const y0 = +mostCommon(dYears);
      yc = { year: y0, candidates: [y0], ambiguous: false, source: 'digitaco' };
    } else yc = inferYear([]);
    const byUser = !!o.year;
    const year = byUser ? +o.year : yc.year;
    const yearChoice = { year, candidates: yc.candidates, ambiguous: !!yc.ambiguous, source: yc.source, by_user: byUser };

    // 地点
    const places = {}, nameCount = new Map();
    const newAlias = { haisha: 0, digitaco: 0 };
    let resolveName;
    if (o.places) {
      Object.assign(places, JSON.parse(JSON.stringify(o.places)));
      const known = ((o.aliases || {}).known || []).slice().sort((a, b) => b[0].length - a[0].length);
      const proposed = (o.aliases || {}).proposed || [];
      resolveName = (name) => {
        for (const [al, pid] of known) if (name.includes(al)) return [pid, false];
        for (const [al, pid] of proposed) if (name.includes(al)) return [pid, true];
        return [null, false];
      };
    } else {
      const byKey = new Map();
      const ua = Object.entries(o.userAliases || {}).map(([k, v]) => [nfkc(k).trim(), nfkc(v).trim()]).filter(([k]) => k).sort((a, b) => b[0].length - a[0].length);
      resolveName = (name) => {
        const t = nfkc(name).trim().replace(/\s+/g, ' ');
        if (!t) return [null, false];
        let target = t, viaAlias = false;
        for (const [al, to] of ua) if (t.includes(al)) { target = to; viaAlias = true; break; }
        let res = o.munis && o.munis.length ? matchMuni(target, o.munis) : { reason: '地図の市区町村の表がありません' };
        const key = res.m ? 'm' + (res.m.code || res.m.name) : 'n' + t;
        if (!byKey.has(key)) {
          const nn = pad(Object.keys(places).length + 1), id = `p${nn}`;
          places[id] = res.m
            ? { id, pseudo: `地点 ${nn}`, short: res.m.short, muni: res.m.name, label: t, code: res.m.code, pref: res.m.pref, lon: res.m.lon, lat: res.m.lat, aliases: [t], placed: true }
            : { id, pseudo: `地点 ${nn}`, short: t.length > 6 ? t.slice(0, 6) : t, muni: '', label: t, code: null, pref: null, lon: null, lat: null, aliases: [t], placed: false, reason: res.reason };
          byKey.set(key, id);
        }
        const id = byKey.get(key);
        if (!places[id].aliases.includes(t)) places[id].aliases.push(t);
        void viaAlias;
        return [id, false];
      };
    }
    const resolve = (name, src) => {
      if (!name) return null;
      const [pid, isNew] = resolveName(name);
      if (isNew && src) newAlias[src]++;
      if (pid) nameCount.set(pid, (nameCount.get(pid) || 0) + 1);
      return pid;
    };

    // 配車表
    let wl = null, hrows = [];
    const unreadable = [];
    const haishaLegs = [];
    let nonlegCells = 0;
    if (hf) {
      hrows = hf.rows;
      wl = wideToLong(hrows, year);
      for (const u of wl.unreadable) unreadable.push({ ...u, file: hf.name });
      let hseq = 0;
      for (const r of wl.rows) {
        const pc = parseCell(r.text);
        if (pc.errors.length) { unreadable.push({ line: r.line, col: r.col, original: r.text, reason: pc.errors[0], file: hf.name, plate: r.plate }); continue; }
        if (pc.nonleg) { nonlegCells++; continue; }
        pc.legs.forEach((lg, idx) => {
          hseq++;
          const ids = [lg.frm, lg.to].map((nm) => (nm ? resolve(nm, 'haisha') : null));
          haishaLegs.push({
            id: `H${pad(hseq, 3)}`, plate: r.plate, date: r.date, idx, frm: ids[0], to: ids[1], empty: lg.empty, arr: lg.arr, weight_kg: lg.weight_kg,
            ref: { file: hf.name, where: `${r.line} 行目・${+r.date.slice(5, 7)}/${+r.date.slice(8, 10)} の列`, text: r.text },
          });
        });
      }
    }

    // デジタコ（ファイル名の 4 桁が車番。車両の並びは配車表の順）
    const order = wl ? wl.plates.map((p) => p.plate) : [];
    const dInfo = dfs.map((f) => ({ f, plate: normalizePlate(f.name.replace(/\.[^.]*$/, '')) }));
    const dOk = dInfo.filter((x) => x.plate).sort((a, b) => {
      const ia = order.indexOf(a.plate), ib = order.indexOf(b.plate);
      return (ia < 0 ? 1e9 : ia) - (ib < 0 ? 1e9 : ib) || cmp(a.f.name, b.f.name);
    });
    const runs = [], stops = [], dFiles = [], digValues = {};
    let dseq = 0, sseq = 0;
    const noPlate = dInfo.filter((x) => !x.plate);
    for (const { f, plate } of dOk) {
      const head = f.rows[0] || [];
      const recs = f.rows.slice(1).filter((r) => !blankRow(r));
      head.forEach((h, ci) => { if (!digValues[h]) digValues[h] = []; for (const r of recs) digValues[h].push(r[ci] == null ? '' : r[ci]); });
      const pr = parseDigitaco(f.rows, (nm) => resolve(nm, 'digitaco'), plate);
      for (const x of pr.runs) { dseq++; x.id = `D${pad(dseq, 3)}`; x.ref = { file: f.name, where: `${x.line} 行目`, text: `走行 ${x.start.slice(11, 16)}〜${x.end.slice(11, 16)} ${x.km == null ? 'None' : pyFloat(x.km)} km` }; runs.push(x); }
      for (const x of pr.stops) { sseq++; x.id = `S${pad(sseq, 3)}`; x.ref = { file: f.name, where: `${x.line} 行目`, text: `停車 ${x.start.slice(11, 16)}〜${x.end.slice(11, 16)}（${x.minutes} 分）` }; stops.push(x); }
      for (const e of pr.errors) unreadable.push({ line: e.line, col: null, original: '', reason: e.reason, file: f.name, plate, where: `${e.line} 行目` });
      dFiles.push({ file: f.name, plate, rows: recs.length, errors: pr.errors.length, columns: head.slice(), encoding: f.encoding });
    }
    for (const x of noPlate) {
      unreadable.push({ line: 1, col: null, original: x.f.name, reason: 'ファイル名から車番が分かりません（digitaco_1234.csv のように、車番 4 桁を入れてください）', file: x.f.name, where: 'ファイル名' });
    }
    for (const f of otherFiles) {
      unreadable.push({ line: 1, col: null, original: (f.rows[0] || []).slice(0, 6).join('、'), reason: '配車表にもデジタコにも見えません（車番と日付の列、または開始・終了の日時の列が必要です）', file: f.name, where: '見出しの行' });
    }

    // 車両
    const vlist = [];
    const addPlate = (plate, raw, type) => { if (!vlist.some((v) => v.plate === plate)) vlist.push({ plate, raw, type }); };
    if (wl) for (const p of wl.plates) addPlate(p.plate, p.raw, p.type);
    for (const { plate } of dOk) addPlate(plate, plate, '');
    const dvals = (plate) => dFiles.find((d) => d.plate === plate);
    const firstDriver = (plate) => { const r = runs.find((x) => x.plate === plate); return r ? r.driver : null; };
    const dl = o.driverLabels || {};
    const givenV = {};
    for (const v of o.vehicles || []) givenV[v.plate] = v;
    const largeKg = o.largeMaxLoadKg || 10000;
    const vehicles = vlist.map(({ plate, raw, type }) => {
      if (givenV[plate]) return { registered: true, ...givenV[plate] };
      const cls = classOf(type), digital = !!dvals(plate), drv = firstDriver(plate);
      return {
        plate, raw_plate: raw, type_raw: type, cls, gvw_kg: null, max_load_kg: cls === '大型' ? largeKg : null, registered: false, max_load_assumed: cls === '大型',
        tachograph: digital ? 'digital' : 'analog', tachograph_label: TACHO_LABEL[digital ? 'digital' : 'analog'], driver: drv ? (dl[drv] || drv) : null,
      };
    });

    // 基点と主な行き先
    let base = o.base, dock = o.dock;
    if (!base) {
      const fr = haishaLegs.map((l) => l.frm).filter(Boolean);
      base = mostCommon(fr) || mostCommon(runs.map((r) => r.frm).filter(Boolean)) || null;
    }
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
      if (o.estKm) return null; // 試験用に推定の表を渡したときは、その表にある組だけ
      if (a.lon == null || b.lon == null) return null;
      return { km: pyRound(haversine(a, b) * ROAD_FACTOR, 1), src: `直線距離 × ${ROAD_FACTOR} の推定` };
    };

    // 結び付けと実車・空車
    const lk = link(haishaLegs, runs, estFn);
    const cl = classifyLegs(lk.legs);
    const legs = cl.legs;
    for (const l of legs) l.dir = (dock && l.to === dock) || (base && l.frm === base) ? 'out' : ((base && l.to === base) || (dock && l.frm === dock) ? 'back' : null);
    legs.sort((a, b) => cmp(a.date, b.date) || cmp(a.plate, b.plate) || cmp(a.dir === 'out' ? 0 : 1, b.dir === 'out' ? 0 : 1));
    legs.forEach((l, i) => { l.id = `L${pad(i + 1, 3)}`; });
    const decOf = (key) => (o.decisions || {})[key] || null;
    const legNote = {}; // 確認の決定のうち、区間に当てたもの { key: { applied, note } }
    for (const l of legs) {
      for (const fl of l.flags) {
        const key = `link:${l.id}:${fl.type}`, dd = decOf(key);
        if (!dd) continue;
        let applied = false, note = '';
        if (dd.act === 'ok') note = '確定しました（値は変えていません）';
        else if (fl.type === 'distance') {
          if (dd.act === 'skip') {
            const ends = endsOfLeg(l), est = ends.length === 2 ? estFn(ends) : null;
            if (est) { l.km = est.km; l.sources.km = est.src; applied = true; note = `デジタコの距離を読まず、${est.src}の ${pyFloat(est.km)} km を使いました`; } else note = '代わりの推定距離がないので、デジタコの距離のままです';
          } else {
            const v = parseKm(dd.text);
            if (v != null) { l.km = v; l.sources.km = '利用者の修正'; applied = true; note = `距離を ${pyFloat(v)} km にしました`; } else note = '距離を数で入れてください（例: 551.8）。値は変えていません';
          }
        } else if (dd.act === 'skip') { l.arr = null; l.sources.time = '配車表'; applied = true; note = 'デジタコの到着時刻を読まず、配車表の着の時刻を使います（実車率などは変わりません）'; }
        else {
          const t = parseTime(dd.text);
          if (t) { l.arr = (l.arr ? l.arr.slice(0, 11) : `${l.date}T`) + t; l.sources.time = '利用者の修正'; applied = true; note = `到着時刻を ${t} にしました（実車率などは変わりません）`; } else note = '時刻を 5:30 のように入れてください。値は変えていません';
        }
        legNote[key] = { applied, note };
      }
    }
    for (const l of legs) {
      if (l.dir === 'out') { l.cycle_wd = weekdayOf(l.date); l.cycle_dep = l.dep; }
      else {
        const prev = legs.filter((x) => x.plate === l.plate && x.dir === 'out' && daysBetween(x.date, l.date) > 0 && daysBetween(x.date, l.date) <= 2);
        if (prev.length) {
          let oo = prev[0];
          for (const x of prev) if (x.date > oo.date) oo = x;
          l.cycle_wd = weekdayOf(oo.date); l.cycle_dep = oo.dep;
        } else { l.cycle_wd = weekdayOf(addDays(l.date, -1)); l.cycle_dep = null; }
      }
      l.color = l.flags.some((fl) => !decOf(`link:${l.id}:${fl.type}`)) ? 'yellow' : l.kind === 'unknown' ? 'gray' : 'green';
    }
    const legByDid = {};
    for (const l of legs) if (l.did) legByDid[l.did] = l;
    for (const s of stops) {
      const prev = runs.filter((r) => r.plate === s.plate && r.end === s.start);
      s.cycle_wd = prev.length && legByDid[prev[0].id] ? legByDid[prev[0].id].cycle_wd : null;
    }

    // 拘束時間
    const shifts = [];
    let kseq = 0;
    for (const v of vehicles) {
      const mine = runs.filter((r) => r.plate === v.plate);
      if (!mine.length) continue;
      const evs = mine.map((r) => ({ start: r.start, end: r.end, line: r.line })).concat(stops.filter((s) => s.plate === v.plate).map((s) => ({ start: s.start, end: s.end, line: s.line })));
      const drv = mine[0].driver, sh = buildShifts(evs);
      for (const k of kousokuHours(sh)) {
        kseq++;
        const lines = (sh.find((x) => x.start === k.start).lines).filter((n) => n);
        const { lines: _drop, ...kk } = k;
        shifts.push({ ...kk, id: `K${pad(kseq, 3)}`, driver: drv, driver_label: dl[drv] || drv, plate: v.plate, ref: { file: dFiles.find((d) => d.plate === v.plate).file, where: `${Math.min(...lines)}〜${Math.max(...lines)} 行目` } });
      }
    }

    const estLabel = o.estKm ? 'OSRM の推定' : '推定';
    const kp = kpis(legs, vehicles);
    const table = weekdayTable(legs, stops, dock);
    const mon = legs.length ? o.mon || +(mostCommon(haishaLegs.map((l) => +l.date.slice(5, 7))) || mostCommon(runs.map((r) => +r.start.slice(5, 7))) || 0) : null;
    const ins = insights({ legs, stops, shifts, vehicles, places, base, dock, month: mon, est_label: estLabel });

    // 列の対応づけ
    const { columns, items: colItems } = buildColumns(wl, hrows, digValues, hf);

    // 確認待ち
    const confirm = [];
    const aliasTotal = newAlias.haisha + newAlias.digitaco;
    if (aliasTotal && o.places && dock && places[dock]) {
      confirm.push({
        id: 'C1', key: 'alias:辰巳', effect: 'none', kind: 'alias', source: '配車表・デジタコ', title: '地点の別名「辰巳」', original: `「辰巳」（配車表 ${newAlias.haisha} か所、デジタコ ${newAlias.digitaco} か所）`,
        proposal: `${places[dock].pseudo}（${places[dock].label}）の別名にする`, confidence: 0.7, color: 'yellow',
        formula: `辞書に無い別名です。${places[dock].pseudo} の住所「江東区辰巳」に含まれるため、ローカルの規則で候補にしました（AI は使っていません）`, claude: null, count: aliasTotal,
      });
    }
    for (const it of colItems) if (it.color === 'yellow') confirm.push(it);
    for (const l of legs) {
      for (const fl of l.flags) {
        let orig, prop;
        if (fl.type === 'time') { orig = `配車表の着 ${fl.hint} と、デジタコの到着 ${fl.digitaco} が ${Math.abs(fl.diff_min)} 分ずれています`; prop = '時刻はデジタコの値を使う'; }
        else { orig = `デジタコの走行距離 ${pyFloat(fl.digitaco_km)} km が、${estLabel} ${pyFloat(fl.est_km)} km より ${pyRound(fl.ratio * 100)}% 長くなっています`; prop = '距離はデジタコのメーターを使う（迂回した可能性）'; }
        confirm.push({
          id: '', key: `link:${l.id}:${fl.type}`, effect: fl.type === 'distance' ? 'kpi' : 'display', kind: 'link', source: '配車表とデジタコ', title: `${md(l.date)} 車番 ${l.plate} の${l.dir === 'out' ? '行き' : '帰り'}（${fl.type === 'time' ? '時刻' : '距離'}）`,
          original: orig, proposal: prop, confidence: null, color: 'yellow', leg: l.id, formula: '距離が 15% 以上、時刻が 60 分以上ずれたら確認待ちに入れます', claude: null,
        });
      }
    }
    confirm.forEach((it, n) => { it.id = `C${n + 1}`; });
    for (const c of confirm) { // 利用者の決定を添える（カードは決定のあとも残す。取り消せるように）
      const dd = decOf(c.key);
      if (!dd) { c.decision = null; continue; }
      let applied = false, note = '';
      if (c.kind === 'link') ({ applied, note } = legNote[c.key] || { applied: false, note: '' });
      else if (c.kind === 'column') {
        const known = !!roleOfDict(c.column);
        if (dd.act === 'ok') note = '確定しました（読み方は変えていません）';
        else if (c.source !== 'デジタコ') note = '配車表の列は、読み方を変えられません。計算は変わりません';
        else if (dd.act === 'skip') { applied = true; note = known ? 'この列を読まずに計算し直しました' : '辞書にない列なので、もともと読んでいません。計算は変わりません'; }
        else { const r = resolveRole(dd.text); applied = !!r; note = r ? `この列を「${ROLE_LABEL[r]}」として計算し直しました` : '役割の名前が分かりません（例: 走行距離、開始の日時）。読み方は変えていません'; }
      } else note = dd.act === 'ok' ? '確定しました' : '計算は変わりません';
      c.decision = { act: dd.act, text: dd.text || '', applied, note };
    }
    if (hf) for (const u of unreadable) if (u.file === hf.name && u.col != null && u.where == null) u.where = `${u.line} 行目・「${hrows[0][u.col]}」の列`;
    for (const u of unreadable) if (u.where == null) u.where = `${u.line} 行目`;

    // ファイルごとの結果
    const files2 = [];
    const hUn = unreadable.filter((u) => hf && u.file === hf.name).length;
    if (hf) {
      const hs = wl.stats, hCells = wl.rows.length + wl.unreadable.length;
      const pendH = confirm.filter((c) => c.kind === 'alias' || c.kind === 'link' || c.source === '配車表').length;
      files2.push({
        file: hf.name, kind: '配車表', shape: '日付が列に並ぶ横の形', encoding: hf.encoding,
        verdict: `日付が列に並ぶ横の形です。1 行 = 1 車両 × 1 日に直しました（車番 ${hs.plate_groups} 台、日付の列 ${hs.date_cols} 個。`
          + `車番が空欄の続きの行 ${hs.filled_rows} か所を、上の行の車番で埋めました。色は読みません）。`,
        cells: hCells, ok: hCells - hUn, pending: pendH, unreadable: hUn,
      });
    }
    const pendD = confirm.filter((c) => c.source === 'デジタコ').length;
    dFiles.forEach((df, i) => {
      const mapped = df.columns.filter((c) => roleOf(c)).length, miss = df.columns.filter((c) => !roleOf(c));
      files2.push({
        file: df.file, kind: 'デジタコ', shape: '1 行 = 1 回の走行か停車の縦の形', encoding: df.encoding,
        verdict: `1 行 = 1 回の走行か停車の縦の形です。列 ${df.columns.length} 個のうち ${mapped} 個を辞書で対応づけました。${miss.length} 個は分かりません（列名: ${miss.join('、')}）。読まずに進めます。`,
        cells: df.rows, ok: df.rows - df.errors, pending: i === 0 ? pendD : 0, unreadable: df.errors,
        note: i === 0 ? null : `列の対応は ${dFiles[0].file} と同じです`,
      });
    });
    for (const x of noPlate) files2.push({ file: x.f.name, kind: 'デジタコ', shape: '読めませんでした', encoding: x.f.encoding, verdict: 'ファイル名から車番が分からないため、読みませんでした。', cells: 0, ok: 0, pending: 0, unreadable: 1 });
    for (const f of otherFiles) files2.push({ file: f.name, kind: '不明', shape: '読めませんでした', encoding: f.encoding, verdict: '配車表にもデジタコにも見えないため、読みませんでした。', cells: Math.max(0, f.rows.length - 1), ok: 0, pending: 0, unreadable: 1 });

    const counts = {
      legs: legs.length, loaded: cl.counts.loaded, empty: cl.counts.empty, unknown: cl.counts.unknown, unknown_ratio: pyRound(cl.unknown_ratio, 4),
      stops: stops.length, shifts: shifts.length, confirm: confirm.length, unreadable: unreadable.length, digitaco_only: lk.digitaco_only, haisha_only: lk.haisha_only, nonleg_cells: nonlegCells,
      outside_pair: legs.filter((l) => !l.dir).length,
    };
    const unplaced = Object.values(places).filter((p) => !p.placed && p.code == null && o.places == null).map((p) => ({ id: p.id, name: p.label, count: nameCount.get(p.id) || 0, reason: p.reason }));
    const co = o.company || {};
    return {
      version: 1, fiction: false, month: legs.length ? `${pad(year, 4)}-${pad(mon)}` : '', year, mon, year_choice: yearChoice,
      period: legs.length ? { from: legs.reduce((a, l) => (l.date < a ? l.date : a), legs[0].date), to: legs.reduce((a, l) => (l.date > a ? l.date : a), legs[0].date) } : null,
      failed: legs.length ? null : '区間を 1 件も読めませんでした' + (unreadable.length ? '（' + unreadable.slice(0, 3).map((u) => `${u.file}: ${u.reason}`).join('、') + '）' : ''),
      company: { name: co.name || '自社のファイル', city: co.city || '', code: co.code || '', vehicles: vehicles.length },
      places, base, dock,
      route: { path: o.routePath || null, osrm_km: null, note: o.routeNote || '' },
      vehicles, files: files2, unreadable, paper: { count: 0, text: '紙の日報・チャート紙は読みません' },
      columns: columns.map((c) => { const { claude_input: _x, ...rest } = c; return rest; }),
      claude: { present: false, model: null, created_at: null, label: 'Claude の提案: 使いません' },
      confirm, legs: legs.map(legJson), stops: stops.map(stopJson), shifts,
      kpis: kp, table, insights: ins, counts, national: NATIONAL, unplaced,
      encodings: parsed.map((f) => ({ file: f.name, encoding: f.encoding })),
    };
  }

  function legJson(l) {
    const keep = ['id', 'plate', 'date', 'dir', 'cycle_wd', 'cycle_dep', 'dep', 'arr', 'arr_hint', 'frm', 'to', 'km', 'kind', 'kind_src', 'weight_kg', 'flags', 'color', 'ref', 'dref', 'driver'];
    const d = {};
    for (const k of keep) d[k] = l[k] === undefined ? null : l[k];
    d.km_src = (l.sources || {}).km == null ? null : l.sources.km;
    d.time_src = (l.sources || {}).time == null ? null : l.sources.time;
    return d;
  }
  function stopJson(s) {
    const d = {};
    for (const k of ['id', 'plate', 'place', 'name', 'start', 'end', 'minutes', 'driver', 'cycle_wd', 'ref']) d[k] = s[k] === undefined ? null : s[k];
    return d;
  }

  // 列ごとに、辞書の結果・値の形の一致率を出す。Claude の一致は使わない（常に偽）
  function buildColumns(wl, hrows, digValues, hf) {
    const columns = [], items = [];
    const specs = [];
    if (wl && hf) {
      const head = wl.head, pc = wl.plateCol, tc = wl.typeCol;
      const plateVals = hrows.slice(1).map((r) => r[pc] || '').filter((v) => v.trim());
      const dateHeads = head.filter((h, i) => i !== pc && i !== tc);
      specs.push(['配車表', head[pc] || '車番', 'plate', true, plateVals]);
      if (tc >= 0) specs.push(['配車表', head[tc], 'vehicle_type', true, hrows.slice(1).map((r) => r[tc] || '').filter((v) => v.trim())]);
      specs.push(['配車表', `日付の見出し（${dateHeads.length} 列）`, 'date', true, dateHeads]);
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
      const entry = {
        source, column: col, dict_hit: hit, dict_role: hit ? role : null, dict_label: roleLabel, shape_expected: exp, shape_ratio: pyRound(ratio, 4), shape_best_role: hit ? null : (role || null),
        claude_proposal: null, claude_note: '使いません（ブラウザの中だけで計算します）', claude_agrees: false, confidence: c, color,
        formula: `0.5 × max(辞書 ${hit ? 1 : 0}, Claude 0) + 0.5 × 値の形 ${fix(ratio, 3)} = ${fix(c, 3)}`,
        claude_input: { source, column: col, row_count: vals.length, non_blank_ratio: pyRound(nonBlank(vals).length / Math.max(1, vals.length), 4) },
      };
      columns.push(entry);
      if (color === 'yellow') {
        items.push({
          id: '', key: `column:${source}:${col}`, effect: source === 'デジタコ' ? 'kpi' : 'none', kind: 'column', source, file: source === '配車表' ? hf.name : 'デジタコの CSV', title: `列「${col}」の対応づけ`, original: `列名「${col}」。値の形は「${exp}」に ${pyRound(ratio * 100)}% 一致`,
          proposal: `${roleLabel || '不明'}として読む`, confidence: c, color, formula: entry.formula, claude: null, column: col,
        });
      }
    }
    return { columns, items };
  }

  /* ------------------------------------------------------------ 書き出し */

  const csvCell = (v) => { const s = v == null ? '' : String(v); return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
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
    const out = { perKm: k, perHour: h, errors: { perKm: pk.error, perHour: ph.error }, emptyKm: null, longCount: 0, longMin: 0, empty: null, wait: null };
    if (!d.legs.length) return out;
    const emptyKm = pyRound(d.legs.filter((l) => l.kind === 'empty').reduce((a, l) => a + (l.km || 0), 0), 1);
    const long = d.stops.filter((s) => s.minutes >= R3_MIN_STOP_MIN);
    const longMin = long.reduce((a, s) => a + s.minutes, 0);
    out.emptyKm = emptyKm; out.longCount = long.length; out.longMin = longMin;
    if (k != null) out.empty = { yen: Math.round(emptyKm * k), formula: `空車 ${commas1(emptyKm)} km × ${num6(k)} 円/km` };
    if (h != null) { const hours = pyRound(longMin / 60, 1); out.wait = { yen: Math.round(hours * h), formula: `30 分以上の停車 ${fix(hours, 1)} 時間 × ${num6(h)} 円/時` }; }
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
    L.push(`空車で走った距離 ${commas1(c.emptyKm)} km、30 分以上の停車 ${c.longCount} 回・${fix(c.longMin / 60, 1)} 時間`);
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
        basis: ks === 'デジタコ' ? '実測' : ks === '利用者の修正' ? '仮定' : '推定',
        source: [l.ref, l.dref].filter((x) => x).map((x) => `${x.file} ${x.where}`).join(' / ') || '配車表・デジタコ',
      });
    }
    return {
      meta: { title: `${d.company.name} ${d.year} 年 ${d.mon} 月の運行`, fiction: !!d.fiction, note: skipped ? `地図に置けない地点を通る区間 ${skipped} 件は含めていません` : '' },
      legs,
    };
  }

  return {
    analyze, decode, parseCsv, wideToLong, parseCell, parseTime, parseDigitaco, normalizePlate, link, classifyLegs, confidence, confidenceColor, shapeRatio, bestRoleFor,
    kousokuHours, buildShifts, kpis, weekdayTable, insights, matchMuni, legsCsv, costs, summaryText, pyRound,
    toKitLegs, resolveRole, inferYear, assignHeadYears, parseKm, parseUnit, YEAR_RANGE: [YEAR_MIN, YEAR_MAX], DIGITACO_DICT, HAISHA_DICT, ROLE_LABEL, NATIONAL,
  };
})();
if (typeof window !== 'undefined') window.OwnCalc = OwnCalc;
