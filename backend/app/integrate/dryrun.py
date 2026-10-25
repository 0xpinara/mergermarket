"""Dry run: execute the plan against the legacy exports *without importing anything*, check the simulated import
files the way a strict importer would, and run the reconciliation engine on legacy vs simulated so predicted
issues use the same rule IDs and anchors as the post-import audit.

Importer model (conservative, stated in every report): a row that fails a required, type, length, vocabulary,
key-uniqueness or reference check is rejected, and rows that reference a rejected row are rejected in turn.
MergeProof does not know the destination's actual importer; it models the strict case."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict

from .. import config, db, ingest
from .. import normalize as nz
from ..engine import canonical, metrics, runner
from ..schema import ENTITIES
from . import plan as planmod
from . import transforms

BANNER = "SIMULATED OUTPUT — NOT IMPORTED, NOT VERIFIED"
IMPORTER_MODEL = ("Strict importer model: rows failing a required, type, length, vocabulary, key or reference check "
                  "are rejected, and rows that reference a rejected row are rejected too. The destination's real "
                  "importer may be more lenient; it is never assumed to be.")

CHECKS = {
    "PF-MAP": ("Required column has no approved source", True),
    "PF-REQ": ("Required value is blank", True),
    "PF-DEP": ("Deposit balance is blank (unknown — not zero)", True),
    "PF-TYPE": ("Value can't be read as the column's type", True),
    "PF-ENUM": ("Value has no equivalent in the target's code list", True),
    "PF-LEN": ("Value is longer than the target allows", True),
    "PF-KEY": ("Duplicate key", True),
    "PF-REF": ("Reference doesn't resolve", True),
    "PF-CASCADE": ("References a row that was itself rejected", True),
    "PF-WARN": ("Optional value unreadable — imported blank", False),
}
PII_TYPES = {"name", "email", "phone"}


class Sim:
    def __init__(self):
        self.tables: dict[str, dict] = {}
        self.crosswalk: dict[str, dict[str, str]] = defaultdict(dict)
        self.rejected_keys: dict[str, dict[str, dict]] = defaultdict(dict)
        self.problems: list[dict] = []


def execute(conn, plan: dict, src=None) -> Sim:
    """Run every approved/suggested transform over the legacy rows. Pure: writes nothing."""
    src = src or planmod.sources_for(conn, plan["deal_id"])
    items = {i["id"]: i for i in plan["items"]}
    bindings = {t["name"]: t for t in plan["tables"]}
    sim = Sim()
    for tb in plan["template"]["tables"]:
        b = bindings[tb["name"]]
        cols = tb["columns"]
        out = {"name": tb["name"], "entity": tb["entity"], "file": tb["file"], "columns": [c["name"] for c in cols],
               "source_file": b["source_file"], "rows": [], "lineage": [], "rejected": [], "source_rows": 0}
        sim.tables[tb["name"]] = out
        if not b["snapshot_id"] or b["snapshot_id"] not in src.snaps:
            continue
        rows = src.numbered(b["snapshot_id"])
        out["source_rows"] = len(rows)
        key_item = items[f"{tb['name']}.{tb['key']}"]
        unmapped_required = [c["name"] for c in cols if c["required"]
                             and not items[f"{tb['name']}.{c['name']}"]["transform"]]
        seen_keys: dict[str, int] = {}
        for seq, (rn, raw) in enumerate(rows, start=1):
            vals, lin, probs = {}, {}, []
            src_key = nz.ident(raw.get(key_item["sources"][0])) if key_item["sources"] else None
            for c in cols:
                it = items[f"{tb['name']}.{c['name']}"]
                t = it["transform"]
                value, prob = None, None
                if t:
                    raw_vals = [raw.get(s) for s in it["sources"]]
                    op = t["op"]
                    if op == "rekey":
                        value = f"{t['prefix']}{seq:0{t['width']}d}" if src_key else None
                    elif op == "copy" and c["type"] == "key":
                        value = nz.text(raw_vals[0]) or None
                    elif op == "ref_lookup":
                        ref_val = nz.ident(raw_vals[0])
                        if ref_val is not None:
                            if ref_val in sim.crosswalk[t["table"]]:
                                value = sim.crosswalk[t["table"]][ref_val]
                            elif ref_val in sim.rejected_keys[t["table"]]:
                                prob = {"check": "PF-CASCADE", "cause": sim.rejected_keys[t["table"]][ref_val]}
                            else:
                                prob = {"check": "PF-REF", "detail": f"no {t['table']} row has this key"}
                    else:
                        value, err = transforms.apply_value(t, raw_vals)
                        if err:
                            blocking = c["required"] or c["financial"]
                            check = ("PF-ENUM" if op == "enum_map" else "PF-TYPE") if blocking else "PF-WARN"
                            prob = {"check": check, "detail": err}
                    if prob:
                        prob.update(column=c["name"], value=None if c["type"] in PII_TYPES else
                                    nz.text(raw_vals[0] if raw_vals else "")[:40])
                        probs.append(prob)
                    lin[c["name"]] = {"sources": it["sources"], "op": op}
                if value is None and c["required"] and t and not prob:
                    check = "PF-DEP" if (tb["entity"] == "deposits" and c["type"] == "money") else "PF-REQ"
                    probs.append({"check": check, "column": c["name"], "value": None})
                if value is not None and c.get("max_length") and len(value) > c["max_length"]:
                    probs.append({"check": "PF-LEN", "column": c["name"], "value": None,
                                  "detail": f"{len(value)} > {c['max_length']} characters"})
                vals[c["name"]] = value
            if unmapped_required:
                probs.append({"check": "PF-MAP", "column": unmapped_required[0], "value": None,
                              "detail": ", ".join(unmapped_required)})
            if src_key is not None:
                if src_key in seen_keys:
                    probs.append({"check": "PF-KEY", "column": tb["key"], "value": nz.text(raw.get(key_item["sources"][0])),
                                  "detail": f"same key as source row {seen_keys[src_key]}"})
                else:
                    seen_keys[src_key] = rn
            blocking = [p for p in probs if CHECKS[p["check"]][1]]
            for p in probs:
                p.update(table=tb["name"], row=rn, file=b["source_file"], blocking=CHECKS[p["check"]][1])
                sim.problems.append(p)
            if blocking:
                root = blocking[0].get("cause") or {"check": blocking[0]["check"], "table": tb["name"],
                                                    "column": blocking[0]["column"]}
                if src_key is not None and src_key not in sim.rejected_keys[tb["name"]]:
                    sim.rejected_keys[tb["name"]][src_key] = root
                out["rejected"].append({"row": rn, "root": root, "problems": [
                    {k: p.get(k) for k in ("check", "column", "detail")} for p in probs]})
            else:
                if src_key is not None and vals.get(tb["key"]):
                    sim.crosswalk[tb["name"]].setdefault(src_key, vals[tb["key"]])
                out["rows"].append({c: ("" if v is None else v) for c, v in vals.items()})
                out["lineage"].append({"row": rn, "file": b["source_file"], "columns": lin,
                                       "legacy_key": nz.text(raw.get(key_item["sources"][0])) if key_item["sources"]
                                       else None})
    return sim


def table_csv(table: dict) -> bytes:
    """The simulated import file. Typed columns are validated; free-text cells that a spreadsheet would treat as a
    formula are prefixed with an apostrophe (counted in the manifest)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(table["columns"])
    for r in table["rows"]:
        w.writerow([r[c] for c in table["columns"]])
    return buf.getvalue().encode("utf-8")


