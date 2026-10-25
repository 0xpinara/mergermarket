"""The integration plan: one per deal, versioned, hashed, and changed only by named reviewers.

The plan hash covers what the plan *does* (template, source contract, table bindings, every column's sources and
transform, which columns are ignored). Approvals are tracked per item but are not part of the hash, so approving
a column doesn't invalidate a dry run that already exercised exactly that mapping — while any change to what is
mapped does. Every action is appended to integration_events with before/after copies of the item."""
from __future__ import annotations

import copy
import hashlib
import json

from .. import db
from ..ai import embeddings
from . import mapping, profile, transforms
from . import template as tpl

ACTIONS = ("approve", "reject", "edit", "ignore", "reopen")
DECIDED = ("approved", "ignored")


class PlanError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def plan_hash(template: dict, contract_fp: str, tables: list[dict], items: list[dict]) -> str:
    basis = {
        "template": [template["id"], template["version"]], "contract": contract_fp,
        "tables": [(t["name"], t["source_file"]) for t in tables],
        "items": [(i["id"], i["sources"], i["transform"], i["status"] == "ignored") for i in items],
    }
    return hashlib.sha256(db.dumps(basis).encode()).hexdigest()[:32]


def sources_for(conn, deal_id: str) -> mapping.Sources:
    snaps = profile.legacy_snapshots(conn, deal_id)
    if not snaps:
        raise PlanError("Add the legacy (before) exports first — the plan is built from them.")
    return mapping.Sources(conn, profile.build(conn, deal_id), snaps)


def available_templates(conn, deal_id: str) -> list[dict]:
    out = [{"id": t["id"], "name": t["name"], "version": t["version"], "source": "bundled",
            "synthetic": t["synthetic"], "tables": len(t["tables"]),
            "columns": sum(len(x["columns"]) for x in t["tables"])} for t in tpl.bundled()]
    n_after = conn.execute("SELECT COUNT(*) FROM snapshots WHERE deal_id=? AND side='after'", (deal_id,)).fetchone()[0]
    out.append({"id": "derived-from-consolidated", "name": "Derived from the consolidated exports",
                "version": "derived", "source": "derived", "synthetic": False, "available": bool(n_after),
                "note": "Uses the target system's own export columns as the import format."
                        if n_after else "Upload a consolidated (after) export to derive a template from it."})
    return out


def resolve_template(conn, deal: dict, template_id: str) -> dict:
    if template_id == "derived-from-consolidated":
        snaps = [dict(r) for r in conn.execute(
            "SELECT * FROM snapshots WHERE deal_id=? AND side='after' ORDER BY entity, file_name", (deal["id"],))]
        if not snaps:
            raise PlanError("Upload a consolidated (after) export first.", 409)
        rows = {s["id"]: [json.loads(r["raw_json"]) for r in conn.execute(
            "SELECT raw_json FROM source_rows WHERE snapshot_id=? ORDER BY row_number LIMIT 2000", (s["id"],))]
            for s in snaps}
        return tpl.derive_from_snapshots(snaps, rows, deal.get("new_system") or "the consolidated system")
    t = tpl.get_bundled(template_id)
    if t is None:
        raise PlanError("Unknown template.", 404)
    return t


def load(conn, deal_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM integration_plans WHERE deal_id=?", (deal_id,)).fetchone()
    if row is None:
        return None
    p = dict(row)
    p["template"] = json.loads(p.pop("template_json"))
    p["tables"] = json.loads(p.pop("tables_json"))
    p["items"] = json.loads(p.pop("items_json"))
    return p


def _event(conn, plan: dict, actor: str, action: str, item_id: str | None = None, note: str | None = None,
           before=None, after=None) -> None:
    conn.execute(
        "INSERT INTO integration_events (id, plan_id, deal_id, version, actor, action, item_id, note, before_json, "
        "after_json, plan_hash, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (db.new_id("iev"), plan["id"], plan["deal_id"], plan["version"], actor, action, item_id, note,
         db.dumps(before) if before is not None else None, db.dumps(after) if after is not None else None,
         plan["plan_hash"], db.now()))


