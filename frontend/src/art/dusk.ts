/*
 * dusk: the end of the working day over a rental portfolio. The sun is low on the horizon to the right, so the
 * sky runs from ink overhead through indigo to a hot amber band along the rooftops. Long thin clouds catch the
 * light on their undersides and drift east. A hazy far skyline stands against the glow with a few windows lit,
 * the wet street below throws back a shimmering column of sun, and stars come out in the dark half of the sky.
 *
 * Static light (sky, sun, far skyline, street) is shaded once per size. Each frame re-shades only the cloud band,
 * the stars, the far windows and the street reflection. Every cell is drawn as a halftone dot sized by its
 * brightness, ordered-dithered, in the palette colour nearest its hue.
 */
export interface SceneFrame {
  glyph: Uint8Array;            // 0 empty, 1-3 dot sizes, 4 a bright star cross
  color: Uint8Array;            // palette index
  changed: Int32Array | null;   // null = redraw everything
  count: number;                // number of valid entries in `changed`
}

export interface Scene {
  palette: string[];
  ground: string;
  create(cols: number, rows: number, cellAspect: number): (t: number) => SceneFrame;
}

const PALETTE = [
  // ink and night blues
  "#0b1220", "#111a2e", "#18223b", "#202c4a", "#2a3a5e", "#3a4d74", "#55688e", "#7d8db0", "#a9b6cf",
  // dusk indigo and violet
  "#1a1633", "#251d45", "#33265a", "#46306c", "#5d3a78",
  // rose into coral
  "#6e3a5e", "#8a4258", "#a84c52", "#c4553f", "#e0643f", "#f07a45", "#f39350",
  // amber and gold
  "#f5ab5a", "#f7c26e", "#f9d68a", "#fbe6b0",
  // cream and starlight
  "#fdf3dc", "#fffaf0", "#eef2fb",
  // the verified-lease lime, for a few far windows
  "#c6f36b", "#8fb84a", "#5f7f33",
  // street haze
  "#2f4656", "#3f5d6e",
];

const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5].map((v) => v / 16 - 0.47);
const COVER = [0, 0.3, 0.62, 1];

type RGB = [number, number, number];
const ZENITH: RGB = [0.018, 0.03, 0.07];
const MID: RGB = [0.06, 0.05, 0.15];
const LOW: RGB = [0.3, 0.11, 0.22];
const HORIZON: RGB = [0.9, 0.42, 0.22];
const HORIZON_HOT: RGB = [1.0, 0.76, 0.46];
const SUN_LIGHT: RGB = [1.0, 0.7, 0.4];
const FAR: RGB = [0.06, 0.045, 0.11];
const STREET_NEAR: RGB = [0.06, 0.05, 0.1];
const STREET_FAR: RGB = [0.022, 0.028, 0.05];

function hash(x: number, y: number): number {
  let h = Math.imul(x | 0, 0x27d4eb2d) ^ Math.imul(y | 0, 0x165667b1);
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

// value noise, optionally periodic in x so the cloud strip can loop without a seam
function noise(x: number, y: number, period: number): number {
  const xi = Math.floor(x), yi = Math.floor(y);
  const fx = x - xi, fy = y - yi;
  const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
  let x0 = xi, x1 = xi + 1;
  if (period > 0) {
    x0 = ((xi % period) + period) % period;
    x1 = (x0 + 1) % period;
  }
  const a = hash(x0, yi), b = hash(x1, yi), c = hash(x0, yi + 1), d = hash(x1, yi + 1);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}

function fbm(x: number, y: number, octaves: number, period: number): number {
  let s = 0, n = 0, amp = 0.5, f = 1;
  for (let i = 0; i < octaves; i++) {
    s += amp * noise(x * f, y * f, period * f);
    n += amp;
    amp *= 0.5;
    f *= 2;
  }
  return s / n;
}

const clamp = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);
const smooth = (a: number, b: number, v: number) => {
  const k = clamp((v - a) / (b - a));
  return k * k * (3 - 2 * k);
};
const mix = (a: number, b: number, k: number) => a + (b - a) * k;
const hex = (s: string): RGB => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16) / 255) as RGB;

