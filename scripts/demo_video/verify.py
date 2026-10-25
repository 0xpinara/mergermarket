"""Verify the exported demo: transcribe its audio with ElevenLabs speech-to-text and check that every narration
scene is heard, in order, starting where the edit placed it (i.e. where its on-screen actions begin)."""
from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import eleven  # noqa: E402

BUILD = eleven.ROOT / "demo-build"


def transcribe(path: Path) -> dict:
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="model_id"\r\n\r\nscribe_v1\r\n'.encode(),
             f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
             f'Content-Type: audio/mpeg\r\n\r\n'.encode() + path.read_bytes() + b"\r\n",
             f"--{boundary}--\r\n".encode()]
    return eleven.call("POST", "/speech-to-text", raw=b"".join(parts),
                       content_type=f"multipart/form-data; boundary={boundary}", timeout=300)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def main() -> None:
    r = transcribe(BUILD / "final_audio.mp3")
    (BUILD / "transcript.json").write_text(json.dumps(r, indent=1))
    words = [w for w in r.get("words", []) if w.get("type", "word") == "word"]
    placed = json.loads((BUILD / "audio_placement.json").read_text())
    narr = {m["id"]: m for m in (json.loads(p.read_text()) for p in sorted((BUILD / "narration").glob("*.json")))}
    print("language:", r.get("language_code"), "| words heard:", len(words))
    worst = 0.0
    cursor = 0
    for pl in placed:
        first = norm(narr[pl["id"]]["words"][0]["w"])
        # first transcribed word at/after the previous scene that matches this scene's first word
        hit = next((w for w in words[cursor:] if norm(w["text"]) == first and w["start"] >= pl["start"] - 1.0), None)
        if hit is None:
            print(f"  {pl['id']:9s} NOT FOUND (expected '{first}' at {pl['start']:.2f}s)")
            worst = 99
            continue
        cursor = words.index(hit) + 1
        lead = narr[pl["id"]]["words"][0]["start"]
        err = hit["start"] - (pl["start"] + lead)
        worst = max(worst, abs(err))
        print(f"  {pl['id']:9s} expected {pl['start'] + lead:6.2f}s  heard {hit['start']:6.2f}s  offset {err:+.2f}s")
    heard = norm(r.get("text", ""))
    script = norm(" ".join(m["text"] for m in narr.values()))
    sw, hw = script.split(), set(heard.split())
    coverage = sum(1 for w in sw if w in hw) / len(sw)
    print(f"script words found in transcript: {coverage * 100:.1f}%   worst scene offset: {worst:.2f}s")
    print("transcript:", r.get("text", "")[:2000])


if __name__ == "__main__":
    main()