def _save(conn, plan: dict) -> None:
    conn.execute(
        "UPDATE integration_plans SET template_id=?, template_version=?, template_json=?, version=?, contract_fp=?, "
        "tables_json=?, items_json=?, plan_hash=?, updated_at=? WHERE id=?",
        (plan["template"]["id"], plan["template"]["version"], db.dumps(plan["template"]), plan["version"],
         plan["contract_fp"], db.dumps(plan["tables"]), db.dumps(plan["items"]), plan["plan_hash"], db.now(),
         plan["id"]))


def _rehash(plan: dict) -> bool:
    h = plan_hash(plan["template"], plan["contract_fp"], plan["tables"], plan["items"])
    changed = h != plan["plan_hash"]
    if changed:
        plan["plan_hash"] = h
        plan["version"] += 1
    return changed


def create(conn, deal: dict, template_id: str, actor: str) -> dict:
    template = resolve_template(conn, deal, template_id)
    src = sources_for(conn, deal["id"])
    tables, items = mapping.suggest(template, src, embeddings.get_if_ready())
    fp = src.profile["fingerprint"]
    h = plan_hash(template, fp, tables, items)
    existing = load(conn, deal["id"])
    ts = db.now()
    if existing:
        plan = {**existing, "template": template, "contract_fp": fp, "tables": tables, "items": items,
                "version": existing["version"] + 1, "plan_hash": h}
        _save(conn, plan)
        _event(conn, plan, actor, "regenerated", note=f"Suggestions rebuilt from {template['name']}; "
                                                      f"earlier decisions were cleared.")
        return plan
    plan = {"id": db.new_id("plan"), "deal_id": deal["id"], "template": template, "version": 1, "contract_fp": fp,
            "tables": tables, "items": items, "plan_hash": h, "created_at": ts, "updated_at": ts}
    conn.execute(
        "INSERT INTO integration_plans (id, deal_id, template_id, template_version, template_json, version, "
        "contract_fp, tables_json, items_json, plan_hash, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (plan["id"], deal["id"], template["id"], template["version"], db.dumps(template), 1, fp, db.dumps(tables),
         db.dumps(items), h, ts, ts))
    _event(conn, plan, actor, "created", note=f"Plan created from {template['name']} ({template['version']}).")
    return plan


def _template_col(plan: dict, item: dict) -> tuple[dict, dict]:
    tb = next(t for t in plan["template"]["tables"] if t["name"] == item["table"])
    return tb, next(c for c in tb["columns"] if c["name"] == item["column"])


def _binding(plan: dict, table: str) -> dict:
    return next(t for t in plan["tables"] if t["name"] == table)


