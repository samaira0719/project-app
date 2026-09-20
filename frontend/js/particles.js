/**
 * Decide Well - login-screen dot mesh.
 *
 * A perspective grid of tiny dots displaced by a slow travelling wave field.
 * The pointer lifts the sheet around it and lets it settle back over a long,
 * low-gravity decay, so the surface keeps drifting after the mouse stops.
 * Scoped to the auth screen only - start() on show, stop() on enter.
 */

const CFG = {
  near: 1.5,          // nearest depth plane (world units)
  far: 8.0,           // farthest depth plane
  focal: 1.5,
  camY: 0.9,          // camera height above the sheet
  horizon: 0.1,       // horizon line as a fraction of canvas height
  amp: 0.22,          // wave amplitude in world units
  timeScale: 0.00042, // ms -> wave phase

  reach: 270,         // pointer influence radius, px
  lift: 52,           // base upward push under the cursor, px
  liftGain: 58,       // extra push scaled by pointer energy
  spread: 16,         // sideways nudge away from the cursor, px
  ease: 0.085,        // pointer follow - low = floatier
  decay: 0.982,       // energy decay per frame - high = long moon-like settle

  hueBuckets: 12,
  // deep blue -> cyan -> periwinkle, left to right
  ramp: [
    [56, 108, 232],
    [46, 158, 234],
    [42, 206, 218],
    [86, 214, 196],
    [124, 176, 246],
    [167, 139, 250],
  ],
};

let canvas = null;
let ctx = null;
let raf = 0;
let observer = null;
let t0 = 0;

// per-row geometry, rebuilt on resize
let rows = [];
let cols = 0;
let colX = null;      // screen x per column
let colHue = null;    // hue bucket per column
let width = 0;
let height = 0;

const pointer = {
  rawX: -9999, rawY: -9999,
  x: -9999, y: -9999,
  energy: 0,
  active: false,
};

let highlight = new Float32Array(0);
let highlightLen = 0;

const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const lerp = (a, b, k) => a + (b - a) * k;

function rampColor(k) {
  const r = CFG.ramp;
  const pos = clamp(k, 0, 1) * (r.length - 1);
  const i = Math.min(Math.floor(pos), r.length - 2);
  const f = pos - i;
  return [
    Math.round(lerp(r[i][0], r[i + 1][0], f)),
    Math.round(lerp(r[i][1], r[i + 1][1], f)),
    Math.round(lerp(r[i][2], r[i + 1][2], f)),
  ];
}

function build() {
  const w = width;
  const h = height;

  cols = clamp(Math.round(w / 8), 70, 170);
  const rowCount = clamp(Math.round(h / 15), 24, 52);

  // dots are spaced evenly across the screen, not across the world plane -
  // otherwise the near rows would run several screens wide and mostly waste work
  colX = new Float32Array(cols);
  colHue = new Uint8Array(cols);
  const x0 = -0.05 * w;
  const dx = (w * 1.1) / (cols - 1);
  for (let i = 0; i < cols; i++) {
    colX[i] = x0 + i * dx;
    colHue[i] = Math.min(CFG.hueBuckets - 1, Math.floor((i / cols) * CFG.hueBuckets));
  }

  const S = Math.min(w * 0.62, h * 0.95);
  const horizonY = h * CFG.horizon;
  const cx = w / 2;

  rows = [];
  for (let j = 0; j < rowCount; j++) {
    // geometric depth spacing keeps the rows roughly even on screen
    const z = CFG.near * Math.pow(CFG.far / CFG.near, j / (rowCount - 1));
    const p = CFG.focal / z;
    const ps = p * S;
    const depth = j / (rowCount - 1); // 0 = near, 1 = far
    const alpha = lerp(0.9, 0.26, depth);

    const colors = [];
    for (let b = 0; b < CFG.hueBuckets; b++) {
      const [r, g, bl] = rampColor(b / (CFG.hueBuckets - 1));
      colors.push(`rgba(${r},${g},${bl},${alpha.toFixed(3)})`);
    }

    rows.push({
      z,
      ps,
      cx,
      baseY: horizonY + CFG.camY * ps,
      invScale: 1 / ps,
      size: clamp(ps * 0.0055, 0.55, 2.1),
      colors,
    });
  }

  highlight = new Float32Array(cols * rowCount * 2);
}

