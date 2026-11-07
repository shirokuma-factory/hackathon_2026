(function () {
  var D = FLOW_DATA, MODE = document.body.dataset.mode, DUR = parseFloat(document.body.dataset.duration);
  var cv = document.getElementById("c"), ctx = cv.getContext("2d");
  var W = 1920, H = 1080, SY = 410, MCY = 410, TAU = Math.PI * 2;
  var BG = "#05080c";
  var TMAX = D.tmax, TMAX_P = Math.pow(TMAX, 0.6);

  function rng(seed) { var a = seed >>> 0; return function () { a = (a + 0x6D2B79F5) >>> 0; var t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function ss(a, b, x) { x = clamp((x - a) / (b - a), 0, 1); return x * x * (3 - 2 * x); }
  function ease(x) { x = clamp(x, 0, 1); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function lerp(a, b, x) { return a + (b - a) * x; }

  // 外形の Path2D
  var outline = new Path2D();
  D.rings.forEach(function (r) { outline.moveTo(r[0][0], r[0][1]); for (var i = 1; i < r.length; i++) outline.lineTo(r[i][0], r[i][1]); outline.closePath(); });

  // 地図の中心（外形の外接矩形）
  var bx0 = 1e9, bx1 = -1e9;
  D.rings.forEach(function (r) { r.forEach(function (p) { if (p[0] < bx0) bx0 = p[0]; if (p[0] > bx1) bx1 = p[0]; }); });
  var CX = (bx0 + bx1) / 2, CY = MCY;

  // レーン: 進行方向の右へ膨らむ 2 次ベジェを、弧長で等間隔に引き直した表にする
  var NS = 200;
  var lanes = D.lanes.map(function (l, idx) {
    var A = D.pos[l.a], B = D.pos[l.b];
    var dx = B[0] - A[0], dy = B[1] - A[1], len = Math.hypot(dx, dy);
    var rx = -dy / len, ry = dx / len; // 進行方向の右（画面は y が下向き）
    var off = 3.5; // 起点と終点も右へ少しずらす
    var ax = A[0] + rx * off, ay = A[1] + ry * off, bx = B[0] + rx * off, by = B[1] + ry * off;
    var bend = len * 0.07 + 3;
    var cx = (ax + bx) / 2 + rx * bend * 2, cy = (ay + by) / 2 + ry * bend * 2;
    var raw = [], cum = [0], M = 400, i;
    for (i = 0; i <= M; i++) { var u = i / M, v = 1 - u; raw.push([v * v * ax + 2 * v * u * cx + u * u * bx, v * v * ay + 2 * v * u * cy + u * u * by]); }
    for (i = 1; i <= M; i++) cum.push(cum[i - 1] + Math.hypot(raw[i][0] - raw[i - 1][0], raw[i][1] - raw[i - 1][1]));
    var L = cum[M], pts = new Float32Array((NS + 1) * 2), j = 0;
    for (i = 0; i <= NS; i++) { var target = L * i / NS; while (j < M - 1 && cum[j + 1] < target) j++; var f = (target - cum[j]) / ((cum[j + 1] - cum[j]) || 1); pts[i * 2] = raw[j][0] + (raw[j + 1][0] - raw[j][0]) * f; pts[i * 2 + 1] = raw[j][1] + (raw[j + 1][1] - raw[j][1]) * f; }
    var q = Math.min(1, Math.pow(l.t, 0.6) / TMAX_P); // 太さと明るさの目安（0〜1）
    var n;
    if (MODE === "kago" && l.k === 1) n = 420; else if (MODE === "kago" && l.k === 2) n = Math.round(420 * D.kagoRev / D.kagoFwd);
    else n = Math.round(12 + 100 * Math.pow(l.t / TMAX, 0.62));
    if (MODE === "kago" && !l.k) n = Math.round(n * 0.8);
    if (MODE === "kago" && l.k) q = (l.k === 1 ? 0.8 : 0.8 * Math.pow(D.kagoRev / D.kagoFwd, 0.35));
    var R = rng(1000 + idx * 7919), p0 = new Float32Array(n), sp = new Float32Array(n), ja = new Float32Array(n), jp = new Float32Array(n), tr = new Float32Array(n);
    for (i = 0; i < n; i++) { p0[i] = R(); sp[i] = 95 * (0.75 + 0.5 * R()); ja[i] = (R() * 2 - 1); jp[i] = R(); tr[i] = 0.7 + 0.6 * R(); }
    return { idx: idx, k: l.k, t: l.t, a: l.a, b: l.b, L: L, pts: pts, q: q, n: n, p0: p0, sp: sp, ja: ja, jp: jp, tr: tr, bend: bend };
  });
  // 重い流れを後から描く。鹿児島の 2 本は最後
  var order = lanes.slice().sort(function (a, b) { var ka = a.k ? 1 : 0, kb = b.k ? 1 : 0; return (ka - kb) || (a.t - b.t); });

  function at(l, s, out) { // s: 0..1。out = [x, y, 右向き法線x, 法線y]
    var x = s * NS, i = Math.min(NS - 1, Math.floor(x)), f = x - i, p = l.pts;
    out[0] = p[i * 2] + (p[i * 2 + 2] - p[i * 2]) * f; out[1] = p[i * 2 + 1] + (p[i * 2 + 3] - p[i * 2 + 1]) * f;
    var tx = p[i * 2 + 2] - p[i * 2], ty = p[i * 2 + 3] - p[i * 2 + 1], tl = Math.hypot(tx, ty) || 1;
    out[2] = -ty / tl; out[3] = tx / tl;
    return out;
  }

  // カメラ
  var ik = D.prefs.indexOf("鹿児島県"), it = D.prefs.indexOf("東京都");
  var KP = D.pos[ik], TP = D.pos[it];
  var KCX = (KP[0] + TP[0]) / 2 - 6, KCY = (KP[1] + TP[1]) / 2 - 7, KZ = 2.9;
  function camera(t) {
    if (MODE === "jp") return { z: 1 + 0.08 * clamp(t / DUR, 0, 1), cx: CX, cy: CY };
    var z0 = 1 + 0.03 * clamp(t / 2, 0, 1);
    var e = ease((t - 2.0) / 2.6);
    var zEnd = KZ + 0.12 * clamp((t - 4.6) / 4.4, 0, 1);
    var z = t < 2.0 ? z0 : Math.exp(lerp(Math.log(z0), Math.log(zEnd), e));
    return { z: z, cx: lerp(CX, KCX, e), cy: lerp(CY, KCY, e) };
  }

  var tmp = [0, 0, 0, 0], tmp2 = [0, 0, 0, 0];
  var BASE = [90, 165, 255], WHITE = [255, 255, 255], YEL = [240, 192, 74];
  function mix(a, b, x) { return [lerp(a[0], b[0], x), lerp(a[1], b[1], x), lerp(a[2], b[2], x)]; }
  function rgba(c, a) { return "rgba(" + (c[0] | 0) + "," + (c[1] | 0) + "," + (c[2] | 0) + "," + a.toFixed(3) + ")"; }

  var LABELS = MODE === "jp" ? ["北海道", "宮城県", "東京都", "福岡県"] : ["鹿児島県", "東京都"];

  function drawLane(l, t, cam, f, col, boost) {
    if (f <= 0.004) return;
    var z = cam.z, zw = Math.pow(z, -0.5); // 画面上の太さは z^0.5 で増える
    var q = l.q, k = boost;
    // 流れの下地（広い光と細い芯）
    ctx.beginPath(); ctx.moveTo(l.pts[0], l.pts[1]);
    for (var i = 1; i <= NS; i += 2) ctx.lineTo(l.pts[i * 2], l.pts[i * 2 + 1]);
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.lineWidth = (4 + 12 * q) * zw * k; ctx.strokeStyle = rgba(col, f * (0.008 + 0.02 * q)); ctx.stroke();
    ctx.lineWidth = (1.2 + 2.2 * q) * zw * k; ctx.strokeStyle = rgba(col, f * (0.012 + 0.035 * q)); ctx.stroke();
    // 粒（細い尾と明るい頭）。粒の太さは 3 段階
    var trailBase = 46 * Math.pow(z, -0.4) / l.L;
    var amp = (1 + 6 * q) * zw * k;
    for (var c = 0; c < 3; c++) {
      var sh = 0.72 + 0.28 * Math.sin(TAU * (t * 0.55 + c / 3 + l.idx * 0.137));
      var wd = (0.75 + 0.45 * c) * (0.7 + 0.8 * q) * zw * k;
      ctx.beginPath();
      var hx = new Float32Array(Math.ceil(l.n / 3) * 4 + 4), hn = 0;
      for (var m = c; m < l.n; m += 3) {
        var s = l.p0[m] + t * l.sp[m] / l.L; s -= Math.floor(s);
        var env = Math.min(1, s / 0.05, (1 - s) / 0.05);
        if (env <= 0.02) continue;
        var tl = trailBase * l.tr[m] * env; if (tl > s) tl = s;
        var lat = amp * l.ja[m] * Math.sin(TAU * (l.jp[m] + t * (0.18 + 0.1 * c)));
        at(l, s, tmp); var hx0 = tmp[0] + tmp[2] * lat, hy0 = tmp[1] + tmp[3] * lat;
        at(l, s - tl * 0.45, tmp2); var mx = tmp2[0] + tmp2[2] * lat, my = tmp2[1] + tmp2[3] * lat;
        at(l, s - tl, tmp2); var tx = tmp2[0] + tmp2[2] * lat, ty = tmp2[1] + tmp2[3] * lat;
        ctx.moveTo(tx, ty); ctx.lineTo(mx, my);
        hx[hn++] = mx; hx[hn++] = my; hx[hn++] = hx0; hx[hn++] = hy0;
      }
      ctx.lineWidth = wd * 0.55; ctx.strokeStyle = rgba(col, f * sh * (0.07 + 0.07 * q)); ctx.stroke();
      ctx.beginPath();
      for (var h = 0; h < hn; h += 4) { ctx.moveTo(hx[h], hx[h + 1]); ctx.lineTo(hx[h + 2], hx[h + 3]); }
      ctx.lineWidth = wd; ctx.strokeStyle = rgba(col, f * sh * (0.2 + 0.2 * q)); ctx.stroke();
    }
  }

  function render(ms) {
    var t = clamp(ms / 1000, 0, DUR);
    var cam = camera(t), z = cam.z;
    var master = ss(0, 0.6, t) * (1 - ss(DUR - 0.4, DUR, t));
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
    ctx.fillStyle = BG; ctx.fillRect(0, 0, W, H);
    var g = ctx.createRadialGradient(W / 2, 420, 100, W / 2, 420, 1000);
    g.addColorStop(0, "rgba(16,28,42,0.9)"); g.addColorStop(1, "rgba(5,8,12,0)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    ctx.globalAlpha = master;
    ctx.setTransform(z, 0, 0, z, W / 2 - z * cam.cx, SY - z * cam.cy);
    var dim = MODE === "kago" ? lerp(1, 0.6, ss(2.0, 3.0, t)) : 1;
    ctx.fillStyle = "rgba(16,26,38,0.9)"; ctx.fill(outline);
    ctx.lineWidth = 1.0 / z * Math.pow(z, 0.35); ctx.lineJoin = "round";
    ctx.strokeStyle = "rgba(140,170,200," + (0.55 * dim).toFixed(3) + ")"; ctx.stroke(outline);
    // 流れ（加算合成）
    ctx.globalCompositeOperation = "lighter";
    order.forEach(function (l) {
      var f = 1, col = BASE, boost = 1;
      if (MODE === "jp") {
        f = 0.7 + 0.3 * l.q;
        col = mix(BASE, WHITE, Math.pow(l.q, 2) * 0.3);
      } else {
        var h = ss(2.0, 2.9, t), dimA = 0.62;
        if (l.k) {
          f = lerp(dimA, 1, h);
          col = mix(BASE, l.k === 1 ? WHITE : YEL, h);
          boost = lerp(1, l.k === 1 ? 1.6 : 1.8, h);
        } else {
          f = lerp(dimA, 0.15, h);
          col = mix(BASE, WHITE, Math.pow(l.q, 2) * 0.4);
        }
      }
      drawLane(l, t, cam, f, col, boost);
    });
    ctx.globalCompositeOperation = "source-over";
    // 地名（画面座標で描く）
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.textBaseline = "middle";
    ctx.font = "500 24px 'Noto Sans JP','Yu Gothic',sans-serif";
    LABELS.forEach(function (nm) {
      var p = D.pos[D.prefs.indexOf(nm)];
      var sx = W / 2 + z * (p[0] - cam.cx), sy = SY + z * (p[1] - cam.cy);
      var a = MODE === "jp" ? 0.55 : 0.35 + 0.35 * ss(2.0, 3.0, t);
      ctx.fillStyle = "rgba(200,215,230," + (a * master).toFixed(3) + ")";
      ctx.shadowColor = "rgba(0,0,0,0.8)"; ctx.shadowBlur = 8;
      var left = (nm === "福岡県" || nm === "大阪府" || nm === "鹿児島県");
      ctx.textAlign = left ? "right" : "left";
      ctx.beginPath(); ctx.arc(sx, sy, 3, 0, TAU); ctx.fill();
      ctx.fillText(nm, sx + (left ? -12 : 12), sy);
    });
    ctx.shadowBlur = 0;
    if (MODE === "kago") drawTags(t, cam, master);
    ctx.globalAlpha = 1;
    var v = ctx.createRadialGradient(W / 2, 460, 520, W / 2, 460, 1150);
    v.addColorStop(0, "rgba(5,8,12,0)"); v.addColorStop(1, "rgba(5,8,12,0.6)");
    ctx.fillStyle = v; ctx.fillRect(0, 0, W, H);
  }

  function drawTags(t, cam, master) {
    var a = ss(3.5, 4.1, t); if (a <= 0) return;
    var z = cam.z;
    var tags = [{ k: 1, text: "行き " + fmt(D.kagoFwd) + "t", col: "255,255,255" }, { k: 2, text: "帰り " + fmt(D.kagoRev) + "t", col: "240,192,74" }];
    tags.forEach(function (g) {
      var l = lanes.filter(function (x) { return x.k === g.k; })[0];
      var r = at(l, 0.5, [0, 0, 0, 0]);
      var out = 78;
      var sx = W / 2 + z * (r[0] - cam.cx) + r[2] * out, sy = SY + z * (r[1] - cam.cy) + r[3] * out;
      ctx.font = "700 34px 'Noto Sans JP','Yu Gothic',sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.shadowColor = "rgba(0,0,0,0.85)"; ctx.shadowBlur = 14; ctx.shadowOffsetY = 2;
      ctx.fillStyle = "rgba(" + g.col + "," + (a * master).toFixed(3) + ")";
      ctx.fillText(g.text, sx, sy + (1 - a) * 8);
    });
    ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
  }
  function fmt(n) { return Math.round(n).toLocaleString("en-US"); }

  if (MODE === "kago" && (Math.round(D.kagoFwd) !== 1060 || Math.round(D.kagoRev) !== 190)) throw new Error("tonnage mismatch");
  window.__seek = function (ms) { render(ms); return ms; };
  var fontReady = document.fonts && document.fonts.load ? Promise.all([document.fonts.load("700 34px 'Noto Sans JP'"), document.fonts.load("500 24px 'Noto Sans JP'")]).catch(function () {}) : Promise.resolve();
  fontReady.then(function () { return document.fonts.ready; }).then(function () { window.__seek(0); window.__ready = true; });
})();
