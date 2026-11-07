'use strict';
/* 荷物の編集と見積の作業パネル（SPEC §15.2）。
   運賃・順位・内訳・版の規則はすべて /api/*（Python の src/shipcompare）が返す。ここでは入力と表示の取り回しだけを行う。 */

const W = {
  hooks: {}, scenarios: [], C: null, draft: null, scenarioSel: '', cargoOpen: false, detailOpen: false, touched: false, defaultApplied: false, simple: { kind: 'box', size: null },
  open: false, everOpened: false, step: 1, qform: null, request: null, requestKey: '', cmp: null,
  issuesText: '', saved: null, token: 0, place: null, // place: 着地のパネルで選んだ発地・着地の名前と道路距離（依頼文の区間に使う。SPEC §31.5 #16）
};
const $w = (s) => document.querySelector(s);
const wesc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const wyen = (v) => Math.round(v).toLocaleString('ja-JP');
const wclone = (o) => JSON.parse(JSON.stringify(o));
const isoLocal = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

const COSTS = [['pickup', '集荷'], ['trunk', '幹線'], ['delivery', '配達'], ['handling', '荷役'], ['fuel', '燃料'], ['highway', '高速'], ['other', 'その他']];
const COST_COLOR = { pickup: '#5cc8ff', trunk: '#3b82d6', delivery: '#55d6a6', handling: '#f0b64e', fuel: '#a78bfa', highway: '#8fa3b8', other: '#d9c7a0' };
const PARTY = [['法人', '法人'], ['個人事業主', '個人事業主'], ['個人', '個人']];
const STATE_LABEL = { complete: '内訳がそろった', incomplete: '不明の項目あり', history: '条件を変える前' };
const STATE_CHIP = { complete: 'ok', incomplete: 'check', history: 'no' };

