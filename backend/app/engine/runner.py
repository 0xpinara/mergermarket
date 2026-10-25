"""Audit orchestration: load snapshots -> canonical records -> matching ->
checks -> persisted findings. Each stage is timed and reported to the UI."""
from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime, timezone

from .. import config, db, ingest
from ..ai import embeddings
from ..schema import ENTITIES, ENTITY_ORDER
from . import canonical, matching, rules


class AuditBlocked(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def labels_for(deal: dict) -> dict:
    def short(name: str | None, suffix: str, fallback: str) -> str:
        if not name:
            return fallback
        first = name.split()[0].rstrip(",")
        return f"{first}'s {suffix}"

    old = short(deal.get("target_business"), "export", "the legacy export")
    new = short(deal.get("acquirer"), "import", "the consolidated system")
    return {
        "old": old, "new": new,
        "old_short": (deal.get("target_business") or "legacy").split()[0],
        "new_short": (deal.get("acquirer") or "consolidated").split()[0],
        "old_system": deal.get("old_system") or "the legacy system",
        "new_system": deal.get("new_system") or "the consolidated system",
    }


def as_of_for(deal: dict) -> str:
    """Cutover date used to judge lease terms; today (UTC) when the deal has none."""
    return deal.get("deal_date") or datetime.now(timezone.utc).date().isoformat()


def load_inputs(conn, deal_id: str) -> tuple[list[canonical.SnapshotInput], list[dict]]:
    snaps = [dict(r) for r in conn.execute(
        "SELECT * FROM snapshots WHERE deal_id=? ORDER BY side, entity, file_name, id", (deal_id,))]
    inputs = []
    for s in snaps:
        rows = [(r["row_number"], json.loads(r["raw_json"])) for r in conn.execute(
            "SELECT row_number, raw_json FROM source_rows WHERE snapshot_id=? ORDER BY row_number", (s["id"],))]
        mapping = json.loads(s["mapping_json"])
        label = f"{s['file_name']} › {s['sheet']}" if s["sheet"] else s["file_name"]
        inputs.append(canonical.SnapshotInput(s["id"], s["side"], s["entity"], label,
                                              json.loads(s["headers_json"]), rows, mapping))
    return inputs, snaps


def fingerprint(conn, deal: dict, snaps: list[dict]) -> str:
    overrides = [tuple(r) for r in conn.execute(
        "SELECT entity, source_key, target_key FROM match_overrides WHERE deal_id=? AND revoked_at IS NULL "
        "ORDER BY entity, source_key", (deal["id"],))]
    basis = {
        "engine": config.ENGINE_VERSION, "as_of": as_of_for(deal), "labels": labels_for(deal),
        "ai": embeddings.model_id() if embeddings.enabled() else None,
        "snapshots": sorted((s["side"], s["entity"], s["file_name"], s["sheet"] or "", s["content_hash"],
                             s["mapping_json"]) for s in snaps),
        "overrides": overrides,
    }
    return hashlib.sha256(db.dumps(basis).encode()).hexdigest()[:24]


def check_ready(snaps: list[dict]) -> tuple[list[str], list[str]]:
    problems, skipped = [], []
    sides = {"before": set(), "after": set()}
    for s in snaps:
        sides[s["side"]].add(s["entity"])
        mapping = json.loads(s["mapping_json"])
        v = ingest.validate_mapping(s["entity"], mapping, json.loads(s["headers_json"]), [])
        if v["missing"] or v["problems"]:
            label = f"{s['file_name']} ({s['side']})"
            problems.append(f"{label}: map {', '.join(v['missing'] + v['problems'])}")
    if not sides["before"]:
        problems.append("Add at least one legacy (before) export.")
    if not sides["after"]:
        problems.append("Add at least one consolidated (after) export.")
    for side in ("before", "after"):
        if sides[side] and "leases" not in sides[side]:
            problems.append(f"The {'legacy' if side == 'before' else 'consolidated'} side needs a leases export — "
                            f"lease continuity is the core check.")
    for ent in ENTITY_ORDER:
        if (ent in sides["before"]) != (ent in sides["after"]) and sides["before"] and sides["after"]:
            have = "legacy" if ent in sides["before"] else "consolidated"
            skipped.append(f"{ENTITIES[ent].label} were only provided for the {have} side — those checks were skipped.")
    return problems, skipped


class Analysis:
    """Everything one engine pass produces, before anything is persisted."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def analyze(inputs: list[canonical.SnapshotInput], labels: dict, as_of: str, overrides: dict | None = None,
            ai=None, stage=None) -> Analysis:
    """Pure reconciliation of a before side against an after side. Used by the audit (legacy vs actual
    consolidated export) and by the integration dry run (legacy vs simulated import)."""
    def note(name, detail, **extra):
        if stage:
            stage(name, detail, **extra)

    present = {"before": {s.entity for s in inputs if s.side == "before"},
               "after": {s.entity for s in inputs if s.side == "after"}}
    both = present["before"] & present["after"]
    used = [s for s in inputs if s.entity in both]
    n_rows = sum(len(s.rows) for s in used)
    note("Preparing exports", f"{n_rows:,} rows across {len(used)} files ({len(both)} record types on both sides)",
         rows=n_rows, files=len(used))

    records, dq, stats = canonical.build(used, as_of)
    note("Mapping fields", f"{stats['values']:,} values normalized · {len(dq)} data-quality notes",
         values=stats["values"])

    def recs(side):
        return {ent: records.get((side, ent), []) for ent in ENTITY_ORDER}

    S, T = matching.Side("before", recs("before")), matching.Side("after", recs("after"))
    ov = defaultdict(dict, overrides or {})
    M = {}
    M["properties"] = matching.match_properties(S, T, ov["properties"])
    M["units"] = matching.match_units(S, T, M["properties"], ov["units"])
    M["tenants"] = matching.match_tenants(S, T, ov["tenants"])
    M["leases"] = matching.match_leases(S, T, M["units"], M["tenants"], ov["leases"], M["properties"])
    matching.corroborate_tenants(S, T, M["tenants"], M["leases"])
    M["maintenance"] = matching.match_tickets(S, T, M["properties"], M["units"], ov["maintenance"])
    paired = sum(len(r.matches) for r in M.values())
    review = sum(len(r.review) for r in M.values())
    note("Matching entities", f"{paired:,} records paired one-to-one · {review} need human review",
         paired=paired, review=review)

    ctx = rules.run_all(S, T, M, dq, labels, as_of, ai=ai)
    ai_suggestions = sum(1 for f in ctx.findings for cnd in f["candidates"] if cnd.get("origin") == "ai")
    checks = rules.aggregate_checks(ctx)
    benign = rules.benign_changes(ctx)
    rules_run = _rules_checked(ctx, S, both)
    note("Testing invariants", f"{len(rules_run)} record-level checks + {len(checks)} totals · "
                               f"{len(ctx.findings)} findings"
                               + (f" · {ai_suggestions} AI suggestion{'s' if ai_suggestions != 1 else ''}" if ai else ""),
         findings=len(ctx.findings))
    return Analysis(S=S, T=T, M=M, ctx=ctx, checks=checks, benign=benign, rules_run=rules_run, records=records,
                    dq=dq, stats=stats, both=both, used=used, ai_suggestions=ai_suggestions)


def run_audit(conn, deal: dict) -> dict:
    run_id = db.new_id("run")
    started = db.now()
    stages: list[dict] = []
    clock = time.perf_counter()

    def stage(name: str, detail: str, **extra):
        nonlocal clock
        t = time.perf_counter()
        stages.append({"name": name, "detail": detail, "ms": round((t - clock) * 1000, 1), **extra})
        clock = t

    inputs, snaps = load_inputs(conn, deal["id"])
    problems, skipped = check_ready(snaps)
    if problems:
        raise AuditBlocked(problems)
    ov = defaultdict(dict)
    for r in conn.execute("SELECT entity, source_key, target_key FROM match_overrides "
                          "WHERE deal_id=? AND revoked_at IS NULL ORDER BY created_at", (deal["id"],)):
        ov[r["entity"]][r["source_key"]] = r["target_key"]
    labels = labels_for(deal)
    as_of = as_of_for(deal)
    emb = embeddings.get()
    a = analyze(inputs, labels, as_of, ov, ai=emb, stage=stage)

    fp = fingerprint(conn, deal, snaps)
    summary = _run_summary(a.S, a.T, a.M, a.ctx, a.checks, a.benign, a.rules_run, skipped, labels, as_of, a.used)
    summary["ai"] = {**embeddings.status(), "suggestions": a.ai_suggestions, "decisions_by_ai": 0,
                     "used_for": ["work-order candidates when rules find none", "column-mapping suggestions"]}
    conn.execute("INSERT INTO audit_runs (id, deal_id, started_at, status, engine_version, fingerprint, stages_json) "
                 "VALUES (?,?,?,?,?,?,?)", (run_id, deal["id"], started, "running", config.ENGINE_VERSION, fp, "[]"))
    _persist_findings(conn, deal["id"], run_id, a.ctx.findings)
    _persist_matches(conn, run_id, a.M)
    _persist_canonical(conn, deal["id"], run_id, a.records)
    stage("Building evidence", f"{len(a.ctx.findings)} evidence packets with file + row provenance saved",
          saved=len(a.ctx.findings))
    conn.execute("UPDATE audit_runs SET finished_at=?, status=?, stages_json=?, summary_json=? WHERE id=?",
                 (db.now(), "completed", db.dumps(stages), db.dumps(summary), run_id))
    conn.execute("UPDATE deals SET updated_at=? WHERE id=?", (db.now(), deal["id"]))
    return {"run_id": run_id, "stages": stages, "fingerprint": fp}


def _rules_checked(ctx: rules.Ctx, S: matching.Side, both: set) -> list[dict]:
    by_rule = defaultdict(int)
    for f in ctx.findings:
        by_rule[f["rule_id"]] += 1
    population = {
        "leases": len(S.leases), "tenants": len(S.tenants), "deposits": len(S.deposits),
        "maintenance": len(S.tickets), "properties": len(S.props), "units": len(S.units),
        "data_quality": sum(len(v) for v in (S.leases, S.tenants, S.deposits, S.tickets, S.props, S.units)),
    }
    out = []
    for rid, meta in rules.RULES.items():
        ent = meta["entity"]
        if ent != "data_quality" and ent not in both:
            continue
        out.append({"rule_id": rid, "name": meta["name"], "check": meta["check"], "entity": ent,
                    "default_severity": meta["severity"], "records_checked": population.get(ent, 0),
                    "findings": by_rule.get(rid, 0)})
    return out


def _run_summary(S, T, M, ctx, checks, benign, rules_run, skipped, labels, as_of, used) -> dict:
    counts = {}
    pairs = {"properties": (S.props, T.props), "units": (S.units, T.units), "tenants": (S.tenants, T.tenants),
             "leases": (S.leases, T.leases), "deposits": (S.deposits, T.deposits),
             "maintenance": (S.tickets, T.tickets)}
    for ent, (sd, td) in pairs.items():
        c = {"before": len(sd), "after": len(td)}
        if ent in M:
            c.update(M[ent].counts())
        counts[ent] = c
    lease_states = {}
    for k, l in S.leases.items():
        lease_states[k] = {"active": bool(l.n.get("is_active")), "match": M["leases"].status_of(k),
                           "unit": S.lease_unit.get(k), "rent": l.n.get("monthly_rent") or 0, "id": l.id}
    unit_map = []
    for pk, p in S.props.items():
        units = []
        for uk in sorted(S.units_by_prop.get(pk, []), key=lambda u: (S.units[u].n.get("unit_label") or "")):
            u = S.units[uk]
            active = [l for l in S.leases_by_unit.get(uk, []) if S.leases[l].n.get("is_active")]
            from ..normalize import floor_of
            units.append({"key": uk, "label": u.f.get("unit_label"), "norm": u.n.get("unit_label"),
                          "floor": floor_of(u.n.get("unit_label")), "lease": active[0] if active else None,
                          "tenant": S.tenant_name(S.lease_tenant.get(active[0])) if active else None,
                          "rent": S.leases[active[0]].n.get("monthly_rent") if active else None})
        unit_map.append({"key": pk, "name": p.f.get("name") or p.f.get("address") or p.id,
                         "address": p.f.get("address"), "units": units})
    files = [{"side": s.side, "entity": s.entity, "file": s.file, "rows": len(s.rows)} for s in used]
    sa = [l for l in S.leases.values() if l.n.get("is_active")]
    return {
        "labels": labels, "as_of": as_of, "counts": counts, "checks": checks, "benign": benign,
        "rules": rules_run, "skipped": skipped, "lease_states": lease_states, "unit_map": unit_map,
        "files": files,
        "totals": {"active_leases": len(sa), "active_rent": sum(l.n.get("monthly_rent") or 0 for l in sa),
                   "source_records": sum(len(v) for v in (S.props, S.units, S.tenants, S.leases, S.tickets)),
                   "target_records": sum(len(v) for v in (T.props, T.units, T.tenants, T.leases, T.tickets))},
        "stale_overrides": {e: r.overrides_stale for e, r in M.items() if r.overrides_stale},
    }


def finding_id(deal_id: str, anchor: str) -> str:
    return "F-" + hashlib.sha1(f"{deal_id}|{anchor}".encode()).hexdigest()[:10].upper()


def evidence_hash(f: dict) -> str:
    basis = {
        "rule": f["rule_id"], "severity": f["severity"], "compare": f["compare"],
        "src": (f["source"] or {}).get("raw"), "tgt": (f["target"] or {}).get("raw"),
        "extra": [x.get("raw") for x in f["extra_targets"]],
        "cands": [(c["target_key"], c["score"]) for c in f["candidates"]],
        "impact": f["impact"].get("cents"), "members": f["members"],
    }
    return hashlib.sha1(db.dumps(basis).encode()).hexdigest()[:16]


def _persist_findings(conn, deal_id: str, run_id: str, findings: list[dict]) -> None:
    existing = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, status, active, reviewed_hash, needs_rereview, evidence_hash FROM findings WHERE deal_id=?",
        (deal_id,))}
    ts = db.now()
    seen = set()
    for f in findings:
        fid = finding_id(deal_id, f["anchor"])
        if fid in seen:
            continue
        seen.add(fid)
        f["id"] = fid
        eh = evidence_hash(f)
        payload = db.dumps(f)
        cols = (f["rule_id"], f["severity"], f["entity"], f["title"], f["subject"], f["rent_risk_cents"],
                f["risk_key"], f["deposit_cents"], f["pending_rent_cents"], f["confidence"], eh, run_id, payload, ts)
        if fid in existing:
            ex = existing[fid]
            needs = ex["needs_rereview"]
            changed = ex["reviewed_hash"] is not None and ex["reviewed_hash"] != eh
            if ex["status"] != "open" and changed and not needs:
                needs = 1
                conn.execute(
                    "INSERT INTO review_events (id, finding_id, deal_id, reviewer, action, note, from_status, "
                    "to_status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (db.new_id("rev"), fid, deal_id, "MergeProof", "evidence_changed",
                     "Source data changed since this finding was reviewed — please re-review.",
                     ex["status"], ex["status"], ts))
            conn.execute(
                "UPDATE findings SET rule_id=?, severity=?, entity=?, title=?, subject=?, rent_risk_cents=?, "
                "risk_key=?, deposit_cents=?, pending_rent_cents=?, confidence=?, evidence_hash=?, last_run_id=?, "
                "payload_json=?, updated_at=?, active=1, needs_rereview=? WHERE id=?", cols + (needs, fid))
        else:
            conn.execute(
                "INSERT INTO findings (rule_id, severity, entity, title, subject, rent_risk_cents, risk_key, "
                "deposit_cents, pending_rent_cents, confidence, evidence_hash, last_run_id, payload_json, updated_at, "
                "id, deal_id, first_run_id, created_at, status, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                "'open',1)", cols + (fid, deal_id, run_id, ts))
    gone = [fid for fid, ex in existing.items() if fid not in seen and ex["active"]]
    for fid in gone:
        conn.execute("UPDATE findings SET active=0, updated_at=? WHERE id=?", (ts, fid))


def _persist_matches(conn, run_id: str, M: dict) -> None:
    rows = []
    for ent, res in M.items():
        for s, m in res.matches.items():
            rows.append((run_id, ent, s, m.t, m.status, m.score, db.dumps({"reasons": m.reasons, "notes": m.notes})))
        for s, cands in res.review.items():
            top = cands[0]["pair"]
            rows.append((run_id, ent, s, None, "needs_review", top.score,
                         db.dumps({"candidates": [[c["pair"].t, c["pair"].score] for c in cands]})))
        for s in res.unmatched_s:
            if s not in res.review:
                rows.append((run_id, ent, s, None, "unmatched", 0, "{}"))
    conn.executemany("INSERT INTO entity_matches (run_id, entity, source_key, target_key, status, score, reasons_json) "
                     "VALUES (?,?,?,?,?,?,?)", rows)


def _persist_canonical(conn, deal_id: str, run_id: str, records: dict) -> None:
    # Keep canonical records only for the latest run (older runs keep their summaries and matches).
    conn.execute("DELETE FROM canonical_records WHERE run_id IN (SELECT id FROM audit_runs WHERE deal_id=? AND id<>?)",
                 (deal_id, run_id))
    rows = []
    for (side, ent), lst in records.items():
        for r in lst:
            rows.append((run_id, side, ent, r.key, r.id, r.file, r.row, db.dumps(r.raw), db.dumps(r.n)))
    conn.executemany("INSERT OR REPLACE INTO canonical_records (run_id, side, entity, key, record_id, file, row_number, "
                     "raw_json, normalized_json) VALUES (?,?,?,?,?,?,?,?,?)", rows)
