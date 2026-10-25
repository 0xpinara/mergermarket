import { useEffect, useRef } from "react";
import type { Scene, SceneFrame } from "../art/dusk";
import { usePrefersReducedMotion } from "../lib/store";

interface Props {
  scene: Scene;
  className?: string;
  cellW?: number;   // CSS px per cell
  cellH?: number;
  fps?: number;
  stillAt?: number; // the moment shown when motion is reduced
}

// Dot radii as a share of the cell's short side, for glyphs 1-3; glyph 4 is a star cross.
const RADII = [0, 0.15, 0.28, 0.43];

/** Renders an ASCII halftone scene on a canvas: one dot per character cell, redrawing only what changed. */
export function AsciiScene({ scene, className, cellW = 5, cellH = 8, fps = 12, stillAt = 9 }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    const canvas = ref.current;
    const host = canvas?.parentElement;
    if (!canvas || !host) return;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return;

    let raf = 0;
    let visible = true;
    let step: ((t: number) => SceneFrame) | null = null;
    let atlas: HTMLCanvasElement | null = null;
    let cols = 0, rows = 0, dpr = 1, cw = cellW, ch = cellH;
    const start = performance.now();
    let last = 0;

    const buildAtlas = () => {
      const a = document.createElement("canvas");
      a.width = Math.ceil(5 * cw);
      a.height = Math.ceil(scene.palette.length * ch);
      const g = a.getContext("2d")!;
      scene.palette.forEach((c, i) => {
        g.fillStyle = c;
        g.strokeStyle = c;
        for (let s = 1; s <= 3; s++) {
          g.beginPath();
          g.arc(s * cw + cw / 2, i * ch + ch / 2, RADII[s] * Math.min(cw, ch * 0.75), 0, Math.PI * 2);
          g.fill();
        }
        const cx = 4 * cw + cw / 2, cy = i * ch + ch / 2, arm = Math.min(cw, ch) * 0.48;
        g.lineWidth = Math.max(1, dpr * 0.9);
        g.beginPath();
        g.moveTo(cx - arm, cy);
        g.lineTo(cx + arm, cy);
        g.moveTo(cx, cy - arm * 1.1);
        g.lineTo(cx, cy + arm * 1.1);
        g.stroke();
      });
      return a;
    };

    const draw = (t: number) => {
      if (!step || !atlas) return;
      const f = step(t);
      const paint = (k: number) => {
        const x = (k % cols) * cw, y = ((k / cols) | 0) * ch;
        ctx.fillStyle = scene.ground;
        ctx.fillRect(x, y, cw, ch);
        const g = f.glyph[k];
        if (g) ctx.drawImage(atlas!, g * cw, f.color[k] * ch, cw, ch, x, y, cw, ch);
      };
      if (f.changed === null) {
        ctx.fillStyle = scene.ground;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        for (let k = 0; k < cols * rows; k++) if (f.glyph[k]) paint(k);
      } else {
        for (let i = 0; i < f.count; i++) paint(f.changed[i]);
      }
    };

    const setup = () => {
      const w = host.clientWidth, h = host.clientHeight;
      if (!w || !h) return;
      dpr = Math.min(2, window.devicePixelRatio || 1);
      cw = Math.round(cellW * dpr);
      ch = Math.round(cellH * dpr);
      cols = Math.ceil((w * dpr) / cw);
      rows = Math.ceil((h * dpr) / ch);
      canvas.width = cols * cw;
      canvas.height = rows * ch;
      canvas.style.width = `${(cols * cw) / dpr}px`;
      canvas.style.height = `${(rows * ch) / dpr}px`;
      atlas = buildAtlas();
      step = scene.create(cols, rows, cw / ch);
      draw(reduced ? stillAt : stillAt + (performance.now() - start) / 1000);
    };

    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (!visible || document.hidden || now - last < 1000 / fps) return;
      last = now;
      draw(stillAt + (now - start) / 1000);
    };

    setup();
    if (!reduced) raf = requestAnimationFrame(loop);

    let timer = 0;
    const ro = new ResizeObserver(() => {
      clearTimeout(timer);
      timer = window.setTimeout(setup, 120);
    });
    ro.observe(host);
    const io = new IntersectionObserver(([e]) => (visible = e.isIntersecting));
    io.observe(canvas);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(timer);
      ro.disconnect();
      io.disconnect();
    };
  }, [scene, cellW, cellH, fps, stillAt, reduced]);

  return <canvas ref={ref} className={className} aria-hidden="true" />;
}
