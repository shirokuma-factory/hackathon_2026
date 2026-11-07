/* トレーラーの図の共通部品。window.TK を使う。
   run(D, render) : window.__seek(ms) を作る。奥行きの寄せ（背景 1.02 / 図 1.04 / 文字 1.06、12 秒あたり）と最後 0.3 秒の退場もここで行う */
(function () {
  var TK = window.TK = {}, NS = 'http://www.w3.org/2000/svg';
  function bez(x1, y1, x2, y2) {
    return function (t) {
      if (t <= 0) return 0; if (t >= 1) return 1;
      var lo = 0, hi = 1, x = 0.5;
      for (var i = 0; i < 32; i++) {
        x = (lo + hi) / 2;
        var cx = 3 * (1 - x) * (1 - x) * x * x1 + 3 * (1 - x) * x * x * x2 + x * x * x;
        if (cx < t) lo = x; else hi = x;
      }
      x = (lo + hi) / 2;
      return 3 * (1 - x) * (1 - x) * x * y1 + 3 * (1 - x) * x * x * y2 + x * x * x;
    };
  }
  TK.out = bez(0.16, 1, 0.3, 1);
  TK.mov = bez(0.65, 0, 0.35, 1);
  TK.inn = bez(0.7, 0, 0.84, 0);
  TK.pr = function (s, a, b) { return Math.min(1, Math.max(0, (s - a) / (b - a))); };
  TK.$ = function (id) { return document.getElementById(id); };
  TK.svg = function (tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };
  TK.rr = function (x, y, w, h, r) {
    return 'M' + (x + r) + ' ' + y + ' H' + (x + w - r) + ' A' + r + ' ' + r + ' 0 0 1 ' + (x + w) + ' ' + (y + r) +
      ' V' + (y + h - r) + ' A' + r + ' ' + r + ' 0 0 1 ' + (x + w - r) + ' ' + (y + h) + ' H' + (x + r) +
      ' A' + r + ' ' + r + ' 0 0 1 ' + x + ' ' + (y + h - r) + ' V' + (y + r) + ' A' + r + ' ' + r + ' 0 0 1 ' + (x + r) + ' ' + y + ' Z';
  };
  TK.circ = function (cx, cy, r) {
    return 'M' + (cx - r) + ' ' + cy + ' A' + r + ' ' + r + ' 0 1 1 ' + (cx + r) + ' ' + cy + ' A' + r + ' ' + r + ' 0 1 1 ' + (cx - r) + ' ' + cy;
  };
  /* 線を引く（pathLength=1 の要素を 0〜1 で描く） */
  TK.draw = function (el, p) {
    if (p <= 0.0005) { el.style.opacity = 0; return; }
    el.style.opacity = 1;
    if (p >= 0.9995) { el.style.strokeDasharray = 'none'; el.style.strokeDashoffset = '0'; return; }
    el.style.strokeDasharray = '1 1';
    el.style.strokeDashoffset = String(1 - p);
  };
  /* トレーラーの線画。ローカル座標は幅 974 x 高さ 334。箱 (0,0)-(700,250)、運転席 716〜974、車輪の中心 y=300 */
  TK.CAB = 'M716 266 L716 72 Q716 50 738 50 L850 50 Q872 50 886 68 L956 166 Q968 184 970 204 L974 266';
  TK.WIN = 'M740 80 L846 80 Q858 80 866 92 L908 158 L740 158 Z';
  TK.trailer = function (parent, o) {
    var g = TK.svg('g', { transform: 'translate(' + o.x + ' ' + o.y + ') scale(' + o.s + ')' }, parent);
    var sw = o.sw / o.s, T = { g: g, s: o.s, x: o.x, y: o.y, sw: sw };
    function P(d, extra) {
      var a = { fill: 'none', stroke: o.color || '#fff', 'stroke-width': sw, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', pathLength: 1, d: d };
      for (var k in extra) a[k] = extra[k];
      var e = TK.svg('path', a, g); e.style.opacity = 0; return e;
    }
    T.box = P(TK.rr(0, 0, 700, 250, 18));
    T.cab = P(TK.CAB);
    T.win = P(TK.WIN);
    T.rail = P('M16 266 L974 266');
    T.wheels = [140, 240, 860].map(function (cx) { return P(TK.circ(cx, 300, 34)); });
    T.hubs = [140, 240, 860].map(function (cx) { return P(TK.circ(cx, 300, 11)); });
    return T;
  };
  /* 線画が自分で引かれていく。t0 から約 1.1 秒 */
  TK.trailerDraw = function (T, s, t0) {
    var P = TK.pr;
    TK.draw(T.box, TK.mov(P(s, t0, t0 + 0.6)));
    TK.draw(T.cab, TK.mov(P(s, t0 + 0.12, t0 + 0.7)));
    TK.draw(T.rail, TK.mov(P(s, t0 + 0.2, t0 + 0.7)));
    TK.draw(T.win, TK.mov(P(s, t0 + 0.4, t0 + 0.8)));
    T.wheels.forEach(function (w, i) { TK.draw(w, TK.mov(P(s, t0 + 0.4 + i * 0.07, t0 + 0.85 + i * 0.07))); });
    T.hubs.forEach(function (w, i) { TK.draw(w, TK.mov(P(s, t0 + 0.7 + i * 0.07, t0 + 1.0 + i * 0.07))); });
  };
  /* 文字を 1 字ずつの覆い（マスク）に分ける。戻り値は中身の要素の配列 */
  TK.split = function (el) {
    var t = el.textContent, out = [];
    el.textContent = '';
    Array.prototype.forEach.call(t, function (c) {
      var m = document.createElement('span'); m.className = 'mk';
      var i = document.createElement('span'); i.className = 'mi'; i.textContent = c;
      m.appendChild(i); el.appendChild(m); out.push(i);
    });
    return out;
  };
  TK.reveal = function (chars, s, t0, stag, dur) {
    chars.forEach(function (c, i) {
      var k = TK.out(TK.pr(s, t0 + i * stag, t0 + i * stag + dur));
      c.style.transform = 'translateY(' + ((1 - k) * 115) + '%)';
    });
  };
  /* ふつうの要素が下から上がって現れる */
  TK.rise = function (el, s, t0, dur, dy) {
    var k = TK.out(TK.pr(s, t0, t0 + (dur || 0.5)));
    el.style.opacity = k;
    el.style.transform = 'translateY(' + ((dy === undefined ? 16 : dy) * (1 - k)) + 'px)';
  };
  TK.run = function (D, render) {
    var stage = TK.$('stage'), bg = TK.$('bg'), fig = TK.$('fig'), txt = TK.$('txt');
    window.__seek = function (ms) {
      var s = ms / 1000, t = s / 12;
      bg.style.transform = 'scale(' + (1 + 0.02 * t) + ')';
      fig.style.transform = 'scale(' + (1 + 0.04 * t) + ')';
      txt.style.transform = 'scale(' + (1 + 0.06 * t) + ')';
      var o = TK.pr(s, D - 0.3, D - 1 / 30), e = TK.inn(o);
      stage.style.opacity = 1 - o;
      stage.style.transform = 'translateY(' + (-12 * e) + 'px)';
      render(s);
    };
    window.__seek(0);
    document.fonts.ready.then(function () { window.__seek(0); window.__ready = true; });
  };
})();
