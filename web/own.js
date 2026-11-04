'use strict';
/* 自社の運行（SPEC §22、架空の会社のサンプル）。
   数字・区間・気づきは tools/build_own_sample.py（src/shipcompare/own.py）が作った web/data/own_sample.json を表示するだけで、
   ここでは計算しない。例外は地図の線の形（向きごとのずらし、点線）、タブ、確認の「確定・修正・読まない」（ページの中だけ）。 */

const Own = (() => {
  const O = {
    data: null, loaded: false, active: false, tab: 'import', market: false, dec: {}, fixTxt: {}, open: new Set(), geo: null, markers: [], dropMsg: '',
  };
  const WD = '月火水木金土日';
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const q = (s) => document.querySelector(s);
  const pc = (v, d = 0) => (v == null ? '—' : (v * 100).toFixed(d) + '%');
  const n0 = (v) => Math.round(v).toLocaleString('ja-JP');
  const COLOR = { loaded: [92, 200, 255, 240], empty: [244, 150, 70, 245], unknown: [150, 162, 180, 215] };
  const DOT = { green: 'g', yellow: 'y', red: 'r', gray: 'n' };
  const KIND = { loaded: '実車', empty: '空車', unknown: '不明' };
  const NOT_YET = 'Claude の提案: 未作成';

  /* ------------------------------------------------------------ 読み込み */

  async function load() {
    if (O.loaded) return;
    try {
      O.data = await getJSON('data/own_sample.json');
      O.loaded = true;
      buildGeo();
    } catch (e) {
      console.error(e);
      O.err = e.message;
    }
  }

  /* ------------------------------------------------------------ 地図の線 */

  function offsetPath(path, d) {
    const out = [];
    for (let i = 0; i < path.length; i++) {
      const a = path[Math.max(0, i - 1)], b = path[Math.min(path.length - 1, i + 1)], k = Math.cos((path[i][1] * Math.PI) / 180);
      const dx = (b[0] - a[0]) * k, dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1;
      out.push([path[i][0] + ((-dy / L) * d) / k, path[i][1] + (dx / L) * d]);
    }
    return out;
  }
  // 道のりを一定の長さで区切って点線にする（座標は度のまま。見た目の長さは拡大で変わる）
  function dashes(path, dash, gap) {
    const out = [];
    let cur = [], on = true, left = dash;
    for (let i = 0; i < path.length - 1; i++) {
      let [x0, y0] = path[i];
      const [x1, y1] = path[i + 1], k = Math.cos((y0 * Math.PI) / 180);
      let seg = Math.hypot((x1 - x0) * k, y1 - y0);
      const ux = (x1 - x0) / (seg || 1), uy = (y1 - y0) / (seg || 1);
      if (on && !cur.length) cur.push([x0, y0]);
      while (seg > 1e-9) {
        const step = Math.min(left, seg);
        x0 += ux * step; y0 += uy * step; seg -= step; left -= step;
        if (on) cur.push([x0, y0]);
        if (left <= 1e-9) {
          if (on) { out.push(cur); cur = []; } else cur = [[x0, y0]];
          on = !on; left = on ? dash : gap;
        }
      }
    }
    if (on && cur.length > 1) out.push(cur);
    return out;
  }
  // 進む向きを示す「く」の字（山形）を、線の上の指定の位置に置く
  function chevrons(path, fracs, size, flip) {
    const out = [], n = path.length - 1;
    for (const f of fracs) {
      const i = Math.max(1, Math.min(n - 1, Math.round(n * f))), a = path[i - 1], b = path[i + 1], p = path[i];
      const k = Math.cos((p[1] * Math.PI) / 180);
      let dx = (b[0] - a[0]) * k, dy = b[1] - a[1];
      const L = Math.hypot(dx, dy) || 1;
      dx /= L; dy /= L;
      if (flip) { dx = -dx; dy = -dy; }
      const tip = [p[0] + (dx * size * 0.6) / k, p[1] + dy * size * 0.6];
      const wing = (s) => {
        const c = Math.cos(s), sn = Math.sin(s), rx = dx * c - dy * sn, ry = dx * sn + dy * c;
        return [tip[0] - (rx * size) / k, tip[1] - ry * size];
      };
      out.push([wing(0.5), tip, wing(-0.5)]);
    }
    return out;
  }

  // 向きと種類ごとに、道路の形を少しずつ横へずらして並べる（重ならないように）。行きは北側、帰りは南側
  const LANE = { out: { loaded: 0.07, unknown: 0.19, empty: 0.31 }, back: { loaded: -0.07, empty: -0.19, unknown: -0.31 } };

  function buildGeo() {
    const d = O.data, base = d.route.path;
    const groups = {};
    for (const l of d.legs) {
      if (!l.dir) continue;
      const k = `${l.dir}|${l.kind}`;
      groups[k] = (groups[k] || 0) + 1;
    }
    const items = [], marks = [];
    for (const [k, n] of Object.entries(groups)) {
      const [dir, kind] = k.split('|');
      const pth = offsetPath(base, LANE[dir][kind]);
      const w = Math.min(9, 1.8 + 0.16 * n);
      const col = COLOR[kind];
      if (kind === 'empty') for (const s of dashes(pth, 0.14, 0.1)) items.push({ path: s, w, col, dir, kind, n });
      else items.push({ path: pth, w: kind === 'unknown' ? Math.max(1.5, w * 0.6) : w, col: kind === 'unknown' ? [col[0], col[1], col[2], 190] : col, dir, kind, n });
      if (kind === 'loaded') marks.push(...chevrons(pth, [0.28, 0.5, 0.72], 0.06, dir === 'back').map((p) => ({ path: p })));
    }
    O.geo = { items, marks };
  }

  function layers() {
    if (!O.geo) return [];
    const L = deck;
    return [
      new L.PathLayer({
        id: 'o-path', data: O.geo.items, getPath: (x) => x.path, getWidth: (x) => x.w, getColor: (x) => x.col, widthUnits: 'pixels', capRounded: true, jointRounded: true,
        pickable: false, widthMinPixels: 1.2,
      }),
      new L.PathLayer({
        id: 'o-chev', data: O.geo.marks, getPath: (x) => x.path, getWidth: 2.4, getColor: [236, 248, 255, 235], widthUnits: 'pixels', capRounded: true, jointRounded: true, pickable: false,
      }),
      new L.ScatterplotLayer({
        id: 'o-pts', data: Object.values(O.data.places), getPosition: (p) => [p.lon, p.lat], getRadius: 7, radiusUnits: 'pixels', getFillColor: [255, 255, 255, 255],
        getLineColor: [92, 200, 255, 255], stroked: true, lineWidthUnits: 'pixels', getLineWidth: 3, pickable: false,
      }),
    ];
  }

  function syncMarkers() {
    if (!O.loaded) return;
    if (!O.markers.length) {
      for (const p of Object.values(O.data.places)) {
        const el = document.createElement('div');
        el.className = 'own-tag';
        el.innerHTML = p.id === O.data.base
          ? `<b>${esc(p.short)}</b><small>自社の車庫</small>`
          : `<b>${esc(p.pseudo)}</b><small>${esc(p.label)}</small>`;
        O.markers.push({ el, mk: new maplibregl.Marker({ element: el, anchor: 'bottom', offset: [0, -12] }).setLngLat([p.lon, p.lat]).addTo(map) });
      }
    }
    for (const m of O.markers) m.el.style.display = O.active ? '' : 'none';
  }

  /* ------------------------------------------------------------ 左: タブ */

  function counts() {
    const d = O.data;
    const pending = d.confirm.filter((c) => !O.dec[c.id]).length;
    return { pending, ins: d.insights.length };
  }

  function dotCls(c) { return `o-dot ${DOT[c] || 'n'}`; }

  function importHtml() {
    const d = O.data;
    const rows = d.files.map((f) => `<tr><td class="o-fn"><b>${esc(f.file)}</b><small>${esc(f.kind)}${f.note ? '・' + esc(f.note) : ''}</small></td>
      <td class="num">${f.ok}</td><td class="num ${f.pending ? 'o-y' : ''}">${f.pending}</td><td class="num ${f.unreadable ? 'o-r' : ''}">${f.unreadable}</td></tr>`).join('');
    const unread = d.unreadable.map((u) => `<tr><td class="o-where"><small>${esc(u.file)}</small>${esc(u.where)}</td><td class="o-mono">${esc(u.original)}</td><td class="o-r">${esc(u.reason)}</td></tr>`).join('');
    const cols = d.columns.map((c) => `<div class="o-col" title="${esc(c.formula)}">
      <span class="o-cn"><b>${esc(c.column)}</b><small>${esc(c.source)}</small></span>
      <span class="o-cd">${c.dict_hit ? '辞書 ○' : '辞書 ×'}</span><span class="o-cs num">形 ${pc(c.shape_ratio)}</span>
      <span class="o-cc"><i class="${dotCls(c.color)}"></i><span class="num">${c.confidence.toFixed(2)}</span></span>
      <span class="o-cl">${c.claude_proposal ? 'Claude: ' + esc(c.claude_proposal.role) : NOT_YET}</span></div>`).join('');
    const g = d.columns.filter((c) => c.color === 'green').length, y = d.columns.filter((c) => c.color === 'yellow').length, r = d.columns.filter((c) => c.color === 'red').length;
    return `<div class="o-drop" id="own-drop" tabindex="0" role="button" aria-label="自社のファイルを落とす欄（この版ではサンプルだけを扱います）">
        <b>自社のファイルを落とす</b><span>配車表・デジタコの CSV</span><input id="own-file" type="file" multiple hidden></div>
      <p class="o-drop-msg" id="own-drop-msg" ${O.dropMsg ? '' : 'hidden'}>${esc(O.dropMsg)}</p>
      <table class="o-tbl"><thead><tr><th>ファイル</th><th>読めた行</th><th>確認待ち</th><th>読めない行</th></tr></thead><tbody>${rows}
        <tr class="o-paper"><td colspan="4">${esc(d.paper.text)}</td></tr></tbody></table>
      <details class="o-det" open><summary>表の形の判定</summary>${d.files.map((f) => `<p class="o-verdict"><b>${esc(f.file)}</b> ${esc(f.verdict)}</p>`).join('')}</details>
      <div class="o-h">読めない ${d.unreadable.length} 行（わざと入れた例）</div>
      <table class="o-tbl o-un"><tbody>${unread}</tbody></table>
      <details class="o-det" open><summary>列の対応づけ（緑 ${g}・黄 ${y}・赤 ${r}）</summary>
        <p class="o-claude ${d.claude.present ? '' : 'none'}">${esc(d.claude.label)}${d.claude.present ? '' : '（開発のときに作る記録の欄は空です。確かさは辞書の一致と値の形だけで計算しています）'}</p>
        <div class="o-cols">${cols}</div></details>`;
  }

  function confirmHtml() {
    const d = O.data, cnt = { ok: 0, fix: 0, skip: 0 };
    for (const c of d.confirm) if (O.dec[c.id]) cnt[O.dec[c.id]]++;
    const pend = d.confirm.length - cnt.ok - cnt.fix - cnt.skip;
    const cards = d.confirm.map((c) => {
      const st = O.dec[c.id] || '';
      const claude = c.kind === 'column' ? `<div class="o-kv"><span>Claude</span><span class="${c.claude ? '' : 'o-none'}">${c.claude ? 'Claude の提案: ' + esc(c.claude.role) : NOT_YET}</span></div>` : '';
      const conf = c.confidence != null ? `<div class="o-kv"><span>確かさ</span><span class="num o-formula">${esc(c.formula)}</span></div>` : `<div class="o-kv"><span>しきい値</span><span class="o-formula">${esc(c.formula)}</span></div>`;
      const label = { ok: '確定しました', fix: '修正しました', skip: '読まないことにしました' }[st] || '';
      return `<div class="o-card ${st ? 'done ' + st : ''}" data-id="${c.id}">
        <div class="o-ch"><i class="${dotCls(st ? 'green' : c.color)}"></i><b>${esc(c.title)}</b><small>${esc(c.source)}</small></div>
        <div class="o-kv"><span>元の値</span><span>${esc(c.original)}</span></div>
        <div class="o-kv"><span>提案</span><span>${esc(c.proposal)}</span></div>${claude}${conf}
        <div class="o-btns">${st ? `<span class="o-res">${label}${st === 'fix' && O.fixTxt[c.id] ? '：' + esc(O.fixTxt[c.id]) : ''}</span><button type="button" class="btn-link" data-act="undo">取り消す</button>`
          : '<button type="button" class="btn-sm" data-act="ok">確定</button><button type="button" class="btn-sm" data-act="fix">修正</button><button type="button" class="btn-sm" data-act="skip">読まない</button>'}</div>
        <div class="o-fix" ${O.open.has('fix' + c.id) && !st ? '' : 'hidden'}><input class="ctl sm" type="text" placeholder="直した値を入れる" aria-label="直した値"><button type="button" class="btn-sm" data-act="fixok">反映</button></div>
      </div>`;
    }).join('');
    return `<p class="o-sum">確認待ち <b class="num">${pend}</b> 件・確定 ${cnt.ok}・修正 ${cnt.fix}・読まない ${cnt.skip}<small>押した結果はこのページの中だけに残ります。</small></p>${cards}`;
  }

  function insightsHtml() {
    const d = O.data;
    if (!d.insights.length) return '<p class="o-sum">この月の気づきはありません。</p>';
    return d.insights.map((i) => {
      const open = O.open.has('ev' + i.id);
      const ev = open ? `<div class="o-ev">${i.evidence.map((e) => `<div class="o-evr"><b>${esc(e.label)}</b><small>${esc(e.file || '')} ${esc(e.where || '')}</small><span>${esc(e.text || '')}</span></div>`).join('')}</div>` : '';
      return `<div class="o-ins" data-id="${i.id}">
        <div class="o-ih"><span class="o-rule">規則 ${i.rule}</span></div>
        <p class="o-it">${esc(i.text)}</p>
        <div class="o-chips">${i.numbers.map((n) => `<span class="o-chip"><small>${esc(n.k)}</small><b class="num">${esc(n.v)}</b></span>`).join('')}</div>
        <div class="o-btns"><button type="button" class="${i.next.action === 'draft_backhaul' ? 'btn-primary sm' : 'btn-sm'}" data-act="next" data-a="${esc(i.next.action)}">${esc(i.next.label)}</button>
          <button type="button" class="btn-link" data-act="ev">${open ? '元の行を閉じる' : '元の行を見る'}（${i.evidence.length}）</button></div>${ev}</div>`;
    }).join('');
  }

  function renderLeft() {
    const body = q('#own-body');
    if (!body || !O.loaded) return;
    const c = counts();
    q('#own-tabs').innerHTML = `<button data-tab="import" aria-pressed="${O.tab === 'import'}">取り込みの結果</button>
      <button data-tab="confirm" aria-pressed="${O.tab === 'confirm'}">確認<em class="${c.pending ? 'y' : ''}">${c.pending}</em></button>
      <button data-tab="insight" aria-pressed="${O.tab === 'insight'}">気づき<em>${c.ins}</em></button>`;
    const keep = body.scrollTop;
    body.innerHTML = O.tab === 'import' ? importHtml() : O.tab === 'confirm' ? confirmHtml() : insightsHtml();
    body.scrollTop = keep;
    bindDrop();
  }

  /* ------------------------------------------------------------ 右: 数字と表 */

  function tile(label, val, sub, nat, tip) {
    return `<div class="o-tile" title="${esc(tip)}"><div class="o-tl">${label}</div><div class="o-tv num">${val}</div><div class="o-ts">${sub}</div>${nat ? `<div class="o-tn">${nat}</div>` : ''}</div>`;
  }

  function renderRight() {
    const el = q('#own-right');
    if (!el || !O.loaded) return;
    const d = O.data, k = d.kpis, n = d.national;
    const ar = k.actual_rate, lr = k.load_rate, le = k.load_efficiency, lar = k.large_actual_rate;
    const tiles = [
      tile('実車率', `${(ar.value * 100).toFixed(1)}<small>%</small>`, `判定できた区間 ${pc(ar.share)}（${ar.legs} / ${ar.of} 区間）`,
        `全国 ${n.actual_rate_pct}%（営業用普通車）`, ar.formula),
      tile('大型の積載率', `${(lr.value * 100).toFixed(1)}<small>%</small>`, `計算できた区間 ${pc(lr.share)}（${lr.legs} 区間。大型 ${lr.of_large} 区間のうち）`, '', lr.formula),
      tile('大型の積載効率', `${(le.value * 100).toFixed(1)}<small>%</small>`, `大型の積載率 × 大型の実車率 ${pc(lar.value)}`, `全国 ${n.load_efficiency_pct}%（トンキロの比。式が違います）`, le.formula),
    ].join('');
    const rowsOf = (dir) => {
      const rs = d.table.filter((t) => t.dir === dir);
      const tot = rs.reduce((a, t) => ({ trips: a.trips + t.trips, empty: a.empty + t.empty, km: a.km + t.empty_km, ls: a.ls + (t.long_stops || 0), unk: a.unk + t.unknown }), { trips: 0, empty: 0, km: 0, ls: 0, unk: 0 });
      const body = rs.map((t) => {
        const hot = dir === 'back' && t.trips >= 4 && t.empty * 4 >= (t.trips - t.unknown) * 3 && (t.trips - t.unknown) >= 4;
        return `<tr class="${hot ? 'hot' : ''}"><td>${WD[t.wd]}曜発</td><td class="num">${t.trips}</td><td class="num">${t.empty}</td><td class="num">${t.empty_km ? n0(t.empty_km) : '—'}</td><td class="num">${t.long_stops == null ? '—' : t.long_stops}</td></tr>`;
      }).join('');
      return `<tr class="grp"><td colspan="5">${dir === 'out' ? '行き' : '帰り'}　${esc(d.places[dir === 'out' ? d.base : d.dock].short)} → ${esc(d.places[dir === 'out' ? d.dock : d.base].short)}</td></tr>${body}
        <tr class="tot"><td>合計</td><td class="num">${tot.trips}</td><td class="num">${tot.empty}</td><td class="num">${n0(tot.km)}</td><td class="num">${dir === 'out' ? tot.ls : '—'}</td></tr>`;
    };
    el.innerHTML = `<header><div class="m-rh"><b>自社の運行 ${d.mon} 月</b><span class="m-rsub">${esc(d.company.name)}・車両 ${d.company.vehicles} 台</span></div></header>
      <div class="o-tiles">${tiles}</div>
      <p class="o-caution">${esc(n.caution)}</p>
      <p class="o-unk">実車か空車かが不明の区間 ${d.counts.unknown} 区間（${pc(d.counts.unknown_ratio)}）は、実車率に入れていません。距離は元の記録のメーター、無いときは OSRM の推定です。</p>
      <div class="o-tw"><table class="o-tab" aria-label="区間と向きと曜日の表"><thead><tr><th>向き・出た曜日</th><th>便数</th><th>空車</th><th>空車の km</th><th>30 分以上の停車</th></tr></thead>
        <tbody>${rowsOf('out')}${rowsOf('back')}</tbody></table></div>
      <div class="o-foot"><label class="f-chk"><input type="checkbox" id="own-mkt" ${O.market ? 'checked' : ''}><span>市場の矢印を重ねる</span></label>
        <div class="o-lg"><span><i class="lg-l"></i>実車（濃い線）</span><span><i class="lg-e"></i>空車（点線）</span><span><i class="lg-u"></i>不明</span><span class="mlg-faint">太さ = 便数</span>
        <span><i class="lg-m"></i>市場の矢印（薄い青）</span></div>
        <details class="o-src"><summary>全国の値の出典</summary><p>${esc(n.source)}</p></details></div>`;
    const cb = q('#own-mkt');
    if (cb) cb.addEventListener('change', () => { O.market = cb.checked; });
  }

  /* ------------------------------------------------------------ 操作 */

  function bindDrop() {
    const z = q('#own-drop'), f = q('#own-file');
    if (!z || z.dataset.bound) return;
    z.dataset.bound = '1';
    const say = () => { O.dropMsg = 'この版ではサンプルだけを扱います'; const m = q('#own-drop-msg'); m.textContent = O.dropMsg; m.hidden = false; toast(O.dropMsg); };
    z.addEventListener('click', () => f.click());
    z.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); f.click(); } });
    f.addEventListener('change', () => { f.value = ''; say(); });
    ['dragenter', 'dragover'].forEach((t) => z.addEventListener(t, (e) => { e.preventDefault(); z.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((t) => z.addEventListener(t, (e) => { e.preventDefault(); z.classList.remove('over'); }));
    z.addEventListener('drop', () => say()); // ファイルは読まない
  }

  function draftNote() {
    const ins = O.data.insights.find((i) => i.rule === 1);
    const p = ins ? ins.next.payload : null;
    const m = O.data.mon;
    if (!p) return `${m} 月の運行から、帰り便の下書きを作りました。11/13 の帰り便 T7 はこの下書きから登録した、という設定の架空の便です。`;
    const body = p.empty === p.trips ? `帰りが ${p.empty} 回とも空でした` : `帰りが ${p.trips} 回のうち ${p.empty} 回空でした`;
    return `${m} 月は${p.weekday}曜${p.night ? 'の夜' : ''}に出た便の${body}。11/13 の帰り便 T7 はこの下書きから登録した、という設定の架空の便です。`;
  }

  function draft() {
    App.setView('find');
    const st = Find.state;
    if (st.carriers && st.carriers.some((c) => c.id === 'C4')) st.carrier = 'C4';
    Find.setRole('carrier');
    const el = q('#own-draft');
    if (el) {
      el.querySelector('.od-text').textContent = draftNote();
      el.hidden = false;
    }
  }

  function onNext(a) {
    if (a === 'draft_backhaul') draft();
    else if (a === 'import') { O.dropMsg = 'この版ではサンプルだけを扱います'; toast(O.dropMsg); }
  }

  function bindLeft() {
    const root = q('#own-view');
    root.addEventListener('click', (e) => {
      const tb = e.target.closest('#own-tabs button[data-tab]');
      if (tb) { setTab(tb.dataset.tab); return; }
      const b = e.target.closest('button[data-act]');
      if (!b) return;
      const card = b.closest('.o-card'), ins = b.closest('.o-ins');
      const act = b.dataset.act;
      if (card) {
        const id = card.dataset.id;
        if (act === 'ok' || act === 'skip') O.dec[id] = act;
        else if (act === 'fix') { O.open.add('fix' + id); }
        else if (act === 'fixok') {
          const v = card.querySelector('.o-fix input').value.trim();
          if (!v) { toast('直した値を入れてください'); return; }
          O.fixTxt[id] = v; O.dec[id] = 'fix'; O.open.delete('fix' + id);
        } else if (act === 'undo') { delete O.dec[id]; delete O.fixTxt[id]; }
        renderLeft();
      } else if (ins) {
        const id = ins.dataset.id;
        if (act === 'ev') { const k = 'ev' + id; if (O.open.has(k)) O.open.delete(k); else O.open.add(k); renderLeft(); }
        else if (act === 'next') {
          const a = b.dataset.a;
          if (a === 'show_evidence') { O.open.add('ev' + id); renderLeft(); } else onNext(a);
        }
      }
    });
    q('#own-draft .od-close').addEventListener('click', () => { q('#own-draft').hidden = true; });
  }

  function setTab(t) {
    O.tab = t;
    renderLeft();
    writeHash();
  }

  /* ------------------------------------------------------------ 見方の出入り */

  function fit(animate) {
    const rw = (q('#own-right') && q('#own-right').offsetWidth) || 540;
    map.fitBounds([[134.2, 34.1], [140.6, 36.2]], { padding: { top: 70, bottom: 150, left: 500, right: rw + 60 }, duration: animate ? 1500 : 0, essential: true });
  }

  async function enter() {
    O.active = true;
    await load();
    renderLeft(); renderRight(); syncMarkers();
    fit(true);
  }
  function leave() { O.active = false; syncMarkers(); }

  async function init() {
    await load();
    if (!O.loaded) return;
    bindLeft();
    renderLeft(); renderRight();
  }

  function hashParams(p) { if (O.tab !== 'import') p.set('ot', O.tab); }
  function readHash(p) { const t = p.get('ot'); if (['import', 'confirm', 'insight'].includes(t)) O.tab = t; }
  function writeHash() {
    if (typeof S === 'undefined' || S.view !== 'own') return;
    const p = new URLSearchParams();
    p.set('v', 'own');
    hashParams(p);
    history.replaceState(null, '', '#' + p.toString());
  }

  return { init, enter, leave, layers, market: () => O.market, setTab, hashParams, readHash, state: O, fit };
})();
