"""Suggest a source column (and transform) for every target-template column.

Evidence, strongest first:
  audit mapping  the canonical binding the audit already uses (and a reviewer may have confirmed) for this file
  references     for keys: do the other files' references point at this column's values? for refs: do this
                 column's values exist in the referenced table's key?
  name           the column is literally (or nearly) called what the template calls it
  local AI       semantic similarity of headers — only when nothing structural exists, and never alone
Type guards remove candidates that can't hold the value (a key must be unique and complete; money must parse).
Disagreeing evidence, two close candidates, or name/AI-only support routes the column to a reviewer. Every
suggestion is just that: nothing is used for an export until a named person approves it."""
from __future__ import annotations

import json
from collections import Counter

from rapidfuzz import fuzz

from .. import normalize as nz
from ..ai import assist
from ..ingest import norm_header
from ..schema import ENTITIES
from . import transforms
from .template import VOCABULARIES

MARGIN = 0.15          # top candidate must lead the runner-up by this much to avoid review
REF_OK = 0.98          # share of reference values that must resolve for a key/ref to count as consistent
REF_CONFLICT = 0.5     # below this the reference evidence actively contradicts the candidate
AI_MIN_SIM = assist.HEADER_MIN_SIM

TYPE_OK = {
    "key": {"id", "int", "text"}, "ref": {"id", "int", "text"}, "money": {"money", "int"}, "date": {"date"},
    "int": {"int"}, "enum": {"enum", "text"}, "email": {"email"}, "phone": {"phone"},
    "unit": {"id", "text", "int", "enum"}, "name": {"text", "enum", "id"}, "text": {"text", "enum", "id", "int"},
}


class Sources:
    """Legacy snapshots + their profile, with cached value sets for reference checks."""

    def __init__(self, conn, profile: dict, snaps: list[dict]):
        self.conn, self.profile = conn, profile
        self.snaps = {s["id"]: s for s in snaps}
        self.files = {f["snapshot_id"]: f for f in profile["files"]}
        self._rows: dict[str, list[tuple[int, dict]]] = {}
        self._vals: dict[tuple[str, str], set] = {}

    def by_entity(self, entity: str) -> list[dict]:
        return sorted((f for f in self.profile["files"] if f["entity"] == entity), key=lambda f: -f["rows"])

    def numbered(self, sid: str) -> list[tuple[int, dict]]:
        """(spreadsheet row number, raw row) in file order."""
        if sid not in self._rows:
            self._rows[sid] = [(r["row_number"], json.loads(r["raw_json"])) for r in self.conn.execute(
                "SELECT row_number, raw_json FROM source_rows WHERE snapshot_id=? ORDER BY row_number", (sid,))]
        return self._rows[sid]

    def rows(self, sid: str) -> list[dict]:
        return [r for _, r in self.numbered(sid)]

    def values(self, sid: str, col: str) -> set:
        k = (sid, col)
        if k not in self._vals:
            self._vals[k] = {nz.ident(r.get(col)) for r in self.rows(sid)} - {None}
        return self._vals[k]

    def mapping(self, sid: str) -> dict:
        return json.loads(self.snaps[sid]["mapping_json"])

    def headers(self, sid: str) -> list[str]:
        return json.loads(self.snaps[sid]["headers_json"])

    def col(self, sid: str, name: str) -> dict | None:
        return next((c for c in self.files[sid]["columns"] if c["column"] == name), None)


def _inclusion(src: Sources, a_sid: str, a_col: str, b_sid: str, b_col: str) -> dict:
    a, b = src.values(a_sid, a_col), src.values(b_sid, b_col)
    hit = len(a & b)
    return {"values": len(a), "found": hit, "ratio": round(hit / len(a), 4) if a else None}


def _name_sim(target: str, header: str) -> float:
    a, b = norm_header(target), norm_header(header)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return fuzz.token_sort_ratio(a, b) / 100


def bind_tables(template: dict, src: Sources) -> list[dict]:
    out = []
    for tb in template["tables"]:
        files = src.by_entity(tb["entity"])
        f = files[0] if files else None
        out.append({"name": tb["name"], "entity": tb["entity"], "file": tb["file"],
                    "snapshot_id": f["snapshot_id"] if f else None, "source_file": f["file"] if f else None,
                    "rows": f["rows"] if f else 0,
                    "unused": [x["file"] for x in files[1:]],
                    "note": None if f else f"No legacy {ENTITIES[tb['entity']].label.lower()} export to fill it from."})
    return out