// 公開版のサーバーが来訪者ごとの架空の状態（/api/b/*）を分けるための ID。タブごとに 1 つ、sessionStorage に持つ。
function demoSessionId() {
  let id = null;
  try { id = sessionStorage.getItem('demoSession'); } catch (e) { /* 使えない環境 */ }
  if (!id) {
    id = (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
      : 'd' + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
    try { sessionStorage.setItem('demoSession', id); } catch (e) { /* 保存できなくても、このページの間は同じ ID を使う */ }
  }
  return id;
}
const DEMO_SESSION = demoSessionId();

async function wapi(path, body) {
  const opt = body === undefined ? { headers: { 'X-Demo-Session': DEMO_SESSION } }
    : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Demo-Session': DEMO_SESSION }, body: JSON.stringify(body) };
  let r;
  try { r = await fetch(path, opt); } catch (e) { throw new Error('サーバーに接続できません。tools/serve_web.py を起動してください'); }
  let j = null;
  try { j = await r.json(); } catch (e) { /* 本文なし */ }
  if (!r.ok) throw new Error((j && j.error) || `通信に失敗しました（${r.status}）`);
  return j;
}

/* ------------------------------------------------------------ 状態 */

const emptyCase = () => ({ schema_version: 2, versions: [], quotes: [], chosen_quote_id: null, reason: '', open_issues: [] });
const curVer = () => (W.C && W.C.versions.length ? W.C.versions[W.C.versions.length - 1] : null);
const confirmedShipment = () => (curVer() ? curVer().shipment : null);
function caseForApi() {
  const { estimates, notice, ...rest } = W.C; // 記録用の欄は送り返さない
  return rest;
}
const blankShipment = () => ({
  items: [{ count: null, length_cm: null, width_cm: null, height_cm: null, weight_kg: null }],
  route: null, distance_km: null, origin_in_kanto: null, destinations: 1, sender_type: null, receiver_type: null,
  pickup: null, delivery: null, ship_date: null, desired_date: null, forklift: null, tail_lift: null, manual_load: null,
  cargo_name: null, fragile_note: null, monthly_shipments: null,
});
const newQForm = () => {
  const t = new Date(), v = new Date(t.getTime() + 14 * 86400000);
  const lines = {};
  for (const [k] of COSTS) lines[k] = { kind: 'unknown', amount: '' };
  return { carrier: '', product: '', total: '', tax: '税抜', received_on: isoLocal(t), valid_until: isoLocal(v), is_demo: false, lines, error: '' };
};
// 合計だけの回答（内訳が分からない）の見分け方: 「その他」に合計、ほかの項目はすべて「含む」。画面が作る形で、保存した回答でもこの形で見分ける（SPEC §42）
const isTotalOnly = (q) => !!(q && q.lines && q.lines.other && q.lines.other.kind === 'amount' && COSTS.every(([k]) => k === 'other' || (q.lines[k] && q.lines[k].kind === 'included')));
const totalOnlyMark = (q) => (isTotalOnly(q) ? '<span class="demo">（内訳不明）</span>' : '');
// 見積依頼文に書く送り方: 結果の表で選んだ行（app.js の S.pickLabel）。なければ表の丸ボタンの選択
const pickedMode = () => {
  const a = window.__app && window.__app.S ? window.__app.S.pickLabel : null;
  const r = document.querySelector('#cards input[name="cc-pick"]:checked');
  const v = a || (r ? r.value : null);
  return v ? String(v).split(' ')[0] : null;   // 「貸切 4t車」は送り方の名前「貸切」だけを使う
};
const versionSig = () => JSON.stringify([confirmedShipment(), curVer() && curVer().scenario, W.place, pickedMode()]);

function scenarioOfVersion() {
  const v = curVer();
  return v && v.scenario && v.scenario.id ? W.scenarios.find((s) => s.id === v.scenario.id) || null : null;
}
function canAddDemo() {
  const v = curVer(), sc = scenarioOfVersion();
  return !!(v && sc && sc.has_demo_quotes && !v.scenario.modified);
}

/* ------------------------------------------------------------ 荷物の欄（左上） */

function cargoSummary(sh) {
  let n = 0, kg = 0;
  for (const it of sh.items) {
    if (typeof it.count === 'number') { n += it.count; if (typeof it.weight_kg === 'number') kg += it.count * it.weight_kg; }
  }
  return { n, kg: Math.round(kg * 100) / 100 };
}
// 荷物の欄の見出し（ピル）は、いまの入力（確定前を含む）の 4 項目を映す
function pillLabel(d) {
  if (d.items.length !== 1) return d.cargo_name || `${d.items.length}種類の荷`;
  const sm = inferSimple(d) || W.simple;
  if (sm.kind === 'pallet') return 'パレット';
  return typeof sm.size === 'number' ? `段ボール${sm.size}` : '段ボール';
}
function renderCargoHead() {
  const v = curVer();
  if (!v) return;
  const { n, kg } = cargoSummary(W.draft || v.shipment);
  const label = pillLabel(W.draft || v.shipment);
  $w('#cargo').innerHTML = `<span class="cg-name">${wesc(label)}</span><span class="num">${n}個・${kg}kg</span>`;
  $w('#cargo').title = v.scenario ? `${v.scenario.name}（${v.scenario.label || '想定の荷主'}）${v.scenario.modified ? 'を元に変えた条件' : ''}` : '手入力の条件';
  $w('#cargo-ver').textContent = '';
}

function optionsHtml(list, cur, blank) {
  return (blank ? `<option value=""${cur === null || cur === undefined || cur === '' ? ' selected' : ''}>${blank}</option>` : '') +
    list.map(([v, l]) => `<option value="${wesc(v)}"${String(cur) === String(v) ? ' selected' : ''}>${wesc(l)}</option>`).join('');
}
const fv = (v) => (v === null || v === undefined ? '' : v);
const segHtml = (f, list, cur) => `<div class="seg" role="group" data-seg="${f}">` +
  list.map((x) => `<button type="button" data-v="${x}" aria-pressed="${cur === x}">${x}</button>`).join('') + '</div>';
const triOpts = (cur, yes, no) => optionsHtml([['true', yes], ['false', no]], cur === true ? 'true' : cur === false ? 'false' : '', '未入力');

/* ---- 荷物の 4 項目（荷の形・1 個の大きさ・重さ・個数）。荷物の行（items）の 1 行目に書き込む。詳しい条件の行と同じ値を指す */
const BOX_SIZES = [60, 80, 100, 120, 140, 160];   // 宅配サイズ（3 辺の合計 cm）
const boxDims = (size) => [Math.round(size * 0.5), Math.round(size * 0.3), Math.round(size * 0.2)];   // 3 辺の合計が size になる代表の箱（縦・横・高さ）
const PALLET_STD = [110, 110, 120];   // 1100×1100mm のパレット。荷の高さ 120cm（パレット込み）は仮定
const DEFAULTS = { sender_type: '法人', receiver_type: '法人', pickup: '集荷', delivery: '配達', destinations: 1 };
const DEFAULTS_LINE = '初めの設定: 法人から法人へ・集荷して配達・届け先 1 か所。出荷日と希望着日は見積依頼文の画面で入れます。';
const DEFAULT_CARGO_LINE = ' 荷物の初めの値は段ボール 100 サイズ・10kg・10 個です（仮の値。書き換えてください）。';
function simpleDefault() {
  const d = blankShipment();
  d.items = [{ count: 10, length_cm: 50, width_cm: 30, height_cm: 20, weight_kg: 10 }];
  d.route = 'kanto_kansai';   // 届け先を選ぶと、区間は届け先に合わせて入れ替わる
  applyDefaults(d);
  return d;
}
function applyDefaults(d) { for (const [k, v] of Object.entries(DEFAULTS)) if (d[k] === null || d[k] === undefined) d[k] = v; }
// いまの荷物の行から、荷の形と大きさを読み取る（例を読み込んだ・詳しい条件で直したとき）。1 行でないときは null
function inferSimple(d) {
  if (!d || d.items.length !== 1) return null;
  const it = d.items[0], L = Number(it.length_cm), Wd = Number(it.width_cm), H = Number(it.height_cm);
  if (!(L > 0 && Wd > 0)) return { kind: W.simple.kind, size: null };
  if (Math.max(L, Wd) >= 100 && Math.min(L, Wd) >= 80) return { kind: 'pallet', size: L === 110 && Wd === 110 ? 'std' : 'other' };
  const sum = L + Wd + (H > 0 ? H : 0);
  return { kind: 'box', size: H > 0 ? (BOX_SIZES.find((x) => sum <= x + 0.01) || 'over') : null };
}
function simpleHtml() {
  const d = W.draft;
  if (d.items.length !== 1) {   // 例の荷が複数種類のとき: 4 項目で入れ直すか、詳しい条件で行ごとに直す
    const cs = cargoSummary(d), sc0 = W.scenarios.find((x) => x.id === W.scenarioSel);
    return `<p class="sc-multi">${d.items.length} 種類の荷物（${cs.n}個・${cs.kg}kg）が入っています${sc0 ? `。例 ${wesc(sc0.id)}（架空の荷主）です` : ''}。</p>
      <div><button type="button" class="btn-sm" id="sc-reset">荷物を 1 種類で入れ直す</button></div>
      <p class="sc-assume">行ごとに直すときは「詳しい条件」を開いてください。</p>`;
  }
  const it = d.items[0], sm = W.simple, over = sm.kind === 'box' ? sm.size === 'over' : sm.size === 'other';
  const btn = (attr, v, label, on) => `<button type="button" ${attr}="${v}" aria-pressed="${on}">${label}</button>`;
  const sizes = sm.kind === 'box'
    ? BOX_SIZES.map((x) => btn('data-sc-size', x, x, sm.size === x)).join('') + btn('data-sc-size', 'over', 'それ以上', sm.size === 'over')
    : btn('data-sc-size', 'std', '1100×1100 標準', sm.size === 'std') + btn('data-sc-size', 'other', 'その他', sm.size === 'other');
  const num = (k, t, step) => `<label><span class="lbl">${t}</span><input class="ctl sm num" type="number" inputmode="decimal" min="0" step="${step}" data-sc="${k}" value="${fv(it[k])}"></label>`;
  let assume = '';
  if (sm.kind === 'box' && typeof sm.size === 'number') assume = `${sm.size} サイズは、約 ${boxDims(sm.size).join('×')}cm の箱として計算します（3 辺の合計が ${sm.size}cm）。`;
  else if (sm.kind === 'pallet' && sm.size === 'std') assume = `1100×1100mm のパレットに、高さ ${PALLET_STD[2]}cm（パレット込み）まで積んだものとして計算します。高さは仮定です。`;
  else if (over) assume = '大きさ（縦・横・高さ cm）を入れてください。';
  const sc = W.scenarios.find((x) => x.id === W.scenarioSel);
  const start = sc ? `<p class="sc-assume">例 ${wesc(sc.id)}（架空の荷主）の値が入っています。書き換えてください。</p>` : '';
  return `<div><span class="lbl">荷の形</span><div class="seg" role="group" aria-label="荷の形">${btn('data-sc-kind', 'box', '段ボール', sm.kind === 'box')}${btn('data-sc-kind', 'pallet', 'パレット', sm.kind === 'pallet')}</div></div>
    <div><span class="lbl">1 個の大きさ${sm.kind === 'box' ? '（宅配サイズ。3 辺の合計 cm）' : ''}</span><div class="seg" role="group" aria-label="1 個の大きさ">${sizes}</div>
      <div class="sc-dims"${over ? '' : ' hidden'}>${num('length_cm', '縦 cm', 'any')}${num('width_cm', '横 cm', 'any')}${num('height_cm', '高さ cm', 'any')}</div>
      ${assume ? `<p class="sc-assume">${wesc(assume)}</p>` : ''}</div>
    <div class="sc-two">${num('weight_kg', '重さ（1 個）kg', 'any')}${num('count', '個数', '1')}</div>${start}`;
}
// 4 項目 → 荷物の 1 行目。詳しい条件の行の入力欄にも同じ値を入れる
function setSimpleDims(dims) {
  const it = W.draft.items[0];
  [it.length_cm, it.width_cm, it.height_cm] = dims;
}
function syncDetailRow() {
  const row = $w('#cargo-editor .it-row[data-i="0"]');
  if (!row || W.draft.items.length < 1) return;
  row.querySelectorAll('[data-k]').forEach((e) => { e.value = fv(W.draft.items[0][e.dataset.k]); });
}
function refreshSimple(focusSel) {
  const el = $w('#sc-simple');
  if (!el) return;
  el.innerHTML = simpleHtml();
  if (focusSel) { const f = el.querySelector(focusSel); if (f) f.focus(); }
}

function renderCargoEditor() {
  const d = W.draft, ed = $w('#cargo-editor');
  const inf = inferSimple(d);
  if (inf) W.simple = { kind: inf.kind, size: inf.size === undefined ? null : inf.size };
  const rows = d.items.map((it, i) => `<div class="it-row" data-i="${i}">` +
    ['count', 'length_cm', 'width_cm', 'height_cm', 'weight_kg'].map((k) =>
      `<input class="ctl xs num" type="number" inputmode="decimal" min="0" step="any" data-item="${i}" data-k="${k}" value="${fv(it[k])}" aria-label="${i + 1}行目 ${{ count: '個数', length_cm: '縦', width_cm: '横', height_cm: '高さ', weight_kg: '1個の重さ' }[k]}">`).join('') +
    `<button type="button" class="it-del" data-del="${i}" aria-label="${i + 1}行目を消す"${d.items.length < 2 ? ' disabled' : ''}>&#10005;</button></div>`).join('');
  const sc = W.scenarios.find((s) => s.id === W.scenarioSel);
  ed.innerHTML = `
    <div class="sc-simple" id="sc-simple">${simpleHtml()}</div>
    <div class="ed-ai" id="cargo-ai"></div>
    <details class="ed-detail" id="ed-detail"${W.detailOpen ? ' open' : ''}><summary>詳しい条件</summary>
    <p class="ed-defaults">${wesc(DEFAULTS_LINE)}${W.defaultApplied ? wesc(DEFAULT_CARGO_LINE) : ''}</p>
    <p class="ed-origin-note">集荷できるのは関東の 7 か所です。ほかの地域は<a href="compare_lite.html">地図なしの比べるページ</a>へ。</p>
    <div class="ed-sec">
      <label class="lbl" for="sc-select">例の荷物を読み込む（架空）</label>
      <select id="sc-select" class="ctl sm">${optionsHtml([['', '新しく入力']].concat(W.scenarios.map((s) => [s.id, `${s.id} ${s.name}`])), W.scenarioSel)}</select>
      ${sc ? `<p class="ed-note">${wesc(sc.assumption_note || '')}</p>` : ''}
    </div>
    <div class="ed-sec">
      <div class="lbl">荷物（箱の種類ごと）</div>
      <div class="it-head"><span>個数</span><span>縦cm</span><span>横cm</span><span>高さcm</span><span>1個kg</span><span></span></div>
      <div class="items">${rows}</div>
      <button type="button" class="btn-link" id="add-item">+ 行を追加</button>
    </div>
    <div class="ed-grid">
      <label><span class="lbl">発送者</span><select class="ctl sm" data-f="sender_type">${optionsHtml(PARTY, d.sender_type, '選択')}</select></label>
      <label><span class="lbl">受取人</span><select class="ctl sm" data-f="receiver_type">${optionsHtml(PARTY, d.receiver_type, '選択')}</select></label>
      <div><span class="lbl">集荷か持込か</span>${segHtml('pickup', ['集荷', '持込'], d.pickup)}</div>
      <div><span class="lbl">配達か引取か</span>${segHtml('delivery', ['配達', '引取'], d.delivery)}</div>
      <label><span class="lbl">届け先の数</span><input class="ctl sm num" type="number" min="1" step="1" data-f="destinations" value="${fv(d.destinations)}"></label>
      <label><span class="lbl">月の出荷回数（任意）</span><input class="ctl sm num" type="number" min="1" step="1" data-f="monthly_shipments" value="${fv(d.monthly_shipments)}"></label>
      <label><span class="lbl">出荷日</span><input class="ctl sm" type="date" data-f="ship_date" value="${fv(d.ship_date)}"></label>
      <label><span class="lbl">希望着日</span><input class="ctl sm" type="date" data-f="desired_date" value="${fv(d.desired_date)}"></label>
    </div>
    <details class="ed-more"><summary>荷役設備・品目（任意）</summary>
      <div class="ed-grid">
        <label><span class="lbl">フォークリフト</span><select class="ctl sm" data-f="forklift" data-tri>${triOpts(d.forklift, 'あり', 'なし')}</select></label>
        <label><span class="lbl" title="荷台に昇降機つき">ゲート車</span><select class="ctl sm" data-f="tail_lift" data-tri>${triOpts(d.tail_lift, '必要', '不要')}</select></label>
        <label><span class="lbl">手積み</span><select class="ctl sm" data-f="manual_load" data-tri>${triOpts(d.manual_load, '可', '不可')}</select></label>
        <label><span class="lbl">品目</span><input class="ctl sm" type="text" data-f="cargo_name" value="${wesc(fv(d.cargo_name))}"></label>
        <label class="wide"><span class="lbl">壊れ物の扱い</span><input class="ctl sm" type="text" data-f="fragile_note" value="${wesc(fv(d.fragile_note))}"></label>
      </div>
    </details>
    </details>
    <div class="ed-foot"><span class="ed-state" id="ed-state" role="status"></span><button type="button" class="btn-link" id="ed-3d">積み方を 3D で見る</button><button type="button" class="btn-primary sm" id="ed-confirm">この条件で確定</button></div>`;
  updateEdState();
  mountCargoAi();
}

// 荷物の条件を文で書いて AI に表にしてもらう（SPEC §53.4）。ai_explain.js が使えるときだけ出る。案は、行ごとに「入れる」を押したときだけ荷物の表に入る
function mountCargoAi() {
  const el = $w('#cargo-ai');
  if (!el) return;
  if (window.AiExplain && AiExplain.mountCargo) { AiExplain.mountCargo(el, { onInsert: insertAiCargoRow }); return; }
  if (!W.cargoAiWait) { W.cargoAiWait = true; window.addEventListener('load', () => { W.cargoAiWait = false; mountCargoAi(); }, { once: true }); }   // ai_explain.js は後から読み込まれる
}
function insertAiCargoRow(r) {
  const it = { count: r.n ?? null, length_cm: r.l_cm ?? null, width_cm: r.w_cm ?? null, height_cm: r.h_cm ?? null, weight_kg: r.kg ?? null };
  const items = W.draft.items;
  const blank = (x) => ['count', 'length_cm', 'width_cm', 'height_cm', 'weight_kg'].every((k) => x[k] === null || x[k] === undefined || x[k] === '');
  if (items.length === 1 && blank(items[0])) items[0] = it; else items.push(it);
  W.touched = true;
  W.detailOpen = true;   // 入った行を、詳しい条件の表で見られるようにする
  renderCargoEditor();
  renderCargoHead();
}

function updateEdState(err) {
  const el = $w('#ed-state');
  if (!el) return;
  const v = curVer();
  el.classList.toggle('err', !!err);
  if (err) { el.textContent = err; return; }
  if (!v) { el.textContent = '条件はまだ確定していません'; return; }
  const dirty = JSON.stringify(W.draft) !== JSON.stringify(v.shipment);
  el.textContent = dirty ? 'まだ確定していない変更があります' : '確定済み';
  el.classList.toggle('dirty', dirty);
}

function bindCargo() {
  const ed = $w('#cargo-editor');
  const num = (v) => (v === '' ? null : Number(v));
  ed.addEventListener('input', (e) => {
    const t = e.target;
    if (e.isTrusted) W.touched = true;
    setTimeout(renderCargoHead, 0);   // 入力のあとの値でピルを更新する
    if (t.dataset.sc !== undefined) {   // 4 項目の入力（重さ・個数・寸法）
      W.draft.items[0][t.dataset.sc] = num(t.value);
      syncDetailRow();
      updateEdState();
      return;
    }
    if (t.dataset.item !== undefined) { W.draft.items[Number(t.dataset.item)][t.dataset.k] = num(t.value); refreshSimple(); }
    else if (t.dataset.f) {
      const f = t.dataset.f;
      if (t.type === 'number') W.draft[f] = num(t.value);
      else if (t.type === 'date') W.draft[f] = t.value || null;
      else if (t.dataset.tri !== undefined) W.draft[f] = t.value === '' ? null : t.value === 'true';
      else if (t.tagName === 'SELECT') W.draft[f] = t.value || null;
      else W.draft[f] = t.value === '' ? null : t.value;
    } else return;
    updateEdState();
  });
  ed.addEventListener('change', (e) => {
    const t = e.target;
    if (t.id === 'sc-select') {
      W.scenarioSel = t.value;
      const sc = W.scenarios.find((s) => s.id === t.value);
      if (sc) { W.draft = wclone(sc.shipment); if (!W.draft.items.length) W.draft.items = blankShipment().items; applyDefaults(W.draft); }   // 例で未入力の発送者・受取人などは初めの設定にする
      else if (t.value === '') { W.draft = blankShipment(); applyDefaults(W.draft); } // 「新しく入力」: 荷物の行・品目・日付を空にする（前の場面の値を残さない。SPEC §42）
      renderCargoEditor();
    } else if (t.dataset.f && t.tagName === 'SELECT') {
      W.draft[t.dataset.f] = t.dataset.tri !== undefined ? (t.value === '' ? null : t.value === 'true') : (t.value || null);
      updateEdState();
    }
  });
  ed.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (e.isTrusted) W.touched = true;
    setTimeout(renderCargoHead, 0);
    if (b.dataset.scKind !== undefined || b.dataset.scSize !== undefined) {   // 荷の形・1 個の大きさ
      if (W.draft.items.length !== 1) return;
      const it = W.draft.items[0];
      if (b.dataset.scKind !== undefined) {
        if (W.simple.kind !== b.dataset.scKind) {
          W.simple = { kind: b.dataset.scKind, size: null };
          it.length_cm = it.width_cm = it.height_cm = null;
        }
        refreshSimple(`[data-sc-kind="${b.dataset.scKind}"]`);
      } else {
        const v = b.dataset.scSize, n = Number(v);
        W.simple.size = Number.isFinite(n) ? n : v;
        if (typeof W.simple.size === 'number') setSimpleDims(boxDims(W.simple.size));
        else if (v === 'std') setSimpleDims(PALLET_STD);
        refreshSimple(`[data-sc-size="${v}"]`);
      }
      syncDetailRow();
      updateEdState();
      return;
    }
    if (b.id === 'sc-reset') {   // 例の荷を消して、4 項目の入力に切り替える
      W.draft.items = blankShipment().items;
      W.simple = { kind: 'box', size: null };
      renderCargoEditor();
      const f = ed.querySelector('[data-sc-size]'); if (f) f.focus();
      return;
    }
    if (b.dataset.v !== undefined && b.parentElement.dataset.seg) {
      const f = b.parentElement.dataset.seg, on = b.getAttribute('aria-pressed') === 'true';
      W.draft[f] = on ? null : b.dataset.v;
      b.parentElement.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', String(W.draft[f] === x.dataset.v)));
      updateEdState();
    } else if (b.id === 'add-item') {
      W.draft.items.push(blankShipment().items[0]);
      renderCargoEditor();
      ed.querySelector(`.it-row[data-i="${W.draft.items.length - 1}"] input`).focus();
    } else if (b.dataset.del !== undefined) {
      W.draft.items.splice(Number(b.dataset.del), 1);
      renderCargoEditor();
    } else if (b.id === 'ed-confirm') confirmCargo();
    else if (b.id === 'ed-3d' && window.LoadView) LoadView.open(W.draft); // 確定前の入力も見られる（SPEC §20.5）
  });
  ed.addEventListener('toggle', (e) => { if (e.target.id === 'ed-detail') W.detailOpen = e.target.open; }, true);
  $w('#cargo-toggle').addEventListener('click', () => setCargoOpen(!W.cargoOpen));
}

