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
    '実車率': '走った距離のうち、荷を積んで走った距離の割合です。',
    '積載率': '積んだ重さ ÷ その車の最大積載量です。この画面では大型の車だけで計算します。',
    '積載効率': '積載率 × 実車率です。荷を積んで走った割合と、荷台がどれだけ埋まっていたかを 1 つにした数です。',
    '空車 km': '荷を積まずに走った距離です。',
    '1口': '帰り便の空きを荷主に売る 1 つの単位です。料金は 1 口あたりで出します。',
    '基準積載率': '個建運賃の参考額を出すとき、荷台が何割埋まる前提で 1 台の運賃を割るか、という割合です。',
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
  // 同じ月・同じ署名は置き換え、ほかは残す。24 件まで
  function save(entry) {
    const es = list().filter((e) => !(e.month === entry.month && e.sig === entry.sig));
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
      empty_km: c ? c.emptyKm : round1(k.actual_rate.km_empty || 0), wait_min: c ? c.longMin : 0, wait_count: c ? c.longCount : 0,
      legs: d.counts.legs, vehicles: d.vehicles.length, company: d.company ? d.company.name : '',
    };
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

  // 帰りの向きの空車の便から、代表の km（中央値）・車格（いちばん多いもの）・便数を決める。対象が無ければ null
  function revenue(d) {
    const emptyBack = d.legs.filter((l) => l.dir === 'back' && l.kind === 'empty' && l.km != null);
    if (!emptyBack.length) return null;
    const cnt = {};
    for (const l of emptyBack) {
      const v = d.vehicles.find((x) => x.plate === l.plate), cls = v && CLASS_OF[v.cls];
      if (cls) cnt[cls] = (cnt[cls] || 0) + 1;
    }
    const cls = Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a])[0];
    const kms = emptyBack.map((l) => l.km).sort((a, b) => a - b), mid = kms.length >> 1;
    const km = round1(kms.length % 2 ? kms[mid] : (kms[mid - 1] + kms[mid]) / 2);
    const from = d.places[d.base], pref = from && from.pref;
    const res = { trips: emptyBack.length, km, cls: cls || null, fare: null, total: null };
    if (!cls) return res;
    // 帰りは自社の車庫に向かうので、運賃の地域は帰りの発地（主な行き先）の県で決める。置けないときは車庫の県
    const dock = d.places[d.dock], pr = (dock && dock.pref) || pref || '';
    res.fare = fare(km, cls, pr);
    if (res.fare) res.total = res.fare.amount * res.trips;
    return res;
  }

  /* ------------------------------------------------------------ 求荷求車に貼る文 */

  const WDN = '月火水木金土日';
  const place = (p) => (p ? `${p.pref || ''}${p.muni || p.label || ''}` : '（地点を入れてください）');

  // leg: 下書きのもとにする帰りの区間。payload: 気づきの規則 1 のデータ（曜日・便数）。連絡先と運賃は空欄
  function boardText(d, leg, payload) {
    if (!leg) return '';
    const a = d.places[leg.frm], b = d.places[leg.to], v = d.vehicles.find((x) => x.plate === leg.plate);
    const dep = (leg.dep || '').slice(11, 16);
    const wd = payload && payload.wd != null ? payload.wd : leg.cycle_wd;
    const day = wd == null ? '（曜日を入れてください）' : `毎週${WDN[wd]}曜`;
    const basis = payload && payload.trips ? `（${payload.month} 月は${payload.weekday}曜に出た帰り ${payload.trips} 便のうち ${payload.empty} 便が空）` : '';
    const cls = v ? `${v.cls || '大型'}${CLASS_OF[v.cls] ? `（${CLASS_OF[v.cls]}車）` : ''}` : '（車格を入れてください）';
    const kg = v && v.max_load_kg ? `最大 ${(v.max_load_kg / 1000).toLocaleString('ja-JP', { maximumFractionDigits: 1 })} t${v.max_load_assumed ? '（仮定）' : ''}まで` : '（空きの重さを入れてください）';
    return ['【求車】帰り便の空きがあります', `発地: ${place(a)}`, `着地: ${place(b)}`, `曜日・時刻: ${day}${dep ? ` ${dep} ごろ出発` : ''}${basis}`,
      `車格: ${cls}`, `空き: ${kg}（積む荷の大きさや形は、ご相談で決めます）`, '運賃: ご相談ください', '連絡先: （会社名・電話番号・担当者を書き入れてください）'].join('\n');
  }

  /* ------------------------------------------------------------ デジタコの停車から荷待ちの下書き */

  // 30 分以上の停車を、荷待ちの表の行にする。停車の記録に無い欄（契約の相手・荷役開始・荷主等の都合の待機か）は空欄のまま。
  // 車種は、車が配車表の車両に当たるか、車両がすべて同じ車種のときだけ入れる
  function draftRows(d) {
    const rows = [], kinds = [...new Set(d.vehicles.map((v) => v.cls).filter((c) => ['小型', '中型', '大型'].includes(c)))];
    for (const s of d.stops.filter((x) => x.minutes >= 30)) {
      const v = d.vehicles.find((x) => x.plate === s.plate);
      const cls = v && ['小型', '中型', '大型'].includes(v.cls) ? v.cls : (kinds.length === 1 ? kinds[0] : '');
      rows.push({ date: s.start.slice(0, 10), vehicle_no: s.plate, vehicle_class: cls, partner: '', place: s.name || (d.places[s.place] || {}).label || '',
        arrive: s.start.slice(11, 16), depart: s.end.slice(11, 16), _draft: true });
    }
    return rows;
  }

  return { GLOSS, tipHtml, sig, headSig, list, save, clearAll, rolesGet, rolesSet, summary, compareRows, FARE, fare, revenue, boardText, draftRows, hash };
})();
if (typeof window !== 'undefined') window.OwnMonth = OwnMonth;
