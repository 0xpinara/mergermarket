"""Minimal ElevenLabs client for the demo video. The API key is read from the repo's .env (gitignored) or the
environment and is never printed, logged or written anywhere else."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = "https://api.elevenlabs.io/v1"


def api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY")
    env = ROOT / ".env"
    if not key and env.is_file():
        for line in env.read_text().splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY is not set (.env or environment).")
    return key


def call(method: str, path: str, body: dict | None = None, raw: bytes | None = None,
         content_type: str = "application/json", timeout: int = 120):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"xi-api-key": api_key(), "Content-Type": content_type,
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:  # surface the API's message, never the key
        raise SystemExit(f"ElevenLabs {e.code}: {e.read()[:400].decode(errors='replace')}") from None