def output_hash(sim: Sim) -> str:
    h = hashlib.sha256()
    for name in sorted(sim.tables):
        h.update(name.encode())
        h.update(table_csv(sim.tables[name]))
    return h.hexdigest()


def _simulated_inputs(plan: dict, sim: Sim) -> list[canonical.SnapshotInput]:
    out = []
    for tb in plan["template"]["tables"]:
        t = sim.tables[tb["name"]]
        if not t["source_rows"]:
            continue
        mapping = {c["canonical"]: c["name"] for c in tb["columns"] if c["canonical"]}
        rows = [(i + 2, r) for i, r in enumerate(t["rows"])]
        out.append(canonical.SnapshotInput(f"sim:{tb['name']}", "after", tb["entity"], f"{tb['file']} (simulated)",
                                           t["columns"], rows, mapping))
    return out


def _legacy_records(conn, deal: dict) -> dict:
    inputs, _ = runner.load_inputs(conn, deal["id"])
    legacy = [i for i in inputs if i.side == "before"]
    records, _, _ = canonical.build(legacy, runner.as_of_for(deal))
    out = {}
    for (side, ent), recs in records.items():
        for r in recs:
            out[(r.file, r.row)] = r
    return out


def _blast(plan: dict, sim: Sim, legacy: dict) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for tname, t in sim.tables.items():
        for rj in t["rejected"]:
            root = rj["root"]
            k = (root["check"], root["table"], root["column"])
            g = groups.setdefault(k, {"check": root["check"], "table": root["table"], "column": root["column"],
                                      "rows": defaultdict(int), "active_leases": 0, "rent_cents": 0,
                                      "deposit_cents": 0, "open_work_orders": 0})
            g["rows"][tname] += 1
            rec = legacy.get((t["source_file"], rj["row"]))
            if rec is None:
                continue
            if rec.entity == "leases" and rec.n.get("is_active"):
                g["active_leases"] += 1
                g["rent_cents"] += rec.n.get("monthly_rent") or 0
            elif rec.entity == "deposits":
                g["deposit_cents"] += rec.n.get("balance") or 0
            elif rec.entity == "maintenance" and rec.n.get("is_open"):
                g["open_work_orders"] += 1
    out = []
    for g in groups.values():
        g["rows"] = dict(g["rows"])
        g["rows_total"] = sum(g["rows"].values())
        g["label"] = CHECKS[g["check"]][0]
        out.append(g)
    out.sort(key=lambda g: (-g["rent_cents"], -g["rows_total"]))
    return out


