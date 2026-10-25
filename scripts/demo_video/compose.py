"""Compose the recorded walkthrough into MERGEPROOF_DEMO_FINAL.mp4 (1080p, H.264 + AAC).

    python scripts/demo_video/compose.py

Input is the live screencast (demo-build/frames + timeline.json) and the narration clips. Editing applied:
  * narration clips placed at the exact moment each scene started in the recording
  * silent stretches where the UI was still working (page loads, the off-camera approvals) are sped up,
    never the narrated parts. Every frame is a real recorded frame; nothing is synthesized
  * subtle eased camera zooms on the regions the recorder logged
  * captions timed word by word from the ElevenLabs alignment, in the product's own typefaces
  * a small "live product · synthetic data" tag at the start and an end card over the live final frame"""
from __future__ import annotations

import bisect
import json
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "demo-build"
FRAMES = BUILD / "frames"
OUT = ROOT / "MERGEPROOF_DEMO_FINAL.mp4"
W, H, FPS = 1920, 1080, 30
SR = 44100
MAX_TOTAL = 115.0          # hard target; the brief's ceiling is 119
TARGET = 112.5             # aim a little under it
FONTS = ROOT / "frontend/node_modules/@fontsource"
FF = imageio_ffmpeg.get_ffmpeg_exe()


def font(family: str, weight: int, size: int, italic: bool = False) -> ImageFont.FreeTypeFont:
    style = "italic" if italic else "normal"
    return ImageFont.truetype(str(FONTS / family / "files" / f"{family}-latin-{weight}-{style}.woff2"), size)


# ----------------------------------------------------------------------------------------------- time remapping

class TimeMap:
    """Piecewise-linear map from recording time to output time. Narrated windows play at 1x; silent windows where
    the UI was still busy play faster."""

    def __init__(self, t0: float, t_end: float, fast: list[tuple[float, float, float]]):
        self.knots_rec = [t0]
        self.knots_out = [0.0]
        cur = t0
        for a, b, speed in sorted(fast):
            if b <= cur:
                continue
            a = max(a, cur)
            self._add(a, 1.0)
            self._add(b, speed)
            cur = b
        self._add(t_end, 1.0)

    def _add(self, t_rec: float, speed: float):
        if t_rec <= self.knots_rec[-1]:
            return
        self.knots_out.append(self.knots_out[-1] + (t_rec - self.knots_rec[-1]) / speed)
        self.knots_rec.append(t_rec)

    def out(self, t_rec: float) -> float:
        return float(np.interp(t_rec, self.knots_rec, self.knots_out))

    def rec(self, t_out: float) -> float:
        return float(np.interp(t_out, self.knots_out, self.knots_rec))

    @property
    def duration(self) -> float:
        return self.knots_out[-1]


def build_timemap(tl: dict, narr: dict) -> tuple[TimeMap, list[dict]]:
    ev = tl["events"]
    scenes = [e for e in ev if e["type"] == "scene"]
    t0 = next(e["t"] for e in ev if e["type"] == "start")
    t_end = next(e["t"] for e in ev if e["type"] == "end")
    windows = []
    for i, sc in enumerate(scenes):
        speech_end = sc["t"] + narr[sc["id"]]["speech_end"] + 0.35
        nxt = scenes[i + 1]["t"] if i + 1 < len(scenes) else None
        if nxt and nxt - speech_end > 0.45:
            windows.append([speech_end + 0.1, nxt - 0.05])
    # Silent windows: a static screen (waiting on a page load or the off-camera approvals) is cut down hard; one
    # where the UI is visibly working plays at up to 2x. Narrated time always plays at 1x.
    f_times = [f[0] for f in tl["frames"]]
    def remap(active_speed: float) -> TimeMap:
        fast = []
        for a, b in windows:
            n = bisect.bisect_right(f_times, b) - bisect.bisect_left(f_times, a)
            fast.append((a, b, 4.0 if n / (b - a) < 4 else active_speed))
        return TimeMap(t0, t_end, fast)

    speed, tm = 2.0, remap(2.0)
    while tm.duration > TARGET and speed < 2.6:   # keep a margin under the 115 s target
        speed += 0.1
        tm = remap(speed)
    return tm, scenes


# ----------------------------------------------------------------------------------------------- camera

FULL = (0.0, 0.0, 2240.0, 1260.0)   # recording viewport; set from timeline.json in main()


def zoom_rect(box: dict, scale: float, pad: float, max_scale: float) -> tuple[float, float, float, float]:
    pad *= FULL[2] / 1440
    x, y, w, h = (box["x"] - pad) * scale, (box["y"] - pad) * scale, (box["width"] + 2 * pad) * scale, \
        (box["height"] + 2 * pad) * scale
    cw = max(w, h * 16 / 9, FULL[2] / max_scale)
    cw = min(cw, FULL[2])
    ch = cw * 9 / 16
    cx, cy = x + w / 2, y + h / 2
    x0 = min(max(0.0, cx - cw / 2), FULL[2] - cw)
    y0 = min(max(0.0, cy - ch / 2), FULL[3] - ch)
    return (x0, y0, x0 + cw, y0 + ch)


