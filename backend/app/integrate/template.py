"""Target import templates: what the destination system expects, column by column.

A template column binds to a MergeProof canonical field, so the same normalization the audit uses also drives the
source -> target mapping. Templates are data, validated strictly on load; nothing in them is executed."""
from __future__ import annotations

import json
import re
from collections import Counter

from .. import config
from .. import normalize as nz
from ..schema import ENTITIES, ENTITY_ORDER

TYPES = {"key", "ref", "text", "name", "unit", "int", "money", "date", "enum", "email", "phone"}
DATE_FORMATS = {"MM/DD/YYYY": "%m/%d/%Y", "YYYY-MM-DD": "%Y-%m-%d", "DD/MM/YYYY": "%d/%m/%Y"}
VOCABULARIES = {"lease_status": nz.lease_status, "ticket_status": nz.ticket_status, "priority": nz.priority}
TEMPLATE_DIR = config.SAMPLE_DIR / "target_template"

_TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_COL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _#.\-]{0,60}$")
_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,80}\.csv$")
_PREFIX_RE = re.compile(r"^[A-Z0-9]{0,6}-?$")

# canonical reference field -> entity it points at
REF_TARGETS = {"property_id": "properties", "unit_id": "units", "tenant_id": "tenants", "lease_id": "leases"}


class TemplateError(ValueError):
    pass


def _err(where: str, msg: str):
    raise TemplateError(f"{where}: {msg}")


def validate(t: dict) -> dict:
    """Check a template's structure and return a normalized copy. Raises TemplateError."""
    if not isinstance(t, dict):
        _err("template", "must be an object")
    for k in ("id", "name", "version"):
        if not isinstance(t.get(k), str) or not t[k].strip() or len(t[k]) > 120:
            _err("template", f"'{k}' must be a short string")
    tables = t.get("tables")
    if not isinstance(tables, list) or not 1 <= len(tables) <= 12:
        _err("template", "'tables' must list 1-12 tables")
    seen_tables: dict[str, dict] = {}
    out_tables = []
    for ti, tb in enumerate(tables):
        where = f"tables[{ti}]"
        if not isinstance(tb, dict):
            _err(where, "must be an object")
        name = tb.get("name")
        if not isinstance(name, str) or not _TABLE_RE.match(name) or name in seen_tables:
            _err(where, "needs a unique lowercase 'name'")
        entity = tb.get("entity")
        if entity not in ENTITIES:
            _err(where, f"unknown entity {entity!r}")
        if not isinstance(tb.get("file"), str) or not _FILE_RE.match(tb["file"]):
            _err(where, "'file' must be a plain .csv file name")
        prefix = tb.get("key_prefix") or ""
        if not isinstance(prefix, str) or not _PREFIX_RE.match(prefix):
            _err(where, "'key_prefix' must be up to 6 capitals/digits and an optional '-'")
        cols = tb.get("columns")
        if not isinstance(cols, list) or not 1 <= len(cols) <= 60:
            _err(where, "'columns' must list 1-60 columns")
        spec = ENTITIES[entity]
        names, out_cols = set(), []
        for ci, c in enumerate(cols):
            cw = f"{where}.columns[{ci}]"
            if not isinstance(c, dict):
                _err(cw, "must be an object")
            cname, ctype = c.get("name"), c.get("type")
            if not isinstance(cname, str) or not _COL_RE.match(cname) or cname in names:
                _err(cw, "needs a unique 'name'")
            names.add(cname)
            if ctype not in TYPES:
                _err(cw, f"unknown type {ctype!r}")
            canon = c.get("canonical")
            if canon is not None and (not isinstance(canon, str) or spec.field(canon) is None):
                _err(cw, f"canonical field {canon!r} is not a {spec.label} field")
            col = {"name": cname, "type": ctype, "canonical": canon, "required": bool(c.get("required")),
                   "financial": bool(c.get("financial")) or ctype == "money",
                   "description": str(c.get("description") or "")[:240]}
            if c.get("max_length") is not None:
                ml = c["max_length"]
                if not isinstance(ml, int) or not 1 <= ml <= 2000:
                    _err(cw, "'max_length' must be 1-2000")
                col["max_length"] = ml
            if ctype == "ref":
                ref = c.get("ref")
                if ref not in seen_tables:
                    _err(cw, "'ref' must name an earlier table in the template")
                col["ref"] = ref
            if ctype == "date":
                fmt = c.get("format") or "YYYY-MM-DD"
                if fmt not in DATE_FORMATS:
                    _err(cw, f"date format must be one of {', '.join(DATE_FORMATS)}")
                col["format"] = fmt
            if ctype == "enum":
                vals = c.get("values")
                if not isinstance(vals, dict) or not 1 <= len(vals) <= 40:
                    _err(cw, "'values' must map 1-40 target values to canonical values")
                vocab = c.get("vocabulary")
                if canon is not None and vocab not in VOCABULARIES:
                    _err(cw, f"'vocabulary' must be one of {', '.join(VOCABULARIES)}")
                for tv, cv in vals.items():
                    if not isinstance(tv, str) or not 1 <= len(tv) <= 40 or (cv is not None and not isinstance(cv, str)):
                        _err(cw, "enum values must be short strings")
                col["values"] = dict(vals)
                col["vocabulary"] = vocab
            if c.get("suggest_constant") is not None:
                sc = c["suggest_constant"]
                if not isinstance(sc, str) or (ctype == "enum" and sc not in col["values"]):
                    _err(cw, "'suggest_constant' must be one of the column's values")
                col["suggest_constant"] = sc
            out_cols.append(col)
        key = tb.get("key")
        key_col = next((c for c in out_cols if c["name"] == key), None)
        if key_col is None or key_col["type"] != "key":
            _err(where, "'key' must name one of its columns of type 'key'")
        out = {"name": name, "entity": entity, "file": tb["file"], "key": key, "key_prefix": prefix,
               "columns": out_cols}
        seen_tables[name] = out
        out_tables.append(out)
    return {"id": t["id"].strip(), "name": t["name"].strip(), "version": t["version"].strip(),
            "synthetic": bool(t.get("synthetic")), "notes": str(t.get("notes") or "")[:600],
            "source": t.get("source") or "bundled", "tables": out_tables}