def preflight(plan: dict, sim: Sim) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for p in sim.problems:
        k = (p["check"], p["table"], p["column"])
        g = groups.setdefault(k, {"check": p["check"], "label": CHECKS[p["check"]][0], "table": p["table"],
                                  "column": p["column"], "blocking": p["blocking"], "rows": 0, "examples": []})
        g["rows"] += 1
        if len(g["examples"]) < 5:
            ex = {"file": p["file"], "row": p["row"], "value": p.get("value"), "detail": p.get("detail")}
            if p.get("cause"):
                ex["cause"] = p["cause"]
            g["examples"].append(ex)
    out = sorted(groups.values(), key=lambda g: (not g["blocking"], -g["rows"], g["table"], g["column"]))
    # Checks that ran and found nothing are listed too, so a clean run shows what was tested.
    ran = []
    for tb in plan["template"]["tables"]:
        t = sim.tables[tb["name"]]
        ran.append({"table": tb["name"], "file": tb["file"], "source_file": t["source_file"],
                    "source_rows": t["source_rows"], "accepted": len(t["rows"]), "rejected": len(t["rejected"])})
    return out, ran


def _property_tieout(a) -> list[dict]:
    S, T, MP = a.S, a.T, a.M["properties"]
    out = []
    t_rent = defaultdict(int)
    t_count = defaultdict(int)
    t_unassigned = [0, 0]
    for k, l in T.leases.items():
        if not l.n.get("is_active"):
            continue
        p = T.unit_prop.get(T.lease_unit.get(k)) if T.lease_unit.get(k) else None
        if p:
            t_rent[p] += l.n.get("monthly_rent") or 0
            t_count[p] += 1
        else:
            t_unassigned[0] += l.n.get("monthly_rent") or 0
            t_unassigned[1] += 1
    for pk, p in S.props.items():
        rent = count = 0
        for k, l in S.leases.items():
            if l.n.get("is_active") and S.unit_prop.get(S.lease_unit.get(k)) == pk:
                rent += l.n.get("monthly_rent") or 0
                count += 1
        tk = MP.matches[pk].t if pk in MP.matches else None
        out.append({"property": p.f.get("name") or p.id, "legacy_id": p.id,
                    "legacy": {"active_leases": count, "rent_cents": rent},
                    "simulated": {"active_leases": t_count.get(tk, 0), "rent_cents": t_rent.get(tk, 0)}
                    if tk else None,
                    "ties": bool(tk) and rent == t_rent.get(tk, 0) and count == t_count.get(tk, 0)})
    if t_unassigned[1]:
        out.append({"property": "No property (unlinked in the simulated import)", "legacy_id": None,
                    "legacy": {"active_leases": 0, "rent_cents": 0},
                    "simulated": {"active_leases": t_unassigned[1], "rent_cents": t_unassigned[0]}, "ties": False})
    return out