def ease(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return p * p * p * (p * (6 * p - 15) + 10)   # smootherstep


class Camera:
    def __init__(self):
        self.kf: list[tuple[float, tuple]] = [(0.0, FULL)]

    def sample(self, t: float) -> tuple:
        kf = self.kf
        if t <= kf[0][0]:
            return kf[0][1]
        for (ta, ra), (tb, rb) in zip(kf, kf[1:]):
            if ta <= t <= tb:
                e = ease((t - ta) / (tb - ta)) if tb > ta else 1.0
                return tuple(a + (b - a) * e for a, b in zip(ra, rb))
        return kf[-1][1]

    def add(self, t: float, rect: tuple, hold: float, t_in: float = 0.7, t_out: float = 0.8):
        now = self.sample(t)
        self.kf = [k for k in self.kf if k[0] < t] + [(t, now), (t + t_in, rect), (t + t_in + hold, rect),
                                                       (t + t_in + hold + t_out, FULL)]


# ----------------------------------------------------------------------------------------------- captions

def phrases(words: list[dict], max_words: int = 7) -> list[dict]:
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        end_punct = w["w"][-1] in ".?!:;"
        comma = w["w"][-1] == ","
        if end_punct or len(cur) >= max_words or (comma and len(cur) >= 3):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    res = []
    for g in out:
        text = " ".join(w["w"] for w in g).rstrip(",;")
        res.append({"text": text, "start": g[0]["start"], "end": g[-1]["end"]})
    return res


class CaptionRenderer:
    def __init__(self):
        self.f = font("ibm-plex-sans", 500, 44)
        self.cache: dict[str, Image.Image] = {}

    def image(self, text: str) -> Image.Image:
        if text in self.cache:
            return self.cache[text]
        d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        l, t, r, b = d.textbbox((0, 0), text, font=self.f)
        pw, ph = 34, 18
        w, h = r - l + 2 * pw, b - t + 2 * ph
        im = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((20, 24, 20 + w, 24 + h), radius=16, fill=(0, 0, 0, 120))
        sh = sh.filter(ImageFilter.GaussianBlur(10))
        im.alpha_composite(sh)
        dd = ImageDraw.Draw(im)
        dd.rounded_rectangle((20, 20, 20 + w, 20 + h), radius=16, fill=(11, 18, 32, 222))
        dd.rounded_rectangle((20, 20, 20 + w, 20 + h), radius=16, outline=(198, 243, 107, 70), width=2)
        dd.text((20 + pw - l, 20 + ph - t), text, font=self.f, fill=(251, 248, 241, 255))
        self.cache[text] = im
        return im


def tag_image() -> Image.Image:
    f = font("ibm-plex-mono", 500, 22)
    text = "LIVE PRODUCT WALKTHROUGH  ·  SYNTHETIC DATA"
    d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    w, h = r - l + 58, b - t + 24
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dd = ImageDraw.Draw(im)
    dd.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=(11, 18, 32, 215))
    dd.ellipse((18, h / 2 - 6, 30, h / 2 + 6), fill=(240, 100, 74, 255))
    dd.text((40 - l, 12 - t), text, font=f, fill=(198, 243, 107, 255))
    return im


def end_card() -> Image.Image:
    im = Image.new("RGBA", (W, H), (7, 12, 22, 0))
    d = ImageDraw.Draw(im)
    serif, serif_i = font("instrument-serif", 400, 150), font("instrument-serif", 400, 150, italic=True)
    sans = font("ibm-plex-sans", 400, 38)
    mono = font("ibm-plex-mono", 500, 24)
    a, b = "Merge", "Proof"
    wa = d.textlength(a, font=serif)
    wb = d.textlength(b, font=serif_i)
    x = (W - wa - wb) / 2
    y = H / 2 - 130
    d.text((x, y), a, font=serif, fill=(251, 248, 241, 255))
    d.text((x + wa, y), b, font=serif_i, fill=(198, 243, 107, 255))
    tag = "Every record, accounted for."
    d.text(((W - d.textlength(tag, font=sans)) / 2, y + 175), tag, font=sans, fill=(196, 203, 216, 255))
    small = "plan  ·  dry run  ·  verify      github.com/0xpinara/mergermarket"
    d.text(((W - d.textlength(small, font=mono)) / 2, y + 250), small, font=mono, fill=(122, 137, 166, 255))
    return im


# ----------------------------------------------------------------------------------------------- audio

