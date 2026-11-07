/* 最初の画面の背景を、three.js の生の 3D で描く（Blender の黄昏前の写真と同じ構図）。
   - 使えるのは、WebGL があり、動きを減らす設定でなく、幅が 700px を超え、通信量を抑える設定でない端末だけ。使えない・読み込みが 4 秒を超えたときは start() が false を返し、呼び出し側が今までの映像・静止画を使う
   - 静止画は下に残したまま、最初のフレームが描けたら 300ms でキャンバスに切り替える
   - 画面を離れる（stop）・タブが隠れる（visibilitychange）と描画を止める。stop で GPU の資源も解放する
   - three.js は web/vendor/three/（r160、MIT）。外部の CDN には頼らない
   Blender のカメラ: 方位 38°・仰角 48°・距離 600・注視点 (2, 3, 0.8)・横幅 118 の望遠（35mm 換算 約 183mm）。座標は glTF（Y 上）に直して使う */
(function () {
  'use strict';
  const BASE = 'vendor/three/', MODEL = 'models/v3/japan_diorama_v3.glb', ROUTES = 'models/v3/routes.json', SEA = 'models/v3/sea_sdf.png';
  const TRUCK_LOADED = 'models/v2/truck_10t_loaded.glb', TRUCK_EMPTY = 'models/v2/truck_10t_empty.glb';
  const LOAD_LIMIT_MS = 4000, FADE_MS = 300, DPR_MAX = 1.5;
  const FX0 = -100, FX1 = 100, FY0 = -75, FY1 = 100, Z_SEA = 0.3;   // 海の距離場の範囲（Blender の x, y）と海面の高さ
  const TRUCK_SCALE = 0.5, TRUCK_SPEED = 1.15, DASH_SPEED = 0.9, DASH_PITCH = 2.1;

  let cur = null;   // 動いている 1 つの場面
  let three = null; // 読み込み済みの three.js（stop のあとも使い回す）

  const rad = (d) => (d * Math.PI) / 180;

  function webglOk() {
    try {
      const c = document.createElement('canvas');
      return !!(c.getContext('webgl2') || c.getContext('webgl'));
    } catch (e) { return false; }
  }
  function capable() {
    if (innerWidth <= 700) return false;
    if (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) return false;
    const c = navigator.connection;
    if (c && (c.saveData || /(^|-)2g$/.test(c.effectiveType || ''))) return false;
    return webglOk();
  }

  // 臨界減衰のばね（Unity の SmoothDamp と同じ式）。マウスの動きにカメラをなめらかに追従させる
  function smoothDamp(cur0, target, vel, smooth, dt) {
    const om = 2 / smooth, x = om * dt, e = 1 / (1 + x + 0.48 * x * x + 0.235 * x * x * x);
    const ch = cur0 - target, tmp = (vel.v + om * ch) * dt;
    vel.v = (vel.v - om * tmp) * e;
    return target + (ch + tmp) * e;
  }
  const lerpAngle = (a, b, k) => {   // 最短の向きで回る
    let d = ((b - a + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
    return a + d * k;
  };

  async function loadThree() {
    if (three) return three;
    const [T, G] = await Promise.all([import('./' + BASE + 'three.module.min.js'), import('./' + BASE + 'GLTFLoader.js')]);
    three = { T, GLTFLoader: G.GLTFLoader };
    return three;
  }

  function seaMaterial(T, tex) {
    const col = (h) => new T.Color(h);
    const mat = new T.ShaderMaterial({
      toneMapped: false,
      uniforms: {
        sdf: { value: tex }, uTime: { value: 0 }, uSun: { value: new T.Vector3(-0.918, 0.259, 0.2985) },
        uHaze: { value: col('#f6d2a8') }, uRange: { value: new T.Vector4(FX0, FY0, FX1 - FX0, FY1 - FY0) },
        c0: { value: col('#86dcd2') }, c1: { value: col('#5cc3cc') }, c2: { value: col('#3496bd') },
        c3: { value: col('#216ea3') }, c4: { value: col('#164b7f') }, c5: { value: col('#0e3262') },
      },
      vertexShader: `varying vec3 vW; void main(){ vec4 w = modelMatrix * vec4(position,1.0); vW = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }`,
      fragmentShader: `
        uniform sampler2D sdf; uniform float uTime; uniform vec3 uSun, uHaze, c0,c1,c2,c3,c4,c5; uniform vec4 uRange;
        varying vec3 vW;
        float h21(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
        float vn(vec2 p){ vec2 i = floor(p), f = fract(p); f = f*f*(3.0-2.0*f);
          return mix(mix(h21(i), h21(i+vec2(1,0)), f.x), mix(h21(i+vec2(0,1)), h21(i+vec2(1,1)), f.x), f.y); }
        float fbm(vec2 p){ float a = 0.5, s = 0.0; for(int i=0;i<4;i++){ s += a*vn(p); p = p*2.03+7.1; a *= 0.5; } return s / 0.9375; }
        vec3 ramp(float t){
          if (t < 0.03) return mix(c0, c1, t/0.03);
          if (t < 0.10) return mix(c1, c2, (t-0.03)/0.07);
          if (t < 0.26) return mix(c2, c3, (t-0.10)/0.16);
          if (t < 0.55) return mix(c3, c4, (t-0.26)/0.29);
          return mix(c4, c5, (t-0.55)/0.45);
        }
        void main(){
          vec2 xy = vec2(vW.x, -vW.z);
          vec2 uv = (xy - uRange.xy) / uRange.zw;
          float s = texture2D(sdf, uv).r * 48.0 - 16.0;
          float dist = max(-s, 0.0);
          vec3 base = ramp(clamp(dist / 22.0, 0.0, 1.0));
          vec2 q = xy * 2.2 / 6.0 + vec2(uTime * 0.012, uTime * 0.007);
          float n = fbm(q);
          float foam = clamp(clamp(1.0 - dist / 0.8, 0.0, 1.0) * (n * 0.8 + 0.45) * 1.1, 0.0, 1.0);
          float ringv = pow(0.5 + 0.5 * sin(dist * 13.0 - n * 4.5 - uTime * 0.35), 10.0);
          float ring = ringv * clamp((1.3 - dist) / 1.0, 0.0, 1.0) * 0.14;
          float f = clamp(foam + ring, 0.0, 1.0);
          vec3 c = mix(base, vec3(0.90, 0.97, 0.96), f);
          // さざ波のつや
          vec2 e = vec2(0.05, 0.0);
          vec2 p2 = xy * 1.15 + vec2(uTime * 0.05, -uTime * 0.03);
          float h0 = fbm(p2), hx = fbm(p2 + e.xy), hy = fbm(p2 + e.yx);
          vec3 N = normalize(vec3(-(hx - h0) * 0.9, 1.0, (hy - h0) * 0.9));
          vec3 V = normalize(cameraPosition - vW), H = normalize(V + normalize(uSun));
          float spec = pow(max(dot(N, H), 0.0), 140.0) * 0.45;
          float fres = pow(1.0 - max(dot(N, V), 0.0), 4.0) * 0.10;
          c += spec * vec3(1.0, 0.78, 0.55) * (1.0 - f) + fres * vec3(0.55, 0.65, 0.8);
          float d = length(cameraPosition - vW);
          c = mix(c, uHaze, (1.0 - exp(-pow(d * 0.00045, 2.0))) * 0.35);
          gl_FragColor = vec4(c, 1.0);
          #include <tonemapping_fragment>
          #include <colorspace_fragment>
        }`,
    });
    return mat;
  }

  function saturate(geo, k) {
    const a = geo && geo.attributes.color;
    if (!a) return;
    for (let i = 0; i < a.count; i++) {
      const r = a.getX(i), g = a.getY(i), b = a.getZ(i), l = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      a.setXYZ(i, Math.max(0, l + (r - l) * k), Math.max(0, l + (g - l) * k), Math.max(0, l + (b - l) * k));
    }
    a.needsUpdate = true;
  }
  function loadTexture(T, url) {
    return new Promise((res, rej) => new T.TextureLoader().load(url, res, undefined, rej));
  }
  function loadJson(url) { return fetch(url).then((r) => { if (!r.ok) throw new Error(url); return r.json(); }); }

  /* 経路の曲線（弧長で等間隔に進める）。points は glTF 座標の [x, y, z] の並び */
  function makePath(T, pts, y) {
    const c = new T.CatmullRomCurve3(pts.map((p) => new T.Vector3(p[0], y == null ? p[1] : y, p[2])), false, 'centripetal');
    c.arcLengthDivisions = 800;
    c.updateArcLengths();
    return c;
  }

  async function build(box, abort) {
    const { T, GLTFLoader } = await loadThree();
    if (abort.v) return null;
    const loader = new GLTFLoader();
    const [gltf, routes, seaTex, tl, te] = await Promise.all([
      loader.loadAsync(MODEL), loadJson(ROUTES), loadTexture(T, SEA), loader.loadAsync(TRUCK_LOADED), loader.loadAsync(TRUCK_EMPTY),
    ]);
    if (abort.v) return null;
    seaTex.colorSpace = T.NoColorSpace; seaTex.wrapS = seaTex.wrapT = T.ClampToEdgeWrapping; seaTex.minFilter = T.LinearFilter; seaTex.generateMipmaps = false;

    const canvas = document.createElement('canvas');
    canvas.setAttribute('aria-hidden', 'true');
    canvas.tabIndex = -1;
    const renderer = new T.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' });
    renderer.setClearColor(0x164b7f, 1);
    renderer.outputColorSpace = T.SRGBColorSpace;
    renderer.toneMapping = T.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.0;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = T.PCFSoftShadowMap;

    const scene = new T.Scene();
    scene.fog = new T.FogExp2(0xf6d2a8, 0.00045);
    const camera = new T.PerspectiveCamera(6.36, 16 / 9, 50, 3000);

    // 太陽（高さ 15°・方位 198°、Blender の座標から glTF へ: (x, y, z) → (x, z, -y)）と空の光
    const el = rad(15), az = rad(198);
    const sunDir = new T.Vector3(Math.cos(el) * Math.cos(az), Math.sin(el), -Math.cos(el) * Math.sin(az));
    const target = new T.Vector3(2, 0.8, -3);
    const sun = new T.DirectionalLight(0xffd2a8, 7.6);
    sun.position.copy(target).addScaledVector(sunDir, 260);
    sun.target.position.copy(target);
    sun.castShadow = true;
    const ms = Math.min(4096, renderer.capabilities.maxTextureSize || 2048);
    sun.shadow.mapSize.set(ms, ms);
    const sc = sun.shadow.camera;
    sc.left = -75; sc.right = 75; sc.top = 62; sc.bottom = -62; sc.near = 20; sc.far = 560;
    sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.06; sun.shadow.radius = 3;
    scene.add(sun, sun.target);
    scene.add(new T.HemisphereLight(0xa9c4f0, 0x9a9a80, 1.25));

    // 海
    const sea = new T.Mesh(new T.PlaneGeometry(3000, 3000).rotateX(-Math.PI / 2), seaMaterial(T, seaTex));
    sea.position.y = Z_SEA;
    sea.frustumCulled = false;
    scene.add(sea);

    // 日本の地形・街・木・道路・経路
    const world = gltf.scene;
    const dashes = [];
    world.traverse((o) => {
      if (/^dash\d+$/.test(o.name)) { dashes.push(o); return; }
      if (o.isMesh) {
        if (o.material && /^route_/.test(o.material.name || '')) o.material.emissiveIntensity *= 2.0;   // ACES で光が白くつぶれないよう、経路の発光を強める
        if (o.name === 'land' || /^land/.test(o.name)) saturate(o.geometry, 1.3);   // ACES で薄まる緑を、頂点色の彩度を上げて補う
        o.castShadow = o.name !== 'route_loaded' && !/^route/.test(o.name);
        o.receiveShadow = true;
      }
    });
    // 点線は経路に沿って流すので、置いてあったものは元の形だけ取り出して作り直す
    let dashProto = null;
    for (const d of dashes) { if (!dashProto) { dashProto = d; if (d.material) d.material.emissiveIntensity *= 1.8; } d.parent.remove(d); }
    scene.add(world);

    // 経路
    const loaded = makePath(T, routes.loaded.slice(0, -2));
    const empty = makePath(T, routes.empty.slice().reverse());   // 関東 → 関西（点の並びは関西 → 関東なので逆にする）
    const Le = empty.getLength(), Ll = loaded.getLength();
    const dashGroup = new T.Group();
    scene.add(dashGroup);
    const dashList = [];
    if (dashProto) {
      const nD = Math.ceil(Le / DASH_PITCH);
      for (let i = 0; i < nD; i++) {
        const m = dashProto.clone();
        m.position.set(0, 0, 0); m.rotation.set(0, 0, 0); m.scale.set(1, 1, 1); m.castShadow = false;
        dashGroup.add(m);
        dashList.push(m);
      }
    }

    // トラック（積荷あり: 青い線の上を関西 → 関東、空車: 橙の点線の上を関東 → 関西）
    const trucks = [];
    const prep = (src) => {
      const g = src.scene;
      g.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
      const bb = new T.Box3().setFromObject(g);
      const holder = new T.Group();
      g.position.y = -bb.min.y;
      holder.add(g);
      holder.scale.setScalar(TRUCK_SCALE);
      return holder;
    };
    const mk = (src, curve, len, phase) => {
      const h = prep({ scene: src.scene.clone(true) });
      h.visible = false;
      scene.add(h);
      trucks.push({ h, curve, len, phase, yaw: null });
    };
    mk(tl, loaded, Ll, 0.0); mk(tl, loaded, Ll, 0.5);
    mk(te, empty, Le, 0.25); mk(te, empty, Le, 0.75);

    // 画面の大きさ（object-fit: cover・object-position: 30% 50% と同じ見え方）
    const state = { w: 0, h: 0 };
    function resize() {
      const w = Math.max(1, box.clientWidth), h = Math.max(1, box.clientHeight);
      if (w === state.w && h === state.h) return;
      state.w = w; state.h = h;
      const dpr = Math.min(window.devicePixelRatio || 1, DPR_MAX);
      renderer.setPixelRatio(dpr);
      renderer.setSize(w, h, false);
      canvas.style.width = '100%'; canvas.style.height = '100%';
      const ar = w / h, ref = 16 / 9;
      let fw, fh;                                   // 16:9 の絵を覆うように拡大した大きさ（CSS 画素）
      if (ar >= ref) { fw = w; fh = w / ref; } else { fh = h; fw = h * ref; }
      camera.aspect = ref;
      camera.fov = 6.36;
      camera.setViewOffset(fw, fh, (fw - w) * 0.3, (fh - h) * 0.5, w, h);
      camera.updateProjectionMatrix();
    }
    const camDist = 600;
    const baseAz = rad(38), basePitch = rad(48);
    const mouse = { x: 0, y: 0 }, sm = { x: 0, y: 0 }, vx = { v: 0 }, vy = { v: 0 };
    const onMove = (e) => { mouse.x = (e.clientX / innerWidth) * 2 - 1; mouse.y = (e.clientY / innerHeight) * 2 - 1; };
    window.addEventListener('pointermove', onMove, { passive: true });
    function placeCamera(t) {
      const a = baseAz + rad(1.5) * sm.x + rad(0.35) * Math.sin(t * 0.11);
      const p = basePitch + rad(1.0) * sm.y + rad(0.25) * Math.sin(t * 0.083 + 1.3);
      camera.position.set(
        target.x + Math.sin(a) * Math.cos(p) * camDist,
        target.y + Math.sin(p) * camDist,
        target.z + Math.cos(a) * Math.cos(p) * camDist,
      );
      camera.lookAt(target);
    }

    const tmp = new T.Vector3(), tan = new T.Vector3();
    const edge = (u) => Math.min(1, u * 14, (1 - u) * 14);   // 端でふわっと現れて消える（拡大縮小）
    let tNow = 0;
    function update(dt) {
      tNow += dt;
      sea.material.uniforms.uTime.value = tNow;
      sm.x = smoothDamp(sm.x, mouse.x, vx, 0.9, dt);
      sm.y = smoothDamp(sm.y, mouse.y, vy, 0.9, dt);
      placeCamera(tNow);
      for (const tr of trucks) {
        const u = (((tNow * TRUCK_SPEED) / tr.len + tr.phase) % 1 + 1) % 1;   // 一定の速さ（弧長）
        tr.curve.getPointAt(u, tmp);
        tr.curve.getTangentAt(u, tan);
        const want = Math.atan2(tan.x, tan.z);
        tr.yaw = tr.yaw == null ? want : lerpAngle(tr.yaw, want, 1 - Math.exp(-dt * 5));
        tr.h.position.copy(tmp);
        tr.h.rotation.y = tr.yaw;
        const s = TRUCK_SCALE * (0.2 + 0.8 * edge(u));
        tr.h.scale.setScalar(s);
        tr.h.visible = true;
      }
      const off = (tNow * DASH_SPEED) % DASH_PITCH;
      dashList.forEach((m, i) => {
        const d = i * DASH_PITCH + off;
        if (d >= Le) { m.visible = false; return; }
        const u = d / Le;
        empty.getPointAt(u, m.position);
        empty.getTangentAt(u, tan);
        m.rotation.y = Math.atan2(-tan.x, -tan.z);
        m.visible = true;
      });
    }

    resize();
    placeCamera(0);
    update(0);
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : null;
    if (ro) ro.observe(box);
    return { T, scene, camera, renderer, canvas, update, resize, ro, onMove, world, sea };
  }

  function start(box) {
    if (cur) return cur.p;   // 読み込み中・動作中に呼ばれたら同じ結果を返す（映像へ誤って切り替えない）
    if (!box || !capable()) return Promise.resolve(false);
    const s = { abort: { v: false } };
    cur = s;
    s.p = startInner(box, s);
    return s.p;
  }
  async function startInner(box, s0) {
    const abort = s0.abort, s = s0;
    Object.assign(s, { canvas: null, raf: 0, last: 0, running: false, ctx: null, onVis: null, timer: 0, fps: { n: 0, t0: 0, last: 0 } });
    let timedOut = false;
    const to = new Promise((r) => { s.timer = setTimeout(() => { timedOut = true; r(null); }, LOAD_LIMIT_MS); });
    let ctx = null;
    try { ctx = await Promise.race([build(box, abort), to]); } catch (e) { ctx = null; }
    clearTimeout(s.timer);
    if (!ctx || timedOut || abort.v) {
      abort.v = true;
      if (ctx) disposeCtx(ctx);
      if (cur === s) cur = null;
      return false;
    }
    s.ctx = ctx;
    const canvas = ctx.canvas;
    canvas.className = 'hero3d';
    box.appendChild(canvas);
    s.canvas = canvas;
    const loop = (now) => {
      if (!s.running) return;
      const dt = Math.min(0.1, Math.max(0, (now - (s.last || now)) / 1000));
      s.last = now;
      ctx.update(dt);
      ctx.renderer.render(ctx.scene, ctx.camera);
      const f = s.fps;
      if (!f.t0) f.t0 = now;
      f.n++;
      if (now - f.t0 >= 1000) { f.last = (f.n * 1000) / (now - f.t0); f.n = 0; f.t0 = now; }
      s.raf = requestAnimationFrame(loop);
    };
    s.run = () => { if (s.running) return; s.running = true; s.last = 0; s.fps.t0 = 0; s.fps.n = 0; s.raf = requestAnimationFrame(loop); };
    s.pause = () => { s.running = false; cancelAnimationFrame(s.raf); };
    s.onVis = () => { if (document.hidden) s.pause(); else s.run(); };
    document.addEventListener('visibilitychange', s.onVis);
    // 最初の 1 フレームを描いてから、静止画とクロスフェードする
    ctx.update(0);
    ctx.renderer.render(ctx.scene, ctx.camera);
    if (!document.hidden) s.run();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (cur !== s) return;
      canvas.classList.add('on');
      box.classList.add('has-3d');
      // クロスフェードが終わったら映像があれば止めて外す（見えない映像を回し続けない）
      setTimeout(() => { const v = box.querySelector('video'); if (v && cur === s) v.remove(); }, FADE_MS + 60);
    }));
    return true;
  }

  function disposeCtx(ctx) {
    try {
      if (ctx.ro) ctx.ro.disconnect();
      window.removeEventListener('pointermove', ctx.onMove);
      ctx.scene.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        const ms = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
        for (const m of ms) {
          for (const k of Object.keys(m)) { const v = m[k]; if (v && v.isTexture) v.dispose(); }
          if (m.uniforms) for (const k of Object.keys(m.uniforms)) { const v = m.uniforms[k].value; if (v && v.isTexture) v.dispose(); }
          m.dispose();
        }
      });
      ctx.renderer.dispose();
      if (ctx.renderer.forceContextLoss) ctx.renderer.forceContextLoss();
      if (ctx.canvas && ctx.canvas.parentNode) ctx.canvas.parentNode.removeChild(ctx.canvas);
    } catch (e) { /* 解放に失敗しても画面は動かす */ }
  }

  function stop() {
    const s = cur;
    if (!s) return;
    cur = null;
    s.abort.v = true;
    clearTimeout(s.timer);
    if (s.pause) s.pause();
    if (s.onVis) document.removeEventListener('visibilitychange', s.onVis);
    if (s.canvas && s.canvas.parentNode) s.canvas.parentNode.classList.remove('has-3d');
    if (s.ctx) disposeCtx(s.ctx);
  }

  window.Hero3D = {
    start, stop, capable,
    active: () => !!(cur && cur.ctx),
    fps: () => (cur && cur.fps ? cur.fps.last : 0),
  };
})();
