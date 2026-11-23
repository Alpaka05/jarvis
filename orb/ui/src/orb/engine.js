/* Jarvis Orb – die Kugel selbst.
 *
 * Eine verformte, leuchtende Kugel (Three.js) und darüber die Ringe (Canvas 2D). Gebraucht vom
 * Overlay (klein in einer Bildschirmecke, overlay.js) und vom Dashboard (groß in der Mitte,
 * hud/OrbStage.svelte). Wo der Orb sitzt und wie groß er ist, bestimmen die Optionen.
 */
import * as THREE from "three";

const TAU = Math.PI * 2;
const TS = matchMedia("(prefers-reduced-motion: reduce)").matches ? 0.35 : 1;

const PALETTE = { base: [0.31, 0.85, 1.0], listen: [0.30, 0.62, 1.0], think: [1.0, 0.71, 0.28] };
const ERROR_COL = [1.0, 0.33, 0.40];
export const STATES = {
  idle:      { label: "",           scale: .45, amp: .05, speed: .25, glow: .6,  bright: .7,  ring: .12, ringA: .4,  bars: 0, spin: 0, pSpeed: .15, pSpread: .6,  pAlpha: .35, levelAmp: 0,   col: "base" },
  wake:      { label: "Aktiviert",  scale: .45, amp: .16, speed: .9,  glow: 1.4, bright: 1.1, ring: 1.4, ringA: 1,   bars: 0, spin: 0, pSpeed: 1.2, pSpread: 1.3, pAlpha: 1,   levelAmp: 0,   col: "base" },
  listening: { label: "Höre zu",    scale: .45, amp: .07, speed: .6,  glow: 1.0, bright: .95, ring: .35, ringA: .85, bars: 1, spin: 0, pSpeed: .4,  pSpread: 1,   pAlpha: .7,  levelAmp: .24, col: "listen" },
  thinking:  { label: "Denke nach", scale: .45, amp: .13, speed: 1.3, glow: 1.1, bright: .9,  ring: 1.8, ringA: .9,  bars: 0, spin: 1, pSpeed: 1.6, pSpread: .8,  pAlpha: 1,   levelAmp: 0,   col: "think" },
  speaking:  { label: "Spricht",    scale: .45, amp: .06, speed: .7,  glow: 1.15,bright: 1.0, ring: .5,  ringA: .9,  bars: 1, spin: 0, pSpeed: .6,  pSpread: 1.1, pAlpha: .8,  levelAmp: .28, col: "base" },
};
const KEYS = ["scale", "amp", "speed", "glow", "bright", "ring", "ringA", "bars", "spin", "pSpeed", "pSpread", "pAlpha", "levelAmp"];
const TOOL_MS = 1800, ERROR_MS = 1500, LEVEL_STALE_MS = 250, IDLE_FPS = 24;

const NOISE = `
  vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
  vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
  vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
  vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
  float snoise(vec3 v){
    const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
    vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
    vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
    vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
    i=mod289(i);
    vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
    float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
    vec4 j=p-49.0*floor(p*ns.z*ns.z); vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
    vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
    vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
    vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
    vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
    vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
    vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
    p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
    vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
    return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
  }`;
const BLOB_VS = NOISE + `
  uniform float uTime, uAmp, uFreq;
  varying float vDisp; varying vec3 vN;
  void main(){
    vec3 n = normalize(position);
    float d = snoise(n*uFreq + vec3(0.0, uTime*0.6, uTime))*0.7 + snoise(n*uFreq*2.4 - vec3(uTime*0.8))*0.3;
    d *= uAmp; vDisp = d;
    vN = normalize(normalMatrix * n);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(n*(1.0+d), 1.0);
  }`;
