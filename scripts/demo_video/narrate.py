"""Generate the narration, one clip per scene, with ElevenLabs character timestamps.

    python scripts/demo_video/narrate.py          # writes demo-build/narration/<scene>.mp3 + .json

Each request passes the neighbouring scenes as previous_text/next_text so the delivery flows like one take."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

sys.path.insert(0, str(Path(__file__).parent))
import eleven  # noqa: E402

ROOT = eleven.ROOT
OUT = ROOT / "demo-build" / "narration"


def words(text: str, alignment: dict) -> list[dict]:
    """Word-level timings from character timings."""
    chars, starts, ends = alignment["characters"], alignment["character_start_times_seconds"], \
        alignment["character_end_times_seconds"]
    out, cur = [], None
    for c, s, e in zip(chars, starts, ends):
        if c.isspace():
            if cur:
                out.append(cur)
                cur = None
            continue
        if cur is None:
            cur = {"w": c, "start": s, "end": e}
        else:
            cur["w"] += c
            cur["end"] = e
    if cur:
        out.append(cur)
    return out


def duration(mp3: Path) -> float:
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    r = subprocess.run([ff, "-hide_banner", "-i", str(mp3), "-f", "null", "-"], capture_output=True, text=True)
    t = [ln for ln in r.stderr.splitlines() if "time=" in ln][-1]
    hh, mm, ss = t.split("time=")[1].split()[0].split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(ss)


def main() -> None:
    cfg = json.loads((Path(__file__).parent / "script.json").read_text())
    v = cfg["voice"]
    OUT.mkdir(parents=True, exist_ok=True)
    scenes = cfg["scenes"]
    total = 0.0
    for i, sc in enumerate(scenes):
        cached = OUT / f"{i:02d}_{sc['id']}.json"
        if cached.is_file() and json.loads(cached.read_text())["text"] == sc["text"]:
            total += json.loads(cached.read_text())["duration"]
            print(f"{sc['id']:10s} cached")
            continue
        body = {"text": sc["text"], "model_id": v["model_id"], "voice_settings": v["settings"],
                "previous_text": " ".join(s["text"] for s in scenes[max(0, i - 2):i]) or None,
                "next_text": scenes[i + 1]["text"] if i + 1 < len(scenes) else None}
        body = {k: x for k, x in body.items() if x is not None}
        r = eleven.call("POST", f"/text-to-speech/{v['voice_id']}/with-timestamps?output_format=mp3_44100_128", body)
        mp3 = OUT / f"{i:02d}_{sc['id']}.mp3"
        mp3.write_bytes(base64.b64decode(r["audio_base64"]))
        al = r["alignment"]
        d = duration(mp3)
        meta = {"id": sc["id"], "text": sc["text"], "audio": mp3.name, "duration": d,
                "speech_end": al["character_end_times_seconds"][-1], "words": words(sc["text"], al)}
        (OUT / f"{i:02d}_{sc['id']}.json").write_text(json.dumps(meta, indent=1))
        total += d
        print(f"{sc['id']:10s} {d:6.2f}s  {len(meta['words'])} words")
    print(f"total speech {total:.1f}s")


if __name__ == "__main__":
    main()
