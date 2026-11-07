'use strict';
/* 自社の運行の補助（SPEC §41）。own.js から使う。計算と保存だけを持ち、画面の描画は own.js が行う。
   - 月ごとの結果の保存と並べる比較（localStorage。try/catch。ファイルの中身は保存しない: 数字の要約、地名の対応、確認の決定、列の役割だけ）
   - 帰り荷 1 便の売上の目安（標準的運賃の距離制の表。data/tariffs/mlit_standard_fare_*.json と同じ数。試験で照合する）
   - 求荷求車に貼る文、デジタコの停車から荷待ちの下書き、用語の一言説明 */

const OwnMonth = (() => {
  const KEY = 'backfull.own.v41';
  const ROLE_KEY = 'backfull.own.v41.roles';
  const pad2 = (n) => String(n).padStart(2, '0');
  const round1 = (v) => Math.round(v * 10) / 10;

  /* ------------------------------------------------------------ 用語の一言説明 */

  const GLOSS = {
    '実車率': '荷を積んで走った割合',
    '積載率': '積んだ重さの割合',
    '積載効率': '積載率×実車率',
    '空車 km': '荷なしで走った距離',
    '1口': '空きを売る単位',
    '基準積載率': '運賃を割る積み具合',
  };

  // 用語のあとに付ける「?」。押す・Enter・フォーカスで開く。マウスを乗せても開く（CSS）
  function tipHtml(term, extra) {
    const t = GLOSS[term];
    if (!t) return '';
    return `<span class="o-tipw"><button type="button" class="o-tip" aria-label="${term}とは" aria-expanded="false" title="${t}">?</button><span class="o-tipb" role="tooltip">${t}${extra ? extra : ''}</span></span>`;
  }

  /* ------------------------------------------------------------ 保存（localStorage） */

  function hash(s) {
    let h = 0x811c9dc5;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return h.toString(16).padStart(8, '0');
  }
  // 読んだファイルの一式の署名（名前と中身）。同じファイルを開き直したか、を見分ける
  const sig = (files) => hash(files.map((f) => `${f.name}\u0001${f.text}`).sort().join('\u0002'));
  // 列の役割を覚える鍵: 見出しの行の形（数字は # にする。月が変わっても同じ形のファイルに当たる）
  const headSig = (text) => hash(String(text || '').split(/\r?\n/).slice(0, 5).join('\n').replace(/[0-9０-９]+/g, '#'));

  function readJSON(key) {
    try { const t = localStorage.getItem(key); return t ? JSON.parse(t) : null; } catch (e) { return null; }
  }
  function writeJSON(key, v) {
    try { localStorage.setItem(key, JSON.stringify(v)); return true; } catch (e) { return false; }
  }
  const list = () => { const o = readJSON(KEY); return o && Array.isArray(o.entries) ? o.entries : []; };
  // 同じ月は最新の 1 件に置き換える（同じ月の別の版のファイルを何度も落としても、同じ月が並ばない）。ほかの月は残す。24 件まで。
  function save(entry) {
    const es = list().filter((e) => e.month !== entry.month);
    es.push({ ...entry, savedAt: new Date().toISOString() });
    es.sort((a, b) => (a.month < b.month ? -1 : a.month > b.month ? 1 : 0));
    return writeJSON(KEY, { v: 1, entries: es.slice(-24) });
  }
  function clearAll() { try { localStorage.removeItem(KEY); localStorage.removeItem(ROLE_KEY); } catch (e) { /* 使えないとき */ } }
  const rolesGet = (hs) => { const o = readJSON(ROLE_KEY); return (o && o[hs]) || null; };
  function rolesSet(hs, roles) { const o = readJSON(ROLE_KEY) || {}; o[hs] = roles; writeJSON(ROLE_KEY, o); }

  // 画面に出す数字の要約。ファイルの中身は入れない
  function summary(d, c) {
    const k = d.kpis;
    return {
      month: `${d.year}-${pad2(d.mon)}`, label: `${d.year} 年 ${d.mon} 月`,
      actual_rate: k.actual_rate.value, load_rate: k.load_rate.value, load_eff: k.load_efficiency.value,
      empty_km: c ? c.emptyKm : round1(k.actual_rate.km_empty || 0), wait_min: c ? (c.waitMissing ? null : c.longMin) : 0, wait_count: c ? (c.waitMissing ? null : c.longCount) : 0,
      legs: d.counts.legs, vehicles: d.vehicles.length, company: d.company ? d.company.name : '',
    };
  }

  // 2 か月以上を一緒に落としたとき（d.multi_month）は、月ごとの要約を返す（d.months_detail）。1 か月ならこれまでの summary 1 件
  function summaries(d, c) {
    const md = d.multi_month && Array.isArray(d.months_detail) && d.months_detail.length > 1 ? d.months_detail : null;
    if (!md) return [summary(d, c)];
    return md.map((m) => {
      const k = m.kpis || {}, st = (d.stops || []).filter((x) => x.minutes >= 30 && String(x.start).slice(0, 7) === m.month);
      return {
        month: m.month, label: m.label || `${m.year} 年 ${m.mon} 月`,
        actual_rate: k.actual_rate ? k.actual_rate.value : null, load_rate: k.load_rate ? k.load_rate.value : null, load_eff: k.load_efficiency ? k.load_efficiency.value : null,
        empty_km: round1((k.actual_rate && k.actual_rate.km_empty) || 0), wait_min: c && c.waitMissing ? null : st.reduce((a, x) => a + x.minutes, 0), wait_count: c && c.waitMissing ? null : st.length,
        legs: m.counts ? m.counts.legs : 0, vehicles: d.vehicles.length, company: d.company ? d.company.name : '',
      };
    });
  }

  const pp = (a, b) => (a == null || b == null ? '—' : `${(b - a) * 100 >= 0 ? '+' : '−'}${Math.abs((b - a) * 100).toFixed(1)} ポイント`);
  const sgn = (a, b, unit, dig = 1) => (a == null || b == null ? '—' : `${b - a >= 0 ? '+' : '−'}${Math.abs(b - a).toLocaleString('ja-JP', { minimumFractionDigits: dig, maximumFractionDigits: dig })} ${unit}`);
  const pct = (v) => (v == null ? '—' : (v * 100).toFixed(1) + '%');
  const hours = (m) => (m == null ? '—' : (m / 60).toFixed(1) + ' 時間');

  // 前の月と今月を並べた行。良くなったか悪くなったかは決めず、差だけを出す
  function compareRows(prev, cur) {
    return [
      { name: '実車率', a: pct(prev.actual_rate), b: pct(cur.actual_rate), diff: pp(prev.actual_rate, cur.actual_rate) },
      { name: '大型の積載率', a: pct(prev.load_rate), b: pct(cur.load_rate), diff: pp(prev.load_rate, cur.load_rate) },
      { name: '空車 km', a: `${(prev.empty_km ?? 0).toLocaleString('ja-JP', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} km`, b: `${(cur.empty_km ?? 0).toLocaleString('ja-JP', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} km`, diff: sgn(prev.empty_km, cur.empty_km, 'km') },
      { name: '荷待ち（30 分以上の停車）', a: hours(prev.wait_min), b: hours(cur.wait_min), diff: sgn(prev.wait_min == null ? null : prev.wait_min / 60, cur.wait_min == null ? null : cur.wait_min / 60, '時間') },
    ];
  }

  /* ------------------------------------------------------------ 帰り荷 1 便の売上の目安 */

  // data/tariffs/mlit_standard_fare_kanto.json と _kinki.json の数（試験 tests/test_v41_own_screen.py が照合する）
  const FARE = {
    source: 'https://www.mlit.go.jp/report/press/content/001732088.pdf', checked_on: '2026-11-03', base_km: 200, step_km: 20, over_500_km: 500, over_500_step_km: 50,
    regions: {
      kanto: { label: '関東運輸局', v: { '2t': [52300, 3830, 9580], '4t': [60360, 4380, 10950], '10t': [80110, 5850, 14620] } },
      kinki: { label: '近畿運輸局', v: { '2t': [50270, 3680, 9210], '4t': [57350, 4180, 10450], '10t': [77370, 5650, 14130] } },
    },
    kanto_prefs: ['東京都', '茨城県', '栃木県', '群馬県', '埼玉県', '千葉県', '神奈川県', '山梨県'],
    kinki_prefs: ['滋賀県', '京都府', '大阪府', '兵庫県', '奈良県', '和歌山県'],
  };
  const CLASS_OF = { '小型': '2t', '中型': '4t', '大型': '10t' };
  // 車の種類（own_calc.js の cls: 大型 / 4t / 2t / 3t / 不明 など）を標準的運賃の車格（2t・4t・10t）にする。
  // 8t 以上は 10t、3t 以下は 2t、その間は 4t。種類が分からないとき（不明・空）は null で、売上の目安に数えない
  function tariffClass(cls) {
    const c = String(cls == null ? '' : cls).normalize('NFKC').trim();
    if (CLASS_OF[c]) return CLASS_OF[c];
    const m = /^(\d+(?:\.\d+)?)\s*(?:t|トン)$/i.exec(c);
    if (!m) return null;
    const t = +m[1];
    return t >= 8 ? '10t' : t <= 3 ? '2t' : '4t';
  }
  const ceil9 = (x) => Math.ceil(Math.round(x * 1e9) / 1e9);

  // 距離 km の距離制運賃（estimate.kashikiri_fares と同じ式）。200 km 未満は表が無いので null
  function fare(km, cls, pref) {
    const rk = FARE.kinki_prefs.includes(pref) ? 'kinki' : 'kanto', reg = FARE.regions[rk], row = reg.v[cls];
    if (!row || km == null || !(km >= FARE.base_km)) return null;
    let steps, over = 0;
    if (km <= FARE.over_500_km) steps = ceil9((km - FARE.base_km) / FARE.step_km);
    else { steps = ceil9((FARE.over_500_km - FARE.base_km) / FARE.step_km); over = ceil9((km - FARE.over_500_km) / FARE.over_500_step_km); }
    const amount = row[0] + steps * row[1] + over * row[2];
    const f = `${row[0].toLocaleString('ja-JP')}${steps ? ` + ${steps} 区分 × ${row[1].toLocaleString('ja-JP')}` : ''}${over ? ` + ${over} 区分 × ${row[2].toLocaleString('ja-JP')}` : ''}`;
    return { amount, formula: f, region: reg.label, regionKey: rk, assumedRegion: rk === 'kanto' && !FARE.kanto_prefs.includes(pref), steps, over };
  }

  const WDN = '月火水木金土日';
  const wdOf = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || ''); return m ? (new Date(+m[1], +m[2] - 1, +m[3]).getDay() + 6) % 7 : null; };
  // 市の名前だけが書かれて市役所のある区に置いた地点は、その区を書かない（別の区かもしれない）。元の名前に「（区は確認）」を付ける
  const isHallWard = (p) => !!(p && (p.hall_ward || (p.note && /市役所のある区/.test(p.note))));
  const placeName = (p) => {
    if (!p) return '（地点を入れてください）';
    if (isHallWard(p)) { const l = p.label || p.short || '', pf = p.pref || ''; return `${pf && l.startsWith(pf) ? '' : pf}${l}（区を確かめてください）`; }
    return `${p.pref || ''}${p.muni || p.label || ''}`;
  };
  const shortName = (p) => (p ? (p.short || p.muni || p.label || '?') : '?');

  // 空車の帰りの便（向きが帰りで、空車と分かった便）。発地ごとに別の区間として扱う
  const emptyBackLegs = (d) => d.legs.filter((l) => l.dir === 'back' && l.kind === 'empty');

  /* 帰り荷 1 便の売上の目安: 空車の帰りの便ごとに、その便の車の種類（2t・4t・10t。8t 以上は 10t）と km で標準的運賃を出す。
     区間（発地 → 着地）ごとの小計と、全部の帰りに荷が付いた場合の上限（便ごとの合計）を返す。
     車の種類が分からない便と、200 km 未満で表が無い便は数えず、件数を返す。 */
  function revenue(d) {
    const back = emptyBackLegs(d);
    if (!back.length) return null;
    const dock = d.places[d.dock], base = d.places[d.base], fallbackPref = (dock && dock.pref) || (base && base.pref) || '';
    const res = { trips: back.length, counted: 0, noKm: 0, noClass: 0, noFare: 0, perTrip: [], pairs: [], total: null, min: null, max: null, example: null };
    const pairs = new Map();
    for (const l of back) {
      const v = d.vehicles.find((x) => x.plate === l.plate), cls = tariffClass(v && v.cls), a = d.places[l.frm];
      if (l.km == null) { res.noKm++; continue; }
      if (!cls) { res.noClass++; continue; }
      // 運賃の地域は、その便の発地（帰りの出発地）の県で決める。置けないときは車庫の県
      const f = fare(l.km, cls, (a && a.pref) || fallbackPref);
      if (!f) { res.noFare++; continue; }
      const t = { id: l.id, plate: l.plate, date: l.date, frm: l.frm, to: l.to, km: l.km, cls, rawCls: v && v.cls, fare: f, amount: f.amount };
      res.perTrip.push(t);
      const k = `${l.frm}|${l.to}`;
      if (!pairs.has(k)) pairs.set(k, { frm: l.frm, to: l.to, n: 0, subtotal: 0, min: Infinity, max: 0, kmMin: Infinity, kmMax: 0, byCls: {} });
      const p = pairs.get(k);
      p.n++; p.subtotal += f.amount; p.min = Math.min(p.min, f.amount); p.max = Math.max(p.max, f.amount); p.kmMin = Math.min(p.kmMin, l.km); p.kmMax = Math.max(p.kmMax, l.km);
      p.byCls[cls] = (p.byCls[cls] || 0) + 1;
    }
    res.counted = res.perTrip.length;
    res.pairs = [...pairs.values()].sort((x, y) => y.n - x.n);
    if (res.counted) {
      res.total = res.perTrip.reduce((a, t) => a + t.amount, 0);
      res.min = Math.min(...res.perTrip.map((t) => t.amount)); res.max = Math.max(...res.perTrip.map((t) => t.amount));
      const sorted = res.perTrip.slice().sort((x, y) => x.amount - y.amount);
      res.example = sorted[sorted.length >> 1];
    }
    return res;
  }

  /* 売上の目安が数えた範囲。数えたのは「車庫に向かう帰り」（dir が back）の空車だけ。ほかの空車（遠くの発地からの空車、車庫から出る向きの空車、向きが決まらない空車）は数えない。
     空車の費用は全部の空車で計算するので、範囲が違う。画面に「帰り ○ 便だけ。ほかの空車 ○ 便（○ km）は数えていません」と書くための数 */
  function revenueScope(d) {
    const all = d.legs.filter((l) => l.kind === 'empty'), back = all.filter((l) => l.dir === 'back'), other = all.filter((l) => l.dir !== 'back');
    const km = (ls) => round1(ls.reduce((a, l) => a + (l.km || 0), 0));
    return { all: all.length, back: back.length, other: other.length, otherKm: km(other), allKm: km(all), backKm: km(back) };
  }

  /* 自社の成約単価（円/km か 円/便）で出した帰り荷の売上の目安。rate: { kind: 'km' | 'trip', value }。標準的運賃とは別に出す。
     円/km は距離が分かる便だけ、円/便はすべての空車の帰りの便に掛ける。値が正の数でないときは null */
  function ownRevenue(d, rate) {
    const val = rate && Number(String(rate.value == null ? '' : rate.value).normalize('NFKC').replace(/[,\s円]/g, ''));
    if (!rate || !(val > 0) || !isFinite(val)) return null;
    const back = emptyBackLegs(d), pairs = new Map();
    let counted = 0, noKm = 0, total = 0;
    for (const l of back) {
      let amt;
      if (rate.kind === 'km') { if (l.km == null) { noKm++; continue; } amt = Math.round(l.km * val); } else amt = Math.round(val);
      counted++; total += amt;
      const k = `${l.frm}|${l.to}`;
      if (!pairs.has(k)) pairs.set(k, { frm: l.frm, to: l.to, n: 0, subtotal: 0 });
      const p = pairs.get(k); p.n++; p.subtotal += amt;
    }
    return { kind: rate.kind, value: val, trips: back.length, counted, noKm, total, pairs: [...pairs.values()].sort((x, y) => y.n - x.n),
      unit: rate.kind === 'km' ? '円/km' : '円/便' };
  }

  /* 空車の帰りの区間（発地 → 着地）ごとの集計。求荷求車の文と地図が同じ組を使う。
     own_calc.js が区間の集計（d.pairs）を返すようになったら、その便数・曜日を優先する（無ければ d.legs から数える） */
  function returnPairs(d) {
    const m = new Map();
    for (const l of emptyBackLegs(d)) {
      if (!l.frm || !l.to || l.frm === l.to) continue;
      const k = `${l.frm}|${l.to}`;
      if (!m.has(k)) m.set(k, { frm: l.frm, to: l.to, n: 0, wd: {}, dep: {}, plates: {}, dates: [] });
      const p = m.get(k), w = wdOf(l.dep || l.date), dep = (l.dep || '').slice(11, 16), dt = (l.dep || l.date || '').slice(0, 10);
      if (dt && !p.dates.includes(dt)) p.dates.push(dt);
      p.n++; if (w != null) p.wd[w] = (p.wd[w] || 0) + 1; if (dep) p.dep[dep] = (p.dep[dep] || 0) + 1; p.plates[l.plate] = (p.plates[l.plate] || 0) + 1;
    }
    // 区間の集計（d.pairs: own_calc.js）があれば、その空車の便数と曜日別の空車の便数を優先する
    if (Array.isArray(d.pairs)) {
      for (const p of m.values()) {
        const e = d.pairs.find((x) => x && x.frm === p.frm && x.to === p.to);
        if (!e || !(e.empty > 0)) continue;
        p.n = e.empty;
        if (Array.isArray(e.by_weekday)) { const wd = {}; for (const w of e.by_weekday) if (w && w.empty > 0 && w.wd != null) wd[w.wd] = w.empty; if (Object.keys(wd).length) p.wd = wd; }
      }
    }
    for (const p of m.values()) p.dates.sort();
    return [...m.values()].sort((a, b) => b.n - a.n);
  }

  /* ------------------------------------------------------------ 求荷求車に貼る文 */

  const place = placeName;
  const tonsText = (kg) => (kg / 1000).toLocaleString('ja-JP', { maximumFractionDigits: 1 });
  const BOARD_HEAD = '【求荷】空車情報'; // 空車を出して荷を探す掲示。荷を出す側の【求車】ではない
  const BOARD_TAIL = ['運賃: ご相談ください', '連絡先: （会社名・電話番号・担当者を書き入れてください）'];

  // 1 つの区間の文（旧: 帰りの区間 1 本から作る版。試験と呼び出し側のために残す）
  function boardText(d, leg, payload) {
    if (!leg) return '';
    const a = d.places[leg.frm], b = d.places[leg.to], v = d.vehicles.find((x) => x.plate === leg.plate);
    const dep = (leg.dep || '').slice(11, 16);
    const wd = payload && payload.wd != null ? payload.wd : leg.cycle_wd;
    const day = wd == null ? '（曜日を入れてください）' : `毎週${WDN[wd]}曜`;
    const basis = payload && payload.trips ? `（${payload.month} 月は${payload.weekday}曜に出た帰り ${payload.trips} 便のうち ${payload.empty} 便が空）` : '';
    const cls = v ? `${v.cls || '大型'}${CLASS_OF[v.cls] ? `（${CLASS_OF[v.cls]}車）` : ''}` : '（車格を入れてください）';
    const kg = v && v.max_load_kg ? `最大 ${tonsText(v.max_load_kg)} t${v.max_load_assumed ? '（仮定）' : ''}まで` : '（空きの重さを入れてください）';
    return [BOARD_HEAD, `発地: ${place(a)}`, `着地: ${place(b)}`, `曜日・時刻: ${day}${dep ? ` ${dep} ごろ出発` : ''}${basis}`,
      `車格: ${cls}`, `空き: ${kg}（積む荷の大きさや形は、ご相談で決めます）`, ...BOARD_TAIL].join('\n');
  }

  // 「毎週○曜」と書くのは、その月に同じ曜日が 3 回以上あるときだけ。月に 1〜2 回の曜日は約束にならないので、日付を並べる
  const WEEKLY_MIN = 3;
  const dateWd = (iso) => `${+iso.slice(5, 7)}/${+iso.slice(8, 10)}（${WDN[wdOf(iso)]}）`;
  function dayText(p) {
    const weekly = Object.keys(p.wd).map(Number).filter((w) => p.wd[w] >= WEEKLY_MIN).sort((x, y) => p.wd[y] - p.wd[x] || x - y).slice(0, 3).sort((x, y) => x - y);
    const rest = (p.dates || []).filter((iso) => !weekly.includes(wdOf(iso)));
    const shown = rest.slice(0, 6).map(dateWd).join('・') + (rest.length > 6 ? `・ほか ${rest.length - 6} 日` : '');
    const w = weekly.length ? `毎週${weekly.map((x) => WDN[x] + '曜').join('・')}` : '';
    if (w && shown) return `${w}（ほか ${shown}）`;
    if (w) return w;
    if (shown) return shown;
    return '（曜日を入れてください）';
  }

  /* 明日の空きから 1 便ぶんの文を作る（配車担当の毎日の仕事用）。入れ物の初期値は、いちばん多い空車の帰りの区間から取る */
  function pad(n) { return String(n).padStart(2, '0'); }
  const isoOf = (dt) => `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;
  function tomorrowIso(now) { const t = new Date((now || new Date()).getTime()); t.setDate(t.getDate() + 1); return isoOf(t); }
  /* 配車表で車が動いた曜日（0 = 月 … 6 = 日）。日付ごとに区間が 1 つでもあれば動いた日とし、曜日ごとに動いた日の数を数えて、いちばん多い曜日の半分以上ある曜日を稼働日とする。
     全車が「休」の日曜のように、動いた日が無い曜日は入らない。区間が無いときは null（曜日を決められない） */
  function workingWeekdays(d) {
    const days = new Set();
    for (const l of d.legs || []) { const dt = (l.dep || l.date || '').slice(0, 10); if (/^\d{4}-\d{2}-\d{2}$/.test(dt)) days.add(dt); }
    if (!days.size) return null;
    const cnt = {};
    for (const dt of days) { const w = wdOf(dt); if (w != null) cnt[w] = (cnt[w] || 0) + 1; }
    const mx = Math.max(...Object.values(cnt));
    return new Set(Object.keys(cnt).map(Number).filter((w) => cnt[w] * 2 >= mx));
  }
  /* 明日の空きの日付の初期値: 今日の次の日から数えて、配車表で車が動いている曜日の最初の日。休みの曜日（日曜など）は飛ばす。戻り値は { iso, skipped（飛ばした日の iso）, open（稼働日の曜日の一覧）, basis（配車表から決めたか） } */
  function nextWorkday(d, now) {
    const open = workingWeekdays(d), t = new Date((now || new Date()).getTime()), skipped = [];
    t.setDate(t.getDate() + 1);
    if (!open) return { iso: isoOf(t), skipped, open: null, basis: false };
    for (let i = 0; i < 14; i++) {
      const w = (t.getDay() + 6) % 7;
      if (open.has(w)) return { iso: isoOf(t), skipped, open: [...open].sort((a, b) => a - b), basis: true };
      skipped.push(isoOf(t));
      t.setDate(t.getDate() + 1);
    }
    const first = new Date((now || new Date()).getTime()); first.setDate(first.getDate() + 1);
    return { iso: isoOf(first), skipped: [], open: [...open].sort((a, b) => a - b), basis: false };
  }
  function boardDefaults(d, now) {
    const p = returnPairs(d)[0];
    const base = { date: nextWorkday(d, now).iso, frm: '', to: '', cls: '', kg: '', time: '', timeTo: '', bed: '', size: '' };
    if (!p) return base;
    const top = (o) => Object.keys(o).sort((x, y) => o[y] - o[x] || (x < y ? -1 : 1))[0];
    const plate = top(p.plates), v = d.vehicles.find((x) => x.plate === plate);
    let kg = v && v.max_load_kg;
    if (!kg) { const m = /^(\d+(?:\.\d+)?)\s*t$/i.exec(String((v && v.cls) || '')); if (m) kg = +m[1] * 1000; }
    return { date: base.date, frm: placeName(d.places[p.frm]), to: placeName(d.places[p.to]), cls: (v && tariffClass(v.cls)) || '', kg: kg ? String(kg / 1000) : '', time: top(p.dep) || '', timeTo: '', bed: '', size: '' };
  }
  /* 読めた割合。区間の数（読めた区間 + 読めなかった行）のうち、実車か空車かが分かった区間の割合。読めない・不明が 2 割を超えるとき low を真にし、
     実車率を「不明を空車とした値（lo）〜不明を外した値（hi）」の幅で出せるようにする。lo が求まらないとき（距離が無い）は null */
  function readQuality(d) {
    const c = d.counts || {}, legs = c.legs || 0, unread = c.unreadable_rows != null ? c.unreadable_rows : (d.unreadable || []).length, unk = c.unknown || 0;
    const total = legs + unread, known = Math.max(0, legs - unk), share = total ? known / total : 1;
    const k = d.kpis || {}, pick = (o) => (o == null ? null : typeof o === 'object' ? (o.value != null ? o.value : null) : o);
    let lo = pick(k.loaded_rate_if_unknown_empty);
    if (lo == null) lo = pick(k.actual_rate_if_unknown_empty);
    const hi = k.actual_rate ? k.actual_rate.value : null;
    return { total, known, unread, unk, share, low: total > 0 && 1 - share > 0.2, lo, hi };
  }
  const CLASS_CHOICES = ['2t', '4t', '10t'];
  // 掲示に書く荷台の形（求荷求車の掲示は、車格のほかに荷台の形と寸法、積める時間の幅で探される）
  const BED_CHOICES = ['ウイング', '平ボディ', '箱（バン）', '冷凍・冷蔵', 'ユニック・クレーン付き', 'その他'];
  // f: { date: 'YYYY-MM-DD', frm, to, cls, kg（t）, time }。足りない欄は「（○○を入れてください）」と書く
  function boardOne(f) {
    const ok = /^\d{4}-\d{2}-\d{2}$/.test(f.date || '');
    const when = f.time ? (f.timeTo ? ` ${f.time}〜${f.timeTo} の間に積めます（出発）` : ` ${f.time} ごろ出発`) : '';
    const day = ok ? `${dateWd(f.date)}${when}` : '（日付を入れてください）';
    const kgv = Number(String(f.kg || '').normalize('NFKC').replace(/[^\d.]/g, ''));
    const bed = String(f.bed || '').trim(), size = String(f.size || '').trim();
    return [BOARD_HEAD, `発地: ${f.frm || '（発地を入れてください）'}`, `着地: ${f.to || '（着地を入れてください）'}`, `日時: ${day}`,
      `車格: ${f.cls || '（車格を入れてください）'}${f.cls ? '車' : ''}`, ...(bed || size ? [`荷台: ${bed || '（形を入れてください）'}${size ? `（内寸 ${size}）` : ''}`] : []), `空き: ${kgv > 0 ? `最大 ${tonsText(kgv * 1000)} t まで` : '（空きの重さを入れてください）'}（積む荷の大きさや形は、ご相談で決めます）`, ...BOARD_TAIL].join('\n');
  }

  // 空車の帰りの区間（発地 → 着地）ごとに 1 つずつ、求荷求車に貼る文を作る。[{ key, frm, to, label, n, text }]
  // 曜日・便数・車の種類・空きの重さは、その区間の便から数える。連絡先と運賃は空欄
  function boardTexts(d) {
    const out = [];
    for (const p of returnPairs(d)) {
      const a = d.places[p.frm], b = d.places[p.to];
      const day = dayText(p);
      const wdCount = Object.keys(p.wd).map(Number).sort((x, y) => x - y).map((w) => `${WDN[w]}曜 ${p.wd[w]} 便`).join('・');
      const dep = Object.keys(p.dep).sort((x, y) => p.dep[y] - p.dep[x] || (x < y ? -1 : 1))[0];
      const mon = d.mon ? `${d.mon} 月は` : '';
      const basis = `（${mon}この区間の帰りの空車 ${p.n} 便${wdCount ? `。${wdCount}` : ''}）`;
      // 車の種類ごとに、便数と最大積載量（登録があればその値、無ければ種類の呼び名の重さ）
      const g = {};
      for (const [plate, n] of Object.entries(p.plates)) {
        const v = d.vehicles.find((x) => x.plate === plate), raw = (v && v.cls) || '', tc = tariffClass(raw), key = tc || raw || '?';
        if (!g[key]) g[key] = { tc, raw, n: 0, kg: 0, assumed: false, nominal: false };
        const e = g[key]; e.n += n;
        let kg = v && v.max_load_kg, nominal = false;
        if (!kg) { const m = /^(\d+(?:\.\d+)?)\s*t$/i.exec(String(raw)); if (m) { kg = +m[1] * 1000; nominal = true; } }
        if (kg && kg >= e.kg) { e.kg = kg; e.assumed = !!(v && v.max_load_assumed); e.nominal = nominal; }
      }
      const gs = Object.values(g).sort((x, y) => y.n - x.n), multi = gs.length > 1;
      const clsLine = gs.length ? gs.map((e) => `${e.tc ? `${e.tc}車${e.raw && e.raw !== e.tc ? `（${e.raw}）` : ''}` : (e.raw && e.raw !== '不明' ? e.raw : '（車格を入れてください）')}${multi ? ` ${e.n} 便` : ''}`).join(' / ') : '（車格を入れてください）';
      const kgLine = gs.some((e) => e.kg)
        ? gs.map((e) => `${multi ? `${e.tc || e.raw}車 ` : ''}${e.kg ? `最大 ${tonsText(e.kg)} t まで${e.assumed ? '（仮定）' : e.nominal ? '（車種から推定。車検証の値に直してください）' : ''}` : '（空きの重さを入れてください）'}`).join(' / ')
        : '（空きの重さを入れてください）';
      const text = [BOARD_HEAD, `発地: ${place(a)}`, `着地: ${place(b)}`, `曜日・時刻: ${day}${dep ? ` ${dep} ごろ出発` : ''}${basis}`,
        `車格: ${clsLine}`, `空き: ${kgLine}（積む荷の大きさや形は、ご相談で決めます）`, ...BOARD_TAIL].join('\n');
      out.push({ key: `${p.frm}|${p.to}`, frm: p.frm, to: p.to, label: `${shortName(a)} → ${shortName(b)}`, n: p.n, text });
    }
    return out;
  }

  /* ------------------------------------------------------------ デジタコの停車から荷待ちの下書き */

  // 30 分以上の停車を、荷待ちの表の行にする。停車の記録に無い欄（契約の相手・荷役開始・荷主等の都合の待機か）は空欄のまま。
  // 車種は、車が配車表の車両に当たるか、車両がすべて同じ車種のときだけ入れる
  // 車の種類を荷待ちの表の車種（小型・中型・大型）にする。売上の目安と同じ区分（3 t 以下は小型、4〜7 t と増 t は中型、8 t 以上は大型）。分からないとき ''
  const WAIT_OF = { '2t': '小型', '4t': '中型', '10t': '大型' };
  function waitClass(cls) {
    const c = String(cls == null ? '' : cls).normalize('NFKC').trim();
    if (['小型', '中型', '大型', 'トレーラー'].includes(c)) return c;
    if (/^増\s*(t|トン)?$/i.test(c) || /^増t/i.test(c)) return '中型';
    return WAIT_OF[tariffClass(c)] || '';
  }
  function draftRows(d) {
    const rows = [], kinds = [...new Set(d.vehicles.map((v) => waitClass(v.cls)).filter((c) => c))];
    for (const s of d.stops.filter((x) => x.minutes >= 30)) {
      const v = d.vehicles.find((x) => x.plate === s.plate);
      const cls = (v && waitClass(v.cls)) || (kinds.length === 1 ? kinds[0] : '');
      rows.push({ date: s.start.slice(0, 10), vehicle_no: s.plate, vehicle_class: cls, partner: '', place: s.name || (d.places[s.place] || {}).label || '',
        arrive: s.start.slice(11, 16), depart: s.end.slice(11, 16), _draft: true });
    }
    return rows;
  }

  return { GLOSS, tipHtml, sig, headSig, list, save, clearAll, rolesGet, rolesSet, summary, summaries, compareRows, FARE, fare, tariffClass, revenue, revenueScope, returnPairs, boardText, workingWeekdays, nextWorkday, boardTexts, draftRows, waitClass, ownRevenue, boardDefaults, boardOne, CLASS_CHOICES, BED_CHOICES, readQuality, tomorrowIso, isHallWard, placeName, hash };
})();
if (typeof window !== 'undefined') window.OwnMonth = OwnMonth;