const BLOB_FS = `
  uniform vec3 uColor; uniform float uGlow, uBright, uFlash, uCore;
  varying float vDisp; varying vec3 vN;
  void main(){
    float f = 1.0 - abs(vN.z);
    float rim = pow(f, 2.0);
    float center = pow(1.0 - f, 4.0);
    vec3 col = uColor*(0.10 + rim*1.4*uGlow) + uColor*max(vDisp, 0.0)*3.0 + mix(uColor, vec3(1.0), 0.6)*center*uCore;
    col += vec3(uFlash)*0.6;
    float a = clamp((0.25 + rim*0.9 + center*uCore)*uBright, 0.0, 1.0);
    gl_FragColor = vec4(col*uBright, a);
  }`;
const HALO_VS = `varying vec2 vP; void main(){ vP = uv*2.0-1.0; gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }`;
const HALO_FS = `
  uniform vec3 uColor; uniform float uGlow; varying vec2 vP;
  void main(){ float r = length(vP); float a = (exp(-r*r*7.0)*0.5 + exp(-r*r*38.0)*0.35)*uGlow*smoothstep(1.0, 0.7, r); gl_FragColor = vec4(uColor, a); }`;
const PTS_VS = `
  attribute vec4 aSeed; attribute float aTilt;
  uniform float uTime, uSpread, uSize, uDpr; varying float vA;
  void main(){
    float th = aSeed.y + uTime*aSeed.w;
    float r = 1.0 + (aSeed.x - 1.0)*uSpread;
    vec3 p = vec3(cos(th)*r, sin(th)*r*cos(aSeed.z), sin(th)*r*sin(aSeed.z));
    float c = cos(aTilt), s = sin(aTilt);
    p = vec3(c*p.x + s*p.z, p.y, -s*p.x + c*p.z);
    vA = (0.35 + 0.65*fract(aSeed.y*13.7)) * smoothstep(-2.4, 0.4, p.z);
    gl_Position = projectionMatrix*modelViewMatrix*vec4(p, 1.0);
    gl_PointSize = uSize*uDpr*(0.5 + fract(aSeed.x*31.3));
  }`;
const PTS_FS = `
  uniform vec3 uColor; uniform float uAlpha; varying float vA;
  void main(){ float r = length(gl_PointCoord - 0.5); if (r > 0.5) discard; gl_FragColor = vec4(uColor, smoothstep(0.5, 0.0, r)*vA*uAlpha); }`;

/**
 * Baut einen Orb in `stage` (enthält die beiden Canvas-Elemente).
 *
 * Optionen:
 *   anchor(W, H, rand)  → {x, y}: Mittelpunkt des Orbs; `rand` ist der Platz, den er samt Ringen braucht
 *   radius(W, H)        → Grundgröße (der äußere Ring ist etwa 0,96 × so groß)
 *   visible()           → false: es wird nichts gezeichnet (spart Akku, wenn ausgeblendet)
 *   onFrame(cx, cy, rD, now)  nach jedem gezeichneten Bild (z.B. Untertitel daneben setzen)
 *   toolCallout, stateLabel   Beschriftungen auf der Zeichenfläche (Standard: an)
 */
