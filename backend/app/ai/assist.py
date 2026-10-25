"""AI suggestions layered on top of the deterministic engine.

Rules for everything in this module:
  * Suggestions only. Nothing here maps a column, pairs records, or changes an amount by itself; a person
    applies or confirms each suggestion, and the engine then re-checks it like any other match.
  * Every suggestion carries its evidence (similarity plus the structural context it was filtered by) and is
    labelled as coming from the local model.
  * Callers must treat ``emb=None`` as "AI unavailable" and carry on without suggestions.
"""
from __future__ import annotations

import re
from datetime import date

from ..schema import ENTITIES

HEADER_MIN_SIM = 0.35   # calibrated on messy PMS headers (see backend/eval); suggestions still need a human
HEADER_MARGIN = 0.05
TICKET_MIN_SIM = 0.30   # descriptions are short; structure (property, dates) does most of the filtering
TICKET_MAX_DAYS = 30


def _header_text(h: str) -> str:
    h = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", h)
    return re.sub(r"[^a-z0-9]+", " ", h.lower()).strip()


def header_suggestions(emb, entity: str, headers: list[str], mapping: dict[str, str]) -> dict[str, dict]:
    """For fields still unmapped, suggest the closest unused column. Returns {field: suggestion}."""
    if emb is None or entity not in ENTITIES:
        return {}
    spec = ENTITIES[entity]
    used = set(mapping.values())
    free = [h for h in headers if h not in used and _header_text(h)]
    fields = [f for f in spec.fields if f.key not in mapping]
    if not free or not fields:
        return {}
    phrases, owners = [], []
    for f in fields:
        for p in dict.fromkeys((f.label.lower(),) + tuple(a.lower() for a in f.aliases)):
            phrases.append(p)
            owners.append(f.key)
    sims = emb.similarity([_header_text(h) for h in free], phrases)
    best: list[tuple[float, str, str, float]] = []
    for i, h in enumerate(free):
        per_field: dict[str, float] = {}
        for j, owner in enumerate(owners):
            per_field[owner] = max(per_field.get(owner, -1.0), float(sims[i, j]))
        ranked = sorted(per_field.items(), key=lambda kv: -kv[1])
        top_field, top = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else -1.0
        if top >= HEADER_MIN_SIM and top - runner_up >= HEADER_MARGIN:
            best.append((top, h, top_field, runner_up))
    out: dict[str, dict] = {}
    taken: set[str] = set()
    for sim, h, fkey, runner_up in sorted(best, key=lambda x: -x[0]):
        if fkey in out or h in taken:
            continue
        out[fkey] = {"column": h, "similarity": round(sim, 3), "runner_up": round(runner_up, 3),
                     "method": "AI suggestion (local model)", "origin": "ai"}
        taken.add(h)
    return out


def _days(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def ticket_suggestions(emb, source: dict, pool: list[dict], k: int = 3) -> list[dict]:
    """Rank unmatched consolidated work orders that might be ``source`` with its description rewritten.

    ``source``/``pool`` items: {"key", "text", "opened", "same_property": bool, "same_unit": bool}.
    Only candidates in the same property (when known) and opened within TICKET_MAX_DAYS are considered.
    """
    if emb is None or not source.get("text"):
        return []
    eligible = []
    for c in pool:
        if not c.get("text") or c.get("same_property") is False:
            continue
        d = _days(source.get("opened"), c.get("opened"))
        if d is not None and d > TICKET_MAX_DAYS:
            continue
        eligible.append((c, d))
    if not eligible:
        return []
    sims = emb.similarity([source["text"]], [c["text"] for c, _ in eligible])[0]
    ranked = sorted(zip(eligible, sims), key=lambda x: -float(x[1]))
    out = []
    for (c, d), sim in ranked[:k]:
        if float(sim) < TICKET_MIN_SIM:
            break
        out.append({"key": c["key"], "similarity": round(float(sim), 3), "days": d,
                    "same_unit": bool(c.get("same_unit")), "same_property": c.get("same_property")})
    return out
