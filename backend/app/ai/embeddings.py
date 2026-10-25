"""Local text embeddings used only for *suggestions*.

Runs fully on this machine with an open-source static embedding model (no API calls, no data leaves the
server). Everything in MergeProof works without it: if the package or model is missing, disabled, or fails to
load, callers get ``None`` and the engine stays purely rule-based.

Environment:
  MERGEPROOF_AI=off            disable AI assistance entirely
  MERGEPROOF_EMBED_MODEL=...   Hugging Face model id or local path (default minishlab/potion-retrieval-32M)
"""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("mergeproof.ai")

DEFAULT_MODEL = "minishlab/potion-retrieval-32M"
_lock = threading.Lock()
_state: dict = {"embedder": None, "tried": False, "reason": None, "load_ms": None}


def enabled() -> bool:
    return os.environ.get("MERGEPROOF_AI", "on").strip().lower() not in ("off", "0", "false", "no")


def model_id() -> str:
    return os.environ.get("MERGEPROOF_EMBED_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


class LocalEmbedder:
    def __init__(self, mid: str):
        import numpy as np
        from model2vec import StaticModel

        self._np = np
        try:  # use the cached copy when present; only fetch when it isn't
            self.model = StaticModel.from_pretrained(mid, force_download=False)
        except TypeError:  # older model2vec without the flag
            self.model = StaticModel.from_pretrained(mid)
        self.model_id = mid
        self.dim = int(self.model.dim)
        self._cache: dict[str, object] = {}

    def encode(self, texts: list[str]):
        np = self._np
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            vecs = np.asarray(self.model.encode(missing), dtype=np.float32)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            vecs = vecs / np.where(norms == 0, 1, norms)
            if len(self._cache) > 50_000:
                self._cache.clear()
            for t, v in zip(missing, vecs):
                self._cache[t] = v
        return np.stack([self._cache[t] for t in texts]) if texts else np.zeros((0, self.dim), dtype=np.float32)

    def similarity(self, a: list[str], b: list[str]):
        """Cosine similarity matrix, shape (len(a), len(b))."""
        if not a or not b:
            return self._np.zeros((len(a), len(b)), dtype=self._np.float32)
        return self.encode(a) @ self.encode(b).T


def get() -> LocalEmbedder | None:
    """The shared embedder, loading it on first use. Returns None when AI is unavailable."""
    if not enabled():
        return None
    if _state["tried"]:
        return _state["embedder"]
    with _lock:
        if _state["tried"]:
            return _state["embedder"]
        t0 = time.perf_counter()
        try:
            _state["embedder"] = LocalEmbedder(model_id())
            _state["load_ms"] = round((time.perf_counter() - t0) * 1000)
            log.info("local embedding model %s loaded in %sms", model_id(), _state["load_ms"])
        except ImportError:
            _state["reason"] = "model2vec is not installed"
        except Exception as exc:  # network, missing files, corrupt cache — degrade to rules only
            _state["reason"] = f"model could not be loaded ({type(exc).__name__})"
            log.warning("AI assistance unavailable: %s", _state["reason"])
        _state["tried"] = True
        return _state["embedder"]


def get_if_ready() -> LocalEmbedder | None:
    """The embedder if it has already loaded; never waits (for page views that shouldn't block)."""
    return _state["embedder"] if enabled() and _state["tried"] else None


def warm_in_background() -> None:
    """Load the model off the request path so the first upload or audit isn't slowed down."""
    if enabled() and not _state["tried"]:
        threading.Thread(target=get, name="mergeproof-ai-warm", daemon=True).start()


def status() -> dict:
    e = _state["embedder"]
    if not enabled():
        return {"enabled": False, "available": False, "model": None, "reason": "turned off (MERGEPROOF_AI=off)",
                "local": True}
    return {"enabled": True, "available": e is not None, "model": e.model_id if e else model_id(),
            "dim": e.dim if e else None, "load_ms": _state["load_ms"], "local": True,
            "reason": None if e else (_state["reason"] or "loading")}


def reset_for_tests(embedder: LocalEmbedder | None = None, tried: bool = False) -> None:
    _state.update({"embedder": embedder, "tried": tried, "reason": None, "load_ms": None})


if __name__ == "__main__":  # python -m app.ai.embeddings  -> pre-download the model (e.g. at build time)
    logging.basicConfig(level=logging.INFO)
    emb = get()
    print(status())
    raise SystemExit(0 if emb else 1)