export function createOrb(stageEl, glCanvas, hud, options = {}) {
  const opts = Object.assign({
    anchor: (W, H) => ({ x: W / 2, y: H / 2 }),
    radius: (W, H) => Math.min(W, H) * 0.11,
    visible: () => true,
    onFrame: null,
    toolCallout: true,
    stateLabel: true,
  }, options);
  const hctx = hud.getContext("2d");

  let state = "idle", renderUntil = 0, stopped = false, raf = 0;
  let toolName = "", toolUntil = 0, errorUntil = 0;
  let levelTarget = 0, levelAt = 0, level = 0;
  let ring1 = 0, ring2 = 0, spinA = 0, flash = 0, shake = 0, t = 0, moving = true;
  const cur = Object.assign({}, STATES.idle, { col: PALETTE.base.slice(), cx: -1, cy: -1, toolA: 0, labelA: 0 });
  let W = 1, H = 1, DPR = 1, R = 100;

  let gl = null;
  try {
    const renderer = new THREE.WebGLRenderer({ canvas: glCanvas, alpha: true, antialias: true });
    renderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -5000, 5000);
    camera.position.z = 1000;
    const additive = { transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, depthTest: false };
    const add = (o) => { o.frustumCulled = false; scene.add(o); return o; };
    const blobMat = (core) => new THREE.ShaderMaterial(Object.assign({
      uniforms: { uTime: { value: 0 }, uAmp: { value: .1 }, uFreq: { value: 1.6 }, uColor: { value: new THREE.Color() }, uGlow: { value: 1 }, uBright: { value: 1 }, uFlash: { value: 0 }, uCore: { value: core } },
      vertexShader: BLOB_VS, fragmentShader: BLOB_FS,
    }, additive));
    const halo = add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.ShaderMaterial(Object.assign({
      uniforms: { uColor: { value: new THREE.Color() }, uGlow: { value: 1 } },
      vertexShader: HALO_VS, fragmentShader: HALO_FS,
    }, additive))));
    const core = add(new THREE.Mesh(new THREE.IcosahedronGeometry(1, 48), blobMat(.35)));
    const inner = add(new THREE.Mesh(new THREE.IcosahedronGeometry(1, 24), blobMat(1.2)));
    const shell = add(new THREE.LineSegments(new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(1, 2)),
      new THREE.LineBasicMaterial(Object.assign({ color: 0xffffff, opacity: .1 }, additive))));
    const N = 900, seeds = new Float32Array(N * 4), tilts = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      seeds.set([1.25 + Math.pow(Math.random(), 1.6) * 1.1, Math.random() * TAU, (Math.random() - .5) * 2.6, (.2 + Math.random() * .8) * (Math.random() < .5 ? -1 : 1)], i * 4);
      tilts[i] = Math.random() * TAU;
    }
    const pGeo = new THREE.BufferGeometry();
    pGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(N * 3), 3));
    pGeo.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 4));
    pGeo.setAttribute("aTilt", new THREE.BufferAttribute(tilts, 1));
    const points = add(new THREE.Points(pGeo, new THREE.ShaderMaterial(Object.assign({
      uniforms: { uTime: { value: 0 }, uSpread: { value: 1 }, uSize: { value: 2.4 }, uDpr: { value: 1 }, uColor: { value: new THREE.Color() }, uAlpha: { value: 1 } },
      vertexShader: PTS_VS, fragmentShader: PTS_FS,
    }, additive))));
    gl = { renderer, scene, camera, halo, core, inner, shell, points, coreTime: 0, pTime: 0 };
  } catch (e) {
    gl = null; // ohne WebGL zeichnet die HUD-Ebene einen einfachen Leuchtkern
  }

  // ── Größe: folgt der Bühne (ganzes Fenster beim Overlay, ein Feld im Dashboard) ──
  function resize() {
    const rect = stageEl.getBoundingClientRect();
    W = Math.max(1, rect.width); H = Math.max(1, rect.height);
    DPR = Math.min(devicePixelRatio || 1, 2);
    hud.width = Math.round(W * DPR); hud.height = Math.round(H * DPR);
    R = opts.radius(W, H);
    if (gl) {
      gl.renderer.setPixelRatio(DPR); gl.renderer.setSize(W, H, false);
      Object.assign(gl.camera, { left: -W / 2, right: W / 2, top: H / 2, bottom: -H / 2 });
      gl.camera.updateProjectionMatrix();
      gl.points.material.uniforms.uDpr.value = DPR;
    }
    moving = true;
  }
  addEventListener("resize", resize);
  const observer = typeof ResizeObserver === "function" ? new ResizeObserver(resize) : null;
  if (observer) observer.observe(stageEl);
  resize();

  // ── HUD-Ebene ────────────────────────────────────────────────────────────
  const rgba = (c, a) => `rgba(${c[0] * 255 | 0},${c[1] * 255 | 0},${c[2] * 255 | 0},${Math.max(0, Math.min(1, a)).toFixed(3)})`;
  const whiten = (c, k) => c.map((v) => v + (1 - v) * k);

  function drawHud(cx, cy, Rs) {
    const c = hctx, col = cur.col, A = cur.ringA;
    c.setTransform(DPR, 0, 0, DPR, 0, 0);
    c.clearRect(0, 0, W, H);
    c.globalCompositeOperation = "lighter";
    c.lineCap = "round";

    if (!gl) {
      const g = c.createRadialGradient(cx, cy, 0, cx, cy, Rs * 1.5);
      g.addColorStop(0, rgba(whiten(col, .7), .95 * cur.bright));
      g.addColorStop(.4, rgba(col, .55 * cur.bright * (1 + level * .5)));
      g.addColorStop(1, rgba(col, 0));
      c.fillStyle = g; c.beginPath(); c.arc(cx, cy, Rs * 1.5, 0, TAU); c.fill();
    }

    c.lineWidth = 1; c.strokeStyle = rgba(col, .25 * A);
    c.beginPath(); c.arc(cx, cy, Rs * 1.42, 0, TAU); c.stroke();

    // Skalenring
    const rT = Rs * 1.62, major = new Path2D(), minor = new Path2D();
    for (let i = 0; i < 120; i++) {
      const a = i / 120 * TAU + ring1, big = i % 10 === 0, p = big ? major : minor;
      const r1 = rT + (big ? 9 : 4);
      p.moveTo(cx + Math.cos(a) * rT, cy + Math.sin(a) * rT);
      p.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
    }
    c.strokeStyle = rgba(col, .35 * A); c.lineWidth = 1; c.stroke(minor);
    c.strokeStyle = rgba(col, .85 * A); c.lineWidth = 1.6; c.stroke(major);

    // Bogensegmente
    const rB = Rs * 1.88;
    c.lineWidth = 2.5; c.strokeStyle = rgba(col, .75 * A);
    for (const [o, l] of [[0, .9], [1.45, .5], [2.6, 1.3], [4.45, .7]]) {
      c.beginPath(); c.arc(cx, cy, rB, ring2 + o, ring2 + o + l); c.stroke();
    }
    // Außenring gestrichelt
    const rD = Rs * 2.14;
    c.setLineDash([2, 7]); c.lineDashOffset = ring1 * 60; c.lineWidth = 1; c.strokeStyle = rgba(col, .35 * A);
    c.beginPath(); c.arc(cx, cy, rD, 0, TAU); c.stroke();
    c.setLineDash([]);

    // Pegel-Strahlen
    if (cur.bars > .01) {
      const n = 96, r0 = Rs * 1.3, p = new Path2D();
      for (let i = 0; i < n; i++) {
        const a = i / n * TAU - Math.PI / 2;
        const wob = .5 + .5 * Math.sin(i * 1.9 + t * 9) * Math.sin(i * .53 - t * 4.3 + 1);
        const len = 1.5 + level * (.3 + .7 * wob) * Rs * .27 * cur.bars;
        p.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
        p.lineTo(cx + Math.cos(a) * (r0 + len), cy + Math.sin(a) * (r0 + len));
      }
      c.lineWidth = 2; c.strokeStyle = rgba(whiten(col, .2), .8 * cur.bars); c.stroke(p);
    }

    // Denk-Spinner
    if (cur.spin > .01) {
      c.lineWidth = 3; c.strokeStyle = rgba(whiten(col, .3), .9 * cur.spin);
      for (const o of [0, Math.PI]) { c.beginPath(); c.arc(cx, cy, rT - 8, spinA + o, spinA + o + 1.1); c.stroke(); }
      c.lineWidth = 1.5; c.strokeStyle = rgba(col, .7 * cur.spin);
      c.beginPath(); c.arc(cx, cy, rB + 9, -spinA * .7, -spinA * .7 + 2.2); c.stroke();
    }

    // Tool-Beschriftung
    if (opts.toolCallout && cur.toolA > .01 && toolName) {
      const right = cx + rB + 170 < W, down = cy - rB - 80 < 0, dir = right ? 1 : -1;
      const ang = (right ? 1 : 3) * Math.PI / 4 * (down ? 1 : -1);
      c.lineWidth = 5; c.strokeStyle = rgba(whiten(col, .2), cur.toolA);
      c.beginPath(); c.arc(cx, cy, rB, ang - .22, ang + .22); c.stroke();
      const x0 = cx + Math.cos(ang) * (rB + 6), y0 = cy + Math.sin(ang) * (rB + 6);
      const x1 = x0 + dir * 26, y1 = y0 + (down ? 26 : -26), x2 = x1 + dir * 90;
      c.lineWidth = 1; c.strokeStyle = rgba(col, .8 * cur.toolA);
      c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.lineTo(x2, y1); c.stroke();
      c.textAlign = right ? "left" : "right";
      c.fillStyle = rgba(col, .7 * cur.toolA); c.font = "500 10px ui-monospace, Menlo, Consolas, monospace";
      c.fillText("TOOL-AUFRUF", x1, down ? y1 + 14 : y1 - 22);
      c.fillStyle = rgba(whiten(col, .4), cur.toolA); c.font = "600 15px system-ui, -apple-system, 'Segoe UI', sans-serif";
      c.fillText(toolName.toUpperCase(), x1, down ? y1 + 30 : y1 - 6);
    }

    // Zustandsname zur Bildschirmmitte hin (unter dem Orb an oberen Ecken, sonst darüber)
    if (opts.stateLabel && cur.labelA > .01 && STATES[state].label) {
      c.textAlign = "center";
      c.fillStyle = rgba(whiten(col, .3), .85 * cur.labelA);
      c.font = "600 12px system-ui, -apple-system, 'Segoe UI', sans-serif";
      if ("letterSpacing" in c) c.letterSpacing = "3px";
      c.fillText(STATES[state].label.toUpperCase(), cx, cy < H / 2 ? cy + rD + 26 : cy - rD - 16);
      if ("letterSpacing" in c) c.letterSpacing = "0px";
    }

    // Wake-Blitz
    if (flash > 0) {
      c.lineWidth = 2 + flash * 4; c.strokeStyle = rgba(whiten(col, .5), flash * .9);
      c.beginPath(); c.arc(cx, cy, Rs * (1 + (1 - flash) * 1.9), 0, TAU); c.stroke();
    }
  }

  // ── Hauptschleife ────────────────────────────────────────────────────────
  let last = performance.now(), pending = 0;
  function frame(now) {
    raf = requestAnimationFrame(frame);
    const realDt = Math.min(.1, (now - last) / 1000); last = now;
    if (stopped) return;
    if (!opts.visible() && now > renderUntil) return; // ausgeblendet: nichts zeichnen
    pending += realDt;
    const calm = state === "idle" && !moving && now > toolUntil + 500 && now > errorUntil && flash === 0 && shake === 0;
    if (calm && pending < 1 / IDLE_FPS) return; // Ruhezustand: weniger Bilder pro Sekunde
    const dtReal = Math.min(.1, pending), dt = dtReal * TS;
    pending = 0; t += dt;

    let target = 0;
    if ((state === "listening" || state === "speaking") && now - levelAt < LEVEL_STALE_MS) target = levelTarget;
    level += (target - level) * (1 - Math.exp(-dtReal * (target > level ? 30 : 9)));

    const s = STATES[state], k = 1 - Math.exp(-dt * 5);
    for (const key of KEYS) cur[key] += (s[key] - cur[key]) * k;
    const colGoal = now < errorUntil ? ERROR_COL : PALETTE[s.col];
    cur.col = cur.col.map((v, i) => v + (colGoal[i] - v) * k);
    cur.toolA += ((now < toolUntil ? 1 : 0) - cur.toolA) * k;
    cur.labelA += ((state === "idle" ? 0 : 1) - cur.labelA) * k;

    const rest = opts.anchor(W, H, R * STATES.idle.scale * 2.3 + 24), gx = rest.x, gy = rest.y;
    if (cur.cx < 0) { cur.cx = gx; cur.cy = gy; }
    const kp = 1 - Math.exp(-dt * 4);
    cur.cx += (gx - cur.cx) * kp; cur.cy += (gy - cur.cy) * kp;
    moving = Math.abs(gx - cur.cx) > .5 || Math.abs(gy - cur.cy) > .5 || Math.abs(s.scale - cur.scale) > .002;

    ring1 += dt * cur.ring * .35; ring2 -= dt * cur.ring * .22; spinA += dt * (2.4 + cur.ring);
    flash = Math.max(0, flash - dt * 1.4); shake = Math.max(0, shake - dt * 1.5);

    const cx = cur.cx + Math.sin(t * 70) * shake * 10, cy = cur.cy;
    const Rs = R * cur.scale * (1 + level * .05 * cur.bars);
    const amp = Math.min(.32, cur.amp + level * cur.levelAmp);

    if (gl) {
      const x = cx - W / 2, y = H / 2 - cy, col = cur.col;
      gl.core.position.set(x, y, 0); gl.core.scale.setScalar(Rs);
      gl.inner.position.set(x, y, 1); gl.inner.scale.setScalar(Rs * .52);
      gl.shell.position.set(x, y, 0); gl.shell.scale.setScalar(Rs * 1.2);
      gl.shell.rotation.y += dt * (.1 + cur.ring * .2); gl.shell.rotation.x += dt * .05;
      gl.shell.material.color.setRGB(col[0], col[1], col[2]); gl.shell.material.opacity = .1 * cur.ringA;
      gl.halo.position.set(x, y, -1); gl.halo.scale.setScalar(Rs * 3.2);
      gl.halo.material.uniforms.uColor.value.setRGB(col[0], col[1], col[2]);
      gl.halo.material.uniforms.uGlow.value = cur.glow * (.75 + level * .6) + flash;
      gl.coreTime += dt * cur.speed * (1 + level * 1.5);
      for (const [m, f, a, wc] of [[gl.core, 1.5, amp, 0], [gl.inner, 2.8, amp * .8, .55]]) {
        const u = m.material.uniforms;
        u.uTime.value = gl.coreTime; u.uAmp.value = a; u.uFreq.value = f;
        u.uColor.value.setRGB(...whiten(col, wc)); u.uGlow.value = cur.glow; u.uBright.value = cur.bright * (1 + level * .35); u.uFlash.value = flash;
      }
      const pu = gl.points.material.uniforms;
      gl.points.position.set(x, y, 0); gl.points.scale.setScalar(Rs);
      gl.pTime += dt * cur.pSpeed;
      pu.uTime.value = gl.pTime; pu.uSpread.value = cur.pSpread * (1 + level * .25); pu.uAlpha.value = cur.pAlpha;
      pu.uColor.value.setRGB(...whiten(col, .25));
      gl.renderer.render(gl.scene, gl.camera);
    }
    drawHud(cx, cy, Rs);
    if (opts.onFrame) opts.onFrame(cx, cy, Rs * 2.14, now);
  }
  raf = requestAnimationFrame(frame);

  return {
    get state() { return state; },
    /** Zustand setzen; true, wenn es ihn gibt. */
    setState(name) {
      if (!STATES[name]) return false;
      state = name;
      if (name === "wake") flash = 1;
      return true;
    },
    setLevel(value) {
      levelTarget = Math.max(0, Math.min(1, Number(value) || 0));
      levelAt = performance.now();
    },
    tool(name) { toolName = String(name || ""); toolUntil = performance.now() + TOOL_MS; },
    error() { errorUntil = performance.now() + ERROR_MS; shake = 1; },
    /** Neu einschwingen (z.B. nach Eckenwechsel oder Einblenden). */
    poke() { moving = true; },
    /** Auch ausgeblendet noch `ms` weiterzeichnen (Ausblenden zu Ende führen). */
    keepRendering(ms) { renderUntil = performance.now() + ms; },
    resize,
    destroy() {
      stopped = true;
      cancelAnimationFrame(raf);
      removeEventListener("resize", resize);
      if (observer) observer.disconnect();
      if (gl) gl.renderer.dispose();
    },
  };
}
