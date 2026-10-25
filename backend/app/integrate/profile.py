"""Profile the legacy exports: what each column actually contains, which columns are keys, and which columns
reference which (value inclusion). The result is the *source contract* the integration plan is built against;
its fingerprint changes whenever the structure or content of a legacy export changes."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from .. import db
from .. import normalize as nz
from ..schema import ENTITIES

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-#./]*$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DATEISH = re.compile(r"[-/]|[A-Za-z]{3}")
PII_FIELDS = {"full_name", "first_name", "last_name", "email", "phone"}


def shape(v: str) -> str:
    """'BV-U-0001' -> 'AA-A-9999'. Long runs are capped so patterns stay readable."""
    out = re.sub(r"[A-Za-z]", "A", v)
    out = re.sub(r"\d", "9", out)
    return re.sub(r"(A{6,})", "A…", re.sub(r"(9{8,})", "9…", out))[:24]


def _share(values: list[str], ok) -> float:
    return sum(1 for v in values if ok(v)) / len(values) if values else 0.0


def _money_ok(v: str) -> bool:
    try:
        return nz.parse_money(v) is not None
    except nz.MoneyError:
        return False


def infer_type(values: list[str]) -> tuple[str, float]:
    """Best-supported type for the non-blank values, with the share of values that support it.
    Judged on up to 400 distinct values — enough to tell a date column from an ID column."""
    if not values:
        return "empty", 0.0
    values = sorted(set(values))[:400]
    tests = [
        ("email", lambda v: bool(_EMAIL_RE.match(v))),
        ("int", lambda v: bool(re.fullmatch(r"-?\d{1,6}", v))),
        ("date", lambda v: bool(_DATEISH.search(v)) and nz.parse_date(v) is not None),
        ("money", lambda v: bool(re.fullmatch(r"[$€£\s]*-?[\d,]*\d(\.\d{1,2})?\s*", v)) and _money_ok(v)),
        ("phone", lambda v: 7 <= len(re.sub(r"\D", "", v)) <= 15 and bool(re.fullmatch(r"[\d\s()+.\-]+", v))
         and bool(re.search(r"[\s()+.\-]", v))),
        ("id", lambda v: bool(_ID_RE.match(v)) and bool(re.search(r"\d", v))),
    ]
    for name, ok in tests:
        share = _share(values, ok)
        if share >= 0.95:
            return name, round(share, 3)
    distinct = len(set(values))
    if distinct <= 12 and distinct / len(values) <= 0.25:
        return "enum", 1.0
    return "text", 1.0


def profile_column(header: str, raw_values: list, pii: bool) -> dict:
    n = len(raw_values)
    texts = [nz.text(v) for v in raw_values]
    non_blank = [v for v in texts if not nz.blank(v)]
    distinct = set(non_blank)
    ctype, support = infer_type(non_blank)
    zeros = 0
    if ctype in ("money", "int"):
        for v in non_blank:
            try:
                zeros += nz.parse_money(v) == 0
            except nz.MoneyError:
                pass
    pats = Counter(shape(v) for v in non_blank).most_common(3)
    out = {
        "column": header, "type": ctype, "type_support": support, "rows": n,
        "blank": n - len(non_blank), "distinct": len(distinct), "zero": zeros,
        "unique": bool(non_blank) and len(distinct) == len(non_blank),
        "complete": len(non_blank) == n and n > 0,
        "patterns": [{"shape": p, "count": c} for p, c in pats],
        "min_length": min((len(v) for v in non_blank), default=0),
        "max_length": max((len(v) for v in non_blank), default=0),
        "pii": pii,
    }
    if ctype == "enum" and not pii:
        out["values"] = [{"value": v, "count": c} for v, c in Counter(non_blank).most_common(12)]
    if not pii:
        out["examples"] = sorted(distinct)[:3] if ctype != "text" else [v[:40] for v in list(dict.fromkeys(non_blank))[:3]]
    out["key_candidate"] = out["unique"] and out["complete"] and ctype in ("id", "int", "text") and \
        all(" " not in v for v in non_blank)
    return out


def _snapshot_rows(conn, sid: str) -> list[dict]:
    return [json.loads(r["raw_json"]) for r in conn.execute(
        "SELECT raw_json FROM source_rows WHERE snapshot_id=? ORDER BY row_number", (sid,))]


def legacy_snapshots(conn, deal_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM snapshots WHERE deal_id=? AND side='before' ORDER BY entity, file_name, id", (deal_id,))]


def contract_fingerprint(snaps: list[dict]) -> str:
    basis = sorted((s["entity"], s["file_name"], s["sheet"] or "", s["headers_json"], s["row_count"],
                    s["content_hash"]) for s in snaps)
    return hashlib.sha256(db.dumps(basis).encode()).hexdigest()[:24]


_CACHE: dict[str, dict] = {}


def build(conn, deal_id: str) -> dict:
    """Profile every legacy snapshot and the reference relationships between them. Pure function of the snapshots'
    content and audit mappings, so it is cached on exactly those."""
    snaps = legacy_snapshots(conn, deal_id)
    key = hashlib.sha256(db.dumps([contract_fingerprint(snaps)] + sorted(
        (s["id"], s["mapping_json"]) for s in snaps)).encode()).hexdigest()
    if key not in _CACHE:
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = _build(conn, snaps)
    return json.loads(json.dumps(_CACHE[key]))


def _build(conn, snaps: list[dict]) -> dict:
    files, values_by_col = [], {}
    for s in snaps:
        headers = json.loads(s["headers_json"])
        mapping = json.loads(s["mapping_json"])
        rows = _snapshot_rows(conn, s["id"])
        pii_cols = {mapping[f] for f in PII_FIELDS if mapping.get(f)} if s["entity"] == "tenants" else set()
        cols = []
        for h in headers:
            raw = [r.get(h, "") for r in rows]
            c = profile_column(h, raw, pii=h in pii_cols)
            if not c["pii"] and c["type"] == "email":
                c["pii"] = True
                c.pop("examples", None)
            if not c["pii"] and c["type"] == "phone" and s["entity"] == "tenants":
                c["pii"] = True
                c.pop("examples", None)
            c["mapped_to"] = next((f for f, col in mapping.items() if col == h), None)
            cols.append(c)
            if c["type"] in ("id", "int", "text") and c["distinct"]:
                values_by_col[(s["id"], h)] = {nz.ident(v) for v in raw if nz.ident(v)}
        primary = mapping.get(ENTITIES[s["entity"]].id_field)
        files.append({"snapshot_id": s["id"], "file": s["file_name"], "sheet": s["sheet"], "entity": s["entity"],
                      "entity_label": ENTITIES[s["entity"]].label, "rows": s["row_count"], "columns": cols,
                      "content_hash": s["content_hash"], "primary_key": primary,
                      "keys": [c["column"] for c in cols if c["key_candidate"]]})
    return {"fingerprint": contract_fingerprint(snaps), "files": files,
            "relationships": relationships(files, values_by_col)}


def relationships(files: list[dict], values_by_col: dict) -> list[dict]:
    """Value-inclusion dependencies: column A (any file) whose values occur in key column B of another file."""
    # Reference targets: a file's primary key, or a unique column the audit mapping doesn't use for anything
    # else (a second key such as a door code). A unique foreign-key column (deposit.lease_id) is not a target.
    keys = [(f["snapshot_id"], f["file"], f["entity"], k) for f in files for k in f["keys"]
            if k == f["primary_key"] or not next(c for c in f["columns"] if c["column"] == k)["mapped_to"]]
    out = []
    for f in files:
        for c in f["columns"]:
            a = values_by_col.get((f["snapshot_id"], c["column"]))
            if not a or c["pii"] or c["column"] == f["primary_key"]:
                continue
            for sid, kfile, kent, kcol in keys:
                if sid == f["snapshot_id"]:
                    continue
                b = values_by_col.get((sid, kcol))
                if not b:
                    continue
                hit = len(a & b)
                if hit / len(a) >= 0.5 and hit >= 2:
                    out.append({"from": {"file": f["file"], "entity": f["entity"], "column": c["column"]},
                                "to": {"file": kfile, "entity": kent, "column": kcol},
                                "inclusion": round(hit / len(a), 4), "values": len(a), "missing": len(a) - hit})
    out.sort(key=lambda r: (r["from"]["file"], r["from"]["column"], -r["inclusion"]))
    return out


def inclusion(conn, from_sid: str, from_col: str, to_sid: str, to_col: str) -> dict:
    """Share of distinct non-blank values in from_col that occur in to_col."""
    a = {nz.ident(r.get(from_col)) for r in _snapshot_rows(conn, from_sid)} - {None}
    b = {nz.ident(r.get(to_col)) for r in _snapshot_rows(conn, to_sid)} - {None}
    hit = len(a & b)
    return {"values": len(a), "found": hit, "ratio": round(hit / len(a), 4) if a else None}