def _predicted(a) -> tuple[list[dict], dict]:
    fs = []
    for f in a.ctx.findings:
        fs.append({
            "id": f["anchor"], "anchor": f["anchor"], "rule_id": f["rule_id"], "severity": f["severity"],
            "title": f["title"], "entity": f["entity"], "subject": f["subject"],
            "rent_risk_cents": f["rent_risk_cents"], "risk_key": f["risk_key"], "deposit_cents": f["deposit_cents"],
            "pending_rent_cents": f["pending_rent_cents"], "explanation": f["explanation"],
            "source": {k: (f["source"] or {}).get(k) for k in ("file", "row", "id")} if f["source"] else None,
            "target": {k: (f["target"] or {}).get(k) for k in ("file", "row", "id")} if f["target"] else None,
            "status": "open", "active": 1, "needs_rereview": 0,
        })
    rent, lines, _ = metrics.attribute_rent(fs)
    sev = defaultdict(int)
    for f in fs:
        sev[f["severity"]] += 1
    kpis = {"findings": len(fs), "critical": sev["critical"], "high": sev["high"], "medium": sev["medium"],
            "low": sev["low"], "rent_at_risk_cents": rent, "deposit_cents": sum(f["deposit_cents"] for f in fs)}
    fs.sort(key=lambda f: (metrics.SEVERITY_ORDER.get(f["severity"], 9), -(f["rent_risk_cents"] + f["deposit_cents"]),
                           f["rule_id"], f["subject"]))
    return fs, kpis


def _diff(prev: dict | None, cur: dict) -> dict | None:
    if not prev:
        return None
    pk = {f"{g['check']}|{g['table']}|{g['column']}": g["rows"] for g in prev["preflight"]}
    ck = {f"{g['check']}|{g['table']}|{g['column']}": g["rows"] for g in cur["preflight"]}
    pa = {f["anchor"] for f in prev["predicted"]}
    ca = {f["anchor"] for f in cur["predicted"]}
    return {
        "previous_id": prev["id"], "previous_version": prev["plan_version"],
        "preflight": {"resolved": sorted(k for k in pk if k not in ck), "new": sorted(k for k in ck if k not in pk),
                      "changed": sorted(k for k in ck if k in pk and ck[k] != pk[k])},
        "predicted": {"resolved": len(pa - ca), "new": len(ca - pa), "unchanged": len(pa & ca)},
        "rejected_rows": {"before": prev["totals"]["rejected"], "after": cur["totals"]["rejected"]},
        "rent_at_risk": {"before": prev["predicted_kpis"]["rent_at_risk_cents"],
                         "after": cur["predicted_kpis"]["rent_at_risk_cents"]},
    }


def latest(conn, deal_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM dry_runs WHERE deal_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                       (deal_id,)).fetchone()
    if row is None:
        return None
    out = db.loads(row["summary_json"])
    out.update(id=row["id"], plan_hash=row["plan_hash"], plan_version=row["plan_version"],
               contract_fp=row["contract_fp"], output_hash=row["output_hash"], created_at=row["created_at"])
    return out


def legacy_mapping_problems(conn, deal_id: str) -> list[str]:
    """The simulated verification reads the legacy exports exactly as the audit does, so their audit mapping must
    be complete; otherwise every legacy record without an ID is dropped and the prediction is meaningless."""
    out = []
    for s in conn.execute("SELECT * FROM snapshots WHERE deal_id=? AND side='before' ORDER BY entity, file_name",
                          (deal_id,)):
        v = ingest.validate_mapping(s["entity"], json.loads(s["mapping_json"]), json.loads(s["headers_json"]), [])
        if v["missing"] or v["problems"]:
            out.append(f"{s['file_name']}: map {', '.join(v['missing'] + v['problems'])}")
    return out


