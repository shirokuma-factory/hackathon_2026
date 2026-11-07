/* 新規モーション用の共通部品。window.__seek(ms) だけで絵が決まる（時間の純関数）。 */
(function () {
  var NS = 'http://www.w3.org/2000/svg';
  function bez(a, b, c, d) {
    return function (x) {
      if (x <= 0) return 0; if (x >= 1) return 1;
      var lo = 0, hi = 1, t = x;
      for (var i = 0; i < 32; i++) {
        var cx = 3 * a * t * (1 - t) * (1 - t) + 3 * c * t * t * (1 - t) + t * t * t;
        if (Math.abs(cx - x) < 1e-6) break;
        if (cx < x) lo = t; else hi = t;
        t = (lo + hi) / 2;
      }
      return 3 * b * t * (1 - t) * (1 - t) + 3 * d * t * t * (1 - t) + t * t * t;
    };
  }
  var E = { out: bez(.16, 1, .3, 1), mov: bez(.65, 0, .35, 1), inn: bez(.7, 0, .84, 1) };
  function P(s, t0, d, e) {
    var x = (s - t0) / d; x = x < 0 ? 0 : x > 1 ? 1 : x;
    return (e || E.out)(x);
  }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function hex(c) { return [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)]; }
  function mix(c1, c2, t) {
    var a = hex(c1), b = hex(c2);
    return 'rgb(' + Math.round(lerp(a[0], b[0], t)) + ',' + Math.round(lerp(a[1], b[1], t)) + ',' + Math.round(lerp(a[2], b[2], t)) + ')';
  }
  function el(tag, attrs, parent) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function div(parent, css, html) {
    var d = document.createElement('div');
    d.style.cssText = 'position:absolute;white-space:nowrap;' + (css || '');
    if (html != null) d.innerHTML = html;
    parent.appendChild(d);
    return d;
  }
  /* 数字は 1 桁ずつ固定幅の箱に入れる（桁のがたつきを防ぐ） */
  function cells(str) {
    var o = '';
    for (var i = 0; i < str.length; i++) {
      var ch = str[i];
      if (ch === ',') o += '<span style="display:inline-block;width:.3em;text-align:center">,</span>';
      else if (ch === '.') o += '<span style="display:inline-block;width:.3em;text-align:center">.</span>';
      else o += '<span style="display:inline-block;width:.6em;text-align:center">' + ch + '</span>';
    }
    return o;
  }
  function show(n, p, dx, dy) {
    n.style.opacity = p;
    n.style.transform = 'translate(' + (dx || 0) * (1 - p) + 'px,' + (dy || 0) * (1 - p) + 'px)';
  }
  /* トレーラー（荷台と運転席）の線のアイコン。96x52。 */
  function truck(parent, sw) {
    var g = el('g', {}, parent), s = sw || 3.5;
    var o = {
      g: g,
      box: el('rect', { x: 3, y: 4, width: 58, height: 33, rx: 5 }, g),
      cab: el('path', { d: 'M66 13 H80 L92 25 V37 H66 Z' }, g),
      win: el('path', { d: 'M71.5 18.5 H78 L84 25 H71.5 Z' }, g),
      ch: el('path', { d: 'M3 37 H92' }, g),
      w: [20, 48, 79].map(function (cx) { return el('circle', { cx: cx, cy: 44, r: 6 }, g); })
    };
    var all = [o.box, o.cab, o.win, o.ch].concat(o.w);
    all.forEach(function (n) {
      n.setAttribute('fill', 'none'); n.setAttribute('stroke-width', s);
      n.setAttribute('stroke-linecap', 'round'); n.setAttribute('stroke-linejoin', 'round');
    });
    o.win.setAttribute('stroke-width', s * .6);
    o.all = all;
    /* color: 線の色, gap: 点線の空き(0 で実線), tint: 荷台と運転席の色づけ(0〜1), tintColor */
    o.set = function (color, gap, tint, tintColor) {
      all.forEach(function (n) {
        n.setAttribute('stroke', color);
        if (gap > 0.05 && o.w.indexOf(n) < 0) n.setAttribute('stroke-dasharray', '5 ' + (1 + gap * .6));
        else n.removeAttribute('stroke-dasharray');
      });
      o.w.forEach(function (n) { n.setAttribute('fill', 'rgba(5,8,12,.92)'); });
      var f = tint > 0 ? 'rgba(' + hex(tintColor || '#f0c04a').join(',') + ',' + tint + ')' : 'none';
      o.box.setAttribute('fill', f); o.cab.setAttribute('fill', f);
    };
    return o;
  }
  /* 箱（荷物）の線のアイコン。64x64。 */
  function parcel(parent, sw) {
    var g = el('g', {}, parent), s = sw || 3.5;
    var a = el('path', { d: 'M32 4 L58 18 V46 L32 60 L6 46 V18 Z' }, g);
    var b = el('path', { d: 'M6 18 L32 32 L58 18 M32 32 V60' }, g);
    var c = el('path', { d: 'M19 11 L45 25' }, g);
    [a, b, c].forEach(function (n) {
      n.setAttribute('fill', 'none'); n.setAttribute('stroke-width', s);
      n.setAttribute('stroke-linecap', 'round'); n.setAttribute('stroke-linejoin', 'round');
    });
    return { g: g, set: function (color) { [a, b, c].forEach(function (n) { n.setAttribute('stroke', color); }); } };
  }
  /* 印: 行き = 白い実線、帰り = 点線、帰りが黄色で埋まる */
  function motif(parent, x1, xm, x2, y, sw) {
    var w = sw || 6, da = Math.max(.1, 14 - w) + ' ' + (16 + w);
    var solid = el('line', { x1: x1, y1: y, x2: x1, y2: y, stroke: '#ffffff', 'stroke-width': w, 'stroke-linecap': 'round' }, parent);
    var dash = el('line', { x1: xm, y1: y, x2: xm, y2: y, stroke: '#3a4757', 'stroke-width': w, 'stroke-linecap': 'round', 'stroke-dasharray': da }, parent);
    var fill = el('line', { x1: xm, y1: y, x2: xm, y2: y, stroke: '#f0c04a', 'stroke-width': w, 'stroke-linecap': 'round', 'stroke-dasharray': da }, parent);
    return {
      set: function (ps, pd, pf) {
        solid.setAttribute('x2', lerp(x1, xm, ps));
        dash.setAttribute('x2', lerp(xm, x2, pd));
        fill.setAttribute('x2', lerp(xm, x2, pf));
        solid.style.opacity = ps > 0 ? 1 : 0; dash.style.opacity = pd > 0 ? 1 : 0; fill.style.opacity = pf > 0 ? 1 : 0;
      }
    };
  }
  /* 土台: 3 層（背景・図・文字）に別の寄せ、最後の 0.3 秒で上へ 12px 動きながら消える */
  function setup(D, render) {
    var stage = document.getElementById('stage');
    var L = { bg: document.getElementById('bg'), fig: document.getElementById('fig'), txt: document.getElementById('txt') };
    var amt = { bg: .02, fig: .04, txt: .06 };
    window.__seek = function (ms) {
      var s = ms / 1000;
      for (var k in L) {
        var sc = 1 + amt[k] * (s / 12) + amt[k] * .2 * (1 - P(s, 0, 1.4, E.out));
        L[k].style.transform = 'scale(' + sc + ')';
      }
      render(s);
      var x = P(s, D - .3, .3, E.inn);
      stage.style.opacity = 1 - x;
      stage.style.transform = 'translateY(' + (-12 * x) + 'px)';
    };
    window.__seek(0);
    (document.fonts ? document.fonts.ready : Promise.resolve()).then(function () { window.__seek(0); window.__ready = true; });
  }
  window.K = { E: E, P: P, lerp: lerp, mix: mix, el: el, div: div, cells: cells, show: show, truck: truck, parcel: parcel, motif: motif, setup: setup, NS: NS };
})();