def act(conn, plan: dict, item_id: str, action: str, reviewer: str, note: str | None = None,
        sources: list[str] | None = None, transform: dict | None = None) -> dict:
    if action not in ACTIONS:
        raise PlanError("Unknown action.", 422)
    item = next((i for i in plan["items"] if i["id"] == item_id), None)
    if item is None:
        raise PlanError("No such column in this plan.", 404)
    before = copy.deepcopy(item)
    tb, col = _template_col(plan, item)
    decision = {"by": reviewer, "at": db.now(), "action": action, "note": note}
    if action == "approve":
        if item["status"] in DECIDED:
            raise PlanError(f"Already {item['status']}. Reopen it first.")
        if not item["transform"]:
            raise PlanError("Nothing to approve — choose a source column (edit) or ignore the column.")
        if mapping.needs_note(item) and not note:
            raise PlanError("This mapping is contested or weakly supported — add a note explaining why it's right.",
                            422)
        item["status"] = "approved"
    elif action == "reject":
        if item["status"] in DECIDED:
            raise PlanError(f"Already {item['status']}. Reopen it first.")
        item["sources"], item["transform"], item["status"] = [], None, "rejected"
    elif action == "edit":
        b = _binding(plan, item["table"])
        if not b["snapshot_id"]:
            raise PlanError("This table has no legacy export to map from.")
        headers = json.loads(conn.execute("SELECT headers_json FROM snapshots WHERE id=?",
                                          (b["snapshot_id"],)).fetchone()[0])
        srcs = [s for s in (sources or []) if isinstance(s, str)]
        t = transform or (item["transform"] if item["transform"] and srcs == item["sources"] else
                          transforms.default_for(col, tb))
        try:
            t = transforms.validate(col, tb, t, srcs, headers)
        except transforms.TransformError as exc:
            raise PlanError(str(exc), 422) from exc
        src = sources_for(conn, plan["deal_id"])
        if b["snapshot_id"] in src.files:
            for s in srcs:
                guard = mapping._type_guard(col, src.col(b["snapshot_id"], s))
                if guard and not (t["op"] == "concat" and col["type"] in ("name", "text")):
                    raise PlanError(f"`{s}` {guard}.", 422)
        if mapping.needs_note(item, srcs) and not note:
            raise PlanError("You're choosing something other than the best-supported source — add a note "
                            "explaining why.", 422)
        item["sources"], item["transform"], item["status"] = srcs, t, "approved"
    elif action == "ignore":
        if col["required"]:
            raise PlanError("Required columns can't be ignored — the import would reject every row.", 422)
        if not note:
            raise PlanError("Say why this column can be left empty.", 422)
        item["sources"], item["transform"], item["status"] = [], None, "ignored"
    elif action == "reopen":
        if item["status"] not in ("approved", "rejected", "ignored"):
            raise PlanError("Only decided columns can be reopened.")
        src = sources_for(conn, plan["deal_id"])
        bindings = {t["name"]: t for t in plan["tables"]}
        fresh = mapping.suggest_column(plan["template"], tb, col, bindings, src, embeddings.get_if_ready())
        for k in ("sources", "transform", "status", "confidence", "reasons", "candidates", "excluded", "warnings"):
            item[k] = fresh[k]
        if "enum_coverage" in fresh:
            item["enum_coverage"] = fresh["enum_coverage"]
    item["decision"] = decision if action != "reopen" else None
    item.setdefault("history", []).append(decision)
    _rehash(plan)
    _save(conn, plan)
    _event(conn, plan, reviewer, action, item_id, note, before=_slim(before), after=_slim(item))
    return item


def bulk_approve(conn, plan: dict, reviewer: str) -> list[str]:
    done = []
    for item in plan["items"]:
        if mapping.bulk_eligible(item):
            before = _slim(item)
            item["status"] = "approved"
            item["decision"] = {"by": reviewer, "at": db.now(), "action": "bulk_approve", "note": None}
            item.setdefault("history", []).append(item["decision"])
            done.append(item["id"])
            _event(conn, plan, reviewer, "bulk_approve", item["id"], None, before=before, after=_slim(item))
    _save(conn, plan)
    return done


def _slim(item: dict) -> dict:
    return {k: item.get(k) for k in ("sources", "transform", "status", "confidence")}


def drift(conn, plan: dict) -> dict:
    """Has the legacy source contract changed since the plan was built or last rebased?"""
    snaps = profile.legacy_snapshots(conn, plan["deal_id"])
    current = profile.contract_fingerprint(snaps)
    if current == plan["contract_fp"]:
        return {"changed": False, "plan_fp": plan["contract_fp"], "current_fp": current, "issues": []}
    issues = []
    by_entity: dict[str, dict] = {}
    for s in sorted(snaps, key=lambda s: -s["row_count"]):
        by_entity.setdefault(s["entity"], s)
    for b in plan["tables"]:
        cur = by_entity.get(b["entity"])
        if b["snapshot_id"] and not cur:
            issues.append({"table": b["name"], "issue": f"{b['source_file']} was removed."})
            continue
        if not cur:
            continue
        headers = set(json.loads(cur["headers_json"]))
        for it in plan["items"]:
            if it["table"] == b["name"]:
                gone = [s for s in it["sources"] if s not in headers]
                if gone:
                    issues.append({"table": b["name"], "item": it["id"],
                                   "issue": f"`{gone[0]}` is no longer in {cur['file_name']}."})
    return {"changed": True, "plan_fp": plan["contract_fp"], "current_fp": current, "issues": issues}