def decode(mp3: Path) -> np.ndarray:
    r = subprocess.run([FF, "-v", "error", "-i", str(mp3), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32)


def build_audio(scenes: list[dict], tm: TimeMap, narr: dict, total: float) -> Path:
    buf = np.zeros(int((total + 1) * SR), dtype=np.float32)
    placed = []
    for sc in scenes:
        clip = decode(BUILD / "narration" / sc["audio"])
        start = int(tm.out(sc["t"]) * SR)
        fade = int(0.012 * SR)
        clip = clip.copy()
        clip[:fade] *= np.linspace(0, 1, fade)
        clip[-fade:] *= np.linspace(1, 0, fade)
        end = min(len(buf), start + len(clip))
        buf[start:end] += clip[: end - start]
        placed.append({"id": sc["id"], "start": start / SR, "end": end / SR})
    buf = buf[: int(total * SR)]
    peak = float(np.max(np.abs(buf))) or 1.0
    pcm = (buf / peak * 0.89 * 32767).astype(np.int16)
    path = BUILD / "narration_mix.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    (BUILD / "audio_placement.json").write_text(json.dumps(placed, indent=1))
    return path


# ----------------------------------------------------------------------------------------------- main

def main() -> None:
    tl = json.loads((BUILD / "timeline.json").read_text())
    narr = {m["id"]: m for m in (json.loads(p.read_text()) for p in sorted((BUILD / "narration").glob("*.json")))}
    tm, scenes = build_timemap(tl, narr)
    total = tm.duration
    print(f"recording {tm.knots_rec[-1] - tm.knots_rec[0]:.1f}s -> output {total:.1f}s")
    if total > 119:
        raise SystemExit("would exceed 119 s")

    global FULL
    FULL = (0.0, 0.0, float(tl["view"]["width"]), float(tl["view"]["height"]))
    frames = tl["frames"]
    f_times = [f[0] for f in frames]
    cam = Camera()
    for e in tl["events"]:
        if e["type"] == "zoom":
            cam.add(tm.out(e["t"]), zoom_rect(e["box"], tl["scale"], e["pad"], e["max_scale"]), e["hold"])

    caps = []
    for sc in scenes:
        base = tm.out(sc["t"])
        ph = phrases(narr[sc["id"]]["words"])
        for i, p in enumerate(ph):
            nxt = ph[i + 1]["start"] if i + 1 < len(ph) else p["end"] + 0.45
            caps.append({"text": p["text"], "a": base + p["start"] - 0.05, "b": base + min(nxt, p["end"] + 0.45)})
    (BUILD / "captions.json").write_text(json.dumps(caps, indent=1))
    cr = CaptionRenderer()
    tag = tag_image()
    card = end_card()
    close = next(s for s in scenes if s["id"] == "close")
    card_at = tm.out(close["t"]) + 1.05      # once the Command Center is back, as the line lands

    audio = build_audio(scenes, tm, narr, total)
    n_out = int(total * FPS)
    enc = subprocess.Popen([
        FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", str(audio), "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-tune", "animation", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:d=0.15,afade=t=out:st=%.2f:d=0.6" % (total - 0.6),
        "-metadata", "title=MergeProof — live product walkthrough", "-map_metadata", "-1",
        "-movflags", "+faststart", "-t", f"{total:.3f}", str(OUT)], stdin=subprocess.PIPE)

    cache_name, cache_img = None, None
    for k in range(n_out):
        t = k / FPS
        tr = tm.rec(t)
        i = max(0, bisect.bisect_right(f_times, tr) - 1)
        name = frames[i][1]
        if name != cache_name:
            cache_img = Image.open(FRAMES / name).convert("RGB")
            cache_name = name
        rect = cam.sample(t)
        img = cache_img.resize((W, H), Image.LANCZOS, box=rect).convert("RGBA")
        if t < 6.0:
            a = min(1.0, (t - 0.3) / 0.4, (6.0 - t) / 0.5)
            if a > 0:
                tg = tag.copy()
                tg.putalpha(tg.getchannel("A").point(lambda v, a=a: int(v * a)))
                img.alpha_composite(tg, (W - tag.width - 40, 36))
        for c in caps:
            if c["a"] <= t <= c["b"]:
                ci = cr.image(c["text"])
                a = min(1.0, (t - c["a"]) / 0.12, (c["b"] - t) / 0.12)
                if a < 1:
                    ci = ci.copy()
                    ci.putalpha(ci.getchannel("A").point(lambda v, a=a: int(v * a)))
                img.alpha_composite(ci, ((W - ci.width) // 2, H - ci.height - 46))
                break
        if t >= card_at:
            a = ease((t - card_at) / 0.8)
            veil = Image.new("RGBA", (W, H), (7, 12, 22, int(214 * a)))
            img.alpha_composite(veil)
            cc = card.copy()
            cc.putalpha(cc.getchannel("A").point(lambda v, a=a: int(v * a)))
            img.alpha_composite(cc)
        enc.stdin.write(img.convert("RGB").tobytes())
        if k % 300 == 0:
            print(f"  frame {k}/{n_out}", flush=True)
    enc.stdin.close()
    if enc.wait() != 0:
        raise SystemExit("ffmpeg failed")
    (BUILD / "edit.json").write_text(json.dumps({"duration": total, "knots_rec": tm.knots_rec,
                                                 "knots_out": tm.knots_out,
                                                 "scenes": [{"id": s["id"], "out": tm.out(s["t"])} for s in scenes]},
                                                indent=1))
    print(f"wrote {OUT} ({total:.1f}s)")


if __name__ == "__main__":
    sys.exit(main())
