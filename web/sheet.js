'use strict';
/* 狭い画面（幅 600px 以下、SPEC §30.7）の下のパネル。
   市場を見る・送り方を比べるは、パネルを 1 枚ずつ下から出し、下の 1 行のタブで切り替える。
   空きを探す・つなぐと自社の運行は、左右のパネルを 1 枚に縦に積む（style.css の .sheet-wrap）。
   地図の fitBounds の余白は、画面の大きさと下のパネルの高さで決める（余白が地図より大きいと
   "Map cannot fit within canvas" の警告が出るため）。計算や判定はしない。表示の出し入れだけ。 */

const Sheet = (() => {
  const mq = window.matchMedia('(max-width: 600px)');
  const q = (s) => document.querySelector(s);
  const SHEET_RATIO = 0.44; // 下のパネルの高さ（画面の高さに対する割合）。地図は残りのうち上の切り替え（約 6%）を引いても 45% 以上見える

  // 見方ごとのタブ。[パネルの id, 名前, 出す条件（body のクラス。省略は常に）]
  const TABS = {
    market: [['m-right', '区間'], ['m-left', '条件と凡例'], ['m-summary', '数字'], ['layers', '重ねる'], ['m-credits', '出典']],
    compare: [['panel', '条件'], ['detail', '着地', 'has-detail'], ['layers', '表示'], ['legend', '凡例'], ['timebar', '時間', 'has-route']],
  };
  const PANELS = ['m-right', 'm-left', 'm-summary', 'm-credits', 'panel', 'layers', 'legend', 'detail', 'work', 'timebar'];
  const st = { pick: {}, prev: new Set(), key: '' };

  const narrow = () => mq.matches;
  const sheetPx = () => Math.round(innerHeight * SHEET_RATIO);
  const view = () => ['market', 'find', 'compare', 'own'].find((v) => document.body.classList.contains('view-' + v)) || 'market';

  // 地図に渡す余白。狭い画面は、上の切り替えと下のパネルを避ける。左右は区間の端の名前の札（幅 約 60px）が切れない広さ
  function pad() { return { top: 56, bottom: sheetPx() + 12, left: 40, right: 40 }; }

  // 余白が地図の大きさを超えないようにする（広い画面でも、地図の幅・高さの 85% までに収める）
  function clamp(p, w, h) {
    const o = { top: 0, bottom: 0, left: 0, right: 0, ...p };
    const fx = (o.left + o.right) / (w * 0.85), fy = (o.top + o.bottom) / (h * 0.85);
    const kx = fx > 1 ? 1 / fx : 1, ky = fy > 1 ? 1 / fy : 1;
    return { top: Math.floor(o.top * ky), bottom: Math.floor(o.bottom * ky), left: Math.floor(o.left * kx), right: Math.floor(o.right * kx) };
  }

  function patchMap(map) {
    const fix = (opts) => {
      const o = { ...(opts || {}) };
      const c = map.getContainer();
      const w = c.clientWidth || innerWidth, h = c.clientHeight || innerHeight;
      if (narrow()) o.padding = pad();
      if (o.padding && typeof o.padding === 'object') o.padding = clamp(o.padding, w, h);
      return o;
    };
    for (const name of ['fitBounds', 'cameraForBounds']) {
      const orig = map[name].bind(map);
      map[name] = (b, opts, ...rest) => orig(b, fix(opts), ...rest);
    }
  }

  const visible = (t) => !t[2] || document.body.classList.contains(t[2]);

  function defaultPick(v) {
    const b = document.body.classList;
    if (v === 'compare') return b.contains('has-detail') ? 'detail' : 'panel';
    return 'm-right';
  }

  function apply() {
    const b = document.body.classList;
    // body の class は値が変わるときだけ書き換える。classList.add / remove は値が同じでも属性を書き直し、
    // onBody の MutationObserver が呼ばれ続けて止まらなくなるため
    if (!narrow()) {
      if (b.contains('sheet-mode')) b.remove('sheet-mode');
      if (b.contains('sheet-tabs')) b.remove('sheet-tabs');
      PANELS.forEach((id) => { const e = q('#' + id); if (e) e.classList.remove('sh-on'); });
      q('#sheet-tabs').hidden = true;
      st.key = '';
      return;
    }
    if (!b.contains('sheet-mode')) b.add('sheet-mode');
    const v = view(), nav = q('#sheet-tabs');
    let on = [], tabs = [];
    if (v === 'compare' && b.contains('has-work')) on = ['work'];
    else if (TABS[v]) {
      tabs = TABS[v].filter(visible);
      let pick = st.pick[v];
      if (!tabs.some((t) => t[0] === pick)) pick = defaultPick(v);
      if (!tabs.some((t) => t[0] === pick)) pick = tabs[0][0];
      st.pick[v] = pick;
      on = [pick];
    }
    const key = v + '|' + on.join() + '|' + tabs.map((t) => t[0]).join() + '|' + (on.length ? st.pick[v] : '');
    if (key === st.key) return;
    st.key = key;
    PANELS.forEach((id) => { const e = q('#' + id); if (e) e.classList.toggle('sh-on', on.includes(id)); });
    const showTabs = tabs.length > 0;
    if (b.contains('sheet-tabs') !== showTabs) b.toggle('sheet-tabs', showTabs);
    nav.hidden = !showTabs;
    nav.innerHTML = tabs.map((t) => `<button type="button" data-sh="${t[0]}" aria-pressed="${t[0] === st.pick[v]}">${t[1]}</button>`).join('');
    if (on.includes('m-credits')) { const d = q('#m-credits-d'); if (d) d.open = true; }
  }

  // 選択や開閉で body のクラスが変わったとき、開くパネルを自動で切り替える
  function onBody() {
    const b = document.body.classList, v = view();
    const now = new Set(['has-detail', 'm-has-pair', 'has-route', 'has-work', 'view-' + v].filter((c) => b.contains(c)));
    const added = (c) => now.has(c) && !st.prev.has(c), removed = (c) => !now.has(c) && st.prev.has(c);
    if (v === 'compare') {
      if (added('has-detail')) st.pick.compare = 'detail';
      else if (removed('has-detail') && st.pick.compare === 'detail') st.pick.compare = 'panel';
    }
    if (v === 'market' && added('m-has-pair')) st.pick.market = 'm-right';
    if ([...now].some((c) => c.startsWith('view-') && !st.prev.has(c))) delete st.pick[v];
    st.prev = now;
    apply();
  }

  function scrollTo(sel) {
    if (!narrow()) return;
    const e = q(sel);
    if (e) setTimeout(() => e.scrollIntoView({ block: 'start' }), 60);
  }

  function init(map) {
    patchMap(map);
    q('#sheet-tabs').addEventListener('click', (e) => {
      const bt = e.target.closest('button[data-sh]');
      if (!bt) return;
      st.pick[view()] = bt.dataset.sh;
      st.key = '';
      apply();
    });
    new MutationObserver(onBody).observe(document.body, { attributes: true, attributeFilter: ['class'] });
    mq.addEventListener('change', () => { st.key = ''; apply(); });
    st.prev = new Set();
    onBody();
  }

  return { init, narrow, pad, clamp, scrollTo, apply };
})();
