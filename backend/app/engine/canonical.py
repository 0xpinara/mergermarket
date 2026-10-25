"""Turn mapped source rows into canonical records that keep full provenance."""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import normalize as nz
from ..schema import ENTITIES


@dataclass
class Rec:
    key: str            # unique per side+entity (normalized id, or id#rN for duplicate ids)
    side: str           # "before" | "after"
    entity: str
    id: str | None      # normalized primary id
    file: str
    row: int            # spreadsheet row number (header = 1)
    raw: dict           # original column -> value
    f: dict             # canonical field -> raw mapped value
    n: dict             # canonical field -> normalized value
    issues: list = field(default_factory=list)

    def ref(self) -> dict:
        return {"key": self.key, "id": self.id, "file": self.file, "row": self.row, "side": self.side,
                "entity": self.entity}


@dataclass
class SnapshotInput:
    snapshot_id: str
    side: str
    entity: str
    file: str
    headers: list[str]
    rows: list[tuple[int, dict]]   # (row_number, {header: value})
    mapping: dict[str, str]        # field -> column


_KIND = {
    "id": nz.ident, "text": nz.text, "name": nz.name, "email": nz.email, "phone": nz.phone, "date": nz.parse_date,
    "lease_status": nz.lease_status, "ticket_status": nz.ticket_status, "priority": nz.priority,
    "address": nz.address, "unit": nz.unit_label, "int": nz.integer,
}


def _normalize(kind: str, value, issues: list, label: str):
    if kind == "money":
        try:
            return nz.parse_money(value)
        except nz.MoneyError:
            issues.append({"field": label, "kind": "money", "value": nz.text(value)[:60]})
            return None
    out = _KIND[kind](value)
    if kind == "date" and out is None and not nz.blank(value):
        issues.append({"field": label, "kind": "date", "value": nz.text(value)[:60]})
    return out


def build(snapshots: list[SnapshotInput], as_of: str | None) -> tuple[dict, list[dict], dict]:
    """Returns (records[(side, entity)] -> list[Rec], dq_events, stats)."""
    records: dict[tuple[str, str], list[Rec]] = {}
    dq: list[dict] = []
    stats = {"rows": 0, "values": 0, "skipped_blank_id": 0}
    seen_ids: dict[tuple[str, str], dict[str, Rec]] = {}
    for snap in sorted(snapshots, key=lambda s: (s.side, s.entity, s.file, s.snapshot_id)):
        spec = ENTITIES[snap.entity]
        bucket = records.setdefault((snap.side, snap.entity), [])
        ids = seen_ids.setdefault((snap.side, snap.entity), {})
        for row_no, raw in snap.rows:
            stats["rows"] += 1
            f, n, issues = {}, {}, []
            for fs in spec.fields:
                col = snap.mapping.get(fs.key)
                if not col:
                    continue
                val = raw.get(col, "")
                f[fs.key] = nz.text(val)
                n[fs.key] = _normalize(fs.kind, val, issues, fs.label)
                stats["values"] += 1
            rid = n.get(spec.id_field)
            if rid is None:
                if spec.field(spec.id_field).required:
                    stats["skipped_blank_id"] += 1
                    dq.append({"type": "blank_id", "side": snap.side, "entity": snap.entity, "file": snap.file,
                               "row": row_no})
                    continue
                key = f"{snap.file}#r{row_no}"
            else:
                key = rid
            if key in ids:
                first = ids[key]
                dq.append({"type": "duplicate_id", "side": snap.side, "entity": snap.entity, "id": rid,
                           "file": snap.file, "row": row_no, "first": first.ref()})
                key = f"{rid}#r{row_no}"
            _derive(snap.entity, n, as_of, f, issues)
            rec = Rec(key, snap.side, snap.entity, rid, snap.file, row_no, dict(raw), f, n, issues)
            ids[key] = rec
            bucket.append(rec)
            for iss in issues:
                dq.append({"type": "bad_value", "side": snap.side, "entity": snap.entity, "rec": rec.ref(), **iss})
    return records, dq, stats


def _derive(entity: str, n: dict, as_of: str | None, f: dict, issues: list) -> None:
    if entity == "tenants":
        full = n.get("full_name")
        if not full and (n.get("first_name") or n.get("last_name")):
            full = " ".join(p for p in (n.get("first_name"), n.get("last_name")) if p)
        n["name"] = full or None
    elif entity == "leases":
        status = n.get("status")
        if status == "unknown":
            issues.append({"field": "Lease status", "kind": "status", "value": nz.text(f.get("status"))[:60]})
        elif status is None:
            # No status column (or blank): use the term against the as-of date; a plain rent roll with
            # no dates is treated as a list of current leases.
            start, end = n.get("start_date"), n.get("end_date")
            if as_of and start and start > as_of:
                status = "future"
            elif as_of and end and end < as_of:
                status = "expired"
            else:
                status = "active"
            n["status_inferred"] = True
        n["state"] = status or "unknown"
        n["is_active"] = n["state"] == "active"
    elif entity == "maintenance":
        st = n.get("status") or "unknown"
        if st == "unknown":
            issues.append({"field": "Work order status", "kind": "status", "value": nz.text(f.get("status"))[:60]})
        n["is_open"] = st in ("open", "in_progress")


def display_name(rec: Rec | None) -> str | None:
    if rec is None:
        return None
    full = rec.f.get("full_name")
    if not full:
        full = " ".join(p for p in (rec.f.get("first_name"), rec.f.get("last_name")) if p)
    return full or rec.f.get("email") or rec.id