def _audit_col(src: Sources, binding: dict | None, canonical: str | None) -> str | None:
    if not binding or not binding["snapshot_id"] or not canonical:
        return None
    return src.mapping(binding["snapshot_id"]).get(canonical)


def inbound_refs(template: dict, table_name: str) -> list[tuple[dict, dict]]:
    """(table, column) pairs in the template whose ref points at table_name."""
    return [(tb, c) for tb in template["tables"] for c in tb["columns"]
            if c["type"] == "ref" and c.get("ref") == table_name]


def ref_evidence(template: dict, bindings: dict, src: Sources, table: dict, key_source: str,
                 ref_sources: dict | None = None) -> dict | None:
    """How well the template's references into `table` resolve if its key comes from `key_source`.
    ref_sources overrides which source column each referencing item uses ({item_id: column})."""
    b = bindings.get(table["name"])
    if not b or not b["snapshot_id"]:
        return None
    parts, found, total = [], 0, 0
    for tb, c in inbound_refs(template, table["name"]):
        rb = bindings.get(tb["name"])
        item_id = f"{tb['name']}.{c['name']}"
        col = (ref_sources or {}).get(item_id) or _audit_col(src, rb, c["canonical"])
        if not rb or not rb["snapshot_id"] or not col or col not in src.headers(rb["snapshot_id"]):
            continue
        inc = _inclusion(src, rb["snapshot_id"], col, b["snapshot_id"], key_source)
        if not inc["values"]:
            continue
        parts.append({"table": tb["name"], "column": c["name"], "source_file": rb["source_file"],
                      "source_column": col, **inc})
        found += inc["found"]
        total += inc["values"]
    if not total:
        return None
    return {"ratio": round(found / total, 4), "found": found, "values": total, "parts": parts}


def _type_guard(col: dict, prof: dict | None) -> str | None:
    if prof is None:
        return "column missing"
    if prof["type"] == "empty":
        return "column is empty"
    if prof["type"] not in TYPE_OK[col["type"]]:
        return f"holds {prof['type']} values, not {col['type']}"
    if col["type"] == "key" and not prof["key_candidate"]:
        why = "has blanks" if not prof["complete"] else ("has duplicates" if not prof["unique"] else "contains spaces")
        return f"can't be a key: {why}"
    if col["type"] == "ref" and prof["pii"]:
        return "personal data, not a reference"
    if col["type"] == "enum" and prof["distinct"] > 40:
        return "too many distinct values for a code list"
    return None


def _enum_coverage(src: Sources, sid: str, column: str, t: dict) -> dict:
    fn = VOCABULARIES.get(t.get("vocabulary") or "")
    counts = Counter(nz.text(r.get(column)) for r in src.rows(sid))
    counts.pop("", None)
    mapped, unmapped = {}, {}
    for v, n in counts.items():
        canon = fn(v) if fn else None
        target = t["values"].get(canon) if canon else None
        if target:
            mapped[v] = {"canonical": canon, "target": target, "rows": n}
        else:
            unmapped[v] = {"canonical": canon, "rows": n}
    return {"mapped": mapped, "unmapped": unmapped}