def rebase(conn, plan: dict, reviewer: str) -> dict:
    """Accept the changed source contract: rebind tables, re-check every column, keep sound decisions."""
    d = drift(conn, plan)
    if not d["changed"]:
        raise PlanError("The source exports haven't changed since this plan was built.")
    src = sources_for(conn, plan["deal_id"])
    new_tables = mapping.bind_tables(plan["template"], src)
    bindings = {t["name"]: t for t in new_tables}
    reset = 0
    for item in plan["items"]:
        b = bindings[item["table"]]
        tb, col = _template_col(plan, item)
        headers = src.headers(b["snapshot_id"]) if b["snapshot_id"] else []
        ok = b["snapshot_id"] and all(s in headers for s in item["sources"]) and not any(
            mapping._type_guard(col, src.col(b["snapshot_id"], s)) for s in item["sources"])
        if item["status"] in ("approved", "ignored") and (ok or item["status"] == "ignored"):
            continue
        fresh = mapping.suggest_column(plan["template"], tb, col, bindings, src, embeddings.get_if_ready())
        if item["status"] == "approved":
            fresh["status"], fresh["confidence"] = "needs_review", "conflict"
            fresh["reasons"] = ["The source export changed and the approved column no longer fits."] + fresh["reasons"]
            reset += 1
        for k in ("sources", "transform", "status", "confidence", "reasons", "candidates", "excluded", "warnings"):
            item[k] = fresh[k]
        item["decision"] = None
    plan["tables"], plan["contract_fp"] = new_tables, src.profile["fingerprint"]
    _rehash(plan)
    _save(conn, plan)
    _event(conn, plan, reviewer, "rebased",
           note=f"Accepted changed legacy exports ({len(d['issues'])} issue(s)); {reset} approval(s) sent back to "
                f"review.")
    return plan


def dependency_warnings(conn, plan: dict) -> dict[str, list[str]]:
    """Live check of keys against the references that depend on them, using the plan's *current* choices."""
    out: dict[str, list[str]] = {}
    try:
        src = sources_for(conn, plan["deal_id"])
    except PlanError:
        return out
    bindings = {t["name"]: t for t in plan["tables"]}
    if any(b["snapshot_id"] and b["snapshot_id"] not in src.snaps for b in plan["tables"]):
        return out  # drifted; the drift banner covers it
    items = {i["id"]: i for i in plan["items"]}
    for tb in plan["template"]["tables"]:
        key_item = items.get(f"{tb['name']}.{tb['key']}")
        if not key_item or not key_item["sources"]:
            continue
        b = bindings[tb["name"]]
        if not b["snapshot_id"]:
            continue
        for rtb, rc in mapping.inbound_refs(plan["template"], tb["name"]):
            ref_item = items.get(f"{rtb['name']}.{rc['name']}")
            rb = bindings.get(rtb["name"])
            if not ref_item or not ref_item["sources"] or not rb or not rb["snapshot_id"]:
                continue
            inc = mapping._inclusion(src, rb["snapshot_id"], ref_item["sources"][0], b["snapshot_id"],
                                     key_item["sources"][0])
            if inc["values"] and inc["ratio"] < mapping.REF_OK:
                msg = (f"Only {inc['found']}/{inc['values']} `{ref_item['sources'][0]}` values exist in "
                       f"{b['source_file']} `{key_item['sources'][0]}` (the {tb['name']} key) — the rest can't be "
                       f"linked and a strict importer will reject those rows.")
                out.setdefault(ref_item["id"], []).append(msg)
                out.setdefault(key_item["id"], []).append(
                    f"{rtb['name']}.{rc['name']}: {inc['values'] - inc['found']} reference value(s) don't resolve "
                    f"through `{key_item['sources'][0]}`.")
    return out


def counts(plan: dict) -> dict:
    c = {"total": len(plan["items"]), "approved": 0, "ignored": 0, "needs_review": 0, "suggested": 0,
         "rejected": 0, "unmapped": 0}
    for i in plan["items"]:
        c[i["status"]] = c.get(i["status"], 0) + 1
    c["bulk_eligible"] = sum(1 for i in plan["items"] if mapping.bulk_eligible(i))
    c["financial_pending"] = sum(1 for i in plan["items"] if i["financial"] and i["status"] not in DECIDED)
    c["required_open"] = sum(1 for i in plan["items"] if i["required"] and i["status"] != "approved")
    c["decided"] = c["approved"] + c["ignored"]
    return c


def events(conn, deal_id: str, limit: int = 200) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM integration_events WHERE deal_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
                          (deal_id, limit)):
        e = dict(r)
        e["before"] = json.loads(e.pop("before_json") or "null")
        e["after"] = json.loads(e.pop("after_json") or "null")
        out.append(e)
    return out