function setCargoOpen(on) {
  W.cargoOpen = on;
  $w('#cargo-editor').hidden = !on;
  $w('#cargo-toggle').setAttribute('aria-expanded', String(on));
  document.body.classList.toggle('cargo-open', on);
}

function afterCase(changed) {
  W.request = null; W.cmp = null;
  renderCargoHead();
  if (W.cargoOpen) updateEdState();
  renderResume();
  if (changed && W.hooks.onCargoChange) W.hooks.onCargoChange();
  if (W.open) loadStep();
}

// 版のお知らせは、送り方を比べるの画面のときだけ出す（自社の運行などの別の画面では、関係のないカードや地図の層の話になる）
const toastCompare = (m) => { if (document.body.classList.contains('view-compare')) toast(m); };

async function confirmCargo() {
  const btn = $w('#ed-confirm');
  btn.disabled = true;
  try {
    const hadQuotes = W.C.quotes.length > 0;
    const r = await wapi('/api/confirm', { case: caseForApi(), shipment: W.draft, scenario_id: W.scenarioSel || null });
    W.C = r.case;
    afterCase(r.changed);
    if (r.changed) toastCompare(`荷物の条件を変えました。運賃を計算し直しています${hadQuotes ? '。前の見積回答は別に分けました' : ''}。`);
    else toastCompare('条件は変わっていません');
  } catch (e) {
    updateEdState(e.message);
    toast(e.message);
  } finally { btn.disabled = false; }
}