def bundled() -> list[dict]:
    out = []
    for p in sorted(TEMPLATE_DIR.glob("*.json")):
        out.append(validate(json.loads(p.read_text(encoding="utf-8"))))
    return out


def get_bundled(template_id: str) -> dict | None:
    return next((t for t in bundled() if t["id"] == template_id), None)


def _guess_date_format(values: list[str]) -> str:
    vals = [v for v in values if v][:200]
    if vals and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in vals):
        return "YYYY-MM-DD"
    if vals and all(re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", v) for v in vals):
        # US order unless a first part exceeds 12
        return "DD/MM/YYYY" if any(int(v.split("/")[0]) > 12 for v in vals) else "MM/DD/YYYY"
    return "YYYY-MM-DD"


_KIND_TO_TYPE = {"money": "money", "date": "date", "unit": "unit", "int": "int", "email": "email",
                 "phone": "phone", "name": "name", "address": "text", "text": "text",
                 "lease_status": "enum", "ticket_status": "enum", "priority": "enum"}


def derive_from_snapshots(after_snaps: list[dict], rows_by_snap: dict[str, list[dict]], label: str) -> dict:
    """Build a template from the consolidated system's own exports: each mapped column becomes a target
    column bound to the same canonical field. Keys are copied (we don't know the target's key scheme)."""
    by_entity: dict[str, dict] = {}
    for s in after_snaps:
        by_entity.setdefault(s["entity"], s)
    tables, names = [], {}
    for ent in ENTITY_ORDER:
        s = by_entity.get(ent)
        if not s:
            continue
        spec = ENTITIES[ent]
        mapping = json.loads(s["mapping_json"])
        rows = rows_by_snap.get(s["id"], [])
        tname = re.sub(r"[^a-z0-9_]", "_", spec.key)
        cols = []
        for fs in spec.fields:
            col = mapping.get(fs.key)
            if not col:
                continue
            values = [nz.text(r.get(col)) for r in rows]
            c: dict = {"name": col, "canonical": fs.key, "required": fs.required}
            if fs.key == spec.id_field:
                c["type"] = "key"
            elif fs.key in REF_TARGETS and REF_TARGETS[fs.key] in names and fs.key != spec.id_field:
                c["type"], c["ref"] = "ref", names[REF_TARGETS[fs.key]]
            elif fs.kind == "id":
                c["type"] = "text"
            else:
                c["type"] = _KIND_TO_TYPE.get(fs.kind, "text")
            if c["type"] == "date":
                c["format"] = _guess_date_format(values)
            if c["type"] == "enum":
                fn = VOCABULARIES[fs.kind]
                seen: dict[str, Counter] = {}
                for v in values:
                    if v:
                        seen.setdefault(fn(v) or "unknown", Counter())[v] += 1
                vals = {cnt.most_common(1)[0][0]: canon for canon, cnt in seen.items() if canon != "unknown"}
                if not vals:
                    c["type"] = "text"
                else:
                    c["values"], c["vocabulary"] = vals, fs.kind
            if c["type"] == "money" or (ent == "leases" and fs.key == "status"):
                c["financial"] = True
            cols.append(c)
        if not any(c["type"] == "key" for c in cols):
            continue
        key = next(c["name"] for c in cols if c["type"] == "key")
        file = re.sub(r"[^A-Za-z0-9_.\-]", "_", s["file_name"].rsplit(".", 1)[0])[:70] + ".csv"
        tables.append({"name": tname, "entity": ent, "file": file, "key": key, "key_prefix": "", "columns": cols})
        names[ent] = tname
    if not tables:
        raise TemplateError("The consolidated exports have no mapped ID columns to build a template from.")
    return validate({"id": "derived-from-consolidated", "name": f"Derived from {label}", "version": "derived",
                     "source": "derived", "tables": tables,
                     "notes": "Built from the consolidated system's own exports. Keys are copied from the legacy "
                              "system because the target's key scheme is unknown."})