def run(conn, deal: dict, plan: dict, actor: str) -> dict:
    problems = legacy_mapping_problems(conn, deal["id"])
    if problems:
        raise planmod.PlanError("Finish mapping the legacy exports in Workspace first — the dry run's simulated "
                                "verification reads them the way the audit does. " + "; ".join(problems))
    src = planmod.sources_for(conn, deal["id"])
    sim = execute(conn, plan, src)
    pre, ran = preflight(plan, sim)
    legacy = _legacy_records(conn, deal)
    blast = _blast(plan, sim, legacy)
    inputs, _ = runner.load_inputs(conn, deal["id"])
    before = [i for i in inputs if i.side == "before"]
    a = runner.analyze(before + _simulated_inputs(plan, sim), runner.labels_for(deal), runner.as_of_for(deal))
    predicted, pkpis = _predicted(a)
    statuses = planmod.counts(plan)
    totals = {"source_rows": sum(t["source_rows"] for t in sim.tables.values()),
              "accepted": sum(len(t["rows"]) for t in sim.tables.values()),
              "rejected": sum(len(t["rejected"]) for t in sim.tables.values()),
              "blocking_checks": sum(1 for g in pre if g["blocking"]),
              "warnings": sum(1 for g in pre if not g["blocking"])}
    did = db.new_id("dry")
    summary = {
        "banner": BANNER, "importer_model": IMPORTER_MODEL, "engine_version": config.ENGINE_VERSION,
        "template": {"id": plan["template"]["id"], "name": plan["template"]["name"],
                     "version": plan["template"]["version"]},
        "plan_status": statuses, "provisional": statuses["total"] - statuses["decided"],
        "tables": ran, "totals": totals, "preflight": pre, "blast_radius": blast,
        "tieouts": a.checks, "properties": _property_tieout(a), "predicted": predicted, "predicted_kpis": pkpis,
        "matched": {e: r.counts() for e, r in a.M.items()},
        "files": {name: {"file": t["file"], "rows": len(t["rows"]), "sha256": hashlib.sha256(table_csv(t)).hexdigest()}
                  for name, t in sim.tables.items()},
        "actor": actor,
    }
    prev = latest(conn, deal["id"])
    cur = {**summary, "id": did, "plan_version": plan["version"]}
    summary["diff"] = _diff(prev, cur)
    oh = output_hash(sim)
    conn.execute("INSERT INTO dry_runs (id, deal_id, plan_id, plan_version, plan_hash, contract_fp, output_hash, "
                 "summary_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (did, deal["id"], plan["id"], plan["version"], plan["plan_hash"], plan["contract_fp"], oh,
                  db.dumps(summary), db.now()))
    planmod._event(conn, plan, actor, "dry_run", note=f"Dry run {did}: {totals['accepted']} rows simulated, "
                                                      f"{totals['rejected']} rejected, {pkpis['findings']} predicted "
                                                      f"finding(s).")
    return latest(conn, deal["id"])


def preview(conn, plan: dict, table: str, offset: int, limit: int, rejected_only: bool = False) -> dict:
    sim = execute(conn, plan)
    if table not in sim.tables:
        raise planmod.PlanError("No such table in the template.", 404)
    t = sim.tables[table]
    if rejected_only:
        rows = t["rejected"][offset:offset + limit]
        return {"banner": BANNER, "table": table, "columns": t["columns"], "rejected": rows,
                "total": len(t["rejected"]), "offset": offset}
    return {"banner": BANNER, "table": table, "file": t["file"], "columns": t["columns"],
            "rows": t["rows"][offset:offset + limit], "lineage": t["lineage"][offset:offset + limit],
            "total": len(t["rows"]), "rejected_total": len(t["rejected"]), "offset": offset,
            "entity": ENTITIES[t["entity"]].label}