/* 着地のパネルから: 区間と距離を当てはめた荷物で確定し、作業パネルを開く */
async function startFromDestination(est) {
  const v = curVer();
  try {
    const r = await wapi('/api/confirm', { case: caseForApi(), shipment: est.shipment, scenario_id: v && v.scenario ? v.scenario.id : null });
    const changed = r.changed;
    W.C = r.case;
    const g = est.geo, label = W.hooks.routeLabel ? W.hooks.routeLabel() : '';
    // 船・飛行機でしか行けない着地は距離を持たない。届け先の名前（label）だけ渡し、km は省く（依頼文に「距離 不明 km」と書かない）
    const kmKnown = !!(g && g.km && !g.no_route && !g.ferry);
    W.place = label && (kmKnown || (g && (g.no_route || g.ferry))) ? { label, km: kmKnown ? g.km : null, note: kmKnown ? (g.origin_note || null) : null } : null;
    for (const k of ['route', 'distance_km', 'origin_in_kanto']) W.draft[k] = est.shipment[k];
    afterCase(changed);
    if (changed) toastCompare('届け先に合わせて区間を変えました');
    openWork(1);
  } catch (e) { toast(e.message); }
}

/* ------------------------------------------------------------ 作業パネル */

function renderResume() {
  const b = $w('#work-resume');
  const v = curVer();
  const show = W.everOpened && !W.open && v;
  b.hidden = !show;
  if (show) b.textContent = `見積の続きに戻る（回答 ${W.C.quotes.length} 件）`;
}
function openWork(step) {
  W.open = true; W.everOpened = true;
  document.body.classList.add('has-work');
  $w('#work').setAttribute('aria-hidden', 'false');
  if (W.hooks.onWorkToggle) W.hooks.onWorkToggle(true);
  renderResume();
  setStep(step || W.step);
}
function closeWork() {
  if (!W.open) return;
  W.open = false;
  document.body.classList.remove('has-work');
  $w('#work').setAttribute('aria-hidden', 'true');
  if (W.hooks.onWorkToggle) W.hooks.onWorkToggle(false);
  renderResume();
}

function setStep(n) {
  W.step = n;
  $w('#work').classList.toggle('w-s1', n === 1);   // 案内の表示では、1 の依頼文だけ、作業の段の切り替えと下の帯を隠す（style.css）
  document.querySelectorAll('#w-steps button').forEach((b) => b.setAttribute('aria-selected', String(Number(b.dataset.step) === n)));
  $w('#w-prev').disabled = n === 1;
  $w('#w-next').hidden = n === 4;
  $w('#w-next').textContent = ['', '次: 回答を入れる', '次: 比べる', '次: 選んで保存'][n] || '';
  $w('#w-body').scrollTop = 0;
  loadStep();
}

async function loadStep() {
  const v = curVer();
  if (!v) return;
  const tok = ++W.token, body = $w('#w-body');
  const sh = v.shipment;
  const p = [];
  p.push(`${wesc(W.hooks.routeLabel ? W.hooks.routeLabel() : '')}`);
  $w('#w-route').innerHTML = `<span>${p[0]}</span>`;
  try {
    if (W.step === 1) {
      const key = versionSig();
      if (!W.request || W.requestKey !== key) {
        W.request = await wapi('/api/request', { shipment: sh, scenario: v.scenario, ...(W.place ? { place: W.place } : {}), ...(pickedMode() ? { mode: pickedMode() } : {}) });
        W.requestKey = key;
      }
    } else {
      W.cmp = await wapi('/api/compare', { quotes: W.C.quotes, version: v.number });
    }
  } catch (e) {
    if (tok === W.token) body.innerHTML = `<p class="w-err">${wesc(e.message)}</p>`;
    return;
  }
  if (tok !== W.token) return;
  body.innerHTML = [null, renderRequest, renderQuotes, renderCompare, renderJudgment][W.step]();
  const f = [null, bindRequest, bindQuotes, bindCompare, bindJudgment][W.step];
  if (f) f();
  const n = W.C.quotes.length, cur = W.C.quotes.filter((q) => q.version === v.number).length;
  $w('#w-state').textContent = `見積回答 ${n} 件（今の条件 ${cur} 件）`;
}

