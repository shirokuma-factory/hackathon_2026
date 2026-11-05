'use strict';
/* 自社の運行（SPEC §22、架空の会社のサンプル）。
   数字・区間・気づきは tools/build_own_sample.py（src/shipcompare/own.py）が作った web/data/own_sample.json を表示するだけで、
   ここでは計算しない。例外は地図の線の形（向きごとのずらし、点線）、タブ、確認の「確定・修正・読まない」（ページの中だけ）。 */

const Own = (() => {
  // 荷待ちを入れる（SPEC §23）の状態。記録はこのページのメモリにだけあり、保存しない
  const W = { year: 2026, rows: [], res: null, err: '', tok: 0, tm: null, partner: '', pasteMsg: '', pasteNote: '', bulkMsg: '', idxMap: [], bad: new Set() };
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
      <button data-tab="insight" aria-pressed="${O.tab === 'insight'}">気づき<em>${c.ins}</em></button>
      <button data-tab="wait" aria-pressed="${O.tab === 'wait'}">荷待ちを入れる</button>`;
    const keep = body.scrollTop;
    body.innerHTML = O.tab === 'import' ? importHtml() : O.tab === 'confirm' ? confirmHtml() : O.tab === 'wait' ? waitHtml() : insightsHtml();
    body.scrollTop = keep;
    const tag = q('#own-view .m-tag');
    if (tag) tag.textContent = O.tab === 'wait' ? '自社の記録を入れる' : '架空のサンプル';
    bindDrop();
  }

  /* ------------------------------------------------------------ 右: 数字と表 */

  function tile(label, val, sub, nat, tip) {
    return `<div class="o-tile" title="${esc(tip)}"><div class="o-tl">${label}</div><div class="o-tv num">${val}</div><div class="o-ts">${sub}</div>${nat ? `<div class="o-tn">${nat}</div>` : ''}</div>`;
  }

  function renderRight() {
    const el = q('#own-right');
    if (!el || !O.loaded) return;
    if (O.tab === 'wait') { const keep = (el.querySelector('.w-rbody') || {}).scrollTop || 0; el.innerHTML = waitRightHtml(); const b = el.querySelector('.w-rbody'); if (b) b.scrollTop = keep; return; }
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


  /* ------------------------------------------------------------ 荷待ちを入れる（SPEC §23）
     計算は /api/waiting/calc（tools/serve_web.py → src/shipcompare/waiting.py）。ここは入力を送って、返った値を並べるだけで、金額も回数も計算しない。 */

  const W_COLS = [
    { k: 'date', t: '日付', ph: '10/5', w: 76 }, { k: 'vehicle_no', t: '車番', w: 58 },
    { k: 'vehicle_class', t: '車種', sel: ['', '小型', '中型', '大型', 'トレーラー'], w: 78 },
    { k: 'partner', t: '契約の相手', w: 118 }, { k: 'place', t: '場所', w: 104 },
    { k: 'arrive', t: '到着', ph: '09:00', w: 52 }, { k: 'start', t: '荷役開始', ph: '09:30', w: 52 },
    { k: 'end', t: '荷役終了', ph: '09:52', w: 52 }, { k: 'depart', t: '出発', ph: '10:00', w: 52 },
    { k: 'handler', t: '荷役をした人', sel: ['', '運転者', '荷主側'], w: 76 },
    { k: 'method', t: '荷役の方法', sel: ['', '手積み', 'フォークリフト等'], w: 96 },
    { k: 'shipper_fault', t: '荷主等の都合の待機か', sel: ['', 'はい', 'いいえ'], w: 66 },
    { k: 'appointed', t: '指定の到着時刻', ph: '08:30', w: 56 },
    { k: 'side_work', t: '附帯業務の内容', w: 110 }, { k: 'side_min', t: '附帯業務の分', ph: '20', w: 52 },
    { k: 'post_wait', t: '荷役のあとの待ちを荷主等の都合として数える', sel: ['', 'はい'], w: 124 },
  ];
  const W_PROTECT = [
    '参考額は告示の表に当てた計算です。請求するか、いくらにするかは運送会社が決めます。法的な判断は弁護士・運輸支局などに確かめてください',
    '入れた記録はこの PC の中だけで計算し、外に送りません',
  ];
  const W_BASIS_QUOTE = '貸切運賃料金適用方（参考例）第 21 項（Q&A 集に収録）は、待機時間に「荷主による積込み・取卸しの時間を含む」とし、30 分を超える部分に待機時間料を収受するとしています。荷主側が荷役をした行は、その時間も待機に入れています。';
  const W_BASIS = [
    W_BASIS_QUOTE,
    '書面交付の法定事項に、荷役作業・附帯業務の内容と対価が入ります（改正法 Q&A 問2-2・2-3）。',
    '標準運送約款 第 34 条（標準貨物自動車運送約款）: 「到着後、荷送人又は荷受人の責により待機した時間」について待機時間料を収受できます。荷役のあとの待ち（書類待ちなど）がこれに当たるかは、契約の相手と確かめてください。既定では数えず、行ごとに「荷役のあとの待ちを荷主等の都合として数える」を はい にした行だけ、出発 − 荷役終了 を待機に足します（30 分の差し引きは 1 か所ごとに 1 回、2 時間の境は待機の始まりから）。',
    '取適法では、長時間の待機の費用を負担しないことが違反になり得ます（Q42・Q132。規模の基準に当たる場合）。',
    'トラック・物流Gメンの情報提供の窓口があります（荷主には元請けを含みます）。',
    '数え方は標準的運賃（2024 年 3 月 22 日告示）の別紙の表と、Q&A 集 Q29（到着時刻から数える。最初の 30 分を差し引く）です。積込料・取卸料の数え方は告示の文言からの読み取りで、Q&A の例で確かめたのは待機時間料だけです。',
  ];
  const W_PROCESS = [
    '次回からの契約の書面に、待機時間料と積込料・取卸料の欄を入れてもらうよう頼みます。過去分を求めるかどうかは、運送会社が決めます。',
    '国交省のリーフレットは、記録を待機時間料などを収受する根拠にできると書いています。',
    '取適法では、相手（元請など）が規模の基準（資本金か従業員数）に当たるとき、国土交通大臣（トラック・物流Gメン）などに知らせたことを理由にした取引の停止などの不利益な扱いが禁じられています（第 5 条第 1 項第 7 号）。相手が基準に当たらないときは使えません。',
    'トラック・物流Gメンの窓口: https://truck-logistics-gmen.mlit.go.jp/s/',
    '指定の到着時刻より早く着いたときは、指定の時刻から待機を数えています（控えめな扱い）。附帯業務は記録して一覧に出しますが、告示で実費として収受とされているため、参考額は付けていません。',
  ];
  const W_DEFAULT_FAULT_NOTE = '荷主等の都合の欄が無いので『はい』として計算しました。違う行は『いいえ』に変えてください';
  const W_POST_WAIT_NOTE = '「荷役のあとの待ちを荷主等の都合として数える」を はい にした行は、出発 − 荷役終了 も待機の分に足しています。標準運送約款 第 34 条の「到着後、荷送人又は荷受人の責により待機した時間」に当たるかは、契約の相手と確かめてください。';
  const W_EXAMPLE_DAYS = [1, 2, 5, 6, 7, 8, 9, 13, 14, 15, 16];
  const blankRow = () => Object.fromEntries(W_COLS.map((c) => [c.k, '']));
  const isBlank = (r) => W_COLS.every((c) => !String(r[c.k] || '').trim());
  const hm = (m) => (m ? `${Math.floor(m / 60)} 時間 ${m % 60} 分` : '0 分');
  const yen = (v) => `${n0(v)} 円`;

  // 全角・半角の違いを同じ相手として数える（サーバーの計算と同じ正規化）
  const nzp = (s) => String(s == null ? '' : s).normalize('NFKC').replace(/\s+/g, ' ').trim();

  function bulkOptions() {
    const names = [...new Set(W.rows.filter((r) => !isBlank(r)).map((r) => nzp(r.partner)).filter(Boolean))];
    return '<option value="">すべての行</option>' + names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
  }

  function bulkRefresh() {
    const sel = q('#w-bulk-partner');
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = bulkOptions();
    sel.value = [...sel.options].some((o) => o.value === cur) ? cur : '';
  }

  // 選んだ相手（「すべての行」なら全部）の行の「荷主等の都合の待機か」を まとめて はい / いいえ にする
  function bulkFault(val) {
    const sel = q('#w-bulk-partner'), who = sel ? sel.value : '';
    let n = 0;
    W.rows.forEach((r) => { if (!isBlank(r) && (!who || nzp(r.partner) === who)) { r.shipper_fault = val; n += 1; } });
    W.bulkMsg = n ? `${n} 行を『${val}』にしました` : '対象の行がありません';
    renderLeft(); waitCalc();
  }

  // 空欄の欄を、すぐ上の入っている値で埋める（Excel の結合セルを貼ると下の行が空になるため）
  function fillDown(key, label) {
    let last = '', n = 0;
    W.rows.forEach((r) => {
      if (isBlank(r)) return;
      if (String(r[key] || '').trim()) last = r[key];
      else if (last) { r[key] = last; n += 1; }
    });
    W.bulkMsg = n ? `${label}の空欄 ${n} か所を、上の行の値で埋めました` : `${label}の空欄はありません`;
    renderLeft(); waitCalc();
  }

  function waitInit() { if (!W.rows.length) W.rows = [blankRow(), blankRow(), blankRow()]; }

  function exampleRows() {
    // 11 行（地点07、中型、荷主等の都合）。8 行は到着 09:00・荷役開始 09:30・荷主側の荷役 22 分で待機 52 分（荷主側の荷役も待機に入る）。
    // 3 番目と 8 番目は荷役なしで待機 75 分。最後の行は指定の到着時刻 08:30（到着 08:10）で、運転者が 10:15〜10:55 に手積み
    // （待機は 08:30 から数え、2 時間の境は 10:30 になる）
    return W_EXAMPLE_DAYS.map((d, i) => {
      const base = { ...blankRow(), date: `${W.year}-10-${String(d).padStart(2, '0')}`, vehicle_class: '中型', partner: '架空 元請A物流', place: '江東区 地点07', shipper_fault: 'はい' };
      if (i === 2 || i === 7) return { ...base, arrive: '09:00', start: '10:15', depart: '10:25' };
      if (i === 10) return { ...base, arrive: '08:10', appointed: '08:30', start: '10:15', end: '10:55', depart: '11:05', handler: '運転者', method: '手積み', side_work: '検品・仕分け', side_min: '20' };
      return { ...base, arrive: '09:00', start: '09:30', end: '09:52', depart: '10:00', handler: '荷主側' };
    });
  }

  function waitRowHtml(r, i) {
    const cells = W_COLS.map((c) => {
      const v = r[c.k] || '';
      if (c.sel) return `<td><select class="w-in" data-i="${i}" data-k="${c.k}" aria-label="${esc(c.t)}">${(c.sel.includes(v) || !v ? c.sel : [...c.sel, v]).map((o) => `<option value="${esc(o)}"${o === v ? ' selected' : ''}>${esc(o) || '—'}</option>`).join('')}</select></td>`;
      return `<td><input class="w-in" type="text" data-i="${i}" data-k="${c.k}" value="${esc(v)}" placeholder="${esc(c.ph || '')}" aria-label="${esc(c.t)}" autocomplete="off"></td>`;
    }).join('');
    return `<tr data-i="${i}"${W.bad.has(i) ? ' class="bad"' : ''}><td class="w-n">${i + 1}</td>${cells}<td><button type="button" class="w-del" data-w="del" data-i="${i}" aria-label="${i + 1} 行目を消す">×</button></td></tr>`;
  }

  function waitErrsHtml() {
    if (W.err) return `<p class="w-err">${esc(W.err)}</p>`;
    const r = W.res;
    if (!r) return '';
    const rowNotes = r.rows.filter((x) => x.notes && x.notes.length).map((x) => x.notes.map((m) => `<div>表の ${(W.idxMap[x.row - 1] ?? x.row - 1) + 1} 行目: ${esc(m)}</div>`).join('')).join('');
    const notesHtml = rowNotes ? `<div class="w-note">${rowNotes}</div>` : '';
    if (!r.errors.length) return notesHtml;
    return notesHtml + `<div class="w-errs"><b>読めなかった行 ${new Set(r.errors.map((e) => e.row)).size} 行（計算から外しています）</b>` +
      r.errors.map((e) => `<div>表の ${(W.idxMap[e.row - 1] ?? e.row - 1) + 1} 行目: ${esc(e.message)}</div>`).join('') + '</div>';
  }

  function waitHtml() {
    waitInit();
    const years = []; for (let y = 2024; y <= 2027; y++) years.push(y);
    return `<p class="w-lead">運送を頼まれた相手ごとに、荷待ち・荷役の記録を入れます。1 行 = 1 か所での 1 回の荷待ち・荷役です。</p>
      <div class="w-bar"><label>年（M/D で入れた日付に使います）<select id="w-year" class="ctl sm">${years.map((y) => `<option value="${y}"${y === W.year ? ' selected' : ''}>${y} 年</option>`).join('')}</select></label>
        <button type="button" class="btn-sm" data-w="example">例を入れる</button><button type="button" class="btn-sm" data-w="clear">表を空にする</button></div>
      <div class="w-tw"><table class="w-tbl" aria-label="荷待ち・荷役の記録の表"><thead><tr><th>#</th>${W_COLS.map((c) => `<th style="min-width:${c.w}px">${esc(c.t)}</th>`).join('')}<th></th></tr></thead>
        <tbody id="w-tbody">${W.rows.map(waitRowHtml).join('')}</tbody></table></div>
      <div class="w-bar"><button type="button" class="btn-sm" data-w="add">行を足す</button><small>時刻は HH:MM（日付をまたぐ時刻は翌日として扱います）。荷役終了は荷役をしたときに入れます。指定の到着時刻と附帯業務は、あるときだけ入れます。</small></div>
      <div class="w-bar" id="w-bulk">
        <label>相手<select id="w-bulk-partner" class="ctl sm" aria-label="まとめて変える相手">${bulkOptions()}</select></label>
        <button type="button" class="btn-sm" data-w="fault-yes">この相手の行をすべて荷主等の都合にする</button>
        <button type="button" class="btn-sm" data-w="fault-no">この相手の行をすべて荷主等の都合でないにする</button>
        <button type="button" class="btn-sm" data-w="fill-partner">契約の相手を下の行に埋める</button>
        <button type="button" class="btn-sm" data-w="fill-class">車種を下の行に埋める</button>
        <small id="w-bulk-msg">${esc(W.bulkMsg)}</small></div>
      <p class="w-note" id="w-paste-note">${esc(W.pasteNote).replace(/\n/g, '<br>')}</p>
      <div id="w-errs-box">${waitErrsHtml()}</div>
      <details class="o-det" open><summary>Excel から貼り付ける</summary>
        <p class="w-small">Excel の表をコピーして貼り付けます（タブ区切り）。1 行目が見出しなら、列の順は問わず見出しの名前で読みます。見出しが無いときだけ、列の順は上の表と同じにしてください。日付は 10月5日・R8.10.5・2026/10/5、時刻は 9:00:00・9時00分・0900、車種は 4t車・4トン・中型車、荷主等の都合は ○ / × でも読みます。</p>
        <textarea id="w-paste" class="w-paste" rows="3" placeholder="ここに貼り付け" aria-label="Excel から貼り付ける欄"></textarea>
        <div class="w-bar"><button type="button" class="btn-sm" data-w="paste">貼り付けた内容を表に入れる</button><span id="w-paste-msg" class="w-small">${esc(W.pasteMsg)}</span></div></details>`;
  }

  function partnerRows(name) { return W.res ? W.res.rows.filter((r) => r.partner === name) : []; }

  function draftHtml(p) {
    const t = W.res.tariff, rows = partnerRows(p.partner);
    const classes = [...new Set(rows.map((r) => r.vehicle_class))];
    const methods = [...new Set(rows.filter((r) => r.handler === '運転者' && r.method).map((r) => r.method))];
    const rec = rows.map((r) => `<tr><td>${esc(r.date)}</td><td>${esc(r.vehicle_no)}</td><td>${esc(r.vehicle_class)}</td><td>${esc(r.place)}</td><td>${esc(r.arrive)}</td><td>${esc(r.start)}</td><td>${esc(r.end)}</td><td>${esc(r.handler)}</td>
      <td class="num">${r.wait_min}${r.handler === '荷主側' && r.wait_min > 0 && r.end ? '<small>荷主側の荷役を待機に含む</small>' : ''}${r.post_wait_min > 0 ? `<small>荷役のあとの待ち ${r.post_wait_min} 分を含む</small>` : ''}${r.wait_base_min > 0 ? `<small>（指定 ${esc(r.appointed)} から）</small>` : ''}</td><td class="num">${r.handling_min}</td><td>${esc(r.side_work)}${r.side_min ? ` ${r.side_min} 分` : ''}</td><td class="num">${n0(r.amount)}</td></tr>`).join('');
    const rate = classes.map((c) => `<tr><td>${esc(t.class_labels[c] || c)}</td><td class="num">${n0(t.waiting.normal[c])}</td><td class="num">${n0(t.waiting.over_2h[c])}</td>${methods.map((m) => `<td class="num">${n0(t.handling[m].normal[c])} / ${n0(t.handling[m].over_2h[c])}</td>`).join('')}</tr>`).join('');
    return `<h3>荷待ち・荷役に関する協議のお願い（下書き）</h3>
      <dl class="w-dl"><dt>相手</dt><dd>${esc(p.partner)} 御中</dd><dt>期間</dt><dd>${esc(p.date_from)} 〜 ${esc(p.date_to)}（${p.count} 回）</dd>
        <dt>差出人</dt><dd>（運送会社の名称・担当者を書き入れてください）</dd></dl>
      <h4>記録の一覧</h4>
      <div class="w-recw"><table class="w-rec"><thead><tr><th>日付</th><th>車番</th><th>車種</th><th>場所</th><th>到着</th><th>荷役開始</th><th>荷役終了</th><th>荷役をした人</th><th>待機（分）</th><th>荷役（分）</th><th>附帯業務（記録のみ）</th><th>参考額（円）</th></tr></thead><tbody>${rec}</tbody></table></div>
      <h4>参考額と計算の根拠</h4>
      <p>参考額の合計 <b class="num">${yen(p.amount)}</b>（税抜。待機時間料 ${yen(p.wait_fee)}、積込料・取卸料 ${yen(p.handling_fee)}）。待機の合計 ${hm(p.wait_minutes)}、運転者の荷役の合計 ${hm(p.handling_minutes)}。</p>
      <p class="w-small">標準的運賃（${esc(t.applies_from)} 告示）の別紙の表に当てた額です。待機は到着時刻（指定の到着時刻があって実際の到着が早いときは指定の時刻）から数え、最初の ${t.free_minutes} 分を差し引き、${t.unit_minutes} 分までごとに通常の額、到着から ${t.threshold_minutes / 60} 時間を超える部分は ${t.unit_minutes} 分までごとに 2 時間超の額です（Q&A 集 Q29）。荷主側が荷役をした時間も待機に入れています。出典: ${esc(t.source_url)}</p>
      <p class="w-small">${esc(W_BASIS_QUOTE)}</p>
      ${rows.some((r) => r.post_wait) ? `<p class="w-small">${esc(W_POST_WAIT_NOTE)}</p>` : ''}
      <div class="w-recw"><table class="w-rec"><thead><tr><th>使った車種の単価（税抜・円）</th><th>待機時間料<small>${t.unit_minutes} 分までごと</small></th><th>2 時間超</th>${methods.map((m) => `<th>${esc(m)}の積込料・取卸料<small>通常 / 2 時間超</small></th>`).join('')}</tr></thead><tbody>${rate}</tbody></table></div>
      <h4>次の契約の書面（運送引受書）に入れる欄の案</h4>
      <ul class="w-list"><li>待機時間料: 荷主等の都合による待機について、到着（指定の到着時刻があるときは、その時刻）から ${t.free_minutes} 分を超えた時間に、${t.unit_minutes} 分までごとに上の単価（到着から 2 時間を超える部分は 2 時間超の単価）。</li>
        <li>積込料・取卸料: 運転者が荷役をしたとき、荷役の時間に ${t.unit_minutes} 分までごとに上の単価（手積み / フォークリフト等で別）。</li>
        <li>荷主等の都合でない待機（予期せぬ渋滞など）は対象にしません。</li></ul>
      <p class="w-sentence">下記の荷待ち・荷役について、標準的運賃の表に当てた参考額は計 ${n0(p.amount)} 円（税抜）です。次回からの契約の書面に、待機時間料と積込料・取卸料の扱いを記載いただけるようご相談します。</p>`;
  }

  const processHtml = () => `<aside class="w-process no-print" aria-label="協議の進め方"><b>協議の進め方</b><ul>${W_PROCESS.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></aside>`;

  function waitRightHtml() {
    const r = W.res;
    const protect = `<div class="w-protect"><b>守る線</b>${W_PROTECT.map((x) => `<p>${esc(x)}</p>`).join('')}</div>`;
    const basis = `<div class="w-basis"><b>根拠</b><ul>${W_BASIS.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>`;
    let mid;
    if (!r || !r.by_partner.length) {
      mid = '<p class="w-empty">まだ計算できる記録がありません。左の表に記録を入れるか、「例を入れる」を押してください。</p>';
    } else {
      const sel = r.by_partner.some((p) => p.partner === W.partner) ? W.partner : r.by_partner[0].partner;
      W.partner = sel;
      const p = r.by_partner.find((x) => x.partner === sel);
      const tb = r.by_partner.map((x) => `<tr class="${x.partner === sel ? 'on' : ''}" data-w="pick" data-p="${esc(x.partner)}"><td>${esc(x.partner)}</td><td class="num">${x.count}</td><td class="num">${hm(x.wait_minutes)}</td>
        <td class="num">${hm(x.handling_minutes)}</td><td class="num"><b>${n0(x.amount)}</b></td><td class="num">${x.over_1h_count}</td><td class="num">${x.over_2h_count}</td></tr>`).join('');
      mid = `<div class="w-sect"><div class="m-rh"><b>契約の相手ごと</b><span class="m-rsub">参考額は税抜・円</span></div>
        <table class="w-par"><thead><tr><th>契約の相手</th><th>回数</th><th title="荷主側の荷役を含む待機">待機の合計</th><th>運転者の荷役の合計</th><th>参考額</th><th title="待機（荷主側の荷役を含む）が 1 時間以上の回数">1 時間以上の荷待ち</th><th title="待機と運転者の荷役の合計が 2 時間以上の回数">2 時間以上の荷待ち・荷役</th></tr></thead><tbody>${tb}</tbody></table></div>
        <div class="w-sect"><div class="m-rh"><b>協議の書面の下書き</b><span class="m-rsub no-print">上の表の行を押すと相手を切り替えます</span></div>
          <div class="w-bar no-print"><button type="button" class="btn-primary sm" data-w="print">印刷して残す</button><button type="button" class="btn-sm" data-w="csv">計算結果を CSV で書き出す</button></div>
          <div class="w-draftwrap"><article id="wait-doc" class="w-doc">${draftHtml(p)}</article>${processHtml()}</div></div>`;
    }
    return `<header><div class="m-rh"><b>荷待ち・荷役の記録から協議の書面を作る</b><span class="m-rsub">${r ? esc(`標準的運賃 ${r.tariff.applies_from} 告示`) : ''}</span></div></header>
      <div class="w-rbody">${protect}${mid}${basis}</div>`;
  }

  function collectRows() {
    W.idxMap = [];
    const rows = [];
    W.rows.forEach((r, i) => { if (!isBlank(r)) { rows.push({ ...r }); W.idxMap.push(i); } });
    return rows;
  }

  async function waitCalc() {
    const rows = collectRows(), tok = ++W.tok;
    if (!rows.length) { W.res = null; W.err = ''; waitRefresh(); return; }
    try {
      const res = await Work.api('/api/waiting/calc', { year: W.year, rows });
      if (tok !== W.tok) return;
      W.res = res; W.err = '';
    } catch (e) { if (tok !== W.tok) return; W.res = null; W.err = e.message; }
    waitRefresh();
  }

  function waitRefresh() {
    W.bad = new Set(W.res ? W.res.errors.map((e) => W.idxMap[e.row - 1]) : []);
    document.querySelectorAll('#w-tbody tr').forEach((tr) => tr.classList.toggle('bad', W.bad.has(Number(tr.dataset.i))));
    const box = q('#w-errs-box');
    if (box) box.innerHTML = waitErrsHtml();
    bulkRefresh();
    renderRight();
  }

  const waitSoon = () => { clearTimeout(W.tm); W.tm = setTimeout(waitCalc, 350); };

  function waitCsv() {
    const heads = ['日付', '車番', '車種', '契約の相手', '場所', '到着', '荷役開始', '荷役終了', '出発', '荷役をした人', '荷役の方法', '荷主等の都合の待機か', '指定の到着時刻', '附帯業務の内容', '附帯業務の分', '荷役のあとの待ちを荷主等の都合として数える', '待機の数え始め', '待機（分）', '運転者の荷役（分）',
      '待機時間料 通常（回）', '待機時間料 2時間超（回）', '待機時間料（円）', '積込料・取卸料 通常（回）', '積込料・取卸料 2時間超（回）', '積込料・取卸料（円）', '参考額（円・税抜）'];
    const cell = (v) => { const t = String(v ?? ''); return /[",\r\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t; };
    const lines = [heads, ...W.res.rows.map((r) => [r.date, r.vehicle_no, r.vehicle_class, r.partner, r.place, r.arrive, r.start, r.end, r.depart, r.handler, r.method, r.shipper_fault ? 'はい' : 'いいえ', r.appointed, r.side_work, r.side_min || '', r.post_wait ? 'はい' : '', r.wait_from,
      r.wait_min, r.handling_min, r.wait_normal_units, r.wait_over_units, r.wait_fee, r.handling_normal_units, r.handling_over_units, r.handling_fee, r.amount])];
    const blob = new Blob(['﻿' + lines.map((l) => l.map(cell).join(',')).join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'waiting_calc.csv';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  async function waitPaste() {
    const text = q('#w-paste').value;
    if (!text.trim()) { toast('貼り付ける欄が空です'); return; }
    try {
      const res = await Work.api('/api/waiting/calc', { year: W.year, text });
      const inp = res.input_rows || [];
      W.rows = inp.length ? inp.map((r) => ({ ...blankRow(), ...r })) : [blankRow(), blankRow(), blankRow()];
      W.pasteMsg = `${inp.length} 行を表に入れました`;
      W.pasteNote = ((res.defaulted || []).includes('shipper_fault') ? [W_DEFAULT_FAULT_NOTE] : []).concat(res.notes || []).join('\n');
    } catch (e) { W.pasteMsg = e.message; }
    renderLeft();
    waitCalc();
  }

  function bindWait() {
    const root = q('#own-view'), right = q('#own-right');
    root.addEventListener('input', (e) => {
      const t = e.target;
      if (t.classList && t.classList.contains('w-in')) { W.rows[Number(t.dataset.i)][t.dataset.k] = t.value; waitSoon(); }
    });
    root.addEventListener('change', (e) => {
      const t = e.target;
      if (t.id === 'w-year') { W.year = Number(t.value); waitCalc(); }
    });
    for (const el of [root, right]) {
      el.addEventListener('click', (e) => {
        const b = e.target.closest('[data-w]');
        if (!b) return;
        const a = b.dataset.w;
        if (a === 'example') { W.rows = exampleRows(); renderLeft(); waitCalc(); }
        else if (a === 'clear') { W.pasteNote = ''; W.rows = [blankRow(), blankRow(), blankRow()]; W.res = null; W.err = ''; W.bad = new Set(); renderLeft(); renderRight(); }
        else if (a === 'add') { W.rows.push(blankRow()); renderLeft(); const ins = document.querySelectorAll('#w-tbody tr:last-child .w-in'); if (ins[0]) ins[0].focus(); }
        else if (a === 'del') { W.rows.splice(Number(b.dataset.i), 1); if (!W.rows.length) W.rows.push(blankRow()); renderLeft(); waitCalc(); }
        else if (a === 'paste') waitPaste();
        else if (a === 'fault-yes') bulkFault('はい');
        else if (a === 'fault-no') bulkFault('いいえ');
        else if (a === 'fill-partner') fillDown('partner', '契約の相手');
        else if (a === 'fill-class') fillDown('vehicle_class', '車種');
        else if (a === 'pick') { W.partner = b.dataset.p; renderRight(); }
        else if (a === 'print') window.print();
        else if (a === 'csv') { if (W.res && W.res.rows.length) waitCsv(); }
      });
    }
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
    syncMode();
    renderLeft();
    renderRight();
    writeHash();
  }

  // 荷待ちのタブでは、左を広げて表を入れ、右に相手ごとの表と書面の下書きを出す（own.css の body.own-wait）
  function syncMode() { document.body.classList.toggle('own-wait', O.active && O.tab === 'wait'); }

  /* ------------------------------------------------------------ 見方の出入り */

  function fit(animate) {
    const rw = (q('#own-right') && q('#own-right').offsetWidth) || 540;
    map.fitBounds([[134.2, 34.1], [140.6, 36.2]], { padding: { top: 70, bottom: 150, left: 500, right: rw + 60 }, duration: animate ? 1500 : 0, essential: true });
  }

  async function enter() {
    O.active = true;
    syncMode();
    await load();
    renderLeft(); renderRight(); syncMarkers();
    fit(true);
  }
  function leave() { O.active = false; syncMode(); syncMarkers(); }

  async function init() {
    await load();
    if (!O.loaded) return;
    bindLeft();
    bindWait();
    renderLeft(); renderRight();
  }

  function hashParams(p) { if (O.tab !== 'import') p.set('ot', O.tab); }
  function readHash(p) { const t = p.get('ot'); if (['import', 'confirm', 'insight', 'wait'].includes(t)) O.tab = t; }
  function writeHash() {
    if (typeof S === 'undefined' || S.view !== 'own') return;
    const p = new URLSearchParams();
    p.set('v', 'own');
    hashParams(p);
    history.replaceState(null, '', '#' + p.toString());
  }

  return { init, enter, leave, layers, market: () => O.market, setTab, hashParams, readHash, state: O, fit };
})();
