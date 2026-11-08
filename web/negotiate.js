'use strict';
/* 帰り便の提案（SPEC §44）。運送会社の配車担当が、自社の過去の空車の記録から、相談する荷主の種類と条件を絞り、人が編集する交渉文を作る。
   前半は画面に依らない純粋な関数（集計の見方・条件の判定・金額の検討・文面・社内情報の混入の検査・編集した文面の保護）。後半は自社の運行の右の欄に置く小さな画面。
   通信はしない（ネットワークや外部のアプリを呼ぶ処理を持たない）。送信・連絡先の検索・予約・発注は作らない。操作は「文面をコピー」だけ。
   復路の集計は web/own_calc.js（OwnCalc.returnPairs / returnTrips / returnTarget）。Python 側は src/shipcompare/own.py の return_*。 */

const Negotiate = (() => {
  const WD = '月火水木金土日';
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const n0 = (v) => Math.round(v).toLocaleString('ja-JP');
  const n1 = (v) => v.toLocaleString('ja-JP', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const SRC_JA = { digitaco: 'デジタコ', haisha: '配車表', file: 'ファイル', estimate: '推定', user: '手で直した値', unknown: '不明' };

  /* ------------------------------------------------------------ 相手の種類と確認する条件 */

  // 相手の種類の名前と説明は、選んだ区間（空車で走る向き: 発地 fr → 着地 to）の両端の名前から作る。決まった地名は書かない（SPEC §49）
  // 例: 空車が 名古屋 → 春日部 なら「春日部方面へ商品を出す荷主」。通い箱・パレットも 名古屋 → 春日部 の向きで返す
  const TYPES = {
    commodity: { label: '商品を出す荷主', hint: '出荷を、空いている曜日にまとめられるかを確認します。' },
    returnable: { label: '空の通い箱・パレットを返したい荷主', hint: '種類・数・返す先と、重ねて積めるかを聞きます。' },
  };
  function labelOf(k, v, a) {
    const to = a && a.pair && a.pair.to_label;
    if (k === 'commodity' && to) return `${esc(to)}方面へ商品を出す荷主`;
    return v.label;
  }
  function hintOf(k, v, a) {
    const fr = a && a.pair && a.pair.fr_label, to = a && a.pair && a.pair.to_label;
    if (!fr || !to) return v.hint;
    if (k === 'commodity') return `例として、${esc(fr)}側の倉庫から${esc(to)}周辺の店舗へ商品を送る荷主です。出荷を、空いている曜日にまとめられるかを確認します。`;
    if (k === 'returnable') return `${esc(fr)}側から${esc(to)}側へ、空の通い箱やパレットを返す荷主です。種類・数・返す先と、重ねて積めるかを聞きます。`;
    return v.hint;
  }
  const COND_STATES = { unconfirmed: '未確認', ok: '合う', ng: '合わない' };
  const HOLD_OPTS = [
    ['load_time', '積込の日時を変えられるか'], ['recv_time', '受取の日時を変えられるか'], ['range', '日程を広げられる範囲'],
    ['size', '荷姿・数量を分けられるか'], ['equip', '荷役の設備・所要時間を変えられるか'], ['other_day', '別の曜日の集荷が可能か'],
  ];
  const HOLD_ASK = {
    load_time: '積み込みの日時を変えることは可能でしょうか。', recv_time: '受け取りの日時を変えることは可能でしょうか。', range: '積み込み・受け取りの日程を広げられる範囲を教えてください。',
    size: '荷姿・数量を分けて運ぶことは可能でしょうか。', equip: '荷役の方法や所要時間を変えていただくことは可能でしょうか。', other_day: '集荷を別の曜日にしていただくことは可能でしょうか。',
  };
  // 質問。cond は、その条件が未確認のときだけ聞く（load: 積込、recv: 受取、always: いつも）
  const QUESTIONS = {
    commodity: [
      ['cargo', '荷物の品名・荷姿（箱・パレットなど）、1 個あたりの寸法・重量、数量'],
      ['load', '積み込みを希望する日時と、日程を広げられる範囲'],
      ['load', '積み込み場所の荷役設備（フォークリフト・ドックなど）と、積み込みにかかる時間'],
      ['load', 'ほかの荷物との混載の可否と、積み降ろしの順の希望'],
      ['load', '集荷先のおおよその場所（当社の通常の経路から離れる場合があるため）'],
      ['recv', '納品先（受け取り側）が受け取れる日時'],
      ['recv', '納品先の荷役設備と、荷降ろしにかかる時間'],
      ['always', '現在の輸送の方法と、その条件（比較のため）'],
    ],
    returnable: [
      ['cargo', '通い箱・パレットの種類（規格・寸法）と数量'],
      ['load', '積み重ねの可否と、重ねられる段数'],
      ['load', '回収・受け渡しの方法（積み込みのご担当と、検品の方法）'],
      ['load', '回収を希望する日時と、日程を広げられる範囲'],
      ['load', '集荷先のおおよその場所（当社の通常の経路から離れる場合があるため）'],
      ['recv', '返却先（受け取り側）が受け取れる日時'],
      ['recv', '返却先の荷役設備と、荷降ろしにかかる時間'],
      ['always', '容器の所有者と返却先'],
      ['always', '現在の返送の方法と、その条件（比較のため）'],
    ],
  };
  const INTERNAL_CHECK = ['車格・床の空き・重量の余力', '追加の集荷で増える距離と時間', '次の運行との関係（帰庫の時刻・次の便への影響）', '荷役の設備と所要時間', '積み降ろしの順・混載の可否'];

  /* ------------------------------------------------------------ 金額（任意の社内の検討） */

  // 空欄は blank、数でない文字や負の値は invalid。明示の 0 は 0 円として扱う（空欄を 0 にしない）
  function parseMoney(s) {
    const t = String(s == null ? '' : s).normalize('NFKC').replace(/[,\s円¥￥]/g, '');
    if (t === '') return { state: 'blank', v: null };
    if (/^\d+(\.\d+)?$/.test(t)) return { state: 'ok', v: Number(t) };
    return { state: 'invalid', v: null };
  }
  const SCOPE = { fuel: '寄り道分の燃料・高速代', labor: '人件費（拘束時間）', wait: '荷待ち・荷役', fee: '手数料等', other: 'そのほか' };
  const emptyEcon = () => ({ quote: '', qUnit: '', qTax: '', cost: '', cUnit: '', cTax: '', scope: { fuel: false, labor: false, wait: false, fee: false, other: false }, scopeOk: false });
  function economics(e) {
    const q = parseMoney(e.quote), c = parseMoney(e.cost);
    if (q.state === 'blank' && c.state === 'blank') return { state: 'none', missing: [], profit: null };
    const miss = [];
    if (q.state === 'blank') miss.push('希望見積額が未入力です');
    if (q.state === 'invalid') miss.push('希望見積額が数値ではありません');
    if (c.state === 'blank') miss.push('追加費用が未入力です（0 円なら 0 と入れてください）');
    if (c.state === 'invalid') miss.push('追加費用が数値ではありません');
    if (!e.qUnit || !e.cUnit) miss.push('見積と費用の単位（1 件・1 便）を選んでください');
    else if (e.qUnit !== e.cUnit) miss.push('見積と費用の単位がそろっていません');
    if (!e.qTax || !e.cTax) miss.push('見積と費用の税込・税抜を選んでください');
    else if (e.qTax !== e.cTax) miss.push('見積と費用の税込・税抜がそろっていません');
    if (!Object.values(e.scope || {}).some(Boolean) || !e.scopeOk) miss.push('追加費用に含めた範囲にチェックを入れてください');
    if (miss.length) return { state: 'incomplete', missing: miss, profit: null };
    const profit = q.v - c.v;
    return { state: profit <= 0 ? 'skip' : 'ok', missing: [], profit };
  }

  /* ------------------------------------------------------------ 状況（保留・見送り・下書き） */

  const STATUS_JA = { hold: '保留', skip: '見送り', skip_econ: '見送り（増えるもうけが 0 円以下）', ready: '条件はすべて確認済みです', draft: '未確認の項目は、相手への質問にしています' };
  // 条件が「合う」でも、確認した内容が空なら、確認できたとは書けない（相手への文では未確認として質問にする）
  const okCond = (c) => c.state === 'ok' && !!String(c.note || '').trim();
  function statusOf(p) {
    const ec = economics(p.econ || emptyEcon());
    if (ec.state === 'skip') return 'skip_econ';
    if (p.load.state === 'ng' || p.recv.state === 'ng') return p.hold && p.hold.action === 'skip' ? 'skip' : 'hold';
    return okCond(p.load) && okCond(p.recv) ? 'ready' : 'draft';
  }

  /* ------------------------------------------------------------ 文面（社外にコピーする文。社内の根拠・原価・車番などは入れない） */

  const placeMuni = (data, id) => { const p = (data.places || {})[id] || {}; return p.muni || ''; };
  const phrase = (muni) => (muni ? `${muni}周辺` : '【地名を入れてください】周辺');

  // 結びと署名（日本語の直し 2026-11-08）。案内文の最後に付ける
  const SIGN = ['', 'お手数ですが、ご検討のほどよろしくお願いいたします。', '', '【運送会社名】【担当者名】', '連絡先: 【電話・メール】'];
  function buildDraft(p) {
    const st = statusOf(p);
    if (st === 'skip' || st === 'skip_econ') return '';
    const from = phrase(p.fromMuni), to = phrase(p.toMuni), day = p.weekday ? `${p.weekday}曜日` : '【曜日】';
    const head = ['【宛先（会社名・ご担当者名）】 様', '', '突然のご連絡にて失礼いたします。【運送会社名】の【担当者名】と申します。'];
    if (st === 'hold') {
      head[2] = 'いつもお世話になっております。【運送会社名】の【担当者名】です。';
      const picks = (p.hold && p.hold.picks) || [];
      return [...head, '', `先日ご相談した、${from}から${to}へ戻る便でのお運びの件で、条件が合わない点がありました。条件を変えられるか、あらためてご相談させてください。`, '',
        '■再確認したいこと', ...(picks.length ? picks.map((k) => `・${HOLD_ASK[k]}`) : ['・【再確認する内容を選ぶか、ここに書いてください】']), '',
        '※ 条件の確認のご連絡です。お受けできるかは、確認のあとでお伝えします。', ...SIGN].join('\n');
    }
    const type = p.type === 'returnable' ? 'returnable' : 'commodity';
    // 「毎週」と書くのは、その曜日の帰りの便の 8 割以上が空車で、3 回以上あるときだけ。そうでなければ、記録にあったことだけを書く
    const lead = type === 'returnable'
      ? (p.weekly ? `毎週${day}に、${from}から${to}へ戻る便に空きがあります。` : `${day}は、${from}から${to}へ戻る便に空きが出ることがあります。`)
      : (p.weekly ? `毎週${day}に、${from}から${to}へ戻るトラックに空きがあります。` : `${day}は、${from}から${to}へ戻るトラックに空きが出ることがあります。`);
    const intro = type === 'returnable'
      ? `${lead}空の通い箱・パレットの返送にお使いいただけます。`
      : `${lead}この方面へ出荷される荷物があれば、この便でお運びできるかもしれません。`;
    const confirmed = [];
    if (okCond(p.load)) confirmed.push(`・積み込みの条件: ${p.load.note.trim()}`);
    if (okCond(p.recv)) confirmed.push(`・受け取りの条件: ${p.recv.note.trim()}`);
    // 内容を書いていない「合う」は未確認として質問に残す。荷物の内容（cargo）は、積込・受取の確認とは別に、いつも聞く
    const asks = QUESTIONS[type].filter(([c]) => c === 'always' || c === 'cargo' || (c === 'load' && !okCond(p.load)) || (c === 'recv' && !okCond(p.recv))).map(([, t]) => `・${t}`);
    return [...head, '', intro, 'お運びできるかを確かめるため、次の点をお聞かせください。', '', ...(confirmed.length ? ['■確認できている条件', ...confirmed, ''] : []),
      '■お聞かせいただきたいこと', ...asks, '',
      '■進め方', 'まずは 1 回、試しにお運びさせてください。条件を伺ったうえで、お受けできるかどうかとお見積りをお送りします。', ...SIGN].join('\n');
  }

  // 社外の文に入れてはいけない語。車番・運転者・元のファイル名と行・他の地点名・会社名・社内の数字・入れた金額
  function internalTokens(data, rows, ctx) {
    const t = new Set();
    const add = (s) => { s = String(s == null ? '' : s).trim(); if (s.length >= 2) t.add(s); };
    for (const r of rows) {
      add(r.plate); add(r.driver); add(r.leg_id);
      // 運転者名は姓だけ（先頭 2 字・空白で分けた各部分）でも比べる。記号だけの符号（D01 など）は対象にしない
      const dn = String(r.driver == null ? '' : r.driver).normalize('NFKC').trim();
      if (/[぀-ヿ一-鿿]/.test(dn)) { for (const part of dn.split(/\s+/)) add(part); add(dn.replace(/\s+/g, '').slice(0, 2)); }
      for (const x of [r.ref, r.dref]) if (x) { add(x.file); add(x.where); }
    }
    for (const f of (data.files || [])) add(f.file);
    add(data.company && data.company.name);
    const route = new Set([ctx.fr, ctx.to]);
    for (const [id, p] of Object.entries(data.places || {})) { add(p.label); if (!route.has(id)) { add(p.short); add(p.pseudo); } }
    for (const w of ['5/5', 'cycle_wd', '2,790', '2790']) add(w);
    if (ctx.kmText) { add(ctx.kmText); add(ctx.kmText.replace(/,/g, '')); }
    for (const k of ['quote', 'cost']) { const m = parseMoney((ctx.econ || {})[k]); if (m.state === 'ok' && String(m.v).length >= 3) { add(String(m.v)); add(n0(m.v)); } }
    // 文面に書く地名（相談する区間の市区町村）に含まれる語は、社外に出してよい
    const allowed = [ctx.fr, ctx.to].map((id) => ((data.places || {})[id] || {}).muni || '').filter(Boolean);
    return [...t].filter((k) => !allowed.some((a) => a.includes(k)));
  }
  // 比べる前の正規化。NFKC（全角→半角）、数字の間のカンマ、空白、「4.5万円」「四万五千円」の円への直し
  const KNUM = { 〇: 0, 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
  function kanjiToNum(s) {
    let total = 0, sec = 0, cur = 0;
    for (const c of s) {
      if (c in KNUM) cur = KNUM[c];
      else if (c === '十') { sec += (cur || 1) * 10; cur = 0; }
      else if (c === '百') { sec += (cur || 1) * 100; cur = 0; }
      else if (c === '千') { sec += (cur || 1) * 1000; cur = 0; }
      else if (c === '万') { total += (sec + cur || 1) * 10000; sec = 0; cur = 0; }
    }
    return total + sec + cur;
  }
  function normLeak(t) {
    let s = String(t == null ? '' : t).normalize('NFKC').toLowerCase().replace(/(?<=\d)[,，](?=\d)/g, '');
    s = s.replace(/[一二三四五六七八九十百千万〇]+(?=\s*円)/g, (m) => String(kanjiToNum(m)));
    s = s.replace(/(\d+(?:\.\d+)?)\s*万\s*(?:(\d+(?:\.\d+)?)\s*千)?\s*(\d{1,4})?/g, (_, a, b, c) => String(Math.round(+a * 10000 + (b ? +b * 1000 : 0) + (c ? +c : 0))));
    s = s.replace(/(\d+(?:\.\d+)?)\s*千\s*(\d{1,3})?/g, (_, a, c) => String(Math.round(+a * 1000 + (c ? +c : 0))));
    return s;
  }
  const PLATE_PAT = /[一-龥々]{2,4}\s*\d{2,3}\s*[ぁ-んァ-ヶ]\s*[・.\-\d]{1,6}/;
  const MONEY_PAT = /\d[\d.]*\s*円|万\s*円/;
  function leaks(text, tokens) {
    const out = [];
    const push = (k) => { if (!out.includes(k)) out.push(k); };
    const a = normLeak(text), b = a.replace(/\s+/g, '');
    for (const k of tokens) {
      const nk = normLeak(k), nb = nk.replace(/\s+/g, '');
      if (!nb) continue;
      let hit;
      if (/^\d+$/.test(nb)) hit = new RegExp(`(?<![0-9])${nb}(?![0-9])`).test(a);
      else hit = b.includes(nb);
      // 車番は末尾の数字だけでも比べる
      if (!hit && PLATE_PAT.test(nk)) {
        const m = nk.match(/(\d{1,4})\s*$/);
        if (m && m[1].length >= 3) hit = new RegExp(`(?<![0-9])${m[1]}(?![0-9])`).test(a);
      }
      if (hit) push(k);
    }
    // 一覧にない車番・金額も、形で止める
    const pm = b.match(/[一-龥々]{2,4}\d{2,3}[ぁ-んァ-ヶ][・.\-\d]{1,6}/);
    if (pm) push(pm[0]);
    const mm = a.match(MONEY_PAT);
    if (mm) push(mm[0].replace(/\s+/g, ''));
    return out;
  }

  /* ------------------------------------------------------------ 編集した文面の保護
     draft = { text, gen, key }。gen は最後に自動で作った文、key はそのときの入力。text !== gen なら人が編集している。 */

  const newDraft = () => ({ text: '', gen: '', key: null });
  const isEdited = (d) => d.key !== null && d.text !== d.gen;
  const isStale = (d, key) => d.key !== null && d.key !== key;
  // 条件が変わったとき。編集していなければ作り直し、編集していれば何も変えない（stale だけが立つ）
  function syncDraft(d, text, key) {
    if (d.key === null || (!isEdited(d) && d.key !== key)) { d.text = text; d.gen = text; d.key = key; return 'generated'; }
    return isEdited(d) ? (d.key !== key ? 'stale' : 'kept') : 'kept';
  }
  const regenerate = (d, text, key) => { d.text = text; d.gen = text; d.key = key; };

  /* ------------------------------------------------------------ 集計の見方 */

  // 最初に見せる曜日: 着地まで確認できた空車の便が最も多い曜日。同数なら空車の割合が高い曜日、それも同じなら便数が多い曜日（データから決める）
  function defaultWd(model, fallback) {
    const c = model.by_weekday.filter((w) => w.empty > 0);
    if (!c.length) return fallback;
    c.sort((x, y) => y.empty - x.empty || y.empty / y.trips - x.empty / x.trips || y.trips - x.trips || x.wd - y.wd);
    return c[0].wd;
  }
  function analysisOf(data, pairKey, wd) {
    const pairs = OwnCalc.returnPairs(data.legs || [], data.places, data.base, data.dock);
    const pair = pairs.find((p) => p.key === pairKey) || pairs[0] || null;
    if (!pair) return { pairs, pair: null, model: null, target: null };
    const pid = ((data.pairs || []).find((x) => x.frm === pair.fr && x.to === pair.to) || {}).id || null;
    const model = OwnCalc.returnTrips(data.legs, pair.fr, pair.to, data.base, data.dock, pid);
    const sel = wd != null && model.by_weekday.some((w) => w.wd === wd) ? wd : defaultWd(model, pair.default_wd);
    return { pairs, pair, model, target: OwnCalc.returnTarget(model, sel) };
  }
  // 積込の確認の根拠の 1 行。空車の便だけを数え、出発の時刻が無い空車の便は、その本数を書く
  function evLoadOf(a, tm) {
    if (!tm) return '';
    const er = emptyRowsOf(a.target), noTime = er.filter((r) => r.dt_basis !== 'dep').length;
    return `${a.pair.fr_label}発・${a.target.weekday}曜の空車の便 ${er.length} 本（${(tm.split(' / ').find((x) => x.startsWith('出発')) || '').replace('出発の記録: ', '出発 ')}${noTime ? `。出発の時刻の記録が無い便 ${noTime} 本` : ''}）`;
  }
  const hm = (iso) => (iso && iso.length >= 16 ? iso.slice(11, 16).replace(/^0/, '') : null);
  // 時刻の記録は、空車の便だけで数える（実車で走った便の時刻を、空いている時刻として出さない）
  const emptyRowsOf = (target) => target.rows.filter((r) => r.kind === 'empty');
  function timesNote(target) {
    const er = emptyRowsOf(target);
    const deps = [...new Set(er.filter((r) => r.dt_basis === 'dep').map((r) => hm(r.dt)))];
    const arrs = er.filter((r) => r.arr).map((r) => ({ t: hm(r.arr), wd: OwnCalc.returnWhen({ date: r.arr.slice(0, 10) })[2] })).filter((x) => x.t);
    const out = [];
    if (deps.length) out.push(`出発の記録: ${deps.sort().join('、')}`);
    if (arrs.length) { const ts = arrs.map((a) => a.t).sort(); out.push(`到着の記録: ${WD[arrs[0].wd]}曜 ${ts[0]}〜${ts[ts.length - 1]}`); }
    return out.join(' / ');
  }

  /* ------------------------------------------------------------ 画面 */

  const S = {
    entry: null, view: 'result', pairKey: null, wd: null, type: 'commodity', load: { state: 'unconfirmed', note: '' }, recv: { state: 'unconfirmed', note: '' },
    hold: { action: 'hold', picks: [] }, econ: emptyEcon(), draft: newDraft(), regenAsk: false, copyMsg: '', lastCopied: null, sig: null, sawImport: false, gsub: 'type', picking: false, goal: '', goalDone: false,
    aiAvail: false, aiAsked: false, polish: null, polishSeq: 0,
  };
  let ctx = { data: null, mode: 'sample', fileNames: [], error: '', useSample: null, pickFile: null };
  let root = null;

  const getRoot = () => {
    if (root) return root;
    root = document.createElement('section');
    root.id = 'neg-root'; root.className = 'neg'; root.setAttribute('aria-label', '空車の分析と荷主への提案');
    root.addEventListener('click', onClick);
    root.addEventListener('change', onChange);
    root.addEventListener('input', onInput);
    root.addEventListener('keydown', onKey);
    return root;
  };
  const park = () => { if (root && root.parentNode) root.remove(); };
  function mount(slot, c) {
    if (c) ctx = { ...ctx, ...c };
    if (!slot) return;
    const r = getRoot();
    if (r.parentNode !== slot) slot.appendChild(r);
    render();
  }
  function update(c) { ctx = { ...ctx, ...c }; if (root && root.isConnected) render(); }

  const A = () => (ctx.data ? analysisOf(ctx.data, S.pairKey, S.wd) : null);
  const kindWord = () => (ctx.mode === 'import' ? '自社のファイル' : '架空のサンプル');

  function params(a) {
    const t = a.target, d = ctx.data;
    const c = t.confirmed || {};
    return { weekly: c.trips >= 3 && c.empty >= 3 && c.empty / c.trips >= 0.8, type: S.type, fromMuni: placeMuni(d, a.pair.fr), toMuni: placeMuni(d, a.pair.to), weekday: t.weekday, load: S.load, recv: S.recv, hold: S.hold, econ: S.econ };
  }
  const draftKey = (a) => JSON.stringify([ctx.mode, a.pair.key, a.target.wd, S.type, S.load, S.recv, S.hold, ((S.econ && economics(S.econ).state) === 'skip')]);
  const kmText = (a) => (a.target.confirmed.empty ? n1(a.target.confirmed.empty_km) : '');

  function headerHtml(a) {
    const per = a && a.model && a.model.period ? `${a.model.period.first}〜${a.model.period.last}（帰りの便の日付）` : '帰りの便の記録がありません';
    const pr = a && a.pair ? `${esc(a.pair.fr_label)} → ${esc(a.pair.to_label)}` : '対象の区間: なし';
    const m = ctx.mode === 'import' ? ' neg-file' : '';
    return `<div class="neg-head${m}" id="neg-head"><span><small>データ</small><b>${kindWord()}</b></span><span><small>期間</small><b>${per}</b></span><span><small>対象の区間</small><b>${pr}</b></span>
      <button type="button" class="btn-sm" data-act="to-entry">別のデータを選ぶ</button></div>`;
  }

  // CSV の入口に出す、必要な列の案内（畳んだ欄の中に隠さない）
  const csvGuideHtml = () => '<p class="neg-sub neg-csvguide">最低限ほしい列は、配車表なら「日付・車番・発地か着地」、デジタコなら「開始と終了の日時」か「日付と実車・空車の距離」です。Excel は「CSV として保存」します。文字コードは UTF-8 でも Shift_JIS でも読めます。</p>';
  function entryHtml() {
    const err = ctx.error ? `<p class="neg-err" role="alert">${esc(ctx.error)}</p>` : '';
    return `<div class="neg-box"><h3>帰りの空車を、荷主に案内する文を作る</h3>
      <p class="neg-sub">帰りが空いている曜日を調べ、荷主に空きを案内する文を作ります。送信はしません。</p>
      <div class="neg-btns"><button type="button" class="btn-primary" id="neg-entry-sample" data-act="entry-sample">架空のサンプルで試す</button>
        <button type="button" class="btn-sm" id="neg-entry-file" data-act="entry-file">自社の運行記録を読み込む</button></div>${err}</div>`;
  }

  function fileWaitHtml() {
    return `<div class="neg-box"><h3>自社の運行記録を読み込む</h3>
      <p class="neg-sub">配車表・デジタコの CSV を選んでください。読み込めないときは理由をここに出します。ファイルは外に送りません。</p>
      ${ctx.error ? `<p class="neg-err" role="alert">${esc(ctx.error)}</p>` : ''}
      ${csvGuideHtml()}<div class="neg-btns"><button type="button" class="btn-primary" id="neg-entry-file" data-act="entry-file">CSV を選ぶ</button><button type="button" class="btn-sm" id="neg-entry-sample" data-act="entry-sample">架空のサンプルで試す</button>${ctx.saveSample ? '<button type="button" class="btn-sm" data-act="sample-csv">見本の CSV を保存</button>' : ''}</div></div>`;
  }

  const wdOptions = (a) => (a.target.wd == null ? '<option value="" selected>曜日を選ぶ</option>' : '') + a.model.by_weekday.map((w) => `<option value="${w.wd}" ${w.wd === a.target.wd ? 'selected' : ''}>${w.weekday}曜（空車 ${w.empty} / ${w.trips} 便）</option>`).join('');

  function evidenceHtml(a, pre) {
    const rows = a.model.rows, tg = new Set(a.target.rows.map((r) => r));
    const body = rows.map((r, i) => `<tr class="neg-trow ${tg.has(r) ? 'neg-hit' : ''}" data-i="${i}" tabindex="0" role="button" aria-expanded="false" aria-label="${esc(r.leg_id)}の運行の図を開く"><td data-l="便">${esc(r.leg_id)}</td><td class="neg-mono" data-l="ファイルの行">${esc(r.row_id)}</td><td data-l="">${esc(r.pair_id)}</td>
      <td data-l="出発日時">${r.dt ? esc(r.dt.replace('T', ' ')) : '—'}<small>${r.dt_basis === 'dep' ? '出発' : r.dt_basis === 'date' ? '記録日（時刻不明）' : '日時なし'}・${SRC_JA[r.dt_src]}</small></td>
      <td data-l="曜日">${r.wd == null ? '—' : WD[r.wd]}</td><td data-l="空車・実車">${{ empty: '空車', loaded: '実車', unknown: '不明' }[r.kind]}<small>${SRC_JA[r.kind_src_cat]}</small></td>
      <td class="num" data-l="距離 km">${r.km == null ? '不明' : n1(r.km)}<small>${SRC_JA[r.km_src_cat]}</small></td><td data-l="行き先">${r.dest_confirmed ? '確認済み' : '未確認'}</td></tr>`).join('');
    const wdRows = a.model.by_weekday.map((w) => `<tr><td>${w.weekday}</td><td class="num">${w.trips}</td><td class="num">${w.empty}</td><td class="num">${w.loaded}</td><td class="num">${w.unknown}</td><td class="num">${n1(w.empty_km)}${w.km_missing ? `（距離不明 ${w.km_missing} 便）` : ''}</td><td class="num">${w.basis.date}</td></tr>`).join('');
    return `<details class="neg-ev" id="neg-ev"><summary>どの便を数えたか見る</summary>${pre || ''}
      <ul class="neg-ul" id="neg-ev-list"><li>帰りの便: ${a.model.candidates.trips} 本</li><li>行き先が分かった便: ${a.model.confirmed.trips} 本</li><li>うち空車: ${a.model.confirmed.empty} 本</li><li>時刻なし（日付で数えた）: ${a.model.basis.date + a.model.basis.none} 本</li><li>距離が分からない便: ${a.target.confirmed.km_missing || 0} 本</li></ul>
      <div class="neg-tw scroll-x"><table class="neg-tbl"><caption>曜日別の帰りの便（行き先が分かった便）</caption><thead><tr><th>曜日</th><th>便数</th><th>空車</th><th>実車</th><th>不明</th><th>空車の km</th><th>時刻なしの便</th></tr></thead><tbody>${wdRows}</tbody></table></div>
      <div class="neg-tw neg-cardable scroll-x"><table class="neg-tbl"><caption>帰りの便 ${a.model.candidates.trips} 本（色つき: 選んだ曜日）</caption><thead><tr><th>便</th><th>ファイルの行</th><th></th><th>出発日時</th><th>曜日</th><th>空車・実車</th><th>距離 km</th><th>行き先</th></tr></thead><tbody>${body}</tbody></table></div>
      <div class="neg-tripbox" id="neg-tripbox" hidden></div></details>`;
  }

  function resultHtml(a) {
    if (!a.pair) return `<div class="neg-box"><h3>曜日ごとの帰りの空車</h3><p class="neg-warn">帰りの便の記録がありません。配車表の帰りの列と、デジタコの時刻を確かめてください。</p></div>`;
    const m = a.model, c = m.candidates, k = m.confirmed, u = m.unconfirmed, t = a.target, o = t.others;
    const noEmpty = !t.has_empty;
    const pairSel = a.pairs.length > 1 ? `<label>区間<select id="neg-pair" class="ctl sm">${a.pairs.map((p) => `<option value="${esc(p.key)}" ${p.key === a.pair.key ? 'selected' : ''}>${esc(p.label)}（候補 ${p.candidates} 便・空車 ${p.confirmed_empty} 便）</option>`).join('')}</select></label>` : '';
    const wdSel = m.by_weekday.length ? `<label>曜日<select id="neg-wd" class="ctl sm">${wdOptions(a)}</select></label>` : '';
    const bar = (e, n) => `<span class="neg-bar"><i style="width:${n ? Math.round((e / n) * 100) : 0}%"></i></span>`;
    const lines = t.wd == null
      ? ''
      : `<div class="neg-row neg-sel"><span class="neg-lab">${t.weekday}曜の帰り便</span>${bar(t.confirmed.empty, t.confirmed.trips)}<b class="neg-val">${t.confirmed.empty} / ${t.confirmed.trips} 便が空車</b></div>
         <div class="neg-row"><span class="neg-lab">ほかの曜日</span>${bar(o.empty, o.judged)}<b class="neg-val">${o.empty} / ${o.judged} 便が空車</b></div>`;
    const basisNote = k.trips ? `行き先が分かった ${k.trips} 本のうち ${m.basis.date + m.basis.none} 本は出発時刻の記録がなく、帰りの便の日付の曜日で数えています。` : '';
    const warn = noEmpty
      ? `<p class="neg-warn" id="neg-noempty" role="status">${m.confirmed.empty === 0 ? '行き先まで分かった空車の便が 1 本もないため、' : `${t.weekday ? t.weekday + '曜' : 'この曜日'}には、行き先まで分かった空車の便がないため、`}案内文は作れません。空車の記録がある曜日を選び直すか、実車か空車か分からない帰りの便があるときは、「確認」の欄で空車か実車かを決めてください（決めると数え直します）。それでも空車が出ないときは、配車表の空車の印の列を確かめてください。</p>` : '';
    const kmLine = t.confirmed.empty ? `<p class="neg-note">${t.weekday}曜の空車の走行距離は合計 ${n1(t.confirmed.empty_km)} km${t.confirmed.km_missing ? `（距離が記録にない ${t.confirmed.km_missing} 便は含みません）` : ''}。既に走り終えた距離です。帰り荷があっても走行が同じだけ減るとは限らないので、削減した距離や金額には換算していません。</p>` : '';
    return `<div class="neg-box" id="neg-result"><h3>曜日ごとの帰りの空車</h3>
      <div class="neg-pick">${pairSel}${wdSel}</div>
      <p class="neg-base" id="neg-base">帰りの便 <b>${c.trips} 本</b>のうち、行き先が分かった <b>${k.trips} 本</b>（空車 ${k.empty}・実車 ${k.loaded}）を数えています。</p>
      ${lines}<p class="neg-note">空車か実車か分かった便で割っています。</p>
      ${warn}
      <details class="neg-more" id="neg-more"><summary>数え方</summary>
        <ul class="neg-ul"><li>ほかの曜日で空車か実車か分からない便: ${o.unknown} 本</li><li id="neg-basis">時刻なし（日付で数えた）: ${m.basis.date + m.basis.none} 本</li><li>同じ行を 1 本にした: ${m.duplicates_dropped || 0} 件</li><li>${t.weekday}曜の空車の距離: ${n1(t.confirmed.empty_km)} km</li><li>過去の記録です。今後は未定</li></ul></details>
      ${evidenceHtml(a)}
      <div class="neg-btns"><button type="button" class="btn-primary" id="neg-go-cond" data-act="go-cond" ${noEmpty ? 'disabled aria-disabled="true"' : ''}>案内する荷主と条件を選ぶ</button></div></div>`;
  }

  /* ------------------------------------------------------------ 手順に沿って進める画面（guided。ctx.guided が真のとき）
     1 データを選ぶ → 2 空いた帰りを見る → 3 荷主に案内する（3 は 荷主の種類 → 条件 → 文面）。
     1 画面に出すのは、見出し・短い 1 文・中身・主ボタン 1 つ。根拠と補足は閉じた「詳しく」「集計の根拠を見る」に入れる。 */

  const guidedStep = () => (S.entry === null || (S.entry === 'file' && ctx.mode !== 'import') || !ctx.data ? 1 : S.view === 'result' ? 2 : 3);
  function stepsHtml(n) {
    const L = ['データを選ぶ', '空いた帰りを見る', '荷主に案内する'];
    return `<div class="neg-stephead"><ol class="neg-steps" id="neg-steps" aria-label="進み具合">${L.map((t, i) => `<li class="${i + 1 === n ? 'on' : i + 1 < n ? 'done' : ''}" ${i + 1 === n ? 'aria-current="step"' : ''}><b>${i + 1}</b> ${t}</li>`).join('')}</ol>
      ${ctx.toFull ? '<button type="button" class="btn-link neg-full" data-act="to-full">すべての項目を見る</button>' : ''}</div>`;
  }
  function gHeadHtml(a) {
    const pr = a && a.pair ? `${esc(a.pair.fr_label)} → ${esc(a.pair.to_label)}` : '区間がありません';
    return `<p class="neg-head neg-ctx${ctx.mode === 'import' ? ' neg-file' : ''}" id="neg-head"><span><small>データ</small><b>${kindWord()}</b></span><span><small>区間</small><b>${pr}</b></span></p>`;
  }
  function gEntryHtml() {
    return `<div class="neg-box"><h3>データを選ぶ</h3><p class="neg-sub">ファイルはこのブラウザの外には送りません。</p>${ctx.error ? `<p class="neg-err" role="alert">${esc(ctx.error)}</p>` : ''}
      <div class="neg-btns"><button type="button" class="btn-primary" id="neg-entry-sample" data-act="entry-sample">架空のサンプルで試す</button>
        <button type="button" class="btn-sm" id="neg-entry-file" data-act="entry-file">自社の運行記録を読み込む</button>
        ${ctx.home ? '<button type="button" class="btn-link" data-act="home">最初の画面に戻る</button>' : ''}</div></div>`;
  }
  function gFileWaitHtml() {
    return `<div class="neg-box"><h3>自社の運行記録を読み込む</h3><p class="neg-sub">配車表・デジタコの CSV を選びます。</p>${csvGuideHtml()}${ctx.error ? `<p class="neg-err" role="alert">${esc(ctx.error)}</p>` : ''}
      <div class="neg-btns"><button type="button" class="btn-primary" id="neg-entry-file" data-act="entry-file">CSV を選ぶ</button><button type="button" class="btn-sm" id="neg-entry-sample" data-act="entry-sample">架空のサンプルで試す</button>${ctx.saveSample ? '<button type="button" class="btn-sm" data-act="sample-csv">見本の CSV を保存</button>' : ''}
        <button type="button" class="btn-link" data-act="to-entry">戻る</button></div></div>`;
  }
  function gResultHtml(a) {
    if (!a.pair) return `<div class="neg-box"><h3>空いた帰りを見る</h3><p class="neg-warn">帰りの便の記録がありません。配車表の帰りの列と、デジタコの時刻を確かめてください。</p>
      <div class="neg-btns"><button type="button" class="btn-sm" data-act="to-entry">別のデータを選ぶ</button></div></div>`;
    const m = a.model, c = m.candidates, k = m.confirmed, u = m.unconfirmed, t = a.target, o = t.others, noEmpty = !t.has_empty;
    const pairSel = a.pairs.length > 1 ? `<label>区間<select id="neg-pair" class="ctl sm">${a.pairs.map((p) => `<option value="${esc(p.key)}" ${p.key === a.pair.key ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select></label>` : '';
    const wdSel = m.by_weekday.length ? `<label>曜日<select id="neg-wd" class="ctl sm">${wdOptions(a)}</select></label>` : '';
    const bar = (e, n) => `<span class="neg-bar"><i style="width:${n ? Math.round((e / n) * 100) : 0}%"></i></span>`;
    const lines = t.wd == null ? '' : `<div class="neg-row neg-sel"><span class="neg-lab">${t.weekday}曜</span>${bar(t.confirmed.empty, t.confirmed.trips)}<b class="neg-val">${t.confirmed.empty} / ${t.confirmed.trips} 便が空車</b></div>
      <div class="neg-row"><span class="neg-lab">ほかの曜日</span>${bar(o.empty, o.judged)}<b class="neg-val">${o.empty} / ${o.judged} 便が空車</b></div>`;
    const basisNote = k.trips ? `行き先が分かった ${k.trips} 本のうち ${m.basis.date + m.basis.none} 本は出発時刻の記録がなく、帰りの便の日付の曜日で数えています。` : '';
    const kmLine = t.confirmed.empty ? `<p class="neg-note">${t.weekday}曜の空車の走行距離は合計 ${n1(t.confirmed.empty_km)} km${t.confirmed.km_missing ? `（距離が記録にない ${t.confirmed.km_missing} 便は含みません）` : ''}。既に走り終えた距離です。帰り荷があっても走行が同じだけ減るとは限らないので、削減した距離や金額には換算していません。</p>` : '';
    const warn = noEmpty ? `<p class="neg-warn" id="neg-noempty" role="status">${m.confirmed.empty === 0 ? '行き先まで分かった空車の便が 1 本もないため、' : `${t.weekday ? t.weekday + '曜' : 'この曜日'}には、行き先まで分かった空車の便がないため、`}案内文は作れません。空車の記録がある曜日を選び直すか、実車か空車か分からない帰りの便があるときは、「確認」の欄で空車か実車かを決めてください（決めると数え直します）。それでも空車が出ないときは、配車表の空車の印の列を確かめてください。</p>` : '';
    const pre = `<p class="neg-base" id="neg-base">帰りの便 <b>${c.trips} 本</b>（空車 ${c.empty}・実車 ${c.loaded}・不明 ${c.unknown}）のうち、行き先が分かった便は <b>${k.trips} 本</b>（空車 ${k.empty}・実車 ${k.loaded}）です。残り ${u.trips} 本は行き先と積荷が分からないので、${esc(a.pair.to_label)}に着いた便には入れていません。</p>
      <p class="neg-note">ほかの曜日で空車か実車か分からない ${o.unknown} 本は別に数えています。</p><p class="neg-note" id="neg-basis">${esc(basisNote)}${m.duplicates_dropped ? `同じ内容の行 ${m.duplicates_dropped} 件は 1 便として数えました。` : ''}</p>${kmLine}
      <p class="neg-note">${ctx.mode === 'import' ? '過去の記録の集計です。今後も同じ曜日に空く保証ではなく、今予約できる便でもありません。' : '架空の過去の運行記録です。今予約できる便や、今後も毎週空く保証ではありません。'}</p>`;
    return `<div class="neg-box" id="neg-result"><h3>空いた帰りを見る</h3>
      <div class="neg-pick">${pairSel}${wdSel}</div>${lines}
      <p class="neg-lg" id="neg-lg"><i class="lg-s"></i>実線＝荷物あり、<i class="lg-d"></i>点線＝空車</p>
      <p class="neg-note" id="neg-denom">行き先まで分かった便だけ数えています（ほか ${u.trips} 本は除外）。</p>${warn}
      ${evidenceHtml(a, pre)}
      <div class="neg-btns"><button type="button" class="btn-primary" id="neg-go-cond" data-act="go-cond" ${noEmpty ? 'disabled aria-disabled="true"' : ''}>荷主に案内する文を作る</button>
        <button type="button" class="btn-sm" data-act="to-entry">別のデータを選ぶ</button></div></div>`;
  }
  function gTypeHtml(a) {
    const types = Object.entries(TYPES).map(([k, v]) => `<label class="neg-rad neg-type"><input type="radio" name="neg-type" data-type="${k}" ${S.type === k ? 'checked' : ''}><span><b>${labelOf(k, v, a)}</b><small>${hintOf(k, v, a)}</small></span></label>`).join('');
    return `<div class="neg-box" id="neg-cond"><h3>案内する荷主の種類を選ぶ</h3><p class="neg-sub">実在の会社は出てきません。相手の種類だけ選びます。</p>
      <fieldset class="neg-types"><legend>${esc(a.pair.fr_label)} → ${esc(a.pair.to_label)}・${a.target.weekday}曜</legend>${types}</fieldset>
      <div class="neg-btns"><button type="button" class="btn-primary" id="neg-g-conds" data-act="g-conds">条件を確認する</button><button type="button" class="btn-sm" data-act="to-result">空いた帰りに戻る</button></div></div>`;
  }
  function gCondCard(key, title, ev, st) {
    // 未確認・適合・不適合は 1 つの選択にする（押せる所を 8 以内にするため。SPEC §47）
    const opts = Object.entries(COND_STATES).map(([v, l]) => `<option value="${v}" ${st.state === v ? 'selected' : ''}>${l}</option>`).join('');
    return `<fieldset class="neg-cond" id="neg-cond-${key}"><legend>${title}</legend><p class="neg-ev1">${esc(ev || '記録に時刻がありません')}</p>
      <label class="neg-pickrow">確認した結果<select id="neg-sel-${key}" class="ctl sm" name="neg-${key}" data-cond="${key}" aria-label="${title}の確認した結果">${opts}</select></label>
      ${st.state === 'unconfirmed' ? '' : `<label class="neg-nl">確認した条件（案内文にそのまま入ります。例: フォークリフトで積み込み、9〜11 時）<input type="text" class="ctl" data-note="${key}" value="${esc(st.note)}" autocomplete="off"></label>`}
      ${st.state === 'ok' && !st.note.trim() ? '<p class="neg-note neg-y">確認した内容を書いてください（書くまでは、相手への文では未確認として質問にします）</p>' : ''}</fieldset>`;
  }
  function gCondHtml(a) {
    const tm = timesNote(a.target);
    const evLoad = evLoadOf(a, tm);
    const evRecv = tm && tm.includes('到着') ? tm.split(' / ').find((x) => x.startsWith('到着')) : '';
    return `<div class="neg-box" id="neg-cond"><h3>積込と受取の条件を確認する</h3><p class="neg-sub">分かっている条件を選びます。未確認は案内文で質問します。</p>
      ${gCondCard('load', `${a.target.weekday}曜の積込`, evLoad, S.load)}${gCondCard('recv', '受取（納品・返却）', evRecv, S.recv)}
      <details class="neg-int" id="neg-int"><summary>社内の確認と採算（案内文に入りません）</summary><ul class="neg-ul">${INTERNAL_CHECK.map((x) => `<li>${x}</li>`).join('')}</ul>
        <p class="neg-note"><b>採算を計算する（任意）</b></p>${econInner()}</details>
      ${statusHtml(a, 'cond')}
      <div class="neg-btns"><button type="button" class="btn-primary" id="neg-go-draft" data-act="go-draft">案内文を見る</button><button type="button" class="btn-sm" data-act="g-type">荷主の種類に戻る</button></div></div>`;
  }

  function condCard(key, title, ev, st) {
    const radios = Object.entries(COND_STATES).map(([v, l]) => `<label class="neg-rad"><input type="radio" name="neg-${key}" data-cond="${key}" value="${v}" ${st.state === v ? 'checked' : ''}>${l}</label>`).join('');
    return `<fieldset class="neg-cond" id="neg-cond-${key}"><legend>${title}</legend>
      <p class="neg-ev1"><small>自社の記録</small>${esc(ev || '記録に時刻がありません')}</p>
      <div class="neg-rads" role="radiogroup" aria-label="${title}の確認した結果"><small>確認した結果</small>${radios}</div>
      <label class="neg-nl">確認した条件（案内文にそのまま入ります。例: フォークリフトで積み込み、9〜11 時）<input type="text" class="ctl" data-note="${key}" value="${esc(st.note)}" autocomplete="off" ${st.state === 'unconfirmed' ? 'placeholder="確認したら入れます"' : ''}></label>
      ${st.state === 'ok' && !st.note.trim() ? '<p class="neg-note neg-y">確認した内容を書いてください（書くまでは、相手への文では未確認として質問にします）</p>' : ''}</fieldset>`;
  }

  function econHtml() {
    return `<details class="neg-econ" id="neg-econ" ${economics(S.econ).state !== 'none' ? 'open' : ''}><summary>採算を計算する（任意）</summary>${econInner()}</details>`;
  }
  function econInner() {
    const e = S.econ;
    const unit = (k, v) => `<select class="ctl sm" data-e="${k}" aria-label="単位"><option value="">単位</option><option value="1件" ${v === '1件' ? 'selected' : ''}>1 件</option><option value="1便" ${v === '1便' ? 'selected' : ''}>1 便</option></select>`;
    const tax = (k, v) => `<select class="ctl sm" data-e="${k}" aria-label="税"><option value="">税</option><option value="税込" ${v === '税込' ? 'selected' : ''}>税込</option><option value="税抜" ${v === '税抜' ? 'selected' : ''}>税抜</option></select>`;
    const sc = Object.entries(SCOPE).map(([k, l]) => `<label class="neg-rad"><input type="checkbox" data-scope="${k}" ${e.scope[k] ? 'checked' : ''}>${l}</label>`).join('');
    return `<div class="neg-econbody" id="neg-econbody"><div class="neg-eg"><label>出したい見積額（円）<input class="ctl sm" type="text" inputmode="decimal" data-e="quote" value="${esc(e.quote)}" autocomplete="off">${unit('qUnit', e.qUnit)}${tax('qTax', e.qTax)}</label>
        <label>追加費用（円）<input class="ctl sm" type="text" inputmode="decimal" data-e="cost" value="${esc(e.cost)}" autocomplete="off">${unit('cUnit', e.cUnit)}${tax('cTax', e.cTax)}</label></div>
      <fieldset class="neg-scope"><legend>追加費用に含めた範囲</legend>${sc}<label class="neg-rad"><input type="checkbox" data-e="scopeOk" ${e.scopeOk ? 'checked' : ''}>この範囲で費用を確認した</label></fieldset>
      <div id="neg-econ-out">${econOutHtml()}</div>
      <p class="neg-note">金額は案内文に入りません。0 円のときは 0 と入れます。</p></div>`;
  }
  function econOutHtml() {
    const r = economics(S.econ);
    if (r.state === 'none') return '<p class="neg-note">見積額と費用を入れると、差額を出します。</p>';
    if (r.state === 'incomplete') return `<p class="neg-warn">差額を出すには、次を入れてください。</p><ul class="neg-ul">${r.missing.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
    const p = n0(r.profit);
    return `<p class="${r.state === 'skip' ? 'neg-warn' : 'neg-ok'}"><b>増えるもうけ ${p} 円</b><span title="見積額−追加費用">（?）</span>${r.state === 'skip' ? '<br>0 円以下なので、見送りです。' : ''}</p>`;
  }

  function statusHtml(a, where) {
    const st = statusOf(params(a));
    if (where === 'cond' && st !== 'hold' && st !== 'skip') return ''; // 下書きの状態は文面の画面だけに出す。ボタンに見える灰色の箱を、条件の確認では出さない
    const cls = st === 'ready' ? 'ok' : st === 'draft' ? 'warn' : 'ng';
    const plain = !!ctx.guided && where === 'draft' && st !== 'hold' && st !== 'skip';
    let extra = '';
    if (st === 'hold' || st === 'skip') {
      extra = `<div class="neg-hold"><label class="neg-rad"><input type="radio" name="neg-hold" data-hold="hold" ${S.hold.action === 'hold' ? 'checked' : ''}>保留（条件を変えて再確認する）</label>
        <label class="neg-rad"><input type="radio" name="neg-hold" data-hold="skip" ${S.hold.action === 'skip' ? 'checked' : ''}>見送る（この荷主には案内しない）</label>
        ${S.hold.action === 'hold' ? `<div class="neg-picks"><small>再確認する内容を選ぶ</small>${HOLD_OPTS.map(([k, l]) => `<label class="neg-rad"><input type="checkbox" data-pick="${k}" ${S.hold.picks.includes(k) ? 'checked' : ''}>${l}</label>`).join('')}</div>` : ''}
        <p class="neg-note">合わない条件のままでは、案内文は作りません。保留を選ぶと、再確認の文を作ります。</p></div>`;
    }
    return `<div class="neg-status ${cls}${plain ? ' plain' : ''}" id="neg-status" role="status"><b>${STATUS_JA[st]}</b>${extra}</div>`;
  }

  function condHtml(a) {
    const tm = timesNote(a.target);
    const evLoad = evLoadOf(a, tm);
    const evRecv = tm && tm.includes('到着') ? tm.split(' / ').find((x) => x.startsWith('到着')) : '';
    const types = Object.entries(TYPES).map(([k, v]) => `<label class="neg-rad neg-type"><input type="radio" name="neg-type" data-type="${k}" ${S.type === k ? 'checked' : ''}><span><b>${labelOf(k, v, a)}</b><small>${hintOf(k, v, a)}</small></span></label>`).join('');
    return `<div class="neg-box" id="neg-cond"><h3>案内する荷主と条件を選ぶ</h3>
      <p class="neg-sub">案内する区間: ${esc(a.pair.fr_label)} → ${esc(a.pair.to_label)}・${a.target.weekday}曜。案内する荷主の種類を選びます（実在の会社は出ません）。</p>
      <fieldset class="neg-types"><legend>案内する荷主の種類</legend>${types}</fieldset>
      ${condCard('load', `${a.target.weekday}曜の積込の条件`, evLoad, S.load)}${condCard('recv', '受取（納品・返却）の条件', evRecv, S.recv)}
      <p class="neg-note">未確認の項目は、案内文で質問にします。</p>
      <details class="neg-int"><summary>社内で確かめること（文面には入れません）</summary><ul class="neg-ul">${INTERNAL_CHECK.map((x) => `<li>${x}</li>`).join('')}</ul></details>
      ${econHtml()}${statusHtml(a)}
      <div class="neg-btns"><button type="button" class="btn-primary" id="neg-go-draft" data-act="go-draft">案内文を見る</button><button type="button" class="btn-sm" data-act="to-result">空車の便に戻る</button></div></div>`;
  }

  function internalHtml(a) {
    const t = a.target, k = t.confirmed;
    return `<details class="neg-int" id="neg-internal"><summary>社内用のメモ（コピーされません）</summary><ul class="neg-ul">
      <li>${esc(a.pair.fr_label)} → ${esc(a.pair.to_label)}・${t.weekday}曜: 行き先が分かった帰りの便 ${k.trips} 本中 空車 ${k.empty} 本（空車の走行 ${n1(k.empty_km)} km）</li>
      <li>ほかの曜日: 空車・実車が判定できた ${t.others.judged} 便中 空車 ${t.others.empty} 便（不明 ${t.others.unknown} 便は別）</li>
      <li>${esc(timesNote(t) || '時刻の記録なし')}</li><li>これは過去の記録です。今後の空きや受注の保証ではありません</li></ul></details>`;
  }

  function staleHtml(a) {
    const stale = isStale(S.draft, draftKey(a));
    if (!stale && !S.regenAsk) return '';
    const ask = S.regenAsk
      ? `<div class="neg-ask" role="alertdialog" aria-label="作り直しの確認"><p>手で直した文面を、現在の条件で作った文に置き換えます。直した内容は消えます。</p>
          <button type="button" class="btn-primary" data-act="regen-yes" id="neg-regen-yes">作り直す</button><button type="button" class="btn-sm" data-act="regen-no" id="neg-regen-no">やめる</button></div>` : '';
    return `${stale ? '<p class="neg-stale" id="neg-stale" role="status">条件を変えました。手で直した文はそのままです。</p>' : ''}${ask}`;
  }

  function draftHtml(a) {
    const p = params(a), st = statusOf(p), key = draftKey(a);
    syncDraft(S.draft, buildDraft(p), key);
    const none = st === 'skip' || st === 'skip_econ';
    const toks = internalTokens(ctx.data, a.model.rows, { fr: a.pair.fr, to: a.pair.to, kmText: kmText(a), econ: S.econ });
    const lk = leaks(S.draft.text, toks);
    const can = !none && S.draft.text.trim() && !(st === 'hold' && !S.hold.picks.length);
    const g = !!ctx.guided;
    return `<div class="neg-box" id="neg-draft"><h3>${g ? '案内文を直してコピーする' : '案内文を直してコピーする'}</h3>
      ${statusHtml(a, 'draft')}${internalHtml(a)}
      ${none ? `<p class="neg-warn" id="neg-none">${STATUS_JA[st]}のため、文面は作りません。条件に戻って見直してください。</p>` : `
      <label class="neg-tl">案内文（【 】の宛先・社名・担当者名を書き換えてください）
        <textarea id="neg-text" class="o-ta" rows="${g ? 11 : 16}" spellcheck="false">${esc(S.draft.text)}</textarea></label>`}
      <div id="neg-live">${staleHtml(a)}<p class="neg-warn" id="neg-leak" ${lk.length ? '' : 'hidden'}>${lk.length ? `社内の情報（${esc(lk.join('、'))}）が文面に入っています。消してからコピーしてください。` : ''}</p></div>
      ${none ? '' : `<div id="neg-ai" class="neg-ai">${aiHtml()}</div>`}
      <div class="neg-btns"><button type="button" class="btn-primary" id="neg-copy" data-act="copy" ${can && !lk.length ? '' : 'disabled aria-disabled="true"'}>文面をコピー</button>
        <button type="button" class="btn-sm" id="neg-regen" data-act="regen">今の条件で作り直す</button><button type="button" class="btn-sm" data-act="to-cond">条件に戻る</button></div>
      <p class="neg-note" id="neg-copymsg" role="status">${esc(S.copyMsg)}</p>
      <p class="neg-note">${g ? '送信や予約はしません。コピーするだけです。' : '送信や予約はしません。コピーするだけです。'}</p></div>`;
  }

  /* ------------------------------------------------------------ AI で読みやすく整える（SPEC §53.2）
     サーバーで開き、AiExplain.status が available と答えたときだけ出る（ファイルで開いた版・単独の版には AiExplain が無く、何も出ない）。
     送るのは、確認の枠に出した案内文の本文だけ。「送る」を押すまで何も送らない。結果は案として出し、「この案に置き換える」を押すまで文面は変わらない */
  const POLISH_NOTE = '送るのはこの文だけです（社内の情報が入っていないことを確かめてあります）。送った内容は保存しません。費用の目安は 1 回 $0.002 以下です。';
  function aiProbe() {
    if (S.aiAsked || typeof AiExplain === 'undefined' || !AiExplain.status) return;
    S.aiAsked = true;
    AiExplain.status().then((st) => { if (st && st.available) { S.aiAvail = true; paintAi(); } }).catch(() => {});
  }
  function paintAi() { const el = root && root.querySelector('#neg-ai'); if (el) el.innerHTML = aiHtml(); }
  function aiHtml() {
    aiProbe();
    if (!S.aiAvail || typeof AiExplain === 'undefined' || !AiExplain.offerPolish) return '';
    const P = S.polish;
    if (!P) return '<div class="neg-aibtns"><button type="button" class="btn-sm" id="neg-ai-ask" data-act="ai-ask">AI で読みやすく整える</button></div>';
    if (P.phase === 'confirm') {
      return `<div class="neg-aibox" id="neg-ai-confirm"><b>この文を Anthropic の API に送ります</b>
        <pre class="neg-aitext" id="neg-ai-sent">${esc(P.text)}</pre><p class="neg-note">${POLISH_NOTE}</p>
        <div class="neg-aibtns"><button type="button" class="btn-primary" id="neg-ai-send" data-act="ai-send">送る</button><button type="button" class="btn-sm" id="neg-ai-cancel" data-act="ai-close">やめる</button></div></div>`;
    }
    if (P.phase === 'busy') return '<div class="neg-aibox" id="neg-ai-busy" role="status"><p>Haiku 5.5 が文を整えています…</p></div>';
    if (P.phase === 'error') return `<div class="neg-aibox"><p class="neg-warn" id="neg-ai-msg">${esc(P.msg)}</p><div class="neg-aibtns"><button type="button" class="btn-sm" data-act="ai-close">閉じる</button></div></div>`;
    const r = P.res, ok = r.ready;
    return `<div class="neg-aibox" id="neg-ai-result"><b>${ok ? 'AI の案' : '確認が必要な案'}</b>
      ${ok ? '' : `<p class="neg-warn" id="neg-ai-problems">元の文と食い違う点があります。内容を確かめてから使ってください。</p><ul class="neg-ailist">${(r.problems || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`}
      <pre class="neg-aitext" id="neg-ai-proposal">${esc(r.text)}</pre>
      ${(r.removed || []).length ? `<div class="neg-aigone" id="neg-ai-removed"><b>AI の案で消えた項目（${r.removed.length} 件）</b><ul class="neg-ailist">${r.removed.map((x) => `<li>${esc(x)}</li>`).join('')}</ul><p class="neg-note">必要な項目は、置き換えたあとで文に書き足してください。</p></div>` : ''}
      <p class="neg-note">元の文は、置き換えるまで変わりません。</p>
      <div class="neg-aibtns"><button type="button" class="btn-primary" id="neg-ai-replace" data-act="ai-replace">この案に置き換える</button><button type="button" class="btn-sm" id="neg-ai-close" data-act="ai-close">閉じる</button></div></div>`;
  }
  function aiAsk() {
    const ta = root.querySelector('#neg-text'), a = A();
    if (!ta || !a) return;
    S.draft.text = ta.value;
    if (!S.draft.text.trim()) return;
    const toks = internalTokens(ctx.data, a.model.rows, { fr: a.pair.fr, to: a.pair.to, kmText: kmText(a), econ: S.econ });
    const lk = leaks(S.draft.text, toks);
    if (lk.length) { S.polish = { phase: 'error', msg: `社内の情報（${lk.join('、')}）が文面に入っているため、AI には送っていません。消してからもう一度押してください。` }; paintAi(); return; }
    S.polish = { phase: 'confirm', text: S.draft.text };   // 確認の枠に出した本文を、そのまま送る
    paintAi();
  }
  function aiSend() {
    const P = S.polish;
    if (!P || P.phase !== 'confirm') return;
    const seq = ++S.polishSeq, text = P.text;
    S.polish = { phase: 'busy', text };
    paintAi();
    AiExplain.offerPolish(text).then((res) => {
      if (seq !== S.polishSeq || !S.polish || S.polish.phase !== 'busy') return;
      S.polish = { phase: 'result', text, res }; paintAi();
    }).catch((e) => {
      if (seq !== S.polishSeq || !S.polish || S.polish.phase !== 'busy') return;
      S.polish = { phase: 'error', msg: `${(e && e.message ? String(e.message) : '整えられませんでした').replace(/。?今の文のままお使いください。?$/, '').replace(/。$/, '')}。今の文のままお使いください。` }; paintAi();
    });
  }
  function aiReplace() {
    const P = S.polish;
    if (!P || P.phase !== 'result' || !P.res) return;
    S.draft.text = P.res.text;   // gen は変えない: 手で直した文と同じ扱いになり、条件を変えても保護される
    S.copyMsg = 'AI の案に置き換えました。内容を確かめてからコピーしてください';
    S.polish = null; S.polishSeq++;
    render();
  }
  const aiClose = () => { S.polish = null; S.polishSeq++; paintAi(); };

  function guidedHtml() {
    const n = guidedStep();
    if (n === 1) return stepsHtml(1) + (S.entry === 'file' && ctx.mode !== 'import' ? gFileWaitHtml() : gEntryHtml());
    const a = A();
    if (a && a.pair && S.pairKey == null) S.pairKey = a.pair.key;
    if (a && a.target && S.wd == null) S.wd = a.target.wd;
    if (!a || !a.pair || !a.target || (!a.target.has_empty && S.view !== 'result')) S.view = 'result';
    // 「荷主に相談する文を作りたい」から来たときは、データを選んだあと、荷主の種類から始める（一度だけ）
    if (S.goal === 'propose' && !S.goalDone && a && a.pair && a.target && a.target.has_empty) { S.goalDone = true; S.view = 'cond'; S.gsub = 'type'; }
    const st = guidedStep();
    const body = st === 2 ? gResultHtml(a) : S.view === 'draft' ? draftHtml(a) : S.gsub === 'cond' ? gCondHtml(a) : gTypeHtml(a);
    return stepsHtml(st) + gHeadHtml(a) + body;
  }

  /* 1 便ごとの根拠の行を押すと、その行の下に運行の図（web/trip_view.js）を出す。開くのは 1 つだけ。描き直し・別の行・閉じるときは destroy する */
  let tripRow = null;
  function closeTrip() {
    const box = root && root.querySelector('#neg-tripbox');
    if (box && !box.hidden) { const t = box.querySelector('.neg-trip'); if (t && typeof TripView === 'object') TripView.destroy(t); box.innerHTML = ''; box.hidden = true; }
    if (!tripRow) return;
    const tr = tripRow; tripRow = null;
    tr.setAttribute('aria-expanded', 'false'); tr.classList.remove('neg-open');
  }
  function toggleTrip(tr) {
    const was = tripRow === tr;
    closeTrip();
    if (was || typeof TripView !== 'object') return;
    const a = A();
    const row = a && a.model && a.model.rows[+tr.dataset.i];
    if (!row) return;
    const d = ctx.data, tc = (ctx.trip && ctx.trip()) || { places: d.places, vehicles: d.vehicles, route: d.route, base: d.base, dock: d.dock };
    // 運行の図は、表の行の中ではなく、表の下の全幅の箱に出す（狭い画面でも、広い画面でも、はみ出さない）
    const host = root.querySelector('#neg-tripbox');
    if (!host) return;
    host.hidden = false;
    host.innerHTML = `<div class="neg-triphead"><b>運行の図　便 ${esc(row.leg_id)}</b><button type="button" class="btn-sm" data-act="trip-close">図を閉じる</button></div><div class="neg-trip"></div>`;
    // 行は選んだ区間の便なので、発地・着地を足して道路の形が出るようにする
    try { TripView.render(host.querySelector('.neg-trip'), Object.assign({}, row, { frm: a.pair.fr, to: a.pair.to }), tc); } catch (e) { host.querySelector('.neg-trip').textContent = '運行の図を描けませんでした'; }
    tr.setAttribute('aria-expanded', 'true'); tr.classList.add('neg-open'); tripRow = tr;
    try { host.scrollIntoView({ block: 'nearest' }); } catch (e) { /* 使えないとき */ }
  }

  // 手順を進めたら、欄を先頭までもどす（スクロールする親があればその先頭へ。親がページなら、欄の先頭が見える位置へ）。動きは付けない
  function toTop(r) {
    let e = r, done = false;
    while (e && e !== document.body && e !== document.documentElement) {
      try { if (e.scrollHeight > e.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.scrollTop > 0) { e.scrollTop = 0; done = true; } } catch (x) { /* 使えないとき */ }
      e = e.parentElement;
    }
    if (!done) { const t = r.getBoundingClientRect().top; if (t < 0) window.scrollBy(0, t - 8); }
  }
  // 下に留まる主ボタンの帯の実際の高さを --neg-btns-h に入れる。描き直したときと、帯の高さが変わったとき（折り返し、文字の大きさ、画面の向き）に更新する
  let btnsRO = null;
  function watchBtns(r) {
    const b = r.querySelector('.neg-btns');
    const set = () => r.style.setProperty('--neg-btns-h', (b ? Math.ceil(b.getBoundingClientRect().height) : 0) + 'px');
    set();
    if (btnsRO) btnsRO.disconnect();
    if (b && typeof ResizeObserver !== 'undefined') { btnsRO = new ResizeObserver(set); btnsRO.observe(b); }
  }

  function render() {
    closeTrip();
    const r = getRoot();
    if (S.view !== 'draft' && S.polish) { S.polish = null; S.polishSeq++; }
    if (ctx.mode === 'import' && !(ctx.guided && S.picking)) { S.entry = 'file'; S.sawImport = true; S.picking = false; } else if (S.sawImport && ctx.mode !== 'import') { S.sawImport = false; if (S.entry === 'file') S.entry = 'sample'; }
    const sig = `${ctx.mode}|${(ctx.data && ctx.data.month) || ''}|${((ctx.data && ctx.data.legs) || []).length}`;
    if (S.sig !== sig) { if (S.sig !== null) { S.pairKey = null; S.wd = null; } S.sig = sig; }
    const act = document.activeElement, keep = act && r.contains(act) && act.id ? { id: act.id, s: act.selectionStart, e: act.selectionEnd } : null;
    let html;
    if (ctx.guided) html = guidedHtml();
    else if (S.entry === null) html = !ctx.guided && ctx.data && ctx.mode !== 'import' && !S.picking
      ? `<details class="neg-entry-d"><summary>荷主に案内する文を作る</summary>${entryHtml()}</details>`   // サンプルがもう読み込まれている全部を見る画面では、入口を閉じた欄に入れる（主ボタンを出さない）
      : entryHtml();
    else if (S.entry === 'file' && ctx.mode !== 'import') html = fileWaitHtml();
    else if (!ctx.data) html = '';
    else {
      const a = A();
      if (a && a.pair && S.pairKey == null) S.pairKey = a.pair.key;
      if (a && a.target && S.wd == null) S.wd = a.target.wd;
      if (!a || !a.pair || !a.target || (!a.target.has_empty && S.view !== 'result')) S.view = 'result';
      html = `${headerHtml(a)}` + (S.view === 'result' ? resultHtml(a) : S.view === 'cond' ? condHtml(a) : draftHtml(a));
    }
    r.classList.toggle('neg-guided', !!ctx.guided);
    const sk = ctx.guided ? [guidedStep(), S.view, S.gsub, S.entry].join('|') : '';
    r.classList.toggle('neg-enter', !!sk && sk !== S.lastStep);
    S.lastStep = sk;
    r.innerHTML = html;
    if (ctx.guided && sk && sk !== S.focusedStep) { // 手順が変わったら、フォーカスを新しい手順の見出しへ移す（キーボードで選んだあと、地図の部品を通らずに続きから操作できる）
      const had = S.focusedStep != null;
      S.focusedStep = sk;
      const h = r.querySelector('.neg-box h3');
      if (had && h && !(keep && r.querySelector('#' + keep.id))) { h.setAttribute('tabindex', '-1'); try { h.focus({ preventScroll: true }); } catch (e) { /* 使えないとき */ } toTop(r); }
    }
    // 下に留まる主ボタンが内容を覆わないよう、ボタンの高さぶんを下に空ける（狭い画面。own.css の .neg-guided の padding-bottom）
    if (ctx.guided) watchBtns(r);
    if (ctx.onStep) ctx.onStep(ctx.guided ? guidedStep() : 0);
    if (ctx.onFocus) {
      const a2 = ctx.guided && guidedStep() >= 2 ? A() : null;
      ctx.onFocus(a2 && a2.pair && a2.target ? { pair: a2.pair, wd: a2.target.wd, rows: a2.model.rows, ids: a2.target.rows.map((x) => x.leg_id) } : null);
    }
    if (keep) { const el = r.querySelector('#' + keep.id); if (el) { el.focus(); try { if (keep.s != null) el.setSelectionRange(keep.s, keep.e); } catch (e) { /* 種類による */ } } }
  }

  // 入力のたびに全体を描き直さない（入力欄の位置と文字を保つ）。変わる部分だけを描き直す
  function live() {
    const a = A();
    if (!root || !a || !a.pair) return;
    const eo = root.querySelector('#neg-econ-out'), stt = root.querySelector('#neg-status');
    if (eo) eo.innerHTML = econOutHtml();
    if (stt) { const fresh = document.createElement('div'); fresh.innerHTML = statusHtml(a); stt.replaceWith(fresh.firstElementChild); }
    const lv = root.querySelector('#neg-live');
    if (lv && S.view === 'draft') {
      const toks = internalTokens(ctx.data, a.model.rows, { fr: a.pair.fr, to: a.pair.to, kmText: kmText(a), econ: S.econ });
      const lk = leaks(S.draft.text, toks);
      lv.innerHTML = `${staleHtml(a)}<p class="neg-warn" id="neg-leak" ${lk.length ? '' : 'hidden'}>${lk.length ? `社内の情報（${esc(lk.join('、'))}）が文面に入っています。消してからコピーしてください。` : ''}</p>`;
      const cp = root.querySelector('#neg-copy');
      if (cp) { const dis = lk.length > 0 || !S.draft.text.trim(); cp.disabled = dis; cp.setAttribute('aria-disabled', String(dis)); }
    }
  }

  /* ------------------------------------------------------------ 操作 */

  async function copyNow() {
    const ta = root.querySelector('#neg-text');
    const a = A();
    if (!ta || !a) return;
    S.draft.text = ta.value;
    const toks = internalTokens(ctx.data, a.model.rows, { fr: a.pair.fr, to: a.pair.to, kmText: kmText(a), econ: S.econ });
    const lk = leaks(S.draft.text, toks);
    // 【宛先】【運送会社名】【担当者名】などの書き換え忘れが残っていれば、コピーの前に知らせる。もう一度押すとそのままコピーする
    const ph = [...new Set(S.draft.text.match(/【[^】]*】/g) || [])];
    if (!lk.length && ph.length && S.phAsked !== S.draft.text) { S.phAsked = S.draft.text; S.copyMsg = `${ph.join('')}がまだ書き換わっていません。直してからコピーしてください（このままコピーするときは、もう一度押してください）`; render(); return; }
    if (lk.length) { S.copyMsg = `社内の情報（${lk.join('、')}）が入っているので、コピーしていません`; render(); return; }
    const text = S.draft.text;
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (e) { /* 予備へ */ }
    if (!ok) { try { ta.focus(); ta.select(); ok = document.execCommand('copy') === true; } catch (e) { ok = false; } }
    if (ok) { S.lastCopied = text; S.copyMsg = 'コピーしました'; }
    else {
      const t2 = root.querySelector('#neg-text');
      if (t2) { t2.focus(); t2.select(); }
      S.copyMsg = '自動でコピーできませんでした。文面を選択しました。Ctrl+C（Mac は ⌘+C）でコピーしてください';
      const m = root.querySelector('#neg-copymsg'); if (m) m.textContent = S.copyMsg;
      return;
    }
    const m = root.querySelector('#neg-copymsg'); if (m) m.textContent = S.copyMsg;
  }

  function onClick(e) {
    const tr = e.target.closest('tr.neg-trow');
    if (tr && root.contains(tr)) { toggleTrip(tr); return; }
    const b = e.target.closest('[data-act]');
    if (!b || b.disabled) return;
    const act = b.dataset.act;
    if (act === 'sample-csv') { if (ctx.saveSample) ctx.saveSample(); return; }
    if (act === 'entry-sample') { S.picking = false; S.entry = 'sample'; S.view = 'result'; if ((ctx.mode === 'import' || !ctx.data) && ctx.useSample) ctx.useSample(); else render(); }
    else if (act === 'entry-file') { if (!(ctx.guided && S.picking)) S.entry = 'file'; render(); if (ctx.pickFile) ctx.pickFile(); }
    else if (act === 'to-entry') { S.entry = null; S.view = 'result'; S.regenAsk = false; S.picking = true; S.wd = null; S.goalDone = false; render(); }
    else if (act === 'home') { if (ctx.home) ctx.home(); }
    else if (act === 'to-full') { if (ctx.toFull) ctx.toFull(); }
    else if (act === 'g-conds') { S.gsub = 'cond'; render(); }
    else if (act === 'g-type') { S.gsub = 'type'; render(); }
    else if (act === 'go-cond') { S.view = 'cond'; S.gsub = 'type'; render(); }
    else if (act === 'go-draft') { S.view = 'draft'; S.copyMsg = ''; render(); }
    else if (act === 'to-cond') { const ta = root.querySelector('#neg-text'); if (ta) S.draft.text = ta.value; S.view = 'cond'; S.gsub = 'cond'; S.regenAsk = false; render(); }
    else if (act === 'to-result') { S.view = 'result'; render(); }
    else if (act === 'regen') {
      const a = A(), ta = root.querySelector('#neg-text');
      if (ta) S.draft.text = ta.value;
      if (isEdited(S.draft)) { S.regenAsk = true; render(); }
      else if (a) { regenerate(S.draft, buildDraft(params(a)), draftKey(a)); S.copyMsg = '現在の条件で文面を作り直しました'; S.polish = null; S.polishSeq++; render(); }
    }
    else if (act === 'regen-yes') { const a = A(); if (a) { regenerate(S.draft, buildDraft(params(a)), draftKey(a)); S.copyMsg = '現在の条件で文面を作り直しました'; S.polish = null; S.polishSeq++; } S.regenAsk = false; render(); }
    else if (act === 'regen-no') { S.regenAsk = false; render(); }
    else if (act === 'trip-close') closeTrip();
    else if (act === 'copy') copyNow();
    else if (act === 'ai-ask') aiAsk();
    else if (act === 'ai-send') aiSend();
    else if (act === 'ai-close') aiClose();
    else if (act === 'ai-replace') aiReplace();
  }

  function onChange(e) {
    const t = e.target;
    if (t.id === 'neg-pair') { S.pairKey = t.value; S.wd = null; render(); return; }
    if (t.id === 'neg-wd') { S.wd = t.value === '' ? null : +t.value; render(); return; }
    if (t.dataset.type) { S.type = t.dataset.type; live(); }
    else if (t.dataset.cond) { S[t.dataset.cond].state = t.value; render(); }
    else if (t.dataset.hold) { S.hold.action = t.dataset.hold; render(); }
    else if (t.dataset.pick) { const s = new Set(S.hold.picks); if (t.checked) s.add(t.dataset.pick); else s.delete(t.dataset.pick); S.hold.picks = HOLD_OPTS.map((x) => x[0]).filter((k) => s.has(k)); live(); }
    else if (t.dataset.scope) { S.econ.scope[t.dataset.scope] = t.checked; live(); }
    else if (t.dataset.e) { S.econ[t.dataset.e] = t.type === 'checkbox' ? t.checked : t.value; live(); }
  }

  function onKey(e) {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('tr.neg-trow')) { e.preventDefault(); toggleTrip(e.target); }
  }
  function onInput(e) {
    const t = e.target;
    if (t.id === 'neg-text') { S.draft.text = t.value; S.copyMsg = ''; live(); }
    else if (t.dataset.note) S[t.dataset.note].note = t.value;
    else if (t.dataset.e && t.type === 'text') { S.econ[t.dataset.e] = t.value; live(); }
  }

  const picked = (mode) => { S.picking = false; if (mode === 'import') S.entry = 'file'; };
  const setGoal = (g) => { const v = g === 'propose' ? 'propose' : g === 'empty' ? 'empty' : ''; if (S.goal !== v) { S.goal = v; S.goalDone = false; } };
  return { mount, update, park, render, picked, setGoal, step: () => (ctx.guided ? guidedStep() : 0), state: S, ctx: () => ctx,
    __timesNote: (t) => timesNote(t), __evLoad: (a) => evLoadOf(a, timesNote(a.target)), parseMoney, economics, statusOf, buildDraft, internalTokens, leaks, newDraft, isEdited, isStale, syncDraft, regenerate, analysisOf, defaultWd, emptyEcon, draftKey, params,
    TYPES, labelOf, hintOf, QUESTIONS, HOLD_OPTS, STATUS_JA };
})();