/* ---- 1 依頼文 */
// 案内の表示では、本文の最初の数行だけを開いて見せ、「全文を読む」で全文にする（最初に目に入る文字を増やしすぎない）
const reqHead = (t) => String(t).split(String.fromCharCode(10)).slice(0, 5).join(String.fromCharCode(10)) + String.fromCharCode(10) + '…';
function renderRequest() {
  const r = W.request;
  const miss = r.missing_required.length ? `<p class="miss"><b>荷主側で未入力の必須項目:</b> ${r.missing_required.map(wesc).join('、')}</p>` : '';
  const opt = r.missing_optional.length ? `<details class="miss-opt"><summary>入れなくてもよい項目 ${r.missing_optional.length} 件</summary><p>${r.missing_optional.map(wesc).join('、')}</p></details>` : '';
  // 見積を頼む先。手段の列も名前も持たない（試算額を出した手段と会社を結び付けない。SPEC §31.2）
  const rows = r.contacts.map((c) => `<tr><td>${c.has_window
    ? `<a href="${wesc(c.url)}" target="_blank" rel="noopener">${wesc(c.name)}</a>` : `<span class="no-win">${wesc(c.name)}</span> <b class="need">窓口の確認が必要</b>`}</td><td>${wesc(c.condition)}</td><td class="nw num">${wesc(c.checked_on)}</td></tr>`).join('');
  // 案内の文の「比べるページ」は compare_lite.html へのリンクにする（SPEC §33 #9）
  const pointer = r.contacts_note ? `<p class="hint">${wesc(r.contacts_note).replace('比べるページ', '<a href="compare_lite.html" target="_blank" rel="noopener">比べるページ</a>')}。</p>` : '';
  const table = rows ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>窓口</th><th>条件</th><th>確認日</th></tr></thead><tbody>${rows}</tbody></table></div>` : '';
  // 案内の表示: 荷主側の未入力の必須項目（出荷日・希望着日）は、ここで入れて確定できる（依頼文の上）。確定すると条件の版が 1 つ上がり、依頼文が作り直される
  const needDate = r.missing_required.filter((m) => m === '出荷日' || m === '希望着日');
  const need = needDate.length
    ? `<div class="w-need" role="group" aria-label="未入力の必須項目"><b>先に入れてください</b>
      ${needDate.map((m) => `<label><span class="lbl">${m}</span><input class="ctl sm" type="date" data-need="${m === '出荷日' ? 'ship_date' : 'desired_date'}" value="${fv(W.draft[m === '出荷日' ? 'ship_date' : 'desired_date'])}"></label>`).join('')}
      <span class="hint">2 つ入れると、依頼文に入ります。</span></div>` : '';
  // 案内の表示: 依頼文と「依頼文をコピー」だけを前に出し、窓口の表・保存・ほかの作業は「詳しく」に入れる
  if (document.body.classList.contains('guided')) {
    return `<section class="sec">
    <div class="sec-h"><h3>見積依頼文</h3><span class="chip ${r.status === '依頼準備完了' ? 'ok' : 'check'}">${wesc(r.status === '依頼準備完了' ? '依頼文ができました' : r.status)}</span></div>
    ${need}
    <pre class="req-text w-peek" id="req-text">${wesc(reqHead(r.text))}</pre>
    <button type="button" class="btn-link w-peek-btn" id="req-more" aria-expanded="false" aria-controls="req-text">全文を読む</button>
    <div class="w-primary"><button type="button" class="btn-primary" id="req-copy">依頼文をコピー</button></div>
    <p class="hint">送信はしません。運送会社の窓口に貼り付けて使います。</p>
    <details class="w-more"><summary>頼む先と保存</summary>
      ${opt}
      <div class="btn-row"><button type="button" class="btn-sm" id="req-dl">テキストで保存</button></div>
      <div class="sec-h"><h3>見積を頼む先</h3>${rows ? '<span class="hint">会社の五十音順（確認日つき）</span>' : ''}</div>
      ${table}${pointer}
      <div class="w-steps-btns" role="group" aria-label="ほかの作業"><button type="button" class="btn-sm" data-goto="2">見積回答を入れる</button><button type="button" class="btn-sm" data-goto="3">比較する</button><button type="button" class="btn-sm" data-goto="4">選んで保存する</button></div>
    </details>
  </section>`;
  }
  return `<section class="sec">
    <div class="sec-h"><h3>見積依頼文</h3><span class="chip ${r.status === '依頼準備完了' ? 'ok' : 'check'}">${wesc(r.status === '依頼準備完了' ? '依頼文ができました' : r.status)}</span></div>
    ${miss}${opt}
    <pre class="req-text" id="req-text" tabindex="0">${wesc(r.text)}</pre>
    <div class="btn-row"><button type="button" class="btn-sm" id="req-copy">コピー</button><button type="button" class="btn-sm" id="req-dl">テキストで保存</button>
      <span class="hint">送信はしません。運送会社の窓口に貼り付けて使います。</span></div>
  </section>
  <section class="sec">
    <div class="sec-h"><h3>見積を頼む先</h3>${rows ? '<span class="hint">会社の五十音順（確認日つき）</span>' : ''}</div>
    ${table}${pointer}
  </section>`;
}
function bindRequest() {
  const more = document.getElementById('req-more');
  if (more) more.addEventListener('click', () => { const t = $w('#req-text'), on = !t.classList.contains('open'); t.classList.toggle('open', on); if (on) t.tabIndex = 0; else t.removeAttribute('tabindex'); t.textContent = on ? W.request.text : reqHead(W.request.text); more.setAttribute('aria-expanded', String(on)); more.textContent = on ? '閉じる' : '全文を読む'; });
  // 出荷日と希望着日の両方が入ったら、その日付で確定して（条件の版が 1 つ上がる）依頼文を作り直す
  const ins = [...document.querySelectorAll('#w-body [data-need]')];
  ins.forEach((i) => i.addEventListener('change', () => {
    if (ins.some((x) => !x.value)) return;
    for (const x of ins) W.draft[x.dataset.need] = x.value;
    confirmCargo();
  }));
  document.querySelectorAll('#w-body [data-goto]').forEach((b) => b.addEventListener('click', () => setStep(Number(b.dataset.goto))));
  $w('#req-copy').addEventListener('click', async () => {
    const text = W.request.text;
    try { await navigator.clipboard.writeText(text); } catch (e) {
      const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } finally { ta.remove(); }
    }
    toast('依頼文をコピーしました');
  });
  $w('#req-dl').addEventListener('click', () => {
    download(`request_v${curVer().number}.txt`, W.request.text, 'text/plain;charset=utf-8');
    toast('依頼文を保存しました');
  });
}
function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/* ---- 2 見積回答 */
function costRowsHtml() {
  const q = W.qform;
  return COSTS.map(([k, name]) => {
    const l = q.lines[k];
    return `<div class="cl" data-k="${k}"><span class="cl-n">${name}</span>
      <div class="seg sm" role="group" aria-label="${name}の入力">${[['amount', '金額'], ['included', '含む'], ['unknown', '不明']].map(([v, t]) =>
        `<button type="button" data-kind="${v}" aria-pressed="${l.kind === v}">${t}</button>`).join('')}</div>
      <input class="ctl xs num" type="number" min="0" step="100" inputmode="numeric" placeholder="円" value="${wesc(l.amount)}"${l.kind === 'amount' ? '' : ' disabled'} aria-label="${name}の金額（円）"></div>`;
  }).join('');
}
function quoteListHtml() {
  const evs = W.cmp ? W.cmp.evaluations : [];
  if (!evs.length) return '<p class="hint pad">見積回答はまだありません。</p>';
  return evs.map((e) => {
    const q = e.quote;
    return `<article class="q-card ${e.state}"><div class="q-top"><b>${wesc(q.carrier)}${q.is_demo ? '<span class="demo">（架空）</span>' : ''}${totalOnlyMark(q)}</b>
      <small>${wesc(q.product)}${q.product ? ' ・ ' : ''}${wesc(q.tax)} ・ 有効期限 ${wesc(q.valid_until || '未入力')}</small>
      <span class="chip ${STATE_CHIP[e.state]}">${STATE_LABEL[e.state]}</span><button type="button" class="q-del" data-id="${wesc(q.id)}" aria-label="${wesc(q.carrier)}の回答を消す">消す</button></div>
      <div class="q-sum num">${wesc(e.summary)}</div></article>`;
  }).join('');
}
function renderQuotes() {
  const q = W.qform, v = curVer();
  const demo = canAddDemo() ? `<div class="demo-box"><button type="button" class="btn-sm" id="q-demo">架空の回答例を入れる</button>
      <span class="hint">架空A社・架空B社の回答例を追加します。</span></div>`
    : (v.scenario && v.scenario.modified ? '<p class="hint">例の荷物を変えたため、回答例は入れられません。</p>' : '');
  return `<section class="sec">
    <div class="sec-h"><h3>見積回答を入れる</h3><span class="hint">今の荷物の条件の回答として入ります</span></div>
    ${demo}
    <form id="q-form" autocomplete="off" novalidate>
      <div class="qf-grid">
        <label><span class="lbl">会社名</span><input class="ctl sm" type="text" name="carrier" value="${wesc(q.carrier)}" placeholder="例: 架空A社"></label>
        <label><span class="lbl">商品名</span><input class="ctl sm" type="text" name="product" value="${wesc(q.product)}" placeholder="例: 路線便"></label>
        <label><span class="lbl">受領日</span><input class="ctl sm" type="date" name="received_on" value="${wesc(q.received_on)}"></label>
        <label><span class="lbl">有効期限</span><input class="ctl sm" type="date" name="valid_until" value="${wesc(q.valid_until)}"></label>
      </div>
      <div class="qf-tax"><span class="lbl">税区分</span><div class="seg" role="group" data-tax>${['税抜', '税込'].map((t) => `<button type="button" data-tax="${t}" aria-pressed="${q.tax === t}">${t}</button>`).join('')}</div></div>
      <label class="qf-total"><span class="lbl">合計（円）。内訳が分からなければ合計だけ</span><input class="ctl sm num" type="number" min="1" step="100" inputmode="numeric" name="total" value="${wesc(q.total)}" placeholder="例: 120000"></label>
      <div class="lbl cl-head">費用の内訳（任意）<span title="合計だけなら空のまま">（?）</span></div>
      <div class="cl-grid">${costRowsHtml()}</div>
      <div class="qf-foot"><label class="chk"><input type="checkbox" name="is_demo"${q.is_demo ? ' checked' : ''}> 架空の回答（デモ用）</label>
        <span class="qf-err" id="qf-err" role="alert">${wesc(q.error)}</span>
        <button type="submit" class="btn-primary sm" id="q-add">この回答を追加</button></div>
    </form>
  </section>
  <section class="sec"><div class="sec-h"><h3>入っている回答</h3><span class="hint">${W.C.quotes.length}件</span></div><div id="q-list">${quoteListHtml()}</div></section>`;
}
function bindQuotes() {
  const q = W.qform, form = $w('#q-form');
  form.addEventListener('input', (e) => {
    const t = e.target;
    if (t.name && t.name in q && t.type !== 'checkbox') q[t.name] = t.value;
    else if (t.name === 'is_demo') q.is_demo = t.checked;
    else if (t.closest('.cl')) q.lines[t.closest('.cl').dataset.k].amount = t.value;
  });
  form.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.kind) {
      const row = b.closest('.cl'), l = q.lines[row.dataset.k];
      l.kind = b.dataset.kind;
      row.querySelectorAll('.seg button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      const inp = row.querySelector('input');
      inp.disabled = l.kind !== 'amount';
      if (l.kind === 'amount') inp.focus();
    } else if (b.dataset.tax) {
      q.tax = b.dataset.tax;
      b.parentElement.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    }
  });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = (m) => { q.error = m; $w('#qf-err').textContent = m; };
    if (!q.carrier.trim()) return err('会社名を入れてください');
    const lines = {};
    const anyLine = COSTS.some(([k]) => q.lines[k].kind !== 'unknown');
    const totalTxt = String(q.total == null ? '' : q.total).trim();
    if (totalTxt !== '' && anyLine) return err('合計と内訳は、どちらか一方だけ入れてください（合計だけの回答は、内訳を「不明」のままにします）');
    if (totalTxt !== '') {
      if (!Number.isInteger(Number(totalTxt)) || Number(totalTxt) <= 0) return err('合計は 1 以上の整数（円）で入れてください');
      for (const [k] of COSTS) lines[k] = k === 'other' ? { kind: 'amount', amount: Number(totalTxt) } : { kind: 'included', amount: null };   // 合計だけ。ほかの項目は合計に含む（内訳不明の印は isTotalOnly で付く）
    } else for (const [k, name] of COSTS) {
      const l = q.lines[k];
      if (l.kind === 'amount') {
        if (l.amount === '' || !Number.isInteger(Number(l.amount)) || Number(l.amount) < 0) return err(`${name}の金額を 0 以上の整数で入れてください`);
        lines[k] = { kind: 'amount', amount: Number(l.amount) };
      } else lines[k] = { kind: l.kind, amount: null };
    }
    err('');
    $w('#q-add').disabled = true;
    try {
      const quote = { carrier: q.carrier, product: q.product, tax: q.tax, received_on: q.received_on || null, valid_until: q.valid_until || null, is_demo: q.is_demo, lines };
      const r = await wapi('/api/add_quote', { case: caseForApi(), quote });
      W.C = r.case;
      const name = q.carrier.trim();
      Object.assign(q, newQForm(), { tax: q.tax, received_on: q.received_on, valid_until: q.valid_until });
      renderResume();
      toast(`${name} の回答を追加しました`);
      await loadStep();
      $w('#w-body').querySelector('input[name="carrier"]').focus();
    } catch (er) { err(er.message); $w('#q-add').disabled = false; }
  });
  const demo = $w('#q-demo');
  if (demo) demo.addEventListener('click', async () => {
    try {
      const r = await wapi('/api/add_demo_quotes', { case: caseForApi() });
      W.C = r.case;
      renderResume();
      toast(r.added ? `架空の回答例を ${r.added} 件入れました` : '回答例はもう入っています');
      await loadStep();
    } catch (er) { toast(er.message); }
  });
  $w('#q-list').addEventListener('click', quoteListClick);
}
async function quoteListClick(e) {
  const b = e.target.closest('.q-del');
  if (!b) return;
  W.C.quotes = W.C.quotes.filter((x) => x.id !== b.dataset.id);
  if (W.C.chosen_quote_id === b.dataset.id) W.C.chosen_quote_id = null;
  renderResume();
  await loadStep();
}

/* ---- 3 比較 */
const swatches = () => `<div class="cost-legend">${COSTS.map(([k, n]) => `<span><i style="background:${COST_COLOR[k]}"></i>${n}</span>`).join('')}<span><i class="unk"></i>不明（空欄）</span></div>`;
function stackHtml(e, scale) {
  const amts = e.line_amounts_ex_tax, label = (k) => COSTS.find((c) => c[0] === k)[1];
  const segs = COSTS.filter(([k]) => amts[k]).map(([k]) => {
    const w = (amts[k] / scale) * 100;
    return `<i class="seg-b" style="width:${w.toFixed(2)}%;background:${COST_COLOR[k]}" title="${label(k)} ${wyen(amts[k])}円（税抜）">${w >= 8 ? `<span>${label(k)}</span>` : ''}</i>`;
  }).join('');
  const unk = e.unknown_keys.length ? `<i class="seg-u" title="不明: ${e.unknown_keys.map(label).join('・')}"><span>不明</span></i>` : '';
  if (isTotalOnly(e.quote)) return `<div class="stack"><i class="seg-b" style="width:${((e.total_ex_tax || e.partial_ex_tax || 0) / scale * 100).toFixed(2)}%;background:#8b95a5" title="内訳不明（合計のみ）"><span>内訳不明</span></i></div>`;
  return `<div class="stack">${segs}${unk}</div>`;
}
function renderCompare() {
  const c = W.cmp, v = curVer();
  const cur = c.evaluations.filter((e) => e.state !== 'history');
  const hist = c.evaluations.filter((e) => e.state === 'history');
  if (!c.evaluations.length) return '<section class="sec"><p class="hint pad">見積回答がまだありません。「見積回答」で追加してください。</p></section>';
  const scale = Math.max(1, ...cur.map((e) => (e.state === 'complete' ? e.total_ex_tax : e.partial_ex_tax)));
  const rows = cur.map((e) => {
    const q = e.quote, done = e.state === 'complete';
    const incl = COSTS.filter(([k]) => q.lines[k] && q.lines[k].kind === 'included' && !e.unknown_keys.includes(k)).map((x) => x[1]);
    return `<div class="cmp-row ${e.state}${e.rank === 1 ? ' first' : ''}">
      <div class="cmp-rank num">${e.rank ? `<b>${e.rank}</b>位` : '<span>順位なし</span>'}</div>
      <div class="cmp-main">
        <div class="cmp-top"><span class="nm"><b>${wesc(q.carrier)}${q.is_demo ? '<span class="demo">（架空）</span>' : ''}${totalOnlyMark(q)}</b><small>${wesc(q.product)}</small></span>
          <span class="tot num">${done ? `<b>${wyen(e.total_ex_tax)}</b>円` : `分かっている分の合計 <b>${wyen(e.partial_ex_tax)}</b>円`}<small>税抜</small></span></div>
        ${stackHtml(e, scale)}
        <div class="cmp-sub num">${done
          ? (c.ranking_shown ? (e.diff_from_lowest === 0 ? '最安' : `最安との差額 <b>+${wyen(e.diff_from_lowest)}円</b>`) : '比べる回答が 1 件だけです')
          : `不明: ${e.unknown_keys.map((k) => COSTS.find((x) => x[0] === k)[1]).join('・') || 'なし'}${e.reasons.length ? `（${wesc(e.reasons.join('、'))}）` : ''}`}
          ${isTotalOnly(q) ? '<span class="incl">内訳不明（合計だけの回答。順位には入ります）</span>' : incl.length ? `<span class="incl">含む: ${incl.join('・')}</span>` : ''}</div>
      </div></div>`;
  }).join('');
  const head = COSTS.map((x) => `<th>${x[1]}</th>`).join('');
  const body = cur.map((e) => `<tr class="${e.state}"><td class="nw">${wesc(e.quote.carrier)}${e.quote.is_demo ? '（架空）' : ''}${isTotalOnly(e.quote) ? '（内訳不明）' : ''}</td>${isTotalOnly(e.quote) ? `<td class="muted" colspan="${COSTS.length}">内訳不明（合計だけの回答）</td>` : COSTS.map(([k]) => {
    if (e.unknown_keys.includes(k)) return '<td class="unknown">不明</td>';
    if (k in e.line_amounts_ex_tax) return `<td class="num">${wyen(e.line_amounts_ex_tax[k])}</td>`;
    return '<td class="muted">含む</td>';
  }).join('')}<td class="num tt">${e.state === 'complete' ? wyen(e.total_ex_tax) : '—'}</td></tr>`).join('');
  const histHtml = hist.length ? `<details class="hist"><summary>条件を変える前の回答 ${hist.length} 件（比べません）</summary>${hist.map((e) =>
    `<div class="hist-row"><b>${wesc(e.quote.carrier)}${e.quote.is_demo ? '（架空）' : ''}</b><span>${wesc(e.reasons.join('、'))}</span></div>`).join('')}</details>` : '';
  return `<section class="sec">
    <div class="sec-h"><h3>比較</h3><span class="hint">${wesc(c.message)}（${wesc(c.today)} 時点）</span></div>
    ${swatches()}
    <div class="cmp-list">${rows}</div>
    <p class="hint">金額は税抜です。不明の項目がある回答は順位に入れません。</p>
  </section>
  <section class="sec"><div class="sec-h"><h3>内訳の表</h3><span class="hint">税抜の円</span></div>
    <div class="tbl-wrap"><table class="tbl bd"><thead><tr><th>会社</th>${head}<th>合計</th></tr></thead><tbody>${body}</tbody></table></div>
    ${histHtml}</section>`;
}
function bindCompare() { /* 表示だけ */ }

/* ---- 4 判断と保存 */
function renderJudgment() {
  const c = W.cmp, v = curVer();
  const done = c.evaluations.filter((e) => e.state === 'complete');
  const ch = W.C.chosen_quote_id;
  const chosenEv = c.evaluations.find((e) => e.quote.id === ch);
  let warn = '';
  if (ch && (!chosenEv || chosenEv.state !== 'complete')) {
    warn = !chosenEv ? '選んだ回答が見積回答の一覧にありません。'
      : chosenEv.state === 'history' ? '選んだ回答は条件変更前のものです。いまの条件で見積を取り直してください。'
        : '選んだ回答に不明の項目があります。回答の内容を確かめてください。';
  }
  const opts = [`<label class="opt"><input type="radio" name="choice" value=""${ch ? '' : ' checked'}><span class="o-t">選ばない</span></label>`].concat(done.map((e) =>
    `<label class="opt"><input type="radio" name="choice" value="${wesc(e.quote.id)}"${ch === e.quote.id ? ' checked' : ''}>
      <span class="o-t">${wesc(e.quote.carrier)}${e.quote.is_demo ? '（架空）' : ''}${isTotalOnly(e.quote) ? '（内訳不明）' : ''} <small>${wesc(e.quote.product)}</small></span>
      <span class="o-v num">${wyen(e.total_ex_tax)}円${e.rank && c.ranking_shown ? ` ・ ${e.rank}位${e.diff_from_lowest ? `（+${wyen(e.diff_from_lowest)}円）` : ''}` : ''}</span></label>`));
  if (ch && chosenEv && chosenEv.state !== 'complete') {
    opts.push(`<label class="opt"><input type="radio" name="choice" value="${wesc(ch)}" checked><span class="o-t">${wesc(chosenEv.quote.carrier)} <small>選択済み（不明の項目あり）</small></span></label>`);
  }
  const sv = W.saved ? `<p class="saved num" role="status">保存しました: <b>${wesc(W.saved.name)}</b>（回答 ${W.saved.quotes} 件${W.saved.chosen ? `・選んだ回答 ${wesc(W.saved.chosen)}` : ''}・${wesc(W.saved.at)}）</p>` : '';
  return `<div class="notice" role="note">${wesc(c.notice)}</div>
  <section class="sec">
    <div class="sec-h"><h3>選ぶ回答</h3><span class="hint">内訳がそろった回答だけ選べます</span></div>
    ${warn ? `<p class="miss">${wesc(warn)}</p>` : ''}
    <div class="opts">${opts.join('')}</div>
    <label class="blk"><span class="lbl">選んだ理由</span><textarea class="ctl ta" id="j-reason" rows="3" placeholder="例: 集荷から配達まで含み、総額が最も低いため">${wesc(W.C.reason)}</textarea></label>
    <label class="blk"><span class="lbl">残る確認事項（1行に1つ）</span><textarea class="ctl ta" id="j-issues" rows="3" placeholder="例: 受託の可否を電話で確かめる">${wesc(W.issuesText)}</textarea></label>
  </section>
  <section class="sec">
    <div class="sec-h"><h3>保存と読み込み</h3><span class="hint">保存はブラウザのダウンロードです。サーバーには残しません</span></div>
    <div class="btn-row"><button type="button" class="btn-primary sm" id="j-save">この見積をファイルに保存</button>
      <label class="btn-sm file" tabindex="0">保存したファイルを開く<input type="file" id="j-load" accept=".json,application/json"></label></div>
    ${sv}<p class="qf-err" id="j-err" role="alert"></p>
  </section>`;
}
function issuesFromText() { return W.issuesText.split('\n').map((x) => x.trim()).filter(Boolean); }
function bindJudgment() {
  $w('#w-body').querySelectorAll('input[name="choice"]').forEach((r) => r.addEventListener('change', () => { W.C.chosen_quote_id = r.value || null; }));
  $w('#j-reason').addEventListener('input', (e) => { W.C.reason = e.target.value; });
  $w('#j-issues').addEventListener('input', (e) => { W.issuesText = e.target.value; W.C.open_issues = issuesFromText(); });
  $w('#j-save').addEventListener('click', saveCase);
  $w('#j-load').addEventListener('change', loadCase);
}
async function saveCase() {
  try {
    const body = caseForApi();
    body.open_issues = issuesFromText();
    const res = await wapi('/api/save', body);
    const text = JSON.stringify(res, null, 2) + '\n';
    const d = new Date(), name = `shipment_case_${isoLocal(d).replace(/-/g, '')}.json`;
    download(name, text, 'application/json');
    const ch = res.quotes.find((q) => q.id === res.chosen_quote_id);
    W.saved = { name, kb: (new Blob([text]).size / 1024).toFixed(1), versions: res.versions.length, quotes: res.quotes.length,
      chosen: ch ? `${ch.carrier}${ch.is_demo ? '（架空）' : ''}` : '', at: (res.saved_at || '').replace('T', ' ').slice(0, 16) };
    toast('ファイルに保存しました');
    $w('#w-body').innerHTML = renderJudgment();
    bindJudgment();
  } catch (e) { $w('#j-err').textContent = e.message; }
}
async function loadCase(e) {
  const f = e.target.files[0];
  if (!f) return;
  const err = (m) => { const el = $w('#j-err'); if (el) el.textContent = m; };
  try {
    let obj;
    try { obj = JSON.parse(await f.text()); } catch (x) { throw new Error('JSON として読めません。保存したファイルを選んでください'); }
    const res = await wapi('/api/load', obj);
    W.C = res;
    const v = curVer();
    W.draft = v ? wclone(v.shipment) : blankShipment();
    W.scenarioSel = v && v.scenario && v.scenario.id ? v.scenario.id : '';
    W.issuesText = (res.open_issues || []).join('\n');
    W.saved = null;
    if (W.cargoOpen) renderCargoEditor();
    afterCase(true);
    toast(`読み込みました（回答 ${res.quotes.length} 件）`);
  } catch (x) { err(x.message); toast(x.message); }
  finally { e.target.value = ''; }
}

/* ------------------------------------------------------------ 起動 */

function bindWork() {
  $w('#w-close').addEventListener('click', closeWork);
  $w('#work-resume').addEventListener('click', () => openWork());
  document.querySelectorAll('#w-steps button').forEach((b) => b.addEventListener('click', () => setStep(Number(b.dataset.step))));
  $w('#w-prev').addEventListener('click', () => setStep(Math.max(1, W.step - 1)));
  $w('#w-next').addEventListener('click', () => setStep(Math.min(4, W.step + 1)));
  addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && W.open && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) { closeWork(); e.stopImmediatePropagation(); }
  });
}

// 案内の「送り方を比べる」は、例の荷ではなく 1 種類の簡単な荷（段ボール 100 サイズ・10kg・10 個）から始める。
// 利用者が触る前で、いまの確定が例のままのときだけ、一度だけ入れ替えて自動で確定する（版の違いの警告を出さない）
async function retryInitConfirm() {   // 起動時に確定できなかったときは、比べる画面に入ったときにもう一度試し、だめなら誤りを出す
  if (!W.initError || W.initRetrying) return;
  W.initRetrying = true;
  try {
    const r = await wapi('/api/confirm', { case: caseForApi(), shipment: W.draft, scenario_id: W.scenarioSel || null });
    W.C = r.case; W.initError = '';
    renderCargoEditor();
    afterCase(true);
  } catch (e) {
    W.initError = e.message || W.initError;
    if (!W.initShown) { W.initShown = true; toast(`荷物の条件を確定できませんでした（${W.initError}）。送り方の比較は使えません。`); }
  } finally { W.initRetrying = false; }
}

async function applySimpleDefault() {
  const b = document.body.classList, v = curVer();
  if (!v && W.initError && b.contains('view-compare')) { retryInitConfirm(); return; }
  if (W.defaultApplied || W.touched || !v || !b.contains('guided') || !b.contains('view-compare')) return;
  if (v.scenario && v.scenario.modified) return;
  if (JSON.stringify(W.draft) !== JSON.stringify(v.shipment)) return;   // 確定していない入力がある
  W.defaultApplied = true;
  try {
    W.draft = simpleDefault();
    W.scenarioSel = '';
    const r = await wapi('/api/confirm', { case: caseForApi(), shipment: W.draft, scenario_id: null });
    W.C = r.case;
    renderCargoEditor();
    afterCase(r.changed);
  } catch (e) { W.defaultApplied = false; }
}

async function winit(hooks) {
  W.hooks = hooks;
  W.scenarios = (await wapi('/api/scenarios')).scenarios;
  W.C = emptyCase();
  W.qform = newQForm();
  const s1 = W.scenarios.find((s) => s.id === 'S1') || W.scenarios[0];
  W.draft = s1 ? wclone(s1.shipment) : blankShipment();
  applyDefaults(W.draft);   // 詳しい条件を開かない人でも、発送者・受取人・集荷・配達・届け先の数が入った状態で確定する
  W.scenarioSel = s1 ? s1.id : '';
  // 荷物の確定は送り方を比べるだけで使う。失敗しても全国の物流傾向・空きを探すは開けるようにし、誤りは比べる画面に入ったときに出す
  try {
    const r = await wapi('/api/confirm', { case: caseForApi(), shipment: W.draft, scenario_id: W.scenarioSel || null });
    W.C = r.case;
    W.initError = '';
  } catch (e) { W.initError = e.message || '荷物の条件を確定できませんでした'; }
  bindCargo();
  bindWork();
  renderCargoHead();
  renderCargoEditor();
  $w('#cargo-editor').hidden = true;
  renderResume();
  new MutationObserver(applySimpleDefault).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  applySimpleDefault();
}

window.Work = {
  init: winit, api: wapi, confirmedShipment, startFromDestination, openWork, closeWork,
  isOpen: () => W.open, version: () => (curVer() ? curVer().number : 0),
  scenarioId: () => (curVer() && curVer().scenario ? curVer().scenario.id : null),
  // 荷主が自分で入れた荷物か（例の場面そのままではない）。空きを探すが、入れた荷物を例に差し替えないために使う
  ownCargo: () => { const v = curVer(); return !!v && (!v.scenario || !!v.scenario.modified); },
};