function wave(x, z, t) {
  return (
    0.62 * Math.sin(x * 0.42 + z * 0.20 + t * 0.21) +
    0.40 * Math.sin(x * 0.75 - z * 0.45 - t * 0.16) +
    0.26 * Math.sin(x * 0.28 + z * 0.90 + t * 0.31) +
    0.14 * Math.sin(x * 1.40 + t * 0.13)
  );
}

function resize() {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  width = rect.width;
  height = rect.height;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  build();
}

function frame(now) {
  raf = requestAnimationFrame(frame);
  if (!rows.length) return;

  const t = (now - t0) * CFG.timeScale;

  // the cursor is chased, never snapped to - the lag is most of the float
  if (pointer.active) {
    const nx = lerp(pointer.x, pointer.rawX, CFG.ease);
    const ny = lerp(pointer.y, pointer.rawY, CFG.ease);
    pointer.energy = Math.min(1, pointer.energy + Math.hypot(nx - pointer.x, ny - pointer.y) * 0.02);
    pointer.x = nx;
    pointer.y = ny;
  }
  pointer.energy *= CFG.decay;

  const reach = CFG.reach;
  const push = CFG.lift + CFG.liftGain * pointer.energy;
  const live = pointer.active || pointer.energy > 0.002;

  ctx.clearRect(0, 0, width, height);
  highlightLen = 0;

  for (let j = 0; j < rows.length; j++) {
    const row = rows[j];
    const { ps, baseY, cx, invScale, size, colors, z } = row;
    const half = size * 0.5;
    let bucket = -1;

    for (let i = 0; i < cols; i++) {
      const hue = colHue[i];
      if (hue !== bucket) {
        bucket = hue;
        ctx.fillStyle = colors[bucket];
      }

      let sx = colX[i];
      const worldX = (sx - cx) * invScale;
      let sy = baseY - wave(worldX, z, t) * CFG.amp * ps;

      if (live) {
        const dx = sx - pointer.x;
        const dy = sy - pointer.y;
        const d = Math.hypot(dx, dy);
        if (d < reach) {
          const k = 1 - d / reach;
          const f = k * k * (3 - 2 * k); // smoothstep falloff
          sy -= f * push;
          if (d > 0.001) sx += (dx / d) * f * CFG.spread;
          if (f > 0.35) {
            highlight[highlightLen++] = sx;
            highlight[highlightLen++] = sy;
          }
        }
      }

      if (sy < -40 || sy > height + 40) continue;
      ctx.fillRect(sx - half, sy - half, size, size);
    }
  }

  // a second pass paints the dots the cursor is holding up
  if (highlightLen) {
    ctx.fillStyle = `rgba(214,238,255,${(0.5 + 0.45 * pointer.energy).toFixed(3)})`;
    for (let i = 0; i < highlightLen; i += 2) {
      ctx.fillRect(highlight[i] - 1.1, highlight[i + 1] - 1.1, 2.2, 2.2);
    }
  }
}

function onPointerMove(event) {
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  if (!pointer.active) { pointer.x = x; pointer.y = y; }
  pointer.rawX = x;
  pointer.rawY = y;
  pointer.active = true;
}

function onPointerLeave() {
  pointer.active = false;
}

export function startParticles() {
  if (raf) return;
  canvas = document.getElementById("auth-canvas");
  if (!canvas) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  ctx = canvas.getContext("2d");
  if (!ctx) return;

  t0 = performance.now();
  resize();
  observer = new ResizeObserver(resize);
  observer.observe(canvas);
  window.addEventListener("pointermove", onPointerMove, { passive: true });
  window.addEventListener("pointerleave", onPointerLeave, { passive: true });
  raf = requestAnimationFrame(frame);
}

export function stopParticles() {
  if (!raf) return;
  cancelAnimationFrame(raf);
  raf = 0;
  observer?.disconnect();
  observer = null;
  window.removeEventListener("pointermove", onPointerMove);
  window.removeEventListener("pointerleave", onPointerLeave);
  if (ctx) ctx.clearRect(0, 0, width, height);
  rows = [];
  pointer.active = false;
  pointer.energy = 0;
}
