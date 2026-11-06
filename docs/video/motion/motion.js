/* 動く図の共通の動き。window.__seek(ms) で、その時刻の見た目を決まった値で描く（実時間では動かさない）。
   data-at="秒"            : その時刻から 0.3 秒で現れる（下から 12px）
   data-grow="開始秒,秒数"  : 棒が左から伸びる（幅は親の .fill の width）
   data-ul="秒"            : 黄の下線を 0.3 秒で左から引く
   data-count="開始,終了,終値,書式" : 数え上げ。書式 f2 は小数 2 桁、int は 3 桁区切り、plain は整数
   data-fill="秒" / data-empty="秒" : 小さな四角を青で埋める／黄の枠だけにする */
(function () {
  var D = parseFloat(document.body.dataset.duration);
  var LAST = D - 1 / 30; // 最後のコマで不透明度が 0 になるようにする
  var ease = function (t) { return 1 - Math.pow(1 - t, 3); };
  var pr = function (s, a, b) { return Math.min(1, Math.max(0, (s - a) / (b - a))); };
  var $$ = function (q) { return Array.prototype.slice.call(document.querySelectorAll(q)); };
  function fmt(v, f) {
    if (f === 'f2') return v.toFixed(2);
    if (f === 'int') return Math.round(v).toLocaleString('en-US');
    return String(Math.round(v));
  }
  window.__seek = function (ms) {
    var s = ms / 1000;
    var e = ease(pr(s, 0, 0.35));
    var panel = document.getElementById('panel');
    panel.style.opacity = e * (1 - pr(s, D - 0.3, LAST));
    panel.style.transform = 'translateY(' + (24 * (1 - e)) + 'px)';
    $$('[data-at]').forEach(function (el) {
      var a = parseFloat(el.dataset.at);
      var k = ease(pr(s, a, a + 0.3));
      el.style.opacity = k;
      el.style.transform = 'translateY(' + (12 * (1 - k)) + 'px)';
    });
    $$('[data-grow]').forEach(function (el) {
      var a = el.dataset.grow.split(',').map(parseFloat);
      el.style.transform = 'scaleX(' + ease(pr(s, a[0], a[0] + a[1])) + ')';
    });
    $$('[data-ul]').forEach(function (el) {
      var a = parseFloat(el.dataset.ul);
      el.style.transform = 'scaleX(' + ease(pr(s, a, a + 0.3)) + ')';
    });
    $$('[data-count]').forEach(function (el) {
      var a = el.dataset.count.split(',');
      var v = parseFloat(a[2]) * ease(pr(s, parseFloat(a[0]), parseFloat(a[1])));
      el.textContent = fmt(v, a[3]);
    });
    $$('[data-fill]').forEach(function (el) {
      var on = s >= parseFloat(el.dataset.fill);
      var em = el.dataset.empty !== undefined && s >= parseFloat(el.dataset.empty);
      el.className = 'sq' + (em ? ' empty' : on ? ' on' : '');
    });
  };
  window.__seek(0);
  document.fonts.ready.then(function () { window.__seek(0); window.__ready = true; });
})();
