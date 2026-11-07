"""最初の画面（ビューポート）に見えている文字数・操作部品の数を数える（SPEC §44 追記の予算の試験で使う）。
地図の canvas と出典（maplibre の attribution）は数えない。文字は、親が見えていて画面に交わるテキストノードを足す。

主ボタン（.btn-primary）の数え方: 同じ重みの選択肢を並べた「選択の組」は 1 つの主ボタンとして数える。
組は data-choice-group を付けた要素で表し、その中の主ボタンは何個あっても 1 と数える（2026-11-07）。
今の例は最初の画面の手順 1（荷主の方 / 運送会社の方）。どちらも同じ重みの入口で、片方を副ボタンに下げると
もう片方が「おすすめ」に見えるため、2 つの役を 1 つの選択として測る。組の外の主ボタンは 1 つずつ数える。"""

COUNT_JS = r"""(function () {
  const vw = innerWidth, vh = innerHeight;
  const skip = (e) => e.closest('canvas, .maplibregl-ctrl-attrib, .maplibregl-marker, #attr-toggle, #toast, script, style, [hidden], [inert]');
  const shown = (e) => {
    for (let n = e; n && n.nodeType === 1; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
      if (n.tagName === 'DETAILS' && !n.open && e !== n && !(e.closest('summary') && e.closest('summary').parentElement === n)) return false;   // 閉じた詳細の中身
    }
    return true;
  };
  const inView = (r) => r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0 && r.left < vw && r.top < vh;
  let chars = 0, blocks = new Set(), samples = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let t = w.nextNode(); t; t = w.nextNode()) {
    const s = t.nodeValue.replace(/\s+/g, ' ').trim();
    if (!s) continue;
    const p = t.parentElement;
    if (!p || skip(p) || !shown(p)) continue;
    const range = document.createRange(); range.selectNodeContents(t);
    if (!inView(range.getBoundingClientRect())) continue;
    chars += s.length; blocks.add(p); samples.push(s);
  }
  const ctl = [...document.querySelectorAll('button, a[href], input, select, textarea, summary, [role=tab]')].filter((e) => !skip(e) && shown(e) && inView(e.getBoundingClientRect()) && e.type !== 'hidden');
  const prim = ctl.filter((e) => e.classList.contains('btn-primary'));
  const groups = new Set(); let primary = 0;
  for (const e of prim) { const g = e.closest('[data-choice-group]'); if (!g) primary++; else if (!groups.has(g)) { groups.add(g); primary++; } }
  return JSON.stringify({ chars, blocks: blocks.size, controls: ctl.length, primary, samples });
})()"""