def suggest_column(template: dict, table: dict, col: dict, bindings: dict, src: Sources, emb=None) -> dict:
    b = bindings[table["name"]]
    item = {
        "id": f"{table['name']}.{col['name']}", "table": table["name"], "column": col["name"], "type": col["type"],
        "canonical": col["canonical"], "required": col["required"], "financial": col["financial"],
        "description": col.get("description") or "", "sources": [], "transform": None, "status": "unmapped",
        "confidence": "none", "reasons": [], "candidates": [], "excluded": [], "warnings": [], "decision": None,
    }
    if not b["snapshot_id"]:
        item["reasons"].append(b["note"])
        return item
    sid = b["snapshot_id"]
    spec = ENTITIES[table["entity"]]
    headers = src.headers(sid)
    amap = src.mapping(sid)
    snap = src.snaps[sid]
    confirmed = bool(snap["user_mapped"])
    cands: list[dict] = []

    def add(sources: list[str], signals: list[dict], transform: dict | None = None):
        score = round(min(1.0, sum(s["points"] for s in signals)), 3)
        cands.append({"sources": sources, "transform": transform or transforms.default_for(col, table),
                      "score": score, "signals": signals})

    canon = col["canonical"]
    # Derived candidates the audit itself uses.
    if canon == "legacy_ref" and amap.get(spec.id_field):
        add([amap[spec.id_field]], [{"kind": "audit", "points": 0.6,
                                     "detail": f"The legacy system's own {spec.singular} ID — kept for traceability."}],
            {"op": "copy"})
    elif canon == "full_name" and not amap.get("full_name") and amap.get("first_name") and amap.get("last_name"):
        add([amap["first_name"], amap["last_name"]],
            [{"kind": "audit", "points": 0.6,
              "detail": f"The audit builds the tenant name from `{amap['first_name']}` + `{amap['last_name']}`."}],
            {"op": "concat", "sep": " "})
    if canon is None and col.get("suggest_constant") is not None:
        add([], [{"kind": "template", "points": 0.4,
                  "detail": f"No legacy equivalent; the template suggests “{col['suggest_constant']}”."}],
            {"op": "constant", "value": col["suggest_constant"]})

    for h in headers:
        if canon == "legacy_ref" or (canon == "full_name" and cands):
            break
        prof = src.col(sid, h)
        guard = _type_guard(col, prof)
        signals = []
        if canon and amap.get(canon) == h:
            signals.append({"kind": "audit", "points": 0.5,
                            "detail": f"The audit maps {spec.field(canon).label.lower()} to `{h}` in {b['source_file']}"
                                      + (" (confirmed by a reviewer)." if confirmed else ".")})
        sim = _name_sim(col["name"], h)
        if sim == 1.0:
            signals.append({"kind": "name", "points": 0.3, "detail": f"Column name matches `{col['name']}` exactly."})
        elif sim >= 0.75:
            signals.append({"kind": "name", "points": round(0.15 * sim, 3),
                            "detail": f"Similar name to `{col['name']}` ({round(sim * 100)}%)."})
        if guard:
            if signals:
                item["excluded"].append({"sources": [h], "reason": guard,
                                         "signals": [s["detail"] for s in signals]})
            continue
        if col["type"] == "key":
            ev = ref_evidence(template, bindings, src, table, h)
            if ev:
                pts = round(0.3 * ev["ratio"], 3)
                signals.append({"kind": "references", "points": pts, "ratio": ev["ratio"],
                                "detail": "; ".join(f"{p['found']}/{p['values']} {p['source_file']} `{p['source_column']}` "
                                                    f"values match" for p in ev["parts"]),
                                "parts": ev["parts"]})
        elif col["type"] == "ref":
            ref_t = next(t for t in template["tables"] if t["name"] == col["ref"])
            rb = bindings.get(ref_t["name"])
            key_col = next(c for c in ref_t["columns"] if c["name"] == ref_t["key"])
            key_src = _audit_col(src, rb, key_col["canonical"])
            if rb and rb["snapshot_id"] and key_src:
                inc = _inclusion(src, sid, h, rb["snapshot_id"], key_src)
                if inc["values"]:
                    signals.append({"kind": "references", "points": round(0.3 * inc["ratio"], 3),
                                    "ratio": inc["ratio"],
                                    "detail": f"{inc['found']}/{inc['values']} values exist in {rb['source_file']} "
                                              f"`{key_src}`."})
        # Keep a column with audit/name support, or one the other files' references point at almost entirely: the
        # latter catches a key the audit mapping missed. Reference-only support is never auto-suggested (weak).
        structural = any(s["kind"] == "references" and s.get("ratio", 0) >= REF_OK for s in signals)
        if not any(s["kind"] in ("audit", "name") for s in signals) and not structural:
            continue
        add([h], signals)

    if emb is not None and not any(s["kind"] in ("audit", "name") for c in cands for s in c["signals"]):
        free = [h for h in headers if not _type_guard(col, src.col(sid, h))]
        if free:
            sims = emb.similarity([assist._header_text(h) for h in free], [norm_header(col["name"]) or col["name"]])
            ranked = sorted(((float(sims[i, 0]), h) for i, h in enumerate(free)), reverse=True)
            if ranked and ranked[0][0] >= AI_MIN_SIM:
                sim, h = ranked[0]
                add([h], [{"kind": "ai", "points": round(0.2 * sim, 3), "origin": "ai",
                           "detail": f"Local AI: `{h}` reads like `{col['name']}` (similarity {sim:.2f}). "
                                     f"Suggestion only."}])

    cands.sort(key=lambda c: (-c["score"], headers.index(c["sources"][0]) if c["sources"] else -1))
    item["candidates"] = cands[:5]
    if not cands:
        item["reasons"].append("No compatible legacy column." if canon else "No legacy equivalent.")
        return item
    top = cands[0]
    item["sources"], item["transform"] = list(top["sources"]), top["transform"]
    kinds = {s["kind"] for s in top["signals"]}
    conflicts = []
    name_winner = next((c for c in cands[1:] if any(s["kind"] == "name" and s["points"] >= 0.3 for s in c["signals"])),
                       None)
    if name_winner:
        msg = (f"Name match `{name_winner['sources'][0]}` disagrees with the stronger evidence for "
               f"`{top['sources'][0]}`")
        top_ref = next((s for s in top["signals"] if s["kind"] == "references"), None)
        alt_ref = next((s for s in name_winner["signals"] if s["kind"] == "references"), None)
        if top_ref and alt_ref:
            msg += (f": {round(top_ref['ratio'] * 100)}% of the other files' references resolve through "
                    f"`{top['sources'][0]}`, {round(alt_ref['ratio'] * 100)}% through `{name_winner['sources'][0]}`")
        conflicts.append(msg + ".")
    ref_sig = next((s for s in top["signals"] if s["kind"] == "references"), None)
    if ref_sig and ref_sig["ratio"] < REF_OK:
        conflicts.append(f"Only {round(ref_sig['ratio'] * 100)}% of references resolve through `{top['sources'][0]}`.")
    if len(cands) > 1 and top["score"] - cands[1]["score"] < MARGIN:
        conflicts.append(f"Two plausible sources: `{'+'.join(top['sources']) or 'constant'}` and "
                         f"`{'+'.join(cands[1]['sources']) or 'constant'}`.")
    if top["transform"]["op"] == "enum_map":
        cov = _enum_coverage(src, sid, top["sources"][0], top["transform"])
        item["enum_coverage"] = cov
        if cov["unmapped"]:
            n = sum(v["rows"] for v in cov["unmapped"].values())
            conflicts.append(f"{n} row(s) have values with no target equivalent: "
                             + ", ".join(f"“{k}”" for k in list(cov["unmapped"])[:4]) + ".")
    prof = src.col(sid, top["sources"][0]) if top["sources"] else None
    if prof and col.get("max_length") and prof["max_length"] > col["max_length"]:
        item["warnings"].append(f"Some values are longer than the template's {col['max_length']}-character limit.")
    if conflicts:
        item["status"], item["confidence"] = "needs_review", "conflict"
        item["reasons"] = conflicts
    elif "audit" in kinds and top["transform"]["op"] not in ("concat",):
        item["status"], item["confidence"] = "suggested", "exact"
        item["reasons"] = [s["detail"] for s in top["signals"]]
    elif "audit" in kinds:
        item["status"], item["confidence"] = "suggested", "derived"
        item["reasons"] = [s["detail"] for s in top["signals"]]
    else:
        item["status"], item["confidence"] = "needs_review", "weak"
        item["reasons"] = [s["detail"] for s in top["signals"]] + ["No structural evidence — confirm by hand."]
    return item


def suggest(template: dict, src: Sources, emb=None) -> tuple[list[dict], list[dict]]:
    tables = bind_tables(template, src)
    bindings = {t["name"]: t for t in tables}
    items = [suggest_column(template, tb, c, bindings, src, emb) for tb in template["tables"] for c in tb["columns"]]
    return tables, items


def bulk_eligible(item: dict) -> bool:
    return item["status"] == "suggested" and item["confidence"] == "exact" and not item["financial"]


def needs_note(item: dict, new_sources: list[str] | None = None) -> bool:
    """Approving against the evidence (or a conflicted/weak suggestion) requires a written reason."""
    if item["confidence"] in ("conflict", "weak", "none"):
        return True
    if new_sources is not None and item["candidates"] and new_sources != item["candidates"][0]["sources"]:
        return True
    return False
