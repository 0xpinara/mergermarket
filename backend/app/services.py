"""Deal and snapshot operations shared by the API and the sample loader."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import config, db, ingest
from .ai import assist, embeddings
from .schema import ENTITIES

DEAL_FIELDS = ("title", "industry", "acquirer", "target_business", "deal_date", "old_system", "new_system",
               "source_notes", "source_ref")

SAMPLE_DEAL = {
    "title": "Cedar × Bayview | Tenant & Rent Roll Migration",
    "industry": "Property management · small multifamily",
    "acquirer": "Cedar Property Management",
    "target_business": "Bayview Rentals",
    "deal_date": "2026-09-30",
    "old_system": "Bayview legacy PMS",
    "new_system": "Cedar consolidated platform",
    "source_notes": ("Legacy export taken 2026-09-30 at cutover; consolidated export pulled 2026-10-02 after the "
                     "migration batch. Fictional companies, people and amounts — synthetic demonstration data."),
    "source_ref": "Synthetic seed v1 (deterministic)",
}


def create_deal(conn, workspace: str, data: dict, is_sample: bool = False) -> str:
    did = db.new_id("deal")
    ts = db.now()
    vals = {k: (data.get(k) or None) for k in DEAL_FIELDS}
    vals["title"] = (data.get("title") or "Untitled integration audit").strip()[:160]
    conn.execute(
        f"INSERT INTO deals (id, workspace, {', '.join(DEAL_FIELDS)}, is_sample, created_at, updated_at) "
        f"VALUES (?, ?, {', '.join('?' for _ in DEAL_FIELDS)}, ?, ?, ?)",
        (did, workspace, *[vals[k] for k in DEAL_FIELDS], 1 if is_sample else 0, ts, ts))
    return did


def table_hash(table: ingest.Table) -> str:
    h = hashlib.sha256()
    h.update(db.dumps(table.headers).encode())
    for n, r in zip(table.row_numbers, table.rows):
        h.update(db.dumps([n, r]).encode())
    return h.hexdigest()[:24]


def add_snapshot(conn, deal_id: str, side: str, table: ingest.Table, origin: str,
                 entity: str | None = None) -> str:
    detected, conf, scores = ingest.detect_entity(table.file_name if not table.sheet else table.sheet + "_" +
                                                  table.file_name, table.headers)
    entity = entity or detected
    meta = ingest.suggest_mapping(entity, table.headers)
    mapping = {k: v["column"] for k, v in meta.items()}
    sid = db.new_id("snap")
    ts = db.now()
    conn.execute(
        "INSERT INTO snapshots (id, deal_id, side, entity, origin, file_name, sheet, headers_json, row_count, "
        "mapping_json, mapping_meta_json, entity_confidence, warnings_json, content_hash, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, deal_id, side, entity, origin, table.file_name, table.sheet, db.dumps(table.headers), len(table.rows),
         db.dumps(mapping), db.dumps({"fields": meta, "entity_scores": scores, "detected": detected}), conf,
         db.dumps(table.warnings), table_hash(table), ts, ts))
    conn.executemany(
        "INSERT INTO source_rows (snapshot_id, row_number, raw_json) VALUES (?,?,?)",
        [(sid, n, db.dumps(dict(zip(table.headers, r)))) for n, r in zip(table.row_numbers, table.rows)])
    return sid


def snapshot_view(conn, snap: dict, preview: int = 6) -> dict:
    headers = json.loads(snap["headers_json"])
    mapping = json.loads(snap["mapping_json"])
    meta = json.loads(snap["mapping_meta_json"])
    rows = [json.loads(r["raw_json"]) for r in conn.execute(
        "SELECT raw_json FROM source_rows WHERE snapshot_id=? ORDER BY row_number", (snap["id"],))]
    grid = [[r.get(h, "") for h in headers] for r in rows]
    validation = ingest.validate_mapping(snap["entity"], mapping, headers, grid)
    ai_suggestions = assist.header_suggestions(embeddings.get_if_ready(), snap["entity"], headers, mapping)
    nums = [r[0] for r in conn.execute("SELECT row_number FROM source_rows WHERE snapshot_id=? ORDER BY row_number "
                                       "LIMIT ?", (snap["id"], preview))]
    return {
        "id": snap["id"], "side": snap["side"], "entity": snap["entity"],
        "entity_label": ENTITIES[snap["entity"]].label, "origin": snap["origin"], "file_name": snap["file_name"],
        "sheet": snap["sheet"], "headers": headers, "row_count": snap["row_count"], "mapping": mapping,
        "mapping_meta": meta.get("fields", {}), "detected_entity": meta.get("detected"),
        "entity_confidence": snap["entity_confidence"], "user_mapped": bool(snap["user_mapped"]),
        "warnings": json.loads(snap["warnings_json"]), "validation": validation, "ai_suggestions": ai_suggestions,
        "preview": [{"row": n, "values": g} for n, g in zip(nums, grid[:preview])],
        "created_at": snap["created_at"], "updated_at": snap["updated_at"],
    }


def load_sample(conn, workspace: str) -> str:
    did = create_deal(conn, workspace, SAMPLE_DEAL, is_sample=True)
    for side in ("before", "after"):
        for path in sorted((config.SAMPLE_DIR / side).glob("*.csv")):
            tables = ingest.parse_upload(path.name, path.read_bytes())
            for t in tables:
                add_snapshot(conn, did, side, t, origin="seed")
    return did


def sample_files() -> list[dict]:
    out = []
    for side in ("before", "after", "messy"):
        for p in sorted((config.SAMPLE_DIR / side).glob("*.csv")):
            out.append({"side": side, "name": p.name, "bytes": p.stat().st_size,
                        "rows": max(0, sum(1 for _ in p.open(encoding="utf-8")) - 1)})
    return out


def sample_path(side: str, name: str) -> Path | None:
    if side not in ("before", "after", "messy"):
        return None
    safe = ingest.sanitize_filename(name)
    p = config.SAMPLE_DIR / side / safe
    return p if p.is_file() and p.parent == config.SAMPLE_DIR / side else None