export const dusk: Scene = {
  palette: PALETTE,
  ground: "#070b14",
  create(cols, rows, aspect) {
    const N = cols * rows;
    const W = (cols * aspect) / rows;          // scene width in units of its height
    const YH = 0.865;                           // horizon, level with the near buildings' footings
    const SX = W * 0.84, SY = 0.585, SR = 0.072; // the sun, a clear disc just above the rooftops
    const xu = (x: number) => ((x + 0.5) * aspect) / rows;
    const yu = (r: number) => (r + 0.5) / rows;
    const toward = (x: number, width: number) => Math.exp(-(((xu(x) - SX) / (W * width)) ** 2));

    // --- palette lookup -------------------------------------------------------------------------------
    const P = PALETTE.map(hex);
    const lut = new Uint8Array(32768).fill(255);
    const nearest = (r: number, g: number, b: number): number => {
      const k = (Math.min(31, (r * 31.99) | 0) << 10) | (Math.min(31, (g * 31.99) | 0) << 5) | Math.min(31, (b * 31.99) | 0);
      if (lut[k] !== 255) return lut[k];
      let best = 0, bd = 1e9;
      for (let i = 0; i < P.length; i++) {
        const dr = P[i][0] - r, dg = P[i][1] - g, db = P[i][2] - b;
        const d = 0.3 * dr * dr + 0.5 * dg * dg + 0.2 * db * db;
        if (d < bd) (bd = d), (best = i);
      }
      return (lut[k] = best);
    };

    const glyph = new Uint8Array(N);
    const color = new Uint8Array(N);
    // Halftone one cell: brightness picks the dot size (ordered dither), hue picks the ink, and the ink is
    // brightened to make up what the dot size couldn't. `cap` keeps dim regions in dark ink.
    const dot = (k: number, x: number, r: number, cr: number, cg: number, cb: number, cap: number, floor = 0) => {
      const peak = Math.max(cr, cg, cb, 1e-4);
      const level = clamp(floor + (1 - floor) * Math.pow(peak, 0.8) * 0.97);
      const step = Math.max(0, Math.min(3, Math.round(level * 3 + BAYER[(r & 3) * 4 + (x & 3)])));
      const want = step ? Math.min(1, (level + 0.06) / COVER[step]) : 0;
      const s = Math.min(cap, 0.3 + 0.7 * want) / peak;
      const g = step as number;
      const c = nearest(clamp(cr * s), clamp(cg * s), clamp(cb * s));
      if (glyph[k] !== g || color[k] !== c) {
        glyph[k] = g;
        color[k] = c;
        return true;
      }
      return false;
    };

    // --- static light -----------------------------------------------------------------------------------
    const sr = new Float32Array(N), sg = new Float32Array(N), sb = new Float32Array(N), cap = new Float32Array(N);
    const fl = new Float32Array(N);
    const inSun = (x: number, r: number) => Math.hypot(xu(x) - SX, (yu(r) - SY) * 1.15) < SR * 0.97;
    const sky = (x: number, r: number): RGB => {
      const t = clamp((YH - yu(r)) / YH);
      const band = (a: RGB, b: RGB, k: number): RGB => [mix(a[0], b[0], k), mix(a[1], b[1], k), mix(a[2], b[2], k)];
      // banded like a real sunset: hot at the rooftops, rose, then indigo into ink
      let c: RGB;
      if (t < 0.05) c = band(HORIZON_HOT, HORIZON, t / 0.05);
      else if (t < 0.24) c = band(HORIZON, LOW, smooth(0.05, 0.24, t));
      else if (t < 0.5) c = band(LOW, MID, smooth(0.24, 0.5, t));
      else c = band(MID, ZENITH, smooth(0.5, 1, t));
      const warm = toward(x, 0.3) * (1 - t) ** 4 * 0.45;
      const dx = xu(x) - SX, dy = (yu(r) - SY) * 1.15, d = Math.hypot(dx, dy);
      const glow = (d > SR ? 0.16 * Math.exp(-(d - SR) / 0.05) : 0) + 0.2 * Math.exp(-d / 0.14) + 0.07 * Math.exp(-d / 0.42);
      c = [c[0] + warm + glow * SUN_LIGHT[0], c[1] + warm * 0.55 + glow * SUN_LIGHT[1], c[2] + warm * 0.25 + glow * SUN_LIGHT[2]];
      if (d < SR) {
        // the disc: cream at the centre, deepening toward its rim
        const k = smooth(SR, SR * 0.9, d), rim = d / SR;
        c = [mix(c[0], 1.0, k), mix(c[1], 0.95 - 0.22 * rim * rim, k), mix(c[2], 0.82 - 0.45 * rim * rim, k)];
      }
      return c;
    };

    // the far skyline: blocks of varied width and height, a few towers, gaps for streets
    const roof = new Float32Array(cols);
    const block = new Int32Array(cols).fill(-1);
    for (let x = 0, id = 0; x < cols; id++) {
      const w = 2 + Math.floor(hash(id, 1) * 8);
      let h = 0.05 + 0.11 * hash(id, 2) ** 1.4;
      if (hash(id, 3) < 0.14) h += 0.07;
      for (let i = x; i < Math.min(cols, x + w); i++) (roof[i] = h), (block[i] = id);
      x += w + (hash(id, 4) < 0.25 ? 1 + Math.floor(hash(id, 5) * 3) : 0);
    }

    const windows: { k: number; x: number; r: number; rgb: RGB; phase: number; rate: number }[] = [];
    const reflection: { k: number; x: number; r: number; base: number }[] = [];
    for (let r = 0; r < rows; r++) {
      const y = yu(r);
      for (let x = 0; x < cols; x++) {
        const k = r * cols + x;
        let c: RGB;
        let cp = 0.35;
        if (y < YH && y > YH - roof[x]) {
          // a far building: hazier toward its base, its sun-facing edge and roofline catching the light
          const base = sky(x, r);
          const haze = 0.12 + 0.16 * smooth(YH - 0.2, YH, y); // a little of the glow bleeds into the far blocks
          c = [mix(FAR[0], base[0], haze), mix(FAR[1], base[1], haze), mix(FAR[2], base[2], haze)];
          const lit = toward(x, 0.5);
          if (block[x] !== block[x + 1] && xu(x) < SX) (c[0] += 0.3 * lit), (c[1] += 0.15 * lit), (c[2] += 0.06 * lit);
          if (y - (YH - roof[x]) < 1.2 / rows) (c[0] += 0.18 * lit), (c[1] += 0.09 * lit), (c[2] += 0.04 * lit);
          const inside = block[x] === block[x - 1] && block[x] === block[x + 1] && y - (YH - roof[x]) > 1.5 / rows;
          if (inside && (x & 1) === 0 && (r & 1) === 0 && hash(x * 7, r * 3) < 0.17) {
            const lime = hash(x, r + 91) < 0.14;
            windows.push({ k, x, r, rgb: lime ? [0.62, 0.86, 0.36] : [0.98, 0.66, 0.3], phase: hash(x + 3, r) * 6.28,
                           rate: 0.3 + hash(r, x) * 0.9 });
          }
        } else if (y >= YH) {
          // the street: dark, wet, a warm line at the horizon and a column of reflected sun
          const depth = (y - YH) / (1 - YH);
          c = [mix(STREET_NEAR[0], STREET_FAR[0], depth), mix(STREET_NEAR[1], STREET_FAR[1], depth),
               mix(STREET_NEAR[2], STREET_FAR[2], depth)];
          const line = Math.exp(-(((y - YH) / 0.012) ** 2)) * toward(x, 0.45);
          c = [c[0] + 0.55 * line, c[1] + 0.28 * line, c[2] + 0.12 * line];
          const rw = 0.02 + 0.07 * depth;
          const refl = Math.exp(-(((xu(x) - SX) / rw) ** 2)) * (1 - depth * 0.55);
          if (refl > 0.04) reflection.push({ k, x, r, base: refl });
          cp = 0.32;
        } else {
          c = sky(x, r);
          cp = 0.32 + 0.9 * Math.max(...c);
        }
        sr[k] = c[0], sg[k] = c[1], sb[k] = c[2], cap[k] = Math.min(1, cp);
        if (inSun(x, r)) (fl[k] = 0.35), (cap[k] = 1);
      }
    }

    // --- clouds: a strip twice the view's width, periodic so it loops without a seam ---------------------
    const LX = 7, LY = 1.8; // cells per noise unit across and down: long thin streaks
    const period = Math.ceil((cols * 2) / LX);
    const TWc = period * LX;
    const r0 = Math.floor(rows * 0.14), r1 = Math.ceil(rows * 0.52);
    const strip = new Float32Array((r1 - r0) * TWc);
    for (let r = r0; r < r1; r++) {
      const y = yu(r);
      const band = smooth(0.14, 0.24, y) * smooth(0.52, 0.42, y);
      for (let i = 0; i < TWc; i++) {
        const n = fbm(i / LX, r / LY + 11, 4, period);
        strip[(r - r0) * TWc + i] = smooth(0.6, 0.82, n) * band;
      }
    }
    const density = (x: number, r: number, off: number) => {
      if (r < r0 || r >= r1) return 0;
      const fx = (((x + off) % TWc) + TWc) % TWc, i0 = Math.floor(fx), a = fx - i0, i1 = (i0 + 1) % TWc;
      const row = (r - r0) * TWc;
      return strip[row + i0] * (1 - a) + strip[row + i1] * a;
    };

    // --- stars: the dark upper sky, thinning toward the glow -------------------------------------------
    const stars: { k: number; x: number; r: number; s: number; tw: number; p: number; ph: number; cross: boolean }[] = [];
    for (let r = 0; r < rows; r++) {
      const y = yu(r);
      if (y > YH - 0.42) break;
      for (let x = 0; x < cols; x++) {
        const t = (YH - y) / YH;
        if (hash(x * 13 + 5, r * 17 + 1) < 0.9895 - 0.006 * t) continue;
        if (Math.max(sr[r * cols + x], sg[r * cols + x], sb[r * cols + x]) > 0.16) continue;
        const m = hash(x + 29, r + 41) ** 3;
        stars.push({ k: r * cols + x, x, r, s: 0.2 + 0.55 * m, tw: hash(x, r + 7) < 0.35 ? 0.3 : 0.08,
                     p: 2.2 + 4 * hash(r, x + 3), ph: hash(x + 9, r) * 6.28, cross: false });
      }
    }
    // a few bright ones on the right, away from the headline
    stars.filter((s) => xu(s.x) > W * 0.5 && s.s > 0.4).slice(0, 4).forEach((s) => ((s.cross = true), (s.s = 0.95)));

    const cloudy = new Uint8Array(N);
    for (let r = r0; r < r1; r++) for (let x = 0; x < cols; x++) if (!(yu(r) > YH - roof[x] && yu(r) < YH)) cloudy[r * cols + x] = 1;
    const changed = new Int32Array(N);
    let first = true;

    return (t: number): SceneFrame => {
      let n = 0;
      const mark = (k: number, did: boolean) => {
        if (did && !first) changed[n++] = k;
      };
      if (first) {
        for (let k = 0; k < N; k++) dot(k, k % cols, (k / cols) | 0, sr[k], sg[k], sb[k], cap[k], fl[k]);
      }
      const off = t * 0.32; // cells per second, drifting east
      for (let r = r0; r < r1; r++) {
        for (let x = 0; x < cols; x++) {
          const k = r * cols + x;
          if (!cloudy[k]) continue;
          const d = density(x, r, off);
          if (d < 0.01 && !first) {
            mark(k, dot(k, x, r, sr[k], sg[k], sb[k], cap[k], fl[k]));
            continue;
          }
          // undersides catch the sun on its side of the sky; the rest are dark bars that hide the stars
          const under = clamp((d - density(x, r + 1, off)) * 4);
          const lit = toward(x, 0.28) * (0.15 + 0.85 * under);
          const cr = mix(0.07, 0.95, lit), cg = mix(0.055, 0.5, lit), cb = mix(0.14, 0.3, lit);
          const a = d * 0.85;
          mark(k, dot(k, x, r, mix(sr[k], cr, a), mix(sg[k], cg, a), mix(sb[k], cb, a),
                      Math.min(1, cap[k] + a * 0.6)));
        }
      }
      for (const s of stars) {
        const v = s.s * (1 - s.tw + s.tw * (0.5 + 0.5 * Math.sin((t / s.p) * 6.283 + s.ph))) * (1 - density(s.x, s.r, off));
        const k = s.k;
        if (s.cross && v > 0.5) {
          if (glyph[k] !== 4 || first) {
            glyph[k] = 4;
            color[k] = nearest(0.98, 0.98, 1);
            mark(k, true);
          }
        } else {
          mark(k, dot(k, s.x, s.r, Math.max(sr[k], v * 0.95), Math.max(sg[k], v * 0.97), Math.max(sb[k], v), 1));
        }
      }
      for (const w of windows) {
        const v = 0.55 + 0.3 * Math.sin(t * w.rate + w.phase) + (Math.sin(t * w.rate * 0.37 + w.phase * 2) > 0.92 ? -0.6 : 0);
        mark(w.k, dot(w.k, w.x, w.r, w.rgb[0] * v, w.rgb[1] * v, w.rgb[2] * v, 1));
      }
      for (const p of reflection) {
        const sh = noise(xu(p.x) * 55, yu(p.r) * 85 - t * 1.6, 0);
        const v = p.base * (0.25 + 0.95 * smooth(0.45, 0.9, sh));
        mark(p.k, dot(p.k, p.x, p.r, sr[p.k] + v * 0.95, sg[p.k] + v * 0.62, sb[p.k] + v * 0.32, 1));
      }
      const out: SceneFrame = { glyph, color, changed: first ? null : changed, count: n };
      first = false;
      return out;
    };
  },
};
