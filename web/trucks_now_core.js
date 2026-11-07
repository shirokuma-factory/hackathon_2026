/* いまの大型車（web/trucks_now.html）の計算の部分。画面（DOM・地図）に触れない関数だけを置く。
   ブラウザでは window.TrucksCore、node では module.exports で使える（tests/test_v54_trucks_now.py が node で確かめる）。
   欠測は null のまま扱い、0 台にしない。 */
(function (root) {
  'use strict';
  const NEUTRAL = [176, 188, 206];
  const BLUE = [74, 163, 255];
  const ORANGE = [255, 150, 48];
  const DEAD = Math.log2(1.1); // 1.1 倍までは「変わらない」色にする

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  /* 時間 T（0 〜 n-1 の小数）での値。隣り合う 2 時間のどちらかが欠測なら null。整数に近い T はその時間の値をそのまま返す */
  function valueAt(series, T) {
    const n = series.length;
    const t = clamp(T, 0, n - 1);
    const r = Math.round(t);
    if (Math.abs(t - r) < 1e-3) return series[r] == null ? null : series[r];
    const i = Math.floor(t), f = t - i;
    const a = series[i], b = series[i + 1];
    if (a == null || b == null) return null;
    return a + (b - a) * f;
  }

  /* 変化の比。台数が小さいときの 2 倍・半分で色が振れすぎないよう、両方に 3 台を足してから割る */
  function changeRatio(cur, ref) {
    if (cur == null || ref == null) return null;
    return (cur + 3) / (ref + 3);
  }

  /* 比 → 色。null（比べる相手が無い）は無彩色。青 = 少ない、橙 = 多い */
  function changeColor(r) {
    if (r == null || !isFinite(r)) return NEUTRAL.slice();
    const t = clamp(Math.log2(r), -1, 1);
    const a = clamp((Math.abs(t) - DEAD) / (1 - DEAD), 0, 1);
    const tgt = t < 0 ? BLUE : ORANGE;
    const k = Math.pow(a, 0.8);
    return NEUTRAL.map((c, i) => Math.round(c + (tgt[i] - c) * k));
  }

  /* 色分けに使う比べ方。先週の同じ時刻は最新の時間の分しか無いので、最新の時間の近く以外は「前の時間」にする */
  function effectiveMode(mode, T, n) {
    return mode === 'week' && T >= (n || 24) - 1.5 ? 'week' : 'prev';
  }

  function refFor(point, T, effMode) {
    if (effMode === 'week') return point.week_ago == null ? null : point.week_ago;
    return valueAt(point.series, Math.max(0, T - 1));
  }

  /* ---- 道路の区間の「混み具合」。速度ではなく、各地点のふだんの多さ（24 時間の最大）との比 ---- */
  const NODATA = [110, 120, 135];
  const MIN_REF = 20; // 24 時間の最大が小さい地点は、台数のわずかな差で赤くならないよう 20 台/時を下限にする
  const STATE_BINS = [
    { max: 0.35, center: 0.175, rgb: [52, 199, 89], name: 'すいている' },
    { max: 0.6, center: 0.475, rgb: [245, 197, 24], name: 'ふつう' },
    { max: 0.8, center: 0.7, rgb: [242, 140, 40], name: '多い' },
    { max: Infinity, center: 0.9, rgb: [224, 58, 46], name: 'とても多い' },
  ];
  const DENS_MAX = 0.6; // 流れる点の数の上限（1 km・片方向あたり）
  const DENS_AT = 600; // この台数/時で上限

  function refMax(series) {
    let m = null;
    for (const v of series) if (v != null && (m == null || v > m)) m = v;
    return m == null ? null : Math.max(MIN_REF, m);
  }
  /* 地点の混み具合 = その時間の台数 ÷ その地点の 24 時間の最大。欠測は null */
  function stateIndex(series, T) {
    const v = valueAt(series, T), m = refMax(series);
    return v == null || m == null ? null : v / m;
  }
  function stateBin(idx) {
    if (idx == null) return -1;
    for (let i = 0; i < STATE_BINS.length; i++) if (idx < STATE_BINS[i].max) return i;
    return STATE_BINS.length - 1;
  }
  /* 混み具合 → 色。各区分の中心でその区分の色、間は連続して混ぜる。null は灰色（データなし） */
  function stateColor(idx) {
    if (idx == null || !isFinite(idx)) return NODATA.slice();
    const B = STATE_BINS;
    if (idx <= B[0].center) return B[0].rgb.slice();
    for (let i = 1; i < B.length; i++) {
      if (idx <= B[i].center) {
        const t = (idx - B[i - 1].center) / (B[i].center - B[i - 1].center);
        return B[i - 1].rgb.map((c, k) => Math.round(c + (B[i].rgb[k] - c) * t));
      }
    }
    return B[B.length - 1].rgb.slice();
  }
  /* 流れる点の密度（1 km・片方向あたり）。台数に比例し、上限で頭打ち。欠測・0 台は 0 */
  function particleDensity(count) {
    if (count == null || !(count > 0)) return 0;
    return Math.min(DENS_MAX, (DENS_MAX * count) / DENS_AT);
  }
  /* 流れる点の速さ（km/秒、見せ方）。混んでいるほど遅い */
  function particleSpeed(idx) {
    return 8 - 6.5 * clamp(idx == null ? 0 : idx, 0, 1);
  }
  /* 区間の値 = 近くの観測点の値の重みづけ平均。欠測の点は外して重みを取り直す。全部欠測なら null */
  function blend(w, arr) {
    let s = 0, t = 0;
    for (let i = 0; i < w.length; i++) { const v = arr[w[i][0]]; if (v != null && !Number.isNaN(v)) { s += w[i][1] * v; t += w[i][1]; } }
    return t > 0 ? s / t : null;
  }

  /* その時間（整数に丸める）に欠測の地点数 */
  function missingAt(points, T) {
    let n = 0;
    for (const p of points) if (valueAt(p.series, Math.round(T)) == null) n++;
    return n;
  }

  /* 欠測の時間数（24 時間のうち） */
  function missingHours(p) {
    let n = 0;
    for (const v of p.series) if (v == null) n++;
    return n;
  }

  /* 時間コード 202611062100 の分解 */
  function parseCode(code) {
    const s = String(code);
    return { y: +s.slice(0, 4), m: +s.slice(4, 6), d: +s.slice(6, 8), h: +s.slice(8, 10) };
  }
  const WD = ['日', '月', '火', '水', '木', '金', '土'];
  function fmtHour(code) {
    const c = parseCode(code);
    const w = WD[new Date(Date.UTC(c.y, c.m - 1, c.d)).getUTCDay()];
    return `${c.m}月${c.d}日(${w}) ${c.h}時台`;
  }
  function shiftCode(code, hours) {
    const c = parseCode(code);
    const t = new Date(Date.UTC(c.y, c.m - 1, c.d, c.h) + hours * 3600000);
    const p = (x) => String(x).padStart(2, '0');
    return Number(`${t.getUTCFullYear()}${p(t.getUTCMonth() + 1)}${p(t.getUTCDate())}${p(t.getUTCHours())}00`);
  }
  /* fetched_at は日本時間（+09:00）で書かれているので、文字列のまま切り出す */
  function hhmm(iso) { return String(iso).slice(11, 16); }
  function mdhm(iso) { const s = String(iso); return `${+s.slice(5, 7)}月${+s.slice(8, 10)}日 ${s.slice(11, 16)}`; }
  function ageText(iso, nowMs) {
    const min = Math.max(0, Math.round((nowMs - Date.parse(iso)) / 60000));
    if (min < 90) return `${min} 分前`;
    const h = min / 60;
    return h < 36 ? `約 ${Math.round(h)} 時間前` : `約 ${Math.round(h / 24)} 日前`;
  }

  /* 折れ線の path。null の所で線を切る */
  function sparkPath(series, w, h, max, pad) {
    const p = pad == null ? 1 : pad, n = series.length;
    const x = (i) => p + (i * (w - 2 * p)) / (n - 1);
    const y = (v) => p + (1 - clamp(v / max, 0, 1)) * (h - 2 * p);
    let d = '', pen = false;
    series.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  }

  /* 画面上部の流れの帯に出す数字（すべて JSON から。T は表示中の時間） */
  function pipelineModel(data, T) {
    const pts = data.points, idx = Math.round(clamp(T, 0, data.hours.length - 1));
    let missing = 0, comparable = 0, kinds = {};
    const eff = effectiveMode('week', T, data.hours.length);
    for (const p of pts) {
      kinds[p.type] = (kinds[p.type] || 0) + 1;
      if (p.series[idx] == null) { missing++; continue; }
      if (refFor(p, idx, eff) != null) comparable++;
    }
    const withData = data.prefs.filter((q) => q.series_avg[idx] != null).length;
    return {
      hourCode: data.hours[idx], idx,
      api: { points: pts.length, kinds },
      fetch: { requests: data.requests, at: hhmm(data.fetched_at), fetchedAt: data.fetched_at },
      missing: { count: missing, observed: pts.length - missing },
      agg: { prefs: data.prefs.length, withData },
      draw: { drawn: pts.length - missing, comparable },
    };
  }

  /* 県の順位。値は T での 1 地点あたり大型車、変化は最新の時間なら先週比、そうでなければ前の時間との比 */
  function rankPrefs(data, T) {
    const n = data.hours.length, eff = effectiveMode('week', T, n);
    const rows = data.prefs.map((q) => {
      const v = valueAt(q.series_avg, T);
      let ratio = null;
      if (eff === 'week') ratio = q.week_ratio == null ? null : q.week_ratio;
      else { const a = valueAt(q.series_avg, Math.max(0, T - 1)); ratio = v != null && a ? v / a : null; }
      return { pref: q.pref, v, ratio, n_points: q.n_points, n_observed: q.n_observed };
    });
    rows.sort((a, b) => (b.v == null ? -1 : b.v) - (a.v == null ? -1 : a.v));
    return { mode: eff, rows };
  }

  /* 新しい JSON で台数が変わった地点の id の集合（脈動で見せる） */
  function changedIds(oldData, newData) {
    const out = new Set();
    if (!oldData) return out;
    const old = new Map(oldData.points.map((p) => [p.id + '|' + p.type, p]));
    for (const p of newData.points) {
      const o = old.get(p.id + '|' + p.type);
      if (!o || o.series.join(',') !== p.series.join(',') || o.week_ago !== p.week_ago) out.add(p.id + '|' + p.type);
    }
    return out;
  }

  function isNewer(oldData, newData) {
    return !oldData || newData.fetched_at !== oldData.fetched_at || newData.latest_hour_code !== oldData.latest_hour_code;
  }

  const api = { NEUTRAL, BLUE, ORANGE, NODATA, STATE_BINS, DENS_MAX, refMax, stateIndex, stateBin, stateColor, particleDensity, particleSpeed, blend, valueAt, changeRatio, changeColor, effectiveMode, refFor, missingAt, missingHours,
    parseCode, fmtHour, shiftCode, hhmm, mdhm, ageText, sparkPath, pipelineModel, rankPrefs, changedIds, isNewer };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.TrucksCore = api;
})(typeof window !== 'undefined' ? window : globalThis);
